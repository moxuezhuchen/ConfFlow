#!/usr/bin/env python3

"""Second-round Astra independent-audit regressions (R1-R7).

These are the authoritative frozen counterexamples from the second
independent review, kept in the suite so the failures they proved can
never regress.  Attack conditions are preserved verbatim:

- R1: ambient/explicit/remote environment identity vs actual launch env.
- R2: (retired in R2.2 with the ``target`` field and transport seam.)
- R3: live cancellation reaches the running native process.
- R4: result references resolve against the published universe.
- R5: (retired in R2.2 with the tspes chain and live analysis.)
- R6: a new run generation can never leave an old terminal manifest current.
- R7: a durable worker bundle is reconciled before any retry attempt.

Run with the shared V4 conftest (vendor native installs scrubbed from
PATH); every fake native here is a test-owned script executed by an
absolute interpreter path.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import (
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import FrozenDict
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._helpers.audit_native import (
    REPO_ROOT,
    WATER_XYZ,
    _digest_over,
    _last_record,
    _launches,
    _science_native,
    _single_step_doc,
    _stored_environment_digest,
)


def _water_inputs() -> RunInputs:
    return RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)}))


def _run(
    doc: dict[str, Any],
    run_root: Path,
) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=_water_inputs(),
            run_root=str(run_root),
            import_sources=FrozenDict({"structures": WATER_XYZ}),
        )
    )


def _energy(report: Any) -> Any:
    results = [
        item.value for step in report.step_results for item in step.results if item.kind == "energy"
    ]
    return results[0] if results else None


class TestR1EnvironmentIdentity:
    """R1: the executed environment IS the hashed environment."""

    def test_explicit_env_change_invalidates_and_relaunches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit binding env -5 -> -10 -> removed: real relaunches."""
        script = _science_native(tmp_path)
        monkeypatch.delenv("SCIENCE_ENV", raising=False)
        run_root = tmp_path / "run-explicit"
        report = _run(_single_step_doc(script, env={"SCIENCE_ENV": "-5"}), run_root)
        assert report.status == "completed"
        assert _energy(report) == -5.0
        assert _launches(tmp_path) == 1

        report = _run(_single_step_doc(script, env={"SCIENCE_ENV": "-10"}), run_root)
        assert _energy(report) == -10.0
        assert _launches(tmp_path) == 2

        report = _run(_single_step_doc(script, env={}), run_root)
        assert _energy(report) == -30.0
        assert _launches(tmp_path) == 3

    def test_inherited_env_change_invalidates_and_relaunches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ambient inheritance is digested: -5 -> -10 -> removed relaunch."""
        script = _science_native(tmp_path)
        run_root = tmp_path / "run-ambient"

        monkeypatch.setenv("SCIENCE_ENV", "-5")
        report = _run(_single_step_doc(script), run_root)
        assert _energy(report) == -5.0
        assert _launches(tmp_path) == 1

        monkeypatch.setenv("SCIENCE_ENV", "-10")
        report = _run(_single_step_doc(script), run_root)
        assert _energy(report) == -10.0
        assert _launches(tmp_path) == 2

        monkeypatch.delenv("SCIENCE_ENV")
        report = _run(_single_step_doc(script), run_root)
        assert _energy(report) == -30.0
        assert _launches(tmp_path) == 3

    def test_explicit_env_wins_over_inherited_and_ambient_is_digest_inert_when_shadowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Binding wins; a shadowed ambient change does not invalidate."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-5")
        run_root = tmp_path / "run-shadow"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-40"})
        report = _run(doc, run_root)
        assert _energy(report) == -40.0
        assert _launches(tmp_path) == 1

        monkeypatch.setenv("SCIENCE_ENV", "-50")
        report = _run(doc, run_root)
        assert _energy(report) == -40.0
        assert _launches(tmp_path) == 1

    def test_actual_subprocess_env_equals_hashed_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R1 proof: the launched env mapping recomputes the stored digest."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-77")
        monkeypatch.setenv("AUDIT_R2_SENTINEL", "launch-only")
        run_root = tmp_path / "run-identity"
        doc = _single_step_doc(script, env={"BINDING_ONLY": "explicit"})
        report = _run(doc, run_root)
        assert report.status == "completed"

        received = _last_record(tmp_path)["env"]
        assert received["SCIENCE_ENV"] == "-77"
        assert received["AUDIT_R2_SENTINEL"] == "launch-only"
        assert received["BINDING_ONLY"] == "explicit"
        assert _stored_environment_digest(run_root, "optimize") == _digest_over(script, received)

    def test_label_metadata_change_does_not_invalidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Presentation-only changes must not invalidate scientific reuse."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-21")
        run_root = tmp_path / "run-label"
        doc = _single_step_doc(script)
        doc["steps"][0]["label"] = "first label"
        doc["steps"][0]["annotations"] = {"ui": {"color": "blue"}}
        report = _run(doc, run_root)
        assert _energy(report) == -21.0
        assert _launches(tmp_path) == 1

        doc2 = copy.deepcopy(doc)
        doc2["steps"][0]["label"] = "renamed label"
        doc2["steps"][0]["annotations"] = {"ui": {"color": "red"}}
        report = _run(doc2, run_root)
        assert _energy(report) == -21.0
        assert _launches(tmp_path) == 1


@pytest.fixture(autouse=True)
def _clean_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient audit variables from leaking between tests."""
    monkeypatch.delenv("SCIENCE_ENV", raising=False)
    monkeypatch.delenv("AUDIT_R2_SENTINEL", raising=False)


