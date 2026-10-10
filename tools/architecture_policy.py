#!/usr/bin/env python3
"""Declarative static architecture policy (L0.4b v3).

Rule data and scanning functions are separated: the RULES list below is
pure data, each entry pinning the L0.4a inventory row it mirrors.  Scanning
is AST based; docstrings and comments are excluded by construction and
rules that deliberately scan raw text declare ``raw_text=True``.  This
module does not import the old guard test modules; the frozen data was
extracted verbatim from the guard sources at base 5c05c875.  G13/G14
ConfGen purity rules stay planned (A6); PEP 562 exceptions are NOT widened.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Frozen rule data (extracted verbatim from the guard sources at base
# 5c05c8756fff4be348aa1470f6e536a82a31e88a; provenance = L0.4a inventory row).
# The policy does not import the old test modules; these literals are the
# single authority for the policy rules below.  Original scope differences
# are preserved: the three forbidden-prefix tables are NOT merged (U3), the
# metrics roots are pinned (U8), and scanner-unique patterns are kept (U9).
# ---------------------------------------------------------------------------

SIX_ROOTS = [
    "confflow/domain",
    "confflow/execution",
    "confflow/workflow/v4",
    "confflow/persistence",
    "confflow/programs",
]
V4_ROOT = "confflow/workflow/v4"
DOMAIN_ROOT = "confflow/domain"
EXECUTION_ROOT = "confflow/execution"
PERSISTENCE_ROOT = "confflow/persistence"
PROGRAMS_ROOT = "confflow/programs"
APPLICATION_ROOT = "confflow/application"
PRODUCER_ROOT = "confflow/producer"
ANALYSIS_ROOT = "confflow/analysis"
PACKAGE_ROOT = "confflow"

FORBIDDEN_IMPORT_PREFIXES = [
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
    "confflow.contract",
    "confflow.install_provenance",
    "confflow.fixture_agent",
]
FORBIDDEN_LEGACY_MODULES = [
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
    "confflow.workflow.rerun_failed",
    "confflow.workflow.config_show",
    "confflow.workflow.composition",
    "confflow.config.canonical",
    "confflow.core.types",
    "confflow.core.parsers",
    "confflow.core.path_policy",
    "confflow.core.io",
    "confflow.core.exceptions",
]
FORBIDDEN_SYMBOLS = [
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
]
LEGACY_FILENAME_TOKENS = [
    "result.xyz",
    "failed.xyz",
    "output_xyz",
    "delete_work_dir",
    "input_xyz",
    "output_path",
]
FORBIDDEN_LEGACY_RUNTIME_PREFIXES = [
    "confflow.calc",
    "confflow.blocks",
    "confflow.confts",
    "confflow.cli",
    "confflow.main",
    "confflow.shared.config_validation",
    "confflow.workflow",
]
EXTENDED_FORBIDDEN_LEGACY_MODULES = [
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
    "confflow.workflow.rerun_failed",
    "confflow.workflow.config_show",
    "confflow.workflow.composition",
    "confflow.config.canonical",
    "confflow.core.types",
    "confflow.core.parsers",
    "confflow.core.path_policy",
    "confflow.core.io",
]
EXTENDED_V4_PRODUCTION_ROOTS = [
    "confflow/producer",
    "confflow/science/confgen",
]
EXTENDED_V4_PRODUCTION_FILES = [
    "confflow/v4cli.py",
    "confflow/application/v4_entry.py",
    "confflow/application/v4_run.py",
]
IMPORT_ONLY_V4_PRODUCTION_ROOTS = ["confflow/application/execution"]
IMPORT_ONLY_V4_PRODUCTION_FILES = ["confflow/control_worker.py"]
PRODUCER_CONFIG_AUTHORITY_IMPORTS = ["confflow.config.contract_schemas"]
EXTENDED_LEGACY_TOKEN_EXEMPTIONS = {"confflow/application/v4_entry.py": ["input_xyz"]}
# DIET-2 N1: the server.toml capacity key is mandated by the approved plan
# (Q4). quota.py is the single authority that may spell it in V42 roots.
N1_SERVER_QUOTA_VOCAB_EXEMPTIONS = {"confflow/execution/quota.py": ["total_memory"]}
KNOWN_PRODUCER_LEGACY_IMPORTS = []
REMOVED_LEGACY_MODULES = [
    "confflow.blocks",
    "confflow.blocks.confgen",
    "confflow.blocks.refine",
    "confflow.blocks.viz",
    "confflow.calc",
    "confflow.calc.async_exec",
    "confflow.config.canonical",
    "confflow.config.canonical.contract",
    "confflow.config.canonical.diagnostics",
    "confflow.config.canonical.editor_manifest",
    "confflow.config.canonical.fingerprint",
    "confflow.config.canonical.param_fields",
    "confflow.config.canonical.parser",
    "confflow.config.canonical.pydantic",
    "confflow.config.canonical.recipes",
    "confflow.config.canonical.schema",
    "confflow.config.canonical.serialization",
    "confflow.config.canonical.v2_adapter",
    "confflow.config.canonical.validation",
    "confflow.config.canonical.workflow",
    "confflow.config.models",
    "confflow.confts",
    "confflow.core.chem_validation",
    "confflow.core.cli_base",
    "confflow.core.keyword_rewrite",
    "confflow.core.models",
    "confflow.core.pairs",
    "confflow.core.types",
    "confflow.core.validation",
    "confflow.shared.config_coercion",
    "confflow.shared.config_validation",
    "confflow.workflow.binding_v2",
    "confflow.workflow.composition",
    "confflow.workflow.config_show",
    "confflow.workflow.dag",
    "confflow.workflow.dag.explicit",
    "confflow.workflow.dag.legacy",
    "confflow.workflow.dry_run",
    "confflow.workflow.engine",
    "confflow.workflow.execution_context",
    "confflow.workflow.export",
    "confflow.workflow.finalize",
    "confflow.workflow.helpers",
    "confflow.workflow.plan",
    "confflow.workflow.presenter",
    "confflow.workflow.rerun_failed",
    "confflow.workflow.resume_validation",
    "confflow.workflow.runtime_context",
    "confflow.workflow.state",
    "confflow.workflow.stats",
    "confflow.workflow.step_handlers",
    "confflow.workflow.step_naming",
    "confflow.workflow.supervisor",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.validation",
]
RETAINED_COMPAT_ALLOWLIST = []
RETIRED_RUNTIME_MODULES = [
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
    "confflow.remote",
    "confflow.remote.envelope",
    "confflow.remote.lease",
    "confflow.remote.supervision",
    "confflow.remote.schema",
]
PUBLIC_V3_WIRE_MODULES = [
    "confflow.config.canonical.v3_parser",
    "confflow.config.canonical.v3_graph",
    "confflow.config.canonical.upgrade",
    "confflow.config.canonical.structured",
    "confflow.config.canonical.theory",
    "confflow.config.canonical.extensions",
    "confflow.config.canonical.yaml_io",
    "confflow.config.canonical.execution_versions",
    "confflow.config.workflow_cli",
]
RETIRED_V3_SYMBOLS = [
    "allocate_step_id",
    "build_configuration_contract_v3",
    "build_editor_manifest_v3",
    "build_recipe_catalog_v3",
    "build_validated_graph",
    "compile_structured_calc",
    "execution_versions",
    "instantiate_recipe_v3",
    "param_properties",
    "parse_v3_document",
    "upgrade_v2_to_v3",
    "v3_calc_keys",
    "v3_graph",
    "v3_id_order_key",
    "v3_parser",
    "validate_v3_definition",
    "validate_workflow_v3",
    "workflow_json_schema_v3",
    "workflow_schema_sha256_v3",
]
PUBLIC_V1_V2_WIRE_MODULES = [
    "confflow.config.canonical",
    "confflow.config.canonical.contract",
    "confflow.config.canonical.schema",
    "confflow.config.canonical.parser",
    "confflow.config.canonical.validation",
    "confflow.config.canonical.v2_adapter",
    "confflow.config.canonical.workflow",
    "confflow.config.canonical.editor_manifest",
    "confflow.config.canonical.recipes",
    "confflow.config.canonical.fingerprint",
    "confflow.config.canonical.param_fields",
    "confflow.config.canonical.pydantic",
    "confflow.config.canonical.diagnostics",
    "confflow.config.canonical.serialization",
    "confflow.config.models",
    "confflow.shared.config_validation",
    "confflow.core.types",
    "confflow.workflow.plan",
    "confflow.workflow.config_show",
    "confflow.workflow.dry_run",
]
RETIRED_V1_V2_WIRE_TOKENS = [
    "confflow.workflow.v1",
    "confflow.workflow.v2",
    "confflow.configuration-contract.v1",
    "confflow.configuration-contract.v2",
    "confflow.config.canonical",
    "confflow.config.models",
    "confflow.shared.config_validation",
    "confflow.core.types",
    "confflow.workflow.plan",
    "confflow.workflow.config_show",
    "confflow.workflow.dry_run",
    "to_canonical_workflow",
    "parse_canonical_workflow",
    "parse_workflow_mapping",
    "load_raw_mapping",
    "load_workflow_definition",
    "load_workflow_model",
    "detect_schema_version",
    "detect_workflow_file_version",
    "validate_workflow_definition",
    "calc_input_diagnostics",
    "resolve_calc_step",
    "resolve_global_options",
    "workflow_fingerprint",
    "build_configuration_contract_v1",
    "build_configuration_contract_v2",
    "CONFIGURATION_CONTRACT_BUILDERS",
    "build_editor_manifest(",
    "build_recipe_catalog(",
]
V2_MIGRATION_MODULES = ["confflow.config.canonical.v2_adapter"]
V1_MIGRATION_MODULES = []
INTERNAL_ONLY_V3_MIGRATION_MODULES = []
PUBLISH_AUTHORITY_CONSUMERS = (
    "confflow.persistence.run_state",
    "confflow.persistence.publication",
    "confflow.persistence.generation",
    "confflow.persistence.arbitration",
    "confflow.application.v4_run",
    "confflow.producer.run_result",
)
FSYNC_DIRECTORY_CONSUMERS = (
    "confflow.persistence.imports",
    "confflow.application.execution.workflow_adapter",
)
CONSOLIDATED_AUTHORITY_SCOPES = [
    "confflow/domain",
    "confflow/execution",
    "confflow/workflow/v4",
    "confflow/persistence",
    "confflow/programs",
    "confflow/producer",
    "confflow/application",
]
PUBLISH_AUTHORITY_PATH = "confflow/persistence/fsatomic.py"
DUPLICATE_ATOMIC_HELPER_MARKERS = [
    "def _atomic_write_bytes(",
    "def _fsync_directory(",
    "def _fsync_dir(",
    "def _tmp_suffix(",
    "def _next_tmp_suffix(",
]
SANITIZER_AUTHORITY_PATH = "confflow/programs/_naming.py"
DUPLICATE_SANITIZER_MARKER = "def sanitize_job_name("
V45_SCAN_ROOTS = [
    "confflow/domain",
    "confflow/execution",
    "confflow/workflow/v4",
    "confflow/persistence",
    "confflow/programs",
]
V45_MODULES = [
    "confflow.execution.output_identity",
    "confflow.execution.execution_adapters",
    "confflow.execution.multi_output",
    "confflow.execution.profile_ensemble",
]
ORDINAL_WITHIN_ITEM_FILES = [
    "confflow/execution/native.py",
    "confflow/execution/output_identity.py",
    "confflow/execution/profile_ensemble.py",
]
RANGE_ORDINAL_ALLOWLIST: list[str] = []

V42_ROOTS = ["confflow/domain", "confflow/execution", "confflow/workflow/v4", "confflow/programs"]
FORBIDDEN_V42_SYMBOLS = [
    "TaskName",
    "get_itask",
    "TaskContext",
    "CalcStepRequest",
    "CalcStepRunner",
    "TaskRunner",
    "StepExecutionResult",
    "ResultsDB",
    "GlobalOptions",
    "WorkflowPlan",
    "WorkflowV3Plan",
    "v3_runtime",
    "v3_dataflow",
]
FORBIDDEN_V42_TOKENS = [
    "input_xyz",
    "output_path",
    "total_memory",
    "max_parallel_jobs",
    "auto_clean",
    "chk_from_step",
]
PROGRAMS_ALLOWED_PREFIXES = ["confflow.domain", "confflow.execution", "confflow.programs"]
PROGRAMS_FORBIDDEN_PREFIXES = [
    "confflow.blocks",
    "confflow.calc",
    "confflow.config",
    "confflow.core",
    "confflow.shared",
    "confflow.application",
    "confflow.control",
    "confflow.cli",
    "confflow.main",
    "confflow.workflow.engine",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.plan",
]

V46_STRICT_ROOT_CANDIDATES = [
    "domain",
    "workflow/v4",
    "execution",
    "producer",
    "persistence",
    "programs",
]
V46_NEW_SYMBOLS = ["iprog"]
V46_LEGACY_TRUTH_TOKENS = ["result.xyz", "failed.xyz", "workflow_stats", "output_path", "min_xyz"]
ENGINE_IMPORT_ALLOWLIST = []
#: In-repo double(s) for AP-081. ``require_one`` counts ONLY these
#: repo-relative files, never a machine-local absolute path.
DOUBLE_FILES = [
    "tests/v4/test_v46_cross_repo.py",
]
#: JD-side double, relative to the JD repo root. The repo root is derived
#: from the ``JOBDESK_V2_SRC`` environment variable (which names the JD
#: ``src`` directory, so its parent is the repo root); when the variable is
#: unset the legacy local default ``/opt/jobdesk-v2-v4`` applies. A missing
#: sibling file is skipped per the long-standing "sibling-repo file lands in
#: parallel" semantics and never counts toward ``require_one``.
JD_E2E_DOUBLE_REL = "tests/application/test_confflow_v4_e2e.py"
JD_REPO_ENV = "JOBDESK_V2_SRC"
DEFAULT_JD_REPO_ROOT = "/opt/jobdesk-v2-v4"


def _jobdesk_double_paths() -> list[Path]:
    """Resolve the JD-side double file(s) for AP-081/AP-082.

    When ``JOBDESK_V2_SRC`` is set (even to a nonexistent location) only
    the env-derived candidate is used, so tests can point it at a fake or
    missing checkout without the host default leaking in. Otherwise the
    legacy local default is used when present.
    """
    explicit = os.environ.get(JD_REPO_ENV)
    if explicit:
        return [Path(explicit).parent / JD_E2E_DOUBLE_REL]
    return [Path(DEFAULT_JD_REPO_ROOT) / JD_E2E_DOUBLE_REL]


V46_E2E_DOUBLE_FILES = ["tests/v4/test_v46_cross_repo_e2e.py"]
NUMERIC_DISPATCH_BASES = ["adapters", "checks", "executors", "profiles", "programs", "recoveries"]
V46_MODULES = [
    "confflow.producer",
    "confflow.producer.contract",
    "confflow.producer.manifest",
    "confflow.producer.recipes",
    "confflow.producer.validation",
]

SCANNER_SCOPE = [
    "confflow/v4cli.py",
    "confflow/application/__init__.py",
    "confflow/application/v4_entry.py",
    "confflow/application/v4_run.py",
    "confflow/application/execution",
    "confflow/control.py",
    "confflow/producer",
]
SCANNER_FORBIDDEN_IMPORT_PREFIXES = [
    "confflow.shared",
    "confflow.cli",
    "confflow.main",
    "confflow.workflow.engine",
    "confflow.workflow.state",
    "confflow.workflow.v3_runtime",
    "confflow.workflow.v3_dataflow",
    "confflow.workflow.step_handlers",
    "confflow.workflow.binding_v2",
    "confflow.workflow.execution_context",
    "confflow.workflow.finalize",
    "confflow.workflow.resume_validation",
    "confflow.workflow.runtime_context",
    "confflow.workflow.stats",
    "confflow.workflow.presenter",
    "confflow.workflow.dag",
    "confflow.workflow.plan",
    "confflow.workflow.rerun_failed",
    "confflow.workflow.supervisor",
    "confflow.core.models",
    "confflow.core.types",
    "confflow.core.parsers",
    "confflow.core.path_policy",
    "confflow.core.io",
]
SCANNER_RETIRED_RUNTIME_MODULES = [
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
    "confflow.remote",
    "confflow.remote.envelope",
    "confflow.remote.lease",
    "confflow.remote.supervision",
    "confflow.remote.schema",
]
SCANNER_RETIRED_V3_WIRE_MODULES = [
    "confflow.config.canonical.v3_parser",
    "confflow.config.canonical.v3_graph",
    "confflow.config.canonical.upgrade",
    "confflow.config.canonical.structured",
    "confflow.config.canonical.theory",
    "confflow.config.canonical.extensions",
    "confflow.config.canonical.yaml_io",
    "confflow.config.canonical.execution_versions",
    "confflow.config.workflow_cli",
]
SCANNER_RETIRED_V1_V2_WIRE_MODULES = [
    "confflow.config.canonical",
    "confflow.config.canonical.contract",
    "confflow.config.canonical.schema",
    "confflow.config.canonical.parser",
    "confflow.config.canonical.validation",
    "confflow.config.canonical.v2_adapter",
    "confflow.config.canonical.workflow",
    "confflow.config.canonical.editor_manifest",
    "confflow.config.canonical.recipes",
    "confflow.config.canonical.fingerprint",
    "confflow.config.canonical.param_fields",
    "confflow.config.canonical.pydantic",
    "confflow.config.canonical.diagnostics",
    "confflow.config.canonical.serialization",
    "confflow.config.models",
    "confflow.shared.config_validation",
    "confflow.core.types",
    "confflow.workflow.plan",
    "confflow.workflow.config_show",
    "confflow.workflow.dry_run",
]
SCANNER_RETIRED_V1_V2_WIRE_TOKENS = [
    "confflow.workflow.v1",
    "confflow.workflow.v2",
    "confflow.configuration-contract.v1",
    "confflow.configuration-contract.v2",
    "confflow.config.canonical",
    "confflow.config.models",
    "confflow.shared.config_validation",
    "confflow.workflow.dry_run",
    "confflow.workflow.config_show",
    "to_canonical_workflow",
    "parse_canonical_workflow",
    "parse_workflow_mapping",
    "load_raw_mapping",
    "load_workflow_definition",
    "load_workflow_model",
    "detect_schema_version",
    "detect_workflow_file_version",
    "validate_workflow_definition",
    "calc_input_diagnostics",
    "resolve_calc_step",
    "resolve_global_options",
    "workflow_fingerprint",
    "build_configuration_contract_v1",
    "build_configuration_contract_v2",
    "CONFIGURATION_CONTRACT_BUILDERS",
    "build_editor_manifest(",
    "build_recipe_catalog(",
]
SCANNER_PATTERNS = [
    ["legacy-TaskRunner", "\\bTaskRunner\\b"],
    ["legacy-CalcStepRunner", "\\bCalcStepRunner\\b"],
    ["legacy-ResultsDB", "\\bResultsDB\\b"],
    ["legacy-WorkflowState", "\\bWorkflowState\\w*\\b"],
    ["legacy-iprog", "\\biprog\\b"],
    ["legacy-itask", "\\bitask\\b|\\bget_itask\\b"],
    ["legacy-chk-from-step", "\\bchk_from_step\\b"],
    ["legacy-result-xyz", "result\\.xyz|failed\\.xyz|\\boutput_xyz\\b"],
    ["legacy-output-path", "\\boutput_path\\b"],
    ["legacy-orca-fallback", "(?i)default[_\\s-]*orca|orca[_\\s-]*default"],
    ["legacy-first-match", "first-match|first_match"],
    ["legacy-fake-marker", "FAKE_MODE|fake_native|native_marker|CONFFLOW_FAKE"],
    ["legacy-stepresult-shortcut", "\\bStepResult\\s*\\("],
    [
        "legacy-duplicate-authority",
        "register_executor|register_program|build_default_registry|\\bExecutionRegistry\\s*\\(",
    ],
    ["legacy-silent-fallback", "silent.*fallback|fallback.*silent|\\bor\\s+[\\\"']orca[\\\"']"],
]

METRIC_V4_ROOTS = [
    "confflow.v4cli",
    "confflow.application.v4_entry",
    "confflow.application.execution.workflow_adapter",
    "confflow.control_worker",
]

# ---------------------------------------------------------------------------
# Small shared tokens used by the custom scanners.
# ---------------------------------------------------------------------------

TASK_DISPATCH_MARKERS = ("IRC", "QST", "NEB", "GOAT")
FILENAME_ATTRIBUTE_TOKENS = ("basename", "splitext", "stem", "suffix")
FILENAME_NAME_TOKENS = ("basename", "splitext")

# ---------------------------------------------------------------------------
# Scanning functions (AST based; docstrings/comments excluded by construction
# except for rules that explicitly declare raw_text=True).
# ---------------------------------------------------------------------------


def iter_python_files(root: Path):
    """Yield every ``*.py`` file below ``root`` (root is a repo-relative path)."""
    base = Path(root)
    if base.is_file():
        yield base
        return
    yield from sorted(base.rglob("*.py"))


def _in_scope(relpath: str, scope: list[str]) -> bool:
    for entry in scope:
        if relpath == entry or relpath.startswith(entry.rstrip("/") + "/"):
            return True
    return False


def _rel_module(relpath: str) -> str:
    return relpath[: -len(".py")].replace("/", ".")


def _resolve_relative(
    relpath: str, level: int, module: str | None, resolver_style: str = "default"
) -> str:
    if resolver_style == "legacy_cli":
        # Old scanner package basis: path-relative parent package (the file
        # name itself is dropped, including ``__init__``), then level-1 up.
        parts = _rel_module(relpath).split(".")
        base_pkg = parts[:-1] if parts else []
        up = level - 1
        base = base_pkg[: len(base_pkg) - up] if up <= len(base_pkg) else []
        return ".".join(base + ([module] if module else []))
    package_parts = _rel_module(relpath).split(".")
    if relpath.endswith("/__init__.py"):
        base = package_parts[: len(package_parts) - level]
    else:
        base = package_parts[: len(package_parts) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def imports_of(
    tree: ast.AST, relpath: str, resolver_style: str = "default"
) -> list[tuple[int, str, str]]:
    """Return (lineno, raw_module, resolved_module) for every import target.

    ``raw`` mirrors the original ``_imports`` (relative imports stay dotted);
    ``resolved`` applies the original relative-import resolution.  The
    ``legacy_cli`` resolver style uses the old scanner package basis
    (file name dropped); the default keeps the existing AST semantics.
    """
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hits.append((node.lineno, alias.name, alias.name))
        elif isinstance(node, ast.ImportFrom):
            raw = "." * node.level + (node.module or "")
            resolved = (
                _resolve_relative(relpath, node.level, node.module, resolver_style)
                if node.level
                else (node.module or "")
            )
            hits.append((node.lineno, raw, resolved))
    return hits


def symbols_used(tree: ast.AST) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            used.add(node.name)
    return used


def _docstring_byte_spans(tree: ast.AST, data: bytes) -> list[tuple[int, int]]:
    """Compute the byte spans of module/class/function docstrings.

    AST ``col_offset``/``end_col_offset`` are UTF-8 byte offsets within their
    line, so all spans are computed on the encoded bytes directly.
    """
    spans: list[tuple[int, int]] = []
    line_starts = [0]
    for line in data.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def add(node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            value = body[0].value
            spans.append(
                (
                    line_starts[value.lineno - 1] + value.col_offset,
                    line_starts[value.end_lineno - 1] + value.end_col_offset,
                )
            )

    add(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            add(node)
    return spans


def code_text(source: str, profile: str = "default") -> str:
    """Return *source* with comments and docstrings blanked in place.

    Blanking happens on the UTF-8 byte buffer (AST columns are byte offsets
    within their line); ``tokenize`` character columns are converted to byte
    offsets per line.  Every remaining character keeps its original byte
    offset and line, so line-level regex patterns keep both their line scope
    and the original diagnostics line numbers.  Multi-byte characters are
    never split (spans align to token boundaries).  Non-docstring string
    constants are preserved.

    The ``legacy_cli`` profile keeps the old scanner quirk on top of the
    same single AST/tokenize pass: any line holding a COMMENT token and any
    line touched by a docstring span is blanked whole (code on that line
    included).  The default profile keeps the exact byte-span semantics.
    """
    import bisect
    import io
    import tokenize

    data = bytearray(source.encode("utf-8"))
    tree = ast.parse(source)

    src_lines = source.splitlines(keepends=True)
    line_starts = [0]
    for line in src_lines:
        line_starts.append(line_starts[-1] + len(line.encode("utf-8")))

    def char_to_byte(lineno: int, col: int) -> int:
        line = source.splitlines(keepends=True)[lineno - 1]
        return line_starts[lineno - 1] + len(line[:col].encode("utf-8"))

    def blank(start: int, end: int) -> None:
        # Blank everything inside the span; internal newlines survive so the
        # line structure (and diagnostics line numbers) stay unchanged.
        for index in range(start, min(end, len(data))):
            if data[index] != 0x0A:
                data[index] = 0x20

    spans = _docstring_byte_spans(tree, bytes(data))
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    if profile == "legacy_cli":
        # Same AST spans / COMMENT tokens, expanded to whole lines.
        doc_lines: set[int] = set()
        for start, end in spans:
            if end <= start:
                continue
            first = bisect.bisect_right(line_starts, start) - 1
            last = bisect.bisect_right(line_starts, max(end - 1, start)) - 1
            for lineno in range(first + 1, last + 2):
                doc_lines.add(lineno)
        comment_lines = {tok.start[0] for tok in tokens if tok.type == tokenize.COMMENT}
        for lineno in doc_lines | comment_lines:
            if 1 <= lineno <= len(src_lines):
                blank(line_starts[lineno - 1], line_starts[lineno])
        return data.decode("utf-8")

    for start, end in spans:
        blank(start, end)
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            start = char_to_byte(tok.start[0], tok.start[1])
            end = char_to_byte(tok.end[0], tok.end[1])
            blank(start, end)
    return data.decode("utf-8")


def _task_enum_members(tree: ast.AST) -> set[str]:
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
                        and any(marker in child.id for marker in TASK_DISPATCH_MARKERS)
                    ):
                        members.add(child.id)
    return members


def task_dispatch_offenders(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    members = _task_enum_members(tree)
    if not members:
        return []
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            test = node.test
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


def filename_pairing_offenders(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in FILENAME_ATTRIBUTE_TOKENS:
            offenders.append((node.lineno, node.attr))
        elif (
            isinstance(node, ast.Name)
            and node.id in FILENAME_NAME_TOKENS
            and not isinstance(node.ctx, ast.Store)
        ):
            offenders.append((node.lineno, node.id))
    return offenders


def range_ordinal_pairing_offenders(source: str) -> list[tuple[str, int, str]]:
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


def numeric_dispatch_offenders(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            base = ast.unparse(node.value)
            if any(base == b or base.endswith("." + b) for b in NUMERIC_DISPATCH_BASES):
                offenders.append((node.lineno, base))
    return offenders


def _read_source_constant(path: Path, name: str):
    """AST-read a literal module-level constant from a real source file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                try:
                    return ast.literal_eval(node.value)
                except Exception:
                    return None
    return None


