#!/usr/bin/env python3

"""Terminal arbitration and generation ownership contract (Astra P1-1/P1-2).

These tests pin the formal arbitration authority introduced for the two
confirmed final-review races:

- P1-1 cancellation publication race: a durable cancel accepted before the
  completion terminal claim wins; a cancel accepted after the claim is
  rejected and completion is never reverted.
- P1-2 stale generation writer: once a newer generation owns the run root,
  an older writer's manifest/generation publication is refused
  (``StaleGenerationError``) and the current truth never regresses.

Matrices A (cancel vs completion), B (completion crash windows), C
(generation writers), D (cancel x generation), and E (service consistency)
are covered here; the randomized adversarial races live in
``test_terminal_arbitration_races.py``.

Every test drives formal seams: the V4 application, the execution service,
the control worker, and the arbitration API.
"""

from __future__ import annotations

import json
import os
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
from confflow.control_worker import run_control_worker
from confflow.domain import FrozenDict
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence import arbitration
from confflow.persistence.generation import load_run_generation
from confflow.workflow.v4.assembly import RunInputs
from tests.v4.test_audit_regressions_r2 import (
    REPO_ROOT,
    WATER_XYZ,
    _science_native,
    _single_step_doc,
)
from tests.v4.test_crossstate_hardening import (
    RUN_ID as CONTROL_RUN_ID,
)
from tests.v4.test_crossstate_hardening import (
    _control_setup,
    _crash_worker,
)

ARBITRATION_DEADLOCK_TIMEOUT = 60.0


def _native(root: Path) -> Path:
    return _science_native(root)


def _inputs() -> RunInputs:
    return RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)}))


def _run_doc(
    doc: dict[str, Any],
    run_root: Path,
    *,
    should_cancel: Any = None,
) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=_inputs(),
            run_root=str(run_root),
            import_sources=FrozenDict({"structures": WATER_XYZ}),
            should_cancel=should_cancel,
        )
    )


def _launches(root: Path) -> int:
    count = root / "science-count"
    return len(count.read_text().splitlines()) if count.exists() else 0


def _generation(run_root: Path) -> Any:
    record = load_run_generation(str(run_root))
    assert record is not None, "generation record must exist"
    return record


def _manifest(run_root: Path) -> dict[str, Any]:
    return json.loads((run_root / RUN_RESULT_FILENAME).read_text())


def _assert_terminal_consistency(run_root: Path, expected_status: str) -> None:
    """Every durable terminal surface must agree on one winner."""
    generation = _generation(run_root)
    manifest = _manifest(run_root)
    ledger = arbitration.load_ledger(str(run_root))
    assert ledger is not None
    assert generation.status == expected_status, generation.status
    assert manifest["status"] == expected_status, manifest["status"]
    assert generation.generation_id == manifest["generation_id"]
    assert ledger.current_generation_id == generation.generation_id
    assert ledger.terminal_status == expected_status
    assert ledger.terminal_confirmed is True


