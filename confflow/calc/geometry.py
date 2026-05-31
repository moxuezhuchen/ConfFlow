#!/usr/bin/env python3

"""Geometry and general parsing utilities.

- Parse the last structure from log files.
- Check for normal termination.
"""

from __future__ import annotations

import os

from ..core.elements import canonicalize_element_symbol
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

    # For ORCA, prefer the companion .xyz file when it is available.
    if prog_id == 2:
        xyz_file_path = os.path.splitext(log_file)[0] + ".xyz"
        if os.path.exists(xyz_file_path):
            try:
                with open(xyz_file_path) as f:
                    coords: list[str] = []
                    lines = f.readlines()
                    num = int(lines[0].strip())
                    for line in lines[2 : 2 + num]:
                        if line.strip():
                            p = line.split()
                            sym = canonicalize_element_symbol(p[0])
                            coords.append(
                                f"{sym:<2s} {float(p[1]): >12.6f} {float(p[2]): >12.6f} {float(p[3]): >12.6f}"
                            )
                    return coords
            except (OSError, IndexError, ValueError) as e:
                logger.debug(f"ORCA XYZ read failed {xyz_file_path}: {e}")

    try:
        handle = open(log_file, errors="ignore")
    except OSError:
        return None

    with handle:
        if prog_id == 1:  # Gaussian
            last_coords: list[str] = []
            current_coords: list[str] | None = None
            delimiter_count = 0
            collecting_coords = False
            for idx, line in enumerate(handle):
                if "Standard orientation:" in line or "Input orientation:" in line:
                    current_coords = []
                    delimiter_count = 0
                    collecting_coords = False
                    continue
                if current_coords is None:
                    continue
                if "---" in line:
                    if collecting_coords:
                        if current_coords:
                            last_coords = current_coords
                        current_coords = None
                        collecting_coords = False
                        continue
                    delimiter_count += 1
                    if delimiter_count >= 2:
                        collecting_coords = True
                    continue
                if not collecting_coords:
                    continue
                p = line.split()
                if len(p) == 6:
                    try:
                        an = int(p[1])
                        sym = get_element_symbol(an)
                        current_coords.append(
                            f"{sym:<2s} {float(p[3]): >12.6f} {float(p[4]): >12.6f} {float(p[5]): >12.6f}"
                        )
                    except (IndexError, ValueError) as e:
                        logger.debug(f"Gaussian coordinate parse failed at line {idx}: {e}")
            if current_coords and collecting_coords:
                last_coords = current_coords
            return last_coords or None

        if prog_id == 2:  # Fall back to parsing the ORCA log file directly.
            orca_last_coords: list[str] = []
            orca_current_coords: list[str] | None = None
            for line in handle:
                if "CARTESIAN COORDINATES (ANGSTROEM)" in line:
                    orca_current_coords = []
                    continue
                if orca_current_coords is None:
                    continue
                stripped = line.strip()
                if not stripped:
                    if orca_current_coords:
                        orca_last_coords = orca_current_coords
                    orca_current_coords = None
                    continue
                if set(stripped) == {"-"}:
                    continue
                p = line.split()
                if len(p) == 4:
                    try:
                        sym = canonicalize_element_symbol(p[0])
                        orca_current_coords.append(
                            f"{sym:<2s} {float(p[1]): >12.6f} {float(p[2]): >12.6f} {float(p[3]): >12.6f}"
                        )
                    except ValueError as e:
                        logger.debug(f"ORCA coordinate parse failed: {e}")
            if orca_current_coords:
                orca_last_coords = orca_current_coords
            return orca_last_coords or None

    return None


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
