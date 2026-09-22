"""Regression tests for CLI execution identity and failed-run recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.application.execution import (
    ErrorCode,
    ExecutionServiceError,
    RunState,
    SQLiteExecutionRepository,
    StateRoot,
)
from confflow.application.execution.workflow_adapter import (
    _inputs_digest,
    run_workflow_through_service,
)
from confflow.cli import _service_run_id
from confflow.workflow.engine import run_workflow as real_workflow_runner
from confflow.workflow.resume_validation import ResumeArtifactCompatibilityError


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\ninput\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text("global: {}\nsteps: []\n", encoding="utf-8")
    return input_xyz, config, tmp_path / "work"


def test_service_run_id_binds_file_content_and_order(tmp_path: Path):
    first = tmp_path / "first.xyz"
    second = tmp_path / "second.xyz"
    config = tmp_path / "config.yaml"
    first.write_text("1\nfirst\nH 0 0 0\n", encoding="utf-8")
    second.write_text("1\nsecond\nH 1 0 0\n", encoding="utf-8")
    config.write_text("global: {}\nsteps: []\n", encoding="utf-8")
    work = str(tmp_path / "work")

    original = _service_run_id(str(config), [str(first), str(second)], work)
    assert original != _service_run_id(str(config), [str(second), str(first)], work)

    first.write_text("1\nchanged\nH 5 0 0\n", encoding="utf-8")
    assert original != _service_run_id(str(config), [str(first), str(second)], work)

    config.write_text("global: {}\nsteps:\n  - name: changed\n", encoding="utf-8")
    assert original != _service_run_id(str(config), [str(first), str(second)], work)


def test_input_manifest_digest_preserves_file_boundaries_and_order(tmp_path: Path):
    first = tmp_path / "first.xyz"
    second = tmp_path / "second.xyz"
    combined = tmp_path / "combined.xyz"
    first.write_bytes(b"ab")
    second.write_bytes(b"c")
    combined.write_bytes(b"abc")

    assert _inputs_digest([str(first), str(second)]) != _inputs_digest([str(combined)])
    assert _inputs_digest([str(first), str(second)]) != _inputs_digest([str(second), str(first)])


def test_failed_resume_uses_new_controlled_record_and_engine_resume(tmp_path: Path):
    input_xyz, config, work = _files(tmp_path)
    state_root = tmp_path / "state"
    calls: list[bool] = []

    def runner(**kwargs):
        calls.append(bool(kwargs["resume"]))
        if len(calls) == 1:
            raise RuntimeError("controlled first failure")
        return {"resumed": kwargs["resume"]}

    common = dict(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=state_root,
        run_id="run-failed-resume",
        workflow_runner=runner,
    )
    with pytest.raises(RuntimeError, match="controlled first failure"):
        run_workflow_through_service(**common)

    result = run_workflow_through_service(**common, resume=True)
    assert result == {"resumed": True}
    assert calls == [False, True]

    repository = SQLiteExecutionRepository(StateRoot.resolve(state_root))
    original = repository.read("run-failed-resume")
    retry = repository.read("run-failed-resume.resume.1")
    assert original is not None and original.state is RunState.FAILED
    assert retry is not None and retry.state is RunState.COMPLETED


def test_failed_resume_revalidates_completed_retries_with_real_engine(tmp_path: Path):
    """Every explicit resume must recheck real completed ConfGen artifacts."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text(
        "1\nframe-1\nH 0 0 0\n" "1\nframe-2\nH 0 0 1\n",
        encoding="utf-8",
    )
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n" "steps:\n" "  - name: gen\n" "    type: confgen\n" "    params: {}\n",
        encoding="utf-8",
    )
    work = tmp_path / "work"
    state_root = tmp_path / "state"
    calls: list[bool] = []

    def controlled_runner(**kwargs):
        calls.append(bool(kwargs["resume"]))
        result = real_workflow_runner(**kwargs)
        if len(calls) == 1:
            raise RuntimeError("controlled post-engine failure")
        return result

    common = dict(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=state_root,
        run_id="run-real-confgen-retry",
        workflow_runner=controlled_runner,
    )
    with pytest.raises(RuntimeError, match="controlled post-engine failure"):
        run_workflow_through_service(**common)

    output = work / "gen" / "search.xyz"
    signature = work / "gen" / ".confgen_signature"
    assert output.is_file()
    assert signature.is_file()
    repository = SQLiteExecutionRepository(StateRoot.resolve(state_root))
    base = repository.read("run-real-confgen-retry")
    assert base is not None and base.state is RunState.FAILED

    first_resume = run_workflow_through_service(**common, resume=True)
    assert first_resume is not None
    assert calls == [False, True]
    retry_one = repository.read("run-real-confgen-retry.resume.1")
    assert retry_one is not None and retry_one.state is RunState.COMPLETED
    assert repository.read("run-real-confgen-retry").state is RunState.FAILED

    output_bytes = output.read_bytes()
    output_mtime = output.stat().st_mtime_ns
    signature_bytes = signature.read_bytes()
    second_resume = run_workflow_through_service(**common, resume=True)
    assert second_resume is not None
    assert calls == [False, True, True]
    assert output.read_bytes() == output_bytes
    assert output.stat().st_mtime_ns == output_mtime
    assert signature.read_bytes() == signature_bytes
    retry_two = repository.read("run-real-confgen-retry.resume.2")
    assert retry_two is not None and retry_two.state is RunState.COMPLETED

    signature.unlink()
    with pytest.raises(RuntimeError, match="confgen signature is missing") as caught:
        run_workflow_through_service(**common, resume=True)
    assert calls == [False, True, True, True]
    assert isinstance(caught.value.__cause__, ResumeArtifactCompatibilityError)
    assert output.read_bytes() == output_bytes
    assert output.stat().st_mtime_ns == output_mtime
    assert not signature.exists()
    assert repository.read("run-real-confgen-retry").state is RunState.FAILED
    assert repository.read("run-real-confgen-retry.resume.1").state is RunState.COMPLETED
    assert repository.read("run-real-confgen-retry.resume.2").state is RunState.COMPLETED
    retry_three = repository.read("run-real-confgen-retry.resume.3")
    assert retry_three is not None and retry_three.state is RunState.FAILED