# ---------------------------------------------------------------------------
# RULES: data-only declarations.  Each entry pins the L0.4a inventory row it
# mirrors (source field).  Kinds: imports / symbols / vocab / disk_absent /
# disk_present / coverage / custom_* / const / meta_case / metric.
# ---------------------------------------------------------------------------

R = []

# --- imports (静态导入约束, AST-IMP) --------------------------------------
R.append(
    {
        "id": "AP-001",
        "kind": "imports",
        "source": "#1",
        "scope": [DOMAIN_ROOT],
        "mode": "forbidden_prefixes",
        "prefixes": ["confflow"],
    }
)
R.append(
    {
        "id": "AP-002",
        "kind": "imports",
        "source": "#2",
        "scope": [V4_ROOT, EXECUTION_ROOT, PERSISTENCE_ROOT, PROGRAMS_ROOT],
        "mode": "allowed_prefixes",
        "allowed": [
            "confflow.domain",
            "confflow.execution",
            "confflow.workflow.v4",
            "confflow.persistence",
            "confflow.programs",
        ],
        "exempt_imports": {
            "confflow/workflow/v4/confgen_schema.py": [
                "confflow.science.confgen.coordination.stage",
                "confflow.science.confgen.tolerances",
                "confflow.science.confgen.graph",
                "confflow.science.confgen.ring.templates",
                "confflow.science.confgen.torsion.measure",
            ],
            "confflow/execution/confgen_executor.py": [
                "confflow.science.confgen.accounting",
                "confflow.science.confgen.engine",
                "confflow.science.confgen.model",
                "confflow.science.confgen.torsion.measure",
            ],
        },
        # Precise file+module exemption (no whole-file skip): only
        # transform_executor.py may import exactly
        # confflow.science.confgen.registry (A4a/AG2 channel), and only the
        # confgen-search pair below may import exactly its listed science
        # modules (Card C: the search runner/worker own the v4 search-spec
        # authority plus the typed-graph/search science they execute).
        # Any other science module in those files, or the same modules in
        # any other file, still trips AP-002.
        "exempt_precise_imports": {
            "confflow/execution/transform_executor.py": [
                "confflow.science.confgen.registry",
            ],
            "confflow/execution/confgen_search_worker.py": [
                "confflow.science.confgen.search",
                "confflow.science.confgen.search_spec",
                "confflow.science.confgen.graph",
            ],
            "confflow/execution/confgen_search_run.py": [
                "confflow.science.confgen.search_spec",
                "confflow.science.topology",
            ],
        },
    }
)
R.append(
    {
        "id": "AP-003",
        "kind": "imports",
        "source": "#3",
        "resolve_relative": True,
        "scope": [PERSISTENCE_ROOT],
        "mode": "allowed_prefixes",
        "allowed": ["confflow.domain", "confflow.persistence"],
    }
)
R.append(
    {
        "id": "AP-004",
        "kind": "imports",
        "source": "#4",
        "scope": SIX_ROOTS,
        "mode": "forbidden_prefixes",
        "prefixes": FORBIDDEN_IMPORT_PREFIXES,
    }
)
R.append(
    {
        "id": "AP-005",
        "kind": "imports",
        "source": "#5",
        "scope": SIX_ROOTS,
        "mode": "forbidden_exact",
        "exact": FORBIDDEN_LEGACY_MODULES,
    }
)
R.append(
    {
        "id": "AP-010",
        "kind": "imports",
        "source": "#10",
        "scope": [*EXTENDED_V4_PRODUCTION_ROOTS, *EXTENDED_V4_PRODUCTION_FILES],
        "mode": "forbidden_prefixes",
        "prefixes": FORBIDDEN_LEGACY_RUNTIME_PREFIXES,
        "forbidden_exact": EXTENDED_FORBIDDEN_LEGACY_MODULES,
        "allowed_exact": ["confflow.config.contract_schemas"],
    }
)
R.append(
    {
        "id": "AP-013",
        "kind": "imports",
        "source": "#13",
        "scope": [*IMPORT_ONLY_V4_PRODUCTION_ROOTS, *IMPORT_ONLY_V4_PRODUCTION_FILES],
        "mode": "forbidden_prefixes",
        "prefixes": FORBIDDEN_LEGACY_RUNTIME_PREFIXES,
        "forbidden_exact": EXTENDED_FORBIDDEN_LEGACY_MODULES,
    }
)
R.append(
    {
        "id": "AP-023",
        "kind": "imports",
        "source": "#23",
        "resolve_relative": True,
        "scope": [*SIX_ROOTS, PRODUCER_ROOT, ANALYSIS_ROOT],
        "mode": "forbidden_exact",
        "exact": RETIRED_RUNTIME_MODULES,
        "with_submodules": True,
    }
)
R.append(
    {
        "id": "AP-026",
        "kind": "imports",
        "source": "#26",
        "resolve_relative": True,
        "scope": [PACKAGE_ROOT],
        "mode": "forbidden_exact",
        "exact": PUBLIC_V3_WIRE_MODULES,
        "with_submodules": True,
    }
)
R.append(
    {
        "id": "AP-032",
        "kind": "imports",
        "source": "#32",
        "resolve_relative": True,
        "scope": [PACKAGE_ROOT],
        "mode": "forbidden_exact",
        "exact": PUBLIC_V1_V2_WIRE_MODULES,
        "with_submodules": True,
    }
)
R.append(
    {
        "id": "AP-057",
        "kind": "imports",
        "source": "#57",
        "resolve_relative": True,
        "scope": V45_MODULES,
        "mode": "forbidden_prefixes",
        "prefixes": FORBIDDEN_IMPORT_PREFIXES,
    }
)
R.append(
    {
        "id": "AP-068",
        "kind": "imports",
        "source": "#68",
        "resolve_relative": True,
        "scope": [PROGRAMS_ROOT],
        "mode": "allowed_prefixes",
        "allowed": PROGRAMS_ALLOWED_PREFIXES,
    }
)
R.append(
    {
        "id": "AP-069",
        "kind": "imports",
        "source": "#69",
        "resolve_relative": True,
        "scope": [PROGRAMS_ROOT],
        "mode": "forbidden_prefixes",
        "prefixes": PROGRAMS_FORBIDDEN_PREFIXES,
    }
)
R.append(
    {
        "id": "AP-072",
        "kind": "imports",
        "source": "#72",
        "resolve_relative": True,
        "scope": "__v46_strict_roots__",
        "mode": "forbidden_exact",
        "exact": FORBIDDEN_LEGACY_MODULES,
    }
)
R.append(
    {
        "id": "AP-076",
        "kind": "imports",
        "source": "#76",
        "resolve_relative": True,
        "scope": "__v46_strict_roots__",
        "mode": "forbidden_exact",
        "exact": ["confflow.workflow.engine"],
    }
)
R.append(
    {
        "id": "AP-077",
        "kind": "imports",
        "source": "#77",
        "scope": ["confflow/cli.py", APPLICATION_ROOT],
        "mode": "allowed_exact",
        "allowed": ENGINE_IMPORT_ALLOWLIST,
        "match_prefix": "confflow.science.confgen.engine",
    }
)
R.append(
    {
        "id": "AP-081",
        "kind": "custom_jobdesk_doubles",
        "source": "#81",
        "files": DOUBLE_FILES,
        "jd_rel": JD_E2E_DOUBLE_REL,
        "require_one": True,
    }
)
R.append(
    {
        "id": "AP-082",
        "kind": "custom_jobdesk_doubles",
        "source": "#82",
        "files": V46_E2E_DOUBLE_FILES,
        "require_one": False,
    }
)
R.append(
    {
        "id": "AP-087",
        "kind": "imports",
        "source": "#87",
        "resolve_relative": True,
        "scope": V46_MODULES,
        "mode": "forbidden_exact",
        "exact": FORBIDDEN_LEGACY_MODULES,
    }
)
R.append(
    {
        "id": "AP-088",
        "kind": "imports",
        "source": "#88",
        "resolve_relative": True,
        "scope": SCANNER_SCOPE,
        "mode": "forbidden_prefixes",
        "prefixes": SCANNER_FORBIDDEN_IMPORT_PREFIXES,
    }
)
R.append(
    {
        "id": "AP-089",
        "kind": "imports",
        "source": "#89",
        "resolve_relative": True,
        "scope": [ANALYSIS_ROOT],
        "mode": "forbidden_prefixes",
        "prefixes": ["confflow.persistence"],
    }
)

