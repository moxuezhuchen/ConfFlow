"""Tests for the additive canonical raw-configuration boundary."""

from __future__ import annotations

import pytest

from confflow.config.canonical import (
    ConfigValidationError,
    load_raw_mapping,
    parse_workflow_mapping,
)


def test_load_raw_mapping_returns_owned_mapping(tmp_path):
    config_file = tmp_path / "workflow.yaml"
    config_file.write_text("global: {}\nsteps: []\n", encoding="utf-8")

    raw = load_raw_mapping(config_file)
    raw["global"]["keyword"] = "HF"

    assert load_raw_mapping(config_file) == {"global": {}, "steps": []}


def test_load_raw_mapping_normalizes_empty_yaml_and_rejects_non_mapping(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("- not-a-workflow\n", encoding="utf-8")

    assert load_raw_mapping(empty) == {}
    with pytest.raises(ConfigValidationError, match="root must be a mapping"):
        load_raw_mapping(invalid)


def test_load_raw_mapping_preserves_missing_file_and_wraps_yaml_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_raw_mapping(tmp_path / "missing.yaml")
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("global: [\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="Invalid YAML configuration"):
        load_raw_mapping(invalid)


def test_parse_workflow_mapping_keeps_typed_model_and_wraps_rule_errors():
    model = parse_workflow_mapping({"global": {}, "steps": []})
    assert model.steps == ()
    with pytest.raises(ConfigValidationError, match="unsupported type"):
        parse_workflow_mapping({"global": {}, "steps": [{"type": "other"}]})


def test_parse_workflow_mapping_rejects_non_mapping_global_and_params():
    with pytest.raises(ConfigValidationError) as global_error:
        parse_workflow_mapping({"global": [], "steps": []})
    assert global_error.value.issue.path == "global"

    with pytest.raises(ConfigValidationError) as params_error:
        parse_workflow_mapping({"global": {}, "steps": [{"type": "calc", "params": []}]})
    assert params_error.value.issue.path == "steps[1].params"
