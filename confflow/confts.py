#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""confts - TS 专用入口（当前主要提供 scan keyword 改写工具）。

说明
- 名称：confts
- scan 的“方法”与 TS 相同（使用同一套程序/基组等），但 Gaussian keyword 需要做规则化改写。

规则（针对 Gaussian keyword 字符串）：
- 对 opt(...) / opt=(...) 括号内：移除 calcfc、tight、ts、noeigentest。
- 若存在 freq 关键词（任何形式：freq / freq=... / freq(...)），需要移除。
- nomicro 不做处理（保留）。
- "ts 关键词" 若出现在 opt() 之外，则不移除（仅移除 opt() 括号内的 ts）。
"""

from __future__ import annotations

import argparse
import os
import re
from typing import Optional


_REMOVE_OPT_ITEMS = {"calcfc", "tight", "ts", "noeigentest"}


def make_scan_keyword_from_ts_keyword(keyword: str) -> str:
    """将 TS keyword 改写为 scan 用 keyword。

    仅做字符串层面的规则化：
    - 重写 opt(...) / opt=(...) 中的子选项
    - 移除 freq 关键词

    Args:
        keyword: 原始 keyword 字符串

    Returns:
        改写后的 keyword 字符串（保持尽量少的改动，不保证格式完全等同）
    """
    kw = (keyword or "").strip()
    if not kw:
        return ""

    def _rewrite_opt_group(match: re.Match[str]) -> str:
        full = match.group(0)
        inner = match.group(1) or ""
        has_equal = "=" in full

        # split by comma, strip whitespace
        items = [x.strip() for x in inner.split(",") if x.strip()]
        kept: list[str] = []
        for item in items:
            # gaussian opt items are generally case-insensitive keywords, sometimes key=value
            key = item.split("=")[0].strip().lower()
            if key in _REMOVE_OPT_ITEMS:
                continue
            kept.append(item)

        if not kept:
            return "opt"
        joined = ",".join(kept)
        return f"opt{'=' if has_equal else ''}({joined})"

    # 1) rewrite opt group (first occurrence is most common; handle multiple defensively)
    kw = re.sub(r"(?i)\bopt\s*(?:=\s*)?\(([^)]*)\)", _rewrite_opt_group, kw)

    # 2) remove freq keyword (freq / freq=... / freq(...)) only as standalone directive
    #    Keep it simple: delete occurrences at word boundary plus optional =(...) / (...) / =...
    kw = re.sub(r"(?i)(^|\s)freq\b(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s]+)?", " ", kw)

    # normalize whitespace
    kw = re.sub(r"\s+", " ", kw).strip()
    return kw


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="confts",
        description="confts - TS 专用执行器（包含 TS 失败后 scan 救援）",
    )
    parser.add_argument("input_xyz", nargs="?", help="输入 XYZ（可多帧）")
    parser.add_argument("-s", "--settings", help="INI 配置文件路径（与 confcalc 一致）")
    parser.add_argument(
        "--rewrite-scan-keyword",
        metavar="KEYWORD",
        help="输出 scan 用 keyword（基于 TS keyword 规则改写）",
    )

    args = parser.parse_args(argv)

    if args.rewrite_scan_keyword is not None:
        print(make_scan_keyword_from_ts_keyword(args.rewrite_scan_keyword))
        return 0

    if not args.input_xyz or not args.settings:
        parser.print_help()
        return 1

    # 作为执行器运行：等价于 confcalc，但会对 itask=ts 默认开启 ts_rescue_scan
    if args.input_xyz and args.settings:
        from . import calc

        if not os.path.exists(args.input_xyz):
            raise SystemExit(f"❌ 输入文件不存在: {args.input_xyz}")
        if not os.path.exists(args.settings):
            raise SystemExit(f"❌ 配置文件不存在: {args.settings}")

        manager = calc.ChemTaskManager(settings_file=args.settings)
        # 若是 TS 任务，默认开启救援（INI 里显式设 false 时尊重）
        itask = calc.get_itask(manager.config)
        if itask == 4 and str(manager.config.get("ts_rescue_scan", "true")).lower() != "false":
            manager.config["ts_rescue_scan"] = "true"
        manager.run(args.input_xyz)
        return 0

    parser.print_help()
    return 1


def main(args_list: Optional[list[str]] = None):
    raise SystemExit(_cli(args_list))
