#!/usr/bin/env python3

"""ORCA native-output parsing for the V4 program adapter.

Pure parsing helpers ported from the legacy V3 ORCA implementation.  The
algorithms are extracted from ``confflow.calc.policies.orca``,
``confflow.calc.geometry``, and ``confflow.calc.analysis``; this module owns
V4-native copies and never imports those legacy packages.
"""

from __future__ import annotations

import math
import os
import re

from ...domain.elements import canonical_element_symbol

__all__ = [
    "FREQUENCY_NOISE_FLOOR_CM",
    "TERMINATION_MARKER",
    "analyze_vibrational_frequencies",
    "orca_error_details",
    "parse_cartesian_blocks",
    "parse_energies",
    "parse_frequencies",
    "parse_xyz_companion",
    "read_log_text",
    "termination_reached",
    "true_vibrational_modes",
]

#: Marker proving ORCA terminated normally.
TERMINATION_MARKER: str = "****ORCA TERMINATED NORMALLY****"

#: Modes within this many cm^-1 of zero are numerical noise, never imaginary.
FREQUENCY_NOISE_FLOOR_CM: float = 10.0

_FINAL_ENERGY_PATTERN = re.compile(r"FINAL SINGLE POINT ENERGY\s+([\d.\-]+)")
_GIBBS_CORRECTION_PATTERN = re.compile(r"G-E\(el\)\s+\.\.\.\s+([\d.\-]+)\s+Eh")
_GIBBS_ENERGY_PATTERN = re.compile(r"Final Gibbs free energy\s+\.\.\.\s+([\d.\-]+)\s+Eh")
_FREQ_VALUE_PATTERN = re.compile(r"\d+:\s+([-\d.]+)\s+cm")

_COORD_HEADER = "CARTESIAN COORDINATES (ANGSTROEM)"

_ERROR_TAIL_BYTES: int = 2000


def read_log_text(path: str) -> str | None:
    """Read a log file as text, tolerating undecodable bytes.

    Parameters
    ----------
    path : str
        Filesystem path of the log file.

    Returns
    -------
    str | None
        File content, or ``None`` when the file cannot be read.
    """
    try:
        with open(path, errors="ignore") as handle:
            return handle.read()
    except OSError:
        return None


def termination_reached(text: str) -> bool:
    """Return whether the log text carries the normal-termination marker.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    bool
        True when ``****ORCA TERMINATED NORMALLY****`` is present.
    """
    return TERMINATION_MARKER in text


def orca_error_details(text: str) -> str:
    """Summarize failure hints from the tail of log text.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    str
        ``" | "``-joined hints, or ``""`` when no known pattern matches.
    """
    tail = text[-_ERROR_TAIL_BYTES:]
    details = []
    if "ORCA finished by error" in tail:
        details.append("Abnormal program termination")
    if "SCF NOT CONVERGED" in tail:
        details.append("SCF not converged")
    return " | ".join(details)


def _to_float(token: str) -> float | None:
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_energies(text: str) -> dict[str, float]:
    """Parse Hartree energies from ORCA log text.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    dict[str, float]
        Parsed subset of ``electronic`` (FINAL SINGLE POINT ENERGY),
        ``gibbs_correction`` (G-E(el)), and ``gibbs`` (Final Gibbs free
        energy); last match wins per key.
    """
    energies: dict[str, float] = {}
    for line in text.splitlines():
        match = _FINAL_ENERGY_PATTERN.search(line)
        if match is not None:
            value = _to_float(match.group(1))
            if value is not None:
                energies["electronic"] = value
        match = _GIBBS_CORRECTION_PATTERN.search(line)
        if match is not None:
            value = _to_float(match.group(1))
            if value is not None:
                energies["gibbs_correction"] = value
        match = _GIBBS_ENERGY_PATTERN.search(line)
        if match is not None:
            value = _to_float(match.group(1))
            if value is not None:
                energies["gibbs"] = value
    return energies


def parse_frequencies(text: str) -> list[float]:
    """Parse the last explicitly completed vibrational-frequency section.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    list[float]
        Frequencies of the last section committed by a ``NORMAL MODES`` or
        ``IR SPECTRUM`` trailer; a trailing candidate cut off at EOF is never
        committed, so the result is empty when no section completed.
    """
    committed: list[float] = []
    candidate: list[float] = []
    in_section = False
    for line in text.splitlines():
        if "VIBRATIONAL FREQUENCIES" in line:
            candidate = []
            in_section = True
            continue
        if in_section:
            found = _FREQ_VALUE_PATTERN.findall(line)
            if found:
                candidate.extend(
                    value for token in found if (value := _to_float(token)) is not None
                )
            elif "NORMAL MODES" in line or "IR SPECTRUM" in line:
                in_section = False
                if candidate:
                    committed = candidate
                    candidate = []
    return committed


