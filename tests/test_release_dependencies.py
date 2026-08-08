"""Tests for the fail-closed runtime lock and binary wheelhouse validator."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test compatibility
    import tomli as tomllib

from confflow.install_provenance import sha256_hex
from confflow.release_dependencies import (
    DependencyInputError,
    parse_dependency_lock,
    parse_wheelhouse_manifest,
    validate_dependency_inputs,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_LOCK = REPO_ROOT / "release" / "confflow-1.5.3-py312-linux-x86_64.lock"
REAL_MANIFEST = REPO_ROOT / "release" / "confflow-1.5.3-py312-linux-x86_64.SHA256SUMS"

RUNTIME_IDENTITY = {
    "python_version": "3.12.3",
    "python_implementation": "CPython",
    "platform": "linux-x86_64",
    "machine": "x86_64",
}


def _lock(path: Path, entries: list[tuple[str, str, str]]) -> None:
    lines = ["--only-binary=:all:"]
    lines.extend(f"{name}=={version} --hash=sha256:{digest}" for name, version, digest in entries)
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
    assert evidence.wheelhouse_manifest_sha256 == sha256_hex(wheelhouse / "SHA256SUMS")
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


def test_real_runtime_lock_covers_control_dependency_closure():
    """The production lock must include control imports and their closure."""
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        re.split(r"[<>=!~ ]", requirement, maxsplit=1)[0].replace("_", "-").lower()
        for requirement in project["project"]["dependencies"]
    }
    direct_control = {"jsonschema", "referencing", "rfc8785"}
    transitive_control = {"attrs", "jsonschema-specifications", "rpds-py"}
    locked = parse_dependency_lock(REAL_LOCK)
    manifest = parse_wheelhouse_manifest(REAL_MANIFEST)

    assert direct_control <= declared
    assert direct_control | transitive_control <= set(locked)
    assert {
        "jsonschema-4.26.0-py3-none-any.whl",
        "jsonschema_specifications-2025.9.1-py3-none-any.whl",
        "referencing-0.37.0-py3-none-any.whl",
        "rfc8785-0.1.4-py3-none-any.whl",
        "attrs-26.1.0-py3-none-any.whl",
        "rpds_py-2026.6.3-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    } <= set(manifest)


def test_real_runtime_manifest_preserves_multitag_pillow_filename():
    """The committed wheelhouse manifest must match pip's full wheel name."""
    manifest = parse_wheelhouse_manifest(REAL_MANIFEST)

    assert "pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl" in manifest
    assert "pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.whl" not in manifest
