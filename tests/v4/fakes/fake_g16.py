#!/usr/bin/env python3

"""Fake Gaussian ``g16`` executable for ConfFlow Workflow V4-2 execution tests.

The script simulates a native program behind the V4 process boundary. It
reads the ``.gjf`` input geometry from the current work directory, shifts the
coordinates deterministically, and writes a ``.log`` file whose content
satisfies the real V4 Gaussian parser
(:mod:`confflow.programs.gaussian.parsing`):

- ``SCF Done:`` energy lines,
- ``Standard orientation`` coordinate blocks,
- ``Frequencies --`` sections committed by ``Zero-point correction=``,
- thermochemistry lines (Gibbs correction and sum),
- ``Normal termination`` markers.

The same module exposes pure log-rendering helpers so adapter parse tests
can build fixture logs without launching a subprocess. Only ``main`` touches
the filesystem or the process environment.
"""

from __future__ import annotations

import glob
import os
import signal
import sys
import time

#: Deterministic geometry shift (Angstrom, applied to x) for produced logs.
SHIFT_ANGSTROM = 0.01

#: Fixed electronic energy reported by successful modes.
ENERGY_HARTREE = -76.4589123456

#: Fixed Gibbs correction, added to the electronic energy for the free energy.
GIBBS_CORRECTION = 0.0149

#: Fixed imaginary frequency for the transition-state candidate mode.
TS_IMAG_FREQUENCY = -500.1234

#: Real vibrational frequencies reported by frequency modes.
REAL_FREQUENCIES = (100.1234, 200.2345, 300.3456, 1500.0, 1700.0, 3800.0)

#: Element symbol to atomic number table for orientation blocks.
ATOMIC_NUMBERS = {
    "H": 1,
    "He": 2,
    "Li": 3,
    "Be": 4,
    "B": 5,
    "C": 6,
    "N": 7,
    "O": 8,
    "F": 9,
    "Ne": 10,
    "Na": 11,
    "Mg": 12,
    "Al": 13,
    "Si": 14,
    "P": 15,
    "S": 16,
    "Cl": 17,
    "Ar": 18,
    "K": 19,
    "Ca": 20,
    "Br": 35,
    "I": 53,
}


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


