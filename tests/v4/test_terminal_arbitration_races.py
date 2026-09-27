#!/usr/bin/env python3

"""Randomized adversarial races for the terminal arbitration contract.

- A5/C3: 100-iteration real-thread races (cancel vs completion claim;
  two concurrent generation writers).
- Race X: the completion claim / manifest publication / cancel intent
  pipeline attacked at different barrier positions.
- Race Y: three generation writers (G1/G2/G3) with randomized scheduling,
  200 arbitration-level iterations plus full-application triples.

Invariants asserted for every iteration: exactly one terminal winner;
run_generation, run_result, arbitration ledger, and the service aggregate
agree; a superseded writer can never regress the current generation.
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

from confflow.application.execution.errors import ExecutionServiceError
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
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence import arbitration
from confflow.persistence.contracts import PersistenceError
from confflow.persistence.generation import load_run_generation
from confflow.workflow.v4.assembly import RunInputs
from tests.v4.test_audit_regressions_r2 import WATER_XYZ, _science_native, _single_step_doc

TIMEOUT = 60.0
CANCEL_ITERATIONS = 100
GENERATION_ITERATIONS = 100
RACE_Y_ITERATIONS = 200
RACE_Y_APP_ITERATIONS = 10


def _native(root: Path) -> Path:
    return _science_native(root)


def _inputs() -> RunInputs:
    return RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)}))


def _manifest_status(run_root: Path) -> str | None:
    path = run_root / RUN_RESULT_FILENAME
    if not path.is_file():
        return None
    return json.loads(path.read_text())["status"]


class _Gate:
    """Per-iteration publication barrier shared by the patched method."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        self.active = False


_GATE = _Gate()
_ORIGINAL_PUBLISH = V4RunApplication._publish_manifest


def _gated_publish(self: Any, **kwargs: Any) -> Any:
    if _GATE.active and kwargs.get("status") == "completed" and not _GATE.reached.is_set():
        _GATE.reached.set()
        assert _GATE.release.wait(TIMEOUT)
    return _ORIGINAL_PUBLISH(self, **kwargs)


def _gated_publish_after(self: Any, **kwargs: Any) -> Any:
    result = _ORIGINAL_PUBLISH(self, **kwargs)
    if _GATE.active and kwargs.get("status") == "completed" and not _GATE.reached.is_set():
        _GATE.reached.set()
        assert _GATE.release.wait(TIMEOUT)
    return result


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


def _assert_one_winner(run_root: Path, service_state: RunState | None = None) -> str:
    generation = load_run_generation(str(run_root))
    assert generation is not None
    ledger = arbitration.load_ledger(str(run_root))
    assert ledger is not None
    assert generation.status in arbitration.TERMINAL_STATUSES, generation.status
    assert ledger.terminal_status == generation.status
    assert ledger.terminal_confirmed is True
    assert ledger.current_generation_id == generation.generation_id
    manifest_status = _manifest_status(run_root)
    if manifest_status is not None:
        assert manifest_status == generation.status
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["generation_id"] == generation.generation_id
    if service_state is not None:
        expected = {
            RunState.COMPLETED: "completed",
            RunState.CANCELLED: "cancelled",
            RunState.FAILED: "failed",
        }[service_state]
        assert generation.status == expected
    return generation.status


# ---------------------------------------------------------------------------
# A5: cancel vs completion claim, 100 real-thread iterations
# ---------------------------------------------------------------------------


