import json
import os
import docker

# 配置
JSON_PATH = "/home/kndy666/Programming/Agent/data_collect/swebench-live/analysis_results_part2.json"
EXPORT_DIR = "/home/kndy666/Programming/Agent/exported_images"
os.makedirs(EXPORT_DIR, exist_ok=True)

def get_repo_info(instance):
    # 你需要根据实际json结构调整repo和repo_id的获取方式
    repo = instance.get("repo")
    repo_id = instance.get("number")
    return repo, repo_id

def main():
    client = docker.from_env()
    with open(JSON_PATH, "r") as f:
        data = json.load(f)
    
    images_to_export = []
    for inst in data:
        fail_to_pass = inst.get("FAIL_TO_PASS")
        if fail_to_pass is not None:
            repo, repo_id = get_repo_info(inst)
            if not repo or not repo_id:
                continue
            repo_lower = repo.replace("/", "_").lower()
            image_name = f"cached_{repo_lower}:{repo_id}"
            images_to_export.append((image_name, repo, repo_id))
    
    print(f"需要导出的镜像数量: {len(images_to_export)}")
    for image_name, repo, repo_id in images_to_export:
        try:
            image = client.images.get(image_name)
            export_path = os.path.join(EXPORT_DIR, f"{image_name.replace(':', '_')}.tar")
            print(f"正在导出镜像 {image_name} 到 {export_path}")
            with open(export_path, "wb") as f:
                for chunk in image.save(named=True):
                    f.write(chunk)
        except Exception as e:
            print(f"导出镜像 {image_name} 失败: {e}")

if __name__ == "__main__":
    main()
