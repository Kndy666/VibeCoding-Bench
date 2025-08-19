import requests
import time
import re
import json
import toml
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple
from datetime import datetime
from tqdm import tqdm

# --- 配置加载 ---
def load_config():
    """加载配置文件"""
    config_file = Path(__file__).parent / "config.toml"
    with open(config_file, 'r', encoding='utf-8') as f:
        return toml.load(f)

CONFIG = load_config()

# --- 配置区 ---

TOKEN = CONFIG['common']['github_token']
HEADERS = {'Authorization': f'Bearer {TOKEN}', 'Accept': 'application/vnd.github.v3+json'}

# 爬取模式配置
CRAWL_MODE = CONFIG['common']['crawl_mode']
CRAWL_JSON_FILE = Path(__file__).parent / CONFIG['common']['crawl_json_file']

# 筛选阈值
MIN_STARS = CONFIG['release_collector']['min_stars_range']
RANK_START = CONFIG['release_collector']['rank_start']
RANK_END = CONFIG['release_collector']['rank_end']
MIN_RELEASES = CONFIG['release_collector']['min_releases']
MIN_RELEASE_BODY_LENGTH = CONFIG['release_collector']['min_release_body_length']
MIN_RELEASE_DATE = CONFIG['release_collector']['min_release_date']
EXCLUDED_TOPICS = set(CONFIG['release_collector']['excluded_topics'])

# Test case相关配置
TEST_DIRECTORIES = CONFIG['release_collector']['test_directories']
TEST_FILE_PATTERNS = CONFIG['release_collector']['test_file_patterns']
BOT_USERS = set(CONFIG['release_collector']['bot_users'])

# 缓存文件路径
CACHE_FILE = Path(__file__).parent / CONFIG['common']['output_dir'] / CONFIG['release_collector']['cache_file']

# --- 数据类定义 ---

@dataclass
class Release:
    """表示一个发布版本"""
    tag_name: str
    name: str
    body: str
    published_at: str
    target_commitish: str
    version_tuple: Tuple[int, ...]
    version_key: str
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Release':
        release = cls(**data)
        return release

@dataclass
class Repository:
    """表示一个仓库及其发布信息"""
    full_name: str
    stargazers_count: int
    size: int
    topics: List[str]
    releases_count: int
    major_releases: List[Release]
    readme_content: str
    ci_configs: Dict[str, str]  # 新增：CI/CD配置文件内容
    processed_at: str
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data['major_releases'] = [release.to_dict() for release in self.major_releases]
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Repository':
        repo = cls(**data)
        repo.major_releases = [Release.from_dict(release_data) for release_data in data.get("major_releases", [])]
        return repo

# --- 缓存管理函数 ---

def load_processed_repos() -> Dict[str, Repository]:
    """从JSON文件加载已处理的仓库信息。"""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                processed_repos = {}
                for repo_name, repo_data in data.items():
                    processed_repos[repo_name] = Repository.from_dict(repo_data)
                print(f"✅ 从缓存文件加载了 {len(processed_repos)} 个已处理的仓库")
                return processed_repos
        except (json.JSONDecodeError, Exception) as e:
            print(f"⚠️ 加载缓存文件失败: {e}，将重新开始处理")
            return {}
    else:
        print("📝 缓存文件不存在，将创建新的缓存")
        return {}

def save_processed_repo(repository: Repository):
    """保存单个仓库的处理结果到JSON文件。"""
    # 加载现有数据
    processed_repos_dict = {}
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                processed_repos_dict = json.load(f)
        except:
            pass
    
    # 添加新的仓库数据
    processed_repos_dict[repository.full_name] = repository.to_dict()
    
    # 保存到文件
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(processed_repos_dict, f, indent=2, ensure_ascii=False)
        print(f"💾 已保存仓库 {repository.full_name} 的处理结果到缓存")
    except Exception as e:
        print(f"⚠️ 保存缓存失败: {e}")

# --- 核心功能函数 ---

