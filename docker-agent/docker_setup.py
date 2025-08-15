import docker
import os
import base64
import logging
from typing import List, Dict, Optional
import docker.models.containers

# 获取logger实例，不重新配置
logger = logging.getLogger(__name__)

def check_cached_container(repo: str) -> Optional[docker.models.containers.Container]:
    """检查是否存在缓存的容器"""
    client = docker.from_env()
    
    try:
        # 查找已存在的容器
        container = client.containers.get(repo)
        
        # 检查容器状态
        if container.status == 'running':
            logger.info(f"发现运行中的缓存容器: {repo}")
            return container
        elif container.status == 'exited':
            logger.info(f"发现已停止的缓存容器: {repo}，正在重启...")
            container.start()
            return container
        else:
            logger.warning(f"容器 {repo} 状态异常: {container.status}，将重新创建")
            container.remove(force=True)
            return None
            
    except docker.errors.NotFound:
        logger.info(f"未找到缓存容器: {repo}")
        return None
    except Exception as e:
        logger.error(f"检查缓存容器时出错: {str(e)}")
        return None

def setup_container_and_environment(repo: str) -> docker.models.containers.Container:
    """创建Docker容器并配置测试环境（带缓存支持）"""

    # 首先检查是否存在缓存的容器
    cached_container = check_cached_container(repo.replace("/", "_"))
    if cached_container:
        return cached_container

    logger.info(f"创建新容器: {repo.replace('/', '_')}")
    client = docker.from_env()

    # 创建带有GPU支持的容器，挂载trae-agent目录和配置文件
    container = client.containers.run(
        image="codegen_base",
        name=repo.replace("/", "_"),
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

    logger.info(f"容器 {repo.replace('/', '_')} 创建成功（带GPU支持）")

    # 构建仓库URL
    repo_url = f"https://hk.gh-proxy.com/https://github.com/{repo}.git"
    
    # 在容器中执行环境配置命令
    commands = f"git clone {repo_url}"
    
    logger.info(f"执行命令: {commands}")
        
    # 执行命令并获取流式输出
    exec_result = container.exec_run(
        f"/bin/bash -c '{commands}'",
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
            logger.debug(f"命令输出: {line_str.rstrip()}")
            print(line_str, end='', flush=True)  # 保留实时显示
            output_lines.append(line_str)
    except Exception as e:
        logger.error(f"读取输出时出错: {e}")
    
    # 等待命令完成并获取退出码
    exec_result.output.close() if hasattr(exec_result.output, 'close') else None
    exit_code = exec_result.exit_code
    output_str = ''.join(output_lines)

    logger.info(f"命令完成，返回码: {exit_code}")
    if exit_code is not None and exit_code != 0:
        logger.error(f"命令执行失败: {commands}\n错误: {output_str}")
        raise RuntimeError(f"命令执行失败: {commands}\n错误: {output_str}")
    
    return container

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
            logger.error(f"写入patch到临时文件失败: {output.decode()}")
            raise RuntimeError(f"写入patch到临时文件失败: {output.decode()}")
        
        # 2. 应用patch到目标文件（在仓库根目录下执行）
        apply_cmd = f"cd {repo_name} && patch -p1 < /tmp/patch.tmp"
        exit_code, output = container.exec_run(f"/bin/bash -c '{apply_cmd}'", workdir="/workdir")
        output_str = output.decode()
        
        if exit_code != 0:
            logger.error(f"应用patch到 {filename} 失败: {output_str}")
            raise RuntimeError(f"应用patch到 {filename} 失败: {output_str}")
        
        modified_files.append(filename)
        logger.info(f"成功应用patch到: {filename}")
    
    return modified_files

def cleanup_container(container: docker.models.containers.Container, force_remove: bool = False) -> None:
    """清理容器资源"""

    if container:
        try:
            if force_remove:
                container.stop()
                container.remove()
                logger.info(f"容器 {container.name} 已删除")
            else:
                # 不删除容器，保留作为缓存
                logger.info(f"容器 {container.name} 保留作为缓存")
                
        except Exception as e:
            logger.error(f"处理容器时出错: {str(e)}")
            container.stop()
            container.remove()
            logger.info(f"容器 {container.name} 已删除")

def checkout_commit(container: docker.models.containers.Container, repo_name: str, commit_hash: str) -> None:
    """
    在容器中切换到指定的commit（强制切换，丢弃本地更改）
    
    Args:
        container: 目标容器
        repo_name: 仓库名称
        commit_hash: 要切换到的commit哈希值
    """
    logger.info(f"正在强制切换到commit: {commit_hash}")
    
    # 先丢弃所有本地更改，然后强制切换
    commands = [
        f"cd {repo_name} && git reset --hard",  # 重置所有已跟踪文件的更改
        f"cd {repo_name} && git clean -fd",     # 删除未跟踪的文件和目录
        f"cd {repo_name} && git checkout {commit_hash}"  # 切换到指定commit
    ]
    
    for cmd in commands:
        exit_code, output = container.exec_run(f"/bin/bash -c '{cmd}'", workdir="/workdir")
        output_str = output.decode()
        
        if exit_code != 0:
            logger.error(f"执行命令失败: {cmd}\n错误: {output_str}")
            raise RuntimeError(f"执行命令失败: {cmd}\n错误: {output_str}")
        
        logger.info(f"执行成功: {cmd.split('&&')[-1].strip()}")
    
    logger.info(f"成功强制切换到commit: {commit_hash}")
    logger.debug(f"输出: {output_str}")