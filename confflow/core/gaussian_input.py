#!/usr/bin/env python3

"""Gaussian input and coordinate parsing helpers."""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "calculate_bond_length",
    "coords_lines_to_array",
    "parse_gaussian_input",
    "parse_gaussian_input_text",
]


def _parse_tail_coordinates(parts: list[str]) -> tuple[float, float, float]:
    """Parse the trailing ``x y z`` coordinate triplet from a tokenized line."""
    return float(parts[-3]), float(parts[-2]), float(parts[-1])


def coords_lines_to_array(
    coords_lines: list[str],
) -> list[tuple[str, float, float, float]] | None:
    """Convert coordinate lines to a list of ``(symbol, x, y, z)`` tuples."""
    try:
        result = []
        for line in coords_lines:
            parts = line.split()
            if len(parts) < 4:
                return None

            symbol = parts[0]
            x, y, z = _parse_tail_coordinates(parts)
            result.append((symbol, x, y, z))

        return result
    except (ValueError, TypeError, IndexError):
        return None


def parse_gaussian_input(filepath: str) -> dict[str, Any]:
    """Parse a Gaussian input file (.gjf/.com)."""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return parse_gaussian_input_text(text, filepath)
    except OSError as e:
        raise OSError(f"Failed to read Gaussian input {filepath}: {e}") from e


def parse_gaussian_input_text(text: str, source_label: str = "text") -> dict[str, Any]:
    """Parse Gaussian input text."""
    from .data import get_element_symbol

    lines = text.splitlines()
    qm_idx = None
    charge = 0
    mult = 1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^\s*-?\d+\s+-?\d+\s*$", s):
            qm_idx = i
            parts = s.split()
            charge = int(parts[0])
            mult = int(parts[1])
            break

    if qm_idx is None:
        raise ValueError(f"Cannot find charge/multiplicity line in {source_label}")

    atoms: list[str] = []
    coords_list: list[list[float]] = []
    coords_formatted: list[str] = []
    raw_coords_lines: list[str] = []

    for ln in lines[qm_idx + 1 :]:
        raw_ln = ln.strip()
        if not raw_ln:
            break
        parts = raw_ln.split()
        if len(parts) < 4:
            break

        raw_coords_lines.append(raw_ln)
        sym = parts[0]
        if sym.isdigit():
            sym = get_element_symbol(int(sym))

        try:
            x, y, z = _parse_tail_coordinates(parts)
        except (ValueError, TypeError, IndexError):
            break
        atoms.append(sym)
        coords_list.append([x, y, z])
        coords_formatted.append(f"{sym} {x:.8f} {y:.8f} {z:.8f}")

    return {
        "charge": charge,
        "multiplicity": mult,
        "atoms": atoms,
        "coords": coords_list,
        "coords_lines": coords_formatted,
        "raw_coords_lines": raw_coords_lines,
    }


def calculate_bond_length(coords_lines: list[str], atom1: int, atom2: int) -> float | None:
    """Calculate the distance between two atoms."""
    coords_array = coords_lines_to_array(coords_lines)
    if coords_array is None:
        return None

    if atom1 < 1 or atom2 < 1 or atom1 > len(coords_array) or atom2 > len(coords_array):
        return None

    _, x1, y1, z1 = coords_array[atom1 - 1]
    _, x2, y2, z2 = coords_array[atom2 - 1]

    dx, dy, dz = x1 - x2, y1 - y2, z1 - z2
    return float((dx * dx + dy * dy + dz * dz) ** 0.5)
