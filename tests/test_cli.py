"""cli 模块测试（合并版）"""

import os
import signal
import subprocess
import time
from unittest.mock import patch, MagicMock

import pytest

from confflow.cli import (
    _parse_gaussian_input_geometry,
    _convert_gjf_to_xyz,
    build_parser,
    kill_proc_tree,
    stop_all_confflow_processes,
    main,
)


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

    with pytest.raises(ValueError, match="No geometry found"):
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
    with pytest.raises(RuntimeError, match="I refuse to kill myself"):
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
    """Test _convert_gjf_to_xyz with unreadable file"""
    non_existent = tmp_path / "missing.gjf"
    xyz_out = tmp_path / "out.xyz"
    with pytest.raises(RuntimeError, match="无法读取 Gaussian 输入文件"):
        _convert_gjf_to_xyz(str(non_existent), str(xyz_out))


def test_kill_proc_tree_timeout():
    """Test kill_proc_tree with timeout triggers SIGKILL"""
    # Start a process that ignores SIGTERM
    p = subprocess.Popen(
        ["python", "-c", "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); import time; time.sleep(60)"],
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
    """Test stop_all_confflow_processes iterates through multiple processes"""
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
    """Test stop_all_confflow_processes handles AccessDenied"""
    mock_p = MagicMock()
    mock_p.pid = 100
    mock_p.status.return_value = "running"
    mock_p.info = {"cmdline": ["confflow"], "pid": 100}
    
    with patch("psutil.process_iter", return_value=[mock_p]):
        with patch("psutil.Process") as mock_proc:
            mock_myself = MagicMock()
            mock_myself.pid = 1
            mock_proc.return_value = mock_myself
            
            with patch("confflow.cli.kill_proc_tree", side_effect=Exception("Access Denied")):
                result = stop_all_confflow_processes()
                assert result == 0


def test_main_value_error_consistency(tmp_path, monkeypatch):
    """Test main handles ValueError for consistency check"""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")
    
    error_msg = "多文件输入模式要求所有输入具有相同的原子顺序"
    
    with patch("confflow.cli.run_workflow", side_effect=ValueError(error_msg)):
        with patch("sys.stdin.isatty", return_value=False):
            with patch("sys.stdout.isatty", return_value=False):
                result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
                assert result == 1


def test_main_value_error_chain_consistency(tmp_path, monkeypatch):
    """Test main handles ValueError for chain consistency"""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")
    
    error_msg = "柔性链在不同输入间不一致"
    
    with patch("confflow.cli.run_workflow", side_effect=ValueError(error_msg)):
        with patch("sys.stdin.isatty", return_value=False):
            with patch("sys.stdout.isatty", return_value=False):
                result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
                assert result == 1


def test_main_generic_exception(tmp_path):
    """Test main handles generic exceptions"""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")
    
    with patch("confflow.cli.run_workflow", side_effect=RuntimeError("Unexpected error")):
        result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert result == 1


def test_main_missing_config(tmp_path):
    """Test main with missing config file"""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    
    with pytest.raises(SystemExit):
        main([str(input_xyz)])


def test_main_resume_flag(tmp_path):
    """Test main with resume flag"""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")
    
    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work"), "--resume"])
        assert mock_run.called
        args, kwargs = mock_run.call_args
        assert kwargs["resume"] is True


def test_main_verbose_flag(tmp_path):
    """Test main with verbose flag"""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")
    
    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work"), "--verbose"])
        assert mock_run.called
        args, kwargs = mock_run.call_args
        assert kwargs["verbose"] is True


def test_main_multiple_inputs(tmp_path):
    """Test main with multiple input files"""
    input1 = tmp_path / "input1.xyz"
    input1.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    input2 = tmp_path / "input2.xyz"
    input2.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")
    
    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input1), str(input2), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
        assert mock_run.called


def test_main_work_dir_default(tmp_path):
    """Test main with default work directory"""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")
    
    with patch("confflow.cli.run_workflow") as mock_run:
        main([str(input_xyz), "-c", str(config_yaml)])
        assert mock_run.called
        args, kwargs = mock_run.call_args
        assert "input_work" in kwargs["work_dir"]


def test_main_consistency_error_no_interactive_prompt_on_tty(tmp_path):
    """Consistency errors should be written to txt without interactive prompt, even on TTY."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text("global: {}\nsteps: []")

    error_msg = "多文件输入模式要求所有输入具有相同的原子顺序"

    with patch("confflow.cli.run_workflow", side_effect=ValueError(error_msg)):
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True):
            with patch("builtins.input", side_effect=AssertionError("input() should not be called")):
                result = main([str(input_xyz), "-c", str(config_yaml), "-w", str(tmp_path / "work")])
                assert result == 1

    output_txt = tmp_path / "input.txt"
    assert output_txt.exists()
    content = output_txt.read_text(encoding="utf-8")
    assert "输入一致性校验失败" in content
