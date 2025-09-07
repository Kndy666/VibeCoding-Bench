import re
import base64
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Union
from dataclasses import dataclass

@dataclass
class PatchInfo:
    """单个文件的patch信息"""
    filename: str
    status: str  # added, modified, removed, renamed
    patch_content: str
    is_test_file: bool = False
    old_filename: Optional[str] = None  # 用于重命名文件

class PatchAnalyzer:
    """统一的patch分析和应用器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # 常见的测试文件模式
        self.test_patterns = [
            r'test.*\.py$',
            r'.*test\.py$', 
            r'.*_test\.py$',
            r'.*/test[s]?/.*\.py$',
            r'.*/testing/.*\.py$',
        ]
    
    def is_test_file(self, filename: str) -> bool:
        """判断文件是否为测试文件"""
        filename_lower = filename.lower()
        return any(re.search(pattern, filename_lower) for pattern in self.test_patterns)
    
    def parse_unified_diff(self, diff_content: str) -> List[PatchInfo]:
        """解析统一diff格式，返回每个文件的patch信息"""
        patches = []
        
        # 分割为单个文件的diff
        file_diffs = re.split(r'\ndiff --git', diff_content)
        
        for i, file_diff in enumerate(file_diffs):
            if i > 0:  # 第一个分割可能不以diff开头
                file_diff = 'diff --git' + file_diff
            
            if not file_diff.strip():
                continue
                
            patch_info = self._parse_single_file_diff(file_diff)
            if patch_info:
                patches.append(patch_info)
        
        self.logger.info(f"解析到 {len(patches)} 个文件的patch，其中测试文件: {sum(1 for p in patches if p.is_test_file)}")
        return patches
    
    def _parse_single_file_diff(self, diff_content: str) -> Optional[PatchInfo]:
        """解析单个文件的diff"""
        lines = diff_content.strip().split('\n')
        
        if not lines:
            return None
        
        # 解析文件路径和状态
        git_line = lines[0]
        filename, status, old_filename = self._extract_file_info(git_line, lines)
        
        if not filename:
            return None
        
        # 提取patch内容（排除头部信息）
        patch_lines = []
        in_hunk = False
        
        for line in lines:
            if line.startswith('@@'):
                in_hunk = True
                patch_lines.append(line)
            elif in_hunk and (line.startswith(('+', '-', ' ')) or line == ''):
                patch_lines.append(line)
            elif line.startswith('\\'):  # 处理"No newline at end of file"
                patch_lines.append(line)
        
        patch_content = '\n'.join(patch_lines)
        is_test = self.is_test_file(filename)
        
        return PatchInfo(
            filename=filename,
            status=status,
            patch_content=patch_content,
            is_test_file=is_test,
            old_filename=old_filename
        )
    
    def _extract_file_info(self, git_line: str, all_lines: List[str]) -> Tuple[Optional[str], str, Optional[str]]:
        """从git diff行和相关行中提取文件信息"""
        # 解析 diff --git a/file b/file
        git_match = re.match(r'diff --git a/(.*?) b/(.*)', git_line)
        if not git_match:
            return None, "unknown", None
        
        old_file, new_file = git_match.groups()
        
        # 检查文件状态
        status = "modified"
        old_filename = None
        
        for line in all_lines[:10]:  # 只检查前几行
            if line.startswith('new file mode'):
                status = "added"
                break
            elif line.startswith('deleted file mode'):
                status = "removed"
                break
            elif line.startswith('rename from'):
                status = "renamed"
                old_filename = old_file
                break
        
        filename = new_file if status != "removed" else old_file
        return filename, status, old_filename
    
    def read_patch_file(self, patch_path: Union[str, Path]) -> str:
        """读取patch文件内容"""
        patch_path = Path(patch_path)
        
        if not patch_path.exists():
            raise FileNotFoundError(f"Patch文件不存在: {patch_path}")
        
        try:
            with patch_path.open('r', encoding='utf-8') as f:
                content = f.read()
            self.logger.info(f"成功读取patch文件: {patch_path}")
            return content
        except Exception as e:
            raise RuntimeError(f"读取patch文件失败: {e}")
    
    def filter_patches(self, patches: List[PatchInfo], include_test: bool = True, 
                      include_source: bool = True) -> List[PatchInfo]:
        """过滤patch列表"""
        filtered = []
        
        for patch in patches:
            if patch.is_test_file and include_test:
                filtered.append(patch)
            elif not patch.is_test_file and include_source:
                filtered.append(patch)
        
        self.logger.info(f"过滤后保留 {len(filtered)} 个patch (测试文件: {include_test}, 源码文件: {include_source})")
        return filtered
    
    def apply_patches_to_container(self, patches: List[PatchInfo], docker_executor, workdir: str) -> List[str]:
        """在容器中应用patch列表"""
        applied_files = []
        
        for patch in patches:
            try:
                success = self._apply_single_patch_to_container(patch, docker_executor, workdir)
                if success:
                    applied_files.append(patch.filename)
                    self.logger.info(f"成功应用patch: {patch.filename} ({patch.status})")
                else:
                    self.logger.warning(f"应用patch失败: {patch.filename}")
            except Exception as e:
                self.logger.error(f"应用patch {patch.filename} 时出错: {e}")
        
        return applied_files
    
    def _apply_single_patch_to_container(self, patch: PatchInfo, docker_executor, workdir: str) -> bool:
        """在容器中应用单个patch"""
        # 构建完整的diff内容
        diff_content = self._build_complete_diff(patch)
        
        # 将patch内容编码为base64并写入临时文件
        patch_base64 = base64.b64encode(diff_content.encode('utf-8')).decode('utf-8')
        write_cmd = f"echo '{patch_base64}' | base64 -d > /tmp/single_patch.tmp"
        
        exit_code, output = docker_executor.execute(write_cmd, tty=False, timeout=30)
        if exit_code != 0:
            self.logger.error(f"写入patch到临时文件失败: {output}")
            return False
        
        # 应用patch（使用参数避免交互）
        apply_cmd = "patch -p1 --no-backup-if-mismatch --force < /tmp/single_patch.tmp"
        exit_code, output = docker_executor.execute(apply_cmd, workdir, tty=False, timeout=30)
        
        if exit_code != 0:
            self.logger.error(f"应用patch失败: {output}")
            return False
        
        return True
    
    def _build_complete_diff(self, patch: PatchInfo) -> str:
        """构建完整的diff格式内容"""
        header = f"diff --git a/{patch.filename} b/{patch.filename}\n"
        
        if patch.status == "added":
            diff_content = (
                f"{header}"
                f"new file mode 100644\n"
                f"index 0000000..1111111\n"
                f"--- /dev/null\n"
                f"+++ b/{patch.filename}\n"
                f"{patch.patch_content}\n"
            )
        elif patch.status == "removed":
            diff_content = (
                f"{header}"
                f"deleted file mode 100644\n"
                f"index 1111111..0000000\n"
                f"--- a/{patch.filename}\n"
                f"+++ /dev/null\n"
                f"{patch.patch_content}\n"
            )
        elif patch.status == "renamed":
            old_name = patch.old_filename or patch.filename
            diff_content = (
                f"diff --git a/{old_name} b/{patch.filename}\n"
                f"similarity index 100%\n"
                f"rename from {old_name}\n"
                f"rename to {patch.filename}\n"
                f"{patch.patch_content}\n"
            )
        else:  # modified
            diff_content = (
                f"{header}"
                f"index 1111111..2222222 100644\n"
                f"--- a/{patch.filename}\n"
                f"+++ b/{patch.filename}\n"
                f"{patch.patch_content}\n"
            )
        
        return diff_content
    
    def apply_patch_file_to_container(self, patch_file_path: Union[str, Path], 
                                     docker_executor, workdir: str, 
                                     include_test: bool = True, include_source: bool = True) -> Dict[str, any]:
        """从patch文件应用到容器的完整流程"""
        # 读取并解析patch文件
        patch_content = self.read_patch_file(patch_file_path)
        patches = self.parse_unified_diff(patch_content)
        
        # 过滤patch
        filtered_patches = self.filter_patches(patches, include_test, include_source)
        
        # 应用patch
        applied_files = self.apply_patches_to_container(filtered_patches, docker_executor, workdir)
        
        # 返回结果
        return {
            "total_files_num": len(filtered_patches),
            "applied_files_num": len(applied_files),
            "applied_files": applied_files,
            "patch_content": patch_content
        }
