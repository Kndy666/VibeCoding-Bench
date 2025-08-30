import json
import docker
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_analysis_results(path: Path) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def filter_null_fail_to_pass(samples: list) -> list:
    """返回 FAIL_TO_PASS 为 null 或缺失的样本"""
    return [s for s in samples if s.get("FAIL_TO_PASS") is None]

def build_image_name(repo: str, repo_id: str) -> str:
    """与 docker_setup.py 保持一致"""
    repo_lower = repo.replace("/", "_").lower()
    return f"cached_{repo_lower}:{repo_id}"

def remove_images(images_to_remove: set[str]):
    client = docker.from_env()
    for image_name in images_to_remove:
        try:
            image = client.images.get(image_name)
            client.images.remove(image.id, force=True)
            logger.info(f"已删除镜像: {image_name}")
        except docker.errors.ImageNotFound:
            logger.info(f"镜像不存在，跳过: {image_name}")
        except docker.errors.APIError as e:
            logger.error(f"删除镜像失败 {image_name}: {e}")

def main():
    analysis_path = Path("/home/kndy666/Programming/Agent/data_collect/swebench-live/analysis_results.json")
    samples = load_analysis_results(analysis_path)
    null_samples = filter_null_fail_to_pass(samples)

    images_to_remove = set()
    for sample in null_samples:
        repo = sample.get("repo")
        repo_id = sample.get("number")
        if repo and repo_id:
            images_to_remove.add(build_image_name(repo, repo_id))

    if images_to_remove:
        logger.info(f"准备删除 {len(images_to_remove)} 个镜像")
        remove_images(images_to_remove)
    else:
        logger.info("没有需要删除的镜像")

if __name__ == "__main__":
    main()
 