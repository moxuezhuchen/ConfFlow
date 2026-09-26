#!/usr/bin/env python3

"""V4 architecture debt gate.

Static (AST) and runtime (subprocess import) checks that the greenfield core
carries zero legacy semantics:

- ``confflow.domain`` imports nothing from the repository except itself;
- ``confflow.workflow.v4`` and ``confflow.execution`` import only
  ``confflow.domain`` / ``confflow.execution`` / ``confflow.workflow.v4``;
- forbidden legacy symbols never appear as code (docstrings may explain them);
- importing the V4 core never imports the V2/V3 runtime, legacy config, or the
  calc subsystem;
- the V4 packages are discoverable by ``setuptools.find_packages``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "confflow"
DOMAIN_ROOT = PACKAGE_ROOT / "domain"
EXECUTION_ROOT = PACKAGE_ROOT / "execution"
V4_ROOT = PACKAGE_ROOT / "workflow" / "v4"
PERSISTENCE_ROOT = PACKAGE_ROOT / "persistence"
PROGRAMS_ROOT = PACKAGE_ROOT / "programs"
REMOTE_ROOT = PACKAGE_ROOT / "remote"

FORBIDDEN_IMPORT_PREFIXES = (
    "confflow.blocks",
    "confflow.calc",
    "confflow.config",
    "confflow.core",
    "confflow.shared",
    "confflow.application",
    "confflow.control",
    "confflow.control_worker",
    "confflow.worker_attempt",
    "confflow.worker_handoff",
    "confflow.worker_sidecars",
    "confflow.worker_staging",
    "confflow.worker_supervision",
    "confflow.artifact_json",
    "confflow.cli",
    "confflow.main",
    "confflow.confts",
    "confflow.contract",
    "confflow.install_provenance",
    "confflow.fixture_agent",
)

FORBIDDEN_LEGACY_MODULES = (
    "confflow.workflow.engine",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.plan",
    "confflow.workflow.binding_v2",
    "confflow.workflow.step_handlers",
    "confflow.workflow.state",
    "confflow.workflow.stats",
    "confflow.workflow.helpers",
    "confflow.workflow.presenter",
    "confflow.workflow.validation",
    "confflow.workflow.supervisor",
    "confflow.workflow.runtime_context",
    "confflow.workflow.execution_context",
    "confflow.workflow.finalize",
    "confflow.workflow.resume_validation",
    "confflow.workflow.dag",
    "confflow.workflow.dry_run",
    "confflow.workflow.export",
    "confflow.workflow.rerun_failed",
    "confflow.workflow.config_show",
    "confflow.workflow.composition",
    "confflow.config.canonical",
    "confflow.calc.runner",
    "confflow.core.models",
    "confflow.core.types",
    "confflow.core.parsers",
    "confflow.core.path_policy",
    "confflow.core.io",
    "confflow.core.exceptions",
)

FORBIDDEN_SYMBOLS = (
    "WorkflowPlan",
    "WorkflowV3Plan",
    "v3_runtime",
    "v3_dataflow",
    "TaskName",
    "itask",
    "get_itask",
    "CalcStepRequest",
    "CalcStepRunner",
    "TaskRunner",
    "StepExecutionResult",
    "ResultsDB",
    "GlobalOptions",
    "TaskContext",
    "input_xyz",
    "output_path",
    "total_memory",
    "max_parallel_jobs",
    "chk_from_step",
    "checkpoint_from",
    "auto_clean",
    "output_xyz",
    "task_results",
    "WorkflowStateV1",
    "WorkflowStateV2",
    "WorkflowStateV3",
    "CheckpointManager",
    "WorkflowStatsTracker",
    "FailureTracker",
    "TaskStatsCollector",
    "delete_work_dir",
    "binding_v2",
    "workflow_config",
    "worker_config",
    "backup_dir",
    "ibkout",
)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _module_name(path: Path) -> str:
    relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return "confflow." + ".".join(parts)


def _imports(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative imports stay inside the containing package.
                found.append(("." * node.level + (node.module or ""), node.lineno))
            else:
                found.append((node.module or "", node.lineno))
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
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Attribute):
                    symbols.add(decorator.attr)
        elif isinstance(node, ast.keyword):
            if node.arg:
                symbols.add(node.arg)
        elif isinstance(node, ast.arg):
            symbols.add(node.arg)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Attribute):
                        symbols.add(child.attr)
    return symbols


class TestStaticImports:
    """Static import-boundary checks over the V4 core sources."""

    def test_domain_has_no_repository_imports(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for path in _iter_python_files(DOMAIN_ROOT):
            for module, lineno in _imports(path):
                if module.startswith("confflow"):
                    offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_v4_and_execution_import_only_allowed_repository_modules(self) -> None:
        allowed_prefixes = (
            "confflow.domain",
            "confflow.execution",
            "confflow.workflow.v4",
            "confflow.programs",
            "confflow.persistence",
            "confflow.remote",
        )
        offenders: list[tuple[str, str, int]] = []
        for root in (V4_ROOT, EXECUTION_ROOT, PERSISTENCE_ROOT, PROGRAMS_ROOT, REMOTE_ROOT):
            for path in _iter_python_files(root):
                for module, lineno in _imports(path):
                    if not module.startswith("confflow"):
                        continue
                    if module.startswith(allowed_prefixes):
                        continue
                    offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_persistence_imports_only_domain_and_self(self) -> None:
        allowed_prefixes = ("confflow.domain", "confflow.persistence")
        offenders: list[tuple[str, str, int]] = []
        for path in _iter_python_files(PERSISTENCE_ROOT):
            module = _module_name(path)
            package_parts = module.split(".")
            if path.name != "__init__.py":
                package_parts = package_parts[:-1]
            for raw, lineno in _imports(path):
                relative = str(path.relative_to(REPO_ROOT))
                if not raw.startswith("."):
                    if raw.startswith("confflow") and not raw.startswith(allowed_prefixes):
                        offenders.append((relative, raw, lineno))
                    continue
                level = len(raw) - len(raw.lstrip("."))
                remainder = raw.lstrip(".")
                if level - 1 > len(package_parts):
                    offenders.append((relative, raw, lineno))
                    continue
                base = package_parts[: len(package_parts) - (level - 1)]
                absolute = ".".join(base + ([remainder] if remainder else []))
                if absolute.startswith("confflow") and not absolute.startswith(allowed_prefixes):
                    offenders.append((relative, absolute, lineno))
        assert offenders == []

    def test_forbidden_import_prefixes_absent_everywhere_in_v4_core(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for root in (
            DOMAIN_ROOT,
            EXECUTION_ROOT,
            V4_ROOT,
            PERSISTENCE_ROOT,
            PROGRAMS_ROOT,
            REMOTE_ROOT,
        ):
            for path in _iter_python_files(root):
                for module, lineno in _imports(path):
                    if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_no_legacy_runtime_import_in_v4_core(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for root in (
            DOMAIN_ROOT,
            EXECUTION_ROOT,
            V4_ROOT,
            PERSISTENCE_ROOT,
            PROGRAMS_ROOT,
            REMOTE_ROOT,
        ):
            for path in _iter_python_files(root):
                for module, lineno in _imports(path):
                    if module in FORBIDDEN_LEGACY_MODULES:
                        offenders.append((str(path.relative_to(REPO_ROOT)), module, lineno))
        assert offenders == []

    def test_no_forbidden_legacy_symbols_as_code(self) -> None:
        offenders: list[tuple[str, str, str]] = []
        for root in (
            DOMAIN_ROOT,
            EXECUTION_ROOT,
            V4_ROOT,
            PERSISTENCE_ROOT,
            PROGRAMS_ROOT,
            REMOTE_ROOT,
        ):
            for path in _iter_python_files(root):
                if path.name == "test_architecture_boundaries.py":
                    continue
                symbols = _code_symbols(path)
                for forbidden in FORBIDDEN_SYMBOLS:
                    if forbidden in symbols:
                        offenders.append((str(path.relative_to(REPO_ROOT)), forbidden, "symbol"))
        assert offenders == []


def _code_text_only(source: str) -> str:
    """Strip docstrings and comments, leaving code tokens only."""
    tree = ast.parse(source)

    def _strip(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                child.value.value = ""
            _strip(child)

    _strip(tree)
    return ast.unparse(tree)


class TestStaticCodeVocabulary:
    """Forbidden filename/result contracts must not appear as code tokens."""

    def test_domain_has_no_output_path_contract(self) -> None:
        offenders: list[str] = []
        for path in _iter_python_files(DOMAIN_ROOT):
            text = _code_text_only(path.read_text(encoding="utf-8"))
            if "output_path" in text or "input_xyz" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []

    def test_v4_core_has_no_legacy_path_contracts(self) -> None:
        offenders: list[str] = []
        for root in (EXECUTION_ROOT, V4_ROOT, PERSISTENCE_ROOT, PROGRAMS_ROOT, REMOTE_ROOT):
            for path in _iter_python_files(root):
                text = _code_text_only(path.read_text(encoding="utf-8"))
                if "output_path" in text:
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert offenders == []


class TestStaticLegacyFilenameContracts:
    """Legacy result-file contracts must not appear as code tokens.

    ``result.xyz``/``failed.xyz`` contain dots, so the AST symbol scanner in
    :class:`TestStaticImports` cannot catch them; this scan inspects the
    docstring- and comment-stripped code text instead.
    """

    LEGACY_FILENAME_TOKENS = (
        "result.xyz",
        "failed.xyz",
        "output_xyz",
        "delete_work_dir",
        "input_xyz",
        "output_path",
    )

    def test_no_legacy_filename_contracts_as_code(self) -> None:
        offenders: list[tuple[str, str]] = []
        for root in (
            DOMAIN_ROOT,
            EXECUTION_ROOT,
            V4_ROOT,
            PERSISTENCE_ROOT,
            PROGRAMS_ROOT,
            REMOTE_ROOT,
        ):
            for path in _iter_python_files(root):
                text = _code_text_only(path.read_text(encoding="utf-8"))
                for token in self.LEGACY_FILENAME_TOKENS:
                    if token in text:
                        offenders.append((str(path.relative_to(REPO_ROOT)), token))
        assert offenders == []


class TestRemoteBoundary:
    """The remote worker consumes compiled semantics, never legacy contracts."""

    LEGACY_REMOTE_TOKENS = (
        "input_xyz",
        "workflow_config",
        "TaskRunner",
        "CalcStepRunner",
        "TaskName",
        "get_itask",
        "GlobalOptions",
        "ResultsDB",
        "chk_from_step",
        "backup_dir",
        "ibkout",
        "result.xyz",
        "failed.xyz",
        "output_path",
    )

    def test_remote_has_zero_legacy_code_tokens(self) -> None:
        offenders: list[tuple[str, str]] = []
        for path in _iter_python_files(REMOTE_ROOT):
            text = _code_text_only(path.read_text(encoding="utf-8"))
            for token in self.LEGACY_REMOTE_TOKENS:
                if token in text:
                    offenders.append((str(path.relative_to(REPO_ROOT)), token))
        assert offenders == []

    def test_remote_has_no_compiler_or_yaml_imports(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for path in _iter_python_files(REMOTE_ROOT):
            module = _module_name(path)
            package_parts = module.split(".")
            if path.name != "__init__.py":
                package_parts = package_parts[:-1]
            for raw, lineno in _imports(path):
                candidates = [raw]
                if raw.startswith("."):
                    level = len(raw) - len(raw.lstrip("."))
                    remainder = raw.lstrip(".")
                    base = package_parts[: len(package_parts) - (level - 1)]
                    candidates.append(".".join(base + ([remainder] if remainder else [])))
                for candidate in candidates:
                    segments = candidate.split(".")
                    if "compiler" in candidate or "yaml" in segments:
                        offenders.append((str(path.relative_to(REPO_ROOT)), candidate, lineno))
        assert offenders == []


class TestRuntimeIsolation:
    """Runtime import isolation, checked in subprocesses."""

    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_importing_domain_does_not_import_legacy_packages(self) -> None:
        script = (
            "import sys; import confflow.domain; "
            "forbidden = [m for m in sys.modules if m == 'confflow.core' "
            "or m.startswith('confflow.config') or m.startswith('confflow.calc') "
            "or m.startswith('confflow.workflow')]; "
            "assert not forbidden, forbidden"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr

    def test_importing_v4_core_does_not_import_v3_runtime(self) -> None:
        script = (
            "import sys; import confflow.workflow.v4 as v4; "
            "import confflow.execution; "
            "forbidden = [m for m in sys.modules if m.startswith('confflow.workflow.') "
            "and not m.startswith('confflow.workflow.v4')]; "
            "assert not forbidden, forbidden; "
            "assert 'confflow.config' not in sys.modules; "
            "assert not [m for m in sys.modules if m.startswith('confflow.calc')]"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr

    def test_importing_workflow_package_stays_lazy(self) -> None:
        script = (
            "import sys; import confflow.workflow; "
            "assert 'confflow.workflow.engine' not in sys.modules, 'eager engine import'; "
            "from confflow.workflow import run_workflow; "
            "assert callable(run_workflow); "
            "assert 'confflow.workflow.engine' in sys.modules"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr


class TestPackaging:
    """The V4 packages must ship in wheels."""

    def test_packages_are_discoverable(self) -> None:
        import setuptools

        packages = set(setuptools.find_packages(where=str(REPO_ROOT)))
        for expected in (
            "confflow.domain",
            "confflow.execution",
            "confflow.workflow.v4",
            "confflow.persistence",
            "confflow.programs",
            "confflow.remote",
        ):
            assert expected in packages, expected

    def test_no_namespace_package_gaps(self) -> None:
        for path in (DOMAIN_ROOT, EXECUTION_ROOT, V4_ROOT, REMOTE_ROOT):
            assert (path / "__init__.py").is_file(), path


class TestSchemaHasNoHiddenCleanup:
    """The V4 schema has no hidden auto-clean semantics."""

    def test_schema_text_has_no_cleanup_vocabulary(self) -> None:
        from confflow.workflow.v4 import build_workflow_json_schema

        text = repr(build_workflow_json_schema()).lower()
        for forbidden in ("auto_clean", "delete_work_dir", "clean_opts", "ibkout"):
            assert forbidden not in text, forbidden

    def test_calculation_model_exposes_only_the_frozen_blocks(self) -> None:
        from confflow.workflow.v4.schema import CalculationModel

        assert set(CalculationModel.model_fields) == {
            "program",
            "role",
            "execution_adapter",
            "result_profile",
            "native",
            "checks",
            "check_params",
            "recovery",
            "seed",
            "overrides",
        }


@pytest.mark.parametrize("module", sorted(FORBIDDEN_LEGACY_MODULES))
def test_forbidden_module_is_not_importable_from_v4_root(module: str) -> None:
    """Sanity: the forbidden list refers to modules that actually exist today."""
    base = REPO_ROOT / Path(module.replace(".", "/"))
    assert base.with_suffix(".py").exists() or (base / "__init__.py").exists(), module


#: New V4-5 production modules.  Every entry must live under one of the
#: scanned production-core roots above (so the import, symbol, and filename
#: gates cover it automatically) and must import without pulling legacy
#: runtimes (checked in a subprocess below).
V45_MODULES = (
    "confflow.execution.output_identity",
    "confflow.execution.execution_adapters",
    "confflow.execution.multi_output",
    "confflow.execution.named_structures",
    "confflow.execution.profile_ensemble",
    "confflow.execution.profile_path_endpoints",
    "confflow.execution.atom_mapping",
    "confflow.programs.orca.path",
    "confflow.programs.orca.goat",
    "confflow.programs.orca.ensemble_parse",
    "confflow.programs.orca.neb",
    "confflow.programs.gaussian.path",
    "confflow.programs.gaussian.named",
)

_V45_SCAN_ROOTS = (
    DOMAIN_ROOT,
    EXECUTION_ROOT,
    V4_ROOT,
    PERSISTENCE_ROOT,
    PROGRAMS_ROOT,
    REMOTE_ROOT,
)


class TestV45ModuleCoverage:
    """All new V4-5 production modules sit inside the debt-gate scan roots."""

    def test_v45_modules_exist(self) -> None:
        for module in V45_MODULES:
            base = REPO_ROOT / Path(module.replace(".", "/"))
            assert base.with_suffix(".py").is_file(), module

    def test_v45_modules_are_covered_by_scan_roots(self) -> None:
        for module in V45_MODULES:
            path = REPO_ROOT / Path(module.replace(".", "/")).with_suffix(".py")
            assert any(path == root or root in path.parents for root in _V45_SCAN_ROOTS), module

    def test_v45_modules_face_no_legacy_imports(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for module in V45_MODULES:
            path = REPO_ROOT / Path(module.replace(".", "/")).with_suffix(".py")
            for imported, lineno in _imports(path):
                absolute = _resolve_import(path, imported)
                if absolute.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    offenders.append((module, absolute, lineno))
        assert offenders == []


def _resolve_import(path: Path, raw: str) -> str:
    """Resolve a possibly relative import of *path* to an absolute module."""
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


_TASK_DISPATCH_MARKERS = ("IRC", "QST", "NEB", "GOAT")


def _task_enum_members(tree: ast.AST) -> set[str]:
    """Return Enum member names carrying task-type markers (e.g. ``IRC``)."""
    members: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(
            getattr(base, "attr", "") == "Enum" or getattr(base, "id", "") == "Enum"
            for base in node.bases
        ):
            continue
        for statement in node.body:
            targets: list[ast.AST] = []
            if isinstance(statement, ast.Assign):
                targets = list(statement.targets)
            elif isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
            for target in targets:
                for child in ast.walk(target):
                    if (
                        isinstance(child, ast.Name)
                        and isinstance(child.ctx, ast.Store)
                        and any(marker in child.id for marker in _TASK_DISPATCH_MARKERS)
                    ):
                        members.add(child.id)
    return members


def _task_dispatch_offenders(source: str) -> list[tuple[int, str]]:
    """Flag ``if``/``while``/``assert``/``match`` tests on task-enum members.

    Only references to Enum member attributes/names count.  Scientific
    keyword *strings* (``"IRC=RCFC"``) and role strings
    (``"path_endpoint_forward"``) are constants and never trip this scan.
    """
    tree = ast.parse(source)
    members = _task_enum_members(tree)
    if not members:
        return []
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            test: ast.AST | None = node.test
        elif isinstance(node, ast.Match):
            test = node.subject
        else:
            continue
        for child in ast.walk(test):
            if isinstance(child, ast.Attribute) and child.attr in members:
                offenders.append((node.lineno, child.attr))
            elif (
                isinstance(child, ast.Name)
                and child.id in members
                and not isinstance(child.ctx, ast.Store)
            ):
                offenders.append((node.lineno, child.id))
    return offenders


_FIXTURE_TASK_DISPATCH = """from enum import Enum


