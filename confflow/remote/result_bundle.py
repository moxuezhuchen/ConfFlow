#!/usr/bin/env python3

"""V4 remote result bundles: package and validate worker outputs (V4-4).

The remote worker executes the same :class:`WorkItemExecutor` path as local
execution and then funnels everything the producer needs through one typed
directory: ``result.json`` (a frozen :class:`ResultBundle` envelope) plus a
``files/`` sidecar directory holding the produced artifact bytes.  The
producer side imports this directory without ever trusting worker-local
paths: every file is checksummed here, and the envelope digest covers the
result payload and artifact identity.

Security posture mirrors the handoff boundary: result files are written
atomically with ``fsync``, and reads use the same ``O_NOFOLLOW`` /
owner-only / size-bounded discipline.  This module imports the frozen
envelope plus the standard library only; resolvers for adapters, profiles,
checks, and recovery live on the worker side, never here.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from .envelope import (
    ResultBundle,
    ResultProducedArtifact,
    WorkerHandoffV2,
    canonical_envelope_bytes,
)

if TYPE_CHECKING:
    from ..domain.work_item import WorkItemResult

__all__ = [
    "MAX_RESULT_BUNDLE_BYTES",
    "ResultBundleError",
    "package_result_bundle",
    "read_result_bundle",
]

#: Upper bound for a result-bundle envelope file; larger payloads fail closed.
MAX_RESULT_BUNDLE_BYTES: Final[int] = 64 * 1024 * 1024

#: Envelope file name inside a packaged result directory.
_RESULT_FILE_NAME: Final[str] = "result.json"

#: Sidecar directory holding produced artifact bytes inside a result directory.
_FILES_DIR_NAME: Final[str] = "files"

#: Streaming chunk size for checksums and copies.
_CHUNK_SIZE: Final[int] = 1024 * 1024


class ResultBundleError(ValueError):
    """Report a result-bundle packaging or validation failure."""


def package_result_bundle(
    *,
    work_item_result: WorkItemResult,
    handoff: WorkerHandoffV2,
    result_dir: str,
    environment: dict[str, Any],
    transfer_files_from: str,
) -> str:
    """Package one executed work-item result into a result-bundle directory.

    Parameters
    ----------
    work_item_result : WorkItemResult
        The executor result to publish, with artifacts discovered by the
        program adapter.
    handoff : WorkerHandoffV2
        The validated handoff envelope this work item was launched from;
        its identity fields are echoed into the bundle.
    result_dir : str
        Directory receiving ``result.json`` plus the ``files/`` sidecar.
    environment : dict[str, Any]
        Worker-side environment identity recorded in the bundle.
    transfer_files_from : str
        Worker-local directory holding the produced artifact bytes, keyed
        by produced file name.

    Returns
    -------
    str
        Absolute path of the written ``result.json`` envelope.

    Raises
    ------
    ResultBundleError
        Raised when inputs are malformed, a produced file is missing or
        fails its checksum, or any write cannot be completed.
    """
    try:
        return _package_result_bundle(
            work_item_result=work_item_result,
            handoff=handoff,
            result_dir=result_dir,
            environment=environment,
            transfer_files_from=transfer_files_from,
        )
    except ResultBundleError:
        raise
    except Exception as exc:
        raise ResultBundleError(f"failed to package result bundle: {exc}") from exc


def read_result_bundle(*, path: str) -> ResultBundle:
    """Read and strictly validate one result-bundle envelope.

    Parameters
    ----------
    path : str
        Path of the ``result.json`` envelope to read.

    Returns
    -------
    ResultBundle
        The validated bundle; its digest is recomputed by the model.

    Raises
    ------
    ResultBundleError
        Raised when the file cannot be securely read, is oversized or
        malformed, or fails strict model validation (including digest).
    """
    if not isinstance(path, str) or not path:
        raise ResultBundleError("result bundle path must be a non-empty string")
    raw = _secure_read_bytes(path=path, max_bytes=MAX_RESULT_BUNDLE_BYTES, label="result bundle")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResultBundleError(f"result bundle is not valid UTF-8 JSON: {path}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResultBundleError(f"result bundle is malformed: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResultBundleError(f"result bundle must be a JSON object: {path}")
    try:
        return ResultBundle(**payload)
    except Exception as exc:
        raise ResultBundleError(f"result bundle validation failed: {exc}") from exc


def _package_result_bundle(
    *,
    work_item_result: WorkItemResult,
    handoff: WorkerHandoffV2,
    result_dir: str,
    environment: dict[str, Any],
    transfer_files_from: str,
) -> str:
    """Implement :func:`package_result_bundle` with typed errors."""
    if not isinstance(result_dir, str) or not result_dir:
        raise ResultBundleError("result_dir must be a non-empty string")
    if not isinstance(transfer_files_from, str) or not transfer_files_from:
        raise ResultBundleError("transfer_files_from must be a non-empty string")
    if not os.path.isdir(transfer_files_from):
        raise ResultBundleError(f"transfer directory is not usable: {transfer_files_from}")
    if not isinstance(environment, Mapping):
        raise ResultBundleError("environment must be a mapping")
    environment_map = dict(environment)
    try:
        result_payload = work_item_result.to_dict()
    except Exception as exc:
        raise ResultBundleError(f"work item result is not serializable: {exc}") from exc
    if not isinstance(result_payload, dict):
        raise ResultBundleError("work item result payload must be a mapping")
    try:
        produced = tuple(work_item_result.artifacts)
    except Exception as exc:
        raise ResultBundleError(f"work item result artifacts are not readable: {exc}") from exc

    files_dir = os.path.join(os.path.abspath(result_dir), _FILES_DIR_NAME)
    os.makedirs(files_dir, exist_ok=True)
    rows = _transfer_produced_artifacts(
        produced=produced,
        transfer_files_from=transfer_files_from,
        files_dir=files_dir,
    )
    try:
        bundle = ResultBundle.new(
            run_id=handoff.run_id,
            step_id=handoff.step_id,
            work_item_id=handoff.work_item_id,
            attempt_number=handoff.attempt_number,
            launch_token=handoff.launch_token,
            work_item_digest=handoff.work_item_digest,
            environment=environment_map,
            result=result_payload,
            produced_artifacts=tuple(rows),
            transport_metadata={
                "worker_pid": os.getpid(),
                "finished_wall": time.time(),
            },
        )
    except Exception as exc:
        raise ResultBundleError(f"result bundle envelope is not constructible: {exc}") from exc
    result_path = os.path.join(os.path.abspath(result_dir), _RESULT_FILE_NAME)
    _atomic_write_bytes(result_path, canonical_envelope_bytes(bundle))
    return result_path


def _transfer_produced_artifacts(
    *,
    produced: tuple[Any, ...],
    transfer_files_from: str,
    files_dir: str,
) -> list[ResultProducedArtifact]:
    """Checksum, copy, and describe every produced artifact.

    Parameters
    ----------
    produced : tuple[Any, ...]
        Adapter-discovered artifact references of the executed result.
    transfer_files_from : str
        Worker-local directory holding the produced bytes by file name.
    files_dir : str
        Destination sidecar directory receiving ``files/<basename>`` copies.

    Returns
    -------
    list[ResultProducedArtifact]
        Bundle rows in deterministic artifact-id order.

    Raises
    ------
    ResultBundleError
        Raised when a produced file is missing, unreadable, fails its
        recorded checksum, or cannot be copied.
    """
    ordered = sorted(produced, key=lambda reference: reference.id)
    seen_names: set[str] = set()
    rows: list[ResultProducedArtifact] = []
    for reference in ordered:
        try:
            artifact_id = reference.id
            role = reference.role
            locator = reference.locator
        except Exception as exc:
            raise ResultBundleError(f"produced artifact reference is not readable: {exc}") from exc
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ResultBundleError("produced artifact id must be a non-empty string")
        if not isinstance(role, str) or not role:
            raise ResultBundleError(f"produced artifact {artifact_id!r} needs a role")
        relative = getattr(locator, "path", None)
        if not isinstance(relative, str) or not relative:
            raise ResultBundleError(
                f"produced artifact {artifact_id!r} has no transferable file locator"
            )
        basename = os.path.basename(relative)
        if not basename or basename in (".", ".."):
            raise ResultBundleError(f"produced artifact {artifact_id!r} has an unusable file name")
        if basename in seen_names:
            raise ResultBundleError(f"produced artifacts collide on file name {basename!r}")
        seen_names.add(basename)
        source = os.path.join(transfer_files_from, basename)
        if not os.path.isfile(source):
            raise ResultBundleError(
                f"produced file for artifact {reference.id!r} is missing: {basename!r}"
            )
        destination = os.path.join(files_dir, basename)
        checksum, size_bytes = _copy_file_atomic(source, destination)
        recorded = reference.checksum
        if recorded is not None and recorded != f"sha256:{checksum}":
            raise ResultBundleError(
                f"produced file for artifact {reference.id!r} fails its checksum"
            )
        try:
            rows.append(
                ResultProducedArtifact(
                    artifact_id=reference.id,
                    role=reference.role,
                    checksum=f"sha256:{checksum}",
                    size_bytes=size_bytes,
                    subject_structure_id=reference.subject_structure_id,
                    media_type=reference.media_type,
                    bundle_locator=f"{_FILES_DIR_NAME}/{basename}",
                )
            )
        except Exception as exc:
            raise ResultBundleError(
                f"produced artifact {reference.id!r} is not describable: {exc}"
            ) from exc
    return rows


def _copy_file_atomic(source: str, destination: str) -> tuple[str, int]:
    """Copy *source* to *destination* atomically while checksumming.

    Parameters
    ----------
    source : str
        Existing regular file to copy.
    destination : str
        Final path receiving the bytes via a temporary plus rename.

    Returns
    -------
    tuple[str, int]
        Lowercase hex SHA-256 of the copied bytes and their size.

    Raises
    ------
    ResultBundleError
        Raised when the copy cannot be completed durably.
    """
    tmp_path = f"{destination}.tmp-{os.getpid()}"
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with open(source, "rb") as handle_in:
            out_fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                while True:
                    chunk = handle_in.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size_bytes += len(chunk)
                    _write_all(out_fd, chunk)
                os.fsync(out_fd)
            finally:
                os.close(out_fd)
        os.replace(tmp_path, destination)
        _fsync_dir(os.path.dirname(os.path.abspath(destination)))
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise ResultBundleError(f"cannot copy produced file {source!r}: {exc}") from exc
    return digest.hexdigest(), size_bytes


def _write_all(fd: int, chunk: bytes) -> None:
    """Write one chunk fully to an open descriptor.

    Parameters
    ----------
    fd : int
        Open writable file descriptor.
    chunk : bytes
        Bytes to write without short writes.
    """
    view = memoryview(chunk)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _atomic_write_bytes(path: str, data: bytes) -> None:
    """Write *data* to *path* atomically with durability barriers.

    Parameters
    ----------
    path : str
        Final file path receiving the bytes.
    data : bytes
        Payload bytes to persist.
    """
    tmp_path = f"{path}.tmp-{os.getpid()}"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, path)
        _fsync_dir(os.path.dirname(os.path.abspath(path)))
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise ResultBundleError(f"cannot write result file {path!r}: {exc}") from exc


def _fsync_dir(directory: str) -> None:
    """Flush one directory entry table to durable storage.

    Parameters
    ----------
    directory : str
        Directory whose listing must survive a crash.
    """
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _secure_read_bytes(*, path: str, max_bytes: int, label: str) -> bytes:
    """Read one owner-controlled regular file through a stable descriptor.

    Parameters
    ----------
    path : str
        File path to read without following symlinks.
    max_bytes : int
        Upper bound on accepted payload bytes.
    label : str
        Human-readable file kind used in error messages.

    Returns
    -------
    bytes
        Raw file content.

    Raises
    ------
    ResultBundleError
        Raised when the file is a symlink, is not an owner-owned regular
        file, is group/world-writable, or exceeds *max_bytes*.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise ResultBundleError(f"{label} cannot be securely opened: {path}: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ResultBundleError(f"{label} must be a regular file: {path}")
        if metadata.st_uid != os.getuid():
            raise ResultBundleError(f"{label} must be owner-owned: {path}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ResultBundleError(f"{label} must not be group/world-writable: {path}")
        if metadata.st_size > max_bytes:
            raise ResultBundleError(f"{label} exceeds the size bound: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, _CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ResultBundleError(f"{label} exceeds the size bound: {path}")
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)
