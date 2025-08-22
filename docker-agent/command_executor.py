import subprocess
import logging
from typing import Tuple
import docker.models.containers
from abc import ABC, abstractmethod
import pty
import os
import select
import fcntl
import shutil

# 获取终端尺寸并定义环境变量
terminal_size = shutil.get_terminal_size()
terminal_width = terminal_size.columns
terminal_height = terminal_size.lines

# 定义需要注入的环境变量
PROXY_URL = "http://127.0.0.1:58591"

docker_environment = {
    # "http_proxy": PROXY_URL,
    # "https_proxy": PROXY_URL,
    "COLUMNS": str(terminal_width),
    "LINES": str(terminal_height),
    "PYTHONUNBUFFERED": "1"
}


class BaseCommandExecutor(ABC):
    """命令执行器基类"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    @abstractmethod
    def execute(self, command: str, workdir: str = None, stream: bool = False) -> Tuple[int, str]:
        """执行命令"""
        pass
    
    @abstractmethod
    def execute_stream(self, command: str, workdir: str = None):
        """流式执行命令"""
        pass


class LocalCommandExecutor(BaseCommandExecutor):
    """本地命令执行器"""
    
    def execute(self, command: str, workdir: str = None, stream: bool = False) -> Tuple[int, str]:
        """在本地执行命令"""
        self.logger.info(f"本地执行命令: {command}")
        
        try:
            # 合并环境变量
            env = dict(os.environ)
            env.update(docker_environment)
            
            if stream:
                return self._execute_stream_internal(command, workdir)
            else:
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    env=env
                )
                return result.returncode, result.stdout + result.stderr
        except Exception as e:
            self.logger.error(f"本地命令执行出错: {e}")
            return 1, str(e)
    
    def execute_stream(self, command: str, workdir: str = None):
        """流式执行本地命令"""
        self.logger.info(f"本地流式执行命令: {command}")
        
        try:
            return self._execute_with_pty(command, workdir)
        except Exception as e:
            self.logger.error(f"PTY执行失败，回退到标准方式: {e}")
            return self._execute_stream_fallback(command, workdir)
    
    def _execute_with_pty(self, command: str, workdir: str = None) -> Tuple[int, str]:
        """使用PTY执行命令以更好地捕获进度信息"""
        master_fd, slave_fd = pty.openpty()
        
        try:
            # 合并环境变量
            env = dict(os.environ)
            env.update(docker_environment)
            
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=workdir,
                stdout=slave_fd,
                stderr=slave_fd,
                stdin=slave_fd,
                preexec_fn=os.setsid,
                env=env
            )
            
            os.close(slave_fd)  # 子进程会使用这个fd，父进程应该关闭它
            
            # 设置非阻塞读取
            fcntl.fcntl(master_fd, fcntl.F_SETFL, os.O_NONBLOCK)
            
            output_lines = []
            
            while True:
                # 检查进程是否结束
                if process.poll() is not None:
                    # 进程已结束，读取剩余输出
                    try:
                        remaining = os.read(master_fd, 4096).decode('utf-8', errors='replace')
                        if remaining:
                            print(remaining, end='', flush=True)
                            output_lines.append(remaining)
                    except (OSError, BlockingIOError):
                        pass
                    break
                
                # 使用select等待数据
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                
                if ready:
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            text = data.decode('utf-8', errors='replace')
                            print(text, end='', flush=True)
                            output_lines.append(text)
                    except (OSError, BlockingIOError):
                        continue
            
            process.wait()
            return process.returncode, ''.join(output_lines)
            
        finally:
            try:
                os.close(master_fd)
            except OSError:
                pass
    
    def _execute_stream_fallback(self, command: str, workdir: str = None) -> Tuple[int, str]:
        """回退的流式执行方法"""
        try:
            # 合并环境变量
            env = dict(os.environ)
            env.update(docker_environment)
            
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                bufsize=0,  # 无缓冲
                universal_newlines=True,
                env=env
            )
            
            output_lines = []
            for line in process.stdout:
                self.logger.debug(f"命令输出: {line.rstrip()}")
                print(line, end='', flush=True)
                output_lines.append(line)
            
            process.wait()
            return process.returncode, ''.join(output_lines)
            
        except Exception as e:
            self.logger.error(f"回退流式执行本地命令出错: {e}")
            return 1, str(e)
    
    def _execute_stream_internal(self, command: str, workdir: str = None) -> Tuple[int, str]:
        """内部流式执行方法"""
        return self.execute_stream(command, workdir)


class DockerCommandExecutor(BaseCommandExecutor):
    """Docker容器命令执行器"""
    
    def __init__(self, container: docker.models.containers.Container):
        super().__init__()
        self.container = container
        self.environment = docker_environment
    
    def execute(self, command: str, workdir: str = "/workdir", stream: bool = False) -> Tuple[int, str]:
        """在Docker容器中执行命令"""
        self.logger.info(f"Docker容器执行命令: {command}")
        
        try:
            if stream:
                return self._execute_stream_internal(command, workdir)
            else:
                exit_code, output = self.container.exec_run(
                    f"/bin/bash -c '{command}'",
                    workdir=workdir,
                    environment=self.environment
                )
                return exit_code, output.decode('utf-8', errors='replace')
        except Exception as e:
            self.logger.error(f"Docker命令执行出错: {e}")
            return 1, str(e)
    
    def execute_stream(self, command: str, workdir: str = "/workdir"):
        """在Docker容器中流式执行命令"""
        self.logger.info(f"Docker容器流式执行命令: {command}")
        
        try:
            exec_result = self.container.exec_run(
                f"/bin/bash -c '{command}'",
                workdir=workdir,
                stdout=True,
                stderr=True,
                stream=True,
                tty=True,
                environment=self.environment
            )
            
            output_lines = []
            for line in exec_result.output:
                line_str = line.decode('utf-8', errors='replace')
                self.logger.debug(f"命令输出: {line_str.rstrip()}")
                print(line_str, end='', flush=True)
                output_lines.append(line_str)
            
            exec_result.output.close() if hasattr(exec_result.output, 'close') else None
            return exec_result.exit_code, ''.join(output_lines)
            
        except Exception as e:
            self.logger.error(f"Docker流式命令执行出错: {e}")
            return 1, str(e)