# R2.2 (G18): TestR2TargetSemantics and its _analysis_doc vehicle are retired
# with the ``target`` field and the R1.2 transport compat seam.


def _sleeping_native(root: Path, *, sleep_seconds: float) -> Path:
    """Fake native that proves live cancellation: started/slept/finished."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "sleep_orca"
    script.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os, sys, time
            from pathlib import Path
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.v4.fakes import fake_orca as f
            (Path({str(root / "started")!r})).write_text("started")
            time.sleep({sleep_seconds!r})
            (Path({str(root / "finished")!r})).write_text("finished")
            os.environ["FAKE_MODE"] = "ts_candidate"
            sys.exit(f.main(sys.argv))
            """))
    script.chmod(0o755)
    return script


class TestR4ResultReferenceIntegrity:
    """R4: result references resolve against the published universe.

    R2.3a (G18): the reaction-profile projector (and its synthesized
    ``reaction_profile`` payloads) is retired with ``confflow.analysis``;
    only the retained reference-universe guard (duplicate ids fail
    closed) remains.
    """

    def test_reference_index_rejects_duplicate_produced_ids(self) -> None:
        from dataclasses import replace

        from confflow.domain import ResultSet, ScientificResult
        from confflow.domain.units import Unit
        from confflow.producer.run_result import build_result_reference_index

        first = ScientificResult(kind="energy", value=-1.0, unit=Unit.HARTREE, result_id="dup")
        second = ScientificResult(kind="energy", value=-2.0, unit=Unit.HARTREE, result_id="dup")
        step = type(
            "StepStub",
            (),
            {"step_id": "s", "results": ResultSet((first, replace(second)))},
        )()
        with pytest.raises(ValueError) as error:
            build_result_reference_index((step,))
        assert "duplicate" in str(error.value)


# R2.2 (G18): TestR5TypedGrouping is retired: its end-to-end vehicle
# (the tspes chain plus a live reaction-profile analysis) is gone.