def test_a5_concurrent_cancel_and_completion_claim_100_iterations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(V4RunApplication, "_publish_manifest", _gated_publish)
    rng = random.Random(20260927)
    outcomes: dict[str, int] = {}
    for index in range(CANCEL_ITERATIONS):
        case = tmp_path / f"a5-{index}"
        script = _native(case)
        service, executor, spec = _service(case, _single_step_doc(script), run_id=f"a5-{index}")
        global _GATE
        _GATE = _Gate()
        _GATE.active = True
        assert _GATE.reached.wait(TIMEOUT)

        cancel_outcome: list[str] = []

        def do_cancel(
            delay: float,
            _service: Any = service,
            _spec: Any = spec,
            _outcome: list[str] = cancel_outcome,
        ) -> None:
            time.sleep(delay)
            try:
                _service.cancel(_spec.run_id)
                _outcome.append("accepted")
            except ExecutionServiceError as error:
                _outcome.append(error.code.value)

        cancel_thread = threading.Thread(target=do_cancel, args=(rng.random() * 0.002,))
        cancel_thread.start()
        if index % 2 == 0:
            # Cancellation ordering: wait until the durable cancel intent is
            # visible, then release the publication.  The claim must lose.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                ledger = arbitration.load_ledger(str(spec.work_dir))
                if ledger is not None and ledger.cancel_intent is not None:
                    break
                time.sleep(0.001)
            _GATE.release.set()
        else:
            # Completion ordering: release first; the claim wins and the
            # cancel is rejected by the arbitration winner.
            _GATE.release.set()
        cancel_thread.join(TIMEOUT)
        executor.wait(TIMEOUT)
        _GATE.active = False

        state = service.status(spec.run_id).state
        assert state in {RunState.COMPLETED, RunState.CANCELLED}, state
        winner = _assert_one_winner(Path(spec.work_dir), state)
        assert cancel_outcome, "the cancel attempt must have completed"
        events = [
            event.type for event in service._repository.read(spec.run_id).events
        ]  # noqa: SLF001
        if winner == "cancelled":
            assert cancel_outcome == ["accepted"], cancel_outcome
            assert events[-1] == "cancelled"
            assert "completed" not in events, events
        else:
            assert cancel_outcome[0] in {"invalid_state_transition", "terminal_run"}, cancel_outcome
            assert events[-1] == "completed"
        outcomes[winner] = outcomes.get(winner, 0) + 1
    assert sum(outcomes.values()) == CANCEL_ITERATIONS
    # Both orderings must be exercised by the random schedule.
    assert outcomes.get("cancelled", 0) > 0 and outcomes.get("completed", 0) > 0, outcomes
    print(f"A5_OUTCOMES iterations={CANCEL_ITERATIONS} {outcomes}")


# ---------------------------------------------------------------------------
# C3: two concurrent generation writers, 100 iterations
# ---------------------------------------------------------------------------


def test_c3_two_concurrent_generation_writers_100_iterations(tmp_path: Path) -> None:
    rng = random.Random(777)
    outcomes: dict[str, int] = {}
    for index in range(GENERATION_ITERATIONS):
        case = tmp_path / f"c3-{index}"
        script = _native(case)
        run_root = case / "run"
        doc = _single_step_doc(script)
        reports: dict[int, Any] = {}
        errors: dict[int, BaseException] = {}

        def writer(
            slot: int,
            delay: float,
            _reports: dict[int, Any] = reports,
            _errors: dict[int, BaseException] = errors,
            _doc: dict[str, Any] = doc,
            _run_root: Path = run_root,
        ) -> None:
            time.sleep(delay)
            try:
                _reports[slot] = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(
                        workflow_document=_doc,
                        run_inputs=_inputs(),
                        run_root=str(_run_root),
                        import_sources=FrozenDict({"structures": WATER_XYZ}),
                    )
                )
            except BaseException:  # noqa: BLE001 - stale rejection is expected
                _errors[slot] = sys.exc_info()[1]

        threads = [
            threading.Thread(target=writer, args=(slot, rng.random() * 0.002)) for slot in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT)

        winner = _assert_one_winner(run_root)
        generation = load_run_generation(str(run_root))
        assert generation is not None
        # The current generation must be one of the writers; the other is
        # either superseded (completed earlier) or rejected as stale.
        assert len(reports) + len(errors) == 2
        for slot, exc in errors.items():
            # A concurrent writer loses either to supersession (stale) or to
            # the older owner's live item claim (fail-closed blocked
            # publication); both are legitimate race outcomes.
            assert isinstance(exc, (arbitration.StaleGenerationError, PersistenceError)), (
                slot,
                exc,
            )
        # Exactly one current generation owns a terminal, manifest-consistent
        # truth; the last writer to begin is the owner.
        assert winner in {"completed", "failed", "cancelled"}
        outcomes["stale_writers"] = outcomes.get("stale_writers", 0) + len(errors)
        outcomes["completed_writers"] = outcomes.get("completed_writers", 0) + len(reports)
    print(f"C3_OUTCOMES iterations={GENERATION_ITERATIONS} {outcomes}")


# ---------------------------------------------------------------------------
# Race X: barrier positions across claim / manifest / cancel
# ---------------------------------------------------------------------------


