import json
from typing import List, Dict

def process_entry(entry: Dict) -> List[Dict]:
    """处理单个原始条目，提取并转换为目标格式列表"""
    processed = []
    repo = entry.get("repository")
    version = entry.get("release")
    
    # 处理每个enhanced_new_features
    for feature in entry.get("enhanced_new_features", []):
        # 处理每个pr_analyses
        for pr in feature.get("pr_analyses", []):
            # 提取基础信息
            pr_number = pr.get("pr_number")
            base_commit = pr.get("base_commit", {}).get("sha", "")
            created_at = pr.get("base_commit", {}).get("date", "")
            detailed_desc = pr.get("detailed_description", "")
            
            # 提取组织名（repo的前半部分）
            org = repo.split("/")[0] if "/" in repo else repo
            
            # 生成instance_id
            instance_id = f"{repo.replace('/', '__')}-{pr_number}"
            
            # 获取所有文件变更记录
            all_file_changes = pr.get("file_changes", [])
            
            # 获取test_files列表并提取对应的变更
            test_file_names = pr.get("test_files", [])
            test_changes = [
                fc for fc in all_file_changes 
                if fc.get("filename") in test_file_names
            ]

            # 获取non_test_files列表并提取对应的变更
            # non_test_file_names = pr.get("non_test_files", [])
            non_test_changes = [
                fc for fc in all_file_changes 
                if fc.get("filename") not in test_file_names
            ]
            
            # 构建目标格式字典，直接保存文件变更列表
            processed_item = {
                "repo": repo,
                "instance_id": instance_id,
                "base_commit": base_commit,
                "patch": non_test_changes,
                "test_patch": test_changes,
                "problem_statement": detailed_desc,
                "hints_text": "",
                "created_at": created_at,
                "version": version,
                "org": org,
                "number": int(pr_number) if pr_number else 0,
                "PASS_TO_PASS": "",
                "FAIL_TO_PASS": "",
                "test_files": test_file_names
            }
            processed.append(processed_item)
    
    return processed


def main(input_path: str, output_path: str):
    """主函数：读取输入JSON，处理后写入输出文件"""
    # 读取原始JSON
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 处理所有条目
    all_processed = []
    for entry in data.get("results", []):
        processed = process_entry(entry)
        all_processed.extend(processed)
    
    # 按 instance_id 去重，保留最后一条
    dedup_map = {item["instance_id"]: item for item in all_processed}
    deduped = list(dedup_map.values())
    
    # 写入处理结果
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, indent=2, ensure_ascii=False)
    
    print(f"处理完成，共生成 {len(deduped)} 条记录（已去重），已保存至 {output_path}")


if __name__ == "__main__":
    # 示例用法（可根据实际路径修改）
    input_json_path = "data_collect/swebench-live/final_analysis_results.json"   # 输入JSON文件路径
    output_json_path = "data_collect/swebench-live/analysis_results.json" # 输出结果路径
    main(input_json_path, output_json_path)