def analyze_vibrational_frequencies(
    freqs: list[float],
    *,
    skip_rigid_modes: int = 0,
    noise_floor_cm: float = FREQUENCY_NOISE_FLOOR_CM,
) -> tuple[int, float | None]:
    """Count imaginary modes and report the lowest true vibrational frequency.

    Parameters
    ----------
    freqs : list[float]
        Raw parsed frequencies in cm^-1.
    skip_rigid_modes : int, optional
        Leading modes to drop before analysis.
    noise_floor_cm : float, optional
        Modes within this distance of zero count as numerical noise.

    Returns
    -------
    tuple[int, float | None]
        Imaginary-mode count and lowest true frequency (``None`` when empty).
    """
    modes: list[float] = []
    for value in freqs[skip_rigid_modes:]:
        try:
            freq = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(freq) or abs(freq) <= noise_floor_cm:
            continue
        modes.append(freq)
    num_imag = sum(1 for freq in modes if freq < 0.0)
    return num_imag, min(modes) if modes else None


def true_vibrational_modes(
    freqs: list[float], *, noise_floor_cm: float = FREQUENCY_NOISE_FLOOR_CM
) -> tuple[float, ...]:
    """Filter committed frequencies down to true vibrational modes.

    Parameters
    ----------
    freqs : list[float]
        Raw parsed frequencies in cm^-1.
    noise_floor_cm : float, optional
        Modes within this distance of zero count as numerical noise.

    Returns
    -------
    tuple[float, ...]
        Finite modes with ``abs(freq) > noise_floor_cm``, in parsed order.
    """
    modes: list[float] = []
    for value in freqs:
        try:
            freq = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(freq) or abs(freq) <= noise_floor_cm:
            continue
        modes.append(freq)
    return tuple(modes)


def _parse_coord_line(line: str) -> tuple[str, float, float, float] | None:
    """Parse one ``<symbol> <x> <y> <z>`` line with local element validation.

    Parameters
    ----------
    line : str
        Candidate coordinate line.

    Returns
    -------
    tuple[str, float, float, float] | None
        Canonical ``(symbol, x, y, z)`` triple, or ``None`` when unparseable.
    """
    parts = line.split()
    if len(parts) != 4:
        return None
    try:
        symbol = canonical_element_symbol(parts[0])
        point = (float(parts[1]), float(parts[2]), float(parts[3]))
    except (ValueError, ArithmeticError):
        return None
    if not all(math.isfinite(value) for value in point):
        return None
    return symbol, point[0], point[1], point[2]


def parse_xyz_companion(path: str) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...]] | None:
    """Parse an ORCA ``.xyz`` companion file.

    Parameters
    ----------
    path : str
        Filesystem path of the companion file.

    Returns
    -------
    tuple | None
        ``(atoms, coordinates)`` pairs, or ``None`` when the file is missing
        or malformed (callers fall back to log blocks).
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, errors="ignore") as handle:
            lines = handle.readlines()
        count = int(lines[0].strip())
        atoms: list[str] = []
        coords: list[tuple[float, ...]] = []
        for line in lines[2 : 2 + count]:
            if not line.strip():
                continue
            parsed = _parse_coord_line(line)
            if parsed is None:
                return None
            symbol, x, y, z = parsed
            atoms.append(symbol)
            coords.append((x, y, z))
        if len(atoms) != count:
            return None
        return tuple(atoms), tuple(coords)
    except (OSError, IndexError, ValueError):
        return None


def parse_cartesian_blocks(
    text: str,
) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...]] | None:
    """Parse the last ``CARTESIAN COORDINATES (ANGSTROEM)`` block.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    tuple | None
        ``(atoms, coordinates)`` of the last non-empty block, or ``None``
        when no block parsed.
    """
    last: tuple[tuple[str, ...], tuple[tuple[float, ...], ...]] | None = None
    current_atoms: list[str] | None = None
    current_coords: list[tuple[float, ...]] | None = None
    for line in text.splitlines():
        if _COORD_HEADER in line:
            current_atoms = []
            current_coords = []
            continue
        if current_atoms is None or current_coords is None:
            continue
        stripped = line.strip()
        if not stripped:
            if current_coords:
                last = (tuple(current_atoms), tuple(current_coords))
            current_atoms = None
            current_coords = None
            continue
        if set(stripped) == {"-"}:
            continue
        parsed = _parse_coord_line(line)
        if parsed is not None:
            symbol, x, y, z = parsed
            current_atoms.append(symbol)
            current_coords.append((x, y, z))
    if current_atoms and current_coords:
        last = (tuple(current_atoms), tuple(current_coords))
    return last
