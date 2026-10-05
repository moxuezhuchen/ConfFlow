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
    "confflow/remote",
]
V4_ROOT = "confflow/workflow/v4"
DOMAIN_ROOT = "confflow/domain"
EXECUTION_ROOT = "confflow/execution"
PERSISTENCE_ROOT = "confflow/persistence"
PROGRAMS_ROOT = "confflow/programs"
REMOTE_ROOT = "confflow/remote"
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
    "confflow/analysis",
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
REMOTE_LEGACY_REMOTE_TOKENS = [
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
]
V45_SCAN_ROOTS = [
    "confflow/domain",
    "confflow/execution",
    "confflow/workflow/v4",
    "confflow/persistence",
    "confflow/programs",
    "confflow/remote",
]
V45_MODULES = [
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
]
ORDINAL_WITHIN_ITEM_FILES = [
    "confflow/execution/native.py",
    "confflow/execution/output_identity.py",
    "confflow/execution/profile_ensemble.py",
    "confflow/execution/profile_path_endpoints.py",
]
RANGE_ORDINAL_ALLOWLIST = ["confflow/execution/atom_mapping.py"]

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
    "analysis",
    "producer",
    "persistence",
    "programs",
    "remote",
]
V46_NEW_SYMBOLS = ["iprog"]
V46_LEGACY_TRUTH_TOKENS = ["result.xyz", "failed.xyz", "workflow_stats", "output_path", "min_xyz"]
ENGINE_IMPORT_ALLOWLIST = []
DOUBLE_FILES = [
    "tests/v4/test_v46_cross_repo.py",
    "/opt/jobdesk-v2-v4/tests/application/test_confflow_v4_e2e.py",
]
V46_E2E_DOUBLE_FILES = ["tests/v4/test_v46_cross_repo_e2e.py"]
NUMERIC_DISPATCH_BASES = ["adapters", "checks", "executors", "profiles", "programs", "recoveries"]
V46_MODULES = [
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
]

SCANNER_SCOPE = [
    "confflow/v4cli.py",
    "confflow/application/__init__.py",
    "confflow/application/v4_entry.py",
    "confflow/application/v4_run.py",
    "confflow/application/execution",
    "confflow/control.py",
    "confflow/producer",
    "confflow/remote",
    "confflow/analysis",
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
        "scope": [V4_ROOT, EXECUTION_ROOT, PERSISTENCE_ROOT, PROGRAMS_ROOT, REMOTE_ROOT],
        "mode": "allowed_prefixes",
        "allowed": [
            "confflow.domain",
            "confflow.execution",
            "confflow.workflow.v4",
            "confflow.persistence",
            "confflow.programs",
            "confflow.remote",
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
        "id": "AP-015",
        "kind": "imports",
        "source": "#15",
        "resolve_relative": True,
        "scope": [REMOTE_ROOT],
        "mode": "forbidden_substrings",
        "substrings": ["compiler", "yaml"],
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
        "scope": [EXECUTION_ROOT, V4_ROOT, PERSISTENCE_ROOT, PROGRAMS_ROOT, REMOTE_ROOT],
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
        "id": "AP-014",
        "kind": "vocab",
        "source": "#14",
        "scope": [REMOTE_ROOT],
        "tokens": REMOTE_LEGACY_REMOTE_TOKENS,
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
        "files": [f"{r}/__init__.py" for r in (DOMAIN_ROOT, EXECUTION_ROOT, V4_ROOT, REMOTE_ROOT)],
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
            exempt = rule.get("exempt_imports", {})
            resolve_relative = rule.get("resolve_relative", False)
            for relpath, _path in _file_iter(scope, root, skip_pycache=legacy):
                if relpath in exempt:
                    continue
                row = parsed(relpath)
                if row is None:
                    continue
                source, tree = row
                for lineno, raw, resolved in imports_of(
                    tree, relpath, "legacy_cli" if legacy else "default"
                ):
                    module = resolved if resolve_relative else raw
                    allowed_here = any(
                        module == a or module.startswith(a + ".") for a in exempt.get(relpath, [])
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
            scanned = 0
            for file in rule["files"]:
                path = root / file
                if not path.is_file():
                    continue  # sibling-repo file lands in parallel
                scanned += 1
                tree = ast.parse(path.read_text(encoding="utf-8"))
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
                        for lineno, _raw, module in imports_of(sub, file):
                            if module == "confflow" or module.startswith("confflow."):
                                violations.append(
                                    {
                                        "rule": rid,
                                        "path": file,
                                        "line": lineno,
                                        "detail": f"{owner}:{module}",
                                    }
                                )
            if rule.get("require_one"):
                assert scanned >= 1, "expected at least one double file to exist"
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
        "id": "RT-016",
        "source": "#16",
        "cases": [
            [
                {"op": "import", "module": "confflow.remote"},
                {
                    "op": "export_absent",
                    "module": "confflow.remote",
                    "names": ["lease", "supervision", "schema"],
                },
            ]
        ],
    },
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
        "id": "RT-019",
        "source": "#19",
        "cases": [
            [
                {"op": "import", "module": "confflow.remote.handoff"},
                {"op": "import", "module": "confflow.remote.staging"},
                {"op": "import", "module": "confflow.remote.transport"},
                {"op": "import", "module": "confflow.remote.worker"},
                {
                    "op": "forbid",
                    "exact": [
                        "confflow.remote.lease",
                        "confflow.remote.supervision",
                        "confflow.remote.schema",
                    ],
                },
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


RULE_COUNT = len(RULES)
