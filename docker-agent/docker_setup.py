import docker
import os
import base64
import logging
from pathlib import Path
from typing import List, Dict, Optional
import docker.models.containers
from command_executor import LocalCommandExecutor, DockerCommandExecutor, docker_environment
from docker_image_builder import DockerImageBuilder

class CacheManager:
    """容器和镜像缓存管理器"""
    
    def __init__(self, repo: str, repo_id: str, timeout=300):
        self.logger = logging.getLogger(__name__)
        self.client = docker.from_env(timeout=timeout)
        self.repo = repo.replace("/", "_")
        self.repo_id = repo_id
        self.image_builder = DockerImageBuilder(timeout)
        self.base_path = Path(__file__).parent
    
    def check_cached_container(self) -> Optional[docker.models.containers.Container]:
        """检查是否存在缓存的容器"""
        
        try:
            # 查找已存在的容器
            container = self.client.containers.get(self.repo)
            
            # 检查容器状态
            if container.status == 'running':
                self.logger.info(f"发现运行中的缓存容器: {self.repo}")
                return container
            elif container.status == 'exited':
                self.logger.info(f"发现已停止的缓存容器: {self.repo}，正在重启...")
                container.start()
                return container
            else:
                self.logger.warning(f"容器 {self.repo} 状态异常: {container.status}，将重新创建")
                container.remove(force=True)
                return None
                
        except docker.errors.NotFound:
            self.logger.info(f"未找到缓存容器: {self.repo}")
            return None
        except Exception as e:
            self.logger.error(f"检查缓存容器时出错: {str(e)}")
            return None

    def save_container_as_image(self, container: docker.models.containers.Container) -> str:
        """保存容器为新的镜像"""

        image_name = f"cached_{self.repo}"
        
        try:
            self.logger.info(f"正在保存容器为镜像: {image_name}")
            
            # 提交容器为新镜像
            image = container.commit(repository=image_name, tag=self.repo_id)
            
            self.logger.info(f"成功保存镜像: {image_name}:latest (ID: {image.id[:12]})")
            return image.id
            
        except Exception as e:
            self.logger.error(f"保存容器镜像失败: {str(e)}")
            raise RuntimeError(f"保存容器镜像失败: {str(e)}")

    def check_cached_image(self) -> bool:
        """检查是否存在缓存的镜像"""

        image_name = f"cached_{self.repo}:{self.repo_id}"
        
        try:
            self.client.images.get(image_name)
            self.logger.info(f"发现缓存镜像: {image_name}")
            return True
        except docker.errors.ImageNotFound:
            self.logger.info(f"未找到缓存镜像: {image_name}")
            return False
        except Exception as e:
            self.logger.error(f"检查缓存镜像时出错: {str(e)}")
            return False

    def create_container_from_cached_image(self) -> docker.models.containers.Container:
        """从缓存镜像创建容器"""

        image_name = f"cached_{self.repo}:{self.repo_id}"
        
        self.logger.info(f"从缓存镜像创建容器: {image_name}")
        
        container = self.client.containers.run(
            image=image_name,
            name=self.repo,
            command="/bin/bash",
            detach=True,
            tty=True,
            runtime="nvidia",
            network_mode="host",
            device_requests=[{
                'count': -1,
                'capabilities': [['gpu']]
            }],
            environment=docker_environment,
            volumes={
                self.base_path / "swap": {
                    "bind": "/workdir/swap",
                    "mode": "rw"
                }
            }
        )
        
        self.logger.info(f"从缓存镜像成功创建容器: {self.repo}")
        return container

    def create_new_container(self) -> docker.models.containers.Container:
        """创建新的容器"""
        self.logger.info(f"创建新容器: {self.repo}")

        # 构建动态镜像
        image_name = self.image_builder.build_image(self.repo)

        # 创建带有GPU支持的容器
        container = self.client.containers.run(
            image=image_name,
            name=self.repo,
            command="/bin/bash",
            detach=True,
            tty=True,
            runtime="nvidia",
            network_mode="host",
            device_requests=[{
                'count': -1,
                'capabilities': [['gpu']]
            }],
            environment=docker_environment,
            volumes={
                self.base_path / "swap": {
                    "bind": "/workdir/swap",
                    "mode": "rw"
                }
            }
        )

        self.logger.info(f"容器 {self.repo} 创建成功（带GPU支持）")
        return container

