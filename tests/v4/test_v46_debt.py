#!/usr/bin/env python3

"""V4-6 production-path debt gate.

Extends -- never duplicates -- the existing boundary gates.  All shared
vocabulary (forbidden symbols, legacy modules, filename/ordinal scanners and
their allowlists) is IMPORTED from ``tests/v4/test_architecture_boundaries.py``
first (see ``TestV46BoundaryReuse``); this file only ADDS V4-6-specific gates:

- new-symbol ban: ``iprog`` (+ int-indexed program/executor dispatch idiom);
- extended scan roots: ``confflow/analysis`` + ``confflow/producer`` are
  scanned automatically when those sibling workstreams land (absent today);
- producer legacy-truth ban: ``result.xyz`` / ``failed.xyz`` /
  ``workflow_stats`` / ``output_path`` / ``min_xyz`` must never appear as code
  tokens on the V4 production path -- the RunResultManifest is the only
  authoritative output;
- old-runtime-fallback gate: no ``confflow.workflow.engine`` import anywhere
  in the V4 production roots; the two legacy entry-point imports in
  ``confflow/cli.py`` + ``confflow/application`` are pinned by an exact
  allowlist so any NEW fallback trips the gate;
- JobDesk-side doubles ban: ``*jobdesk*``/``*double*`` helpers in the V4-6
  cross-repo tests must not import ``confflow`` (they simulate the other repo).
"""

from __future__ import annotations

import ast
from pathlib import Path

# Existing boundaries FIRST: reuse, do not redefine (no dup).
from tests.v4.test_architecture_boundaries import (
    FORBIDDEN_LEGACY_MODULES,
    FORBIDDEN_SYMBOLS,
    _ORDINAL_WITHIN_ITEM_FILES,
    _RANGE_ORDINAL_ALLOWLIST,
    _filename_pairing_offenders,
    _range_ordinal_pairing_offenders,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "confflow"


def _existing_dir(name: str) -> Path | None:
    candidate = PACKAGE_ROOT / name
    return candidate if candidate.is_dir() else None


def _v46_strict_roots() -> list[Path]:
    """ACTIVE V4 production path, extended with analysis/producer when landed."""
    roots: list[Path] = []
    for name in (
        "domain",
        "workflow/v4",
        "execution",
        "analysis",  # sibling workstream; absent today
        "producer",  # sibling workstream; absent today
        "persistence",
        "programs",
        "remote",
    ):
        candidate = PACKAGE_ROOT / Path(name)
        if candidate.is_dir():
            roots.append(candidate)
    return roots


#: V4-6 NEW forbidden code symbols (disjoint from FORBIDDEN_SYMBOLS by test).
V46_NEW_SYMBOLS = ("iprog",)

#: Producer-contract legacy-truth tokens: never authoritative outputs on V4.
V46_LEGACY_TRUTH_TOKENS = (
    "result.xyz",
    "failed.xyz",
    "workflow_stats",
    "output_path",
    "min_xyz",
)

#: Legacy CLI/application entry points allowed to import the old runtime.
#: Any import of ``confflow.workflow.engine`` outside this exact allowlist
#: (in particular any NEW one) fails the gate.
ENGINE_IMPORT_ALLOWLIST = frozenset(
    {
        "confflow/cli.py",
        "confflow/application/execution/workflow_adapter.py",
    }
)

#: Files holding the JobDesk-side doubles (other repo scanned iff present).
DOUBLE_FILES = (
    REPO_ROOT / "tests" / "v4" / "test_v46_cross_repo.py",
    Path("/opt/jobdesk-v2-v4/tests/application/test_confflow_v4_e2e.py"),
)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return "confflow." + ".".join(parts)


def _resolve_import(path: Path, raw: str) -> str:
    if not raw.startswith("."):
        return raw
    module = _module_name(path)
    package_parts = module.split(".")
    if path.name != "__init__.py":
        package_parts = package_parts[:-1]
    level = len(raw) - len(raw.lstrip("."))
    remainder = raw.lstrip(".")
    if level - 1 > len(package_parts):
        return raw
    base = package_parts[: len(package_parts) - (level - 1)]
    return ".".join(base + ([remainder] if remainder else []))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append("." * node.level + (node.module or ""))
            else:
                found.append(node.module or "")
    return found


def _code_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                symbols.add(alias.asname or alias.name)
            if node.module:
                symbols.add(node.module.split(".")[-1])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.keyword):
            if node.arg:
                symbols.add(node.arg)
        elif isinstance(node, ast.arg):
            symbols.add(node.arg)
    return symbols


