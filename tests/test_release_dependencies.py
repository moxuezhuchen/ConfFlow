"""Tests for the fail-closed runtime lock and binary wheelhouse validator."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from confflow.install_provenance import sha256_hex
from confflow.release_dependencies import (
    DependencyInputError,
    validate_dependency_inputs,
)

RUNTIME_IDENTITY = {
    "python_version": "3.12.3",
    "python_implementation": "CPython",
    "platform": "linux-x86_64",
    "machine": "x86_64",
}


def _lock(path: Path, entries: list[tuple[str, str, str]]) -> None:
    lines = ["--only-binary=:all:"]
    lines.extend(
        f"{name}=={version} --hash=sha256:{digest}"
        for name, version, digest in entries
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _manifest(path: Path, entries: list[tuple[str, str]]) -> None:
    path.write_text(
        "".join(f"{digest}  {filename}\n" for filename, digest in entries),
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, filename: str = "demo-1.0-py3-none-any.whl"):
    lock_path = tmp_path / "runtime.lock"
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / filename
    wheel.write_bytes(b"binary-wheel-fixture")
    digest = sha256_hex(wheel)
    _lock(lock_path, [("demo", "1.0", digest)])
    _manifest(wheelhouse / "SHA256SUMS", [(filename, digest)])
    return lock_path, wheelhouse, wheel, digest


def _validate(lock_path: Path, wheelhouse: Path):
    return validate_dependency_inputs(
        lock_path,
        wheelhouse,
        runtime_identity=RUNTIME_IDENTITY,
    )


def test_missing_wheel_is_rejected(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _lock(lock_path, [("demo", "1.0", "a" * 64)])
    _manifest(wheelhouse / "SHA256SUMS", [])

    with pytest.raises(DependencyInputError, match="missing locked dependency"):
        _validate(lock_path, wheelhouse)


def test_extra_wheel_is_rejected(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "extra-1.0-py3-none-any.whl"
    wheel.write_bytes(b"extra")
    digest = sha256_hex(wheel)
    _lock(lock_path, [])
    _manifest(wheelhouse / "SHA256SUMS", [(wheel.name, digest)])

    with pytest.raises(DependencyInputError, match="extra dependency wheel"):
        _validate(lock_path, wheelhouse)


def test_tampered_wheel_is_rejected(tmp_path):
    lock_path, wheelhouse, wheel, digest = _fixture(tmp_path)
    wheel.write_bytes(b"tampered-wheel")
    _manifest(wheelhouse / "SHA256SUMS", [(wheel.name, digest)])

    with pytest.raises(DependencyInputError, match="SHA256 mismatch"):
        _validate(lock_path, wheelhouse)


def test_lock_hash_mismatch_is_rejected(tmp_path):
    lock_path, wheelhouse, wheel, digest = _fixture(tmp_path)
    _lock(lock_path, [("demo", "1.0", "b" * 64)])
    _manifest(wheelhouse / "SHA256SUMS", [(wheel.name, digest)])

    with pytest.raises(DependencyInputError, match="not present in dependency lock"):
        _validate(lock_path, wheelhouse)


def test_sdist_is_rejected(tmp_path):
    lock_path = tmp_path / "runtime.lock"
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _lock(lock_path, [])
    _manifest(wheelhouse / "SHA256SUMS", [])
    (wheelhouse / "demo-1.0.tar.gz").write_bytes(b"sdist")

    with pytest.raises(DependencyInputError, match="non-binary-wheel"):
        _validate(lock_path, wheelhouse)


def test_wrong_platform_wheel_is_rejected(tmp_path):
    filename = "demo-1.0-cp311-cp311-win_amd64.whl"
    lock_path, wheelhouse, wheel, digest = _fixture(tmp_path, filename)
    _manifest(wheelhouse / "SHA256SUMS", [(filename, digest)])

    with pytest.raises(DependencyInputError, match="not compatible"):
        _validate(lock_path, wheelhouse)


def test_duplicate_manifest_row_is_rejected(tmp_path):
    lock_path, wheelhouse, wheel, digest = _fixture(tmp_path)
    (wheelhouse / "SHA256SUMS").write_text(
        f"{digest}  {wheel.name}\n{digest}  {wheel.name}\n",
        encoding="utf-8",
    )

    with pytest.raises(DependencyInputError, match="duplicate filename"):
        _validate(lock_path, wheelhouse)


def test_manifest_and_lock_digests_are_returned(tmp_path):
    lock_path, wheelhouse, _wheel, _digest = _fixture(tmp_path)
    evidence = _validate(lock_path, wheelhouse)

    assert evidence.dependency_lock_sha256 == sha256_hex(lock_path)
    assert evidence.wheelhouse_manifest_sha256 == sha256_hex(
        wheelhouse / "SHA256SUMS"
    )
    assert len(evidence.wheel_filenames) == 1


def test_wrong_runtime_platform_is_rejected(tmp_path):
    lock_path, wheelhouse, _wheel, _digest = _fixture(tmp_path)
    identity = dict(RUNTIME_IDENTITY, platform="linux-aarch64", machine="aarch64")

    with pytest.raises(DependencyInputError, match="Linux x86_64"):
        validate_dependency_inputs(
            lock_path,
            wheelhouse,
            runtime_identity=identity,
        )
