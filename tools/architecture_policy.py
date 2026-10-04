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
import re
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


def _resolve_relative(relpath: str, level: int, module: str | None) -> str:
    package_parts = _rel_module(relpath).split(".")
    if relpath.endswith("/__init__.py"):
        base = package_parts[: len(package_parts) - level]
    else:
        base = package_parts[: len(package_parts) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def imports_of(tree: ast.AST, relpath: str) -> list[tuple[int, str, str]]:
    """Return (lineno, raw_module, resolved_module) for every import target.

    ``raw`` mirrors the original ``_imports`` (relative imports stay dotted);
    ``resolved`` applies the original relative-import resolution.
    """
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                hits.append((node.lineno, alias.name, alias.name))
        elif isinstance(node, ast.ImportFrom):
            raw = "." * node.level + (node.module or "")
            resolved = (
                _resolve_relative(relpath, node.level, node.module)
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


def code_text(source: str) -> str:
    """Return *source* with comments and docstrings blanked in place.

    Blanking happens on the UTF-8 byte buffer (AST columns are byte offsets
    within their line); ``tokenize`` character columns are converted to byte
    offsets per line.  Every remaining character keeps its original byte
    offset and line, so line-level regex patterns keep both their line scope
    and the original diagnostics line numbers.  Multi-byte characters are
    never split (spans align to token boundaries).  Non-docstring string
    constants are preserved.
    """
    import io
    import tokenize

    data = bytearray(source.encode("utf-8"))
    tree = ast.parse(source)

    line_starts = [0]
    for line in source.splitlines(keepends=True):
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

    for start, end in _docstring_byte_spans(tree, data):
        blank(start, end)
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
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


def _file_iter(scope_resolved: list[str], root: Path):
    for entry in scope_resolved:
        path = root / entry
        if not path.exists():
            continue  # scope files that do not exist in the tree are not scannable
        if path.suffix == ".py":
            yield entry, path
        elif path.is_dir():
            for sub in sorted(path.rglob("*.py")):
                yield str(sub.relative_to(root)), sub


def _match_import(module: str, rule: dict) -> bool:
    for self_prefix in rule.get("self_prefixes", []):
        if module == self_prefix or module.startswith(self_prefix + "."):
            return False
    mode = rule["mode"]
    if mode.startswith("allowed") and not (module == "confflow" or module.startswith("confflow.")):
        return False
    if mode == "forbidden_prefixes":
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


def scan(root: Path) -> list[dict]:
    """Run every static rule; return a list of violation dicts."""
    root = Path(root)
    violations: list[dict] = []
    cache: dict[str, tuple[str, ast.AST]] = {}

    def parsed(relpath: str) -> tuple[str, ast.AST]:
        if relpath not in cache:
            source = (root / relpath).read_text(encoding="utf-8")
            cache[relpath] = (source, ast.parse(source))
        return cache[relpath]

    for rule in RULES:
        kind = rule["kind"]
        rid = rule["id"]
        if kind == "imports":
            scope = _resolve_scope(rule["scope"], root)
            exempt = rule.get("exempt_imports", {})
            resolve_relative = rule.get("resolve_relative", False)
            for relpath, _path in _file_iter(scope, root):
                if relpath in exempt:
                    continue
                source, tree = parsed(relpath)
                for lineno, raw, resolved in imports_of(tree, relpath):
                    module = resolved if resolve_relative else raw
                    allowed_here = any(
                        module == a or module.startswith(a + ".") for a in exempt.get(relpath, [])
                    )
                    if allowed_here:
                        continue
                    if _match_import(module, rule):
                        violations.append(
                            {"rule": rid, "path": relpath, "line": lineno, "detail": module}
                        )
        elif kind == "symbols":
            scope = _resolve_scope(rule["scope"], root)
            exempt = rule.get("exempt_symbols", {})
            for relpath, _path in _file_iter(scope, root):
                _, tree = parsed(relpath)
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
            for relpath, _path in _file_iter(scope, root):
                if relpath in allowed_files or relpath in exempt_files:
                    continue
                source, tree = parsed(relpath)
                text = source if raw else code_text(source)
                skip = set(exempt_symbols.get(relpath, []))
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
            for relpath, _path in _file_iter(scope, root):
                text = code_text((root / relpath).read_text(encoding="utf-8"))
                for lineno, line in enumerate(text.splitlines(), start=1):
                    for check, pattern in compiled:
                        if pattern.search(line):
                            violations.append(
                                {"rule": rid, "path": relpath, "line": lineno, "detail": check}
                            )
        elif kind == "custom_task_dispatch":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root):
                for lineno, member in task_dispatch_offenders(
                    (root / relpath).read_text(encoding="utf-8")
                ):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": member}
                    )
        elif kind == "custom_filename_idioms":
            scope = _resolve_scope(rule["scope"], root)
            for relpath, _path in _file_iter(scope, root):
                for lineno, token in filename_pairing_offenders(
                    (root / relpath).read_text(encoding="utf-8")
                ):
                    violations.append(
                        {"rule": rid, "path": relpath, "line": lineno, "detail": token}
                    )
        elif kind == "custom_range_ordinal":
            scope = _resolve_scope(rule["scope"], root)
            allow = set(rule.get("allowlist", []))
            for relpath, _path in _file_iter(scope, root):
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
            for relpath, _path in _file_iter(scope, root):
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

    The guarded authorities live in the guard test modules, so this check
    reads their current values from the real source files instead of trusting
    the copies embedded above.  Any deviation is reported as a problem.
    """
    problems: list[str] = []
    guard_source = root / "tests/v4/test_architecture_boundaries.py"
    v46_source = root / "tests/v4/test_v46_debt.py"

    internal = _read_source_constant(guard_source, "INTERNAL_ONLY_V3_MIGRATION_MODULES")
    if internal is None:
        problems.append("AP-027: guard source missing INTERNAL_ONLY_V3_MIGRATION_MODULES")
    else:
        internal = list(internal)
        if internal != []:
            problems.append("AP-027: INTERNAL_ONLY_V3_MIGRATION_MODULES must be empty")
        for module in internal:
            relpath = module.replace(".", "/") + ".py"
            if (root / relpath).is_file():
                problems.append(f"AP-027: retired module present on disk: {module}")

    v1 = list(_read_source_constant(guard_source, "V1_MIGRATION_MODULES") or [])
    v2 = list(_read_source_constant(guard_source, "V2_MIGRATION_MODULES") or [])
    if v1 != []:
        problems.append("AP-033a: V1 migration kernel must be empty")
    if v2 != ["confflow.config.canonical.v2_adapter"]:
        problems.append("AP-033a: V2 migration kernel must be exactly v2_adapter")
    for module in [*(v1 or []), *(v2 or [])]:
        relpath = module.replace(".", "/") + ".py"
        if (root / relpath).is_file():
            problems.append(f"AP-033a: retired kernel module present on disk: {module}")

    forbidden = _read_source_constant(guard_source, "FORBIDDEN_SYMBOLS")
    new46 = _read_source_constant(v46_source, "V46_NEW_SYMBOLS")
    if forbidden is None or new46 is None:
        problems.append("AP-070: guard symbol tables missing from sources")
    elif not set(new46).isdisjoint(set(forbidden)):
        problems.append("AP-070: V46 new symbols overlap the forbidden symbol table")
    return problems


RULE_COUNT = len(RULES)
