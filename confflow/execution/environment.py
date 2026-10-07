#!/usr/bin/env python3

"""V4 execution-environment measurement.

Digest says *where* a computation ran: program, full executable content,
and the COMPLETE effective native env; endpoint locators, absolute paths,
file stat, scheduler width, and presentation facts are digest-inert audit
provenance only. Executable identity is the FULL content hash with no prefix
truncation; stat is provenance plus cache invalidation only. The measurer
cache is stat-gated and re-validated on every read, so mid-run replacement
is re-measured not aliased; in-place rewrites preserving all stat are out
of scope, call ``measure_executable`` to bypass the cache. ``relevant_env``
is the single-authority ``effective_native_env`` snapshot; launch and hashed
env are the SAME mapping, so any inherited variable change/delete can never
reuse a stale result. Pure executors use ``build_pure_environment`` with no
native binary; unknown measurements never equal verified ones (fail-closed nonce).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.errors import DomainError
from .contracts import ExecutionEnvironment
from .native import ProgramAdapter

__all__ = [
    "EXECUTION_ENVIRONMENT_METADATA_KEY",
    "ExecutableIdentity",
    "EnvironmentMeasurer",
    "build_pure_environment",
    "measure_executable",
    "select_relevant_env",
]

#: Metadata key carrying the verified worker-measured execution environment
#: on an imported remote result, for the batch commit path.
EXECUTION_ENVIRONMENT_METADATA_KEY: Final[str] = "remote_execution_environment"

_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    """Measured file identity of one executable.

    ``sha256`` is the FULL content hash: every byte participates, so no
    truncated-prefix alias class exists. ``size_bytes``/``mtime_ns``/
    ``device``/``inode`` are provenance plus cache-invalidation keys only
    and never enter any digest.
    """

    requested: str
    resolved_path: str
    real_path: str
    size_bytes: int
    mtime_ns: int
    device: int
    inode: int
    sha256: str
    program_version: str | None = None
    probe_metadata: dict[str, object] | None = None

    @property
    def digest(self) -> str:
        """Return the full-content digest in ``sha256:<hex>`` form."""
        return f"sha256:{self.sha256}"

    def stat_key(self) -> tuple[str, int, int, int, int]:
        """Return the cache-invalidation key for this measurement."""
        return (
            self.real_path,
            self.device,
            self.inode,
            self.size_bytes,
            self.mtime_ns,
        )


def _validate_executable_candidate(candidate: str) -> str:
    if not isinstance(candidate, str) or not candidate.strip():
        raise DomainError("executable must be a non-empty string")
    text = candidate.strip()
    if "\x00" in text:
        raise DomainError("executable must not contain NUL")
    shell_characters = (";", "&", "|", "`", "$", "(", ")", "<", ">", "\n", "\\")
    if any(char in text for char in shell_characters):
        raise DomainError(f"executable {text!r} contains shell metacharacters")
    return text


def _stat_key_of(real_path: str) -> tuple[str, int, int, int, int]:
    """Return the current stat key of *real_path*, failing closed."""
    try:
        stat = os.stat(real_path)
    except OSError as exc:
        raise DomainError(f"cannot stat executable {real_path!r}: {exc}") from exc
    return (real_path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def measure_executable(
    candidate: str,
    *,
    adapter: ProgramAdapter | None = None,
    max_hash_bytes: int | None = None,
) -> ExecutableIdentity:
    """Measure the full file identity of *candidate*.

    Every byte of the file is hashed: there is no prefix cap and no
    stat contribution to the content digest. ``max_hash_bytes`` is
    retained for call compatibility only and is IGNORED (prefix
    truncation was removed as an alias hazard); passing it changes
    nothing.

    Parameters
    ----------
    candidate : str
        Absolute path or bare executable name resolved via ``PATH``.
    adapter : ProgramAdapter | None
        Adapter consulted for a safe program-version probe.
    max_hash_bytes : int | None
        Deprecated, ignored. Full content is always hashed.

    Raises
    ------
    DomainError
        Raised when the executable cannot be resolved to a regular file.
    """
    text = _validate_executable_candidate(candidate)
    if os.path.isabs(text):
        resolved = text
    else:
        resolved = shutil.which(text) or ""
    if not resolved or not os.path.isfile(resolved):
        raise DomainError(f"executable {candidate!r} did not resolve to a file")
    try:
        real_path = os.path.realpath(resolved)
        stat = os.stat(real_path)
    except OSError as exc:
        raise DomainError(f"cannot stat executable {candidate!r}: {exc}") from exc
    digest = hashlib.sha256()
    try:
        with open(real_path, "rb") as handle:
            while True:
                chunk = handle.read(_HASH_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise DomainError(f"cannot hash executable {candidate!r}: {exc}") from exc
    program_version: str | None = None
    probe_metadata: dict[str, object] | None = None
    if adapter is not None:
        try:
            probe = adapter.environment_probe(resolved)
        except Exception:
            probe = {}
        if isinstance(probe, dict) and probe:
            version = probe.get("program_version")
            program_version = str(version) if version is not None else None
            probe_metadata = {str(key): value for key, value in probe.items()}
    return ExecutableIdentity(
        requested=candidate,
        resolved_path=resolved,
        real_path=real_path,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        sha256=digest.hexdigest(),
        program_version=program_version,
        probe_metadata=probe_metadata,
    )


def select_relevant_env(full_env: Mapping[str, Any], *, declared: Iterable[str]) -> dict[str, str]:
    """Select the declared scientifically relevant subset of *full_env*."""
    if not isinstance(full_env, Mapping):
        raise DomainError("full_env must be a mapping")
    names = tuple(declared)
    for name in names:
        if not isinstance(name, str) or not name or name != name.strip():
            raise DomainError("declared relevance names must be non-empty, trimmed strings")
    selected: dict[str, str] = {}
    for name in names:
        if name not in full_env:
            continue
        value = full_env[name]
        if not isinstance(value, str):
            raise DomainError(
                f"relevant env var {name!r} must be a string, got {type(value).__name__}"
            )
        selected[name] = value
    return selected


def build_pure_environment(
    *,
    implementation: str,
    implementation_version: str,
    relevant_env: Mapping[str, str] | None = None,
    metadata: Mapping[str, object] | None = None,
) -> ExecutionEnvironment:
    """Build the environment identity of a pure (non-native) executor.

    No native executable is measured or required: identity is the real
    implementation plus its version plus the declared relevant subset.
    Callers (application, remote worker) MUST pass the actual registered
    implementation versions (executor contract versions), never literals:
    an implementation change must move the digest so stale pure results
    can never reuse.

    Parameters
    ----------
    implementation : str
        Executor implementation identity (e.g. ``"confflow.confgen"``).
    implementation_version : str
        Real implementation/contract version of the executor.
    relevant_env : Mapping[str, str] | None
        Declared scientifically relevant explicit environment, or ``None``.
    metadata : Mapping[str, object] | None
        Operational provenance (never digested).
    """
    if not isinstance(implementation, str) or not implementation.strip():
        raise DomainError("implementation must be a non-empty string")
    if not isinstance(implementation_version, str) or not implementation_version.strip():
        raise DomainError("implementation_version must be a non-empty string")
    relevant = dict(relevant_env) if relevant_env is not None else {}
    for key, value in relevant.items():
        if not isinstance(key, str) or not key.strip():
            raise DomainError("relevant_env keys must be non-empty strings")
        if not isinstance(value, str):
            raise DomainError("relevant_env values must be strings")
    return ExecutionEnvironment(
        program=implementation.strip(),
        program_version=implementation_version.strip(),
        executable_digest=None,
        relevant_env=FrozenDict(relevant),
        metadata=FrozenDict(dict(metadata) if metadata is not None else {}),
    )


class EnvironmentMeasurer:
    """Stat-gated cached executable measurement for one batch execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str | None], ExecutableIdentity] = {}

    def measure(
        self, candidate: str, *, adapter: ProgramAdapter | None = None
    ) -> ExecutableIdentity:
        """Return the cached identity of *candidate* for *adapter*.

        A cached entry is re-validated against fresh stat on every read:
        binary replacement (changed dev/ino/size/mtime or a vanished file)
        is re-measured or fails closed instead of aliasing the stale
        identity. In-place rewrites preserving every stat field are
        undetectable without rehashing and are out of scope; call
        :func:`measure_executable` to bypass the cache.
        """
        adapter_name = adapter.program_name.value if adapter is not None else None
        key = (candidate, adapter_name)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            try:
                fresh = _stat_key_of(cached.real_path)
            except DomainError:
                fresh = None
            if fresh is not None and fresh == cached.stat_key():
                return cached
        identity = measure_executable(candidate, adapter=adapter)
        with self._lock:
            self._cache[key] = identity
        return identity

    def build_environment(
        self,
        candidate: str,
        *,
        adapter: ProgramAdapter,
        target: str | None = None,
        relevant_env: Mapping[str, str] | None = None,
    ) -> ExecutionEnvironment:
        """Measure *candidate* and build its execution environment."""
        identity = self.measure(candidate, adapter=adapter)
        metadata: dict[str, object] = {
            "adapter_version": adapter.adapter_version,
            "parser_version": adapter.parser_version,
            "resolved_path": identity.resolved_path,
            "real_path": identity.real_path,
        }
        if identity.probe_metadata:
            try:
                FrozenDict({"probe": identity.probe_metadata})
            except DomainError:
                metadata["probe_unusable"] = True
            else:
                metadata["probe"] = identity.probe_metadata
        relevant = dict(relevant_env) if relevant_env is not None else {}
        return ExecutionEnvironment(
            program=adapter.program_name.value,
            program_version=identity.program_version,
            executable_digest=identity.digest,
            relevant_env=FrozenDict(relevant),
            target=target,
            metadata=FrozenDict(metadata),
        )
