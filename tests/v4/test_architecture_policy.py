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
import shutil
import subprocess as _sp
import sys
from pathlib import Path

import pytest

import tools.architecture_policy as _policy
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
_REAL_ROOT = Path(__file__).resolve().parents[2]
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


def _copy_scanner_tree(dest: Path) -> None:
    """Copy the real thin scanner plus its policy authority into a fixture.

    The thin ``scripts/v4_arch_scan.py`` loads ``tools/architecture_policy``
    from its own tree, so a fixture must carry both files; otherwise the
    fixture would silently bind back to the real tree.  Every fixture that
    needs a working scanner uses this helper (never a script-only copy).
    """
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    (dest / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_REAL_ROOT / "scripts/v4_arch_scan.py", dest / "scripts/v4_arch_scan.py")
    shutil.copyfile(
        _REAL_ROOT / "tools/architecture_policy.py",
        dest / "tools/architecture_policy.py",
    )


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
    elif kind == "custom_confgen_axis_literals":
        _write(root, _anchor_file(scope), 'x = "rings"\n' if violate else "x = 1\n")
    elif kind == "custom_confgen_axis_attrs":
        body = "def f(o):\n    return o.rings\n"
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_confgen_component_imports":
        body = "from confflow.science.confgen.torsion import stage\n"
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_confgen_getattr_guard":
        body = (
            "def __getattr__(name):\n"
            '    if name in ("InheritedTorsionLock", "ExtraLock"):\n'
            "        from confflow.science.confgen.torsion.inherited import ExtraLock\n"
            "        return ExtraLock\n"
            "    raise AttributeError(name)\n"
        )
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_confgen_target_owner":
        body = "def as_kernel_target(t):\n    return t\n"
        _write(root, _anchor_file(scope), body if violate else "x = 1\n")
    elif kind == "custom_confgen_top_register":
        _write(root, _anchor_file(scope), 'register("x")\n' if violate else "x = 1\n")
    elif kind == "custom_loc_budget":
        # Minimal P0.2 budget: one catch-all subsystem plus the tests total.
        # Violating tree has more physical lines than the tiny limit; the
        # clean tree fits inside a generous limit.
        import json

        limit = 3 if violate else 10_000
        budget = {
            "subsystems": [{"id": "common", "patterns": [], "prod_loc_limit": limit}],
            "tests_total_loc_limit": 10_000,
            "grandfathered": [],
        }
        _write(root, "tools/loc_budget.json", json.dumps(budget))
        lines = "x = 1\n" * (6 if violate else 1)
        _write(root, "confflow/hurt.py", lines)
    elif kind == "custom_test_filename":
        # AP-108 only fires on NEW stage-named files: the fixture budget
        # carries an empty grandfathered list, so any matching name fires.
        import json

        budget = {
            "subsystems": [],
            "tests_total_loc_limit": 10_000,
            "grandfathered": [],
        }
        _write(root, "tools/loc_budget.json", json.dumps(budget))
        if violate:
            _write(root, "tests/v4/test_v45_brand_new.py", "x = 1\n")
        else:
            _write(root, "tests/v4/test_behavior_named.py", "x = 1\n")
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
# Runtime per-rule clean/mutant fixture trees (L0.4c-v2).
#
# Every entry is a COMPLETE minimal fixture for its rule: ``clean`` contains
# every module the rule's cases touch, so each case really executes and the
# rule must stay silent.  ``mutant`` re-declares exactly one file to inject a
# single target violation and nothing else.  The test isolates the rule under
# test by monkeypatching RUNTIME_RULES, so a violation can only come from the
# target rule (a shared fixture can never let an unrelated rule pass for it).
# ---------------------------------------------------------------------------

INIT = ""

_PUBLISH = "confflow.persistence.fsatomic.publish_bytes"
_FSYNC = "confflow.persistence.fsatomic.fsync_directory"
_PUBLISH_CONSUMERS = [
    "confflow/application/v4_run.py",
    "confflow/persistence/arbitration.py",
    "confflow/persistence/generation.py",
    "confflow/persistence/publication.py",
    "confflow/persistence/run_state.py",
    "confflow/producer/run_result.py",
]
_FSYNC_CONSUMERS = [
    "confflow/application/execution/workflow_adapter.py",
    "confflow/persistence/imports.py",
]
_RT038_CLEAN = {
    "confflow/persistence/fsatomic.py": "def publish_bytes(path, data):\n    pass\n\n\ndef fsync_directory(path):\n    pass\n"
}
_RT038_CLEAN.update(
    {rel: "from confflow.persistence.fsatomic import publish_bytes\n" for rel in _PUBLISH_CONSUMERS}
)
_RT038_CLEAN["confflow/application/execution/workflow_adapter.py"] = (
    "from confflow.persistence.fsatomic import fsync_directory, publish_bytes\n"
)
_RT038_CLEAN["confflow/persistence/imports.py"] = (
    "from confflow.persistence.fsatomic import fsync_directory\n"
)

_RT039_CLEAN = {
    "confflow/programs/_naming.py": "def sanitize_job_name(name):\n    return name\n",
    "confflow/programs/gaussian/rendering.py": (
        "from confflow.programs._naming import sanitize_job_name\n"
    ),
    "confflow/programs/orca/rendering.py": (
        "from confflow.programs._naming import sanitize_job_name\n"
    ),
}

_RT043_CLEAN = {
    "confflow/config/contract_schemas.py": (
        'CONFIGURATION_VALIDATION_SCHEMA = "confflow.configuration-validation.v1"\n'
        'EDITOR_MANIFEST_SCHEMA = "confflow.editor-manifest.v1"\n'
        'RECIPE_CATALOG_SCHEMA = "confflow.recipe-catalog.v1"\n'
    ),
    "confflow/producer/contract.py": (
        "from confflow.config.contract_schemas import CONFIGURATION_VALIDATION_SCHEMA\n"
    ),
    "confflow/producer/validation.py": (
        "from confflow.config.contract_schemas import CONFIGURATION_VALIDATION_SCHEMA\n"
        "VALIDATION_RESPONSE_SCHEMA = CONFIGURATION_VALIDATION_SCHEMA\n"
    ),
    "confflow/producer/manifest.py": (
        "from confflow.config.contract_schemas import EDITOR_MANIFEST_SCHEMA\n"
    ),
    "confflow/producer/recipes.py": (
        "from confflow.config.contract_schemas import RECIPE_CATALOG_SCHEMA\n"
    ),
}

_RT047_ENTRIES = [
    "confflow/v4cli.py",
    "confflow/application/__init__.py",
    "confflow/application/v4_entry.py",
    "confflow/control_worker.py",
]

_RT065_MODULES = [
    "confflow/execution/atom_mapping.py",
    "confflow/execution/execution_adapters.py",
    "confflow/execution/multi_output.py",
    "confflow/execution/named_structures.py",
    "confflow/execution/output_identity.py",
    "confflow/execution/profile_ensemble.py",
    "confflow/execution/profile_path_endpoints.py",
    "confflow/programs/gaussian/named.py",
    "confflow/programs/gaussian/path.py",
    "confflow/programs/orca/ensemble_parse.py",
    "confflow/programs/orca/goat.py",
    "confflow/programs/orca/neb.py",
    "confflow/programs/orca/path.py",
]

# RT-096 is a scanner gate; the fixture must carry the scanner itself.
_RT096_CLEAN: dict[str, str] = {}

