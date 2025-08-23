import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
import os
from typing import Optional
import docker.models.containers
from agent_config import AgentConfig
from command_executor import LocalCommandExecutor, DockerCommandExecutor

logger = logging.getLogger(__name__)

class AgentTaskType(Enum):
    """Agent任务类型枚举"""
    FILE_LIST = "file_list"
    ENV_SETUP = "env_setup"

class AgentExecutor:
    """Agent执行器类"""
    
    def __init__(self, config: AgentConfig, use_docker: bool = True):
        self.config = config
        self.use_docker = use_docker
        self.bash_path = Path(__file__).parent
        self.executor = None  # 将在需要时初始化

    def _get_executor(self, container: Optional[docker.models.containers.Container] = None):
        """获取合适的命令执行器"""
        if self.use_docker:
            if container is None:
                raise ValueError("使用Docker模式时必须提供container参数")
            return DockerCommandExecutor(container)
        else:
            return LocalCommandExecutor()

    def _generate_file_list_prompt(self, repo_name: str) -> str:
        """生成用于列出环境配置文件的prompt"""
        template = self.config.file_list_prompt_template
        
        return template.format(
            repo_name=repo_name,
            setup_files=self.config.setup_files_name,
            version_file=self.config.version_file_name,
            default_version=self.config.default_python_version
        )

    def _generate_env_setup_prompt(self, repo_name: str, created_time: Optional[str] = None) -> str:
        """生成用于配置环境的prompt"""
        template = self.config.env_setup_prompt_template

        # 新增 created_time 变量传递，转换为 YYYY-MM-DD 格式
        formatted_created_time = ""
        if created_time:
            try:
                # 兼容 ISO 格式（如 2025-04-11T22:41:15Z）
                dt = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
                formatted_created_time = dt.strftime("%Y-%m-%d")
            except Exception:
                # 如果解析失败，直接传原始字符串
                formatted_created_time = created_time

        return template.format(
            repo_name=repo_name,
            setup_files=self.config.setup_files_name,
            version_file=self.config.version_file_name,
            created_time=formatted_created_time
        )

    def _build_trae_command(self, prompt: str, repo_name: str, trajectory_file: str) -> str:
        """构建trae-cli命令"""
        escaped_prompt = prompt.replace('"', '\\"').replace("'", "'\\''").replace('\n', '\\n')
        activate_cmd = self.config.get("trae", "activate_command")
        if self.use_docker:
            working_dir = Path("/workdir/swap") / repo_name
            config_file = "/workdir/swap/trae-agent/trae_config.yaml"
        else:
            working_dir = self.bash_path / "swap" / repo_name
            config_file = str(self.bash_path / "swap" / "trae-agent" / "trae_config.yaml")
        
        # 确保使用bash执行包含source命令的脚本
        return f"""bash -c "{activate_cmd} && uv run trae-cli run \\"{escaped_prompt}\\" --config-file {config_file} --working-dir {working_dir} --trajectory-file {trajectory_file}\""""

    def _execute_trae_command(self, command: str, 
                             container: Optional[docker.models.containers.Container] = None) -> tuple[int, str]:
        """执行trae命令并返回退出码和输出"""
        executor = self._get_executor(container)
        
        if self.use_docker:
            workdir = "/workdir/trae-agent"
        else:
            workdir = str(self.bash_path / "swap" / "trae-agent")
        
        logger.info(f"执行trae-agent命令: {'容器内' if self.use_docker else '本机'}")
        
        try:
            exit_code, output = executor.execute(command, workdir, stream=True, tty=True)
            return exit_code, output
        except Exception as e:
            logger.error(f"命令执行失败: {str(e)}")
            raise RuntimeError(f"命令执行失败: {str(e)}")

    def _generate_trajectory_filename(self, repo_name: str, repo_id: str, stage: str) -> str:
        """生成轨迹文件名"""
        timestamp_format = self.config.get("trae", "trajectory_timestamp_format")
        timestamp = datetime.now().strftime(timestamp_format)
        
        if self.use_docker:
            trajectory_path = Path("/workdir/swap/trajectory") / repo_name
        else:
            trajectory_path = self.bash_path / "swap" / "trajectory" / repo_name
            os.makedirs(trajectory_path, exist_ok=True)

        return trajectory_path / f"{repo_id}_{timestamp}_{stage}_trajectory.json"

    def call_trae_agent(self, repo_name: str, repo_id: str, 
                       task_type: AgentTaskType, created_time: str = None,
                       container: Optional[docker.models.containers.Container] = None) -> str:
        """在容器内或本机执行trae-agent命令的主协调函数"""
        
        if self.use_docker and container is None:
            raise ValueError("使用Docker模式时必须提供container参数")
        
        # 生成prompt
        if task_type == AgentTaskType.FILE_LIST:
            prompt = self._generate_file_list_prompt(repo_name)
            stage = task_type.value
        elif task_type == AgentTaskType.ENV_SETUP:
            prompt = self._generate_env_setup_prompt(repo_name, created_time)
            stage = task_type.value
        else:
            raise ValueError(f"不支持的任务类型: {task_type}")
        
        # 生成轨迹文件名
        trajectory_file = self._generate_trajectory_filename(repo_name, repo_id, stage)
        
        # 构建命令
        command = self._build_trae_command(prompt, repo_name, trajectory_file)
        
        # 执行命令
        exit_code, output_str = self._execute_trae_command(command, container)
        
        # 处理结果
        logger.info(f"trae-agent执行完成，返回码: {exit_code}")
        
        if exit_code is not None and exit_code != 0:
            raise RuntimeError(f"trae-agent命令失败，返回码: {exit_code}\n输出: {output_str}")
        
        logger.info("trae-agent执行成功")
        return output_str