"""Public read-only estimates resolve physical rotors before multiplying.

The preview takes the typed v3 confgen scope (``ConfgenModelV3``) and
reuses the runtime context build, so the reported topology digest, rotors
and deduped raw count are the runtime's own.  Positive cases ride the
fixed full-hydrogen butane golden geometry; a bare carbon chain has no
measurable dihedral frame and is refused like the runtime refuses it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from confflow.producer.authoring import dispatch_request
from confflow.producer.path_preview import preview_paths

GOLDEN = Path(__file__).resolve().parent.parent / "fixtures" / "paths_equivalence"

#: 1-4 along the butane backbone: three bridge bonds, explicit 3-angle grid.
PATH_1_4 = {"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0, 240.0]}


def _butane() -> dict[str, Any]:
    """Return the fixed full-hydrogen butane geometry (golden hydrogen case)."""
    case = json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))["hydrogen_cases"][0]
    record = case["record"]
    return {
        "id": "preview",
        "atoms": list(record["atoms"]),
        "coordinates": [list(point) for point in record["coordinates"]],
    }


def _short_middle_butane() -> dict[str, Any]:
    """Return the butane geometry with backbone bond 2-3 compressed to 0.90 A.

    Atoms carried by C3 or C4 (and C3/C4 themselves) translate rigidly along
    the 2-3 axis, so only the middle bond lands below the short-bond ratio
    while the rest of the graph keeps its original distances.
    """
    import math

    structure = _butane()
    atoms = structure["atoms"]
    coords = [list(point) for point in structure["coordinates"]]
    length = math.dist(coords[1], coords[2])
    axis = [(coords[2][i] - coords[1][i]) / length for i in range(3)]
    delta = [(0.90 - length) * axis[i] for i in range(3)]

    def nearest_carbon(index: int) -> int:
        return min(range(4), key=lambda carbon: math.dist(coords[index], coords[carbon]))

    moving = {2, 3} | {
        index for index, atom in enumerate(atoms) if atom == "H" and nearest_carbon(index) in (2, 3)
    }
    for index in moving:
        coords[index] = [coords[index][i] + delta[i] for i in range(3)]
    structure["coordinates"] = coords
    return structure


@pytest.fixture
def geometry_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if the preview ever touches geometry or the engine."""
    from confflow.science.confgen.engine import ConfgenEngine
    from confflow.science.confgen.torsion.stage import TorsionStage

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the preview must not generate geometry or run the engine")

    monkeypatch.setattr(TorsionStage, "realize", _boom)
    monkeypatch.setattr(ConfgenEngine, "run", _boom)


def test_preview_counts_each_resolved_bond_and_deduplicates(geometry_guard: None) -> None:
    result = preview_paths(_butane(), {"paths": [PATH_1_4] * 2})
    assert result["resolved_rotors"] == 3
    assert result["raw_conformers"] == 27
    assert result["paths"][0]["chain"] == [1, 2, 3, 4]
    assert result["paths"][0]["move"] == "end"
    assert result["paths"][0]["source"] == "$.paths[0]"
    assert not result["exceeds_limit"]
    assert result["hard_limit"] == 10000
    assert result["topology_digest"].startswith("sha256:")
    assert [rotor["bond"] for rotor in result["rotors"]] == [[1, 2], [2, 3], [3, 4]]
    assert result["rotors"][0]["moving_atoms"]


def test_preview_reports_oversize_and_explicit_limits_purely(geometry_guard: None) -> None:
    # 24 angles on each of the three backbone bonds: 24**3 = 13824.
    result = preview_paths(
        _butane(), {"paths": [{"start": 1, "end": 4, "move": "end", "step": 15}]}
    )
    assert result["raw_conformers"] == 24**3
    assert result["exceeds_limit"]
    assert result["hard_limit"] == 10000
    # An explicit limit moves the guard; the output cap is not the hard limit.
    raised = preview_paths(
        _butane(),
        {
            "paths": [{"start": 1, "end": 4, "move": "end", "step": 15}],
            "limits": {"max_declared_states": 20000, "max_output_structures": 5},
        },
    )
    assert raised["raw_conformers"] == 24**3
    assert not raised["exceeds_limit"]
    assert raised["hard_limit"] == 20000


def test_preview_refuses_active_advanced_scopes(geometry_guard: None) -> None:
    torsion = {
        "id": "t1",
        "bond": [1, 2],
        "model": "relative_rotation_grid",
        "angles": [0.0, 120.0],
        "treatment": "enumerate",
    }
    with pytest.raises(ValueError, match="advanced scopes"):
        preview_paths(_butane(), {"paths": [PATH_1_4], "torsions": [torsion]})
    with pytest.raises(ValueError, match="advanced scopes"):
        preview_paths(
            _butane(), {"paths": [PATH_1_4], "rings": [{"id": "r1", "atoms": [1, 2, 3, 4]}]}
        )
    with pytest.raises(ValueError, match="advanced scopes"):
        preview_paths(_butane(), {"paths": [PATH_1_4], "sampling": {"cap": 2}, "seed": 1})
    with pytest.raises(ValueError, match="advanced scopes"):
        preview_paths(
            _butane(),
            {
                "paths": [PATH_1_4],
                "exclusions": [{"axis": "torsions", "match": {"id": "t1"}, "reason": "policy"}],
            },
        )


