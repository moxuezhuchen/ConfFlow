import shutil
import sys
from datetime import datetime
from typing import Optional, List
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

# 初始化全局 Console 实例（底层真实对象）
# 强制关闭颜色和终端特性，确保输出一致性（纯文本，无 ANSI 码）
_console = Console(
    theme=custom_theme,
    soft_wrap=False,
    force_terminal=False,
    color_system=None,
    no_color=True,
    width=target_width,
)


class _ConsoleProxy:
    """A proxy that keeps Rich Console output bound to the *current* sys.stdout.

    Why: pytest's `capsys` replaces `sys.stdout` per-test; a Console created at import-time
    would otherwise keep writing to the original stdout (making `capsys.readouterr()` empty).

    This proxy also helps CLI redirection: when CLI redirects sys.stdout to a txt file, all
    `console.print()` calls follow automatically.
    """

    def __init__(self, inner: Console):
        self._inner = inner

    def _sync(self) -> None:
        try:
            # Always follow the current sys.stdout (which may be redirected/captured)
            self._inner.file = sys.stdout  # type: ignore[attr-defined]
        except Exception:
            try:
                setattr(self._inner, "file", sys.stdout)
            except Exception:
                pass

    def __getattr__(self, name: str):
        self._sync()
        return getattr(self._inner, name)


# Exported global console used across the project
console = _ConsoleProxy(_console)

# ============================================================================
# 新输出格式常量
# ============================================================================
LINE_WIDTH = _console.width
DOUBLE_LINE = "=" * LINE_WIDTH
SINGLE_LINE = "─" * LINE_WIDTH


def print_step_header(step_idx: int, total_steps: int, name: str, step_type: str, input_count: int, width: Optional[int] = None):
    """打印标准化的步骤标题（新格式）"""
    console.print()
    header = f"[Step {step_idx}/{total_steps}] {name} | {step_type}"
    right_info = f"Input: {input_count}"
    # 计算填充使右侧信息靠右
    padding = LINE_WIDTH - len(header) - len(right_info)
    if padding < 1:
        padding = 1
    console.print(f"{header}{' ' * padding}{right_info}")
    console.print(SINGLE_LINE)


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

# Compatibility, concise English helpers (preferred)
def info(message: str):
    """INFO: message"""
    console.print(f"INFO: {message}")

def success(message: str):
    """SUCCESS: message"""
    console.print(f"SUCCESS: {message}")

def warning(message: str):
    """WARNING: message"""
    console.print(f"WARNING: {message}")

def error(message: str):
    """ERROR: message"""
    console.print(f"ERROR: {message}")

def heading(title: str):
    """Print a short English heading using a rule"""
    console.rule(title)

def print_table(tbl):
    """Print a Rich Table (or fallback)"""
    console.print(tbl)


# ============================================================================
# 新输出格式函数
# ============================================================================

def print_workflow_header(input_file: str, input_count: int):
    """打印工作流开始头部"""
    console.print(DOUBLE_LINE)
    title = "ConfFlow v1.0"
    console.print(f"{title:^{LINE_WIDTH}}")
    started = f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    console.print(f"{started:^{LINE_WIDTH}}")
    inp = f"Input: {input_file} ({input_count} conformer{'s' if input_count > 1 else ''})"
    console.print(f"{inp:^{LINE_WIDTH}}")
    console.print(DOUBLE_LINE)


def print_step_result(status: str, in_count: int, out_count: int, failed: int, duration: str):
    """打印步骤完成结果行"""
    mark = "✓" if status in ("completed", "skipped", "skipped_multi_frame") else "✗"
    failed_str = f" ({failed} failed)" if failed > 0 else ""
    console.print(f"  {mark} {status.capitalize()} | {in_count} → {out_count}{failed_str} | {duration}")


def print_final_report_header():
    """打印最终报告头部"""
    console.print()
    console.print(DOUBLE_LINE)
    title = "FINAL REPORT"
    console.print(f"{title:^{LINE_WIDTH}}")
    finished = f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    console.print(f"{finished:^{LINE_WIDTH}}")


def print_section_header(title: str):
    """打印报告区块标题"""
    console.print()
    console.print(title)
    console.print(SINGLE_LINE)


def print_workflow_end():
    """打印工作流结束"""
    console.print(DOUBLE_LINE)


def format_step_table(steps: List[dict]) -> str:
    """格式化步骤表格"""
    lines = []
    header = f"  {'Step':>4}   {'Name':<10}  {'Type':<8}  {'Status':<10}  {'In':>5}  {'Out':>5}  {'Failed':>6}  {'Time':>10}"
    lines.append(header)
    
    for step in steps:
        idx = step.get('index', 0)
        name = str(step.get('name', ''))[:10]
        stype = str(step.get('type', ''))[:8]
        status = str(step.get('status', 'unknown'))[:10]
        inp = step.get('input_conformers', 0)
        out = step.get('output_conformers', 0)
        failed = step.get('failed_conformers')
        dur = step.get('duration_str', '')
        
        failed_str = "-" if failed is None else str(int(failed))
        
        line = f"  {idx:>4}   {name:<10}  {stype:<8}  {status:<10}  {inp:>5}  {out:>5}  {failed_str:>6}  {dur:>10}"
        lines.append(line)
    
    return "\n".join(lines)


def format_conformer_table(conformers: List[dict]) -> str:
    """格式化构象能量表格"""
    lines = []
    header = f"  {'Rank':>4}  {'Energy (Ha)':>14}  {'ΔG (kcal)':>11}  {'Pop (%)':>9}  {'Imag':>5}  {'TSBond':>10}"
    lines.append(header)
    
    for conf in conformers:
        rank = conf.get('rank', 0)
        energy = conf.get('energy')
        dg = conf.get('dg', 0.0)
        pop = conf.get('pop', 0.0)
        imag = conf.get('imag', '-')
        tsbond = conf.get('tsbond', '-')
        
        e_str = f"{energy:.7f}" if energy is not None else "N/A"
        tsbond_str = f"{float(tsbond):.4f}" if tsbond not in ('-', None) else "-"
        
        line = f"  {rank:>4}  {e_str:>14}  {dg:>11.2f}  {pop:>9.1f}  {str(imag):>5}  {tsbond_str:>10}"
        lines.append(line)
    
    return "\n".join(lines)


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


def redirect_console(stream=None) -> None:
    """Force the underlying Rich Console to write to `stream` (defaults to sys.stdout)."""
    if stream is None:
        stream = sys.stdout
    try:
        _console.file = stream  # type: ignore[attr-defined]
    except Exception:
        try:
            setattr(_console, "file", stream)
        except Exception:
            pass
