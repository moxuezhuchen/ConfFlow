#!/usr/bin/env python3

"""ORCA native-input rendering for the V4 program adapter.

Pure formatting helpers ported from the legacy V3 ORCA implementation.  The
algorithms are extracted from ``confflow.calc.policies.orca``,
``confflow.calc.components.input_helpers``, and ``confflow.shared.orca_blocks``;
this module owns V4-native copies and never imports those legacy packages.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "ALLOWED_NATIVE_KEYS",
    "BYTES_PER_MB",
    "MIN_MAXCORE_MB",
    "compute_maxcore_mb",
    "format_coord_lines",
    "format_orca_blocks",
    "orca_constraint_block",
    "render_orca_input",
    "resolve_blocks_text",
    "resolve_keyword",
    "resolve_maxcore",
    "sanitize_job_name",
]

#: Strict native vocabulary accepted in ``ResolvedCalculationInputs.native``.
ALLOWED_NATIVE_KEYS: frozenset[str] = frozenset(
    {"keyword", "blocks", "maxcore", "atom_mapping", "neb", "goat", "irc"}
)

#: Bytes per megabyte used when deriving ``%maxcore`` from byte resources.
BYTES_PER_MB: int = 1024 * 1024

#: Floor for derived ``%maxcore`` values, in megabytes.
MIN_MAXCORE_MB: int = 100

_JOB_SANITIZE_PATTERN = re.compile(r"[^A-Za-z0-9_.\-]+")


def format_orca_blocks(blocks: Any) -> str:
    """Convert a mapping or string into ORCA ``%block ... end`` syntax.

    Parameters
    ----------
    blocks : Any
        Either a pre-rendered string (returned with a trailing newline) or a
        mapping of block names to nested content.

    Returns
    -------
    str
        Rendered blocks ending with a newline, or ``""`` when empty.
    """
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
        if isinstance(content, Mapping):
            for key, value in content.items():
                if isinstance(value, (Mapping, list, tuple)):
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


def orca_constraint_block(freeze_indices_1based: Sequence[int]) -> str:
    """Render the ``%geom Constraints`` block for 1-based freeze indices.

    Parameters
    ----------
    freeze_indices_1based : Sequence[int]
        1-based atom indices to freeze; ORCA addresses atoms 0-based, so each
        index is decremented by one.

    Returns
    -------
    str
        Rendered constraint block ending with a newline, or ``""`` when empty.
    """
    if not freeze_indices_1based:
        return ""
    lines = ["%geom Constraints"]
    for atom_idx in freeze_indices_1based:
        lines.append(f"  {{ C {int(atom_idx) - 1} C }}")
    lines.append("  end")
    lines.append("end")
    return "\n".join(lines) + "\n"


def sanitize_job_name(value: str, *, fallback: str = "job") -> str:
    """Sanitize a logical key into a filesystem-safe ORCA job name.

    Parameters
    ----------
    value : str
        Candidate job name, usually the work-item logical key.
    fallback : str, optional
        Name used when nothing sanitizable remains.

    Returns
    -------
    str
        Deterministic job name containing only ``[A-Za-z0-9_.-]``.
    """
    cleaned = _JOB_SANITIZE_PATTERN.sub("_", str(value).strip()).strip("._")
    if not cleaned:
        cleaned = _JOB_SANITIZE_PATTERN.sub("_", str(fallback).strip()).strip("._")
    if not cleaned:
        cleaned = "job"
    return cleaned[:128]


def resolve_keyword(native: Mapping[str, Any]) -> str:
    """Return the required non-empty ``keyword`` entry from native options.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs.

    Returns
    -------
    str
        Stripped keyword line.

    Raises
    ------
    ValueError
        Raised when ``keyword`` is missing or empty.
    """
    keyword = native.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError("native_input_error: ORCA 'keyword' must be a non-empty string")
    return keyword.strip()


def resolve_blocks_text(native: Mapping[str, Any]) -> str:
    """Render the user ``blocks`` entry to ``%block`` text.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs.

    Returns
    -------
    str
        Rendered blocks ending with a newline, or ``""`` when absent.

    Raises
    ------
    ValueError
        Raised when ``blocks`` is neither a string nor a mapping.
    """
    blocks = native.get("blocks", "")
    if blocks is None:
        return ""
    if isinstance(blocks, str):
        return format_orca_blocks(blocks)
    if isinstance(blocks, Mapping):
        return format_orca_blocks(dict(blocks))
    raise ValueError("native_input_error: ORCA 'blocks' must be a string or a mapping")


def compute_maxcore_mb(memory_bytes: int, cores: int) -> int:
    """Derive per-core ``%maxcore`` megabytes from byte resources.

    Parameters
    ----------
    memory_bytes : int
        Memory reserved per work item in bytes.
    cores : int
        CPU cores reserved per work item.

    Returns
    -------
    int
        Per-core megabytes floored to hundreds, minimum 100.
    """
    per_core_mb = float(memory_bytes) / float(cores) / float(BYTES_PER_MB)
    floored = int(per_core_mb / 100) * 100
    return max(MIN_MAXCORE_MB, floored)


def resolve_maxcore(
    native: Mapping[str, Any], *, memory_bytes: int | None, cores: int | None
) -> str:
    """Resolve the ``%maxcore`` value, preferring an explicit override.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs.
    memory_bytes : int | None
        Memory reserved per work item in bytes.
    cores : int | None
        CPU cores reserved per work item.

    Returns
    -------
    str
        Explicit override passed through verbatim after integer validation,
        otherwise the derived integer megabytes.

    Raises
    ------
    ValueError
        Raised when neither an override nor complete resources are available,
        or when the override is not integer-like.
    """
    override = native.get("maxcore")
    if override is not None and str(override).strip():
        text = str(override).strip()
        try:
            int(text)
            return text
        except (ValueError, TypeError):
            try:
                as_float = float(text)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"native_input_error: ORCA 'maxcore' must be integer-like, got {override!r}"
                ) from exc
            if not as_float.is_integer():
                raise ValueError(
                    f"native_input_error: ORCA 'maxcore' must be integer-like, got {override!r}"
                ) from None
            return str(int(as_float))
    if memory_bytes is None or cores is None or cores < 1:
        raise ValueError(
            "native_input_error: ORCA '%maxcore' needs 'maxcore' or resolved resources"
        )
    return str(compute_maxcore_mb(int(memory_bytes), int(cores)))


def format_coord_lines(atoms: Sequence[str], coordinates: Sequence[Sequence[float]]) -> str:
    """Format structure atoms and coordinates as ORCA ``* xyz`` lines.

    Parameters
    ----------
    atoms : Sequence[str]
        Element symbols in atom order.
    coordinates : Sequence[Sequence[float]]
        Cartesian coordinates in Angstrom.

    Returns
    -------
    str
        Newline-joined ``"<symbol> <x> <y> <z>"`` lines.
    """
    lines = []
    for symbol, point in zip(atoms, coordinates):
        x, y, z = float(point[0]), float(point[1]), float(point[2])
        lines.append(f"{symbol} {x:.6f} {y:.6f} {z:.6f}")
    return "\n".join(lines)


def render_orca_input(
    *,
    keyword: str,
    cores: int,
    maxcore: str,
    blocks_text: str,
    freeze: Sequence[int] | None,
    charge: int,
    multiplicity: int,
    coords_text: str,
) -> str:
    """Render the complete ORCA input file content.

    Parameters
    ----------
    keyword : str
        ORCA keyword line without the leading ``!``.
    cores : int
        ``%pal nprocs`` value.
    maxcore : str
        ``%maxcore`` value in megabytes.
    blocks_text : str
        Rendered user ``%block`` text (may be empty).
    freeze : Sequence[int] | None
        1-based frozen atom indices appended as ``%geom Constraints``.
    charge : int
        Total charge for the ``* xyz`` block.
    multiplicity : int
        Spin multiplicity for the ``* xyz`` block.
    coords_text : str
        Newline-joined coordinate lines.

    Returns
    -------
    str
        Full ``.inp`` file content following the legacy ORCA template.
    """
    generated_blocks = blocks_text
    if freeze:
        generated_blocks += orca_constraint_block(tuple(int(index) for index in freeze))
    return (
        f"! {keyword}\n"
        f"%pal nprocs {cores} end\n"
        f"%maxcore {maxcore}\n"
        f"{generated_blocks}* xyz {charge} {multiplicity}\n"
        f"{coords_text}\n"
        "*\n"
    )
