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
import importlib
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
PRODUCER_ROOT = PACKAGE_ROOT / "producer"
ANALYSIS_ROOT = PACKAGE_ROOT / "analysis"
APPLICATION_ROOT = PACKAGE_ROOT / "application"

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

#: Extended formal V4 production roots (Architecture Diet PR-0).
#:
#: The original gate only scanned domain/execution/workflow.v4/persistence/
#: programs/remote.  The producer contract, the analysis engine, and the
#: formal application/V4 CLI entrypoints are equally part of the V4
#: production closure and must not regrow legacy-runtime imports.
EXTENDED_V4_PRODUCTION_ROOTS = (
    PRODUCER_ROOT,
    ANALYSIS_ROOT,
)

#: Individual formal V4 production files outside the package roots above.
EXTENDED_V4_PRODUCTION_FILES = (
    PACKAGE_ROOT / "v4cli.py",
    APPLICATION_ROOT / "v4_entry.py",
    APPLICATION_ROOT / "v4_run.py",
)

#: Formal V4 runtime paths added to the IMPORT-ONLY legacy gate.  Their public
#: compatibility signatures legitimately carry historical parameter names
#: (``input_xyz`` on the service boundary), so the symbol/filename scans stay
#: on the roots above while the import ban covers these paths too.
IMPORT_ONLY_V4_PRODUCTION_ROOTS = (APPLICATION_ROOT / "execution",)
IMPORT_ONLY_V4_PRODUCTION_FILES = (PACKAGE_ROOT / "control_worker.py",)

#: Import prefixes that must never (re)appear in a formal V4 production
#: file.  ``confflow.core.exceptions`` is intentionally NOT in the exact
#: forbidden-module tuple below: the formal entry module needs the shared
#: ``ConfFlowError`` type.  Every other legacy ``confflow.core`` facade is
#: forbidden.
FORBIDDEN_LEGACY_RUNTIME_PREFIXES = (
    "confflow.calc",
    "confflow.blocks",
    "confflow.confts",
    "confflow.cli",
    "confflow.main",
    "confflow.shared.config_validation",
    "confflow.workflow",
)

#: ``confflow.workflow.v4`` is the V4 engine and stays allowed, so the bare
#: ``confflow.workflow`` prefix above needs exactly one exemption.
_ALLOWED_WORKFLOW_V4_PREFIX = "confflow.workflow.v4"

#: Exact legacy modules that must never be imported by an extended root.
#: ``confflow.core.exceptions`` is excluded (shared error type, see above).
EXTENDED_FORBIDDEN_LEGACY_MODULES = tuple(
    module for module in FORBIDDEN_LEGACY_MODULES if module != "confflow.core.exceptions"
)

#: The only configuration module the producer may import: the dependency-free
#: schema authority (Architecture Diet PR-2).  Before PR-2 the producer
#: imported three constants from ``confflow.config.canonical``; that debt is
#: removed and must not come back.
PRODUCER_CONFIG_AUTHORITY_IMPORTS = frozenset({"confflow.config.contract_schemas"})

#: The single file-scoped legacy-token exemption:
#: ``formal_v4_runner`` accepts the historical keyword surface
#: (``input_xyz``) at the formal boundary by design.  The name is a
#: compatibility parameter of the one documented drop-in runner, not a
#: legacy execution path.
EXTENDED_LEGACY_TOKEN_EXEMPTIONS: dict[str, frozenset[str]] = {
    "confflow/application/v4_entry.py": frozenset({"input_xyz"}),
}

#: Exact transitive legacy-dependency set pulled by ``import confflow.producer``.
#:
#: Architecture Diet PR-2 drove this baseline to EMPTY: the producer now reads
#: the three schema identifiers from the dependency-free
#: :mod:`confflow.config.contract_schemas` authority instead of importing
#: ``confflow.config.canonical``.  The equality guard stays so any future
#: legacy dependency fails immediately; ``confflow.config`` (thin shell),
#: ``confflow.config.contract_schemas`` (the authority), and the bare
#: ``confflow.workflow`` parent package (required to import
#: ``confflow.workflow.v4``) are intentionally not debt.
KNOWN_PRODUCER_LEGACY_IMPORTS: frozenset[str] = frozenset()


