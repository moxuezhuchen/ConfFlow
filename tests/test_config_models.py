#!/usr/bin/env python3

"""Tests for the typed workflow configuration model."""

from __future__ import annotations

import pytest

from confflow.calc.artifacts import compute_config_digest
from confflow.config.models import (
    CalcStepParams,
    GlobalOptions,
    WorkflowConfig,
    load_workflow_model,
)


def test_load_workflow_model_coerces_global_and_steps(tmp_path):
    cfg = tmp_path / "workflow.yaml"
    cfg.write_text(
        "global:\n"
        "  iprog: gaussian\n"
        "  itask: opt\n"
        "  keyword: B3LYP/6-31G(d)\n"
        "  cores_per_task: '4'\n"
        "  max_parallel_jobs: '2'\n"
        "  freeze: '1-3,5'\n"
        "  ts_bond_atoms: '1,2'\n"
        "  allowed_executables: g16,orca\n"
        "steps:\n"
        "  - name: optimize\n"
        "    type: calc\n"
        "    params:\n"
        "      itask: ts\n"
        "      clean_params: '-t 0.4 -ewin 5 --energy-tolerance 0.02'\n",
        encoding="utf-8",
    )

    model = load_workflow_model(str(cfg))
    assert isinstance(model, WorkflowConfig)
    assert model.global_options.iprog == "g16"
    assert model.global_options.cores_per_task == 4
    assert model.global_options.freeze == (1, 2, 3, 5)
    assert model.global_options.ts_bond_atoms == (1, 2)
    assert model.global_options.allowed_executables == ("g16", "orca")

    calc = CalcStepParams.from_params(model.steps[0].params, model.global_options)
    assert calc.program == "g16"
    assert calc.task == "ts"
    assert calc.keyword == "B3LYP/6-31G(d)"
    assert calc.cleanup.enabled is True
    assert calc.cleanup.rmsd_threshold == 0.4
    assert calc.cleanup.energy_window == 5.0
    assert calc.cleanup.energy_tolerance == 0.02
    assert calc.ts.bond_atoms == (1, 2)


def test_calc_step_params_runtime_dict_is_canonical_and_runner_friendly():
    global_options = GlobalOptions.from_mapping(
        {
            "iprog": "orca",
            "itask": "opt_freq",
            "keyword": "HF def2-SVP",
            "total_memory": "8GB",
            "auto_clean": "false",
            "allowed_executables": ["orca"],
            "sandbox_root": "/tmp/sandbox",
        }
    )
    params = CalcStepParams.from_params(
        {
            "itask": "sp",
            "charge": "-1",
            "multiplicity": "2",
            "blocks": {"pal": "nprocs 2 end"},
        },
        global_options,
        input_chk_dir="/tmp/chk",
    )

    runtime = params.to_runtime_dict()
    assert runtime["iprog"] == "orca"
    assert runtime["itask"] == "sp"
    assert runtime["keyword"] == "HF def2-SVP"
    assert runtime["charge"] == -1
    assert runtime["multiplicity"] == 2
    assert runtime["auto_clean"] is False
    assert runtime["input_chk_dir"] == "/tmp/chk"
    assert runtime["allowed_executables"] == ["orca"]
    assert "%pal" in runtime["blocks"]
    assert "backup_dir" not in params.canonical_dict()


def test_calc_step_params_rejects_invalid_program_task_and_memory():
    global_options = GlobalOptions.from_mapping({"keyword": "HF"})

    with pytest.raises(ValueError, match="Unsupported calc program"):
        CalcStepParams.from_params({"iprog": "bad", "keyword": "HF"}, global_options)

    with pytest.raises(ValueError, match="Unsupported calc task"):
        CalcStepParams.from_params({"itask": "bad", "keyword": "HF"}, global_options)

    with pytest.raises(ValueError, match="total_memory"):
        GlobalOptions.from_mapping({"total_memory": "lots"})


def test_workflow_model_as_legacy_shape_keeps_engine_contract(tmp_path):
    cfg = tmp_path / "workflow.yaml"
    cfg.write_text(
        "global:\n"
        "  max_parallel_jobs: 3\n"
        "steps:\n"
        "  - name: calc1\n"
        "    type: calc\n"
        "    enabled: false\n"
        "    params:\n"
        "      keyword: HF\n",
        encoding="utf-8",
    )

    legacy = load_workflow_model(str(cfg)).as_legacy_shape()
    assert legacy["global"]["max_parallel_jobs"] == 3
    assert legacy["steps"][0]["name"] == "calc1"
    assert legacy["steps"][0]["enabled"] is False
    assert legacy["steps"][0]["params"]["keyword"] == "HF"


def test_workflow_model_rejects_step_types_not_executed_by_engine(tmp_path):
    cfg = tmp_path / "workflow.yaml"
    cfg.write_text(
        "global:\n"
        "  keyword: HF\n"
        "steps:\n"
        "  - name: unsupported\n"
        "    type: refine\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported type: 'refine'"):
        load_workflow_model(str(cfg))


def test_calc_step_params_honors_clean_opts_and_digest_changes():
    global_options = GlobalOptions.from_mapping({"keyword": "HF"})
    base = CalcStepParams.from_params(
        {"iprog": "orca", "itask": "sp", "clean_opts": "-t 0.4 -ewin 5"},
        global_options,
    )
    changed = CalcStepParams.from_params(
        {"iprog": "orca", "itask": "sp", "clean_opts": "-t 0.6 -ewin 5"},
        global_options,
    )

    assert base.cleanup.enabled is True
    assert base.cleanup.rmsd_threshold == 0.4
    assert base.cleanup.energy_window == 5.0
    assert base.canonical_dict()["rmsd_threshold"] == 0.4
    assert compute_config_digest(base) != compute_config_digest(changed)


def test_calc_step_digest_tracks_checkpoint_inheritance_and_gaussian_chk_write():
    global_options = GlobalOptions.from_mapping({"keyword": "HF", "iprog": "g16"})
    base = CalcStepParams.from_params({"itask": "sp"}, global_options)
    inherited = CalcStepParams.from_params(
        {"itask": "sp"},
        global_options,
        input_chk_dir="/tmp/previous_chks",
    )
    write_chk = CalcStepParams.from_params(
        {"itask": "sp", "gaussian_write_chk": True},
        global_options,
    )

    assert "input_chk_dir" in inherited.canonical_dict()
    assert "gaussian_write_chk" in write_chk.canonical_dict()
    assert compute_config_digest(base) != compute_config_digest(inherited)
    assert compute_config_digest(base) != compute_config_digest(write_chk)
