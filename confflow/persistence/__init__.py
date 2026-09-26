#!/usr/bin/env python3

"""V4 durable persistence: per-item truth for resume and reuse.

Authority hierarchy (frozen)::

    WorkItemStore  = per-item execution / attempts / resume / reuse truth
    StepResult     = published semantic output truth
    RunState       = workflow / step lifecycle truth
    Artifacts      = durable external / native file objects

Filenames, directory names, log existence, and ``result.xyz``-style side
channels are never semantic truth.

This package imports only ``confflow.domain`` plus the standard library.
The executor side (``confflow.execution``) depends on this package, never the
reverse.  ``contracts.py`` is main-agent owned and frozen; implementation
modules are owned by their respective workstreams.
"""

from __future__ import annotations

from .artifacts import (
    ArtifactIntegrityError,
    apply_gc,
    plan_gc,
    verify_artifact,
)
from .contracts import (
    ALLOWED_TRANSITIONS,
    PERSISTENCE_SCHEMA_VERSION,
    RUN_STATE_FILENAME,
    SCHEMA_KIND,
    STEP_RESULT_FILENAME,
    STEP_STORE_FILENAME,
    CorruptStateError,
    GCEntry,
    GCPlan,
    OwnerIdentity,
    OwnerVerdict,
    PersistenceError,
    ReuseCode,
    ReuseDecision,
    RunState,
    RunStepStatus,
    SchemaVersionError,
    StateTransitionError,
    StepLifecycle,
    StoredWorkItemStatus,
    is_legal_transition,
    is_retryable_status,
    is_terminal_status,
    item_work_dir,
    run_state_path,
    step_artifacts_dir,
    step_dir,
    step_result_path,
    store_path,
    validate_run_root,
    wall_now,
)
from .publication import (
    STEP_RESULT_DIGEST_KIND,
    load_published_step_result,
    publish_step_result,
    rebuild_step_result,
    verify_for_publication,
)
from .recovery import owner_identity_current, reconcile_owner
from .reuse import ReuseInputs, build_producer_provenance, evaluate_reuse
from .run_state import (
    detect_published,
    ensure_step,
    load_run_state,
    repair_after_publish,
    save_run_state,
    transition_step,
)
from .work_items import SqliteWorkItemStore, StoredAttempt

__all__ = [
    "ALLOWED_TRANSITIONS",
    "GCEntry",
    "GCPlan",
    "ArtifactIntegrityError",
    "OwnerIdentity",
    "OwnerVerdict",
    "PERSISTENCE_SCHEMA_VERSION",
    "CorruptStateError",
    "PersistenceError",
    "ReuseCode",
    "ReuseDecision",
    "ReuseInputs",
    "RunState",
    "RunStepStatus",
    "RUN_STATE_FILENAME",
    "SCHEMA_KIND",
    "STEP_RESULT_DIGEST_KIND",
    "STEP_RESULT_FILENAME",
    "STEP_STORE_FILENAME",
    "SchemaVersionError",
    "SqliteWorkItemStore",
    "StateTransitionError",
    "StepLifecycle",
    "StoredAttempt",
    "StoredWorkItemStatus",
    "apply_gc",
    "build_producer_provenance",
    "detect_published",
    "ensure_step",
    "evaluate_reuse",
    "is_legal_transition",
    "is_retryable_status",
    "is_terminal_status",
    "item_work_dir",
    "load_published_step_result",
    "load_run_state",
    "owner_identity_current",
    "plan_gc",
    "publish_step_result",
    "rebuild_step_result",
    "reconcile_owner",
    "repair_after_publish",
    "run_state_path",
    "save_run_state",
    "step_artifacts_dir",
    "step_dir",
    "step_result_path",
    "store_path",
    "transition_step",
    "validate_run_root",
    "verify_artifact",
    "verify_for_publication",
    "wall_now",
]
