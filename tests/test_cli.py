#!/usr/bin/env python3

"""Tests for cli module (merged)."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from confflow.cli import (
    _convert_gjf_to_xyz,
    _is_confflow_process_cmdline,
    _parse_gaussian_input_geometry,
    _resolve_default_work_dir,
    build_parser,
    kill_proc_tree,
    main,
    stop_all_confflow_processes,
)
from tests.support.v4_manifest import publish_completed_v4_manifest


def _v4_config(path):
    """Write a minimal V4 document (formal runtime; runner is mocked in these tests)."""
    path.write_text(
        "schema: confflow.workflow.v4\n"
        "inputs:\n"
        "  structures: {kind: structure, cardinality: many}\n"
        "global:\n"
        "  scientific_defaults: {charge: 0, multiplicity: 1}\n"
        "steps:\n"
        "  - id: s_opt\n"
        "    executor: calculation\n"
        "    bindings:\n"
        "      structure: {source: {run: structures}}\n"
        "    calculation:\n"
        "      program: orca\n"
        "      role: opt\n"
        "      execution_adapter: standard\n"
        "      result_profile: standard\n"
        "      native: {keyword: B3LYP Opt}\n"
        "      checks: [normal_termination]\n"
        "      recovery: {profile: none}\n",
        encoding="utf-8",
    )
    return path


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


def test_stop_process_cmdline_matcher_ignores_unrelated_paths():
    assert _is_confflow_process_cmdline(["python", "-m", "confflow", "input.xyz"])
    assert _is_confflow_process_cmdline(["/usr/bin/confflow", "input.xyz"])
    assert _is_confflow_process_cmdline(["python", "confflow", "run"])

    assert not _is_confflow_process_cmdline(["python", "/tmp/confflow-not-a-run.py"])
    assert not _is_confflow_process_cmdline(["vim", "confflow.yaml"])
    assert not _is_confflow_process_cmdline(["python", "-m", "confflow", "--stop"])


def test_main_no_args(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["confflow"])
    with pytest.raises(SystemExit):
        main()


def test_main_stop_command():
    with patch("confflow.cli.stop_all_confflow_processes", return_value=0) as mock_stop:
        assert main(["--stop"]) == 0
        mock_stop.assert_called_once()


def test_main_normal_path_still_calls_run_workflow(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    # Formal V4 runtime: the normal path dispatches V4 documents to the runner.
    config_yaml = _v4_config(tmp_path / "config.yaml")

    with patch("confflow.cli.run_workflow") as mock_run:
        # Worker-D contract: V4 status owns the aggregate — a MagicMock
        # default (undeterminable) fails closed to FAILED, so the mock
        # must report an explicit completed V4 status for the rc==0 path.
        # L2-CF-delete: the success path also requires the typed COMPLETED
        # manifest, so the mock publishes what the V4 application would.
        def _completed_side_effect(**kwargs):
            publish_completed_v4_manifest(Path(str(kwargs["work_dir"])))
            return {"status": "completed"}

        mock_run.side_effect = _completed_side_effect
        result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])

    assert result == 0
    mock_run.assert_called_once()


@patch("confflow.cli.run_workflow")
def test_main_full_run(mock_run, tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = _v4_config(tmp_path / "config.yaml")

    work_dir = tmp_path / "work"
    with patch("os.makedirs"):
        main([str(input_xyz), "-c", str(config_yaml), "-w", str(work_dir)])
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        assert kwargs["work_dir"] == str(work_dir)


def test_main_gjf_conversion(tmp_path):
    gjf_file = tmp_path / "test.gjf"
    gjf_file.write_text("title\n\n0 1\nC 0 0 0\nH 0 0 1\n\n")
    config_yaml = _v4_config(tmp_path / "config.yaml")

    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(gjf_file), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert mock_run.called
        conv_xyz = tmp_path / "work" / "_converted_inputs" / "test.xyz"
        assert conv_xyz.exists()


def test_cli_accepts_gjf_and_converts_to_xyz(monkeypatch, tmp_path):
    from confflow import cli
    from confflow.core.io import read_xyz_file

    gjf = tmp_path / "input.gjf"
    yaml_cfg = _v4_config(tmp_path / "confflow.yaml")

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
        *,
        input_xyz,
        config_file,
        work_dir,
        pause_beacon_file=None,
        cancel_beacon_file=None,
        on_step_status_change=None,
    ):
        seen["input_xyz"] = input_xyz
        seen["config_file"] = config_file
        seen["work_dir"] = work_dir
        # Worker-D contract: V4 status owns the aggregate — only an
        # explicit completed status yields rc 0 (None is undeterminable).
        # L2-CF-delete: the success path also requires the typed COMPLETED
        # manifest, so the double publishes what the V4 application would.
        publish_completed_v4_manifest(Path(work_dir))
        return {"status": "completed"}

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
            sys.executable,
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
def test_main_value_error_messages(error_msg, input_xyz, tmp_path):
    """Main returns 1 when run_workflow raises ValueError for different messages."""
    config_yaml = _v4_config(tmp_path / "config.yaml")
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
    config_yaml = _v4_config(tmp_path / "config.yaml")

    with patch("confflow.cli.run_workflow", side_effect=RuntimeError("Unexpected error")):
        result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert result == 2


def test_main_handles_cli_output_setup_failure(tmp_path):
    """Failure entering cli_output_to_txt should still return runtime error cleanly."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
    config_yaml = _v4_config(tmp_path / "config.yaml")

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
def test_main_flags(flag, key, expected, input_xyz, tmp_path):
    """Main forwards simple boolean flags to the service facade as kwargs.

    L2-CF-delete: ``resume``/``verbose`` are service-owned flags. The
    typed formal runner never read them, so the adapter no longer forwards
    them to the runner; they are asserted on the service facade instead.
    """
    config_yaml = _v4_config(tmp_path / "config.yaml")
    with patch("confflow.cli.run_workflow_through_service") as mock_facade:
        main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work"), flag])
        assert mock_facade.called
        args, kwargs = mock_facade.call_args
        assert kwargs[key] is expected


