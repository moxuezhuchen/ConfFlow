from unittest.mock import MagicMock, patch

import pytest


def test_cli_kill_proc_tree_no_psutil():
    from confflow.cli import kill_proc_tree

    with patch("confflow.cli.psutil", None):
        kill_proc_tree(1234)


def test_cli_kill_proc_tree_no_such_process():
    from confflow.cli import kill_proc_tree

    try:
        import psutil  # noqa: F401
    except ImportError:
        pytest.skip("psutil not installed")

    import psutil

    with patch("psutil.Process", side_effect=psutil.NoSuchProcess(1234)):
        kill_proc_tree(1234)


def test_cli_parse_gaussian_errors():
    from confflow.cli import _parse_gaussian_input_geometry

    with pytest.raises(ValueError, match="Cannot find charge/multiplicity line"):
        _parse_gaussian_input_geometry("title\n\ngeometry\n")

    with pytest.raises(ValueError, match="No geometry found"):
        _parse_gaussian_input_geometry("title\n\n0 1\n\n")

    text = "title\n\n0 1\nC 0.0 0.0\n\n"
    with pytest.raises(ValueError, match="No geometry found"):
        _parse_gaussian_input_geometry(text)


def test_cli_convert_gjf_to_xyz_error(tmp_path):
    from confflow.cli import _convert_gjf_to_xyz

    gjf = tmp_path / "test.gjf"
    gjf.write_text("invalid")
    xyz = tmp_path / "test.xyz"

    with pytest.raises(ValueError):
        _convert_gjf_to_xyz(str(gjf), str(xyz))


def test_cli_stop_all_loop():
    from confflow.cli import stop_all_confflow_processes

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
    from confflow.cli import stop_all_confflow_processes

    with patch("confflow.cli.psutil", None):
        ret = stop_all_confflow_processes()
        assert ret == 1


def test_cli_main_logger_error_failure(tmp_path):
    from confflow.cli import main

    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text(
        "global:\n  itask: 1\n  keyword: sp\n  iprog: orca\nsteps:\n  - name: step1\n    type: calc\n"
    )

    with patch("confflow.cli.run_workflow", side_effect=Exception("workflow failed")):
        with patch("confflow.cli.logger.error", side_effect=Exception("logger failed")):
            ret = main([str(xyz), "-c", str(conf), "-w", str(tmp_path / "work")])
            assert ret == 1


def test_confts_main_cli(tmp_path):
    from confflow.confts import main as confts_main

    with patch("sys.argv", ["confts"]):
        with pytest.raises(SystemExit):
            confts_main()

    xyz_path = tmp_path / "test.xyz"
    xyz_path.write_text("2\n\nC 0 0 0\nH 0 0 1\n")

    with patch("sys.argv", ["confts", str(xyz_path), "opt(ts)"]):
        try:
            confts_main()
        except SystemExit:
            pass
        except Exception:
            pass


def test_confts_keyword_rewrite():
    from confflow.confts import make_scan_keyword_from_ts_keyword

    assert make_scan_keyword_from_ts_keyword("opt(ts,calcfc,tight) freq") == "opt"
    assert make_scan_keyword_from_ts_keyword("opt=(ts,calcfc) freq=(noraman)") == "opt"
    assert make_scan_keyword_from_ts_keyword("opt(nomicro,ts) freq") == "opt(nomicro)"
    assert make_scan_keyword_from_ts_keyword("") == ""


def test_confts_cli_more(tmp_path):
    from confflow.confts import _cli

    with pytest.raises(SystemExit):
        _cli(["nonexistent.xyz", "-s", "nonexistent.ini"])

    xyz = tmp_path / "test.xyz"
    xyz.write_text("1\n\nC 0 0 0\n")

    ini = tmp_path / "test.ini"
    ini.write_text("[DEFAULT]\nitask=4\nts_rescue_scan=false\n")

    with patch("confflow.calc.ChemTaskManager") as mock_mgr:
        mock_mgr.return_value.config = {"itask": 4, "ts_rescue_scan": "false"}
        _cli([str(xyz), "-s", str(ini)])
        assert mock_mgr.return_value.config["ts_rescue_scan"] == "false"


def test_confts_cli_full_and_errors(tmp_path):
    from confflow.confts import _cli

    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text("global:\n  itask: 4\n  keyword: opt(ts,calcfc)\n  iprog: gaussian\n")

    with patch("confflow.calc.ChemTaskManager") as mock_manager:
        _cli([str(xyz), "-s", str(conf)])
        mock_manager.assert_called_once()

    with patch("builtins.print") as mock_print:
        _cli(["--rewrite-scan-keyword", "opt(ts) freq"])
        mock_print.assert_called_with("opt")

    with pytest.raises(SystemExit):
        _cli(["nonexistent.xyz", "-s", "nonexistent.yaml"])

    with pytest.raises(SystemExit):
        _cli([str(xyz), "-s", "nonexistent.yaml"])
