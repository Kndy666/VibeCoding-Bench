import json
import argparse
from typing import Dict, List, Any, Union

def merge_processed_fields(target_item: Dict[str, Any], source_item: Dict[str, Any]) -> Dict[str, Any]:
    """
    合并已处理项目的特定字段
    
    Args:
        target_item: 目标项目（基础项目）
        source_item: 源项目（要合并的项目）
    
    Returns:
        合并后的项目
    """
    # 需要合并的字段列表
    merge_fields = ['post_passed', 'pre_passed', 'processed', 'FAIL_TO_PASS', 'PASS_TO_PASS']
    
    # 复制目标项目作为基础
    merged_item = target_item.copy()
    
    # 合并指定字段
    for field in merge_fields:
        if field in source_item:
            if field in ['post_passed', 'pre_passed']:
                # 对于列表字段，进行列表合并去重
                target_list = merged_item.get(field, [])
                source_list = source_item.get(field, [])
                if isinstance(target_list, list) and isinstance(source_list, list):
                    merged_item[field] = list(set(target_list + source_list))
                else:
                    merged_item[field] = source_item[field]
            else:
                # 对于其他字段，直接覆盖
                merged_item[field] = source_item[field]
    
    return merged_item

def find_item_by_instance_id(items: List[Dict[str, Any]], instance_id: str) -> Dict[str, Any]:
    """
    根据instance_id查找项目
    
    Args:
        items: 项目列表
        instance_id: 实例ID
    
    Returns:
        找到的项目，如果没找到返回None
    """
    for item in items:
        if item.get('instance_id') == instance_id:
            return item
    return None

def merge_json_files(target_file: str, source_file: str, output_file: str = None) -> List[Dict[str, Any]]:
    """
    合并两个JSON文件
    
    Args:
        target_file: 目标JSON文件路径
        source_file: 源JSON文件路径
        output_file: 输出文件路径（可选）
    
    Returns:
        合并后的数据列表
    """
    # 读取目标文件
    try:
        with open(target_file, 'r', encoding='utf-8') as f:
            target_data = json.load(f)
    except FileNotFoundError:
        print(f"错误：找不到目标文件 {target_file}")
        return []
    except json.JSONDecodeError as e:
        print(f"错误：目标文件JSON格式错误 - {e}")
        return []
    
    # 读取源文件
    try:
        with open(source_file, 'r', encoding='utf-8') as f:
            source_data = json.load(f)
    except FileNotFoundError:
        print(f"错误：找不到源文件 {source_file}")
        return []
    except json.JSONDecodeError as e:
        print(f"错误：源文件JSON格式错误 - {e}")
        return []
    
    # 确保数据是列表格式
    if not isinstance(target_data, list) or not isinstance(source_data, list):
        print("错误：JSON文件应该包含项目数组")
        return []
    
    # 创建结果列表，从目标数据开始
    result_data = []
    processed_instance_ids = set()
    
    # 处理目标数据中的每个项目
    for target_item in target_data:
        instance_id = target_item.get('instance_id')
        if not instance_id:
            print("警告：发现没有instance_id的项目，跳过")
            continue
        
        # 在源数据中查找对应项目
        source_item = find_item_by_instance_id(source_data, instance_id)
        
        if source_item:
            # 检查源项目是否已处理
            if source_item.get('processed') is True:
                # 合并处理过的项目
                merged_item = merge_processed_fields(target_item, source_item)
                result_data.append(merged_item)
                print(f"已合并处理过的项目：{instance_id}")
            else:
                # 直接使用源项目（复制）
                result_data.append(target_item.copy())
                print(f"已复制未处理的项目：{instance_id}")
        else:
            # 源数据中没有对应项目，保留目标项目
            result_data.append(target_item.copy())
            print(f"保留目标项目（源中未找到）：{instance_id}")
        
        processed_instance_ids.add(instance_id)
    
    # 添加源数据中目标数据没有的项目
    for source_item in source_data:
        instance_id = source_item.get('instance_id')
        if instance_id and instance_id not in processed_instance_ids:
            result_data.append(source_item.copy())
            print(f"添加源文件中的新项目：{instance_id}")
    
    # 输出结果
    if output_file:
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result_data, f, indent=2, ensure_ascii=False)
            print(f"合并结果已保存到：{output_file}")
        except Exception as e:
            print(f"错误：无法保存到输出文件 - {e}")
    
    return result_data

def main():
    """
    主函数，处理命令行参数
    """
    parser = argparse.ArgumentParser(description='合并两个JSON文件，根据processed字段决定合并策略')
    parser.add_argument('target_file', help='目标JSON文件路径')
    parser.add_argument('source_file', help='源JSON文件路径（待合并文件）')
    parser.add_argument('-o', '--output', help='输出文件路径（默认为merged_result.json）', 
                       default='merged_result.json')
    parser.add_argument('--dry-run', action='store_true', help='只显示会合并什么，不实际保存文件')
    
    args = parser.parse_args()
    
    # 执行合并
    if args.dry_run:
        print("=== 干运行模式：只显示合并信息，不保存文件 ===")
        result = merge_json_files(args.target_file, args.source_file)
        print(f"将会合并 {len(result)} 个项目")
    else:
        result = merge_json_files(args.target_file, args.source_file, args.output)
        print(f"合并完成，总共 {len(result)} 个项目")

if __name__ == "__main__":
    main()
