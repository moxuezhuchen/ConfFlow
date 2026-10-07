"""Behavior tests for tools/test_contribution.py (DIET-2 T0)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from coverage import CoverageData

_SPEC = importlib.util.spec_from_file_location(
    "test_contribution",
    Path(__file__).resolve().parents[1] / "tools" / "test_contribution.py",
)
tc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tc)

A = "tests/test_a.py::test_alpha|run"
B = "tests/test_a.py::test_beta|run"
C = "tests/test_a.py::test_gamma|run"


def _write(tmp_path, name, payload, arcs=False):
    data = CoverageData(basename=str(tmp_path / name))
    for ctx, files in payload.items():
        data.set_context(ctx)
        if arcs:
            data.add_arcs(files)
        else:
            data.add_lines(files)
    data.write()
    got = CoverageData(basename=str(tmp_path / name))
    got.read()
    return got


def _by_node(report):
    return {t["node"]: t for t in report["tests"]}


def test_exclusive_lines_and_zero_candidate(tmp_path):
    data = _write(
        tmp_path,
        "cov_lines",
        {A: {"confflow/a.py": {1, 2}}, B: {"confflow/a.py": {2, 3}}, C: {"confflow/a.py": {2}}},
    )
    report = tc.build_report(data, list(tc.DEFAULT_PROTECT_KEYWORDS), str(tmp_path))
    by_node = _by_node(report)
    assert by_node["tests/test_a.py::test_alpha"]["exclusive_lines"] == 1
    assert by_node["tests/test_a.py::test_beta"]["exclusive_lines"] == 1
    zero = by_node["tests/test_a.py::test_gamma"]
    assert (zero["exclusive_lines"], zero["exclusive_arcs"]) == (0, 0)
    assert report["candidates"]["deletable"] == ["tests/test_a.py::test_gamma"]
    group = report["by_test_file"][0]
    assert (group["n_tests"], group["n_zero"], group["zero_ratio"]) == (3, 1, 0.333)


def test_exclusive_arcs(tmp_path):
    data = _write(
        tmp_path,
        "cov_arcs",
        {
            A: {"confflow/a.py": {(1, 2), (2, 3)}},
            B: {"confflow/a.py": {(2, 3), (3, 4)}},
        },
        arcs=True,
    )
    report = tc.build_report(data, list(tc.DEFAULT_PROTECT_KEYWORDS), str(tmp_path))
    by_node = _by_node(report)
    assert by_node["tests/test_a.py::test_alpha"]["exclusive_arcs"] == 1
    assert by_node["tests/test_a.py::test_beta"]["exclusive_arcs"] == 1
    assert report["summary"]["n_exclusive_arcs"] == 2
    assert report["candidates"]["deletable"] == []


def test_protect_keywords_excluded_from_deletable(tmp_path):
    quota_node = "tests/test_quota_x.py::test_quota_limit|run"
    data = _write(
        tmp_path,
        "cov_protect",
        {A: {"confflow/a.py": {1, 2}}, quota_node: {"confflow/a.py": {1}}},
    )
    report = tc.build_report(data, list(tc.DEFAULT_PROTECT_KEYWORDS), str(tmp_path))
    assert report["candidates"]["deletable"] == []
    assert report["candidates"]["protected"][0]["protect_hits"] == ["quota"]
    limit_node = "tests/test_a.py::test_limit_check"
    data2 = _write(
        tmp_path,
        "cov_custom",
        {A: {"confflow/a.py": {1, 2}}, limit_node + "|run": {"confflow/a.py": {1}}},
    )
    plain = tc.build_report(data2, list(tc.DEFAULT_PROTECT_KEYWORDS), str(tmp_path))
    assert plain["candidates"]["deletable"] == [limit_node]
    custom = tc.build_report(data2, tc.parse_protect_args(["limit"]), str(tmp_path))
    assert custom["candidates"]["deletable"] == []
    assert custom["candidates"]["protected"][0]["node"] == limit_node


def test_header_notes_and_formats(tmp_path):
    data = _write(tmp_path, "cov_head", {A: {"confflow/a.py": {1}}})
    report = tc.build_report(data, list(tc.DEFAULT_PROTECT_KEYWORDS), str(tmp_path))
    assert report["usage_note"] == tc.USAGE_NOTE
    assert report["zero_note"] == tc.ZERO_NOTE
    assert "用途仅限于给候选排序" in tc.USAGE_NOTE
    table = tc.format_table(report, "cov_head")
    assert table.startswith("# tools/test_contribution.py")
    assert tc.USAGE_NOTE in table and tc.ZERO_NOTE in table
    assert tc.FULL_RUN_COMMAND in table
    assert "--cov-context=test" in table