def test_main_multiple_inputs(tmp_path):
    """Test main with multiple input files."""
    input1 = tmp_path / "input1.xyz"
    input1.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    input2 = tmp_path / "input2.xyz"
    input2.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = _v4_config(tmp_path / "config.yaml")

    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input1), str(input2), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert mock_run.called


def test_main_work_dir_default(tmp_path, monkeypatch):
    """Test main with default work directory."""
    monkeypatch.chdir(tmp_path)
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = _v4_config(tmp_path / "config.yaml")

    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input_xyz), "-c", str(config_yaml)])
        assert mock_run.called
        args, kwargs = mock_run.call_args
        assert "input_work" in kwargs["work_dir"]


def test_resolve_default_work_dir_shape():
    """The default work directory is derived from the input basename only."""
    assert _resolve_default_work_dir(["/tmp/one.xyz"]) == "one_work"
    assert _resolve_default_work_dir(["/tmp/one.xyz", "/tmp/two.xyz"]) == "one_multi_work"


def test_main_consistency_error_no_interactive_prompt_on_tty(tmp_path):
    """Consistency errors should be written to txt without interactive prompt, even on TTY."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = _v4_config(tmp_path / "config.yaml")

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


def test_build_parser_step_dest():
    """Test that --step parameter has correct dest."""
    parser = build_parser()
    args = parser.parse_args(["--rerun-failed", "dir", "-c", "conf.yaml", "--step", "opt1"])
    assert args.step == "opt1"
    assert args.rerun_failed_step_dir == "dir"


# =============================================================================
# Capability handshake tests (JobDesk <-> ConfFlow v1.4.2)
# =============================================================================


def test_version_flag_exits_zero(monkeypatch, capsys):
    """--version prints the version string and exits with code 0."""
    import re
    import sys

    monkeypatch.setattr(sys, "argv", ["confflow", "--version"])
    result = main(["--version"])
    assert result == 0
    captured = capsys.readouterr()
    assert captured.out.strip()
    assert re.match(r"\d+\.\d+\.\d+", captured.out.strip())


def test_version_flag_does_not_require_input_xyz(monkeypatch):
    """--version exits before the input-XYZ check."""
    import sys

    monkeypatch.setattr(sys, "argv", ["confflow", "--version"])
    result = main(["--version"])
    assert result == 0


def test_capabilities_flag_exits_zero_and_returns_json(monkeypatch, capsys):
    """--capabilities prints JSON and exits with code 0."""
    import json
    import sys

    from confflow.contract import (
        CAPABILITY_SCHEMA_VERSION,
        OUTPUT_MANIFEST_FILE,
        REQUIRED_COMMANDS,
        RUN_MIN_XYZ_TEMPLATE,
        RUN_REPORT_FILE,
        RUN_SUMMARY_FILE,
        WORKFLOW_STATE_FILE,
        WORKFLOW_STATS_FILE,
    )

    monkeypatch.setattr(sys, "argv", ["confflow", "--capabilities"])
    result = main(["--capabilities"])
    assert result == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["schema_version"] == CAPABILITY_SCHEMA_VERSION == 4
    assert "version" in data
    assert isinstance(data["version"], str)
    caps = data["capabilities"]
    assert "workflow_state" in caps
    assert "resume" in caps
    assert "dag" in caps
    assert caps["workflow_state"] is True
    assert caps["resume"] is True
    assert caps["dag"] is True
    artifacts = data["artifacts"]
    assert artifacts == {
        "run_summary": RUN_SUMMARY_FILE,
        "workflow_stats": WORKFLOW_STATS_FILE,
        "workflow_state": WORKFLOW_STATE_FILE,
        "run_report": RUN_REPORT_FILE,
        "min_xyz": RUN_MIN_XYZ_TEMPLATE,
        "output_manifest": OUTPUT_MANIFEST_FILE,
    }
    assert set(data["commands"]) == set(REQUIRED_COMMANDS)
    assert all(isinstance(value, bool) for value in data["commands"].values())
    assert data["build"] == {"commit": None, "dirty": None}


def test_capabilities_json_alias_is_accepted(monkeypatch, capsys):
    """JobDesk's exact --capabilities --json command is accepted."""
    import json

    from confflow.contract import CAPABILITY_SCHEMA_VERSION

    result = main(["--capabilities", "--json"])
    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == CAPABILITY_SCHEMA_VERSION == 4