def test_failed_resume_rejects_changed_input_for_explicit_run_id(tmp_path: Path):
    input_xyz, config, work = _files(tmp_path)
    state_root = tmp_path / "state"

    def runner(**_kwargs):
        raise RuntimeError("controlled first failure")

    common = dict(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=state_root,
        run_id="run-failed-input-binding",
        workflow_runner=runner,
    )
    with pytest.raises(RuntimeError, match="controlled first failure"):
        run_workflow_through_service(**common)

    input_xyz.write_text("1\nchanged\nH 9 0 0\n", encoding="utf-8")
    with pytest.raises(ExecutionServiceError) as caught:
        run_workflow_through_service(**common, resume=True)
    assert caught.value.code is ErrorCode.IDEMPOTENCY_CONFLICT


def test_completed_attach_rejects_missing_required_output(tmp_path: Path):
    input_xyz, config, work = _files(tmp_path)
    state_root = tmp_path / "state"
    output = work / "result.xyz"

    def runner(**_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nresult\nH 0 0 0\n", encoding="utf-8")
        (work / "workflow_stats.json").write_text(
            '{"final_output": "' + str(output) + '"}', encoding="utf-8"
        )
        return {"ok": True}

    common = dict(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=state_root,
        run_id="run-completed-attach",
        workflow_runner=runner,
    )
    assert run_workflow_through_service(**common) == {"ok": True}
    output.unlink()

    with pytest.raises(ExecutionServiceError) as caught:
        run_workflow_through_service(**common)
    assert caught.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
