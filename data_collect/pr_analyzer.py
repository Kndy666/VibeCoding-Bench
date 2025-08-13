import ast
import re
import json
import time
import toml
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, asdict
import openai
from pathlib import Path
import requests
import base64
from utils import is_test_file
from tqdm import tqdm

# --- 配置加载 ---
def load_config():
    """加载配置文件"""
    config_file = Path(__file__).parent / "config.toml"
    with open(config_file, 'r', encoding='utf-8') as f:
        return toml.load(f)

CONFIG = load_config()

# --- 配置区 ---
GITHUB_TOKEN = CONFIG['common']['github_token']
OPENAI_API_KEY = CONFIG['common']['openai_api_key']
OPENAI_MODEL = CONFIG['common']['openai_model']

# 缓存文件
PR_ANALYSIS_CACHE_FILE = Path(__file__).parent / CONFIG['common']['output_dir'] / CONFIG['pr_analyzer']['pr_analysis_cache_file']

# GitHub API 基础URL
GITHUB_API_BASE = CONFIG['common']['github_api_base']

HEADERS = {
    'Authorization': f'token {GITHUB_TOKEN}',
    'Accept': 'application/vnd.github.v3+json'
}

# --- 数据类定义 ---

@dataclass
class TestFile:
    """表示一个测试文件"""
    path: str
    content: str
    size: int
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'TestFile':
        return cls(**data)

@dataclass
class FileChange:
    """表示一个文件的变更信息"""
    filename: str
    status: str  # 'added', 'removed', 'modified', 'renamed'
    additions: int
    deletions: int
    changes: int
    patch: Optional[str] = None  # diff 内容
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'FileChange':
        return cls(**data)

@dataclass
class Commit:
    """表示一个Git提交"""
    sha: str
    message: str
    date: str
    author: str
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Commit':
        return cls(**data)

