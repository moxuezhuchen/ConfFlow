#!/usr/bin/env python3

"""Formatting helpers for ORCA ``%block ... end`` sections."""

from __future__ import annotations

from typing import Any

__all__ = ["format_orca_blocks"]


def format_orca_blocks(blocks: Any) -> str:
    """Convert a dict or string into ORCA ``%block ... end`` syntax."""
    if not blocks:
        return ""

    if isinstance(blocks, str):
        content = blocks.strip()
        if not content:
            return ""
        return content + "\n"

    def _fmt_val(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _render_content(content: Any, indent: int = 2) -> list[str]:
        lines: list[str] = []
        prefix = " " * indent
        if isinstance(content, dict):
            for key, value in content.items():
                if isinstance(value, (dict, list, tuple)):
                    lines.append(f"{prefix}{key}")
                    lines.extend(_render_content(value, indent + 2))
                    lines.append(f"{prefix}end")
                else:
                    lines.append(f"{prefix}{key} {_fmt_val(value)}")
        elif isinstance(content, (list, tuple)):
            for item in content:
                lines.append(f"{prefix}{_fmt_val(item)}")
        elif isinstance(content, str):
            for line in content.strip().splitlines():
                lines.append(f"{prefix}{line.strip()}")
        elif content is not None:
            lines.append(f"{prefix}{_fmt_val(content)}")
        return lines

    result: list[str] = []
    for block_name, content in blocks.items():
        result.append(f"%{block_name}")
        result.extend(_render_content(content))
        result.append("end")

    return "\n".join(result) + "\n"
