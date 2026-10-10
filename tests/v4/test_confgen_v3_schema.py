"""Typed v3 scope rejects ambiguous input before geometry generation."""

import json

import pytest
from pydantic import ValidationError

from confflow.science.confgen.planner import normalize_spec
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from confflow.workflow.v4.schema import StepModel


def declaration():
    return {
        "schema_version": 3,
        "torsions": [
            {
                "id": "T1",
                "atoms": [1, 2, 3, 4],
                "model": "absolute_dihedral_grid",
                "angles": [60, 180, 300],
            }
        ],
    }


def test_typed_scope_normalizes_all_indices_once():
    model = ConfgenModelV3.model_validate(declaration())
    once = normalize_spec(model.scientific_native())
    assert once["torsions"][0]["atoms"] == [0, 1, 2, 3]
    assert normalize_spec(once) == once
    assert "overrides" not in once


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 3.0},
        {"index_base": True},
        {"seed": True},
        {"sampling": {"cap": True}},
        {"sampling": {"cap": 4, "seed": 1}},
        {"limits": {"max_declared_states": 0}},
        {"tolerances": {"clash_threshold": float("nan")}},
        {"rings": [{"id": "r1", "atoms": [1, 2, 3, 4], "hidden_option": 1}]},
        {"native": {"chains": ["1-2-3-4"]}},
    ],
)
def test_unknown_or_ambiguous_scope_rejected(changes):
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate({**declaration(), **changes})


def test_step_accepts_typed_definitions_and_rejects_legacy_native():
    with pytest.raises(ValidationError):
        StepModel.model_validate(
            {"id": "c", "executor": "confgen", "confgen": {"native": {"chains": ["1-2"]}}}
        )
    typed = StepModel.model_validate({"id": "c", "executor": "confgen", "confgen": declaration()})
    assert isinstance(typed.confgen, ConfgenModelV3)


def test_sampling_uses_step_seed_only():
    model = ConfgenModelV3.model_validate({**declaration(), "seed": 7, "sampling": {"cap": 2}})
    normalized = normalize_spec(model.scientific_native())
    assert normalized["seed"] == 7
    assert normalized["sampling"]["cap"] == 2


def test_topology_add_bond_accepts_typed_forming():
    model = ConfgenModelV3.model_validate(
        {
            "schema_version": 3,
            "index_base": 1,
            "topology": {"add_bond": [[1, 2], {"atoms": [2, 3], "kind": "FORMING"}]},
        }
    )
    assert model.topology is not None and model.topology.add_bond is not None
    assert model.topology.add_bond[0] == (1, 2)
    assert model.topology.add_bond[1].kind == "FORMING"
    wire = model.scientific_native()["topology"]
    assert wire["add_bond"][0] == [1, 2]
    assert wire["add_bond"][1] == {"atoms": [2, 3], "kind": "FORMING", "provenance": "explicit"}
    for bad in (
        {"atoms": [1, 2], "kind": "forming"},
        {"atoms": [1, 2, 3], "kind": "FORMING"},
        {"kind": "FORMING"},
        {"atoms": [1, 2], "kind": "FORMING", "bogus": 1},
        [[1]],
        ["1-2"],
    ):
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {"schema_version": 3, "index_base": 1, "topology": {"add_bond": [bad]}}
            )
    # del_bond stays plain pairs only: typed entries are refused there.
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "index_base": 1,
                "topology": {"del_bond": [{"atoms": [1, 2], "kind": "FORMING"}]},
            }
        )


#: Canonical topology wire for a plain-pair add_bond, pinned from HEAD code
#: before typed entries were accepted (the typed form must not disturb it).
_PLAIN_ADD_BOND_WIRE = '{"add_bond": [[1, 2]], "atoms": []}'


def test_plain_pair_add_bond_wire_stable():
    model = ConfgenModelV3.model_validate(
        {"schema_version": 3, "index_base": 1, "topology": {"add_bond": [[1, 2]]}}
    )
    assert json.dumps(model.scientific_native()["topology"], sort_keys=True) == (
        _PLAIN_ADD_BOND_WIRE
    )
