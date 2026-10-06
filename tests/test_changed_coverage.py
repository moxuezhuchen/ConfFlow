"""Behavior tests for tools/changed_coverage.py (DIET-2 P0.1a)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "changed_coverage", Path(__file__).resolve().parents[1] / "tools" / "changed_coverage.py"
)
cc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cc)


def _xml(tmp_path, rows):
    body = "".join(f'<line number="{n}" hits="{h}"/>' for n, h in rows)
    p = tmp_path / "coverage.xml"
    p.write_text(
        f'<coverage><packages><package><classes><class filename="confflow/a.py">'
        f"<lines>{body}</lines></class></classes></package></packages></coverage>"
    )
    return str(p)


def test_changed_lines_below_floor_fail(tmp_path):
    cov = cc.coverage_by_file(_xml(tmp_path, [(1, 1), (2, 0), (3, 0)]))
    total, covered, missed = cc.evaluate({"confflow/a.py": {1, 2, 3, 9}}, cov)
    assert (total, covered) == (3, 1)
    assert missed == ["confflow/a.py:2", "confflow/a.py:3"]


def test_pure_deletion_and_non_executable_lines_pass(tmp_path, monkeypatch):
    cov = cc.coverage_by_file(_xml(tmp_path, [(1, 0)]))
    assert cc.evaluate({}, cov) == (0, 0, [])
    monkeypatch.setattr(cc, "changed_lines", lambda base: {"confflow/a.py": {50}})
    assert cc.main(["--xml", _xml(tmp_path, [(1, 0)])]) == 0


def test_main_fails_when_changed_lines_uncovered(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "changed_lines", lambda base: {"confflow/a.py": {1, 2}})
    assert cc.main(["--xml", _xml(tmp_path, [(1, 1), (2, 0)])]) == 1
    assert cc.main(["--xml", _xml(tmp_path, [(1, 1), (2, 1)])]) == 0
