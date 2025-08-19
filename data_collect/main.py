import argparse
import json
import time
from pathlib import Path
from typing import List, Dict
import sys
import toml
from tqdm import tqdm

from release_collector import (
    Repository,
    load_processed_repos,
    get_repositories_to_process,
    process_single_repository,
)
from release_analyzer import analyze_repository_releases, ReleaseAnalysis, load_analysis_cache
from pr_analyzer import enhance_release_analysis_with_pr_details, load_pr_analysis_cache

# --- 配置加载 ---
def load_config():
    """加载配置文件"""
    config_file = Path(__file__).parent / "config.toml"
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = toml.load(f)
        return config
    except Exception as e:
        print(f"❌ 无法加载配置文件: {e}")
        sys.exit(1)

CONFIG = load_config()
OUTPUT_DIR = Path(__file__).parent / CONFIG['common']['output_dir']
FINAL_RESULTS_FILE = OUTPUT_DIR / CONFIG['main']['final_results_file']

def setup_output_directory():
    """创建输出目录"""
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"✅ 输出目录已准备: {OUTPUT_DIR}")

def collect_repositories(use_cache: bool = True, crawl_mode: str = None) -> List[Repository]:
    """收集和处理仓库"""
    print("\n🔍 === 步骤1: 收集仓库信息 ===")
    
    # 获取需要处理的仓库列表和已处理的仓库
    pre_filtered_repos, processed_repos = get_repositories_to_process(use_cache, crawl_mode)
    
    if not pre_filtered_repos:
        print("❌ 没有仓库通过初步筛选")
        # 如果没有新仓库通过筛选，但有已处理的仓库，返回已处理的仓库
        if processed_repos:
            print(f"📂 返回 {len(processed_repos)} 个已处理的仓库")
            return list(processed_repos.values())
        return []
        
    print(f"✅ {len(pre_filtered_repos)} 个仓库通过初步筛选")
    
    # 处理每个仓库
    final_repositories = []
    
    # 先添加已处理的仓库
    if processed_repos:
        final_repositories.extend(processed_repos.values())
        print(f"📂 加载了 {len(processed_repos)} 个已处理的仓库")
    
    # 使用tqdm显示处理进度
    with tqdm(pre_filtered_repos, desc="处理仓库", unit="repo") as pbar:
        for repo in pbar:
            repo_name = repo['full_name']
            pbar.set_description(f"处理: {repo_name}")
            
            try:
                repository = process_single_repository(repo, use_cache)
                final_repositories.append(repository)
                pbar.write(f"  ✅ {repo_name}: 处理完成")
            except Exception as e:
                pbar.write(f"  ❌ {repo_name}: {str(e)}")
                continue
    
    print(f"\n✅ 收集阶段完成，共处理 {len(final_repositories)} 个仓库")
    return final_repositories

def analyze_releases(repositories: List[Repository]) -> List[ReleaseAnalysis]:
    """分析所有仓库的release"""
    print("\n📊 === 步骤2: 分析Release功能 ===")
    
    all_analyses = []
    
    # 使用tqdm显示分析进度
    with tqdm(repositories, desc="分析仓库", unit="repo") as pbar:
        for repository in pbar:
            pbar.set_description(f"分析: {repository.full_name}")
            analyses = analyze_repository_releases(repository)
            all_analyses.extend(analyses)
            
            # 统计当前仓库的分析结果
            total_new_features = sum(len(a.new_features) for a in analyses)
            total_improvements = sum(len(a.improvements) for a in analyses)
            total_bug_fixes = sum(len(a.bug_fixes) for a in analyses)
            
            pbar.write(f"  ✅ {repository.full_name}: 新功能({total_new_features}) 改进({total_improvements}) 修复({total_bug_fixes})")
    
    # 统计总体结果
    total_new_features = sum(len(a.new_features) for a in all_analyses)
    total_improvements = sum(len(a.improvements) for a in all_analyses)
    total_bug_fixes = sum(len(a.bug_fixes) for a in all_analyses)
    
    print(f"\n✅ Release分析完成!")
    print(f"  - 共分析 {len(all_analyses)} 个release")
    print(f"  - 新增功能: {total_new_features}")
    print(f"  - 功能改进: {total_improvements}")
    print(f"  - 漏洞修复: {total_bug_fixes}")
    
    return all_analyses

def enhance_with_pr_analysis(release_analyses: List[ReleaseAnalysis]) -> List[Dict]:
    """增强PR分析"""
    print("\n🔧 === 步骤3: 增强PR详细分析 ===")
    
    enhanced_results = []
    
    # 使用tqdm显示PR分析进度
    with tqdm(release_analyses, desc="分析PR", unit="release") as pbar:
        for analysis in pbar:
            pbar.set_description(f"分析: {analysis.repo_name}-{analysis.tag_name}")
            
            # 只对新功能进行PR详细分析
            enhanced_features = enhance_release_analysis_with_pr_details(analysis)
            
            if enhanced_features:
                result = {
                    'repository': analysis.repo_name,
                    'release': analysis.tag_name,
                    'analyzed_at': analysis.analyzed_at,
                    'enhanced_new_features': [ef.to_dict() for ef in enhanced_features],
                    'original_analysis': analysis.to_dict()
                }
                enhanced_results.append(result)
                pbar.write(f"  ✅ {analysis.repo_name}-{analysis.tag_name}: 分析了 {len(enhanced_features)} 个PR")
            else:
                pbar.write(f"  ⚠️ {analysis.repo_name}-{analysis.tag_name}: 没有可分析的新功能")
    
    print(f"\n✅ PR详细分析完成，共分析 {len(enhanced_results)} 个Release")
    return enhanced_results