class TaskName(str, Enum):
    IRC = "irc"
    QST2 = "qst2"


def run(task: TaskName) -> int:
    if task == TaskName.IRC:
        return 1
    return 0
"""

_FIXTURE_SCIENTIFIC_STRINGS = """KEYWORD = "IRC=RCFC"
ROLE = "path_endpoint_forward"


def is_irc(keyword: str) -> bool:
    if keyword == "IRC=RCFC":
        return True
    return False
"""


class TestNoTaskDispatch:
    """No ``if`` dispatch on task-type enums in execution + programs."""

    def test_scanner_flags_task_enum_dispatch(self) -> None:
        offenders = _task_dispatch_offenders(_FIXTURE_TASK_DISPATCH)
        assert offenders, "scanner must flag `if task == TaskName.IRC`"
        assert offenders[0][1] == "IRC"

    def test_scanner_ignores_scientific_strings(self) -> None:
        assert _task_dispatch_offenders(_FIXTURE_SCIENTIFIC_STRINGS) == []

    def test_no_task_enum_dispatch_in_tree(self) -> None:
        offenders: list[tuple[str, int, str]] = []
        for root in (EXECUTION_ROOT, PROGRAMS_ROOT):
            for path in _iter_python_files(root):
                for lineno, member in _task_dispatch_offenders(path.read_text(encoding="utf-8")):
                    offenders.append((str(path.relative_to(REPO_ROOT)), lineno, member))
        assert offenders == []


_FILENAME_ATTRIBUTE_TOKENS = ("basename", "splitext", "stem", "suffix")
_FILENAME_NAME_TOKENS = ("basename", "splitext")


def _filename_pairing_offenders(source: str) -> list[tuple[int, str]]:
    """Flag filename-idiom tokens (cross-set pairing by file name).

    ``os.path.basename`` / ``splitext`` / pathlib ``.stem`` / ``.suffix``
    must never appear as code in the pairing-sensitive roots: downstream
    pairing uses identity, group, and subject, never file names.
    """
    tree = ast.parse(source)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FILENAME_ATTRIBUTE_TOKENS:
            offenders.append((node.lineno, node.attr))
        elif (
            isinstance(node, ast.Name)
            and node.id in _FILENAME_NAME_TOKENS
            and not isinstance(node.ctx, ast.Store)
        ):
            offenders.append((node.lineno, node.id))
    return offenders


def _range_ordinal_pairing_offenders(source: str) -> list[tuple[str, int, str]]:
    """Flag ``range``-ordinal subscripts shared across distinct collections.

    The banned idiom is positional cross-set pairing (``a[i]`` matched with
    ``b[i]`` for a ``range`` ordinal ``i``).  Dict/keyed access and the
    documented explicit-permutation application in
    ``confflow/execution/atom_mapping.py`` are not flagged by construction
    (the former never shares a ``range`` ordinal; the latter is allowlisted
    at the call site).
    """
    tree = ast.parse(source)
    offenders: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for loop in [child for child in ast.walk(node) if isinstance(child, ast.For)]:
            iterator = loop.iter
            if not (isinstance(iterator, ast.Call) and getattr(iterator.func, "id", "") == "range"):
                continue
            ordinals = {
                child.id
                for child in ast.walk(loop.target)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
            }
            if not ordinals:
                continue
            by_index: dict[str, set[str]] = {}
            for child in ast.walk(loop):
                if isinstance(child, ast.Subscript):
                    by_index.setdefault(ast.unparse(child.slice), set()).add(
                        ast.unparse(child.value)
                    )
            for index, bases in by_index.items():
                if len(bases) >= 2 and index in ordinals:
                    offenders.append((node.name, loop.lineno, index))
    return offenders


_FIXTURE_FILENAME_PAIRING = """import os


