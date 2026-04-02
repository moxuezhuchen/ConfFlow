#!/usr/bin/env python3

"""Tests for confflow.config.loader — load_workflow_config_file."""

from __future__ import annotations

import pytest

from confflow.config.loader import ConfigurationError, load_workflow_config_file


class TestLoadWorkflowConfigFile:
    """Tests for load_workflow_config_file edge cases."""

    def test_empty_path_raises(self):
        with pytest.raises(ConfigurationError, match="must not be empty"):
            load_workflow_config_file("")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_workflow_config_file(str(tmp_path / "nonexistent.yaml"))

    def test_directory_path_raises(self, tmp_path):
        with pytest.raises(ConfigurationError, match="not a file"):
            load_workflow_config_file(str(tmp_path))

    def test_invalid_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":\n  - [invalid: {yaml\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="YAML"):
            load_workflow_config_file(str(bad))

    def test_non_dict_root_raises(self, tmp_path):
        cfg = tmp_path / "list.yaml"
        cfg.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="root must be a dict"):
            load_workflow_config_file(str(cfg))

    def test_valid_config_returns_keys(self, tmp_path):
        cfg = tmp_path / "ok.yaml"
        cfg.write_text(
            "global:\n"
            "  charge: 0\n"
            "  multiplicity: 1\n"
            "steps:\n"
            "  - name: step1\n"
            "    type: calc\n"
            "    params:\n"
            "      iprog: gaussian\n"
            "      itask: opt\n"
            "      keyword: B3LYP\n",
            encoding="utf-8",
        )
        result = load_workflow_config_file(str(cfg))
        assert "global" in result
        assert "steps" in result
        assert "raw" in result
        assert len(result["steps"]) == 1

    def test_global_config_model_coerces_quoted_scalar_values(self, tmp_path):
        cfg = tmp_path / "typed.yaml"
        cfg.write_text(
            "global:\n"
            "  cores_per_task: '4'\n"
            "  max_parallel_jobs: '2'\n"
            "  enable_dynamic_resources: 'true'\n"
            "  resume_from_backups: 'false'\n"
            "  ts_bond_atoms: '1,2'\n"
            "steps:\n"
            "  - name: step1\n"
            "    type: calc\n"
            "    params:\n"
            "      iprog: gaussian\n"
            "      itask: opt\n"
            "      keyword: B3LYP\n",
            encoding="utf-8",
        )
        result = load_workflow_config_file(str(cfg))
        assert result["global"]["cores_per_task"] == 4
        assert result["global"]["max_parallel_jobs"] == 2
        assert result["global"]["enable_dynamic_resources"] is True
        assert result["global"]["resume_from_backups"] is False
        assert result["global"]["ts_bond_atoms"] == [1, 2]

    def test_global_config_model_validation_error_raises_configuration_error(self, tmp_path):
        cfg = tmp_path / "bad_global.yaml"
        cfg.write_text(
            "global:\n"
            "  total_memory: invalid\n"
            "steps:\n"
            "  - name: step1\n"
            "    type: calc\n"
            "    params:\n"
            "      iprog: gaussian\n"
            "      itask: opt\n"
            "      keyword: B3LYP\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="Global configuration model validation failed"):
            load_workflow_config_file(str(cfg))

    def test_step_missing_name_raises(self, tmp_path):
        cfg = tmp_path / "no_name.yaml"
        cfg.write_text(
            "global: {}\n"
            "steps:\n"
            "  - type: calc\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="missing.*'name'"):
            load_workflow_config_file(str(cfg))

    def test_step_missing_type_raises(self, tmp_path):
        cfg = tmp_path / "no_type.yaml"
        cfg.write_text(
            "global: {}\n"
            "steps:\n"
            "  - name: s1\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="missing.*'type'"):
            load_workflow_config_file(str(cfg))

    def test_global_not_dict_raises_validation_error(self, tmp_path):
        cfg = tmp_path / "bad_global_shape.yaml"
        cfg.write_text(
            "global:\n"
            "  - invalid\n"
            "steps:\n"
            "  - name: step1\n"
            "    type: calc\n"
            "    params:\n"
            "      iprog: gaussian\n"
            "      itask: opt\n"
            "      keyword: B3LYP\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="'global' must be a dict"):
            load_workflow_config_file(str(cfg))

    def test_non_dict_step_item_raises_validation_error(self, tmp_path):
        cfg = tmp_path / "bad_step_shape.yaml"
        cfg.write_text(
            "global: {}\n"
            "steps:\n"
            "  - invalid\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="step 1 must be a dict"):
            load_workflow_config_file(str(cfg))

    def test_step_params_not_dict_raises_validation_error(self, tmp_path):
        cfg = tmp_path / "bad_params_shape.yaml"
        cfg.write_text(
            "global: {}\n"
            "steps:\n"
            "  - name: step1\n"
            "    type: calc\n"
            "    params:\n"
            "      - invalid\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="'params' must be a dict"):
            load_workflow_config_file(str(cfg))

    def test_legacy_ts_bond_in_step_params_raises(self, tmp_path):
        cfg = tmp_path / "legacy_step.yaml"
        cfg.write_text(
            "global: {}\n"
            "steps:\n"
            "  - name: s1\n"
            "    type: calc\n"
            "    params:\n"
            "      iprog: orca\n"
            "      itask: opt\n"
            "      keyword: B3LYP\n"
            "      ts_bond: '1,2'\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="Legacy key 'ts_bond'"):
            load_workflow_config_file(str(cfg))

    def test_steps_null_raises_validation_error(self, tmp_path):
        cfg = tmp_path / "null_steps.yaml"
        cfg.write_text("global: {}\nsteps:\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="steps"):
            load_workflow_config_file(str(cfg))