RUNTIME_SCENARIOS: dict[str, dict[str, dict[str, str]]] = {
    "RT-016": {
        "clean": {"confflow/remote/__init__.py": INIT},
        "mutant": {"confflow/remote/__init__.py": "__all__ = ['lease']\nlease = 1\n"},
    },
    "RT-017": {
        "clean": {"confflow/domain/__init__.py": INIT, "confflow/core/__init__.py": INIT},
        "mutant": {"confflow/domain/__init__.py": "import confflow.core\n"},
    },
    "RT-018": {
        "clean": {
            "confflow/workflow/v4/__init__.py": INIT,
            "confflow/execution/__init__.py": INIT,
        },
        "mutant": {
            "confflow/workflow/v4/__init__.py": "import confflow.calc\n",
            "confflow/calc/__init__.py": INIT,
        },
    },
    "RT-019": {
        "clean": {
            "confflow/remote/handoff.py": INIT,
            "confflow/remote/staging.py": INIT,
            "confflow/remote/transport.py": INIT,
            "confflow/remote/worker.py": INIT,
        },
        "mutant": {
            "confflow/remote/handoff.py": "import confflow.remote.lease\n",
            "confflow/remote/lease.py": INIT,
        },
    },
    "RT-020": {
        "clean": {
            "confflow/v4cli.py": INIT,
            "confflow/application/__init__.py": INIT,
            "confflow/application/v4_entry.py": INIT,
            "confflow/application/execution/workflow_adapter.py": INIT,
            "confflow/control_worker.py": INIT,
        },
        "mutant": {
            "confflow/v4cli.py": "import confflow.fixture_agent\n",
            "confflow/fixture_agent.py": INIT,
        },
    },
    "RT-021": {
        # An in-tree confflow package so require_import_fail cannot fall
        # through to an installed confflow elsewhere on sys.path.
        "clean": {"confflow/__init__.py": INIT},
        "mutant": {
            "confflow/__init__.py": INIT,
            "confflow/workflow/engine.py": INIT,
        },
    },
    "RT-031": {
        "clean": {"confflow/__init__.py": INIT},
        "mutant": {"confflow/__init__.py": INIT, "confflow/core/types.py": INIT},
    },
    "RT-038": {
        "clean": dict(_RT038_CLEAN),
        "mutant": {
            "confflow/persistence/run_state.py": "def publish_bytes(path, data):\n    return 'copy'\n"
        },
    },
    "RT-039": {
        "clean": dict(_RT039_CLEAN),
        "mutant": {
            "confflow/programs/gaussian/rendering.py": "def sanitize_job_name(name):\n    return 'copy'\n"
        },
    },
    "RT-040": {
        "clean": {"confflow/producer/__init__.py": INIT},
        "mutant": {
            "confflow/producer/__init__.py": "import confflow.calc\n",
            "confflow/calc/__init__.py": INIT,
        },
    },
    "RT-041": {
        "clean": {"confflow/producer/__init__.py": INIT, "confflow/producer/contract.py": INIT},
        "mutant": {
            "confflow/producer/__init__.py": "import confflow.config.canonical\n",
            "confflow/config/canonical/__init__.py": INIT,
        },
    },
    "RT-042": {
        "clean": {"confflow/producer/__init__.py": INIT},
        "mutant": {
            "confflow/producer/__init__.py": "import confflow.calc.async_exec\n",
            "confflow/calc/async_exec.py": INIT,
        },
    },
    "RT-043": {
        "clean": dict(_RT043_CLEAN),
        # A same-valued but distinct string object: breaks the identity pin
        # without changing any value, so only RT-043's identity op can fire.
        "mutant": {
            "confflow/producer/validation.py": (
                "from confflow.config.contract_schemas import CONFIGURATION_VALIDATION_SCHEMA\n"
                'VALIDATION_RESPONSE_SCHEMA = "".join(["confflow.configuration", "-validation.v1"])\n'
            )
        },
    },
    "RT-044": {
        "clean": {
            "confflow/config/__init__.py": INIT,
            "confflow/config/contract_schemas.py": (
                'CONFIGURATION_VALIDATION_SCHEMA = "confflow.configuration-validation.v1"\n'
            ),
        },
        "mutant": {"confflow/config/__init__.py": "WorkflowConfig = 1\n"},
    },
    "RT-045": {
        "clean": {"confflow/core/__init__.py": INIT},
        "mutant": {
            "confflow/core/__init__.py": "import confflow.config\n",
            "confflow/config/__init__.py": INIT,
        },
    },
    "RT-046": {
        "clean": {
            "confflow/application/__init__.py": INIT,
            "confflow/application/execution/__init__.py": INIT,
        },
        "mutant": {
            "confflow/application/__init__.py": "import confflow.application.execution\n",
        },
    },
    "RT-047": {
        "clean": {rel: INIT for rel in _RT047_ENTRIES},
        "mutant": {
            "confflow/v4cli.py": "import confflow.config.canonical\n",
            "confflow/config/canonical/__init__.py": INIT,
        },
    },
    "RT-065": {
        "clean": {rel: INIT for rel in _RT065_MODULES},
        "mutant": {
            "confflow/execution/output_identity.py": "import confflow.config\n",
            "confflow/config/__init__.py": INIT,
        },
    },
    "RT-096": {
        "clean": dict(_RT096_CLEAN),
        "mutant": {"confflow/producer/evil.py": "x = TaskRunner\n"},
    },
}

# RT-096 clean/mutant needs the real scanner copied into the fixture tree.
_SCANNER_RT = {"RT-096"}


def _materialise(root: Path, files: dict[str, str], rule_id: str) -> None:
    for rel, content in files.items():
        _write(root, rel, content)
    # Fixture confflow must be a regular package tree: only leaf files are
    # declared, so fill every missing parent ``__init__.py`` with empty
    # content.  Existing files (e.g. a mutant init with explicit content)
    # are never overwritten, preserving scenario semantics.
    for rel in files:
        parent = Path(rel).parent
        if not parent.parts or parent.parts[0] != "confflow":
            continue
        for depth in range(1, len(parent.parts) + 1):
            pkg = Path(*parent.parts[:depth])
            init_rel = pkg / "__init__.py"
            if str(init_rel) == rel:
                continue
            init_path = root / init_rel
            if not init_path.exists():
                _write(root, str(init_rel), "")
    if rule_id in _SCANNER_RT:
        _copy_scanner_tree(root)


def test_runtime_fixture_isolated_from_absolute_host_pythonpath(
    tmp_path: Path, monkeypatch
) -> None:
    import os

    import tools.architecture_policy as policy

    host = tmp_path / "host"
    (host / "confflow/remote").mkdir(parents=True)
    (host / "confflow/__init__.py").write_text("")
    (host / "confflow/remote/__init__.py").write_text("__all__ = ['lease']\nlease = 1\n")
    host_abs = str(host.resolve())
    monkeypatch.setenv(
        "PYTHONPATH",
        host_abs + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
    )
    rule = next(r for r in policy.RUNTIME_RULES if r["id"] == "RT-016")
    monkeypatch.setattr(policy, "RUNTIME_RULES", [rule])
    scenario = RUNTIME_SCENARIOS["RT-016"]
    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    _materialise(clean_root, scenario["clean"], "RT-016")
    clean = scan_runtime(clean_root)
    assert clean == [], f"clean leaked to absolute host {host_abs}: {clean}"
    mutant_root = tmp_path / "mutant"
    mutant_root.mkdir()
    _materialise(mutant_root, scenario["clean"], "RT-016")
    _materialise(mutant_root, scenario["mutant"], "RT-016")
    mutant = scan_runtime(mutant_root)
    fired = [v for v in mutant if v["rule"] == "RT-016"]
    assert fired, f"mutant did not fire RT-016: {mutant}"
    assert not [v for v in fired if "source_binding" in str(v)], fired
    assert all(v.get("op") == "export_absent" for v in fired), fired
    assert not [v for v in fired if "error" in v], fired


@pytest.mark.parametrize("rule_id", sorted(RUNTIME_SCENARIOS))
def test_runtime_rule_clean_and_mutant(rule_id: str, tmp_path: Path, monkeypatch) -> None:
    import tools.architecture_policy as policy

    rule = next(r for r in policy.RUNTIME_RULES if r["id"] == rule_id)
    scenario = RUNTIME_SCENARIOS[rule_id]
    monkeypatch.setattr(policy, "RUNTIME_RULES", [rule])

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    _materialise(clean_root, scenario["clean"], rule_id)
    clean = scan_runtime(clean_root)
    assert [
        v for v in clean if v["rule"] == rule_id
    ] == [], f"clean fixture fired {rule_id}: {[v for v in clean if v['rule'] == rule_id]}"

    mutant_root = tmp_path / "mutant"
    mutant_root.mkdir()
    _materialise(mutant_root, scenario["clean"], rule_id)
    _materialise(mutant_root, scenario["mutant"], rule_id)
    mutant = scan_runtime(mutant_root)
    fired = [v for v in mutant if v["rule"] == rule_id]
    assert fired, f"mutant did not fire {rule_id}: {mutant}"
    # Every case must have really executed.  ``require_import_fail`` reports
    # ``error='importable'`` as its intended violation, so only genuine
    # exceptions (an AttributeError/ImportError, i.e. a broken fixture) count
    # as a false pass here.
    crashed = [
        v
        for v in mutant
        if "error" in v
        and not (v.get("op") == "require_import_fail" and v.get("error") == "importable")
    ]
    assert not crashed, f"steps crashed instead of asserting: {crashed}"
    if rule.get("kind") == "scanner_gate":
        # A scanner gate reports through its two stages, not through op.
        assert {v["stage"] for v in fired} == {"inproc", "cli"}, fired
        assert all("TaskRunner" in v["detail"] for v in fired), fired
    elif "legacy_debt" in rule:
        # RT-040 measures the imported legacy set, not a step op.
        assert all(v["stage"] == "debt" for v in fired), fired
        assert all("legacy import debt changed" in v["detail"] for v in fired), fired
    else:
        # The target violation must carry the rule's own op, not a crash.
        assert all(v.get("op") for v in fired), f"target violation has no op: {fired}"


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
    if kind == "custom_confgen_axis_literals":
        return "rings"
    if kind == "custom_confgen_axis_attrs":
        return ".rings"
    if kind == "custom_confgen_component_imports":
        return "confflow.science.confgen.torsion"
    if kind == "custom_confgen_getattr_guard":
        return "ExtraLock"
    if kind == "custom_confgen_target_owner":
        return "as_kernel_target"
    if kind == "custom_confgen_top_register":
        return "register"
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
    "custom_confgen_axis_literals",
    "custom_confgen_axis_attrs",
    "custom_confgen_component_imports",
    "custom_confgen_getattr_guard",
    "custom_confgen_target_owner",
    "custom_confgen_top_register",
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


# ---------------------------------------------------------------------------
# L0.4d1: the authoritative source of the const tables is the policy module of
# the tree UNDER INSPECTION (read through the AST helper), not the constants
# already imported into this process and not the legacy guard test modules.
# ---------------------------------------------------------------------------

POLICY_SOURCE_REL = "tools/architecture_policy.py"