def test_race_x_cancel_before_claim_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _native(tmp_path)
    service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="race-x1")
    global _GATE
    _GATE = _Gate()
    _GATE.active = True
    monkeypatch.setattr(V4RunApplication, "_publish_manifest", _gated_publish)
    assert _GATE.reached.wait(TIMEOUT)
    service.cancel(spec.run_id)
    _GATE.release.set()
    executor.wait(TIMEOUT)
    assert service.status(spec.run_id).state is RunState.CANCELLED
    _assert_one_winner(Path(spec.work_dir), RunState.CANCELLED)
    _GATE.active = False


def test_race_x_cancel_during_manifest_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cancel cannot interleave inside the claim+manifest lock region.

    The producer's durable terminal claim is already recorded while the
    manifest write is in flight, so cancellation admission is refused
    promptly (fail closed) instead of blocking on or entering the region,
    and no misleading cancel_requested is recorded.
    """
    script = _native(tmp_path)
    service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="race-x2")
    entered = threading.Event()
    release = threading.Event()
    original = V4RunApplication._build_and_write_manifest

    def paused(self: Any, **kwargs: Any) -> Any:
        if not entered.is_set():
            entered.set()
            assert release.wait(TIMEOUT)
        return original(self, **kwargs)

    monkeypatch.setattr(V4RunApplication, "_build_and_write_manifest", paused)
    assert entered.wait(TIMEOUT)

    outcome: list[str] = []

    def do_cancel() -> None:
        try:
            service.cancel(spec.run_id)
            outcome.append("accepted")
        except ExecutionServiceError as error:
            outcome.append(error.code.value)

    cancel_thread = threading.Thread(target=do_cancel)
    cancel_thread.start()
    cancel_thread.join(TIMEOUT)
    assert not cancel_thread.is_alive(), "the refusal must not block on the publication lock"
    assert outcome and outcome[0] == "invalid_state_transition", outcome
    release.set()
    executor.wait(TIMEOUT)
    assert service.status(spec.run_id).state is RunState.COMPLETED
    _assert_one_winner(Path(spec.work_dir), RunState.COMPLETED)
    events = [event.type for event in service._repository.read(spec.run_id).events]
    assert "cancel_requested" not in events


def test_race_x_cancel_after_confirm_loses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _native(tmp_path)
    service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="race-x3")
    barrier = _Gate()
    monkeypatch.setattr(V4RunApplication, "_publish_manifest", _gated_publish_after)
    global _GATE
    _GATE = barrier
    barrier.active = True
    # "after" semantics: pause AFTER the real publication completes.
    assert barrier.reached.wait(TIMEOUT)
    with pytest.raises(ExecutionServiceError):
        service.cancel(spec.run_id)
    barrier.release.set()
    executor.wait(TIMEOUT)
    assert service.status(spec.run_id).state is RunState.COMPLETED
    _assert_one_winner(Path(spec.work_dir), RunState.COMPLETED)
    barrier.active = False


def test_race_x_stale_step_publication_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer superseded after item commit cannot publish its step result."""
    from confflow.execution.batch import BatchStepExecutor
    from confflow.persistence import detect_published

    slow = _science_native(tmp_path, delay=1.2)
    (tmp_path / "slow_orca").write_text(slow.read_text())
    (tmp_path / "slow_orca").chmod(0o755)
    fast = _science_native(tmp_path, delay=0.0)
    (tmp_path / "fast_orca").write_text(fast.read_text())
    (tmp_path / "fast_orca").chmod(0o755)

    run_root = tmp_path / "run"
    entered = threading.Event()
    release = threading.Event()
    original_assemble = BatchStepExecutor._assemble

    def paused_assemble(self: Any, request: Any, collected: Any, errors: Any) -> Any:
        if request.step.step_id == "ts" and not entered.is_set():
            entered.set()
            assert release.wait(TIMEOUT)
        return original_assemble(self, request, collected, errors)

    monkeypatch.setattr(BatchStepExecutor, "_assemble", paused_assemble)
    results: dict[str, Any] = {}

    def g1() -> None:
        try:
            results["g1"] = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                V4RunRequest(
                    workflow_document=_single_step_doc(tmp_path / "slow_orca"),
                    run_inputs=_inputs(),
                    run_root=str(run_root),
                    import_sources=FrozenDict({"structures": WATER_XYZ}),
                )
            )
        except BaseException as exc:  # noqa: BLE001
            results["g1_error"] = exc

    thread = threading.Thread(target=g1)
    thread.start()
    assert entered.wait(TIMEOUT)

    g2 = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=_single_step_doc(tmp_path / "fast_orca"),
            run_inputs=_inputs(),
            run_root=str(run_root),
            import_sources=FrozenDict({"structures": WATER_XYZ}),
        )
    )
    assert g2.status == "completed"
    release.set()
    thread.join(TIMEOUT)
    assert isinstance(results.get("g1_error"), arbitration.StaleGenerationError)
    _assert_one_winner(run_root, RunState.COMPLETED)
    manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
    published = detect_published(run_root=str(run_root), step_id="ts")
    assert manifest["steps"][0]["digest"] == published
    assert manifest["generation_id"] == g2.generation_id


