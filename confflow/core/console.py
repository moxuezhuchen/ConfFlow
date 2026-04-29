#!/usr/bin/env python3
"""Rich console output utilities and formatting helpers."""

from __future__ import annotations

import io
import os
import sys
import textwrap
import time
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.theme import Theme

__all__ = [
    "custom_theme",
    "FIXED_WIDTH",
    "target_width",
    "console",
    "LINE_WIDTH",
    "DOUBLE_LINE",
    "SINGLE_LINE",
    "print_step_header",
    "wrap_text",
    "print_kv",
    "print_info",
    "print_success",
    "print_warning",
    "print_error",
    "info",
    "success",
    "warning",
    "error",
    "heading",
    "print_table",
    "print_workflow_header",
    "print_step_result",
    "print_final_report_header",
    "print_section_header",
    "print_workflow_end",
    "format_step_table",
    "format_conformer_table",
    "DummyProgress",
    "create_progress",
    "CalcProgressReporter",
    "redirect_console",
    "require_existing_path",
]

# ---------------------------------------------------------------------------
# Pastel theme — soft, low-saturation colours
# ---------------------------------------------------------------------------
custom_theme = Theme(
    {
        # Brand / structural
        "brand": "bold #A8DADC",  # soft teal — titles, brand name
        "step_tag": "bold #BDB2FF",  # lavender — step badges
        "sep": "#555577",  # muted indigo — separator characters
        # Message levels
        "info": "#7EC8E3",  # sky blue
        "warning": "#FFD166",  # amber
        "error": "#EF8585",  # coral
        "success": "#95D5B2",  # sage green
        # Text roles
        "label": "#A0A0B8",  # silver-blue — kv labels
        "muted": "#777799",  # dimmed — secondary values
        "hi": "bold",  # highlight numbers inline
    }
)

# Fixed output width: 80 columns
FIXED_WIDTH = 80
target_width = FIXED_WIDTH

