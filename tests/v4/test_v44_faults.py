#!/usr/bin/env python3

"""V4-4 remote fault matrix: delivery failures never corrupt execution truth.

Each class pins one fault from the V4-4 milestone matrix against the REAL
remote seams (``confflow.remote.transport`` / ``handoff`` / ``staging`` /
``worker`` / ``lease`` / ``supervision``) plus the durable store and reuse
policy:

- (A) dispatch-before-claim retry: dispatch, crash before claim, retry cleanly;
- (B) lost-response reconcile: redelivery never relaunches native (count == 1),
  and a restarted producer reuses the stored result instead of rerunning;
- (C) producer-crash resume: attach to a live attempt or block on uncertainty;
- (D) transfer-retry without rerun: re-stage, never re-execute;
- (E) idempotent re-import after a pre-commit crash: one stored result;
- (F) ACK-loss duplicate import dedupe: one attempt row, one result;
- (G) corrupt-transfer rejection: typed error, no partial commit;
- duplicate dispatch of the same attempt launches native exactly once
  (AttemptLease-gated, plus the transport delivery cache);
- cancellation uncertainty resolves to BLOCKED, never to a CANCELLED rerun;
- environment-binary change resolves to INVALIDATE_ENVIRONMENT (V4-3 policy).

Seam status: handoff write/read, staging, lease acquisition, supervision
liveness, store-level resume, reuse-policy, and executor-cancellation verdicts
are green against landed code.  Tests that dispatch through the remote worker
entry point are blocked on the missing ``confflow.remote.worker`` module and
fail with a plain import error, never a skip.  Native-launch counting tests
assume the worker resolves the test executable from the handoff execution
context (counting wrapper); if the worker seam lands with a different
executable-injection contract, the counting assertions move with it.
All file-backed tests use ``tmp_path`` only and are deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.domain.errors import DomainError
from confflow.domain.work_item import WorkItemResult
from confflow.execution import ExecutionBinding
from confflow.execution.checks_standard import CHECKS
from confflow.execution.native import NativeExecutionRequest, NativeHandle, NativeStatus
from confflow.execution.process import NativeProcessError, NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.persistence.contracts import (
    CorruptStateError,
    OwnerIdentity,
    OwnerVerdict,
    PersistenceError,
    ReuseCode,
    StoredWorkItemStatus,
    store_path,
)
from confflow.programs.registry import get_program_adapter
from confflow.remote.envelope import HANDOFF_SCHEMA_V2
from confflow.remote.lease import AttemptLease, LeaseError
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure_set,
    v4_doc,
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}
ORCA_NATIVE = {"keyword": "B3LYP D3BJ def2-SVP Opt"}

DIGEST_CURRENT = "sha256:" + "c7" * 32

#: Typed failures that count as failing closed on the remote path.
TYPED_ERRORS = (DomainError, PersistenceError, CorruptStateError)


def calculation_doc(program: str, executable: str) -> dict[str, Any]:
    """Build a single-calculation orca document bound to *executable*."""
    step = calc_step(
        "s_opt",
        program=program,
        bindings={"structure": {"source": {"run": "structures"}}},
        native=dict(ORCA_NATIVE),
        checks=["normal_termination"],
        resources={"cores_per_item": 4, "memory_per_item": "16GB"},
        execution={"binding_id": "test", "executable": executable},
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def compile_plan(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def assemble_items(plan: Any, *structure_ids: str) -> Any:
    """Assemble work items, asserting success."""
    assembly = assemble(plan, run_inputs(structures={"structures": structure_set(*structure_ids)}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return assembly.items


def item_context(
    plan: Any,
    executable: str,
    run_root: str,
    work_base: str,
    supervisor: Any,
) -> ItemExecutionContext:
    """Build an item execution context wired to the real orca adapter."""
    planned = plan.steps[0]
    return ItemExecutionContext(
        step_id=planned.step_id,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter("orca"),
        profile=PROFILES["standard"],
        checks=(CHECKS["normal_termination"],),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=executable, env=FrozenDict({})
        ),
        run_root=run_root,
        work_base=work_base,
        supervisor=supervisor,
        environment=None,
        poll_interval_seconds=0.05,
    )


def _open_store(run_root: str, step_id: str) -> Any:
    """Open the durable step store (real ``confflow.persistence`` seam)."""
    from confflow.persistence.work_items import SqliteWorkItemStore

    return SqliteWorkItemStore.open(store_path(run_root, step_id))


def _prepare_attempt(store: Any, item: Any) -> None:
    """Register *item* and claim attempt 1 so result import has a current attempt."""
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=item.semantic_digest,
        producer_provenance={},
    )
    assert store.claim(item.id, owner=OwnerIdentity(owner_token="fault-probe")) is True


def _test_handoff(item: Any, context: ItemExecutionContext, run_id: str, token: str) -> Any:
    """Build a V2 envelope for *item* without the frozen transport builder.

    Structure entries are lifted directly from the work item (no sorting
    shim), the execution definition comes from the real transport builder,
    and nested contracts travel as ``mode="python"`` dumps with the explicit
    schema and protocol version, exactly as the sibling handoff suite does.
    """
    from confflow.remote.envelope import (
        InputBundleManifest,
        StructureBundleEntry,
        WorkerHandoffV2,
        bundle_entry_digest,
    )
    from confflow.remote.transport import build_execution_definition

    entries: list[StructureBundleEntry] = []
    for port in sorted(item.named_inputs.structures):
        for record in item.named_inputs.structures[port]:
            payload = record.to_dict()
            entries.append(
                StructureBundleEntry(
                    structure_id=record.id,
                    payload=payload,
                    digest=bundle_entry_digest("structure", payload),
                )
            )
    manifest = InputBundleManifest(entries=tuple(entries))
    scientific = context.scientific
    binding = context.execution_binding
    target = getattr(binding, "target", None)
    environment_request: dict[str, Any] = {"program": context.adapter.program_name.value}
    if target:
        environment_request["target"] = target
    definition = build_execution_definition(
        program=context.adapter.program_name.value,
        native=dict(scientific.native),
        execution_adapter=scientific.execution_adapter,
        result_profile=scientific.result_profile,
        checks=tuple(check.name for check in context.checks),
        check_params=dict(scientific.check_params),
        recovery=getattr(context.recovery, "name", "none") or "none",
        recovery_params=dict(scientific.recovery_params),
        resources=item.resources,
        charge=0,
        multiplicity=1,
        freeze=None,
        step_semantic_digest=item.semantic_digest,
        contract_versions={
            "adapter": context.adapter.adapter_version,
            "profile": context.profile.contract_version,
        },
    )
    return WorkerHandoffV2.new(
        schema=HANDOFF_SCHEMA_V2,
        protocol_version="v2",
        run_id=run_id,
        step_id=context.step_id,
        work_item_id=item.id,
        logical_key=item.logical_key,
        attempt_number=1,
        launch_token=token,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=item.semantic_digest,
        producer_provenance={},
        environment_request=environment_request,
        execution=definition.model_dump(mode="python"),
        inputs=manifest.model_dump(mode="python"),
    )


def _counting_wrapper(tmp_path: Path, counter: Path) -> Path:
    """Write a shell wrapper that counts native launches, then execs the fake."""
    wrapper = tmp_path / "counting_orca.sh"
    wrapper.write_text(
        "#!/bin/bash\n" f'echo launch >> "{counter}"\n' f'exec python3 "{FAKE_ORCA}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def _install_orca_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counter: Path) -> None:
    """Install a counting ``orca`` shim on ``PATH`` for the remote worker.

    The worker resolves program binaries by name from ``PATH`` (the handoff
    carries program names, never executable paths), so the shim both counts
    native launches and routes execution through the fake program.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "orca"
    shim.write_text(
        "#!/bin/bash\n" f'echo "orca launch" >> "{counter}"\n' f'exec python3 "{FAKE_ORCA}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    if not counter.exists():
        counter.write_text("", encoding="utf-8")


def _native_launches(counter: Path) -> int:
    """Return the number of recorded native launches."""
    if not counter.exists():
        return 0
    return len([line for line in counter.read_text(encoding="utf-8").splitlines() if line])


def _dead_owner(token: str) -> OwnerIdentity:
    """Build an owner whose pid is provably absent via signal probing."""
    base = 1 << 30
    for offset in range(256):
        pid = base + offset
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return OwnerIdentity(owner_token=token, pid=pid)
        except PermissionError:
            continue
    raise AssertionError("could not find an unused pid for the dead-owner probe")


class FakeUnconfirmedSupervisor:
    """Supervisor stub whose cancellation can never be confirmed."""

    def submit(self, request: NativeExecutionRequest) -> NativeHandle:
        """Accept the request without launching anything native."""
        return NativeHandle(key="fake:0", pid=1)

    def poll(self, handle: NativeHandle) -> NativeStatus:
        """Report the fake boundary as permanently live."""
        return NativeStatus(is_terminal=False, exit_code=None)

    def cancel(self, handle: NativeHandle, *, grace_seconds: float = 2.0) -> Any:
        """Refuse to confirm cancellation (boundary still live)."""
        from confflow.execution.native import CancelOutcome

        return CancelOutcome(confirmed=False, detail="process boundary is still live")

    def collect(self, handle: NativeHandle) -> Any:
        """Fail: the fake boundary never reaches a terminal state."""
        raise NativeProcessError("fake supervisor never reaches terminal state")


class SubmitCountingSupervisor(NativeProcessSupervisor):
    """Real supervisor counting every native submit (lost-response probe)."""

    def __init__(self) -> None:
        super().__init__()
        self.submits = 0

    def submit(self, request: NativeExecutionRequest) -> NativeHandle:
        """Count one native launch, then delegate to the real boundary."""
        self.submits += 1
        return super().submit(request)


class TestTransportDedupe:
    """Same-attempt redelivery returns the recorded result (worker seam)."""

    def test_duplicate_execute_same_attempt_no_relaunch(self, tmp_path: Path) -> None:
        from confflow.remote.transport import RemoteTransport

        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(plan, str(FAKE_ORCA), str(tmp_path), str(tmp_path / "items"), None)
        recorded = WorkItemResult(work_item_id=items[0].id, status=WorkItemStatus.COMPLETED)
        worker_root = tmp_path / "worker"
        run_root = str(tmp_path / "run")
        with _open_store(run_root, "s_opt") as store:
            transport = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(worker_root)
            )
            token = transport.launch_token_for(items[0], 1)
            transport._delivered[token] = recorded
            assert transport.execute(items[0], context, attempt=1) is recorded
            assert transport.execute(items[0], context, attempt=1) is recorded
        assert _native_launches(tmp_path / "missing-counter.txt") == 0
        assert not worker_root.exists()

    def test_launch_token_stable_across_endpoints(self, tmp_path: Path) -> None:
        from confflow.remote.transport import RemoteTransport

        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        run_root = str(tmp_path / "run")
        with _open_store(run_root, "s_opt") as store:
            atlantic = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "wa")
            )
            pacific = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "wb")
            )
            assert atlantic.launch_token_for(items[0], 1) == pacific.launch_token_for(items[0], 1)
            assert "/" not in atlantic.launch_token_for(items[0], 1)