# ---------------------------------------------------------------------------
# Cross-process arbitration (the production concurrency boundary)
# ---------------------------------------------------------------------------


def _run_process_script(tmp_path: Path, body: str, *, name: str) -> subprocess.Popen[bytes]:
    script = tmp_path / name
    script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, '/opt/ConfFlow')\n"
        "from confflow.persistence import arbitration\n"
        f"ROOT = Path({str(tmp_path / 'root')!r})\n" + body
    )
    return subprocess.Popen(
        [sys.executable, str(script)],
        env=dict(os.environ),  # identical env for the crashed attempt and the retry
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _wait_for(path: Path, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def test_cross_process_stale_writer_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ready = tmp_path / "ready"
    go = tmp_path / "go"
    outcome = tmp_path / "outcome"
    process = _run_process_script(
        tmp_path,
        (
            "arbitration.begin_generation(str(ROOT), generation_id='gen-A', run_id='x', "
            "definition_digest='sha256:' + 'a' * 64)\n"
            f"(Path({str(ready)!r})).write_text('ready')\n"
            f"while not Path({str(go)!r}).exists():\n"
            "    time.sleep(0.01)\n"
            "try:\n"
            "    with arbitration.terminal_publication(str(ROOT), generation_id='gen-A', "
            "requested_status='completed') as scope:\n"
            "        scope.confirm(manifest_generation_id='gen-A')\n"
            f"    (Path({str(outcome)!r})).write_text('won')\n"
            "except arbitration.StaleGenerationError:\n"
            f"    (Path({str(outcome)!r})).write_text('stale')\n"
        ),
        name="stale_writer.py",
    )
    try:
        _wait_for(ready)
        # The in-process "newer generation" becomes current while the other
        # PROCESS is between begin and publication.
        arbitration.begin_generation(
            str(root),
            generation_id="gen-B",
            run_id="x",
            definition_digest="sha256:" + "a" * 64,
        )
        with arbitration.terminal_publication(
            str(root), generation_id="gen-B", requested_status="completed"
        ) as scope:
            scope.confirm(manifest_generation_id="gen-B")
        go.write_text("go")
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
        assert outcome.read_text() == "stale"
        ledger = arbitration.load_ledger(str(root))
        generation = load_run_generation(str(root))
        assert ledger is not None and ledger.current_generation_id == "gen-B"
        assert generation is not None and generation.generation_id == "gen-B"
    finally:
        if process.poll() is None:
            process.kill()


def test_cross_process_cancel_intent_wins_over_completion_claim(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ready = tmp_path / "ready"
    go = tmp_path / "go"
    outcome = tmp_path / "outcome"
    process = _run_process_script(
        tmp_path,
        (
            "arbitration.begin_generation(str(ROOT), generation_id='gen-A', run_id='x', "
            "definition_digest='sha256:' + 'a' * 64)\n"
            f"(Path({str(ready)!r})).write_text('ready')\n"
            f"while not Path({str(go)!r}).exists():\n"
            "    time.sleep(0.01)\n"
            "with arbitration.terminal_publication(str(ROOT), generation_id='gen-A', "
            "requested_status='completed') as scope:\n"
            "    scope.confirm(manifest_generation_id='gen-A')\n"
            "    status = scope.claim.status\n"
            f"(Path({str(outcome)!r})).write_text(status)\n"
        ),
        name="cancel_intent.py",
    )
    try:
        _wait_for(ready)
        assert arbitration.record_cancel_intent(str(root), source="process-test") is None
        go.write_text("go")
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
        assert outcome.read_text() == "cancelled"
        generation = load_run_generation(str(root))
        assert generation is not None and generation.status == "cancelled"
    finally:
        if process.poll() is None:
            process.kill()


# ---------------------------------------------------------------------------
# Race Y: three writers, randomized scheduling
# ---------------------------------------------------------------------------


def test_race_y_three_writers_200_arbitration_iterations(tmp_path: Path) -> None:
    rng = random.Random(424242)
    digest = "sha256:" + "a" * 64
    totals = {"completed": 0, "stale": 0}
    for index in range(RACE_Y_ITERATIONS):
        root = tmp_path / f"y-{index}"
        root.mkdir()
        generation_ids = [f"gen-y{index}-{slot}" for slot in range(3)]
        begin_order: list[str] = []
        completed: list[str] = []
        stale: list[str] = []
        lock = threading.Lock()

        def writer(
            slot: int,
            _generation_ids: list[str] = generation_ids,
            _root: Path = root,
            _index: int = index,
            _lock: Any = lock,
            _stale: list[str] = stale,
            _begin_order: list[str] = begin_order,
            _completed: list[str] = completed,
            _rng: random.Random = rng,
        ) -> None:
            generation_id = _generation_ids[slot]
            try:
                arbitration.begin_generation(
                    str(_root),
                    generation_id=generation_id,
                    run_id=f"y-{_index}",
                    definition_digest=digest,
                )
            except arbitration.StaleGenerationError:
                with _lock:
                    _stale.append(generation_id)
                return
            with _lock:
                _begin_order.append(generation_id)
            time.sleep(_rng.random() * 0.002)
            try:
                with arbitration.terminal_publication(
                    str(_root), generation_id=generation_id, requested_status="completed"
                ) as scope:
                    time.sleep(_rng.random() * 0.002)
                    scope.confirm(manifest_generation_id=generation_id)
                with _lock:
                    _completed.append(generation_id)
            except (arbitration.StaleGenerationError, arbitration.TerminalOwnershipLostError):
                with _lock:
                    _stale.append(generation_id)

        threads = [threading.Thread(target=writer, args=(slot,)) for slot in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT)

        assert len(begin_order) == 3, (index, begin_order, stale)
        ledger = arbitration.load_ledger(str(root))
        assert ledger is not None
        generation = load_run_generation(str(root))
        assert generation is not None
        # The current owner is the LAST generation to begin (the lock
        # serializes begin_generation), and it must have published.
        assert ledger.current_generation_id == begin_order[-1], (index, ledger, begin_order)
        assert generation.generation_id == begin_order[-1]
        assert generation.status == "completed"
        assert ledger.terminal_status == "completed"
        assert ledger.terminal_confirmed is True
        assert set(ledger.superseded_generation_ids) <= set(generation_ids)
        assert completed, index
        assert begin_order[-1] in completed
        # No stale writer may have published after the winner.
        assert set(stale).isdisjoint(completed)
        totals["completed"] += len(completed)
        totals["stale"] += len(stale)
    print(f"RACE_Y_OUTCOMES iterations={RACE_Y_ITERATIONS} {totals}")


def test_race_y_three_writers_full_application(tmp_path: Path) -> None:
    rng = random.Random(31337)
    totals = {"completed": 0, "stale": 0}
    for index in range(RACE_Y_APP_ITERATIONS):
        case = tmp_path / f"yapp-{index}"
        script = _native(case)
        run_root = case / "run"
        doc = _single_step_doc(script)
        reports: list[Any] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def writer(
            delay: float,
            _doc: dict[str, Any] = doc,
            _run_root: Path = run_root,
            _lock: Any = lock,
            _reports: list[Any] = reports,
            _errors: list[BaseException] = errors,
        ) -> None:
            time.sleep(delay)
            try:
                report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(
                        workflow_document=_doc,
                        run_inputs=_inputs(),
                        run_root=str(_run_root),
                        import_sources=FrozenDict({"structures": WATER_XYZ}),
                    )
                )
                with _lock:
                    _reports.append(report)
            except BaseException:  # noqa: BLE001
                captured = sys.exc_info()[1]
                with _lock:
                    _errors.append(captured)

        threads = [threading.Thread(target=writer, args=(rng.random() * 0.003,)) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT)

        winner = _assert_one_winner(run_root)
        generation = load_run_generation(str(run_root))
        assert generation is not None
        # The last writer to begin owns the terminal truth; a writer blocked
        # by a still-live older owner publishes a failed generation instead of
        # duplicating native work (fail closed).
        assert winner in {"completed", "failed"}
        for exc in errors:
            assert isinstance(exc, (arbitration.StaleGenerationError, PersistenceError)), exc
        totals["completed"] += len(reports)
        totals["stale"] += len(errors)
    print(f"RACE_Y_APP_OUTCOMES iterations={RACE_Y_APP_ITERATIONS} {totals}")
