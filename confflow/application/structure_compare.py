"""``confflow compare`` / ``confflow dedupe``: same-structure checks for XYZ files.

This module only does I/O and argument handling. The identity science (graph
automorphisms, mirror handling, RMSD bounds) lives in
:mod:`confflow.science.confgen.compare`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..science.confgen.compare import dedupe, pair_rmsd, structure_identity

EXIT_OK = 0
EXIT_USAGE = 2

HARTREE_TO_KCAL = 627.5094740631

_EH_RE = re.compile(r"energy_eh=(\S+)")
_ORCA_RE = re.compile(r"\bE\s+(-?\d+\.\d+)")


class StructureCompareError(ValueError):
    """A user-facing input or argument error (reported with exit code 2)."""


@dataclass(frozen=True)
class XyzFrame:
    """One frame of a multi-frame XYZ file."""

    elements: tuple[str, ...]
    coords: np.ndarray
    comment: str


@dataclass(frozen=True)
class Entry:
    """A frame together with the file and frame index it came from."""

    file: str
    index: int
    elements: tuple[str, ...]
    coords: np.ndarray
    comment: str
    energy: float | None


def parse_xyz_frames(text: str, *, source: str) -> list[XyzFrame]:
    """Parse every frame of a multi-frame XYZ text, skipping blank lines between frames."""
    lines = text.splitlines()
    frames: list[XyzFrame] = []
    pos = 0
    while True:
        while pos < len(lines) and not lines[pos].strip():
            pos += 1
        if pos >= len(lines):
            break
        header_line = pos + 1
        try:
            natoms = int(lines[pos].strip())
        except ValueError:
            raise StructureCompareError(
                f"{source}: line {header_line}: expected an atom count, got {lines[pos].strip()!r}"
            ) from None
        if natoms < 1:
            raise StructureCompareError(f"{source}: line {header_line}: atom count must be >= 1")
        if pos + 1 >= len(lines):
            raise StructureCompareError(
                f"{source}: line {header_line}: truncated frame (missing comment line)"
            )
        comment = lines[pos + 1]
        body = lines[pos + 2 : pos + 2 + natoms]
        if len(body) < natoms:
            raise StructureCompareError(
                f"{source}: line {header_line}: truncated frame, expected {natoms} atoms, "
                f"found {len(body)}"
            )
        elements: list[str] = []
        coords: list[list[float]] = []
        for offset, line in enumerate(body):
            line_no = pos + 3 + offset
            if not line.strip():
                raise StructureCompareError(
                    f"{source}: line {line_no}: truncated frame, expected {natoms} atoms, "
                    f"found {offset}"
                )
            tokens = line.split()
            if len(tokens) < 4:
                raise StructureCompareError(
                    f"{source}: line {line_no}: expected an element and three coordinates"
                )
            try:
                xyz = [float(value) for value in tokens[1:4]]
            except ValueError:
                raise StructureCompareError(
                    f"{source}: line {line_no}: non-numeric coordinate in {line.strip()!r}"
                ) from None
            elements.append(tokens[0])
            coords.append(xyz)
        frames.append(
            XyzFrame(
                elements=tuple(elements),
                coords=np.asarray(coords, dtype=float),
                comment=comment,
            )
        )
        pos += 2 + natoms
    if not frames:
        raise StructureCompareError(f"{source}: no XYZ frames found")
    return frames


def parse_energy(comment: str) -> float | None:
    """Energy in Eh from a comment line: ``energy_eh=`` first, then ORCA ``E <float>``."""
    match = _EH_RE.search(comment)
    if match is not None:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    match = _ORCA_RE.search(comment)
    if match is not None:
        return float(match.group(1))
    return None


def _read_entries(paths: list[str], *, no_energy: bool) -> list[Entry]:
    """Read all frames of all files and enforce one shared element sequence."""
    entries: list[Entry] = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise StructureCompareError(f"cannot read {path}: {exc.strerror or exc}") from None
        for index, frame in enumerate(parse_xyz_frames(text, source=path)):
            energy = None if no_energy else parse_energy(frame.comment)
            entries.append(
                Entry(
                    file=path,
                    index=index,
                    elements=frame.elements,
                    coords=frame.coords,
                    comment=frame.comment,
                    energy=energy,
                )
            )
    reference = entries[0]
    for entry in entries[1:]:
        if entry.elements != reference.elements:
            raise StructureCompareError(
                f"element sequence of {entry.file}@{entry.index} differs from "
                f"{reference.file}@{reference.index}"
            )
    return entries


def _split_selector(text: str) -> tuple[str, int]:
    """Split ``path@k`` into the path and a 0-based frame index (default 0)."""
    head, sep, tail = text.rpartition("@")
    if sep and head and tail.isdigit():
        return head, int(tail)
    return text, 0


def _select(entries: list[Entry], path: str, index: int) -> Entry:
    """Pick frame ``index`` of ``path`` from already-read entries."""
    frames = [entry for entry in entries if entry.file == path]
    if index >= len(frames):
        raise StructureCompareError(
            f"frame index {index} out of range for {path} ({len(frames)} frames)"
        )
    return frames[index]


def _one_based(value: int, natoms: int, what: str) -> int:
    if not 1 <= value <= natoms:
        raise StructureCompareError(f"{what} {value} out of range 1..{natoms}")
    return value - 1


def _identity_options(args: argparse.Namespace, elements: tuple[str, ...], coords: np.ndarray):
    """Build the shared identity from one structure, validating CLI indices."""
    if args.rmsd <= 0:
        raise StructureCompareError("--rmsd must be positive")
    if args.bond_scale <= 0:
        raise StructureCompareError("--bond-scale must be positive")
    if args.e_tol < 0:
        raise StructureCompareError("--e-tol must be non-negative")
    natoms = len(elements)
    metal = None
    if args.metal is not None:
        metal = _one_based(args.metal, natoms, "--metal atom")
    pairs: list[tuple[int, int]] = []
    for first, second in args.pair or []:
        i = _one_based(first, natoms, "--pair atom")
        j = _one_based(second, natoms, "--pair atom")
        if i == j:
            raise StructureCompareError(f"--pair {first} {second}: atoms must differ")
        pairs.append((i, j))
    return structure_identity(
        list(elements),
        coords,
        bond_scale=args.bond_scale,
        metal=metal,
        reaction_pairs=tuple(pairs),
        r_tol=args.rmsd,
        mirror=not args.no_mirror,
        e_tol=args.e_tol,
    )


def _format_kcal(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _run_compare(args: argparse.Namespace) -> int:
    path_a, index_a = _split_selector(args.a)
    path_b, index_b = _split_selector(args.b)
    entries = _read_entries(
        [path_a, path_b] if path_a != path_b else [path_a], no_energy=args.no_energy
    )
    first = _select(entries, path_a, index_a)
    second = _select(entries, path_b, index_b)
    identity = _identity_options(args, first.elements, first.coords)
    sym, plain = pair_rmsd(identity, first.coords, second.coords)
    delta = None
    if first.energy is not None and second.energy is not None:
        delta = (second.energy - first.energy) * HARTREE_TO_KCAL
    same = sym < args.rmsd and (delta is None or abs(delta) <= args.e_tol)
    verdict = "same" if same else "different"
    label_a = f"{path_a}@{index_a}"
    label_b = f"{path_b}@{index_b}"
    if args.json:
        report: dict[str, Any] = {
            "a": {"file": path_a, "frame": index_a},
            "b": {"file": path_b, "frame": index_b},
            "symmetry_rmsd": float(sym),
            "heavy_atom_rmsd": float(plain),
            "delta_kcal": None if delta is None else float(delta),
            "rmsd_threshold": args.rmsd,
            "e_tol": args.e_tol,
            "verdict": verdict,
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"A: {label_a}")
        print(f"B: {label_b}")
        print(f"symmetry RMSD: {sym:.4f} A")
        print(f"heavy-atom RMSD (plain): {plain:.4f} A")
        print(f"dE: {_format_kcal(delta)} kcal/mol")
        print(f"verdict: {verdict}")
    return EXIT_OK


def _write_representatives(
    path: str, entries: list[Entry], kept: list[int], members: Counter
) -> None:
    """Write kept frames with their comments plus ``members=<n>``."""
    chunks: list[str] = []
    for index in kept:
        entry = entries[index]
        comment = (
            f"{entry.comment} members={members[index]}"
            if entry.comment
            else (f"members={members[index]}")
        )
        chunks.append(f"{len(entry.elements)}\n{comment}\n")
        for element, (x, y, z) in zip(entry.elements, entry.coords.tolist(), strict=True):
            chunks.append(f"{element} {x:.8f} {y:.8f} {z:.8f}\n")
    try:
        Path(path).write_text("".join(chunks), encoding="utf-8")
    except OSError as exc:
        raise StructureCompareError(f"cannot write {path}: {exc.strerror or exc}") from None


def _run_dedupe(args: argparse.Namespace) -> int:
    entries = _read_entries(args.inputs, no_energy=args.no_energy)
    first = entries[0]
    identity = _identity_options(args, first.elements, first.coords)
    energies = None if args.no_energy else [entry.energy for entry in entries]
    rows = dedupe(identity, [entry.coords for entry in entries], energies)
    kept = [row.index for row in rows if row.representative == row.index]
    members = Counter(row.representative for row in rows)
    known = [entry.energy for entry in entries]
    if all(energy is not None for energy in known):
        kept.sort(key=lambda index: float(known[index] or 0.0))
    if args.output:
        _write_representatives(args.output, entries, kept, members)
    if args.json:
        frames = []
        for row in rows:
            entry = entries[row.index]
            rep = entries[row.representative]
            frames.append(
                {
                    "file": entry.file,
                    "frame": entry.index,
                    "representative_file": rep.file,
                    "representative_frame": rep.index,
                    "rmsd": None if row.rmsd is None else float(row.rmsd),
                    "delta_kcal": None if row.delta_kcal is None else float(row.delta_kcal),
                }
            )
        report: dict[str, Any] = {
            "total": len(entries),
            "unique": len(kept),
            "output": args.output,
            "frames": frames,
        }
        print(json.dumps(report, indent=2))
        return EXIT_OK
    print(f"{len(entries)} frames -> {len(kept)} unique")
    for row in rows:
        entry = entries[row.index]
        label = f"{entry.file}@{entry.index}"
        if row.representative == row.index:
            print(f"{label}: unique")
            continue
        rep = entries[row.representative]
        detail = f"rmsd {row.rmsd:.2f} A"
        if row.delta_kcal is not None:
            detail += f", dE {row.delta_kcal:.2f} kcal/mol"
        print(f"{label}: duplicate of {rep.file}@{rep.index} ({detail})")
    if args.output:
        print(f"wrote {args.output} ({len(kept)} frames)")
    return EXIT_OK


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--rmsd", type=float, default=0.2, help="same-structure RMSD threshold, A (default 0.2)"
    )
    parser.add_argument(
        "--no-mirror", action="store_true", help="do not treat mirror images as the same"
    )
    parser.add_argument(
        "--bond-scale", type=float, default=1.15, help="distance-bond scale factor (default 1.15)"
    )
    parser.add_argument(
        "--metal", type=int, default=None, help="1-based index of the metal atom (default: auto)"
    )
    parser.add_argument(
        "--pair",
        nargs=2,
        type=int,
        action="append",
        metavar=("I", "J"),
        help="1-based reaction pair (forming bond); repeatable",
    )
    parser.add_argument(
        "--e-tol", type=float, default=0.5, help="energy tolerance, kcal/mol (default 0.5)"
    )
    parser.add_argument(
        "--no-energy", action="store_true", help="ignore energies; compare geometry only"
    )
    parser.add_argument("--json", action="store_true", help="print a JSON report")


def _build_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"confflow {command}")
    if command == "compare":
        parser.add_argument("a", help="structure A as PATH[@k] (0-based frame, default 0)")
        parser.add_argument("b", help="structure B as PATH[@k] (0-based frame, default 0)")
    else:
        parser.add_argument(
            "inputs", nargs="+", metavar="IN.xyz", help="multi-frame XYZ input files"
        )
        parser.add_argument(
            "-o", "--output", help="write unique representative frames to this XYZ file"
        )
    _add_common_options(parser)
    return parser


def main(argv: list[str]) -> int:
    """Entry point for ``confflow compare`` and ``confflow dedupe``; argv[0] is the command."""
    if not argv or argv[0] not in ("compare", "dedupe"):
        print("Error: expected 'compare' or 'dedupe'", file=sys.stderr)
        return EXIT_USAGE
    command = argv[0]
    args = _build_parser(command).parse_args(argv[1:])
    try:
        if command == "compare":
            return _run_compare(args)
        return _run_dedupe(args)
    except StructureCompareError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
