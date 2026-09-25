#!/usr/bin/env python3

"""Fake ORCA executable for ConfFlow Workflow V4-2 execution tests.

The script simulates a native program behind the V4 process boundary. It
reads the ``.inp`` input geometry from the current work directory, shifts the
coordinates deterministically, and writes an ``.out`` file whose content
satisfies the real V4 ORCA parser (:mod:`confflow.programs.orca.parsing`):

- ``FINAL SINGLE POINT ENERGY`` lines,
- ``CARTESIAN COORDINATES (ANGSTROEM)`` blocks,
- ``VIBRATIONAL FREQUENCIES`` sections committed by ``NORMAL MODES``,
- ``G-E(el)`` / ``Final Gibbs free energy`` thermochemistry lines,
- ``****ORCA TERMINATED NORMALLY****`` markers.

It also writes the ``.xyz`` companion geometry file and a ``.gbw``
wavefunction file, mirroring what the real adapter discovers. The same module
exposes pure log-rendering helpers so adapter parse tests can build fixture
logs without launching a subprocess. Only ``main`` touches the filesystem or
the process environment.
"""

from __future__ import annotations

import glob
import os
import signal
import sys
import time

#: Deterministic geometry shift (Angstrom, applied to x) for produced logs.
SHIFT_ANGSTROM = 0.01

#: Fixed single-point energy reported by successful modes.
ENERGY_HARTREE = -76.4601112223

#: Fixed Gibbs correction, added to the electronic energy for the free energy.
GIBBS_CORRECTION = 0.0149

#: Fixed imaginary frequency for the transition-state candidate mode.
TS_IMAG_FREQUENCY = -500.1234

#: Real vibrational frequencies reported by frequency modes.
REAL_FREQUENCIES = (100.12, 200.23, 300.35, 1500.0, 1700.0, 3800.0)

#: Near-zero rigid modes, filtered by the parser noise floor.
RIGID_FREQUENCIES = (0.0, 0.0, 1.5, 2.5, 3.5, 4.5)

#: Element symbols accepted in coordinate lines.
KNOWN_ELEMENTS = frozenset(
    {
        "H",
        "He",
        "Li",
        "Be",
        "B",
        "C",
        "N",
        "O",
        "F",
        "Ne",
        "Na",
        "Mg",
        "Al",
        "Si",
        "P",
        "S",
        "Cl",
        "Ar",
        "K",
        "Ca",
        "Br",
        "I",
    }
)


def gibbs_energy() -> float:
    """Return the fixed Gibbs free energy for successful modes."""
    return ENERGY_HARTREE + GIBBS_CORRECTION


