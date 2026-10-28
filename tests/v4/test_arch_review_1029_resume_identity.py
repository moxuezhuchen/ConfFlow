"""Architecture review 2026-10-29 N1: resumed items advance their identity.

A work item whose durable record is PENDING, or RUNNING under a definitely
dead owner, must compare the stored identity with the current one before it
is re-claimed.  An environment-only change advances the registration through
the same generation rule used for terminal rows, and the abandoned attempt
stays in the attempt history.  Definition, input, or provenance changes keep
their own invalidation rules and never re-execute.

The scientific executor is replaced by a recorder; the store and the batch
executor are real.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.execution.batch import BatchStepExecutor
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import (
    OwnerIdentity,
    PersistenceError,
    StoredWorkItemStatus,
    store_path,
)
from confflow.persistence.work_items import SqliteWorkItemStore
from tests.v4._helpers import v43_coverage as h

DEAD_OWNER_PID = 2**30
ENV_A = "A"
ENV_B = "B"
STATES = ("pending", "running_dead", "completed", "failed", "interrupted")
AXES = ("definition", "input", "provenance")


class _Recorder:
    """Scientific stand-in that records the environment each launch ran under."""

    def __init__(self, outcomes: tuple[WorkItemStatus, ...] = ()) -> None:
        self.calls: list[str] = []
        self._outcomes = list(outcomes)

    def execute(self, item: Any, context: Any, **_: Any) -> Any:
        label = context.native_env["SCIENCE_ENV"]
        self.calls.append(label)
        status = self._outcomes.pop(0) if self._outcomes else WorkItemStatus.COMPLETED
        return dataclasses.replace(
            h._result(item, status=status),
            metadata=FrozenDict({"actual_environment": label}),
        )


@dataclasses.dataclass
class _Case:
    plan: Any
    item: Any
    req_a: Any
    req_b: Any
    run_root: str


def _case(root: Path) -> _Case:
    plan = h._compile(h._document())
    item = h._items(plan, 1)[0]
    run_root = str(root / "run")
    base = h._request(plan, (item,), run_root)

    def with_env(label: str) -> Any:
        env = dataclasses.replace(base.environment, relevant_env=FrozenDict({"SCIENCE_ENV": label}))
        return dataclasses.replace(base, environment=env, native_env=env.relevant_env)

    return _Case(
        plan=plan, item=item, req_a=with_env(ENV_A), req_b=with_env(ENV_B), run_root=run_root
    )


def _batch(recorder: _Recorder) -> BatchStepExecutor:
    return BatchStepExecutor(recorder).with_supervisor(NativeProcessSupervisor())


def _register(store: SqliteWorkItemStore, case: _Case, **overrides: Any) -> None:
    """Register the item under the A identity, with optional stored overrides."""
    batch = _batch(_Recorder())
    axes: dict[str, Any] = {
        "work_item_id": case.item.id,
        "logical_key": case.item.logical_key,
        "step_id": case.item.step_id,
        "work_item_digest": case.item.semantic_digest,
        "step_semantic_digest": case.plan.steps[0].step_semantic_digest,
        "environment_digest": case.req_a.environment.digest(),
        "producer_provenance": dict(batch._current_provenance(case.req_a).thaw()),
    }
    axes.update(overrides)
    store.register_item(**axes)


def _seed(root: Path, case: _Case, state: str, **overrides: Any) -> None:
    """Create the durable state *state* under the A identity."""
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        _register(store, case, **overrides)
        if state == "pending":
            return
        if state in ("completed", "failed"):
            outcome = WorkItemStatus.COMPLETED if state == "completed" else WorkItemStatus.FAILED
            _execute(store, case, case.req_a, _Recorder((outcome,)), "seed-A")
            return
        assert store.claim(
            case.item.id, owner=OwnerIdentity(owner_token="ghost", pid=DEAD_OWNER_PID)
        )
        if state == "interrupted":
            store.mark_interrupted(case.item.id, reason="seeded interruption")


def _execute(
    store: SqliteWorkItemStore, case: _Case, request: Any, recorder: _Recorder, token: str
) -> Any:
    batch = _batch(recorder)
    return batch.execute_step_resumable(
        request, store=store, run_root=case.run_root, owner_token=token
    )


def _run(case: _Case, request: Any, recorder: _Recorder, token: str) -> Any:
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        return _execute(store, case, request, recorder, token)


def _state(case: _Case) -> dict[str, Any]:
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        registered = store.get_registered(case.item.id)
        attempts = [(a.attempt_number, a.status) for a in store.get_attempts(case.item.id)]
        result = store.get_result(case.item.id)
        return dict(
            identity=(
                registered["work_item_digest"],
                registered["step_semantic_digest"],
                registered["producer_provenance"],
            ),
            environment_digest=registered["environment_digest"],
            attempts=attempts,
            actual_environment=(
                result.metadata["actual_environment"] if result is not None else None
            ),
        )


@pytest.mark.parametrize("state", STATES)
def test_same_environment_resume_reuses_or_relaunches_once(tmp_path: Path, state: str) -> None:
    case = _case(tmp_path)
    _seed(tmp_path, case, state)
    first = _Recorder()
    _run(case, case.req_a, first, "resume-A")
    assert first.calls == ([] if state == "completed" else [ENV_A])
    second = _Recorder()
    _run(case, case.req_a, second, "resume-A-again")
    assert second.calls == []
    assert _state(case)["environment_digest"] == case.req_a.environment.digest()


@pytest.mark.parametrize("state", STATES)
def test_changed_environment_advances_registration_and_recomputes(
    tmp_path: Path, state: str
) -> None:
    case = _case(tmp_path)
    _seed(tmp_path, case, state)
    digest_a = case.req_a.environment.digest()
    digest_b = case.req_b.environment.digest()
    assert digest_a != digest_b

    changed = _Recorder()
    _run(case, case.req_b, changed, "resume-B")
    assert changed.calls == [ENV_B]
    after_b = _state(case)
    assert after_b["environment_digest"] == digest_b
    assert after_b["actual_environment"] == ENV_B
    numbers = [number for number, _ in after_b["attempts"]]
    assert numbers == sorted(set(numbers))
    if state == "running_dead":
        # The abandoned attempt is interrupted, kept for audit, and not reopened.
        assert after_b["attempts"][0][1] is StoredWorkItemStatus.INTERRUPTED

    # Switching back to A must not reuse the B result.
    back = _Recorder()
    _run(case, case.req_a, back, "resume-A-back")
    assert back.calls == [ENV_A]
    after_a = _state(case)
    assert after_a["environment_digest"] == digest_a
    assert after_a["actual_environment"] == ENV_A


@pytest.mark.parametrize("state", ("pending", "running_dead"))
@pytest.mark.parametrize(
    ("axis", "code"),
    [
        ("definition", "invalidate_definition"),
        ("input", "invalidate_input"),
        ("provenance", "invalidate_provenance"),
    ],
)
def test_changed_definition_input_or_provenance_keeps_own_rule(
    tmp_path: Path, state: str, axis: str, code: str
) -> None:
    case = _case(tmp_path)
    stored_override: dict[str, Any] = {
        "definition": {"step_semantic_digest": "sha256:" + "5" * 64},
        "input": {"work_item_digest": "sha256:" + "6" * 64},
        "provenance": {"producer_provenance": {"adapter_version": "old.v0"}},
    }[axis]
    _seed(tmp_path, case, state, **stored_override)
    before = _state(case)

    recorder = _Recorder()
    # Strict publication: the invalidated item has no durable payload, so the
    # step refuses to publish and names the per-item reason.
    with pytest.raises(PersistenceError, match=f"failed:{code}"):
        _run(case, case.req_a, recorder, "resume-A")

    assert recorder.calls == []
    assert _state(case) == before


def test_refresh_for_new_environment_keeps_pending_status(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _seed(tmp_path, case, "pending")
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        registered = store.get_registered(case.item.id)
        store.refresh_for_new_environment(
            case.item.id,
            work_item_digest=registered["work_item_digest"],
            step_semantic_digest=registered["step_semantic_digest"],
            environment_digest=case.req_b.environment.digest(),
            producer_provenance=registered["producer_provenance"],
            reason="unit: pending advance",
        )
        assert store.get_state(case.item.id) is StoredWorkItemStatus.PENDING
        assert store.get_registered(case.item.id)["environment_digest"] == (
            case.req_b.environment.digest()
        )
        assert store.get_attempts(case.item.id) == ()


def test_refresh_rejects_running_item(tmp_path: Path) -> None:
    case = _case(tmp_path)
    _seed(tmp_path, case, "running_dead")
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        registered = store.get_registered(case.item.id)
        with pytest.raises(PersistenceError, match="cannot advance environment generation"):
            store.refresh_for_new_environment(
                case.item.id,
                work_item_digest=registered["work_item_digest"],
                step_semantic_digest=registered["step_semantic_digest"],
                environment_digest=case.req_b.environment.digest(),
                producer_provenance=registered["producer_provenance"],
            )