class _PublicationBarrier:
    """Patch ``_publish_manifest`` to pause before/after the real publication."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, position: str) -> None:
        self.position = position
        self.reached = threading.Event()
        self.release = threading.Event()
        original = V4RunApplication._publish_manifest

        def wrapper(self_: Any, **kwargs: Any) -> Any:
            if kwargs.get("status") != "completed" or self.reached.is_set():
                return original(self_, **kwargs)
            if position == "before":
                self.reached.set()
                assert self.release.wait(ARBITRATION_DEADLOCK_TIMEOUT)
                return original(self_, **kwargs)
            result = original(self_, **kwargs)
            self.reached.set()
            assert self.release.wait(ARBITRATION_DEADLOCK_TIMEOUT)
            return result

        monkeypatch.setattr(V4RunApplication, "_publish_manifest", wrapper)


def _service(
    tmp_path: Path,
    doc: dict[str, Any],
    *,
    run_id: str,
) -> tuple[Any, Any, WorkflowRunSpec]:
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


# ---------------------------------------------------------------------------
# Astra P1-1: cancellation publication race
# ---------------------------------------------------------------------------


class TestAstraCancellationPublicationRace:
    def test_cancel_accepted_before_completion_claim_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The frozen Astra repro: cancel accepted, then publication resumes."""
        script = _native(tmp_path)
        service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="astra-p1-1")
        barrier = _PublicationBarrier(monkeypatch, position="before")
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        assert not (Path(spec.work_dir) / RUN_RESULT_FILENAME).exists()

        service.cancel(spec.run_id)
        repository = service._repository.read(spec.run_id)  # noqa: SLF001
        assert repository.cancel_pending is True
        assert [event.type for event in repository.events][-1] == "cancel_requested"

        barrier.release.set()
        executor.wait(ARBITRATION_DEADLOCK_TIMEOUT)

        assert service.status(spec.run_id).state is RunState.CANCELLED
        run_root = Path(spec.work_dir)
        _assert_terminal_consistency(run_root, "cancelled")
        events = [
            event.type for event in service._repository.read(spec.run_id).events
        ]  # noqa: SLF001
        assert events == ["prepared", "queued", "running", "cancel_requested", "cancelled"]
        assert _launches(tmp_path) == 1

    def test_cancel_after_completion_claim_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Completion claimed first: the late cancel never reverts it."""
        script = _native(tmp_path)
        service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="astra-p1-1b")
        barrier = _PublicationBarrier(monkeypatch, position="after")
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)

        with pytest.raises(ExecutionServiceError):
            service.cancel(spec.run_id)
        barrier.release.set()
        executor.wait(ARBITRATION_DEADLOCK_TIMEOUT)

        assert service.status(spec.run_id).state is RunState.COMPLETED
        _assert_terminal_consistency(Path(spec.work_dir), "completed")
        events = [
            event.type for event in service._repository.read(spec.run_id).events
        ]  # noqa: SLF001
        assert events[-1] == "completed"


# ---------------------------------------------------------------------------
# Astra P1-2: stale generation writer
# ---------------------------------------------------------------------------


class TestAstraStaleGenerationWriter:
    def test_late_generation_one_publication_cannot_overwrite_generation_two(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The frozen Astra repro: G1 resumes after G2 became current."""
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        first_thread: list[int] = []
        first_ready = threading.Event()
        release = threading.Event()
        original = V4RunApplication._publish_manifest

        def paused(self: Any, **kwargs: Any) -> Any:
            if threading.get_ident() == first_thread[0] and kwargs.get("status") == "completed":
                first_ready.set()
                assert release.wait(ARBITRATION_DEADLOCK_TIMEOUT)
            return original(self, **kwargs)

        monkeypatch.setattr(V4RunApplication, "_publish_manifest", paused)

        doc = _single_step_doc(script)
        old = json.loads(json.dumps(doc))
        old["steps"][0]["execution"]["env"] = {"SCIENCE_ENV": "-23"}
        new = json.loads(json.dumps(doc))
        new["steps"][0]["execution"]["env"] = {"SCIENCE_ENV": "-47"}

        results: dict[str, Any] = {}

        def first() -> None:
            first_thread.append(threading.get_ident())
            try:
                results["g1"] = _run_doc(old, run_root)
            except Exception as exc:  # noqa: BLE001 - the expected stale rejection
                results["g1_error"] = exc

        thread = threading.Thread(target=first)
        thread.start()
        assert first_ready.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        g1_id = _generation(run_root).generation_id

        second = _run_doc(new, run_root)
        assert second.status == "completed"
        g2_id = second.generation_id
        assert g2_id != g1_id
        _assert_terminal_consistency(run_root, "completed")
        assert _generation(run_root).generation_id == g2_id
        assert [item.value for item in second.step_results[0].results if item.kind == "energy"] == [
            -47.0
        ]

        release.set()
        thread.join(ARBITRATION_DEADLOCK_TIMEOUT)
        assert isinstance(results.get("g1_error"), arbitration.StaleGenerationError)

        # G1's late publication changed nothing: current is still G2.
        _assert_terminal_consistency(run_root, "completed")
        assert _generation(run_root).generation_id == g2_id
        assert _manifest(run_root)["generation_id"] == g2_id
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and g1_id in ledger.superseded_generation_ids
        assert _launches(tmp_path) == 2


# ---------------------------------------------------------------------------
# Matrix A: cancel vs completion
# ---------------------------------------------------------------------------


