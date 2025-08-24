"""
代码变更分析工具 - 比较patch前后的完整代码

使用方法:
analyzer = CodeChangeAnalyzer()
changes = analyzer.analyze_changes(code_before, code_after)
print(analyzer.format_results(changes))
"""

import ast
from typing import Dict, List, Set, Optional
from dataclasses import dataclass

@dataclass
class CodeChange:
    """代码变更信息"""
    name: str
    change_type: str  # 'added', 'modified', 'deleted'
    code_type: str    # 'class', 'function', 'method'

class PytestFilter:
    """Pytest测试过滤器 - 筛选出pytest相关的测试方法和函数"""
    
    def is_pytest_function(self, func_name: str) -> bool:
        """判断是否是pytest测试函数"""
        return func_name.startswith('test_')
    
    def is_pytest_class(self, class_name: str) -> bool:
        """判断是否是pytest测试类"""
        return class_name.startswith('Test')
    
    def is_pytest_method(self, method_name: str) -> bool:
        """判断是否是pytest测试方法 (格式: TestClass.test_method)"""
        if '.' not in method_name:
            return False
        
        class_name, method = method_name.split('.', 1)
        return self.is_pytest_class(class_name) and self.is_pytest_function(method)
    
    def filter_pytest_changes(self, changes: List[CodeChange]) -> List[CodeChange]:
        """过滤出pytest相关的代码变更"""
        pytest_changes = []
        
        for change in changes:
            if change.code_type == 'function' and self.is_pytest_function(change.name):
                pytest_changes.append(change)
            elif change.code_type == 'method' and self.is_pytest_method(change.name):
                pytest_changes.append(change)
        
        return pytest_changes
    
    def format_pytest_results(self, pytest_changes: List[CodeChange]) -> str:
        """格式化pytest测试结果"""
        if not pytest_changes:
            return "没有检测到pytest相关的测试变更。"
        
        result = []
        result.append("🧪 Pytest 测试变更:")
        result.append("=" * 40)
        
        # 按变更类型分组
        added = [c for c in pytest_changes if c.change_type == 'added']
        modified = [c for c in pytest_changes if c.change_type == 'modified']
        deleted = [c for c in pytest_changes if c.change_type == 'deleted']
        
        if added:
            result.append("🟢 新增测试:")
            for change in sorted(added, key=lambda x: x.name):
                result.append(f"  - {change.code_type}: {change.name}")
        
        if modified:
            result.append("🟡 修改测试:")
            for change in sorted(modified, key=lambda x: x.name):
                result.append(f"  - {change.code_type}: {change.name}")
        
        if deleted:
            result.append("🔴 删除测试:")
            for change in sorted(deleted, key=lambda x: x.name):
                result.append(f"  - {change.code_type}: {change.name}")
        
        # 生成pytest运行命令建议
        result.append("\n💡 建议的pytest运行命令:")
        result.append("-" * 30)
        
        # 按类型生成运行命令
        test_functions = [c.name for c in pytest_changes if c.code_type == 'function']
        test_classes = [c.name for c in pytest_changes if c.code_type == 'class']
        test_methods = [c.name for c in pytest_changes if c.code_type == 'method']
        
        if test_functions:
            result.append("运行特定测试函数:")
            for func in test_functions:
                result.append(f"  pytest -v -k {func}")
        
        if test_classes:
            result.append("运行特定测试类:")
            for cls in test_classes:
                result.append(f"  pytest -v -k {cls}")
        
        if test_methods:
            result.append("运行特定测试方法:")
            for method in test_methods:
                # 转换 TestClass.test_method 为 pytest 格式
                class_name, method_name = method.split('.', 1)
                result.append(f"  pytest -v -k 'test_file.py::{class_name}::{method_name}'")
                # 也可以用简化的 -k 参数
                result.append(f"  pytest -v -k '{class_name} and {method_name}'")
        
        return '\n'.join(result)
    
    def get_pytest_run_commands(self, pytest_changes: List[CodeChange], test_file_path: str = "test_file.py") -> List[str]:
        """生成具体的pytest运行命令列表"""
        commands = []
        
        for change in pytest_changes:
            if change.code_type == 'function':
                commands.append(f"pytest -v -k {change.name} {test_file_path}")
            elif change.code_type == 'class':
                commands.append(f"pytest -v -k {change.name} {test_file_path}")
            elif change.code_type == 'method':
                class_name, method_name = change.name.split('.', 1)
                commands.append(f"pytest -v {test_file_path}::{class_name}::{method_name}")
        
        return commands

