#!/usr/bin/env python3

"""Tests for cli module (merged)."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from confflow.cli import (
    _convert_gjf_to_xyz,
    _parse_gaussian_input_geometry,
    _resolve_default_work_dir,
    build_parser,
    kill_proc_tree,
    main,
    stop_all_confflow_processes,
)
from confflow.workflow.dry_run import estimate_confgen_combinations


def test_parse_gaussian_input_geometry_basic():
    text = """%mem=4GB
# opt b3lyp/6-31g(d)

Title Card

0 1
C 0.0 0.0 0.0
H 0.0 0.0 1.0
H 0.0 1.0 0.0
H 1.0 0.0 0.0

"""
    charge, mult, atoms, coords = _parse_gaussian_input_geometry(text)
    assert charge == 0
    assert mult == 1
    assert atoms == ["C", "H", "H", "H"]
    assert len(coords) == 4
    assert coords[0] == [0.0, 0.0, 0.0]


def test_parse_gaussian_input_geometry_ignores_numeric_title_line():
    text = """%mem=4GB
# opt

1 1

0 1
C 0.0 0.0 0.0
H 0.0 0.0 1.0

"""
    charge, mult, atoms, coords = _parse_gaussian_input_geometry(text)
    assert charge == 0
    assert mult == 1
    assert atoms == ["C", "H"]
    assert coords[1] == [0.0, 0.0, 1.0]


def test_parse_gaussian_input_geometry_frozen_and_atomic_numbers():
    text = """0 1
C -1 0.0 0.0 0.0
H 0 0.0 0.0 1.0
"""
    charge, mult, atoms, coords = _parse_gaussian_input_geometry(text)
    assert atoms == ["C", "H"]
    assert coords[0] == [0.0, 0.0, 0.0]

    text2 = """0 1