def shift_coordinates(
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Shift coordinates by the fixed x delta."""
    shifted = tuple((x + SHIFT_ANGSTROM, y, z) for x, y, z in coordinates)
    return atoms, shifted


def render_cartesian_block(
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render a ``CARTESIAN COORDINATES (ANGSTROEM)`` block."""
    lines = [
        "CARTESIAN COORDINATES (ANGSTROEM)",
        "-------------------------",
    ]
    for symbol, point in zip(atoms, coordinates):
        x, y, z = point
        lines.append(f"  {symbol:<2s}  {x:11.6f}    {y:11.6f}    {z:11.6f}")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_xyz_text(
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render the ``.xyz`` companion file content."""
    lines = [str(len(atoms)), "FakeOrca XYZ companion"]
    for symbol, point in zip(atoms, coordinates):
        x, y, z = point
        lines.append(f"{symbol} {x:.6f} {y:.6f} {z:.6f}")
    return "\n".join(lines) + "\n"


def render_frequencies_block(frequencies: tuple[float, ...]) -> str:
    """Render a vibrational frequency section with a ``NORMAL MODES`` trailer."""
    lines = ["VIBRATIONAL FREQUENCIES", "-----------------------"]
    for index, value in enumerate(frequencies):
        lines.append(f"  {index:3d}:      {value:9.2f} cm**-1")
    lines.extend(["NORMAL MODES", "------------"])
    return "\n".join(lines) + "\n"


def render_thermochemistry_block() -> str:
    """Render the Gibbs correction and final free-energy lines."""
    return (
        f" G-E(el) ...   {GIBBS_CORRECTION:.6f} Eh\n"
        f" Final Gibbs free energy ...   {gibbs_energy():.6f} Eh\n"
    )


def render_log_text(
    mode: str,
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render the full fake ORCA log for *mode* and *geometry*."""
    shifted_atoms, shifted = shift_coordinates(atoms, coordinates)
    parts = [
        "Fake ORCA V4-2 fixture log",
        "***************************",
    ]
    if mode == "abnormal":
        parts.append(render_cartesian_block(shifted_atoms, shifted))
        parts.append("SCF NOT CONVERGED after 128 iterations")
        parts.append("[ ORCA finished by error termination ]")
        return "\n".join(parts) + "\n"
    if mode not in (
        "success_opt",
        "success_sp",
        "success_freq",
        "missing_geometry",
        "missing_freq",
        "ts_candidate",
    ):
        raise ValueError(f"unknown fake_orca mode: {mode!r}")
    parts.append(f"FINAL SINGLE POINT ENERGY     {ENERGY_HARTREE:.10f}")
    if mode != "success_sp" and mode != "missing_geometry":
        parts.append(render_cartesian_block(shifted_atoms, shifted))
    if mode in ("success_freq", "ts_candidate"):
        frequencies = (
            REAL_FREQUENCIES
            if mode == "success_freq"
            else (
                TS_IMAG_FREQUENCY,
                *REAL_FREQUENCIES[1:],
            )
        )
        parts.append(render_frequencies_block((*RIGID_FREQUENCIES, *frequencies)))
        parts.append(render_thermochemistry_block())
    elif mode == "missing_freq":
        lines = ["VIBRATIONAL FREQUENCIES", "-----------------------"]
        for index, value in enumerate(REAL_FREQUENCIES):
            lines.append(f"  {index:3d}:      {value:9.2f} cm**-1")
        parts.append("\n".join(lines) + "\n")
    parts.append("****ORCA TERMINATED NORMALLY****")
    return "\n".join(parts) + "\n"


def parse_inp_coordinates(
    path: str,
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Parse element symbols and coordinates from an ORCA ``.inp`` file."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    start: int | None = None
    for position, line in enumerate(lines):
        if line.strip().lower().startswith("* xyz"):
            start = position
            break
    if start is None:
        raise ValueError(f"no * xyz block found in {path!r}")
    atoms: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped or stripped == "*":
            if coordinates:
                break
            continue
        tokens = stripped.split()
        if len(tokens) != 4 or tokens[0] not in KNOWN_ELEMENTS:
            continue
        try:
            point = (float(tokens[1]), float(tokens[2]), float(tokens[3]))
        except ValueError:
            continue
        atoms.append(tokens[0])
        coordinates.append(point)
    if not atoms:
        raise ValueError(f"no coordinates found in {path!r}")
    return tuple(atoms), tuple(coordinates)


def find_input_file(explicit: str | None) -> str:
    """Resolve the ``.inp`` input file from argv or the work directory."""
    if explicit:
        return explicit
    matches = sorted(glob.glob("*.inp"))
    if not matches:
        raise ValueError("no .inp input file found in the work directory")
    return matches[0]


def write_outputs(
    stem: str,
    mode: str,
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
    log_text: str,
) -> None:
    """Write the fake log, xyz companion, and wavefunction files."""
    with open(f"{stem}.out", "w", encoding="utf-8") as handle:
        handle.write(log_text)
    if mode != "success_sp" and mode != "missing_geometry":
        shifted_atoms, shifted = shift_coordinates(atoms, coordinates)
        with open(f"{stem}.xyz", "w", encoding="utf-8") as handle:
            handle.write(render_xyz_text(shifted_atoms, shifted))
    if mode != "abnormal":
        with open(f"{stem}.gbw", "wb") as handle:
            handle.write(b"FAKE-ORCA-GBW\n" + stem.encode("utf-8") + b"\n")


def main(argv: list[str]) -> int:
    """Run the fake ORCA executable."""
    mode = os.environ.get("FAKE_MODE", "success_opt")
    if mode == "cancel_trap":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(30)
        return 0
    if mode == "slow_term":
        time.sleep(30)
        return 0
    try:
        input_path = find_input_file(argv[1] if len(argv) > 1 else None)
        atoms, coordinates = parse_inp_coordinates(input_path)
        log_text = render_log_text(mode, atoms, coordinates)
    except (ValueError, OSError) as exc:
        print(f"fake_orca: {exc}", file=sys.stderr)
        return 3
    stem = input_path.rsplit(".", 1)[0] if "." in input_path else input_path
    write_outputs(stem, mode, atoms, coordinates, log_text)
    return 0 if mode != "abnormal" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