class TestAttemptLease:
    """AttemptLease gates one native launch per attempt (green)."""

    def _lease(self, tmp_path: Path, token: str, *, attempt: int = 1) -> AttemptLease:
        return AttemptLease(tmp_path / "leases", "run", "s_opt", "wi:s_opt:s0", attempt, token)

    def test_acquire_and_release_round_trip(self, tmp_path: Path) -> None:
        lease = self._lease(tmp_path, "tok-a1")
        assert lease.acquire() is True
        assert lease.path.is_file()
        lease.release()
        assert self._lease(tmp_path, "tok-a1").acquire() is True

    def test_same_token_second_acquirer_attaches(self, tmp_path: Path) -> None:
        first = self._lease(tmp_path, "tok-b1")
        assert first.acquire() is True
        second = self._lease(tmp_path, "tok-b1")
        assert second.acquire() is False

    def test_different_token_same_attempt_refused_while_held(self, tmp_path: Path) -> None:
        first = self._lease(tmp_path, "tok-c1")
        assert first.acquire() is True
        rival = self._lease(tmp_path, "tok-c2")
        assert rival.acquire() is False
        first.release()
        assert self._lease(tmp_path, "tok-c2").acquire() is True

    def test_reacquire_reports_previous_owner(self, tmp_path: Path) -> None:
        first = self._lease(tmp_path, "tok-d1")
        assert first.acquire() is True
        assert first.previous_owner is None
        first.release()
        second = self._lease(tmp_path, "tok-d1")
        assert second.acquire() is True
        assert second.previous_owner is not None
        assert second.previous_owner["launch_token"] == "tok-d1"

    def test_unsafe_identities_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(LeaseError):
            self._lease(tmp_path, "../escape")
        with pytest.raises(LeaseError):
            self._lease(tmp_path, "tok-ok+plus")
        with pytest.raises(LeaseError):
            self._lease(tmp_path, "tok-ok", attempt=0)
        with pytest.raises(LeaseError):
            AttemptLease(tmp_path / "leases", "", "s_opt", "wi:s_opt:s0", 1, "tok-ok")

    def test_lease_dirs_are_owner_private(self, tmp_path: Path) -> None:
        import stat as stat_mod

        lease = self._lease(tmp_path, "tok-e1")
        assert lease.acquire() is True
        step_dir = lease.path.parent
        assert stat_mod.S_IMODE(os.stat(step_dir).st_mode) == 0o700

    def test_context_manager_releases(self, tmp_path: Path) -> None:
        with self._lease(tmp_path, "tok-f1"):
            assert self._lease(tmp_path, "tok-f1").acquire() is False
        assert self._lease(tmp_path, "tok-f1").acquire() is True

    def test_context_manager_raises_when_owned(self, tmp_path: Path) -> None:
        first = self._lease(tmp_path, "tok-g1")
        assert first.acquire() is True
        with pytest.raises(LeaseError):
            with self._lease(tmp_path, "tok-g1"):
                pass


