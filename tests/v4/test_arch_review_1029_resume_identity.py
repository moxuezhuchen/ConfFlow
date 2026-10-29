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
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.execution import batch as batch_module
from confflow.execution.batch import BatchStepExecutor
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import (
    OwnerIdentity,
    OwnerVerdict,
    PersistenceError,
    ReuseCode,
    ReuseDecision,
    StoredWorkItemStatus,
    store_path,
)
from confflow.persistence.reuse import ReuseInputs, evaluate_reuse
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


def _seed_stored(root: Path, case: _Case, state: str, **overrides: Any) -> None:
    """Create *state* with stored axes *overrides* without the batch comparison.

    Terminal rows are written through the store API (claim, then record the
    finished result), because executing them would compare the overridden
    axes against the live request and refuse before the test starts.
    """
    if not overrides or state not in ("completed", "failed"):
        _seed(root, case, state, **overrides)
        return
    outcome = WorkItemStatus.COMPLETED if state == "completed" else WorkItemStatus.FAILED
    with SqliteWorkItemStore.open(store_path(case.run_root, case.item.step_id)) as store:
        _register(store, case, **overrides)
        assert store.claim(case.item.id, owner=OwnerIdentity(owner_token="seed-A", pid=os.getpid()))
        finished = dataclasses.replace(
            h._result(case.item, status=outcome),
            metadata=FrozenDict({"actual_environment": ENV_A}),
        )
        store.record_finished(finished, environment_digest=case.req_a.environment.digest())


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


# ---------------------------------------------------------------------------
# F1: environment plus another axis is refused, never advanced
# ---------------------------------------------------------------------------

_STORED_OVERRIDE: dict[str, dict[str, Any]] = {
    "definition": {"step_semantic_digest": "sha256:" + "5" * 64},
    "input": {"work_item_digest": "sha256:" + "6" * 64},
}
_REFUSAL_CODE: dict[str, str] = {
    "definition": "invalidate_definition",
    "input": "invalidate_input",
    "artifact": "invalidate_artifact",
}
#: Artifact verification runs only for COMPLETED rows, so the artifact axis is
#: exercised there; the other statuses are covered at the decision level below.
COMBINED_CASES = [(state, axis) for state in STATES for axis in ("input", "definition")] + [
    ("completed", "artifact")
]


@pytest.mark.parametrize(("state", "axis"), COMBINED_CASES)
def test_environment_plus_other_axis_is_refused_like_that_axis_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, axis: str
) -> None:
    case = _case(tmp_path)
    _seed_stored(tmp_path, case, state, **_STORED_OVERRIDE.get(axis, {}))
    if axis == "artifact":
        monkeypatch.setattr(
            BatchStepExecutor, "_verify_stored_artifacts", staticmethod(lambda *_: False)
        )
    before = _state(case)

    recorder = _Recorder()
    with pytest.raises(PersistenceError, match=f"failed:{_REFUSAL_CODE[axis]}"):
        _run(case, case.req_b, recorder, "resume-B")

    assert recorder.calls == []
    assert _state(case) == before


@pytest.mark.parametrize("state", STATES)
def test_environment_refresh_fails_closed_when_other_axis_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """Defensive guard: a reuse verdict of environment-only is re-checked before any advance."""
    case = _case(tmp_path)
    _seed_stored(tmp_path, case, state, **_STORED_OVERRIDE["input"])
    before = _state(case)

    def forced_environment_only(**kwargs: Any) -> ReuseDecision:
        return ReuseDecision(
            decision=ReuseCode.INVALIDATE_ENVIRONMENT,
            reason="forced environment-only verdict",
            work_item_id=kwargs["work_item_id"],
        )

    monkeypatch.setattr(batch_module, "evaluate_reuse", forced_environment_only)
    recorder = _Recorder()
    with pytest.raises(PersistenceError, match="failed:invalidate_environment"):
        _run(case, case.req_b, recorder, "resume-B")

    assert recorder.calls == []
    assert _state(case) == before


def _reuse_inputs(environment: str, **overrides: Any) -> Any:
    axes: dict[str, Any] = {
        "work_item_digest": "sha256:" + "a" * 64,
        "step_semantic_digest": "sha256:" + "b" * 64,
        "environment_digest": environment,
        "producer_provenance": FrozenDict({"adapter_version": "adapter.v1"}),
        "artifact_checksums": (),
    }
    axes.update(overrides)
    return ReuseInputs(**axes)


