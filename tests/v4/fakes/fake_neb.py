#!/usr/bin/env python3

"""Fake ORCA NEB executable for ConfFlow Workflow V4-5 chain tests.

Reads the ``.inp`` reactant geometry, emits ``FAKE_NEB_IMAGES`` path
images (default 5) in the crafted NEB dialect parsed by
:mod:`confflow.programs.orca.neb` (explicit ``CONFFLOW NEB IMAGE <i> OF
<n>`` banners — identity from native ordinals, never encounter order),
plus the normal-termination marker and an ``.xyz`` companion.  When
``FAKE_NEB_TS=1``, an explicit ``CONFFLOW NEB-TS OPTIMIZED TRANSITION
STATE`` banner with a TS geometry is appended; without it no TS
candidate exists (a path maximum is never promoted).  Only ``main``
touches the filesystem or the process environment.
"""

from __future__ import annotations

import glob
import os
import sys

#: Default image count (overridden by ``FAKE_NEB_IMAGES``).
DEFAULT_IMAGES = 5


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
    """Run the fake NEB job; return the process exit code."""
    matches = sorted(glob.glob("*.inp"))
    if not matches:
        raise ValueError("no .inp input file found in the work directory")
    inp = os.path.abspath(matches[0])
    _ = argv
    atoms = _read_atoms(inp)
    base = os.path.splitext(os.path.basename(inp))[0]
    count = int(os.environ.get("FAKE_NEB_IMAGES", str(DEFAULT_IMAGES)))
    sections = []
    for image in range(1, count + 1):
        shift = 0.02 * (image - 1)
        rows = "\n".join(f"{symbol} {x + shift:.6f} {y:.6f} {z:.6f}" for symbol, x, y, z in atoms)
        energy = -76.46 + 0.001 * abs(image - (count + 1) / 2)
        sections.append(
            f"CONFFLOW NEB IMAGE {image} OF {count}\n"
            f"ENERGY {energy:.6f}\n"
            f"GEOMETRY\n{rows}\nEND GEOMETRY"
        )
    if os.environ.get("FAKE_NEB_TS") == "1":
        rows = "\n".join(f"{symbol} {x + 0.05:.6f} {y:.6f} {z:.6f}" for symbol, x, y, z in atoms)
        sections.append(
            "CONFFLOW NEB-TS OPTIMIZED TRANSITION STATE\n"
            "ENERGY -76.455000\n"
            f"GEOMETRY\n{rows}\nEND GEOMETRY"
        )
    log = "\n".join(sections) + "\n****ORCA TERMINATED NORMALLY****\n"
    with open(base + ".out", "w", encoding="utf-8") as handle:
        handle.write(log)
    with open(base + ".xyz", "w", encoding="utf-8") as handle:
        handle.write(f"{len(atoms)}\nfirst image\n")
        for symbol, x, y, z in atoms:
            handle.write(f"{symbol} {x:.6f} {y:.6f} {z:.6f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
