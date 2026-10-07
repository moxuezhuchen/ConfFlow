#!/usr/bin/env python3
"""Per-test exclusive coverage report (DIET-2 T0; PLAN section 6 T0/W1).

Reads a coverage data file produced by ``pytest --cov=confflow
--cov-context=test`` (coverage.py dynamic contexts) and computes, for each
test node id, the lines and branch arcs covered ONLY by that test. Tests
with zero exclusive lines and arcs are listed as T1 candidates, grouped by
test file with the in-file candidate ratio.

Only the standard library plus ``coverage`` (already a project dependency)
is used, through the ``coverage.CoverageData`` API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from coverage import CoverageData

USAGE_NOTE = "用途仅限于给候选排序(W1)：独有覆盖只用于生成 T1 候选排序，不作为删除依据。"
ZERO_NOTE = (
    "独有覆盖为零不能说明测试多余：崩溃一致性、并发与竞争、安全、取消、续算、"
    "跨仓 contract 类别一律保留(T1 删除条件)。"
)
DEFAULT_PROTECT_KEYWORDS = (
    "crash",
    "race",
    "security",
    "cancel",
    "resume",
    "cross_repo",
    "contract",
    "terminal_arbitration",
    "quota",
)
FULL_RUN_COMMAND = (
    "pytest tests/ --cov=confflow --cov-context=test --cov-report= -p no:cacheprovider -q"
)
FULL_RUN_ESTIMATE = (
    "单测试上下文全量约需 1-2 小时；必须串行运行，不得使用 -n "
    "(pytest-xdist 会丢失或混淆动态上下文)；建议在 CI 或服务器上生成数据后再用本脚本分析。"
)
_CANDIDATE_FILES = (".coverage", ".coverage_temp")


def resolve_data_file(explicit: str | None) -> str:
    """Return the coverage data file to read, honouring an explicit path."""
    if explicit:
        return explicit
    for name in _CANDIDATE_FILES:
        if os.path.exists(name):
            return name
    return _CANDIDATE_FILES[0]


def load_data(path: str) -> CoverageData:
    """Read a coverage data file and return the CoverageData object."""
    data = CoverageData(basename=os.path.abspath(path))
    data.read()
    return data


def node_of_context(raw: str) -> str:
    """Strip the pytest-cov phase suffix (``|run``) from a raw context."""
    return raw.split("|")[0]


def is_test_context(raw: str) -> bool:
    """Check whether a raw context names a test node id."""
    return "::" in node_of_context(raw)


def collect_nodes(data: CoverageData) -> dict[str, list[str]]:
    """Map each test node id to its raw coverage contexts."""
    nodes: dict[str, list[str]] = {}
    for raw in sorted(data.measured_contexts()):
        if is_test_context(raw):
            nodes.setdefault(node_of_context(raw), []).append(raw)
    return nodes


def _relpath(filename: str, root: str) -> str:
    if os.path.isabs(filename):
        try:
            return os.path.relpath(filename, root)
        except ValueError:
            return filename
    return filename


def line_cover(data: CoverageData, files: list[str]) -> dict[str, dict[int, set[str]]]:
    """Map each file to {line: {covering node ids}} via contexts_by_lineno."""
    data.set_query_contexts(None)
    cover: dict[str, dict[int, set[str]]] = {}
    for filename in files:
        per_line: dict[int, set[str]] = {}
        for lineno, contexts in data.contexts_by_lineno(filename).items():
            owners = {node_of_context(c) for c in contexts if is_test_context(c)}
            if owners:
                per_line[lineno] = owners
        cover[filename] = per_line
    return cover


def _anchored(raws: list[str]) -> list[str]:
    return ["^" + re.escape(raw) + "$" for raw in raws]


def arc_cover(
    data: CoverageData, nodes: dict[str, list[str]], files: list[str]
) -> dict[str, dict[tuple[int, int], set[str]]]:
    """Map each file to {arc: {covering node ids}} with per-node queries."""
    cover: dict[str, dict[tuple[int, int], set[str]]] = {f: {} for f in files}
    if not data.has_arcs():
        return cover
    for node, raws in sorted(nodes.items()):
        data.set_query_contexts(_anchored(raws))
        for filename in files:
            for arc in data.arcs(filename) or []:
                cover[filename].setdefault((arc[0], arc[1]), set()).add(node)
    data.set_query_contexts(None)
    return cover


def protect_hits(node: str, keywords: list[str]) -> list[str]:
    """Return the protect keywords matched (case-insensitively) by a node."""
    lowered = node.lower()
    return [kw for kw in keywords if kw.lower() in lowered]


def parse_protect_args(values: list[str] | None) -> list[str]:
    """Merge --protect values (comma-separated, repeatable) into keywords."""
    keywords = list(DEFAULT_PROTECT_KEYWORDS)
    for value in values or []:
        for part in value.split(","):
            part = part.strip()
            if part and part not in keywords:
                keywords.append(part)
    return keywords


def build_report(data: CoverageData, keywords: list[str], root: str) -> dict:
    """Compute exclusive lines/arcs per test and assemble the report dict."""
    nodes = collect_nodes(data)
    files = sorted(data.measured_files())
    lines = line_cover(data, files)
    arcs = arc_cover(data, nodes, files)
    per_test: dict[str, dict] = {}
    for node in nodes:
        test_file = node.split("::")[0]
        per_test[node] = {
            "node": node,
            "file": test_file,
            "exclusive_lines": 0,
            "exclusive_arcs": 0,
            "exclusive_line_refs": [],
            "exclusive_arc_refs": [],
            "protect_hits": protect_hits(node, keywords),
        }
    file_rows: list[dict] = []
    for filename in files:
        line_owners = lines[filename]
        exclusive_lines = sorted(ln for ln, owners in line_owners.items() if len(owners) == 1)
        arc_owners = arcs[filename]
        exclusive_arcs = sorted(arc for arc, owners in arc_owners.items() if len(owners) == 1)
        for ln in exclusive_lines:
            (owner,) = line_owners[ln]
            entry = per_test[owner]
            entry["exclusive_lines"] += 1
            entry["exclusive_line_refs"].append(f"{_relpath(filename, root)}:{ln}")
        for arc in exclusive_arcs:
            (owner,) = arc_owners[arc]
            entry = per_test[owner]
            entry["exclusive_arcs"] += 1
            entry["exclusive_arc_refs"].append(f"{_relpath(filename, root)}:{arc[0]}->{arc[1]}")
        file_rows.append(
            {
                "path": _relpath(filename, root),
                "covered_lines": len(line_owners),
                "exclusive_lines": len(exclusive_lines),
                "exclusive_arcs": len(exclusive_arcs),
            }
        )
    tests = sorted(per_test.values(), key=lambda t: t["node"])
    zero = [t for t in tests if not t["exclusive_lines"] and not t["exclusive_arcs"]]
    protected = [t for t in zero if t["protect_hits"]]
    deletable = [t for t in zero if not t["protect_hits"]]
    by_file: dict[str, dict] = {}
    for t in tests:
        group = by_file.setdefault(t["file"], {"test_file": t["file"], "n_tests": 0, "n_zero": 0})
        group["n_tests"] += 1
    for t in zero:
        by_file[t["file"]]["n_zero"] += 1
    groups = sorted(by_file.values(), key=lambda g: g["test_file"])
    for g in groups:
        g["zero_ratio"] = round(g["n_zero"] / g["n_tests"], 3) if g["n_tests"] else 0.0
    return {
        "usage_note": USAGE_NOTE,
        "zero_note": ZERO_NOTE,
        "full_run": {"command": FULL_RUN_COMMAND, "estimate": FULL_RUN_ESTIMATE},
        "summary": {
            "n_tests": len(tests),
            "n_files": len(files),
            "n_exclusive_lines": sum(t["exclusive_lines"] for t in tests),
            "n_exclusive_arcs": sum(t["exclusive_arcs"] for t in tests),
            "n_zero": len(zero),
            "n_protected": len(protected),
            "n_deletable": len(deletable),
        },
        "files": file_rows,
        "tests": tests,
        "candidates": {
            "deletable": [t["node"] for t in deletable],
            "protected": [
                {"node": t["node"], "protect_hits": t["protect_hits"]} for t in protected
            ],
        },
        "by_test_file": groups,
    }


def format_table(report: dict, data_file: str) -> str:
    """Render the report as a concise human-readable table."""
    out: list[str] = []
    out.append("# tools/test_contribution.py (DIET-2 T0)")
    out.append(f"# {report['usage_note']}")
    out.append(f"# {report['zero_note']}")
    s = report["summary"]
    out.append(
        f"# 数据: {data_file} | 测试: {s['n_tests']} | 源文件: {s['n_files']} | "
        f"独有行: {s['n_exclusive_lines']} | 独有弧: {s['n_exclusive_arcs']} | "
        f"零独有: {s['n_zero']} (可删候选 {s['n_deletable']}, 保留标注 {s['n_protected']})"
    )
    out.append(f"# 全量命令: {report['full_run']['command']}")
    out.append(f"# {report['full_run']['estimate']}")
    out.append("== 按源文件汇总 ==")
    out.append("path covered_lines exclusive_lines exclusive_arcs")
    for row in report["files"]:
        out.append(
            f"{row['path']} {row['covered_lines']} {row['exclusive_lines']} {row['exclusive_arcs']}"
        )
    out.append("== T1 候选：可删候选 (独有覆盖为零且无保留标注) ==")
    for node in report["candidates"]["deletable"]:
        out.append(node)
    if not report["candidates"]["deletable"]:
        out.append("(无)")
    out.append("== T1 候选：保留标注 (单独列出，不进入可删清单) ==")
    for item in report["candidates"]["protected"]:
        out.append(f"{item['node']} protect={','.join(item['protect_hits'])}")
    if not report["candidates"]["protected"]:
        out.append("(无)")
    out.append("== 按测试文件分组 (候选占比) ==")
    out.append("test_file n_tests n_zero zero_ratio")
    for g in report["by_test_file"]:
        out.append(f"{g['test_file']} {g['n_tests']} {g['n_zero']} {g['zero_ratio']:.3f}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Entry point: build the report from a coverage data file."""
    parser = argparse.ArgumentParser(
        description=(
            f"计算每个测试独有覆盖的行和分支并输出 T1 候选报告 (DIET-2 T0)。{USAGE_NOTE}{ZERO_NOTE}"
        ),
        epilog=(
            "数据生成：先运行 pytest --cov=confflow --cov-context=test "
            "产生覆盖数据文件，再用本脚本分析。推荐的全量命令： "
            f"{FULL_RUN_COMMAND} 。{FULL_RUN_ESTIMATE}"
        ),
    )
    parser.add_argument(
        "--data-file", default=None, help="覆盖数据文件 (默认自动找 .coverage 或 .coverage_temp)"
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--output", default=None, help="报告输出路径 (默认 stdout)")
    parser.add_argument("--root", default=".", help="仓库根目录 (用于显示相对路径)")
    parser.add_argument(
        "--protect",
        action="append",
        default=[],
        help="追加保留关键词 (逗号分隔，可重复；默认表: "
        + ",".join(DEFAULT_PROTECT_KEYWORDS)
        + ")",
    )
    args = parser.parse_args(argv)
    data_file = resolve_data_file(args.data_file)
    data = load_data(data_file)
    keywords = parse_protect_args(args.protect)
    report = build_report(data, keywords, os.path.abspath(args.root))
    if args.format == "json":
        text = json.dumps(report, indent=2, sort_keys=False) + "\n"
    else:
        text = format_table(report, data_file)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
