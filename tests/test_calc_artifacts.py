#!/usr/bin/env python3

"""Tests for manifest-based calc artifacts."""

from __future__ import annotations

import json

import pytest

from confflow.calc.artifacts import (
    CalcArtifactManager,
    CalcManifestCompatibilityError,
    CalcResumeCompatibilityError,
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


def _snapshot_tree(root):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_dir():
            snapshot[rel + "/"] = b""
        else:
            snapshot[rel] = path.read_bytes()
    return snapshot


def _completed_step_dir(tmp_path):
    """Return (input_xyz, step_dir) with a completed manifest plus extra artifacts."""
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_01_calc"
    manager = CalcArtifactManager(
        step_dir, step_name="calc", config=_calc_config(), input_path=input_xyz
    )
    step_dir.mkdir()
    manager.mark_running()
    output = step_dir / "result.xyz"
    output.write_text("result", encoding="utf-8")
    manager.mark_completed(
        output_path=output, failed_path=None, total_tasks=1, succeeded=1, failed_count=0
    )
    (step_dir / "results.db").write_bytes(b"results")
    backup_dir = step_dir / "backups"
    backup_dir.mkdir()
    (backup_dir / "old.xyz").write_text("backup", encoding="utf-8")
    (step_dir / "sentinel.txt").write_text("keep-me", encoding="utf-8")
    return input_xyz, step_dir


def test_strict_resume_config_mismatch_leaves_step_dir_untouched(tmp_path):
    input_xyz, step_dir = _completed_step_dir(tmp_path)
    before = _snapshot_tree(step_dir)

    changed = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=_calc_config(keyword="B3LYP"),
        input_path=input_xyz,
    )

    with pytest.raises(CalcResumeCompatibilityError):
        changed.prepare(resume=True)

    assert _snapshot_tree(step_dir) == before


def test_strict_resume_input_mismatch_leaves_step_dir_untouched(tmp_path):
    input_xyz, step_dir = _completed_step_dir(tmp_path)
    before = _snapshot_tree(step_dir)

    other_input = _xyz(tmp_path / "other.xyz", comment="changed")
    changed = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=_calc_config(),
        input_path=other_input,
    )

    with pytest.raises(CalcResumeCompatibilityError):
        changed.prepare(resume=True)

    assert _snapshot_tree(step_dir) == before


def test_strict_resume_missing_manifest_leaves_step_dir_untouched(tmp_path):
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_01_calc"
    step_dir.mkdir()
    (step_dir / "result.xyz").write_text("orphan", encoding="utf-8")
    (step_dir / "sentinel.txt").write_text("keep-me", encoding="utf-8")
    before = _snapshot_tree(step_dir)

    manager = CalcArtifactManager(
        step_dir, step_name="calc", config=_calc_config(), input_path=input_xyz
    )

    with pytest.raises(CalcResumeCompatibilityError):
        manager.prepare(resume=True)

    assert _snapshot_tree(step_dir) == before


def test_normal_run_config_mismatch_cleans_stale_artifacts(tmp_path):
    input_xyz, step_dir = _completed_step_dir(tmp_path)

    changed = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=_calc_config(keyword="B3LYP"),
        input_path=input_xyz,
    )
    prepared = changed.prepare(resume=False)

    assert prepared.cleaned_stale_artifacts is True
    assert not (step_dir / "result.xyz").exists()
    assert not (step_dir / "results.db").exists()
    assert not (step_dir / "sentinel.txt").exists()
    assert list(step_dir.iterdir()) == []


def test_manifest_cleans_stale_output_when_config_changes(tmp_path):
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_01_calc"
    old = CalcArtifactManager(
        step_dir, step_name="calc", config=_calc_config(), input_path=input_xyz
    )
    step_dir.mkdir()
    stale = step_dir / "result.xyz"
    stale.write_text("stale", encoding="utf-8")
    old.mark_completed(
        output_path=stale, failed_path=None, total_tasks=1, succeeded=1, failed_count=0
    )

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


@pytest.mark.parametrize("manifest_text", ("{not-json", "[]", '{"schema_version": 999}'))
def test_unknown_or_malformed_manifest_fails_closed_without_cleanup(tmp_path, manifest_text):
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_01_calc"
    step_dir.mkdir()
    sentinel = step_dir / "result.xyz"
    sentinel.write_text("preserve", encoding="utf-8")
    (step_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")

    manager = CalcArtifactManager(
        step_dir, step_name="calc", config=_calc_config(), input_path=input_xyz
    )

    with pytest.raises(CalcManifestCompatibilityError):
        manager.prepare(resume=False)

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert (step_dir / "manifest.json").read_text(encoding="utf-8") == manifest_text
