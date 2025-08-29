import logging
import json
import signal
import sys
import shutil
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any

from agent_config import AgentConfig
from agent_executor import AgentExecutor, AgentTaskType
from docker_setup import DockerEnvironmentManager, ContainerOperator
from locate_test import CodeChange, CodeChangeAnalyzer, PytestFilter
from pytest_output_parse import TestStatus

class DockerAgentRunner:
    """Docker Agent运行器类"""
    
    def __init__(self, config_path: str = "config.toml", test_only: bool = False):
        self.config = AgentConfig(config_path)
        self.docker_executor = AgentExecutor(self.config, use_docker=True)
        self.local_executor = AgentExecutor(self.config, use_docker=False)
        self.active_containers = []
        self.cleanup_in_progress = False
        self.docker_manager = DockerEnvironmentManager()
        self.base_path = Path(__file__).parent
        self.test_only = test_only
        
        # 配置日志
        self._setup_logging()
        
        # 注册信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger = logging.getLogger(__name__)

    def _setup_logging(self):
        """配置日志"""
        logging.basicConfig(
            level=getattr(logging, self.config.log_level),
            format=self.config.log_format,
            handlers=[
                logging.FileHandler(self.config.log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )

    def _signal_handler(self, signum, frame):
        """处理终止信号"""
        if self.cleanup_in_progress:
            self.logger.info("清理已在进行中，忽略重复信号")
            return
            
        self.cleanup_in_progress = True
        self.logger.info(f"\n收到信号 {signum}，正在清理容器...")
        
        for container in self.active_containers[:]:
            if container:
                try:
                    try:
                        response = input(f"\n是否要删除容器 {container.name}? (y/N): ").strip().lower()
                        force_remove = response in ['y', 'yes']
                    except (EOFError, KeyboardInterrupt):
                        force_remove = False
                        self.logger.info("用户中断输入，默认保留容器")
                    
                    self.docker_manager.cleanup_container(container, force_remove=force_remove)
                    self.active_containers.remove(container)
                except Exception as e:
                    self.logger.error(f"清理容器 {container.name} 时出错: {e}")
        
        self.cleanup_in_progress = False
        sys.exit(0)

    def _load_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """加载并按仓库分组specs"""
        with self.config.analysis_file.open("r", encoding="utf-8") as f:
            specs = json.load(f)

        specs_by_repo = defaultdict(list)
        for spec in specs:
            repo = spec["repo"]
            specs_by_repo[repo].append(spec)
        
        return specs_by_repo

    def _save_specs(self, specs_by_repo: Dict[str, List[Dict[str, Any]]]):
        """保存specs到文件"""
        updated_specs = []
        for all_repo_specs in specs_by_repo.values():
            updated_specs.extend(all_repo_specs)
        
        with self.config.analysis_file.open("w", encoding="utf-8") as f:
            json.dump(updated_specs, f, indent=2, ensure_ascii=False)

    def _setup_repo_environment(self, container, repo: str, repo_name: str, spec: Dict[str, Any]):
        """设置仓库环境"""
        self.logger.info(f"第二阶段：为仓库 {repo} 配置环境")

        self._restore_setup_files(repo, repo_name)
        
        self.docker_executor.call_trae_agent(
            repo_name,
            spec["instance_id"], AgentTaskType.ENV_SETUP, [file for file in spec["test_files"] if file.endswith(".py")], spec["created_at"], container
        )

    def _prepare_setup_files(self, repo: str, repo_name: str, spec: Dict[str, Any]):
        # 检查是否已经存在该仓库的配置文件列表
        swap_dir = self.base_path / "swap"
        setup_files_json = swap_dir / "setup_files_list.json"
        operator = ContainerOperator(repo=repo)
        
        if setup_files_json.exists():
            try:
                with setup_files_json.open("r", encoding="utf-8") as f:
                    existing_data = json.load(f)

                if repo.replace("/", "_") in existing_data:
                    operator.checkout_commit(spec["base_commit"], use_docker=False)
                    self.logger.info(f"仓库 {repo} 的配置文件列表已存在，跳过第一阶段")
                    return
            except Exception as e:
                self.logger.warning(f"读取现有配置文件列表时出错: {e}")
        
        operator.repo_clone(use_docker=False)

        operator.checkout_commit(spec["base_commit"], use_docker=False)

        self.logger.info(f"第一阶段：为仓库 {repo} 列出环境配置文件")
        self.local_executor.call_trae_agent( 
            repo_name, 
            spec['instance_id'], AgentTaskType.FILE_LIST
        )
        self._transfer_and_merge_setup_files(repo, repo_name)

    def _transfer_and_merge_setup_files(self, repo: str, repo_name: str):
        """将生成的JSON文件转移到swap目录并按仓库合并"""
        try:
            # 定义文件路径
            base_dir = self.base_path / "swap" / repo_name
            swap_dir = self.base_path / "swap"
            
            # 定义要处理的文件
            files_to_process = [
                "recommended_python_version.json",
                "setup_files_list.json"
            ]
            
            for filename in files_to_process:
                if filename == "recommended_python_version.json":
                    source_file = base_dir / filename
                    target_file = swap_dir / filename
                    if source_file.exists():
                        with source_file.open("r", encoding="utf-8") as f:
                            new_data = f.read().strip()
                    else:
                        self.logger.warning(f"源文件不存在: {source_file}")
                        continue

                    # 合并到json
                    merged_data = {}
                    if target_file.exists():
                        with target_file.open("r", encoding="utf-8") as f:
                            merged_data = json.load(f)
                    merged_data[repo.replace("/", "_")] = new_data
                    with target_file.open("w", encoding="utf-8") as f:
                        json.dump(merged_data, f, indent=2, ensure_ascii=False)
                    self.logger.info(f"已将 {filename} 转移并合并到 {target_file}")
                    source_file.unlink()
                else:
                    source_file = base_dir / filename
                    target_file = swap_dir / filename
                    if source_file.exists():
                        with source_file.open("r", encoding="utf-8") as f:
                            new_data = json.load(f)
                        merged_data = {}
                        if target_file.exists():
                            with target_file.open("r", encoding="utf-8") as f:
                                merged_data = json.load(f)
                        merged_data[repo.replace("/", "_")] = new_data
                        with target_file.open("w", encoding="utf-8") as f:
                            json.dump(merged_data, f, indent=2, ensure_ascii=False)
                        self.logger.info(f"已将 {filename} 转移并合并到 {target_file}")
                        source_file.unlink()
                    else:
                        self.logger.warning(f"源文件不存在: {source_file}")
                    
        except Exception as e:
            self.logger.error(f"转移和合并设置文件时出错: {str(e)}")

    def _restore_setup_files(self, repo: str, repo_name: str):
        """将swap目录中的配置文件恢复到对应的仓库目录"""
        try:
            # 定义文件路径
            base_dir = self.base_path / "swap" / repo_name
            swap_dir = self.base_path / "swap"
            
            # 确保目标目录存在
            base_dir.mkdir(parents=True, exist_ok=True)
            
            # 定义要处理的文件
            files_to_restore = [
                # 只输出json
                "recommended_python_version.json",
                "setup_files_list.json"
            ]
            
            for filename in files_to_restore:
                source_file = swap_dir / filename
                target_file = base_dir / filename
                
                if source_file.exists():
                    with source_file.open("r", encoding="utf-8") as f:
                        merged_data = json.load(f)
                    if repo.replace("/", "_") in merged_data:
                        repo_data = merged_data[repo.replace("/", "_")]
                        with target_file.open("w", encoding="utf-8") as f:
                            json.dump(repo_data, f, indent=2, ensure_ascii=False)
                        self.logger.info(f"已恢复 {filename} 到 {target_file}")
                    else:
                        self.logger.warning(f"在 {filename} 中未找到仓库 {repo} 的数据")
                else:
                    self.logger.warning(f"合并文件不存在: {source_file}")
                    
        except Exception as e:
            self.logger.error(f"恢复设置文件时出错: {str(e)}")

    def _save_test_logs(self, repo_name: str, pre_logs: str, post_logs: str):
        """保存测试日志到 logs/test_logs.json（简化版本）"""
        logs_file = self.base_path / "logs" / "test_logs.json"
        logs_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            existing_logs = {}
            if logs_file.exists():
                with logs_file.open("r", encoding="utf-8") as f:
                    existing_logs = json.load(f)

            existing_logs[repo_name] = {
                "pre_logs": pre_logs,
                "post_logs": post_logs
            }

            with logs_file.open("w", encoding="utf-8") as f:
                json.dump(existing_logs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"保存测试日志失败: {e}")

    def _process_spec(self, container, spec: Dict[str, Any], repo_name: str):
        """处理单个spec"""

        operator = ContainerOperator(repo_name, container)
        operator.checkout_commit(spec["base_commit"], use_docker=False)

        # 应用测试补丁并运行测试
        test_code_before = self._get_test_code(spec, repo_name)
        operator.apply_patches(spec["test_patch"], repo_name)
        test_code_after = self._get_test_code(spec, repo_name)

        test_func = self._get_test_func(test_code_before, test_code_after)
        if all(not changes for changes_dict in test_func for changes in changes_dict.values()):
            self.logger.info(f"跳过 spec {spec['instance_id']} 的测试")
            spec["processed"] = True
            return
        
        p2p_pre_passed, p2p_pre_logs = operator.run_tests_in_container(repo_name, expected_statuses=[TestStatus.PASSED])
        f2p_failed, f2p_pre_logs = operator.run_tests_in_container(repo_name, test_func, [TestStatus.FAILED, TestStatus.ERROR])
        self.logger.info(f"patch前未通过的测试文件: {sorted(f2p_failed)}")
        self.logger.info(f"patch前通过的测试文件: {sorted(p2p_pre_passed)[:5]}")

        # 应用主补丁并运行测试
        operator.apply_patches(spec.get("patch", []), repo_name)
        f2p_passed, f2p_post_logs = operator.run_tests_in_container(repo_name, test_func, [TestStatus.PASSED])
        p2p_post_passed, p2p_post_logs = operator.run_tests_in_container(repo_name, expected_statuses=[TestStatus.PASSED])
        self.logger.info(f"patch后通过的测试文件: {sorted(f2p_passed)}")
        self.logger.info(f"patch后仍通过的测试文件: {sorted(p2p_post_passed)[:5]}")
        
        # 保存测试日志
        self._save_test_logs(repo_name, p2p_pre_logs, p2p_post_logs)

        # 计算结果
        fail_to_pass = f2p_failed & f2p_passed
        pass_to_pass = p2p_pre_passed & p2p_post_passed

        spec["FAIL_TO_PASS"] = ", ".join(sorted(fail_to_pass)) if fail_to_pass else None
        spec["PASS_TO_PASS"] = ", ".join(sorted(pass_to_pass)) if pass_to_pass else None
        spec["processed"] = True

        self.logger.info("=== 测试结果总结 ===")
        self.logger.info(f"仅patch后通过的测试: {spec['FAIL_TO_PASS']}")
        self.logger.info(f"补丁前后均通过的测试: {spec['PASS_TO_PASS']}")
    
    def _get_test_code(self, spec: Dict[str, Any], repo_name: str):
        test_py = []
        for f in spec["test_files"]:
            if f.endswith(".py"):
                try:
                    test_py.append(Path(self.base_path / "swap" / repo_name / f).read_text(encoding="utf-8", errors='replace'))
                except FileNotFoundError:
                    test_py.append("")

        file_names = [f for f in spec["test_files"] if f.endswith(".py")]
        return [{name: text} for name, text in zip(file_names, test_py)]
    
    def _get_test_func(self, code_before: List[Dict[str, Any]], code_after: List[Dict[str, Any]]) -> List[Dict[str, CodeChange]]:
        analyzer = CodeChangeAnalyzer()
        pytest_filter = PytestFilter()
        result = []
        for before, after in zip(code_before, code_after):
            file_name = list(before.keys())[0]
            before_code = before[file_name]
            after_code = after[file_name]
            changes = analyzer.analyze_changes(before_code, after_code)
            pytest_changes = pytest_filter.filter_pytest_changes(changes)
            result.append({file_name: pytest_changes})
        return result

    def run(self):
        """主运行方法"""
        specs_by_repo = self._load_specs()

        for repo, repo_specs in list(specs_by_repo.items()):
            for spec in repo_specs[:self.config.max_specs_per_repo]:
                if not self.test_only:
                    if spec.get("processed", False):
                        self.logger.info(f"跳过已处理的 spec: {spec['instance_id']}")
                        continue
                else:
                    if spec.get("PASS_TO_PASS", None) is not None:
                        continue

                container = None
                repo_name = repo.split('/')[-1]
                
                try:
                    if not self.test_only:
                        self._prepare_setup_files(repo, repo_name, spec)
                        container = self.docker_manager.setup_container_and_environment(repo, spec["instance_id"].split("-")[-1])
                        try:
                            self._setup_repo_environment(container, repo, repo_name, spec)
                            
                            # 保存镜像
                            try:
                                self.docker_manager.cache_manager.save_container_as_image(container)
                                self.logger.info(f"已为仓库 {repo.lower()}#{spec['instance_id'].split('-')[-1]} 保存配置后的镜像")
                            except Exception as save_err:
                                self.logger.error(f"保存仓库 {repo.lower()}#{spec['instance_id'].split('-')[-1]} 镜像失败: {str(save_err)}")

                        except Exception as setup_err:
                            self.logger.error(f"为仓库 {repo.lower()}#{spec['instance_id'].split("-")[-1]} 配置环境时出错: {str(setup_err)}")
                            continue
                    else:
                        container = self.docker_manager.setup_container_and_environment(repo, spec["instance_id"].split("-")[-1])
                    
                    try:
                        self._process_spec(container, spec, repo_name)
                        
                        # 立即保存结果
                        spec["processed"] = True
                        self._save_specs(specs_by_repo)
                        self.logger.info(f"已保存 {spec['instance_id']} 的结果")

                    except Exception as inst_err:
                        self.logger.error(f"处理 {spec['instance_id']} 时出错: {str(inst_err)}")
                        
                except Exception as repo_err:
                    self.logger.error(f"处理仓库 {repo} 时出错: {str(repo_err)}")
                finally:
                    if container is not None and not self.cleanup_in_progress:
                        self.docker_manager.cleanup_container(container, force_remove=True)

        self.logger.info("所有处理完成")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Docker Agent Runner")
    parser.add_argument("--test-only", action="store_true", help="仅执行测试，跳过环境配置与镜像保存")
    args = parser.parse_args()
    runner = DockerAgentRunner(test_only=args.test_only)
    runner.run()

if __name__ == "__main__":
    main()