def test_preview_rejects_legacy_values_and_undeclared_sampling(geometry_guard: None) -> None:
    with pytest.raises(ValueError, match="angle_step"):
        preview_paths(
            _butane(), {"paths": [{"start": 1, "end": 4, "move": "end"}], "angle_step": 120}
        )
    # Typed scopes fail closed without explicit per-path sampling.
    with pytest.raises(ValueError, match="paths rejected"):
        preview_paths(_butane(), {"paths": [{"start": 1, "end": 4, "move": "end"}]})


def test_preview_index_base_zero_is_equivalent(geometry_guard: None) -> None:
    base_one = preview_paths(_butane(), {"index_base": 1, "paths": [PATH_1_4]})
    base_zero = preview_paths(_butane(), {"index_base": 0, "paths": [PATH_1_4]})
    assert base_zero == base_one


def test_preview_honors_structure_topology_patch(geometry_guard: None) -> None:
    structure = _butane()
    structure["topology"] = {"delete": [[2, 3]]}
    with pytest.raises(ValueError, match="PATH_DISCONNECTED"):
        preview_paths(structure, {"paths": [PATH_1_4]})


def test_preview_honors_typed_strict_bond_check() -> None:
    from confflow.science.confgen.torsion.paths import PATH_SHORT_BOND

    structure = _short_middle_butane()
    loose = preview_paths(structure, {"paths": [PATH_1_4]})
    assert any("WARNING_SHORT_BOND" in warning for warning in loose["warnings"])
    with pytest.raises(ValueError, match=PATH_SHORT_BOND):
        preview_paths(structure, {"paths": [PATH_1_4], "strict_path_bond_check": True})


def test_preview_public_boundary_returns_resolved_count() -> None:
    response = dispatch_request(
        json.dumps(
            {
                "content_schema": "confflow.authoring.v4",
                "operation": "preview_paths",
                "parameters": {
                    "structure": _butane(),
                    "native": {"schema_version": 3, "index_base": 1, "paths": [PATH_1_4]},
                },
            }
        )
    )
    assert response["ok"]
    assert response["result"]["raw_conformers"] == 27


def test_preview_plain_and_empty_patch_retain_typed_scale() -> None:
    plain = _butane()
    empty_patch = _butane()
    empty_patch["topology"] = {"add": []}
    shrunk = {"tolerances": {"bond_scale": 0.9}}
    for structure in (plain, empty_patch):
        with pytest.raises(ValueError, match="PATH_DISCONNECTED"):
            preview_paths(structure, {"paths": [PATH_1_4], **shrunk})
        resolved = preview_paths(structure, {"paths": [PATH_1_4]})
        assert resolved["raw_conformers"] == 27


def test_preview_terminal_hydrogen_gets_the_runtime_diagnostic() -> None:
    """Atom 5 is a terminal hydrogen; the refusal names key, atom and bond."""
    with pytest.raises(ValueError) as excinfo:
        preview_paths(
            _butane(),
            {"paths": [{"start": 5, "end": 4, "move": "end", "angles": [0.0, 90.0]}]},
        )
    message = str(excinfo.value)
    assert "confgen.paths[0].start (atom 5)" in message
    assert "is a terminal atom with no measurable dihedral frame" in message
    assert "bond 5-1" in message


def test_preview_matches_the_runtime_context_digest() -> None:
    """The preview digest is the runtime context's digest, verbatim."""
    from confflow.domain import StructureRecord
    from confflow.science.confgen.model import build_context

    structure = _butane()
    native: dict[str, Any] = {
        "schema_version": 3,
        "index_base": 1,
        "tolerances": {"bond_scale": 1.1},
        "paths": [PATH_1_4],
    }
    result = preview_paths(structure, native)
    record = StructureRecord(
        id="preview",
        atoms=tuple(structure["atoms"]),
        coordinates=tuple(tuple(point) for point in structure["coordinates"]),
    )
    context = build_context(record, dict(native))
    resolved = context.resolved_spec["paths_resolved"]
    assert result["topology_digest"] == resolved["topology_digest"]
    assert result["raw_conformers"] == resolved["raw_cartesian_size"]
    assert [rotor["bond"] for rotor in result["rotors"]] == [
        list(rotor["bond"]) for rotor in resolved["rotors"]
    ]
    assert [rotor["moving_atoms"] for rotor in result["rotors"]] == [
        list(rotor["moving"]) for rotor in resolved["rotors"]
    ]
