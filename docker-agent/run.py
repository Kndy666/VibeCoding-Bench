import logging
from docker_setup import (
    setup_container_and_environment,
    apply_patches,
    checkout_commit,
    cleanup_container,
    save_container_image
)
from agent_executor import (
    run_tests_in_container,
    call_trae_agent,
)
from typing import Dict
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, List
import signal
import sys
import os

# 获取当前脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 统一配置所有模块的日志到一个文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(SCRIPT_DIR, 'docker_agent.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

ANALYSIS_FILE = Path("data_collect/output/analysis_results.json")

# 全局变量存储当前活动的容器
active_containers = []

def signal_handler(signum, frame):
    """处理终止信号"""
    logger.info(f"\n收到信号 {signum}，正在清理容器...")
    
    for container in active_containers:
        if container:
            try:
                # 询问用户是否删除容器
                try:
                  response = input(f"\n是否要删除容器 {container.name}? (y/N): ").strip().lower()
                  force_remove = response in ['y', 'yes']
                except (EOFError, KeyboardInterrupt):
                  force_remove = False  # 默认不删除容器
                cleanup_container(container, force_remove=force_remove)
            except Exception as e:
                logger.error(f"清理容器 {container.name} 时出错: {e}")
    sys.exit(0)

def main():
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # 终止信号
    
    # 读取并按 repo 分组
    with ANALYSIS_FILE.open("r", encoding="utf-8") as f:
        specs: List[Dict[str, Any]] = json.load(f)

    specs_by_repo: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for spec in specs:
        repo = spec["repo"]
        specs_by_repo[repo].append(spec)

    # 跟踪已保存镜像的仓库
    saved_repos = set()

    # 每个仓库只配置/启动一次容器
    for repo, repo_specs in specs_by_repo.items():
        container = None
        repo_name = repo.split('/')[-1]
        try:
            container = setup_container_and_environment(repo)
            active_containers.append(container)  # 添加到活动容器列表
            
            for spec in repo_specs[:3]:
                # 检查是否已经处理过
                if spec.get("processed", False):
                    logger.info(f"跳过已处理的 spec: {spec['instance_id']}")
                    continue
                    
                try:
                    checkout_commit(container, repo_name, spec["base_commit"])
                    call_trae_agent(container, repo_name, spec["test_files"], spec["instance_id"])

                    # 在第一次call_trae_agent后保存镜像
                    if repo not in saved_repos:
                        try:
                            save_container_image(container, repo)
                            saved_repos.add(repo)
                            logger.info(f"已为仓库 {repo} 保存配置后的镜像")
                        except Exception as save_err:
                            logger.error(f"保存仓库 {repo} 镜像失败: {str(save_err)}")

                    test_modified_files = apply_patches(container, spec["test_patch"], repo_name)
                    pre_passed, pre_logs = run_tests_in_container(container, spec["test_files"], repo_name)
                    logger.info(f"patch前通过的测试文件: {sorted(pre_passed)}")
                    logger.debug(f"patch前测试日志:\n{pre_logs}")

                    main_modified_files = apply_patches(container, spec.get("patch", []), repo_name)
                    post_passed, post_logs = run_tests_in_container(container, spec["test_files"], repo_name)
                    logger.info(f"patch后通过的测试文件: {sorted(post_passed)}")
                    logger.debug(f"patch后测试日志:\n{post_logs}")

                    pass_to_pass = pre_passed & post_passed  # 前后都通过
                    fail_to_pass = post_passed - pre_passed  # 仅patch后通过

                    spec["PASS_TO_PASS"] = ", ".join(sorted(pass_to_pass)) if pass_to_pass else "None"
                    spec["FAIL_TO_PASS"] = ", ".join(sorted(fail_to_pass)) if fail_to_pass else "None"
                    spec["post_passed"] = list(post_passed)
                    spec["pre_passed"] = list(pre_passed)
                    spec["processed"] = True  # 标记为已处理
                    
                    logger.info("=== 测试结果总结 ===")
                    logger.info(f"前后均通过的测试: {spec['PASS_TO_PASS']}")
                    logger.info(f"仅patch后通过的测试: {spec['FAIL_TO_PASS']}")

                    # 立即写回文件
                    updated_specs = []
                    for all_repo_specs in specs_by_repo.values():
                        updated_specs.extend(all_repo_specs)
                    
                    with ANALYSIS_FILE.open("w", encoding="utf-8") as f:
                        json.dump(updated_specs, f, indent=2, ensure_ascii=False)
                    
                    logger.info(f"已保存 {spec['instance_id']} 的结果到 {ANALYSIS_FILE}")

                except Exception as inst_err:
                    logger.error(f"处理 {spec['instance_id']} 时出错: {str(inst_err)}")
        finally:
            if container is not None:
                # 从活动容器列表中移除
                if container in active_containers:
                    active_containers.remove(container)
                cleanup_container(container, force_remove=True)

    logger.info("所有处理完成")

if __name__ == "__main__":
    main()