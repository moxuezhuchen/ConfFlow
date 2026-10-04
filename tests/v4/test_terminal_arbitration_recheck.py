#!/usr/bin/env python3

"""Astra final-recheck blockers: control-cancel and step-publication arbitration.

Two confirmed races:

- P1 — the real ``confflow control cancel`` did not participate in the run
  root's generation arbitration.  The service recorded ``cancel_requested``
  first and the executor only touched the CANCEL beacon, so a completion
  that had already claimed terminal ownership still won while the API had
  reported ``ok``.  Admission now durably claims the run root's ordering
  before any service-state mutation; a terminal claim already recorded by
  the producer refuses the cancel explicitly (no misleading
  ``cancel_requested``), and the beacon stays only the worker stop signal.
- P0 — step-result publication validated generation ownership and then wrote
  outside that validation (TOCTOU).  A superseded generation could overwrite
  the current generation's durable StepResult, leaving the manifest digest
  inconsistent with the bytes on disk.  The expected-owner check and every
  generation-scoped step write now share one cross-process arbitration
  region (StepResult and run-state lifecycle), so a stale writer raises
  ``StaleGenerationError`` before replacing a byte.

The deterministic tests mirror the two Astra repros
(``/tmp/astra-terminal-recheck/control_claim.py`` and ``stale_step.py``);
the randomized tests attack the adjacent windows around the claim/manifest
and the step publication lock with 100 interleavings each.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from confflow.application.execution.errors import ErrorCode, ExecutionServiceError
from confflow.application.execution.models import RunState
from confflow.application.execution.workflow_adapter import (
    WorkflowRunSpec,
    _prepare_request,
    build_workflow_service,
    executor_identity,
)
from confflow.application.v4_run import (
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import FrozenDict
from confflow.domain.completion import CompletionPolicy, StepStatus, WorkItemStatus
from confflow.domain.result import ResultSet, ScientificResult, make_result_id
from confflow.domain.step_result import StepProvenance, StepResult
from confflow.domain.units import Unit
from confflow.domain.work_item import WorkItemResult
from confflow.execution import batch as batch_module
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence import arbitration, detect_published, publish_step_result
from confflow.persistence.generation import load_run_generation
from confflow.persistence.run_state import load_run_state
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._helpers.audit_native import WATER_XYZ, _science_native, _single_step_doc

TIMEOUT = 60.0
ITERATIONS = 100
REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROL_ENTRY = "import sys; from confflow.main import main; sys.exit(main())"


def _inputs() -> RunInputs:
    return RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)}))


def _doc(script: Path, value: int) -> dict[str, Any]:
    return _single_step_doc(script, env={"SCIENCE_ENV": str(value)})


def _run_doc(doc: dict[str, Any], run_root: Path) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=_inputs(),
            run_root=str(run_root),
            import_sources=FrozenDict({"structures": WATER_XYZ}),
        )
    )


def _service(tmp_path: Path, doc: dict[str, Any], *, run_id: str) -> tuple[Any, Any, Any]:
    work = tmp_path / "work"
    config = tmp_path / "config.json"
    config.write_text(json.dumps(doc), encoding="utf-8")
    xyz = tmp_path / "input.xyz"
    xyz.write_text(WATER_XYZ, encoding="utf-8")
    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=(str(xyz),),
        config_file=str(config),
        work_dir=str(work),
    )
    service, executor = build_workflow_service(spec, state_root=tmp_path / "state")
    service.prepare(_prepare_request(spec, executor_identity(service)))
    service.execute(run_id)
    return service, executor, spec


def _control_cancel(state_root: Path, run_id: str) -> subprocess.CompletedProcess[str]:
    """Run the real ``confflow control cancel`` in a separate process."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            CONTROL_ENTRY,
            "control",
            "cancel",
            "--state-root",
            str(state_root),
            "--run-id",
            run_id,
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=dict(os.environ),
    )