def _code_text_only(source: str) -> str:
    tree = ast.parse(source)

    def _strip(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                child.value.value = ""
            _strip(child)

    _strip(tree)
    return ast.unparse(tree)


_NUMERIC_DISPATCH_BASES = frozenset(
    {"programs", "executors", "adapters", "profiles", "checks", "recoveries"}
)


def _numeric_dispatch_offenders(source: str) -> list[tuple[int, str]]:
    """Flag int-indexed dispatch (``programs[0]``): V4 dispatches by name/version."""
    tree = ast.parse(source)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        value = node.value
        if not (isinstance(value, ast.Name) and value.id in _NUMERIC_DISPATCH_BASES):
            continue
        subscript = node.slice
        if isinstance(subscript, ast.Constant) and isinstance(subscript.value, int):
            offenders.append((node.lineno, f"{value.id}[{subscript.value}]"))
    return offenders


class TestV46BoundaryReuse:
    """Existing boundaries are checked first; V4-6 additions do not overlap."""

    def test_new_symbols_are_disjoint_from_existing(self) -> None:
        overlap = set(V46_NEW_SYMBOLS) & set(FORBIDDEN_SYMBOLS)
        assert overlap == set()

    def test_existing_symbols_still_absent_from_extended_roots(self) -> None:
        offenders: list[tuple[str, str]] = []
        for root in _v46_strict_roots():
            for path in _iter_python_files(root):
                symbols = _code_symbols(path)
                for forbidden in FORBIDDEN_SYMBOLS:
                    if forbidden in symbols:
                        offenders.append((str(path.relative_to(REPO_ROOT)), forbidden))
        assert offenders == []

    def test_existing_legacy_modules_still_unimported(self) -> None:
        offenders: list[tuple[str, str]] = []
        for root in _v46_strict_roots():
            for path in _iter_python_files(root):
                for raw in _imports(path):
                    if _resolve_import(path, raw) in FORBIDDEN_LEGACY_MODULES:
                        offenders.append((str(path.relative_to(REPO_ROOT)), raw))
        assert offenders == []


class TestV46NewSymbols:
    """``iprog`` numeric program dispatch never appears as code on the V4 path."""

    def test_no_iprog_symbol(self) -> None:
        offenders: list[tuple[str, str]] = []
        for root in _v46_strict_roots():
            for path in _iter_python_files(root):
                symbols = _code_symbols(path)
                for forbidden in V46_NEW_SYMBOLS:
                    if forbidden in symbols:
                        offenders.append((str(path.relative_to(REPO_ROOT)), forbidden))
        assert offenders == []

    def test_no_numeric_program_dispatch(self) -> None:
        offenders: list[tuple[str, int, str]] = []
        for root in _v46_strict_roots():
            for path in _iter_python_files(root):
                for lineno, expr in _numeric_dispatch_offenders(
                    path.read_text(encoding="utf-8")
                ):
                    offenders.append((str(path.relative_to(REPO_ROOT)), lineno, expr))
        assert offenders == []


class TestV46ProducerLegacyTruth:
    """Legacy output contracts are never authoritative on the V4 path."""

    def test_no_legacy_truth_tokens_as_code(self) -> None:
        offenders: list[tuple[str, str]] = []
        for root in _v46_strict_roots():
            for path in _iter_python_files(root):
                text = _code_text_only(path.read_text(encoding="utf-8"))
                for token in V46_LEGACY_TRUTH_TOKENS:
                    if token in text:
                        offenders.append((str(path.relative_to(REPO_ROOT)), token))
        assert offenders == []


class TestV46NoOldRuntimeFallback:
    """The V4 path never falls back to ``confflow.workflow.engine``."""

    def test_no_engine_import_in_strict_roots(self) -> None:
        offenders: list[str] = []
        for root in _v46_strict_roots():
            for path in _iter_python_files(root):
                for raw in _imports(path):
                    if _resolve_import(path, raw) == "confflow.workflow.engine":
                        offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_engine_imports_pinned_to_legacy_entrypoints(self) -> None:
        """cli/application engine imports are exactly the known legacy ones."""
        found: set[str] = set()
        for root in [PACKAGE_ROOT / "cli.py", PACKAGE_ROOT / "application"]:
            targets = [root] if root.is_file() else _iter_python_files(root)
            for path in targets:
                for raw in _imports(path):
                    if _resolve_import(path, raw) == "confflow.workflow.engine":
                        found.add(str(path.relative_to(REPO_ROOT)))
        assert found == set(ENGINE_IMPORT_ALLOWLIST), found


class TestV46NoFilenameOrdinalPairing:
    """Filename/ordinal pairing ban reused on workflow/v4 + execution (+new roots)."""

    def _scan_roots(self) -> list[Path]:
        names = ["workflow/v4", "execution", "analysis", "producer"]
        return [
            PACKAGE_ROOT / Path(name)
            for name in names
            if (PACKAGE_ROOT / Path(name)).is_dir()
        ]

    def test_no_filename_idioms(self) -> None:
        offenders: list[tuple[str, int, str]] = []
        for root in self._scan_roots():
            for path in _iter_python_files(root):
                for lineno, token in _filename_pairing_offenders(
                    path.read_text(encoding="utf-8")
                ):
                    offenders.append((str(path.relative_to(REPO_ROOT)), lineno, token))
        assert offenders == []

    def test_no_range_ordinal_pairing(self) -> None:
        offenders: list[tuple[str, str, int, str]] = []
        for root in self._scan_roots():
            for path in _iter_python_files(root):
                relative = str(path.relative_to(REPO_ROOT))
                if relative in _RANGE_ORDINAL_ALLOWLIST:
                    continue
                for function, lineno, index in _range_ordinal_pairing_offenders(
                    path.read_text(encoding="utf-8")
                ):
                    offenders.append((relative, function, lineno, index))
        assert offenders == []

    def test_ordinal_within_item_usage_is_confined(self) -> None:
        offenders: list[str] = []
        for root in self._scan_roots():
            for path in _iter_python_files(root):
                relative = str(path.relative_to(REPO_ROOT))
                text = _code_text_only(path.read_text(encoding="utf-8"))
                if "member_index" in text or "point_ordinal" in text:
                    if relative not in _ORDINAL_WITHIN_ITEM_FILES:
                        offenders.append(relative)
        assert offenders == []


def _jobdesk_double_offenders(path: Path) -> list[tuple[str, int, str]]:
    """JobDesk-side doubles must not import ``confflow`` (they simulate it)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[tuple[str, int, str]] = []

    def _check(node: ast.AST, owner: str) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "confflow" or alias.name.startswith("confflow."):
                        offenders.append((owner, child.lineno, alias.name))
            elif isinstance(child, ast.ImportFrom):
                module = _resolve_import(path, ("." * child.level) + (child.module or ""))
                if module == "confflow" or module.startswith("confflow."):
                    offenders.append((owner, child.lineno, module))
            elif isinstance(child, ast.Attribute):
                value = child.value
                if isinstance(value, ast.Name) and value.id == "confflow":
                    offenders.append((owner, child.lineno, f"confflow.{child.attr}"))

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and "jobdesk" in node.name.lower():
            _check(node, node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.lower()
            if "jobdesk" in lowered and not node.name.startswith("Test"):
                _check(node, node.name)
    return offenders


class TestV46JobdeskDoublesHaveNoConfflowImports:
    """The JobDesk simulation doubles are other-repo code: zero confflow imports."""

    def test_doubles_are_confflow_free(self) -> None:
        offenders: list[tuple[str, str, int, str]] = []
        scanned = 0
        for path in DOUBLE_FILES:
            if not path.is_file():
                continue  # sibling file lands in parallel; scanned when present
            scanned += 1
            for owner, lineno, module in _jobdesk_double_offenders(path):
                offenders.append((str(path), owner, lineno, module))
        assert scanned >= 1, "expected at least the ConfFlow cross-repo file to exist"
        assert offenders == []


# ---------------------------------------------------------------------------
# V4-6 E2E additions (workstream E).  Genuinely new assertions only: nothing
# below duplicates ``test_architecture_boundaries.py`` or the gates above.
# ---------------------------------------------------------------------------

#: The workstream-E cross-repo E2E file also hosts JobDesk-side consumer
#: doubles (``JobdeskV4ConsumerDouble``); they simulate the other repo and
#: must stay ``confflow``-free exactly like ``DOUBLE_FILES``.
V46_E2E_DOUBLE_FILES = (
    REPO_ROOT / "tests" / "v4" / "test_v46_cross_repo_e2e.py",
)

#: Landed V4-6 production modules (producer + analysis).  Every entry must
#: live under the scan roots above and must not import exact forbidden
#: legacy modules.  (Runtime isolation beyond exact-module matching is NOT
#: asserted: the producer legitimately reads schema constants through
#: ``confflow.config.canonical.*`` submodules, which exact matching allows.)
V46_MODULES = (
    "confflow.producer",
    "confflow.producer.contract",
    "confflow.producer.manifest",
    "confflow.producer.recipes",
    "confflow.producer.validation",
    "confflow.analysis.executor",
    "confflow.analysis.grouping",
    "confflow.analysis.models",
    "confflow.analysis.pes",
    "confflow.analysis.reaction",
    "confflow.analysis.registry",
    "confflow.analysis.thermochemistry",
    "confflow.analysis.units",
)


class TestV46E2EJobdeskDoublesHaveNoConfflowImports:
    """The workstream-E doubles are other-repo code: zero confflow imports."""

    def test_e2e_doubles_are_confflow_free(self) -> None:
        offenders: list[tuple[str, str, int, str]] = []
        scanned = 0
        for path in V46_E2E_DOUBLE_FILES:
            if not path.is_file():
                continue
            scanned += 1
            for owner, lineno, module in _jobdesk_double_offenders(path):
                offenders.append((str(path), owner, lineno, module))
        assert scanned >= 1, "expected the workstream-E cross-repo E2E file to exist"
        assert offenders == []


class TestV46NewSymbolsInApplicationCli:
    """``iprog`` never appears as code in ``confflow/application`` + ``cli``.

    Legacy symbols legitimately live in the legacy entry points, so only the
    V4-6 NEW symbols are gated here (the strict roots above already gate the
    old vocabulary where it must be absent).
    """

    def _targets(self) -> list[Path]:
        targets: list[Path] = []
        application = PACKAGE_ROOT / "application"
        if application.is_dir():
            targets.extend(_iter_python_files(application))
        cli = PACKAGE_ROOT / "cli.py"
        if cli.is_file():
            targets.append(cli)
        return targets

    def test_no_iprog_symbol_in_application_cli(self) -> None:
        offenders: list[tuple[str, str]] = []
        for path in self._targets():
            symbols = _code_symbols(path)
            for forbidden in V46_NEW_SYMBOLS:
                if forbidden in symbols:
                    offenders.append((str(path.relative_to(REPO_ROOT)), forbidden))
        assert offenders == []

    def test_no_numeric_program_dispatch_in_application_cli(self) -> None:
        offenders: list[tuple[str, int, str]] = []
        for path in self._targets():
            for lineno, expr in _numeric_dispatch_offenders(path.read_text(encoding="utf-8")):
                offenders.append((str(path.relative_to(REPO_ROOT)), lineno, expr))
        assert offenders == []


class TestV46ModuleCoverage:
    """All landed V4-6 production modules sit inside the debt-gate roots."""

    def test_v46_modules_exist(self) -> None:
        for module in V46_MODULES:
            base = REPO_ROOT / Path(module.replace(".", "/"))
            path = base.with_suffix(".py") if base.parent != REPO_ROOT else base.with_suffix(".py")
            is_pkg = (REPO_ROOT / Path(module.replace(".", "/")) / "__init__.py").is_file()
            assert path.is_file() or is_pkg, module

    def test_v46_modules_are_covered_by_scan_roots(self) -> None:
        roots = _v46_strict_roots()
        for module in V46_MODULES:
            path = REPO_ROOT / Path(module.replace(".", "/")).with_suffix(".py")
            if not path.is_file():
                path = REPO_ROOT / Path(module.replace(".", "/")) / "__init__.py"
            assert any(path == root or root in path.parents for root in roots), module

    def test_v46_modules_face_no_exact_legacy_imports(self) -> None:
        offenders: list[tuple[str, str]] = []
        for module in V46_MODULES:
            base = REPO_ROOT / Path(module.replace(".", "/"))
            path = base.with_suffix(".py")
            if not path.is_file():
                path = base / "__init__.py"
            for raw in _imports(path):
                if _resolve_import(path, raw) in FORBIDDEN_LEGACY_MODULES:
                    offenders.append((module, raw))
        assert offenders == []
