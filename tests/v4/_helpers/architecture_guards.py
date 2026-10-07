"""Shared architecture-guard constants and scanners (moved verbatim).

Source of truth for the guard constants lives in
tests/v4/test_architecture_boundaries.py; see SOURCE-ANCHOR-MAP.json for the
per-symbol anchor mapping used by L0.4a re-location.
"""

from __future__ import annotations

import ast

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
    "confflow.workflow.rerun_failed",
    "confflow.workflow.config_show",
    "confflow.workflow.composition",
    "confflow.config.canonical",
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


_ORDINAL_WITHIN_ITEM_FILES = frozenset(
    {
        "confflow/execution/native.py",
        "confflow/execution/output_identity.py",
        "confflow/execution/profile_ensemble.py",
        "confflow/execution/profile_path_endpoints.py",
    }
)


_RANGE_ORDINAL_ALLOWLIST: frozenset[str] = frozenset()


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
    ``b[i]`` for a ``range`` ordinal ``i``).  Dict/keyed access never
    shares a ``range`` ordinal and is not flagged by construction.
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