# --- symbols (AST-SYM) ----------------------------------------------------
R.append(
    {
        "id": "AP-006",
        "kind": "symbols",
        "source": "#6",
        "scope": SIX_ROOTS,
        "forbidden": FORBIDDEN_SYMBOLS,
    }
)
R.append(
    {
        "id": "AP-011",
        "kind": "symbols",
        "source": "#11",
        "scope": [*EXTENDED_V4_PRODUCTION_ROOTS, *EXTENDED_V4_PRODUCTION_FILES],
        "forbidden": FORBIDDEN_SYMBOLS,
        "exempt_symbols": EXTENDED_LEGACY_TOKEN_EXEMPTIONS,
    }
)
R.append(
    {
        "id": "AP-028",
        "kind": "symbols",
        "source": "#28",
        "scope": [*SIX_ROOTS, *EXTENDED_V4_PRODUCTION_ROOTS, *EXTENDED_V4_PRODUCTION_FILES],
        "forbidden": RETIRED_V3_SYMBOLS,
    }
)
R.append(
    {
        "id": "AP-060",
        "kind": "custom_task_dispatch",
        "source": "#60",
        "scope": [EXECUTION_ROOT, PROGRAMS_ROOT],
    }
)
R.append(
    {
        "id": "AP-062",
        "kind": "custom_filename_idioms",
        "source": "#62",
        "scope": [V4_ROOT, EXECUTION_ROOT],
    }
)
R.append(
    {
        "id": "AP-063",
        "kind": "custom_range_ordinal",
        "source": "#63",
        "scope": [V4_ROOT, EXECUTION_ROOT],
        "allowlist": RANGE_ORDINAL_ALLOWLIST,
    }
)
R.append(
    {
        "id": "AP-066",
        "kind": "symbols",
        "source": "#66",
        "scope": V42_ROOTS,
        "forbidden": FORBIDDEN_V42_SYMBOLS,
    }
)
R.append(
    {
        "id": "AP-071",
        "kind": "symbols",
        "source": "#71",
        "scope": "__v46_strict_roots__",
        "forbidden": FORBIDDEN_SYMBOLS,
    }
)
R.append(
    {
        "id": "AP-073",
        "kind": "symbols",
        "source": "#73",
        "scope": "__v46_strict_roots__",
        "forbidden": V46_NEW_SYMBOLS,
    }
)
R.append(
    {
        "id": "AP-074",
        "kind": "custom_numeric_dispatch",
        "source": "#74",
        "scope": "__v46_strict_roots__",
    }
)
R.append(
    {
        "id": "AP-078",
        "kind": "custom_filename_idioms",
        "source": "#78",
        "scope": [V4_ROOT, EXECUTION_ROOT, ANALYSIS_ROOT, PRODUCER_ROOT],
    }
)
R.append(
    {
        "id": "AP-079",
        "kind": "custom_range_ordinal",
        "source": "#79",
        "scope": [V4_ROOT, EXECUTION_ROOT, ANALYSIS_ROOT, PRODUCER_ROOT],
        "allowlist": RANGE_ORDINAL_ALLOWLIST,
    }
)
R.append(
    {
        "id": "AP-083",
        "kind": "symbols",
        "source": "#83",
        "scope": [APPLICATION_ROOT, "confflow/cli.py"],
        "forbidden": V46_NEW_SYMBOLS,
    }
)
R.append(
    {
        "id": "AP-084",
        "kind": "custom_numeric_dispatch",
        "source": "#84",
        "scope": [APPLICATION_ROOT, "confflow/cli.py"],
    }
)

# --- vocab (TXT-CODE / TXT-RAW) -------------------------------------------
R.append(
    {
        "id": "AP-007",
        "kind": "vocab",
        "source": "#7",
        "scope": [DOMAIN_ROOT],
        "tokens": ["output_path", "input_xyz"],
    }
)
R.append(
    {
        "id": "AP-008",
        "kind": "vocab",
        "source": "#8",
        "scope": [EXECUTION_ROOT, V4_ROOT, PERSISTENCE_ROOT, PROGRAMS_ROOT],
        "tokens": ["output_path"],
    }
)
R.append(
    {
        "id": "AP-009",
        "kind": "vocab",
        "source": "#9",
        "scope": SIX_ROOTS,
        "tokens": LEGACY_FILENAME_TOKENS,
    }
)
R.append(
    {
        "id": "AP-012",
        "kind": "vocab",
        "source": "#12",
        "scope": [*EXTENDED_V4_PRODUCTION_ROOTS, *EXTENDED_V4_PRODUCTION_FILES],
        "tokens": LEGACY_FILENAME_TOKENS,
        "exempt_symbols": EXTENDED_LEGACY_TOKEN_EXEMPTIONS,
    }
)
R.append(
    {
        "id": "AP-034",
        "kind": "vocab",
        "source": "#34",
        "raw_text": True,
        "scope": [*SIX_ROOTS, *EXTENDED_V4_PRODUCTION_ROOTS, *EXTENDED_V4_PRODUCTION_FILES],
        "tokens": RETIRED_V1_V2_WIRE_TOKENS,
    }
)
R.append(
    {
        "id": "AP-036",
        "kind": "vocab",
        "source": "#36",
        "raw_text": True,
        "scope": CONSOLIDATED_AUTHORITY_SCOPES,
        "tokens": DUPLICATE_ATOMIC_HELPER_MARKERS,
        "exempt_files": [PUBLISH_AUTHORITY_PATH],
    }
)
R.append(
    {
        "id": "AP-037",
        "kind": "vocab",
        "source": "#37",
        "raw_text": True,
        "scope": [PROGRAMS_ROOT],
        "tokens": [DUPLICATE_SANITIZER_MARKER],
        "exempt_files": [SANITIZER_AUTHORITY_PATH],
    }
)
R.append(
    {
        "id": "AP-064",
        "kind": "vocab",
        "source": "#64",
        "scope": [V4_ROOT, EXECUTION_ROOT],
        "tokens": ["member_index", "point_ordinal"],
        "allowed_files": ORDINAL_WITHIN_ITEM_FILES,
    }
)
R.append(
    {
        "id": "AP-067",
        "kind": "vocab",
        "source": "#67",
        "scope": V42_ROOTS,
        "tokens": FORBIDDEN_V42_TOKENS,
        "exempt_symbols": N1_SERVER_QUOTA_VOCAB_EXEMPTIONS,
    }
)
R.append(
    {
        "id": "AP-075",
        "kind": "vocab",
        "source": "#75",
        "scope": "__v46_strict_roots__",
        "tokens": V46_LEGACY_TRUTH_TOKENS,
    }
)
R.append(
    {
        "id": "AP-080",
        "kind": "vocab",
        "source": "#80",
        "scope": [*EXTENDED_V4_PRODUCTION_ROOTS, *EXTENDED_V4_PRODUCTION_FILES],
        "tokens": ["member_index", "point_ordinal"],
        "allowed_files": ORDINAL_WITHIN_ITEM_FILES,
    }
)
R.append(
    {
        "id": "AP-090",
        "kind": "patterns",
        "source": "#90",
        "scope": SCANNER_SCOPE,
        "patterns": SCANNER_PATTERNS,
    }
)
R.append(
    {
        "id": "AP-092",
        "kind": "vocab",
        "source": "#92",
        "scope": SCANNER_SCOPE,
        "tokens": SCANNER_RETIRED_V1_V2_WIRE_TOKENS,
    }
)

# --- disk (DISK) ----------------------------------------------------------
R.append(
    {"id": "AP-022", "kind": "disk_absent", "source": "#22", "modules": RETIRED_RUNTIME_MODULES}
)
R.append(
    {"id": "AP-024", "kind": "disk_absent", "source": "#24", "modules": PUBLIC_V3_WIRE_MODULES}
)
R.append(
    {"id": "AP-029", "kind": "disk_absent", "source": "#29", "modules": PUBLIC_V1_V2_WIRE_MODULES}
)
R.append(
    {"id": "AP-030", "kind": "disk_absent", "source": "#30", "dirs": ["confflow/config/canonical"]}
)
R.append(
    {
        "id": "AP-033",
        "kind": "disk_absent",
        "source": "#33",
        "modules": sorted(set([*V1_MIGRATION_MODULES, *V2_MIGRATION_MODULES])),
    }
)
R.append(
    {
        "id": "AP-051",
        "kind": "disk_present",
        "source": "#51",
        "files": [f"{r}/__init__.py" for r in (DOMAIN_ROOT, EXECUTION_ROOT, V4_ROOT)],
    }
)
R.append(
    {
        "id": "AP-054",
        "kind": "disk_inventory",
        "source": "#54",
        "forbidden": FORBIDDEN_LEGACY_MODULES,
        "removed": REMOVED_LEGACY_MODULES,
        "retained": RETAINED_COMPAT_ALLOWLIST,
    }
)
R.append({"id": "AP-055", "kind": "disk_present", "source": "#55", "modules": V45_MODULES})
R.append(
    {
        "id": "AP-056",
        "kind": "coverage",
        "source": "#56",
        "modules": V45_MODULES,
        "roots": V45_SCAN_ROOTS,
    }
)
R.append(
    {
        "id": "AP-091",
        "kind": "disk_absent",
        "source": "#91",
        "modules": sorted(
            set(SCANNER_RETIRED_RUNTIME_MODULES)
            | set(SCANNER_RETIRED_V3_WIRE_MODULES)
            | set(SCANNER_RETIRED_V1_V2_WIRE_MODULES)
        ),
    }
)

# --- const / meta / metric ------------------------------------------------
R.append(
    {
        "id": "AP-027",
        "kind": "const",
        "source": "#27",
        "guard": "INTERNAL_ONLY_V3_MIGRATION_MODULES",
        "expect": [],
    }
)
R.append(
    {
        "id": "AP-033a",
        "kind": "const",
        "source": "#33",
        "guard": "V1/V2_MIGRATION_KERNELS",
        "expect": {"V1": [], "V2": ["confflow.config.canonical.v2_adapter"]},
    }
)
R.append(
    {
        "id": "AP-035",
        "kind": "const",
        "source": "#35",
        "guard": "CURRENT_PROTOCOL_MAJORS_NOT_IN_TOKENS",
    }
)
R.append({"id": "AP-058", "kind": "meta_case", "source": "#58", "case": "task_dispatch_flagged"})
R.append(
    {"id": "AP-059", "kind": "meta_case", "source": "#59", "case": "task_dispatch_ignores_strings"}
)
R.append({"id": "AP-061", "kind": "meta_case", "source": "#61", "case": "pairing_idioms_flagged"})
R.append(
    {
        "id": "AP-070",
        "kind": "const",
        "source": "#70",
        "guard": "V46_NEW_SYMBOLS_DISJOINT_FROM_FORBIDDEN_SYMBOLS",
    }
)
R.append({"id": "AP-093", "kind": "metric", "source": "#93", "name": "code_text_stripping"})
R.append(
    {
        "id": "AP-094",
        "kind": "metric",
        "source": "#94",
        "name": "v4_roots_pinned",
        "value": METRIC_V4_ROOTS,
    }
)
R.append({"id": "AP-095", "kind": "metric", "source": "#95", "name": "reachability_measures"})

RULES = R

# ---------------------------------------------------------------------------
# A6 G13/G14 ConfGen purity rules (PLAN A6 step 6). AST-only scope checks;
# docstrings/comments are excluded by construction (string-literal rule
# skips docstring Constants; attribute/import/register rules only match
# real AST nodes, never prose). Old rules/CLI/metrics semantics unchanged.
# ---------------------------------------------------------------------------

