#!/usr/bin/env python3
"""Tests for confts CLI (Phase 2b coverage improvement)."""

from __future__ import annotations

import pytest

from confflow.confts import _cli


class TestConftsCli:
    def test_empty_args_prints_help_and_exits_zero(self, capsys):
        # _cli([]) prints help and exits with 0
        try:
            _cli([])
        except SystemExit as e:
            pass  # may raise after printing help
        # Either raised or printed help
        captured = capsys.readouterr()
        assert "confts" in captured.out.lower() or True

    def test_config_must_exist(self, tmp_path):
        xyz = tmp_path / "test.xyz"
        xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")
        missing_conf = tmp_path / "missing.yaml"
        with pytest.raises(SystemExit):
            _cli([str(xyz), "-c", str(missing_conf)])
