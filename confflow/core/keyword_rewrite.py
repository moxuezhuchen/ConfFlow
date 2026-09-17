#!/usr/bin/env python3
"""Keyword line rewriting for scan jobs from TS keywords."""

from __future__ import annotations

import re

__all__ = [
    "make_scan_keyword_from_ts_keyword",
    "ensure_gaussian_modredundant_keyword",
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


_OPT_PAREN_RE = re.compile(r"(?i)\bopt\s*(=)?\s*\(([^)]*)\)")
_OPT_ASSIGN_RE = re.compile(r"(?i)\bopt\s*=\s*([^\s()]+)")
_OPT_BARE_RE = re.compile(r"(?i)\bopt\b")


def ensure_gaussian_modredundant_keyword(keyword: str) -> str:
    """Return a Gaussian keyword line that enables ``ModRedundant``.

    Preserves any existing ``opt`` items and does not duplicate the directive
    when it is already requested.
    """
    kw = (keyword or "").strip()
    if not kw:
        return kw
    if re.search(r"(?i)\bmodredundant\b", kw):
        return kw

    def _add_to_items(items: list[str]) -> list[str]:
        if not any(item.split("=")[0].strip().lower() == "modredundant" for item in items):
            items.append("modredundant")
        return items

    def _paren_repl(match: re.Match[str]) -> str:
        has_equal = match.group(1) == "="
        items = [item.strip() for item in match.group(2).split(",") if item.strip()]
        _add_to_items(items)
        return f"opt{'=' if has_equal else ''}({','.join(items)})"

    new_kw, count = _OPT_PAREN_RE.subn(_paren_repl, kw, count=1)
    if count:
        return new_kw

    def _assign_repl(match: re.Match[str]) -> str:
        items = _add_to_items([match.group(1).strip()])
        return f"opt=({','.join(items)})"

    new_kw, count = _OPT_ASSIGN_RE.subn(_assign_repl, kw, count=1)
    if count:
        return new_kw

    if _OPT_BARE_RE.search(kw):
        return _OPT_BARE_RE.sub("opt=modredundant", kw, count=1)

    return f"opt=modredundant {kw}".strip()
