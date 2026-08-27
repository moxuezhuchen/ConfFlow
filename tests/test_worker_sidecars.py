"""Tests for the extracted worker sidecar publication boundary."""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path

import pytest

import confflow.control_worker as control_worker
import confflow.worker_sidecars as worker_sidecars
from confflow.application.execution.state_root import StateRoot

pytestmark = pytest.mark.skipif(os.name != "posix", reason="sidecar contract requires POSIX")


def _root_and_attempt(tmp_path: Path) -> tuple[StateRoot, Path]:
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir(mode=0o700)
    os.chmod(attempt_root, 0o700)
    state_path = attempt_root / "state"
    state_path.mkdir(mode=0o700)
    os.chmod(state_path, 0o700)
    return StateRoot.resolve(state_path), attempt_root


def test_sidecar_sources_are_fixed_by_the_input_stem(tmp_path: Path) -> None:
    input_xyz = tmp_path / "methane.xyz"
    assert worker_sidecars._sidecar_sources(str(input_xyz)) == (
        tmp_path / "methane.txt",
        tmp_path / "methanemin.xyz",
    )


def test_control_worker_wrapper_preserves_sidecar_stage_patch_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, attempt_root = _root_and_attempt(tmp_path)
    input_xyz = attempt_root / "methane.xyz"
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    report = attempt_root / "methane.txt"
    report.write_text("workflow report\n", encoding="utf-8")
    minimum = attempt_root / "methanemin.xyz"
    minimum.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    work_dir = attempt_root / "results" / "methane_confflow_work"
    work_dir.mkdir(parents=True, mode=0o700)

    calls: list[tuple[str, str, str]] = []

    def fake_stage(source: str, destination: Path, *, expected_digest: str) -> Path:
        calls.append((Path(source).name, destination.name, expected_digest))
        destination.write_bytes(Path(source).read_bytes())
        return destination

    monkeypatch.setattr(control_worker, "_stage_file", fake_stage)
    control_worker._publish_worker_sidecars(
        root,
        staged_input=str(input_xyz),
        work_dir=str(work_dir),
    )

    assert [source for source, _, _ in calls] == ["methane.txt", "methanemin.xyz"]
    assert [destination for _, destination, _ in calls] == ["methane.txt", "methanemin.xyz"]
    assert all(
        digest == hashlib.sha256((attempt_root / name).read_bytes()).hexdigest()
        for name, _, digest in calls
    )
    assert (attempt_root / "results" / "methane.txt").read_text(encoding="utf-8") == (
        "workflow report\n"
    )
    assert (attempt_root / "results" / "methanemin.xyz").is_file()


def test_control_worker_keeps_sidecar_wrapper_outside_the_publisher() -> None:
    package_root = Path(__file__).parents[1] / "confflow"
    control_source = (package_root / "control_worker.py").read_text(encoding="utf-8")
    sidecar_source = (package_root / "worker_sidecars.py").read_text(encoding="utf-8")
    control_tree = ast.parse(control_source)
    wrapper = next(
        node
        for node in control_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_publish_worker_sidecars"
    )
    wrapper_text = ast.unparse(wrapper)
    assert "_worker_sidecars._publish_worker_sidecars" in wrapper_text
    assert "stage_file=_stage_file" in wrapper_text
    assert "file_digest=_file_digest" in wrapper_text
    assert "output_txt_path_for_input" not in control_source
    assert "def _publish_worker_sidecars" in sidecar_source
    assert "def _sidecar_sources" in sidecar_source
