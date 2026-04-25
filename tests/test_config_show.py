"""Tests for --config-show functionality."""

from __future__ import annotations

import json
from pathlib import Path

from confflow.core.contracts import ExitCode


class TestConfigShow:
    """Test suite for --config-show CLI flag."""

    def test_config_show_text_output_all_steps(self, tmp_path: Path, capsys):
        """Test basic text output showing global config and all steps."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4
  cores_per_task: 8

steps:
  - name: gen
    type: confgen
    params:
      chains: "1-2-3"
  - name: opt
    type: calc
    params:
      iprog: g16
      itask: opt
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file)])
        assert result == ExitCode.SUCCESS

        captured = capsys.readouterr()
        output = captured.out
        assert "Global config:" in output
        assert "max_parallel_jobs: 4" in output
        assert "[1] gen (confgen)" in output
        assert "[2] opt (calc)" in output
        assert "cores_per_task: 8" in output

    def test_config_show_json_output(self, tmp_path: Path, capsys):
        """Test JSON output format."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4

steps:
  - name: gen
    type: confgen
    params:
      chains: "1-2-3"
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--format", "json"])
        assert result == ExitCode.SUCCESS

        captured = capsys.readouterr()
        output = captured.out
        data = json.loads(output)
        assert "global_config" in data
        assert "steps" in data
        assert data["global_config"]["max_parallel_jobs"] == 4
        assert len(data["steps"]) == 1
        assert data["steps"][0]["step_name"] == "gen"

    def test_config_show_single_step_by_name(self, tmp_path: Path, capsys):
        """Test filtering by step name."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4

steps:
  - name: gen
    type: confgen
  - name: opt
    type: calc
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--step", "opt"])
        assert result == ExitCode.SUCCESS

        captured = capsys.readouterr()
        output = captured.out
        assert "Step [2]: opt (calc)" in output
        assert "[1] gen" not in output

    def test_config_show_single_step_by_index(self, tmp_path: Path, capsys):
        """Test filtering by 1-based step index."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4

steps:
  - name: gen
    type: confgen
  - name: opt
    type: calc
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--step", "2"])
        assert result == ExitCode.SUCCESS

        captured = capsys.readouterr()
        output = captured.out
        assert "Step [2]: opt (calc)" in output

    def test_config_show_step_index_out_of_range(self, tmp_path: Path, capsys):
        """Test error handling for out-of-range step index."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4

steps:
  - name: gen
    type: confgen
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--step", "999"])
        assert result == ExitCode.USAGE_ERROR

        captured = capsys.readouterr()
        output = captured.err
        assert "out of range" in output.lower()

    def test_config_show_step_name_not_found(self, tmp_path: Path, capsys):
        """Test error handling for non-existent step name."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4

steps:
  - name: gen
    type: confgen
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--step", "nonexistent"])
        assert result == ExitCode.USAGE_ERROR

        captured = capsys.readouterr()
        output = captured.err
        assert "not found" in output.lower() or "no workflow step" in output.lower()

    def test_config_show_missing_config(self, capsys):
        """Test error when --config is not provided."""
        from confflow.cli import main

        result = main(["--config-show"])
        assert result == ExitCode.USAGE_ERROR

        captured = capsys.readouterr()
        output = captured.err
        assert "--config" in output and "required" in output.lower()

    def test_config_show_json_single_step(self, tmp_path: Path, capsys):
        """Test JSON output with single step filter."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4

steps:
  - name: gen
    type: confgen
  - name: opt
    type: calc
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--step", "gen", "--format", "json"])
        assert result == ExitCode.SUCCESS

        captured = capsys.readouterr()
        output = captured.out
        data = json.loads(output)
        assert data["step_name"] == "gen"
        assert data["step_index"] == 1
        assert "resolved_config" in data

    def test_config_show_step_config_merge(self, tmp_path: Path, capsys):
        """Test that step params merge correctly with global config."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4
  cores_per_task: 8

steps:
  - name: opt
    type: calc
    params:
      cores_per_task: 16
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--step", "opt", "--format", "json"])
        assert result == ExitCode.SUCCESS

        captured = capsys.readouterr()
        output = captured.out
        data = json.loads(output)
        # Step params should override global config
        resolved = data["resolved_config"]
        assert resolved["cores_per_task"] == 16
        assert resolved["max_parallel_jobs"] == 4

    def test_config_show_csv_format_treated_as_text(self, tmp_path: Path, capsys):
        """Test that --format csv is treated as text for --config-show."""
        from confflow.cli import main

        config_content = """
global:
  max_parallel_jobs: 4

steps:
  - name: gen
    type: confgen
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        result = main(["--config-show", "-c", str(config_file), "--format", "csv"])
        assert result == ExitCode.SUCCESS

        captured = capsys.readouterr()
        output = captured.out
        # Should be text output, not CSV
        assert "Global config:" in output or "Config:" in output

    def test_config_show_invalid_yaml(self, tmp_path: Path, capsys):
        """Test error handling for invalid YAML."""
        from confflow.cli import main

        config_file = tmp_path / "config.yaml"
        config_file.write_text("invalid: yaml: content:")

        result = main(["--config-show", "-c", str(config_file)])
        assert result == ExitCode.USAGE_ERROR

    def test_config_show_missing_file(self, capsys):
        """Test error when config file does not exist."""
        from confflow.cli import main

        result = main(["--config-show", "-c", "/nonexistent/config.yaml"])
        assert result == ExitCode.USAGE_ERROR

        captured = capsys.readouterr()
        output = captured.err
        assert "error" in output.lower() or "not found" in output.lower()
