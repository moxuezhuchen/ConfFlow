#!/usr/bin/env python3

"""Tests for schema module (merged)."""

from __future__ import annotations

import pytest

from confflow.config.schema import ConfigSchema, merge_step_params


def test_schema_parse_freeze_string():
    assert ConfigSchema._parse_freeze_string("1,2,3") == [1, 2, 3]
    assert ConfigSchema._parse_freeze_string("1-3,5") == [1, 2, 3, 5]
    assert ConfigSchema._parse_freeze_string("") == []
    assert ConfigSchema._parse_freeze_string(None) == []


def test_normalize_global_config_basic():
    raw = {"cores_per_task": 4, "freeze": "1-3,5"}
    normalized = ConfigSchema.normalize_global_config(raw)
    assert normalized["cores_per_task"] == 4
    assert normalized["freeze"] == [1, 2, 3, 5]


def test_normalize_global_config_ts_bond_atoms():
    raw = {"ts_bond_atoms": [1, 2]}
    normalized = ConfigSchema.normalize_global_config(raw)
    assert normalized["ts_bond_atoms"] == [1, 2]


def test_schema_normalize_global_extended():
    raw = {"freeze": [1, 2]}
    norm = ConfigSchema.normalize_global_config(raw)
    assert norm["freeze"] == [1, 2]

    raw = {"ts_bond_atoms": [1, 2]}
    norm = ConfigSchema.normalize_global_config(raw)
    assert norm["ts_bond_atoms"] == [1, 2]

    raw = {"ts_bond_atoms": "1,2"}
    norm = ConfigSchema.normalize_global_config(raw)
    assert norm["ts_bond_atoms"] == [1, 2]


def test_normalize_step_config_overrides():
    global_cfg = {"cores_per_task": 1, "total_memory": "4GB"}
    step_cfg = {"params": {"cores_per_task": 2}}
    normalized = ConfigSchema.normalize_step_config(step_cfg, global_cfg)
    assert normalized["cores_per_task"] == 2
    assert normalized["total_memory"] == "4GB"


def test_normalize_step_config_no_overrides():
    global_cfg = {"cores_per_task": 1}
    step_cfg = {"params": {"other_param": "val"}}
    normalized = ConfigSchema.normalize_step_config(step_cfg, global_cfg)
    # other_param is not in STEP_OVERRIDES, so it should be ignored
    assert "other_param" not in normalized
    assert normalized["cores_per_task"] == 1


def test_schema_normalize_step_extended():
    global_cfg = {"cores_per_task": 4, "itask": "opt"}
    step_cfg = {"params": {"cores_per_task": 8}}
    norm = ConfigSchema.normalize_step_config(step_cfg, global_cfg)
    assert norm["cores_per_task"] == 8
    assert norm["itask"] == "opt"

    step_cfg = {"params": {"extra_param": "val"}}
    norm = ConfigSchema.normalize_step_config(step_cfg, global_cfg)
    # extra_param is not in STEP_OVERRIDES, so it should not appear
    assert "extra_param" not in norm


def test_schema_validate_calc_extended():
    with pytest.raises(ValueError, match="calc config missing required parameter"):
        ConfigSchema.validate_calc_config({"iprog": "orca"})

    with pytest.raises(ValueError, match="invalid iprog"):
        ConfigSchema.validate_calc_config({"iprog": "invalid", "itask": "opt", "keyword": "B3LYP"})

    with pytest.raises(ValueError, match="invalid itask"):
        ConfigSchema.validate_calc_config({"iprog": "orca", "itask": "invalid", "keyword": "B3LYP"})

    with pytest.raises(ValueError, match="cores_per_task must be an integer"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "cores_per_task": "abc"}
        )
    with pytest.raises(ValueError, match="cores_per_task must be >= 1"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "cores_per_task": 0}
        )

    with pytest.raises(ValueError, match="max_parallel_jobs must be an integer"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "max_parallel_jobs": "abc"}
        )
    with pytest.raises(ValueError, match="max_parallel_jobs must be >= 1"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "max_parallel_jobs": 0}
        )

    with pytest.raises(ValueError, match="charge must be an integer"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "charge": "abc"}
        )
    with pytest.raises(ValueError, match="multiplicity must be an integer"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "multiplicity": "abc"}
        )
    with pytest.raises(ValueError, match="multiplicity must be >= 1"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "multiplicity": 0}
        )

    with pytest.raises(ValueError, match="ts_bond_atoms format error"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "ts_bond_atoms": "1,2,3"}
        )
    with pytest.raises(ValueError, match="ts_bond_atoms must be two integers"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "ts_bond_atoms": "1,abc"}
        )
    with pytest.raises(ValueError, match="ts_bond_atoms must be two atom indices"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "ts_bond_atoms": [1, 2, 3]}
        )
    with pytest.raises(ValueError, match="ts_bond_atoms must be two integers"):
        ConfigSchema.validate_calc_config(
            {"iprog": "orca", "itask": "opt", "keyword": "B3LYP", "ts_bond_atoms": [1, "abc"]}
        )


def test_merge_step_params():
    global_cfg = {"a": 1}
    step_cfg = {"params": {"keyword": "B3LYP"}}
    res = merge_step_params(step_cfg, global_cfg)
    assert res["a"] == 1
    # keyword is in STEP_OVERRIDES, so it should be applied
    assert res["keyword"] == "B3LYP"

    # Non-override params should be ignored
    step_cfg2 = {"params": {"b": 2}}
    res2 = merge_step_params(step_cfg2, global_cfg)
    assert "b" not in res2


def test_loader_rejects_legacy_ts_bond_key(tmp_path):
    from confflow.config.loader import ConfigurationError, load_workflow_config_file

    cfg = tmp_path / "legacy.yaml"
    cfg.write_text(
        "global:\n"
        "  ts_bond: '1,2'\n"
        "steps:\n"
        "  - name: step1\n"
        "    type: calc\n"
        "    params:\n"
        "      iprog: orca\n"
        "      itask: opt\n"
        "      keyword: B3LYP\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="Legacy key 'ts_bond'"):
        load_workflow_config_file(str(cfg))
