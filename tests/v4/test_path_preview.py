"""Public read-only estimates resolve physical rotors before multiplying."""

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