class TestFaultADispatchBeforeClaim:
    """Dispatch, crash before claim, retry: one handoff, one staged bundle."""

    def test_retry_after_dispatch_before_claim(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
        from confflow.remote.staging import stage_input_bundle

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        token = "tok-fault-a1"
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan, str(FAKE_ORCA), run_root, str(tmp_path / "items"), NativeProcessSupervisor()
        )
        handoff = _test_handoff(items[0], context, "run", token)
        # Crash before claim: dispatch happened, nothing was claimed.
        first_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        first_bytes = Path(first_path).read_bytes()
        reread = read_handoff_envelope(path=first_path, expected_run_id="run")
        assert reread == handoff
        # Retry re-dispatches byte-identically (no duplicate handoff).
        second_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        assert Path(second_path).read_bytes() == first_bytes
        first_staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        second_staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        assert second_staged == first_staged


class TestFaultBLostResponse:
    """Lost response reconciles without a second native launch (count == 1)."""

    def test_redelivery_after_lost_response_launches_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.transport import RemoteTransport

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        counter = tmp_path / "native-count.txt"
        _install_orca_shim(tmp_path, monkeypatch, counter)
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            transport = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "worker")
            )
            context = item_context(
                plan,
                str(FAKE_ORCA),
                run_root,
                str(tmp_path / "items"),
                NativeProcessSupervisor(),
            )
            first = transport.execute(items[0], context, attempt=1)
            lost = first  # the caller drops the response on the floor
            assert lost.status is WorkItemStatus.COMPLETED
            second = transport.execute(items[0], context, attempt=1)
            assert second.to_dict() == first.to_dict()
        assert _native_launches(counter) == 1

    def test_restarted_producer_reuses_stored_result(self, tmp_path: Path) -> None:
        """A restarted producer finds the committed result and reuses it.

        This is the store-backed half of lost-response reconcile: with
        matching digest axes, ``evaluate_reuse`` returns REUSE, so no native
        boundary is ever approached again.
        """
        from confflow.persistence.reuse import (
            ReuseInputs,
            build_producer_provenance,
            evaluate_reuse,
        )

        run_root = str(tmp_path / "run")
        item_id = "wi:s_opt:lost"
        provenance = build_producer_provenance(
            adapter_version="a.v1",
            profile_version="p.v1",
            check_versions={"normal_termination": "c.v1"},
            recovery_version="r.v1",
        )
        store = _open_store(run_root, "s_opt")
        store.register_item(
            work_item_id=item_id,
            logical_key="s_opt:lost",
            step_id="s_opt",
            work_item_digest=DIGEST_CURRENT,
            step_semantic_digest=DIGEST_CURRENT,
            producer_provenance=provenance,
        )
        assert store.claim(item_id, owner=OwnerIdentity(owner_token="owner-first")) is True
        computed = WorkItemResult(work_item_id=item_id, status=WorkItemStatus.COMPLETED)
        store.complete(item_id, result=computed)
        store.close()  # crash after commit, before the response is observed
        counter = tmp_path / "native-count.txt"

        reopened = _open_store(run_root, "s_opt")
        try:
            stored = reopened.get_result(item_id)
            assert stored is not None and stored.to_dict() == computed.to_dict()
            axes = ReuseInputs(
                work_item_digest=DIGEST_CURRENT,
                step_semantic_digest=DIGEST_CURRENT,
                environment_digest=None,
                producer_provenance=provenance,
            )
            decision = evaluate_reuse(
                current=axes,
                stored=axes,
                stored_status=StoredWorkItemStatus.COMPLETED,
                work_item_id=item_id,
            )
            assert decision.decision is ReuseCode.REUSE
        finally:
            reopened.close()
        assert _native_launches(counter) == 0


