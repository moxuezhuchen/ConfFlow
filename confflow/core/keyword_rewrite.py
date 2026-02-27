#!/usr/bin/env python3

from __future__ import annotations

import re

__all__ = [
    "make_scan_keyword_from_ts_keyword",
]

_REMOVE_OPT_ITEMS = {"calcfc", "tight", "ts", "noeigentest", "rcfc", "readfc"}


def make_scan_keyword_from_ts_keyword(keyword: str) -> str:
    """Rewrite a TS keyword line into one suitable for a scan job."""
    kw = (keyword or "").strip()
    if not kw:
        return ""

    def _rewrite_opt_group(match: re.Match[str]) -> str:
        full = match.group(0)
        inner = match.group(1) or ""
        has_equal = "=" in full

        items = [x.strip() for x in inner.split(",") if x.strip()]
        kept: list[str] = []
        for item in items:
            key = item.split("=")[0].strip().lower()
            if key in _REMOVE_OPT_ITEMS:
                continue
            kept.append(item)

        if not kept:
            return "opt"
        joined = ",".join(kept)
        return f"opt{'=' if has_equal else ''}({joined})"

    kw = re.sub(r"(?i)\bopt\s*(?:=\s*)?\(([^)]*)\)", _rewrite_opt_group, kw)
    kw = re.sub(r"(?i)(^|\s)freq\b(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s]+)?", " ", kw)
    kw = re.sub(r"\s+", " ", kw).strip()
    return kw