class TestMatrixACancelVsCompletion:
    def test_a1_cancel_before_first_commit_wins(self, tmp_path: Path) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        report = _run_doc(_single_step_doc(script), run_root, should_cancel=lambda: True)
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 0
        _assert_terminal_consistency(run_root, "cancelled")

    def test_a2_cancel_after_step_result_before_claim_wins(self, tmp_path: Path) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        report = _run_doc(
            _single_step_doc(script),
            run_root,
            should_cancel=lambda: (run_root / "steps" / "ts" / "step_result.json").exists(),
        )
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 1
        _assert_terminal_consistency(run_root, "cancelled")

    def test_a3_cancel_during_publication_before_claim_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _native(tmp_path)
        service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="a3")
        barrier = _PublicationBarrier(monkeypatch, position="before")
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        service.cancel(spec.run_id)
        barrier.release.set()
        executor.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        assert service.status(spec.run_id).state is RunState.CANCELLED
        _assert_terminal_consistency(Path(spec.work_dir), "cancelled")

    def test_a4_completion_claim_won_then_cancel_loses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _native(tmp_path)
        service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="a4")
        barrier = _PublicationBarrier(monkeypatch, position="after")
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        with pytest.raises(ExecutionServiceError):
            service.cancel(spec.run_id)
        barrier.release.set()
        executor.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        assert service.status(spec.run_id).state is RunState.COMPLETED
        _assert_terminal_consistency(Path(spec.work_dir), "completed")

    def test_a5_cancel_between_steps_keeps_durable_completed_items(self, tmp_path: Path) -> None:
        """A cancel after item 1 commits never rolls the completed item back."""
        script = _native(tmp_path)
        two_blocks = WATER_XYZ + WATER_XYZ
        run_root = tmp_path / "run"
        stop_file = tmp_path / "stop-now"
        script.write_text(
            script.read_text().replace(
                'count.write_text("\\n".join(lines + ["launch"]) + "\\n")',
                'count.write_text("\\n".join(lines + ["launch"]) + "\\n")\n'
                f'(Path({str(stop_file)!r})).write_text("stop")',
            )
        )
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=_single_step_doc(script),
                run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(two_blocks)})),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": two_blocks}),
                should_cancel=lambda: stop_file.exists(),
            )
        )
        assert report.status == "cancelled"
        _assert_terminal_consistency(run_root, "cancelled")


# ---------------------------------------------------------------------------
# Matrix B: completion crash windows
# ---------------------------------------------------------------------------


