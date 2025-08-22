import docker
import toml
import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any

class DockerImageBuilder:
    """Docker镜像构建器"""
    
    def __init__(self, timeout=300):
        self.logger = logging.getLogger(__name__)
        self.client = docker.from_env(timeout=timeout)
        self.api_client = docker.APIClient(timeout=timeout)  # 添加低级API客户端
        self.base_path = Path(__file__).parent

        self.config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        config_path = self.base_path / "config.toml"
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return toml.load(f)
        except Exception as e:
            self.logger.error(f"加载配置文件失败: {e}")
            raise
    
    def _read_python_version(self, repo: str) -> str:
        """从项目中读取推荐的Python版本"""
        version_file = self.base_path / "swap" / self.config['files']['recommended_python_version']
        
        try:
            if version_file.exists():
                version = json.loads(version_file.read_text())
                self.logger.info(f"从文件读取到Python版本: {version[repo]}")
                return version[repo]
            else:
                default_version = self.config['execution']['default_python_version']
                self.logger.info(f"未找到版本文件，使用默认版本: {default_version}")
                return default_version
        except Exception as e:
            self.logger.warning(f"读取Python版本失败: {e}，使用默认版本")
            return self.config['execution']['default_python_version']
    
    def _generate_dockerfile_content(self, python_version: str) -> str:
        """生成Dockerfile内容"""
        template = self.config['dockerfile']['template']
        
        # 替换模板中的占位符
        dockerfile_content = template.format(
            base_image=f"python:{python_version}-bullseye",
            agent_prompt= "swap/trae-agent/trae_agent/prompt/agent_prompt.py"
        )
        
        # 添加代理环境变量到Dockerfile开头
        proxy_lines = []
        if self.config.get('proxy', {}).get('enabled', False):
            proxy_config = self.config['proxy']
            if proxy_config.get('http_proxy'):
                proxy_lines.append(f"ARG HTTP_PROXY={proxy_config['http_proxy']}")
                proxy_lines.append(f"ARG http_proxy={proxy_config['http_proxy']}")
            if proxy_config.get('https_proxy'):
                proxy_lines.append(f"ARG HTTPS_PROXY={proxy_config['https_proxy']}")
                proxy_lines.append(f"ARG https_proxy={proxy_config['https_proxy']}")
        
        if proxy_lines:
            dockerfile_content = '\n'.join(proxy_lines) + '\n\n' + dockerfile_content
        
        return dockerfile_content
    
    def build_image(self, repo: str) -> str:
        """构建Docker镜像"""
        # 读取Python版本
        python_version = self._read_python_version(repo)
        
        # 生成镜像名称
        image_name = f"codegen_{python_version}"
        
        # 检查镜像是否已存在
        try:
            existing_image = self.client.images.get(image_name)
            self.logger.info(f"发现已存在的镜像: {image_name}")
            return image_name
        except docker.errors.ImageNotFound:
            pass
        
        # 生成Dockerfile内容
        dockerfile_content = self._generate_dockerfile_content(python_version)
        
        # 准备构建参数
        buildargs = {}
        if self.config.get('proxy', {}).get('enabled', False):
            proxy_config = self.config['proxy']
            if proxy_config.get('http_proxy'):
                buildargs['HTTP_PROXY'] = proxy_config['http_proxy']
                buildargs['http_proxy'] = proxy_config['http_proxy']
            if proxy_config.get('https_proxy'):
                buildargs['HTTPS_PROXY'] = proxy_config['https_proxy']
                buildargs['https_proxy'] = proxy_config['https_proxy']
        
        # 在项目根目录创建临时Dockerfile
        dockerfile_path = self.base_path / "Dockerfile.tmp"
        
        try:
            dockerfile_path.write_text(dockerfile_content)
            
            self.logger.info(f"开始构建镜像: {image_name} (Python {python_version})")
            if buildargs:
                self.logger.info(f"使用代理设置: {list(buildargs.keys())}")
            
            # 使用项目根目录作为构建上下文
            for chunk in self.api_client.build(
                path=str(self.base_path),
                tag=image_name,
                rm=True,
                forcerm=True,
                dockerfile="Dockerfile.tmp",
                network_mode="host",
                buildargs=buildargs,
                decode=True
            ):
                if 'stream' in chunk:
                    log_line = chunk['stream'].strip()
                    if log_line:
                        print(log_line, flush=True)  # 直接打印到控制台
                        self.logger.info(log_line)  # 同时记录到日志
            
            self.logger.info(f"镜像构建成功: {image_name}")
            return image_name
            
        except Exception as e:
            self.logger.error(f"镜像构建失败: {e}")
            raise RuntimeError(f"镜像构建失败: {e}")
        finally:
            # 清理临时Dockerfile
            if dockerfile_path.exists():
                dockerfile_path.unlink()
                self.logger.debug("已清理临时Dockerfile")