import docker
import os
import base64
import logging
from pathlib import Path
from typing import List, Dict, Optional, Set, Any
import docker.models.containers
from command_executor import LocalCommandExecutor, DockerCommandExecutor, docker_environment
from docker_image_builder import DockerImageBuilder
from locate_test import CodeChange
from pytest_output_parse import TestStatus, PytestResultParser

class CacheManager:
    """容器和镜像缓存管理器"""
    
    def __init__(self, repo: str, repo_id: str, timeout=300):
        self.logger = logging.getLogger(__name__)
        self.client = docker.from_env(timeout=timeout)
        self.repo = repo.replace("/", "_")
        self.repo_id = repo_id
        self.repo_lower = self.repo.lower()
        self.image_builder = DockerImageBuilder(timeout)
        self.base_path = Path(__file__).parent

    @property
    def common_container_config(self) -> Dict[str, Any]:
        """提取并返回通用的容器创建参数"""
        
        config = {
            "name": self.repo,
            "command": "/bin/bash",
            "detach": True,
            "tty": True,
            "runtime": "nvidia",
            "network_mode": "host",
            "device_requests": [{
                'count': -1,
                'capabilities': [['gpu']]
            }],
            "environment": docker_environment,
            "volumes": {
                str(self.base_path / "swap"): {
                    "bind": "/workdir/swap",
                    "mode": "rw"
                }
            }
        }

        # if os.name == 'posix':
        #     uid = os.getuid()
        #     gid = os.getgid()
        #     self.logger.info(f"在 POSIX 系统上运行，设置容器用户为 UID={uid}, GID={gid}")
        #     config['user'] = f"{uid}:{gid}"
            
        return config
    
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

        # 镜像名必须小写
        image_name = f"cached_{self.repo_lower}"
        
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

        image_name = f"cached_{self.repo_lower}:{self.repo_id}"
        
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

        image_name = f"cached_{self.repo_lower}:{self.repo_id}"
        
        self.logger.info(f"从缓存镜像创建容器: {image_name}")
        
        container = self.client.containers.run(
            image=image_name,
            **self.common_container_config
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
            **self.common_container_config
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

        if self.container:
            self.docker_executor.execute(f"git config --global --add safe.directory /workdir/swap/{self.repo_name}")

    def repo_clone(self, use_docker=True):
        """克隆仓库"""
        # 检查目录是否已存在
        if use_docker:
            check_cmd = f"test -d swap/{self.repo_name}"
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
            exit_code, output = self.docker_executor.execute(command, "/workdir/swap", stream=True, tty=True)
        else:
            exit_code, output = self.local_executor.execute(command, self.base_path / "swap", stream=True, tty=True)

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
                exit_code, output = self.docker_executor.execute(cmd, str(Path("/workdir/swap") / self.repo_name), tty=False, timeout=30)
            else:
                exit_code, output = self.local_executor.execute(cmd, self.base_path / "swap" / self.repo_name, tty=False, timeout=30)

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
            status = change.get("status", "")
            
            if not filename or not patch_content or not status:
                continue
            
            # 构建完整的diff格式内容
            header = f"diff --git a/{filename} b/{filename}\n"
            if status == "added":
                diff_content = (
                    f"{header}"
                    f"new file mode 100644\n"
                    f"--- /dev/null\n"
                    f"+++ b/{filename}\n"
                    f"{patch_content}\n"
                )
            elif status == "modified":
                diff_content = (
                    f"{header}"
                    f"--- a/{filename}\n"
                    f"+++ b/{filename}\n"
                    f"{patch_content}\n"
                )
            elif status == "removed":
                diff_content = (
                    f"{header}"
                    f"deleted file mode 100644\n"
                    f"--- a/{filename}\n"
                    f"+++ /dev/null\n"
                    f"{patch_content}\n"
                )
            elif status == "renamed":
                continue

            # 将patch内容编码为base64并写入临时文件
            patch_base64 = base64.b64encode(diff_content.encode('utf-8')).decode('utf-8')
            write_cmd = f"echo '{patch_base64}' | base64 -d > /tmp/patch.tmp"
            
            if use_docker:
                exit_code, output = self.docker_executor.execute(write_cmd, tty=False, timeout=30)
            else:
                exit_code, output = self.local_executor.execute(write_cmd, tty=False, timeout=30)
                
            if exit_code != 0:
                self.logger.error(f"写入patch到临时文件失败: {output}")
                raise RuntimeError(f"写入patch到临时文件失败: {output}")
            
            # 应用patch到目标文件
            apply_cmd = "patch -p1 < /tmp/patch.tmp"
            if use_docker:
                exit_code, output = self.docker_executor.execute(apply_cmd, str(Path("/workdir/swap") / self.repo_name), tty=False, timeout=30)
            else:
                exit_code, output = self.local_executor.execute(apply_cmd, self.base_path / "swap" / self.repo_name, tty=False, timeout=30)

            if exit_code != 0:
                self.logger.error(f"应用patch到 {filename} 失败: {output}")
                raise RuntimeError(f"应用patch到 {filename} 失败: {output}")
            
            modified_files.append(filename)
            self.logger.info(f"成功应用patch到: {filename}")
        
        return modified_files

    def _find_test_dirs(self, repo_name: str, use_docker: bool = True) -> List[str]:
        """递归检测仓库中的测试目录（容器内或本地），返回存在的目录列表（若未检测到返回 ['tests']）"""
        candidates = ["tests", "test", "Tests", "TESTS", "unit_tests", "TEST"]
        ignore_dirs = [".venv"]

        prune_expr = " -o ".join([f"-path './{d}' -prune" for d in ignore_dirs])
        prune_expr = f"\\( {prune_expr} \\) -o "

        # 完整find命令
        find_cmd = (
            f"find . {prune_expr}-type d \\( " +
            " -o ".join([f"-name '{d}'" for d in candidates]) +
            " \\) -print"
        )
        
        if use_docker:
            workdir = f"/workdir/swap/{repo_name}"
            exit_code, output = self.docker_executor.execute(find_cmd, workdir, tty=False, timeout=30)
        else:
            workdir = str(self.base_path / "swap" / repo_name)
            exit_code, output = self.local_executor.execute(find_cmd, workdir, tty=False, timeout=30)

        if output is None:
            output = ""
        
        # 清理路径，移除开头的./
        found = [line.strip().lstrip('./') for line in output.splitlines() if line.strip()]

        if not found:
            self.logger.info(f"未检测到常见测试目录（{candidates}），回退到默认 'tests'")
            return ["tests"]

        self.logger.info(f"检测到测试目录: {found}")
        return found

    def run_tests_in_container(self, repo_name: str, test_files: Optional[List[Dict[str, CodeChange]]] = None, expected_statuses: Optional[List[TestStatus]] = None) -> tuple[Set[str], str]:
        """在容器中运行测试并返回通过的测试文件和日志"""
        pytest_args = []

        if test_files is None:
            dirs = self._find_test_dirs(repo_name, use_docker=True)
            for d in dirs:
                pytest_args.append(f"{d}/")
        else:
            for test_file in test_files:
                for file_name, changes in test_file.items():
                    for change in changes:
                        if change.change_type == 'deleted':
                            continue
                        elif change.code_type == 'function':
                            pytest_args.append(f"{file_name}::{change.name}")
                        elif change.code_type == 'method':
                            class_name, method_name = change.name.split('.', 1)
                            pytest_args.append(f"{file_name}::{class_name}::{method_name}")
        
        cmd = f"python3 -m pytest -q -rA --tb=no -p no:pretty --timeout=15 --continue-on-collection-errors {' '.join(pytest_args)}"
        
        exit_code, output = self.docker_executor.execute(cmd, f"/workdir/swap/{repo_name}", stream=True, tty=True, timeout=300)
        matched_files = self.parse_pytest_output(output, pytest_args, expected_statuses)
        return matched_files, output

    def parse_pytest_output(self, logs: str, test_cases: List[str], expected_statuses: List[TestStatus]) -> Set[str]:
        """解析pytest输出，提取完全通过测试的文件（无失败和错误）"""
        
        parser = PytestResultParser(logs)
        
        # 检查test_cases是否为目录形式
        is_directory_test = any(arg.endswith('/') for arg in test_cases)
        
        if is_directory_test:
            # 使用 parser 提供的筛选函数获取所有符合期望状态的测试项
            matched = parser.filter_tests_by_status(expected_statuses)
            self.logger.info(f"目录测试匹配到 {len(matched)} 个符合期望状态的测试")
            return matched
        else:
            # 原有的处理逻辑
            results = parser.query_tests(test_cases)
            self.logger.info("查询结果:")
            for test, status in results.items():
                self.logger.info(f"  {test}: {status.value}")
            return set(test for test, status in results.items() if status in expected_statuses)

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