def _control_response(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, completed.stdout + completed.stderr
    payload = json.loads(lines[-1])
    assert isinstance(payload, dict)
    return payload


def _events(service: Any, run_id: str) -> list[str]:
    repository = service._repository.read(run_id)  # noqa: SLF001 - durable projection
    return [event.type for event in repository.events]


def _minimal_step_result(value: float) -> StepResult:
    result_id = make_result_id(
        step_id="ts",
        work_item_id="wi:ts:all",
        kind="energy",
        subject_structure_id="s",
        producer_digest="sha256:" + "0" * 64,
    )
    results = ResultSet(
        (
            ScientificResult(
                kind="energy",
                value=value,
                unit=Unit.HARTREE,
                subject_structure_id="s",
                source_step_id="ts",
                source_work_item_id="wi:ts:all",
                result_id=result_id,
            ),
        )
    )
    item = WorkItemResult(
        work_item_id="wi:ts:all",
        status=WorkItemStatus.COMPLETED,
        results=results,
        semantic_digest="sha256:" + "0" * 64,
    )
    return StepResult(
        step_id="ts",
        status=StepStatus.COMPLETED,
        results=results,
        item_results=(item,),
        provenance=StepProvenance(),
        summary=FrozenDict(
            {
                "total": 1,
                "completed": 1,
                "failed": 0,
                "cancelled": 0,
                "completion_mode": CompletionPolicy().mode.value,
                "status": "completed",
            }
        ),
    )


class _Barrier:
    """Pause the first completion publication at a named window."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, position: str) -> None:
        assert position in {"before-claim", "during-manifest", "after-manifest"}
        self.reached = threading.Event()
        self.release = threading.Event()
        if position == "before-claim":
            original = V4RunApplication._publish_manifest

            def wrapper(self_: Any, **kwargs: Any) -> Any:
                if kwargs.get("status") == "completed" and not self.reached.is_set():
                    self.reached.set()
                    assert self.release.wait(TIMEOUT)
                return original(self_, **kwargs)

            monkeypatch.setattr(V4RunApplication, "_publish_manifest", wrapper)
        elif position == "during-manifest":
            original_build = V4RunApplication._build_and_write_manifest

            def build_pause(self_: Any, **kwargs: Any) -> Any:
                if kwargs.get("status") == "completed" and not self.reached.is_set():
                    self.reached.set()
                    assert self.release.wait(TIMEOUT)
                return original_build(self_, **kwargs)

            monkeypatch.setattr(V4RunApplication, "_build_and_write_manifest", build_pause)
        else:
            original_build = V4RunApplication._build_and_write_manifest

            def build_wrapper(self_: Any, **kwargs: Any) -> Any:
                result = original_build(self_, **kwargs)
                if kwargs.get("status") == "completed" and not self.reached.is_set():
                    self.reached.set()
                    assert self.release.wait(TIMEOUT)
                return result

            monkeypatch.setattr(V4RunApplication, "_build_and_write_manifest", build_wrapper)


# ---------------------------------------------------------------------------
# P1: real control cancel arbitration
# ---------------------------------------------------------------------------


class TestRealControlCancel:
    def test_cancel_before_completion_claim_wins_via_real_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _science_native(tmp_path)
        barrier = _Barrier(monkeypatch, position="before-claim")
        service, executor, spec = _service(tmp_path, _doc(script, -31), run_id="ctl-before")
        assert barrier.reached.wait(TIMEOUT)

        completed = _control_cancel(tmp_path / "state", spec.run_id)
        response = _control_response(completed)
        assert completed.returncode == 0, (response, completed.stderr)
        assert response["ok"] is True
        assert response["state"] == "running"

        barrier.release.set()
        executor.wait(TIMEOUT)
        assert service.status(spec.run_id).state is RunState.CANCELLED
        run_root = Path(spec.work_dir)
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_status == "cancelled"
        generation = load_run_generation(str(run_root))
        assert generation is not None and generation.status == "cancelled"
        assert json.loads((run_root / RUN_RESULT_FILENAME).read_text())["status"] == "cancelled"
        events = _events(service, spec.run_id)
        assert "cancel_requested" in events and events[-1] == "cancelled"

    def test_cancel_between_claim_and_manifest_is_rejected_via_real_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Astra control_claim window: claim durable, manifest not yet written."""
        script = _science_native(tmp_path)
        barrier = _Barrier(monkeypatch, position="during-manifest")
        service, executor, spec = _service(tmp_path, _doc(script, -31), run_id="ctl-during")
        assert barrier.reached.wait(TIMEOUT)
        run_root = Path(spec.work_dir)
        assert not (run_root / RUN_RESULT_FILENAME).exists()
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_status == "completed"
        assert ledger.terminal_confirmed is False

        started = time.monotonic()
        completed = _control_cancel(tmp_path / "state", spec.run_id)
        elapsed = time.monotonic() - started
        response = _control_response(completed)
        assert completed.returncode != 0, response
        assert response["ok"] is False
        assert response["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value
        assert elapsed < TIMEOUT / 2, "the refusal must not wait on the publication lock"
        assert "cancel_requested" not in _events(service, spec.run_id)

        barrier.release.set()
        executor.wait(TIMEOUT)
        assert service.status(spec.run_id).state is RunState.COMPLETED
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_confirmed is True
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "completed"
        published = detect_published(run_root=str(run_root), step_id="ts")
        assert published is not None
        assert manifest["steps"][0]["digest"] == published

    def test_cancel_after_completed_run_is_rejected_via_real_cli(self, tmp_path: Path) -> None:
        """Completion fully confirmed first: the late cancel loses explicitly."""
        script = _science_native(tmp_path)
        service, executor, spec = _service(tmp_path, _doc(script, -31), run_id="ctl-completed")
        executor.wait(TIMEOUT)
        assert service.status(spec.run_id).state is RunState.COMPLETED

        completed = _control_cancel(tmp_path / "state", spec.run_id)
        response = _control_response(completed)
        assert completed.returncode != 0, response
        assert response["ok"] is False
        assert response["error"]["code"] in {
            ErrorCode.INVALID_STATE_TRANSITION.value,
            ErrorCode.TERMINAL_RUN.value,
        }, response
        assert response["error"]["retryable"] is False
        events = _events(service, spec.run_id)
        assert "cancel_requested" not in events
        assert events[-1] == "completed"
        ledger = arbitration.load_ledger(str(Path(spec.work_dir)))
        assert ledger is not None and ledger.terminal_status == "completed"

    def test_cancel_during_claim_window_is_rejected_via_real_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Claim recorded, manifest not yet durable, lock still held."""
        script = _science_native(tmp_path)
        barrier = _Barrier(monkeypatch, position="after-manifest")
        service, executor, spec = _service(tmp_path, _doc(script, -31), run_id="ctl-window")
        assert barrier.reached.wait(TIMEOUT)
        run_root = Path(spec.work_dir)
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_status == "completed"
        assert ledger.terminal_confirmed is False

        started = time.monotonic()
        completed = _control_cancel(tmp_path / "state", spec.run_id)
        elapsed = time.monotonic() - started
        response = _control_response(completed)
        assert completed.returncode != 0, response
        assert response["ok"] is False
        assert response["error"]["code"] == ErrorCode.INVALID_STATE_TRANSITION.value
        assert elapsed < TIMEOUT / 2, "the refusal must not wait on the publication lock"
        assert "cancel_requested" not in _events(service, spec.run_id)

        barrier.release.set()
        executor.wait(TIMEOUT)
        assert service.status(spec.run_id).state is RunState.COMPLETED
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_confirmed is True
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "completed"
        assert detect_published(run_root=str(run_root), step_id="ts") is not None
        assert manifest["steps"][0]["digest"] == detect_published(
            run_root=str(run_root), step_id="ts"
        )

    def test_won_cancellation_reports_success_after_self_terminalization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cancel that wins must not report terminal_run for its own winner.

        The admitted intent makes the producer terminalize the run as
        cancelled while the cancel call is still doing its service
        bookkeeping; the call must report the idempotent success (the CI
        3.10/3.11 A5 race).
        """
        from confflow.application.execution.service import ExecutionService

        script = _science_native(tmp_path)
        barrier = _Barrier(monkeypatch, position="before-claim")
        service, executor, spec = _service(tmp_path, _doc(script, -31), run_id="ctl-won-race")
        assert barrier.reached.wait(TIMEOUT)

        original_claim = ExecutionService._claim_cancel
        cancel_thread_id: list[int] = []
        outcome: dict[str, Any] = {}

        def delayed_claim(self_: Any, record: Any) -> Any:
            if threading.get_ident() == cancel_thread_id[0]:
                deadline = time.monotonic() + TIMEOUT
                while time.monotonic() < deadline:
                    if service.status(spec.run_id).state is RunState.CANCELLED:
                        break
                    time.sleep(0.001)
            return original_claim(self_, record)

        monkeypatch.setattr(ExecutionService, "_claim_cancel", delayed_claim)

        def do_cancel() -> None:
            cancel_thread_id.append(threading.get_ident())
            try:
                outcome["snapshot"] = service.cancel(spec.run_id)
            except BaseException as exc:  # noqa: BLE001 - the CI regression
                outcome["error"] = exc

        cancel_thread = threading.Thread(target=do_cancel)
        cancel_thread.start()
        # Wait for the durable cancel intent, then let the completion publish
        # its cancelled winner while the cancel call is still in bookkeeping.
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            ledger = arbitration.load_ledger(str(Path(spec.work_dir)))
            if ledger is not None and ledger.cancel_intent is not None:
                break
            time.sleep(0.001)
        barrier.release.set()
        cancel_thread.join(TIMEOUT)
        executor.wait(TIMEOUT)

        assert "error" not in outcome, outcome.get("error")
        snapshot = outcome["snapshot"]
        assert snapshot.state is RunState.CANCELLED
        assert service.status(spec.run_id).state is RunState.CANCELLED
        run_root = Path(spec.work_dir)
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_status == "cancelled"
        events = _events(service, spec.run_id)
        assert events[-1] == "cancelled"
        assert "completed" not in events

    def test_cancel_revokes_crashed_unconfirmed_claim(self, tmp_path: Path) -> None:
        """A crashed claim with no manifest never linearized: cancel wins.

        The non-blocking admission probe must repair (revoke) the stale claim
        under the lock instead of refusing the cancel from a claim that no
        live publication owns.
        """
        import dataclasses

        root = tmp_path / "run"
        root.mkdir()
        arbitration.begin_generation(
            str(root),
            generation_id="gen-crashed",
            run_id="crashed",
            definition_digest="sha256:" + "a" * 64,
        )
        ledger = arbitration.load_ledger(str(root))
        assert ledger is not None
        crashed = dataclasses.replace(
            ledger,
            terminal_status="completed",
            terminal_claimant="completion",
            terminal_confirmed=False,
        )
        arbitration._save_ledger_locked(str(root), crashed)  # noqa: SLF001 - crash seam
        assert not (root / RUN_RESULT_FILENAME).exists()

        assert arbitration.record_cancel_intent(str(root), source="operator") is None
        status = arbitration.finalize_generation(
            str(root), generation_id="gen-crashed", requested_status="completed"
        )
        assert status == "cancelled"
        record = load_run_generation(str(root))
        assert record is not None and record.status == "cancelled"

    def test_concurrent_control_cancel_and_completion_stay_consistent(self, tmp_path: Path) -> None:
        """Randomized real-CLI race: one winner, no false cancel acceptance."""
        rng = random.Random(20260927)
        accepted_count = 0
        rejected_count = 0
        for index in range(10):
            iteration = tmp_path / f"cli-race-{index}"
            iteration.mkdir()
            script = _science_native(iteration, delay=0.3)
            service, executor, spec = _service(
                iteration, _doc(script, -31), run_id=f"ctl-race-{index}"
            )
            time.sleep(rng.random() * 0.25)
            completed = _control_cancel(iteration / "state", spec.run_id)
            response = _control_response(completed)
            accepted = response.get("ok") is True
            executor.wait(TIMEOUT)
            final = service.status(spec.run_id).state
            run_root = Path(spec.work_dir)
            ledger = arbitration.load_ledger(str(run_root))
            assert ledger is not None
            events = _events(service, spec.run_id)
            if accepted:
                assert final is RunState.CANCELLED, (index, response, final)
                assert ledger.terminal_status == "cancelled", (index, response, ledger)
                assert "cancel_requested" in events
                accepted_count += 1
            else:
                assert response["error"]["code"] in {
                    ErrorCode.INVALID_STATE_TRANSITION.value,
                    ErrorCode.TERMINAL_RUN.value,
                }, (index, response)
                assert final is RunState.COMPLETED, (index, response, final)
                assert ledger.terminal_status == "completed", (index, response, ledger)
                assert "cancel_requested" not in events, (index, events)
                rejected_count += 1
        print(f"CONTROL_CLI_RACE accepted={accepted_count} rejected={rejected_count}")


class TestControlExecutorAdmission:
    def test_control_executor_refuses_lost_cancel_without_beacon(self, tmp_path: Path) -> None:
        """The control executor itself claims arbitration before the beacon."""
        from confflow.application.execution.models import CancelRequest
        from confflow.application.execution.state_root import StateRoot
        from confflow.application.execution.workflow_adapter import (
            _AgentControlExecutor,
            _publish_control_run_root,
        )

        state_root = tmp_path / "state"
        state_root.mkdir(mode=0o700)
        os.chmod(state_root, 0o700)
        root = StateRoot.resolve(state_root)
        run_id = "direct-cancel"
        run_paths = root.ensure_run_paths(run_id)
        run_root = tmp_path / "run"
        run_root.mkdir()
        _publish_control_run_root(run_paths, str(run_root))
        arbitration.begin_generation(
            str(run_root),
            generation_id="gen-direct",
            run_id=run_id,
            definition_digest="sha256:" + "a" * 64,
        )
        with arbitration.terminal_publication(
            str(run_root), generation_id="gen-direct", requested_status="completed"
        ) as scope:
            scope.confirm(manifest_generation_id="gen-direct")

        executor = _AgentControlExecutor(root)
        with pytest.raises(ExecutionServiceError) as rejected:
            executor.ensure_cancelled(
                CancelRequest(run_id=run_id, token="cancel-token", launch_token="launch", attempt=1)
            )
        assert rejected.value.code is ErrorCode.INVALID_STATE_TRANSITION
        assert not (run_paths.work / "CANCEL").exists()

    def test_corrupt_or_absent_control_run_root_pointer(self, tmp_path: Path) -> None:
        """An absent pointer is the pre-generation window; a corrupt one fails closed."""
        from confflow.application.execution.state_root import StateRoot
        from confflow.application.execution.workflow_adapter import resolve_control_run_root

        state_root = tmp_path / "state"
        state_root.mkdir(mode=0o700)
        os.chmod(state_root, 0o700)
        root = StateRoot.resolve(state_root)
        assert resolve_control_run_root(root, "never-built") is None

        run_id = "corrupt-pointer"
        run_paths = root.ensure_run_paths(run_id)
        (run_paths.work / "run_root").write_text("\n", encoding="utf-8")
        with pytest.raises(ExecutionServiceError) as rejected:
            resolve_control_run_root(root, run_id)
        assert rejected.value.code is ErrorCode.INTERNAL


# ---------------------------------------------------------------------------
# P0: generation-owned step publication
# ---------------------------------------------------------------------------


class TestStepPublicationFence:
    def test_stale_step_write_after_advisory_guard_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The frozen Astra stale_step repro: G1 resumes after G2 is current."""
        script = _science_native(tmp_path)
        run_root = tmp_path / "run"
        first_thread: list[int] = []
        ready = threading.Event()
        release = threading.Event()
        original = batch_module.publish_step_result

        def pause(**kwargs: Any) -> Any:
            if threading.get_ident() == first_thread[0]:
                ready.set()
                assert release.wait(TIMEOUT)
            return original(**kwargs)

        monkeypatch.setattr(batch_module, "publish_step_result", pause)

        results: dict[str, Any] = {}

        def g1() -> None:
            first_thread.append(threading.get_ident())
            try:
                results["g1"] = _run_doc(_doc(script, -31), run_root)
            except BaseException as exc:  # noqa: BLE001 - expected stale rejection
                results["g1_error"] = exc

        thread = threading.Thread(target=g1)
        thread.start()
        assert ready.wait(TIMEOUT)
        # G2 becomes current and publishes its own durable step result while G1
        # is paused after its advisory ownership guard, before the real write.
        g2 = _run_doc(_doc(script, -59), run_root)
        assert g2.status == "completed"
        digest_before = detect_published(run_root=str(run_root), step_id="ts")
        assert digest_before is not None
        manifest_before = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest_before["steps"][0]["digest"] == digest_before

        release.set()
        thread.join(TIMEOUT)
        assert isinstance(results.get("g1_error"), arbitration.StaleGenerationError)

        digest_after = detect_published(run_root=str(run_root), step_id="ts")
        assert digest_after == digest_before, "the stale writer must not replace a byte"
        manifest_after = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest_after["generation_id"] == g2.generation_id
        assert manifest_after["steps"][0]["digest"] == digest_after
        generation = load_run_generation(str(run_root))
        assert generation is not None and generation.generation_id == g2.generation_id

    def test_stale_run_state_write_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import confflow.application.v4_run as v4run

        script = _science_native(tmp_path)
        run_root = tmp_path / "run"
        first_thread: list[int] = []
        ready = threading.Event()
        release = threading.Event()
        original = v4run._save_run_state_fenced

        def pause(run_root_arg: str, state: Any, generation_id: str) -> None:
            if threading.get_ident() == first_thread[0] and not ready.is_set():
                ready.set()
                assert release.wait(TIMEOUT)
            return original(run_root_arg, state, generation_id)

        monkeypatch.setattr(v4run, "_save_run_state_fenced", pause)

        results: dict[str, Any] = {}

        def g1() -> None:
            first_thread.append(threading.get_ident())
            try:
                results["g1"] = _run_doc(_doc(script, -31), run_root)
            except BaseException as exc:  # noqa: BLE001 - expected stale rejection
                results["g1_error"] = exc

        thread = threading.Thread(target=g1)
        thread.start()
        assert ready.wait(TIMEOUT)
        g2 = _run_doc(_doc(script, -59), run_root)
        assert g2.status == "completed"
        state_before = load_run_state(str(run_root))
        assert state_before is not None

        release.set()
        thread.join(TIMEOUT)
        assert isinstance(results.get("g1_error"), arbitration.StaleGenerationError)
        state_after = load_run_state(str(run_root))
        assert state_after == state_before, "the stale writer must not replace run state"
        assert state_after is not None
        assert all(step.status.value == "completed" for step in state_after.steps)

    def test_cross_process_stale_step_writer_is_refused(self, tmp_path: Path) -> None:
        """Flock ownership is process-wide, not thread-local."""
        root = tmp_path / "root"
        root.mkdir()
        ready = tmp_path / "ready"
        go = tmp_path / "go"
        outcome = tmp_path / "outcome"
        child = tmp_path / "stale_step_writer.py"
        child.write_text(
            "import sys, time\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "from confflow.domain import FrozenDict\n"
            "from confflow.domain.completion import CompletionPolicy, StepStatus, WorkItemStatus\n"
            "from confflow.domain.result import ResultSet, ScientificResult, make_result_id\n"
            "from confflow.domain.step_result import StepProvenance, StepResult\n"
            "from confflow.domain.units import Unit\n"
            "from confflow.domain.work_item import WorkItemResult\n"
            "from confflow.persistence import arbitration\n"
            "from confflow.persistence.publication import publish_step_result\n"
            f"ROOT = Path({str(root)!r})\n"
            "GEN = 'gen-child'\n"
            "def build(value):\n"
            "    result_id = make_result_id(step_id='ts', work_item_id='wi:ts:all', kind='energy',\n"
            "        subject_structure_id='s', producer_digest='sha256:' + '0' * 64)\n"
            "    results = ResultSet((ScientificResult(kind='energy', value=value,\n"
            "        unit=Unit.HARTREE, subject_structure_id='s', source_step_id='ts',\n"
            "        source_work_item_id='wi:ts:all', result_id=result_id),))\n"
            "    item = WorkItemResult(work_item_id='wi:ts:all', status=WorkItemStatus.COMPLETED,\n"
            "        results=results, semantic_digest='sha256:' + '0' * 64)\n"
            "    return StepResult(step_id='ts', status=StepStatus.COMPLETED, results=results,\n"
            "        item_results=(item,), provenance=StepProvenance(),\n"
            "        summary=FrozenDict({'total': 1, 'completed': 1, 'failed': 0,\n"
            "            'cancelled': 0, 'completion_mode': CompletionPolicy().mode.value,\n"
            "            'status': 'completed'}))\n"
            "arbitration.begin_generation(str(ROOT), generation_id=GEN, run_id='x',\n"
            "    definition_digest='sha256:' + 'a' * 64)\n"
            f"(Path({str(ready)!r})).write_text('ready')\n"
            f"while not Path({str(go)!r}).exists():\n"
            "    time.sleep(0.01)\n"
            "try:\n"
            "    digest = publish_step_result(run_root=str(ROOT), step_id='ts',\n"
            "        step_result=build(-31.0), expected_generation_id=GEN)\n"
            f"    (Path({str(outcome)!r})).write_text('won:' + digest)\n"
            "except arbitration.StaleGenerationError:\n"
            f"    (Path({str(outcome)!r})).write_text('stale')\n"
        )
        process = subprocess.Popen(
            [sys.executable, str(child)],
            env=dict(os.environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + TIMEOUT
            while not ready.exists():
                assert time.monotonic() < deadline, "child never began its generation"
                time.sleep(0.01)
            arbitration.begin_generation(
                str(root),
                generation_id="gen-parent",
                run_id="x",
                definition_digest="sha256:" + "a" * 64,
            )
            parent_digest = publish_step_result(
                run_root=str(root),
                step_id="ts",
                step_result=_minimal_step_result(-59.0),
                expected_generation_id="gen-parent",
            )
            go.write_text("go")
            stdout, stderr = process.communicate(timeout=TIMEOUT)
            assert process.returncode == 0, (stdout, stderr)
            assert outcome.read_text() == "stale"
            assert detect_published(run_root=str(root), step_id="ts") == parent_digest
            generation = load_run_generation(str(root))
            assert generation is not None and generation.generation_id == "gen-parent"
        finally:
            if process.poll() is None:
                process.kill()

    def test_manifest_step_digest_matches_durable_step_result(self, tmp_path: Path) -> None:
        """Every terminal completed run keeps manifest digest == disk bytes."""
        script = _science_native(tmp_path)
        run_root = tmp_path / "run"

        def assert_consistent(generation: Any) -> None:
            manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
            published = detect_published(run_root=str(run_root), step_id="ts")
            assert published is not None
            assert manifest["generation_id"] == generation.generation_id
            assert manifest["steps"][0]["digest"] == published
            record = load_run_generation(str(run_root))
            assert record is not None and record.generation_id == generation.generation_id

        first = _run_doc(_doc(script, -31), run_root)
        assert first.status == "completed"
        assert_consistent(first)
        second = _run_doc(_doc(script, -59), run_root)
        assert second.status == "completed"
        assert_consistent(second)


# ---------------------------------------------------------------------------
# Internal red-team: randomized adjacent windows
# ---------------------------------------------------------------------------


def test_cancel_vs_completion_100_interleavings(tmp_path: Path) -> None:
    """Attack the claim/manifest windows: no false cancel acceptance."""
    rng = random.Random(424242)
    outcomes = {"cancel_wins": 0, "completion_wins": 0}
    for index in range(ITERATIONS):
        root = tmp_path / f"c-{index}"
        root.mkdir()
        generation_id = f"gen-c{index}"
        arbitration.begin_generation(
            str(root),
            generation_id=generation_id,
            run_id=f"c-{index}",
            definition_digest="sha256:" + "a" * 64,
        )
        result: dict[str, Any] = {}

        def completion(
            _root: Path = root, _generation: str = generation_id, _result: dict[str, Any] = result
        ) -> None:
            time.sleep(rng.random() * 0.003)
            with arbitration.terminal_publication(
                str(_root), generation_id=_generation, requested_status="completed"
            ) as scope:
                time.sleep(rng.random() * 0.003)
                scope.confirm(manifest_generation_id=_generation)
            _result["completion"] = "done"

        def cancel(_root: Path = root, _result: dict[str, Any] = result) -> None:
            time.sleep(rng.random() * 0.003)
            _result["cancel"] = arbitration.record_cancel_intent(str(_root), source="redteam")

        threads = [threading.Thread(target=completion), threading.Thread(target=cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT)
        assert "completion" in result and "cancel" in result, (index, result)
        winner = arbitration.current_terminal_status(str(root))
        if result["cancel"] is None:
            assert winner == "cancelled", (index, winner, result)
            outcomes["cancel_wins"] += 1
        else:
            assert result["cancel"] == "completed", (index, result)
            assert winner == "completed", (index, winner, result)
            outcomes["completion_wins"] += 1
        generation = load_run_generation(str(root))
        assert generation is not None and generation.status == winner
        ledger = arbitration.load_ledger(str(root))
        assert ledger is not None and ledger.terminal_status == winner
        assert ledger.terminal_confirmed is True
    print(f"CANCEL_REDTEAM iterations={ITERATIONS} {outcomes}")


def test_step_publication_vs_supersede_100_interleavings(tmp_path: Path) -> None:
    """Attack the step publication lock: no stale durable overwrite."""
    rng = random.Random(777777)
    outcomes = {"g1_won_before_supersede": 0, "g1_stale": 0}
    for index in range(ITERATIONS):
        root = tmp_path / f"s-{index}"
        root.mkdir()
        arbitration.begin_generation(
            str(root),
            generation_id=f"gen-s{index}-1",
            run_id=f"s-{index}",
            definition_digest="sha256:" + "a" * 64,
        )
        g2_id = f"gen-s{index}-2"
        outcome: dict[str, Any] = {}
        g2_current = threading.Event()

        def supersede(
            _root: Path = root,
            _g2: str = g2_id,
            _index: int = index,
            _current: threading.Event = g2_current,
        ) -> None:
            time.sleep(rng.random() * 0.003)
            arbitration.begin_generation(
                str(_root),
                generation_id=_g2,
                run_id=f"s-{_index}",
                definition_digest="sha256:" + "a" * 64,
            )
            _current.set()

        def writer1(
            _root: Path = root, _index: int = index, _outcome: dict[str, Any] = outcome
        ) -> None:
            time.sleep(rng.random() * 0.003)
            try:
                publish_step_result(
                    run_root=str(_root),
                    step_id="ts",
                    step_result=_minimal_step_result(-31.0),
                    expected_generation_id=f"gen-s{_index}-1",
                )
                _outcome["g1"] = "won"
            except arbitration.StaleGenerationError:
                _outcome["g1"] = "stale"

        def writer2(
            _root: Path = root,
            _g2: str = g2_id,
            _outcome: dict[str, Any] = outcome,
            _current: threading.Event = g2_current,
        ) -> None:
            assert _current.wait(TIMEOUT)
            _outcome["g2_digest"] = publish_step_result(
                run_root=str(_root),
                step_id="ts",
                step_result=_minimal_step_result(-59.0),
                expected_generation_id=_g2,
            )

        threads = [
            threading.Thread(target=supersede),
            threading.Thread(target=writer1),
            threading.Thread(target=writer2),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT)
        assert outcome.get("g1") in {"won", "stale"}, (index, outcome)
        assert "g2_digest" in outcome, (index, outcome)
        # G2 is the last generation installed: its publication is the only
        # durable truth.  A stale G1 write must never be the final bytes.
        assert detect_published(run_root=str(root), step_id="ts") == outcome["g2_digest"], index
        if outcome["g1"] == "won":
            outcomes["g1_won_before_supersede"] += 1
        else:
            outcomes["g1_stale"] += 1
    print(f"STEP_REDTEAM iterations={ITERATIONS} {outcomes}")
