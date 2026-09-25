#!/usr/bin/env python3

"""Persistent, atomically-written workflow state.

Two durable state schema families share the single ``.workflow_state.json``
filename and are recognised strictly by ``content_schema`` — never by
heuristics:

- ``confflow.workflow_state.v1`` — the durable state of Workflow-Config-V2
  documents (the historical reader/writer below, preserved verbatim);
- ``confflow.workflow_state.v2`` — the durable state of Workflow-Config-V3
  documents, keyed by the persisted stable step ID.

Note the deliberate vocabulary: the *state schema* version is independent of
the *workflow configuration* version. A V3 configuration uses state v2; a V2
configuration uses state v1. Cross-version overwrites fail closed in both
directions, and the binding section of a v2 document is immutable once the
run is initialized (binding semantics are R4.2).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from ..artifact_json import write_atomic_json
from ..config.canonical.fingerprint import (
    WorkflowBindingCompatibilityError,
    WorkflowConfigBinding,
)
from ..config.canonical.v3_graph import is_persisted_id
from ..contract import (
    WORKFLOW_STATE_FILE,
    WORKFLOW_STATE_SCHEMA,
    WORKFLOW_STATE_SCHEMA_V2,
)
from .binding_v2 import WorkflowBindingV2

if TYPE_CHECKING:
    from .plan import WorkflowV3Plan

__all__ = [
    "StepRecord",
    "WorkflowState",
    "WorkflowConfigBinding",
    "WorkflowStateCompatibilityError",
    "WorkflowStateStore",
    "StepRecordV2",
    "WorkflowStateV2",
    "WorkflowStateV2Store",
    "WORKFLOW_STATE_SCHEMA_V2",
    "build_initial_state_v2",
    "detect_workflow_state_schema",
    "state_v2_payload",
]


class WorkflowStateCompatibilityError(ValueError):
    """A durable workflow state file cannot be safely interpreted."""


@dataclass
class StepRecord:
    """Persisted state for one configured workflow step."""

    name: str
    type: str
    status: str = "pending"
    submitted_at: float | None = None
    completed_at: float | None = None
    output_xyz: str | None = None
    error: str | None = None
    executor_handle_data: dict[str, Any] | None = None
    fail_count: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StepRecord:
        """Deserialize a record, rejecting shapes that would discard data."""
        if not isinstance(raw, dict):
            raise WorkflowStateCompatibilityError("workflow state step record must be an object")
        return cls(
            name=str(raw["name"]),
            type=str(raw["type"]),
            status=str(raw.get("status", "pending")),
            submitted_at=_optional_float(raw.get("submitted_at")),
            completed_at=_optional_float(raw.get("completed_at")),
            output_xyz=_optional_str(raw.get("output_xyz")),
            error=_optional_str(raw.get("error")),
            executor_handle_data=_optional_dict(raw.get("executor_handle_data")),
            fail_count=int(raw.get("fail_count", 0)),
        )


@dataclass
class WorkflowState:
    """All state required to resume a workflow from its working directory."""

    run_id: str
    work_dir: str
    input_files: list[str]
    original_inputs: list[str]
    config_file: str
    input_digests: list[str] = field(default_factory=list)
    steps: dict[str, StepRecord] = field(default_factory=dict)
    wavefront_index: int = 0
    started_at: float = field(default_factory=time.time)
    last_updated_at: float = field(default_factory=time.time)
    final_status: str = ""
    config_binding: WorkflowConfigBinding | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorkflowState:
        """Deserialize a state file and validate its minimum shape."""
        content_schema = raw.get("content_schema")
        if content_schema is not None and content_schema != WORKFLOW_STATE_SCHEMA:
            raise WorkflowStateCompatibilityError(
                f"unsupported workflow state content_schema {content_schema!r}; "
                f"expected {WORKFLOW_STATE_SCHEMA!r}"
            )
        binding_raw = raw.get("config_binding")
        if "binding" in raw:
            if binding_raw is not None:
                raise WorkflowStateCompatibilityError(
                    "workflow state contains both config_binding and binding"
                )
            binding_raw = raw["binding"]
        try:
            config_binding = (
                None if binding_raw is None else WorkflowConfigBinding.from_dict(binding_raw)
            )
        except WorkflowBindingCompatibilityError as exc:
            raise WorkflowStateCompatibilityError(str(exc)) from exc
        steps_raw = raw.get("steps", {})
        if not isinstance(steps_raw, dict):
            raise WorkflowStateCompatibilityError("workflow state 'steps' must be an object")
        steps: dict[str, StepRecord] = {}
        for key, value in steps_raw.items():
            if not isinstance(value, dict):
                raise WorkflowStateCompatibilityError(
                    f"workflow state step {key!r} must be an object"
                )
            steps[str(key)] = StepRecord.from_dict(value)
        return cls(
            run_id=str(raw["run_id"]),
            work_dir=str(raw["work_dir"]),
            input_files=_string_list(raw.get("input_files")),
            original_inputs=_string_list(raw.get("original_inputs")),
            config_file=str(raw["config_file"]),
            input_digests=_string_list(raw.get("input_digests", [])),
            config_binding=config_binding,
            steps=steps,
            wavefront_index=int(raw.get("wavefront_index", 0)),
            started_at=float(raw.get("started_at", time.time())),
            last_updated_at=float(raw.get("last_updated_at", time.time())),
            final_status=str(raw.get("final_status", "")),
        )


class WorkflowStateStore:
    """Atomically read and write ``<work_dir>/{WORKFLOW_STATE_FILE}``.

    The filename is sourced from :mod:`confflow.contract` so the on-disk
    layout and the cross-repository capability payload can never drift.
    """

    def __init__(self, work_dir: str):
        self.path = os.path.join(work_dir, WORKFLOW_STATE_FILE)

    def load(self) -> WorkflowState | None:
        """Return saved state, or ``None`` when no workflow state exists yet."""
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise WorkflowStateCompatibilityError(
                f"Invalid workflow state file {self.path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise WorkflowStateCompatibilityError(
                f"Invalid workflow state file {self.path}: expected an object"
            )
        try:
            return WorkflowState.from_dict(raw)
        except WorkflowStateCompatibilityError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowStateCompatibilityError(
                f"Invalid workflow state file {self.path}: {exc}"
            ) from exc

    def save(self, state: WorkflowState) -> None:
        """Persist state by replacing the destination only after JSON is complete.

        PD-10: a V1 state must never overwrite a ``workflow_state.v2`` file.
        The two state families share one filename, so a Workflow-Config-V3 run
        and a Workflow-Config-V2 run in the same work directory fail closed
        instead of silently erasing each other's durable identity.
        """
        _refuse_cross_version_overwrite(self.path, forbidden_schema=WORKFLOW_STATE_SCHEMA_V2)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        state.last_updated_at = time.time()
        payload = {"content_schema": WORKFLOW_STATE_SCHEMA, **asdict(state)}
        write_atomic_json(self.path, payload)


def _read_existing_state_schema(path: str) -> str | None:
    """Return the ``content_schema`` of an existing state file, if recognizable.

    ``None`` means the file is missing or unreadable — no schema to protect.
    A pre-schema-era v1 file (no ``content_schema``) reports the v1 schema,
    because that is the only family that ever wrote schema-less state.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    schema = raw.get("content_schema")
    if schema is None:
        return WORKFLOW_STATE_SCHEMA
    return str(schema)


