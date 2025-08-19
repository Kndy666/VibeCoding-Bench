import toml
from pathlib import Path
from typing import Dict, Any
import os

class AgentConfig:
    """Agent配置管理类"""
    
    def __init__(self, config_path: str = "config.toml"):
        self.base_path = Path(__file__).parent
        self.config_path = self.base_path / config_path
        self._config = self._load_config()
        
    def _load_config(self) -> Dict[str, Any]:
        """加载TOML配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
            
        with open(self.config_path, "r") as f:
            return toml.load(f)
    
    def get(self, section: str, key: str, default: Any = None) -> Any:
        """获取配置项"""
        return self._config.get(section, {}).get(key, default)
    
    def get_section(self, section: str) -> Dict[str, Any]:
        """获取整个配置段"""
        return self._config.get(section, {})
    
    @property
    def log_level(self) -> str:
        return self.get("logging", "level", "INFO")
    
    @property
    def log_format(self) -> str:
        return self.get("logging", "format")
    
    @property
    def log_file(self) -> str:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, self.get("logging", "log_file"))
    
    @property
    def analysis_file(self) -> Path:
        return Path(self.get("paths", "analysis_file"))
    
    @property
    def trae_config_path(self) -> str:
        return self.get("paths", "trae_config")
    
    @property
    def swap_dir(self) -> str:
        return self.get("paths", "swap_dir")
    
    @property
    def max_specs_per_repo(self) -> int:
        return self.get("execution", "max_specs_per_repo", 3)
    
    @property
    def default_python_version(self) -> str:
        return self.get("execution", "default_python_version", "3.8")
    
    def get_prompt_template(self, prompt_type: str) -> str:
        """获取prompt模板"""
        return self.get("prompts", prompt_type, {}).get("template", "")
    
    @property
    def file_list_prompt_template(self) -> str:
        return self.get_prompt_template("file_list")
    
    @property
    def env_setup_prompt_template(self) -> str:
        return self.get_prompt_template("env_setup")
    
    @property
    def setup_files_name(self) -> str:
        return self.get("files", "setup_files_list", "setup_files_list.json")
    
    @property
    def version_file_name(self) -> str:
        return self.get("files", "recommended_python_version", "recommended_python_version.json")
    
    @property
    def dockerfile_template(self) -> str:
        """获取Dockerfile模板"""
        return self.get("dockerfile", "template", "")
    
    @property
    def docker_base_image(self) -> str:
        """获取Docker基础镜像"""
        return self.get("docker", "base_image", "ubuntu:22.04")
