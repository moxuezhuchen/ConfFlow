#!/usr/bin/env python3

"""Tests for manifest-based calc artifacts."""

from __future__ import annotations

import json

import pytest

from confflow.calc.artifacts import (
    CalcArtifactManager,
    compute_config_digest,
    compute_input_digest,
)
from confflow.config.models import CalcStepParams, GlobalOptions
from confflow.core.exceptions import PathSafetyError


def _calc_config(**overrides):
    params = {"keyword": "HF", "iprog": "orca", "itask": "sp", "auto_clean": False}
    params.update(overrides)
    return CalcStepParams.from_params(params, GlobalOptions.from_mapping({}))


def _xyz(path, comment="frame"):
    path.write_text(f"1\n{comment}\nH 0 0 0\n", encoding="utf-8")
    return path


def test_manifest_marks_completed_and_reuses_matching_output(tmp_path):
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_01_calc"
    config = _calc_config()
    manager = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=config,
        input_path=input_xyz,
    )

    assert manager.prepare(resume=False).reusable_output is None
    manager.mark_running()
    output = step_dir / "result.xyz"
    output.write_text("1\nok\nH 0 0 0\n", encoding="utf-8")
    manager.mark_completed(
        output_path=output,
        failed_path=None,
        total_tasks=1,
        succeeded=1,
        failed_count=0,
    )

    prepared = manager.prepare(resume=False)
    assert prepared.reusable_output == output
    manifest = json.loads((step_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["output"] == "result.xyz"
    assert manifest["config_digest"] == compute_config_digest(config)
    assert manifest["input_digest"] == compute_input_digest(input_xyz)


def test_manifest_cleans_stale_output_when_config_changes(tmp_path):
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_01_calc"
    old = CalcArtifactManager(step_dir, step_name="calc", config=_calc_config(), input_path=input_xyz)
    step_dir.mkdir()
    stale = step_dir / "result.xyz"
    stale.write_text("stale", encoding="utf-8")
    old.mark_completed(output_path=stale, failed_path=None, total_tasks=1, succeeded=1, failed_count=0)

    new = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=_calc_config(keyword="B3LYP"),
        input_path=input_xyz,
    )
    prepared = new.prepare(resume=False)

    assert prepared.cleaned_stale_artifacts is True
    assert not stale.exists()
    assert list(step_dir.iterdir()) == []


def test_manifest_cleanup_respects_sandbox_root(tmp_path):
    input_xyz = _xyz(tmp_path / "input.xyz")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside_step = tmp_path / "outside_step"
    outside_step.mkdir()
    (outside_step / "result.xyz").write_text("stale", encoding="utf-8")

    config = _calc_config(sandbox_root=str(sandbox))
    manager = CalcArtifactManager(
        outside_step,
        step_name="calc",
        config=config,
        input_path=input_xyz,
    )

    with pytest.raises(PathSafetyError):
        manager.prepare(resume=False)
