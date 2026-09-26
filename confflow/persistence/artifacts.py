#!/usr/bin/env python3

"""Artifact integrity verification and garbage collection for V4 (V4-3).

Verification (:func:`verify_artifact`) fail-closes on every anomaly: locator
escapes, missing files, checksum mismatches, and size mismatches all raise
:class:`ArtifactIntegrityError` and are never silent.  Collection is split
into dry-run planning (:func:`plan_gc`) and execution (:func:`apply_gc`) so
callers inspect a :class:`~confflow.persistence.contracts.GCPlan` before any
byte is deleted.

Retention mapping
-----------------
:class:`~confflow.domain.retention.RetentionClass` is the only retention
authority; this module defines no parallel enum.  ``ArtifactRef.retention``
already carries the class, so planning consumes it directly:

- ``TEMPORARY`` artifacts are collectable once every direct consumer has
  finished (the artifact id is in ``consumed_ids``).
- ``NATIVE_SUPPORTING``-style intermediate outputs map to
  ``RetentionClass.INTERMEDIATE``: collectable once the run is complete
  (``run_complete``) *and* every direct consumer has finished.
- Every other artifact is ``RetentionClass.RETAINED`` (also the
  :class:`~confflow.domain.artifact.ArtifactRef` default) and is never
  collected.

``RESUME_REQUIRED``, ``PUBLISHED``, and ``DOWNSTREAM_REQUIRED`` are not
retention classes; they are protection reasons supplied by the caller as id
sets (``resume_required_ids``, ``protected_ids``, and
``downstream_required_ids`` respectively).  Any id present in any protection
set is never planned, regardless of class.  ``referenced`` is therefore
always ``False`` when this module calls
:func:`~confflow.domain.retention.may_garbage_collect`: published and resume
references arrive as protection ids, never as hidden state.

Planning notes: missing-file candidates are skipped silently (a concurrent
collector may already have removed them); ``EXTERNAL_URI`` candidates are
skipped because they name no local bytes; locator escapes raise
:class:`ArtifactIntegrityError` and are never silently skipped.  Application
re-resolves and containment-checks every entry (TOCTOU), deletes files only,
and never deletes directories.
"""

from __future__ import annotations

import hashlib
import os
from typing import Final

from ..domain.artifact import ArtifactRef, LocatorKind
from ..domain.retention import RetentionClass, may_garbage_collect
from .contracts import GCEntry, GCPlan, PersistenceError, validate_run_root

__all__ = [
    "ArtifactIntegrityError",
    "apply_gc",
    "plan_gc",
    "verify_artifact",
]

#: Streaming hash block size for :func:`verify_artifact`.
_HASH_CHUNK_BYTES: Final[int] = 65536


class ArtifactIntegrityError(PersistenceError):
    """An artifact locator or its bytes failed integrity verification."""


