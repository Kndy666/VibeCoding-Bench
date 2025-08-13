import docker
import re
import signal
import sys
import os
import base64
from typing import List, Tuple, Dict, Optional
import docker.models.containers
import docker.models.images

# 全局变量跟踪当前容器，用于信号处理
_current_container: Optional[docker.models.containers.Container] = None
_current_instance_id: Optional[str] = None

def _signal_handler(signum, frame):
    """处理 Ctrl+C 信号，清理容器"""
    print(f"\n接收到中断信号 {signum}，正在清理资源...")
    if _current_container and _current_instance_id:
        try:
            cleanup_container(_current_container, _current_instance_id)
        except Exception as e:
            print(f"清理容器时出错: {str(e)}")
        sys.exit(0)

def _register_signal_handlers():
    """注册信号处理器"""
    signal.signal(signal.SIGINT, _signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, _signal_handler)  # 终止信号

def check_cached_container(test_spec: Dict) -> Optional[docker.models.containers.Container]:
    """检查是否存在缓存的容器"""
    client = docker.from_env()
    instance_id = test_spec["instance_id"]
    
    try:
        # 查找已存在的容器
        container = client.containers.get(instance_id)
        
        # 检查容器状态
        if container.status == 'running':
            print(f"发现运行中的缓存容器: {instance_id}")
            return container
        elif container.status == 'exited':
            print(f"发现已停止的缓存容器: {instance_id}，正在重启...")
            container.start()
            return container
        else:
            print(f"容器 {instance_id} 状态异常: {container.status}，将重新创建")
            container.remove(force=True)
            return None
            
    except docker.errors.NotFound:
        print(f"未找到缓存容器: {instance_id}")
        return None
    except Exception as e:
        print(f"检查缓存容器时出错: {str(e)}")
        return None

def verify_container_environment(container: docker.models.containers.Container, test_spec: Dict) -> bool:
    """验证容器环境是否正确配置"""
    repo_name = test_spec['repo'].split('/')[-1]
    
    # 检查仓库是否存在且在正确的commit
    cmd = f"cd {repo_name} && git rev-parse HEAD"
    exit_code, output = container.exec_run(f"/bin/bash -c '{cmd}'", workdir="/workdir")
    
    if exit_code != 0:
        print(f"仓库检查失败: {output.decode()}")
        return False
    
    current_commit = output.decode().strip()
    expected_commit = test_spec['base_commit']
    
    if current_commit != expected_commit:
        print(f"容器commit不匹配，当前: {current_commit[:8]}, 期望: {expected_commit[:8]}")
        return False
    
    print(f"容器环境验证通过，commit: {current_commit[:8]}")
    return True

def setup_container_and_environment(test_spec: Dict) -> docker.models.containers.Container:
    """创建Docker容器并配置测试环境（带缓存支持）"""
    global _current_container, _current_instance_id
    
    # 注册信号处理器
    _register_signal_handlers()
    
    # 首先检查是否存在缓存的容器
    cached_container = check_cached_container(test_spec)
    if cached_container:
        # 验证环境是否正确
        if verify_container_environment(cached_container, test_spec):
            print(f"使用缓存容器: {test_spec['instance_id']}")
            _current_container = cached_container
            _current_instance_id = test_spec["instance_id"]
            return cached_container
        else:
            print("缓存容器环境不匹配，将重新创建")
            try:
                cached_container.remove(force=True)
            except Exception as e:
                print(f"删除无效缓存容器时出错: {str(e)}")
    
    print(f"创建新容器: {test_spec['instance_id']}")
    client = docker.from_env()

    # 创建带有GPU支持的容器，挂载trae-agent目录和配置文件
    container = client.containers.run(
        image="codegen_base",
        name=test_spec["instance_id"],
        command="/bin/bash",
        detach=True,
        tty=True,
        runtime="nvidia",  # 启用GPU支持
        network_mode="host",
        device_requests=[{
            'count': -1,
            'capabilities': [['gpu']]
        }],
        volumes={
            os.path.join(os.getcwd(), "swap"): {
                "bind": "/workdir/swap",
                "mode": "rw"
            }
        }
    )

    # 立即注册到全局变量，确保可以被信号处理器清理
    _current_container = container
    _current_instance_id = test_spec["instance_id"]

    print(f"容器 {test_spec['instance_id']} 创建成功（带GPU支持）")
    
    # 构建仓库URL
    repo_url = f"https://hk.gh-proxy.com/https://github.com/{test_spec['repo']}.git"
    
    # 在容器中执行环境配置命令
    repo_name = test_spec['repo'].split('/')[-1]
    commands = [
        f"git clone {repo_url}",
        f"cd {repo_name} && git reset --hard {test_spec['base_commit']}",
    ]
    
    for cmd in commands:
        cmd = cmd.replace("'", "'\\''")
        print(f"执行命令: {cmd}")
        
        # 执行命令并获取流式输出
        exec_result = container.exec_run(
            f"/bin/bash -c '{cmd}'",
            workdir="/workdir",
            stdout=True,
            stderr=True,
            stream=True,
            tty=True
        )

        # 处理流式输出
        output_lines = []
        try:
            for line in exec_result.output:
                line_str = line.decode('utf-8', errors='replace')
                print(line_str, end='', flush=True)  # 实时显示并强制刷新缓冲区
                output_lines.append(line_str)
        except Exception as e:
            print(f"读取输出时出错: {e}")
        
        # 等待命令完成并获取退出码
        exec_result.output.close() if hasattr(exec_result.output, 'close') else None
        exit_code = exec_result.exit_code
        output_str = ''.join(output_lines)

        print(f"\n命令完成，返回码: {exit_code}")
        if exit_code is not None and exit_code != 0:
            import pdb
            pdb.set_trace()
            raise RuntimeError(f"命令执行失败: {cmd}\n错误: {output_str}")
    
    return container