@pytest.mark.parametrize(
    "status",
    [
        StoredWorkItemStatus.PENDING,
        StoredWorkItemStatus.RUNNING,
        StoredWorkItemStatus.COMPLETED,
        StoredWorkItemStatus.FAILED,
        StoredWorkItemStatus.INTERRUPTED,
    ],
)
@pytest.mark.parametrize(
    ("other", "expected"),
    [
        ({"work_item_digest": "sha256:" + "c" * 64}, "invalidate_input"),
        ({"step_semantic_digest": "sha256:" + "c" * 64}, "invalidate_definition"),
        ({"artifact_checksums": ("sha256:" + "c" * 64,)}, "invalidate_artifact"),
    ],
)
def test_decision_reports_other_axis_when_environment_also_changed(
    status: StoredWorkItemStatus, other: dict[str, Any], expected: str
) -> None:
    stored = _reuse_inputs("sha256:" + "1" * 64)
    current = _reuse_inputs("sha256:" + "2" * 64, **other)
    decision = evaluate_reuse(
        current=current,
        stored=stored,
        stored_status=status,
        owner_verdict=OwnerVerdict.DEFINITELY_DEAD,
        work_item_id="wi:f1",
    )
    assert decision.decision.value == expected
    assert decision.details["environment_also_changed"] is True
    assert decision.decision is not ReuseCode.INVALIDATE_ENVIRONMENT


@pytest.mark.parametrize(
    "status",
    [
        StoredWorkItemStatus.PENDING,
        StoredWorkItemStatus.RUNNING,
        StoredWorkItemStatus.COMPLETED,
        StoredWorkItemStatus.FAILED,
        StoredWorkItemStatus.INTERRUPTED,
    ],
)
def test_decision_environment_plus_unverified_artifacts_is_refused(
    status: StoredWorkItemStatus,
) -> None:
    decision = evaluate_reuse(
        current=_reuse_inputs("sha256:" + "2" * 64),
        stored=_reuse_inputs("sha256:" + "1" * 64),
        stored_status=status,
        owner_verdict=OwnerVerdict.DEFINITELY_DEAD,
        artifacts_verified=False,
        work_item_id="wi:f1",
    )
    if status is StoredWorkItemStatus.PENDING or status is StoredWorkItemStatus.RUNNING:
        # PENDING and RUNNING never verify artifacts; the environment advance applies.
        assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT
    else:
        assert decision.decision is ReuseCode.INVALIDATE_ARTIFACT
        assert decision.details["environment_also_changed"] is True


def test_environment_only_change_still_reports_environment() -> None:
    decision = evaluate_reuse(
        current=_reuse_inputs("sha256:" + "2" * 64),
        stored=_reuse_inputs("sha256:" + "1" * 64),
        stored_status=StoredWorkItemStatus.COMPLETED,
        work_item_id="wi:f1",
    )
    assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT
    assert "environment_also_changed" not in decision.details


def test_script_then_mlip_environment_and_input_change_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Application workflow: script output feeds MLIP; both environment and input move."""
    from tests.v4 import test_arch_review_launch_identity as launch

    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    monkeypatch.setenv("REVIEW_LAUNCH_X", "1")
    launch._register_script(tmp_path, tmp_path / "script-launches.log")
    launch._install_mlip_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5\n", encoding="utf-8")
    document = launch._script_doc()
    mlip = launch._mlip_doc(model, sys.executable)["steps"][0]
    mlip["bindings"]["structure"]["source"] = {"step": "s", "port": "structures"}
    document["steps"].append(mlip)
    run_root = tmp_path / "run"
    first = launch._run_script(run_root, document)
    assert first.status == "completed"

    item_id = "wi:uma:all"
    path = store_path(str(run_root), "uma")
    with SqliteWorkItemStore.open(path) as store:
        before = store.get_registered(item_id)
        store.refresh_for_new_environment(
            item_id,
            work_item_digest=before["work_item_digest"],
            step_semantic_digest=before["step_semantic_digest"],
            environment_digest=before["environment_digest"],
            producer_provenance=before["producer_provenance"],
            reason="seed a crashed attempt",
        )
        assert store.claim(item_id, owner=OwnerIdentity(owner_token="ghost", pid=DEAD_OWNER_PID))
        attempts_before = store.get_attempts(item_id)

    monkeypatch.setenv("REVIEW_LAUNCH_X", "2")
    with pytest.raises(PersistenceError, match="failed:invalidate_input"):
        launch._run_script(run_root, document)

    with SqliteWorkItemStore.open(path) as store:
        after = store.get_registered(item_id)
        assert store.get_attempts(item_id) == attempts_before
    assert after["environment_digest"] == before["environment_digest"]
    assert after["work_item_digest"] == before["work_item_digest"]
    assert after["producer_provenance"] == before["producer_provenance"]