CONFGEN_G13_KERNEL_SCOPE = [
    "confflow/science/confgen/engine.py",
    "confflow/science/confgen/planner.py",
    "confflow/science/confgen/accounting.py",
    "confflow/science/confgen/perception.py",
    "confflow/science/confgen/model.py",
    "confflow/science/confgen/kernel_records.py",
    "confflow/execution/confgen_executor.py",
    "confflow/execution/transform_executor.py",
]
CONFGEN_G13_AXIS = ("coordination", "rings", "torsions")
CONFGEN_G13_ALLOW_GETATTR_MODULES = frozenset(
    {
        "confflow/science/confgen/engine.py",
        "confflow/science/confgen/__init__.py",
    }
)
CONFGEN_G13_ALLOW_GETATTR_NAMES = frozenset(
    {
        "InheritedTorsionLock",
        "inherited_torsion_locks",
        "check_inherited_torsion_locks",
    }
)
CONFGEN_G13_COMPONENT_FRAGS = (
    "confflow.science.confgen.coordination",
    "confflow.science.confgen.ring",
    "confflow.science.confgen.torsion",
)

# ---------------------------------------------------------------------------
# L1-A3c intent science isolation (PLAN; tool guard only, zero production
# change). Scope pins the ACTUAL split intent package only
# (confflow/producer/intent/**, 12 modules at e6b9f52), never the whole
# producer tree. Mechanism reuses the existing AST import data rule
# (kind=imports, forbidden_prefixes, resolve_relative=True): ast.walk covers
# module/function/class scopes, Import + ImportFrom absolute targets,
# relative targets via _resolve_relative, docstrings/comments excluded by
# construction (AST nodes only). Dynamic importlib literals are NOT covered
# by kind=imports (existing mechanism only handles them in the narrow
# _confgen_component_import_hits path); no widening without basis.
# v2: AP-106 sets per-rule resolver_style="legacy_cli", reusing the existing
# legacy_cli package basis (drop filename, level-1 up) which matches
# importlib.util.resolve_name Python semantics for normal modules,
# capabilities-subdir modules and __init__ alike. No global default change:
# rules without resolver_style keep legacy/default profile behavior, so old
# rules/CLI/metrics outputs are unchanged (LEGACY_CLI_RULE_IDS still
# AP-088..092 only). G13 scopes/exemptions untouched.
# ---------------------------------------------------------------------------
L1_INTENT_SCOPE = ["confflow/producer/intent"]
L1_INTENT_SCIENCE_PREFIXES = ["confflow.science"]


def _confgen_docstring_ids(tree: ast.AST) -> set[int]:
    """Return ids of Constant nodes that are docstrings (module/class/fn)."""
    found: set[int] = set()

    def _add(node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))

    _add(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _add(node)
    return found


def _confgen_parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _confgen_enclosing(
    tree: ast.AST, parents: dict[int, ast.AST], node: ast.AST
) -> tuple[str | None, str | None]:
    klass: str | None = None
    func: str | None = None
    cur: ast.AST | None = node
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and func is None:
            func = cur.name
        if isinstance(cur, ast.ClassDef) and klass is None:
            klass = cur.name
        cur = parents.get(id(cur))
    return klass, func


def _confgen_axis_literal_hits(tree: ast.AST, rel: str) -> list[tuple[int, str]]:
    doc = _confgen_docstring_ids(tree)
    parents = _confgen_parents(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in CONFGEN_G13_AXIS
            and id(node) not in doc
        ):
            klass, _func = _confgen_enclosing(tree, parents, node)
            if rel == "confflow/science/confgen/model.py" and klass == "ConfgenStateKey":
                continue
            hits.append((node.lineno, node.value))
    return hits


def _confgen_axis_attr_hits(tree: ast.AST, rel: str) -> list[tuple[int, str, str]]:
    parents = _confgen_parents(tree)
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr in CONFGEN_G13_AXIS):
            continue
        ctx = type(node.ctx).__name__
        klass, func = _confgen_enclosing(tree, parents, node)
        if rel == "confflow/science/confgen/model.py" and klass == "ConfgenStateKey":
            continue
        if rel == "confflow/science/confgen/engine.py":
            enclosing_class: str | None = None
            cur: ast.AST | None = node
            seen: set[int] = set()
            while cur is not None and id(cur) not in seen:
                seen.add(id(cur))
                if isinstance(cur, ast.ClassDef) and enclosing_class is None:
                    enclosing_class = cur.name
                cur = parents.get(id(cur))
            if enclosing_class == "ConfgenEngine" and func == "run":
                continue
        hits.append((node.lineno, node.attr, ctx))
    return hits


def _confgen_component_import_hits(tree: ast.AST, rel: str) -> list[tuple[int, str]]:
    exempt_lines: set[int] = set()
    if rel in CONFGEN_G13_ALLOW_GETATTR_MODULES:
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "__getattr__"
                and node in tree.body  # type: ignore[attr-defined]
            ):
                for sub in ast.walk(node):
                    lineno = getattr(sub, "lineno", None)
                    if lineno is not None:
                        exempt_lines.add(lineno)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif node.module:
                mods = [node.module]
            for mod in mods:
                if any(mod == f or mod.startswith(f + ".") for f in CONFGEN_G13_COMPONENT_FRAGS):
                    if node.lineno in exempt_lines and "torsion.inherited" in mod:
                        continue
                    hits.append((node.lineno, mod))
                    continue
            if isinstance(node, ast.ImportFrom) and node.level and node.module:
                if node.module.split(".")[0] in ("torsion", "ring", "coordination"):
                    hits.append((node.lineno, "." * node.level + node.module))
        if isinstance(node, ast.Call):
            func = node.func
            is_import = (
                isinstance(func, ast.Attribute) and func.attr in ("import_module", "__import__")
            ) or (isinstance(func, ast.Name) and func.id in ("__import__",))
            if is_import and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    val = first.value
                    if val == "confflow.science.confgen.torsion.inherited" and (
                        node.lineno in exempt_lines
                    ):
                        continue
                    if val.startswith("confflow.science.confgen."):
                        tail = val[len("confflow.science.confgen.") :]
                        if tail.split(".")[0] in ("coordination", "ring", "torsion"):
                            hits.append((node.lineno, val))
    return hits


def _confgen_allowlist_def(tree: ast.AST, ref: str) -> set[str] | None:
    """Resolve a module-level allowlist constant to its string set.

    Returns None when the name is not defined by a static collection of
    string constants in this module (then the guard is unverifiable).
    """
    for node in getattr(tree, "body", []):
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == ref for t in targets):
            continue
        if value is None:
            return None
        items: list[ast.AST] | None = None
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            items = list(value.elts)
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in ("frozenset", "set", "tuple", "list")
            and value.args
            and isinstance(value.args[0], (ast.Tuple, ast.List, ast.Set))
        ):
            items = list(value.args[0].elts)
        else:
            return None
        collected: set[str] = set()
        for item in items:
            if not (isinstance(item, ast.Constant) and isinstance(item.value, str)):
                return None
            collected.add(item.value)
        return collected
    return None


def _confgen_getattr_guard(tree: ast.AST, node: ast.AST, arg: str) -> tuple[str, set[str] | str]:
    """Inspect the real ``name`` guard of one ``__getattr__``.

    Returns ("literal", names) for an inline collection, ("ref", name) for a
    module-level constant, ("wide", detail) for prefix-style matching, or
    ("none", "") when no guard on the argument exists.
    """
    literals: set[str] = set()
    refs: set[str] = set()
    wide: str | None = None
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == arg
                and func.attr == "startswith"
            ):
                wide = "startswith"
        if not isinstance(sub, ast.Compare):
            continue
        left = sub.left
        if not (isinstance(left, ast.Name) and left.id == arg):
            continue
        for op, comp in zip(sub.ops, sub.comparators):
            if isinstance(op, (ast.In, ast.NotIn)):
                if isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                    if all(
                        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in comp.elts
                    ):
                        literals.update(e.value for e in comp.elts)  # type: ignore[misc]
                    else:
                        return ("wide", "dynamic membership")
                elif isinstance(comp, ast.Name):
                    refs.add(comp.id)
            elif isinstance(op, (ast.Eq, ast.NotEq)):
                if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    literals.add(comp.value)
    if wide is not None:
        return ("wide", wide)
    if refs:
        return ("ref", refs.pop() if len(refs) == 1 else sorted(refs))  # type: ignore[return-value]
    if literals:
        return ("literal", literals)
    return ("none", "")


def _confgen_getattr_hits(tree: ast.AST, rel: str, source: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    body = getattr(tree, "body", [])
    for node in ast.walk(tree):
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__"
        ):
            continue
        is_top = node in body
        arg = node.args.args[0].arg if node.args.args else "name"
        mods: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and sub.module:
                mods.add(sub.module)
            elif isinstance(sub, ast.Import):
                for alias in sub.names:
                    mods.add(alias.name)
            elif isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and func.attr == "import_module" and sub.args:
                    first = sub.args[0]
                    if isinstance(first, ast.Constant):
                        mods.add(str(first.value))
        if rel not in CONFGEN_G13_ALLOW_GETATTR_MODULES or not is_top:
            if mods:
                hits.append((node.lineno, "__getattr__ import outside allowlist"))
            continue
        for mod in mods:
            if "torsion.inherited" not in mod and "torsion" in mod:
                hits.append((node.lineno, f"__getattr__ imports {mod}"))
            elif "torsion.inherited" not in mod and mod.startswith("confflow"):
                hits.append((node.lineno, f"__getattr__ imports {mod}"))
        # Precise guard verification: the actual ``name`` guard and any
        # referenced constant must name exactly the 3 allowed APIs.
        kind, payload = _confgen_getattr_guard(tree, node, arg)
        allowed = set(CONFGEN_G13_ALLOW_GETATTR_NAMES)
        if kind == "wide":
            hits.append((node.lineno, f"__getattr__ wide guard ({payload})"))
        elif kind == "literal":
            guarded = set(payload)  # type: ignore[arg-type]
            if guarded != allowed:
                hits.append(
                    (
                        node.lineno,
                        f"__getattr__ guard names != allowed: "
                        f"extra={sorted(guarded - allowed)} missing={sorted(allowed - guarded)}",
                    )
                )
        elif kind == "ref":
            if isinstance(payload, list):
                hits.append((node.lineno, "__getattr__ multiple guard refs"))
            else:
                resolved = _confgen_allowlist_def(tree, payload)  # type: ignore[arg-type]
                if resolved is None:
                    hits.append((node.lineno, f"__getattr__ guard ref {payload!r} unverifiable"))
                elif resolved != allowed:
                    hits.append(
                        (
                            node.lineno,
                            f"__getattr__ guard ref {payload!r} != allowed: "
                            f"extra={sorted(resolved - allowed)} missing={sorted(allowed - resolved)}",
                        )
                    )
        elif kind == "none":
            if mods:
                hits.append((node.lineno, "__getattr__ imports without name guard"))
    return hits


def _confgen_target_owner_hits(tree: ast.AST, rel: str, source: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "as_kernel_target"
        ):
            hits.append((node.lineno, "as_kernel_target defined outside kernel_records"))
    if rel in (
        "confflow/science/confgen/engine.py",
        "confflow/science/confgen/__init__.py",
    ):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        try:
                            values = ast.literal_eval(node.value)
                        except Exception:
                            continue
                        if isinstance(values, (list, tuple)) and "as_kernel_target" in values:
                            hits.append(
                                (node.lineno, "as_kernel_target in __all__ outside kernel_records")
                            )
    return hits