def _is_legacy_producer_dependency(module: str) -> bool:
    """Return whether *module* is a legacy dependency of ``confflow.producer``.

    The dependency-free schema authority (``confflow.config`` shell +
    ``confflow.config.contract_schemas``) and the ``confflow.workflow``
    parent shell of ``confflow.workflow.v4`` are not legacy dependencies.
    """
    if module == "confflow.config.canonical" or module.startswith("confflow.config.canonical."):
        return True
    if module == "confflow.config.models":
        return True
    if module in {"confflow.core.models", "confflow.core.types", "confflow.core.validation"}:
        return True
    if module.startswith("confflow.workflow.") and not module.startswith(
        _ALLOWED_WORKFLOW_V4_PREFIX
    ):
        return True
    return module.startswith(("confflow.calc", "confflow.blocks", "confflow.confts"))


#: Legacy modules deleted by the architecture diet (PR-1 dead-code removal).
#: They must stay absent: recreating one would silently revive a forbidden
#: import path.  Additions here happen in the same commit that deletes the
#: file, never speculatively.
REMOVED_LEGACY_MODULES: frozenset[str] = frozenset(
    {
        "confflow.workflow.supervisor",
        "confflow.workflow.rerun_failed",
        "confflow.calc.async_exec",
        # PR-4: retired V2/V3 execution runtime.
        "confflow.workflow.engine",
        "confflow.workflow.state",
        "confflow.workflow.v3_runtime",
        "confflow.workflow.step_handlers",
        "confflow.workflow.binding_v2",
        "confflow.workflow.stats",
        "confflow.workflow.presenter",
        "confflow.workflow.execution_context",
        "confflow.workflow.finalize",
        "confflow.workflow.v3_dataflow",
        "confflow.workflow.resume_validation",
        "confflow.workflow.runtime_context",
        "confflow.workflow.dag",
        "confflow.workflow.dag.explicit",
        "confflow.workflow.dag.legacy",
    }
)

#: V2/V3 execution-runtime modules retired by Architecture Diet PR-4.  They
#: must stay physically absent, unimportable, and unreferenced by the V4
#: production sources.  The historically public names resolve to fail-closed
#: retirement stubs (``confflow.workflow._retired_runtime``), never to code.
RETIRED_RUNTIME_MODULES: tuple[str, ...] = (
    "confflow.workflow.engine",
    "confflow.workflow.state",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.step_handlers",
    "confflow.workflow.binding_v2",
    "confflow.workflow.stats",
    "confflow.workflow.presenter",
    "confflow.workflow.execution_context",
    "confflow.workflow.finalize",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.resume_validation",
    "confflow.workflow.runtime_context",
    "confflow.workflow.dag",
    "confflow.workflow.dag.explicit",
    "confflow.workflow.dag.legacy",
)

#: Retained-by-design compatibility modules that are explicitly allowed to
#: be absent from the source tree.  Empty today; a module moves here only
#: with a written compatibility decision.
RETAINED_COMPAT_ALLOWLIST: frozenset[str] = frozenset()


def _legacy_module_exists(module: str) -> bool:
    base = REPO_ROOT / Path(module.replace(".", "/"))
    return base.with_suffix(".py").exists() or (base / "__init__.py").exists()


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


def _iter_extended_production_files() -> list[Path]:
    """Return the extended formal V4 production files to scan."""
    files: list[Path] = []
    for root in EXTENDED_V4_PRODUCTION_ROOTS:
        files.extend(_iter_python_files(root))
    files.extend(EXTENDED_V4_PRODUCTION_FILES)
    return sorted(files)


