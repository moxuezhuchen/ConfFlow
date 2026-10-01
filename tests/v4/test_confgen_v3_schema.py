"""Typed v3 scope rejects ambiguous input before geometry generation."""

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


def test_step_routes_legacy_and_typed_definitions_separately():
    legacy = StepModel.model_validate(
        {"id": "c", "executor": "confgen", "confgen": {"native": {"chains": ["1-2"]}}}
    )
    typed = StepModel.model_validate({"id": "c", "executor": "confgen", "confgen": declaration()})
    assert not isinstance(legacy.confgen, ConfgenModelV3)
    assert isinstance(typed.confgen, ConfgenModelV3)


def test_sampling_uses_step_seed_only():
    model = ConfgenModelV3.model_validate({**declaration(), "seed": 7, "sampling": {"cap": 2}})
    normalized = normalize_spec(model.scientific_native())
    assert normalized["seed"] == 7
    assert normalized["sampling"]["cap"] == 2