@dataclass
class PRAnalysis:
    """表示一个 PR 的详细分析结果"""
    pr_number: str
    repo_name: str
    title: str
    description: str
    state: str  # 'open', 'closed', 'merged'
    merged: bool
    base_commit: Commit  # PR前的commit信息
    head_commit: Commit  # PR后的commit信息
    file_changes: List[FileChange]
    detailed_description: str  # LLM 基于文件变更生成的详细描述
    has_tests: bool  # 是否找到相关测试
    test_files: List[str]  # 测试文件路径列表
    only_modified_existing_functions: bool # 是否只修改了已有函数
    non_test_files: List[str] # 非测试文件路径列表
    analyzed_at: str
    
    def to_dict(self) -> Dict:
        return {
            'pr_number': self.pr_number,
            'repo_name': self.repo_name,
            'title': self.title,
            'description': self.description,
            'state': self.state,
            'merged': self.merged,
            'base_commit': self.base_commit.to_dict(),
            'head_commit': self.head_commit.to_dict(),
            'file_changes': [fc.to_dict() for fc in self.file_changes],
            'detailed_description': self.detailed_description,
            'has_tests': self.has_tests,
            'test_files': self.test_files,
            'only_modified_existing_functions': self.only_modified_existing_functions,
            'non_test_files': self.non_test_files or [],
            'analyzed_at': self.analyzed_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'PRAnalysis':
        return cls(
            pr_number=data['pr_number'],
            repo_name=data['repo_name'],
            title=data['title'],
            description=data['description'],
            state=data['state'],
            merged=data['merged'],
            base_commit=Commit.from_dict(data.get('base_commit', {})),
            head_commit=Commit.from_dict(data.get('head_commit', {})),
            file_changes=[FileChange.from_dict(fc) for fc in data.get('file_changes', [])],
            detailed_description=data.get('detailed_description', ''),
            has_tests=data.get('has_tests', False),
            test_files=data.get('test_files', []),
            only_modified_existing_functions=data.get('only_modified_existing_functions', True),
            non_test_files=data.get('non_test_files', []),
            analyzed_at=data.get('analyzed_at', '')
        )

@dataclass
class EnhancedFeature:
    """增强的功能对象，包含 PR 详细分析"""
    feature_type: str
    description: str
    pr_analyses: List[PRAnalysis]
    feature_detailed_description: str  # 基于所有PR分析的整体详细描述
    
    def to_dict(self) -> Dict:
        return {
            'feature_type': self.feature_type,
            'description': self.description,
            'pr_analyses': [pr.to_dict() for pr in self.pr_analyses],
            'feature_detailed_description': self.feature_detailed_description
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'EnhancedFeature':
        return cls(
            feature_type=data['feature_type'],
            description=data['description'],
            pr_analyses=[PRAnalysis.from_dict(pr) for pr in data.get('pr_analyses', [])],
            feature_detailed_description=data.get('feature_detailed_description', '')
        )

# --- 缓存管理 ---

def load_pr_analysis_cache() -> Dict[str, PRAnalysis]:
    """加载 PR 分析缓存"""
    if PR_ANALYSIS_CACHE_FILE.exists():
        try:
            with open(PR_ANALYSIS_CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cache = {}
                for key, pr_data in data.items():
                    cache[key] = PRAnalysis.from_dict(pr_data)
                print(f"✅ 从缓存加载了 {len(cache)} 个PR分析结果")
                return cache
        except Exception as e:
            print(f"⚠️ 加载PR分析缓存失败: {e}")
            return {}
    return {}

def save_pr_analysis_to_cache(analysis: PRAnalysis):
    """保存 PR 分析结果到缓存"""
    cache = {}
    if PR_ANALYSIS_CACHE_FILE.exists():
        try:
            with open(PR_ANALYSIS_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except:
            pass
    
    cache_key = f"{analysis.repo_name}#{analysis.pr_number}"
    cache[cache_key] = analysis.to_dict()
    
    try:
        with open(PR_ANALYSIS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        print(f"💾 已保存 PR#{analysis.pr_number} 的分析结果到缓存")
    except Exception as e:
        print(f"⚠️ 保存PR分析缓存失败: {e}")

# --- GitHub API 函数 ---

def extract_pr_number_from_url(pr_url: str) -> Optional[str]:
    """从 PR URL 中提取 PR 编号"""
    match = re.search(r'/pull/(\d+)', pr_url)
    return match.group(1) if match else None

def get_pr_info(repo_name: str, pr_number: str) -> Optional[Dict]:
    """获取 PR 基本信息"""
    url = f"{GITHUB_API_BASE}/repos/{repo_name}/pulls/{pr_number}"
    
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"⚠️ 获取PR#{pr_number}信息失败: {response.status_code}")
            return None
    except Exception as e:
        print(f"⚠️ 获取PR#{pr_number}信息异常: {e}")
        return None

def get_pr_files(repo_name: str, pr_number: str) -> List[FileChange]:
    """获取 PR 的文件变更信息"""
    url = f"{GITHUB_API_BASE}/repos/{repo_name}/pulls/{pr_number}/files"
    
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            files_data = response.json()
            file_changes = []
            
            for file_data in files_data:
                file_change = FileChange(
                    filename=file_data.get('filename', ''),
                    status=file_data.get('status', ''),
                    additions=file_data.get('additions', 0),
                    deletions=file_data.get('deletions', 0),
                    changes=file_data.get('changes', 0),
                    patch=file_data.get('patch', '')
                )
                file_changes.append(file_change)
            
            return file_changes
        else:
            print(f"⚠️ 获取PR#{pr_number}文件变更失败: {response.status_code}")
            return []
    except Exception as e:
        print(f"⚠️ 获取PR#{pr_number}文件变更异常: {e}")
        return []

def get_file_content(repo_name: str, file_path: str, ref: str) -> Optional[str]:
    """获取文件内容"""
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_name}/contents/{file_path}"
        time.sleep(0.3)
        response = requests.get(url, headers=HEADERS, params={'ref': ref})
        
        if response.status_code == 200:
            content_data = response.json()
            if content_data.get('encoding') == 'base64':
                content = base64.b64decode(content_data['content']).decode('utf-8', errors='ignore')
                return content
    except Exception as e:
        print(f"    - 获取文件内容失败 {file_path}: {e}")
    
    return None

def get_commit_info(repo_name: str, commit_sha: str) -> Optional[Commit]:
    """获取单个提交的详细信息"""
    url = f"{GITHUB_API_BASE}/repos/{repo_name}/commits/{commit_sha}"
    
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            commit_data = response.json()
            return Commit(
                sha=commit_data.get('sha', ''),
                message=commit_data.get('commit', {}).get('message', ''),
                date=commit_data.get('commit', {}).get('author', {}).get('date', ''),
                author=commit_data.get('commit', {}).get('author', {}).get('name', '')
            )
        else:
            print(f"⚠️ 获取提交{commit_sha[:8]}信息失败: {response.status_code}")
            return None
    except Exception as e:
        print(f"⚠️ 获取提交{commit_sha[:8]}信息异常: {e}")
        return None

def extract_definitions(content: str) -> List[str]:
    """从Python代码内容中提取函数和类定义，包括嵌套关系"""
    if not content:
        return []
    
    try:
        # 解析代码为AST
        tree = ast.parse(content)
        
        # 存储所有定义（包括它们的路径）
        definitions = []
        
        def visit_node(node, path=""):
            """递归访问节点并收集定义信息"""
            if isinstance(node, ast.FunctionDef):
                full_name = f"{path}.{node.name}" if path else node.name
                definitions.append(full_name)
                
                # 递归访问函数体内的定义
                for child in node.body:
                    visit_node(child, full_name)
                    
            elif isinstance(node, ast.ClassDef):
                full_name = f"{path}.{node.name}" if path else node.name
                definitions.append(full_name)
                
                # 递归访问类体内的定义
                for child in node.body:
                    visit_node(child, full_name)
            
            elif isinstance(node, ast.Module):
                # 模块的顶级节点
                for child in node.body:
                    visit_node(child)
        
        # 开始访问
        visit_node(tree)
        return definitions
        
    except SyntaxError:
        print(f"    - ⚠️ 解析代码出错，可能包含语法错误")
        return []

def analyze_function_changes(before_content: str, after_content: str) -> Tuple[bool, List[str], List[str]]:
    """分析函数变更，返回是否只修改了已有函数及新增/删除的函数列表"""
    
    # 分析文件内容变更（使用完整路径区分嵌套关系）
    before_definitions = set(extract_definitions(before_content or ""))
    after_definitions = set(extract_definitions(after_content or ""))
    
    # 找出新增和删除的定义（包括函数和类）
    new_definitions = list(after_definitions - before_definitions)
    deleted_definitions = list(before_definitions - after_definitions)
    
    # 判断是否只修改了已有函数和类
    only_modified = len(new_definitions) == 0 and len(deleted_definitions) == 0
    
    return only_modified, new_definitions, deleted_definitions

# --- LLM 分析 ---

def generate_detailed_description_with_llm(
    feature_description: str,
    pr_info: Dict,
    file_changes: List[FileChange]
) -> Optional[str]:
    """使用 LLM 基于文件变更生成详细的功能描述"""
    
    client = openai.OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.deepseek.com")
    
    # 过滤掉测试文件，只构建非测试文件的摘要
    non_test_file_changes = [fc for fc in file_changes if not is_test_file(fc.filename)]
    
    # 从配置文件读取参数
    max_files = CONFIG['pr_analyzer']['max_files_in_summary']
    max_patch_length = CONFIG['pr_analyzer']['max_patch_length']
    max_patch_preview = CONFIG['pr_analyzer']['max_patch_preview_length']
    
    # 构建文件变更摘要
    files_summary = []
    for fc in non_test_file_changes[:max_files]:  # 使用配置的文件数量限制
        summary = f"- {fc.filename} ({fc.status}): +{fc.additions}/-{fc.deletions}"
        if fc.patch and len(fc.patch) < max_patch_length:  # 使用配置的patch长度限制
            summary += f"\n  {fc.patch[:max_patch_preview]}..."  # 使用配置的预览长度
        files_summary.append(summary)
    
    files_text = "\n".join(files_summary)
    if len(non_test_file_changes) > max_files:
        files_text += f"\n... and {len(non_test_file_changes) - max_files} more files"
    
    # 如果没有非测试文件，提供提示信息
    if not non_test_file_changes:
        files_text = "No files were modified in this PR."
    
    prompt = f"""
You need to create a user-focused description for this software update. Analyze the PR information and code changes to understand what this means for users.

Original Feature Description: {feature_description}

PR Title: {pr_info.get('title', '')}
PR Description: {pr_info.get('body', '')}

File Changes:
{files_text}

Your description must:
- Start with "I want to" and focus on user needs and capabilities
- Explain what users can now accomplish with this update
- Highlight the benefits and value this brings to user experience
- Show how this functionality helps users in their workflow
- Avoid technical implementation details
- Use existing PR context when valuable, but enhance it with code insights
- Provide a cohesive explanation of why this update matters to users

Write directly from the user's perspective about what they can accomplish and what value they receive.
"""

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You help users understand software updates by writing clear, user-focused descriptions. Always start responses with 'I want to' and explain what users can accomplish and the value they receive. Focus on practical benefits rather than technical details."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=1000
        )

        content = response.choices[0].message.content
        return content if content is not None else feature_description
        
    except Exception as e:
        print(f"⚠️ LLM生成详细描述失败: {e}")
        return feature_description

def generate_feature_detailed_description(
    feature_description: str,
    feature_type: str,
    pr_analyses: List[PRAnalysis]
) -> Optional[str]:
    """基于多个 PR 的详细分析，为整个 feature 生成详细描述"""
    
    client = openai.OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.deepseek.com")
    
    # 构建所有PR的详细信息摘要
    pr_summaries = []
    for pr in pr_analyses:
        summary = f"""
PR #{pr.pr_number}: {pr.title}
- Status: {pr.state} (merged: {pr.merged})
- Files changed: {len(pr.file_changes)}
- User benefits: {pr.detailed_description}
"""
        pr_summaries.append(summary)
    
    prs_text = "\n".join(pr_summaries)
    
    prompt = f"""
You need to create a comprehensive user-focused description for this complete feature. Analyze all the related pull requests to understand what this feature enables users to do.

Feature Type: {feature_type}
Original Description: {feature_description}

Associated Pull Requests:
{prs_text}

Your description must:
- Start with "I want to" and focus on what users can accomplish
- Explain the specific capabilities this complete feature provides
- Show how all related changes work together to benefit users
- Highlight what new things users can now do that they couldn't before
- Demonstrate how this improves user workflow and productivity
- Explain the practical value users receive from this feature
- Combine insights from all PRs to show the complete picture
- Focus on user benefits rather than technical implementation
- Explain why this feature matters to users in real-world usage

Write directly from the user's perspective about what they can accomplish and the complete value they receive from this feature.
"""

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You help users understand software features by writing comprehensive, user-focused descriptions. Always start responses with 'I want to' and explain what users can accomplish and the practical value they receive. Synthesize information from multiple sources to show complete user benefits."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=1000
        )
        
        content = response.choices[0].message.content
        return content if content is not None else feature_description
        
    except Exception as e:
        print(f"⚠️ LLM生成feature详细描述失败: {e}")
        return None

# --- 主要功能函数 ---

def analyze_pr(repo_name: str, pr_url: str, feature_description: str, use_cache: bool = True) -> Optional[PRAnalysis]:
    """分析单个 PR"""
    pr_number = extract_pr_number_from_url(pr_url)
    if not pr_number:
        print(f"⚠️ 无法从URL中提取PR编号: {pr_url}")
        return None
    
    cache_key = f"{repo_name}#{pr_number}"
    
    # 检查缓存
    if use_cache:
        cache = load_pr_analysis_cache()
        if cache_key in cache:
            print(f"  > 🔄 从缓存加载 PR#{pr_number} 的分析结果")
            return cache[cache_key]
    
    print(f"  > 🔍 正在分析 PR#{pr_number}...")
    
    # 获取 PR 基本信息
    pr_info = get_pr_info(repo_name, pr_number)
    if not pr_info:
        return None
    
    # 获取文件变更
    file_changes = get_pr_files(repo_name, pr_number)
    
    # 获取详细的 Commit 信息
    base_sha = pr_info['base']['sha']
    head_sha = pr_info['head']['sha']
    
    base_commit = get_commit_info(repo_name, base_sha)
    head_commit = get_commit_info(repo_name, head_sha)
    
    # 如果无法获取提交信息，创建基本的 Commit 对象
    if not base_commit:
        base_commit = Commit(sha=base_sha, message='', date='', author='')
    if not head_commit:
        head_commit = Commit(sha=head_sha, message='', date='', author='')
    
    # 获取详细的 Commit 信息
    base_commit = get_commit_info(repo_name, base_sha)
    head_commit = get_commit_info(repo_name, head_sha)
    
    # 如果无法获取提交信息，创建基本的 Commit 对象
    if not base_commit:
        base_commit = Commit(sha=base_commit.sha, message='', date='', author='')
    if not head_commit:
        head_commit = Commit(sha=head_commit.sha, message='', date='', author='')

    test_files = []
    non_test_files = []
    detailed_description = None
    only_modified_existing_functions = True

    for file_data in file_changes:
        file_path = file_data.filename
        
        if is_test_file(file_path):
            test_files.append(file_path)
            print(f"    - 找到测试文件: {file_path}")
        elif file_path.endswith('.py'):  # 只分析Python文件
            non_test_files.append(file_path)
            
            # 检查函数变更
            status = file_data.status
            
            # 对于新增或删除的文件，只要它们包含函数定义，就不满足条件
            if status == 'added' or status == 'removed':
                content = get_file_content(repo_name, file_path, head_commit.sha if status == 'added' else base_commit.sha)
                if content and extract_definitions(content):
                    print(f"    - ⚠️ 发现{status}文件含有函数定义: {file_path}")
                    only_modified_existing_functions = False
                    break
            
            # 对于修改的文件，需要比较修改前后的函数定义
            elif status == 'modified':
                before_content = get_file_content(repo_name, file_path, base_commit.sha)
                after_content = get_file_content(repo_name, file_path, head_commit.sha)

                # 处理文件内容可能为None的情况
                if before_content is not None and after_content is not None:
                    only_modified, new_funcs, deleted_funcs = analyze_function_changes(before_content, after_content)
                
                    if not only_modified:
                        print(f"    - ⚠️ 文件修改含有函数新增或删除: {file_path}")
                        if new_funcs:
                            print(f"      新增函数: {', '.join(new_funcs)}")
                        if deleted_funcs:
                            print(f"      删除函数: {', '.join(deleted_funcs)}")
                        only_modified_existing_functions = False
                        break
                else:
                    print(f"    - ⚠️ 无法获取文件内容，可能是空文件或不存在: {file_path}")
                    only_modified_existing_functions = False
    
    # 生成详细描述
    if only_modified_existing_functions and test_files and non_test_files:
        detailed_description = generate_detailed_description_with_llm(
            feature_description, pr_info, file_changes
        )
    
    if not detailed_description:
        if not test_files:
            print(f"  > ⏭️ PR#{pr_number} 不包含测试文件变更，跳过分析")
        elif not non_test_files:
            print(f"  > ⏭️ PR#{pr_number} 不包含非测试文件变更，跳过分析")
        elif not only_modified_existing_functions:
            print(f"  > ⏭️ PR#{pr_number} 包含函数新增或删除，跳过分析")
        return None
    
    analysis = PRAnalysis(
        pr_number=pr_number,
        repo_name=repo_name,
        title=pr_info.get('title', ''),
        description=pr_info.get('body', ''),
        state=pr_info.get('state', ''),
        merged=pr_info.get('merged', False),
        base_commit=base_commit,
        head_commit=head_commit,
        file_changes=file_changes,
        detailed_description=detailed_description,
        has_tests=len(test_files) > 0,
        test_files=test_files,
        only_modified_existing_functions=only_modified_existing_functions,
        non_test_files=non_test_files,
        analyzed_at=time.strftime('%Y-%m-%d %H:%M:%S')
    )

    print(f"    - PR#{pr_number}: 有测试={analysis.has_tests}, 测试文件数={len(analysis.test_files)}, 仅修改函数={analysis.only_modified_existing_functions}, 是否仅修改函数={analysis.only_modified_existing_functions}")

    # 保存到缓存
    if use_cache:
        save_pr_analysis_to_cache(analysis)
    
    return analysis

def enhance_feature_with_pr_analysis(feature, repo_name: str) -> Optional[EnhancedFeature]:
    """增强 feature 对象，添加 PR 详细分析"""
    pr_analyses = []
    
    # 使用tqdm显示PR分析进度
    with tqdm(feature.pr_links, desc=f"分析PR", unit="pr", leave=False) as pbar:
        for pr_link in pbar:
            pr_number = extract_pr_number_from_url(pr_link)
            pbar.set_description(f"PR#{pr_number}")
            
            pr_analysis = analyze_pr(repo_name, pr_link, feature.description)
            if pr_analysis:
                pr_analyses.append(pr_analysis)
            
            # 避免API速率限制
            time.sleep(0.5)
    
    # 如果只有一个PR，直接使用PR的详细描述
    if len(pr_analyses) == 1:
        feature_detailed_description = pr_analyses[0].detailed_description
    elif len(pr_analyses) > 1:
        # 多个PR时，基于所有PR分析生成feature的详细描述
        feature_detailed_description = generate_feature_detailed_description(
            feature.description,
            feature.feature_type,
            pr_analyses
        )
    else:
        return None
    
    if feature_detailed_description:
        return EnhancedFeature(
            feature_type=feature.feature_type,
            description=feature.description,
            pr_analyses=pr_analyses,
            feature_detailed_description=feature_detailed_description
        )
    else:
        return None

def enhance_release_analysis_with_pr_details(release_analysis) -> List[EnhancedFeature]:
    """增强 release 分析，只处理 new_features 添加 PR 详细信息"""
    print(f"--- 开始分析 {release_analysis.tag_name} 的新功能 ---")
    
    enhanced_features = []
    
    # 使用tqdm显示功能分析进度
    with tqdm(release_analysis.new_features, desc=f"分析功能", unit="feature", leave=False) as pbar:
        for feature in pbar:
            if feature.pr_links:
                pbar.set_description(f"功能: {feature.description[:30]}...")
                enhanced = enhance_feature_with_pr_analysis(feature, release_analysis.repo_name)
                if enhanced:
                    enhanced_features.append(enhanced)
                    pbar.write(f"    ✅ 已分析功能: {feature.description[:50]}...")
                else:
                    pbar.write(f"    ⚠️ 跳过功能: {feature.description[:50]}...")
    
    return enhanced_features