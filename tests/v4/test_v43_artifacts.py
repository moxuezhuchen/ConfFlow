#!/usr/bin/env python3

"""Artifact integrity verification (V4-3).

Covers :mod:`confflow.persistence.artifacts`: checksum pass/fail, missing
files, locator escapes (``..`` / absolute / symlink-outside), external-URI
pass-through, and module hygiene (no legacy symbols, stdlib plus domain
imports only).
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import os

import pytest

from confflow.domain.artifact import ArtifactLocator, ArtifactRef, LocatorKind
from confflow.domain.retention import RetentionClass
from confflow.persistence import artifacts as artifacts_module
from confflow.persistence.artifacts import (
    ArtifactIntegrityError,
    verify_artifact,
)
from confflow.persistence.contracts import PersistenceError

_DATA = b"confflow-artifact-bytes-0123456789"


def _sha256(data: bytes) -> str:
    """Return the ``sha256:<hex>`` checksum of *data*."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_bytes(run_root: str, relative: str, data: bytes) -> str:
    """Write *data* under *run_root* at *relative*; return the absolute path."""
    path = os.path.join(run_root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _local_ref(
    artifact_id: str,
    relative: str,
    data: bytes = _DATA,
    retention: RetentionClass = RetentionClass.RETAINED,
) -> ArtifactRef:
    """Build a run-relative ref whose checksum matches *data*."""
    return ArtifactRef(
        id=artifact_id,
        role="output",
        locator=ArtifactLocator.run_relative(relative),
        checksum=_sha256(data),
        retention=retention,
    )


def _forged_run_relative(path: str) -> ArtifactLocator:
    """Forge a run-relative locator bypassing domain validation.

    Domain construction already rejects escapes; forging exercises the
    persistence layer's own containment checks.
    """
    locator = object.__new__(ArtifactLocator)
    object.__setattr__(locator, "kind", LocatorKind.RUN_RELATIVE)
    object.__setattr__(locator, "path", path)
    object.__setattr__(locator, "uri", None)
    return locator


def _forged_ref(artifact_id: str, path: str, data: bytes = _DATA) -> ArtifactRef:
    """Build a ref with a forged (possibly escaping) locator."""
    return ArtifactRef(
        id=artifact_id,
        role="output",
        locator=_forged_run_relative(path),
        checksum=_sha256(data),
    )


class TestVerifyChecksum:
    """Streaming checksum verification passes clean bytes, fails tampering."""

    def test_pass_valid_file(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "steps/s1/artifacts/out.chk", _DATA)
        ref = _local_ref("chk-1", "steps/s1/artifacts/out.chk")
        verify_artifact(run_root=run_root, ref=ref)

    def test_pass_without_checksum_checks_existence(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "plain.bin", _DATA)
        ref = ArtifactRef(
            id="plain",
            role="output",
            locator=ArtifactLocator.run_relative("plain.bin"),
        )
        verify_artifact(run_root=run_root, ref=ref)

    def test_fail_flipped_byte_names_artifact(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        tampered = b"X" + _DATA[1:]
        _write_bytes(run_root, "out.chk", tampered)
        ref = _local_ref("chk-flip", "out.chk", _DATA)
        with pytest.raises(ArtifactIntegrityError) as excinfo:
            verify_artifact(run_root=run_root, ref=ref)
        assert "chk-flip" in str(excinfo.value)

    def test_error_is_persistence_error(self) -> None:
        assert issubclass(ArtifactIntegrityError, PersistenceError)


class TestVerifyMissingAndSize:
    """Missing files and size mismatches fail closed."""

    def test_fail_missing_file(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        ref = _local_ref("ghost", "no/such/file.bin")
        with pytest.raises(ArtifactIntegrityError, match="ghost"):
            verify_artifact(run_root=run_root, ref=ref)

    def test_fail_size_mismatch(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "sized.bin", _DATA)
        ref = _local_ref("sized", "sized.bin")
        with pytest.raises(ArtifactIntegrityError, match="sized"):
            verify_artifact(run_root=run_root, ref=ref, expected_size_bytes=len(_DATA) + 1)

    def test_pass_matching_size(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "sized.bin", _DATA)
        ref = _local_ref("sized", "sized.bin")
        verify_artifact(run_root=run_root, ref=ref, expected_size_bytes=len(_DATA))


class TestVerifyEscapes:
    """Absolute paths, ``..`` segments, and symlink-outside fail closed."""

    def test_fail_dotdot_locator(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        ref = _forged_ref("dotdot", "../evil.bin")
        with pytest.raises(ArtifactIntegrityError, match="dotdot"):
            verify_artifact(run_root=run_root, ref=ref)

    def test_fail_absolute_locator(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        ref = _forged_ref("absolute", str(tmp_path / "abs.bin"))
        with pytest.raises(ArtifactIntegrityError, match="absolute"):
            verify_artifact(run_root=run_root, ref=ref)

    @pytest.mark.skipif(os.name != "posix", reason="symlink test requires POSIX")
    def test_fail_symlink_outside(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        outside = tmp_path / "outside.bin"
        outside.write_bytes(_DATA)
        os.symlink(str(outside), os.path.join(run_root, "link.bin"))
        ref = _local_ref("link-out", "link.bin")
        with pytest.raises(ArtifactIntegrityError, match="link-out"):
            verify_artifact(run_root=run_root, ref=ref)

    @pytest.mark.skipif(os.name != "posix", reason="symlink test requires POSIX")
    def test_pass_symlink_inside(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "real.bin", _DATA)
        os.symlink(os.path.join(run_root, "real.bin"), os.path.join(run_root, "link.bin"))
        ref = _local_ref("link-in", "link.bin")
        verify_artifact(run_root=run_root, ref=ref)


class TestVerifyExternalUri:
    """External URIs get a shape check only and never touch the filesystem."""

    def test_external_uri_passes_without_file(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        ref = ArtifactRef(
            id="ext-1",
            role="input",
            locator=ArtifactLocator.external_uri("s3://bucket/key/output.chk"),
        )
        verify_artifact(run_root=run_root, ref=ref)


class TestModuleHygiene:
    """The module stays minimal: no legacy symbols, stdlib plus domain only."""

    def test_no_legacy_delete_work_dir_symbol(self) -> None:
        source = inspect.getsource(artifacts_module)
        assert "delete_work_dir" not in source

    def test_minimal_public_surface(self) -> None:
        assert set(artifacts_module.__all__) == {
            "ArtifactIntegrityError",
            "verify_artifact",
        }

    def test_stdlib_plus_domain_imports_only(self) -> None:
        tree = ast.parse(inspect.getsource(artifacts_module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] != "sqlite3"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    assert module in ("contracts", "domain") or module.startswith("domain.")
                elif module == "confflow" or module.startswith("confflow."):
                    assert (
                        module == "confflow.domain"
                        or module.startswith("confflow.domain.")
                        or module.startswith("confflow.persistence.")
                    )
                    assert not module.startswith("confflow.execution")
                    assert not module.startswith("confflow.workflow")
                    assert not module.startswith("confflow.calc")
                    assert not module.startswith("confflow.core")
