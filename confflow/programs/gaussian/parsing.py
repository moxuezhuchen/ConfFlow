#!/usr/bin/env python3

"""Gaussian native-output parsing for the V4 program adapter.

Pure parsing helpers extracted from the legacy Gaussian output reader. This
module owns V4-native copies of the SCF/archive energy reader, the
commit-on-thermochemistry frequency section machine, the Standard/Input
orientation geometry machine, and the tail termination check. It never
imports the legacy packages those algorithms were extracted from.
"""

from __future__ import annotations

import math
import os
import re

from ...domain.elements import ELEMENT_SYMBOLS

__all__ = [
    "FREQUENCY_NOISE_FLOOR_CM",
    "TERMINATION_MARKER",
    "analyze_vibrational_frequencies",
    "check_termination",
    "parse_energies",
    "parse_final_geometry",
    "parse_frequencies",
    "read_log_text",
    "symbol_for_atomic_number",
    "termination_reached",
    "true_vibrational_modes",
]

#: Marker proving Gaussian terminated normally.
TERMINATION_MARKER: str = "Normal termination"

#: Modes within this many cm^-1 of zero are numerical noise, never imaginary.
FREQUENCY_NOISE_FLOOR_CM: float = 10.0

#: Bytes of log tail inspected for the termination marker and error hints.
_TAIL_BYTES: int = 10000

