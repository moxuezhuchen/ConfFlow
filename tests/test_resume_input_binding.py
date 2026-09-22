#!/usr/bin/env python3

"""Strict-resume input binding and partial-manifest safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from confflow.workflow.engine import run_workflow
from confflow.workflow.helpers import is_multi_frame_any
from confflow.workflow.state import WORKFLOW_STATE_FILE, WorkflowStateStore
from confflow.workflow.step_handlers import (
    StepExecutionResult,
    _build_confgen_run_kwargs,
    _compute_confgen_step_signature,
    _record_confgen_step_signature,
)


def _write_input(tmp_path: Path, name: str, comment: str) -> Path:
    path = tmp_path / name
    path.write_text(f"1\n{comment}\nH 0 0 0\n", encoding="utf-8")
    return path


def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n" "steps:\n" "  - name: gen\n" "    type: confgen\n" "    params: {}\n",
        encoding="utf-8",
    )
    return config


def _patch_confgen(monkeypatch) -> None:
    def fake_confgen(step_dir, current_input, params, input_files, global_config=None):
        path = Path(step_dir) / "search.xyz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1\ngen\nH 0 0 0\n", encoding="utf-8")
        signature = _compute_confgen_step_signature(
            current_input=current_input,
            input_files=input_files,
            run_kwargs=_build_confgen_run_kwargs(params, current_input, global_config),
            multi_frame=len(input_files) == 1 and is_multi_frame_any(current_input),
        )
        _record_confgen_step_signature(step_dir, signature)
        return StepExecutionResult(output_path=str(path))

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)


def _snapshot(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snap[str(path.relative_to(root))] = path.read_bytes()
    return snap


def test_resume_same_input_is_allowed(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(input_xyz)], str(config), str(work_dir))
    stats = run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert Path(stats["final_output"]).name == "search.xyz"


def test_resume_rejects_changed_input_content(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(input_xyz)], str(config), str(work_dir))
    before = _snapshot(work_dir)

    # Same path, different content.
    input_xyz.write_text("1\nchanged\nH 0 0 0\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="do not match the current inputs"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert _snapshot(work_dir) == before


def test_resume_rejects_different_input_path(tmp_path, monkeypatch):
    first = _write_input(tmp_path, "first.xyz", "seed")
    second = _write_input(tmp_path, "second.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(first)], str(config), str(work_dir))

    with pytest.raises(RuntimeError, match="do not match the current inputs"):
        run_workflow([str(second)], str(config), str(work_dir), resume=True)


def test_resume_rejects_changed_multi_input(tmp_path, monkeypatch):
    first = _write_input(tmp_path, "first.xyz", "one")
    second = _write_input(tmp_path, "second.xyz", "two")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(first), str(second)], str(config), str(work_dir))

    second.write_text("1\nchanged\nH 0 0 0\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="do not match the current inputs"):
        run_workflow([str(first), str(second)], str(config), str(work_dir), resume=True)


def test_resume_without_input_binding_is_rejected(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(input_xyz)], str(config), str(work_dir))

    state_path = work_dir / WORKFLOW_STATE_FILE
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw.pop("input_digests", None)
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(RuntimeError, match="no input binding"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)


def test_state_round_trips_input_digests(tmp_path):
    from confflow.workflow.engine import _compute_input_digests
    from confflow.workflow.state import WorkflowState

    input_xyz = _write_input(tmp_path, "input.xyz", "seed")

    digests = _compute_input_digests([str(input_xyz)])
    state = WorkflowState(
        run_id="r",
        work_dir=str(tmp_path),
        input_files=[str(input_xyz)],
        original_inputs=[str(input_xyz)],
        config_file=str(_write_config(tmp_path)),
        input_digests=digests,
    )
    store = WorkflowStateStore(str(tmp_path))
    store.save(state)
    loaded = store.load()
    assert loaded is not None
    assert loaded.input_digests == digests


def test_running_manifest_is_not_reused(tmp_path):
    """A crashed calc step (running manifest + residual output) must be rejected."""
    from confflow.calc.artifacts import CalcArtifactManager
    from confflow.config.canonical import resolve_calc_step
    from confflow.config.models import GlobalOptions
    from confflow.workflow.engine import _reject_incomplete_manifest

    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    step_dir = tmp_path / "step_01_calc"
    step_dir.mkdir()
    config = resolve_calc_step(
        {"keyword": "HF", "iprog": "orca", "itask": "sp"}, GlobalOptions.from_mapping({})
    )
    manager = CalcArtifactManager(
        step_dir, step_name="calc", config=config, input_path=str(input_xyz)
    )
    manager.mark_running()
    (step_dir / "result.xyz").write_text("1\npartial\nH 0 0 0\n", encoding="utf-8")

    prepared = manager.prepare(resume=True)

    assert prepared.reusable_output is None
    assert prepared.manifest is not None
    assert prepared.manifest.status == "running"
    with pytest.raises(RuntimeError, match="not complete"):
        _reject_incomplete_manifest(
            prepared, step_index=1, step_name="calc", step_dir=str(step_dir)
        )


def test_completed_manifest_is_accepted(tmp_path):
    from confflow.calc.artifacts import CalcArtifactManager
    from confflow.config.canonical import resolve_calc_step
    from confflow.config.models import GlobalOptions
    from confflow.workflow.engine import _reject_incomplete_manifest

    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    step_dir = tmp_path / "step_01_calc"
    step_dir.mkdir()
    config = resolve_calc_step(
        {"keyword": "HF", "iprog": "orca", "itask": "sp"}, GlobalOptions.from_mapping({})
    )
    manager = CalcArtifactManager(
        step_dir, step_name="calc", config=config, input_path=str(input_xyz)
    )
    manager.mark_running()
    output = step_dir / "result.xyz"
    output.write_text("1\ndone\nH 0 0 0\n", encoding="utf-8")
    manager.mark_completed(
        output_path=output, failed_path=None, total_tasks=1, succeeded=1, failed_count=0
    )

    prepared = manager.prepare(resume=True)

    assert prepared.reusable_output == output
    # Must not raise for a completed manifest.
    _reject_incomplete_manifest(prepared, step_index=1, step_name="calc", step_dir=str(step_dir))


@pytest.mark.parametrize("corrupt", ["", "1\ntruncated\nH 0 0\n"])
def test_resume_rejects_invalid_completed_xyz_without_writing(tmp_path, monkeypatch, corrupt):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(input_xyz)], str(config), str(work_dir))
    output = work_dir / "gen" / "search.xyz"
    output.write_text(corrupt, encoding="utf-8")
    before = _snapshot(work_dir)

    with pytest.raises(RuntimeError, match="valid XYZ|empty"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert _snapshot(work_dir) == before


def test_resume_rejects_completed_output_directory_without_writing(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(input_xyz)], str(config), str(work_dir))
    output = work_dir / "gen" / "search.xyz"
    output.unlink()
    output.mkdir()
    before = _snapshot(work_dir)

    with pytest.raises(RuntimeError, match="not a file"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert output.is_dir()
    assert _snapshot(work_dir) == before


def test_resume_rejects_unknown_confgen_signature_without_cleanup(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    run_workflow([str(input_xyz)], str(config), str(work_dir))
    signature = work_dir / "gen" / ".confgen_signature"
    signature.write_text("future:v9\n", encoding="utf-8")
    before = _snapshot(work_dir)

    with pytest.raises(RuntimeError, match="signature"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert _snapshot(work_dir) == before


def test_resume_validates_old_checkpoint_output_before_adoption(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: first\n"
        "    type: confgen\n"
        "  - name: second\n"
        "    type: confgen\n",
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)
    run_workflow([str(input_xyz)], str(config), str(work_dir))

    state_path = work_dir / WORKFLOW_STATE_FILE
    state_raw = json.loads(state_path.read_text(encoding="utf-8"))
    state_raw["steps"]["first"]["status"] = "pending"
    state_raw["steps"]["first"]["output_xyz"] = None
    state_path.write_text(json.dumps(state_raw), encoding="utf-8")
    (work_dir / ".checkpoint").write_text(json.dumps({"last_completed_step": 0}), encoding="utf-8")
    output = work_dir / "first" / "search.xyz"
    output.write_text("1\npartial\nH 0 0\n", encoding="utf-8")
    before = _snapshot(work_dir)

    with pytest.raises(RuntimeError, match="valid XYZ"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert _snapshot(work_dir) == before


def test_resume_rejects_completed_calc_manifest_mismatch_without_cleanup(tmp_path, monkeypatch):
    from confflow.calc.artifacts import CalcArtifactManager
    from confflow.config.canonical import resolve_calc_step

    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: calc\n"
        "    type: calc\n"
        "    params: {iprog: orca, itask: sp, keyword: HF}\n",
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"

    def fake_calc(
        step_dir,
        current_input,
        params,
        global_config,
        root_dir,
        steps,
        failure_tracker,
        step_name,
        *,
        typed_global,
        **kwargs,
    ):
        del global_config, root_dir, steps, failure_tracker, kwargs
        resolved = resolve_calc_step(params, typed_global)
        manager = CalcArtifactManager(
            step_dir,
            step_name=step_name,
            config=resolved,
            input_path=current_input,
        )
        manager.mark_running()
        output = Path(step_dir) / "result.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\ncalc\nH 0 0 0\n", encoding="utf-8")
        manager.mark_completed(
            output_path=output,
            failed_path=None,
            total_tasks=1,
            succeeded=1,
            failed_count=0,
        )
        return StepExecutionResult(output_path=str(output))

    monkeypatch.setattr("confflow.workflow.engine._run_calc_step", fake_calc)
    run_workflow([str(input_xyz)], str(config), str(work_dir))

    manifest_path = work_dir / "calc" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["config_digest"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = _snapshot(work_dir)

    with pytest.raises(RuntimeError, match="config digest"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert _snapshot(work_dir) == before


def test_resume_calc_manifest_uses_chk_from_step_in_config_digest(tmp_path):
    from confflow.calc.artifacts import CalcArtifactManager
    from confflow.config.canonical import resolve_calc_step
    from confflow.config.models import GlobalOptions
    from confflow.workflow.resume_validation import validate_reusable_artifact

    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    root_dir = tmp_path / "work"
    step_dir = root_dir / "calc"
    params = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "HF",
        "chk_from_step": "first",
    }
    steps = [
        {"name": "first", "type": "confgen"},
        {"name": "calc", "type": "calc", "params": params},
    ]
    typed_global = GlobalOptions.from_mapping({})
    resolved = resolve_calc_step(
        params,
        typed_global,
        input_chk_dir=str(root_dir / "first" / "backups"),
    )
    manager = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=resolved,
        input_path=str(input_xyz),
    )
    manager.mark_running()
    output = step_dir / "result.xyz"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("1\ncalc\nH 0 0 0\n", encoding="utf-8")
    manager.mark_completed(
        output_path=output,
        failed_path=None,
        total_tasks=1,
        succeeded=1,
        failed_count=0,
    )

    assert validate_reusable_artifact(
        output_path=str(output),
        root_dir=str(root_dir),
        step_dir=str(step_dir),
        step_name="calc",
        step_type="calc",
        current_input=str(input_xyz),
        input_files=[str(input_xyz)],
        params=params,
        global_config={},
        typed_global=typed_global,
        steps=steps,
    ) == str(output.resolve())


def test_resume_accepts_matching_real_confgen_signature(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path, "input.xyz", "seed")
    config = _write_config(tmp_path)
    work_dir = tmp_path / "work"

    def fake_run_generation(**kwargs):
        del kwargs
        Path("search.xyz").write_text("1\ngenerated\nH 0 0 0\n", encoding="utf-8")

    monkeypatch.setattr(
        "confflow.workflow.step_handlers.confgen.run_generation",
        fake_run_generation,
    )
    run_workflow([str(input_xyz)], str(config), str(work_dir))

    assert (work_dir / "gen" / ".confgen_signature").is_file()
    stats = run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert stats["final_output"] == str((work_dir / "gen" / "search.xyz").resolve())


def test_chk_from_step_changes_fingerprint():
    """chk_from_step is consumed by execution and must bind into the fingerprint."""
    from types import SimpleNamespace

    from confflow.config.canonical.fingerprint import workflow_fingerprint
    from confflow.config.canonical.types import GlobalOptions

    def fingerprint(chk_from_step: str) -> str:
        params = {"keyword": "HF", "iprog": "orca", "itask": "sp", "chk_from_step": chk_from_step}
        plan = SimpleNamespace(
            typed_global=GlobalOptions.from_mapping({}),
            steps=[{"name": "c", "type": "calc", "params": params}],
            predecessors={},
            execution_order=["c"],
            terminal_steps=["c"],
            step_dirnames=["step_01_c"],
        )
        return workflow_fingerprint(plan)

    assert fingerprint("step_a") != fingerprint("step_b")
