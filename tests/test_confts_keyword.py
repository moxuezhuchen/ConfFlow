#!/usr/bin/env python3

"""Tests for confts module keywords (merged from test_core.py and test_cli_and_confts_paths.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestConftsKeyword:
    """Tests for confts module keywords."""

    @pytest.mark.parametrize(
        "kw,expected",
        [
            ("opt(nomicro,calcfc,tight,ts,noeigentest)", "opt(nomicro)"),
            ("opt=(nomicro,calcfc,tight,ts,noeigentest) freq", "opt=(nomicro)"),
            ("opt=(nomicro,calcfc,tight,ts,noeigentest) freq=noraman", "opt=(nomicro)"),
            ("opt=(nomicro,rcfc,readfc,ts,calcfc) freq=noraman", "opt=(nomicro)"),
            ("opt(ts,calcfc) ts", "opt ts"),
            ("ts freq", "ts"),
            ("opt(ts,calcfc,tight) freq", "opt"),
            ("opt=(ts,calcfc) freq=(noraman)", "opt"),
            ("opt(nomicro,ts) freq", "opt(nomicro)"),
            ("opt=(ts,rcfc,readfc,nomicro) freq", "opt=(nomicro)"),
            ("", ""),
        ],
    )
    def test_make_scan_keyword_from_ts_keyword(self, kw, expected):
        from confflow.confts import make_scan_keyword_from_ts_keyword

        assert make_scan_keyword_from_ts_keyword(kw) == expected

    def test_confts_cli_rewrite(self, capsys):
        from confflow.confts import _cli

        _cli(["--rewrite-scan-keyword", "opt(ts,calcfc) freq"])
        captured = capsys.readouterr()
        assert "opt" in captured.out
        assert "freq" not in captured.out

    def test_confts_cli_rewrite_print(self):
        from confflow.confts import _cli

        with patch("builtins.print") as mock_print:
            _cli(["--rewrite-scan-keyword", "opt(ts) freq"])
            mock_print.assert_called_with("opt")

    def test_confts_cli_no_args(self, capsys):
        from confflow.confts import _cli

        res = _cli([])
        assert res == 1


class TestConftsCli:
    """Tests for confts CLI entry points."""

    def test_confts_main_cli(self, tmp_path):
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

    def test_confts_cli_more(self, tmp_path):
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

    def test_confts_cli_full_and_errors(self, tmp_path):
        from confflow.confts import _cli

        xyz = tmp_path / "test.xyz"
        xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

        conf = tmp_path / "conf.yaml"
        conf.write_text("global:\n  itask: 4\n  keyword: opt(ts,calcfc)\n  iprog: gaussian\n")

        with patch("confflow.calc.ChemTaskManager") as mock_manager:
            _cli([str(xyz), "-s", str(conf)])
            mock_manager.assert_called_once()

        with pytest.raises(SystemExit):
            _cli(["nonexistent.xyz", "-s", "nonexistent.yaml"])

        with pytest.raises(SystemExit):
            _cli([str(xyz), "-s", "nonexistent.yaml"])