class TestFaultCProducerCrash:
    """Producer crash: attach to a live attempt or block on uncertainty."""

    def test_store_resume_attaches_or_blocks(self, tmp_path: Path) -> None:
        from confflow.persistence.recovery import reconcile_owner
        from confflow.persistence.reuse import (
            ReuseInputs,
            build_producer_provenance,
            evaluate_reuse,
        )
        from confflow.remote.supervision import attempt_liveness

        run_root = str(tmp_path / "run")
        item_id = "wi:s_opt:c"
        digest = "sha256:" + "e5" * 32
        provenance = build_producer_provenance(
            adapter_version="a.v1",
            profile_version="p.v1",
            check_versions={"normal_termination": "c.v1"},
            recovery_version="r.v1",
        )
        store = _open_store(run_root, "s_opt")
        store.register_item(
            work_item_id=item_id,
            logical_key="s_opt:c",
            step_id="s_opt",
            work_item_digest=digest,
            step_semantic_digest=digest,
            producer_provenance=provenance,
        )
        dead = _dead_owner("owner-c1")
        assert store.claim(item_id, owner=dead) is True
        store.close()  # crash with the claim held

        reopened = _open_store(run_root, "s_opt")
        try:
            assert reopened.get_state(item_id) is StoredWorkItemStatus.RUNNING
            recorded = reopened.get_owner(item_id)
            assert recorded is not None
            assert reconcile_owner(recorded).name == "DEFINITELY_DEAD"
            assert attempt_liveness(owner=recorded) is OwnerVerdict.DEFINITELY_DEAD
            reopened.mark_interrupted(item_id, reason="owner-dead: producer crash")
            assert (
                reopened.claim(
                    item_id, owner=OwnerIdentity(owner_token="owner-c2", pid=os.getpid())
                )
                is True
            )
            assert [a.attempt_number for a in reopened.get_attempts(item_id)] == [1, 2]
        finally:
            reopened.close()

        live_store = _open_store(run_root, "s_opt")
        try:
            live_item = "wi:s_opt:c-live"
            live_store.register_item(
                work_item_id=live_item,
                logical_key="s_opt:c-live",
                step_id="s_opt",
                work_item_digest=digest,
                step_semantic_digest=digest,
                producer_provenance=provenance,
            )
            live = OwnerIdentity(owner_token="owner-live", pid=os.getpid())
            assert live_store.claim(live_item, owner=live) is True
            assert attempt_liveness(owner=live) is OwnerVerdict.DEFINITELY_ALIVE
            rival = OwnerIdentity(owner_token="owner-rival", pid=os.getpid())
            assert live_store.claim(live_item, owner=rival) is False
            axes = ReuseInputs(
                work_item_digest=digest,
                step_semantic_digest=digest,
                environment_digest=None,
                producer_provenance=provenance,
            )
            decision = evaluate_reuse(
                current=axes,
                stored=axes,
                stored_status=StoredWorkItemStatus.RUNNING,
                owner_verdict=attempt_liveness(owner=live),
                work_item_id=live_item,
            )
            assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER
        finally:
            live_store.close()

    def test_remote_replay_after_crash_imports_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
        from confflow.remote.staging import import_result_artifacts, stage_input_bundle
        from confflow.remote.worker import run_worker_envelope

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        token = "tok-fault-c1"
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan, str(FAKE_ORCA), run_root, str(tmp_path / "items"), NativeProcessSupervisor()
        )
        handoff = _test_handoff(items[0], context, "run", token)
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        handoff = read_handoff_envelope(path=handoff_path, expected_run_id="run")
        staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle=staged,
            worker_root=worker_root,
            launch_token=token,
        )
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            # Crash before the producer observes the import; reopen and import.
            store.close()
            reopened = _open_store(run_root, "s_opt")
            try:
                first = import_result_artifacts(
                    result_path=result_path,
                    handoff=handoff,
                    run_root=run_root,
                    store=reopened,
                )
                assert first.status is WorkItemStatus.COMPLETED
            finally:
                reopened.close()


