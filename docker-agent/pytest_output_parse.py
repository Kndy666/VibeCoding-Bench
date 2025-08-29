import re
from typing import Dict, List, Optional, Tuple, Set
from enum import Enum
from collections import defaultdict


class TestStatus(Enum):
    """测试状态枚举"""
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class PytestResultParser:
    """
    解析pytest输出结果的工具类
    支持解析 pytest -q -rA --tb=np 格式的输出
    """
    
    def __init__(self, output: str):
        """
        初始化解析器
        
        Args:
            output: pytest的输出字符串
        """
        self.output = output
        self.test_results: Dict[str, TestStatus] = {}
        self._parse_output()
    
    def _clean_ansi_codes(self, text: str) -> str:
        """清理ANSI转义码"""
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        return ansi_escape.sub('', text)
    
    def _parse_output(self):
        """解析pytest输出"""
        clean_output = self._clean_ansi_codes(self.output)
        
        # 查找 "short test summary info" 部分
        summary_start = clean_output.find("short test summary info")
        if summary_start == -1:
            # 如果没有找到summary部分，可能是所有测试都通过了，尝试从整个输出中解析
            self._parse_from_full_output(clean_output)
            return
        
        # 提取summary部分后的内容
        summary_section = clean_output[summary_start:]
        
        # 按行分割并解析每一行
        lines = summary_section.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # 解析测试结果行
            self._parse_test_line(line)
    
    def _parse_from_full_output(self, clean_output: str):
        """从完整输出中解析测试结果（当没有summary section时）"""
        lines = clean_output.split('\n')
        for line in lines:
            line = line.strip()
            # 查找包含测试结果的行
            if any(status.value in line for status in TestStatus):
                self._parse_test_line(line)
    
    def _parse_test_line(self, line: str):
        """解析单行测试结果"""
        # 匹配格式: STATUS test_file.py::TestClass::test_method[params] - error_message
        # 或者: STATUS test_file.py::test_function
        pattern = r'^(PASSED|FAILED|SKIPPED|ERROR)\s+(.+?)(?:\s-\s.*)?$'
        
        match = re.match(pattern, line)
        if match:
            status_str = match.group(1)
            test_path = match.group(2).strip()
            
            try:
                status = TestStatus(status_str)
                self.test_results[test_path] = status
            except ValueError:
                # 如果状态无法识别，标记为UNKNOWN
                self.test_results[test_path] = TestStatus.UNKNOWN
    
    def _get_base_test_name(self, test_path: str) -> str:
        """
        获取测试的基础名称（去掉参数化部分）
        
        Args:
            test_path: 完整的测试路径
            
        Returns:
            基础测试名称
        """
        # 去掉参数化部分 [param1-param2-param3]
        base_name = test_path.split('[')[0] if '[' in test_path else test_path
        return base_name
    
    def _aggregate_parametrized_results(self, test_results: Dict[str, TestStatus]) -> TestStatus:
        """
        聚合参数化测试的结果
        规则：
        - 如果所有结果都为passed或含有skipped(至少有一个passed)返回passed
        - 只要有一个failed、errored、unknown，都返回failed
        
        Args:
            test_results: 同一基础测试名称的所有参数化结果
            
        Returns:
            聚合后的测试状态
        """
        if not test_results:
            return TestStatus.UNKNOWN
        
        statuses = list(test_results.values())
        
        # 如果有任何failed、error或unknown，返回failed
        if any(status in [TestStatus.FAILED, TestStatus.ERROR, TestStatus.UNKNOWN] for status in statuses):
            return TestStatus.FAILED
        
        # 如果所有都是passed或skipped，且至少有一个passed，返回passed
        if all(status in [TestStatus.PASSED, TestStatus.SKIPPED] for status in statuses):
            if any(status == TestStatus.PASSED for status in statuses):
                return TestStatus.PASSED
            else:
                # 全部都是skipped
                return TestStatus.SKIPPED
        
        # 其他情况返回unknown
        return TestStatus.UNKNOWN
    
    def get_test_status(self, test_pattern: str) -> Optional[TestStatus]:
        """
        获取指定测试的状态
        对于参数化测试，会自动聚合所有参数的结果
        
        Args:
            test_pattern: 测试模式，如 "test_api_jws.py::TestJWS::test_encode_with_jwk"
        
        Returns:
            测试状态枚举，如果未找到返回None
        """
        # 先检查是否有精确匹配
        if test_pattern in self.test_results:
            return self.test_results[test_pattern]
        
        # 检查是否是基础测试名称（可能有参数化变体）
        base_name = self._get_base_test_name(test_pattern)
        parametrized_results = {}
        
        for test_path, status in self.test_results.items():
            if self._get_base_test_name(test_path) == base_name:
                parametrized_results[test_path] = status
        
        if parametrized_results:
            return self._aggregate_parametrized_results(parametrized_results)
        
        return None
    
    def query_tests(self, test_patterns: List[str]) -> Dict[str, TestStatus]:
        """
        查询多个测试的状态
        
        Args:
            test_patterns: 测试模式列表
        
        Returns:
            测试模式到状态的映射字典
        """
        results = {}
        for pattern in test_patterns:
            status = self.get_test_status(pattern)
            results[pattern] = status if status else TestStatus.UNKNOWN
        return results
    
    def find_tests_by_pattern(self, pattern: str) -> Dict[str, TestStatus]:
        """
        根据模式查找匹配的测试
        
        Args:
            pattern: 正则表达式模式或简单字符串匹配
        
        Returns:
            匹配的测试及其状态
        """
        results = {}
        
        # 如果pattern包含正则表达式特殊字符，使用正则匹配
        if any(char in pattern for char in r'.*+?^${}[]|()\\/'):
            regex = re.compile(pattern)
            for test_path, status in self.test_results.items():
                if regex.search(test_path):
                    results[test_path] = status
        else:
            # 简单字符串匹配
            for test_path, status in self.test_results.items():
                if pattern in test_path:
                    results[test_path] = status
        
        return results
    
    def find_tests_by_base_name(self, base_pattern: str) -> Dict[str, TestStatus]:
        """
        根据基础测试名称查找所有参数化测试
        
        Args:
            base_pattern: 基础测试名称，如 "test_max_chat_history"
        
        Returns:
            所有匹配的参数化测试及其状态
        """
        results = {}
        for test_path, status in self.test_results.items():
            # 提取基础测试名称（去掉参数化部分）
            base_test = self._get_base_test_name(test_path)
            if base_pattern in base_test:
                results[test_path] = status
        return results
    
    def get_all_results(self) -> Dict[str, TestStatus]:
        """获取所有解析的测试结果"""
        return self.test_results.copy()
    
    def filter_tests_by_status(self, expected_statuses: Optional[List[TestStatus]] = None) -> Set[str]:
        """
        根据期望状态筛选符合期望状态的测试项（已聚合参数化结果）。

        Args:
            expected_statuses: 期望的状态列表（例如 [TestStatus.PASSED]）

        Returns:
            匹配到且聚合后状态在 expected_statuses 中的基础测试路径集合
        """
        if expected_statuses is None or not expected_statuses:
            expected_statuses = [TestStatus.PASSED]

        matched: Set[str] = set()
        # 按基础测试名称分组
        base_test_groups = defaultdict(dict)
        for test_path, status in self.test_results.items():
            base_name = self._get_base_test_name(test_path)
            base_test_groups[base_name][test_path] = status

        # 聚合并筛选
        for base_name, group_results in base_test_groups.items():
            aggregated = self._aggregate_parametrized_results(group_results)
            if aggregated in expected_statuses:
                matched.add(base_name)

        return matched
    
    def get_summary(self) -> Dict[TestStatus, int]:
        """
        获取测试结果统计
        
        Returns:
            状态到数量的映射
        """
        summary = {status: 0 for status in TestStatus}
        for status in self.test_results.values():
            summary[status] += 1
        return summary
    
    def get_aggregated_summary(self) -> Dict[TestStatus, int]:
        """
        获取聚合后的测试结果统计（参数化测试按基础名称聚合）
        
        Returns:
            状态到数量的映射
        """
        # 按基础测试名称分组
        base_test_groups = defaultdict(dict)
        for test_path, status in self.test_results.items():
            base_name = self._get_base_test_name(test_path)
            base_test_groups[base_name][test_path] = status
        
        # 聚合每个基础测试的结果
        aggregated_results = {}
        for base_name, test_results in base_test_groups.items():
            aggregated_results[base_name] = self._aggregate_parametrized_results(test_results)
        
        # 统计聚合后的结果
        summary = {status: 0 for status in TestStatus}
        for status in aggregated_results.values():
            summary[status] += 1
        
        return summary
    
    def check_all_tests_status(self, test_patterns: List[str], expected_statuses: List[TestStatus] = None) -> Tuple[bool, Dict[str, TestStatus]]:
        """
        检查指定测试是否都符合期望状态
        
        Args:
            test_patterns: 要检查的测试模式列表
            expected_statuses: 期望的状态列表，默认为[PASSED]
        
        Returns:
            (是否全部符合期望, 实际状态字典)
        """
        if expected_statuses is None:
            expected_statuses = [TestStatus.PASSED]
        
        results = self.query_tests(test_patterns)
        all_expected = all(status in expected_statuses for status in results.values())
        
        return all_expected, results