def get_candidate_repos():
    """从 GitHub API 获取指定排名范围内的 Python 仓库作为候选池。"""
    print(f"获取 Stars >= {MIN_STARS} 的 Python 仓库，筛选排名 {RANK_START}-{RANK_END}...")
    
    API_URL = "https://api.github.com/search/repositories"
    PARAMS = {
        'q': f'language:python stars:>={MIN_STARS}',
        'sort': 'stars',
        'order': 'desc',
        'per_page': 100  # GitHub API 单次请求最多100个
    }
    
    all_repos = []
    page = 1
    current_repo_count = 0
    
    try:
        while True:
            params_with_page = PARAMS.copy()
            params_with_page['page'] = page
            
            response = requests.get(API_URL, params=params_with_page, headers=HEADERS)
            response.raise_for_status()
            
            data = response.json()
            repos = data.get('items', [])
            
            if not repos:  # 没有更多结果
                break
                
            # 检查当前页的仓库排名范围
            page_start_rank = current_repo_count + 1
            page_end_rank = current_repo_count + len(repos)
            
            print(f"✅ 已获取第 {page} 页，仓库排名 {page_start_rank}-{page_end_rank}")
            
            # 如果当前页的起始排名已经超过了我们需要的结束排名，停止获取
            if page_start_rank > RANK_END:
                print(f"已超过目标排名范围 {RANK_END}，停止获取")
                break
            
            # 筛选出在目标排名范围内的仓库
            for i, repo in enumerate(repos):
                repo_rank = current_repo_count + i + 1
                if RANK_START <= repo_rank <= RANK_END:
                    repo['rank'] = repo_rank  # 添加排名信息
                    all_repos.append(repo)
            
            current_repo_count += len(repos)
            
            # 如果已经获取到目标排名范围的所有仓库，停止获取
            if page_end_rank >= RANK_END:
                print(f"已获取到目标排名范围 {RANK_END}，停止获取")
                break
                
            # GitHub搜索API最多返回1000个结果，且有分页限制
            if current_repo_count >= data.get('total_count', 0) or page >= 10:
                break
                
            page += 1
            time.sleep(0.5)  # 避免API限制
            
        print(f"✅ 总共获取到 {len(all_repos)} 个排名在 {RANK_START}-{RANK_END} 范围内的仓库")
        return all_repos
        
    except requests.exceptions.HTTPError as e:
        print(f"HTTP 错误: {e}")
        if e.response.status_code == 403:
            print("API 速率限制已超出。请使用 Token 或等待一段时间后重试。")
        return []

def has_test_cases(repo_full_name: str) -> bool:
    """检查仓库是否包含测试用例"""
    print(f"  > 正在检查 {repo_full_name} 是否有test case...")
    
    try:
        # 1. 检查是否有测试目录
        contents_url = f"https://api.github.com/repos/{repo_full_name}/contents"
        time.sleep(0.5)
        response = requests.get(contents_url, headers=HEADERS)
        response.raise_for_status()
        
        contents = response.json()
        
        # 检查根目录下是否有测试相关目录
        has_test_directory = False
        test_directories_found = []
        for item in contents:
            if item.get('type') == 'dir':
                dir_name = item.get('name', '').lower()
                if any(test_dir in dir_name for test_dir in TEST_DIRECTORIES):
                    print(f"  > ✅ 发现测试目录: {item.get('name')}")
                    has_test_directory = True
                    test_directories_found.append(item.get('name'))
        
        # 2. 检查根目录下是否有测试文件
        for item in contents:
            if item.get('type') == 'file':
                file_name = item.get('name', '')
                if any(re.match(pattern, file_name) for pattern in TEST_FILE_PATTERNS):
                    print(f"  > ✅ 发现测试文件: {file_name}")
                    return True
        
        # 3. 只有在根目录下发现测试目录时，才递归检查测试目录的内容
        if has_test_directory:
            def check_directory_for_tests(repo_name, directory_path):
                """递归检查目录中是否包含Python测试文件"""
                try:
                    dir_url = f"https://api.github.com/repos/{repo_name}/contents/{directory_path}"
                    time.sleep(0.5)
                    response = requests.get(dir_url, headers=HEADERS)
                    if response.status_code == 200:
                        contents = response.json()
                        if isinstance(contents, list):
                            # 一次性检查该目录下的所有文件
                            files = [item for item in contents if item.get('type') == 'file']
                            for item in files:
                                file_name = item.get('name', '')
                                # 检查是否是Python文件或测试文件
                                if file_name.endswith('.py') or any(re.match(pattern, file_name) for pattern in TEST_FILE_PATTERNS):
                                    print(f"  > ✅ 发现测试目录 {directory_path} 中的Python文件: {file_name}")
                                    return True
                            
                            # 然后递归检查子目录
                            directories = [item for item in contents if item.get('type') == 'dir']
                            for dir_item in directories:
                                sub_dir_path = f"{directory_path}/{dir_item.get('name')}"
                                if check_directory_for_tests(repo_name, sub_dir_path):
                                    return True
                    return False
                except Exception as e:
                    print(f"  > ⚠️ 检查目录 {directory_path} 时出错: {e}")
                    return False
            
            # 对发现的每个测试目录进行递归检查
            for test_dir in test_directories_found:
                if check_directory_for_tests(repo_full_name, test_dir):
                    return True

        print(f"  > ❌ 未发现明显的测试用例")
        return False
        
    except requests.exceptions.HTTPError as e:
        print(f"  > ⚠️ 检查测试用例时出错: {e}")
        return False
    except Exception as e:
        print(f"  > ⚠️ 检查测试用例时发生异常: {e}")
        return False