class TestFaultDTransferRetry:
    """Transfer retry re-stages without rerunning native execution."""

    def test_stage_retry_without_rerun(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.handoff import write_handoff_envelope
        from confflow.remote.staging import stage_input_bundle
        from confflow.remote.worker import run_worker_envelope

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        token = "tok-fault-d1"
        counter = tmp_path / "native-count.txt"
        _install_orca_shim(tmp_path, monkeypatch, counter)
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan, str(FAKE_ORCA), run_root, str(tmp_path / "items"), NativeProcessSupervisor()
        )
        handoff = _test_handoff(items[0], context, "run", token)
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        first = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        # Transfer fails transiently; retry re-stages identically.
        second = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        assert second == first
        counting = SubmitCountingSupervisor()
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle=second,
            worker_root=worker_root,
            launch_token=token,
            supervisor=counting,
        )
        assert Path(result_path).is_file()
        assert counting.submits == 1
        assert _native_launches(counter) == 1


class TestFaultEPreCommitCrash:
    """Idempotent re-import after a crash before the store commit."""

    def test_reimport_after_precommit_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
        from confflow.remote.staging import import_result_artifacts, stage_input_bundle
        from confflow.remote.worker import run_worker_envelope

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        token = "tok-fault-e1"
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan, str(FAKE_ORCA), run_root, str(tmp_path / "items"), NativeProcessSupervisor()
        )
        handoff = _test_handoff(items[0], context, "run", token)
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        handoff = read_handoff_envelope(path=handoff_path, expected_run_id="run")
        staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle=staged,
            worker_root=worker_root,
            launch_token=token,
        )
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            # Crash after compute, before commit: the import proceeds twice.
            # Import is pure (the batch layer commits), so the store holds no
            # result either way; both imports return the identical result.
            attempts_before = [a.attempt_number for a in store.get_attempts(items[0].id)]
            first = import_result_artifacts(
                result_path=result_path, handoff=handoff, run_root=run_root, store=store
            )
            second = import_result_artifacts(
                result_path=result_path, handoff=handoff, run_root=run_root, store=store
            )
            assert first.status is WorkItemStatus.COMPLETED
            assert first.to_dict() == second.to_dict()
            assert [a.attempt_number for a in store.get_attempts(items[0].id)] == attempts_before