class CodeChangeAnalyzer:
    """代码变更分析器"""
    
    def parse_python_code(self, code_content: str) -> Dict[str, Set[str]]:
        """解析Python代码，提取所有类、函数和方法"""
        try:
            tree = ast.parse(code_content)
            result = {
                'classes': set(),
                'functions': set(),
                'methods': set()
            }
            
            # 收集所有类和其方法
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    result['classes'].add(node.name)
                    
                    # 收集类中的方法
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            result['methods'].add(f"{node.name}.{item.name}")
                
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # 检查是否是顶级函数（不在类中）
                    parent_classes = [n for n in ast.walk(tree) 
                                    if isinstance(n, ast.ClassDef) and node in ast.walk(n)]
                    if not parent_classes:
                        result['functions'].add(node.name)
            
            return result
            
        except SyntaxError as e:
            print(f"语法错误，无法解析代码: {e}")
            return {'classes': set(), 'functions': set(), 'methods': set()}
    
    def analyze_changes(self, code_before: str, code_after: str) -> List[CodeChange]:
        """分析两个版本代码之间的变更"""
        changes = []
        
        print("正在分析代码变更...")
        
        # 解析前后代码
        before_elements = self.parse_python_code(code_before)
        after_elements = self.parse_python_code(code_after)
        
        print(f"变更前: {len(before_elements['functions'])} 个函数, "
              f"{len(before_elements['classes'])} 个类, "
              f"{len(before_elements['methods'])} 个方法")
        print(f"变更后: {len(after_elements['functions'])} 个函数, "
              f"{len(after_elements['classes'])} 个类, "
              f"{len(after_elements['methods'])} 个方法")
        
        # 分析每种类型的变更
        for code_type in ['classes', 'functions', 'methods']:
            before_set = before_elements[code_type]
            after_set = after_elements[code_type]
            
            # 新增的元素
            added = after_set - before_set
            for name in added:
                changes.append(CodeChange(name, 'added', code_type.rstrip('s')))
            
            # 删除的元素
            deleted = before_set - after_set
            for name in deleted:
                changes.append(CodeChange(name, 'deleted', code_type.rstrip('s')))
        
        # 分析修改的元素（通过比较代码内容）
        modified_elements = self.find_modified_elements(code_before, code_after, before_elements, after_elements)
        for element_name, element_type in modified_elements:
            # 避免重复添加已经标记为新增或删除的元素
            existing_names = [c.name for c in changes]
            if element_name not in existing_names:
                changes.append(CodeChange(element_name, 'modified', element_type))
        
        return changes
    
    def find_modified_elements(self, code_before: str, code_after: str, 
                             before_elements: Dict, after_elements: Dict) -> List[tuple]:
        """找出被修改的元素（内容发生变化但名称未变）"""
        modified = []
        
        # 检查函数是否被修改
        common_functions = before_elements['functions'] & after_elements['functions']
        for func_name in common_functions:
            if self.is_function_modified(func_name, code_before, code_after):
                modified.append((func_name, 'function'))
        
        # 检查类是否被修改
        common_classes = before_elements['classes'] & after_elements['classes']
        for class_name in common_classes:
            if self.is_class_modified(class_name, code_before, code_after):
                modified.append((class_name, 'class'))
        
        # 检查方法是否被修改
        common_methods = before_elements['methods'] & after_elements['methods']
        for method_name in common_methods:
            if self.is_method_modified(method_name, code_before, code_after):
                modified.append((method_name, 'method'))
        
        return modified
    
    def get_function_node_by_name(self, func_name: str, tree: ast.AST) -> Optional[ast.FunctionDef]:
        """通过函数名获取AST节点"""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
                # 确保是顶级函数，不在类中
                for parent in ast.walk(tree):
                    if isinstance(parent, ast.ClassDef):
                        if node in ast.walk(parent):
                            continue
                return node
        return None
    
    def get_class_node_by_name(self, class_name: str, tree: ast.AST) -> Optional[ast.ClassDef]:
        """通过类名获取AST节点"""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return node
        return None
    
    def extract_function_code_by_ast(self, func_name: str, code: str) -> str:
        """通过AST精确提取函数代码"""
        try:
            tree = ast.parse(code)
            lines = code.split('\n')
            
            func_node = self.get_function_node_by_name(func_name, tree)
            if not func_node or not hasattr(func_node, 'end_lineno'):
                return ""
            
            # 使用 Python 3.8+ 的 end_lineno 属性
            start_line = func_node.lineno - 1
            end_line = func_node.end_lineno
            
            return '\n'.join(lines[start_line:end_line])
            
        except Exception as e:
            print(f"提取函数 {func_name} 代码时出错: {e}")
            return ""
    
    def extract_class_code_by_ast(self, class_name: str, code: str) -> str:
        """通过AST精确提取类代码"""
        try:
            tree = ast.parse(code)
            lines = code.split('\n')
            
            class_node = self.get_class_node_by_name(class_name, tree)
            if not class_node or not hasattr(class_node, 'end_lineno'):
                return ""
            
            # 使用 Python 3.8+ 的 end_lineno 属性
            start_line = class_node.lineno - 1
            end_line = class_node.end_lineno
            
            return '\n'.join(lines[start_line:end_line])
            
        except Exception as e:
            print(f"提取类 {class_name} 代码时出错: {e}")
            return ""
    
    def is_function_modified(self, func_name: str, code_before: str, code_after: str) -> bool:
        """检查函数是否被修改"""
        func_before = self.extract_function_code_by_ast(func_name, code_before)
        func_after = self.extract_function_code_by_ast(func_name, code_after)
        
        if not func_before or not func_after:
            return False
        
        # 标准化代码以便比较（去除空行和缩进差异）
        func_before_normalized = self.normalize_code(func_before)
        func_after_normalized = self.normalize_code(func_after)
        
        is_modified = func_before_normalized != func_after_normalized
        
        if is_modified:
            print(f"函数 {func_name} 被修改")
            print(f"修改前长度: {len(func_before)} 字符")
            print(f"修改后长度: {len(func_after)} 字符")
        
        return is_modified
    
    def is_class_modified(self, class_name: str, code_before: str, code_after: str) -> bool:
        """检查类是否被修改"""
        class_before = self.extract_class_code_by_ast(class_name, code_before)
        class_after = self.extract_class_code_by_ast(class_name, code_after)
        
        if not class_before or not class_after:
            return False
        
        class_before_normalized = self.normalize_code(class_before)
        class_after_normalized = self.normalize_code(class_after)
        
        is_modified = class_before_normalized != class_after_normalized
        
        if is_modified:
            print(f"类 {class_name} 被修改")
        
        return is_modified
    
    def is_method_modified(self, method_name: str, code_before: str, code_after: str) -> bool:
        """检查方法是否被修改"""
        if '.' not in method_name:
            return False
        
        class_name, method = method_name.split('.', 1)
        
        class_before = self.extract_class_code_by_ast(class_name, code_before)
        class_after = self.extract_class_code_by_ast(class_name, code_after)
        
        if not class_before or not class_after:
            return False
        
        # 在类代码中查找方法
        method_before = self.extract_function_code_by_ast(method, class_before)
        method_after = self.extract_function_code_by_ast(method, class_after)
        
        if not method_before or not method_after:
            return False
        
        method_before_normalized = self.normalize_code(method_before)
        method_after_normalized = self.normalize_code(method_after)
        
        is_modified = method_before_normalized != method_after_normalized
        
        if is_modified:
            print(f"方法 {method_name} 被修改")
        
        return is_modified
    
    def normalize_code(self, code: str) -> str:
        """标准化代码以便比较"""
        # 移除空行，标准化空白字符
        lines = []
        for line in code.split('\n'):
            stripped = line.strip()
            if stripped:  # 只保留非空行
                lines.append(stripped)
        
        return '\n'.join(lines)
    
    def format_results(self, changes: List[CodeChange]) -> str:
        """格式化输出结果"""
        if not changes:
            return "没有检测到类或函数的变更。"
        
        result = []
        
        # 按变更类型分组
        added = [c for c in changes if c.change_type == 'added']
        modified = [c for c in changes if c.change_type == 'modified']
        deleted = [c for c in changes if c.change_type == 'deleted']
        
        if added:
            result.append("🟢 新增:")
            for change in sorted(added, key=lambda x: x.name):
                result.append(f"  - {change.code_type}: {change.name}")
        
        if modified:
            result.append("🟡 修改:")
            for change in sorted(modified, key=lambda x: x.name):
                result.append(f"  - {change.code_type}: {change.name}")
        
        if deleted:
            result.append("🔴 删除:")
            for change in sorted(deleted, key=lambda x: x.name):
                result.append(f"  - {change.code_type}: {change.name}")
        
        return '\n'.join(result)