def pair(left: str, right: str) -> bool:
    return os.path.basename(left) == os.path.basename(right)
"""

_FIXTURE_ORDINAL_PAIRING = """def pair(left: list, right: list) -> list:
    paired = []
    for i in range(len(left)):
        paired.append((left[i], right[i]))
    return paired
"""

#: Modules where ordinal-within-item usage is documented and allowed:
#: ``member_index``/``point_ordinal`` are native member identities folded
#: into deterministic entity ids, never cross-set pairing keys.
_ORDINAL_WITHIN_ITEM_FILES = frozenset(
    {
        "confflow/execution/native.py",
        "confflow/execution/output_identity.py",
        "confflow/execution/profile_ensemble.py",
        "confflow/execution/profile_path_endpoints.py",
    }
)

#: The single sanctioned ``range``-ordinal application: an explicitly
#: declared bijective atom permutation applied element-wise (the opposite
#: of guessed cross-set pairing).
_RANGE_ORDINAL_ALLOWLIST = frozenset({"confflow/execution/atom_mapping.py"})


class TestNoFilenameOrdinalPairing:
    """No filename/ordinal cross-set pairing in workflow/v4 + execution."""

    def test_scanners_flag_pairing_idioms(self) -> None:
        assert _filename_pairing_offenders(_FIXTURE_FILENAME_PAIRING), "basename scanner must trip"
        assert _range_ordinal_pairing_offenders(
            _FIXTURE_ORDINAL_PAIRING
        ), "ordinal scanner must trip"

    def test_no_filename_idioms_in_tree(self) -> None:
        offenders: list[tuple[str, int, str]] = []
        for root in (V4_ROOT, EXECUTION_ROOT):
            for path in _iter_python_files(root):
                for lineno, token in _filename_pairing_offenders(path.read_text(encoding="utf-8")):
                    offenders.append((str(path.relative_to(REPO_ROOT)), lineno, token))
        assert offenders == []

    def test_no_range_ordinal_pairing_in_tree(self) -> None:
        offenders: list[tuple[str, str, int, str]] = []
        for root in (V4_ROOT, EXECUTION_ROOT):
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
        for root in (V4_ROOT, EXECUTION_ROOT):
            for path in _iter_python_files(root):
                relative = str(path.relative_to(REPO_ROOT))
                text = _code_text_only(path.read_text(encoding="utf-8"))
                if "member_index" in text or "point_ordinal" in text:
                    if relative not in _ORDINAL_WITHIN_ITEM_FILES:
                        offenders.append(relative)
        assert offenders == []


class TestV45Packaging:
    """All new V4-5 modules import without pulling legacy runtimes."""

    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    @pytest.mark.parametrize("module", sorted(V45_MODULES))
    def test_module_imports_without_legacy(self, module: str) -> None:
        script = (
            f"import sys; import {module}; "
            "forbidden = [m for m in sys.modules if m == 'confflow.core' "
            "or m.startswith('confflow.config') or m.startswith('confflow.calc') "
            "or (m.startswith('confflow.workflow.') "
            "and not m.startswith('confflow.workflow.v4'))]; "
            "assert not forbidden, forbidden"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr
