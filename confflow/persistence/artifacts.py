#!/usr/bin/env python3

"""Artifact integrity verification for V4 (V4-3).

Verification (:func:`verify_artifact`) fail-closes on every anomaly: locator
escapes, missing files, checksum mismatches, and size mismatches all raise
:class:`ArtifactIntegrityError` and are never silent.
"""

from __future__ import annotations

import hashlib
import os
from typing import Final

from ..domain.artifact import ArtifactRef, LocatorKind
from .contracts import PersistenceError, validate_run_root

__all__ = [
    "ArtifactIntegrityError",
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