# ---------------------------------------------------------------------------
# Console — auto-detect colour capability (TTY → colour, pipe → plain)
# ---------------------------------------------------------------------------
_console = Console(
    theme=custom_theme,
    soft_wrap=False,
    highlight=False,  # prevent Rich from auto-styling numbers/paths
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
            stream = sys.stdout
            self._inner.file = stream  # type: ignore[attr-defined]
            # Re-evaluate ANSI capability: disable colour for non-TTY destinations
            # (e.g. pytest capsys capture buffers or redirected .txt files).
            is_tty = hasattr(stream, "isatty") and stream.isatty()
            self._inner._force_terminal = is_tty  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass

    def __getattr__(self, name: str):
        self._sync()
        return getattr(self._inner, name)


# Exported global console used across the project
console = _ConsoleProxy(_console)

# ============================================================================
# Output format constants
# ============================================================================
LINE_WIDTH = _console.width
DOUBLE_LINE = "═" * LINE_WIDTH
SINGLE_LINE = "─" * LINE_WIDTH


def _render_table_to_str(table: Table) -> str:
    """Render a Rich Table to a plain string (no ANSI) for test assertions."""
    buf = io.StringIO()
    tmp = Console(file=buf, no_color=True, highlight=False, width=LINE_WIDTH, theme=custom_theme)
    tmp.print(table)
    return buf.getvalue()


def print_step_header(
    step_idx: int,
    total_steps: int,
    name: str,
    step_type: str,
    input_count: int,
    width: int | None = None,
) -> None:
    """Print a standardised step header."""
    console.print()
    use_width = width or LINE_WIDTH

    badge = f"[ {step_idx} / {total_steps} ]"
    prog_part = step_type.upper()
    left_plain = f"{badge}  {name}  ·  {prog_part}"
    conf_word = "conformer" if input_count == 1 else "conformers"
    right_plain = f"Input: {input_count} {conf_word}"
    padding = max(1, use_width - len(left_plain) - len(right_plain))
    console.print(
        f"[step_tag]{badge}[/step_tag]  [bold]{name}[/bold]  ·  {prog_part}"
        f"{' ' * padding}[muted]{right_plain}[/muted]"
    )
    console.print(f"[sep]{SINGLE_LINE}[/sep]")


def wrap_text(
    text: str, width: int | None = None, initial_indent: str = "", subsequent_indent: str = ""
) -> list[str]:
    """Wrap text to a fixed width to prevent long lines from breaking layout.

    Parameters
    ----------
    text : str
        The text to wrap.
    width : int or None
        Maximum line width (defaults to ``LINE_WIDTH``).
    initial_indent : str
        Indent string for the first line.
    subsequent_indent : str
        Indent string for subsequent lines.

    Returns
    -------
    list[str]
        Wrapped lines.
    """
    use_width = width or LINE_WIDTH
    if text is None:
        return [initial_indent]

    wrapped = textwrap.wrap(
        str(text),
        width=max(20, use_width),
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return wrapped or [initial_indent]


def print_kv(
    label: str, value: str, indent: int = 2, label_width: int = 10, width: int | None = None
) -> None:
    """Print an aligned key-value line, auto-wrapping long values."""
    use_width = width or LINE_WIDTH
    prefix_plain = " " * indent + f"{label:<{label_width}}  "
    continuation = " " * len(prefix_plain)
    content_width = max(20, use_width - len(prefix_plain))
    lines = textwrap.wrap(
        str(value),
        width=content_width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not lines:
        console.print(f"[label]{' ' * indent}{label:<{label_width}}[/label]  ")
        return
    console.print(f"[label]{' ' * indent}{label:<{label_width}}[/label]  {lines[0]}")
    for ln in lines[1:]:
        console.print(continuation + ln)


def print_info(message: str) -> None:
    """Print an info-level message."""
    console.print(f"  [info]○[/info]  {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"  [success]✔[/success]  {message}")


def print_warning(message: str) -> None:
    """Print a warning message."""
    console.print(f"  [warning]⚠[/warning]  {message}")


def print_error(message: str) -> None:
    """Print an error message."""
    console.print(f"  [error]✘[/error]  {message}")


# Keep the short aliases for compatibility with older call sites.
info = print_info
success = print_success
warning = print_warning
error = print_error


def heading(title: str) -> None:
    """Print a short heading using a rule."""
    console.rule(title)


def print_table(tbl) -> None:
    """Print a Rich Table (or any printable)."""
    console.print(tbl)


# ============================================================================
# Workflow-level print functions
# ============================================================================


def print_workflow_header(input_file: str, input_count: int) -> None:
    """Print the workflow start header."""
    console.print()
    console.print(f"[sep]{DOUBLE_LINE}[/sep]")
    title = "✦  ConfFlow  ✦"
    console.print(f"[brand]{title:^{LINE_WIDTH}}[/brand]")
    console.print(f"[sep]{DOUBLE_LINE}[/sep]")
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conf_word = "conformer" if input_count == 1 else "conformers"
    print_kv("Started", started)
    print_kv("Input", f"{input_file}  ·  {input_count} {conf_word}")
    console.print(f"[sep]{SINGLE_LINE}[/sep]")


def print_step_result(
    status: str, in_count: int, out_count: int, failed: int, duration: str
) -> None:
    """Print the step completion result line."""
    ok = status in ("completed", "skipped", "skipped_multi_frame")
    mark_style = "success" if ok else "error"
    mark = "✔" if ok else "✘"
    status_label = status.capitalize()
    failed_part = f"  [error]{failed} failed[/error]" if failed > 0 else ""
    console.print(
        f"  [{mark_style}]{mark} {status_label}[/{mark_style}]"
        f"    [hi]{in_count}[/hi] → [hi]{out_count}[/hi]{failed_part}"
        f"  [muted]{duration}[/muted]"
    )


def print_final_report_header() -> None:
    """Print the final report header."""
    console.print()
    console.print(f"[sep]{DOUBLE_LINE}[/sep]")
    console.print(f"[brand]{'WORKFLOW SUMMARY':^{LINE_WIDTH}}[/brand]")
    finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(f"[muted]{'Finished: ' + finished:^{LINE_WIDTH}}[/muted]")


def print_section_header(title: str) -> None:
    """Print a report section title."""
    console.print()
    console.print(f"[brand]{title}[/brand]")
    console.print(f"[sep]{SINGLE_LINE}[/sep]")


def print_workflow_end() -> None:
    """Print the workflow end marker."""
    console.print(f"[sep]{DOUBLE_LINE}[/sep]")


def format_step_table(steps: list[dict]) -> str:
    """Format the step summary as a plain string (using Rich Table internally)."""
    tbl = Table(
        show_header=True,
        header_style="label",
        box=None,
        padding=(0, 1),
        show_edge=False,
    )
    tbl.add_column("Step", justify="right", style="muted", min_width=4)
    tbl.add_column("Name", justify="left", min_width=12)
    tbl.add_column("Type", justify="left", style="muted", min_width=8)
    tbl.add_column("Status", justify="left", min_width=10)
    tbl.add_column("In", justify="right", style="muted", min_width=5)
    tbl.add_column("Out", justify="right", min_width=5)
    tbl.add_column("Failed", justify="right", min_width=6)
    tbl.add_column("Time", justify="right", style="muted", min_width=10)

    for step in steps:
        idx = str(step.get("index", 0))
        name = str(step.get("name", ""))
        stype = str(step.get("type", ""))
        status = str(step.get("status", "unknown"))
        inp = str(step.get("input_conformers", 0))
        out = str(step.get("output_conformers", 0))
        failed = step.get("failed_conformers")
        dur = str(step.get("duration_str", step.get("duration_seconds", "")))

        failed_str = "–" if failed is None else str(int(failed))

        if status == "completed":
            status_cell = "[success]✔ done[/success]"
        elif status in ("skipped", "skipped_multi_frame"):
            status_cell = "[muted]– skip[/muted]"
        elif status == "failed":
            status_cell = "[error]✘ fail[/error]"
        else:
            status_cell = status

        tbl.add_row(idx, name, stype, status_cell, inp, out, failed_str, dur)

    return _render_table_to_str(tbl)


def format_conformer_table(conformers: list[dict]) -> str:
    """Format the conformer energy table as a plain string (using Rich Table internally)."""
    tbl = Table(
        show_header=True,
        header_style="label",
        box=None,
        padding=(0, 1),
        show_edge=False,
    )
    tbl.add_column("Rank", justify="right", style="muted", min_width=4)
    tbl.add_column("Energy (Ha)", justify="right", min_width=14)
    tbl.add_column("ΔG (kcal)", justify="right", style="muted", min_width=10)
    tbl.add_column("Pop (%)", justify="right", style="muted", min_width=8)
    tbl.add_column("Imag", justify="right", min_width=5)
    tbl.add_column("TSBond", justify="right", style="muted", min_width=10)

    for conf in conformers:
        rank = str(conf.get("rank", 0))
        energy = conf.get("energy")
        dg = conf.get("dg", 0.0)
        pop = conf.get("pop", 0.0)
        imag = conf.get("imag", "-")
        tsbond = conf.get("tsbond", "-")

        e_str = f"{energy:.7f}" if energy is not None else "N/A"
        try:
            tsbond_str = f"{float(tsbond):.4f}" if tsbond not in ("-", None) else "-"
        except (ValueError, TypeError):
            tsbond_str = str(tsbond)

        imag_str = str(imag)
        try:
            if imag_str not in ("-", "0", "") and int(imag_str) > 0:
                imag_str = f"[warning]{imag_str}[/warning]"
        except (ValueError, TypeError):
            pass

        row_style = "bold" if rank == "1" else ""
        tbl.add_row(rank, e_str, f"{dg:.2f}", f"{pop:.1f}", imag_str, tsbond_str, style=row_style)

    return _render_table_to_str(tbl)


class CalcProgressReporter:
    """Plain-text periodic progress reporter for QM calculation tasks.

    Prints one line every *report_every* completed tasks, plus a final summary.

    Output format (during run)::

        ·  opt1         10 / 42   ✔  9  ✘  1   elapsed 00:05:30  eta 00:17:42

    Output format (on exit)::

        ·  opt1         42 / 42   ✔ 39  ✘  3   total   00:23:08
    """

    def __init__(self, total: int, label: str = "Calc", report_every: int | None = None) -> None:
        self.total = total
        self.label = label
        self.report_every = report_every if report_every is not None else max(1, total // 10)
        self._done = 0
        self._ok = 0
        self._fail = 0
        self._start: float = 0.0

    def __enter__(self) -> CalcProgressReporter:
        self._start = time.time()
        return self

    def __exit__(self, *_) -> None:
        elapsed = time.time() - self._start
        w = len(str(self.total))
        console.print(
            f"  ·  [bold]{self.label:<10}[/bold]"
            f"  [hi]{self._done:>{w}}[/hi] / [hi]{self.total}[/hi]"
            f"   [success]✔[/success] [hi]{self._ok}[/hi]"
            f"  [error]✘[/error] [hi]{self._fail}[/hi]"
            f"   [muted]total   {self._fmt(elapsed)}[/muted]"
        )

    def report(self, status: str) -> None:
        """Record one completed task and print a progress line if due."""
        self._done += 1
        if status in ("success", "skipped"):
            self._ok += 1
        else:
            self._fail += 1

        if self._done % self.report_every == 0 or self._done == self.total:
            elapsed = time.time() - self._start
            w = len(str(self.total))
            console.print(
                f"  ·  [bold]{self.label:<10}[/bold]"
                f"  [hi]{self._done:>{w}}[/hi] / [hi]{self.total}[/hi]"
                f"   [success]✔[/success] [hi]{self._ok}[/hi]"
                f"  [error]✘[/error] [hi]{self._fail}[/hi]"
                f"   [muted]elapsed {self._fmt(elapsed)}  eta {self._eta(elapsed)}[/muted]"
            )

    @staticmethod
    def _fmt(seconds: float) -> str:
        s = int(seconds)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"

    def _eta(self, elapsed: float) -> str:
        if self._done == 0:
            return "--:--:--"
        remaining = (elapsed / self._done) * (self.total - self._done)
        return self._fmt(remaining)


class DummyProgress:
    """No-op progress bar (interface-compatible with Rich Progress)."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def add_task(self, *args, **kwargs):
        return 0

    def advance(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass


def create_progress():
    """Return a DummyProgress (animated bars disabled for HPC log compatibility)."""
    return DummyProgress()


def redirect_console(stream=None) -> None:
    """Force the underlying Rich Console to write to *stream*.

    Also re-evaluates terminal capability: if *stream* is not a TTY (e.g. a
    plain file), ANSI escape codes are suppressed so the .txt output stays
    human-readable.
    """
    if stream is None:
        stream = sys.stdout
    try:
        _console.file = stream  # type: ignore[attr-defined]
        # Re-check whether the new destination is a real terminal.
        # _force_terminal=None lets Rich call isatty() on each write; setting it
        # to False explicitly disables ANSI codes for non-TTY destinations.
        is_tty = hasattr(stream, "isatty") and stream.isatty()
        _console._force_terminal = is_tty  # type: ignore[attr-defined]
    except AttributeError:
        pass


# ============================================================================
# CLI helper
# ============================================================================


def require_existing_path(path: str, label: str = "File", *, exit_code: int = 2) -> None:
    """Raise SystemExit if *path* does not exist."""
    if not os.path.exists(path):
        raise SystemExit(f"{label} does not exist: {path}")