6 0.0 0.0 0.0
1 0.0 0.0 1.0
"""
    charge, mult, atoms, coords = _parse_gaussian_input_geometry(text2)
    assert atoms == ["C", "H"]


def test_parse_gaussian_input_geometry_errors():
    with pytest.raises(ValueError, match="Cannot find charge/multiplicity"):
        _parse_gaussian_input_geometry("title\n\nno charge mult here\n")

    with pytest.raises(ValueError, match="does not contain a geometry section"):
        _parse_gaussian_input_geometry("0 1\n\n")


def test_build_parser():
    parser = build_parser()
    args = parser.parse_args(["input.xyz", "-c", "config.yaml"])
    assert args.input_xyz == ["input.xyz"]
    assert args.config == "config.yaml"
    assert args.work_dir is None
    assert not args.resume
    assert not args.verbose
    assert not args.stop
    assert args.export_work_dir is None


def test_convert_gjf_to_xyz(tmp_path):
    gjf = tmp_path / "test.gjf"
    gjf.write_text("title\n\n0 1\nC 0.0 0.0 0.0\nH 0.0 0.0 1.0\n\n")
    xyz = tmp_path / "test.xyz"
    _convert_gjf_to_xyz(str(gjf), str(xyz))
    assert xyz.exists()
    content = xyz.read_text()
    assert "C" in content
    assert "H" in content


def test_stop_process_tree():
    p = subprocess.Popen(["sleep", "10"])
    pid = p.pid
    kill_proc_tree(pid)
    time.sleep(0.2)
    assert p.poll() is not None


def test_kill_proc_tree_refuse_self():
    with pytest.raises(RuntimeError, match="Refusing to stop the current process"):
        kill_proc_tree(os.getpid())


def test_kill_proc_tree_no_process():
    assert kill_proc_tree(999999) is None


@patch("psutil.Process")
def test_kill_proc_tree_mock(mock_proc_class):
    mock_parent = MagicMock()
    mock_child = MagicMock()
    mock_proc_class.return_value = mock_parent
    mock_parent.children.return_value = [mock_child]

    mock_parent.is_running.return_value = False
    mock_child.is_running.return_value = False

    kill_proc_tree(1234, timeout=0.1)

    mock_child.send_signal.assert_called_with(signal.SIGTERM)
    mock_parent.send_signal.assert_called_with(signal.SIGTERM)


@patch("psutil.process_iter")
@patch("psutil.Process")
def test_stop_all_confflow_processes(mock_proc_class, mock_iter):
    mock_myself = MagicMock()
    mock_myself.pid = 1
    mock_proc_class.return_value = mock_myself

    mock_p1 = MagicMock()
    mock_p1.pid = 2
    mock_p1.status.return_value = "running"
    mock_p1.info = {"cmdline": ["python", "-m", "confflow", "input.xyz"], "pid": 2}

    mock_iter.return_value = [mock_p1]

    with patch("confflow.cli.kill_proc_tree") as mock_kill:
        stop_all_confflow_processes()
        mock_kill.assert_called_with(2, timeout=3)


def test_main_no_args(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["confflow"])
    with pytest.raises(SystemExit):
        main()


def test_main_stop_command():
    with patch("confflow.cli.stop_all_confflow_processes", return_value=0) as mock_stop:
        assert main(["--stop"]) == 0
        mock_stop.assert_called_once()


def test_main_export_does_not_call_run_workflow(tmp_path, capsys):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    output = work_dir / "out.csv"

    with (
        patch("confflow.cli.export_results") as mock_export,
        patch("confflow.cli.run_workflow") as mock_run,
    ):
        mock_export.return_value.row_count = 1
        mock_export.return_value.output_path = str(output)
        mock_export.return_value.warnings = []
        result = main(["--export", str(work_dir), "--format", "csv", "-o", str(output)])

    captured = capsys.readouterr()
    assert result == 0
    assert f"Exported 1 result row(s) to {output}" in captured.out
    mock_export.assert_called_once_with(
        str(work_dir),
        output_format="csv",
        output_path=str(output),
    )
    mock_run.assert_not_called()


def test_main_export_missing_work_dir_returns_usage_error(tmp_path, capsys):
    missing = tmp_path / "missing"

    result = main(["--export", str(missing), "--format", "json"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Work directory does not exist" in captured.err


def test_main_normal_path_still_calls_run_workflow(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []\n", encoding="utf-8")

    with patch("confflow.cli.run_workflow") as mock_run:
        result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])

    assert result == 0
    mock_run.assert_called_once()


def test_main_dry_run_does_not_call_run_workflow(tmp_path, capsys):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("3\ntest\nC 0 0 0\nH 0 0 1\nH 0 1 0\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: gen\n"
        "    type: confgen\n"
        "    params:\n"
        "      chains: ['1-2']\n",
        encoding="utf-8",
    )

    with patch("confflow.cli.run_workflow") as mock_run:
        result = main([str(input_xyz), "-c", str(config_yaml), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 0
    assert "ConfFlow dry-run" in captured.out
    assert "gen (confgen)" in captured.out
    mock_run.assert_not_called()


def test_main_dry_run_config_error_returns_usage_error(tmp_path, capsys):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "global: {}\n" "steps:\n" "  - name: gen\n" "    type: confgen\n" "    params: {}\n",
        encoding="utf-8",
    )

    result = main([str(input_xyz), "-c", str(config_yaml), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 1
    assert "confgen step requires" in captured.err


def test_dry_run_confgen_combination_estimate():
    assert estimate_confgen_combinations({"chains": ["1-2-3"], "angle_step": 120}) == 9


def test_main_dry_run_calc_resolved_config_shows_step_override(tmp_path, capsys):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "global:\n"
        "  iprog: orca\n"
        "  itask: sp\n"
        "  keyword: global-keyword\n"
        "  cores_per_task: 1\n"
        "  max_parallel_jobs: 2\n"
        "  total_memory: 4GB\n"
        "steps:\n"
        "  - name: calc1\n"
        "    type: calc\n"
        "    params:\n"
        "      keyword: step-keyword\n"
        "      cores_per_task: 4\n",
        encoding="utf-8",
    )

    result = main([str(input_xyz), "-c", str(config_yaml), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 0
    assert "calc1 (calc)" in captured.out
    assert "keyword=step-keyword" in captured.out
    assert "cores_per_task=4" in captured.out


def test_main_dry_run_missing_executable_path_returns_usage_error(tmp_path, capsys):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    missing_g16 = tmp_path / "missing" / "g16"
    config_yaml.write_text(
        "global:\n"
        "  iprog: gaussian\n"
        "  itask: sp\n"
        "  keyword: hf/sto-3g\n"
        f"  gaussian_path: {missing_g16}\n"
        "steps:\n"
        "  - name: calc1\n"
        "    type: calc\n"
        "    params: {}\n",
        encoding="utf-8",
    )

    result = main([str(input_xyz), "-c", str(config_yaml), "--dry-run"])

    captured = capsys.readouterr()
    assert result == 1
    assert "Gaussian path not found" in captured.err


@patch("confflow.cli.run_workflow")
def test_main_full_run(mock_run, tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")

    work_dir = tmp_path / "work"
    with patch("os.makedirs"):
        main([str(input_xyz), "-c", str(config_yaml), "-w", str(work_dir)])
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert kwargs["work_dir"] == str(work_dir)


def test_main_gjf_conversion(tmp_path):
    gjf_file = tmp_path / "test.gjf"
    gjf_file.write_text("title\n\n0 1\nC 0 0 0\nH 0 0 1\n\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")

    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(gjf_file), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert mock_run.called
        conv_xyz = tmp_path / "work" / "_converted_inputs" / "test.xyz"
        assert conv_xyz.exists()


def test_cli_accepts_gjf_and_converts_to_xyz(monkeypatch, tmp_path):
    from confflow import cli
    from confflow.core.io import read_xyz_file

    gjf = tmp_path / "input.gjf"
    yaml_cfg = tmp_path / "confflow.yaml"
    yaml_cfg.write_text("steps: []\n", encoding="utf-8")

    gjf.write_text(
        """%nproc=1
