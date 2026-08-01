#!/usr/bin/env python3
"""Install-side provenance for ConfFlow releases.

The producer's wheel cannot be its own source of truth for its content
hash. ConfFlow instead records three layers of provenance:

* *wheel-internal build provenance* — ``confflow.__build__.COMMIT`` and
  ``DIRTY`` are set by the wheel build hook (``setup.py``) and never
  describe the wheel file itself.
* *external release provenance* — the release workflow writes
  ``SHA256SUMS`` and (later) an artifact attestation next to the wheel
  in ``dist/``. These are *outside* the wheel.
* *target venv install provenance* — the deployer creates a fresh
  ``<sys.prefix>/share/confflow/install-provenance.json`` after
  verifying the wheel digest against ``SHA256SUMS`` (and, in production,
  the approved attestation). This module is that record's owner.

The capability payload reads ``install_provenance.json`` instead of any
wheel-baked digest. When the file is missing or does not match the
expected schema, the payload falls back to the v4 diagnostic shape
(``producer.wheel = {"filename": null, "sha256": null}``,
``producer.install_provenance.status = "missing"`` and a machine-readable
``reason_code``). JobDesk's production gate treats every non-``verified``
status as candidate-only diagnostic.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

INSTALL_PROVENANCE_RELATIVE_PATH = "share/confflow/install-provenance.json"
INSTALL_PROVENANCE_SCHEMA = "confflow.install-provenance.v2"

# Status/reason codes must stay stable and machine-readable.
STATUS_VERIFIED = "verified"
STATUS_MISSING = "missing"
STATUS_INVALID = "invalid"

REASON_MISSING_FILE = "missing_file"
REASON_INVALID_JSON = "invalid_json"
REASON_SCHEMA_MISMATCH = "schema_mismatch"
REASON_VERSION_MISMATCH = "version_mismatch"
REASON_COMMIT_MISMATCH = "commit_mismatch"
REASON_DIGEST_MISMATCH = "digest_mismatch"
REASON_ATTESTATION_UNVERIFIED = "attestation_unverified"
REASON_EDITABLE_INSTALL = "editable_install"


@dataclass
class InstallProvenanceRecord:
    """The contents of ``<sys.prefix>/share/confflow/install-provenance.json``."""

    schema_: str = field(default=INSTALL_PROVENANCE_SCHEMA)
    package: str = "confflow"
    version: str = ""
    wheel_filename: str = ""
    wheel_sha256: str = ""
    dependency_lock_sha256: str = ""
    wheelhouse_manifest_sha256: str = ""
    python_version: str = ""
    python_implementation: str = ""
    platform: str = ""
    machine: str = ""
    build_commit: str = ""
    build_dirty: bool | None = None
    release_repository: str = ""
    release_tag: str = ""
    release_tag_commit: str = ""
    attestation_verified: bool = False
    attestation_subject_digest: str = ""
    installed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        # ``schema_`` is the Python-friendly name; the JSON uses ``schema``.
        payload["schema"] = payload.pop("schema_")
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> InstallProvenanceRecord:
        schema = str(raw.get("schema"))
        if schema != INSTALL_PROVENANCE_SCHEMA:
            raise ValueError(
                f"unsupported install-provenance schema {schema!r}; "
                f"expected {INSTALL_PROVENANCE_SCHEMA!r}"
            )
        payload = dict(raw)
        payload.setdefault("schema_", payload.pop("schema", INSTALL_PROVENANCE_SCHEMA))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        cleaned = {k: v for k, v in payload.items() if k in known}
        return cls(**cleaned)


def install_provenance_path(sys_prefix: str | None = None) -> Path:
    """Return the canonical install-provenance path for ``sys_prefix``."""
    base = sys_prefix if sys_prefix is not None else sys.prefix
    return Path(base) / INSTALL_PROVENANCE_RELATIVE_PATH


def sha256_hex(path: str | os.PathLike[str]) -> str:
    """Return the lowercase hex SHA-256 of ``path``."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sha256sums(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse a ``SHA256SUMS``-style file into ``{filename: hex_digest}``.

    Duplicate or malformed entries are returned verbatim; callers must
    fail closed on ambiguity.
    """
    result: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split(None, 1)
            if len(parts) != 2:
                continue
            digest, filename = parts[0].strip(), parts[1].strip()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
                continue
            if "*" in filename:
                # Glob-style filenames are not allowed by the deployer.
                continue
            result[filename] = digest.lower()
    return result


@dataclass(frozen=True)
class CapabilityProvenanceDigest:
    """The v4 capability view of the install provenance.

    Either ``status == "verified"`` (``reason_code`` must be ``None`` and
    wheel filename/sha are non-empty), or a non-verified diagnostic
    payload that JobDesk rejects as production input.
    """

    status: str
    reason_code: str | None
    wheel_filename: str | None
    wheel_sha256: str | None


def read_install_provenance(
    sys_prefix: str | None = None,
) -> tuple[CapabilityProvenanceDigest, list[str]]:
    """Return ``(digest, errors)`` for the runtime capability probe.

    ``errors`` is empty when ``status == "verified"``. The diagnostic
    shape for any non-verified status is strictly the v4 contract:

    * ``wheel_filename`` / ``wheel_sha256`` are ``None`` (or any value
      when ``status == "verified"`` and provenance explicitly states
      the chosen wheel),
    * ``reason_code`` is a single machine-readable token,
    * no secret, no environment variable, no full remote output.
    """
    path = install_provenance_path(sys_prefix)
    if not path.exists():
        return (
            CapabilityProvenanceDigest(
                status=STATUS_MISSING,
                reason_code=REASON_MISSING_FILE,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            [],
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_INVALID_JSON,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            [],
        )
    if not isinstance(raw, dict):
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_SCHEMA_MISMATCH,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            [],
        )
    try:
        record = InstallProvenanceRecord.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_SCHEMA_MISMATCH,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            [],
        )
    # ``attestation_verified`` must be ``True`` for a production record.
    if not record.attestation_verified:
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_ATTESTATION_UNVERIFIED,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            ["install-provenance is not attestation-verified"],
        )
    if not record.wheel_filename or not record.wheel_sha256:
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_SCHEMA_MISMATCH,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            ["install-provenance is missing wheel filename or digest"],
        )
    if not record.version or not record.build_commit:
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_SCHEMA_MISMATCH,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            ["install-provenance is missing version or build commit"],
        )
    digest_fields = (
        record.dependency_lock_sha256,
        record.wheelhouse_manifest_sha256,
        record.wheel_sha256,
        record.attestation_subject_digest,
    )
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower())
        for value in digest_fields
    ):
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_SCHEMA_MISMATCH,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            ["install-provenance is missing or has an invalid digest"],
        )
    if not (
        record.python_version
        and record.python_implementation
        and record.platform
        and record.machine
    ):
        return (
            CapabilityProvenanceDigest(
                status=STATUS_INVALID,
                reason_code=REASON_SCHEMA_MISMATCH,
                wheel_filename=None,
                wheel_sha256=None,
            ),
            ["install-provenance is missing runtime identity"],
        )
    return (
        CapabilityProvenanceDigest(
            status=STATUS_VERIFIED,
            reason_code=None,
            wheel_filename=record.wheel_filename,
            wheel_sha256=record.wheel_sha256,
        ),
        [],
    )


def write_install_provenance_atomic(
    destination: str | os.PathLike[str],
    payload: dict[str, object],
) -> None:
    """Atomically replace ``destination`` with the JSON payload.

    Uses the same ``write_atomic_json`` contract as other producer
    artifacts so an interrupted deployer cannot leave a half-written
    ``install-provenance.json`` for the next capability probe.
    """
    from .artifact_json import write_atomic_json

    write_atomic_json(destination, payload)


__all__ = [
    "INSTALL_PROVENANCE_RELATIVE_PATH",
    "INSTALL_PROVENANCE_SCHEMA",
    "InstallProvenanceRecord",
    "CapabilityProvenanceDigest",
    "STATUS_VERIFIED",
    "STATUS_MISSING",
    "STATUS_INVALID",
    "REASON_MISSING_FILE",
    "REASON_INVALID_JSON",
    "REASON_SCHEMA_MISMATCH",
    "REASON_VERSION_MISMATCH",
    "REASON_COMMIT_MISMATCH",
    "REASON_DIGEST_MISMATCH",
    "REASON_ATTESTATION_UNVERIFIED",
    "REASON_EDITABLE_INSTALL",
    "install_provenance_path",
    "sha256_hex",
    "read_sha256sums",
    "read_install_provenance",
    "write_install_provenance_atomic",
]
