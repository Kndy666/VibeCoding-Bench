import re
import logging
from typing import Set, List
from datetime import datetime
import docker.models.containers

# 获取logger实例，不重新配置
logger = logging.getLogger(__name__)

def run_tests_in_container(container: docker.models.containers.Container, test_files: List[str], repo_name: str) -> tuple[Set[str], str]:
    """在容器中运行测试并返回通过的测试文件和日志"""
    test_files = " ".join(test_files)
    cmd = f"cd {repo_name} && pytest -rA {test_files}"
    
    exit_code, output = container.exec_run(f"/bin/bash -c '{cmd}'", workdir="/workdir")
    logs = output.decode()
    passed_files = parse_pytest_output(logs, test_files)

    return passed_files, logs

def parse_pytest_output(logs: str, test_files: List[str]) -> Set[str]:
    """解析pytest输出，提取完全通过测试的文件（无失败和错误）"""
    # 存储所有出现过的测试文件及其状态
    file_status = {}
    # 匹配测试结果行中的文件名
    pattern = r"(PASSED|FAILED|ERROR)\s+([\w/]+.py)(?:::|$)"

    for line in logs.split("\n"):
        match = re.search(pattern, line)
        if match:
            status, file_name = match.groups()
            # 验证文件是否在测试列表中
            if any(file_name.endswith(tf) or tf.endswith(file_name) for tf in test_files):
                # 首次出现该文件时初始化状态为通过
                if file_name not in file_status:
                    file_status[file_name] = True  # True表示通过
                # 如果出现失败或错误，标记为不通过
                if status in ("FAILED", "ERROR"):
                    file_status[file_name] = False

    # 只返回状态为通过的文件
    return set(file for file, status in file_status.items() if status)

def call_trae_agent(container: docker.models.containers.Container, repo_name: str, test_files: List[str], id: str) -> str:
    """在容器内执行trae-agent命令，只输出和记录stderr内容"""
    # _BashSession._timeout = 300.0

    test_files_str = ", ".join(test_files)

    prompt = f"""
Please help me configure the runtime environment for this project.
The project is {repo_name}. The test files that need to run successfully are: {test_files_str}.
Python is already installed in the system. You can use python3 to run your tests.
Please analyze the project structure and README, install necessary dependencies using system pip3, set up the correct Python environment in the system Python installation, and ensure all required packages are installed so that the test files can run properly using the system Python3.
If the project itself needs to be installed as a package, use 'pip3 install -e'. to install it in editable mode, rather than installing other versions from the network.
Focus on resolving any import errors, missing dependencies, or configuration issues.

IMPORTANT: 
1. This Docker container may have been used to configure environments for the same project at different commits before.
Please first check what packages are already installed in the system Python environment to avoid conflicts.
You can use 'pip3 list' or 'pip3 freeze' to see currently installed packages. 
Be careful about package version conflicts that might break previously working configurations.
2. If you encounter any testing-related errors (such as "collected 0 items", "no tests found", "skip", "INTERNALERROR", 
"TypeError: could not get code object", or other pytest framework internal issues), please ignore these errors and focus 
on environment configuration instead. These errors may indicate that test files don't exist, don't contain valid tests, 
or are pytest framework internal issues. In such cases, abandon trying to fix specific test files and continue with 
dependency installation and environment configuration.
3. Do NOT configure the environment in the virtual environment of the agent.
Instead, use the system Python environment. Install all dependencies to the system Python, not in any virtual environment.
Use 'pip3 install' (not pip install in virtual environment) to install packages to the system Python.
"""

    escaped_prompt = prompt.replace('"', '\\"').replace("'", "'\\''").replace('\n', '\\n')
    
    # 添加配置文件路径参数，指向宿主机映射的配置文件
    config_path = "/workdir/swap/trae_config.yaml"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trajectory_file = f"/workdir/swap/{id}_{timestamp}_trajectory.json"
    
    # 直接将prompt通过echo命令传递给trae-cli
    full_command = f"""source .venv/bin/activate && trae-cli run "{escaped_prompt}" --working-dir /workdir/{repo_name} --config-file {config_path} --trajectory-file {trajectory_file}"""
    
    logger.info(f"在容器内执行trae-agent命令: {full_command}")
    logger.info(f"在容器 {container.name} 内执行")
        
    try:
        # 在容器内执行命令，获取流式输出
        exec_result = container.exec_run(
            f"/bin/bash -c '{full_command}'",
            workdir="/workdir/trae-agent",
            stdout=True,
            stderr=True,
            stream=True,
            tty=True,
        )

        # 处理流式输出
        output_lines = []
        try:
            for line in exec_result.output:
                line_str = line.decode('utf-8', errors='replace')
                
                # 打印到控制台
                print(line_str, end='', flush=True)
                
                # 收集输出
                output_lines.append(line_str)
        except Exception as e:
            logger.error(f"读取输出时出错: {e}")
        
        # 等待命令完成并获取退出码
        exec_result.output.close() if hasattr(exec_result.output, 'close') else None
        exit_code = exec_result.exit_code
        output_str = ''.join(output_lines)

        logger.info(f"trae-agent执行完成，返回码: {exit_code}")
        
        if exit_code is not None and exit_code != 0:
            raise RuntimeError(f"trae-agent命令失败，返回码: {exit_code}\n输出: {output_str}")
        
        logger.info("trae-agent执行成功")
        return output_str
    
    except Exception as e:
        logger.error(f"trae-agent执行失败: {str(e)}")
        raise RuntimeError(f"trae-agent命令失败: {str(e)}")