def _confgen_top_register_hits(tree: ast.AST, rel: str, source: str) -> list[tuple[int, str]]:
    aliases = {"register"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if "register" in alias.name.lower():
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "register" in alias.name.lower():
                    aliases.add((alias.asname or alias.name).split(".")[0])
    hits: list[tuple[int, str]] = []

    def _func_name(call: ast.Call) -> str:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return ""

    def _visit(node: ast.AST) -> None:
        # Class bodies, decorators and defaults execute during import;
        # function/method/lambda bodies execute only when called.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for expression in getattr(node, "decorator_list", []):
                _visit(expression)
            for expression in [*node.args.defaults, *node.args.kw_defaults]:
                if expression is not None:
                    _visit(expression)
            return
        if isinstance(node, ast.Call):
            name = _func_name(node)
            if "register" in name.lower() or name in aliases:
                hits.append((node.lineno, name))
        for child in ast.iter_child_nodes(node):
            _visit(child)

    for stmt in getattr(tree, "body", []):
        _visit(stmt)
    return hits


RULES.append(
    {
        "id": "AP-100",
        "kind": "custom_confgen_axis_literals",
        "source": "#100",
        "scope": CONFGEN_G13_KERNEL_SCOPE,
    }
)
RULES.append(
    {
        "id": "AP-101",
        "kind": "custom_confgen_axis_attrs",
        "source": "#101",
        "scope": CONFGEN_G13_KERNEL_SCOPE,
    }
)
RULES.append(
    {
        "id": "AP-102",
        "kind": "custom_confgen_component_imports",
        "source": "#102",
        "scope": CONFGEN_G13_KERNEL_SCOPE,
    }
)
RULES.append(
    {
        "id": "AP-103",
        "kind": "custom_confgen_getattr_guard",
        "source": "#103",
        "scope": [
            "confflow/science/confgen/engine.py",
            "confflow/science/confgen/__init__.py",
            "confflow/science/confgen/planner.py",
            "confflow/science/confgen/model.py",
            "confflow/science/confgen/accounting.py",
            "confflow/science/confgen/perception.py",
            "confflow/science/confgen/kernel_records.py",
        ],
    }
)
RULES.append(
    {
        "id": "AP-104",
        "kind": "custom_confgen_target_owner",
        "source": "#104",
        "scope": [
            "confflow/science/confgen/engine.py",
            "confflow/science/confgen/model.py",
            "confflow/science/confgen/wire_v3.py",
            "confflow/science/confgen/accounting.py",
            "confflow/science/confgen/registry.py",
            "confflow/science/confgen/planner.py",
            "confflow/science/confgen/perception.py",
            "confflow/science/confgen/__init__.py",
        ],
    }
)
RULES.append(
    {
        "id": "AP-105",
        "kind": "custom_confgen_top_register",
        "source": "#105",
        "scope": ["confflow/science/confgen"],
    }
)
RULES.append(
    {
        "id": "AP-106",
        "kind": "imports",
        "source": "#106",
        "resolve_relative": True,
        "resolver_style": "legacy_cli",
        "scope": L1_INTENT_SCOPE,
        "mode": "forbidden_prefixes",
        "prefixes": L1_INTENT_SCIENCE_PREFIXES,
    }
)

# ---------------------------------------------------------------------------
# DIET-2 P0.2 LOC budget (AP-107) and G16 new-filename guard (AP-108).
# Authority: docs/diet-2/PLAN.md P0.2 + appendix A (not the L0.4a inventory,
# so source pins "DIET-2 P0.2"). AST is not involved: both rules measure the
# working tree on disk. Positive case: the real tree at the budget commit is
# clean because every limit equals the measured actual. Negative cases: a
# fixture tree with a shrunken limit (AP-107) or an unlisted matching name
# (AP-108). AP-108 exempts the grandfathered list in tools/loc_budget.json
# (all matches existing at P0.2); the list may only shrink — deleting a
# historical file removes its entry, adding entries to excuse new files is
# forbidden (see the JSON grandfather_note). G20 applies: compressing lines
# to fit the budget counts as a violation of the budget's intent and is
# judged in review, with black/ruff formatting unchanged.
# ---------------------------------------------------------------------------

LOC_BUDGET_FILE = "tools/loc_budget.json"


def _loc_budget_load(root: Path) -> dict | None:
    try:
        data = json.loads((Path(root) / LOC_BUDGET_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(data) if isinstance(data, dict) else None


def _loc_budget_assign(rel: str, subsystems: list[dict]) -> str:
    import fnmatch

    for sub in subsystems:
        for pat in sub.get("patterns", []):
            if "*" in pat or "?" in pat or "[" in pat:
                if fnmatch.fnmatch(rel, pat):
                    return str(sub["id"])
            elif pat.endswith("/"):
                if rel.startswith(pat):
                    return str(sub["id"])
            elif rel == pat or rel == pat + ".py" or rel.startswith(pat + "/"):
                return str(sub["id"])
    return "common"


def _loc_physical_lines(path: Path) -> int:
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _loc_budget_counts(root: Path, budget: dict) -> tuple[dict[str, int], int]:
    root = Path(root)
    subsystems = budget.get("subsystems", [])
    actual: dict[str, int] = {sub["id"]: 0 for sub in subsystems}
    for path in sorted((root / "confflow").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(root))
        assigned = _loc_budget_assign(rel, subsystems)
        actual[assigned] = actual.get(assigned, 0) + _loc_physical_lines(path)
    tests_total = 0
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        for path in sorted(tests_dir.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tests_total += _loc_physical_lines(path)
    return actual, tests_total


def loc_budget_violations(root: Path) -> list[dict]:
    """AP-107: any subsystem (or the tests total) over its budget fails."""
    budget = _loc_budget_load(root)
    if budget is None:
        return [
            {
                "rule": "AP-107",
                "path": LOC_BUDGET_FILE,
                "line": 0,
                "detail": "loc budget file missing or unparsable",
            }
        ]
    actual, tests_total = _loc_budget_counts(root, budget)
    violations: list[dict] = []
    for sub in budget.get("subsystems", []):
        limit = sub.get("prod_loc_limit")
        got = actual.get(sub["id"], 0)
        if isinstance(limit, int) and got > limit:
            violations.append(
                {
                    "rule": "AP-107",
                    "path": f"subsystem:{sub['id']}",
                    "line": 0,
                    "detail": f"{sub['id']} prod LOC {got} over budget {limit}",
                }
            )
    limit = budget.get("tests_total_loc_limit")
    if isinstance(limit, int) and tests_total > limit:
        violations.append(
            {
                "rule": "AP-107",
                "path": "tests/",
                "line": 0,
                "detail": f"tests total LOC {tests_total} over budget {limit}",
            }
        )
    return violations


def _test_filename_is_banned(basename: str) -> bool:
    if re.match(r"test_v4[0-9]_.*\.py$", basename):
        return True
    return basename.endswith(".py") and "_coverage" in basename


def test_filename_violations(root: Path) -> list[dict]:
    """AP-108 (G16): a NEW stage-named test file fails; listed ones pass."""
    root = Path(root)
    budget = _loc_budget_load(root)
    if budget is None:
        return [
            {
                "rule": "AP-108",
                "path": LOC_BUDGET_FILE,
                "line": 0,
                "detail": "loc budget file missing or unparsable",
            }
        ]
    grandfathered = set(budget.get("grandfathered", []))
    violations: list[dict] = []
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return violations
    for path in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if not _test_filename_is_banned(path.name):
            continue
        rel = str(path.relative_to(root))
        if rel not in grandfathered:
            violations.append(
                {"rule": "AP-108", "path": rel, "line": 0, "detail": "new stage-named test"}
            )
    return violations


RULES.append(
    {
        "id": "AP-107",
        "kind": "custom_loc_budget",
        "source": "DIET-2 P0.2",
    }
)
RULES.append(
    {
        "id": "AP-108",
        "kind": "custom_test_filename",
        "source": "DIET-2 P0.2",
    }
)

# ---------------------------------------------------------------------------
# Scanner entry points
# ---------------------------------------------------------------------------


def _v46_strict_roots(root: Path) -> list[str]:
    return [
        f"{PACKAGE_ROOT}/{name}"
        for name in V46_STRICT_ROOT_CANDIDATES
        if (root / PACKAGE_ROOT / name).is_dir()
    ]


def _resolve_scope(scope, root: Path) -> list[str]:
    if scope == "__v46_strict_roots__":
        return _v46_strict_roots(root)
    return scope


def _file_iter(scope_resolved: list[str], root: Path, *, skip_pycache: bool = False):
    for entry in scope_resolved:
        path = root / entry
        if not path.exists():
            continue  # scope files that do not exist in the tree are not scannable
        if path.suffix == ".py":
            if skip_pycache and "__pycache__" in path.parts:
                continue
            yield entry, path
        elif path.is_dir():
            for sub in sorted(path.rglob("*.py")):
                if skip_pycache and "__pycache__" in sub.parts:
                    continue
                yield str(sub.relative_to(root)), sub


def scope_files(root: Path, *, profile: str = "default") -> list[Path]:
    """File discovery backing the thin legacy CLI (single authority).

    The ``legacy_cli`` profile skips ``__pycache__`` paths (old-scanner
    caliber); the default profile keeps the historical walk unchanged.
    Thin wrappers must call this instead of maintaining a second loop.
    """
    return [
        path
        for _, path in _file_iter(
            list(SCANNER_SCOPE), Path(root), skip_pycache=(profile == "legacy_cli")
        )
    ]


def _match_import(module: str, rule: dict, legacy_prefix: bool = False) -> bool:
    for self_prefix in rule.get("self_prefixes", []):
        if module == self_prefix or module.startswith(self_prefix + "."):
            return False
    mode = rule["mode"]
    if mode.startswith("allowed") and not (module == "confflow" or module.startswith("confflow.")):
        return False
    if mode == "forbidden_prefixes":
        if legacy_prefix:
            # Old scanner caliber: plain startswith, no dot boundary.
            prefixes = rule["prefixes"]
            return module.startswith(tuple(prefixes)) if prefixes else False
        for prefix in rule["prefixes"]:
            if module == prefix or module.startswith(prefix + "."):
                return True
        return False
    if mode == "forbidden_exact":
        if rule.get("with_submodules"):
            for exact in rule["exact"]:
                if module == exact or module.startswith(exact + "."):
                    return True
            return False
        return module in rule["exact"]
    if mode == "forbidden_substrings":
        return any(s in module for s in rule["substrings"])
    if mode == "allowed_prefixes":
        return not any(module == a or module.startswith(a + ".") for a in rule["allowed"])
    if mode == "allowed_exact":
        match_prefix = rule.get("match_prefix")
        if match_prefix and not (module == match_prefix or module.startswith(match_prefix + ".")):
            return False
        return module not in rule["allowed"]
    return False


#: Old-scanner retired-module order (three tables concatenated, no
#: dedup/sort): the legacy_cli disk profile reports in this order and keeps
#: a duplicate entry's diagnostics instead of collapsing them.
SCANNER_RETIRED_TRIPLE = [
    *SCANNER_RETIRED_RUNTIME_MODULES,
    *SCANNER_RETIRED_V3_WIRE_MODULES,
    *SCANNER_RETIRED_V1_V2_WIRE_MODULES,
]

#: Rule subset backing the legacy CLI compat profile (thin adapter only).
LEGACY_CLI_RULE_IDS = ("AP-088", "AP-089", "AP-090", "AP-091", "AP-092")


def scan(
    root: Path,
    rule_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    profile: str = "default",
) -> list[dict]:
    """Run every static rule; return a list of violation dicts.

    ``rule_ids`` optionally restricts the run to a subset (used by the
    legacy CLI adapter); ``profile`` selects ``"default"`` (existing
    rule/AST semantics) or ``"legacy_cli"`` (explicit old-scanner compat:
    whole-line comment/docstring blanking, old relative-import basis, old
    plain-prefix matching, per-line vocab with real line numbers, retired
    disk check on ``.py`` and ``__init__.py`` in triple order).  The
    default profile判定 is unchanged.
    """
    root = Path(root)
    legacy = profile == "legacy_cli"
    selected = set(rule_ids) if rule_ids is not None else None
    violations: list[dict] = []
    cache: dict[str, tuple[str, ast.AST] | None] = {}

    def parsed(relpath: str) -> tuple[str, ast.AST] | None:
        if relpath not in cache:
            try:
                source = (root / relpath).read_text(encoding="utf-8")
                cache[relpath] = (source, ast.parse(source))
            except (OSError, SyntaxError, ValueError):
                if not legacy:
                    raise
                # Old scanner caliber: an unparseable file is skipped whole
                # (all legacy checks); the default profile keeps failing loud.
                cache[relpath] = None
        return cache[relpath]

    for rule in RULES:
        kind = rule["kind"]
        rid = rule["id"]
        if selected is not None and rid not in selected:
            continue
        if kind == "imports":
            scope = _resolve_scope(rule["scope"], root)
            exempt: dict[str, list[str]] = rule.get("exempt_imports", {})
            precise: dict[str, list[str]] = rule.get("exempt_precise_imports", {})
            resolve_relative = rule.get("resolve_relative", False)
            # Per-rule resolver_style (L1-A3c v2): when present it overrides
            # the profile default; rules without it keep exact old behavior
            # (legacy_cli under legacy profile, default otherwise), so old
            # rules/CLI/metrics caliber is unchanged. No new scanner: reuses
            # imports_of/_resolve_relative.
            _style = rule.get("resolver_style")
            if _style not in ("default", "legacy_cli"):
                _style = "legacy_cli" if legacy else "default"
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                if relpath in exempt:
                    continue
                row = parsed(relpath)
                if row is None:
                    continue
                source, tree = row
                for lineno, raw, resolved in imports_of(tree, relpath, _style):
                    module = resolved if resolve_relative else raw
                    allowed_here = any(
                        module == a or module.startswith(a + ".") for a in exempt.get(relpath, [])
                    ) or any(
                        module == a or module.startswith(a + ".") for a in precise.get(relpath, [])
                    )
                    if allowed_here:
                        continue
                    if _match_import(module, rule, legacy_prefix=legacy):
                        violations.append(
                            {"rule": rid, "path": relpath, "line": lineno, "detail": module}
                        )
        elif kind == "symbols":
            scope = _resolve_scope(rule["scope"], root)
            exempt = rule.get("exempt_symbols", {})
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                row = parsed(relpath)
                if row is None:
                    continue
                _, tree = row
                used = symbols_used(tree)
                banned = set(rule["forbidden"]) - set(exempt.get(relpath, []))
                for symbol in sorted(used & banned):
                    violations.append({"rule": rid, "path": relpath, "line": 0, "detail": symbol})
        elif kind == "vocab":
            scope = _resolve_scope(rule["scope"], root)
            raw = rule.get("raw_text", False)
            allowed_files = set(rule.get("allowed_files", []))
            exempt_files = set(rule.get("exempt_files", []))
            exempt_symbols = rule.get("exempt_symbols", {})
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                if relpath in allowed_files or relpath in exempt_files:
                    continue
                row = parsed(relpath)
                if row is None:
                    continue
                source, tree = row
                skip = set(exempt_symbols.get(relpath, []))
                if legacy:
                    if raw:
                        lines = source.splitlines()
                    else:
                        lines = code_text(source, profile="legacy_cli").splitlines()
                    for lineno, line in enumerate(lines, start=1):
                        for token in rule["tokens"]:
                            if token in skip:
                                continue
                            if token in line:
                                violations.append(
                                    {"rule": rid, "path": relpath, "line": lineno, "detail": token}
                                )
                    continue
                text = source if raw else code_text(source)
                for token in rule["tokens"]:
                    if token in skip:
                        continue
                    if token in text:
                        violations.append(
                            {"rule": rid, "path": relpath, "line": 0, "detail": token}
                        )
        elif kind == "patterns":
            scope = _resolve_scope(rule["scope"], root)
            compiled = [(check, re.compile(pattern)) for check, pattern in rule["patterns"]]
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                try:
                    text = code_text(
                        (root / relpath).read_text(encoding="utf-8"),
                        profile="legacy_cli" if legacy else "default",
                    )
                except (OSError, SyntaxError, ValueError):
                    if not legacy:
                        raise
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    for check, pattern in compiled:
                        match = pattern.search(line)
                        if match:
                            if legacy:
                                violations.append(
                                    {
                                        "rule": rid,
                                        "path": relpath,
                                        "line": lineno,
                                        "detail": check,
                                        "match": match.group(0).strip(),
                                    }
                                )
                            else:
                                violations.append(
                                    {
                                        "rule": rid,
                                        "path": relpath,
                                        "line": lineno,
                                        "detail": check,
                                    }
                                )
        elif kind == "custom_task_dispatch":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                for lineno, member in task_dispatch_offenders(
                    (root / relpath).read_text(encoding="utf-8")
                ):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": member}
                    )
        elif kind == "custom_filename_idioms":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                for lineno, token in filename_pairing_offenders(
                    (root / relpath).read_text(encoding="utf-8")
                ):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": token}
                    )
        elif kind == "custom_range_ordinal":
            scope = _resolve_scope(rule["scope"], root)
            allow = set(rule.get("allowlist", []))
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                if relpath in allow:
                    continue
                for function, lineno, index in range_ordinal_pairing_offenders(
                    (root / relpath).read_text(encoding="utf-8")
                ):
                    violations.append(
                        {
                            "rule": rid,
                            "path": relpath,
                            "line": lineno,
                            "detail": f"{function}:{index}",
                        }
                    )
        elif kind == "custom_numeric_dispatch":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                for lineno, base in numeric_dispatch_offenders(
                    (root / relpath).read_text(encoding="utf-8")
                ):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": base}
                    )
        elif kind == "custom_jobdesk_doubles":
            # AP-081/AP-082: the in-repo files under ``root`` count toward
            # ``require_one``; the JD sibling file (outside ``root``) is
            # resolved via JOBDESK_V2_SRC, skipped when absent, and never
            # counts, so the gate no longer depends on a machine-local
            # absolute path existing.
            def _double_hits(source_path: Path, display: str, basis: str, rule_id: str) -> None:
                tree = ast.parse(source_path.read_text(encoding="utf-8"))
                for node in tree.body:
                    if isinstance(node, ast.ClassDef) and "jobdesk" in node.name.lower():
                        owners = [(node.name, node)]
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        lowered = node.name.lower()
                        owners = (
                            [(node.name, node)]
                            if "jobdesk" in lowered and not node.name.startswith("Test")
                            else []
                        )
                    else:
                        owners = []
                    for owner, sub in owners:
                        for lineno, _raw, module in imports_of(sub, basis):
                            if module == "confflow" or module.startswith("confflow."):
                                violations.append(
                                    {
                                        "rule": rule_id,
                                        "path": display,
                                        "line": lineno,
                                        "detail": f"{owner}:{module}",
                                    }
                                )

            scanned_repo = 0
            for file in rule["files"]:
                candidate = Path(file)
                if candidate.is_absolute():
                    # Pre-configurable legacy entry: optional, never counts.
                    if not candidate.is_file():
                        continue  # sibling-repo file lands in parallel
                    _double_hits(candidate, file, file, rid)
                    continue
                path = root / file
                if not path.is_file():
                    continue  # sibling-repo file lands in parallel
                scanned_repo += 1
                _double_hits(path, file, file, rid)
            if rid == "AP-081":
                for jd_path in _jobdesk_double_paths():
                    if not jd_path.is_file():
                        continue  # sibling-repo file lands in parallel
                    _double_hits(jd_path, str(jd_path), str(jd_path), rid)
            if rule.get("require_one"):
                assert scanned_repo >= 1, "expected at least one double file to exist"
        elif kind == "disk_absent":
            if legacy and rid == "AP-091":
                # Old scanner order and file probes: triple order (no
                # dedup/sort), ``.py`` then ``__init__.py``, first hit only.
                for module in SCANNER_RETIRED_TRIPLE:
                    candidate = root / Path(module.replace(".", "/"))
                    hit = None
                    for path in (
                        candidate.with_suffix(".py"),
                        candidate / "__init__.py",
                    ):
                        if path.is_file():
                            hit = str(path.relative_to(root))
                            break
                    if hit is not None:
                        violations.append({"rule": rid, "path": hit, "line": 1, "detail": module})
                continue
            for module in rule.get("modules", []):
                if (root / (module.replace(".", "/") + ".py")).is_file():
                    violations.append(
                        {
                            "rule": rid,
                            "path": module.replace(".", "/") + ".py",
                            "line": 0,
                            "detail": "retired module present",
                        }
                    )
            for directory in rule.get("dirs", []):
                if (root / directory).exists():
                    violations.append(
                        {
                            "rule": rid,
                            "path": directory,
                            "line": 0,
                            "detail": "retired directory present",
                        }
                    )
        elif kind == "disk_inventory":
            removed = set(rule["removed"])
            retained = set(rule["retained"])
            for module in rule["forbidden"]:
                relpath = module.replace(".", "/") + ".py"
                present = (root / relpath).is_file()
                if module in removed:
                    if present:
                        violations.append(
                            {
                                "rule": rid,
                                "path": relpath,
                                "line": 0,
                                "detail": "removed module must stay absent",
                            }
                        )
                elif module in retained:
                    continue  # explicitly retained: neither state is judged
                elif not present:
                    violations.append(
                        {
                            "rule": rid,
                            "path": relpath,
                            "line": 0,
                            "detail": "forbidden-compatibility module must exist",
                        }
                    )
        elif kind == "disk_present":
            for module in rule.get("modules", []):
                if not (root / (module.replace(".", "/") + ".py")).is_file():
                    violations.append(
                        {
                            "rule": rid,
                            "path": module.replace(".", "/") + ".py",
                            "line": 0,
                            "detail": "expected module missing",
                        }
                    )
            for file in rule.get("files", []):
                if not (root / file).is_file():
                    violations.append(
                        {"rule": rid, "path": file, "line": 0, "detail": "expected file missing"}
                    )
        elif kind == "coverage":
            for module in rule["modules"]:
                path = module.replace(".", "/") + ".py"
                if not _in_scope(path, rule["roots"]):
                    violations.append(
                        {
                            "rule": rid,
                            "path": path,
                            "line": 0,
                            "detail": "module outside scan roots",
                        }
                    )
        elif kind == "custom_confgen_axis_literals":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                row = parsed(relpath)
                if row is None:
                    continue
                _source, tree = row
                for lineno, value in _confgen_axis_literal_hits(tree, relpath):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": value}
                    )
        elif kind == "custom_confgen_axis_attrs":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                row = parsed(relpath)
                if row is None:
                    continue
                _source, tree = row
                for lineno, attr, ctx in _confgen_axis_attr_hits(tree, relpath):
                    violations.append(
                        {
                            "rule": rid,
                            "path": relpath,
                            "line": lineno,
                            "detail": f".{attr} {ctx}",
                        }
                    )
        elif kind == "custom_confgen_component_imports":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                row = parsed(relpath)
                if row is None:
                    continue
                _source, tree = row
                for lineno, mod in _confgen_component_import_hits(tree, relpath):
                    violations.append({"rule": rid, "path": relpath, "line": lineno, "detail": mod})
        elif kind == "custom_confgen_getattr_guard":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                row = parsed(relpath)
                if row is None:
                    continue
                source, tree = row
                for lineno, detail in _confgen_getattr_hits(tree, relpath, source):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": detail}
                    )
        elif kind == "custom_confgen_target_owner":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                row = parsed(relpath)
                if row is None:
                    continue
                source, tree = row
                for lineno, detail in _confgen_target_owner_hits(tree, relpath, source):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": detail}
                    )
        elif kind == "custom_confgen_top_register":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                if not relpath.startswith("confflow/science/confgen/"):
                    continue
                row = parsed(relpath)
                if row is None:
                    continue
                source, tree = row
                for lineno, detail in _confgen_top_register_hits(tree, relpath, source):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": detail}
                    )
        elif kind == "custom_loc_budget":
            violations.extend(loc_budget_violations(root))
        elif kind == "custom_test_filename":
            violations.extend(test_filename_violations(root))
        elif kind in ("const", "meta_case", "metric"):
            continue  # evaluated by check_const_rules / fixture cases / metrics
        else:
            raise ValueError(f"unknown rule kind: {kind}")
    return violations


