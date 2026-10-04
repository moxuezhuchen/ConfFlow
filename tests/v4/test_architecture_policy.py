"""Tests for the declarative static architecture policy (L0.4b, v2).

Every one of the 69 L0.4b rules is covered through the real ``scan()`` entry
point with its own violating fixture tree (the rule id must appear in the
violations) and its own clean fixture tree (the rule id must be absent).
Rules whose mechanism is not a tree scan (const/meta/metric) get dedicated
evidence: correct data passes, an injected corruption fails.

The old guards stay in place and keep running; this module never imports them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.architecture_policy import (
    FORBIDDEN_SYMBOLS,
    METRIC_V4_ROOTS,
    RETIRED_V1_V2_WIRE_TOKENS,
    RULE_COUNT,
    RULES,
    V46_NEW_SYMBOLS,
    check_const_rules,
    code_text,
    filename_pairing_offenders,
    metrics_snapshot,
    range_ordinal_pairing_offenders,
    scan,
    task_dispatch_offenders,
)

CASES = Path(__file__).resolve().parent / "architecture_policy_cases"
V46_STRICT_CANDIDATES = [
    "domain",
    "workflow/v4",
    "execution",
    "analysis",
    "producer",
    "persistence",
    "programs",
    "remote",
]
NON_SCAN_KINDS = {"const", "meta_case", "metric"}
SCAN_IDS = sorted(r["id"] for r in RULES if r["kind"] not in NON_SCAN_KINDS)


def _rule(rule_id: str) -> dict:
    return next(rule for rule in RULES if rule["id"] == rule_id)


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fixture_scope(rule: dict) -> list[str]:
    scope = rule.get("scope") or list(rule.get("files", []))
    if scope == "__v46_strict_roots__":
        scope = [f"confflow/{name}" for name in V46_STRICT_CANDIDATES]
    return scope


def _anchor_file(scope: list[str]) -> str:
    entry = scope[0]
    if entry.endswith(".py"):
        return entry
    return entry.rstrip("/") + "/hurt.py"


def _hurt_import(rule: dict) -> str:
    mode = rule["mode"]
    if mode == "forbidden_prefixes":
        return f"import {rule['prefixes'][0]}"
    if mode == "forbidden_exact":
        return f"import {rule['exact'][0]}"
    if mode == "forbidden_substrings":
        return "import yaml"
    if mode == "allowed_prefixes":
        return "import confflow.legacy_disallowed"
    if mode == "allowed_exact":
        return "import confflow.science.confgen.engine"
    raise AssertionError(mode)


def _build_tree(root: Path, rule: dict, *, violate: bool) -> None:
    """Materialise the minimal fixture tree for one rule."""
    kind = rule["kind"]
    scope = _fixture_scope(rule)
    if kind == "imports":
        for entry in scope:
            if not entry.endswith(".py"):
                _write(root, entry.rstrip("/") + "/__init__.py", "")
        target = _anchor_file(scope)
        _write(root, target, _hurt_import(rule) if violate else "x = 1\n")
    elif kind == "symbols":
        symbol = rule["forbidden"][0]
        _write(root, _anchor_file(scope), f"x = {symbol}\n" if violate else "x = 1\n")
    elif kind == "vocab":
        token = rule["tokens"][0]
        allowed = set(rule.get("allowed_files", []))
        raw = rule.get("raw_text", False)
        if violate:
            outside = next(f for f in scope if f not in allowed)
            target = outside if outside.endswith(".py") else outside.rstrip("/") + "/hurt.py"
            if raw:
                _write(root, target, f'"""Docstring mentioning {token}."""\n')
            else:
                _write(root, target, f'keep = "{token}"\n')
        else:
            inside = next(iter(allowed), None)
            _write(root, inside or _anchor_file(scope), "x = 1\n")
    elif kind == "patterns":
        _write(root, _anchor_file(scope), "x = TaskRunner\n" if violate else "x = 1\n")
    elif kind == "custom_task_dispatch":
        body = (
            "from enum import Enum\n\n\n"
            "class TaskName(str, Enum):\n    IRC = 'irc'\n\n\n"
            "def run(task):\n    if task == TaskName.IRC:\n        return 1\n    return 0\n"
        )
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_filename_idioms":
        body = "import os\n\n\ndef pair(left, right):\n    return os.path.basename(left)\n"
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_range_ordinal":
        body = (
            "def pair(left, right):\n    out = []\n"
            "    for i in range(len(left)):\n        out.append((left[i], right[i]))\n"
            "    return out\n"
        )
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_numeric_dispatch":
        body = "def first(programs):\n    return programs[0]\n"
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_jobdesk_doubles":
        if violate:
            _write(root, rule["files"][0], "class JobdeskDouble:\n    import confflow\n")
        else:
            _write(root, rule["files"][0], "class JobdeskDouble:\n    pass\n")
    elif kind == "disk_absent":
        if violate:
            for module in rule.get("modules", []):
                _write(root, module.replace(".", "/") + ".py", "x = 1\n")
            for directory in rule.get("dirs", []):
                (root / directory).mkdir(parents=True, exist_ok=True)
    elif kind == "disk_inventory":
        removed = set(rule["removed"])
        for module in rule["forbidden"]:
            if violate and module in removed:
                _write(root, module.replace(".", "/") + ".py", "x = 1\n")
            elif not violate and module not in removed:
                _write(root, module.replace(".", "/") + ".py", "x = 1\n")
    elif kind == "coverage":
        # The module paths are pinned; the violating variant shrinks the roots
        # through a data copy (see the test), so the files always exist here.
        for module in rule["modules"]:
            _write(root, module.replace(".", "/") + ".py", "x = 1\n")
    elif kind == "disk_present":
        if not violate:
            for module in rule.get("modules", []):
                _write(root, module.replace(".", "/") + ".py", "x = 1\n")
            for file in rule.get("files", []):
                _write(root, file, "x = 1\n")
    else:
        raise AssertionError(f"unexpected scan kind: {kind}")


def _scan_rule(tmp_path: Path, rule: dict) -> list[str]:
    import tools.architecture_policy as policy

    saved = policy.RULES
    try:
        policy.RULES = [rule]
        return [v["rule"] for v in policy.scan(tmp_path)]
    finally:
        policy.RULES = saved


# ---------------------------------------------------------------------------
# Per-rule scan-entry coverage (69 L0.4b rules minus the non-scan kinds).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rule_id", SCAN_IDS)
def test_rule_fires_on_violating_fixture(rule_id: str, tmp_path: Path) -> None:
    rule = dict(_rule(rule_id))
    if rule["id"] == "AP-056":
        # structurally-clean data: violate through a shrunken-roots data copy
        rule["roots"] = ["confflow/nowhere"]
    _build_tree(tmp_path, rule, violate=True)
    fired = _scan_rule(tmp_path, rule)
    assert rule_id in fired, f"{rule_id} did not fire on its violating fixture: {fired}"


@pytest.mark.parametrize("rule_id", SCAN_IDS)
def test_rule_stays_silent_on_clean_fixture(rule_id: str, tmp_path: Path) -> None:
    _build_tree(tmp_path, _rule(rule_id), violate=False)
    fired = _scan_rule(tmp_path, _rule(rule_id))
    assert rule_id not in fired, f"{rule_id} flagged its own clean fixture: {fired}"


# ---------------------------------------------------------------------------
# Prose positives: the banned item appears only in a docstring/comment, so
# every non-raw rule must stay silent through the real scan() entry.
# (raw_text rules keep their original attribute: prose IS scanned there.)
# ---------------------------------------------------------------------------


def _banned_item(rule: dict) -> str:
    kind = rule["kind"]
    if kind == "imports":
        if rule["mode"] == "forbidden_prefixes":
            return rule["prefixes"][0]
        if rule["mode"] == "forbidden_substrings":
            return rule["substrings"][0]
        if rule["mode"] == "allowed_prefixes":
            return "confflow.legacy_disallowed"
        return "confflow.science.confgen.engine"
    if kind == "symbols":
        return rule["forbidden"][0]
    if kind == "vocab":
        return rule["tokens"][0]
    if kind == "patterns":
        return "TaskRunner"
    if kind == "custom_task_dispatch":
        return "IRC"
    return "basename"


PROSE_KINDS = {
    "imports",
    "symbols",
    "vocab",
    "patterns",
    "custom_task_dispatch",
    "custom_filename_idioms",
    "custom_range_ordinal",
    "custom_numeric_dispatch",
}
PROSE_IDS = sorted(r["id"] for r in RULES if r["kind"] in PROSE_KINDS and not r.get("raw_text"))


@pytest.mark.parametrize("rule_id", PROSE_IDS)
def test_rule_ignores_prose_in_docstring_and_comment(rule_id: str, tmp_path: Path) -> None:
    rule = dict(_rule(rule_id))
    scope = _fixture_scope(rule)
    item = _banned_item(rule)
    for entry in scope:
        if not entry.endswith(".py"):
            _write(root=tmp_path, relpath=entry.rstrip("/") + "/__init__.py", content="")
    target = _anchor_file(scope)
    _write(
        tmp_path, target, f'"""Prose mentions {item} here."""\n# and {item} in a comment\nx = 1\n'
    )
    fired = _scan_rule(tmp_path, rule)
    assert rule_id not in fired, f"{rule_id} flagged prose-only usage: {fired}"


# ---------------------------------------------------------------------------
# Constant / meta / metric rules: dedicated failure and success evidence.
# ---------------------------------------------------------------------------


def test_ap027_const_rules_clean_on_real_tree() -> None:
    assert check_const_rules(Path(__file__).resolve().parents[2]) == []


def test_ap027_const_rules_detect_corrupted_v3_kernel(tmp_path: Path) -> None:
    # The actual guarded authority is INTERNAL_ONLY_V3_MIGRATION_MODULES in the
    # guard source; a non-empty table (plus its module on disk) must be reported.
    guard = tmp_path / "tests/v4/test_architecture_boundaries.py"
    _write(
        tmp_path,
        str(guard.relative_to(tmp_path)),
        'INTERNAL_ONLY_V3_MIGRATION_MODULES = ("confflow.config.canonical.v3_adapter",)\n',
    )
    _write(tmp_path, "confflow/config/canonical/v3_adapter.py", "x = 1\n")
    problems = check_const_rules(tmp_path)
    assert problems and problems[0].startswith("AP-027"), problems


def test_ap033a_const_kernel_data_detects_corruption(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/v4/test_architecture_boundaries.py",
        'V1_MIGRATION_MODULES = ("confflow.workflow.v1",)\n'
        'V2_MIGRATION_MODULES = ("confflow.config.canonical.v2_adapter",)\n',
    )
    _write(tmp_path, "confflow/workflow/v1.py", "x = 1\n")
    problems = check_const_rules(tmp_path)
    assert any(p.startswith("AP-033a") for p in problems), problems


def test_ap070_const_rules_detect_table_overlap(tmp_path: Path) -> None:
    _write(tmp_path, "tests/v4/test_architecture_boundaries.py", 'FORBIDDEN_SYMBOLS = ("iprog",)\n')
    _write(tmp_path, "tests/v4/test_v46_debt.py", 'V46_NEW_SYMBOLS = ("iprog",)\n')
    problems = check_const_rules(tmp_path)
    assert any(p.startswith("AP-070") for p in problems), problems


def test_ap025_public_v3_wire_modules_are_not_importable() -> None:
    import importlib

    from tools.architecture_policy import PUBLIC_V3_WIRE_MODULES

    for module in PUBLIC_V3_WIRE_MODULES:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{module} is retired but importable")


def test_ap035_current_protocol_majors_are_not_retired() -> None:
    from confflow.config.contract_schemas import (
        CONFIGURATION_VALIDATION_SCHEMA,
        EDITOR_MANIFEST_SCHEMA,
        RECIPE_CATALOG_SCHEMA,
    )
    from confflow.producer.contract import ANALYSIS_REACTION_PROFILE_CONTRACT
    from confflow.remote.envelope import HANDOFF_SCHEMA_V3, RESULT_SCHEMA_V3

    current = (
        CONFIGURATION_VALIDATION_SCHEMA,
        EDITOR_MANIFEST_SCHEMA,
        RECIPE_CATALOG_SCHEMA,
        ANALYSIS_REACTION_PROFILE_CONTRACT,
        HANDOFF_SCHEMA_V3,
        RESULT_SCHEMA_V3,
    )
    banned = sorted(item for item in current if item in RETIRED_V1_V2_WIRE_TOKENS)
    assert banned == []


def test_ap058_meta_task_dispatch_fixture_is_flagged() -> None:
    source = CASES.joinpath("negative/case_task_dispatch.py").read_text(encoding="utf-8")
    offenders = task_dispatch_offenders(source)
    assert offenders and offenders[0][1] == "IRC"


def test_ap059_meta_scientific_strings_are_ignored() -> None:
    source = CASES.joinpath("positive/case_scientific_strings.py").read_text(encoding="utf-8")
    assert task_dispatch_offenders(source) == []


def test_ap061_meta_pairing_fixtures_are_flagged() -> None:
    assert filename_pairing_offenders(
        CASES.joinpath("negative/case_filename_pairing.py").read_text(encoding="utf-8")
    )
    assert range_ordinal_pairing_offenders(
        CASES.joinpath("negative/case_ordinal_pairing.py").read_text(encoding="utf-8")
    )


def test_ap070_new_symbols_are_disjoint_from_forbidden_symbols() -> None:
    assert set(V46_NEW_SYMBOLS).isdisjoint(set(FORBIDDEN_SYMBOLS))


# ---------------------------------------------------------------------------
# Metrics rules (AP-093/094/095): recorded, never asserted (U8).
# ---------------------------------------------------------------------------


def test_ap093_code_text_blanks_docstrings_and_comments_in_place() -> None:
    source = '"""prose with TaskRunner"""\nvalue = result.xyz  # prose with output_path\n'
    text = code_text(source)
    assert "result.xyz" in text  # code and non-docstring strings survive
    assert "TaskRunner" not in text  # docstring blanked
    assert "output_path" not in text  # comment blanked
    assert text.count("\n") == source.count("\n")  # line structure preserved


def test_ap094_v4_roots_are_pinned() -> None:
    assert METRIC_V4_ROOTS == [
        "confflow.v4cli",
        "confflow.application.v4_entry",
        "confflow.application.execution.workflow_adapter",
        "confflow.control_worker",
    ]


def test_ap095_metrics_match_the_old_collect_exactly() -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "metrics_reference", Path("scripts/architecture_metrics.py").resolve()
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["metrics_reference"] = module
    spec.loader.exec_module(module)
    root = Path(__file__).resolve().parents[2]
    old = module.collect(str(root))
    new = metrics_snapshot(root)
    for key in (
        "PHYSICAL_PRODUCTION_MODULES",
        "PHYSICAL_PRODUCTION_LOC",
        "V4_REACHABLE_MODULES",
        "V4_REACHABLE_LOC",
        "LEGACY_OR_NON_V4_REACHABLE_LOC",
    ):
        assert new[key] == old[key], key
    assert new["v4_reachable"] == old["v4_reachable"]


def test_ap095_metrics_missing_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        metrics_snapshot(tmp_path)


# ---------------------------------------------------------------------------
# Full-tree scan + inventory sanity.
# ---------------------------------------------------------------------------


def test_full_tree_scan_is_clean() -> None:
    assert scan(Path(__file__).resolve().parents[2]) == []


def test_rule_count_matches_the_inventory() -> None:
    # 69 L0.4b rows + AP-033a (the const half of inventory row #33, split out
    # honestly from the disk half AP-033 per the v3 root ruling).
    assert RULE_COUNT == 70


def test_rule_ids_are_unique_and_sources_pinned() -> None:
    assert len({r["id"] for r in RULES}) == RULE_COUNT
    assert all(re.match(r"^AP-\d{3}a?$", r["id"]) for r in RULES)  # AP-033a = const half of #33
    assert all(re.match(r"^#\d+$", r["source"]) for r in RULES)


def test_no_g13_g14_rules_are_enabled() -> None:
    assert not [r for r in RULES if "G13" in r["id"] or "G14" in r["id"]]