def render_orientation_block(
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render a ``Standard orientation`` block for the given geometry."""
    lines = [
        " Standard orientation:",
        " ---------------------------------------------------------------------",
        " Center     Atomic      Atomic             Coordinates (Angstroms)",
        " Number     Number       Type             X           Y           Z",
        " ---------------------------------------------------------------------",
    ]
    for position, (symbol, point) in enumerate(zip(atoms, coordinates), start=1):
        number = ATOMIC_NUMBERS.get(symbol, 0)
        x, y, z = point
        lines.append(
            f"      {position:3d}        {number:3d}           0"
            f"      {x:11.6f}  {y:11.6f}  {z:11.6f}"
        )
    lines.append(" ---------------------------------------------------------------------")
    return "\n".join(lines) + "\n"


def render_frequencies_block(frequencies: tuple[float, ...]) -> str:
    """Render a harmonic frequency section with ``Frequencies --`` lines."""
    lines = [
        " Harmonic frequencies (cm**-1), IR intensities (KM/Mole),"
        " Raman scattering activities (A**4/AMU):",
    ]
    for index in range(0, len(frequencies), 3):
        chunk = frequencies[index : index + 3]
        lines.append(" Frequencies --    " + "   ".join(f"{value:10.4f}" for value in chunk))
    return "\n".join(lines) + "\n"


def render_thermochemistry_block() -> str:
    """Render zero-point, Gibbs correction, and Gibbs sum lines."""
    return (
        " Zero-point correction=                           0.021234"
        " (Hartree/Particle)\n"
        f" Thermal correction to Gibbs Free Energy=             {GIBBS_CORRECTION:.6f}\n"
        " Sum of electronic and thermal Free Energies="
        f"        {gibbs_energy():.6f}\n"
    )


def render_log_text(
    mode: str,
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render the full fake Gaussian log for *mode* and *geometry*."""
    shifted_atoms, shifted = shift_coordinates(atoms, coordinates)
    parts = [
        " Entering Gaussian System, Link 0=g16",
        " Fake Gaussian V4-2 fixture log",
    ]
    if mode == "abnormal":
        parts.append(render_orientation_block(shifted_atoms, shifted))
        parts.append(" SCF NOT CONVERGED after 128 cycles")
        parts.append(" Error termination via Lnk1e")
        return "\n".join(parts) + "\n"
    if mode not in (
        "success_opt",
        "success_sp",
        "success_freq",
        "missing_geometry",
        "missing_freq",
        "ts_candidate",
    ):
        raise ValueError(f"unknown fake_g16 mode: {mode!r}")
    if mode != "success_sp" and mode != "missing_geometry":
        parts.append(render_orientation_block(shifted_atoms, shifted))
    parts.append(f" SCF Done:  E(RB3LYP) =  {ENERGY_HARTREE:.10f}     A.U. after   12 cycles")
    if mode in ("success_freq", "ts_candidate"):
        frequencies = (
            REAL_FREQUENCIES
            if mode == "success_freq"
            else (
                TS_IMAG_FREQUENCY,
                *REAL_FREQUENCIES[1:],
            )
        )
        parts.append(render_frequencies_block(frequencies))
        parts.append(render_thermochemistry_block())
    elif mode == "missing_freq":
        parts.append(render_frequencies_block(REAL_FREQUENCIES))
    if mode != "abnormal":
        parts.append(" Normal termination of Gaussian 16")
    return "\n".join(parts) + "\n"


def parse_gjf_coordinates(
    path: str,
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Parse element symbols and coordinates from a ``.gjf`` input file."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    charge_index: int | None = None
    for position, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) == 2:
            try:
                int(tokens[0])
                int(tokens[1])
            except ValueError:
                continue
            charge_index = position
            break
    if charge_index is None:
        raise ValueError(f"no charge/multiplicity line found in {path!r}")
    atoms: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for line in lines[charge_index + 1 :]:
        if not line.strip():
            if coordinates:
                break
            continue
        tokens = line.split()
        if len(tokens) < 4:
            continue
        symbol = tokens[0]
        if symbol not in ATOMIC_NUMBERS:
            continue
        try:
            if len(tokens) >= 5 and tokens[1] in ("0", "-1"):
                point = (float(tokens[2]), float(tokens[3]), float(tokens[4]))
            else:
                point = (float(tokens[-3]), float(tokens[-2]), float(tokens[-1]))
        except ValueError:
            continue
        atoms.append(symbol)
        coordinates.append(point)
    if not atoms:
        raise ValueError(f"no coordinates found in {path!r}")
    return tuple(atoms), tuple(coordinates)


def find_input_file(explicit: str | None) -> str:
    """Resolve the ``.gjf`` input file from argv or the work directory."""
    if explicit:
        return explicit
    matches = sorted(glob.glob("*.gjf"))
    if not matches:
        raise ValueError("no .gjf input file found in the work directory")
    return matches[0]


def write_outputs(stem: str, mode: str, log_text: str) -> None:
    """Write the fake log and companion checkpoint files."""
    with open(f"{stem}.log", "w", encoding="utf-8") as handle:
        handle.write(log_text)
    if mode != "abnormal":
        with open(f"{stem}.chk", "wb") as handle:
            handle.write(b"")


def main(argv: list[str]) -> int:
    """Run the fake Gaussian executable."""
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
        atoms, coordinates = parse_gjf_coordinates(input_path)
        log_text = render_log_text(mode, atoms, coordinates)
    except (ValueError, OSError) as exc:
        print(f"fake_g16: {exc}", file=sys.stderr)
        return 3
    stem = input_path.rsplit(".", 1)[0] if "." in input_path else input_path
    write_outputs(stem, mode, log_text)
    return 0 if mode != "abnormal" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
