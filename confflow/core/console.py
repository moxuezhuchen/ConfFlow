import shutil
from typing import Optional
from rich.console import Console
from rich.theme import Theme
from rich.panel import Panel
from rich import box
from rich.text import Text

# 定义自定义主题
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "magenta",
    "error": "bold red",
    "success": "green",
    "step": "bold blue",
    "highlight": "bold yellow",
})

# 设定 Console 宽度为终端宽度的 4/5 (80%)
term_width = shutil.get_terminal_size((100, 20)).columns
target_width = max(60, int(term_width * 0.8))

# 初始化全局 Console 实例
# 强制关闭颜色和终端特性，确保输出一致性（纯文本，无 ANSI 码）
console = Console(theme=custom_theme, soft_wrap=False, force_terminal=False, color_system=None, no_color=True, width=target_width)


def print_step_header(step_idx: int, total_steps: int, name: str, step_type: str, input_count: int, width: Optional[int] = None):
    """打印标准化的步骤标题"""
    from rich.table import Table
    
    # 使用 Table.grid 实现左右对齐的标题栏
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", style="bold white")
    grid.add_column(justify="right", style="cyan")
    
    step_info = f"Step {step_idx:02d}/{total_steps:02d}: {name}"
    meta_info = f"Type: {step_type} | Input: {input_count}"
    
    grid.add_row(step_info, meta_info)
    
    console.print()
    console.print(Panel(grid, border_style="blue", expand=True, box=box.ASCII))


def print_info(message: str):
    """打印信息日志"""
    console.print(f"[info]INFO:[/info] {message}")


def print_success(message: str):
    """打印成功日志"""
    console.print(f"[success]SUCCESS:[/success] {message}")


def print_warning(message: str):
    """打印警告日志"""
    console.print(f"[warning]WARNING:[/warning] {message}")


def print_error(message: str):
    """打印错误日志"""
    console.print(f"[error]ERROR:[/error] {message}")


class DummyProgress:
    """哑进度条，用于禁用进度条显示但保持接口兼容"""
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def add_task(self, *args, **kwargs): return 0
    def advance(self, *args, **kwargs): pass
    def update(self, *args, **kwargs): pass


def create_progress():
    """
    创建一个标准样式的 Progress 对象。
    由于强制关闭了终端特性，统一返回 DummyProgress 以避免打印非动画的进度日志刷屏。
    """
    return DummyProgress()