%mem=1GB
#p opt b3lyp/6-31g(d)

title

0 1
O  0    1.0 2.0 3.0
H  -1   0.0 0.0 0.0

""",
        encoding="utf-8",
    )

    seen = {}

    def fake_run_workflow(
        *, input_xyz, config_file, work_dir, original_input_files=None, resume=False, verbose=False
    ):
        seen["input_xyz"] = input_xyz
        seen["config_file"] = config_file
        seen["work_dir"] = work_dir
        return None

    monkeypatch.setattr(cli, "run_workflow", fake_run_workflow)

    work_dir = tmp_path / "work"
    rc = cli.main([str(gjf), "-c", str(yaml_cfg), "-w", str(work_dir)])
    assert rc == 0

    assert "input_xyz" in seen
    assert len(seen["input_xyz"]) == 1
    xyz_path = seen["input_xyz"][0]
    assert xyz_path.endswith(".xyz")
    assert os.path.exists(xyz_path)

    frames = read_xyz_file(xyz_path, parse_metadata=True)
    assert len(frames) == 1
    assert frames[0]["natoms"] == 2
    assert frames[0]["atoms"] == ["O", "H"]


def test_convert_gjf_to_xyz_error(tmp_path):
    """Test _convert_gjf_to_xyz with unreadable file."""
    non_existent = tmp_path / "missing.gjf"
    xyz_out = tmp_path / "out.xyz"
    with pytest.raises(RuntimeError, match="Failed to read Gaussian input file"):
        _convert_gjf_to_xyz(str(non_existent), str(xyz_out))


def test_kill_proc_tree_timeout():
    """Test kill_proc_tree with timeout triggers SIGKILL."""
    # Start a process that ignores SIGTERM
    p = subprocess.Popen(
        [
            "python",
            "-c",
            "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); import time; time.sleep(60)",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = p.pid
    time.sleep(0.1)  # Let process start

    # Should timeout and force kill
    kill_proc_tree(pid, timeout=0.5)
    time.sleep(0.3)
    assert p.poll() is not None


def test_stop_all_confflow_processes_loop():
    """Test stop_all_confflow_processes iterates through multiple processes."""
    mock_p1 = MagicMock()
    mock_p1.pid = 100
    mock_p1.status.return_value = "running"
    mock_p1.info = {"cmdline": ["python", "confflow", "run"], "pid": 100}

    mock_p2 = MagicMock()
    mock_p2.pid = 101
    mock_p2.status.return_value = "running"
    mock_p2.info = {"cmdline": ["python", "-m", "confflow"], "pid": 101}

    with patch("psutil.process_iter", return_value=[mock_p1, mock_p2]):
        with patch("psutil.Process") as mock_proc:
            mock_myself = MagicMock()
            mock_myself.pid = 1
            mock_proc.return_value = mock_myself

            with patch("confflow.cli.kill_proc_tree") as mock_kill:
                stop_all_confflow_processes()
                assert mock_kill.call_count == 2


def test_stop_all_confflow_processes_access_denied():
    """Test stop_all_confflow_processes handles AccessDenied."""
    mock_p = MagicMock()
    mock_p.pid = 100
    mock_p.status.return_value = "running"
    mock_p.info = {"cmdline": ["confflow"], "pid": 100}

    with patch("psutil.process_iter", return_value=[mock_p]):
        with patch("psutil.Process") as mock_proc:
            mock_myself = MagicMock()
            mock_myself.pid = 1
            mock_proc.return_value = mock_myself

            with patch("confflow.cli.kill_proc_tree", side_effect=OSError("Access Denied")):
                result = stop_all_confflow_processes()
                assert result == 0


@pytest.mark.parametrize(
    "error_msg",
    [
        "多文件输入模式要求所有输入具有相同的原子顺序",
        "柔性链在不同输入间不一致",
    ],
)
def test_main_value_error_messages(error_msg, input_xyz, config_yaml, tmp_path):
    """Main returns 1 when run_workflow raises ValueError for different messages."""
    with patch("confflow.cli.run_workflow", side_effect=ValueError(error_msg)):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("sys.stdout.isatty", return_value=False),
        ):
            result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
            assert result == 1


def test_main_generic_exception(tmp_path):
    """Test main handles generic exceptions."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")

    with patch("confflow.cli.run_workflow", side_effect=RuntimeError("Unexpected error")):
        result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert result == 2