def _crash_app(tmp_path: Path, doc: dict[str, Any], *, patch: str) -> None:
    """Run one V4 generation in a subprocess that dies at *patch*."""
    runner = tmp_path / f"crash_app_{abs(hash(patch)) % 100000}.py"
    runner.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        + patch
        + "\nfrom confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz\n"
        "from confflow.domain import FrozenDict\n"
        "from confflow.execution.process import NativeProcessSupervisor\n"
        "from confflow.workflow.v4.assembly import RunInputs\n"
        f"WATER = {WATER_XYZ!r}\n"
        f"tmp = Path({str(tmp_path)!r})\n"
        f"doc = json.loads(Path({str(tmp_path / 'document.json')!r}).read_text())\n"
        "V4RunApplication(supervisor=NativeProcessSupervisor()).run(\n"
        "    V4RunRequest(\n"
        "        workflow_document=doc,\n"
        "        run_inputs=RunInputs(structures=FrozenDict({'structures': import_xyz(WATER)})),\n"
        f"        run_root=str(tmp / 'run'),\n"
        "        import_sources=FrozenDict({'structures': WATER}),\n"
        "    )\n"
        ")\n"
    )
    crashed = subprocess.run(
        [sys.executable, str(runner)],
        env={**os.environ, "PATH": "/opt/ConfFlow/.venv/bin:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=300,
        start_new_session=True,
    )
    assert crashed.returncode == 17, crashed.stderr[-3000:]


CRASH_AFTER_CLAIM_BEFORE_MANIFEST = (
    "import confflow.application.v4_run as v4run\n"
    "v4run.V4RunApplication._build_and_write_manifest = lambda self, **kw: os._exit(17)\n"
)
CRASH_AFTER_MANIFEST_BEFORE_GENERATION = (
    "import confflow.persistence.arbitration as arbitration\n"
    "_orig = arbitration._write_public_generation_locked\n"
    "def _write(root, record):\n"
    "    if record.status != 'running':\n"
    "        os._exit(17)\n"
    "    return _orig(root, record)\n"
    "arbitration._write_public_generation_locked = _write\n"
)
CRASH_AFTER_GENERATION_BEFORE_SERVICE = (
    "import confflow.application.execution.workflow_adapter as adapter\n"
    "adapter._commit_v4_terminal = lambda **kw: os._exit(17)\n"
)


class TestMatrixBCompletionCrashWindows:
    def _document(self, tmp_path: Path) -> tuple[dict[str, Any], Path]:
        script = _native(tmp_path)
        doc = _single_step_doc(script)
        (tmp_path / "document.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc, tmp_path / "run"

    def test_b1_crash_after_claim_before_manifest_recovers_to_one_winner(
        self, tmp_path: Path
    ) -> None:
        doc, run_root = self._document(tmp_path)
        _crash_app(tmp_path, doc, patch=CRASH_AFTER_CLAIM_BEFORE_MANIFEST)
        assert _launches(tmp_path) == 1
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_confirmed is False
        assert _generation(run_root).status == "running"
        assert not (run_root / RUN_RESULT_FILENAME).exists()

        report = _run_doc(doc, run_root)
        assert report.status == "completed"
        assert _launches(tmp_path) == 1, "recovery must reuse durable items"
        _assert_terminal_consistency(run_root, "completed")

    def test_b2_crash_after_manifest_before_generation_recovers_from_manifest(
        self, tmp_path: Path
    ) -> None:
        doc, run_root = self._document(tmp_path)
        _crash_app(tmp_path, doc, patch=CRASH_AFTER_MANIFEST_BEFORE_GENERATION)
        assert _launches(tmp_path) == 1
        manifest = _manifest(run_root)
        assert manifest["status"] == "completed"
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.terminal_confirmed is False
        assert _generation(run_root).status == "running"

        report = _run_doc(doc, run_root)
        assert report.status == "completed"
        assert _launches(tmp_path) == 1
        _assert_terminal_consistency(run_root, "completed")

    def test_b3_crash_after_generation_before_service_callback_cancel_loses(
        self, tmp_path: Path
    ) -> None:
        fixture = _control_setup(tmp_path, "CF_TERM_ARB")
        os.environ["CF_TERM_ARB"] = "-42"
        try:
            _crash_worker(tmp_path, fixture, crash=CRASH_AFTER_GENERATION_BEFORE_SERVICE)
            work_dir = fixture["work_dir"]
            manifest = _manifest(work_dir)
            assert manifest["status"] == "completed"
            service = __import__(
                "confflow.application.execution.workflow_adapter",
                fromlist=["open_control_service"],
            ).open_control_service(fixture["state_root"], identity_executable=sys.executable)
            assert service.status(CONTROL_RUN_ID).state is RunState.RUNNING
            service.cancel(CONTROL_RUN_ID)

            state = run_control_worker(
                state_root=fixture["state_root"],
                run_id=CONTROL_RUN_ID,
                handoff_path=fixture["handoff_path"],
            )
            assert state is RunState.COMPLETED, "the durable terminal generation owns the winner"
            _assert_terminal_consistency(work_dir, "completed")
        finally:
            os.environ.pop("CF_TERM_ARB", None)


# ---------------------------------------------------------------------------
# Matrix C: generation writers
# ---------------------------------------------------------------------------


class TestMatrixCGenerationWriters:
    def test_c1_g1_paused_before_claim_g2_current_g1_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        barrier = _PublicationBarrier(monkeypatch, position="before")
        results: dict[str, Any] = {}

        def g1() -> None:
            try:
                results["g1"] = _run_doc(_single_step_doc(script), run_root)
            except Exception as exc:  # noqa: BLE001
                results["g1_error"] = exc

        thread = threading.Thread(target=g1)
        thread.start()
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        g2 = _run_doc(_single_step_doc(script), run_root)
        assert g2.status == "completed"
        barrier.release.set()
        thread.join(ARBITRATION_DEADLOCK_TIMEOUT)
        assert isinstance(results.get("g1_error"), arbitration.StaleGenerationError)
        _assert_terminal_consistency(run_root, "completed")
        assert _generation(run_root).generation_id == g2.generation_id

    def test_c2_live_claim_blocks_a_newer_generation_until_it_finishes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        entered = threading.Event()
        release = threading.Event()
        original = V4RunApplication._build_and_write_manifest

        def paused(self: Any, **kwargs: Any) -> Any:
            if not entered.is_set():
                entered.set()
                assert release.wait(ARBITRATION_DEADLOCK_TIMEOUT)
            return original(self, **kwargs)

        monkeypatch.setattr(V4RunApplication, "_build_and_write_manifest", paused)
        results: dict[str, Any] = {}

        def g1() -> None:
            try:
                results["g1"] = _run_doc(_single_step_doc(script), run_root)
            except Exception as exc:  # noqa: BLE001
                results["g1_error"] = exc

        first = threading.Thread(target=g1)
        first.start()
        assert entered.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        g1_id = _generation(run_root).generation_id

        def g2() -> None:
            try:
                results["g2"] = _run_doc(_single_step_doc(script), run_root)
            except Exception as exc:  # noqa: BLE001
                results["g2_error"] = exc

        second = threading.Thread(target=g2)
        second.start()
        # G2 must wait for G1's live claim (the lock), not supersede it.
        time.sleep(0.3)
        assert "g2" not in results and "g2_error" not in results
        assert _generation(run_root).generation_id == g1_id

        release.set()
        first.join(ARBITRATION_DEADLOCK_TIMEOUT)
        second.join(ARBITRATION_DEADLOCK_TIMEOUT)
        assert results.get("g1") is not None and results["g1"].status == "completed"
        assert results.get("g2") is not None and results["g2"].status == "completed"
        _assert_terminal_consistency(run_root, "completed")
        assert _generation(run_root).generation_id == results["g2"].generation_id

    def test_c4_g2_completed_g1_late_manifest_rejected(self, tmp_path: Path) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        g1 = _run_doc(_single_step_doc(script), run_root)
        g2 = _run_doc(_single_step_doc(script), run_root)
        assert g2.generation_id != g1.generation_id
        _assert_terminal_consistency(run_root, "completed")
        assert _generation(run_root).generation_id == g2.generation_id

    def test_c5_g2_failed_g1_late_completed_manifest_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.v4.test_audit_regressions_r2 import TestR6GenerationLifecycle

        chain = TestR6GenerationLifecycle._science_chain_native(tmp_path)
        run_root = tmp_path / "run"
        barrier = _PublicationBarrier(monkeypatch, position="before")
        results: dict[str, Any] = {}

        def run_chain(doc: dict[str, Any]) -> Any:
            return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                V4RunRequest(
                    workflow_document=doc,
                    run_inputs=TestR6GenerationLifecycle._tspes_inputs(),
                    run_root=str(run_root),
                    import_sources=FrozenDict({"structures": WATER_XYZ}),
                )
            )

        def g1() -> None:
            try:
                results["g1"] = run_chain(
                    TestR6GenerationLifecycle._tspes_doc(chain, sp="-70", freq="-60")
                )
            except Exception as exc:  # noqa: BLE001
                results["g1_error"] = exc

        thread = threading.Thread(target=g1)
        thread.start()
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)

        # G2 is the SAME definition failing downstream (a failed producer
        # blocks the analysis step).
        from confflow.domain.errors import DomainError

        with pytest.raises(DomainError):
            run_chain(TestR6GenerationLifecycle._tspes_doc(chain, sp="-70", freq="invalid-number"))
        g2 = _generation(run_root)
        assert g2.status == "failed"

        barrier.release.set()
        thread.join(ARBITRATION_DEADLOCK_TIMEOUT)
        assert isinstance(results.get("g1_error"), arbitration.StaleGenerationError)
        _assert_terminal_consistency(run_root, "failed")
        assert _generation(run_root).generation_id == g2.generation_id