def _iter_import_only_production_files() -> list[Path]:
    """Return the formal service/control paths covered by the import gate."""
    files: list[Path] = []
    for root in IMPORT_ONLY_V4_PRODUCTION_ROOTS:
        files.extend(_iter_python_files(root))
    files.extend(IMPORT_ONLY_V4_PRODUCTION_FILES)
    return sorted(files)


def _is_forbidden_legacy_runtime_import(path: Path, absolute: str) -> bool:
    """Return whether *absolute* is a forbidden legacy import for *path*.

    The producer's dependency-free schema authority
    (``confflow.config.contract_schemas``) is the single configuration import
    the extended gate allows, and only from the ``confflow/producer``
    package; every other configuration import from any extended root fails.
    ``confflow.workflow.v4`` is always allowed.
    """
    if not absolute.startswith("confflow"):
        return False
    if absolute == _ALLOWED_WORKFLOW_V4_PREFIX or absolute.startswith(
        _ALLOWED_WORKFLOW_V4_PREFIX + "."
    ):
        return False
    if absolute.startswith("confflow.config"):
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts and relative.parts[0] == "producer":
            return absolute not in PRODUCER_CONFIG_AUTHORITY_IMPORTS
        return True
    if absolute in EXTENDED_FORBIDDEN_LEGACY_MODULES:
        return True
    return absolute.startswith(FORBIDDEN_LEGACY_RUNTIME_PREFIXES)