def test_main_handles_cli_output_setup_failure(tmp_path):
    """Failure entering cli_output_to_txt should still return runtime error cleanly."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []\n", encoding="utf-8")

    with (
        patch("confflow.cli.cli_output_to_txt", side_effect=OSError("cannot open output")),
        patch("confflow.cli._append_to_output") as mock_append,
    ):
        result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])

    assert result == 2
    assert mock_append.called
    assert str(input_xyz.with_suffix(".txt")) in mock_append.call_args.args[0]


def test_main_missing_config(tmp_path):
    """Test main with missing config file."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")

    with pytest.raises(SystemExit):
        main([str(input_xyz)])


@pytest.mark.parametrize(
    "flag,key,expected", [("--resume", "resume", True), ("--verbose", "verbose", True)]
)
def test_main_flags(flag, key, expected, input_xyz, config_yaml, tmp_path):
    """Main forwards simple boolean flags to run_workflow as kwargs."""
    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work"), flag])
        assert mock_run.called
        args, kwargs = mock_run.call_args
        assert kwargs[key] is expected


def test_main_multiple_inputs(tmp_path):
    """Test main with multiple input files."""
    input1 = tmp_path / "input1.xyz"
    input1.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    input2 = tmp_path / "input2.xyz"
    input2.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")

    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input1), str(input2), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert mock_run.called


def test_main_work_dir_default(tmp_path, monkeypatch):
    """Test main with default work directory."""
    monkeypatch.chdir(tmp_path)
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")

    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input_xyz), "-c", str(config_yaml)])
        assert mock_run.called
        args, kwargs = mock_run.call_args
        assert "input_work" in kwargs["work_dir"]


def test_resolve_default_work_dir_uses_sandbox_root(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    sandbox = tmp_path / "sandbox"

    resolved = _resolve_default_work_dir([str(input_xyz)], sandbox_root=str(sandbox))

    assert resolved == str(sandbox / "input_work")


def test_main_default_work_dir_inside_sandbox_root(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "global:\n  sandbox_root: " + str(tmp_path / "sandbox") + "\nsteps: []\n"
    )

    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input_xyz), "-c", str(config_yaml)])
        _, kwargs = mock_run.call_args
        assert kwargs["work_dir"] == str(tmp_path / "sandbox" / "input_work")


def test_main_invalid_work_dir_returns_usage_error(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "global:\n  sandbox_root: " + str(tmp_path / "sandbox") + "\nsteps: []\n"
    )

    result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "outside")])

    assert result == 1
    content = (tmp_path / "input.txt").read_text(encoding="utf-8")
    assert "work_dir escapes sandbox_root" in content


