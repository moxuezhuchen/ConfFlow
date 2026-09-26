#!/usr/bin/env python3

"""Fake ORCA GOAT executable for ConfFlow Workflow V4-5 chain tests.

Reads the ``.inp`` input geometry, emits three deterministic conformers
in the crafted ``confflow-goat-v1`` dialect parsed by
:mod:`confflow.programs.orca.ensemble_parse` (explicit ``GOAT CONFORMER``
banners — identity from banner numbers, never encounter order), plus the
normal-termination marker and an ``.xyz`` companion.  Only ``main``
touches the filesystem or the process environment.
"""

from __future__ import annotations

import glob
import os
import sys

#: Fixed conformer energies in Hartree (index-aligned).
ENERGIES = (-76.460111, -76.459872, -76.459301)

#: Deterministic x-shifts per conformer in Angstrom.
SHIFTS = (0.0, 0.03, -0.04)


def _read_atoms(path: str) -> list[tuple[str, float, float, float]]:
    """Return ``(symbol, x, y, z)`` rows from the ``* xyz`` block."""
    atoms: list[tuple[str, float, float, float]] = []
    in_block = False
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped == "* xyz 0 1":
                in_block = True
                continue
            if in_block:
                if stripped == "*":
                    break
                parts = stripped.split()
                if len(parts) == 4:
                    atoms.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
    if not atoms:
        raise ValueError(f"no coordinates found in {path!r}")
    return atoms


def main(argv: list[str]) -> int:
    """Run the fake GOAT job; return the process exit code."""
    matches = sorted(glob.glob("*.inp"))
    if not matches:
        raise ValueError("no .inp input file found in the work directory")
    inp = os.path.abspath(matches[0])
    _ = argv
    atoms = _read_atoms(inp)
    base = os.path.splitext(os.path.basename(inp))[0]
    sections = []
    for index, (energy, shift) in enumerate(zip(ENERGIES, SHIFTS)):
        rows = "\n".join(f"{symbol} {x + shift:.6f} {y:.6f} {z:.6f}" for symbol, x, y, z in atoms)
        sections.append(f"GOAT CONFORMER {index}\n{rows}\nConformer energy: {energy} Hartree")
    log = "\n".join(sections) + "\n****ORCA TERMINATED NORMALLY****\n"
    with open(base + ".out", "w", encoding="utf-8") as handle:
        handle.write(log)
    first = atoms[0]
    with open(base + ".xyz", "w", encoding="utf-8") as handle:
        handle.write(f"{len(atoms)}\nfirst conformer\n")
        for symbol, x, y, z in atoms:
            handle.write(f"{symbol} {x:.6f} {y:.6f} {z:.6f}\n")
    _ = first
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
