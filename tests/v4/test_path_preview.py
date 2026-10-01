"""Public read-only estimates resolve physical rotors before multiplying."""

import copy
import json

import pytest

from confflow.producer.authoring import dispatch_request
from confflow.producer.path_preview import preview_paths


def _chain(n_atoms=5):
    return {"atoms": ["C"] * n_atoms, "coordinates": [[i * 1.52, 0, 0] for i in range(n_atoms)]}


def test_preview_counts_each_resolved_bond_and_deduplicates():
    native = {"paths": [{"start": 1, "end": 5, "move": "end"}] * 2, "angle_step": 120}
    result = preview_paths(_chain(), native)
    assert result["resolved_rotors"] == 4
    assert result["raw_conformers"] == 81
    assert result["paths"][0]["chain"] == [1, 2, 3, 4, 5]
    assert not result["exceeds_limit"]


def test_preview_reports_oversize_without_allocating_geometry():
    result = preview_paths(
        _chain(10), {"paths": [{"start": 1, "end": 10, "move": "end"}], "angle_step": 30}
    )
    assert result["raw_conformers"] == 12**9
    assert result["exceeds_limit"]


def test_preview_refuses_incomplete_advanced_estimate():
    with pytest.raises(ValueError, match="advanced scopes"):
        preview_paths(_chain(), {"paths": [], "torsions": []})


def test_preview_honors_structure_topology_patch():
    structure = _chain()
    structure["topology"] = {"delete": [[2, 3]]}
    with pytest.raises(ValueError, match="PATH_DISCONNECTED"):
        preview_paths(structure, {"paths": [{"start": 1, "end": 5, "move": "end"}]})


def test_preview_public_boundary_returns_resolved_count():
    response = dispatch_request(
        json.dumps(
            {
                "content_schema": "confflow.authoring.v4",
                "operation": "preview_paths",
                "parameters": {
                    "structure": _chain(),
                    "native": {
                        "paths": [{"start": 1, "end": 5, "move": "end"}],
                        "angle_step": 120,
                    },
                },
            }
        )
    )
    assert response["ok"]
    assert response["result"]["raw_conformers"] == 81


def _stretched_repro():
    structure = {
        "atoms": ["C"] * 4,
        "coordinates": [[i * 1.7, 0, 0] for i in range(4)],
        "topology": {"add": [[1, 2]]},
    }
    native = {
        "paths": [{"start": 1, "end": 4, "move": "end"}],
        "angle_step": 120,
        "bond_scale": 1.1,
    }
    return structure, native


def test_preview_matches_runtime_persisted_graph_with_nondefault_bond_scale():
    from confflow.application.v4_run import _apply_declared_input_topology
    from confflow.domain.structure import StructureRecord
    from confflow.domain.topology import TopologyPatch
    from confflow.execution.confgen_executor import ConfgenExecutor

    structure, native = _stretched_repro()
    before_structure = copy.deepcopy(structure)
    before_native = copy.deepcopy(native)
    result = preview_paths(structure, native)
    assert structure == before_structure
    assert native == before_native
    assert result["raw_conformers"] == 27
    assert result["resolved_rotors"] == 3
    assert result["paths"][0]["chain"] == [1, 2, 3, 4]
    assert result["paths"][0]["move"] == "end"

    record = StructureRecord(
        id="repro",
        atoms=tuple(structure["atoms"]),
        coordinates=tuple(tuple(point) for point in structure["coordinates"]),
        topology_patch=TopologyPatch(add_edges=[[1, 2]], delete_edges=(), provenance=""),
    )
    persisted = _apply_declared_input_topology("repro", [record], None)
    driving = list(persisted)[0]
    expected_graph = [[1], [0, 2], [1, 3], [2]]
    assert [list(row) for row in driving.working_topology] == expected_graph

    prep = ConfgenExecutor()._prepare_path_rotors(
        driving=driving,
        native=dict(native),
        chains=[],
        angle_step=120,
        rotate_side="left",
        chain_steps_raw=None,
        chain_angles_raw=None,
        no_rotate=set(),
        add_bond=[],
        del_bond=[],
        bond_scale=1.1,
        strict_path_bond_check=False,
    )
    assert [list(row) for row in prep["adjacency"]] == expected_graph
    assert result["topology_digest"] == prep["resolution"].topology_digest
    assert result["raw_conformers"] == prep["canonical_size"] == 27
    assert prep["resolution"].raw_cartesian_size == 27
    runtime_rotors = list(prep["rotors"])
    assert [entry["bond"] for entry in result["rotors"]] == [
        [first + 1, second + 1] for rotor in runtime_rotors for first, second in [rotor.bond]
    ]
    assert [entry["moving_atoms"] for entry in result["rotors"]] == [
        [atom + 1 for atom in rotor.moving] for rotor in runtime_rotors
    ]
    assert [entry["angles"] for entry in result["rotors"]] == [
        list(rotor.angles) for rotor in runtime_rotors
    ]


def test_preview_plain_and_empty_patch_retain_native_scale():
    natives = {
        "paths": [{"start": 1, "end": 4, "move": "end"}],
        "angle_step": 120,
        "bond_scale": 1.1,
    }
    plain = {
        "atoms": ["C"] * 4,
        "coordinates": [[i * 1.7, 0, 0] for i in range(4)],
    }
    empty_patch = {
        "atoms": ["C"] * 4,
        "coordinates": [[i * 1.7, 0, 0] for i in range(4)],
        "topology": {"add": []},
    }
    with pytest.raises(ValueError, match="PATH_DISCONNECTED"):
        preview_paths(plain, dict(natives))
    with pytest.raises(ValueError, match="PATH_DISCONNECTED"):
        preview_paths(empty_patch, dict(natives))
