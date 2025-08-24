import json
import os
from rich.console import Console
from rich.text import Text
from rich.panel import Panel

console = Console()

def display_logs(file_path):
    if not os.path.exists(file_path):
        console.print(f"[bold red]文件 {file_path} 不存在！[/bold red]")
        return

    with open(file_path, 'r', encoding='utf-8') as file:
        try:
            logs = json.load(file)
        except json.JSONDecodeError as e:
            console.print(f"[bold red]无法解析 JSON 文件: {e}[/bold red]")
            return

    # 将所有日志合并为一个纯文本块，保留原始换行/空格，避免逐条 Panel 导致宽度不一
    parts = []
    for key, value in logs.items():
        parts.append(f"=== {key.upper()} ===")
        for log_type, log_content in value.items():
            parts.append(f"-- {log_type.upper()} --")
            # 保证日志内容为字符串并保留内部换行
            parts.append(str(log_content))

    log_text = "\n".join(parts)

    # 设置 LESS 环境变量：-R 保留颜色，-S 禁用自动换行以启用水平截断/滚动
    os.environ.setdefault("LESS", "-RS")

    # 使用 rich 的 pager，一次性打印整个文本块，禁用 soft_wrap 以防 rich 自动换行
    with console.pager():
        console.print(log_text, markup=False, soft_wrap=False)

if __name__ == "__main__":
    # 替换为你的日志文件路径
    log_file_path = "/home/kndy666/Programming/Agent/docker-agent/logs/test_logs.json"
    display_logs(log_file_path)
