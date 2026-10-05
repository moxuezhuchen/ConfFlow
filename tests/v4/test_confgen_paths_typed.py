#!/usr/bin/env python3

"""Typed v3 path declarations through the executor (C4.3a).

These tests carry the execution-level behaviours that used to be exercised only
through the legacy ``native.paths`` route (ring refusal and the pre-geometry
limit before any geometry, strict short-bond mode, atom ordering, upstream
geometry, explicit single-state grids, schema strictness, preview/runtime graph
agreement) onto the typed ``schema_version: 3`` route, which is the only route
that remains once the legacy native path is retired.  Interior bonds of a C6
chain have measurable dihedral frames; terminal bonds are refused by v3.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.domain import FrozenDict, StructureRecord
from confflow.domain.completion import WorkItemStatus
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.science.confgen.model import build_context
from confflow.science.confgen.torsion.paths import (
    PATH_CROSSES_RING,
    PATH_DISCONNECTED,
    PATH_SHORT_BOND,
)
from confflow.science.confgen.torsion.stage import TorsionStage
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from tests.v4._helpers.repair import _ctx, _item, _sci

GOLDEN = Path(__file__).resolve().parent.parent / "fixtures" / "paths_equivalence"


def _hexane(
    struct_id: str = "hex", *, stretch_bond: tuple[int, float] | None = None
) -> StructureRecord:
    """Linear C6 chain; optionally move atoms after index ``i`` along x by ``dx``."""
    coords = [[float(i) * 1.5, 0.4 * (i % 2), 0.0] for i in range(6)]
    if stretch_bond is not None:
        index, shift = stretch_bond
        for atom in range(index + 1, 6):
            coords[atom][0] += shift
    return StructureRecord(
        id=struct_id,
        atoms=("C",) * 6,
        coordinates=tuple(tuple(point) for point in coords),
        charge=0,
        multiplicity=1,
    )


def _ring_tail() -> StructureRecord:
    """Square ring 1-4 plus tail atom 5 on atom 4."""
    return StructureRecord(
        id="ring-tail",
        atoms=("C",) * 5,
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (1.5, 1.5, 0.0),
            (0.0, 1.5, 0.0),
            (0.0, 3.0, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )


def _run(records: list[StructureRecord], native: dict[str, Any], tmp_path) -> Any:
    item = _item("c1:g1", "c1", records)
    # The wire the parser hands the executor: schema defaults (limits) included.
    wire = ConfgenModelV3.model_validate(
        {"schema_version": 3, "index_base": 1, **native}
    ).scientific_native()
    sci = _sci(seed=None, native=FrozenDict(wire))
    return ConfgenExecutor().execute(item, _ctx(sci, str(tmp_path)))


def _message(result: Any) -> str:
    return " ".join(d.message for d in result.diagnostics)


def _report(tmp_path) -> dict[str, Any]:
    for root, _, files in os.walk(str(tmp_path)):
        if "ensemble_report.json" in files:
            with open(os.path.join(root, "ensemble_report.json"), encoding="utf-8") as fh:
                return json.load(fh)
    raise AssertionError("ensemble_report.json not written")


@pytest.fixture
def geometry_spy(monkeypatch):
    """Fail the test if any torsion realization (geometry) runs."""
    calls = {"n": 0}
    original = TorsionStage.realize

    def _spy(self, *args, **kwargs):
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(TorsionStage, "realize", _spy)
    return calls


def test_ring_refusal_never_reaches_geometry(tmp_path, geometry_spy):
    result = _run(
        [_ring_tail()],
        {"paths": [{"start": 1, "end": 3, "move": "end", "angles": [0.0, 90.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.FAILED
    assert PATH_CROSSES_RING in _message(result)
    assert geometry_spy["n"] == 0


def test_pregeometry_limit_refuses_before_geometry(tmp_path, geometry_spy):
    # 24 angles on each of the 3 interior bonds of 2-5: 24**3 = 13824 > 10000.
    result = _run(
        [_hexane()],
        {
            "paths": [{"start": 2, "end": 5, "move": "end", "step": 15}],
            # Raise the output cap so only the declared-states guard can refuse.
            "limits": {"max_declared_states": 10000, "max_output_structures": 100000},
        },
        tmp_path,
    )
    assert result.status is WorkItemStatus.FAILED
    assert "13824" in _message(result) and "max_declared_states" in _message(result)
    assert geometry_spy["n"] == 0


def _short_interior_bond() -> StructureRecord:
    return StructureRecord(
        id="hex-short",
        atoms=("C",) * 6,
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.4, 0.0),
            (3.0, 0.0, 0.0),
            (4.34, 0.4, 0.0),  # interior bond 3-4 compressed to 1.34 A
            (5.84, 0.0, 0.0),
            (7.34, 0.4, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )


def test_short_bond_warns_and_strict_mode_refuses(tmp_path):
    paths = [{"start": 2, "end": 5, "move": "end", "angles": [0.0]}]
    warned = _run([_short_interior_bond()], {"paths": paths}, tmp_path / "warn")
    assert warned.status is WorkItemStatus.COMPLETED
    assert any(d.code == "warning_short_bond" for d in warned.diagnostics)
    strict = _run(
        [_short_interior_bond()],
        {"paths": paths, "strict_path_bond_check": True},
        tmp_path / "strict",
    )
    assert strict.status is WorkItemStatus.FAILED
    assert PATH_SHORT_BOND in _message(strict)


def test_atom_ordering_is_preserved(tmp_path):
    record = _hexane()
    result = _run(
        [record],
        {"paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0, 180.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 8
    for member in result.structures:
        assert tuple(member.atoms) == tuple(record.atoms)


def test_resolution_follows_the_driving_geometry(tmp_path):
    paths = {"paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0, 180.0]}]}
    near = _hexane("near")
    far = StructureRecord(
        id="upstream-product",
        atoms=near.atoms,
        coordinates=_hexane("far", stretch_bond=(2, 8.0)).coordinates,  # bond 3-4 breaks
        charge=0,
        multiplicity=1,
        parent_ids=("seed-a",),
    )
    assert _run([near], paths, tmp_path / "near").status is WorkItemStatus.COMPLETED
    moved = _run([far], paths, tmp_path / "far")
    assert moved.status is WorkItemStatus.FAILED
    assert PATH_DISCONNECTED in _message(moved)


def test_upstream_driving_geometry_is_audited_by_its_own_identity(tmp_path):
    record = StructureRecord(
        id="upstream-product-7",
        atoms=("C",) * 6,
        coordinates=tuple((x * 1.52, 0.4 * (x % 2), 0.0) for x in range(6)),
        charge=0,
        multiplicity=1,
        parent_ids=("seed-a",),
    )
    result = _run(
        [record],
        {"paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0, 120.0]}]},
        tmp_path,
    )
    assert result.status is WorkItemStatus.COMPLETED
    payload = _report(tmp_path)
    assert payload["driving_id"] == "upstream-product-7"
    assert payload["path_resolution"]["driving_geometry_digest"] == record.geometry_digest


def test_explicit_zero_grid_is_one_valid_state(tmp_path):
    record = _hexane()
    result = _run(
        [record], {"paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0]}]}, tmp_path
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 1
    assert result.structures[0].ordinal == 0
    assert _report(tmp_path)["path_resolution"]["raw_cartesian_size"] == 1


def test_explicit_single_nonzero_grid_rotates_once(tmp_path):
    record = _hexane()
    result = _run(
        [record], {"paths": [{"start": 2, "end": 5, "move": "end", "angles": [90.0]}]}, tmp_path
    )
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 1
    assert tuple(map(tuple, result.structures[0].coordinates)) != tuple(
        map(tuple, record.coordinates)
    )


def test_strict_flag_must_be_a_boolean():
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0]}],
                "strict_path_bond_check": 1,
            }
        )


def test_unknown_path_key_is_rejected():
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0], "via": [3]}],
            }
        )


def test_preview_matches_the_runtime_persisted_graph_with_a_nondefault_bond_scale():
    """Same working graph, rotors, moving sets, size AND digest (typed preview)."""
    from confflow.application.v4_run import _apply_declared_input_topology
    from confflow.domain.topology import TopologyPatch
    from confflow.producer.path_preview import preview_paths

    hydrogen = json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))["hydrogen_cases"][0]
    record_data = hydrogen["record"]
    structure = {
        "atoms": list(record_data["atoms"]),
        "coordinates": [list(point) for point in record_data["coordinates"]],
        "topology": {"add": [[1, 2]]},
    }
    typed = {
        "schema_version": 3,
        "index_base": 1,
        "tolerances": {"bond_scale": 1.1},
        "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0, 240.0]}],
    }
    result = preview_paths(structure, typed)
    assert result["raw_conformers"] == 27
    assert result["resolved_rotors"] == 3

    record = StructureRecord(
        id="repro",
        atoms=tuple(structure["atoms"]),
        coordinates=tuple(tuple(point) for point in structure["coordinates"]),
        topology_patch=TopologyPatch(add_edges=[[1, 2]], delete_edges=(), provenance=""),
    )
    driving = list(_apply_declared_input_topology("repro", [record], None))[0]
    context = build_context(driving, dict(typed))
    resolved = context.resolved_spec["paths_resolved"]
    # The preview digest is the runtime digest, verbatim: no correction
    # metadata (bond_scale/add_bond/del_bond) is folded in on either side.
    assert result["topology_digest"] == resolved["topology_digest"]
    assert resolved["raw_cartesian_size"] == 27
    assert [entry["bond"] for entry in result["rotors"]] == [
        list(entry["bond"]) for entry in resolved["rotors"]
    ]
    assert [entry["moving_atoms"] for entry in result["rotors"]] == [
        list(entry["moving"]) for entry in resolved["rotors"]
    ]


def _preview_record(structure: dict[str, Any]) -> StructureRecord:
    from confflow.domain.topology import TopologyPatch

    patch = None
    if structure.get("topology") is not None:
        patch = TopologyPatch(
            add_edges=structure["topology"].get("add", ()),
            delete_edges=structure["topology"].get("delete", ()),
            provenance=structure["topology"].get("provenance", ""),
        )
    return StructureRecord(
        id="preview",
        atoms=tuple(structure["atoms"]),
        coordinates=tuple(tuple(point) for point in structure["coordinates"]),
        topology_patch=patch,
    )


def _butane_structure() -> dict[str, Any]:
    case = json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))["hydrogen_cases"][0]
    return {
        "atoms": list(case["record"]["atoms"]),
        "coordinates": [list(point) for point in case["record"]["coordinates"]],
    }


def test_preview_digest_tracks_every_runtime_graph_authority():
    """Patch, typed topology, add/del edges and metadata neutrality agree."""
    from confflow.producer.path_preview import preview_paths

    base = _butane_structure()
    backbone = [[1, 2], [2, 3], [3, 4]]
    scenarios = {
        "default_scale": (
            base,
            {
                "schema_version": 3,
                "index_base": 1,
                "paths": [{"start": 1, "end": 2, "move": "end", "angles": [0.0]}],
            },
        ),
        "nondefault_scale": (
            base,
            {
                "schema_version": 3,
                "index_base": 1,
                "tolerances": {"bond_scale": 1.1},
                "paths": [{"start": 1, "end": 2, "move": "end", "angles": [0.0]}],
            },
        ),
        "typed_topology": (
            base,
            {
                "schema_version": 3,
                "index_base": 1,
                "topology": {"bonds": backbone},
                "paths": [{"start": 2, "end": 3, "move": "end", "angles": [0.0]}],
            },
        ),
        "del_edge": (
            base,
            {
                "schema_version": 3,
                "index_base": 1,
                "topology": {"del_bond": [[2, 3]]},
                "paths": [{"start": 1, "end": 2, "move": "end", "angles": [0.0]}],
            },
        ),
        "add_edge": (
            base,
            {
                "schema_version": 3,
                "index_base": 1,
                "topology": {"add_bond": [[5, 6]]},
                "paths": [{"start": 1, "end": 2, "move": "end", "angles": [0.0]}],
            },
        ),
    }
    digests = {}
    for name, (structure, native) in scenarios.items():
        result = preview_paths(structure, native)
        context = build_context(_preview_record(structure), dict(native))
        resolved = context.resolved_spec["paths_resolved"]
        assert result["topology_digest"] == resolved["topology_digest"], name
        assert result["raw_conformers"] == resolved["raw_cartesian_size"], name
        digests[name] = result["topology_digest"]
    # The same graph under different spellings/metadata digests identically.
    assert digests["default_scale"] == digests["nondefault_scale"]
    # Atom labels/roles never enter the digest (same edges, extra metadata).
    labelled = {
        "schema_version": 3,
        "index_base": 1,
        "topology": {
            "bonds": [
                {"atoms": pair, "kind": "COVALENT", "provenance": "declared"} for pair in backbone
            ],
            "atoms": [{"index": 1, "label": "C1", "role": "anchor"}],
        },
        "paths": [{"start": 2, "end": 3, "move": "end", "angles": [0.0]}],
    }
    labelled_result = preview_paths(base, labelled)
    assert labelled_result["topology_digest"] == digests["typed_topology"]


def test_preview_refuses_structured_and_typed_dual_authority():
    """A structure patch plus typed topology corrections fails closed."""
    from confflow.producer.path_preview import preview_paths

    structure = _butane_structure()
    structure["topology"] = {"add": []}
    # An empty patch is no authority; the typed add_bond then applies alone.
    result = preview_paths(
        structure,
        {
            "schema_version": 3,
            "index_base": 1,
            "topology": {"del_bond": [[2, 3]]},
            "paths": [{"start": 1, "end": 2, "move": "end", "angles": [0.0]}],
        },
    )
    assert result["raw_conformers"] == 1
    # A real patch plus a typed correction is a dual-authority conflict.
    structure["topology"] = {"add": [[1, 2]]}
    with pytest.raises(ValueError, match="authority|conflict"):
        preview_paths(
            structure,
            {
                "schema_version": 3,
                "index_base": 1,
                "topology": {"del_bond": [[2, 3]]},
                "paths": [{"start": 1, "end": 2, "move": "end", "angles": [0.0]}],
            },
        )