def test_capability_payload_from_source_with_placeholder_build():
    from confflow.__build__ import COMMIT, DIRTY

    assert {"commit": COMMIT, "dirty": DIRTY} == {"commit": None, "dirty": None}


def test_capabilities_subprocess_stdout_is_pure_json():
    """The installed CLI must not mix import warnings into JSON stdout."""
    import json

    from confflow.contract import (
        CAPABILITY_SCHEMA_VERSION,
        OUTPUT_MANIFEST_FILE,
        REQUIRED_COMMANDS,
        RUN_MIN_XYZ_TEMPLATE,
        RUN_REPORT_FILE,
        RUN_SUMMARY_FILE,
        WORKFLOW_STATE_FILE,
        WORKFLOW_STATS_FILE,
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from confflow.cli import main; raise SystemExit(main())",
            "--capabilities",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == CAPABILITY_SCHEMA_VERSION == 4
    assert payload["capabilities"] == {
        "workflow_state": True,
        "resume": True,
        "dag": True,
        "control_worker": (
            os.name == "posix" and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
        ),
    }
    assert payload["artifacts"] == {
        "run_summary": RUN_SUMMARY_FILE,
        "workflow_stats": WORKFLOW_STATS_FILE,
        "workflow_state": WORKFLOW_STATE_FILE,
        "run_report": RUN_REPORT_FILE,
        "min_xyz": RUN_MIN_XYZ_TEMPLATE,
        "output_manifest": OUTPUT_MANIFEST_FILE,
    }
    assert set(payload["commands"]) == set(REQUIRED_COMMANDS)
    assert all(isinstance(value, bool) for value in payload["commands"].values())
    assert payload["build"] == {"commit": None, "dirty": None}


def test_capabilities_rejects_unknown_arguments():
    """The capability probe must not silently accept misspelled options."""
    with pytest.raises(SystemExit):
        main(["--capabilities", "--definitely-invalid"])


def test_capabilities_flag_does_not_require_input_xyz(monkeypatch):
    """--capabilities exits before the input-XYZ check."""
    import sys

    monkeypatch.setattr(sys, "argv", ["confflow", "--capabilities"])
    result = main(["--capabilities"])
    assert result == 0


def test_capabilities_does_not_call_run_workflow(monkeypatch, tmp_path):
    """--capabilities must never invoke run_workflow."""
    import sys

    monkeypatch.setattr(sys, "argv", ["confflow", "--capabilities"])
    from unittest.mock import patch

    with patch("confflow.cli.run_workflow") as mock_run:
        result = main(["--capabilities"])
        mock_run.assert_not_called()
        assert result == 0


def test_capabilities_does_not_touch_work_dir(monkeypatch, tmp_path):
    """--capabilities must not create any working directory."""
    work_dir = tmp_path / "work"
    from unittest.mock import patch

    with patch("confflow.cli.run_workflow") as mock_run:
        result = main(["--capabilities", "--json", "-w", str(work_dir)])
        mock_run.assert_not_called()
        assert result == 0
        assert not work_dir.exists()


@pytest.mark.skipif(
    not os.environ.get("CONFFLOW_TEST_WHEEL"),
    reason="set CONFFLOW_TEST_WHEEL for the clean-worktree wheel provenance gate",
)
def test_capability_payload_from_wheel_with_real_build(tmp_path):
    """Install a prebuilt wheel and verify its embedded git provenance."""
    from confflow.contract import CAPABILITY_SCHEMA_VERSION

    wheel = os.environ["CONFFLOW_TEST_WHEEL"]
    expected_head = os.environ.get("CONFFLOW_TEST_HEAD")
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)], check=True
    )
    confflow_exe = venv_dir / "bin" / "confflow"
    subprocess.run(
        [
            str(venv_dir / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--force-reinstall",
            wheel,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    completed = subprocess.run(
        [str(confflow_exe), "--capabilities", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == CAPABILITY_SCHEMA_VERSION
    assert payload["build"]["dirty"] is False
    assert re.fullmatch(r"[0-9a-f]{7,40}", payload["build"]["commit"])
    if expected_head:
        assert payload["build"]["commit"] == expected_head


def test_retired_agent_flag_is_rejected() -> None:
    with pytest.raises(SystemExit):
        main(["--agent", "status"])
