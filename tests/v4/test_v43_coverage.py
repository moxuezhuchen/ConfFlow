#!/usr/bin/env python3

"""V4-3 branch coverage: persistence error paths and durable-runner seams.

Fast unit tests over every fail-closed branch the happy-path suites do not
reach: corrupt reconstruction payloads, store misuse, contested claims,
invalidation, live-owner blocking, ghost-owner recovery, and contract
validation.  Three integration tests drive abandoned/invalidated/blocked
recovery through ``execute_step_resumable`` with the fake ORCA executable.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.domain.diagnostics import Diagnostic
from confflow.domain.work_item import WorkItemResult
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.persistence import (
    PERSISTENCE_SCHEMA_VERSION,
    CorruptStateError,
    OwnerIdentity,
    OwnerVerdict,
    PersistenceError,
    ReuseCode,
    ReuseDecision,
    RunState,
    RunStepStatus,
    StateTransitionError,
    StepLifecycle,
    StoredWorkItemStatus,
    is_legal_transition,
    is_retryable_status,
    is_terminal_status,
    run_state_path,
    step_result_path,
    store_path,
    validate_run_root,
    wall_now,
)
from confflow.persistence.artifacts import ArtifactIntegrityError, apply_gc, plan_gc
from confflow.persistence.contracts import GCEntry, GCPlan
from confflow.persistence.recovery import owner_identity_current, reconcile_owner
from confflow.persistence.reuse import ReuseInputs, build_producer_provenance, evaluate_reuse
from confflow.persistence.run_state import (
    detect_published,
    ensure_step,
    load_run_state,
    repair_after_publish,
    save_run_state,
    transition_step,
)
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.registry import get_program_adapter
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure,
    v4_doc,
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"
STEP_ID = "s_opt"


def _document(**overrides: Any) -> dict[str, Any]:
    """Build the single-calculation coverage document."""
    step = calc_step(
        STEP_ID,
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP D3BJ def2-SVP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": str(FAKE_ORCA)},
    )
    return v4_doc([step], inputs={"structures": {"kind": "structure", "cardinality": "many"}})


def _compile(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def _items(plan: Any, count: int) -> tuple[Any, ...]:
    """Assemble *count* distinct work items."""
    structures = StructureSet.of(
        *(structure(f"c{i:03d}", offset=float(i) * 0.002) for i in range(count))
    )
    assembly = assemble(plan, run_inputs(structures={"structures": structures}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return tuple(assembly.items)


def _request(plan: Any, items: tuple[Any, ...], run_root: str) -> StepExecutionRequest:
    """Build a durable step request wired to the fake ORCA executable."""
    planned = plan.steps[0]
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter("orca"),
        profile=PROFILES["standard"],
        checks=(CHECKS["normal_termination"],),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(FAKE_ORCA), env=FrozenDict({})
        ),
        run_root=run_root,
        environment=None,
        definition_digest=plan.definition_digest,
    )


def _result(item: Any, *, status: WorkItemStatus) -> WorkItemResult:
    """Build a minimal terminal result for *item*."""
    from confflow.domain.work_item import RecoveryInfo, ResultError, Timing

    error = None
    diagnostics: tuple[Diagnostic, ...] = ()
    if status is not WorkItemStatus.COMPLETED:
        error = ResultError(code="native_execution_error", message="boom", retryable=True)
        diagnostics = (Diagnostic(code="native_execution_error", message="boom"),)
    return WorkItemResult(
        work_item_id=item.id,
        status=status,
        diagnostics=diagnostics,
        timing=Timing(finished_at=wall_now(), duration_seconds=0.1),
        error=error,
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=item.semantic_digest,
    )


def _store(tmp_path: Path, name: str = "store") -> SqliteWorkItemStore:
    """Open a step store under *tmp_path*."""
    path = str(tmp_path / "run" / "steps" / STEP_ID / f"{name}.sqlite")
    return SqliteWorkItemStore.open(path)


def _register(store: SqliteWorkItemStore, item: Any) -> None:
    """Register *item* with its live digests."""
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest="sha256:" + "a" * 64,
        environment_digest=None,
        producer_provenance={},
    )


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


class TestContractValidation:
    """Every frozen contract fails closed on bad input."""

    def test_run_root_validation(self) -> None:
        with pytest.raises(PersistenceError):
            validate_run_root("")
        with pytest.raises(PersistenceError):
            validate_run_root("  ")
        with pytest.raises(PersistenceError):
            validate_run_root("a\x00b")
        assert validate_run_root("/tmp/x") == "/tmp/x"
        assert wall_now() > 0.0

    def test_layout_helpers_reject_segments(self) -> None:
        with pytest.raises(PersistenceError):
            store_path("/tmp/r", "../evil")
        with pytest.raises(PersistenceError):
            store_path("/tmp/r", "a/b")
        with pytest.raises(PersistenceError):
            step_result_path("/tmp/r", "")
        assert store_path("/tmp/r", "s").endswith("work_items.sqlite")
        assert step_result_path("/tmp/r", "s").endswith("step_result.json")
        assert run_state_path("/tmp/r").endswith("run_state.json")

    def test_state_machine_predicates(self) -> None:
        assert is_legal_transition(StoredWorkItemStatus.PENDING, StoredWorkItemStatus.RUNNING)
        assert not is_legal_transition(StoredWorkItemStatus.COMPLETED, StoredWorkItemStatus.RUNNING)
        assert not is_legal_transition(StoredWorkItemStatus.CANCELLED, StoredWorkItemStatus.RUNNING)
        assert is_terminal_status(StoredWorkItemStatus.COMPLETED)
        assert is_terminal_status(StoredWorkItemStatus.CANCELLED)
        assert not is_terminal_status(StoredWorkItemStatus.FAILED)
        assert is_retryable_status(StoredWorkItemStatus.FAILED)
        assert is_retryable_status(StoredWorkItemStatus.INTERRUPTED)
        assert not is_retryable_status(StoredWorkItemStatus.PENDING)
        assert PERSISTENCE_SCHEMA_VERSION == 1

    def test_reuse_decision_validation(self) -> None:
        with pytest.raises(PersistenceError):
            ReuseDecision(decision="reuse", reason="x", work_item_id="w")  # type: ignore[arg-type]
        with pytest.raises(PersistenceError):
            ReuseDecision(decision=ReuseCode.REUSE, reason="  ", work_item_id="w")
        with pytest.raises(PersistenceError):
            ReuseDecision(decision=ReuseCode.REUSE, reason="x", work_item_id="")
        decision = ReuseDecision(
            decision=ReuseCode.REUSE, reason="ok", work_item_id="w", details={"a": 1}
        )
        assert isinstance(decision.details, FrozenDict)
        assert decision.to_dict()["decision"] == "reuse"

    def test_owner_identity_validation(self) -> None:
        with pytest.raises(PersistenceError):
            OwnerIdentity(owner_token="  ")
        with pytest.raises(PersistenceError):
            OwnerIdentity(owner_token="t", pid=0)
        with pytest.raises(PersistenceError):
            OwnerIdentity(owner_token="t", pid=True)
        with pytest.raises(PersistenceError):
            OwnerIdentity(owner_token="t", create_time="now")  # type: ignore[arg-type]
        identity = OwnerIdentity(owner_token="t", pid=os.getpid())
        assert identity.to_dict()["owner_token"] == "t"

    def test_run_state_from_dict_corrupt(self) -> None:
        with pytest.raises(CorruptStateError):
            RunState.from_dict([])
        with pytest.raises(CorruptStateError):
            RunState.from_dict({})
        with pytest.raises(CorruptStateError):
            RunState.from_dict({"run_id": "r", "schema_version": 999})
        with pytest.raises(CorruptStateError):
            RunState.from_dict({"run_id": "r", "steps": [{"step_id": "s", "status": "bogus"}]})
        with pytest.raises(CorruptStateError):
            RunState.from_dict(
                {
                    "run_id": "r",
                    "steps": [{"step_id": "s"}, {"step_id": "s"}],
                }
            )
        state = RunState.from_dict({"run_id": "r"})
        assert state.step("missing") is None
        assert state.to_dict()["run_id"] == "r"
        with pytest.raises(PersistenceError):
            StepLifecycle(step_id="s", status="running")  # type: ignore[arg-type]
        with pytest.raises(PersistenceError):
            RunState(run_id="  ")
        with pytest.raises(PersistenceError):
            RunState(run_id="r", steps=("nope",))  # type: ignore[list-item]

    def test_gc_contracts_validation(self) -> None:
        with pytest.raises(PersistenceError):
            GCEntry(artifact_id="", reason="x")
        with pytest.raises(PersistenceError):
            GCEntry(artifact_id="a", reason="  ")
        with pytest.raises(PersistenceError):
            GCPlan(entries=("nope",))  # type: ignore[list-item]
        plan = GCPlan(entries=(GCEntry(artifact_id="a", reason="x", locator_path="/tmp/a"),))
        assert plan.artifact_ids == ("a",)
        assert plan.to_dict()["entries"][0]["artifact_id"] == "a"


# ---------------------------------------------------------------------------
# Store misuse and corruption
# ---------------------------------------------------------------------------


class TestStoreMisuse:
    """Store guards fail closed on every misuse shape."""

    def test_open_variants(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "ghost.sqlite")
        with pytest.raises(PersistenceError):
            SqliteWorkItemStore.open(missing, create=False)
        with pytest.raises(PersistenceError):
            SqliteWorkItemStore.open(":memory:")
        with pytest.raises(PersistenceError):
            SqliteWorkItemStore.open("")
        with pytest.raises(PersistenceError):
            SqliteWorkItemStore.open(123)  # type: ignore[arg-type]
        target = tmp_path / "adir"
        target.mkdir()
        with pytest.raises(PersistenceError):
            SqliteWorkItemStore.open(str(target))
        store = SqliteWorkItemStore.open(missing)
        assert store.path.endswith("ghost.sqlite")
        store.close()
        store.close()
        with pytest.raises(PersistenceError):
            store.get_state("wi:x")

    def test_register_validation(self, tmp_path: Path) -> None:
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        with _store(tmp_path) as store:
            with pytest.raises(PersistenceError):
                store.register_item(
                    work_item_id="",
                    logical_key=item.logical_key,
                    step_id=item.step_id,
                    work_item_digest=item.semantic_digest,
                    step_semantic_digest="sha256:" + "a" * 64,
                )
            with pytest.raises(PersistenceError):
                store.register_item(
                    work_item_id=item.id,
                    logical_key=item.logical_key,
                    step_id=item.step_id,
                    work_item_digest="not-a-digest with spaces\x00",
                    step_semantic_digest="sha256:" + "a" * 64,
                )
            assert store.get_state(item.id) is None
            assert store.get_owner(item.id) is None
            assert store.get_attempts(item.id) == ()
            with pytest.raises(PersistenceError):
                store.get_registered(item.id)
            assert store.list_artifact_rows(item.id) == ()
            assert store.get_result(item.id) is None
            with pytest.raises(PersistenceError):
                store.list_items("pending")  # type: ignore[arg-type]

    def test_claim_validation(self, tmp_path: Path) -> None:
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        owner = OwnerIdentity(owner_token="t")
        with _store(tmp_path) as store:
            with pytest.raises(PersistenceError):
                store.claim(item.id, owner="nope")  # type: ignore[arg-type]
            assert store.claim("wi:ghost", owner=owner) is False
            _register(store, item)
            assert store.claim(item.id, owner=owner) is True
            assert store.claim(item.id, owner=owner) is False
            assert store.get_owner(item.id) is not None
            assert store.get_owner(item.id).owner_token == "t"

    def test_terminal_misuse(self, tmp_path: Path) -> None:
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        owner = OwnerIdentity(owner_token="t")
        bad = _result(item, status=WorkItemStatus.FAILED)
        with _store(tmp_path) as store:
            _register(store, item)
            with pytest.raises(PersistenceError):
                store.complete(item.id, result="nope")  # type: ignore[arg-type]
            with pytest.raises(PersistenceError):
                store.complete("wi:ghost", result=_result(item, status=WorkItemStatus.COMPLETED))
            with pytest.raises(PersistenceError):
                store.complete(item.id, result=bad)
            with pytest.raises(PersistenceError):
                store.complete(item.id, result=bad, environment_digest="bogus")
            with pytest.raises(PersistenceError):
                store.fail(item.id, result="nope")  # type: ignore[arg-type]
            with pytest.raises(PersistenceError):
                store.fail(item.id, result=_result(item, status=WorkItemStatus.COMPLETED))
            with pytest.raises(StateTransitionError):
                store.complete(item.id, result=_result(item, status=WorkItemStatus.COMPLETED))
            with pytest.raises(PersistenceError):
                store.cancel_item("wi:ghost")
            with pytest.raises(PersistenceError):
                store.mark_interrupted("wi:ghost")
            with pytest.raises(StateTransitionError):
                store.cancel_item(item.id)
            assert store.claim(item.id, owner=owner) is True
            store.cancel_item(item.id, reason="stop")
            assert store.get_state(item.id) is StoredWorkItemStatus.CANCELLED
            with pytest.raises(StateTransitionError):
                store.mark_interrupted(item.id)

    def test_interrupted_roundtrip(self, tmp_path: Path) -> None:
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        owner = OwnerIdentity(owner_token="t")
        with _store(tmp_path) as store:
            _register(store, item)
            assert store.claim(item.id, owner=owner) is True
            store.mark_interrupted(item.id, reason="crash")
            assert store.get_state(item.id) is StoredWorkItemStatus.INTERRUPTED
            assert store.claim(item.id, owner=owner) is True
            store.fail(item.id, result=_result(item, status=WorkItemStatus.FAILED))
            assert [a.attempt_number for a in store.get_attempts(item.id)] == [1, 2]
            assert store.claim(item.id, owner=owner) is True
            store.complete(
                item.id,
                result=_result(item, status=WorkItemStatus.COMPLETED),
                environment_digest="sha256:" + "b" * 64,
            )
            assert [a.attempt_number for a in store.get_attempts(item.id)] == [1, 2, 3]
            assert store.get_registered(item.id)["environment_digest"] == ("sha256:" + "b" * 64)

    def test_ports_validation(self, tmp_path: Path) -> None:
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        with _store(tmp_path) as store:
            with pytest.raises(PersistenceError):
                store.record_started("wi:ghost", item.step_id, item.logical_key)
            with pytest.raises(PersistenceError):
                store.record_finished("nope")  # type: ignore[arg-type]
            with pytest.raises(PersistenceError):
                store.lookup("")
            assert store.lookup("sha256:" + "0" * 64) is None
            with pytest.raises(PersistenceError):
                store.store("nope")  # type: ignore[arg-type]
            _register(store, item)
            store.record_started(item.id, item.step_id, item.logical_key)
            store.record_started(item.id, item.step_id, item.logical_key)
            with pytest.raises(PersistenceError):
                store.record_started(item.id, "other-step", item.logical_key)
            store.record_finished(_result(item, status=WorkItemStatus.FAILED))
            assert store.lookup(item.semantic_digest) is None  # FAILED never satisfies lookup
            # FAILED is retryable: re-claim succeeds and moves back to RUNNING.
            store.record_started(item.id, item.step_id, item.logical_key)
            assert store.get_state(item.id) is StoredWorkItemStatus.RUNNING
            store.record_finished(_result(item, status=WorkItemStatus.COMPLETED))
            with pytest.raises(PersistenceError):
                store.record_started(item.id, item.step_id, item.logical_key)
            found = store.lookup(item.semantic_digest)
            assert found is not None
            assert found.work_item_id == item.id
            store.store(_result(item, status=WorkItemStatus.FAILED))
            completed = _result(item, status=WorkItemStatus.COMPLETED)
            store.store(completed)  # unknown to the store: validating no-op
            assert store.get_result(item.id) is not None

    def test_corrupt_result_json(self, tmp_path: Path) -> None:
        import sqlite3

        plan = _compile(_document())
        (item,) = _items(plan, 1)
        with _store(tmp_path, name="corrupt") as store:
            _register(store, item)
            store.claim(item.id, owner=OwnerIdentity(owner_token="t"))
            connection = sqlite3.connect(store.path)
            connection.execute(
                "UPDATE attempts SET result_json = '{\"status\": 42}'" " WHERE work_item_id = ?",
                (item.id,),
            )
            connection.execute(
                "UPDATE attempts SET status = 'completed' WHERE work_item_id = ?",
                (item.id,),
            )
            connection.execute(
                "UPDATE items SET status = 'completed' WHERE work_item_id = ?",
                (item.id,),
            )
            connection.commit()
            connection.close()
            with pytest.raises(CorruptStateError):
                store.get_result(item.id)

    def test_artifact_rows_roundtrip(self, tmp_path: Path) -> None:
        from confflow.domain.artifact import ArtifactLocator as Locator

        plan = _compile(_document())
        (item,) = _items(plan, 1)
        ref = ArtifactRef(
            id="a1",
            role="native_output",
            locator=Locator.run_relative("steps/s_opt/a1.out"),
            checksum="sha256:" + "c" * 64,
            subject_structure_id="c000",
        )
        result = _result(item, status=WorkItemStatus.COMPLETED)
        import dataclasses

        result = dataclasses.replace(result, artifacts=ArtifactSet.of(ref))
        with _store(tmp_path) as store:
            _register(store, item)
            store.claim(item.id, owner=OwnerIdentity(owner_token="t"))
            store.complete(item.id, result=result)
            rows = store.list_artifact_rows(item.id)
            assert len(rows) == 1
            assert rows[0]["checksum"] == "sha256:" + "c" * 64
            assert rows[0]["locator_path"] == "steps/s_opt/a1.out"
            assert rows[0]["size_bytes"] is None


# ---------------------------------------------------------------------------
# Reuse / recovery / publication / run-state edges
# ---------------------------------------------------------------------------


class TestReuseEdges:
    """Reuse validation and verdict normalization edges."""

    def _inputs(self, **overrides: Any) -> ReuseInputs:
        from confflow.domain import FrozenDict as _Frozen

        fields: dict[str, Any] = {
            "work_item_digest": "sha256:" + "a" * 64,
            "step_semantic_digest": "sha256:" + "b" * 64,
            "environment_digest": None,
            "producer_provenance": _Frozen({}),
            "artifact_checksums": (),
        }
        fields.update(overrides)
        return ReuseInputs(**fields)

    def test_input_validation(self) -> None:
        with pytest.raises(PersistenceError):
            self._inputs(work_item_digest="")
        with pytest.raises(PersistenceError):
            self._inputs(step_semantic_digest="  ")
        with pytest.raises(PersistenceError):
            self._inputs(environment_digest="  ")
        with pytest.raises(PersistenceError):
            self._inputs(producer_provenance=[])
        with pytest.raises(PersistenceError):
            self._inputs(artifact_checksums=[""])
        assert self._inputs(artifact_checksums=["b", "a"]).artifact_checksums == ("a", "b")
        assert self._inputs(producer_provenance={"k": "v"}).producer_provenance["k"] == "v"
        with pytest.raises(PersistenceError):
            build_producer_provenance(
                adapter_version="",
                profile_version="p",
                check_versions={},
                recovery_version="r",
            )
        with pytest.raises(PersistenceError):
            build_producer_provenance(
                adapter_version="a",
                profile_version="p",
                check_versions={"c": ""},
                recovery_version="r",
            )
        provenance = build_producer_provenance(
            adapter_version="a",
            profile_version="p",
            check_versions={"c": "v"},
            recovery_version="r",
        )
        assert provenance["check_versions"] == {"c": "v"}

    def test_evaluate_type_guards(self) -> None:
        with pytest.raises(PersistenceError):
            evaluate_reuse(
                current="nope",  # type: ignore[arg-type]
                stored=None,
                stored_status=None,
            )
        with pytest.raises(PersistenceError):
            evaluate_reuse(
                current=self._inputs(),
                stored="nope",  # type: ignore[arg-type]
                stored_status=None,
            )
        decision = evaluate_reuse(current=self._inputs(), stored=None, stored_status="bogus-status")
        assert decision.decision is ReuseCode.EXECUTE_NEW
        decision = evaluate_reuse(
            current=self._inputs(),
            stored=self._inputs(),
            stored_status=StoredWorkItemStatus.RUNNING,
            owner_verdict="bogus-verdict",
        )
        assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER
        decision = evaluate_reuse(
            current=self._inputs(),
            stored=self._inputs(),
            stored_status=StoredWorkItemStatus.COMPLETED,
        )
        assert decision.decision is ReuseCode.REUSE
        assert decision.work_item_id == "unknown"

    def test_owner_verdict_matrix(self) -> None:
        current = self._inputs()
        dead = evaluate_reuse(
            current=current,
            stored=current,
            stored_status=StoredWorkItemStatus.RUNNING,
            owner_verdict=OwnerVerdict.DEFINITELY_DEAD,
            work_item_id="w",
        )
        assert dead.decision is ReuseCode.RECOVER_ABANDONED
        alive = evaluate_reuse(
            current=current,
            stored=current,
            stored_status=StoredWorkItemStatus.RUNNING,
            owner_verdict=OwnerVerdict.DEFINITELY_ALIVE,
            work_item_id="w",
        )
        assert alive.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER
        assert alive.reason.startswith("owner_alive:")


class TestRecoveryEdges:
    """Owner-identity capture and verdict edges without live processes."""

    def test_current_identity_shape(self) -> None:
        identity = owner_identity_current(owner_token="ctl")
        assert identity.pid == os.getpid()
        assert identity.claimed_wall is not None
        with pytest.raises(PersistenceError):
            owner_identity_current(owner_token="  ")

    def test_none_pid_is_uncertain(self) -> None:
        assert reconcile_owner(OwnerIdentity(owner_token="t", pid=None)) is OwnerVerdict.UNCERTAIN

    def test_psutil_absent_is_uncertain(self) -> None:
        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert reconcile_owner(ghost, psutil_module=None) is OwnerVerdict.UNCERTAIN
        assert owner_identity_current(owner_token="t", psutil_module=None).create_time is None

    def test_ghost_without_groups_is_dead(self) -> None:
        ghost = OwnerIdentity(owner_token="t", pid=2**30)
        assert reconcile_owner(ghost) is OwnerVerdict.DEFINITELY_DEAD


class TestPublicationEdges:
    """Publication loader and run-state guards on corrupt input."""

    def test_load_variants(self, tmp_path: Path) -> None:
        from confflow.persistence.publication import load_published_step_result

        run_root = str(tmp_path / "run")
        assert load_published_step_result(run_root=run_root, step_id=STEP_ID) is None
        target = Path(step_result_path(run_root, STEP_ID))
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\x00 not json")
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)
        target.write_bytes(b'{"step_id": "other"}')
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=run_root, step_id=STEP_ID)

    def test_run_state_guards(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        assert load_run_state(run_root) is None
        with pytest.raises(PersistenceError):
            save_run_state(run_root, "nope")  # type: ignore[arg-type]
        state = RunState(run_id="r")
        with pytest.raises(PersistenceError):
            ensure_step("nope", STEP_ID)  # type: ignore[arg-type]
        with pytest.raises(PersistenceError):
            transition_step(state, "ghost", RunStepStatus.RUNNING)
        with pytest.raises(PersistenceError):
            transition_step(state, STEP_ID, "running")  # type: ignore[arg-type]
        with pytest.raises(PersistenceError):
            repair_after_publish(state, STEP_ID, RunStepStatus.COMPLETED, "  ")
        assert detect_published(run_root, STEP_ID) is None
        target = Path(step_result_path(run_root, STEP_ID))
        target.parent.mkdir(parents=True)
        target.write_bytes(b"garbage")
        with pytest.raises(CorruptStateError):
            detect_published(run_root, STEP_ID)


class TestArtifactEdges:
    """Integrity and GC edges beyond the happy path."""

    def _ref(self, **overrides: Any) -> ArtifactRef:
        fields: dict[str, Any] = {
            "id": "a1",
            "role": "native_output",
            "locator": ArtifactLocator.run_relative("steps/s_opt/a1.out"),
            "checksum": None,
        }
        fields.update(overrides)
        return ArtifactRef(**fields)

    def test_verify_edges(self, tmp_path: Path) -> None:
        from confflow.persistence.artifacts import verify_artifact

        run_root = str(tmp_path / "run")
        os.makedirs(os.path.join(run_root, "steps", "s_opt"))
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(run_root=run_root, ref=self._ref(), expected_size_bytes=-1)
        # Malformed checksums are rejected by the domain constructor first;
        # persistence still guards unsupported algorithms that pass the shape.
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(
                run_root=run_root,
                ref=self._ref(checksum="rot13:" + "a" * 32),
            )
        target = Path(run_root) / "steps" / "s_opt" / "a1.out"
        target.write_bytes(b"bytes")
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(run_root=run_root, ref=self._ref(), expected_size_bytes=999)
        verify_artifact(run_root=run_root, ref=self._ref(), expected_size_bytes=5)
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(
                run_root=run_root,
                ref=self._ref(checksum="sha256:" + "0" * 64),
            )
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(
                run_root=run_root,
                ref=self._ref(checksum="rot13:" + "a" * 32),
            )

    def test_gc_edges(self, tmp_path: Path) -> None:
        from confflow.domain.retention import RetentionClass

        run_root = str(tmp_path / "run")
        directory = Path(run_root) / "steps" / "s_opt"
        directory.mkdir(parents=True)
        (directory / "gone.out").write_bytes(b"")
        temporary = ArtifactRef(
            id="t1",
            role="native_output",
            locator=ArtifactLocator.run_relative("steps/s_opt/gone.out"),
            retention=RetentionClass.TEMPORARY,
        )
        plan = plan_gc(run_root=run_root, candidates=(temporary,), consumed_ids=frozenset({"t1"}))
        assert plan.artifact_ids == ("t1",)
        forged = GCPlan(
            entries=(
                GCEntry(artifact_id="rel", reason="test", locator_path="relative/path"),
                GCEntry(artifact_id="dir", reason="test", locator_path=str(directory)),
            )
        )
        removed, failed = apply_gc(run_root=run_root, plan=forged)
        assert removed == ()
        assert set(failed) == {"rel", "dir"}


# ---------------------------------------------------------------------------
# Durable-runner seams through the real path
# ---------------------------------------------------------------------------


class _SpyExecutor(WorkItemExecutor):
    """Work-item executor recording every execution call without patching."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        self._calls_lock = threading.Lock()

    def execute(self, work_item: Any, *args: Any, **kwargs: Any) -> Any:
        with self._calls_lock:
            self.calls.append(work_item.id)
        return super().execute(work_item, *args, **kwargs)


