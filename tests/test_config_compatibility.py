"""Compatibility contract for legacy and typed configuration entry points."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from confflow.calc.artifacts import compute_config_digest
from confflow.config.models import CalcStepParams, GlobalOptions, load_workflow_model
from confflow.core.exceptions import ConfigurationError
from confflow.core.models import CalcConfigModel, GlobalConfigModel

SHARED_GLOBAL_CASES = (
    {
        "cores_per_task": "4",
        "total_memory": "8GB",
        "max_parallel_jobs": "2",
        "charge": "-1",
        "multiplicity": "2",
        "freeze": "1-3,5",
        "ts_bond_atoms": "6,7",
    },
    {
        "cores_per_task": 1,
        "total_memory": "500MB",
        "max_parallel_jobs": 1,
        "freeze": [2, "4-5"],
        "ts_bond_atoms": [8, 9],
    },
)


@pytest.mark.parametrize("raw", SHARED_GLOBAL_CASES)
def test_legacy_and_typed_global_models_share_v2_wire_coercions(raw):
    legacy = GlobalConfigModel(**raw)
    typed = GlobalOptions.from_mapping(raw)

    assert typed.cores_per_task == legacy.cores_per_task
    assert typed.total_memory == legacy.total_memory
    assert typed.max_parallel_jobs == legacy.max_parallel_jobs
    assert typed.charge == legacy.charge
    assert typed.multiplicity == legacy.multiplicity
    assert typed.freeze == tuple(legacy.freeze)
    assert typed.ts_bond_atoms == (
        None if legacy.ts_bond_atoms is None else tuple(legacy.ts_bond_atoms)
    )


@pytest.mark.parametrize(
    "raw",
    (
        {"cores_per_task": 0},
        {"max_parallel_jobs": 0},
        {"multiplicity": 0},
        {"total_memory": "lots"},
    ),
)
def test_legacy_and_typed_global_models_reject_shared_invalid_values(raw):
    with pytest.raises((ValidationError, ValueError)):
        GlobalConfigModel(**raw)
    with pytest.raises(ValueError):
        GlobalOptions.from_mapping(raw)


def test_yaml_wire_and_mapping_factory_have_identical_typed_results(tmp_path):
    raw = {
        "global": dict(SHARED_GLOBAL_CASES[0], keyword="HF", iprog="orca"),
        "steps": [{"name": "sp", "type": "calc", "params": {"itask": "sp"}}],
    }
    config_path = tmp_path / "workflow.yaml"
    import yaml

    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    wire = load_workflow_model(config_path)
    direct = GlobalOptions.from_mapping(raw["global"])

    assert wire.global_options == direct
    assert wire.steps[0].params == raw["steps"][0]["params"]


@pytest.mark.parametrize(
    "global_raw",
    (
        {"cores_per_task": 0},
        {"max_parallel_jobs": 0},
        {"multiplicity": 0},
        {"total_memory": "lots"},
    ),
)
def test_yaml_wire_rejects_the_shared_invalid_global_corpus(tmp_path, global_raw):
    import yaml

    config_path = tmp_path / "invalid.yaml"
    config_path.write_text(yaml.safe_dump({"global": global_raw, "steps": []}), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_workflow_model(config_path)


@pytest.mark.parametrize(
    ("raw", "program", "task"),
    (
        ({"iprog": "orca", "itask": "sp", "keyword": "HF"}, "orca", "sp"),
        ({"iprog": "g16", "itask": "ts", "keyword": "B3LYP"}, "g16", "ts"),
        ({"iprog": 1, "itask": 0, "keyword": "B3LYP"}, "g16", "opt"),
    ),
)
def test_legacy_and_typed_calc_factories_accept_the_shared_v2_corpus(raw, program, task):
    legacy = CalcConfigModel(**raw)
    typed = CalcStepParams.from_params(raw, GlobalOptions.from_mapping({}))

    assert legacy.keyword == typed.keyword
    assert typed.program == program
    assert typed.task == task


@pytest.mark.parametrize(
    "raw",
    (
        {"iprog": "orca", "itask": "sp", "keyword": "HF", "cores_per_task": 0},
        {"iprog": "orca", "itask": "sp", "keyword": "HF", "max_parallel_jobs": 0},
        {"iprog": "orca", "itask": "sp", "keyword": "HF", "multiplicity": 0},
        {"iprog": "orca", "itask": "sp", "keyword": "HF", "total_memory": "lots"},
    ),
)
def test_legacy_and_typed_calc_factories_reject_the_shared_invalid_corpus(raw):
    with pytest.raises((ValidationError, ValueError)):
        CalcConfigModel(**raw)
    with pytest.raises(ValueError):
        CalcStepParams.from_params(raw, GlobalOptions.from_mapping({}))


def test_v2_documented_boundary_keeps_typed_ts_pair_tolerant():
    """Do not tighten this legacy/typed difference in a v2 maintenance change."""
    with pytest.raises(ValidationError, match="ts_bond_atoms"):
        GlobalConfigModel(ts_bond_atoms="1,2,3")

    typed = GlobalOptions.from_mapping({"ts_bond_atoms": "1,2,3"})
    assert typed.ts_bond_atoms is None


def test_v2_canonical_payload_and_digest_are_frozen():
    global_options = GlobalOptions.from_mapping(
        {
            "iprog": "gaussian",
            "itask": "opt",
            "keyword": "B3LYP/6-31G(d)",
            "cores_per_task": "4",
            "total_memory": "8GB",
            "max_parallel_jobs": "2",
            "freeze": "1-3,5",
            "auto_clean": "false",
        }
    )
    params = CalcStepParams.from_params(
        {"itask": "ts", "ts_bond_atoms": "1,2", "gaussian_write_chk": True},
        global_options,
        input_chk_dir="previous/chks",
    )
    canonical = params.canonical_dict()

    assert compute_config_digest(params) == (
        "sha256:80391d81276d58f95911f66aba1dc553153fba9b92cb11c30dafbe2dc5661a6f"
    )
    assert canonical == params.to_runtime_dict(include_runtime_paths=False)
    assert "sandbox_root" not in canonical
    assert "allowed_executables" not in canonical


def test_config_layer_does_not_import_legacy_core_model_internals():
    source = Path("confflow/config/models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("core.models"):
                forbidden.extend(alias.name for alias in node.names)
    assert forbidden == []


def test_legacy_calc_model_remains_a_public_v2_compatibility_entry_point():
    model = CalcConfigModel(iprog=1, itask=0, keyword="HF")
    assert model.model_dump()["iprog"] == 1


@pytest.mark.parametrize(
    "field",
    ("cores_per_task", "max_parallel_jobs", "multiplicity"),
)
def test_direct_global_options_construction_enforces_positive_integer_invariants(field):
    with pytest.raises(ValueError):
        GlobalOptions(**{field: 0})


def test_direct_global_options_construction_validates_memory_format():
    with pytest.raises(ValueError, match="total_memory"):
        GlobalOptions(total_memory="lots")


def test_direct_calc_step_params_construction_enforces_multiplicity():
    params = CalcStepParams.from_params(
        {"iprog": "orca", "itask": "sp", "keyword": "HF"},
        GlobalOptions.from_mapping({}),
    )
    with pytest.raises(ValueError, match="multiplicity"):
        replace(params, multiplicity=0)
