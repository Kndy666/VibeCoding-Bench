import json
import time
import toml
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
import openai
from pathlib import Path
from tqdm import tqdm

# --- 配置加载 ---
def load_config():
    """加载配置文件"""
    config_file = Path(__file__).parent / "config.toml"
    with open(config_file, 'r', encoding='utf-8') as f:
        return toml.load(f)

CONFIG = load_config()

# --- 配置区 ---
OPENAI_API_KEY = CONFIG['common']['openai_api_key']
OPENAI_MODEL = CONFIG['common']['openai_model']

# 缓存文件
ANALYSIS_CACHE_FILE = Path(__file__).parent / CONFIG['common']['output_dir'] / CONFIG['release_analyzer']['analysis_cache_file']

# --- 数据类定义 ---

@dataclass
class FeatureAnalysis:
    """表示一个功能分析结果"""
    feature_type: str  # 'new_feature', 'improvement', 'bug_fix', 'other'
    description: str
    pr_links: List[str]  # 相关的PR链接
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'FeatureAnalysis':
        return cls(**data)

@dataclass
class ReleaseAnalysis:
    """表示一个 release 的分析结果"""
    tag_name: str
    repo_name: str
    new_features: List[FeatureAnalysis]
    improvements: List[FeatureAnalysis]
    bug_fixes: List[FeatureAnalysis]
    other_changes: List[FeatureAnalysis]
    processed_body: str  # 处理过PR链接的body
    analyzed_at: str
    
    def to_dict(self) -> Dict:
        return {
            'tag_name': self.tag_name,
            'repo_name': self.repo_name,
            'new_features': [f.to_dict() for f in self.new_features],
            'improvements': [f.to_dict() for f in self.improvements],
            'bug_fixes': [f.to_dict() for f in self.bug_fixes],
            'other_changes': [f.to_dict() for f in self.other_changes],
            'processed_body': self.processed_body,
            'analyzed_at': self.analyzed_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ReleaseAnalysis':
        return cls(
            tag_name=data['tag_name'],
            repo_name=data['repo_name'],
            new_features=[FeatureAnalysis.from_dict(f) for f in data.get('new_features', [])],
            improvements=[FeatureAnalysis.from_dict(f) for f in data.get('improvements', [])],
            bug_fixes=[FeatureAnalysis.from_dict(f) for f in data.get('bug_fixes', [])],
            other_changes=[FeatureAnalysis.from_dict(f) for f in data.get('other_changes', [])],
            processed_body=data.get('processed_body', ''),
            analyzed_at=data.get('analyzed_at', '')
        )

# --- 缓存管理 ---

def load_analysis_cache() -> Dict[str, ReleaseAnalysis]:
    """加载分析缓存"""
    if ANALYSIS_CACHE_FILE.exists():
        try:
            with open(ANALYSIS_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cache = {}
                for key, analysis_data in data.items():
                    cache[key] = ReleaseAnalysis.from_dict(analysis_data)
                print(f"✅ 从缓存加载了 {len(cache)} 个release分析结果")
                return cache
        except Exception as e:
            print(f"⚠️ 加载分析缓存失败: {e}")
            return {}
    return {}

def save_analysis_to_cache(analysis: ReleaseAnalysis):
    """保存分析结果到缓存"""
    cache = {}
    if ANALYSIS_CACHE_FILE.exists():
        try:
            with open(ANALYSIS_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except:
            pass
    
    cache_key = f"{analysis.repo_name}#{analysis.tag_name}"
    cache[cache_key] = analysis.to_dict()
    
    try:
        with open(ANALYSIS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        print(f"💾 已保存 {cache_key} 的分析结果到缓存")
    except Exception as e:
        print(f"⚠️ 保存分析缓存失败: {e}")

# --- LLM 分析 ---

def analyze_release_with_llm(release_body: str, tag_name: str, repo_readme: str = "") -> Dict[str, List[Dict]]:
    """使用 LLM 分析 release body 中的功能变更和PR链接"""
    
    client = openai.OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.deepseek.com")
    
    # 构建包含README上下文的prompt
    readme_context = ""
    if repo_readme.strip():
        # 从配置文件读取参数
        max_readme_length = CONFIG['release_analyzer']['max_readme_length']
        truncation_suffix = CONFIG['release_analyzer']['readme_truncation_suffix']
        
        # 截取README，避免prompt过长
        readme_excerpt = repo_readme[:max_readme_length]
        if len(repo_readme) > max_readme_length:
            readme_excerpt += truncation_suffix
        readme_context = f"""
Repository Context (README):
{readme_excerpt}

---
"""
    
    prompt = f"""
{readme_context}Analyze the following software release notes and categorize the changes into: new_features, improvements, bug_fixes, and other_changes.
For each change, extract any PR references (like #123, PR456, pull #789, etc.) mentioned in the text.

Release version: {tag_name}
Release notes:
{release_body}

Guidelines:
1. new_features: Brand new functionality, commands, rules, or capabilities
2. improvements: Enhancements to existing features, optimizations, performance improvements
3. bug_fixes: Bug fixes, error handling, crash fixes
4. other_changes: Documentation updates, dependency updates, refactoring (only if significant)
5. Extract PR numbers from various formats: #123, PR #456, pull 789, (#101), etc.
6. Only include PR numbers that are explicitly mentioned with the change
7. Ignore trivial changes like version bumps unless they're part of larger features
8. Use the repository context to better understand the project's domain and categorize changes more accurately

Return the result in JSON format:
{{
    "new_features": [
        {{
            "description": "Brief description of the new feature",
            "pr_ids": ["123", "456"]
        }}
    ],
    "improvements": [
        {{
            "description": "Brief description of the improvement", 
            "pr_ids": ["789"]
        }}
    ],
    "bug_fixes": [
        {{
            "description": "Brief description of the bug fix",
            "pr_ids": ["101"]
        }}
    ],
    "other_changes": [
        {{
            "description": "Brief description of other changes",
            "pr_ids": []
        }}
    ]
}}
"""

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a software development expert who specializes in analyzing release notes and categorizing software changes."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0
        )
        
        content = response.choices[0].message.content
        if content is None:
            print("⚠️ LLM返回的内容为空")
            return {"new_features": [], "improvements": [], "bug_fixes": [], "other_changes": []}
        result = json.loads(content)
        return result
            
    except Exception as e:
        print(f"⚠️ LLM分析失败: {e}")
        return {"new_features": [], "improvements": [], "bug_fixes": [], "other_changes": []}

# --- 主要功能函数 ---

def analyze_release(release, repo_name: str, repo_readme: str = "", use_cache: bool = True) -> Optional[ReleaseAnalysis]:
    """分析单个 release"""
    cache_key = f"{repo_name}#{release.tag_name}"
    
    # 检查缓存
    if use_cache:
        cache = load_analysis_cache()
        if cache_key in cache:
            print(f"  > 🔄 从缓存加载 {release.tag_name} 的分析结果")
            return cache[cache_key]
    
    print(f"  > 🔍 正在分析 {release.tag_name} 的release...")
    
    # LLM 分析，传入README内容
    llm_result = analyze_release_with_llm(release.body, release.tag_name, repo_readme)
    
    # 转换为 FeatureAnalysis 对象
    def convert_to_feature_analysis(items: List[Dict], feature_type: str) -> List[FeatureAnalysis]:
        features = []
        for item in items:
            pr_links = []
            # 从LLM结果中获取PR ID并转换为完整链接
            if 'pr_ids' in item:
                for pr_id in item['pr_ids']:
                    pr_links.append(f"https://github.com/{repo_name}/pull/{pr_id}")
            
            features.append(FeatureAnalysis(
                feature_type=feature_type,
                description=item.get('description', ''),
                pr_links=pr_links
            ))
        return features
    
    analysis = ReleaseAnalysis(
        tag_name=release.tag_name,
        repo_name=repo_name,
        new_features=convert_to_feature_analysis(llm_result.get('new_features', []), 'new_feature'),
        improvements=convert_to_feature_analysis(llm_result.get('improvements', []), 'improvement'),
        bug_fixes=convert_to_feature_analysis(llm_result.get('bug_fixes', []), 'bug_fix'),
        other_changes=convert_to_feature_analysis(llm_result.get('other_changes', []), 'other'),
        processed_body=release.body,
        analyzed_at=time.strftime('%Y-%m-%d %H:%M:%S')
    )
    
    # 保存到缓存
    if use_cache:
        save_analysis_to_cache(analysis)
    
    return analysis

def analyze_repository_releases(repository) -> List[ReleaseAnalysis]:
    """分析仓库的所有 major releases"""
    print(f"--- 开始分析仓库 {repository.full_name} 的release功能 ---")
    
    analyses = []
    
    # 使用tqdm显示分析进度
    with tqdm(repository.major_releases, desc=f"分析{repository.full_name}", unit="release") as pbar:
        for release in pbar:
            pbar.set_description(f"分析: {release.tag_name}")
            
            # 传入README内容到分析函数
            analysis = analyze_release(release, repository.full_name, repository.readme_content)
            if analysis:
                analyses.append(analysis)
                # 显示分析结果摘要
                new_features_count = len(analysis.new_features)
                improvements_count = len(analysis.improvements)
                bug_fixes_count = len(analysis.bug_fixes)
                pbar.write(f"    ✅ {release.tag_name}: 新功能({new_features_count}) 改进({improvements_count}) 修复({bug_fixes_count})")
            
            # 避免API速率限制
            time.sleep(1)
    
    return analyses