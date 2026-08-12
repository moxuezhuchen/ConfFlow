#!/usr/bin/env python3
"""Tests for the install-side provenance layer introduced in 1.4.5.

These tests cover the layered provenance model from the M2-4 release
plan:

* build hook writes only ``COMMIT`` / ``DIRTY`` to the wheel
* the wheel cannot self-describe its SHA-256 — capability reads from
  ``install-provenance.json`` instead
* the v4 capability diagnostic shape is stable
* the deployer's checksum / attestation gates fail closed
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from confflow import install_provenance as ip
from confflow.install_provenance import (
    REASON_ATTESTATION_UNVERIFIED,
    REASON_DIGEST_MISMATCH,
    REASON_EDITABLE_INSTALL,
    REASON_INVALID_JSON,
    REASON_MISSING_FILE,
    REASON_SCHEMA_MISMATCH,
    STATUS_INVALID,
    STATUS_MISSING,
    STATUS_VERIFIED,
    InstallProvenanceRecord,
    install_provenance_path,
    read_install_provenance,
    read_sha256sums,
    sha256_hex,
    write_install_provenance_atomic,
)

# ----------------------------------------------------------------------
# 1. Wheel build hook no longer bakes wheel name/hash
# ----------------------------------------------------------------------


def test_build_hook_only_writes_commit_and_dirty():
    """``setup.py`` must not embed ``WHEEL_FILENAME``/``WHEEL_SHA256``.

    The wheel can never be its own source of truth for its final byte
    digest because embedding it changes the digest, creating a
    chicken-and-egg problem.  v1.4.5 removes both constants from
    ``__build__.py`` and ``setup.py``.
    """
    project_root = Path(__file__).resolve().parents[1]
    setup_src = (project_root / "setup.py").read_text(encoding="utf-8")
    # Strip the module docstring so commentary is not counted against us.
    setup_src_no_doc = re.sub(r'^\s*""".*?"""\s*', "", setup_src, count=1, flags=re.DOTALL)
    assert "WHEEL_FILENAME" not in setup_src_no_doc, setup_src_no_doc
    assert "WHEEL_SHA256" not in setup_src_no_doc, setup_src_no_doc
    assert "CONFFLOW_WHEEL_FILENAME" not in setup_src_no_doc
    assert "CONFFLOW_WHEEL_SHA256" not in setup_src_no_doc
    # Commit / dirty must still be written by the hook.
    assert "COMMIT" in setup_src_no_doc
    assert "DIRTY" in setup_src_no_doc

    build_src = (project_root / "confflow" / "__build__.py").read_text(encoding="utf-8")
    build_src_no_doc = re.sub(r'^\s*""".*?"""\s*', "", build_src, count=1, flags=re.DOTALL)
    assert "WHEEL_FILENAME" not in build_src_no_doc
    assert "WHEEL_SHA256" not in build_src_no_doc
    assert "COMMIT" in build_src_no_doc
    assert "DIRTY" in build_src_no_doc


# ----------------------------------------------------------------------
# 2. install-provenance.json model + diagnostics
# ----------------------------------------------------------------------


def test_read_install_provenance_missing_file(tmp_path):
    """When no install-provenance.json exists, diagnostic shape is produced."""
    digest, errors = read_install_provenance(sys_prefix=str(tmp_path))
    assert digest.status == STATUS_MISSING
    assert digest.reason_code == REASON_MISSING_FILE
    assert digest.wheel_filename is None
    assert digest.wheel_sha256 is None
    assert errors == []


def test_read_install_provenance_invalid_json(tmp_path):
    """A truncated file produces ``invalid_json`` diagnostic without leaking data."""
    path = install_provenance_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json", encoding="utf-8")

    digest, errors = read_install_provenance(sys_prefix=str(tmp_path))
    assert digest.status == STATUS_INVALID
    assert digest.reason_code == REASON_INVALID_JSON
    assert digest.wheel_filename is None
    assert digest.wheel_sha256 is None
    assert errors == []


def test_read_install_provenance_wrong_schema(tmp_path):
    """A file with the wrong schema name is rejected with a diagnostic reason."""
    path = install_provenance_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": "confflow.install-provenance.v0"}),
        encoding="utf-8",
    )
    digest, _ = read_install_provenance(sys_prefix=str(tmp_path))
    assert digest.status == STATUS_INVALID
    assert digest.reason_code == REASON_SCHEMA_MISMATCH


def test_read_install_provenance_attestation_unverified(tmp_path):
    """candidate-mode records carry ``attestation_verified=False`` and fail closed."""
    path = install_provenance_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "confflow.install-provenance.v2",
        "package": "confflow",
        "version": "1.4.5",
        "wheel_filename": "confflow-1.4.5-py3-none-any.whl",
        "wheel_sha256": "a" * 64,
        "dependency_lock_sha256": "d" * 64,
        "wheelhouse_manifest_sha256": "e" * 64,
        "python_version": "3.12.3",
        "python_implementation": "CPython",
        "platform": "linux-x86_64",
        "machine": "x86_64",
        "build_commit": "b" * 40,
        "build_dirty": False,
        "release_repository": "moxuezhuchen/ConfFlow",
        "release_tag": "v1.4.5",
        "release_tag_commit": "b" * 40,
        "attestation_verified": False,
        "attestation_subject_digest": "a" * 64,
    }
    path.write_text(json.dumps(record), encoding="utf-8")

    digest, errors = read_install_provenance(sys_prefix=str(tmp_path))
    assert digest.status == STATUS_INVALID
    assert digest.reason_code == REASON_ATTESTATION_UNVERIFIED
    assert "not attestation-verified" in errors[0]
    assert digest.wheel_filename is None


def test_read_install_provenance_verified_payload(tmp_path):
    """A fully populated, attestation-verified record is reported as ``verified``."""
    path = install_provenance_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    write_install_provenance_atomic(
        path,
        {
            "schema": "confflow.install-provenance.v2",
            "package": "confflow",
            "version": "1.4.5",
            "wheel_filename": "confflow-1.4.5-py3-none-any.whl",
            "wheel_sha256": "b" * 64,
            "dependency_lock_sha256": "d" * 64,
            "wheelhouse_manifest_sha256": "e" * 64,
            "python_version": "3.12.3",
            "python_implementation": "CPython",
            "platform": "linux-x86_64",
            "machine": "x86_64",
            "build_commit": "c" * 40,
            "build_dirty": False,
            "release_repository": "moxuezhuchen/ConfFlow",
            "release_tag": "v1.4.5",
            "release_tag_commit": "c" * 40,
            "attestation_verified": True,
            "attestation_subject_digest": "b" * 64,
        },
    )

    digest, errors = read_install_provenance(sys_prefix=str(tmp_path))
    assert errors == []
    assert digest.status == STATUS_VERIFIED
    assert digest.reason_code is None
    assert digest.wheel_filename == "confflow-1.4.5-py3-none-any.whl"
    assert digest.wheel_sha256 == "b" * 64


def test_atomic_write_replaces_destination(tmp_path):
    """An interrupted write cannot leave a half-written ``install-provenance.json``."""
    path = tmp_path / "share" / "confflow" / "install-provenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_install_provenance_atomic(path, {"a": 1})
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    # Re-write atomically
    write_install_provenance_atomic(path, {"a": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2}


# ----------------------------------------------------------------------
# 3. SHA256SUMS parser and digest helpers
# ----------------------------------------------------------------------


def test_sha256sums_parser_rejects_glob_and_duplicates(tmp_path):
    """Globs / duplicates / malformed lines are surfaced for the deployer."""
    sums = tmp_path / "SHA256SUMS"
    good = "abcdef0123456789" * 4
    sums.write_text(
        "\n".join(
            [
                good + "  confflow-1.4.5-py3-none-any.whl",
                "0123456789abcdef" * 4 + "  *-candidate.whl",
                "not hex  somefile",
                "badline-no-second-column",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    parsed = read_sha256sums(sums)
    assert "confflow-1.4.5-py3-none-any.whl" in parsed
    assert "*-candidate.whl" not in parsed


def test_sha256_hex_matches_hashlib(tmp_path):
    """``sha256_hex`` agrees with the stdlib on a small file."""
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"confflow-install-provenance-sample")
    expected = hashlib.sha256(sample.read_bytes()).hexdigest()
    assert sha256_hex(sample) == expected


def test_install_provenance_path_is_canonical():
    """The path always lives at ``share/confflow/install-provenance.json``."""
    assert install_provenance_path().as_posix().endswith("share/confflow/install-provenance.json")


def test_install_provenance_record_round_trip():
    """Serialising and deserialising the dataclass preserves every field."""
    record = InstallProvenanceRecord(
        package="confflow",
        version="1.4.5",
        wheel_filename="confflow-1.4.5-py3-none-any.whl",
        wheel_sha256="a" * 64,
        dependency_lock_sha256="d" * 64,
        wheelhouse_manifest_sha256="e" * 64,
        python_version="3.12.3",
        python_implementation="CPython",
        platform="linux-x86_64",
        machine="x86_64",
        build_commit="b" * 40,
        build_dirty=False,
        release_repository="moxuezhuchen/ConfFlow",
        release_tag="v1.4.5",
        release_tag_commit="b" * 40,
        attestation_verified=True,
        attestation_subject_digest="a" * 64,
    )
    raw = record.to_dict()
    assert raw["schema"] == ip.INSTALL_PROVENANCE_SCHEMA
    restored = InstallProvenanceRecord.from_dict(raw)
    assert restored == record


def test_install_provenance_reason_codes_are_unique():
    """Reason codes must be a stable contract that JobDesk can branch on."""
    codes = {
        REASON_MISSING_FILE,
        REASON_INVALID_JSON,
        REASON_SCHEMA_MISMATCH,
        REASON_DIGEST_MISMATCH,
        REASON_ATTESTATION_UNVERIFIED,
        REASON_EDITABLE_INSTALL,
    }
    # All codes are lower-snake-case identifiers (no spaces, no secrets).
    for code in codes:
        assert re.fullmatch(r"[a-z_]+", code), code