# The clean, five-field policy source every const fixture starts from.  Each
# corruption fixture rewrites exactly one field, so a failure can only come
# from the targeted table.
CLEAN_POLICY_SOURCE = (
    "INTERNAL_ONLY_V3_MIGRATION_MODULES = []\n"
    "V1_MIGRATION_MODULES = []\n"
    'V2_MIGRATION_MODULES = ["confflow.config.canonical.v2_adapter"]\n'
    'FORBIDDEN_SYMBOLS = ("build_recipe_catalog(",)\n'
    'V46_NEW_SYMBOLS = ["iprog"]\n'
)

# field -> the rule id that must be violated when the field is absent or has a
# non-sequence value.  A missing field is never treated as an empty list.
CONST_FIELD_RULES = {
    "INTERNAL_ONLY_V3_MIGRATION_MODULES": "AP-027",
    "V1_MIGRATION_MODULES": "AP-033a",
    "V2_MIGRATION_MODULES": "AP-033a",
    "FORBIDDEN_SYMBOLS": "AP-070",
    "V46_NEW_SYMBOLS": "AP-070",
}


def _write_policy_source(root: Path, *, drop: str | None = None, override: str = "") -> None:
    """Write a complete clean policy source, minus ``drop``, plus ``override``."""
    lines = [
        line
        for line in CLEAN_POLICY_SOURCE.splitlines(keepends=True)
        if drop is None or not line.startswith(f"{drop} =")
    ]
    _write(root, POLICY_SOURCE_REL, "".join(lines) + override)


def test_ap027_const_rules_clean_on_real_tree() -> None:
    assert check_const_rules(Path(__file__).resolve().parents[2]) == []


def test_ap027_const_rules_clean_without_legacy_guard_sources(tmp_path: Path) -> None:
    # Deleting the old guard test sources from the inspected tree must not
    # break the check: the authority now lives in tools/architecture_policy.py.
    _write_policy_source(tmp_path)
    assert not (tmp_path / "tests/v4/test_architecture_boundaries.py").exists()
    assert not (tmp_path / "tests/v4/test_v46_debt.py").exists()
    assert check_const_rules(tmp_path) == []


def test_ap027_const_rules_detect_corrupted_v3_kernel(tmp_path: Path) -> None:
    # The authoritative INTERNAL_ONLY_V3_MIGRATION_MODULES table lives in the
    # policy source; a non-empty table (plus its module on disk) is reported.
    _write_policy_source(
        tmp_path,
        drop="INTERNAL_ONLY_V3_MIGRATION_MODULES",
        override='INTERNAL_ONLY_V3_MIGRATION_MODULES = ("confflow.config.canonical.v3_adapter",)\n',
    )
    _write(tmp_path, "confflow/config/canonical/v3_adapter.py", "x = 1\n")
    problems = check_const_rules(tmp_path)
    assert problems and problems[0].startswith("AP-027"), problems
    assert any("v3_adapter" in p for p in problems), problems


def test_ap033a_const_kernel_data_detects_corruption(tmp_path: Path) -> None:
    _write_policy_source(
        tmp_path,
        drop="V1_MIGRATION_MODULES",
        override='V1_MIGRATION_MODULES = ("confflow.workflow.v1",)\n',
    )
    _write(tmp_path, "confflow/workflow/v1.py", "x = 1\n")
    problems = check_const_rules(tmp_path)
    assert any(p.startswith("AP-033a") for p in problems), problems
    assert any("confflow.workflow.v1" in p for p in problems), problems


def test_ap033a_const_kernel_detects_wrong_v2_table(tmp_path: Path) -> None:
    _write_policy_source(
        tmp_path,
        drop="V2_MIGRATION_MODULES",
        override='V2_MIGRATION_MODULES = ["confflow.config.canonical.v1_adapter"]\n',
    )
    problems = check_const_rules(tmp_path)
    assert any(p.startswith("AP-033a") for p in problems), problems


def test_ap033a_const_kernel_detects_retired_module_on_disk(tmp_path: Path) -> None:
    # The listed V2 kernel is still exactly v2_adapter, but its module exists on
    # disk: AP-033a keeps that as an independent invariant.
    _write_policy_source(tmp_path)
    _write(tmp_path, "confflow/config/canonical/v2_adapter.py", "x = 1\n")
    problems = check_const_rules(tmp_path)
    assert any(p.startswith("AP-033a") and "on disk" in p for p in problems), problems


def test_ap070_const_rules_detect_table_overlap(tmp_path: Path) -> None:
    _write_policy_source(
        tmp_path, drop="FORBIDDEN_SYMBOLS", override='FORBIDDEN_SYMBOLS = ("iprog",)\n'
    )
    problems = check_const_rules(tmp_path)
    assert any(p.startswith("AP-070") for p in problems), problems


def test_ap070_const_rules_report_policy_source_tampering(tmp_path: Path) -> None:
    # Negative counterpart of the clean-without-guard-sources case: tampering
    # the inspected tree's policy source is reported even though this process
    # imported clean copies of the same constants.
    _write(tmp_path, POLICY_SOURCE_REL, "")
    _write(
        tmp_path,
        POLICY_SOURCE_REL,
        'V46_NEW_SYMBOLS = ["iprog"]\nFORBIDDEN_SYMBOLS = ["iprog"]\n',
    )
    problems = check_const_rules(tmp_path)
    assert any(p.startswith("AP-070") and "iprog" in p for p in problems), problems
    assert FORBIDDEN_SYMBOLS != ["iprog"], "in-process constant must stay clean"


@pytest.mark.parametrize("field,rule_id", sorted(CONST_FIELD_RULES.items()))
def test_const_rules_report_missing_policy_field(tmp_path: Path, field: str, rule_id: str) -> None:
    _write_policy_source(tmp_path, drop=field)
    problems = check_const_rules(tmp_path)
    assert any(p.startswith(rule_id) and field in p for p in problems), problems


@pytest.mark.parametrize("field,rule_id", sorted(CONST_FIELD_RULES.items()))
def test_const_rules_report_policy_field_type_error(
    tmp_path: Path, field: str, rule_id: str
) -> None:
    _write_policy_source(tmp_path, drop=field, override=f"{field} = 17\n")
    problems = check_const_rules(tmp_path)
    assert any(p.startswith(rule_id) and field in p for p in problems), problems


def test_const_rules_report_missing_policy_source(tmp_path: Path) -> None:
    problems = check_const_rules(tmp_path)
    assert problems, "absent policy source must violate the const rules"
    assert all(p.split(":")[0] in {"AP-027", "AP-033a", "AP-070"} for p in problems), problems


# ---------------------------------------------------------------------------
# L0.4d1-v2: non-string elements are never silently coerced or skipped.  Each
# of the five const tables must contain only strings; a None/int/list/dict
# element is reported with the field's rule id and field name.
# ---------------------------------------------------------------------------


def _element_override(field: str, element) -> str:
    return f"{field} = {[element]!r}\n"


@pytest.mark.parametrize("field,rule_id", sorted(CONST_FIELD_RULES.items()))
@pytest.mark.parametrize(
    "bad", [None, 17, ["iprog"], {"bad": 1}], ids=["None", "int", "list", "dict"]
)
def test_const_rules_report_policy_field_element_type_error(
    tmp_path: Path, field: str, rule_id: str, bad
) -> None:
    _write_policy_source(tmp_path, drop=field, override=_element_override(field, bad))
    problems = check_const_rules(tmp_path)
    assert any(
        p.startswith(rule_id) and field in p and "only strings" in p for p in problems
    ), problems


def test_ap070_const_rules_reject_malformed_nested_forbidden_element(
    tmp_path: Path,
) -> None:
    # Exact ROOT-MALFORMED-ELEMENT.json repro: full clean five fields except
    # FORBIDDEN_SYMBOLS=[["iprog"]] while V46_NEW_SYMBOLS=["iprog"].
    _write_policy_source(
        tmp_path, drop="FORBIDDEN_SYMBOLS", override='FORBIDDEN_SYMBOLS = [["iprog"]]\n'
    )
    problems = check_const_rules(tmp_path)
    assert problems, "malformed nested element must not silently pass"
    assert any(p.startswith("AP-070") and "FORBIDDEN_SYMBOLS" in p for p in problems), problems


CONST_CLEAN_ELEMENTS = {
    "INTERNAL_ONLY_V3_MIGRATION_MODULES": [],
    "V1_MIGRATION_MODULES": [],
    "V2_MIGRATION_MODULES": ["confflow.config.canonical.v2_adapter"],
    "FORBIDDEN_SYMBOLS": ["build_recipe_catalog("],
    "V46_NEW_SYMBOLS": ["iprog"],
}


@pytest.mark.parametrize("field", sorted(CONST_CLEAN_ELEMENTS))
@pytest.mark.parametrize("container", ["list", "tuple"])
def test_const_rules_accept_valid_string_sequences(
    tmp_path: Path, field: str, container: str
) -> None:
    elements = CONST_CLEAN_ELEMENTS[field]
    if container == "list":
        override = f"{field} = {elements!r}\n"
    else:
        override = f"{field} = {tuple(elements)!r}\n"
    _write_policy_source(tmp_path, drop=field, override=override)
    assert check_const_rules(tmp_path) == []


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