def save_final_results(enhanced_results: List[Dict]):
    """保存最终结果"""
    print("\n💾 === 步骤4: 保存最终结果 ===")
    
    final_output = {
        'metadata': {
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_repositories': len(set(r['repository'] for r in enhanced_results)),
            'total_releases': len(enhanced_results),
            'total_enhanced_features': sum(len(r['enhanced_new_features']) for r in enhanced_results)
        },
        'results': enhanced_results
    }
    
    try:
        with open(FINAL_RESULTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_output, f, indent=2, ensure_ascii=False)
        print(f"✅ 最终结果已保存到: {FINAL_RESULTS_FILE}")
        
        # 打印结果摘要
        print(f"\n📈 === 最终统计 ===")
        print(f"  - 分析仓库数: {final_output['metadata']['total_repositories']}")
        print(f"  - 分析release数: {final_output['metadata']['total_releases']}")
        print(f"  - 增强功能数: {final_output['metadata']['total_enhanced_features']}")
        
    except Exception as e:
        print(f"❌ 保存结果失败: {e}")

def print_sample_results(enhanced_results: List[Dict], limit: int = 5):
    """打印示例结果"""
    # 如果没有指定限制，使用配置文件中的默认值
    if limit is None:
        limit = CONFIG['main']['sample_results_limit']
        
    print(f"\n🎯 === 示例结果预览 (前{limit}个) ===")
    
    for i, result in enumerate(enhanced_results[:limit]):
        print(f"\n--- 示例 {i+1}: {result['repository']} - {result['release']} ---")
        
        enhanced_features = result['enhanced_new_features']
        for j, feature in enumerate(enhanced_features[:2]):  # 每个release只显示前2个功能
            print(f"  功能 {j+1}: {feature['description'][:100]}...")
            if feature['pr_analyses']:
                pr_count = len(feature['pr_analyses'])
                print(f"    - 关联PR数: {pr_count}")
                print(f"    - 详细描述: {feature['feature_detailed_description'][:150]}...")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='GitHub仓库release和PR分析工具')
    parser.add_argument('--no-cache', action='store_true',
                       help='不使用缓存，重新处理所有数据')
    parser.add_argument('--collect-only', action='store_true',
                       help='只执行仓库收集，不进行后续分析')
    parser.add_argument('--analyze-only', action='store_true',
                       help='只执行release分析，跳过仓库收集')
    parser.add_argument('--enhance-only', action='store_true',
                       help='只执行PR增强分析，跳过前面步骤')
    parser.add_argument('--crawl-mode', choices=['stars', 'specified'], default='specified',
                       help='选择爬取模式: stars(按star数筛选) 或 specified(使用指定仓库列表)')
    
    args = parser.parse_args()
    
    print("🚀 开始GitHub仓库分析流程")
    print("=" * 50)
    
    # 设置输出目录
    setup_output_directory()
    
    use_cache = not args.no_cache
    enhanced_results = []
    
    try:
        if not args.analyze_only and not args.enhance_only:
            # 步骤1: 收集仓库
            repositories = collect_repositories(use_cache=use_cache, crawl_mode=args.crawl_mode)
            
            if not repositories:
                print("❌ 没有收集到有效仓库，程序结束")
                return
                
            if args.collect_only:
                print(f"✅ 仅收集模式完成，共收集 {len(repositories)} 个仓库")
                return
        else:
            # 从缓存加载仓库数据
            processed_repos = load_processed_repos()
            repositories = list(processed_repos.values())
            print(f"📂 从缓存加载了 {len(repositories)} 个仓库")

        if not args.enhance_only:
            # 步骤2: 分析release
            release_analyses = analyze_releases(repositories)
            
            if not release_analyses:
                print("❌ 没有分析到有效release，程序结束")
                return
                
            if args.analyze_only:
                print("✅ 仅分析模式完成")
                return
        else:
            cached_analyses = load_analysis_cache()
            release_analyses = list(cached_analyses.values())
            print(f"📂 从缓存加载了 {len(release_analyses)} 个release分析")
        
        # 步骤3: 增强PR分析
        cached_pr_analysis = load_pr_analysis_cache()
        pr_analysis = list(cached_pr_analysis.values())
        print(f"📂 从缓存加载了 {len(pr_analysis)} 个PR分析")

        enhanced_results = enhance_with_pr_analysis(release_analyses)

        if enhanced_results:
            save_final_results(enhanced_results)
            print_sample_results(enhanced_results)
        
        print(f"\n🎉 完整分析流程完成！")
        
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断程序")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 程序执行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()