class TestExtendedV4ProductionRoots:
    """The formal V4 production closure beyond the original six scan roots.

    Architecture Diet PR-0: producer, analysis, and the formal application/V4
    CLI entrypoints are covered by the same import, symbol, and filename
    guardrails as the original V4 core roots.
    """

    def test_extended_roots_have_no_legacy_runtime_imports(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for path in _iter_extended_production_files():
            for raw, lineno in _imports(path):
                absolute = _resolve_import(path, raw)
                if _is_forbidden_legacy_runtime_import(path, absolute):
                    offenders.append((str(path.relative_to(REPO_ROOT)), absolute, lineno))
        assert offenders == []

    def test_extended_roots_have_no_forbidden_symbols(self) -> None:
        offenders: list[tuple[str, str]] = []
        for path in _iter_extended_production_files():
            relative = str(path.relative_to(REPO_ROOT))
            exempt = EXTENDED_LEGACY_TOKEN_EXEMPTIONS.get(relative, frozenset())
            symbols = _code_symbols(path)
            for forbidden in FORBIDDEN_SYMBOLS:
                if forbidden in symbols and forbidden not in exempt:
                    offenders.append((relative, forbidden))
        assert offenders == []

    def test_extended_roots_have_no_legacy_filename_contracts(self) -> None:
        offenders: list[tuple[str, str]] = []
        for path in _iter_extended_production_files():
            relative = str(path.relative_to(REPO_ROOT))
            exempt = EXTENDED_LEGACY_TOKEN_EXEMPTIONS.get(relative, frozenset())
            text = _code_text_only(path.read_text(encoding="utf-8"))
            for token in TestStaticLegacyFilenameContracts.LEGACY_FILENAME_TOKENS:
                if token in text and token not in exempt:
                    offenders.append((relative, token))
        assert offenders == []

    def test_formal_service_and_control_paths_have_no_legacy_runtime_imports(self) -> None:
        """The formal service/control runtime never imports legacy roots.

        Import-only coverage: ``application/execution`` and
        ``control_worker.py`` are the formal V4 runtime service and control
        paths (the PR-3 lazy-facade fix targets exactly this closure), while
        their compatibility signatures keep the historical parameter names.
        """
        offenders: list[tuple[str, str, int]] = []
        for path in _iter_import_only_production_files():
            for raw, lineno in _imports(path):
                absolute = _resolve_import(path, raw)
                if _is_forbidden_legacy_runtime_import(path, absolute):
                    offenders.append((str(path.relative_to(REPO_ROOT)), absolute, lineno))
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
            "import sys\n"
            "import confflow.workflow\n"
            "assert 'confflow.workflow.engine' not in sys.modules, 'eager engine import'\n"
            "from confflow.workflow import run_workflow\n"
            "assert callable(run_workflow)\n"
            "assert 'confflow.workflow.engine' not in sys.modules, 'retired engine loaded'\n"
            "try:\n"
            "    run_workflow()\n"
            "except RuntimeError as exc:\n"
            "    assert 'retired' in str(exc), exc\n"
            "else:\n"
            "    raise AssertionError('retired run_workflow must fail closed')\n"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr


class TestRetiredRuntimeBoundary:
    """The retired V2/V3 execution runtime must stay physically absent.

    Architecture Diet PR-4 removed the runtime; this gate makes sure neither a
    source file nor an import edge nor a lazy export can resurrect it.
    """

    def test_retired_modules_are_not_importable(self) -> None:
        still_present: list[str] = []
        for module in RETIRED_RUNTIME_MODULES:
            try:
                importlib.import_module(module)
            except ModuleNotFoundError:
                continue
            except Exception as exc:  # pragma: no cover - import error is enough
                still_present.append(f"{module}: {type(exc).__name__}")
                continue
            still_present.append(module)
        assert still_present == []

    def test_retired_modules_are_absent_from_disk(self) -> None:
        present = [module for module in RETIRED_RUNTIME_MODULES if _legacy_module_exists(module)]
        assert present == []

    def test_retired_modules_are_not_imported_by_v4_sources(self) -> None:
        offenders: list[tuple[str, str, int]] = []
        for root in (
            DOMAIN_ROOT,
            EXECUTION_ROOT,
            V4_ROOT,
            PERSISTENCE_ROOT,
            PROGRAMS_ROOT,
            REMOTE_ROOT,
            PRODUCER_ROOT,
            ANALYSIS_ROOT,
        ):
            for path in _iter_python_files(root):
                for raw, lineno in _imports(path):
                    absolute = _resolve_import(path, raw)
                    if any(
                        absolute == module or absolute.startswith(module + ".")
                        for module in RETIRED_RUNTIME_MODULES
                    ):
                        offenders.append((str(path.relative_to(REPO_ROOT)), absolute, lineno))
        assert offenders == []

    def test_retirement_stubs_fail_closed(self) -> None:
        from confflow.workflow import _retired_runtime

        assert _retired_runtime.RETIRED_NAMES
        for name in _retired_runtime.RETIRED_NAMES:
            stub = getattr(_retired_runtime, name)
            with pytest.raises(_retired_runtime.RetiredRuntimeError):
                stub()


class TestProducerImportIsolation:
    """Producer import isolation (Architecture Diet PR-0 baseline → PR-2 hard).

    PR-0 pinned the transitive legacy set pulled by ``import
    confflow.producer`` (31 modules of the V1/V2/V3 configuration tree).  PR-2
    decoupled the producer from that tree: the three published schema
    identifiers now live in the dependency-free
    :mod:`confflow.config.contract_schemas` authority, and
    :data:`KNOWN_PRODUCER_LEGACY_IMPORTS` is empty.  The equality guard stays:
    any new legacy dependency fails immediately, and the canonical
    implementation tree must not be loaded at all.
    """

    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_producer_legacy_import_debt_is_empty(self) -> None:
        script = (
            "import sys; import confflow.producer; "
            "print(chr(10).join(sorted(m for m in sys.modules if m.startswith('confflow'))))"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr
        observed = frozenset(
            module
            for module in result.stdout.splitlines()
            if _is_legacy_producer_dependency(module)
        )
        added = sorted(observed - KNOWN_PRODUCER_LEGACY_IMPORTS)
        removed = sorted(KNOWN_PRODUCER_LEGACY_IMPORTS - observed)
        assert observed == KNOWN_PRODUCER_LEGACY_IMPORTS, (
            "producer legacy import debt changed;\n"
            f"  added: {added}\n"
            f"  removed: {removed}\n"
            "the producer must read schema identifiers from the "
            "dependency-free authority only."
        )

    def test_producer_does_not_load_canonical_config_tree(self) -> None:
        """The canonical implementation tree never enters ``sys.modules``.

        Both formal producer entry styles are checked: the package import and
        the direct contract-module import used by JobDesk-side tooling.
        """
        for entry in (
            "import confflow.producer",
            "from confflow.producer.contract import generate_contract_bytes",
        ):
            script = (
                f"import sys; {entry}; "
                "banned = sorted(m for m in sys.modules if ("
                "m == 'confflow.config.canonical' "
                "or m.startswith('confflow.config.canonical.') "
                "or m == 'confflow.config.models')); "
                "assert not banned, banned"
            )
            result = self._run(script)
            assert result.returncode == 0, f"{entry}: {result.stderr}"

    def test_producer_does_not_pull_legacy_runtime_packages(self) -> None:
        """Hard ban: the producer must never import calc/blocks/engine at all."""
        script = (
            "import sys; import confflow.producer; "
            "banned = sorted(m for m in sys.modules if m.startswith(("
            "'confflow.calc', 'confflow.blocks', 'confflow.confts', "
            "'confflow.workflow.engine', 'confflow.workflow.v3_runtime', "
            "'confflow.workflow.v3_dataflow', 'confflow.workflow.binding_v2', "
            "'confflow.config.canonical'))); "
            "assert not banned, banned"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr

    def test_schema_authority_is_single_source_for_legacy_paths(self) -> None:
        """The legacy import paths re-export the same objects (no second copy)."""
        from confflow.config import contract_schemas
        from confflow.config.canonical.contract import CONFIGURATION_VALIDATION_SCHEMA
        from confflow.config.canonical.editor_manifest import EDITOR_MANIFEST_SCHEMA
        from confflow.config.canonical.recipes import RECIPE_CATALOG_SCHEMA

        assert CONFIGURATION_VALIDATION_SCHEMA is contract_schemas.CONFIGURATION_VALIDATION_SCHEMA
        assert EDITOR_MANIFEST_SCHEMA is contract_schemas.EDITOR_MANIFEST_SCHEMA
        assert RECIPE_CATALOG_SCHEMA is contract_schemas.RECIPE_CATALOG_SCHEMA
        assert contract_schemas.CONFIGURATION_VALIDATION_SCHEMA == (
            "confflow.configuration-validation.v1"
        )
        assert contract_schemas.EDITOR_MANIFEST_SCHEMA == "confflow.editor-manifest.v1"
        assert contract_schemas.RECIPE_CATALOG_SCHEMA == "confflow.recipe-catalog.v1"

    def test_config_package_lazy_exports_still_work(self) -> None:
        """The historical ``from confflow.config import X`` surface is intact."""
        from confflow.config import GlobalOptions, WorkflowConfig, load_workflow_model

        assert WorkflowConfig.__name__ == "WorkflowConfig"
        assert GlobalOptions.__name__ == "GlobalOptions"
        assert callable(load_workflow_model)


class TestFacadeLazyIsolation:
    """Compatibility facades stay lazy (Architecture Diet PR-3).

    ``confflow.core`` and ``confflow.application[.execution]`` keep every
    historical public name, but importing the package no longer executes the
    legacy implementation modules behind those names.
    """

    def _run(self, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_core_package_stays_lazy(self) -> None:
        script = (
            "import sys; import confflow.core; "
            "banned = sorted(m for m in sys.modules if m.startswith('confflow.config')); "
            "assert not banned, banned; "
            "assert 'confflow.core.models' not in sys.modules, 'eager core.models'; "
            "assert 'confflow.core.types' not in sys.modules, 'eager core.types'; "
            "assert 'confflow.core.validation' not in sys.modules, 'eager core.validation'"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr

    def test_application_packages_stay_lazy(self) -> None:
        script = (
            "import sys\n"
            "import confflow.application\n"
            "assert 'confflow.application.execution' not in sys.modules, "
            "'eager application.execution'\n"
            "import confflow.application.execution\n"
            "for name in ('memory', 'synthetic_producer', 'sqlite', 'service', "
            "'workflow_adapter'):\n"
            "    module = f'confflow.application.execution.{name}'\n"
            "    assert module not in sys.modules, module\n"
        )
        result = self._run(script)
        assert result.returncode == 0, result.stderr

    def test_v4_runtime_entries_do_not_load_canonical_config(self) -> None:
        for entry in (
            "import confflow.v4cli",
            "import confflow.application.v4_entry",
            "import confflow.control_worker",
        ):
            script = (
                f"import sys; {entry}; "
                "banned = sorted(m for m in sys.modules if ("
                "m == 'confflow.config.canonical' "
                "or m.startswith('confflow.config.canonical.') "
                "or m == 'confflow.core.models' "
                "or m == 'confflow.shared.config_validation')); "
                "assert not banned, banned"
            )
            result = self._run(script)
            assert result.returncode == 0, f"{entry}: {result.stderr}"

    def test_core_public_surface_still_importable(self) -> None:
        from confflow.core import (
            HARTREE_TO_KCALMOL,
            PERIODIC_SYMBOLS,
            TaskContext,
            ValidationError,
            get_atomic_number,
            validate_positive,
        )

        assert TaskContext.__name__ == "TaskContext"
        assert ValidationError.__name__ == "ValidationError"
        assert callable(get_atomic_number)
        assert callable(validate_positive)
        assert len(PERIODIC_SYMBOLS) > 0
        assert isinstance(HARTREE_TO_KCALMOL, float)

    def test_application_public_surface_still_importable(self) -> None:
        from confflow.application import ExecutionService as AppExecutionService
        from confflow.application.execution import (
            ExecutionService,
            InMemoryExecutionRepository,
            RunPaths,
            RunState,
            SyntheticProducerExecutor,
            build_workflow_service,
        )

        assert ExecutionService is AppExecutionService
        assert RunState.__name__ == "RunState"
        assert RunPaths.__name__ == "RunPaths"
        assert InMemoryExecutionRepository.__name__ == "InMemoryExecutionRepository"
        assert SyntheticProducerExecutor.__name__ == "SyntheticProducerExecutor"
        assert callable(build_workflow_service)


class TestPackaging:
    """The V4 packages must ship in wheels."""

    def test_packages_are_discoverable(self) -> None:
        """Every shipped package is discoverable by its ``__init__.py``.

        Discovery is a pure path walk (the same rule setuptools uses for a
        regular package): importing ``setuptools`` here would couple the
        gate to the host's distutils shim state, which is not a property of
        this repository.
        """
        packages = {
            ".".join(init.parent.relative_to(REPO_ROOT).parts)
            for init in PACKAGE_ROOT.rglob("__init__.py")
            if "__pycache__" not in init.parts
        }
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


@pytest.mark.parametrize(
    "module",
    sorted(set(FORBIDDEN_LEGACY_MODULES) | REMOVED_LEGACY_MODULES),
)
def test_legacy_module_inventory_is_intentional(module: str) -> None:
    """Forbidden legacy modules exist unless the diet explicitly removed them.

    Deleted modules must stay deleted (absence, not merely "must not be
    imported"); retained compatibility modules must still exist unless a
    written decision places them in :data:`RETAINED_COMPAT_ALLOWLIST`.
    """
    if module in REMOVED_LEGACY_MODULES:
        assert not _legacy_module_exists(module), f"{module} was removed and must stay absent"
        return
    if module in RETAINED_COMPAT_ALLOWLIST:
        return
    assert _legacy_module_exists(module), module


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