class TestR6GenerationLifecycle:
    """R6: a new generation can never leave an old manifest current."""

    # R2.2: the generation-lifecycle vehicle is the retained single
    # optimize step (the tspes chain is retired); the lifecycle
    # assertions are unchanged in shape.
    def _run_step(self, doc: dict[str, Any], run_root: Path) -> Any:
        return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=_water_inputs(),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": WATER_XYZ}),
            )
        )

    def test_old_completed_generation_never_remains_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case A: gen1 completed, gen2 fails at the step -> gen2 truth wins."""
        from confflow.persistence.generation import load_run_generation

        script = _science_native(tmp_path)
        run_root = tmp_path / "run"
        first = self._run_step(_single_step_doc(script, env={"SCIENCE_ENV": "-70"}), run_root)
        assert first.status == "completed"
        first_manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        first_generation = load_run_generation(str(run_root))
        assert first_generation is not None
        assert first_generation.status == "completed"
        assert first_manifest["generation_id"] == first_generation.generation_id

        failing = _single_step_doc(script, env={"SCIENCE_ENV": "invalid-number"})
        failed = self._run_step(failing, run_root)
        assert failed.status == "failed"

        second_manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        second_generation = load_run_generation(str(run_root))
        assert second_generation is not None
        assert second_generation.status == "failed"
        assert second_generation.generation_id != first_generation.generation_id
        assert second_manifest["status"] == "failed"
        assert second_manifest["generation_id"] == second_generation.generation_id
        assert second_generation.manifest_generation_id == second_generation.generation_id
        # A plain step failure records no failure location (nothing was
        # blocked downstream); the terminal failed truth still wins.
        assert second_generation.failure is None
        step_statuses = {step["id"]: step["status"] for step in second_manifest["steps"]}
        assert step_statuses == {"optimize": "failed"}

    def test_fresh_upstream_failure_publishes_terminal_failed_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case B: the step failed -> explicit terminal failed generation."""
        from confflow.persistence.generation import load_run_generation

        script = _science_native(tmp_path)
        run_root = tmp_path / "run"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "invalid-number"})
        failed = self._run_step(doc, run_root)
        assert failed.status == "failed"
        generation = load_run_generation(str(run_root))
        assert generation is not None and generation.status == "failed"
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "failed"
        assert manifest["generation_id"] == generation.generation_id
        assert manifest["steps"], "the failed step must be durable in the manifest"

    def test_successful_resume_is_a_new_completed_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.persistence.generation import load_run_generation

        script = _science_native(tmp_path)
        run_root = tmp_path / "run"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-70"})
        first = self._run_step(doc, run_root)
        assert first.status == "completed"
        first_launches = _launches(tmp_path)
        assert first_launches > 0
        first_generation = load_run_generation(str(run_root))
        second = self._run_step(doc, run_root)
        assert second.status == "completed"
        # A same-environment resume reuses every durable item: 0 relaunch.
        assert _launches(tmp_path) == first_launches
        second_generation = load_run_generation(str(run_root))
        assert second_generation is not None and second_generation.status == "completed"
        assert second_generation.generation_id != first_generation.generation_id
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["generation_id"] == second_generation.generation_id


