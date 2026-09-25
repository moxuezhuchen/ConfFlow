#!/usr/bin/env python3

"""V4 execution-environment measurement.

The environment digest is the independent axis that says *where* a
computation ran: program identity, executable file identity, adapter and
parser versions.  Absolute machine paths are provenance only and never enter
the digest — a relocated identical binary measures the same environment.

Measurement is cached per resolved executable so repeated items in one step
do not re-hash binaries.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
from dataclasses import dataclass

from ..domain._immutable import FrozenDict
from ..domain.errors import DomainError
from .contracts import ExecutionEnvironment
from .native import ProgramAdapter

__all__ = [
    "ExecutableIdentity",
    "EnvironmentMeasurer",
    "measure_executable",
]

_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    """Measured file identity of one executable."""

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
        """Return the content digest in ``sha256:<hex>`` form."""
        return f"sha256:{self.sha256}"


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


def measure_executable(
    candidate: str,
    *,
    adapter: ProgramAdapter | None = None,
    max_hash_bytes: int = 512 * 1024 * 1024,
) -> ExecutableIdentity:
    """Measure the file identity of *candidate*.

    Parameters
    ----------
    candidate : str
        Absolute path or bare executable name resolved via ``PATH``.
    adapter : ProgramAdapter | None
        Adapter consulted for a safe program-version probe.
    max_hash_bytes : int
        Upper bound on hashed prefix bytes; larger files hash their leading
        prefix plus their size, which still distinguishes builds.

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
    hashed = 0
    try:
        with open(real_path, "rb") as handle:
            while hashed < max_hash_bytes:
                chunk = handle.read(min(_HASH_CHUNK_BYTES, max_hash_bytes - hashed))
                if not chunk:
                    break
                digest.update(chunk)
                hashed += len(chunk)
    except OSError as exc:
        raise DomainError(f"cannot hash executable {candidate!r}: {exc}") from exc
    digest.update(f":{stat.st_size}:{stat.st_mtime_ns}".encode())
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


class EnvironmentMeasurer:
    """Cached executable measurement for one batch execution."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str | None], ExecutableIdentity] = {}

    def measure(
        self, candidate: str, *, adapter: ProgramAdapter | None = None
    ) -> ExecutableIdentity:
        """Return the cached identity of *candidate* for *adapter*."""
        adapter_name = adapter.program_name.value if adapter is not None else None
        key = (candidate, adapter_name)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
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
        return ExecutionEnvironment(
            program=adapter.program_name.value,
            program_version=identity.program_version,
            executable_digest=identity.digest,
            target=target,
            metadata=FrozenDict(metadata),
        )