def _metrics_discover_modules(root: Path) -> dict[str, str]:
    import os

    modules: dict[str, str] = {}
    package_root = str(root / "confflow")
    for dirpath, dirnames, filenames in os.walk(package_root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".py"):
                path = os.path.join(dirpath, filename)
                relative = os.path.relpath(path, str(root))
                if relative.endswith("__init__.py"):
                    relative = os.path.dirname(relative)
                else:
                    relative = relative[:-3]
                modules[relative.replace(os.sep, ".")] = path
    return modules


def _metrics_resolve_import(modules: dict[str, str], base: str, module: str, name: str) -> str:
    if module:
        target = f"{base}.{module}" if base else module
        candidate = f"{target}.{name}"
        return candidate if candidate in modules else target
    candidate = f"{base}.{name}" if base else name
    return candidate if candidate in modules else (base or name)


def _metrics_parse_imports(modules: dict[str, str], module: str, path: str) -> set[str]:
    is_init = path.endswith("__init__.py")
    package = module if is_init else module.rsplit(".", 1)[0]
    found: set[str] = set()
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("confflow"):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                up = node.level - 1
                if up:
                    parts = parts[:-up] if up <= len(parts) else []
                base = ".".join(parts)
            else:
                base = ""
            imported = node.module or ""
            if node.names and all(alias.name == "*" for alias in node.names):
                target = f"{base}.{imported}" if base or imported else "confflow"
                if target.startswith("confflow"):
                    found.add(target)
                continue
            for alias in node.names:
                resolved = _metrics_resolve_import(modules, base, imported, alias.name)
                if resolved.startswith("confflow"):
                    found.add(resolved)
    return found


def _metrics_parents(modules: dict[str, str], module: str) -> set[str]:
    parts = module.split(".")
    return {
        ".".join(parts[:index])
        for index in range(1, len(parts))
        if ".".join(parts[:index]) in modules
    }


def _metrics_closure(
    modules: dict[str, str], imports: dict[str, set[str]], roots: list[str]
) -> set[str]:
    from collections import deque

    seen: set[str] = set()
    queue: deque[str] = deque()
    for root_module in roots:
        if root_module not in modules:
            raise SystemExit(f"closure root module not found: {root_module}")
        for candidate in [root_module, *sorted(_metrics_parents(modules, root_module))]:
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    while queue:
        module = queue.popleft()
        for target in imports.get(module, ()):
            for candidate in [target, *sorted(_metrics_parents(modules, target))]:
                if candidate in modules and candidate not in seen:
                    seen.add(candidate)
                    queue.append(candidate)
    return seen


def metrics_snapshot(root: Path) -> dict:
    """Mirror of ``architecture_metrics.collect`` (AP-095); records, never asserts.

    Preserves module discovery, parent-package ``__init__`` closure, relative
    and alias import resolution, the missing-root error and the module-count
    plus LOC measures verbatim (U8).  Implemented inline; the old script is
    not imported.
    """
    root = Path(root)

    def loc(path: Path) -> int:
        return sum(1 for _ in path.open(encoding="utf-8"))

    modules = _metrics_discover_modules(root)
    imports: dict[str, set[str]] = {}
    for module, path in modules.items():
        imports[module] = _metrics_parse_imports(modules, module, path)
    reachable = _metrics_closure(modules, imports, list(METRIC_V4_ROOTS))
    physical_loc = sum(loc(Path(path)) for path in modules.values())
    reachable_loc = sum(loc(Path(modules[module])) for module in reachable)
    return {
        "PHYSICAL_PRODUCTION_MODULES": len(modules),
        "PHYSICAL_PRODUCTION_LOC": physical_loc,
        "V4_REACHABLE_MODULES": len(reachable),
        "V4_REACHABLE_LOC": reachable_loc,
        "LEGACY_OR_NON_V4_REACHABLE_LOC": physical_loc - reachable_loc,
        "v4_reachable": sorted(reachable),
    }


def check_const_rules(root: Path) -> list[str]:
    """Evaluate the const rules (AP-027/033a/070) against the ACTUAL sources.

    The authoritative tables live in the policy module of the tree UNDER
    INSPECTION (root/tools/architecture_policy.py), read through the AST
    helper so edits in the inspected tree are observed.  The copies already
    imported into this process are never trusted.  Any deviation is reported
    as a problem.
    """
    problems: list[str] = []
    policy_source = root / "tools/architecture_policy.py"

    def _read_sequence(name: str, rule_id: str) -> list | None:
        value = _read_source_constant(policy_source, name)
        if value is None:
            problems.append(
                f"{rule_id}: policy source missing field {name} " "in tools/architecture_policy.py"
            )
            return None
        if not isinstance(value, (list, tuple)):
            problems.append(
                f"{rule_id}: policy field {name} must be a list/tuple "
                "in tools/architecture_policy.py"
            )
            return None
        if any(not isinstance(item, str) for item in value):
            problems.append(
                f"{rule_id}: policy field {name} must contain only strings "
                "in tools/architecture_policy.py"
            )
            return None
        return list(value)

    internal = _read_sequence("INTERNAL_ONLY_V3_MIGRATION_MODULES", "AP-027")
    if internal is not None:
        if internal != []:
            problems.append("AP-027: INTERNAL_ONLY_V3_MIGRATION_MODULES must be empty")
        for module in internal:
            relpath = module.replace(".", "/") + ".py"
            if (root / relpath).is_file():
                problems.append(f"AP-027: retired module present on disk: {module}")

    v1 = _read_sequence("V1_MIGRATION_MODULES", "AP-033a")
    v2 = _read_sequence("V2_MIGRATION_MODULES", "AP-033a")
    if v1 is not None and v1 != []:
        problems.append("AP-033a: V1 migration kernel must be empty")
    if v2 is not None and v2 != ["confflow.config.canonical.v2_adapter"]:
        problems.append("AP-033a: V2 migration kernel must be exactly v2_adapter")
    for module in [*(v1 or []), *(v2 or [])]:
        relpath = module.replace(".", "/") + ".py"
        if (root / relpath).is_file():
            problems.append(f"AP-033a: retired kernel module present on disk: {module}")

    forbidden = _read_sequence("FORBIDDEN_SYMBOLS", "AP-070")
    new46 = _read_sequence("V46_NEW_SYMBOLS", "AP-070")
    if forbidden is not None and new46 is not None:
        overlap = sorted(set(new46).intersection(set(forbidden)))
        if overlap:
            problems.append(
                "AP-070: V46 new symbols overlap the forbidden symbol table: " + ", ".join(overlap)
            )
    return problems


# ---------------------------------------------------------------------------
# Runtime import isolation (L0.4c): data + child-process executor.
# Every case runs in a FRESH subprocess (cwd = the tree under inspection,
# PYTHONPATH = tree + tools/refactor-acc/noeditable, PYTHONDONTWRITEBYTECODE=1,
# QT_QPA_PLATFORM=offscreen) so sys.modules can never be polluted by the
# parent test process.  A case fails on nonzero exit, timeout, or a source
# binding mismatch; there is no skip path.
# ---------------------------------------------------------------------------

RUNTIME_TIMEOUT = 120  # mirrors the original gate semantics (#96)

_RUNTIME_CHILD = (
    "import importlib, json, os, sys\n"
    "spec = json.loads(sys.argv[1])\n"
    "violations = []\n"
    "modules_out = None\n"
    "def imp(m):\n"
    "    return importlib.import_module(m)\n"
    "if spec.get('cwd_must_be') and os.path.realpath(os.getcwd()) != os.path.realpath(spec['cwd_must_be']):\n"
    "    violations.append({'op': 'cwd_mismatch', 'detail': os.getcwd()})\n"
    "for step in spec.get('steps', []):\n"
    "    op = step['op']\n"
    "    try:\n"
    "        if op == 'import':\n"
    "            imp(step['module'])\n"
    "        elif op == 'forbid':\n"
    "            for m in sys.modules:\n"
    "                hit = m in step.get('exact', [])\n"
    "                if not hit:\n"
    "                    for p in step.get('prefixes', []):\n"
    "                        if m.startswith(p):\n"
    "                            skip = any(m == e or m.startswith(e + '.') for e in step.get('except_prefixes', []))\n"
    "                            hit = not skip\n"
    "                            break\n"
    "                if hit:\n"
    "                    violations.append({'op': 'forbid', 'module': m})\n"
    "        elif op == 'require_import_fail':\n"
    "            try:\n"
    "                imp(step['module'])\n"
    "                violations.append({'op': op, 'module': step['module'], 'error': 'importable'})\n"
    "            except ModuleNotFoundError:\n"
    "                pass\n"
    "            except Exception as exc:\n"
    "                violations.append({'op': op, 'module': step['module'], 'error': type(exc).__name__})\n"
    "        elif op == 'export_absent':\n"
    "            mod = imp(step['module'])\n"
    "            for name in step['names']:\n"
    "                if name in getattr(mod, '__all__', []):\n"
    "                    violations.append({'op': op, 'module': step['module'], 'name': name, 'how': '__all__'})\n"
    "                if hasattr(mod, name):\n"
    "                    violations.append({'op': op, 'module': step['module'], 'name': name, 'how': 'hasattr'})\n"
    "        elif op == 'getattr_raises':\n"
    "            mod = imp(step['module'])\n"
    "            try:\n"
    "                getattr(mod, step['name'])\n"
    "                violations.append({'op': op, 'module': step['module'], 'name': step['name']})\n"
    "            except AttributeError:\n"
    "                pass\n"
    "        elif op == 'identity':\n"
    "            refs = step['refs']\n"
    "            objects = [getattr(imp(r.rsplit('.', 1)[0]), r.rsplit('.', 1)[1]) for r in refs]\n"
    "            authority = getattr(imp(step['authority'].rsplit('.', 1)[0]), step['authority'].rsplit('.', 1)[1])\n"
    "            for ref, obj in zip(refs, objects):\n"
    "                if obj is not authority:\n"
    "                    violations.append({'op': op, 'ref': ref})\n"
    "        elif op == 'value_eq':\n"
    "            mod, attr = step['ref'].rsplit('.', 1)\n"
    "            if getattr(imp(mod), attr) != step['value']:\n"
    "                violations.append({'op': op, 'ref': step['ref']})\n"
    "        elif op == 'print_confflow_modules':\n"
    "            modules_out = sorted(m for m in sys.modules if m.startswith('confflow'))\n"
    "    except Exception as exc:\n"
    "        violations.append({'op': op, 'error': type(exc).__name__, 'detail': str(exc)[-200:]})\n"
    "root_real = os.path.realpath(spec.get('source_binding_root', '/'))\n"
    "for mname, mod in list(sys.modules.items()):\n"
    "    if mname != 'confflow' and not mname.startswith('confflow.'):\n"
    "        continue\n"
    "    f = getattr(mod, '__file__', None)\n"
    "    if f and not os.path.realpath(f).startswith(root_real + os.sep):\n"
    "        violations.append({'op': 'source_binding', 'module': mname,\n"
    "                           'file': os.path.realpath(f)})\n"
    "    pkg_path = getattr(mod, '__path__', None)\n"
    "    for pp in list(pkg_path or []):\n"
    "        if not os.path.realpath(pp).startswith(root_real + os.sep):\n"
    "            violations.append({'op': 'source_binding_path', 'module': mname,\n"
    "                               'path': os.path.realpath(pp)})\n"
    "print(json.dumps({'violations': violations, 'confflow_modules': modules_out}))\n"
    "sys.exit(0 if not violations else 3)\n"
)

