#!/usr/bin/env python3

"""Secure file protocol for worker-handoff V2 envelopes (V4-4).

This module moves one :class:`WorkerHandoffV2` envelope from the producer to
a worker sandbox through the filesystem.  The writer publishes
``<worker_root>/inbox/<launch_token>/handoff.json`` with canonical bytes via
an atomic temp-file, fsync, and rename publication; the reader re-opens that
path with ``O_NOFOLLOW``, checks owner-only regular-file metadata, applies a
bounded strict UTF-8 JSON decode, and lets the frozen pydantic model reject
unknown fields and re-verify the manifest digest.

Divergence from the legacy V1 envelope is deliberate: identity here is the
frozen ``HANDOFF_SCHEMA_V2`` string (never a per-run configuration document),
integrity is the model-verified manifest digest (never filename contracts),
and no legacy envelope keys are accepted, generated, or interpreted.

Import rule: stdlib plus ``confflow.domain.canonical`` and the sibling
``.envelope`` contracts only.  This module never imports executor, program,
workflow, persistence, or legacy runtimes at module load.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import stat
from typing import Final, NoReturn

from ..domain.canonical import canonical_json_bytes
from .envelope import HANDOFF_SCHEMA_V2, MAX_HANDOFF_BYTES, WorkerHandoffV2

__all__ = ["HandoffError", "read_handoff_envelope", "write_handoff_envelope"]


class HandoffError(ValueError):
    """Fail-closed error for any worker-handoff V2 file-protocol violation."""


_TOKEN_RE: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*")

_INBOX_NAME: Final = "inbox"

_HANDOFF_FILENAME: Final = "handoff.json"

_TMP_COUNTER = itertools.count()

_READ_CHUNK_SIZE: Final = 1024 * 1024

_GROUP_WORLD_BITS: Final = (
    stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH
)


def _checked_launch_token(launch_token: str) -> str:
    """Return *launch_token* after enforcing the lease-style charset.

    Parameters
    ----------
    launch_token : str
        Directory segment naming one work-item attempt delivery.

    Returns
    -------
    str
        The unchanged token when it is non-empty, starts with an ASCII
        letter or digit, and contains only ``[A-Za-z0-9._+-]`` (`+` carries sanitized `:` separators from work-item ids, which can never contain `+`).

    Raises
    ------
    HandoffError
        Raised when the token is empty, has an unsafe leading character,
        or contains characters outside the allowed set (in particular any
        path separator or parent-traversal segment).
    """
    if not isinstance(launch_token, str) or _TOKEN_RE.fullmatch(launch_token) is None:
        raise HandoffError(
            "launch_token must be non-empty, start with an ASCII letter or digit, "
            "and contain only [A-Za-z0-9._+-]"
        )
    return launch_token


def _checked_worker_root(worker_root: str) -> str:
    """Return the absolute *worker_root* after symlink and owner checks.

    Parameters
    ----------
    worker_root : str
        Producer-managed sandbox root that will own the inbox directory.

    Returns
    -------
    str
        The absolute form of *worker_root*.

    Raises
    ------
    HandoffError
        Raised when the root is empty, resolves to a symlink or a
        non-directory, or is not owned by the current user.  A missing root
        is returned as-is so the writer can create it privately.
    """
    if not isinstance(worker_root, str) or not worker_root:
        raise HandoffError("worker_root must be a non-empty string")
    absolute = os.path.abspath(worker_root)
    try:
        metadata = os.lstat(absolute)
    except FileNotFoundError:
        return absolute
    except OSError as exc:
        raise HandoffError(f"cannot inspect worker_root: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise HandoffError("worker_root must be a non-symlink directory")
    if metadata.st_uid != os.getuid():
        raise HandoffError("worker_root must be owner-owned")
    return absolute


def _ensure_private_dir(path: str, *, label: str) -> None:
    """Create *path* as an owner-private non-symlink directory.

    Parameters
    ----------
    path : str
        Directory to create (including parents) and lock to ``0o700``.
    label : str
        Human-readable name used in error messages.

    Raises
    ------
    HandoffError
        Raised when creation fails, the path is a symlink or non-directory,
        is not owner-owned, or cannot be chmodded to ``0o700``.
    """
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise HandoffError(f"cannot create {label} directory: {exc}") from exc
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise HandoffError(f"cannot inspect {label} directory: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise HandoffError(f"{label} directory must be a non-symlink directory")
    if metadata.st_uid != os.getuid():
        raise HandoffError(f"{label} directory must be owner-owned")
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise HandoffError(f"cannot secure {label} directory: {exc}") from exc


def _tmp_suffix() -> str:
    """Return a unique temp-file suffix for this process."""
    return f".tmp.{os.getpid()}.{next(_TMP_COUNTER)}"


def _atomic_write_bytes(target_path: str, payload: bytes) -> None:
    """Write *payload* to *target_path* atomically via temp file and rename.

    The temp file is created exclusively with mode ``0o600`` in the same
    directory, fsynced, renamed over the target with :func:`os.replace`,
    and followed by a directory fsync, mirroring the persistence
    publication protocol.

    Parameters
    ----------
    target_path : str
        Final destination path; the temp file lives in the same directory.
    payload : bytes
        Exact bytes to durably persist.

    Raises
    ------
    HandoffError
        Raised when the temp file cannot be created, written, or published.
    """
    directory = os.path.dirname(target_path)
    tmp_path = target_path + _tmp_suffix()
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
    except OSError as exc:
        raise HandoffError(f"cannot create handoff temp file: {exc}") from exc
    try:
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise HandoffError("handoff temp write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
    except HandoffError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise HandoffError(f"cannot write handoff envelope: {exc}") from exc
    try:
        os.replace(tmp_path, target_path)
    except OSError as exc:
        try:
            if os.path.lexists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise HandoffError(f"cannot publish handoff envelope: {exc}") from exc
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def write_handoff_envelope(*, handoff: WorkerHandoffV2, worker_root: str, launch_token: str) -> str:
    """Atomically publish *handoff* into the worker inbox.

    The envelope is serialized with canonical JSON bytes and published to
    ``<worker_root>/inbox/<launch_token>/handoff.json``; both directories
    are created owner-private (``0o700``) and symlink-safe, and the file is
    written with mode ``0o600`` through the atomic temp-file protocol.

    Parameters
    ----------
    handoff : WorkerHandoffV2
        Validated envelope to publish; its manifest digest is already fixed
        by construction.
    worker_root : str
        Sandbox root owning the inbox directory tree.
    launch_token : str
        Delivery token used as the inbox segment; must satisfy the
        lease-style charset (``[A-Za-z0-9._+-]``, leading ASCII alnum).

    Returns
    -------
    str
        Absolute path of the published ``handoff.json`` file.

    Raises
    ------
    HandoffError
        Raised when any argument is invalid, containment is violated, the
        payload cannot be serialized, or publication fails.
    """
    if not isinstance(handoff, WorkerHandoffV2):
        raise HandoffError("handoff must be a WorkerHandoffV2 envelope")
    root = _checked_worker_root(worker_root)
    token = _checked_launch_token(launch_token)
    inbox_dir = os.path.join(root, _INBOX_NAME)
    token_dir = os.path.join(inbox_dir, token)
    _ensure_private_dir(inbox_dir, label="inbox")
    _ensure_private_dir(token_dir, label="launch token")
    target = os.path.join(token_dir, _HANDOFF_FILENAME)
    if os.path.abspath(target) != target or not target.startswith(root + os.sep):
        raise HandoffError("handoff path escapes the worker root")
    try:
        payload = canonical_json_bytes(handoff.model_dump(mode="json"))
    except Exception as exc:
        raise HandoffError(f"cannot serialize handoff envelope: {exc}") from exc
    _atomic_write_bytes(target, payload)
    return target


def _reject_constant(value: str) -> NoReturn:
    """Reject non-standard JSON constants during strict parsing."""
    raise ValueError(f"non-standard JSON constant is not accepted: {value}")


def read_handoff_envelope(*, path: str, expected_run_id: str | None = None) -> WorkerHandoffV2:
    """Securely read and validate the V2 envelope stored at *path*.

    The file is opened with ``O_NOFOLLOW`` and checked with ``fstat`` (must
    be an owner-owned regular file with no group/world permission bits),
    read with a hard ``MAX_HANDOFF_BYTES`` bound, decoded as strict UTF-8
    JSON, and validated by the frozen strict model, which rejects unknown
    fields and re-verifies the manifest digest.

    Parameters
    ----------
    path : str
        Filesystem path of the ``handoff.json`` file to read.
    expected_run_id : str | None
        When given, the envelope ``run_id`` must equal this value.

    Returns
    -------
    WorkerHandoffV2
        The validated envelope.

    Raises
    ------
    HandoffError
        Raised for any failure: insecure path or metadata, oversized or
        unreadable payload, invalid JSON, strict-validation errors, schema
        identity mismatch, run-id mismatch, or digest mismatch.
    """
    if not isinstance(path, str) or not path:
        raise HandoffError("path must be a non-empty string")
    if expected_run_id is not None and (
        not isinstance(expected_run_id, str) or not expected_run_id
    ):
        raise HandoffError("expected_run_id must be a non-empty string when given")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise HandoffError(f"cannot securely open handoff envelope: {exc}") from exc
    try:
        try:
            metadata = os.fstat(fd)
        except OSError as exc:
            raise HandoffError(f"cannot inspect handoff envelope: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise HandoffError("handoff envelope must be a regular file")
        if metadata.st_uid != os.getuid():
            raise HandoffError("handoff envelope must be owner-owned")
        if metadata.st_mode & _GROUP_WORLD_BITS:
            raise HandoffError("handoff envelope must not carry group/world permissions")
        chunks: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = os.read(fd, _READ_CHUNK_SIZE)
            except OSError as exc:
                raise HandoffError(f"cannot read handoff envelope: {exc}") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_HANDOFF_BYTES:
                raise HandoffError("handoff envelope exceeds the maximum size")
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HandoffError("handoff envelope is not strict UTF-8") from exc
    try:
        payload = json.loads(text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise HandoffError(f"handoff envelope is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HandoffError("handoff envelope must be a JSON object")
    try:
        handoff = WorkerHandoffV2.model_validate(payload)
    except Exception as exc:
        raise HandoffError(f"handoff envelope failed strict validation: {exc}") from exc
    validated: WorkerHandoffV2 = handoff
    if validated.schema != HANDOFF_SCHEMA_V2:
        raise HandoffError("handoff envelope schema identity mismatch")
    if expected_run_id is not None and validated.run_id != expected_run_id:
        raise HandoffError("handoff envelope run identity does not match")
    return validated
