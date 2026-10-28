"""Application-level storage preflight for V4 run roots.

A run root on a filesystem that cannot honour the WAL policy must be refused
before generation ownership, run state, or publication files are written.
A previously completed run must stay byte-identical when a rerun is refused.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import confflow.persistence.storage_policy as policy
from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.execution.process import NativeProcessSupervisor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4.test_v46_application import WATER_XYZ, _two_step_doc, _wrapper


def _snapshot(root: Path) -> dict[str, str]:
    """Return a digest for every file under *root*, keyed by relative path."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _request(run_root: Path, executable: Path) -> V4RunRequest:
    """Build the two-step water request for *run_root*."""
    return V4RunRequest(
        workflow_document=_two_step_doc(),
        run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)})),
        run_root=str(run_root),
        executables=FrozenDict({"orca": str(executable)}),
    )


def _mount_as_nfs4(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    """Report *run_root* as mounted on nfs4 in the mount table."""
    monkeypatch.setattr(
        policy, "mount_table", lambda: [("/", "ext4"), (str(run_root.resolve()), "nfs4")]
    )


def test_refused_rerun_leaves_completed_run_unchanged(monkeypatch, tmp_path: Path):
    """A completed run rerun on nfs4 is refused without touching any of its files."""
    wrapper = _wrapper(tmp_path, monkeypatch, "done")
    run_root = tmp_path / "run"
    request = _request(run_root, wrapper)
    first = V4RunApplication(supervisor=NativeProcessSupervisor()).run(request)
    assert first.status == "completed"

    tracked = [
        "run_result.json",
        "run_state.json",
        "run_generation.json",
        "generation_ownership.json",
    ]
    before_bytes = {name: (run_root / name).read_bytes() for name in tracked}
    before_files = _snapshot(run_root)

    _mount_as_nfs4(monkeypatch, run_root)
    with pytest.raises(policy.StorageUnsupportedError, match="nfs4"):
        V4RunApplication(supervisor=NativeProcessSupervisor()).run(request)

    assert {name: (run_root / name).read_bytes() for name in tracked} == before_bytes
    assert _snapshot(run_root) == before_files


def test_fresh_run_root_on_nfs4_is_refused_empty(monkeypatch, tmp_path: Path):
    """A fresh run root on nfs4 is refused and left with no files."""
    wrapper = _wrapper(tmp_path, monkeypatch, "fresh")
    run_root = tmp_path / "fresh-run"
    run_root.mkdir()
    _mount_as_nfs4(monkeypatch, run_root)

    with pytest.raises(policy.StorageUnsupportedError, match="nfs4"):
        V4RunApplication(supervisor=NativeProcessSupervisor()).run(_request(run_root, wrapper))

    assert list(run_root.iterdir()) == []