def _refuse_cross_version_overwrite(path: str, *, forbidden_schema: str) -> None:
    """Fail closed when the durable file belongs to the other state family."""
    existing = _read_existing_state_schema(path)
    if existing == forbidden_schema:
        raise WorkflowStateCompatibilityError(
            f"Refusing to overwrite {path}: it holds durable state schema "
            f"{forbidden_schema!r}. The two state schema families share this "
            "filename; remove the file explicitly (or use a new work directory) "
            "to start under the other workflow version."
        )


def detect_workflow_state_schema(path: str) -> str | None:
    """Recognise a state file's schema — the single state-format truth.

    Returns ``None`` when the file is missing, the v1 schema for the legacy
    schema-less layout, or the ``content_schema`` string otherwise (including
    unknown schemas, which the callers must fail closed on). Recognition
    only: this never decides the *workflow configuration* version, which is a
    separate version axis handled by ``detect_schema_version``.
    """
    if not os.path.exists(path):
        return None
    return _read_existing_state_schema(str(path))


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WorkflowStateCompatibilityError(
            "workflow state executor_handle_data must be an object"
        )
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("expected a list of paths")
    return [str(item) for item in value]


# ---------------------------------------------------------------------------
# Workflow State V2 (R4.1) — durable state for Workflow-Config-V3 documents.
#
# Durable identity is the persisted stable step ID and nothing else: records
# are keyed by it, the record duplicates it (and the loader verifies key ==
# record id), and the label is a display snapshot that never participates in
# lookup, identity, paths or equality. The status vocabulary and the atomic
# write primitive are reused from V1 unchanged — the identity model changed,
# not the state machine.
# ---------------------------------------------------------------------------
_V2_STEP_STATUSES = ("pending", "submitted", "completed", "failed", "skipped")
_V2_STEP_TYPES = ("calc", "confgen")
_V2_STEP_RECORD_FIELDS = (
    "id",
    "label",
    "type",
    "status",
    "submitted_at",
    "completed_at",
    "output_xyz",
    "error",
    "fail_count",
    "executor_handle_data",
)
_V2_ROOT_FIELDS = (
    "run_id",
    "work_dir",
    "config_file",
    "input_files",
    "original_inputs",
    "input_digests",
    "definition_fingerprint",
    "binding",
    "steps",
    "execution_order",
    "wavefront_index",
    "started_at",
    "last_updated_at",
    "final_status",
)