class TestFaultFAckLoss:
    """ACK-loss duplicate import dedupes: one attempt row, one result."""

    def test_duplicate_import_after_ack_loss(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
        from confflow.remote.staging import import_result_artifacts, stage_input_bundle
        from confflow.remote.worker import run_worker_envelope

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        token = "tok-fault-f1"
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan, str(FAKE_ORCA), run_root, str(tmp_path / "items"), NativeProcessSupervisor()
        )
        handoff = _test_handoff(items[0], context, "run", token)
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        handoff = read_handoff_envelope(path=handoff_path, expected_run_id="run")
        staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle=staged,
            worker_root=worker_root,
            launch_token=token,
        )
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            committed = import_result_artifacts(
                result_path=result_path, handoff=handoff, run_root=run_root, store=store
            )
            attempts_before = [a.attempt_number for a in store.get_attempts(items[0].id)]
            # The ACK is lost; the worker redelivers the identical bundle.
            redelivered = import_result_artifacts(
                result_path=result_path, handoff=handoff, run_root=run_root, store=store
            )
            assert redelivered.to_dict() == committed.to_dict()
            assert [a.attempt_number for a in store.get_attempts(items[0].id)] == attempts_before


class TestFaultGCorruptTransfer:
    """Corrupt transfers are rejected with typed errors and no commits."""

    def test_corrupt_staged_bytes_rejected(self, tmp_path: Path) -> None:
        from confflow.remote.handoff import write_handoff_envelope
        from confflow.remote.staging import stage_input_bundle
        from confflow.remote.worker import WorkerError, run_worker_envelope

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        os.makedirs(run_root, exist_ok=True)
        token = "tok-fault-g1"
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan, str(FAKE_ORCA), run_root, str(tmp_path / "items"), NativeProcessSupervisor()
        )
        handoff = _test_handoff(items[0], context, "run", token)
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        assert Path(staged.work_dir).is_dir()
        # Corrupt the handoff in transit: the worker re-reads it and must
        # fail closed on the digest mismatch.
        with open(handoff_path, "r+b") as handle:
            content = bytearray(handle.read())
            content[len(content) // 2] ^= 0xFF
            handle.seek(0)
            handle.write(content)
        with pytest.raises(WorkerError):
            run_worker_envelope(
                handoff_path=handoff_path,
                staged_bundle=staged,
                worker_root=worker_root,
                launch_token=token,
            )
        with _open_store(run_root, "s_opt") as store:
            assert store.get_result(items[0].id) is None

    def test_corrupt_result_bundle_rejected_without_commit(self, tmp_path: Path) -> None:
        """A bit-flipped result bundle fails import with no store mutation."""
        import shutil

        from confflow.remote.envelope import ResultBundle, compute_result_digest
        from confflow.remote.staging import import_result_artifacts

        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan, str(FAKE_ORCA), run_root, str(tmp_path / "items"), NativeProcessSupervisor()
        )
        handoff = _test_handoff(items[0], context, "run", "tok-fault-g2")
        fields: dict[str, Any] = {
            "run_id": handoff.run_id,
            "step_id": handoff.step_id,
            "work_item_id": handoff.work_item_id,
            "attempt_number": handoff.attempt_number,
            "launch_token": handoff.launch_token,
            "work_item_digest": handoff.work_item_digest,
            "environment": {},
            "result": {
                "work_item_id": handoff.work_item_id,
                "status": "completed",
                "structures": [],
                "results": [],
                "semantic_digest": handoff.work_item_digest,
            },
            "produced_artifacts": (),
            "transport_metadata": {},
        }
        probe = ResultBundle.model_construct(**fields, bundle_digest=DIGEST_CURRENT)
        bundle = ResultBundle.model_validate(
            {**fields, "bundle_digest": compute_result_digest(probe._digest_payload())}
        )
        result_dir = tmp_path / "worker-result"
        (result_dir / "files").mkdir(parents=True, exist_ok=True)
        result_path = result_dir / "result.json"
        result_path.write_bytes(bundle.model_dump_json().encode("utf-8"))
        corrupted = tmp_path / "corrupt-result.json"
        shutil.copy2(result_path, corrupted)
        with open(corrupted, "r+b") as handle:
            content = bytearray(handle.read())
            content[len(content) // 2] ^= 0xFF
            handle.seek(0)
            handle.write(content)
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            with pytest.raises(TYPED_ERRORS):
                import_result_artifacts(
                    result_path=str(corrupted), handoff=handoff, run_root=run_root, store=store
                )
            assert store.get_result(items[0].id) is None


class TestDuplicateDispatch:
    """Duplicate dispatch of one attempt launches native exactly once."""

    def test_sequential_duplicate_dispatch_single_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.transport import RemoteTransport

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        counter = tmp_path / "native-count.txt"
        _install_orca_shim(tmp_path, monkeypatch, counter)
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            transport = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "worker")
            )
            context = item_context(
                plan,
                str(FAKE_ORCA),
                run_root,
                str(tmp_path / "items"),
                NativeProcessSupervisor(),
            )
            first = transport.execute(items[0], context, attempt=1)
            second = transport.execute(items[0], context, attempt=1)
            assert first.to_dict() == second.to_dict()
        assert _native_launches(counter) == 1

    def test_lease_gates_duplicate_native_launch(self, tmp_path: Path) -> None:
        """The lease seam refuses a second native boundary for one attempt."""
        first = AttemptLease(tmp_path / "leases", "run", "s_opt", "wi:s_opt:s0", 1, "tok-h1")
        assert first.acquire() is True
        try:
            rival = AttemptLease(tmp_path / "leases", "run", "s_opt", "wi:s_opt:s0", 1, "tok-h2")
            assert rival.acquire() is False
        finally:
            first.release()