# ---------------------------------------------------------------------------
# Matrix D: cancel x generation
# ---------------------------------------------------------------------------


class TestMatrixDCancelGeneration:
    def test_d1_g1_cancel_then_g2_current_cannot_be_cancelled_by_g1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        barrier = _PublicationBarrier(monkeypatch, position="before")
        results: dict[str, Any] = {}

        def g1() -> None:
            try:
                results["g1"] = _run_doc(_single_step_doc(script), run_root)
            except Exception as exc:  # noqa: BLE001
                results["g1_error"] = exc

        thread = threading.Thread(target=g1)
        thread.start()
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        assert arbitration.record_cancel_intent(str(run_root), source="test") is None

        g2 = _run_doc(_single_step_doc(script), run_root)
        assert g2.status == "completed"
        barrier.release.set()
        thread.join(ARBITRATION_DEADLOCK_TIMEOUT)
        assert isinstance(results.get("g1_error"), arbitration.StaleGenerationError)
        _assert_terminal_consistency(run_root, "completed")
        assert _generation(run_root).generation_id == g2.generation_id

    def test_d2_late_cancel_after_g2_completion_returns_the_winner(self, tmp_path: Path) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        g2 = _run_doc(_single_step_doc(script), run_root)
        assert arbitration.record_cancel_intent(str(run_root), source="late") == "completed"
        _assert_terminal_consistency(run_root, "completed")
        assert _generation(run_root).generation_id == g2.generation_id

    def test_d3_late_cancel_intent_does_not_touch_the_new_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _native(tmp_path)
        run_root = tmp_path / "run"
        barrier = _PublicationBarrier(monkeypatch, position="before")
        results: dict[str, Any] = {}

        def g1() -> None:
            try:
                results["g1"] = _run_doc(_single_step_doc(script), run_root)
            except Exception as exc:  # noqa: BLE001
                results["g1_error"] = exc

        thread = threading.Thread(target=g1)
        thread.start()
        assert barrier.reached.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        arbitration.record_cancel_intent(str(run_root), source="test")
        barrier.release.set()
        thread.join(ARBITRATION_DEADLOCK_TIMEOUT)

        # G2 starts fresh: the superseded G1 cancel intent must not carry over.
        g2 = _run_doc(_single_step_doc(script), run_root)
        assert g2.status == "completed"
        _assert_terminal_consistency(run_root, "completed")
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None and ledger.cancel_intent is None