def is_valid_release(release_data: dict) -> bool:
    """检查release是否有效（非bot生成且内容充实且在指定日期之后）"""
    # 检查是否由bot生成
    author_login = release_data.get('author', {}).get('login', '')
    if author_login in BOT_USERS:
        return False
    
    # 检查release body长度
    body = release_data.get('body', '') or ''  # 确保body不为None
    if len(body.strip()) < MIN_RELEASE_BODY_LENGTH:
        return False
    
    # 检查发布时间是否在指定日期之后
    published_at = release_data.get('published_at', '')
    if published_at:
        try:
            release_date = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            min_date = datetime.fromisoformat(MIN_RELEASE_DATE + 'T00:00:00+00:00')
            if release_date < min_date:
                return False
        except Exception:
            # 如果日期解析失败，跳过此release
            return False
    else:
        # 如果没有发布日期，跳过此release
        return False
    
    return True

def filter_by_metadata_and_releases(repos):
    """通过 API 对仓库进行宏观指标和release数量的初步筛选。"""
    print(f"通过宏观指标和release数量进行初步筛选（排名范围 {RANK_START}-{RANK_END}）...")
    
    filtered_repos = []
    
    # 使用tqdm显示筛选进度
    with tqdm(repos, desc="筛选仓库", unit="repo") as pbar:
        for repo in pbar:
            repo_name = repo['full_name']
            repo_rank = repo.get('rank', 0)
            pbar.set_description(f"检查: {repo_name} (排名#{repo_rank})")

            # 1. 检查宏观指标（topics筛选）
            repo_topics = set(repo.get('topics', []))
            if repo_topics.intersection(EXCLUDED_TOPICS):
                pbar.write(f"  ❌ {repo_name} (#{repo_rank}): 包含排除的主题")
                continue

            # 2. 检查是否有test case
            if not has_test_cases(repo_name):
                pbar.write(f"  ❌ {repo_name} (#{repo_rank}): 无测试用例")
                continue

            # 3. 检查是否有足够的有效release
            releases_url = f"https://api.github.com/repos/{repo_name}/releases"
            try:
                # 每次检查之间稍作停顿，避免 API 超限
                time.sleep(1)
                response = requests.get(releases_url, headers=HEADERS)
                response.raise_for_status()
                
                releases = response.json()
                # 过滤有效的release
                valid_releases = [r for r in releases if is_valid_release(r)]
                
                if len(valid_releases) >= MIN_RELEASES:
                    repo['releases_count'] = len(valid_releases)
                    repo['releases_data'] = valid_releases  # 保存过滤后的releases数据
                    filtered_repos.append(repo)
                    pbar.write(f"  ✅ {repo_name} (#{repo_rank}): 通过初筛! Stars: {repo['stargazers_count']}, 有效release: {len(valid_releases)}")
                else:
                    pbar.write(f"  ❌ {repo_name} (#{repo_rank}): 有效release数量不足，只有 {len(valid_releases)} 个")

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 403:
                    pbar.write("\nAPI 速率限制超出，初筛提前终止。")
                    break
                pbar.write(f"  ❌ {repo_name} (#{repo_rank}): API 错误: {e.response.status_code}")
    
    return filtered_repos