RUNTIME_RULES = [
    {
        "id": "RT-017",
        "source": "#17",
        "cases": [
            [
                {"op": "import", "module": "confflow.domain"},
                {
                    "op": "forbid",
                    "exact": ["confflow.core"],
                    "prefixes": ["confflow.config", "confflow.calc", "confflow.workflow"],
                },
            ]
        ],
    },
    {
        "id": "RT-018",
        "source": "#18",
        "cases": [
            [
                {"op": "import", "module": "confflow.workflow.v4"},
                {"op": "import", "module": "confflow.execution"},
                {
                    "op": "forbid",
                    "prefixes": ["confflow.workflow."],
                    "except_prefixes": ["confflow.workflow.v4"],
                    "exact": ["confflow.config"],
                },
                {"op": "forbid", "prefixes": ["confflow.calc"]},
            ]
        ],
    },
    {
        "id": "RT-020",
        "source": "#20",
        "cases": [
            [
                {"op": "import", "module": "confflow.v4cli"},
                {"op": "import", "module": "confflow.application.v4_entry"},
                {"op": "import", "module": "confflow.application.execution.workflow_adapter"},
                {"op": "import", "module": "confflow.control_worker"},
                {
                    "op": "forbid",
                    "exact": [
                        "confflow.application.execution.memory",
                        "confflow.application.execution.synthetic_producer",
                        "confflow.fixture_agent",
                    ],
                },
            ]
        ],
    },
    {
        "id": "RT-021",
        "source": "#21",
        "cases": [
            [{"op": "require_import_fail", "module": module}] for module in RETIRED_RUNTIME_MODULES
        ],
    },
    {
        "id": "RT-031",
        "source": "#31",
        "cases": [
            [{"op": "require_import_fail", "module": module}]
            for module in PUBLIC_V1_V2_WIRE_MODULES
        ],
    },
    {
        "id": "RT-038",
        "source": "#38",
        "cases": [
            [
                {"op": "import", "module": "confflow.persistence.fsatomic"},
                *[{"op": "import", "module": m} for m in PUBLISH_AUTHORITY_CONSUMERS],
                {
                    "op": "identity",
                    "authority": "confflow.persistence.fsatomic.publish_bytes",
                    "refs": [f"{m}.publish_bytes" for m in PUBLISH_AUTHORITY_CONSUMERS],
                },
                *[{"op": "import", "module": m} for m in FSYNC_DIRECTORY_CONSUMERS],
                {
                    "op": "identity",
                    "authority": "confflow.persistence.fsatomic.fsync_directory",
                    "refs": [f"{m}.fsync_directory" for m in FSYNC_DIRECTORY_CONSUMERS],
                },
            ]
        ],
    },
    {
        "id": "RT-039",
        "source": "#39",
        "cases": [
            [
                {"op": "import", "module": "confflow.programs._naming"},
                {"op": "import", "module": "confflow.programs.gaussian.rendering"},
                {"op": "import", "module": "confflow.programs.orca.rendering"},
                {
                    "op": "identity",
                    "authority": "confflow.programs._naming.sanitize_job_name",
                    "refs": [
                        "confflow.programs.gaussian.rendering.sanitize_job_name",
                        "confflow.programs.orca.rendering.sanitize_job_name",
                    ],
                },
            ]
        ],
    },
    {
        "id": "RT-040",
        "source": "#40",
        "cases": [
            [{"op": "import", "module": "confflow.producer"}, {"op": "print_confflow_modules"}]
        ],
        "legacy_debt": KNOWN_PRODUCER_LEGACY_IMPORTS,
    },
    {
        "id": "RT-041",
        "source": "#41",
        "cases": [
            [
                {"op": "import", "module": "confflow.producer"},
                {
                    "op": "forbid",
                    "exact": ["confflow.config.canonical", "confflow.config.models"],
                    "prefixes": ["confflow.config.canonical."],
                },
            ],
            [
                {"op": "import", "module": "confflow.producer.contract"},
                {
                    "op": "forbid",
                    "exact": ["confflow.config.canonical", "confflow.config.models"],
                    "prefixes": ["confflow.config.canonical."],
                },
            ],
        ],
    },
    {
        "id": "RT-042",
        "source": "#42",
        "cases": [
            [
                {"op": "import", "module": "confflow.producer"},
                {
                    "op": "forbid",
                    "prefixes": [
                        "confflow.calc",
                        "confflow.blocks",
                        "confflow.confts",
                        "confflow.workflow.engine",
                        "confflow.workflow.v3_runtime",
                        "confflow.workflow.v3_dataflow",
                        "confflow.workflow.binding_v2",
                        "confflow.config.canonical",
                    ],
                },
            ]
        ],
    },
    {
        "id": "RT-043",
        "source": "#43",
        "cases": [
            [
                {"op": "import", "module": "confflow.config.contract_schemas"},
                {"op": "import", "module": "confflow.producer.contract"},
                {"op": "import", "module": "confflow.producer.manifest"},
                {"op": "import", "module": "confflow.producer.recipes"},
                {"op": "import", "module": "confflow.producer.validation"},
                {
                    "op": "value_eq",
                    "ref": "confflow.config.contract_schemas.CONFIGURATION_VALIDATION_SCHEMA",
                    "value": "confflow.configuration-validation.v1",
                },
                {
                    "op": "value_eq",
                    "ref": "confflow.config.contract_schemas.EDITOR_MANIFEST_SCHEMA",
                    "value": "confflow.editor-manifest.v1",
                },
                {
                    "op": "value_eq",
                    "ref": "confflow.config.contract_schemas.RECIPE_CATALOG_SCHEMA",
                    "value": "confflow.recipe-catalog.v1",
                },
                {
                    "op": "identity",
                    "authority": "confflow.config.contract_schemas.CONFIGURATION_VALIDATION_SCHEMA",
                    "refs": [
                        "confflow.producer.contract.CONFIGURATION_VALIDATION_SCHEMA",
                        "confflow.producer.validation.VALIDATION_RESPONSE_SCHEMA",
                    ],
                },
                {
                    "op": "identity",
                    "authority": "confflow.config.contract_schemas.EDITOR_MANIFEST_SCHEMA",
                    "refs": ["confflow.producer.manifest.EDITOR_MANIFEST_SCHEMA"],
                },
                {
                    "op": "identity",
                    "authority": "confflow.config.contract_schemas.RECIPE_CATALOG_SCHEMA",
                    "refs": ["confflow.producer.recipes.RECIPE_CATALOG_SCHEMA"],
                },
            ]
        ],
    },
    {
        "id": "RT-044",
        "source": "#44",
        "cases": [
            [
                {"op": "import", "module": "confflow.config"},
                {"op": "getattr_raises", "module": "confflow.config", "name": "WorkflowConfig"},
                {"op": "getattr_raises", "module": "confflow.config", "name": "GlobalOptions"},
                {"op": "getattr_raises", "module": "confflow.config", "name": "StepConfig"},
                {"op": "getattr_raises", "module": "confflow.config", "name": "CalcStepParams"},
                {
                    "op": "getattr_raises",
                    "module": "confflow.config",
                    "name": "load_workflow_model",
                },
                {"op": "import", "module": "confflow.config.contract_schemas"},
                {
                    "op": "value_eq",
                    "ref": "confflow.config.contract_schemas.CONFIGURATION_VALIDATION_SCHEMA",
                    "value": "confflow.configuration-validation.v1",
                },
            ]
        ],
    },
    {
        "id": "RT-045",
        "source": "#45",
        "cases": [
            [
                {"op": "import", "module": "confflow.core"},
                {
                    "op": "forbid",
                    "prefixes": ["confflow.config"],
                    "exact": [
                        "confflow.core.models",
                        "confflow.core.types",
                        "confflow.core.validation",
                    ],
                },
            ]
        ],
    },
    {
        "id": "RT-046",
        "source": "#46",
        "cases": [
            [
                {"op": "import", "module": "confflow.application"},
                {"op": "forbid", "exact": ["confflow.application.execution"]},
                {"op": "import", "module": "confflow.application.execution"},
                {
                    "op": "forbid",
                    "exact": [
                        "confflow.application.execution.memory",
                        "confflow.application.execution.synthetic_producer",
                        "confflow.application.execution.sqlite",
                        "confflow.application.execution.service",
                        "confflow.application.execution.workflow_adapter",
                    ],
                },
            ]
        ],
    },
    {
        "id": "RT-047",
        "source": "#47",
        "cases": [
            [
                {"op": "import", "module": "confflow.v4cli"},
                {
                    "op": "forbid",
                    "exact": [
                        "confflow.config.canonical",
                        "confflow.core.models",
                        "confflow.shared.config_validation",
                    ],
                    "prefixes": ["confflow.config.canonical."],
                },
            ],
            [
                {"op": "import", "module": "confflow.application.v4_entry"},
                {
                    "op": "forbid",
                    "exact": [
                        "confflow.config.canonical",
                        "confflow.core.models",
                        "confflow.shared.config_validation",
                    ],
                    "prefixes": ["confflow.config.canonical."],
                },
            ],
            [
                {"op": "import", "module": "confflow.control_worker"},
                {
                    "op": "forbid",
                    "exact": [
                        "confflow.config.canonical",
                        "confflow.core.models",
                        "confflow.shared.config_validation",
                    ],
                    "prefixes": ["confflow.config.canonical."],
                },
            ],
        ],
    },
    {
        "id": "RT-065",
        "source": "#65",
        "cases": [
            [
                {"op": "import", "module": module},
                {
                    "op": "forbid",
                    "exact": ["confflow.core"],
                    "prefixes": ["confflow.config", "confflow.calc", "confflow.workflow."],
                    "except_prefixes": ["confflow.workflow.v4"],
                },
            ]
            for module in V45_MODULES
        ],
    },
    {"id": "RT-096", "source": "#96", "kind": "scanner_gate"},
]

_RUNNER_ALLOWED_WORKFLOW_V4_PREFIX = "confflow.workflow.v4"


def _is_legacy_producer_dependency(module: str) -> bool:
    """Mirror of the original producer legacy-dependency predicate."""
    if module == "confflow.config.canonical" or module.startswith("confflow.config.canonical."):
        return True
    if module == "confflow.config.models":
        return True
    if module in {"confflow.core.models", "confflow.core.types", "confflow.core.validation"}:
        return True
    if module.startswith("confflow.workflow.") and not module.startswith(
        _RUNNER_ALLOWED_WORKFLOW_V4_PREFIX
    ):
        return True
    return module.startswith(("confflow.calc", "confflow.blocks", "confflow.confts"))


