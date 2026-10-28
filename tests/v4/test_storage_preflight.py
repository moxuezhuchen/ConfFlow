"""Application-level storage preflight for V4 run roots.

A run root on a filesystem that cannot honour the WAL policy must be refused
before generation ownership, run state, or publication files are written.
A previously completed run must stay byte-identical when a rerun is refused.
"""

from __future__ import annotations

import hashlib
import os
import shutil
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


_TRACKED = [
    "run_result.json",
    "run_state.json",
    "run_generation.json",
    "generation_ownership.json",
]


def _tree(root: Path, *, exclude: Path) -> dict[str, str]:
    """Return a digest for every regular file under *root* except *exclude*."""
    digests: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            if full == exclude:
                continue
            digests[str(full.relative_to(root))] = hashlib.sha256(full.read_bytes()).hexdigest()
    return digests


def _completed_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Complete the two-step water run locally and return its request and root."""
    wrapper = _wrapper(tmp_path, monkeypatch, "linked")
    run_root = tmp_path / "run"
    request = _request(run_root, wrapper)
    first = V4RunApplication(supervisor=NativeProcessSupervisor()).run(request)
    assert first.status == "completed"
    return request, run_root


def _assert_rerun_refused_unchanged(
    request: V4RunRequest, run_root: Path, workspace: Path, link: Path
) -> None:
    """Assert a refused rerun leaves every run file and the generation untouched."""
    assert link.is_symlink()
    before_bytes = {name: (run_root / name).read_bytes() for name in _TRACKED}
    before_tree = _tree(workspace, exclude=link)

    with pytest.raises(policy.StorageUnsupportedError):
        V4RunApplication(supervisor=NativeProcessSupervisor()).run(request)

    assert {name: (run_root / name).read_bytes() for name in _TRACKED} == before_bytes
    assert _tree(workspace, exclude=link) == before_tree


def test_rerun_refused_when_step_directory_links_to_nfs4(monkeypatch, tmp_path: Path):
    """A step directory symlinked into an nfs4 directory refuses a rerun unchanged."""
    request, run_root = _completed_run(monkeypatch, tmp_path)
    network = tmp_path / "network"
    step = run_root / "steps" / "s_opt"
    shutil.move(str(step), str(network))
    step.symlink_to(network, target_is_directory=True)
    monkeypatch.setattr(
        policy, "mount_table", lambda: [("/", "ext4"), (str(network.resolve()), "nfs4")]
    )

    _assert_rerun_refused_unchanged(request, run_root, tmp_path, step)


def test_rerun_refused_when_step_database_is_symlink(monkeypatch, tmp_path: Path):
    """A step database replaced by a symlink refuses a rerun unchanged."""
    request, run_root = _completed_run(monkeypatch, tmp_path)
    database = run_root / "steps" / "s_opt" / "work_items.sqlite"
    copy = tmp_path / "copy.sqlite"
    shutil.copy2(database, copy)
    database.unlink()
    database.symlink_to(copy)

    _assert_rerun_refused_unchanged(request, run_root, tmp_path, database)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_rerun_refused_when_step_side_file_is_symlink(monkeypatch, tmp_path: Path, suffix: str):
    """A SQLite side file that is a symlink refuses a rerun unchanged."""
    request, run_root = _completed_run(monkeypatch, tmp_path)
    database = run_root / "steps" / "s_opt" / "work_items.sqlite"
    side = database.with_name(database.name + suffix)
    target = tmp_path / f"copy{suffix}"
    target.write_bytes(b"sidecar")
    side.symlink_to(target)

    _assert_rerun_refused_unchanged(request, run_root, tmp_path, side)


def test_fresh_run_root_on_nfs4_is_not_created(monkeypatch, tmp_path: Path):
    """A fresh run root on nfs4 is refused before the root directory is created."""
    wrapper = _wrapper(tmp_path, monkeypatch, "not-created")
    run_root = tmp_path / "fresh" / "run"
    _mount_as_nfs4(monkeypatch, run_root)

    with pytest.raises(policy.StorageUnsupportedError, match="nfs4"):
        V4RunApplication(supervisor=NativeProcessSupervisor()).run(_request(run_root, wrapper))

    assert not run_root.exists()