def extract_version_components(tag_name):
    """
    从标签名提取版本号组件。
    支持多种格式如：v1.2.3, 1.2.3, 1-2-3, release-1.2.3, version.1.2.3等
    处理版本号中可能存在的空格，如"v 1.2.3"或"1. 2. 3"
    
    返回：
    - 如果成功提取到版本号，返回一个版本号元组 (major, minor, patch, ...)
    - 如果无法提取，返回 None
    """
    # 先清理输入字符串，去除首尾空格
    tag_name = tag_name.strip()
    
    # 1. 优先尝试直接匹配版本号模式（不依赖前缀判断）
    # 匹配格式：数字.数字.数字.数字... 或 数字-数字-数字... 或 数字_数字_数字...
    direct_version_pattern = re.compile(r'(\d+)(?:\s*[.\-_]\s*(\d+))?(?:\s*[.\-_]\s*(\d+))?(?:\s*[.\-_]\s*(\d+))?')
    
    # 首先尝试从字符串开头匹配版本号
    match = direct_version_pattern.match(tag_name)
    if match:
        version_tuple = tuple(int(group) for group in match.groups() if group is not None)
        if version_tuple:  # 确保至少有一个版本号组件
            return version_tuple
    
    # 2. 如果开头没有匹配到，尝试移除常见前缀后再匹配
    version_string = tag_name
    common_prefixes = ['version', 'release', 'ver', 'rel', 'v']  # 按长度排序，先匹配长的
    
    for prefix in common_prefixes:
        # 使用正则表达式更精确地匹配前缀
        prefix_pattern = re.compile(rf'^{re.escape(prefix)}[.\-_\s]*', re.IGNORECASE)
        if prefix_pattern.match(tag_name):
            version_string = prefix_pattern.sub('', tag_name).strip()
            break
    
    # 3. 在处理后的字符串中查找版本号
    match = direct_version_pattern.match(version_string)
    if match:
        version_tuple = tuple(int(group) for group in match.groups() if group is not None)
        if version_tuple:
            return version_tuple
    
    # 4. 最后尝试在整个字符串中查找任何位置的版本号模式
    match = direct_version_pattern.search(tag_name)
    if match:
        version_tuple = tuple(int(group) for group in match.groups() if group is not None)
        if version_tuple:
            return version_tuple
    
    return None

def get_major_releases(repo_full_name: str, releases_data, limit=5) -> List[Release]:
    """获取仓库的主要版本release（按主版本号分组后取每组最新的）。"""
    print(f"  > 正在获取 {repo_full_name} 的主要版本release...")
    
    all_releases = releases_data
    print(f"  > 使用已获取的 {len(all_releases)} 个有效releases数据")
    
    valid_releases = []
    
    for release in all_releases:
        tag_name = release.get('tag_name', '')
        
        # 使用新的版本提取函数
        version_tuple = extract_version_components(tag_name)
        
        if version_tuple:
            # 跳过预发布版本 (包含 alpha, beta, rc, a, b 等标识)
            if re.search(r'(alpha|beta|rc|a\d+|b\d+)', tag_name.lower()):
                continue
            
            release_obj = Release(
                tag_name=tag_name,
                name=release.get('name', ''),
                body=release.get('body', ''),
                published_at=release.get('published_at', ''),
                target_commitish=release.get('target_commitish', ''),
                version_tuple=version_tuple,
                version_key='.'.join(str(v) for v in version_tuple),
            )
            valid_releases.append(release_obj)
    
    # 按版本号排序，取最新的几个版本
    valid_releases.sort(key=lambda x: x.version_tuple, reverse=True)
    result = valid_releases[:limit]  # 只取前几个版本
    
    print(f"  > 成功获取 {len(result)} 个主要版本release")
    if result:
        version_list = ', '.join([r.version_key for r in result])
        print(f"  > 选择的版本: {version_list}")
    return result