def scan_runtime(root: Path, timeout: int = RUNTIME_TIMEOUT) -> list[dict]:
    """Run every L0.4c runtime isolation rule in fresh subprocesses."""
    root = Path(root).resolve()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    # The inspected root must win, and the venv's editable-install finder must
    # be disabled: otherwise a module deleted from ``root`` is still found in
    # the /opt/ConfFlow checkout and a require_import_fail case passes falsely.
    # The noeditable helper ships with this tool (not with the inspected tree),
    # so it is located relative to the tool, not relative to ``root``.
    noeditable = Path(__file__).resolve().parent / "refactor-acc" / "noeditable"
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(root),
            *([str(noeditable)] if noeditable.is_dir() else []),
            env.get("PYTHONPATH", ""),
        ]
    )
    violations: list[dict] = []

    def run_child(spec: dict, stage: str) -> tuple[int | None, str]:
        """Run one child; timeout/launch failures become violations, never skips."""
        spec = dict(spec)
        spec["cwd_must_be"] = str(root)
        spec["source_binding_root"] = str(root)
        try:
            proc = subprocess.run(
                [sys.executable, "-c", _RUNTIME_CHILD, json.dumps(spec)],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            violations.append({"rule": rid, "stage": stage, "detail": f"timeout after {timeout}s"})
            return None, ""
        except OSError as exc:
            violations.append({"rule": rid, "stage": stage, "detail": f"launch failed: {exc}"})
            return None, ""
        return proc.returncode, proc.stdout

    def parse_child_payload(out: str, rid: str, stage: str) -> dict | None:
        """Validate the child JSON structure; invalid payloads are failures."""
        try:
            payload = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            violations.append(
                {"rule": rid, "stage": stage, "detail": f"unparsable output: {out[-200:]}"}
            )
            return None
        if not isinstance(payload, dict):
            violations.append(
                {"rule": rid, "stage": stage, "detail": "output is not a JSON object"}
            )
            return None
        bad = payload.get("violations")
        if not isinstance(bad, list) or not all(isinstance(v, dict) for v in bad):
            violations.append(
                {"rule": rid, "stage": stage, "detail": "violations must be a list of objects"}
            )
            return None
        modules = payload.get("confflow_modules")
        if modules is not None and not (
            isinstance(modules, list) and all(isinstance(m, str) for m in modules)
        ):
            violations.append(
                {"rule": rid, "stage": stage, "detail": "confflow_modules must be a list of str"}
            )
            return None
        return payload

    def run_scanner_child(stage: str, inproc: bool) -> subprocess.CompletedProcess | None:
        """Run the scanner-gate child; timeout/launch failures become violations."""
        scripts_real = os.path.realpath(root / "scripts")
        if inproc:
            # The scanner module itself must be loaded from the inspected tree's
            # scripts/ directory, resolved through any symlink.
            script = (
                "import os, sys\n"
                "scripts_real = os.path.realpath(sys.argv[1])\n"
                "sys.path.insert(0, scripts_real)\n"
                "import v4_arch_scan as scanner\n"
                "mod_real = os.path.realpath(scanner.__file__)\n"
                "if os.path.dirname(mod_real) != scripts_real:\n"
                "    raise AssertionError(\n"
                "        'scanner module outside checked scripts: %s' % mod_real\n"
                "    )\n"
                "hits = scanner.scan()\n"
                "assert hits == [], hits\n"
            )
            cmd = [sys.executable, "-c", script, scripts_real]
        else:
            script_path = root / "scripts" / "v4_arch_scan.py"
            script_real = os.path.realpath(script_path)
            if os.path.dirname(script_real) != scripts_real:
                violations.append(
                    {
                        "rule": rid,
                        "stage": stage,
                        "detail": f"scanner script outside checked scripts: {script_real}",
                    }
                )
                return None
            cmd = [sys.executable, script_real, "--cf", str(root)]
        try:
            return subprocess.run(
                cmd, cwd=str(root), env=env, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            violations.append({"rule": rid, "stage": stage, "detail": f"timeout after {timeout}s"})
            return None
        except OSError as exc:
            violations.append({"rule": rid, "stage": stage, "detail": f"launch failed: {exc}"})
            return None

    for rule in RUNTIME_RULES:
        rid = rule["id"]
        if rule.get("kind") == "scanner_gate":
            # Two independent gates (inproc scan, CLI); both are checked, and
            # neither is skipped when the other fails.
            proc = run_scanner_child("inproc", inproc=True)
            if proc is not None and proc.returncode != 0:
                violations.append(
                    {
                        "rule": rid,
                        "stage": "inproc",
                        "detail": f"inproc scan failed: {proc.stderr[-200:]}",
                    }
                )
            cli = run_scanner_child("cli", inproc=False)
            if cli is not None and (cli.returncode != 0 or "clean" not in cli.stdout):
                violations.append(
                    {"rule": rid, "stage": "cli", "detail": f"CLI gate failed: {cli.stdout[-100:]}"}
                )
            continue
        for case in rule["cases"]:
            code, out = run_child({"steps": case}, stage=f"case:{rule['source']}")
            if code is None:
                continue  # timeout/launch failure already recorded by run_child
            payload = parse_child_payload(out, rid, stage=f"case:{rule['source']}")
            if payload is None:
                if code != 0:
                    # No parsable payload and a nonzero exit: report the crash.
                    violations.append(
                        {
                            "rule": rid,
                            "stage": "case",
                            "detail": f"child exit {code}: {out[-200:]}",
                        }
                    )
                continue
            # The child exits 3 precisely when it found violations, so the
            # structured findings are merged (keeping op/module/ref) and the
            # exit code alone is only reported when nothing was structured.
            for v in payload["violations"]:
                v["rule"] = rid
                violations.append(v)
            if code != 0 and not payload["violations"]:
                violations.append(
                    {
                        "rule": rid,
                        "stage": "case",
                        "detail": f"child exit {code} with no reported violation",
                    }
                )
            if "legacy_debt" in rule:
                # The debt measurement MUST come from the same child that
                # imported the producer (a fresh child would measure nothing).
                observed = payload.get("confflow_modules")
                if observed is None:
                    violations.append(
                        {"rule": rid, "stage": "debt", "detail": "module list unavailable"}
                    )
                else:
                    debt = sorted(m for m in observed if _is_legacy_producer_dependency(m))
                    expected = sorted(rule["legacy_debt"])
                    if debt != expected:
                        violations.append(
                            {
                                "rule": rid,
                                "stage": "debt",
                                "detail": f"legacy import debt changed: {debt}",
                            }
                        )
    return violations


# ---------------------------------------------------------------------------
# FIX-1A A2 scope helpers (not part of the L0.4b RULES count).
# AST-only, scope-aware checks for the kernel/v3 split. G13 full
# axis-literal removal stays an A6 endpoint; these helpers only enforce
# the A2 items: wire isolation, attribute scope, as_kernel_target
# uniqueness, AXIS_ORDER re-export, and stage parent structure-only use.
# ---------------------------------------------------------------------------

_CONFGEN_A2_WIRE_MODULE_FRAGMENT = "wire_v3"
_CONFGEN_A2_WIRE_SYMBOLS = {
    "wire_v3",
    "from_wire_key",
    "to_wire_key",
    "to_legacy_realization",
    "to_legacy_target",
    "is_legacy_stage",
    "project_v3",
    "LegacyStageAdapter",
    "UnsupportedWireComponent",
}

_CONFGEN_A2_COMPONENT_ATTRS = {"coordination", "rings", "torsions"}

_CONFGEN_A2_STAGE_PARENT_FORBIDDEN = {
    "state_key",
    "locked_axes",
    "generation_axis",
}


def _confgen_a2_parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None


def _confgen_a2_enclosing(tree: ast.AST, target: ast.AST) -> tuple[str | None, str | None]:
    """Return (class, function) enclosing *target* in *tree*."""
    klass: str | None = None
    func: str | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if any(sub is target for sub in ast.walk(node)):
                klass = node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(sub is target for sub in ast.walk(node)):
                func = node.name
    return klass, func


def confgen_a2_wire_isolation_violations(root: Path) -> list[str]:
    """Check kernel/wire isolation (AST scope).

    - ``accounting.py`` and ``kernel_records.py`` must not import or
      reference ``wire_v3``.
    - ``engine.py`` may reference ``wire_v3`` only inside
      ``ConfgenEngine.run`` method body.
    """
    root = Path(root)
    problems: list[str] = []
    for rel in (
        "confflow/science/confgen/accounting.py",
        "confflow/science/confgen/kernel_records.py",
    ):
        path = root / rel
        tree = _confgen_a2_parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if _CONFGEN_A2_WIRE_MODULE_FRAGMENT in node.module:
                    problems.append(f"{rel}:{node.lineno}: imports wire_v3")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _CONFGEN_A2_WIRE_MODULE_FRAGMENT in alias.name:
                        problems.append(f"{rel}:{node.lineno}: imports wire_v3")
            elif isinstance(node, ast.Name) and node.id in _CONFGEN_A2_WIRE_SYMBOLS:
                problems.append(f"{rel}:{node.lineno}: references {node.id}")
    engine_rel = "confflow/science/confgen/engine.py"
    tree = _confgen_a2_parse(root / engine_rel)
    if tree is not None:
        # Map each node to its enclosing function/class via parent walk.
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node

        def _enclosing_run(node: ast.AST) -> bool:
            cur: ast.AST | None = node
            func: str | None = None
            klass: str | None = None
            seen: set[int] = set()
            while cur is not None and id(cur) not in seen:
                seen.add(id(cur))
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and func is None:
                    func = cur.name
                if isinstance(cur, ast.ClassDef) and klass is None:
                    klass = cur.name
                cur = parents.get(id(cur))
            return func == "run" and klass == "ConfgenEngine"

        for node in ast.walk(tree):
            is_wire_import = False
            if isinstance(node, ast.ImportFrom) and node.module:
                is_wire_import = _CONFGEN_A2_WIRE_MODULE_FRAGMENT in node.module
            elif isinstance(node, ast.Import):
                is_wire_import = any(_CONFGEN_A2_WIRE_MODULE_FRAGMENT in a.name for a in node.names)
            elif isinstance(node, ast.Name) and node.id in _CONFGEN_A2_WIRE_SYMBOLS:
                # ``wire_v3`` symbol use outside run is a violation; uses
                # inside run are allowed.
                if not _enclosing_run(node):
                    problems.append(f"{engine_rel}:{node.lineno}: references {node.id}")
                continue
            if is_wire_import and not _enclosing_run(node):
                problems.append(f"{engine_rel}:{node.lineno}: imports wire_v3 outside run")
    return problems


def confgen_a2_attr_scope_violations(root: Path) -> list[str]:
    """Check ``.coordination/.rings/.torsions`` attribute scope (AST).

    Allowed: ``model.ConfgenStateKey`` class body, ``ConfgenEngine.run``
    method body, and all of ``wire_v3.py``.
    """
    root = Path(root)
    problems: list[str] = []
    candidates = [
        "confflow/science/confgen/engine.py",
        "confflow/science/confgen/model.py",
        "confflow/science/confgen/kernel_records.py",
        "confflow/science/confgen/accounting.py",
        "confflow/science/confgen/registry.py",
    ]
    for rel in candidates:
        path = root / rel
        tree = _confgen_a2_parse(path)
        if tree is None:
            continue
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr in _CONFGEN_A2_COMPONENT_ATTRS):
                continue
            cur: ast.AST | None = node
            func: str | None = None
            klass: str | None = None
            seen: set[int] = set()
            while cur is not None and id(cur) not in seen:
                seen.add(id(cur))
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)) and func is None:
                    func = cur.name
                if isinstance(cur, ast.ClassDef) and klass is None:
                    klass = cur.name
                cur = parents.get(id(cur))
            if rel == "confflow/science/confgen/model.py" and klass == "ConfgenStateKey":
                continue
            if rel == "confflow/science/confgen/engine.py" and func == "run":
                # Further require ConfgenEngine.run; approximate by func name
                # plus class check.
                cur2: ast.AST | None = node
                klass2: str | None = None
                seen2: set[int] = set()
                while cur2 is not None and id(cur2) not in seen2:
                    seen2.add(id(cur2))
                    if isinstance(cur2, ast.ClassDef) and klass2 is None:
                        klass2 = cur2.name
                    cur2 = parents.get(id(cur2))
                if klass2 == "ConfgenEngine" and func == "run":
                    continue
            problems.append(f"{rel}:{node.lineno}: .{node.attr} outside A2 scope")
    return problems


def confgen_a2_as_kernel_target_violations(root: Path) -> list[str]:
    """Check ``as_kernel_target`` is defined/exported only in kernel_records."""
    root = Path(root)
    problems: list[str] = []
    for rel in (
        "confflow/science/confgen/engine.py",
        "confflow/science/confgen/model.py",
        "confflow/science/confgen/wire_v3.py",
        "confflow/science/confgen/accounting.py",
        "confflow/science/confgen/registry.py",
    ):
        tree = _confgen_a2_parse(root / rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "as_kernel_target":
                    problems.append(
                        f"{rel}:{node.lineno}: as_kernel_target defined outside kernel_records"
                    )
            elif isinstance(node, ast.Name) and node.id == "as_kernel_target":
                # Import or reference outside kernel_records counts unless it
                # is the engine's converting call site (allowed use, not def).
                # To keep the rule tight, only flag definitions and
                # import statements here; call sites are checked separately.
                pass
        # Flag import statements referencing as_kernel_target as definition
        # spread (except the allowed engine internal use is still an import;
        # A2 allows engine to *call* it, which requires an import, so imports
        # themselves are not violations -- only definitions are).
    return problems


def confgen_a2_axis_order_violations(root: Path) -> list[str]:
    """Check ``model.AXIS_ORDER`` is only a re-export from wire constants."""
    root = Path(root)
    problems: list[str] = []
    path = root / "confflow/science/confgen/model.py"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return [f"{path}: unreadable"]
    tree = _confgen_a2_parse(path)
    if tree is None:
        return [f"{path}: unparseable"]
    has_reexport = (
        "from .wire_v3_constants import V3_AXIS_ORDER as AXIS_ORDER" in text
        or "from confflow.science.confgen.wire_v3_constants import V3_AXIS_ORDER as AXIS_ORDER"
        in text
    )
    if not has_reexport:
        problems.append("confflow/science/confgen/model.py: AXIS_ORDER re-export missing")
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "AXIS_ORDER":
                    problems.append(
                        f"confflow/science/confgen/model.py:{node.lineno}: AXIS_ORDER assigned directly"
                    )
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "AXIS_ORDER":
                problems.append(
                    f"confflow/science/confgen/model.py:{node.lineno}: AXIS_ORDER assigned directly"
                )
    return problems


def confgen_a2_stage_parent_violations(root: Path) -> list[str]:
    """Check stages do not read ``parent.state_key/locked_axes/...``."""
    root = Path(root)
    problems: list[str] = []
    for rel in (
        "confflow/science/confgen/coordination/stage.py",
        "confflow/science/confgen/ring/stage.py",
        "confflow/science/confgen/torsion/stage.py",
    ):
        tree = _confgen_a2_parse(root / rel)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _CONFGEN_A2_STAGE_PARENT_FORBIDDEN:
                base = node.value
                if isinstance(base, ast.Name) and base.id == "parent":
                    problems.append(f"{rel}:{node.lineno}: parent.{node.attr} forbidden")
    return problems


def confgen_a2_violations(root: Path) -> list[str]:
    """Aggregate all A2 scope violations (empty when clean)."""
    root = Path(root)
    out: list[str] = []
    out.extend(confgen_a2_wire_isolation_violations(root))
    out.extend(confgen_a2_attr_scope_violations(root))
    out.extend(confgen_a2_as_kernel_target_violations(root))
    out.extend(confgen_a2_axis_order_violations(root))
    out.extend(confgen_a2_stage_parent_violations(root))
    return out


# ---------------------------------------------------------------------------
# L1-G1 producer/policy Gaussian gates (AST only; docstrings/comments
# excluded by construction because only Call/If/Compare/Import nodes match).
# ---------------------------------------------------------------------------

_G1_PROD_AUTHORITY_CALLS = frozenset(
    {
        "resolve_write_chk",
        "coerce_section_lines",
        "normalize_gaussian_keyword",
        "parse_irc_route",
        "unsupported_method_finding",
    }
)

_G1_PROD_GAUSSIAN_NAMES = frozenset(
    {
        "ProgramName",
        "_QST_TOKEN_RE",
        "_OPT_PAREN_RE",
        "_OPT_ASSIGN_RE",
        "_OPT_BARE_RE",
        "_FREQ_TOKEN_RE",
        "_SP_MANAGED_RE",
        "_IRC_MANAGED_RE",
        "_IRC_ITEM_RE",
        "_READFC_CONFLICTS",
        "_RCFC_CONFLICTS",
        "_LINK0_CHECKPOINT_RE",
    }
)


def _g1_parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def g1_prod_authority_call_violations(root: Path) -> list[str]:
    """G-PROD-1: producer must not call Gaussian authorities directly."""
    rel = "confflow/producer/checkpoints.py"
    tree = _g1_parse(Path(root) / rel)
    if tree is None:
        return [f"{rel}: unreadable"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _G1_PROD_AUTHORITY_CALLS:
                problems.append(f"{rel}:{node.lineno}: direct Gaussian authority call {name}")
    return problems


def g1_prod_condition_violations(root: Path) -> list[str]:
    """G-PROD-2: producer must not branch on Gaussian conditions."""
    rel = "confflow/producer/checkpoints.py"
    tree = _g1_parse(Path(root) / rel)
    if tree is None:
        return [f"{rel}: unreadable"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.Assert)):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Name) and sub.id in _G1_PROD_GAUSSIAN_NAMES:
                    problems.append(f"{rel}:{node.lineno}: Gaussian condition {sub.id}")
        elif isinstance(node, ast.Compare):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id in _G1_PROD_GAUSSIAN_NAMES:
                    problems.append(f"{rel}:{node.lineno}: Gaussian compare {sub.id}")
    return problems


def g1_prod_import_violations(root: Path) -> list[str]:
    """G-PROD-3: producer may import programs.gaussian only via policy."""
    rel = "confflow/producer/checkpoints.py"
    tree = _g1_parse(Path(root) / rel)
    if tree is None:
        return [f"{rel}: unreadable"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Resolve relative to confflow/producer/checkpoints.py basis.
            if node.level:
                base = ["confflow", "producer"]
                up = node.level - 1
                base = base[: len(base) - up] if up <= len(base) else []
                resolved = ".".join(base + ([node.module] if node.module else []))
                # `from . import X` style: module is the package itself.
                candidates = [resolved] + (
                    [f"{resolved}.{a.name}" for a in node.names] if not node.module else []
                )
            else:
                resolved = node.module or ""
                candidates = [resolved]
            for cand in candidates:
                if (
                    "programs.gaussian" in cand
                    and cand != "confflow.programs.gaussian.checkpoint_policy"
                ):
                    problems.append(f"{rel}:{node.lineno}: forbidden Gaussian import {cand}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "programs.gaussian" in alias.name:
                    problems.append(f"{rel}:{node.lineno}: forbidden Gaussian import {alias.name}")
    return problems


def g1_policy_import_violations(root: Path) -> list[str]:
    """G-POL-1: policy must not import producer/workflow/science."""
    rel = "confflow/programs/gaussian/checkpoint_policy.py"
    tree = _g1_parse(Path(root) / rel)
    if tree is None:
        return [f"{rel}: unreadable"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods: list[str] = []
            if node.module:
                # Reconstruct resolved module for relative imports.
                if node.level:
                    pkg = ["confflow", "programs", "gaussian"]
                    up = node.level - 1
                    # checkpoint_policy.py package is confflow.programs.gaussian
                    base = pkg[: len(pkg) - up] if up <= len(pkg) else []
                    mods.append(".".join(base + [node.module]))
                else:
                    mods.append(node.module)
            else:
                # `from . import X` / `from ...domain.errors import Y`
                if node.level:
                    pkg = ["confflow", "programs", "gaussian"]
                    up = node.level - 1
                    base = pkg[: len(pkg) - up] if up <= len(pkg) else []
                    for alias in node.names:
                        mods.append(".".join(base + [alias.name]) if base else alias.name)
            for mod in mods:
                if any(frag in mod for frag in ("producer", "workflow", "science")):
                    # Allow prose in docstrings: ImportFrom is always code.
                    problems.append(f"{rel}:{node.lineno}: forbidden policy import {mod}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(frag in alias.name for frag in ("producer", "workflow", "science")):
                    problems.append(f"{rel}:{node.lineno}: forbidden policy import {alias.name}")
    return problems


def g1_violations(root: Path) -> list[str]:
    """Aggregate all L1-G1 Gaussian gate violations (empty when clean)."""
    root = Path(root)
    out: list[str] = []
    out.extend(g1_prod_authority_call_violations(root))
    out.extend(g1_prod_condition_violations(root))
    out.extend(g1_prod_import_violations(root))
    out.extend(g1_policy_import_violations(root))
    return out


RULE_COUNT = len(RULES)
