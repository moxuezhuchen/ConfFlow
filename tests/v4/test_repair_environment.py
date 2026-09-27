#!/usr/bin/env python3

"""Wave-1 environment-identity root fix (owner: environment lead).

Versioned identity rule v2 for ``confflow.execution.contracts`` +
``confflow.execution.environment``: full executable content identity and
declared scientifically relevant explicit env enter the digest; endpoint
target locators, absolute paths, file stat, and scheduler width are
operational provenance only. Unknown measurements fail closed and can
never satisfy reuse equality.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution.contracts import (
    ENVIRONMENT_DIGEST_KIND,
    ENVIRONMENT_DIGEST_KIND_V2,
    ExecutionEnvironment,
)
from confflow.execution.environment import (
    EnvironmentMeasurer,
    build_pure_environment,
    measure_executable,
    select_relevant_env,
)
from confflow.execution.native import ProgramName


class _StubAdapter:
    """Minimal program adapter surface for environment tests."""

    def __init__(self, program: ProgramName = ProgramName.ORCA) -> None:
        self._program = program

    @property
    def program_name(self) -> ProgramName:
        return self._program

    @property
    def adapter_version(self) -> str:
        return "confflow.contract.adapter.standard.v1"

    @property
    def parser_version(self) -> str:
        return "confflow.contract.parser.standard.v1"

    def environment_probe(self, executable: str) -> dict[str, Any]:
        return {}


def _write(path: Any, data: bytes, *, mtime_ns: int | None = None) -> str:
    path.write_bytes(data)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))
    return str(path)


def test_relocated_identical_content_equal_despite_mtime(tmp_path: Any) -> None:
    """Same bytes at another path with another mtime measure identically."""
    first = tmp_path / "node-a" / "prog"
    first.parent.mkdir()
    _write(first, b"binary-bytes\x00\x01", mtime_ns=1_700_000_000_000_000_000)
    second = tmp_path / "node-b" / "prog"
    second.parent.mkdir()
    _write(second, b"binary-bytes\x00\x01", mtime_ns=1_800_000_000_000_000_000)
    measurer = EnvironmentMeasurer()
    adapter = _StubAdapter()
    assert (
        measurer.build_environment(str(first), adapter=adapter).digest()
        == measurer.build_environment(str(second), adapter=adapter).digest()
    )
    assert measure_executable(str(first)).digest == measure_executable(str(second)).digest


def test_same_size_changed_content_invalidates_same_path(tmp_path: Any) -> None:
    """Same-size replacement under the same requested path moves the digest."""
    target = tmp_path / "prog"
    _write(target, b"A" * 1024, mtime_ns=1_700_000_000_000_000_000)
    measurer = EnvironmentMeasurer()
    before = measurer.measure(str(target))
    _write(target, b"B" * 1024, mtime_ns=1_700_000_001_000_000_000)
    after = measurer.measure(str(target))
    assert before.digest != after.digest
    # Direct (uncached) measurement agrees: no cache alias involved.
    assert measure_executable(str(target)).digest == after.digest
    # The digest is the full content hash.
    assert after.digest == f"sha256:{hashlib.sha256(b'B' * 1024).hexdigest()}"


def test_tail_change_beyond_historical_prefix_cap(tmp_path: Any) -> None:
    """A changed tail past 512MiB moves the digest (no prefix alias)."""
    size = 520 * 1024 * 1024
    target = tmp_path / "huge"
    with open(target, "wb") as handle:
        handle.truncate(size)
        handle.seek(size - 16)
        handle.write(b"tail-version-one")
    first = measure_executable(str(target)).digest
    with open(target, "r+b") as handle:
        handle.seek(size - 16)
        handle.write(b"tail-version-two")
    second = measure_executable(str(target)).digest
    assert first != second


def test_cache_detects_replacement_and_hits_when_stable(tmp_path: Any) -> None:
    """Stat-gated cache: stable reads hit, replacement re-measures."""
    target = tmp_path / "prog"
    _write(target, b"v1", mtime_ns=1_700_000_000_000_000_000)
    measurer = EnvironmentMeasurer()
    first = measurer.measure(str(target))
    assert measurer.measure(str(target)) is first
    _write(target, b"v1", mtime_ns=1_700_000_005_000_000_000)
    touched = measurer.measure(str(target))
    assert touched is not first  # stat moved: re-measured, not aliased
    assert touched.digest == first.digest  # identical content: same identity
    _write(target, b"v2", mtime_ns=1_700_000_009_000_000_000)
    assert measurer.measure(str(target)).digest != first.digest


def test_relevant_env_change_invalidates(tmp_path: Any) -> None:
    """Declared scientifically relevant env participates in identity."""
    target = tmp_path / "prog"
    _write(target, b"x")
    measurer = EnvironmentMeasurer()
    adapter = _StubAdapter()
    one = measurer.build_environment(
        str(target), adapter=adapter, relevant_env={"OMP_NUM_THREADS": "1"}
    )
    eight = measurer.build_environment(
        str(target), adapter=adapter, relevant_env={"OMP_NUM_THREADS": "8"}
    )
    assert one.digest() != eight.digest()
    assert (
        eight.digest()
        == measurer.build_environment(
            str(target), adapter=adapter, relevant_env={"OMP_NUM_THREADS": "8"}
        ).digest()
    )


def test_operational_env_never_enters_digest(tmp_path: Any) -> None:
    """Undeclared operational variables, target, and scheduler are inert."""
    target = tmp_path / "prog"
    _write(target, b"x")
    measurer = EnvironmentMeasurer()
    adapter = _StubAdapter()
    base = measurer.build_environment(
        str(target),
        adapter=adapter,
        target="node-1",
        relevant_env={"OMP_NUM_THREADS": "4"},
    )
    moved = measurer.build_environment(
        str(target),
        adapter=adapter,
        target="node-2",
        relevant_env={"OMP_NUM_THREADS": "4"},
    )
    assert base.digest() == moved.digest()
    # Full binding env differs operationally but the declared subset is
    # identical: selecting first keeps identity stable.
    full_a = {"PATH": "/a", "HOME": "/root", "OMP_NUM_THREADS": "4"}
    full_b = {"PATH": "/b", "HOME": "/other", "OMP_NUM_THREADS": "4"}
    assert select_relevant_env(full_a, declared=("OMP_NUM_THREADS",)) == (
        select_relevant_env(full_b, declared=("OMP_NUM_THREADS",))
    )
    scheduled = measurer.build_environment(
        str(target),
        adapter=adapter,
        target="node-1",
        relevant_env={"OMP_NUM_THREADS": "4"},
    )
    assert scheduled.digest() == base.digest()


def test_pure_implementation_change_invalidates() -> None:
    """Pure executor identity tracks real implementation versions."""
    first = build_pure_environment(
        implementation="confflow.confgen",
        implementation_version="confflow.contract.executor.confgen.v1",
    )
    assert first.digest().startswith("sha256:")
    assert (
        first.digest()
        == build_pure_environment(
            implementation="confflow.confgen",
            implementation_version="confflow.contract.executor.confgen.v1",
        ).digest()
    )
    changed = build_pure_environment(
        implementation="confflow.confgen",
        implementation_version="confflow.contract.executor.confgen.v2",
    )
    assert changed.digest() != first.digest()
    renamed = build_pure_environment(
        implementation="confflow.transform",
        implementation_version="confflow.contract.executor.confgen.v1",
    )
    assert renamed.digest() != first.digest()
    # Pure and native identities never collide silently.
    native_like = ExecutionEnvironment(
        program="confflow.confgen",
        program_version="confflow.contract.executor.confgen.v1",
        executable_digest="sha256:" + "0" * 64,
    )
    assert native_like.digest() != first.digest()


def test_unknown_never_passes_strict_reuse() -> None:
    """Unmeasured identity fails closed against verified and unknown."""
    from confflow.persistence.contracts import ReuseCode, StoredWorkItemStatus
    from confflow.persistence.reuse import ReuseInputs, evaluate_reuse

    provenance = FrozenDict(
        {
            "adapter_version": "a",
            "profile_version": "p",
            "check_versions": {},
            "recovery_version": "r",
            "canonicalization_id": "c",
        }
    )
    verified = ExecutionEnvironment(
        program="orca",
        program_version="6.1.1",
        executable_digest="sha256:" + "1" * 64,
    ).digest()
    unknown_one = ExecutionEnvironment.unknown("orca", reason="probe failed").digest()
    unknown_two = ExecutionEnvironment.unknown("orca", reason="probe failed").digest()
    assert unknown_one != verified
    assert unknown_one != unknown_two

    def decide(env_current: str, env_stored: str) -> Any:
        return evaluate_reuse(
            current=ReuseInputs(
                work_item_digest="sha256:" + "a" * 64,
                step_semantic_digest="sha256:" + "b" * 64,
                environment_digest=env_current,
                producer_provenance=provenance,
                artifact_checksums=(),
            ),
            stored=ReuseInputs(
                work_item_digest="sha256:" + "a" * 64,
                step_semantic_digest="sha256:" + "b" * 64,
                environment_digest=env_stored,
                producer_provenance=provenance,
                artifact_checksums=(),
            ),
            stored_status=StoredWorkItemStatus.COMPLETED,
            work_item_id="wi:s:all",
        ).decision

    assert decide(unknown_one, verified) is ReuseCode.INVALIDATE_ENVIRONMENT
    assert decide(verified, unknown_one) is ReuseCode.INVALIDATE_ENVIRONMENT
    assert decide(unknown_one, unknown_two) is ReuseCode.INVALIDATE_ENVIRONMENT


def test_unknown_validation() -> None:
    """Unknown construction and verified-canonical rules fail closed."""
    with pytest.raises(DomainError):
        ExecutionEnvironment.unknown("", reason="x")
    with pytest.raises(DomainError):
        ExecutionEnvironment.unknown("orca", reason="  ")
    with pytest.raises(DomainError):
        ExecutionEnvironment(program="orca", measurement_status="guessed")
    with pytest.raises(DomainError):
        ExecutionEnvironment(program="orca", measurement_status="verified", unknown_nonce="abc")
    with pytest.raises(DomainError):
        ExecutionEnvironment(program="orca", relevant_env={"K": 1})  # type: ignore[dict-item]
    env = ExecutionEnvironment.unknown("orca", reason="no probe")
    assert not env.is_verified
    assert env.to_dict()["measurement_status"] == "unknown"
    assert ExecutionEnvironment(program="orca").is_verified


def test_identity_rule_version() -> None:
    """v3 rule marker is explicit; v1/v2 payloads can never equal v3 digests."""
    assert ENVIRONMENT_DIGEST_KIND == "confflow.execution_environment.v3"
    assert ENVIRONMENT_DIGEST_KIND_V2 == "confflow.execution_environment.v2"
    assert ExecutionEnvironment(program="orca").digest().startswith("sha256:")
    assert (
        ExecutionEnvironment(program="orca", target="node-1").digest()
        == ExecutionEnvironment(program="orca", target="node-9").digest()
    )


def test_verified_digest_golden() -> None:
    """Pinned v3 digest guards against canonicalization churn."""
    env = ExecutionEnvironment(
        program="gaussian",
        program_version="16.C.01",
        executable_digest="sha256:" + "b" * 64,
        relevant_env=FrozenDict({"OMP_NUM_THREADS": "1"}),
        target="node-1",
    )
    assert env.digest() == (
        "sha256:fe11dd0b8ffbb4af0a8844a69d9e3abd736cf907b4b3c625841211543495b586"
    )