def get_repository_readme(repo_full_name: str) -> str:
    """获取仓库的README内容"""
    print(f"  > 正在获取 {repo_full_name} 的README...")
    
    try:
        # 获取仓库根目录的所有文件
        root_url = f"https://api.github.com/repos/{repo_full_name}/contents"
        time.sleep(0.5)  # 避免API限制
        response = requests.get(root_url, headers=HEADERS)
        response.raise_for_status()
        
        contents = response.json()
        
        # 常见的README文件名模式
        readme_patterns = [r'^readme\.md$', r'^readme\.rst$', r'^readme\.txt$', r'^readme$']
        
        # 在本地检查文件列表是否包含README
        for item in contents:
            if item.get('type') == 'file':
                file_name = item.get('name', '').lower()
                if any(re.match(pattern, file_name, re.IGNORECASE) for pattern in readme_patterns):
                    # 找到README文件，获取内容
                    download_url = item.get('download_url')
                    if download_url:
                        content_response = requests.get(download_url, headers=HEADERS)
                        content_response.raise_for_status()
                        readme_content = content_response.text
                        print(f"  > ✅ 成功获取README ({item.get('name')}), 长度: {len(readme_content)} 字符")
                        return readme_content
        
        print(f"  > ❌ 未找到README文件")
        return ""
    
    except Exception as e:
        print(f"  > ⚠️ 获取README时出错: {e}")
        return ""

def get_ci_configs(repo_full_name: str) -> Dict[str, str]:
    """获取仓库的CI/CD配置文件列表和下载链接"""
    print(f"  > 正在获取 {repo_full_name} 的CI/CD配置文件列表...")
    
    ci_configs = {}
    
    try:
        # 检查.github/workflows目录是否存在
        workflows_url = f"https://api.github.com/repos/{repo_full_name}/contents/.github/workflows"
        time.sleep(0.5)  # 避免API限制
        response = requests.get(workflows_url, headers=HEADERS)
        
        # 如果目录存在，收集其中的所有YAML文件信息
        if response.status_code == 200:
            contents = response.json()
            
            for item in contents:
                if item.get('type') == 'file' and (item.get('name', '').endswith('.yml') or item.get('name', '').endswith('.yaml')):
                    file_name = item.get('name', '')
                    file_path = f".github/workflows/{file_name}"
                    download_url = item.get('download_url', '')
                    
                    if download_url:
                        ci_configs[file_path] = download_url
                        print(f"  > ✅ 发现CI配置: {file_path}")
        
        if ci_configs:
            print(f"  > ✅ 共发现 {len(ci_configs)} 个CI配置文件")
        else:
            print(f"  > ❌ 未找到CI配置文件")
        
        return ci_configs
    
    except Exception as e:
        print(f"  > ⚠️ 获取CI配置列表时出错: {e}")
        return {}

def process_single_repository(repo: Dict, use_cache: bool = True) -> Repository:
    """处理单个仓库，获取其详细信息"""
    repo_name = repo['full_name']
    
    # 获取主要版本release，使用配置文件中的限制
    major_releases = get_major_releases(
        repo_name, 
        releases_data=repo.get('releases_data'), 
        limit=CONFIG['release_collector']['default_release_limit']
    )
    if not major_releases:
        raise ValueError(f"无法获取主要版本release")
        
    # 获取README内容
    readme_content = get_repository_readme(repo_name)

    # 获取CI/CD配置文件
    ci_configs = get_ci_configs(repo_name)
    
    # 创建Repository对象
    repository = Repository(
        full_name=repo_name,
        stargazers_count=repo['stargazers_count'],
        size=repo['size'],
        topics=repo.get('topics', []),
        releases_count=repo['releases_count'],
        major_releases=major_releases,
        readme_content=readme_content,
        ci_configs=ci_configs,
        processed_at=time.strftime('%Y-%m-%d %H:%M:%S')
    )
    
    # 保存到缓存
    if use_cache:
        save_processed_repo(repository)
    
    return repository