class ContainerOperator:
    """容器操作类"""
    
    def __init__(self, repo: str, container: Optional[docker.models.containers.Container] = None):
        self.container = container
        self.logger = logging.getLogger(__name__)
        self.docker_executor = DockerCommandExecutor(container)
        self.local_executor = LocalCommandExecutor()
        self.base_path = Path(__file__).parent
        self.repo = repo
        self.repo_name = repo.split("/")[-1]

    def repo_clone(self, use_docker=True):
        """克隆仓库"""
        # 检查目录是否已存在
        if use_docker:
            check_cmd = f"test -d /workdir/swap/{self.repo_name}"
            exit_code, _ = self.docker_executor.execute(check_cmd)
        else:
            repo_path = self.base_path / "swap" / self.repo_name
            if repo_path.exists():
                exit_code = 0
            else:
                exit_code = 1
        
        if exit_code == 0:
            self.logger.info(f"目录 {self.repo_name} 已存在，跳过克隆")
            return
        
        repo_url = f"https://hk.gh-proxy.com/https://github.com/{self.repo}.git"
        command = f"git clone {repo_url}"
        
        # 使用流式执行命令
        if use_docker:
            exit_code, output = self.docker_executor.execute_stream(command)
        else:
            exit_code, output = self.local_executor.execute_stream(command, self.base_path / "swap")
        
        self.logger.info(f"命令完成，返回码: {exit_code}")
        if exit_code is not None and exit_code != 0:
            self.logger.error(f"命令执行失败: {command}\n错误: {output}")
            raise RuntimeError(f"命令执行失败: {command}\n错误: {output}")

    def checkout_commit(self, commit_hash: str, use_docker=True) -> None:
        """切换到指定的commit"""
        self.logger.info(f"正在强制切换到commit: {commit_hash}")
        
        commands = [
            f"git reset --hard",
            f"git clean -fd",
            f"git checkout {commit_hash}"
        ]
        
        for cmd in commands:
            if use_docker:
                exit_code, output = self.docker_executor.execute(cmd, Path("/workdir/swap") / self.repo_name)
            else:
                exit_code, output = self.local_executor.execute(cmd, self.base_path / "swap" / self.repo_name)

            if exit_code != 0:
                self.logger.error(f"执行命令失败: {cmd}\n错误: {output}")
                raise RuntimeError(f"执行命令失败: {cmd}\n错误: {output}")
                
            self.logger.info(f"执行成功: {cmd.split('&&')[-1].strip()}")
        
        self.logger.info(f"成功强制切换到commit: {commit_hash}")

    def apply_patches(self, file_changes: List[Dict], use_docker=True) -> List[str]:
        """应用文件变更"""
        modified_files = []
        
        for change in file_changes:
            filename = change.get("filename")
            patch_content = change.get("patch", "")
            
            if not filename or not patch_content:
                continue
            
            # 构建完整的diff格式内容
            diff_content = (
                f"diff --git a/{filename} b/{filename}\n"
                f"--- a/{filename}\n"
                f"+++ b/{filename}\n"
                f"{patch_content}\n"
            )
            
            # 将patch内容编码为base64并写入临时文件
            patch_base64 = base64.b64encode(diff_content.encode('utf-8')).decode('utf-8')
            write_cmd = f"echo '{patch_base64}' | base64 -d > /tmp/patch.tmp"
            
            if use_docker:
                exit_code, output = self.docker_executor.execute(write_cmd)
            else:
                exit_code, output = self.local_executor.execute(write_cmd)
                
            if exit_code != 0:
                self.logger.error(f"写入patch到临时文件失败: {output}")
                raise RuntimeError(f"写入patch到临时文件失败: {output}")
            
            # 应用patch到目标文件
            apply_cmd = "patch -p1 < /tmp/patch.tmp"
            if use_docker:
                exit_code, output = self.docker_executor.execute(apply_cmd, Path("/workdir/swap") / self.repo_name)
            else:
                exit_code, output = self.local_executor.execute(apply_cmd, self.base_path / "swap" / self.repo_name)
            
            if exit_code != 0:
                self.logger.error(f"应用patch到 {filename} 失败: {output}")
                raise RuntimeError(f"应用patch到 {filename} 失败: {output}")
            
            modified_files.append(filename)
            self.logger.info(f"成功应用patch到: {filename}")
        
        return modified_files

class DockerEnvironmentManager:
    """Docker环境管理器"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def setup_container_and_environment(self, repo: str, repo_id: str, timeout=300) -> docker.models.containers.Container:
        """创建Docker容器并配置测试环境（带缓存支持）"""

        self.cache_manager = CacheManager(repo, repo_id, timeout)
        # 首先检查是否存在缓存的容器
        cached_container = self.cache_manager.check_cached_container()
        if cached_container:
            return cached_container

        # 检查是否存在缓存的镜像
        if self.cache_manager.check_cached_image():
            return self.cache_manager.create_container_from_cached_image()

        # 创建新容器（使用动态构建的镜像）
        return self.cache_manager.create_new_container()
    
    def cleanup_container(self, container: docker.models.containers.Container, force_remove: bool = False) -> None:
        """清理容器资源"""
        if container:
            try:
                if force_remove:
                    container.stop()
                    container.remove()
                    self.logger.info(f"容器 {container.name} 已删除")
                else:
                    self.logger.info(f"容器 {container.name} 保留作为缓存")
                    
            except Exception as e:
                self.logger.error(f"处理容器时出错: {str(e)}")
                container.stop()
                container.remove()
                self.logger.info(f"容器 {container.name} 已删除")