class TestDurableRunnerSeams:
    """Abandoned, invalidated, and blocked recovery via execute_step_resumable."""

    def _run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        items: tuple[Any, ...],
        store: SqliteWorkItemStore,
        supervisor: NativeProcessSupervisor,
        owner_token: str,
        *,
        executor: WorkItemExecutor | None = None,
    ) -> Any:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = _compile(_document())
        batch = BatchStepExecutor(executor or WorkItemExecutor()).with_supervisor(supervisor)
        return batch.execute_step_resumable(
            _request(plan, items, str(tmp_path / "run")),
            store=store,
            run_root=str(tmp_path / "run"),
            owner_token=owner_token,
        )

    def test_ghost_owner_recovers_and_executes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=plan.steps[0].step_semantic_digest,
                environment_digest=None,
                producer_provenance={},
            )
            assert store.claim(item.id, owner=OwnerIdentity(owner_token="ghost", pid=2**30))
            result = self._run(
                monkeypatch, tmp_path, (item,), store, NativeProcessSupervisor(), "ctl-2"
            )
            assert result.status is StepStatus.COMPLETED
            assert [a.attempt_number for a in store.get_attempts(item.id)] == [1, 2]

    def test_live_owner_blocks_without_execution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest=item.semantic_digest,
                step_semantic_digest=plan.steps[0].step_semantic_digest,
                environment_digest=None,
                producer_provenance={},
            )
            assert store.claim(item.id, owner=owner_identity_current(owner_token="rival-live"))
            spy = _SpyExecutor()
            result = self._run(
                monkeypatch,
                tmp_path,
                (item,),
                store,
                NativeProcessSupervisor(),
                "ctl-2",
                executor=spy,
            )
            assert spy.calls == []
            assert result.status is StepStatus.FAILED
            assert result.item_results[0].error is not None
            assert result.item_results[0].error.code == "blocked_uncertain_owner"
            assert store.get_state(item.id) is StoredWorkItemStatus.RUNNING

    def test_definition_change_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.persistence.reuse import build_producer_provenance

        plan = _compile(_document())
        (item,) = _items(plan, 1)
        provenance = build_producer_provenance(
            adapter_version=get_program_adapter("orca").adapter_version,
            profile_version=PROFILES["standard"].contract_version,
            check_versions={"normal_termination": CHECKS["normal_termination"].contract_version},
            recovery_version=RECOVERIES["none"].contract_version,
        )
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            # Same provenance, different input digest: the input axis fires.
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest="sha256:" + "f" * 64,
                step_semantic_digest=plan.steps[0].step_semantic_digest,
                environment_digest=None,
                producer_provenance=dict(provenance.thaw()),
            )
            store.claim(item.id, owner=OwnerIdentity(owner_token="old"))
            store.complete(item.id, result=_result(item, status=WorkItemStatus.COMPLETED))
            spy = _SpyExecutor()
            result = self._run(
                monkeypatch,
                tmp_path,
                (item,),
                store,
                NativeProcessSupervisor(),
                "ctl-2",
                executor=spy,
            )
            assert spy.calls == []
            assert result.status is StepStatus.FAILED
            assert result.item_results[0].error is not None
            assert result.item_results[0].error.code == "invalidate_input"
            assert any(
                diagnostic.code == "publication_durability_gap" for diagnostic in result.diagnostics
            )
            assert store.get_state(item.id) is StoredWorkItemStatus.COMPLETED

    def test_preflight_reports_without_store_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = _compile(_document())
        (item,) = _items(plan, 1)
        bad = StepExecutionRequest(
            step=plan.steps[0],
            items=(item,),
            scientific=plan.steps[0].scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=None,
            profile=PROFILES["standard"],
            checks=(),
            recovery=RECOVERIES["none"],
            run_root=str(tmp_path / "run"),
        )
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            batch = BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())
            result = batch.execute_step_resumable(bad, store=store, run_root=str(tmp_path / "run"))
            assert result.status is StepStatus.FAILED
            assert store.list_items() == ()

    def test_rescoped_reuse_id_mismatch(self) -> None:
        from confflow.execution.batch import BatchStepExecutor as _Batch

        plan = _compile(_document())
        (item,) = _items(plan, 1)
        stored = _result(item, status=WorkItemStatus.COMPLETED)
        rescoped = _Batch._rescoped_reuse(item, stored)
        assert rescoped.work_item_id == item.id
        assert any(diagnostic.code == "reuse_hit" for diagnostic in rescoped.diagnostics)
        foreign = WorkItemResult(
            work_item_id="wi:s_opt:other",
            status=WorkItemStatus.COMPLETED,
            semantic_digest=item.semantic_digest,
        )
        rescoped_foreign = _Batch._rescoped_reuse(item, foreign)
        assert rescoped_foreign.work_item_id == item.id
        assert rescoped_foreign.semantic_digest == item.semantic_digest

    def test_provenance_guard_without_adapter(self, tmp_path: Path) -> None:
        from confflow.execution.batch import BatchStepExecutor as _Batch

        plan = _compile(_document())
        (item,) = _items(plan, 1)
        bad = StepExecutionRequest(
            step=plan.steps[0],
            items=(item,),
            scientific=plan.steps[0].scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=None,
            profile=None,
            checks=(),
            recovery=None,
            run_root=str(tmp_path / "run"),
        )
        with pytest.raises(PersistenceError):
            _Batch._current_provenance(bad)

    def test_contested_second_loss_blocks(self, tmp_path: Path) -> None:

        plan = _compile(_document())
        (item,) = _items(plan, 1)
        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), STEP_ID)) as store:
            _register(store, item)
            batch = BatchStepExecutor(WorkItemExecutor())
            live = owner_identity_current(owner_token="rival")
            assert store.claim(item.id, owner=live) is True
            context = WorkItemExecutor  # placeholder, never reached
            result, durable = batch._contested_claim(
                _request(plan, (item,), str(tmp_path / "run")),
                item,
                context,  # type: ignore[arg-type]
                store=store,
                owner=OwnerIdentity(owner_token="me"),
                environment_digest=None,
                provenance=FrozenDict({}),
                run_root=str(tmp_path / "run"),
                should_cancel=lambda: False,
            )
            assert durable is False
            assert isinstance(result, WorkItemResult)
            assert result.error is not None
            assert result.error.code == "blocked_uncertain_owner"


class TestThreadingHygiene:
    """Module-level lock objects exist where the design claims them."""

    def test_store_has_own_lock(self, tmp_path: Path) -> None:
        with _store(tmp_path) as store:
            assert isinstance(store._lock, type(threading.RLock()))
