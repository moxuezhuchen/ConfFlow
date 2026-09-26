#!/usr/bin/env python3

"""Secure typed staging for the ConfFlow Workflow V4 remote boundary (V4-4).

Producer-side staging copies validated input bytes into a private worker
sandbox, and producer-side import copies validated worker-produced bytes back
into the run tree.  Both directions reuse the proven no-follow, owner-only,
atomic-replace mechanics of :mod:`confflow.worker_staging` (copied here, never
imported, so this package never touches the legacy worker stack at load).

Security properties (both directions):

- sources open with ``O_NOFOLLOW`` and must be owner-owned regular files;
- destination parents are private ``0o700`` directories pinned through
  directory file descriptors; symlinked ancestors below the trusted anchor
  fail closed;
- bytes stream through ``sha256`` with an expected digest (and an expected
  size when the envelope carries one); mismatches fail closed;
- publication is atomic (same-directory temporary file + ``fsync`` + dev/ino
  stability recheck + pre-replace destination recheck + ``os.replace`` +
  directory ``fsync``), and bytes are re-hashed after the copy;
- any failure raises :class:`StagingError` (a ``ValueError``) and
  best-effort removes this bundle's staged files so nothing partial remains.

Frozen-signature note: ``transport.py`` (frozen) calls
:func:`stage_input_bundle` without producer source paths, but the manifest
only carries content checksums, never producer paths.  The frozen design
resolves this with a ``source_files`` mapping (artifact id to producer
absolute source path) built by the caller from the artifact store.  Until the
transport owner threads that mapping through, ``source_files`` defaults to
``None`` and staging fails closed whenever the manifest carries artifact
entries; structure/result-only bundles stage without it.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
from dataclasses import dataclass
from typing import Any, Final

from pydantic import ValidationError

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.retention import RetentionClass
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import QuantityKind, Unit
from ..domain.work_item import RecoveryInfo, ResultError, Timing, WorkItemResult
from .envelope import InputBundleManifest, ResultBundle, WorkerHandoffV2

__all__ = [
    "StagedBundle",
    "StagedEntry",
    "StagingError",
    "import_result_artifacts",
    "stage_input_bundle",
]

_CHUNK_BYTES: Final[int] = 1024 * 1024
_MAX_RESULT_BYTES: Final[int] = 64 * 1024 * 1024
_TEMP_NAME_PREFIX: Final[str] = ".confflow-stage-"
_TEMP_TOKEN_BYTES: Final[int] = 16
_SAFE_COMPONENT_LIMIT: Final[int] = 200


class StagingError(DomainError, ValueError):
    """Fail-closed staging failure; nothing partial is left behind.

    Dual inheritance follows the domain convention (e.g.
    :class:`CanonicalizationError`): the error is a ``ValueError`` per the
    staging contract and a ``DomainError`` so fail-closed gates that catch
    the domain family accept it.
    """


@dataclass(frozen=True, slots=True)
class StagedEntry:
    """One staged bundle entry.

    Attributes
    ----------
    entry_id:
        Stable entry identity (artifact, structure, or result id).
    kind:
        Entry kind: ``"artifact"``, ``"structure"``, or ``"result"``.
    staged_path:
        Absolute staged file for artifact entries; ``None`` for
        structure/result entries whose payloads ride in the envelope.
    checksum_verified:
        ``True`` only when file bytes were hash-verified during staging.
    """

    entry_id: str
    kind: str
    staged_path: str | None
    checksum_verified: bool


@dataclass(frozen=True, slots=True)
class StagedBundle:
    """The staged input bundle handed to the remote worker.

    Attributes
    ----------
    entries:
        One entry per manifest entry, in manifest order.
    work_dir:
        Absolute private worker directory holding ``files/``.
    """

    entries: tuple[StagedEntry, ...]
    work_dir: str


def stage_input_bundle(
    *,
    manifest: InputBundleManifest,
    run_root: str,
    worker_root: str,
    launch_token: str,
    source_files: dict[str, str] | None = None,
) -> StagedBundle:
    """Stage one input bundle into the worker sandbox.

    Parameters
    ----------
    manifest:
        Typed input manifest from the handoff envelope.
    run_root:
        Producer run root (reserved for provenance; producer bytes arrive
        via *source_files*).
    worker_root:
        Worker sandbox root; the bundle lands in
        ``<worker_root>/work/<safe-token>/``.
    launch_token:
        Attempt launch token; sanitized into a single path component.
    source_files:
        Mapping of artifact id to producer absolute source path.  Required
        whenever *manifest* carries artifact entries; ``None`` (or a mapping
        missing an id) fails closed for artifact entries but still stages
        structure/result-only manifests.

    Returns
    -------
    StagedBundle
        Staged entries plus the private work directory.

    Raises
    ------
    StagingError
        Raised on any validation, security, checksum, or I/O failure.
    """
    if not isinstance(manifest, InputBundleManifest):
        raise StagingError("manifest must be an InputBundleManifest")
    if not isinstance(run_root, str) or not run_root:
        raise StagingError("run_root must be a non-empty string")
    if not isinstance(worker_root, str) or not worker_root:
        raise StagingError("worker_root must be a non-empty string")
    if source_files is None:
        source_files = {}
    if not isinstance(source_files, dict):
        raise StagingError("source_files must map artifact id to source path")
    owner = os.getuid()
    worker_anchor = os.path.abspath(worker_root)
    _check_anchor(worker_anchor, owner=owner, what="worker_root")
    safe_token = _safe_component(launch_token, "launch_token")
    work_dir = os.path.join(worker_anchor, "work", safe_token)
    _ensure_private_dir(work_dir, anchor=worker_anchor, owner=owner)
    _ensure_private_dir(os.path.join(work_dir, "files"), anchor=worker_anchor, owner=owner)

    staged: list[StagedEntry] = []
    seen_artifacts: set[str] = set()
    seen_locators: set[str] = set()
    try:
        for entry in manifest.entries:
            kind = entry.entry_kind
            if kind == "artifact":
                if entry.artifact_id in seen_artifacts:
                    raise StagingError(f"duplicate artifact entry: {entry.artifact_id!r}")
                seen_artifacts.add(entry.artifact_id)
                locator = _validate_relative_locator(entry.bundle_locator, "bundle_locator")
                if locator in seen_locators:
                    raise StagingError(f"duplicate bundle locator: {locator!r}")
                seen_locators.add(locator)
                source = source_files.get(entry.artifact_id)
                if not isinstance(source, str) or not source:
                    raise StagingError(
                        f"no producer source for artifact {entry.artifact_id!r}; pass source_files"
                    )
                destination = os.path.join(work_dir, locator)
                _secure_copy_file(
                    source_path=source,
                    dest_path=destination,
                    expected_checksum=entry.checksum,
                    expected_size=entry.size_bytes,
                    anchor=worker_anchor,
                    owner=owner,
                )
                _reverify_copy(destination, entry.checksum, entry.size_bytes, owner=owner)
                staged.append(
                    StagedEntry(
                        entry_id=entry.artifact_id,
                        kind="artifact",
                        staged_path=destination,
                        checksum_verified=True,
                    )
                )
            elif kind == "structure":
                staged.append(
                    StagedEntry(
                        entry_id=entry.structure_id,
                        kind="structure",
                        staged_path=None,
                        checksum_verified=False,
                    )
                )
            elif kind == "result":
                staged.append(
                    StagedEntry(
                        entry_id=entry.result_id,
                        kind="result",
                        staged_path=None,
                        checksum_verified=False,
                    )
                )
            else:  # pragma: no cover - pydantic forbids unknown kinds
                raise StagingError(f"unknown bundle entry kind: {kind!r}")
    except StagingError:
        _remove_tree_best_effort(work_dir, anchor=worker_anchor)
        raise
    except Exception as exc:
        _remove_tree_best_effort(work_dir, anchor=worker_anchor)
        raise StagingError(f"input bundle staging failed: {exc}") from exc
    return StagedBundle(entries=tuple(staged), work_dir=work_dir)


def import_result_artifacts(
    *,
    result_path: str,
    handoff: WorkerHandoffV2,
    run_root: str,
    store: Any,
) -> WorkItemResult:
    """Import one worker result bundle into the producer run tree.

    Parameters
    ----------
    result_path:
        Absolute path of the worker-written result bundle file.  Result
        bytes live next to it: ``<result_dir>/files/`` (or, for
        envelope-canonical locators, ``<result_dir>/<bundle_locator>``).
    handoff:
        The handoff envelope this result must answer to; schema, identity,
        attempt, launch token, and work-item digest must all agree.
    run_root:
        Producer run root; artifacts land in
        ``steps/<step>/remote/<safe-work-item>/<basename>``.
    store:
        Opaque attempt store; only ``get_attempts(work_item_id)`` is
        called, and the bundle attempt must equal the current attempt.

    Returns
    -------
    WorkItemResult
        The imported result with artifacts replaced by verified refs.
        Nothing is committed to the store; the batch layer commits.

    Raises
    ------
    StagingError
        Raised on any validation, security, checksum, attempt, or
        reconstruction failure.
    """
    if not isinstance(result_path, str) or not result_path:
        raise StagingError("result_path must be a non-empty string")
    if not isinstance(handoff, WorkerHandoffV2):
        raise StagingError("handoff must be a WorkerHandoffV2")
    if not isinstance(run_root, str) or not run_root:
        raise StagingError("run_root must be a non-empty string")
    owner = os.getuid()
    run_anchor = os.path.abspath(run_root)
    _check_anchor(run_anchor, owner=owner, what="run_root")
    result_abs = os.path.abspath(result_path)
    bundle = _read_result_bundle(result_abs, owner=owner)
    _check_bundle_identity(bundle, handoff)
    _check_attempt_current(store, handoff.work_item_id, bundle.attempt_number)
    result_dir = os.path.dirname(result_abs)

    refs: list[ArtifactRef] = []
    seen: set[str] = set()
    safe_step = _safe_component(handoff.step_id, "step_id")
    safe_item = _safe_component(handoff.work_item_id, "work_item_id")
    for produced in bundle.produced_artifacts:
        if produced.artifact_id in seen:
            raise StagingError(f"duplicate produced artifact: {produced.artifact_id!r}")
        seen.add(produced.artifact_id)
        worker_file = _resolve_worker_file(result_dir, produced.bundle_locator)
        actual_hex, actual_size = _hash_file_secure(
            worker_file, owner=owner, what=f"worker file {produced.artifact_id!r}"
        )
        expected_hex = _split_checksum(produced.checksum, "produced checksum")
        if actual_hex != expected_hex:
            raise StagingError(f"worker file changed for artifact {produced.artifact_id!r}")
        if produced.size_bytes is not None and actual_size != produced.size_bytes:
            raise StagingError(f"worker file size mismatch for {produced.artifact_id!r}")
        base = _validate_relative_locator(produced.bundle_locator, "bundle_locator").split("/")[-1]
        dest_dir = os.path.join(run_anchor, "steps", safe_step, "remote", safe_item)
        _ensure_private_dir(dest_dir, anchor=run_anchor, owner=owner)
        destination = os.path.join(dest_dir, base)
        _secure_copy_file(
            source_path=worker_file,
            dest_path=destination,
            expected_checksum=produced.checksum,
            expected_size=produced.size_bytes,
            anchor=run_anchor,
            owner=owner,
        )
        _reverify_copy(destination, produced.checksum, produced.size_bytes, owner=owner)
        relative = f"steps/{safe_step}/remote/{safe_item}/{base}"
        try:
            locator = ArtifactLocator.run_relative(relative)
        except Exception as exc:
            raise StagingError(f"imported locator is not portable: {relative!r}") from exc
        program = bundle.environment.get("program")
        program_name = program if isinstance(program, str) and program.strip() else None
        try:
            refs.append(
                ArtifactRef(
                    id=produced.artifact_id,
                    role=produced.role,
                    locator=locator,
                    checksum=produced.checksum,
                    media_type=produced.media_type,
                    program=program_name,
                    producer_step_id=handoff.step_id,
                    producer_work_item_id=handoff.work_item_id,
                    subject_structure_id=produced.subject_structure_id,
                    retention=RetentionClass.RETAINED,
                )
            )
        except Exception as exc:
            raise StagingError(
                f"invalid produced artifact {produced.artifact_id!r}: {exc}"
            ) from exc
    try:
        artifact_set = ArtifactSet(tuple(refs))
    except Exception as exc:
        raise StagingError(f"invalid imported artifact set: {exc}") from exc
    if not isinstance(bundle.result, dict):
        raise StagingError("result payload must be a mapping")
    return _rebuild_work_item_result(bundle.result, handoff=handoff, artifacts=artifact_set)


def _safe_component(value: str, field_name: str) -> str:
    """Return *value* sanitized to a single portable path component."""
    if not isinstance(value, str) or not value:
        raise StagingError(f"{field_name} must be a non-empty string")
    if "\x00" in value:
        raise StagingError(f"{field_name} must not contain NUL")
    safe = "".join(char if char.isalnum() or char in ("-", "_", ".") else "_" for char in value)
    safe = safe.lstrip(".")
    if not safe or safe in (".", ".."):
        raise StagingError(f"{field_name} has no portable representation")
    if len(safe) > _SAFE_COMPONENT_LIMIT:
        digest = hashlib.sha256(safe.encode("utf-8")).hexdigest()[:32]
        safe = f"{safe[:100]}_{digest}"
    return safe


def _validate_relative_locator(locator: str, field_name: str) -> str:
    """Validate *locator* as a contained POSIX-relative bundle path."""
    if not isinstance(locator, str) or not locator:
        raise StagingError(f"{field_name} must be a non-empty string")
    if "\x00" in locator:
        raise StagingError(f"{field_name} must not contain NUL")
    if "\\" in locator:
        raise StagingError(f"{field_name} must use POSIX separators")
    if locator.startswith("/"):
        raise StagingError(f"{field_name} must be bundle-relative, not absolute")
    parts = locator.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise StagingError(f"{field_name} must not escape the bundle directory: {locator!r}")
    if ":" in parts[0]:
        raise StagingError(f"{field_name} must not carry a drive prefix: {locator!r}")
    return locator


def _split_checksum(checksum: str, field_name: str) -> str:
    """Return the lowercase hex of a ``sha256:<hex>`` checksum."""
    if not isinstance(checksum, str):
        raise StagingError(f"{field_name} must be a string")
    algo, separator, hexpart = checksum.partition(":")
    hexpart = hexpart.lower()
    if separator != ":" or algo.lower() != "sha256":
        raise StagingError(f"{field_name} must use the sha256 algorithm")
    if len(hexpart) != 64 or any(char not in "0123456789abcdef" for char in hexpart):
        raise StagingError(f"{field_name} must be sha256 plus 64 hex digits")
    return hexpart


def _lstat(path: str, what: str) -> os.stat_result:
    """Return ``lstat`` of *path*, failing closed as :class:`StagingError`."""
    try:
        return os.lstat(path)
    except OSError as exc:
        raise StagingError(f"cannot inspect {what} {path!r}: {exc}") from exc


def _check_anchor(anchor: str, *, owner: int, what: str) -> None:
    """Require *anchor* to be an existing owner-owned non-symlink directory."""
    metadata = _lstat(anchor, what)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise StagingError(f"{what} must be a non-symlink directory: {anchor!r}")
    if metadata.st_uid != owner:
        raise StagingError(f"{what} must be owner-owned: {anchor!r}")


def _ensure_private_dir(path: str, *, anchor: str, owner: int) -> str:
    """Create *path* (under *anchor*) as an owner-only directory.

    Existing components below *anchor* are rejected when they are symlinks;
    the final directory is chmodded to ``0o700`` and verified.
    """
    target = os.path.abspath(path)
    if os.path.commonpath([target, anchor]) != anchor:
        raise StagingError(f"staging path escapes its anchor: {path!r}")
    relative = os.path.relpath(target, anchor)
    if relative != ".":
        current = anchor
        for part in relative.split(os.sep):
            if part in ("", ".", ".."):
                raise StagingError(f"staging path is not portable: {path!r}")
            current = os.path.join(current, part)
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                break
            except OSError as exc:
                raise StagingError(f"cannot inspect staging path {current!r}: {exc}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise StagingError(f"staging path must not cross a symlink: {current!r}")
    try:
        os.makedirs(target, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise StagingError(f"cannot create staging directory {target!r}: {exc}") from exc
    metadata = _lstat(target, "staging directory")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise StagingError(f"staging directory must be a non-symlink directory: {target!r}")
    if metadata.st_uid != owner:
        raise StagingError(f"staging directory must be owner-owned: {target!r}")
    try:
        os.chmod(target, 0o700)
    except OSError as exc:
        raise StagingError(f"cannot secure staging directory {target!r}: {exc}") from exc
    metadata = _lstat(target, "staging directory")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StagingError(f"staging directory must be owner-only: {target!r}")
    return target


def _open_parent_fd(directory: str, *, owner: int, what: str) -> int:
    """Open *directory* as a no-follow directory descriptor, verified."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    try:
        parent_fd = os.open(directory, os.O_RDONLY | directory_flag | nofollow)
    except OSError as exc:
        raise StagingError(f"cannot securely open {what} {directory!r}: {exc}") from exc
    try:
        metadata = os.fstat(parent_fd)
    except OSError as exc:
        os.close(parent_fd)
        raise StagingError(f"cannot inspect {what} {directory!r}: {exc}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        os.close(parent_fd)
        raise StagingError(f"{what} must be owner-owned and not group/world-writable")
    return parent_fd


def _check_source_parent(source_abs: str, *, owner: int) -> None:
    """Reject sources whose direct parent is missing, linked, or shared."""
    parent = os.path.dirname(source_abs)
    metadata = _lstat(parent, "source directory")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise StagingError("source directory must be a non-symlink directory")
    if metadata.st_uid != owner:
        raise StagingError("source directory must be owner-owned")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise StagingError("source directory must not be group/world-writable")


def _validate_existing_destination(name: str, parent_fd: int, owner: int) -> None:
    """Reject an existing destination outside the regular-file boundary."""
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StagingError(f"cannot inspect staging destination {name!r}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise StagingError(f"staging destination must be a non-symlink file: {name!r}")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
        raise StagingError(f"staging destination must be an owner-owned regular file: {name!r}")


def _open_temporary_destination(name: str, parent_fd: int, nofollow: int) -> tuple[int, str]:
    """Create a private same-directory temporary file without following links."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
    for _ in range(10):
        candidate = f"{_TEMP_NAME_PREFIX}{secrets.token_hex(_TEMP_TOKEN_BYTES)}"
        try:
            return os.open(candidate, flags, 0o600, dir_fd=parent_fd), candidate
        except FileExistsError:
            continue
        except OSError as exc:
            raise StagingError(f"cannot create staging temporary for {name!r}: {exc}") from exc
    raise StagingError(f"could not allocate a unique staging temporary file for {name!r}")


def _verify_temp_stability(
    temporary_fd: int, temporary_name: str, parent_fd: int, owner: int
) -> None:
    """Require the temporary file to be a stable owner-owned regular file.

    The open descriptor and the directory-relative name must resolve to the
    same device/inode, so a swap between write and publish fails closed.
    """
    try:
        temp_metadata = os.fstat(temporary_fd)
        path_metadata = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise StagingError(f"cannot recheck staging temporary file: {exc}") from exc
    if (
        not stat.S_ISREG(temp_metadata.st_mode)
        or temp_metadata.st_uid != owner
        or not stat.S_ISREG(path_metadata.st_mode)
        or path_metadata.st_uid != owner
        or (path_metadata.st_dev, path_metadata.st_ino)
        != (temp_metadata.st_dev, temp_metadata.st_ino)
    ):
        raise StagingError("staging temporary file must be a stable regular file")


def _stream_copy_verified(
    source_fd: int,
    temporary_fd: int,
    expected_hex: str,
    expected_size: int | None,
    *,
    what: str,
) -> None:
    """Stream *source_fd* to *temporary_fd*, checking digest and size."""
    digest = hashlib.sha256()
    total = 0
    while True:
        try:
            chunk = os.read(source_fd, _CHUNK_BYTES)
        except OSError as exc:
            raise StagingError(f"cannot read {what}: {exc}") from exc
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
        view = memoryview(chunk)
        while view:
            try:
                written = os.write(temporary_fd, view)
            except OSError as exc:
                raise StagingError(f"cannot write {what}: {exc}") from exc
            if written <= 0:
                raise StagingError(f"staging write made no progress for {what}")
            view = view[written:]
    if digest.hexdigest() != expected_hex:
        raise StagingError(f"checksum mismatch while staging {what}")
    if expected_size is not None and total != expected_size:
        raise StagingError(f"size mismatch while staging {what}")


def _secure_copy_file(
    *,
    source_path: str,
    dest_path: str,
    expected_checksum: str,
    expected_size: int | None,
    anchor: str,
    owner: int,
) -> str:
    """Securely copy one verified file to *dest_path* atomically."""
    expected_hex = _split_checksum(expected_checksum, "expected checksum")
    if expected_size is not None and (not isinstance(expected_size, int) or expected_size < 0):
        raise StagingError("expected size must be a non-negative integer or None")
    if not isinstance(source_path, str) or not source_path or "\x00" in source_path:
        raise StagingError("source path must be a non-empty string")
    source_abs = os.path.abspath(source_path)
    dest_abs = os.path.abspath(dest_path)
    if os.path.commonpath([dest_abs, anchor]) != anchor:
        raise StagingError(f"staging destination escapes its anchor: {dest_path!r}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source_abs, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise StagingError(f"cannot securely open source {source_path!r}: {exc}") from exc
    parent_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name: str | None = None
    try:
        try:
            metadata = os.fstat(source_fd)
        except OSError as exc:
            raise StagingError(f"cannot inspect source {source_path!r}: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
            raise StagingError(f"source must be an owner-owned regular file: {source_path!r}")
        _check_source_parent(source_abs, owner=owner)
        dest_parent = os.path.dirname(dest_abs)
        _ensure_private_dir(dest_parent, anchor=anchor, owner=owner)
        parent_fd = _open_parent_fd(dest_parent, owner=owner, what="staging directory")
        base = os.path.basename(dest_abs)
        if not base or base in (".", ".."):
            raise StagingError(f"staging destination name is not portable: {dest_path!r}")
        _validate_existing_destination(base, parent_fd, owner)
        temporary_fd, temporary_name = _open_temporary_destination(base, parent_fd, nofollow)
        try:
            temporary_metadata = os.fstat(temporary_fd)
        except OSError as exc:
            raise StagingError(f"cannot inspect staging temporary file: {exc}") from exc
        if not stat.S_ISREG(temporary_metadata.st_mode) or temporary_metadata.st_uid != owner:
            raise StagingError("staging temporary file must be owner-owned and regular")
        try:
            os.fchmod(temporary_fd, 0o600)
        except OSError as exc:
            raise StagingError(f"cannot secure staging temporary file: {exc}") from exc
        _stream_copy_verified(
            source_fd, temporary_fd, expected_hex, expected_size, what=f"source {source_path!r}"
        )
        try:
            os.fsync(temporary_fd)
        except OSError as exc:
            raise StagingError(f"cannot persist staged bytes: {exc}") from exc
        _verify_temp_stability(temporary_fd, temporary_name, parent_fd, owner)
        _validate_existing_destination(base, parent_fd, owner)
        fd_to_close = temporary_fd
        temporary_fd = None
        try:
            os.close(fd_to_close)
        except OSError as exc:
            raise StagingError(f"cannot publish staged file: {exc}") from exc
        try:
            os.replace(temporary_name, base, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except OSError as exc:
            raise StagingError(f"cannot publish staged file: {exc}") from exc
        temporary_name = None
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            raise StagingError(f"cannot persist staging directory: {exc}") from exc
        try:
            os.chmod(dest_abs, 0o600)
        except OSError as exc:
            raise StagingError(f"cannot secure staged file: {exc}") from exc
        return dest_abs
    finally:
        if temporary_fd is not None:
            try:
                os.close(temporary_fd)
            except OSError:
                pass
        if temporary_name is not None and parent_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except OSError:
                pass
        try:
            os.close(source_fd)
        except OSError:
            pass


def _hash_file_secure(path_abs: str, *, owner: int, what: str) -> tuple[str, int]:
    """Hash one owner-owned regular file through a no-follow descriptor."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(path_abs, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise StagingError(f"cannot securely open {what}: {exc}") from exc
    try:
        try:
            metadata = os.fstat(source_fd)
        except OSError as exc:
            raise StagingError(f"cannot inspect {what}: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
            raise StagingError(f"{what} must be an owner-owned regular file")
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = os.read(source_fd, _CHUNK_BYTES)
            except OSError as exc:
                raise StagingError(f"cannot read {what}: {exc}") from exc
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        return digest.hexdigest(), total
    finally:
        try:
            os.close(source_fd)
        except OSError:
            pass


def _reverify_copy(
    dest_abs: str, expected_checksum: str, expected_size: int | None, *, owner: int
) -> None:
    """Re-hash *dest_abs* after publication; fail closed on any drift."""
    expected_hex = _split_checksum(expected_checksum, "expected checksum")
    actual_hex, actual_size = _hash_file_secure(dest_abs, owner=owner, what="staged file")
    if actual_hex != expected_hex:
        raise StagingError("staged file changed after copy")
    if expected_size is not None and actual_size != expected_size:
        raise StagingError("staged file size changed after copy")


def _remove_tree_best_effort(work_dir: str, *, anchor: str) -> None:
    """Remove *work_dir* without following symlinks; never raises."""
    try:
        target = os.path.abspath(work_dir)
        if os.path.commonpath([target, anchor]) != anchor or target == anchor:
            return
        metadata = os.lstat(target)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return
        shutil.rmtree(target, ignore_errors=True)
    except OSError:
        pass


def _read_result_bundle(result_abs: str, *, owner: int) -> ResultBundle:
    """Read and strictly validate the worker result bundle file."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        bundle_fd = os.open(result_abs, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise StagingError(f"cannot securely open result bundle: {exc}") from exc
    try:
        try:
            metadata = os.fstat(bundle_fd)
        except OSError as exc:
            raise StagingError(f"cannot inspect result bundle: {exc}") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
            raise StagingError("result bundle must be an owner-owned regular file")
        pieces: list[bytes] = []
        total = 0
        while True:
            try:
                chunk = os.read(bundle_fd, _CHUNK_BYTES)
            except OSError as exc:
                raise StagingError(f"cannot read result bundle: {exc}") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_RESULT_BYTES:
                raise StagingError("result bundle exceeds the size bound")
            pieces.append(chunk)
        raw = b"".join(pieces)
    finally:
        try:
            os.close(bundle_fd)
        except OSError:
            pass
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StagingError(f"result bundle is not valid UTF-8: {exc}") from exc
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise StagingError(f"result bundle is not valid JSON: {exc}") from exc
    try:
        bundle = ResultBundle.model_validate(_restore_json_tuples(payload))
    except ValidationError as exc:
        raise StagingError(f"result bundle failed strict validation: {exc}") from exc
    if not isinstance(bundle, ResultBundle):
        raise StagingError("result bundle failed strict validation")
    return bundle


def _reject_json_constant(value: str) -> Any:
    """Reject non-finite JSON constants so payloads stay canonical."""
    raise StagingError(f"result bundle carries a non-canonical constant: {value!r}")


def _restore_json_tuples(payload: Any) -> Any:
    """Restore tuple-typed bundle fields erased by the JSON round-trip.

    JSON has no tuple type, so a worker-written ``model_dump_json`` file
    carries lists where the strict model declares tuples.  Restoring the
    one tuple-typed field (``produced_artifacts``) before validation keeps
    every other check strict; anything misshaped still fails closed.
    """
    if not isinstance(payload, dict):
        return payload
    artifacts = payload.get("produced_artifacts")
    if isinstance(artifacts, list):
        payload = dict(payload)
        payload["produced_artifacts"] = tuple(artifacts)
    return payload


def _check_bundle_identity(bundle: ResultBundle, handoff: WorkerHandoffV2) -> None:
    """Require the bundle to answer exactly to *handoff*; never trust."""
    for field_name in (
        "run_id",
        "step_id",
        "work_item_id",
        "attempt_number",
        "launch_token",
        "work_item_digest",
    ):
        if getattr(bundle, field_name) != getattr(handoff, field_name):
            raise StagingError(f"result bundle {field_name} does not match the handoff")


def _check_attempt_current(store: Any, work_item_id: str, attempt_number: int) -> None:
    """Require *attempt_number* to equal the store's current attempt."""
    get_attempts = getattr(store, "get_attempts", None)
    if not callable(get_attempts):
        raise StagingError("store must expose get_attempts(work_item_id)")
    attempts = get_attempts(work_item_id)
    try:
        numbers = [int(item.attempt_number) for item in attempts]
    except (AttributeError, TypeError, ValueError) as exc:
        raise StagingError(f"store returned unreadable attempt history: {exc}") from exc
    if not numbers:
        raise StagingError("store has no current attempt for this work item")
    if attempt_number != max(numbers):
        raise StagingError("result bundle answers a stale or unknown attempt")


def _resolve_worker_file(result_dir: str, bundle_locator: str) -> str:
    """Resolve a produced artifact to its worker-side file, contained.

    Envelope-canonical locators (``files/<name>``) resolve as
    ``<result_dir>/<locator>``; bare names resolve as
    ``<result_dir>/files/<locator>`` for workers that store the bare name.
    When the preferred layout is absent, the literal
    ``<result_dir>/files/<locator>`` spelling is accepted as a fallback.
    """
    locator = _validate_relative_locator(bundle_locator, "bundle_locator")
    parts = locator.split("/")
    if parts[0] == "files":
        primary = os.path.join(result_dir, *parts)
    else:
        primary = os.path.join(result_dir, "files", *parts)
    literal = os.path.join(result_dir, "files", *parts)
    candidates: list[str] = [primary]
    if literal != primary:
        candidates.append(literal)
    for candidate in candidates:
        candidate_abs = os.path.abspath(candidate)
        if os.path.commonpath([candidate_abs, result_dir]) != result_dir:
            raise StagingError("worker file escapes the result directory")
        try:
            metadata = os.lstat(candidate_abs)
        except OSError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise StagingError("worker file must be a non-symlink file")
        return candidate_abs
    raise StagingError(f"worker file is missing for bundle locator {bundle_locator!r}")


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    """Require *value* to be a mapping, failing closed otherwise."""
    if not isinstance(value, dict):
        raise StagingError(f"{what} must be a mapping")
    return value


def _build_structure_set(items: Any, *, what: str) -> StructureSet:
    """Rebuild a structure set strictly through domain constructors."""
    if items is None:
        items = []
    if not isinstance(items, list):
        raise StagingError(f"{what} must be a list")
    records: list[StructureRecord] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise StagingError(f"{what}[{index}] must be a mapping")
        try:
            records.append(
                StructureRecord(
                    id=item["id"],
                    atoms=item["atoms"],
                    coordinates=item["coordinates"],
                    charge=item.get("charge"),
                    multiplicity=item.get("multiplicity"),
                    parent_ids=tuple(item.get("parent_ids", ())),
                    lineage_root_id=item.get("lineage_root_id"),
                    source_step_id=item.get("source_step_id"),
                    source_work_item_id=item.get("source_work_item_id"),
                    role=item.get("role"),
                    ordinal=item.get("ordinal"),
                    group_key=item.get("group_key"),
                    metadata=FrozenDict(_require_mapping(item.get("metadata", {}), "metadata")),
                )
            )
        except StagingError:
            raise
        except Exception as exc:
            raise StagingError(f"{what}[{index}] is invalid: {exc}") from exc
    try:
        return StructureSet(tuple(records))
    except Exception as exc:
        raise StagingError(f"{what} is invalid: {exc}") from exc


def _build_result_set(items: Any, *, what: str) -> ResultSet:
    """Rebuild a result set strictly through domain constructors."""
    if items is None:
        items = []
    if not isinstance(items, list):
        raise StagingError(f"{what} must be a list")
    records: list[ScientificResult] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise StagingError(f"{what}[{index}] must be a mapping")
        try:
            unit_raw = item.get("unit")
            quantity_raw = item.get("quantity")
            provenance_raw = item.get("provenance")
            provenance: Provenance | None = None
            if provenance_raw is not None:
                if not isinstance(provenance_raw, dict):
                    raise StagingError(f"{what}[{index}].provenance must be a mapping")
                provenance = Provenance(
                    program=provenance_raw.get("program"),
                    program_version=provenance_raw.get("program_version"),
                    method=provenance_raw.get("method"),
                    basis=provenance_raw.get("basis"),
                    adapter=provenance_raw.get("adapter"),
                    step_id=provenance_raw.get("step_id"),
                    work_item_id=provenance_raw.get("work_item_id"),
                    metadata=FrozenDict(
                        _require_mapping(provenance_raw.get("metadata", {}), "metadata")
                    ),
                )
            records.append(
                ScientificResult(
                    kind=item["kind"],
                    value=item["value"],
                    unit=Unit(unit_raw) if unit_raw is not None else None,
                    quantity=QuantityKind(quantity_raw) if quantity_raw is not None else None,
                    subject_structure_id=item.get("subject_structure_id"),
                    source_step_id=item.get("source_step_id"),
                    source_work_item_id=item.get("source_work_item_id"),
                    provenance=provenance,
                    metadata=FrozenDict(_require_mapping(item.get("metadata", {}), "metadata")),
                )
            )
        except StagingError:
            raise
        except Exception as exc:
            raise StagingError(f"{what}[{index}] is invalid: {exc}") from exc
    try:
        return ResultSet(tuple(records))
    except Exception as exc:
        raise StagingError(f"{what} is invalid: {exc}") from exc


def _build_diagnostics(items: Any, *, what: str) -> tuple[Diagnostic, ...]:
    """Rebuild diagnostics strictly through domain constructors."""
    if items is None:
        items = []
    if not isinstance(items, list):
        raise StagingError(f"{what} must be a list")
    rebuilt: list[Diagnostic] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise StagingError(f"{what}[{index}] must be a mapping")
        try:
            severity_raw = item.get("severity", DiagnosticSeverity.ERROR.value)
            rebuilt.append(
                Diagnostic(
                    code=item["code"],
                    message=item["message"],
                    severity=DiagnosticSeverity(severity_raw),
                    step_id=item.get("step_id"),
                    work_item_id=item.get("work_item_id"),
                    logical_key=item.get("logical_key"),
                    field_path=item.get("field_path"),
                    details=FrozenDict(_require_mapping(item.get("details", {}), "details")),
                )
            )
        except StagingError:
            raise
        except Exception as exc:
            raise StagingError(f"{what}[{index}] is invalid: {exc}") from exc
    return tuple(rebuilt)


def _build_timing(value: Any) -> Timing | None:
    """Rebuild timing strictly through the domain constructor."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StagingError("timing must be a mapping or None")
    try:
        return Timing(
            started_at=value.get("started_at"),
            finished_at=value.get("finished_at"),
            duration_seconds=value.get("duration_seconds"),
        )
    except Exception as exc:
        raise StagingError(f"timing is invalid: {exc}") from exc


def _build_error(value: Any) -> ResultError | None:
    """Rebuild a result error strictly through the domain constructor."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StagingError("error must be a mapping or None")
    try:
        return ResultError(
            code=value["code"],
            message=value["message"],
            retryable=bool(value.get("retryable", False)),
            details=FrozenDict(_require_mapping(value.get("details", {}), "details")),
        )
    except StagingError:
        raise
    except Exception as exc:
        raise StagingError(f"error is invalid: {exc}") from exc


def _build_recovery(value: Any) -> RecoveryInfo | None:
    """Rebuild recovery metadata strictly through the domain constructor."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StagingError("recovery must be a mapping or None")
    try:
        return RecoveryInfo(
            profile=value["profile"],
            attempted=bool(value.get("attempted", False)),
            succeeded=value.get("succeeded"),
            details=FrozenDict(_require_mapping(value.get("details", {}), "details")),
        )
    except StagingError:
        raise
    except Exception as exc:
        raise StagingError(f"recovery is invalid: {exc}") from exc


def _rebuild_work_item_result(
    payload: dict[str, Any], *, handoff: WorkerHandoffV2, artifacts: ArtifactSet
) -> WorkItemResult:
    """Rebuild a result from its payload, replacing artifacts with imports."""
    if payload.get("work_item_id") != handoff.work_item_id:
        raise StagingError("result work_item_id does not match the handoff")
    try:
        status = WorkItemStatus(payload.get("status"))
    except ValueError as exc:
        raise StagingError(f"result status is invalid: {payload.get('status')!r}") from exc
    structures = _build_structure_set(payload.get("structures", []), what="structures")
    results = _build_result_set(payload.get("results", []), what="results")
    diagnostics = _build_diagnostics(payload.get("diagnostics", []), what="diagnostics")
    timing = _build_timing(payload.get("timing"))
    error = _build_error(payload.get("error"))
    recovery = _build_recovery(payload.get("recovery"))
    semantic_digest = payload.get("semantic_digest")
    if semantic_digest is not None and semantic_digest != handoff.work_item_digest:
        raise StagingError("result semantic digest does not match the handoff")
    metadata = FrozenDict(_require_mapping(payload.get("metadata", {}), "metadata"))
    try:
        return WorkItemResult(
            work_item_id=handoff.work_item_id,
            status=status,
            structures=structures,
            results=results,
            artifacts=artifacts,
            diagnostics=diagnostics,
            timing=timing,
            error=error,
            recovery=recovery,
            semantic_digest=semantic_digest,
            metadata=metadata,
        )
    except Exception as exc:
        raise StagingError(f"result payload is invalid: {exc}") from exc