class TestR3LiveCancellation:
    """R3: cancellation reaches and terminates the running native process."""

    def test_service_cancel_terminates_running_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        from confflow.application.execution.models import RunState
        from confflow.application.execution.workflow_adapter import (
            WorkflowRunSpec,
            _prepare_request,
            build_workflow_service,
            executor_identity,
        )

        monkeypatch.delenv("SCIENCE_ENV", raising=False)
        script = _sleeping_native(tmp_path, sleep_seconds=5.0)
        work = tmp_path / "work"
        doc = _single_step_doc(script, env={"SLEEP_SENTINEL": "1"})
        config = tmp_path / "config.json"
        config.write_text(json.dumps(doc))
        xyz = tmp_path / "a.xyz"
        xyz.write_text(WATER_XYZ)
        spec = WorkflowRunSpec(
            run_id="cancel-live",
            input_xyz=(str(xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        service, executor = build_workflow_service(spec, state_root=tmp_path / "state")
        service.prepare(_prepare_request(spec, executor_identity(service)))
        service.execute(spec.run_id)
        started = tmp_path / "started"
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists()

        cancel_started = time.monotonic()
        service.cancel(spec.run_id)
        executor.wait(30)
        elapsed = time.monotonic() - cancel_started

        assert service.status(spec.run_id).state is RunState.CANCELLED
        assert not (tmp_path / "finished").exists()
        assert elapsed < 4.0, f"cancel waited for the native sleep ({elapsed:.2f}s)"
        manifest = json.loads((work / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"

    def test_cli_beacon_terminates_running_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess
        import time

        monkeypatch.delenv("SCIENCE_ENV", raising=False)
        script = _sleeping_native(tmp_path, sleep_seconds=5.0)
        work = tmp_path / "work"
        doc = _single_step_doc(script)
        config = tmp_path / "config.json"
        config.write_text(json.dumps(doc))
        xyz = tmp_path / "a.xyz"
        xyz.write_text(WATER_XYZ)
        executable = Path(sys.executable).parent / "confflow"
        if not executable.exists():  # pragma: no cover - environment guard
            pytest.skip("confflow console script is not installed")
        process = subprocess.Popen(
            [str(executable), str(xyz), "-c", str(config), "-w", str(work)],
            env={**os.environ, "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        started = tmp_path / "started"
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists()
        cancel_started = time.monotonic()
        (work / "CANCEL").touch()
        exit_code = process.wait(timeout=30)
        elapsed = time.monotonic() - cancel_started

        assert exit_code != 0
        assert not (tmp_path / "finished").exists()
        assert elapsed < 4.0, f"CLI cancel waited for the native sleep ({elapsed:.2f}s)"
        manifest = json.loads((work / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"

    def test_cancel_before_first_launch_never_launches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-31")
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-31"})
        run_root = tmp_path / "run"
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=_water_inputs(),
                run_root=str(run_root),
                should_cancel=lambda: True,
            )
        )
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 0
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"
        with SqliteWorkItemStore.open(store_path(str(run_root), "optimize")) as store:
            assert store.list_items() == ()

    def test_cancel_between_items_keeps_durable_completed_item(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancel after item 1 commits: item 1 stays durable, item 2 cancelled."""
        from confflow.domain.completion import WorkItemStatus

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-32")
        two_blocks = WATER_XYZ + WATER_XYZ
        run_root = tmp_path / "run"
        stop_file = tmp_path / "stop-now"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-32"})
        # The native writes the stop marker after its first launch; the probe
        # then cancels the second item before it launches.
        script.write_text(
            script.read_text().replace(
                'count.write_text("\\n".join(lines + ["launch"]) + "\\n")',
                'count.write_text("\\n".join(lines + ["launch"]) + "\\n")\n'
                f'(Path({str(stop_file)!r})).write_text("stop")',
            )
        )
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(two_blocks)})),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": two_blocks}),
                should_cancel=lambda: stop_file.exists(),
            )
        )
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 1
        (step_result,) = report.step_results
        statuses = {item.status for item in step_result.item_results}
        assert WorkItemStatus.COMPLETED in statuses
        assert WorkItemStatus.CANCELLED in statuses
        with SqliteWorkItemStore.open(store_path(str(run_root), "optimize")) as store:
            completed = [
                item_id
                for item_id in store.list_items()
                if store.get_state(item_id).value == "completed"
            ]
        assert len(completed) == 1

    def test_cancel_after_step_result_before_manifest_is_cancelled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A durable step result is never rolled back; the run is cancelled."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-33")
        run_root = tmp_path / "run"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-33"})
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=_water_inputs(),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": WATER_XYZ}),
                should_cancel=lambda: (
                    run_root / "steps" / "optimize" / "step_result.json"
                ).exists(),
            )
        )
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 1
        assert report.step_results[0].status.value == "completed"
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"
        with SqliteWorkItemStore.open(store_path(str(run_root), "optimize")) as store:
            (item_id,) = store.list_items()
            assert store.get_state(item_id).value == "completed"
