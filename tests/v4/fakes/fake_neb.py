#!/usr/bin/env python3

"""Fake ORCA NEB executable for ConfFlow Workflow V4-5 chain tests.

Reads the ``.inp`` reactant geometry, emits ``FAKE_NEB_IMAGES``
intermediate images (default 5) plus both endpoints in real ORCA 6.1
grammar (verified against installed-binary HCN/HF-3c and NH3/HF-3c NEB
runs): a ``<base>_MEP_trj.xyz`` trajectory file (standard XYZ blocks
with ``Coordinates from ORCA-job ... E <float>`` comments) and a log
carrying the trajectory announcement.  When ``FAKE_NEB_TS=1``, a real
plain-NEB ``INFORMATION ABOUT HIGHEST ENERGY IMAGE`` report block is
appended; when ``FAKE_NEB_TS=saddle``, a real NEB-TS
``INFORMATION ABOUT SADDLE POINT`` report block (``Climbing image`` /
``SADDLE POINT (ANGSTROEM)``, as the live binary prints under the
NEB-TS keyword) is appended instead.  Without either, no TS candidate
exists (a path maximum is never promoted).  Only ``main`` touches the
filesystem or the process environment.
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
    # Endpoints included: reactant (input geometry), count intermediates,
    # product (shifted far end).  Energies form a deterministic barrier
    # peaking mid-path.
    total = count + 2
    peak = total // 2
    blocks = []
    for position in range(total):
        if position == 0:
            shift, energy = 0.0, -76.460000
        elif position == total - 1:
            shift, energy = 0.2, -76.459000
        else:
            height = 1.0 - abs(position - peak) / max(peak, 1)
            shift, energy = 0.02 * position, -76.460000 + 0.005 * height
        rows = "\n".join(
            f"  {symbol}          {x + shift:.8f}      {y:.8f}      {z:.8f}"
            for symbol, x, y, z in atoms
        )
        blocks.append(f"{len(atoms)}\nCoordinates from ORCA-job {base}_MEP E {energy:.6f}\n{rows}")
    with open(base + "_MEP_trj.xyz", "w", encoding="utf-8") as handle:
        handle.write("\n".join(blocks) + "\n")
    log_lines = [
        "ORCA 6.1 fake preamble (never image data)",
        f"Number of images (incl. end points)     ....  {total}",
        f"Number of intermediate images           ....  {count}",
        f"Current trajectory will be written to    ....  {base}_MEP_trj.xyz",
    ]
    if os.environ.get("FAKE_NEB_TS") == "1":
        ts_rows = "\n".join(
            f"{symbol}     {x + 0.05:.6f}     {y:.6f}     {z:.6f}" for symbol, x, y, z in atoms
        )
        log_lines.extend(
            [
                "---------------------------------------------------------------",
                "           INFORMATION ABOUT HIGHEST ENERGY IMAGE",
                "---------------------------------------------------------------",
                "",
                "Highest energy image                      ....  3",
                "Energy                                    ....  -76.455000 Eh",
                "Max. abs. force                           ....  1.5798e-01 Eh/Bohr",
                "",
                "-----------------------------------------",
                "  HIGHEST ENERGY IMAGE (ANGSTROEM)",
                "-----------------------------------------",
                ts_rows,
                "",
            ]
        )
    elif os.environ.get("FAKE_NEB_TS") == "saddle":
        ts_rows = "\n".join(
            f"{symbol}     {x + 0.05:.6f}     {y:.6f}     {z:.6f}" for symbol, x, y, z in atoms
        )
        log_lines.extend(
            [
                "---------------------------------------------------------------",
                "               INFORMATION ABOUT SADDLE POINT",
                "---------------------------------------------------------------",
                "",
                "Climbing image                            ....  3",
                "Energy                                    ....  -76.455000 Eh",
                "Max. abs. force                           ....  1.5798e-01 Eh/Bohr",
                "",
                "-----------------------------------------",
                "  SADDLE POINT (ANGSTROEM)",
                "-----------------------------------------",
                ts_rows,
                "",
            ]
        )
    log_lines.append("****ORCA TERMINATED NORMALLY****")
    with open(base + ".out", "w", encoding="utf-8") as handle:
        handle.write("\n".join(log_lines) + "\n")
    with open(base + ".xyz", "w", encoding="utf-8") as handle:
        handle.write(f"{len(atoms)}\nfirst image\n")
        for symbol, x, y, z in atoms:
            handle.write(f"{symbol} {x:.6f} {y:.6f} {z:.6f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