def test_ap095_metrics_match_the_old_collect_exactly(tmp_path: Path) -> None:
    # Independent synthetic tree with hand-computed expectations (no
    # self-proof through the shared authority): three production modules,
    # one import edge v4cli -> producer.contract, one orphan module.
    # LOC counts every line including the trailing newline.
    _write(tmp_path, "confflow/__init__.py", "")
    _write(tmp_path, "confflow/v4cli.py", "import confflow.producer.contract\n")
    _write(tmp_path, "confflow/producer/__init__.py", "")
    _write(tmp_path, "confflow/producer/contract.py", "x = 1\n")
    _write(tmp_path, "confflow/orphan.py", "x = 1\ny = 2\n")
    # Stubs for the remaining pinned V4 roots (missing roots fail closed).
    _write(tmp_path, "confflow/application/__init__.py", "")
    _write(tmp_path, "confflow/application/v4_entry.py", "")
    _write(tmp_path, "confflow/application/execution/__init__.py", "")
    _write(tmp_path, "confflow/application/execution/workflow_adapter.py", "")
    _write(tmp_path, "confflow/control_worker.py", "")
    got = metrics_snapshot(tmp_path)
    assert got["PHYSICAL_PRODUCTION_MODULES"] == 10
    assert got["PHYSICAL_PRODUCTION_LOC"] == 4
    assert got["V4_REACHABLE_MODULES"] == 9
    assert got["V4_REACHABLE_LOC"] == 2
    assert got["LEGACY_OR_NON_V4_REACHABLE_LOC"] == 2
    assert got["v4_reachable"] == [
        "confflow",
        "confflow.application",
        "confflow.application.execution",
        "confflow.application.execution.workflow_adapter",
        "confflow.application.v4_entry",
        "confflow.control_worker",
        "confflow.producer",
        "confflow.producer.contract",
        "confflow.v4cli",
    ]