def test_main_consistency_error_no_interactive_prompt_on_tty(tmp_path):
    """Consistency errors should be written to txt without interactive prompt, even on TTY."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")

    error_msg = "all inputs must have the same atom count and element order.\nelement order mismatch (multi-input mode requires full match):"

    with patch("confflow.cli.run_workflow", side_effect=ValueError(error_msg)):
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            with patch(
                "builtins.input", side_effect=AssertionError("input() should not be called")
            ):
                result = main(
                    [str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")]
                )
                assert result == 1

    output_txt = tmp_path / "input.txt"
    assert output_txt.exists()
    content = output_txt.read_text(encoding="utf-8")
    assert "Input consistency validation failed" in content


# =============================================================================
# CLI path-coverage tests (merged from test_cli_and_confts_paths.py)
# =============================================================================


def test_cli_kill_proc_tree_no_psutil():
    with patch("confflow.cli.psutil", None):
        kill_proc_tree(1234)


def test_cli_kill_proc_tree_no_such_process():
    try:
        import psutil  # noqa: F401
    except ImportError:
        pytest.skip("psutil not installed")

    import psutil

    with patch("psutil.Process", side_effect=psutil.NoSuchProcess(1234)):
        kill_proc_tree(1234)


def test_cli_parse_gaussian_errors():
    with pytest.raises(ValueError, match="Cannot find charge/multiplicity line"):
        _parse_gaussian_input_geometry("title\n\ngeometry\n")

    with pytest.raises(ValueError, match="does not contain a geometry section"):
        _parse_gaussian_input_geometry("title\n\n0 1\n\n")

    text = "title\n\n0 1\nC 0.0 0.0\n\n"
    with pytest.raises(ValueError, match="does not contain a geometry section"):
        _parse_gaussian_input_geometry(text)


def test_cli_convert_gjf_to_xyz_error(tmp_path):
    gjf = tmp_path / "test.gjf"
    gjf.write_text("invalid")
    xyz = tmp_path / "test.xyz"

    with pytest.raises(ValueError):
        _convert_gjf_to_xyz(str(gjf), str(xyz))


def test_cli_stop_all_loop():
    with patch("confflow.cli.psutil.process_iter") as mock_iter:
        p1 = MagicMock()
        p1.pid = 99999
        p1.status.return_value = "running"
        p1.info = {"name": "confflow", "cmdline": ["confflow", "run"]}

        p2 = MagicMock()
        p2.pid = 88888
        p2.status.return_value = "zombie"

        mock_iter.return_value = [p1, p2]

        with patch("confflow.cli.psutil.Process") as mock_self:
            mock_self.return_value.pid = 12345
            with patch("confflow.cli.kill_proc_tree") as mock_kill:
                stop_all_confflow_processes()
                mock_kill.assert_called_once()


def test_cli_no_psutil_stop_all_returns_1():
    with patch("confflow.cli.psutil", None):
        ret = stop_all_confflow_processes()
        assert ret == 1


def test_cli_main_logger_error_failure(tmp_path):
    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text(
        "global:\n  itask: 1\n  keyword: sp\n  iprog: orca\nsteps:\n  - name: step1\n    type: calc\n"
    )

    with patch("confflow.cli.run_workflow", side_effect=Exception("workflow failed")):
        with patch("confflow.cli.logger.error", side_effect=Exception("logger failed")):
            ret = main([str(xyz), "-c", str(conf), "-w", str(tmp_path / "work")])
            assert ret == 2


def test_build_parser_config_show():
    """Test that --config-show flag is correctly parsed."""
    parser = build_parser()
    args = parser.parse_args(["--config-show", "-c", "config.yaml"])
    assert args.config_show is True
    assert args.config == "config.yaml"


def test_build_parser_step_dest():
    """Test that --step parameter has correct dest."""
    parser = build_parser()
    args = parser.parse_args(["--rerun-failed", "dir", "-c", "conf.yaml", "--step", "opt1"])
    assert args.step == "opt1"
    assert args.rerun_failed_step_dir == "dir"


def test_build_parser_format_extended():
    """Test that --format choices include text."""
    parser = build_parser()
    args = parser.parse_args(["--config-show", "-c", "config.yaml", "--format", "text"])
    assert args.format == "text"

    args = parser.parse_args(["--config-show", "-c", "config.yaml", "--format", "json"])
    assert args.format == "json"
