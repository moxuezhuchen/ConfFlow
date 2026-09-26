#!/usr/bin/env python3

"""Artifact integrity, retention planning, and GC application (V4-3).

Covers :mod:`confflow.persistence.artifacts`: checksum pass/fail, missing
files, locator escapes (``..`` / absolute / symlink-outside), external-URI
pass-through, protection sets, retention-class eligibility, deterministic
plan order, dry-run purity, apply semantics (files only, idempotent), and
module hygiene (no legacy symbols, stdlib plus domain imports only).
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
    apply_gc,
    plan_gc,
    verify_artifact,
)
from confflow.persistence.contracts import GCEntry, GCPlan, PersistenceError

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


class TestPlanEligibility:
    """Retention class plus consumed/run-complete decides eligibility."""

    def test_temporary_consumed_planned_with_class_reason(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "tmp.bin", _DATA)
        ref = _local_ref("tmp-1", "tmp.bin", retention=RetentionClass.TEMPORARY)
        plan = plan_gc(run_root=run_root, candidates=(ref,), consumed_ids=frozenset({"tmp-1"}))
        assert plan.artifact_ids == ("tmp-1",)
        entry = plan.entries[0]
        assert "temporary" in entry.reason
        assert entry.locator_path == os.path.join(run_root, "tmp.bin")
        assert entry.size_bytes == len(_DATA)

    def test_temporary_unconsumed_not_planned(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "tmp.bin", _DATA)
        ref = _local_ref("tmp-1", "tmp.bin", retention=RetentionClass.TEMPORARY)
        plan = plan_gc(run_root=run_root, candidates=(ref,), run_complete=True)
        assert plan.entries == ()

    def test_intermediate_needs_run_complete(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "mid.bin", _DATA)
        ref = _local_ref("mid-1", "mid.bin", retention=RetentionClass.INTERMEDIATE)
        consumed = frozenset({"mid-1"})
        assert plan_gc(run_root=run_root, candidates=(ref,), consumed_ids=consumed).entries == ()
        assert plan_gc(run_root=run_root, candidates=(ref,), run_complete=True).entries == ()
        plan = plan_gc(
            run_root=run_root,
            candidates=(ref,),
            run_complete=True,
            consumed_ids=consumed,
        )
        assert plan.artifact_ids == ("mid-1",)
        assert "intermediate" in plan.entries[0].reason

    def test_retained_never_planned(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "keep.bin", _DATA)
        ref = _local_ref("keep-1", "keep.bin", retention=RetentionClass.RETAINED)
        plan = plan_gc(
            run_root=run_root,
            candidates=(ref,),
            run_complete=True,
            consumed_ids=frozenset({"keep-1"}),
        )
        assert plan.entries == ()

    @pytest.mark.parametrize(
        "guard", ["protected_ids", "resume_required_ids", "downstream_required_ids"]
    )
    def test_protection_sets_never_planned(self, tmp_path, guard: str) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "prot.bin", _DATA)
        ref = _local_ref("prot-1", "prot.bin", retention=RetentionClass.TEMPORARY)
        plan = plan_gc(
            run_root=run_root,
            candidates=(ref,),
            run_complete=True,
            consumed_ids=frozenset({"prot-1"}),
            **{guard: frozenset({"prot-1"})},
        )
        assert plan.entries == ()

    def test_deterministic_candidate_order(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        refs = tuple(
            _local_ref(
                artifact_id,
                f"{artifact_id}.bin",
                retention=RetentionClass.TEMPORARY,
            )
            for artifact_id in ("zeta", "alpha", "mid")
        )
        for ref in refs:
            _write_bytes(run_root, f"{ref.id}.bin", _DATA)
        plan = plan_gc(
            run_root=run_root,
            candidates=refs,
            consumed_ids=frozenset({"zeta", "alpha", "mid"}),
        )
        assert plan.artifact_ids == ("zeta", "alpha", "mid")

    def test_dry_run_changes_nothing(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        before = {
            name: _write_bytes(run_root, name, _DATA + name.encode()) for name in ("a.bin", "b.bin")
        }
        refs = tuple(
            _local_ref(f"art-{name}", name, _DATA + name.encode(), RetentionClass.TEMPORARY)
            for name in ("a.bin", "b.bin")
        )
        plan = plan_gc(
            run_root=run_root,
            candidates=refs,
            consumed_ids=frozenset({"art-a.bin", "art-b.bin"}),
        )
        assert len(plan.entries) == 2
        for name, path in before.items():
            assert os.path.isfile(path)
            with open(path, "rb") as handle:
                assert handle.read() == _DATA + name.encode()

    def test_missing_candidate_skipped_silently(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        ref = _local_ref("ghost", "gone.bin", retention=RetentionClass.TEMPORARY)
        plan = plan_gc(run_root=run_root, candidates=(ref,), consumed_ids=frozenset({"ghost"}))
        assert plan.entries == ()

    def test_external_candidate_skipped(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        ref = ArtifactRef(
            id="ext-1",
            role="input",
            locator=ArtifactLocator.external_uri("s3://bucket/key/blob.chk"),
            retention=RetentionClass.TEMPORARY,
        )
        plan = plan_gc(
            run_root=run_root,
            candidates=(ref,),
            run_complete=True,
            consumed_ids=frozenset({"ext-1"}),
        )
        assert plan.entries == ()


class TestApplyGc:
    """Apply deletes planned files only, is idempotent, and spares dirs."""

    def _planned_run(self, tmp_path) -> tuple[str, GCPlan]:
        """Build a run with one collectable, one retained, one protected file."""
        run_root = str(tmp_path / "run")
        payloads = {
            "collect.bin": b"collect-me",
            "retain.bin": b"keep-me",
            "shield.bin": b"shield-me",
        }
        for name, data in payloads.items():
            _write_bytes(run_root, name, data)
        candidates = (
            _local_ref("gc-collect", "collect.bin", b"collect-me", RetentionClass.TEMPORARY),
            _local_ref("gc-retain", "retain.bin", b"keep-me", RetentionClass.RETAINED),
            _local_ref("gc-shield", "shield.bin", b"shield-me", RetentionClass.TEMPORARY),
        )
        plan = plan_gc(
            run_root=run_root,
            candidates=candidates,
            protected_ids=frozenset({"gc-shield"}),
            consumed_ids=frozenset({"gc-collect", "gc-retain", "gc-shield"}),
        )
        assert plan.artifact_ids == ("gc-collect",)
        return run_root, plan

    def test_removes_only_planned_files(self, tmp_path) -> None:
        run_root, plan = self._planned_run(tmp_path)
        removed, failed = apply_gc(run_root=run_root, plan=plan)
        assert removed == ("gc-collect",)
        assert failed == ()
        assert not os.path.lexists(os.path.join(run_root, "collect.bin"))
        assert os.path.isfile(os.path.join(run_root, "retain.bin"))
        assert os.path.isfile(os.path.join(run_root, "shield.bin"))

    def test_reapply_is_idempotent(self, tmp_path) -> None:
        run_root, plan = self._planned_run(tmp_path)
        first_removed, first_failed = apply_gc(run_root=run_root, plan=plan)
        assert first_removed == ("gc-collect",)
        assert first_failed == ()
        removed, failed = apply_gc(run_root=run_root, plan=plan)
        assert removed == ("gc-collect",)
        assert failed == ()

    def test_never_deletes_directories(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        target_dir = os.path.join(run_root, "artifacts-dir")
        os.makedirs(target_dir, exist_ok=True)
        plan = GCPlan(
            entries=(
                GCEntry(
                    artifact_id="dir-art",
                    reason="temporary: every direct consumer finished",
                    locator_path=target_dir,
                    size_bytes=None,
                ),
            )
        )
        removed, failed = apply_gc(run_root=run_root, plan=plan)
        assert removed == ()
        assert failed == ("dir-art",)
        assert os.path.isdir(target_dir)

    def test_escape_entry_fails_and_spares_outside(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        os.makedirs(run_root, exist_ok=True)
        outside = tmp_path / "outside.bin"
        outside.write_bytes(_DATA)
        plan = GCPlan(
            entries=(
                GCEntry(
                    artifact_id="escape",
                    reason="temporary: every direct consumer finished",
                    locator_path=str(outside),
                    size_bytes=len(_DATA),
                ),
            )
        )
        removed, failed = apply_gc(run_root=run_root, plan=plan)
        assert removed == ()
        assert failed == ("escape",)
        assert outside.is_file()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits required")
    @pytest.mark.skipif(
        getattr(os, "geteuid", lambda: -1)() == 0,
        reason="root bypasses file permissions",
    )
    def test_permission_failure_reports_failed(self, tmp_path) -> None:
        run_root = str(tmp_path / "run")
        _write_bytes(run_root, "locked/blob.bin", b"locked-bytes")
        ref = _local_ref(
            "locked-art",
            "locked/blob.bin",
            b"locked-bytes",
            RetentionClass.TEMPORARY,
        )
        plan = plan_gc(
            run_root=run_root,
            candidates=(ref,),
            consumed_ids=frozenset({"locked-art"}),
        )
        assert plan.artifact_ids == ("locked-art",)
        locked_dir = os.path.join(run_root, "locked")
        os.chmod(locked_dir, 0o555)
        try:
            removed, failed = apply_gc(run_root=run_root, plan=plan)
        finally:
            os.chmod(locked_dir, 0o755)
        assert removed == ()
        assert failed == ("locked-art",)
        assert os.path.isfile(os.path.join(locked_dir, "blob.bin"))


class TestModuleHygiene:
    """The module stays minimal: no legacy symbols, stdlib plus domain only."""

    def test_no_legacy_delete_work_dir_symbol(self) -> None:
        source = inspect.getsource(artifacts_module)
        assert "delete_work_dir" not in source

    def test_minimal_public_surface(self) -> None:
        assert set(artifacts_module.__all__) == {
            "ArtifactIntegrityError",
            "apply_gc",
            "plan_gc",
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
