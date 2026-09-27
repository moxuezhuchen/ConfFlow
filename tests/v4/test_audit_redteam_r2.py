#!/usr/bin/env python3

"""Internal red-team for the second-audit repair round (fresh attacks).

These attacks are deliberately DIFFERENT from the development regression
inputs: a new environment variable name, new target typos, a different
cancellation moment, new dangling/stale reference combinations, new
SP/frequency numbers, and a different remote crash point (after the
worker bundle import, before the producer commit).  If any of these ever
fails, the corresponding contract is not actually closed.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import textwrap
import time
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
from confflow.domain.errors import DomainError
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.remote.transport import RemoteTransport
from confflow.workflow.v4.assembly import RunInputs
from tests.v4.test_audit_regressions_r2 import (
    REPO_ROOT,
    WATER_XYZ,
    TestR6GenerationLifecycle,
    _digest_over,
    _last_record,
    _launches,
    _science_native,
    _single_step_doc,
    _stored_environment_digest,
)

RED_TEAM_ENV = "CF_R2_REDTEAM_SCIENCE"


def _run_redteam_step(doc: dict[str, Any], run_root: Path, transport: Any = None) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})),
            run_root=str(run_root),
            import_sources=FrozenDict({"structures": WATER_XYZ}),
            transport=transport,
        )
    )


def _redteam_native(root: Path) -> Path:
    """Fake native keyed on the NEW red-team variable name."""
    script = _science_native(root)
    script.write_text(
        script.read_text().replace("SCIENCE_ENV", RED_TEAM_ENV),
    )
    return script


class TestRedTeamEnvironment:
    def test_new_variable_name_ambient_and_explicit(self, tmp_path: Path) -> None:
        script = _redteam_native(tmp_path)
        run_root = tmp_path / "run"
        os.environ[RED_TEAM_ENV] = "-12.5"
        try:
            report = _run_redteam_step(_single_step_doc(script), run_root)
            assert report.step_results[0].results[0].value == -12.5
            os.environ[RED_TEAM_ENV] = "-22.5"
            report = _run_redteam_step(_single_step_doc(script), run_root)
            assert report.step_results[0].results[0].value == -22.5
            assert _launches(tmp_path) == 2
            # Explicit binding shadows the ambient value and its changes.
            doc = _single_step_doc(script, env={RED_TEAM_ENV: "-99.5"})
            report = _run_redteam_step(doc, tmp_path / "explicit")
            assert report.step_results[0].results[0].value == -99.5
            os.environ[RED_TEAM_ENV] = "-1.5"
            report = _run_redteam_step(doc, tmp_path / "explicit")
            assert report.step_results[0].results[0].value == -99.5
            assert _launches(tmp_path) == 3
            received = _last_record(tmp_path)["env"]
            assert received[RED_TEAM_ENV] == "-99.5"
            assert _stored_environment_digest(tmp_path / "explicit", "ts") == _digest_over(
                script, received
            )
        finally:
            os.environ.pop(RED_TEAM_ENV, None)


class TestRedTeamTarget:
    def test_new_unknown_target_fails_closed_on_every_transport(self, tmp_path: Path) -> None:
        from confflow.execution.work_item_executor import WorkItemExecutor
        from confflow.remote.transport import LocalTransport

        script = _redteam_native(tmp_path)
        for label, transport in (
            ("absent", None),
            ("local", LocalTransport(WorkItemExecutor())),
        ):
            doc = _single_step_doc(script)
            doc["steps"][0]["execution"]["target"] = "cluster-r2-ghost"
            with pytest.raises(DomainError):
                _run_redteam_step(doc, tmp_path / label, transport)
        run_root = tmp_path / "remote"
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            unnamed = RemoteTransport(
                run_root=str(run_root),
                store=store,
                worker_root=str(tmp_path / "worker"),
                target_name=None,
            )
            doc = _single_step_doc(script)
            doc["steps"][0]["execution"]["target"] = "cluster-r2-ghost"
            with pytest.raises(DomainError):
                V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(
                        workflow_document=doc,
                        run_inputs=RunInputs(
                            structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})
                        ),
                        run_root=str(run_root),
                        transport=unnamed,
                    )
                )
        assert _launches(tmp_path) == 0

    def test_bad_target_on_a_later_step_launches_nothing(self, tmp_path: Path) -> None:
        """A typo on step 2 must not let step 1 run first."""
        script = _redteam_native(tmp_path)
        doc = copy.deepcopy(TestR6GenerationLifecycle._tspes_doc(script, sp="-70", freq="-60"))
        # Make the *last* step (analysis) carry the bad target.
        doc["steps"][-1]["execution"] = {"target": "cluster-r2-ghost"}
        with pytest.raises(DomainError):
            V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                V4RunRequest(
                    workflow_document=doc,
                    run_inputs=RunInputs(
                        structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})
                    ),
                    run_root=str(tmp_path / "run"),
                    import_sources=FrozenDict({"structures": WATER_XYZ}),
                )
            )
        assert not (tmp_path / "chain-count").exists()


class TestRedTeamCancellation:
    def test_cancel_during_first_native_of_two_items(self, tmp_path: Path) -> None:
        """A different cancel moment: during the first native, not before."""
        script = _science_native(tmp_path, delay=3.0)
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
        started = time.monotonic()
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=_single_step_doc(script),
                run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(two_blocks)})),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": two_blocks}),
                should_cancel=lambda: stop_file.exists(),
            )
        )
        elapsed = time.monotonic() - started
        assert report.status == "cancelled"
        # The native sleeps 3s; the cancel must have terminated it early.
        assert elapsed < 2.5, f"cancel waited for the sleeping native ({elapsed:.2f}s)"
        assert _launches(tmp_path) == 1
        (step_result,) = report.step_results
        statuses = [item.status.value for item in step_result.item_results]
        assert statuses.count("cancelled") >= 1
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"


class TestRedTeamReferences:
    def test_cross_generation_citation_is_refused(self, tmp_path: Path) -> None:
        """A citation that only existed in a prior generation must not resolve."""
        from dataclasses import replace

        from confflow.domain import ResultSet
        from confflow.producer.run_result import (
            build_result_reference_index,
            project_analysis_groups,
        )

        script = TestR6GenerationLifecycle._science_chain_native(tmp_path)
        doc = TestR6GenerationLifecycle._tspes_doc(script, sp="-70", freq="-60")
        run_root = tmp_path / "run"
        first = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=TestR6GenerationLifecycle._tspes_inputs(),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": WATER_XYZ}),
            )
        )
        assert first.status == "completed"
        old_ids = {
            item.result_id for step in first.step_results for item in step.results if item.result_id
        }
        assert old_ids
        # A fresh generation's step set (the same step, no results) must not
        # resolve any prior-generation citation.
        step = next(
            item
            for item in first.step_results
            if any(result.kind == "reaction_profile" for result in item.results)
        )
        empty_step = replace(step, results=ResultSet())
        index = build_result_reference_index((empty_step,))
        profile = next(item for item in step.results if item.kind == "reaction_profile")
        value = dict(profile.value)
        value["source_result_ids"] = [sorted(old_ids)[0]]
        changed = replace(profile, value=FrozenDict(value))
        with pytest.raises(ValueError) as error:
            project_analysis_groups(
                (replace(step, results=ResultSet((changed,))),), references=index
            )
        assert "unresolved" in str(error.value)

    def test_duplicate_produced_and_input_citation_combo(self) -> None:
        from dataclasses import replace

        from confflow.domain import ResultSet, ScientificResult
        from confflow.domain.units import Unit
        from confflow.producer.run_result import build_result_reference_index

        shared_id = "same-id"
        produced = ScientificResult(
            kind="energy", value=-1.0, unit=Unit.HARTREE, result_id=shared_id
        )
        run_input = ScientificResult(
            kind="energy", value=-2.0, unit=Unit.HARTREE, result_id=shared_id
        )
        step = type(
            "StepStub",
            (),
            {"step_id": "s", "results": ResultSet((produced, replace(produced)))},
        )()
        with pytest.raises(ValueError):
            build_result_reference_index((step,))
        step_ok = type(
            "StepStub",
            (),
            {"step_id": "s", "results": ResultSet((produced,))},
        )()
        with pytest.raises(ValueError) as error:
            build_result_reference_index((step_ok,), run_input_results=(run_input,))
        assert "collides" in str(error.value)


class TestRedTeamTspes:
    def test_different_numbers_yield_matching_barriers(self, tmp_path: Path) -> None:
        script = TestR6GenerationLifecycle._science_chain_native(tmp_path)
        doc = TestR6GenerationLifecycle._tspes_doc(script, sp="-123.4", freq="-45.6")
        run_root = tmp_path / "run"
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": WATER_XYZ}),
            )
        )
        assert report.status == "completed"
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        (group,) = [entry for entry in manifest["analyses"] if "group_key" in entry]
        # composite: TS electronic = TS SP (-123.4) plus TS correction (0.10);
        # endpoint electronic = -80.0 plus endpoint correction (0.20).
        expected_barrier = (-123.4 + 0.10) - (-80.0 + 0.20)
        forward = group["barriers"]["forward_endpoint"]["value"]
        reverse = group["barriers"]["reverse_endpoint"]["value"]
        assert forward == pytest.approx(expected_barrier, abs=1e-9)
        assert reverse == pytest.approx(expected_barrier, abs=1e-9)


class TestRedTeamRemoteCrashPoint:
    """Crash AFTER the worker bundle import, BEFORE the producer commit."""

    def test_crash_after_import_reconciles_without_relaunch(self, tmp_path: Path) -> None:
        script = _science_native(tmp_path)
        os.environ[RED_TEAM_ENV] = "-61"
        try:
            runner = tmp_path / "crash_after_import.py"
            runner.write_text(textwrap.dedent(f"""\
                    import copy, os, sys
                    from pathlib import Path
                    sys.path.insert(0, {str(REPO_ROOT)!r})
                    import confflow.remote.staging as staging
                    from confflow.application.v4_run import (
                        V4RunApplication, V4RunRequest, import_xyz,
                    )
                    from confflow.domain import FrozenDict
                    from confflow.execution.process import NativeProcessSupervisor
                    from confflow.persistence.contracts import store_path
                    from confflow.persistence.work_items import SqliteWorkItemStore
                    from confflow.producer import get_recipe_v4
                    from confflow.remote.transport import RemoteTransport
                    from confflow.workflow.v4.assembly import RunInputs

                    tmp = Path({str(tmp_path)!r})
                    run_root = str(tmp / "run")
                    doc = copy.deepcopy(get_recipe_v4("tspes")["document"])
                    doc["steps"] = doc["steps"][:1]
                    doc["global"] = {{"scientific_defaults": {{"charge": 0, "multiplicity": 1}}}}
                    doc["steps"][0]["execution"] = {{
                        "executable": str(tmp / "science_orca"),
                        "target": "cluster",
                    }}
                    inputs = RunInputs(structures=FrozenDict({{
                        "structures": import_xyz({WATER_XYZ!r}),
                    }}))
                    _real = staging.import_result_artifacts
                    def crash_after_import(**kwargs):
                        _real(**kwargs)
                        os._exit(17)
                    staging.import_result_artifacts = crash_after_import
                    with SqliteWorkItemStore.open(store_path(run_root, "ts")) as store:
                        transport = RemoteTransport(
                            run_root=run_root,
                            store=store,
                            worker_root=str(tmp / "worker"),
                            target_name="cluster",
                        )
                        V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                            V4RunRequest(
                                workflow_document=doc,
                                run_inputs=inputs,
                                run_root=run_root,
                                import_sources=FrozenDict({{"structures": {WATER_XYZ!r}}}),
                                transport=transport,
                            )
                        )
                    """))
            crashed = subprocess.run(
                [sys.executable, str(runner)],
                env=dict(os.environ),  # identical env for the crashed attempt and the retry
                capture_output=True,
                text=True,
                timeout=300,
                start_new_session=True,
            )
            assert crashed.returncode == 17, crashed.stderr[-2000:]
            assert _launches(tmp_path) == 1

            run_root = tmp_path / "run"
            doc = _single_step_doc(script, env={RED_TEAM_ENV: "-61"})
            doc["steps"][0]["execution"]["target"] = "cluster"
            with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
                transport = RemoteTransport(
                    run_root=str(run_root),
                    store=store,
                    worker_root=str(tmp_path / "worker"),
                    target_name="cluster",
                )
                report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(
                        workflow_document=doc,
                        run_inputs=RunInputs(
                            structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})
                        ),
                        run_root=str(run_root),
                        import_sources=FrozenDict({"structures": WATER_XYZ}),
                        transport=transport,
                    )
                )
                assert report.status == "completed"
                assert _launches(tmp_path) == 1
                (item_id,) = store.list_items()
                attempts = store.get_attempts(item_id)
                assert [entry.status.value for entry in attempts] == ["completed"]
        finally:
            os.environ.pop(RED_TEAM_ENV, None)