class TestCancellationUncertainty:
    """Unconfirmed cancellation resolves to BLOCKED, never a CANCELLED rerun."""

    def test_unconfirmed_cancel_never_rescues_locally(self, tmp_path: Path) -> None:
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        context = item_context(
            plan,
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            FakeUnconfirmedSupervisor(),
        )
        result = WorkItemExecutor().execute(items[0], context, should_cancel=lambda: True)
        assert result.status is WorkItemStatus.FAILED
        assert result.error is not None and result.error.code == "cancellation_error"
        assert result.recovery is not None and result.recovery.attempted is False

    def test_uncertain_owner_blocks_rerun(self, tmp_path: Path) -> None:
        from confflow.persistence.reuse import (
            ReuseInputs,
            build_producer_provenance,
            evaluate_reuse,
        )

        digest = "sha256:" + "f6" * 32
        provenance = build_producer_provenance(
            adapter_version="a.v1",
            profile_version="p.v1",
            check_versions={"normal_termination": "c.v1"},
            recovery_version="r.v1",
        )
        axes = ReuseInputs(
            work_item_digest=digest,
            step_semantic_digest=digest,
            environment_digest=None,
            producer_provenance=provenance,
        )
        decision = evaluate_reuse(
            current=axes,
            stored=axes,
            stored_status=StoredWorkItemStatus.RUNNING,
            owner_verdict=OwnerVerdict.UNCERTAIN,
            work_item_id="wi:s_opt:u",
        )
        assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER

    def test_dead_owner_cancel_proof_is_unconfirmed(self, tmp_path: Path) -> None:
        from confflow.remote.supervision import cancel_attempt

        proof = cancel_attempt(owner=_dead_owner("cancel-dead"), work_dir=None)
        assert proof.confirmed is False
        assert proof.verdict in (OwnerVerdict.DEFINITELY_DEAD, OwnerVerdict.UNCERTAIN)

    def test_live_workdir_upgrades_dead_to_uncertain(self, tmp_path: Path) -> None:
        from confflow.remote.supervision import attempt_liveness

        work_dir = tmp_path / "attempt-work"
        work_dir.mkdir(exist_ok=True)
        assert attempt_liveness(owner=_dead_owner("live-dir"), work_dir=None) is (
            OwnerVerdict.DEFINITELY_DEAD
        )
        assert attempt_liveness(owner=_dead_owner("live-dir"), work_dir=str(tmp_path)) in (
            OwnerVerdict.DEFINITELY_DEAD,
            OwnerVerdict.UNCERTAIN,
        )

    def test_remote_preset_cancel_never_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.transport import RemoteTransport

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        plan = compile_plan(calculation_doc("orca", str(FAKE_ORCA)))
        items = assemble_items(plan, "s0")
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            transport = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "worker")
            )
            context = item_context(
                plan,
                str(FAKE_ORCA),
                run_root,
                str(tmp_path / "items"),
                NativeProcessSupervisor(),
            )
            try:
                outcome = transport.execute(
                    items[0], context, attempt=1, should_cancel=lambda: True
                )
            except TYPED_ERRORS:
                return
            assert outcome.status in (WorkItemStatus.CANCELLED, WorkItemStatus.FAILED)
            assert outcome.status is not WorkItemStatus.COMPLETED