def verify_artifact(
    *, run_root: str, ref: ArtifactRef, expected_size_bytes: int | None = None
) -> None:
    """Verify one artifact's bytes against its reference, failing closed.

    Parameters
    ----------
    run_root : str
        Managed run root; ``RUN_RELATIVE`` locators resolve under it.
    ref : ArtifactRef
        Reference carrying the locator and an optional checksum.
    expected_size_bytes : int | None
        Optional exact byte size the file must have.

    Returns
    -------
    None
        ``None`` on success; every failure raises, never silent.

    Raises
    ------
    ArtifactIntegrityError
        Raised when the locator escapes the run root (absolute path, ``..``
        segments, or a symlink resolving outside), the file is missing or
        not a regular file, the streaming checksum mismatches (naming the
        artifact id), the size mismatches, or the checksum algorithm is
        unsupported.  ``EXTERNAL_URI`` locators get a shape check only and
        return without touching the filesystem.
    """
    root = validate_run_root(run_root)
    locator = ref.locator
    if locator.kind is LocatorKind.EXTERNAL_URI:
        if not isinstance(locator.uri, str) or not locator.uri:
            raise ArtifactIntegrityError(
                f"artifact {ref.id!r} has an external locator without a uri"
            )
        return
    if locator.kind is not LocatorKind.RUN_RELATIVE:
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} has unsupported locator kind {locator.kind!r}"
        )
    raw = locator.path
    if not isinstance(raw, str) or not raw:
        raise ArtifactIntegrityError(f"artifact {ref.id!r} has an empty locator path")
    if "\x00" in raw:
        raise ArtifactIntegrityError(f"artifact {ref.id!r} has a NUL locator path")
    if os.path.isabs(raw) or raw.startswith("/"):
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} escapes the run root: absolute path {raw!r}"
        )
    segments = raw.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} escapes the run root: unsafe path {raw!r}"
        )
    if "\\" in raw or ":" in segments[0]:
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} escapes the run root: non-portable path {raw!r}"
        )
    candidate = os.path.abspath(os.path.join(root, raw))
    try:
        lexically_inside = os.path.commonpath([root, candidate]) == root
    except ValueError as exc:
        raise ArtifactIntegrityError(f"artifact {ref.id!r} escapes the run root: {raw!r}") from exc
    if not lexically_inside:
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} escapes the run root: {raw!r} resolves outside"
        )
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    try:
        contained = os.path.commonpath([real_root, real_candidate]) == real_root
    except ValueError as exc:
        raise ArtifactIntegrityError(f"artifact {ref.id!r} escapes the run root: {raw!r}") from exc
    if not contained:
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} escapes the run root: symlink {raw!r} resolves outside"
        )
    if not os.path.lexists(candidate):
        raise ArtifactIntegrityError(f"artifact {ref.id!r} is missing: {candidate!r}")
    if os.path.isdir(candidate):
        raise ArtifactIntegrityError(f"artifact {ref.id!r} is not a file: {candidate!r}")
    if expected_size_bytes is not None:
        if (
            isinstance(expected_size_bytes, bool)
            or not isinstance(expected_size_bytes, int)
            or expected_size_bytes < 0
        ):
            raise ArtifactIntegrityError(
                f"artifact {ref.id!r} has an invalid expected size {expected_size_bytes!r}"
            )
        try:
            actual_size = os.path.getsize(candidate)
        except OSError as exc:
            raise ArtifactIntegrityError(
                f"artifact {ref.id!r} is unreadable: {candidate!r}"
            ) from exc
        if actual_size != expected_size_bytes:
            raise ArtifactIntegrityError(
                f"artifact {ref.id!r} size mismatch: expected "
                f"{expected_size_bytes} bytes, found {actual_size}"
            )
    checksum = ref.checksum
    if checksum is None:
        return
    algorithm, separator, expected_hex = checksum.partition(":")
    if not separator or not algorithm or not expected_hex:
        raise ArtifactIntegrityError(f"artifact {ref.id!r} has a malformed checksum {checksum!r}")
    try:
        digest = hashlib.new(algorithm.lower())
    except (TypeError, ValueError) as exc:
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} uses unsupported checksum algorithm {algorithm!r}"
        ) from exc
    try:
        with open(candidate, "rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ArtifactIntegrityError(f"artifact {ref.id!r} is unreadable: {candidate!r}") from exc
    if digest.hexdigest().lower() != expected_hex.lower():
        raise ArtifactIntegrityError(
            f"artifact {ref.id!r} checksum mismatch: expected {checksum}, "
            f"computed {algorithm.lower()}:{digest.hexdigest()}"
        )


def plan_gc(
    *,
    run_root: str,
    candidates: tuple[ArtifactRef, ...],
    protected_ids: frozenset[str] = frozenset(),
    resume_required_ids: frozenset[str] = frozenset(),
    downstream_required_ids: frozenset[str] = frozenset(),
    run_complete: bool = False,
    consumed_ids: frozenset[str] = frozenset(),
) -> GCPlan:
    """Dry-run garbage collection planning; deletes nothing.

    Parameters
    ----------
    run_root : str
        Managed run root the candidates resolve under.
    candidates : tuple[ArtifactRef, ...]
        Artifacts to consider, in deterministic plan order.
    protected_ids : frozenset[str]
        ``PUBLISHED`` protection reason: never planned.
    resume_required_ids : frozenset[str]
        ``RESUME_REQUIRED`` protection reason: never planned.
    downstream_required_ids : frozenset[str]
        ``DOWNSTREAM_REQUIRED`` protection reason: never planned.
    run_complete : bool
        Whether the run reached a terminal state.
    consumed_ids : frozenset[str]
        Ids whose every direct consumer has finished.

    Returns
    -------
    GCPlan
        Entries in candidate order for artifacts where
        :func:`~confflow.domain.retention.may_garbage_collect` allows
        collection.  Each reason names the retention class and its basis;
        ``locator_path`` is the resolved absolute path and ``size_bytes`` is
        set when stating the file succeeds.

    Raises
    ------
    ArtifactIntegrityError
        Raised when a collectable candidate's locator escapes the run root;
        escapes are never silently skipped.

    Notes
    -----
    Missing-file candidates are skipped silently.  ``EXTERNAL_URI``
    candidates are skipped because they name no local bytes to collect.
    """
    root = validate_run_root(run_root)
    real_root = os.path.realpath(root)
    entries: list[GCEntry] = []
    for ref in candidates:
        if (
            ref.id in protected_ids
            or ref.id in resume_required_ids
            or ref.id in downstream_required_ids
        ):
            continue
        consumed = ref.id in consumed_ids
        if not may_garbage_collect(
            ref.retention, referenced=False, consumed=consumed, run_complete=run_complete
        ):
            continue
        if ref.locator.kind is LocatorKind.EXTERNAL_URI:
            continue
        if ref.locator.kind is not LocatorKind.RUN_RELATIVE:
            raise ArtifactIntegrityError(
                f"artifact {ref.id!r} has unsupported locator kind {ref.locator.kind!r}"
            )
        raw = ref.locator.path
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ArtifactIntegrityError(
                f"cannot plan GC for artifact {ref.id!r}: invalid locator path"
            )
        if os.path.isabs(raw) or raw.startswith("/"):
            raise ArtifactIntegrityError(
                f"cannot plan GC for artifact {ref.id!r}: absolute path {raw!r}"
            )
        segments = raw.split("/")
        if any(segment in ("", ".", "..") for segment in segments):
            raise ArtifactIntegrityError(
                f"cannot plan GC for artifact {ref.id!r}: unsafe path {raw!r}"
            )
        if "\\" in raw or ":" in segments[0]:
            raise ArtifactIntegrityError(
                f"cannot plan GC for artifact {ref.id!r}: non-portable path {raw!r}"
            )
        candidate = os.path.abspath(os.path.join(root, raw))
        try:
            inside = (
                os.path.commonpath([real_root, os.path.realpath(candidate)]) == real_root
                and os.path.commonpath([root, candidate]) == root
            )
        except ValueError as exc:
            raise ArtifactIntegrityError(
                f"cannot plan GC for artifact {ref.id!r}: escape {raw!r}"
            ) from exc
        if not inside:
            raise ArtifactIntegrityError(f"cannot plan GC for artifact {ref.id!r}: escape {raw!r}")
        if not os.path.lexists(candidate):
            continue
        try:
            size_bytes: int | None = os.path.getsize(candidate)
        except OSError:
            size_bytes = None
        if ref.retention is RetentionClass.TEMPORARY:
            reason = "temporary: every direct consumer finished"
        else:
            reason = f"{ref.retention.value}: run complete and every direct consumer finished"
        entries.append(
            GCEntry(
                artifact_id=ref.id,
                reason=reason,
                locator_path=candidate,
                size_bytes=size_bytes,
            )
        )
    return GCPlan(entries=tuple(entries))


def apply_gc(*, run_root: str, plan: GCPlan) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Apply a :class:`~confflow.persistence.contracts.GCPlan`, files only.

    Parameters
    ----------
    run_root : str
        Managed run root every entry must resolve under.
    plan : GCPlan
        Dry-run plan produced by :func:`plan_gc`.

    Returns
    -------
    tuple[tuple[str, ...], tuple[str, ...]]
        ``(removed_ids, failed_ids)`` in plan order.  Entries missing at
        apply time count as removed (idempotent re-apply); entries escaping
        the run root, naming directories, or failing deletion count as
        failed.  Directories are never deleted.
    """
    root = validate_run_root(run_root)
    real_root = os.path.realpath(root)
    removed: list[str] = []
    failed: list[str] = []
    for entry in plan.entries:
        target = entry.locator_path
        if not isinstance(target, str) or not target or not os.path.isabs(target):
            failed.append(entry.artifact_id)
            continue
        absolute = os.path.abspath(target)
        try:
            inside = (
                os.path.commonpath([root, absolute]) == root
                and os.path.commonpath([real_root, os.path.realpath(absolute)]) == real_root
            )
        except ValueError:
            failed.append(entry.artifact_id)
            continue
        if not inside:
            failed.append(entry.artifact_id)
            continue
        try:
            if not os.path.lexists(absolute):
                removed.append(entry.artifact_id)
                continue
            if os.path.isdir(absolute):
                failed.append(entry.artifact_id)
                continue
            os.unlink(absolute)
        except FileNotFoundError:
            removed.append(entry.artifact_id)
        except (OSError, ValueError):
            failed.append(entry.artifact_id)
        else:
            removed.append(entry.artifact_id)
    return (tuple(removed), tuple(failed))
