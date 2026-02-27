#!/usr/bin/env python3

"""Geometry and general parsing utilities.

- Parse the last structure from log files.
- Check for normal termination.
"""

from __future__ import annotations

import os
import re

from .constants import get_element_symbol
from .setup import logger

__all__ = [
    "parse_last_geometry",
    "check_termination",
]


def parse_last_geometry(log_file: str, prog_id: int) -> list[str] | None:
    """Extract the last geometry coordinate block from a Gaussian/ORCA output file."""
    if not os.path.exists(log_file):
        return None

    coords: list[str] = []

    # ORCA: try the companion .xyz file first
    if prog_id == 2:
        xyz_file_path = os.path.splitext(log_file)[0] + ".xyz"
        if os.path.exists(xyz_file_path):
            try:
                with open(xyz_file_path) as f:
                    lines = f.readlines()
                    num = int(lines[0].strip())
                    for line in lines[2 : 2 + num]:
                        if line.strip():
                            p = line.split()
                            coords.append(
                                f"{p[0]:<2s} {float(p[1]): >12.6f} {float(p[2]): >12.6f} {float(p[3]): >12.6f}"
                            )
                    return coords
            except (OSError, IndexError, ValueError) as e:
                logger.debug(f"ORCA XYZ read failed {xyz_file_path}: {e}")

    try:
        with open(log_file, errors="ignore") as f:
            lines = f.read().splitlines()
    except OSError:
        return None

    if prog_id == 1:  # Gaussian
        start_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if "Standard orientation:" in lines[i] or "Input orientation:" in lines[i]:
                start_idx = i
                break
        if start_idx != -1:
            idx = start_idx + 5
            while idx < len(lines) and "---" not in lines[idx]:
                p = lines[idx].split()
                if len(p) == 6:
                    try:
                        an = int(p[1])
                        sym = get_element_symbol(an)
                        coords.append(
                            f"{sym:<2s} {float(p[3]): >12.6f} {float(p[4]): >12.6f} {float(p[5]): >12.6f}"
                        )
                    except (IndexError, ValueError) as e:
                        logger.debug(f"Gaussian coordinate parse failed at line {idx}: {e}")
                idx += 1
    elif prog_id == 2:  # ORCA Log Fallback
        content = "\n".join(lines)
        blocks = list(
            re.finditer(
                r"CARTESIAN COORDINATES \(ANGSTROEM\)\n-+\n(.*?)\n\s*\n",
                content,
                re.DOTALL,
            )
        )
        if blocks:
            for line in blocks[-1].group(1).strip().split("\n"):
                p = line.split()
                if len(p) == 4:
                    coords.append(
                        f"{p[0]:<2s} {float(p[1]): >12.6f} {float(p[2]): >12.6f} {float(p[3]): >12.6f}"
                    )

    return coords if coords else None


def check_termination(log_file: str, prog_name: str) -> bool:
    """Check whether Gaussian/ORCA terminated normally (tail keyword check)."""
    if not os.path.exists(log_file):
        return False
    try:
        with open(log_file, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 10000))
            content = f.read().decode("utf-8", errors="ignore")
            if prog_name == "gaussian" and "Normal termination" in content:
                return True
            if prog_name == "orca" and "****ORCA TERMINATED NORMALLY****" in content:
                return True
    except OSError as e:
        logger.debug(f"Termination check failed {log_file}: {e}")
    return False
