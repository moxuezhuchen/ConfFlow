#!/usr/bin/env python3

"""Shared helpers for input file generation (reused by policies).

Constraints:
- Only small pure-computation / pure-formatting functions; avoid introducing I/O.
- These helpers mainly reduce duplicate logic between policies; output format is
  controlled by each policy itself.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from ..setup import UTILS_AVAILABLE

try:
    from ...shared.defaults import DEFAULT_TOTAL_MEMORY
except ImportError:  # pragma: no cover
    DEFAULT_TOTAL_MEMORY = "4GB"
from ...shared.orca_blocks import format_orca_blocks

try:
    from ...core.utils import parse_memory
except ImportError:  # pragma: no cover
    parse_memory = None

try:
    from ...core.utils import parse_index_spec
except ImportError:  # pragma: no cover
    parse_index_spec = None  # type: ignore[assignment]

__all__ = [
    "compute_gaussian_mem",
    "compute_orca_maxcore",
    "normalize_gaussian_keyword",
    "normalize_blocks",
    "parse_freeze_indices",
    "gaussian_apply_freeze",
    "orca_constraint_block",
    "format_orca_blocks",
]


def _total_sys_mb(total_mem_str: Any) -> int:
    if UTILS_AVAILABLE and parse_memory is not None:
        return int(parse_memory(total_mem_str, "MB"))

    mem_str = str(total_mem_str).strip().upper()
    if "GB" in mem_str:
        return int(float(mem_str.replace("GB", "")) * 1024)
    if "MB" in mem_str:
        return int(float(mem_str.replace("MB", "")))
    try:
        return int(float(mem_str))
    except ValueError:
        return 4096


def compute_gaussian_mem(config: dict[str, Any]) -> str:
    max_jobs = int(config.get("max_parallel_jobs", 1))
    total_mem_str = config.get("total_memory", config.get("memory", DEFAULT_TOTAL_MEMORY))

    sys_mb = _total_sys_mb(total_mem_str)
    mem_per_job_mb = sys_mb / max_jobs
    mem_gb = int(mem_per_job_mb / 1024)
    if mem_gb < 1:
        mem_gb = 1
    return f"{mem_gb}GB"


def compute_orca_maxcore(config: dict[str, Any]) -> str:
    cores = int(config.get("cores_per_task", 1))
    max_jobs = int(config.get("max_parallel_jobs", 1))
    total_mem_str = config.get("total_memory", DEFAULT_TOTAL_MEMORY)

    sys_mb = _total_sys_mb(total_mem_str)
    mem_per_job_mb = sys_mb / max_jobs

    orca_maxcore = config.get("orca_maxcore", config.get("maxcore"))
    if orca_maxcore is not None and str(orca_maxcore).strip():
        try:
            return str(int(float(str(orca_maxcore).strip())))
        except (ValueError, TypeError):
            return str(orca_maxcore).strip()

    mem_per_core_mb = mem_per_job_mb / cores
    mem_per_core_hundreds = int(mem_per_core_mb / 100) * 100
    if mem_per_core_hundreds < 100:
        mem_per_core_hundreds = 100
    return str(mem_per_core_hundreds)


def normalize_gaussian_keyword(keyword_line: Any) -> str:
    if not isinstance(keyword_line, str):
        return str(keyword_line)
    return re.sub(r"^\s*(?:#\s*[pPnNtT]?\s*)+", "", keyword_line).strip() or ""


def normalize_blocks(solvent_block: Any, custom_block: Any) -> tuple[str, str]:
    s = "" if solvent_block is None else str(solvent_block)
    c = "" if custom_block is None else str(custom_block)
    if s and not s.endswith("\n"):
        s += "\n"
    if c and not c.endswith("\n"):
        c += "\n"
    return s, c


def parse_freeze_indices(freeze: Any) -> list[int]:
    """Parse freeze configuration and return a list of 1-based atom indices."""
    if freeze is None:
        return []

    if isinstance(freeze, str):
        freeze_str = freeze.strip()
        if not freeze_str or freeze_str.lower() == "0":
            return []
        if parse_index_spec is None:
            # Fall back to parsing a comma-separated list.
            return [int(x.strip()) for x in freeze_str.split(",") if x.strip()]
        return list(parse_index_spec(freeze_str))

    if isinstance(freeze, (list, tuple)):
        out: list[int] = []
        for x in freeze:
            if x is None:
                continue
            xs = str(x).strip()
            if not xs or xs.lower() == "0":
                continue
            out.append(int(float(xs)))
        return out

    return []


def gaussian_apply_freeze(coords_lines: Sequence[str], freeze_indices_1based: Sequence[int]) -> str:
    inds = set(int(i) for i in freeze_indices_1based)
    if not inds:
        return "\n".join(coords_lines)

    mod_c = []
    for i, line in enumerate(coords_lines):
        p = line.split()
        f = -1 if (i + 1) in inds else 0
        mod_c.append(
            f"{p[0]:<2s} {f} {float(p[1]): >12.6f} {float(p[2]): >12.6f} {float(p[3]): >12.6f}"
        )
    return "\n".join(mod_c)


def orca_constraint_block(freeze_indices_1based: Sequence[int]) -> str:
    if not freeze_indices_1based:
        return ""

    constraint_lines = ["%geom Constraints"]
    for atom_idx in freeze_indices_1based:
        constraint_lines.append(f"  {{ C {int(atom_idx) - 1} C }}")
    constraint_lines.append("  end")
    constraint_lines.append("end")
    return "\n".join(constraint_lines) + "\n"
