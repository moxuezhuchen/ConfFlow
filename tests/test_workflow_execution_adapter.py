"""Contract tests for the Phase C workflow-to-service adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from confflow.agent.runner import JobContext, JobRunner
from confflow.agent.state import AgentStateDB, JobStatus
from confflow.application.execution import RunState
from confflow.application.execution.workflow_adapter import run_workflow_through_service
from confflow.core.exceptions import StopRequestedError


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("global: {}\nsteps: []\n", encoding="utf-8")
    return input_xyz, config, tmp_path / "work"


def test_direct_adapter_commits_lifecycle_and_manifest_artifacts(tmp_path: Path):
    input_xyz, config, work = _files(tmp_path)

    def fake_runner(**kwargs):
        output = Path(kwargs["work_dir"]) / "g16_opt" / "output.xyz"
        output.parent.mkdir(parents=True)
        output.write_text("1\noutput\nH 0 0 0\n", encoding="utf-8")
        (Path(kwargs["work_dir"]) / "output_manifest.json").write_text(
            json.dumps(
                {
                    "content_schema": "confflow.output_manifest.v1",
                    "terminals": {"g16_opt": ["g16_opt/output.xyz"]},
                }
            ),
            encoding="utf-8",
        )
        return {"result": "ok"}

    result = run_workflow_through_service(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=str(tmp_path / "state"),
        run_id="run-adapter-001",
        workflow_runner=fake_runner,
    )

    assert result == {"result": "ok"}
    from confflow.application.execution import SQLiteExecutionRepository, StateRoot

    repository = SQLiteExecutionRepository(StateRoot.resolve(tmp_path / "state"))
    aggregate = repository.read("run-adapter-001")
    assert aggregate is not None
    assert aggregate.state is RunState.COMPLETED
    assert [event.type for event in aggregate.events] == [
        "prepared",
        "queued",
        "running",
        "completed",
    ]
    assert aggregate.artifacts[0].path == "g16_opt/output.xyz"
    assert aggregate.artifacts[0].content_schema == "confflow.output_manifest.v1"


def test_direct_adapter_resume_reuses_idempotency_request_and_checkpoint_boundary(tmp_path: Path):
    input_xyz, config, work = _files(tmp_path)
    calls = {"count": 0}

    def fake_runner(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            from confflow.workflow.state import StepRecord

            kwargs["on_step_status_change"](
                StepRecord(name="step", type="confgen", status="completed")
            )
            raise StopRequestedError("pause")
        return {"resumed": kwargs["resume"]}

    common = dict(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=str(tmp_path / "state"),
        run_id="run-adapter-002",
        workflow_runner=fake_runner,
    )
    with pytest.raises(StopRequestedError):
        run_workflow_through_service(**common)

    result = run_workflow_through_service(**common, resume=True)
    assert result == {"resumed": True}


def test_agent_service_path_keeps_agent_db_as_projection(tmp_path: Path, monkeypatch):
    input_xyz, config, work = _files(tmp_path)
    database = AgentStateDB(str(tmp_path / "agent.db"))
    database.add_job(
        "job-service",
        str(config),
        str(input_xyz),
        "2026-01-01T00:00:00Z",
        "test",
    )
    ctx = JobContext(
        job_id="job-service",
        config_file=str(config),
        input_xyz=str(input_xyz),
        work_dir=str(work),
        pause_beacon_file=str(work / "PAUSE"),
        state_db=database,
        execution_state_root=str(tmp_path / "execution-state"),
    )
    monkeypatch.setattr("confflow.agent.runner.run_workflow", lambda **kwargs: {"ok": True})

    JobRunner(ctx).run()

    assert database.get_job("job-service")["status"] == JobStatus.DONE.value
    from confflow.application.execution import SQLiteExecutionRepository, StateRoot

    aggregate = SQLiteExecutionRepository(StateRoot.resolve(tmp_path / "execution-state")).read(
        "job-service"
    )
    assert aggregate is not None and aggregate.state is RunState.COMPLETED
    database.close()
