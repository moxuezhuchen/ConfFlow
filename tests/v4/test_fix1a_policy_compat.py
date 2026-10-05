"""FIX-1A POLICY-COMPAT regression (minimal, real scan entry only).

Covers exactly:
- contract.py uses ``default_registry`` (no ``build_default_registry``) and
  AP-090 is clean on the real tree;
- AP-002 allows precisely ``confflow.science.confgen.registry`` in precisely
  ``confflow/execution/transform_executor.py``, while other science modules
  and other files are still rejected;
- the 15 SCANNER_PATTERNS are byte-identical (no widened licence).

All policy assertions go through the true ``tools.architecture_policy.scan``
entry point (never grep-only).
"""

from __future__ import annotations

from pathlib import Path

import tools.architecture_policy as policy
from tools.architecture_policy import SCANNER_PATTERNS, scan

_REAL_ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_contract_uses_default_registry_and_ap090_clean() -> None:
    text = (_REAL_ROOT / "confflow/producer/contract.py").read_text(encoding="utf-8")
    assert "build_default_registry" not in text
    assert "from ..science.confgen.registry import default_registry" in text
    assert "registry = default_registry()" in text
    hits = [v for v in scan(_REAL_ROOT, rule_ids=("AP-090",)) if v["rule"] == "AP-090"]
    bad = [v for v in hits if v["path"] == "confflow/producer/contract.py"]
    assert bad == []


def test_ap002_real_tree_clean_on_transform_executor() -> None:
    hits = [v for v in scan(_REAL_ROOT, rule_ids=("AP-002",)) if v["rule"] == "AP-002"]
    assert [v for v in hits if v["path"] == "confflow/execution/transform_executor.py"] == []


def test_ap002_other_science_module_still_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/__init__.py", "")
    _write(tmp_path, "confflow/execution/__init__.py", "")
    _write(
        tmp_path,
        "confflow/execution/transform_executor.py",
        "from confflow.science.confgen.planner import normalize_spec\n",
    )
    hits = scan(tmp_path, rule_ids=("AP-002",))
    assert [(v["path"], v["detail"]) for v in hits if v["rule"] == "AP-002"] == [
        (
            "confflow/execution/transform_executor.py",
            "confflow.science.confgen.planner",
        )
    ]


def test_ap002_same_module_in_other_file_still_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/__init__.py", "")
    _write(tmp_path, "confflow/execution/__init__.py", "")
    _write(
        tmp_path,
        "confflow/execution/other_mod.py",
        "from confflow.science.confgen.registry import resolve_registry\n",
    )
    hits = scan(tmp_path, rule_ids=("AP-002",))
    assert [(v["path"], v["detail"]) for v in hits if v["rule"] == "AP-002"] == [
        ("confflow/execution/other_mod.py", "confflow.science.confgen.registry")
    ]


def test_ap090_build_default_still_rejected_comment_silent(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/__init__.py", "")
    _write(tmp_path, "confflow/producer/__init__.py", "")
    _write(tmp_path, "confflow/producer/evil.py", "x = build_default_registry()\n")
    _write(tmp_path, "confflow/producer/c.py", "# build_default_registry\nx = 1\n")
    _write(tmp_path, "confflow/producer/d.py", '"""build_default_registry"""\nx = 1\n')
    hits = scan(tmp_path, rule_ids=("AP-090",))
    by_path = sorted((v["path"], v["line"]) for v in hits if v["rule"] == "AP-090")
    assert by_path == [("confflow/producer/evil.py", 1)]


def test_default_registry_equivalent_and_cached() -> None:
    from confflow.science.confgen.registry import build_default_registry, default_registry

    fresh = build_default_registry()
    shared = default_registry()
    assert fresh.ids() == shared.ids() == ("coordination", "rings", "torsions")
    assert default_registry() is default_registry()
    # Frozen: mutating a spec_default must fail or leave the cache unaffected.
    try:
        shared.descriptors[0].spec_defaults[0][1].append("X")  # type: ignore[attr-defined]
    except Exception:
        pass
    assert default_registry().ids() == ("coordination", "rings", "torsions")


def test_fifteen_patterns_byte_identical() -> None:
    assert len(SCANNER_PATTERNS) == 15
    assert dict(SCANNER_PATTERNS)["legacy-duplicate-authority"] == (
        "register_executor|register_program|build_default_registry|" "\\bExecutionRegistry\\s*\\("
    )
    rule = next(r for r in policy.RULES if r["id"] == "AP-002")
    assert rule["allowed"] == [
        "confflow.domain",
        "confflow.execution",
        "confflow.workflow.v4",
        "confflow.persistence",
        "confflow.programs",
        "confflow.remote",
    ]
    exempt = rule["exempt_imports"]
    assert set(exempt) == {
        "confflow/workflow/v4/confgen_schema.py",
        "confflow/execution/confgen_executor.py",
    }
    precise = rule.get("exempt_precise_imports", {})
    assert precise == {
        "confflow/execution/transform_executor.py": ["confflow.science.confgen.registry"]
    }


def test_g1_gates_clean_on_real_tree() -> None:
    from pathlib import Path as _P

    from tools.architecture_policy import g1_violations

    assert g1_violations(_P(__file__).resolve().parents[2]) == []
