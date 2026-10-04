#!/usr/bin/env python3

"""``refine`` reports connectivity that differs inside a group; it never filters on it (A2-b).

The legacy refine block deleted the minority-topology conformers.  V4 refine keeps
every structure, so a conformer whose bonding changed (for example a bond that
broke during an optimization) must at least be named in the step notes.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from confflow.domain.structure import StructureRecord
from confflow.execution.transform_executor import TransformExecutor

DATA = Path(__file__).resolve().parent.parent / "science" / "data" / "molecules_h.json"
NO_MERGE = {"rmsd_threshold_angstrom": 0}


def _butane() -> tuple[list[str], np.ndarray]:
    payload = json.loads(DATA.read_text(encoding="utf-8"))["butane"]
    return list(payload["atoms"]), np.array(payload["coords"], dtype=float)


def _record(identifier: str, atoms: list[str], coords: np.ndarray) -> StructureRecord:
    return StructureRecord(
        id=identifier,
        atoms=tuple(atoms),
        coordinates=tuple(tuple(float(x) for x in row) for row in coords),
        charge=0,
        multiplicity=1,
    )


def _with_h5_detached() -> tuple[list[str], np.ndarray]:
    """Butane with the hydrogen H5 (bonded to C1) pulled well beyond bonding distance."""
    atoms, coords = _butane()
    moved = coords.copy()
    direction = moved[4] - moved[0]
    moved[4] = moved[0] + direction / np.linalg.norm(direction) * 2.6
    return atoms, moved


def _connectivity_notes(notes: list[str]) -> list[str]:
    return [note for note in notes if note.startswith("connectivity of ")]


def test_a_structure_with_a_lost_bond_is_named_and_kept() -> None:
    atoms, coords = _butane()
    _, detached = _with_h5_detached()
    records = [_record("a", atoms, coords), _record("b", atoms, detached)]
    kept, notes = TransformExecutor()._refine(records, dict(NO_MERGE))
    assert [record.id for record in kept] == ["a", "b"]
    assert _connectivity_notes(notes) == [
        "connectivity of b differs from the majority bonding graph of its group "
        "(1 of 2 structures): gained [], lost [C1-H5]"
    ]


def test_the_majority_graph_is_the_reference() -> None:
    atoms, coords = _butane()
    _, detached = _with_h5_detached()
    records = [
        _record("a", atoms, coords),
        _record("b", atoms, coords + 0.001),
        _record("c", atoms, detached),
    ]
    kept, notes = TransformExecutor()._refine(records, dict(NO_MERGE))
    assert [record.id for record in kept] == ["a", "b", "c"]
    assert _connectivity_notes(notes) == [
        "connectivity of c differs from the majority bonding graph of its group "
        "(2 of 3 structures): gained [], lost [C1-H5]"
    ]


def test_a_gained_bond_is_reported_as_gained() -> None:
    atoms, coords = _butane()
    _, detached = _with_h5_detached()
    records = [
        _record("a", atoms, detached),
        _record("b", atoms, detached + 0.001),
        _record("c", atoms, coords),
    ]
    _, notes = TransformExecutor()._refine(records, dict(NO_MERGE))
    assert _connectivity_notes(notes) == [
        "connectivity of c differs from the majority bonding graph of its group "
        "(2 of 3 structures): gained [C1-H5], lost []"
    ]


def test_equal_connectivity_adds_no_note() -> None:
    atoms, coords = _butane()
    records = [_record("a", atoms, coords), _record("b", atoms, coords + 0.001)]
    kept, notes = TransformExecutor()._refine(records, dict(NO_MERGE))
    assert [record.id for record in kept] == ["a", "b"]
    assert _connectivity_notes(notes) == []
