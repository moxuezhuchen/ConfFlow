"""Tests for the extracted secure worker staging helpers."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from confflow import control_worker, worker_staging
from confflow.application.execution.state_root import StateRoot

pytestmark = pytest.mark.skipif(os.name != "posix", reason="staging contract requires POSIX")


def test_stage_file_preserves_digest_and_owner_only_modes(tmp_path: Path) -> None:
    source = tmp_path / "input.xyz"
    source.write_bytes(b"1\nH\nH 0 0 0\n")
    destination = tmp_path / "stage" / "nested" / "input.xyz"
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    assert (
        worker_staging._stage_file(str(source), destination, expected_digest=expected)
        == destination
    )
    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700


def test_stage_file_rejects_digest_mismatch_after_copy(tmp_path: Path) -> None:
    source = tmp_path / "input.xyz"
    source.write_bytes(b"input")
    destination = tmp_path / "stage" / "input.xyz"

    with pytest.raises(ValueError, match="changed while being staged"):
        worker_staging._stage_file(str(source), destination, expected_digest="0" * 64)


def test_stage_file_does_not_follow_destination_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"source")
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"sentinel")
    destination = tmp_path / "stage"
    destination.mkdir(mode=0o700)
    link = destination / "input"
    link.symlink_to(sentinel)

    with pytest.raises((ValueError, OSError)):
        worker_staging._stage_file(
            str(source), link, expected_digest=hashlib.sha256(source.read_bytes()).hexdigest()
        )
    assert sentinel.read_bytes() == b"sentinel"


def test_ensure_directory_rejects_symlink_and_normalizes_mode(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)
    worker_staging._ensure_directory(directory)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        worker_staging._ensure_directory(link)


def test_control_worker_wrapper_preserves_staging_monkeypatch_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir(mode=0o700)
    os.chmod(attempt_root, 0o700)
    state_path = attempt_root / "state"
    state_path.mkdir(mode=0o700)
    os.chmod(state_path, 0o700)
    root = StateRoot.resolve(state_path)
    config = attempt_root / "workflow.yaml"
    input_xyz = attempt_root / "input.xyz"
    config.write_text("steps: []\n", encoding="utf-8")
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    tasks = [
        {
            "task_id": "input",
            "input_xyz": str(input_xyz),
            "work_dir": str(attempt_root / "work"),
            "sha256": "input-digest",
        }
    ]
    calls: list[str] = []

    def fake_stage(source: str, destination: Path, *, expected_digest: str) -> Path:
        del source, expected_digest
        calls.append(f"stage:{destination.name}")
        return destination

    def fake_ensure(path: Path) -> None:
        calls.append(f"ensure:{path.name}")

    monkeypatch.setattr(control_worker, "_stage_file", fake_stage)
    monkeypatch.setattr(control_worker, "_ensure_directory", fake_ensure)
    staged_config, staged_tasks = control_worker._stage_worker_inputs(
        root,
        "compatibility-run",
        str(config),
        tasks,
        expected_config_digest="config-digest",
    )

    assert staged_config.endswith("/staging/workflow.yaml")
    assert staged_tasks[0]["input_xyz"].endswith("/staging/inputs/input.xyz")
    assert calls == ["stage:workflow.yaml", "stage:input.xyz", "ensure:work"]
