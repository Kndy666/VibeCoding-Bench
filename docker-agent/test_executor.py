import re
import json
import os
from typing import Set, List, Dict
from datetime import datetime
import docker.models.containers

# 获取当前脚本所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# from trae_agent.tools.bash_tool import _BashSession

def run_tests_in_container(container: docker.models.containers.Container, test_spec: Dict, repo_name: str) -> tuple[Set[str], str]:
    """在容器中运行测试并返回通过的测试文件和日志"""
    test_files = " ".join(test_spec["test_files"])
    cmd = f"cd /workdir/{repo_name} && pytest -rA {test_files}"
    
    exit_code, output = container.exec_run(
        f"/bin/bash -c '{cmd}'",
        environment={"PYTHONUNBUFFERED": "1", "NVIDIA_VISIBLE_DEVICES": "all"}
    )
    logs = output.decode()
    passed_files = parse_pytest_output(logs, test_spec)
    
    return passed_files, logs


def parse_pytest_output(logs: str, test_spec: Dict) -> Set[str]:
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
            if any(file_name.endswith(tf) or tf.endswith(file_name) for tf in test_spec["test_files"]):
                # 首次出现该文件时初始化状态为通过
                if file_name not in file_status:
                    file_status[file_name] = True  # True表示通过
                # 如果出现失败或错误，标记为不通过
                if status in ("FAILED", "ERROR"):
                    file_status[file_name] = False

    # 只返回状态为通过的文件
    return set(file for file, status in file_status.items() if status)

def call_trae_agent(container: docker.models.containers.Container, container_name: str, test_spec: Dict) -> str:
    """在容器内执行trae-agent命令，只输出和记录stderr内容"""
    # _BashSession._timeout = 300.0

    test_files = test_spec["test_files"]
    test_files_str = ", ".join(test_files)

    repo_name = test_spec['repo'].split('/')[-1]
    
    prompt = f"""
Please help me configure the runtime environment for this project.
The project is {repo_name} in the docker container {container_name}.
Python is already installed in the system. You can use python3 to run your tests.
IMPORTANT: Do NOT configure the environment in the virtual environment of the agent.
Instead, use the system Python environment.
Install all dependencies to the system Python, not in any virtual environment.
The test files that need to run successfully are: {test_files_str}.
Please analyze the project structure and README, install necessary dependencies using system pip3,
set up the correct Python environment in the system Python installation,
and ensure all required packages are installed so that the test files can run properly using the system Python3.
If the project itself needs to be installed as a package, use pip3 install -e . to install it in editable mode,
rather than installing other versions from the network.
Focus on resolving any import errors, missing dependencies, or configuration issues.
Use pip3 install (not pip install in virtual environment) to install packages to the system Python.
"""
    
    # 添加配置文件路径参数，指向宿主机映射的配置文件
    config_path = "/workdir/swap/trae_config.yaml"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trajectory_file = f"/workdir/swap/{repo_name}_{timestamp}_trajectory.json"
    
    # 将多行提示转换为单行，避免引号问题
    prompt_escaped = prompt.replace('\n', ' ').strip()
    full_command = f"source .venv/bin/activate && trae-cli run \"{prompt_escaped}\" --working-dir /workdir/{repo_name} --config-file {config_path} --trajectory-file {trajectory_file}"
    print(f"在容器内执行trae-agent命令: {full_command}")
    
    # 打开日志文件（追加模式）
    log_file_path = os.path.join(SCRIPT_DIR, 'log.txt')
    with open(log_file_path, 'a', encoding='utf-8') as log_file:
        # 写入执行记录分隔符
        log_file.write(f"\n{'='*50}\n")
        log_file.write(f"开始执行trae-agent: {full_command}\n")
        log_file.write(f"时间: {os.popen('date').read()}\n")
        log_file.write(f"在容器 {container_name} 内执行\n")
        log_file.write(f"{'-'*50}\n")
        log_file.flush()
        os.fsync(log_file.fileno())
        
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
                    
                    # 写入日志并强制刷新
                    log_file.write(line_str)
                    log_file.flush()
                    os.fsync(log_file.fileno())
                    
                    # 打印到控制台
                    print(line_str, end='', flush=True)
                    
                    # 收集输出
                    output_lines.append(line_str)
            except Exception as e:
                print(f"读取输出时出错: {e}")
                log_file.write(f"读取输出时出错: {e}\n")
            
            # 等待命令完成并获取退出码
            exec_result.output.close() if hasattr(exec_result.output, 'close') else None
            exit_code = exec_result.exit_code
            output_str = ''.join(output_lines)

            print(f"\ntrae-agent执行完成，返回码: {exit_code}")
            log_file.write(f"\n执行完成，返回码: {exit_code}\n")
            log_file.flush()
            os.fsync(log_file.fileno())
            
            if exit_code is not None and exit_code != 0:
                raise RuntimeError(f"trae-agent命令失败，返回码: {exit_code}\n输出: {output_str}")
            
            print("trae-agent执行成功，输出已记录到log.txt")
            return output_str
        
        except Exception as e:
            error_msg = f"\ntrae-agent执行失败: {str(e)}"
            print(error_msg)
            log_file.write(error_msg + "\n")
            log_file.flush()
            os.fsync(log_file.fileno())
            raise RuntimeError(f"trae-agent命令失败: {str(e)}")

def save_results_to_jsonl(test_specs: List[Dict], output_file: str = "passed_tests.jsonl") -> None:
    """保存测试结果到JSONL文件"""
    output_file_path = os.path.join(SCRIPT_DIR, output_file)
    with open(output_file_path, 'w', encoding='utf-8') as f:
        for spec in test_specs:
            json.dump(spec, f, ensure_ascii=False)
            f.write('\n')
    print(f"测试结果已保存到 {output_file_path}")