# ---------------------------------------------------------------------------
# Matrix E: service consistency
# ---------------------------------------------------------------------------


class TestMatrixEServiceConsistency:
    def test_e_completed_run_is_consistent_everywhere(self, tmp_path: Path) -> None:
        script = _native(tmp_path)
        service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="e-completed")
        executor.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        assert service.status(spec.run_id).state is RunState.COMPLETED
        run_root = Path(spec.work_dir)
        _assert_terminal_consistency(run_root, "completed")
        events = [
            event.type for event in service._repository.read(spec.run_id).events
        ]  # noqa: SLF001
        assert events == ["prepared", "queued", "running", "completed"]
        with pytest.raises(ExecutionServiceError) as error:
            service.cancel(spec.run_id)
        assert error.value.code is ErrorCode.TERMINAL_RUN

    def test_e_cancelled_run_is_consistent_everywhere(self, tmp_path: Path) -> None:
        script = _native(tmp_path)
        service, executor, spec = _service(tmp_path, _single_step_doc(script), run_id="e-cancelled")
        time.sleep(0.05)
        # A cancel accepted while the native runs must stop it and win.
        service.cancel(spec.run_id)
        executor.wait(ARBITRATION_DEADLOCK_TIMEOUT)
        assert service.status(spec.run_id).state is RunState.CANCELLED
        run_root = Path(spec.work_dir)
        _assert_terminal_consistency(run_root, "cancelled")
        events = [
            event.type for event in service._repository.read(spec.run_id).events
        ]  # noqa: SLF001
        assert "cancel_requested" in events and events[-1] == "cancelled"

    def test_contract5_compare_and_set_generation_rejects_stale_owner(self, tmp_path: Path) -> None:
        from confflow.persistence.generation import RunGeneration, running_record

        run_root = tmp_path / "run"
        run_root.mkdir()
        arbitration.begin_generation(
            str(run_root),
            generation_id="gen-one",
            run_id="cas",
            definition_digest="sha256:" + "a" * 64,
        )
        # A stale writer cannot replace the current generation record.
        stale = RunGeneration(
            run_id="cas",
            generation_id="gen-zero",
            status="completed",
            definition_digest="sha256:" + "a" * 64,
        )
        with pytest.raises(arbitration.StaleGenerationError):
            arbitration.compare_and_set_generation(
                str(run_root), expected_generation_id="gen-zero", record=stale
            )
        # The current owner may update its own running record.
        arbitration.compare_and_set_generation(
            str(run_root),
            expected_generation_id="gen-one",
            record=running_record(
                run_id="cas",
                generation_id="gen-one",
                definition_digest="sha256:" + "a" * 64,
            ),
        )
        assert _generation(run_root).generation_id == "gen-one"

    def test_e_partial_generation_status_is_preserved(self, tmp_path: Path) -> None:
        """Arbitration records ``partial`` verbatim (service maps it to FAILED)."""
        run_root = tmp_path / "run"
        run_root.mkdir()
        arbitration.begin_generation(
            str(run_root),
            generation_id="gen-partial",
            run_id="e-partial",
            definition_digest="sha256:" + "a" * 64,
        )
        with arbitration.terminal_publication(
            str(run_root), generation_id="gen-partial", requested_status="partial"
        ) as scope:
            assert scope.claim.status == "partial"
            scope.confirm(manifest_generation_id=None)
        generation = _generation(run_root)
        assert generation.status == "partial"
        ledger = arbitration.load_ledger(str(run_root))
        assert ledger is not None
        assert ledger.current_generation_id == generation.generation_id
        assert ledger.terminal_status == "partial" and ledger.terminal_confirmed is True