_SCF_DONE_TOKEN: str = "SCF Done:"
_GIBBS_SUMMARY_PATTERN = re.compile(
    r"Sum\s+of\s+electronic\s+and\s+thermal\s+Free\s+Energies=\s*(\S+)"
)
_GIBBS_CORRECTION_PATTERN = re.compile(
    r"Thermal\s+correction\s+to\s+Gibbs\s+Free\s+Energy=\s*(\S+)"
)
_FREQUENCIES_PATTERN = re.compile(r"Frequencies --\s+([-\d.\s]+)")
_ARCHIVE_HF_PATTERN = re.compile(r"\\HF=([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)")
_ARCHIVE_GIBBS_PATTERN = re.compile(r"\\Gibbs=([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def symbol_for_atomic_number(number: int) -> str | None:
    """Return the element symbol for a 1-based atomic number.

    Parameters
    ----------
    number : int
        Atomic number to resolve.

    Returns
    -------
    str | None
        Canonical symbol, or ``None`` when *number* is outside the owned
        element table.
    """
    if isinstance(number, bool) or not isinstance(number, int):
        return None
    if 1 <= number < len(ELEMENT_SYMBOLS):
        symbol = ELEMENT_SYMBOLS[number]
        return symbol or None
    return None


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
    """Return whether log text carries the normal-termination marker.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    bool
        True when ``"Normal termination"`` is present.
    """
    return TERMINATION_MARKER in text


def check_termination(log_path: str) -> bool:
    """Check whether a Gaussian log file terminated normally.

    Parameters
    ----------
    log_path : str
        Filesystem path of the log file.

    Returns
    -------
    bool
        True when ``"Normal termination"`` appears in the trailing bytes of
        the file; False when the file is missing or unreadable.
    """
    if not os.path.exists(log_path):
        return False
    try:
        with open(log_path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            content = handle.read().decode("utf-8", errors="ignore")
            return TERMINATION_MARKER in content
    except OSError:
        return False


def _to_float(token: str) -> float | None:
    """Return a finite float for *token*, or ``None`` when unparseable.

    Parameters
    ----------
    token : str
        Candidate numeric token.

    Returns
    -------
    float | None
        Finite float value, or ``None`` for missing or non-finite input.
    """
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _collect_archive_compact(text: str) -> str:
    """Compact Gaussian archive lines into whitespace-free text.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    str
        Archive chunks joined with whitespace stripped, so numeric fields
        that wrap across lines still parse.
    """
    chunks: list[str] = []
    in_archive = False
    for raw_line in text.splitlines():
        line = raw_line.replace("D", "E")
        if "\\" in line or in_archive:
            chunks.append(line.strip())
            in_archive = "\\@" not in line
    return _WHITESPACE_PATTERN.sub("", "".join(chunks))


def parse_energies(text: str) -> tuple[dict[str, float], dict[str, str]]:
    r"""Parse Hartree energies from Gaussian log text.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    tuple[dict[str, float], dict[str, str]]
        Parsed energy subset and per-key source markers. ``electronic`` comes
        from the last ``SCF Done`` line (with Fortran ``D`` exponents
        rewritten to ``E``), falling back to the last archive ``\\HF=`` value
        only when no ``SCF Done`` line parsed. ``gibbs`` comes from the last
        ``Sum of electronic and thermal Free Energies`` line, falling back to
        the last archive ``\\Gibbs=`` value only when that line is absent.
        ``gibbs_correction`` comes from the last ``Thermal correction to
        Gibbs Free Energy`` line. Malformed lines never overwrite a value
        parsed earlier.
    """
    energies: dict[str, float] = {}
    sources: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.replace("D", "E")
        if _SCF_DONE_TOKEN in raw_line:
            try:
                value = float(line.split()[4])
            except (IndexError, ValueError):
                continue
            if math.isfinite(value):
                energies["electronic"] = value
                sources["electronic"] = "scf_done"
        match = _GIBBS_SUMMARY_PATTERN.search(line)
        if match is not None:
            value = _to_float(match.group(1))
            if value is not None:
                energies["gibbs"] = value
                sources["gibbs"] = "thermochemistry"
        match = _GIBBS_CORRECTION_PATTERN.search(line)
        if match is not None:
            value = _to_float(match.group(1))
            if value is not None:
                energies["gibbs_correction"] = value
                sources["gibbs_correction"] = "thermochemistry"
    if "electronic" not in energies or "gibbs" not in energies:
        compact = _collect_archive_compact(text)
        if "electronic" not in energies:
            matches = _ARCHIVE_HF_PATTERN.findall(compact)
            if matches:
                value = _to_float(matches[-1])
                if value is not None:
                    energies["electronic"] = value
                    sources["electronic"] = "archive_hf"
        if "gibbs" not in energies:
            matches = _ARCHIVE_GIBBS_PATTERN.findall(compact)
            if matches:
                value = _to_float(matches[-1])
                if value is not None:
                    energies["gibbs"] = value
                    sources["gibbs"] = "archive_gibbs"
    return energies, sources


def parse_frequencies(text: str) -> list[float]:
    """Parse the last completed vibrational-frequency section.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    list[float]
        Frequencies of the last section committed by the thermochemistry
        trailer (``Zero-point correction=``). A new ``Harmonic frequencies``
        header resets the candidate; a ``Frequencies --`` line extends it,
        opening a section implicitly when needed. A trailing candidate cut
        off at end of file is discarded, never promoted.
    """
    committed: list[float] = []
    candidate: list[float] = []
    in_section = False
    for raw_line in text.splitlines():
        if "Zero-point correction=" in raw_line:
            if candidate:
                committed = candidate
                candidate = []
            in_section = False
            continue
        if "Harmonic frequencies" in raw_line:
            candidate = []
            in_section = True
            continue
        match = _FREQUENCIES_PATTERN.search(raw_line)
        if match is not None:
            if not in_section:
                candidate = []
                in_section = True
            for token in match.group(1).split():
                value = _to_float(token)
                if value is not None:
                    candidate.append(value)
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


def _parse_orientation_line(line: str) -> tuple[str, float, float, float] | None:
    """Parse one Standard/Input orientation coordinate line.

    Parameters
    ----------
    line : str
        Candidate six-field orientation line.

    Returns
    -------
    tuple[str, float, float, float] | None
        Canonical ``(symbol, x, y, z)`` values, or ``None`` when the line is
        not a usable coordinate row. Unresolvable atomic numbers are skipped
        rather than emitted as placeholder symbols.
    """
    parts = line.split()
    if len(parts) != 6:
        return None
    try:
        atomic = int(parts[1])
    except (TypeError, ValueError):
        return None
    symbol = symbol_for_atomic_number(atomic)
    if symbol is None:
        return None
    point = (_to_float(parts[3]), _to_float(parts[4]), _to_float(parts[5]))
    if any(value is None for value in point):
        return None
    x, y, z = point
    assert x is not None and y is not None and z is not None
    return symbol, x, y, z


def parse_final_geometry(
    text: str,
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]] | None:
    """Parse the last Standard/Input orientation block from log text.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    tuple | None
        ``(atoms, coordinates)`` of the last non-empty orientation block, or
        ``None`` when no block parsed. A block opens on a ``Standard
        orientation:`` or ``Input orientation:`` header; coordinate rows are
        collected after the second ``---`` delimiter and committed on the
        closing ``---`` delimiter. A trailing block without a closing
        delimiter is promoted when non-empty.
    """
    last: tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]] | None = None
    current: list[tuple[str, float, float, float]] | None = None
    delimiter_count = 0
    collecting = False
    for line in text.splitlines():
        if "Standard orientation:" in line or "Input orientation:" in line:
            current = []
            delimiter_count = 0
            collecting = False
            continue
        if current is None:
            continue
        if "---" in line:
            if collecting:
                if current:
                    atoms = tuple(entry[0] for entry in current)
                    coords = tuple((entry[1], entry[2], entry[3]) for entry in current)
                    last = (atoms, coords)
                current = None
                collecting = False
                continue
            delimiter_count += 1
            if delimiter_count >= 2:
                collecting = True
            continue
        if not collecting:
            continue
        parsed = _parse_orientation_line(line)
        if parsed is not None:
            current.append(parsed)
    if current and collecting:
        atoms = tuple(entry[0] for entry in current)
        coords = tuple((entry[1], entry[2], entry[3]) for entry in current)
        last = (atoms, coords)
    return last