def test_ap095_metrics_thin_collect_matches_snapshot_on_real_tree() -> None:
    # The thin script forwards to the same authority: real-tree/synthetic
    # parity is checked against the frozen old algorithm in the output
    # reference diff log, not by calling the same authority twice.
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "metrics_thin", str(_REAL_ROOT / "scripts/architecture_metrics.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["metrics_thin"] = module
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
    # honestly from the disk half AP-033 per the v3 root ruling) + 6 A6
    # G13/G14 confgen purity rules (AP-100..AP-105) + 1 L1-A3c intent
    # science isolation rule (AP-106, tool guard only) + 2 DIET-2 P0.2 rules
    # (AP-107 LOC budget, AP-108 G16 new-filename guard).
    assert RULE_COUNT == 79


def test_rule_ids_are_unique_and_sources_pinned() -> None:
    assert len({r["id"] for r in RULES}) == RULE_COUNT
    assert all(re.match(r"^AP-\d{3}a?$", r["id"]) for r in RULES)  # AP-033a = const half of #33
    assert all(re.match(r"^#\d+$", r["source"]) or r["source"] == "DIET-2 P0.2" for r in RULES)


def test_g13_g14_rules_are_enabled() -> None:
    assert sorted(
        r["id"]
        for r in RULES
        if r["id"] in ("AP-100", "AP-101", "AP-102", "AP-103", "AP-104", "AP-105")
    ) == [
        "AP-100",
        "AP-101",
        "AP-102",
        "AP-103",
        "AP-104",
        "AP-105",
    ]


# ---------------------------------------------------------------------------
# DIET-2 P0.2: LOC budget (AP-107) and G16 new-filename guard (AP-108).
#
# AP-108 only blocks NEW stage-named files: historical matches are exempt
# through the grandfathered list in tools/loc_budget.json (the full set of
# matches existing at P0.2). That list may only shrink — deleting a
# historical file removes its entry; adding entries to excuse new files is
# forbidden. New tests use behavior names and merge into existing files.
# All scan() calls below pass rule_ids limited to the new rules, so they
# never depend on the AP-081 /opt/jobdesk sibling checkout.
# ---------------------------------------------------------------------------

P02_RULE_IDS = ["AP-107", "AP-108"]


def _write_p02_budget(
    root: Path,
    *,
    subsystems: list[dict],
    tests_limit: int,
    grandfathered: list[str],
) -> None:
    import json

    root.joinpath("tools").mkdir(parents=True, exist_ok=True)
    root.joinpath("tools/loc_budget.json").write_text(
        json.dumps(
            {
                "subsystems": subsystems,
                "tests_total_loc_limit": tests_limit,
                "grandfathered": grandfathered,
            }
        ),
        encoding="utf-8",
    )


def test_p02_new_rules_are_registered() -> None:
    kinds = {r["id"]: r["kind"] for r in RULES if r["id"] in P02_RULE_IDS}
    assert kinds == {"AP-107": "custom_loc_budget", "AP-108": "custom_test_filename"}


def test_p02_current_tree_is_clean_for_new_rules() -> None:
    root = Path(__file__).resolve().parents[2]
    assert scan(root, rule_ids=P02_RULE_IDS) == []


def test_p02_grandfathered_covers_every_current_match() -> None:
    import json
    import re

    root = Path(__file__).resolve().parents[2]
    budget = json.loads((root / "tools/loc_budget.json").read_text(encoding="utf-8"))
    current = sorted(
        str(p.relative_to(root))
        for p in (root / "tests").rglob("*.py")
        if "__pycache__" not in p.parts
        and (
            re.match(r"test_v4[0-9]_.*\.py$", p.name)
            or (p.name.endswith(".py") and "_coverage" in p.name)
        )
    )
    assert current, "expected historical stage-named tests to exist"
    assert set(current) <= set(budget["grandfathered"]), sorted(
        set(current) - set(budget["grandfathered"])
    )


def test_p02_over_budget_subsystem_fires(tmp_path: Path) -> None:
    _write_p02_budget(
        tmp_path,
        subsystems=[{"id": "common", "patterns": [], "prod_loc_limit": 3}],
        tests_limit=10_000,
        grandfathered=[],
    )
    _write(tmp_path, "confflow/hurt.py", "x = 1\n" * 6)
    fired = [v for v in scan(tmp_path, rule_ids=["AP-107"]) if v["rule"] == "AP-107"]
    assert fired, "a subsystem over its budget must fail AP-107"
    assert any("common" in v["detail"] and "over budget" in v["detail"] for v in fired)


def test_p02_over_budget_tests_total_fires(tmp_path: Path) -> None:
    _write_p02_budget(
        tmp_path,
        subsystems=[],
        tests_limit=2,
        grandfathered=[],
    )
    _write(tmp_path, "confflow/hurt.py", "x = 1\n")
    _write(tmp_path, "tests/test_behavior_named.py", "x = 1\n" * 5)
    fired = [v for v in scan(tmp_path, rule_ids=["AP-107"]) if v["rule"] == "AP-107"]
    assert fired, "a tests total over its budget must fail AP-107"
    assert any("tests total" in v["detail"] and "over budget" in v["detail"] for v in fired)


def test_p02_new_stage_named_files_fire(tmp_path: Path) -> None:
    _write_p02_budget(
        tmp_path, subsystems=[], tests_limit=10_000, grandfathered=["tests/v4/test_v45_old.py"]
    )
    _write(tmp_path, "tests/v4/test_v45_old.py", "x = 1\n")
    _write(tmp_path, "tests/v4/test_v47_brand_new.py", "x = 1\n")
    _write(tmp_path, "tests/v4/test_engine_recovery_coverage.py", "x = 1\n")
    _write(tmp_path, "tests/v4/test_behavior_named.py", "x = 1\n")
    fired = [v for v in scan(tmp_path, rule_ids=["AP-108"]) if v["rule"] == "AP-108"]
    paths = sorted(v["path"] for v in fired)
    assert "tests/v4/test_v47_brand_new.py" in paths
    assert "tests/v4/test_engine_recovery_coverage.py" in paths
    assert "tests/v4/test_v45_old.py" not in paths, "grandfathered names must stay silent"
    assert "tests/v4/test_behavior_named.py" not in paths


def test_p02_missing_budget_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/hurt.py", "x = 1\n")
    fired_107 = [v for v in scan(tmp_path, rule_ids=["AP-107"]) if v["rule"] == "AP-107"]
    fired_108 = [v for v in scan(tmp_path, rule_ids=["AP-108"]) if v["rule"] == "AP-108"]
    assert fired_107 and fired_108, "a missing budget file must fail closed, never pass"


# ---------------------------------------------------------------------------
# Runtime import isolation (L0.4c): every rule must fire on its own
# violating synthetic fixture tree through the real scan_runtime() entry
# (fresh subprocess per case), and stay silent on the real tree.
# ---------------------------------------------------------------------------

from tools.architecture_policy import RUNTIME_RULES, scan_runtime  # noqa: E402

RUNTIME_IDS = sorted(r["id"] for r in RUNTIME_RULES)

_REAL_ROOT = Path(__file__).resolve().parents[2]

INIT = ""

RUNTIME_NEGATIVE_FIXTURES: dict[str, dict[str, str]] = {
    "RT-016": {
        "confflow/remote/__init__.py": '__all__ = ["lease"]\nlease = 1\n',
    },
    "RT-017": {
        "confflow/domain/__init__.py": "import confflow.core\n",
        "confflow/core/__init__.py": INIT,
    },
    "RT-018": {
        "confflow/workflow/__init__.py": INIT,
        "confflow/workflow/v4/__init__.py": "import confflow.workflow.v3_runtime\n",
        "confflow/workflow/v3_runtime/__init__.py": INIT,
        "confflow/execution/__init__.py": INIT,
        "confflow/config/__init__.py": INIT,
    },
    "RT-019": {
        "confflow/remote/__init__.py": INIT,
        "confflow/remote/handoff.py": "import confflow.remote.lease\n",
        "confflow/remote/lease.py": INIT,
    },
    "RT-020": {
        "confflow/v4cli.py": "import confflow.fixture_agent\n",
        "confflow/fixture_agent.py": INIT,
        "confflow/application/__init__.py": INIT,
        "confflow/application/execution/__init__.py": INIT,
        "confflow/application/execution/workflow_adapter.py": INIT,
        "confflow/control_worker.py": INIT,
    },
    "RT-021": {
        "confflow/workflow/engine.py": INIT,
    },
    "RT-031": {
        "confflow/config/canonical.py": INIT,
    },
    "RT-038": {
        "confflow/persistence/__init__.py": INIT,
        "confflow/persistence/fsatomic.py": (
            "def publish_bytes(path, data):\n    raise NotImplementedError\n\n\n"
            "def fsync_directory(path):\n    raise NotImplementedError\n"
        ),
        "confflow/persistence/run_state.py": (
            "def publish_bytes(path, data):\n    return 'copy'\n"
        ),
    },
    "RT-039": {
        "confflow/programs/__init__.py": INIT,
        "confflow/programs/_naming.py": "def sanitize_job_name(name):\n    return name\n",
        "confflow/programs/gaussian/__init__.py": INIT,
        "confflow/programs/gaussian/rendering.py": (
            "def sanitize_job_name(name):\n    return 'copy'\n"
        ),
        "confflow/programs/orca/__init__.py": INIT,
        "confflow/programs/orca/rendering.py": (
            "from confflow.programs._naming import sanitize_job_name\n"
        ),
    },
    "RT-040": {
        "confflow/__init__.py": INIT,
        "confflow/producer/__init__.py": "import confflow.calc\n",
        "confflow/calc/__init__.py": INIT,
    },
    "RT-041": {
        "confflow/producer/__init__.py": "import confflow.config.canonical.models\n",
        "confflow/config/__init__.py": INIT,
        "confflow/config/canonical/__init__.py": INIT,
        "confflow/config/canonical/models.py": INIT,
    },
    "RT-042": {
        "confflow/producer/__init__.py": "import confflow.calc.async_exec\n",
        "confflow/calc/__init__.py": INIT,
        "confflow/calc/async_exec.py": INIT,
    },
    "RT-043": {
        "confflow/config/__init__.py": INIT,
        "confflow/config/contract_schemas.py": (
            'CONFIGURATION_VALIDATION_SCHEMA = "confflow.configuration-validation.v1"\n'
            'EDITOR_MANIFEST_SCHEMA = "confflow.editor-manifest.v1"\n'
            'RECIPE_CATALOG_SCHEMA = "confflow.recipe-catalog.v1"\n'
        ),
        "confflow/producer/__init__.py": INIT,
        "confflow/producer/contract.py": ('CONFIGURATION_VALIDATION_SCHEMA = "copy"\n'),
        "confflow/producer/manifest.py": (
            'EDITOR_MANIFEST_SCHEMA = "confflow.editor-manifest.v1"\n'
        ),
        "confflow/producer/recipes.py": ('RECIPE_CATALOG_SCHEMA = "confflow.recipe-catalog.v1"\n'),
        "confflow/producer/validation.py": (
            'VALIDATION_RESPONSE_SCHEMA = "confflow.configuration-validation.v1"\n'
        ),
    },
    "RT-044": {
        "confflow/config/__init__.py": "WorkflowConfig = 1\n",
    },
    "RT-045": {
        "confflow/core/__init__.py": "import confflow.config\n",
        "confflow/config/__init__.py": INIT,
    },
    "RT-046": {
        "confflow/application/__init__.py": "import confflow.application.execution.memory\n",
        "confflow/application/execution/__init__.py": INIT,
        "confflow/application/execution/memory.py": INIT,
    },
    "RT-047": {
        "confflow/v4cli.py": "import confflow.config.canonical.models\n",
        "confflow/config/__init__.py": INIT,
        "confflow/config/canonical/__init__.py": INIT,
        "confflow/config/canonical/models.py": INIT,
    },
    "RT-065": {
        "confflow/execution/output_identity.py": "import confflow.calc\n",
        "confflow/calc/__init__.py": INIT,
    },
    "RT-096": {
        # The scanner gate only watches its SCOPE entry paths (v4cli,
        # application, control, producer, remote, analysis) — not workflow/v4.
        "confflow/producer/__init__.py": INIT,
        "confflow/producer/evil.py": "x = TaskRunner\n",
    },
}


@pytest.mark.parametrize("rule_id", RUNTIME_IDS)
def test_runtime_rule_fires_on_violating_fixture(rule_id: str, tmp_path: Path) -> None:
    # The fixture confflow must be a REGULAR package (root __init__), otherwise
    # the namespace merge would resolve submodules from the real tree.
    _write(tmp_path, "confflow/__init__.py", "")
    for relpath, content in RUNTIME_NEGATIVE_FIXTURES[rule_id].items():
        _write(tmp_path, relpath, content)
    if rule_id == "RT-096":
        _copy_scanner_tree(tmp_path)
    fired = [v["rule"] for v in scan_runtime(tmp_path)]
    assert rule_id in fired, f"{rule_id} did not fire on its violating fixture: {fired}"


def test_runtime_rules_are_clean_on_the_real_tree() -> None:
    assert scan_runtime(_REAL_ROOT) == []


# ---------------------------------------------------------------------------
# L0.4c-v2 additions: timeout/launch handling, payload validation, source
# binding, and per-rule clean/mutant fixture coverage through scan_runtime.
# ---------------------------------------------------------------------------


def _blank_tree(tmp_path: Path) -> Path:
    (tmp_path / "confflow").mkdir(exist_ok=True)
    (tmp_path / "confflow/__init__.py").write_text("")
    return tmp_path


def _only(rt_id: str, monkeypatch) -> None:
    """Isolate one runtime rule so only it can report."""
    rule = next(r for r in _policy.RUNTIME_RULES if r["id"] == rt_id)
    monkeypatch.setattr(_policy, "RUNTIME_RULES", [rule])


# --- #1 timeouts and launch errors -----------------------------------------


def test_runtime_normal_case_timeout_is_a_violation(tmp_path: Path, monkeypatch) -> None:
    _blank_tree(tmp_path)
    _only("RT-016", monkeypatch)

    def boom(*a, **kw):
        raise _sp.TimeoutExpired(cmd=a[0], timeout=kw.get("timeout"))

    monkeypatch.setattr(_policy.subprocess, "run", boom)
    fired = scan_runtime(tmp_path)
    assert fired, "a case timeout must be reported, never skipped"
    for v in fired:
        assert v["rule"] == "RT-016", v
        assert v["stage"].startswith("case:"), v
        assert "timeout after" in v["detail"], v


def test_runtime_scanner_inproc_timeout_is_a_violation(tmp_path: Path, monkeypatch) -> None:
    _blank_tree(tmp_path)
    _only("RT-096", monkeypatch)
    seen: list[str] = []

    def boom(cmd, **kw):
        seen.append(cmd[1] if len(cmd) > 1 and cmd[1] == "-c" else cmd[-1])
        raise _sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"))

    monkeypatch.setattr(_policy.subprocess, "run", boom)
    fired = scan_runtime(tmp_path)
    assert [v for v in fired if v["stage"] == "inproc"], fired
    assert [v for v in fired if v["stage"] == "cli"], fired
    assert all(v["rule"] == "RT-096" for v in fired), fired
    assert all("timeout after" in v["detail"] for v in fired), fired


def test_runtime_scanner_cli_timeout_is_a_violation(tmp_path: Path, monkeypatch) -> None:
    _blank_tree(tmp_path)
    (tmp_path / "scripts").mkdir()
    _copy_scanner_tree(tmp_path)
    _only("RT-096", monkeypatch)
    real_run = _policy.subprocess.run

    def cli_only_timeout(cmd, **kw):
        if "-c" in cmd:
            return real_run(cmd, **kw)
        raise _sp.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout"))

    monkeypatch.setattr(_policy.subprocess, "run", cli_only_timeout)
    fired = scan_runtime(tmp_path)
    assert [v for v in fired if v["stage"] == "cli"], fired
    # The inproc gate ran for real and passed, so the CLI timeout is isolated.
    assert not [v for v in fired if v["stage"] == "inproc"], fired


def test_runtime_launch_error_is_a_violation(tmp_path: Path, monkeypatch) -> None:
    _blank_tree(tmp_path)
    _only("RT-016", monkeypatch)

    def boom(*a, **kw):
        raise OSError("no such interpreter")

    monkeypatch.setattr(_policy.subprocess, "run", boom)
    fired = scan_runtime(tmp_path)
    assert fired, "a launch failure must be reported, never skipped"
    assert all("launch failed" in v["detail"] for v in fired), fired


def test_runtime_scanner_launch_error_is_a_violation(tmp_path: Path, monkeypatch) -> None:
    _blank_tree(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/v4_arch_scan.py").write_text("")
    _only("RT-096", monkeypatch)

    def boom(*a, **kw):
        raise OSError("no such interpreter")

    monkeypatch.setattr(_policy.subprocess, "run", boom)
    fired = scan_runtime(tmp_path)
    assert {v["stage"] for v in fired} == {"inproc", "cli"}, fired
    assert all("launch failed" in v["detail"] for v in fired), fired


# --- #2 payload structure validation ---------------------------------------


class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


_BAD_PAYLOADS = [
    ("", "empty output"),
    ("not json at all\n", "non-JSON output"),
    ("null\n", "null top level"),
    ("[1, 2, 3]\n", "non-object top level"),
    ('"a string"\n', "string top level"),
    ("{}\n", "missing violations field"),
    ('{"violations": null}\n', "null violations"),
    ('{"violations": {}}\n', "object instead of list"),
    ('{"violations": "none"}\n', "string instead of list"),
    ('{"violations": [1]}\n', "non-object list element"),
    ('{"violations": [["op"]]}\n', "nested list element"),
    ('{"violations": [], "confflow_modules": 3}\n', "int confflow_modules"),
    ('{"violations": [], "confflow_modules": [1]}\n', "non-str module entry"),
    ('{"violations": [], "confflow_modules": "x"}\n', "string confflow_modules"),
]


@pytest.mark.parametrize("stdout,label", _BAD_PAYLOADS, ids=[p[1] for p in _BAD_PAYLOADS])
def test_runtime_bad_payload_becomes_violation(
    tmp_path: Path, monkeypatch, stdout: str, label: str
) -> None:
    _blank_tree(tmp_path)
    _only("RT-016", monkeypatch)
    monkeypatch.setattr(_policy.subprocess, "run", lambda *a, **kw: _FakeProc(stdout))
    fired = scan_runtime(tmp_path)
    assert fired, f"{label} must produce a violation, never a pass or a crash"
    assert all(v["rule"] == "RT-016" for v in fired), fired
    assert all("stage" in v and v["detail"] for v in fired), fired


def test_runtime_bad_payload_does_not_crash_scan(tmp_path: Path, monkeypatch) -> None:
    # The root counterexample: exit 0 with {"violations": null} raised TypeError.
    _blank_tree(tmp_path)
    _only("RT-016", monkeypatch)
    monkeypatch.setattr(
        _policy.subprocess,
        "run",
        lambda *a, **kw: _FakeProc('{"violations": null, "confflow_modules": null}\n'),
    )
    fired = scan_runtime(tmp_path)  # must not raise TypeError
    assert fired and "violations must be a list" in fired[0]["detail"], fired


def test_runtime_normal_payload_semantics_preserved(tmp_path: Path, monkeypatch) -> None:
    # A well-formed empty payload stays a pass for the rule under test.
    _blank_tree(tmp_path)
    _only("RT-016", monkeypatch)
    monkeypatch.setattr(
        _policy.subprocess,
        "run",
        lambda *a, **kw: _FakeProc('{"violations": [], "confflow_modules": null}\n'),
    )
    assert scan_runtime(tmp_path) == []


def test_runtime_real_violation_payload_is_reported(tmp_path: Path, monkeypatch) -> None:
    # A well-formed payload that carries a violation still reports it.
    _blank_tree(tmp_path)
    _only("RT-016", monkeypatch)
    monkeypatch.setattr(
        _policy.subprocess,
        "run",
        lambda *a, **kw: _FakeProc(
            '{"violations": [{"op": "forbid", "module": "confflow.core"}], '
            '"confflow_modules": ["confflow"]}\n'
        ),
    )
    fired = scan_runtime(tmp_path)
    assert [v for v in fired if v["rule"] == "RT-016" and v["op"] == "forbid"], fired


# --- #3 source binding (paired in-tree / outside-tree proof) ----------------


def _source_binding_fixture(tmp_path: Path, *, outside: bool) -> Path:
    """Build a tree whose confflow.domain either lives inside or outside it."""
    checked = tmp_path / ("checked" if not outside else "checked")
    (checked / "confflow/core").mkdir(parents=True)
    (checked / "confflow/__init__.py").write_text("")
    (checked / "confflow/core/__init__.py").write_text("")
    domain_body = "import confflow.core\n"
    if outside:
        real = tmp_path / "outside"
        (real / "confflow/domain").mkdir(parents=True)
        (real / "confflow/domain/__init__.py").write_text(domain_body)
        (checked / "confflow/domain").symlink_to(real / "confflow/domain", target_is_directory=True)
    else:
        (checked / "confflow/domain").mkdir(parents=True)
        (checked / "confflow/domain/__init__.py").write_text(domain_body)
    return checked


def test_runtime_source_binding_accepts_in_tree_source(tmp_path: Path, monkeypatch) -> None:
    # Paired positive: identical layout, no symlink -> the loaded confflow
    # source really lives under the checked tree, so no source_binding fires.
    checked = _source_binding_fixture(tmp_path, outside=False)
    _only("RT-017", monkeypatch)
    fired = scan_runtime(checked)
    assert not [v for v in fired if "source_binding" in str(v)], fired


def test_runtime_source_binding_rejects_outside_tree(tmp_path: Path, monkeypatch) -> None:
    # Paired negative: confflow.domain is a symlink to a tree-outside copy.
    checked = _source_binding_fixture(tmp_path, outside=True)
    _only("RT-017", monkeypatch)
    fired = scan_runtime(checked)
    binding = [v for v in fired if "source_binding" in str(v)]
    assert binding, fired
    assert all(v["rule"] == "RT-017" for v in binding), binding
    assert all("outside" in str(v) for v in binding), binding


def test_runtime_source_binding_rejects_scanner_from_outside_tree(
    tmp_path: Path, monkeypatch
) -> None:
    # The scanner module must come from the inspected tree's scripts/.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "v4_arch_scan.py").write_text(
        "def scan():\n    return []\n\n\ndef main():\n    print('OK: clean')\n    return 0\n"
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/v4_arch_scan.py").symlink_to(outside / "v4_arch_scan.py")
    _write(tmp_path, "confflow/__init__.py", INIT)
    _only("RT-096", monkeypatch)
    fired = scan_runtime(tmp_path)
    assert any("scanner module outside checked scripts" in v["detail"] for v in fired), fired
    assert any(v["stage"] == "inproc" for v in fired), fired
    assert any("scanner script outside checked scripts" in v["detail"] for v in fired), fired


def test_runtime_source_binding_clean_tree() -> None:
    # The real tree has no out-of-tree sources: no source-binding violations.
    violations = [v for v in scan_runtime(_REAL_ROOT) if "source_binding" in str(v)]
    assert violations == []


# --- RT-096 two gates verified separately ----------------------------------


def _scanner_fixture(tmp_path: Path, *, evil: bool) -> Path:
    (tmp_path / "confflow/producer").mkdir(parents=True)
    (tmp_path / "confflow/__init__.py").write_text("")
    (tmp_path / "confflow/producer/__init__.py").write_text("")
    _copy_scanner_tree(tmp_path)
    if evil:
        (tmp_path / "confflow/producer/evil.py").write_text("x = TaskRunner\n")
    return tmp_path


def test_runtime_scanner_gate_inproc_detects_violation(tmp_path: Path, monkeypatch) -> None:
    _scanner_fixture(tmp_path, evil=True)
    _only("RT-096", monkeypatch)
    fired = scan_runtime(tmp_path)
    assert [v for v in fired if v["stage"] == "inproc"], fired


def test_runtime_scanner_gate_cli_detects_violation(tmp_path: Path, monkeypatch) -> None:
    _scanner_fixture(tmp_path, evil=True)
    _only("RT-096", monkeypatch)
    fired = scan_runtime(tmp_path)
    assert [v for v in fired if v["stage"] == "cli"], fired


def test_runtime_scanner_gate_clean_tree_passes(tmp_path: Path, monkeypatch) -> None:
    _scanner_fixture(tmp_path, evil=False)
    _only("RT-096", monkeypatch)
    assert scan_runtime(tmp_path) == []


def test_runtime_scanner_gate_runs_both_gates(tmp_path: Path, monkeypatch) -> None:
    # Both gates run even when the first one fails; neither is skipped.
    _scanner_fixture(tmp_path, evil=True)
    _only("RT-096", monkeypatch)
    calls: list[list[str]] = []
    real_run = _policy.subprocess.run

    def spy(cmd, **kw):
        calls.append(list(cmd))
        return real_run(cmd, **kw)

    monkeypatch.setattr(_policy.subprocess, "run", spy)
    scan_runtime(tmp_path)
    assert len(calls) == 2, calls
    assert "-c" in calls[0], calls
    assert "v4_arch_scan.py" in calls[1][1], calls


# ---------------------------------------------------------------------------
# L0.4d2 legacy_cli compat profile: the thin CLI calls the same scan
# implementation with profile="legacy_cli" + rule_ids AP-088/089/090/091/092.
# ---------------------------------------------------------------------------

_LEGACY_IDS = ["AP-088", "AP-089", "AP-090", "AP-091", "AP-092"]


def _legacy_hits(root: Path) -> list[tuple]:
    import tools.architecture_policy as policy

    out = []
    for v in policy.scan(root, rule_ids=_LEGACY_IDS, profile="legacy_cli"):
        if v["rule"] == "AP-088":
            out.append((v["path"], v["line"], "legacy-import", v["detail"]))
        elif v["rule"] == "AP-089":
            out.append((v["path"], v["line"], "legacy-analysis-persistence", v["detail"]))
        elif v["rule"] == "AP-090":
            out.append((v["path"], v["line"], v["detail"], v["match"]))
        elif v["rule"] == "AP-091":
            out.append((v["path"], v["line"], "retired-runtime-present", v["detail"]))
        elif v["rule"] == "AP-092":
            out.append((v["path"], v["line"], "retired-v1v2-token", v["detail"]))
    return sorted(out)


def _scoped_producer(root: Path) -> None:
    _write(root, "confflow/__init__.py", "")
    _write(root, "confflow/producer/__init__.py", "")


def test_legacy_cli_trailing_comment_matches_old_and_default_still_fires(
    tmp_path: Path,
) -> None:
    # The STOP_REPORT minimal counterexample: `x = TaskRunner  # trailing`
    # is silent in the old scanner (whole-line blank quirk) and in the
    # legacy profile, while the default profile still reports AP-090.
    _scoped_producer(tmp_path)
    _write(tmp_path, "confflow/producer/evil.py", "x = TaskRunner  # trailing comment\n")
    assert _legacy_hits(tmp_path) == []
    default = [v for v in scan(tmp_path, rule_ids=("AP-090",)) if v["rule"] == "AP-090"]
    assert [(v["path"], v["line"], v["detail"]) for v in default] == [
        ("confflow/producer/evil.py", 1, "legacy-TaskRunner")
    ]


def test_legacy_cli_retired_package_reports_init_while_default_ignores(
    tmp_path: Path,
) -> None:
    # Package-only retired module: old + legacy report the __init__.py at
    # line 1; the default disk_absent profile (module.py only) is unchanged.
    _scoped_producer(tmp_path)
    _write(tmp_path, "confflow/config/__init__.py", "")
    _write(tmp_path, "confflow/config/canonical/__init__.py", "")
    assert _legacy_hits(tmp_path) == [
        (
            "confflow/config/canonical/__init__.py",
            1,
            "retired-runtime-present",
            "confflow.config.canonical",
        )
    ]
    assert [v for v in scan(tmp_path, rule_ids=("AP-091",)) if v["rule"] == "AP-091"] == []


def test_legacy_cli_relative_import_uses_old_package_basis(tmp_path: Path) -> None:
    # `from ...workflow.engine import X` in confflow/producer/sub/mod.py:
    # old basis resolves confflow.workflow.engine (fires); the default
    # resolver keeps the file name (silent). Legacy follows the old basis.
    _write(tmp_path, "confflow/__init__.py", "")
    _write(tmp_path, "confflow/producer/__init__.py", "")
    _write(tmp_path, "confflow/producer/sub/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/sub/mod.py",
        "from ...workflow.engine import X\n",
    )
    assert _legacy_hits(tmp_path) == [
        (
            "confflow/producer/sub/mod.py",
            1,
            "legacy-import",
            "confflow.workflow.engine",
        )
    ]
    assert [v for v in scan(tmp_path, rule_ids=("AP-088",)) if v["rule"] == "AP-088"] == []


def test_legacy_cli_prefix_boundary_matches_old_startswith(tmp_path: Path) -> None:
    # Non-dot-boundary caliber: `import confflow.shared2extra` fires under
    # the old plain startswith and the legacy profile, not by default.
    _scoped_producer(tmp_path)
    _write(tmp_path, "confflow/producer/e.py", "import confflow.shared2extra\n")
    assert _legacy_hits(tmp_path) == [
        (
            "confflow/producer/e.py",
            1,
            "legacy-import",
            "confflow.shared2extra",
        )
    ]
    assert [v for v in scan(tmp_path, rule_ids=("AP-088",)) if v["rule"] == "AP-088"] == []


def test_legacy_cli_fifteen_patterns_match_old_reference(tmp_path: Path) -> None:
    # All 15 SCANNER_PATTERNS: plain code fires in both profiles; the same
    # code with a trailing comment is silent in legacy (old quirk) and still
    # fires by default. Comment-only, docstring, and Chinese-docstring lines
    # stay silent in legacy.
    import tools.architecture_policy as policy

    _scoped_producer(tmp_path)
    probes = {
        "legacy-TaskRunner": "x = TaskRunner",
        "legacy-CalcStepRunner": "x = CalcStepRunner",
        "legacy-ResultsDB": "x = ResultsDB",
        "legacy-WorkflowState": "x = WorkflowStateX",
        "legacy-iprog": "x = iprog",
        "legacy-itask": "x = itask",
        "legacy-chk-from-step": "x = chk_from_step",
        "legacy-result-xyz": "x = result.xyz",
        "legacy-output-path": "x = output_path",
        "legacy-orca-fallback": "x = default_orca",
        "legacy-first-match": "x = first_match",
        "legacy-fake-marker": "x = FAKE_MODE",
        "legacy-stepresult-shortcut": "x = StepResult()",
        "legacy-duplicate-authority": "register_executor()",
        "legacy-silent-fallback": 'x = "silent fallback"',
    }
    assert [c for c, _ in policy.SCANNER_PATTERNS] == list(probes)
    for index, (_check, code) in enumerate(probes.items()):
        _write(tmp_path, f"confflow/producer/p{index}.py", f"{code}\n")
        _write(tmp_path, f"confflow/producer/q{index}.py", f"{code}  # trailing\n")
    _write(tmp_path, "confflow/producer/comment.py", "# TaskRunner ResultsDB\n")
    _write(tmp_path, "confflow/producer/doc.py", '"""TaskRunner ResultsDB"""\nx = 1\n')
    _write(tmp_path, "confflow/producer/cn.py", '"""中文 TaskRunner"""\nx = 1\n')
    legacy = [h for h in _legacy_hits(tmp_path) if h[2].startswith("legacy-")]
    assert sorted(h[2] for h in legacy) == sorted(probes)
    assert all(h[1] == 1 and h[0].startswith("confflow/producer/p") for h in legacy)
    expected_match = {
        "legacy-TaskRunner": "TaskRunner",
        "legacy-CalcStepRunner": "CalcStepRunner",
        "legacy-ResultsDB": "ResultsDB",
        "legacy-WorkflowState": "WorkflowStateX",
        "legacy-iprog": "iprog",
        "legacy-itask": "itask",
        "legacy-chk-from-step": "chk_from_step",
        "legacy-result-xyz": "result.xyz",
        "legacy-output-path": "output_path",
        "legacy-orca-fallback": "default_orca",
        "legacy-first-match": "first_match",
        "legacy-fake-marker": "FAKE_MODE",
        "legacy-stepresult-shortcut": "StepResult(",
        "legacy-duplicate-authority": "register_executor",
        "legacy-silent-fallback": "silent fallback",
    }
    assert {(h[2], h[3]) for h in legacy} == set(expected_match.items())
    default = [v for v in scan(tmp_path, rule_ids=("AP-090",)) if v["rule"] == "AP-090"]
    assert len(default) == 2 * len(probes)


def test_legacy_cli_output_matches_old_cli_fields_sort_exit_code(
    tmp_path: Path,
) -> None:
    # Independent locked expectations (no /tmp old reference at test time):
    # identical (path, line, check, text), identical sort, identical printed
    # lines and exit codes (dirty + clean).
    _scoped_producer(tmp_path)
    _write(tmp_path, "confflow/analysis/__init__.py", "")
    _write(tmp_path, "confflow/producer/a.py", "x = TaskRunner  # trailing\n")
    _write(tmp_path, "confflow/producer/b.py", "x = TaskRunner\ny = ResultsDB\n")
    _write(tmp_path, "confflow/producer/d.py", "x = load_workflow_definition\n")
    _write(tmp_path, "confflow/producer/e.py", "import confflow.shared.foo\n")
    _write(tmp_path, "confflow/analysis/f.py", "import confflow.persistence.store\n")
    expected = [
        (
            "confflow/analysis/f.py",
            1,
            "legacy-analysis-persistence",
            "confflow.persistence.store",
        ),
        ("confflow/producer/b.py", 1, "legacy-TaskRunner", "TaskRunner"),
        ("confflow/producer/b.py", 2, "legacy-ResultsDB", "ResultsDB"),
        (
            "confflow/producer/d.py",
            1,
            "retired-v1v2-token",
            "load_workflow_definition",
        ),
        ("confflow/producer/e.py", 1, "legacy-import", "confflow.shared.foo"),
    ]
    assert _legacy_hits(tmp_path) == expected
    _copy_scanner_tree(tmp_path)
    proc = _sp.run(
        [sys.executable, str(tmp_path / "scripts/v4_arch_scan.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    lines = [f"{p}:{n}:{c}:{t}" for p, n, c, t in expected]
    assert proc.stdout.splitlines() == lines
    assert proc.returncode == (1 if expected else 0)
    if expected:
        assert "FAIL" in proc.stderr
    else:
        assert "clean" in proc.stdout
    for victim in ("confflow/producer/b.py", "confflow/producer/d.py"):
        (tmp_path / victim).write_text("x = 1\n")
    (tmp_path / "confflow/producer/e.py").write_text("x = 1\n")
    (tmp_path / "confflow/analysis/f.py").write_text("x = 1\n")
    proc = _sp.run(
        [
            sys.executable,
            str(tmp_path / "scripts/v4_arch_scan.py"),
            "--cf",
            str(tmp_path),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0
    assert "clean" in proc.stdout


# ---------------------------------------------------------------------------
# L0.4d2-v2: four follow-up gaps (independent expectations, no /tmp ref).
# ---------------------------------------------------------------------------


def test_default_ap090_dict_shape_has_no_match(tmp_path: Path) -> None:
    # ROOT-v1 default_response gap: default AP-090 rows are exactly
    # {rule, path, line, detail}; only legacy_cli may attach "match".
    _scoped_producer(tmp_path)
    _write(tmp_path, "confflow/producer/probe.py", "x = TaskRunner\n")
    default = [v for v in scan(tmp_path, rule_ids=("AP-090",)) if v["rule"] == "AP-090"]
    assert default == [
        {
            "rule": "AP-090",
            "path": "confflow/producer/probe.py",
            "line": 1,
            "detail": "legacy-TaskRunner",
        }
    ]
    legacy = [
        v
        for v in _policy.scan(tmp_path, rule_ids=_LEGACY_IDS, profile="legacy_cli")
        if v["rule"] == "AP-090"
    ]
    assert legacy == [
        {
            "rule": "AP-090",
            "path": "confflow/producer/probe.py",
            "line": 1,
            "detail": "legacy-TaskRunner",
            "match": "TaskRunner",
        }
    ]


def test_l0_isolation_probe_does_not_borrow_host_jd(tmp_path: Path, monkeypatch) -> None:
    # L0 CI isolation regression: isolated scanner tests must not borrow the
    # host JD absolute double; AP-081 require_one stays strict.
    import tools.architecture_policy as policy

    rule = next(r for r in policy.RULES if r["id"] == "AP-081")
    monkeypatch.setitem(rule, "files", ["tests/v4/__missing_l0_ci_isolation_probe__.py"])
    _scoped_producer(tmp_path)
    _write(tmp_path, "confflow/producer/probe.py", "x = TaskRunner\n")
    isolated = [v for v in policy.scan(tmp_path, rule_ids=("AP-090",)) if v["rule"] == "AP-090"]
    assert isolated == [
        {
            "rule": "AP-090",
            "path": "confflow/producer/probe.py",
            "line": 1,
            "detail": "legacy-TaskRunner",
        }
    ]
    with pytest.raises(AssertionError, match="expected at least one double"):
        policy.scan(tmp_path, rule_ids=("AP-081",))


def test_legacy_cli_ignores_pycache_but_keeps_normal_scope(tmp_path: Path) -> None:
    # ROOT-v1 cache_source gap: __pycache__ .py files are skipped by legacy.
    import tools.architecture_policy as policy

    _scoped_producer(tmp_path)
    cache_file = tmp_path / "confflow/producer/__pycache__/probe.py"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("x = TaskRunner\n", encoding="utf-8")
    assert policy.scan(tmp_path, rule_ids=_LEGACY_IDS, profile="legacy_cli") == []
    # Normal in-scope file with the same violation still fires.
    _write(tmp_path, "confflow/producer/real.py", "x = TaskRunner\n")
    legacy = policy.scan(tmp_path, rule_ids=_LEGACY_IDS, profile="legacy_cli")
    assert [(v["path"], v["line"]) for v in legacy] == [("confflow/producer/real.py", 1)]
    # The thin wrapper count reuses the policy file discovery exactly.
    import sys

    sys.path.insert(0, str(_REAL_ROOT / "scripts"))
    try:
        import v4_arch_scan as scanner

        assert [str(p) for p in scanner._iter_files(tmp_path)] == [
            str(p) for p in policy.scope_files(tmp_path, profile="legacy_cli")
        ]
        assert not any("__pycache__" in str(p) for p in scanner._iter_files(tmp_path))
    finally:
        sys.path.pop(0)
        sys.modules.pop("v4_arch_scan", None)


def _other_policy_tree(dest: Path) -> None:
    (dest / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        _REAL_ROOT / "tools/architecture_policy.py",
        dest / "tools/architecture_policy.py",
    )
    (dest / "tools/__init__.py").write_text("", encoding="utf-8")


def _thin_only_fixture(dest: Path, script: str) -> None:
    (dest / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_REAL_ROOT / script, dest / script)


def test_thin_scan_refuses_foreign_policy_tree(tmp_path: Path) -> None:
    # Fail closed: thin script without its own-tree policy must refuse even
    # when PYTHONPATH offers another complete policy tree.
    import os

    other = tmp_path / "other"
    _other_policy_tree(other)
    fix = tmp_path / "fix"
    _thin_only_fixture(fix, "scripts/v4_arch_scan.py")
    assert not (fix / "tools/architecture_policy.py").exists()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(other) + os.pathsep + env.get("PYTHONPATH", "")
    proc = _sp.run(
        [sys.executable, str(fix / "scripts/v4_arch_scan.py"), "--root", str(fix)],
        cwd=str(fix),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode != 0, proc.stdout[-1000:]
    assert "authoritative policy not found" in (proc.stderr + proc.stdout)


def test_thin_metrics_refuses_foreign_policy_tree(tmp_path: Path) -> None:
    import os

    other = tmp_path / "other"
    _other_policy_tree(other)
    fix = tmp_path / "fix"
    _thin_only_fixture(fix, "scripts/architecture_metrics.py")
    assert not (fix / "tools/architecture_policy.py").exists()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(other) + os.pathsep + env.get("PYTHONPATH", "")
    proc = _sp.run(
        [
            sys.executable,
            str(fix / "scripts/architecture_metrics.py"),
            "--root",
            str(fix),
            "--json",
        ],
        cwd=str(fix),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode != 0, proc.stdout[-1000:]
    assert "authoritative policy not found" in (proc.stderr + proc.stdout)


# ---------------------------------------------------------------------------
# FIX-1A A2 scope policy (AST, small examples only; existing rules untouched).
# ---------------------------------------------------------------------------


def test_confgen_a2_scope_clean_on_real_tree() -> None:
    from tools.architecture_policy import confgen_a2_violations

    assert confgen_a2_violations(_REAL_ROOT) == []


def _a2_fixture(tmp_path: Path, rel: str, content: str) -> Path:
    for keep in (
        "confflow/science/confgen/__init__.py",
        "confflow/science/confgen/model.py",
        "confflow/science/confgen/engine.py",
        "confflow/science/confgen/kernel_records.py",
        "confflow/science/confgen/accounting.py",
        "confflow/science/confgen/registry.py",
        "confflow/science/confgen/wire_v3.py",
        "confflow/science/confgen/coordination/stage.py",
        "confflow/science/confgen/ring/stage.py",
        "confflow/science/confgen/torsion/stage.py",
    ):
        src = _REAL_ROOT / keep
        dst = tmp_path / keep
        if keep == rel:
            continue
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return tmp_path


def test_confgen_a2_wire_isolation_fires() -> None:
    import tempfile

    from tools.architecture_policy import confgen_a2_wire_isolation_violations

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _a2_fixture(
            root,
            "confflow/science/confgen/accounting.py",
            "import confflow.science.confgen.wire_v3\n",
        )
        assert confgen_a2_wire_isolation_violations(root)


def test_confgen_a2_attr_scope_fires() -> None:
    import tempfile

    from tools.architecture_policy import confgen_a2_attr_scope_violations

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _a2_fixture(
            root,
            "confflow/science/confgen/kernel_records.py",
            "def f(key):\n    return key.coordination\n",
        )
        assert confgen_a2_attr_scope_violations(root)


def test_confgen_a2_stage_parent_fires() -> None:
    import tempfile

    from tools.architecture_policy import confgen_a2_stage_parent_violations

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _a2_fixture(
            root,
            "confflow/science/confgen/ring/stage.py",
            "def f(parent):\n    return parent.state_key\n",
        )
        assert confgen_a2_stage_parent_violations(root)


# ---------------------------------------------------------------------------
# L1-G1b Gaussian gates (mechanical sync; existing rules untouched).
# ---------------------------------------------------------------------------


def test_g1_gates_clean_on_real_tree() -> None:
    from tools.architecture_policy import g1_violations

    assert g1_violations(_REAL_ROOT) == []


def test_g1_prod_authority_call_fires_on_real_violation(tmp_path: Path) -> None:
    from tools.architecture_policy import g1_prod_authority_call_violations

    _write(tmp_path, "confflow/__init__.py", "")
    _write(tmp_path, "confflow/producer/__init__.py", "")
    _write(tmp_path, "confflow/programs/__init__.py", "")
    _write(tmp_path, "confflow/programs/gaussian/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/checkpoints.py",
        "from confflow.programs.gaussian.rendering import resolve_write_chk\nx = resolve_write_chk({})\n",
    )
    _write(tmp_path, "confflow/programs/gaussian/checkpoint_policy.py", "x = 1\n")
    assert g1_prod_authority_call_violations(tmp_path) != []
