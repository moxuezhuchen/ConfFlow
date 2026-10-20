"""Fixed torsion states: rule, audit, explorer filter, reminder scan, schema, worker and diagnostic.

Torsions are declared by four atoms (0-based in the engine). The rule keeps the reference hand of
a "hand" torsion (|sin| >= 0.5) and the reference side of a "side" torsion (sign of cos). The
molecules are tiny alkanes so the RDKit geometry and the fake engine stay fast.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolTransforms

from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.execution import confgen_search_run as search_run
from confflow.execution import confgen_search_worker as worker
from confflow.science.confgen import explore, search, state_scan, stereo
from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph
from confflow.science.confgen.identity import HARTREE_TO_KCAL, Identity
from confflow.workflow.v4.confgen_schema import ConfgenModelV3, ConfgenSearchModel
from tests.v4.test_confgen_explore import _FakeCalc, _settings
from tests.v4.test_confgen_search_step import _SEARCH, _driving_metal, _run, _scope, _write_fake

_PENTANE = "CCCCC"


def _four(theta: float) -> np.ndarray:
    """Four atoms whose dihedral a-b-c-d is exactly ``theta`` degrees (b-c along x)."""
    rad = np.radians(theta)
    return np.array(
        [[0.0, 1.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, np.cos(rad), np.sin(rad)]]
    )


def _build(dihedrals: list[tuple[tuple[int, int, int, int], float]]):
    """Embed pentane, set the given heavy-atom dihedrals; return (molecule, graph, coords)."""
    mol = Chem.AddHs(Chem.MolFromSmiles(_PENTANE))
    assert AllChem.EmbedMolecule(mol, randomSeed=7) == 0
    AllChem.MMFFOptimizeMolecule(mol)
    conf = mol.GetConformer()
    for quad, degrees in dihedrals:
        rdMolTransforms.SetDihedralDeg(conf, *quad, degrees)
    graph = TypedGraph(
        tuple(AtomRef(index=a.GetIdx(), element=a.GetSymbol()) for a in mol.GetAtoms()),
        tuple(
            TypedEdge(
                b.GetBeginAtomIdx(), b.GetEndAtomIdx(), EdgeType.COVALENT, b.GetBondTypeAsDouble()
            )
            for b in mol.GetBonds()
        ),
        total_charge=0,
        multiplicity=1,
    )
    return mol, graph, np.array(conf.GetPositions(), dtype=float)


def _pentane_ref() -> tuple[TypedGraph, np.ndarray]:
    """Return a pentane reference with gauche 0-1-2-3 (+65) and anti 1-2-3-4 (no C2 symmetry)."""
    _, graph, ref = _build([((0, 1, 2, 3), 65.0), ((1, 2, 3, 4), 180.0)])
    return graph, ref


# ----------------------------------------------------------------------------- 1. the rule


@pytest.mark.parametrize(
    ("theta0", "kind"),
    [
        (60, "hand"),
        (120, "hand"),
        (-70, "hand"),
        (0, "side"),
        (10, "side"),
        (175, "side"),
        (-170, "side"),
    ],
)
def test_rule_chooses_hand_or_side_from_the_reference(theta0: float, kind: str) -> None:
    (state,) = stereo.torsion_states(_four(theta0), [(0, 1, 2, 3)])
    assert state.kind == kind
    assert state.theta0 == pytest.approx(theta0)
    if kind == "hand":
        assert state.sign == (1 if np.sin(np.radians(theta0)) > 0 else -1)
    else:
        assert state.sign == (1 if np.cos(np.radians(theta0)) > 0 else -1)


@pytest.mark.parametrize(
    ("reference", "candidate", "kept"),
    [
        (60.0, 60.0, True),
        (60.0, -60.0, False),  # mirrored hand: sign of sin flips
        (-70.0, 70.0, False),
        (120.0, 130.0, True),  # same hand, moved
        (10.0, -10.0, True),  # mirrored side: cos is unchanged
        (175.0, -175.0, True),  # side state keeps across the +-180 seam
        (-170.0, 170.0, True),
        (175.0, 5.0, False),  # side flips to the other cos sign
        (60.0, 0.0, False),  # exact boundary of a hand state (sin == 0) never passes
    ],
)
def test_candidate_keeps_or_changes_the_declared_side(
    reference: float, candidate: float, kept: bool
) -> None:
    (state,) = stereo.torsion_states(_four(reference), [(0, 1, 2, 3)])
    assert stereo.torsion_state_kept(_four(candidate), state) is kept
    assert stereo.preserves_torsions(_four(candidate), (state,)) is kept


def test_hand_side_threshold_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    coords = _four(30.0)
    edge = abs(np.sin(np.radians(stereo.torsion_angle(coords, (0, 1, 2, 3)))))
    monkeypatch.setattr(stereo, "TORSION_HAND_SIN", edge)
    assert stereo.torsion_states(coords, [(0, 1, 2, 3)])[0].kind == "hand"
    monkeypatch.setattr(stereo, "TORSION_HAND_SIN", float(np.nextafter(edge, 1.0)))
    assert stereo.torsion_states(coords, [(0, 1, 2, 3)])[0].kind == "side"


def test_degenerate_reference_raises_value_error() -> None:
    coincident = _four(60.0)
    coincident[2] = coincident[1]
    with pytest.raises(ValueError, match="undefined"):
        stereo.torsion_states(coincident, [(0, 1, 2, 3)])
    collinear = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 1.0, 0.0]])
    with pytest.raises(ValueError, match="collinear"):
        stereo.torsion_states(collinear, [(0, 1, 2, 3)])


def test_degenerate_candidate_fails_closed() -> None:
    (state,) = stereo.torsion_states(_four(60.0), [(0, 1, 2, 3)])
    collapsed = _four(60.0)
    collapsed[0] = collapsed[1]
    assert not stereo.torsion_state_kept(collapsed, state)
    assert stereo.preserves_torsions(collapsed, ()) is True  # nothing declared, nothing to keep


# ----------------------------------------------------------------------------- 2. the audit


def test_audit_fails_a_mirrored_declared_gauche_torsion() -> None:
    graph, ref = _pentane_ref()
    states = stereo.torsion_states(ref, [(0, 1, 2, 3)])
    _, _, mirrored = _build([((0, 1, 2, 3), -65.0), ((1, 2, 3, 4), 180.0)])
    rec = search.audit_structure(mirrored, graph, None, ref, None, torsion_states=states)
    assert rec.failed_checks == ("fixed_state",)


def test_audit_passes_an_unchanged_declared_torsion() -> None:
    graph, ref = _pentane_ref()
    states = stereo.torsion_states(ref, [(0, 1, 2, 3)])
    assert (
        search.audit_structure(ref, graph, None, ref, None, torsion_states=states).failed_checks
        == ()
    )


def test_audit_without_declaration_passes_the_mirror() -> None:
    graph, ref = _pentane_ref()
    _, _, mirrored = _build([((0, 1, 2, 3), -65.0), ((1, 2, 3, 4), 180.0)])
    assert search.audit_structure(mirrored, graph, None, ref, None).failed_checks == ()


# ----------------------------------------------------------------------------- 3. the explorer


def test_explorer_keeps_the_declared_state_in_every_published_record() -> None:
    graph, ref = _pentane_ref()
    state = stereo.torsion_states(ref, [(0, 1, 2, 3)])[0]
    run = explore.explore(
        graph,
        None,
        ref,
        None,
        _settings(budget=40, fixed_states=((0, 1, 2, 3),)),
        _FakeCalc(),
        map,
    )
    assert run.records, "the fake engine should publish at least one minimum"
    assert all(stereo.torsion_state_kept(r.coords, state) for r in run.records)
    fixed = run.stats["fixed_states"]
    assert fixed["states"] == [asdict(state)]
    assert fixed["states"][0]["kind"] == "hand"
    assert isinstance(fixed["generated_rejected"], int) and fixed["generated_rejected"] >= 0
    assert isinstance(fixed["audit_rejected"], int) and fixed["audit_rejected"] >= 0
    assert "lowest_audit_rejected_kcal" in fixed


@pytest.mark.parametrize(
    "bad",
    [
        ((0, 1, 2),),  # three atoms
        ((0, 1, 1, 2),),  # repeated atom
        ((0, 1, 2, 17),),  # out of range for 17 atoms
    ],
)
def test_explorer_refuses_invalid_declarations(bad: tuple[tuple[int, ...], ...]) -> None:
    graph, ref = _pentane_ref()
    with pytest.raises(search.SearchError):
        explore.explore(graph, None, ref, None, _settings(fixed_states=bad), _FakeCalc(), map)


# ----------------------------------------------------------------------------- 4. reminder scan


def _pool(*dihedrals_list):
    """Return (identity, reference, structures) for pentane variants; the first is the reference."""
    graph, ref = _pentane_ref()
    identity = Identity(graph)
    structures = [ref] + [
        _build([((0, 1, 2, 3), d), ((1, 2, 3, 4), 180.0)])[2] for d in dihedrals_list
    ]
    return identity, ref, structures


def test_scan_reports_a_mirrored_hand_torsion_once() -> None:
    identity, ref, structures = _pool(-65.0)
    energies = [-100.0, -100.0 + 0.001]
    scan = state_scan.scan_states(identity, ref, structures, energies)
    assert scan["aligned"] is True and scan["scanned"] == 2
    (unit,) = scan["units"]
    assert unit["structures"] == 1 and unit["elements"] == 1
    assert unit["atoms"] == [0, 1, 2, 3]
    assert unit["kind"] == "hand"
    assert unit["theta0"] == pytest.approx(65.0, abs=0.1)
    assert unit["lowest_changed_kcal"] == pytest.approx(0.001 * HARTREE_TO_KCAL)
    assert unit["lowest_same_kcal"] == pytest.approx(0.0)


def test_scan_drops_a_torsion_that_reaches_the_anti_boundary() -> None:
    identity, ref, structures = _pool(-65.0, 180.0)
    # the 180 structure keeps (0,1,2,3) at the boundary, so it is not a discrete change
    scan = state_scan.scan_states(identity, ref, structures, [-100.0, -99.999, -100.0005])
    assert scan["units"] == []


def test_scan_excludes_declared_quads_in_either_order() -> None:
    identity, ref, structures = _pool(-65.0)
    energies = [-100.0, -100.0 + 0.001]
    forward = state_scan.scan_states(identity, ref, structures, energies, declared=[(0, 1, 2, 3)])
    reverse = state_scan.scan_states(identity, ref, structures, energies, declared=[(3, 2, 1, 0)])
    assert forward["units"] == [] and reverse["units"] == []


def test_scan_excludes_symmetry_equivalents_of_declared_quads() -> None:
    # gauche/gauche pentane: relabelling may move a 0-1-2-3 flip onto the equivalent 4-3-2-1
    _, graph, ref = _build([((0, 1, 2, 3), 65.0), ((1, 2, 3, 4), 65.0)])
    flipped = _build([((0, 1, 2, 3), -65.0), ((1, 2, 3, 4), 65.0)])[2]
    identity = Identity(graph)
    energies = [-100.0, -100.0 + 0.001]
    assert state_scan.scan_states(identity, ref, [ref, flipped], energies)["units"]
    for declared in ((0, 1, 2, 3), (3, 2, 1, 0), (1, 2, 3, 4), (4, 3, 2, 1)):
        scan = state_scan.scan_states(identity, ref, [ref, flipped], energies, declared=[declared])
        assert scan["units"] == [], declared


def test_scan_needs_at_least_two_structures() -> None:
    identity, ref, structures = _pool(-65.0)
    scan = state_scan.scan_states(identity, ref, structures[:1], [-100.0])
    assert scan == {"aligned": False, "scanned": 1, "units": []}


# ----------------------------------------------------------------------------- 5. schema


def test_schema_accepts_distinct_quads() -> None:
    model = ConfgenSearchModel.model_validate({"fixed_states": [[1, 2, 3, 4], [5, 2, 3, 6]]})
    assert model.fixed_states == [[1, 2, 3, 4], [5, 2, 3, 6]]


@pytest.mark.parametrize(
    "fixed",
    [
        [[1, 2, 3]],  # three atoms
        [[1, 2, 2, 3]],  # repeated atom
        [[1, 2, 3, 4], [1, 2, 3, 4]],  # duplicate
        [[1, 2, 3, 4], [4, 3, 2, 1]],  # reversed duplicate
    ],
)
def test_schema_rejects_malformed_or_duplicate_quads(fixed: list[list[int]]) -> None:
    with pytest.raises(ValidationError):
        ConfgenSearchModel.model_validate({"fixed_states": fixed})


# ----------------------------------------------------------------------------- 6. worker


@pytest.mark.parametrize("index_base", [0, 1])
def test_fixed_states_schema_to_worker_index_conversion(tmp_path: Path, index_base: int) -> None:
    """The public index convention survives compilation, job writing, and worker parsing."""
    import json

    from confflow.domain import FrozenDict

    scope = _scope(
        index_base=index_base,
        coordination={
            "metal_center": index_base,
            "binding_sites": [{"id": f"s{i}", "atoms": [index_base + i + 1]} for i in range(4)],
            "shapes": ["square_planar"],
        },
    )
    # toy atoms 0-3 are collinear on the Pt axis; N1-Pt-Cl3-N2 is a defined, off-axis dihedral
    quad = [index_base + i for i in (1, 0, 3, 2)]
    scope["search"] = {**_SEARCH, "fixed_states": [quad]}
    native = FrozenDict(ConfgenModelV3.model_validate(scope).scientific_native())
    out, _ = _run(native, _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    jobs = list(tmp_path.rglob("search_job.json"))
    assert len(jobs) == 1
    wire = json.loads(jobs[0].read_text())
    assert wire["settings"]["fixed_states"] == [[1, 0, 3, 2]]
    loaded = worker._load_job(str(jobs[0]))
    assert loaded["settings"].fixed_states == ((1, 0, 3, 2),)


# ----------------------------------------------------------------------------- 7. diagnostic


def _item() -> SimpleNamespace:
    return SimpleNamespace(id="cg-item:id", logical_key="cg-item")


def _changes(**unit: Any) -> dict[str, Any]:
    row = {
        "structures": 2,
        "elements": 1,
        "atoms": [0, 1, 2, 3],
        "kind": "hand",
        "theta0": 65.0,
        "lowest_changed_kcal": 0.63,
        "lowest_same_kcal": 0.0,
    }
    row.update(unit)
    return {"aligned": True, "scanned": 4, "units": [row]}


def test_state_change_warning_shifts_atoms_and_pastes_declaration() -> None:
    warn = search_run._state_change_diagnostic({"state_changes": _changes()}, "s", _item(), base=1)
    assert warn is not None and warn.code == "confgen_conformational_states"
    assert warn.severity is DiagnosticSeverity.WARNING
    assert "[1, 2, 3, 4] (hand)" in warn.message
    assert "2 structures in the other state" in warn.message
    assert "0.63 kcal/mol" in warn.message
    assert "search.fixed_states: [[1, 2, 3, 4]]" in warn.message
    details = dict(warn.details)
    assert details["aligned"] is True
    assert list(details["units"][0]["atoms"]) == [1, 2, 3, 4]
    assert details["units"][0]["lowest_same_kcal"] == 0.0


@pytest.mark.parametrize(
    "stats",
    [
        None,
        {},
        {"state_changes": None},
        {"state_changes": {"aligned": True, "units": []}},
        {"state_changes": {"error": "boom", "units": []}},
        {"state_changes": _changes(atoms=[0, 1, 2, True])},
        {"state_changes": _changes(atoms=[0, 1, 2])},
        {"state_changes": _changes(lowest_changed_kcal="x")},
        {"state_changes": {"units": [{"atoms": [0, 1, 2, 3]}]}},
    ],
)
def test_state_change_diagnostic_is_silent_for_missing_or_malformed(stats: Any) -> None:
    assert search_run._state_change_diagnostic(stats, "s", _item(), base=0) is None