def get_specified_repos():
    """从 crawl.json 文件获取指定的仓库列表"""
    print(f"从指定文件获取仓库列表: {CRAWL_JSON_FILE}")
    
    if not CRAWL_JSON_FILE.exists():
        print(f"❌ 指定的仓库文件不存在: {CRAWL_JSON_FILE}")
        return []
    
    try:
        with open(CRAWL_JSON_FILE, 'r', encoding='utf-8') as f:
            crawl_data = json.load(f)
        
        # 收集所有类别的仓库
        all_repos = []
        for category, repos in crawl_data.items():
            print(f"✅ 加载类别 '{category}': {len(repos)} 个仓库")
            for repo_name in repos:
                all_repos.append(repo_name)
        
        print(f"✅ 总共加载 {len(all_repos)} 个指定仓库")
        
        # 为每个仓库获取详细信息
        detailed_repos = []
        with tqdm(all_repos, desc="获取仓库信息", unit="repo") as pbar:
            for repo_name in pbar:
                pbar.set_description(f"获取: {repo_name}")
                try:
                    repo_info = get_repository_info(repo_name)
                    if repo_info:
                        detailed_repos.append(repo_info)
                        pbar.write(f"  ✅ {repo_name}: Stars {repo_info['stargazers_count']}")
                    else:
                        pbar.write(f"  ❌ {repo_name}: 获取信息失败")
                except Exception as e:
                    pbar.write(f"  ❌ {repo_name}: {str(e)}")
                    continue
                
                time.sleep(0.5)  # 避免API限制
        
        print(f"✅ 成功获取 {len(detailed_repos)} 个仓库的详细信息")
        return detailed_repos
        
    except Exception as e:
        print(f"❌ 读取指定仓库文件失败: {e}")
        return []

def get_repository_info(repo_name: str) -> Dict:
    """获取单个仓库的详细信息"""
    try:
        repo_url = f"https://api.github.com/repos/{repo_name}"
        response = requests.get(repo_url, headers=HEADERS)
        response.raise_for_status()
        
        repo_data = response.json()
        
        # 返回与get_candidate_repos相同格式的数据
        return {
            'full_name': repo_data['full_name'],
            'stargazers_count': repo_data['stargazers_count'],
            'size': repo_data['size'],
            'topics': repo_data.get('topics', []),
            'language': repo_data.get('language', ''),
            'archived': repo_data.get('archived', False),
            'disabled': repo_data.get('disabled', False),
            'fork': repo_data.get('fork', False),
        }
        
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"  ⚠️ 仓库不存在: {repo_name}")
        else:
            print(f"  ⚠️ 获取仓库信息失败: {repo_name} - {e}")
        return None
    except Exception as e:
        print(f"  ⚠️ 获取仓库信息异常: {repo_name} - {e}")
        return None

def get_repositories_to_process(use_cache: bool = True, crawl_mode: str = None) -> Tuple[List[Dict], Dict[str, Repository]]:
    """获取需要处理的仓库列表和已处理的仓库"""
    # 加载已处理的仓库缓存
    processed_repos = load_processed_repos() if use_cache else {}
    
    # 确定爬取模式
    mode = crawl_mode or CRAWL_MODE
    
    # 根据模式获取候选仓库
    if mode == "specified":
        print("🎯 使用指定仓库模式")
        candidate_repos = get_specified_repos()
    else:
        print("⭐ 使用按star数筛选模式")
        candidate_repos = get_candidate_repos()
    
    if not candidate_repos:
        return [], processed_repos

    # 过滤掉已处理的仓库
    if processed_repos:
        unprocessed_repos = [repo for repo in candidate_repos if repo['full_name'] not in processed_repos]
        candidate_repos = unprocessed_repos

    # 初步筛选
    pre_filtered_repos = filter_by_metadata_and_releases(candidate_repos)
    
    return pre_filtered_repos, processed_repos