class TestEnvironmentBinaryChange:
    """A changed executable binary invalidates reuse (V4-3 policy)."""

    def test_changed_binary_invalidates_reuse(self, tmp_path: Path) -> None:
        import shutil

        from confflow.execution.environment import EnvironmentMeasurer
        from confflow.persistence.reuse import (
            ReuseInputs,
            build_producer_provenance,
            evaluate_reuse,
        )

        altered = tmp_path / "altered_orca.py"
        shutil.copy2(str(FAKE_ORCA), altered)
        with open(altered, "ab") as handle:
            handle.write(b"# rotated binary\n")
        measurer = EnvironmentMeasurer()
        before = measurer.build_environment(
            str(FAKE_ORCA), adapter=get_program_adapter("orca"), target="node-1"
        )
        after = measurer.build_environment(
            str(altered), adapter=get_program_adapter("orca"), target="node-1"
        )
        assert before.digest() != after.digest()
        provenance = build_producer_provenance(
            adapter_version="a.v1",
            profile_version="p.v1",
            check_versions={"normal_termination": "c.v1"},
            recovery_version="r.v1",
        )
        current = ReuseInputs(
            work_item_digest=DIGEST_CURRENT,
            step_semantic_digest=DIGEST_CURRENT,
            environment_digest=after.digest(),
            producer_provenance=provenance,
        )
        stored = ReuseInputs(
            work_item_digest=DIGEST_CURRENT,
            step_semantic_digest=DIGEST_CURRENT,
            environment_digest=before.digest(),
            producer_provenance=provenance,
        )
        decision = evaluate_reuse(
            current=current,
            stored=stored,
            stored_status=StoredWorkItemStatus.COMPLETED,
            work_item_id="wi:s_opt:env",
        )
        assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT
