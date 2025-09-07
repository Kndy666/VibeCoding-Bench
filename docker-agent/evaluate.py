import logging
import json
import toml
import signal
import sys
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from docker_setup import DockerEnvironmentManager, ContainerOperator, AgentManager
from pytest_output_parse import TestStatus
from patch_analyzer import PatchAnalyzer
from tqdm import tqdm

@dataclass
class AgentConfig:
    """Agent配置类"""
    name: str
    repo_url: str
    branch: str = "main"
    install_command: str = ""
    model: str = ""
    provider: str = ""
    extra_env: Dict[str, str] = None
    
    def __post_init__(self):
        if self.extra_env is None:
            self.extra_env = {}

class AgentEvaluator:
    """Agent评估器"""
    
    def __init__(self, config_path: str = "config.toml"):
        # 先设置基础路径
        self.base_path = Path(__file__).parent
        self.docker_manager = DockerEnvironmentManager()

        # 读取配置文件（支持相对路径）
        cfg_path = Path(config_path)
        if not cfg_path.is_absolute():
            cfg_path = self.base_path / cfg_path
        self._raw_config = toml.load(cfg_path)

        # 将常用配置映射为属性，方便后续访问
        logging_cfg = self._raw_config.get("logging", {})
        self.log_level = logging_cfg.get("level", "INFO")
        self.log_format = logging_cfg.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        self.log_file = logging_cfg.get("log_file", "logs/evaluation.log")

        paths_cfg = self._raw_config.get("paths", {})
        analysis_path = paths_cfg.get("analysis_file", "logs/analysis_results_part1.json")
        self.analysis_file = Path(analysis_path) if isinstance(analysis_path, str) else Path("data_collect/swebench-live/analysis_results_part1.json")

        eval_cfg = self._raw_config.get("evaluation", {})
        self.default_timeout = eval_cfg.get("default_timeout", 1800)
        self.max_instances_per_repo = eval_cfg.get("max_instances_per_repo", 100)

        # 用于追踪当前活跃容器，供信号处理器使用
        self.active_containers: List[Any] = []
         
        self.cleanup_in_progress = False
        
        # 配置日志
        self._setup_logging()
        
        # 注册信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        self.logger = logging.getLogger(__name__)
        
        # 加载agent配置
        self.agents = self._load_agent_configs()

        # 初始化patch分析器
        self.patch_analyzer = PatchAnalyzer()

    def _setup_logging(self):
        """配置日志"""
        logging.basicConfig(
            level=getattr(logging, self.log_level.upper(), logging.INFO),
            format=self.log_format,
            handlers=[
                logging.FileHandler(self.base_path / self.log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )

    def _signal_handler(self, signum, frame):
        """处理终止信号"""
        if self.cleanup_in_progress:
            return
            
        self.cleanup_in_progress = True
        self.logger.info(f"\n收到信号 {signum}，正在清理容器...")
        
        for container in list(self.active_containers):
            if container:
                try:
                    self.docker_manager.cleanup_container(container, force_remove=True)
                    if container in self.active_containers:
                        self.active_containers.remove(container)
                except Exception as e:
                    self.logger.error(f"清理容器时出错: {e}")
        
        sys.exit(0)

    def _load_agent_configs(self) -> List[AgentConfig]:
        """加载agent配置"""
        config_file = self.base_path / "agent_configs.json"
        if not config_file.exists():
            # 创建默认配置文件
            default_configs = [
                {
                    "name": "trae-agent",
                    "repo_url": "https://hk.gh-proxy.com/https://github.com/bytedance/trae-agent.git",
                    "branch": "main",
                    "install_command": "uv sync --all-extras",
                    "model": "deepseek-chat",
                    "provider": "deepseek",
                },
                {
                    "name": "sweagent",
                    "repo_url": "https://github.com/princeton-nlp/SWE-agent.git",
                    "branch": "main", 
                    "install_command": "pip install -e .",
                    "model": "gpt-4o-mini",
                }
            ]
            
            with config_file.open("w", encoding="utf-8") as f:
                json.dump(default_configs, f, indent=2, ensure_ascii=False)
            
        with config_file.open("r", encoding="utf-8") as f:
            configs_data = json.load(f)
            
        return [AgentConfig(**config) for config in configs_data]

    def _load_specs(self) -> Dict[str, List[Dict[str, Any]]]:
        """加载并按仓库分组specs"""
        with self.analysis_file.open("r", encoding="utf-8") as f:
            specs = json.load(f)

        specs_by_repo = defaultdict(list)
        for spec in specs:
            repo = spec["repo"]
            specs_by_repo[repo].append(spec)
        
        return specs_by_repo

    def _save_evaluation_results(self, results: List[Dict[str, Any]]):
        """保存评估结果（追加模式）"""
        results_file = self.base_path / "logs" / "evaluation_results.json"
        results_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 先读取已有内容
        existing_results = []
        if results_file.exists():
            with results_file.open("r", encoding="utf-8") as f:
                try:
                    existing_results = json.load(f)
                except Exception:
                    existing_results = []
        
        # 合并并去重（按instance_id去重）
        all_results = existing_results + results
        seen = set()
        deduped_results = []
        for r in all_results:
            iid = r.get("instance_id")
            if iid and iid not in seen:
                deduped_results.append(r)
                seen.add(iid)
            elif not iid:
                deduped_results.append(r)  # 没有instance_id的也保留
        
        with results_file.open("w", encoding="utf-8") as f:
            json.dump(deduped_results, f, indent=2, ensure_ascii=False)

    def _clean_ansi_codes(self, text: str) -> str:
        """清理ANSI转义码"""
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        return ansi_escape.sub('', text)

    def _parse_agent_log(self, log: str) -> int:
        """
        解析agent日志，提取有用信息
        """
        # 清理ANSI转义码
        clean_log = self._clean_ansi_codes(log)

        execution_summary_start = clean_log.find("Execution Summary")
        summary_section = clean_log[execution_summary_start:]

        # 提取 Total Tokens
        total_tokens = None
        for line in summary_section.split('\n'):
            line = line.strip()
            if line.startswith("│ Total Tokens"):
                # 提取数字
                match = re.search(r'│ Total Tokens\s*│\s*(\d+)', line)
                if match:
                    total_tokens = int(match.group(1))
                    break

        return total_tokens

    def _evaluate_agent_on_spec(self, agent_config: AgentConfig, container, spec: Dict[str, Any], repo_name: str) -> Dict[str, Any]:
        """评估单个agent在特定spec上的表现"""
        self.logger.info(f"开始评估 {agent_config.name} 在 {spec['instance_id']} 上的表现")
        
        operator = ContainerOperator(spec["repo"], container)
        agent_manager = AgentManager(container, agent_config)
        
        try:
            # 设置agent环境
            agent_manager.setup_agent()
            
            # 准备问题环境
            operator.checkout_commit(spec["base_commit"], use_docker=True)
            
            # 运行agent
            agent_success, agent_output = agent_manager.run_agent_on_problem(
                spec["problem_statement"],
                spec["instance_id"],
                repo_name
            )
            
            # 评估结果
            if agent_success:
                operator.checkout_commit(spec["base_commit"], exclude_file=["patch.diff"], use_docker=True)
                patch_application = self._apply_patches(operator, repo_name)
                operator.apply_patches(spec["test_patch"])
                
                # 直接从spec解析测试列表
                f2p_tests, p2p_tests = [], []
                if "FAIL_TO_PASS" in spec:
                    f2p_tests.extend(spec["FAIL_TO_PASS"].split(", "))
                if "PASS_TO_PASS" in spec:
                    p2p_tests.extend(spec["PASS_TO_PASS"].split(", "))
                
                # 运行指定测试
                if f2p_tests:
                    f2p_passed, f2p_logs = operator.run_tests_in_container(
                        repo_name, f2p_tests, [TestStatus.PASSED]
                    )

                operator.checkout_commit(spec["base_commit"], exclude_file=["patch.diff"], use_docker=True)
                patch_application = self._apply_patches(operator, repo_name)
                operator.apply_patches(spec["test_patch"])

                if p2p_tests:
                    p2p_passed, p2p_logs = operator.run_tests_in_container(
                        repo_name, p2p_tests, [TestStatus.PASSED]
                    )
                
                # 检查所有期望测试是否通过
                success_f2p = all(test in f2p_passed for test in f2p_tests)
                success_p2p = all(test in p2p_passed for test in p2p_tests)
                success = success_f2p and success_p2p
                
                evaluation_result = {
                    "agent": agent_config.name,
                    "model": agent_config.model,
                    "instance_id": spec["instance_id"],
                    "success_f2p": success_f2p,
                    "success_p2p": success_p2p,
                    "success": success,
                    "passed_f2p_tests": list(f2p_passed),
                    "passed_p2p_tests": list(p2p_passed),
                    "expected_f2p_tests": f2p_tests,
                    "expected_p2p_tests": p2p_tests,
                    "total_tokens": self._parse_agent_log(agent_output),
                    "patch_application": patch_application,
                }
            else:
                evaluation_result = {
                    "agent": agent_config.name,
                    "model": agent_config.model,
                    "instance_id": spec["instance_id"],
                    "success": False,
                    "error": "Agent failed to generate valid patches"
                }
            
            self.logger.info(f"评估完成: {agent_config.name} on {spec['instance_id']}, 成功: {evaluation_result['success']}")
            return evaluation_result
            
        except Exception as e:
            self.logger.error(f"评估 {agent_config.name} 在 {spec['instance_id']} 时出错: {str(e)}")
            return {
                "agent": agent_config.name,
                "model": agent_config.model,
                "instance_id": spec["instance_id"],
                "success": False,
                "error": str(e)
            }
        
    def _apply_patches(self, operator: ContainerOperator, repo_name: str):
        """在容器内应用补丁 - 完全使用统一的patch分析器"""
        patch_path = self.base_path / "swap" / repo_name / "patch.diff"
        
        try:
            # 直接使用patch分析器的文件应用功能
            workdir = f"/workdir/swap/{repo_name}"
            result = self.patch_analyzer.apply_patch_file_to_container(
                patch_path, operator.docker_executor, workdir, include_test=False, include_source=True
            )
            
            # 记录应用结果和统计信息
            self.logger.info(f"Patch应用结果: 总计{result['total_files_num']}个patch，成功应用{result['applied_files_num']}个")
            self.logger.info(f"应用的文件: {result['applied_files']}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"应用patch时出错: {e}")
            return None
            
    def evaluate(self, agent_names: Optional[List[str]] = None, max_instances: int = 10):
        """主评估方法"""
        specs_by_repo = self._load_specs()
        
        # 读取已处理实例
        results_file = self.base_path / "logs" / "evaluation_results.json"
        processed_instance_ids = set()
        if results_file.exists():
            with results_file.open("r", encoding="utf-8") as f:
                try:
                    prev_results = json.load(f)
                    processed_instance_ids = {r.get("instance_id") for r in prev_results if "instance_id" in r}
                except Exception:
                    processed_instance_ids = set()
        
        # 筛选要评估的agent
        agents_to_evaluate = self.agents
        if agent_names:
            agents_to_evaluate = [agent for agent in self.agents if agent.name in agent_names]
        
        all_results = []
        
        # 用tqdm显示repo进度
        for repo, repo_specs in tqdm(list(specs_by_repo.items()), desc="Repo进度", unit="repo"):
            # 限制每个repo的实例数量
            specs_to_test = repo_specs[:max_instances]
            
            # 用tqdm显示实例进度
            for spec in tqdm(specs_to_test, desc=f"{repo.split('/')[-1]}实例", unit="spec", leave=False):
                if not spec.get("FAIL_TO_PASS"):
                    continue  # 只评估有明确测试目标的实例

                if spec.get("instance_id") in processed_instance_ids:
                    continue

                repo_name = repo.split('/')[-1]
                container = None
                
                try:
                    # 创建容器
                    container = self.docker_manager.setup_container_and_environment(
                        repo, spec["instance_id"].split("-")[-1]
                    )
                    # 记录活跃容器以便信号处理时清理
                    self.active_containers.append(container)
                     
                    # 对每个agent进行评估
                    for agent_config in agents_to_evaluate:
                        result = self._evaluate_agent_on_spec(
                            agent_config, container, spec, repo_name
                        )
                        all_results.append(result)
                        
                        # 立即保存结果
                        self._save_evaluation_results(all_results)
                        
                except Exception as e:
                    self.logger.error(f"处理 {spec['instance_id']} 时出错: {str(e)}")
                finally:
                    if container:
                        try:
                            self.docker_manager.cleanup_container(container, force_remove=True)
                        finally:
                            if container in self.active_containers:
                                self.active_containers.remove(container)

        self.logger.info(f"评估完成，共评估了 {len(all_results)} 个实例")
        return all_results

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent Evaluator")
    parser.add_argument("--agents", default="trae-agent", nargs="+", help="要评估的agent名称列表")
    parser.add_argument("--max-instances", type=int, default=100, help="每个repo最大实例数")
    args = parser.parse_args()
    
    evaluator = AgentEvaluator()
    evaluator.evaluate(agent_names=args.agents, max_instances=args.max_instances)

if __name__ == "__main__":
    main()
