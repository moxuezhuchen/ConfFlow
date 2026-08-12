"""Boundary tests for the external control worker's security helpers."""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from confflow import control_worker
from confflow.worker_handoff import (
    ensure_directory,
    load_handoff,
    stage_file,
    stage_worker_inputs,
    verify_prepared_handoff,
)
from confflow.worker_security import (
    _canonical_json,
    _file_digest,
    _read_json_file,
    _safe_absolute_path,
    _sha256_bytes,
    _validate_attempt_root,
    _validate_path,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="worker security boundary requires POSIX ownership and modes"
)


def test_control_worker_keeps_legacy_security_aliases() -> None:
    assert control_worker._canonical_json is _canonical_json
    assert control_worker._file_digest is _file_digest
    assert control_worker._read_json_file is _read_json_file
    assert control_worker._safe_absolute_path is _safe_absolute_path
    assert control_worker._sha256_bytes is _sha256_bytes
    assert control_worker._validate_attempt_root is _validate_attempt_root
    assert control_worker._validate_path is _validate_path
    assert control_worker._load_handoff is load_handoff
    assert control_worker._stage_worker_inputs is stage_worker_inputs
    assert control_worker._stage_file is stage_file
    assert control_worker._ensure_directory is ensure_directory
    assert control_worker._verify_prepared_handoff is verify_prepared_handoff


def test_control_worker_entrypoint_only_orchestrates_security_components() -> None:
    """The process entrypoint must not grow a second handoff implementation."""
    source = inspect.getsource(control_worker.run_control_worker)
    tree = ast.parse(source)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert {"_load_handoff", "_verify_prepared_handoff", "_stage_worker_inputs"} <= called_names
    assert not called_names & {
        "_canonical_json",
        "_file_digest",
        "_read_json_file",
        "_safe_absolute_path",
        "_validate_attempt_root",
        "_validate_path",
        "_stage_file",
        "_ensure_directory",
    }
    assert "os.open(" not in source
    assert "hashlib" not in source


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("relative/path", "absolute POSIX path"),
        (r"/tmp\escape", "absolute POSIX path"),
        ("/tmp/../escape", "parent traversal"),
        (123, "absolute POSIX path"),
    ],
)
def test_safe_absolute_path_rejects_non_posix_and_traversal(
    value: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _safe_absolute_path(value, "worker.path")

    assert _safe_absolute_path("/tmp/worker//input.xyz", "worker.path") == "/tmp/worker/input.xyz"


def test_validate_attempt_root_rejects_broadly_writable_parent(tmp_path: Path) -> None:
    attempt_root = tmp_path / "attempt"
    state_root = attempt_root / "state"
    attempt_root.mkdir(mode=0o700)
    state_root.mkdir(mode=0o700)
    os.chmod(attempt_root, 0o770)
    root = SimpleNamespace(path=state_root)

    with pytest.raises(ValueError, match="owner-controlled and non-writable"):
        _validate_attempt_root(root)

    os.chmod(attempt_root, 0o700)
    assert _validate_attempt_root(root) == attempt_root.resolve()


def test_validate_path_rejects_escape_symlink_wrong_kind_and_writable_paths(
    tmp_path: Path,
) -> None:
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir(mode=0o700)
    os.chmod(attempt_root, 0o700)
    input_path = attempt_root / "input.xyz"
    input_path.write_text("input", encoding="utf-8")
    os.chmod(input_path, 0o600)
    outside_path = tmp_path / "outside.xyz"
    outside_path.write_text("outside", encoding="utf-8")
    os.chmod(outside_path, 0o600)

    with pytest.raises(ValueError, match="below the worker attempt root"):
        _validate_path(outside_path, attempt_root, "input", kind="file")
    with pytest.raises(ValueError, match="must not be the attempt root"):
        _validate_path(attempt_root, attempt_root, "root", kind="directory")
    with pytest.raises(ValueError, match="does not exist"):
        _validate_path(attempt_root / "missing", attempt_root, "missing", kind="file")
    assert _validate_path(
        attempt_root / "new", attempt_root, "new", kind="file", allow_missing=True
    ) == attempt_root / "new"
    with pytest.raises(ValueError, match="must be a directory"):
        _validate_path(input_path, attempt_root, "input", kind="directory")

    link = attempt_root / "link.xyz"
    link.symlink_to(outside_path)
    with pytest.raises(ValueError, match="non-symlink"):
        _validate_path(link, attempt_root, "link", kind="file")

    writable = attempt_root / "writable.xyz"
    writable.write_text("writable", encoding="utf-8")
    os.chmod(writable, 0o620)
    with pytest.raises(ValueError, match="group/world-writable"):
        _validate_path(writable, attempt_root, "writable", kind="file")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"{", "not valid UTF-8 JSON"),
        (b"\xff", "not valid UTF-8 JSON"),
        (b"[]", "must be an object"),
    ],
)
def test_read_json_file_rejects_invalid_utf8_json_and_non_objects(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / "handoff.json"
    path.write_bytes(content)
    os.chmod(path, 0o600)

    with pytest.raises(ValueError, match=message):
        _read_json_file(path)


def test_read_json_file_rejects_writable_and_symlinked_files(tmp_path: Path) -> None:
    path = tmp_path / "handoff.json"
    path.write_text('{"ok": true}', encoding="utf-8")
    os.chmod(path, 0o620)
    with pytest.raises(ValueError, match="non-writable regular file"):
        _read_json_file(path)

    os.chmod(path, 0o600)
    link = tmp_path / "handoff-link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="cannot securely open"):
        _read_json_file(link)


def test_digest_helpers_detect_changes_and_reject_non_finite_json(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"original")
    expected = _sha256_bytes(b"original")
    assert _file_digest(str(path)) == expected

    path.write_bytes(b"tampered")
    assert _file_digest(str(path)) != expected

    assert _canonical_json({"b": 2, "a": "x"}) == b'{"a":"x","b":2}'
    with pytest.raises(ValueError, match="Out of range float values"):
        _canonical_json({"value": float("nan")})
    with pytest.raises(TypeError):
        _sha256_bytes("not bytes")  # type: ignore[arg-type]
