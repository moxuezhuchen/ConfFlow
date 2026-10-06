"""L1-A3c intent science isolation (tool guard only, zero production change).

Scope pins the ACTUAL split intent package (``confflow/producer/intent``)
and forbids ``confflow.science.*`` static imports through the existing
AST import data rule (AP-106, v2 with per-rule ``resolver_style``).
Every case runs through the real ``scan()`` entry point. No new scanner,
no production intent change.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import tools.architecture_policy as policy
from tools.architecture_policy import L1_INTENT_SCOPE, scan

_REAL_ROOT = Path(__file__).resolve().parents[2]
RULE_ID = "AP-106"


def _write(root: Path, relpath: str, content: str) -> None:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fired(root: Path) -> list[dict]:
    return scan(root, rule_ids=[RULE_ID])


def test_ap106_rule_shape_uses_existing_imports_mechanism() -> None:
    rule = next(r for r in policy.RULES if r["id"] == RULE_ID)
    assert rule["kind"] == "imports"
    assert rule["mode"] == "forbidden_prefixes"
    assert rule["prefixes"] == ["confflow.science"]
    assert rule["scope"] == L1_INTENT_SCOPE == ["confflow/producer/intent"]
    assert rule["resolve_relative"] is True
    # v2: per-rule resolver reuses the existing legacy_cli package basis
    # (drop filename, level-1 up); no new algorithm, no global default change.
    assert rule["resolver_style"] == "legacy_cli"
    assert rule["source"] == "#106"
    # CLI compat profile unchanged: AP-106 must not leak into legacy CLI.
    assert RULE_ID not in policy.LEGACY_CLI_RULE_IDS
    # G13 scopes/exemptions untouched.
    assert "confflow/producer/intent" not in policy.CONFGEN_G13_KERNEL_SCOPE
    # Old imports rules keep no per-rule style (exact old caliber).
    old = next(r for r in policy.RULES if r["id"] == "AP-003")
    assert "resolver_style" not in old


def test_ap106_top_level_import_fires(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, "confflow/producer/intent/hurt.py", "import confflow.science.confgen.engine\n")
    assert any(v["path"].endswith("hurt.py") for v in _fired(tmp_path))


def test_ap106_top_level_from_import_fires(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/intent/hurt.py",
        "from confflow.science.confgen import engine\n",
    )
    fired = _fired(tmp_path)
    assert fired, "absolute ImportFrom must fire"


def test_ap106_function_lazy_import_fires(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/intent/hurt.py",
        "def build():\n    import confflow.science.confgen.engine\n    return 1\n",
    )
    assert _fired(tmp_path), "function-local lazy import must fire (ast.walk scope)"


def test_ap106_class_body_import_fires(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/intent/hurt.py",
        "class Wire:\n    import confflow.science.torsion\n",
    )
    assert _fired(tmp_path), "class-body import must fire (ast.walk scope)"


def test_ap106_class_method_lazy_import_fires(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/intent/hurt.py",
        "class Wire:\n    def build(self):\n        from confflow.science import confgen\n",
    )
    assert _fired(tmp_path), "method-local import must fire"


def test_ap106_root_counterexample_normal_module_relative_fires(tmp_path: Path) -> None:
    # Root counterexample: intent/hurt.py normal module escapes via level-3.
    rel = "confflow/producer/intent/hurt.py"
    src = "from ...science.confgen import engine\n"
    expected = importlib.util.resolve_name("...science.confgen", "confflow.producer.intent")
    assert expected == "confflow.science.confgen"
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, rel, src)
    fired = _fired(tmp_path)
    assert fired, f"root counterexample must fire after v2: {fired}"
    assert any(v["detail"] == expected for v in fired), fired


def test_ap106_absolute_in_all_three_locations_fires(tmp_path: Path) -> None:
    for rel in [
        "confflow/producer/intent/hurt.py",
        "confflow/producer/intent/capabilities/hurt.py",
        "confflow/producer/intent/__init__.py",
    ]:
        _write(tmp_path, rel, "import confflow.science.confgen.engine\n")
    _write(tmp_path, "confflow/producer/intent/capabilities/__init__.py", "")
    fired = _fired(tmp_path)
    paths = {v["path"] for v in fired}
    assert "confflow/producer/intent/hurt.py" in paths, fired
    assert "confflow/producer/intent/capabilities/hurt.py" in paths, fired
    assert "confflow/producer/intent/__init__.py" in paths, fired


def test_ap106_relative_normal_module_matches_resolve_name(tmp_path: Path) -> None:
    rel = "confflow/producer/intent/hurt.py"
    src = "from ...science.confgen import engine\n"
    expected = importlib.util.resolve_name("...science.confgen", "confflow.producer.intent")
    assert expected == "confflow.science.confgen"
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, rel, src)
    fired = _fired(tmp_path)
    assert fired and any(v["detail"] == expected for v in fired), fired


def test_ap106_relative_capabilities_module_matches_resolve_name(tmp_path: Path) -> None:
    rel = "confflow/producer/intent/capabilities/hurt.py"
    src = "from ....science.confgen import engine\n"
    expected = importlib.util.resolve_name(
        "....science.confgen", "confflow.producer.intent.capabilities"
    )
    assert expected == "confflow.science.confgen"
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, "confflow/producer/intent/capabilities/__init__.py", "")
    _write(tmp_path, rel, src)
    fired = _fired(tmp_path)
    assert fired and any(v["detail"] == expected for v in fired), fired


def test_ap106_relative_init_matches_resolve_name(tmp_path: Path) -> None:
    rel = "confflow/producer/intent/__init__.py"
    src = "from ...science.confgen import engine\n"
    expected = importlib.util.resolve_name("...science.confgen", "confflow.producer.intent")
    assert expected == "confflow.science.confgen"
    _write(tmp_path, rel, src)
    fired = _fired(tmp_path)
    assert fired and any(v["detail"] == expected for v in fired), fired


def test_ap106_function_lazy_relative_matches_resolve_name(tmp_path: Path) -> None:
    rel = "confflow/producer/intent/hurt.py"
    src = "def build():\n    from ...science.confgen import engine\n    return engine\n"
    expected = importlib.util.resolve_name("...science.confgen", "confflow.producer.intent")
    assert expected == "confflow.science.confgen"
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, rel, src)
    fired = _fired(tmp_path)
    assert fired and any(v["detail"] == expected for v in fired), fired


def test_ap106_class_method_lazy_relative_matches_resolve_name(tmp_path: Path) -> None:
    rel = "confflow/producer/intent/capabilities/hurt.py"
    src = "class Wire:\n    def build(self):\n        from ....science.confgen import engine\n"
    expected = importlib.util.resolve_name(
        "....science.confgen", "confflow.producer.intent.capabilities"
    )
    assert expected == "confflow.science.confgen"
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, "confflow/producer/intent/capabilities/__init__.py", "")
    _write(tmp_path, rel, src)
    fired = _fired(tmp_path)
    assert fired and any(v["detail"] == expected for v in fired), fired


def test_ap106_relative_intra_intent_stays_clean(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/intent/clean.py",
        "from .common import _fail\nfrom ..cards import CARD_TYPES\n",
    )
    assert _fired(tmp_path) == []


def test_ap106_prose_only_stays_clean(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(
        tmp_path,
        "confflow/producer/intent/clean.py",
        '"""Prose mentions confflow.science.confgen.engine here."""\n'
        "# and confflow.science.confgen.engine in a comment\n"
        "x = 1\n",
    )
    assert _fired(tmp_path) == [], "docstring/comment prose must not count as import"


def test_ap106_scope_does_not_expand_to_whole_producer(tmp_path: Path) -> None:
    # Same science import OUTSIDE intent must not fire AP-106.
    _write(tmp_path, "confflow/producer/other.py", "import confflow.science.confgen.engine\n")
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, "confflow/producer/intent/clean.py", "x = 1\n")
    fired = _fired(tmp_path)
    assert fired == [], f"AP-106 scope must stay intent-only: {fired}"


def test_ap106_clean_fixture_stays_silent(tmp_path: Path) -> None:
    _write(tmp_path, "confflow/producer/intent/__init__.py", "")
    _write(tmp_path, "confflow/producer/intent/clean.py", "x = 1\n")
    assert _fired(tmp_path) == []


def test_ap106_real_tree_scan_is_clean() -> None:
    assert [v for v in scan(_REAL_ROOT) if v["rule"] == RULE_ID] == []
