#!/usr/bin/env python3

"""Pure analysis helpers for the MLIP executor: bond checks, calibration, xyz output.

Nothing here touches the filesystem or a process.  The executor (``mlip_executor``)
owns I/O, supervision and WorkItemResult assembly and calls these functions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.elements import atomic_number
from ..domain.errors import DomainError
from ..domain.structure import StructureRecord
from ..science.bonds import perceive_adjacency
from ..science.topology import DEFAULT_PERCEPTION_BOND_SCALE, resolve_working_adjacency
from .energy_filter import HARTREE_TO_KCAL_MOL

__all__ = [
    "BOND_SCALE",
    "MlipOutput",
    "authoritative_bonds",
    "calibration",
    "fix_deviation",
    "mean",
    "perceive_bonds",
    "spearman",
    "stretch_check",
    "xyz_text",
]

#: Bond-perception scale, the same default as the refine transform.
BOND_SCALE = DEFAULT_PERCEPTION_BOND_SCALE


@dataclass
class MlipOutput:
    """One output structure with the rows it was built from."""

    record: StructureRecord
    stage: str
    energy: float
    coords: Sequence[Sequence[float]]
    meta: dict[str, Any]
    row: Mapping[str, Any]
    max_ratio: float | None
    pair: tuple[int, int] | None
    changed: bool


def perceive_bonds(
    atoms: Sequence[str], coords: Sequence[Sequence[float]]
) -> frozenset[tuple[int, int]]:
    """Covalent bonds (sorted 0-based pairs) perceived on one geometry."""
    numbers = [atomic_number(symbol) for symbol in atoms]
    try:
        adjacency = perceive_adjacency(numbers, coords, bond_scale=BOND_SCALE)
    except ValueError as exc:
        raise DomainError(f"mlip bond perception failed: {exc}") from exc
    return frozenset(
        (min(i, j), max(i, j)) for i, row in enumerate(adjacency) for j in row if i != j
    )


def distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.dist(first, second)


def fix_deviation(
    fix_pairs: Sequence[tuple[int, int]],
    before: Sequence[Sequence[float]],
    after: Sequence[Sequence[float]],
    targets: Sequence[float | None] | None = None,
) -> float:
    """Largest absolute deviation of a fixed pair distance from its reference.

    The reference of pair ``k`` is ``targets[k]`` when given and not ``None``,
    otherwise the pair distance in ``before``. ``targets`` must align with
    ``fix_pairs``. Returns 0.0 when nothing is fixed.
    """
    if targets is not None and len(targets) != len(fix_pairs):
        raise ValueError("targets must align one-to-one with fix_pairs")
    refs = targets if targets is not None else (None,) * len(fix_pairs)
    devs = []
    for (i, j), target in zip(fix_pairs, refs, strict=True):
        ref = distance(before[i], before[j]) if target is None else target
        devs.append(abs(distance(after[i], after[j]) - ref))
    return max(devs, default=0.0)


def authoritative_bonds(record: StructureRecord) -> frozenset[tuple[int, int]]:
    """Covalent bonds (sorted 0-based pairs) of the record's intended working graph.

    Resolved through the one topology authority, so a declared patch or persisted graph
    decides which bonds exist; distance perception only fills in what it does not declare.
    """
    numbers = [atomic_number(symbol) for symbol in record.atoms]
    adjacency = resolve_working_adjacency(record, numbers, record.coordinates)
    return frozenset(
        (min(i, j), max(i, j)) for i, row in enumerate(adjacency) for j in row if i != j
    )


def stretch_check(
    record: StructureRecord,
    after: Sequence[Sequence[float]],
    fix_pairs: Sequence[tuple[int, int]],
) -> tuple[float | None, tuple[int, int] | None, bool]:
    """Return (max bond ratio, its pair, graph changed) for the relaxed geometry.

    The ratio is measured over the authoritative input graph.  ``graph changed`` is a
    diagnostic: whether the bonds perceived on the geometry differ before and after.
    """
    fixed = {(min(i, j), max(i, j)) for i, j in fix_pairs}
    before = record.coordinates
    declared = authoritative_bonds(record) - fixed
    observed_in = perceive_bonds(record.atoms, before) - fixed
    observed_out = perceive_bonds(record.atoms, after) - fixed
    best: tuple[float, tuple[int, int]] | None = None
    for i, j in sorted(declared):
        d_in = distance(before[i], before[j])
        if d_in <= 0:
            continue
        ratio = distance(after[i], after[j]) / d_in
        if best is None or ratio > best[0]:
            best = (ratio, (i, j))
    ratio_pair = best or (None, None)
    return ratio_pair[0], ratio_pair[1], observed_in != observed_out


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks (1-based), ties share the mean rank."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        mean_rank = (start + end) / 2.0 + 1.0
        for position in range(start, end + 1):
            ranks[order[position]] = mean_rank
        start = end + 1
    return ranks


def spearman(first: Sequence[float], second: Sequence[float]) -> float | None:
    """Spearman rank correlation, or None when undefined (fewer than 2 or no variance)."""
    if len(first) < 2 or len(first) != len(second):
        return None
    ra, rb = _ranks(first), _ranks(second)
    mean_a, mean_b = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(ra, rb))
    var_a = sum((a - mean_a) ** 2 for a in ra)
    var_b = sum((b - mean_b) ** 2 for b in rb)
    if var_a == 0 or var_b == 0:
        return None
    return cov / math.sqrt(var_a * var_b)


def xyz_text(outputs: Sequence[MlipOutput], keys: Mapping[str, str]) -> str:
    """Multi-frame xyz with the per-structure comment line described in the plan."""
    frames: list[str] = []
    for out in outputs:
        row = out.row
        comment = (
            f"key={keys[out.record.id]} energy_eh={out.energy:.10f} stage={out.stage} "
            f"converged={row.get('converged')} n_evals={row.get('n_evals')} "
            f"wall_s={row.get('wall_s')} max_bond_stretch={out.max_ratio}"
        )
        lines = [
            f"{symbol} {x:.10f} {y:.10f} {z:.10f}"
            for symbol, (x, y, z) in zip(out.record.atoms, out.coords)
        ]
        frames.append(f"{len(lines)}\n{comment}\n" + "\n".join(lines) + "\n")
    return "".join(frames)


def calibration(pairs: Sequence[tuple[str, float, float]], basis: str) -> dict[str, Any] | None:
    """GFN2 against MLIP energies over (structure id, input Eh, MLIP Eh) pairs.

    Returns None with fewer than two pairs.  ``basis`` names what the MLIP energies are
    (``sp_all_inputs`` or ``relaxed_outputs``) and is reported as given.
    """
    if len(pairs) < 2:
        return None
    gfn2 = [pair[1] for pair in pairs]
    mlip = [pair[2] for pair in pairs]
    lowest = min(pairs, key=lambda pair: pair[1])
    return {
        "basis": basis,
        "n": len(pairs),
        "spearman": spearman(gfn2, mlip),
        "input_lowest": lowest[0],
        "kcal_above_mlip_lowest": (lowest[2] - min(mlip)) * HARTREE_TO_KCAL_MOL,
    }
