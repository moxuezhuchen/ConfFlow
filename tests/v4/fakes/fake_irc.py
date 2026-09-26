#!/usr/bin/env python3

"""Fake IRC executable for ConfFlow Workflow V4-5 TSPES tests.

The script simulates a native program behind the V4 process boundary for
reaction-path (IRC) calculations.  It reads the ``.inp`` input geometry from
the current work directory, shifts the coordinates deterministically in
opposite directions for the two path directions, and writes a ``.out`` file
plus ``<stem>_IRC_F.xyz`` / ``<stem>_IRC_B.xyz`` endpoint files speaking
real ORCA 6.1 IRC grammar (verified against an installed-binary HCN
isomerization IRC): asterisk-framed ``FORWARD IRC`` / ``BACKWARD IRC``
sections with ``Iteration / E(Eh)`` tables, and standard XYZ endpoint
files whose comments read
``Coordinates from ORCA-job <stem> E <energy>``.  Direction always comes
from the banners, never from order.

Banner order follows ``FAKE_IRC_ORDER`` (``forward_first`` by default,
``reverse_first`` as a parser-robustness probe: grammar-valid sections in
an order real binaries never emit, proving banner-driven mapping).
Items listed in ``IRC_FAIL_FILE`` (basenames, one per line) — or every
item under ``FAKE_IRC_MODE=missing_reverse`` — emit only the forward
section plus files, so the parser reports a missing reverse direction
exactly as production does.  Only ``main`` touches the filesystem or the
process environment.
"""

from __future__ import annotations

import glob
import os
import sys

#: Native point ordinals reported per direction (distinct on purpose).
FORWARD_POINT = 12

#: Native point ordinals reported per direction (distinct on purpose).
REVERSE_POINT = 3

#: Fixed forward endpoint energy in Hartree.
FORWARD_ENERGY = -76.401234

#: Fixed reverse endpoint energy in Hartree.
REVERSE_ENERGY = -76.398765

#: Deterministic geometry shift (Angstrom, applied to x) per direction.
FORWARD_SHIFT = 0.02
REVERSE_SHIFT = -0.02

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


def shift_coordinates(
    coordinates: tuple[tuple[float, float, float], ...], delta: float
) -> tuple[tuple[float, float, float], ...]:
    """Shift coordinates by *delta* along x."""
    return tuple((x + delta, y, z) for x, y, z in coordinates)


def render_section(
    native_direction: str,
    energy: float,
    point: int,
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render one real-grammar IRC direction section (table only)."""
    return "\n".join(
        [
            "         *************************************************************",
            f"         *                          {native_direction} IRC                      *",
            "         *************************************************************",
            "",
            "Iteration    E(Eh)      dE(kcal/mol)  max(|G|)   RMS(G)",
            "Convergence thresholds                0.002000  0.000500",
            f"    0       {energy + 0.002:.6f}   -1.000000    0.010000  0.005000",
            f"    {point}       {energy:.6f}   -5.000000    0.030000  0.020000",
            "",
        ]
    )


def render_endpoint_xyz(
    stem: str,
    energy: float,
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render one real-grammar ORCA endpoint XYZ file."""
    lines = [str(len(atoms)), f"Coordinates from ORCA-job {stem} E {energy:.6f}"]
    for symbol, (x, y, z) in zip(atoms, coordinates):
        lines.append(f"  {symbol}          {x:.8f}      {y:.8f}      {z:.8f}")
    return "\n".join(lines) + "\n"


def render_xyz_text(
    atoms: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
) -> str:
    """Render the ``.xyz`` companion file content (forward geometry)."""
    lines = [str(len(atoms)), "FakeIRC XYZ companion"]
    for symbol, (x, y, z) in zip(atoms, coordinates):
        lines.append(f"{symbol} {x:.6f} {y:.6f} {z:.6f}")
    return "\n".join(lines) + "\n"


def read_victims(path: str | None) -> set[str]:
    """Return basenames that must omit the reverse endpoint section."""
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8") as handle:
            return {line.strip() for line in handle.read().splitlines() if line.strip()}
    except OSError:
        return set()


def main(argv: list[str]) -> int:
    """Run the fake IRC executable."""
    mode = os.environ.get("FAKE_IRC_MODE", "success")
    order = os.environ.get("FAKE_IRC_ORDER", "forward_first")
    victims = read_victims(os.environ.get("IRC_FAIL_FILE") or None)
    try:
        input_path = find_input_file(argv[1] if len(argv) > 1 else None)
        atoms, coordinates = parse_inp_coordinates(input_path)
    except (ValueError, OSError) as exc:
        print(f"fake_irc: {exc}", file=sys.stderr)
        return 3
    stem = input_path.rsplit(".", 1)[0] if "." in input_path else input_path
    forward_coords = shift_coordinates(coordinates, FORWARD_SHIFT)
    reverse_coords = shift_coordinates(coordinates, REVERSE_SHIFT)
    forward = render_section("FORWARD", FORWARD_ENERGY, FORWARD_POINT, atoms, forward_coords)
    reverse = render_section("BACKWARD", REVERSE_ENERGY, REVERSE_POINT, atoms, reverse_coords)
    missing_reverse = mode == "missing_reverse" or os.path.basename(input_path) in victims
    if missing_reverse:
        sections = [forward]
    elif order == "reverse_first":
        sections = [reverse, forward]
    elif order == "forward_first":
        sections = [forward, reverse]
    else:
        print(f"fake_irc: unknown FAKE_IRC_ORDER {order!r}", file=sys.stderr)
        return 3
    parts = [
        "ORCA 6.1 fake preamble (never endpoint data)",
        "",
        *sections,
        "",
        "                       IRC PATH SUMMARY",
        "****ORCA TERMINATED NORMALLY****",
    ]
    with open(f"{stem}.out", "w", encoding="utf-8") as handle:
        handle.write("\n".join(parts) + "\n")
    with open(f"{stem}_IRC_F.xyz", "w", encoding="utf-8") as handle:
        handle.write(render_endpoint_xyz(stem, FORWARD_ENERGY, atoms, forward_coords))
    if not missing_reverse:
        with open(f"{stem}_IRC_B.xyz", "w", encoding="utf-8") as handle:
            handle.write(render_endpoint_xyz(stem, REVERSE_ENERGY, atoms, reverse_coords))
    with open(f"{stem}.xyz", "w", encoding="utf-8") as handle:
        handle.write(render_xyz_text(atoms, forward_coords))
    with open(f"{stem}.gbw", "wb") as handle:
        handle.write(b"FAKE-IRC-GBW\n" + stem.encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
