"""Regression tests for CLI execution identity and failed-run recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.application.execution.workflow_adapter import (
    _inputs_digest,
    run_workflow_through_service,
)
from confflow.cli import _service_run_id
from confflow.core.exceptions import ConfFlowError


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
    # Legacy V2/V3 document fails closed at the formal V4 preflight: the
    # legacy engine resume path is not executable; migration required.
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
    with pytest.raises(
        ConfFlowError,
        match="legacy_workflow_not_executable.*not a V4 workflow document.*migration required",
    ):
        run_workflow_through_service(**common)


def test_failed_resume_revalidates_completed_retries_with_real_engine(tmp_path: Path):
    """Legacy confgen document fails closed at the V4 preflight (migration required)."""
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

    def controlled_runner(**kwargs):
        raise RuntimeError("controlled post-engine failure")

    common = dict(
        input_xyz=[str(input_xyz)],
        config_file=str(config),
        work_dir=str(work),
        state_root=state_root,
        run_id="run-real-confgen-retry",
        workflow_runner=controlled_runner,
    )
    with pytest.raises(
        ConfFlowError,
        match="legacy_workflow_not_executable.*not a V4 workflow document.*migration required",
    ):
        run_workflow_through_service(**common)


def test_failed_resume_rejects_changed_input_for_explicit_run_id(tmp_path: Path):
    # Legacy document never reaches idempotency binding: V4 preflight refuses first.
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
    with pytest.raises(
        ConfFlowError,
        match="legacy_workflow_not_executable.*not a V4 workflow document.*migration required",
    ):
        run_workflow_through_service(**common)


def test_completed_attach_rejects_missing_required_output(tmp_path: Path):
    # Legacy document fails closed before any attach: no legacy execution.
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
    with pytest.raises(
        ConfFlowError,
        match="legacy_workflow_not_executable.*not a V4 workflow document.*migration required",
    ):
        run_workflow_through_service(**common)