def _require_digest(value: Any, what: str) -> str:
    """Validate a ``sha256:<64 hex>`` durable digest field."""
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise WorkflowStateCompatibilityError(f"workflow state {what} must be a 'sha256:…' digest")
    digest = value[len("sha256:") :]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
        raise WorkflowStateCompatibilityError(f"workflow state {what} is not a SHA-256 digest")
    return value


def _require_non_empty_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowStateCompatibilityError(f"workflow state {what} must be a non-empty string")
    return value


def _require_optional_float(value: Any, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkflowStateCompatibilityError(f"workflow state {what} must be a number")
    return float(value)


def _require_optional_str(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkflowStateCompatibilityError(f"workflow state {what} must be a string")
    return value


def _require_string_list(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise WorkflowStateCompatibilityError(f"workflow state {what} must be a list of strings")
    return list(value)


@dataclass
class StepRecordV2:
    """Persisted state for one V3 workflow step, identified by its stable ID.

    ``label`` is a display snapshot: it may drift from the document freely and
    never participates in identity, lookup, paths or equality.
    """

    id: str
    type: str
    label: str | None = None
    status: str = "pending"
    submitted_at: float | None = None
    completed_at: float | None = None
    output_xyz: str | None = None
    error: str | None = None
    executor_handle_data: dict[str, Any] | None = None
    fail_count: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StepRecordV2:
        if not isinstance(raw, dict):
            raise WorkflowStateCompatibilityError("workflow state step record must be an object")
        unknown = sorted(set(raw) - set(_V2_STEP_RECORD_FIELDS))
        if unknown:
            raise WorkflowStateCompatibilityError(
                f"workflow state step record has unknown fields: {', '.join(unknown)}"
            )
        step_id = _require_non_empty_str(raw.get("id"), "step id")
        if not is_persisted_id(step_id):
            raise WorkflowStateCompatibilityError(
                f"workflow state step id {step_id!r} is not a legal persisted V3 id"
            )
        step_type = _require_non_empty_str(raw.get("type"), "step type")
        if step_type not in _V2_STEP_TYPES:
            raise WorkflowStateCompatibilityError(
                f"workflow state step {step_id!r} has unsupported type {step_type!r}"
            )
        label = _require_optional_str(raw.get("label"), "step label")
        status = _require_non_empty_str(raw.get("status", "pending"), "step status")
        if status not in _V2_STEP_STATUSES:
            raise WorkflowStateCompatibilityError(
                f"workflow state step {step_id!r} has invalid status {status!r}"
            )
        fail_count = raw.get("fail_count", 0)
        if isinstance(fail_count, bool) or not isinstance(fail_count, int) or fail_count < 0:
            raise WorkflowStateCompatibilityError(
                f"workflow state step {step_id!r} fail_count must be a non-negative integer"
            )
        handle_data = raw.get("executor_handle_data")
        if handle_data is not None and not isinstance(handle_data, dict):
            raise WorkflowStateCompatibilityError(
                f"workflow state step {step_id!r} executor_handle_data must be an object"
            )
        return cls(
            id=step_id,
            type=step_type,
            label=label,
            status=status,
            submitted_at=_require_optional_float(raw.get("submitted_at"), "step submitted_at"),
            completed_at=_require_optional_float(raw.get("completed_at"), "step completed_at"),
            output_xyz=_require_optional_str(raw.get("output_xyz"), "step output_xyz"),
            error=_require_optional_str(raw.get("error"), "step error"),
            executor_handle_data=handle_data,
            fail_count=fail_count,
        )


@dataclass
class WorkflowStateV2:
    """Durable V3 run state keyed by stable step ID.

    ``binding`` is the typed, deeply immutable :class:`WorkflowBindingV2`;
    the wire payload is derived from it on every serialization and no API can
    replace the binding on a live state (step progress never touches it).
    ``execution_order`` is the frozen topological order this run uses —
    verified for internal consistency on every load/save.
    """

    run_id: str
    work_dir: str
    config_file: str
    definition_fingerprint: str
    binding: WorkflowBindingV2
    steps: dict[str, StepRecordV2]
    execution_order: list[str]
    input_files: list[str] = field(default_factory=list)
    original_inputs: list[str] = field(default_factory=list)
    input_digests: list[str] = field(default_factory=list)
    wavefront_index: int = 0
    started_at: float = field(default_factory=time.time)
    last_updated_at: float = field(default_factory=time.time)
    final_status: str = ""

    def step(self, step_id: str) -> StepRecordV2:
        """Return the record for ``step_id`` — stable IDs only, fail closed."""
        try:
            return self.steps[step_id]
        except KeyError:
            raise WorkflowStateCompatibilityError(
                f"workflow state has no step with stable id {step_id!r}"
            ) from None

    def update_step(self, step_id: str, **fields: Any) -> StepRecordV2:
        """Update one record's runtime fields; unknown IDs never create records."""
        record = self.step(step_id)
        mutable = set(_V2_STEP_RECORD_FIELDS) - {"id", "type"}
        unknown = sorted(set(fields) - mutable)
        if unknown:
            raise WorkflowStateCompatibilityError(
                f"workflow state step fields cannot be updated: {', '.join(unknown)}"
            )
        if "status" in fields and fields["status"] not in _V2_STEP_STATUSES:
            raise WorkflowStateCompatibilityError(
                f"workflow state step {step_id!r} has invalid status {fields['status']!r}"
            )
        for key, value in fields.items():
            setattr(record, key, value)
        return record

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorkflowStateV2:
        """Deserialize a v2 state document with closed-shape validation."""
        if not isinstance(raw, dict):
            raise WorkflowStateCompatibilityError("workflow state must be an object")
        schema = raw.get("content_schema")
        if schema != WORKFLOW_STATE_SCHEMA_V2:
            raise WorkflowStateCompatibilityError(
                f"unsupported workflow state content_schema {schema!r}; "
                f"expected {WORKFLOW_STATE_SCHEMA_V2!r}"
            )
        unknown = sorted(set(raw) - {"content_schema", *_V2_ROOT_FIELDS})
        if unknown:
            raise WorkflowStateCompatibilityError(
                f"workflow state has unknown fields: {', '.join(unknown)}"
            )
        run_id = _require_non_empty_str(raw.get("run_id"), "run_id")
        work_dir = _require_non_empty_str(raw.get("work_dir"), "work_dir")
        config_file = _require_non_empty_str(raw.get("config_file"), "config_file")
        fingerprint = _require_digest(raw.get("definition_fingerprint"), "definition_fingerprint")
        try:
            binding = WorkflowBindingV2.from_payload(raw.get("binding"))
        except ValueError as exc:
            raise WorkflowStateCompatibilityError(f"workflow state 'binding': {exc}") from exc

        steps_raw = raw.get("steps")
        if not isinstance(steps_raw, dict) or not steps_raw:
            raise WorkflowStateCompatibilityError(
                "workflow state 'steps' must be a non-empty object keyed by stable step id"
            )
        steps: dict[str, StepRecordV2] = {}
        for key, value in steps_raw.items():
            if not is_persisted_id(str(key)):
                raise WorkflowStateCompatibilityError(
                    f"workflow state step key {key!r} is not a legal persisted V3 id"
                )
            record = StepRecordV2.from_dict(value)
            if record.id != str(key):
                raise WorkflowStateCompatibilityError(
                    f"workflow state step key {key!r} does not match record id {record.id!r}"
                )
            steps[str(key)] = record

        execution_order = _require_string_list(raw.get("execution_order"), "execution_order")
        if len(set(execution_order)) != len(execution_order):
            raise WorkflowStateCompatibilityError(
                "workflow state 'execution_order' contains duplicate step ids"
            )
        if any(not is_persisted_id(step_id) for step_id in execution_order):
            raise WorkflowStateCompatibilityError(
                "workflow state 'execution_order' contains an illegal step id"
            )
        if set(execution_order) != set(steps):
            raise WorkflowStateCompatibilityError(
                "workflow state 'execution_order' does not match the persisted step ids"
            )

        wavefront_index = raw.get("wavefront_index", 0)
        if isinstance(wavefront_index, bool) or not isinstance(wavefront_index, int):
            raise WorkflowStateCompatibilityError(
                "workflow state 'wavefront_index' must be an integer"
            )
        if wavefront_index < 0:
            raise WorkflowStateCompatibilityError(
                "workflow state 'wavefront_index' must be non-negative"
            )

        started_at = raw.get("started_at", time.time())
        last_updated_at = raw.get("last_updated_at", time.time())
        for name, value in (("started_at", started_at), ("last_updated_at", last_updated_at)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WorkflowStateCompatibilityError(f"workflow state '{name}' must be a number")

        return cls(
            run_id=run_id,
            work_dir=work_dir,
            config_file=config_file,
            definition_fingerprint=fingerprint,
            binding=binding,
            steps=steps,
            execution_order=execution_order,
            input_files=_require_string_list(raw.get("input_files", []), "input_files"),
            original_inputs=_require_string_list(raw.get("original_inputs", []), "original_inputs"),
            input_digests=_require_string_list(raw.get("input_digests", []), "input_digests"),
            wavefront_index=wavefront_index,
            started_at=float(started_at),
            last_updated_at=float(last_updated_at),
            final_status=_require_optional_str(raw.get("final_status", ""), "final_status") or "",
        )


def _normalize_binding(binding: WorkflowBindingV2 | dict[str, Any]) -> WorkflowBindingV2:
    """Normalize a binding argument into the typed, immutable model.

    The typed model is the single validation authority for the binding wire
    shape (R4.2); this wrapper only adapts mapping-shaped callers and
    translates its errors into the stable state error type.
    """
    if isinstance(binding, WorkflowBindingV2):
        return binding
    try:
        return WorkflowBindingV2.from_payload(binding)
    except ValueError as exc:
        raise WorkflowStateCompatibilityError(f"workflow state 'binding': {exc}") from exc


def state_v2_payload(state: WorkflowStateV2) -> dict[str, Any]:
    """Return the durable v2 payload (deterministic under sorted-key JSON)."""
    payload = asdict(state)
    payload["binding"] = state.binding.to_payload()
    return {"content_schema": WORKFLOW_STATE_SCHEMA_V2, **payload}


class WorkflowStateV2Store:
    """Atomically read and write ``<work_dir>/{WORKFLOW_STATE_FILE}`` as v2.

    The V2 saver refuses to overwrite any existing state file that is not
    already v2 (legacy v1, explicit v1, or unknown) — the symmetric half of
    the cross-version protection. Writes reuse the V1 atomic primitive.
    """

    def __init__(self, work_dir: str):
        self.path = os.path.join(work_dir, WORKFLOW_STATE_FILE)

    def load(self) -> WorkflowStateV2 | None:
        """Return saved v2 state, ``None`` when no state file exists yet."""
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise WorkflowStateCompatibilityError(
                f"Invalid workflow state file {self.path}: {exc}"
            ) from exc
        try:
            return WorkflowStateV2.from_dict(raw)
        except WorkflowStateCompatibilityError as exc:
            raise WorkflowStateCompatibilityError(
                f"Invalid workflow state file {self.path}: {exc}"
            ) from exc

    def save(self, state: WorkflowStateV2) -> None:
        """Persist v2 state atomically, refusing cross-version overwrites."""
        existing = _read_existing_state_schema(self.path)
        if existing is not None and existing != WORKFLOW_STATE_SCHEMA_V2:
            raise WorkflowStateCompatibilityError(
                f"Refusing to overwrite {self.path}: it holds durable state schema "
                f"{existing!r}. The two state schema families share this filename; "
                "remove the file explicitly (or use a new work directory) to start "
                "under the other workflow version."
            )
        # Validate the exact bytes about to become durable: a mutated live
        # object (bad status, illegal id, inconsistent order) must fail closed
        # here rather than replace a good state file with an unloadable one.
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        state.last_updated_at = time.time()
        payload = state_v2_payload(state)
        WorkflowStateV2.from_dict(payload)
        write_atomic_json(self.path, payload)


def build_initial_state_v2(
    plan: WorkflowV3Plan,
    *,
    run_id: str,
    work_dir: str,
    config_file: str,
    binding: WorkflowBindingV2 | dict[str, Any],
    input_files: list[str] | None = None,
    original_inputs: list[str] | None = None,
    input_digests: list[str] | None = None,
) -> WorkflowStateV2:
    """Build the initial v2 state for a planned V3 workflow.

    Copies only durable-identity data from the plan: stable IDs, the label
    display snapshot, the step type, and the initial status (V1 vocabulary —
    a disabled step starts ``skipped``). No dirname, runtime path, artifact
    path or execution fingerprint is materialised here. ``binding`` must be
    supplied by the caller as a typed :class:`WorkflowBindingV2` (R4.2 owns
    its construction) or an equivalent validated payload mapping; it is
    normalized into the typed, immutable model.
    """
    steps: dict[str, StepRecordV2] = {}
    for planned in plan.steps:
        if planned.id in steps:
            raise WorkflowStateCompatibilityError(
                f"plan contains duplicate stable step id {planned.id!r}"
            )
        steps[planned.id] = StepRecordV2(
            id=planned.id,
            type=planned.type,
            label=planned.label,
            status="skipped" if not planned.enabled else "pending",
        )
    return WorkflowStateV2(
        run_id=_require_non_empty_str(run_id, "run_id"),
        work_dir=_require_non_empty_str(work_dir, "work_dir"),
        config_file=_require_non_empty_str(config_file, "config_file"),
        definition_fingerprint=plan.definition_fingerprint,
        binding=_normalize_binding(binding),
        steps=steps,
        execution_order=list(plan.topological_order),
        input_files=list(input_files or []),
        original_inputs=list(original_inputs if original_inputs is not None else input_files or []),
        input_digests=list(input_digests or []),
    )
