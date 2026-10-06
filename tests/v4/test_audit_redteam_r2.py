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

import json
import os
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
from confflow.execution.process import NativeProcessSupervisor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._helpers.audit_native import (
    WATER_XYZ,
    _digest_over,
    _last_record,
    _launches,
    _science_native,
    _single_step_doc,
    _stored_environment_digest,
)

RED_TEAM_ENV = "CF_R2_REDTEAM_SCIENCE"


def _run_redteam_step(doc: dict[str, Any], run_root: Path) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})),
            run_root=str(run_root),
            import_sources=FrozenDict({"structures": WATER_XYZ}),
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
            assert _stored_environment_digest(tmp_path / "explicit", "optimize") == _digest_over(
                script, received
            )
        finally:
            os.environ.pop(RED_TEAM_ENV, None)


# R2.2 (G18): TestRedTeamTarget is retired with the ``target`` field.


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
        from confflow.domain import ResultSet, ScientificResult
        from confflow.producer.run_result import (
            build_result_reference_index,
            project_analysis_groups,
        )

        # R2.2: the live tspes run is gone with the recipe; the prior
        # generation's citations come from a retained single-step run.
        script = _redteam_native(tmp_path)
        run_root = tmp_path / "run"
        first = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=_single_step_doc(script),
                run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})),
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
        (only_step,) = first.step_results
        empty_step = type(
            "StepStub",
            (),
            {"step_id": only_step.step_id, "results": ResultSet(), "structures": ()},
        )()
        index = build_result_reference_index((empty_step,))
        profile = ScientificResult(
            kind="reaction_profile",
            value=FrozenDict(
                {
                    "group_key": "g",
                    "nodes": {"ts": "T"},
                    "electronic_energy": {"T": -5.0},
                    "gibbs_energy": {"T": -4.9},
                    "barriers": {"forward_endpoint": {"value": 1.0}},
                    "source_result_ids": [sorted(old_ids)[0]],
                }
            ),
            subject_structure_id="T",
            result_id="profile-g",
        )
        with pytest.raises(ValueError) as error:
            project_analysis_groups(
                (
                    type(
                        "StepStub",
                        (),
                        {
                            "step_id": only_step.step_id,
                            "results": ResultSet((profile,)),
                            "structures": (),
                        },
                    )(),
                ),
                references=index,
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


# R2.2 (G18): TestRedTeamTspes is retired with the tspes chain.