def save_container_as_image(container: docker.models.containers.Container, test_spec: Dict) -> docker.models.images.Image:
    """将容器保存为镜像（保留容器用于后续操作）"""
    image = container.commit(
        tag="latest",
        changes=[
            "WORKDIR /workdir",
            "ENV NVIDIA_VISIBLE_DEVICES=all"
        ]
    )
    print(f"容器已保存为镜像: {test_spec['instance_id']}:latest")
    return image

def apply_patches(container: docker.models.containers.Container, file_changes: List[Dict], repo_name: str) -> List[str]:
    """
    应用文件变更到容器中
    
    Args:
        container: 目标容器
        file_changes: 文件变更列表，每个元素包含filename和patch字段
        repo_name: 仓库名称
    
    Returns:
        被修改的文件路径列表
    """
    modified_files = []
    
    for change in file_changes:
        filename = change.get("filename")
        patch_content = change.get("patch", "")
        
        if not filename or not patch_content:
            continue  # 跳过无效的变更记录
        
        # 构建完整的diff格式内容
        diff_content = (
            f"diff --git a/{filename} b/{filename}\n"
            f"--- a/{filename}\n"
            f"+++ b/{filename}\n"
            f"{patch_content}"
        )
        
        # 1. 将patch内容编码为base64并写入容器内的临时文件
        patch_base64 = base64.b64encode(diff_content.encode('utf-8')).decode('utf-8')
        write_cmd = f"echo '{patch_base64}' | base64 -d > /tmp/patch.tmp"
        exit_code, output = container.exec_run(f"/bin/bash -c '{write_cmd}'")
        if exit_code != 0:
            raise RuntimeError(f"写入patch到临时文件失败: {output.decode()}")
        
        # 2. 应用patch到目标文件（在仓库根目录下执行）
        apply_cmd = f"cd {repo_name} && patch -p1 < /tmp/patch.tmp"
        exit_code, output = container.exec_run(f"/bin/bash -c '{apply_cmd}'", workdir="/workdir")
        output_str = output.decode()
        
        if exit_code != 0:
            raise RuntimeError(f"应用patch到 {filename} 失败: {output_str}")
        
        modified_files.append(filename)
        print(f"成功应用patch到: {filename}")
    
    return modified_files

def revert_patches(container: docker.models.containers.Container, modified_files: List[str], repo_name: str) -> None:
    """撤销所有被patch修改的文件（恢复到git原始状态）"""
    if not modified_files:
        print("没有需要撤销的文件")
        return
    
    # 批量恢复所有修改的文件
    files_str = " ".join(modified_files)
    cmd = f"cd {repo_name} && git checkout -- {files_str}"
    exit_code, output = container.exec_run(f"/bin/bash -c '{cmd}'", workdir="/workdir")
    output_str = output.decode()
    
    if exit_code != 0:
        raise RuntimeError(f"撤销文件修改失败: {output_str}")
    
    print(f"已成功撤销以下文件的修改: {', '.join(modified_files)}")

def cleanup_container(container: docker.models.containers.Container, instance_id: str, force_remove: bool = False) -> None:
    """清理容器资源"""
    global _current_container, _current_instance_id
    
    if container:
        try:
            if force_remove:
                container.stop()
                container.remove()
                print(f"\n容器 {instance_id} 已删除")
            else:
                # 不删除容器，保留作为缓存
                print(f"\n容器 {instance_id} 保留作为缓存（未删除）")
            
            # 清理全局变量
            if _current_container == container:
                _current_container = None
                _current_instance_id = None
                
        except Exception as e:
            print(f"处理容器时出错: {str(e)}")
            container.stop()
            container.remove()
            print(f"\n容器 {instance_id} 已删除")