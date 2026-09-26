#!/usr/bin/env python3

"""Worker-handoff V2 file-protocol tests (V4-4).

Covers ``confflow.remote.handoff`` (atomic write/read round-trip, digest
tamper detection, schema and run identity, size bounds, strict JSON and
strict model validation, launch-token charset, symlink and permission
fail-closed behavior) and ``confflow.remote.schema`` (thin JSON Schema
wrappers with no duplicated field definitions).

Legacy V1 envelope keys appear below only as string literals inside
negative-case payload dicts; the V2 implementation itself never defines or
accepts them.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.domain.canonical import canonical_json_bytes
from confflow.remote.envelope import (
    HANDOFF_SCHEMA_V2,
    MAX_HANDOFF_BYTES,
    RESULT_SCHEMA_V2,
    EnvironmentRequest,
    ExecutionDefinition,
    InputBundleManifest,
    WorkerHandoffV2,
)
from confflow.remote.handoff import HandoffError, read_handoff_envelope, write_handoff_envelope
from confflow.remote.schema import SCHEMA_IDS, handoff_json_schema, result_json_schema

RUN_ID = "run-001"
STEP_ID = "s_opt"
TOKEN = "tok_abc-1.2"
DIGEST_A = "sha256:" + "aa" * 32
DIGEST_B = "sha256:" + "bb" * 32


def sample_handoff(*, token: str = TOKEN, run_id: str = RUN_ID) -> WorkerHandoffV2:
    """Build a minimal valid V2 envelope with a computed manifest digest.

    The frozen ``.new()`` constructor only agrees with the model validator
    when digest-covered fields are fully populated, so nested contracts are
    passed as ``mode="python"`` dumps (tuples preserved for strict
    validation; canonical bytes normalize them identically) alongside the
    explicit schema and protocol version.
    """
    execution = ExecutionDefinition(
        program="xtb",
        execution_adapter="standard",
        result_profile="standard",
        recovery="none",
        step_semantic_digest=DIGEST_B,
    )
    return WorkerHandoffV2.new(
        schema=HANDOFF_SCHEMA_V2,
        protocol_version="v2",
        run_id=run_id,
        step_id=STEP_ID,
        work_item_id=f"wi:{STEP_ID}:0001",
        logical_key=f"{STEP_ID}:0001",
        attempt_number=1,
        launch_token=token,
        work_item_digest=DIGEST_A,
        step_semantic_digest=DIGEST_B,
        producer_provenance={},
        environment_request=EnvironmentRequest(program="xtb").model_dump(mode="python"),
        execution=execution.model_dump(mode="python"),
        inputs=InputBundleManifest(entries=()).model_dump(mode="python"),
    )


def write_sample(worker_root: str, *, token: str = TOKEN) -> tuple[WorkerHandoffV2, str]:
    """Write a sample envelope and return the model plus its file path."""
    handoff = sample_handoff(token=token)
    path = write_handoff_envelope(handoff=handoff, worker_root=worker_root, launch_token=token)
    return handoff, path


def rewrite_payload(path: str, payload: dict[str, Any]) -> None:
    """Overwrite *path* with canonical bytes, preserving owner and mode bits."""
    with open(path, "wb") as handle:
        handle.write(canonical_json_bytes(payload))


class TestWriteReadRoundTrip:
    """Write then read preserves the exact validated envelope."""

    def test_round_trip_equality(self, tmp_path: Path) -> None:
        handoff, path = write_sample(str(tmp_path / "worker"))
        assert path == os.path.join(str(tmp_path / "worker"), "inbox", TOKEN, "handoff.json")
        assert os.path.isfile(path)
        loaded = read_handoff_envelope(path=path, expected_run_id=RUN_ID)
        assert loaded == handoff
        assert loaded.model_dump(mode="json") == handoff.model_dump(mode="json")

    def test_read_without_expected_run_id(self, tmp_path: Path) -> None:
        handoff, path = write_sample(str(tmp_path / "worker"))
        assert read_handoff_envelope(path=path) == handoff

    def test_file_holds_canonical_bytes(self, tmp_path: Path) -> None:
        handoff, path = write_sample(str(tmp_path / "worker"))
        with open(path, "rb") as handle:
            assert handle.read() == canonical_json_bytes(handoff.model_dump(mode="json"))

    def test_private_modes(self, tmp_path: Path) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        token_dir = os.path.dirname(path)
        assert stat.S_IMODE(os.stat(token_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(os.path.dirname(token_dir)).st_mode) == 0o700

    def test_handoff_error_is_value_error(self) -> None:
        assert issubclass(HandoffError, ValueError)


class TestDigestIntegrity:
    """Any byte-level or field-level tamper fails closed on read."""

    def test_flipped_raw_byte_rejected(self, tmp_path: Path) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        with open(path, "rb") as handle:
            raw = handle.read()
        assert b"run-001" in raw
        tampered = raw.replace(b"run-001", b"run-002", 1)
        assert tampered != raw
        with open(path, "wb") as handle:
            handle.write(tampered)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)

    def test_mutated_digest_dict_rejected(self, tmp_path: Path) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        with open(path, "rb") as handle:
            raw = handle.read()
        assert DIGEST_A.encode("utf-8") in raw
        tampered = raw.replace(DIGEST_A.encode("utf-8"), DIGEST_B.encode("utf-8"), 1)
        with open(path, "wb") as handle:
            handle.write(tampered)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)

    def test_wrong_work_item_digest_rejected(self, tmp_path: Path) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        handoff = sample_handoff()
        payload = handoff.model_dump(mode="json")
        payload["work_item_digest"] = DIGEST_B
        rewrite_payload(path, payload)
        with pytest.raises(HandoffError, match="digest"):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)


class TestIdentity:
    """Schema identity, run binding, and strict shape are enforced."""

    def test_schema_identity_mismatch_rejected(self, tmp_path: Path) -> None:
        handoff, path = write_sample(str(tmp_path / "worker"))
        payload = handoff.model_dump(mode="json")
        payload["schema"] = "confflow.control.worker-handoff.v1"
        rewrite_payload(path, payload)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)

    def test_run_id_mismatch_rejected(self, tmp_path: Path) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        with pytest.raises(HandoffError, match="run"):
            read_handoff_envelope(path=path, expected_run_id="run-999")

    def test_unknown_top_level_field_rejected(self, tmp_path: Path) -> None:
        handoff, path = write_sample(str(tmp_path / "worker"))
        payload = handoff.model_dump(mode="json")
        payload["unexpected_field"] = "nope"
        rewrite_payload(path, payload)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)

    def test_legacy_envelope_keys_rejected(self, tmp_path: Path) -> None:
        handoff, path = write_sample(str(tmp_path / "worker"))
        payload = handoff.model_dump(mode="json")
        payload["input_xyz"] = "/worker/attempt/input.xyz"
        payload["workflow_config"] = {"path": "/worker/attempt/config.conf"}
        payload["tasks"] = [{"task_id": "t1"}]
        rewrite_payload(path, payload)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)

    def test_strict_types_rejected(self, tmp_path: Path) -> None:
        handoff, path = write_sample(str(tmp_path / "worker"))
        payload = handoff.model_dump(mode="json")
        payload["attempt_number"] = "1"
        rewrite_payload(path, payload)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)


class TestMalformedAndOversized:
    """Unparseable, misshapen, absent, or oversized files fail closed."""

    @pytest.mark.parametrize(
        "raw",
        [
            b'{"schema": "confflow.control.worker-handoff.v2", ',
            b"not json at all",
            b"",
            b"\xff\xfe\x00bad-utf8",
            b"[1, 2, 3]",
            b"null",
            b"42",
        ],
    )
    def test_malformed_payloads_rejected(self, tmp_path: Path, raw: bytes) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        with open(path, "wb") as handle:
            handle.write(raw)
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=path, expected_run_id=RUN_ID)

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(HandoffError):
            read_handoff_envelope(
                path=str(tmp_path / "worker" / "inbox" / TOKEN / "handoff.json"),
                expected_run_id=RUN_ID,
            )

    def test_directory_path_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(HandoffError):
            read_handoff_envelope(path=str(tmp_path), expected_run_id=RUN_ID)

    def test_empty_path_rejected(self) -> None:
        with pytest.raises(HandoffError):
            read_handoff_envelope(path="", expected_run_id=RUN_ID)

    def test_oversized_file_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "handoff.json"
        with open(target, "wb") as handle:
            handle.write(b"{" + b"x" * MAX_HANDOFF_BYTES)
        os.chmod(target, 0o600)
        with pytest.raises(HandoffError, match="maximum size"):
            read_handoff_envelope(path=str(target))


class TestLaunchToken:
    """Only lease-style tokens may name an inbox delivery directory."""

    @pytest.mark.parametrize(
        "token",
        [
            "../escape",
            "..",
            "/absolute",
            "a/b",
            "",
            "-leading-dash",
            ".leading-dot",
            "_leading-underscore",
            "has space",
            "semi;colon",
            "colon:token",
            "back\\slash",
        ],
    )
    def test_traversal_and_unsafe_tokens_rejected(self, tmp_path: Path, token: str) -> None:
        with pytest.raises(HandoffError):
            write_handoff_envelope(
                handoff=sample_handoff(token=TOKEN),
                worker_root=str(tmp_path / "worker"),
                launch_token=token,
            )

    @pytest.mark.parametrize("token", ["a", "A1", "tok_abc-1.2", "x.y_z-9", "0"])
    def test_valid_tokens_round_trip(self, tmp_path: Path, token: str) -> None:
        handoff = sample_handoff(token=token)
        path = write_handoff_envelope(
            handoff=handoff, worker_root=str(tmp_path / "worker"), launch_token=token
        )
        assert os.path.isfile(path)
        assert read_handoff_envelope(path=path, expected_run_id=RUN_ID) == handoff

    def test_non_handoff_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(HandoffError):
            write_handoff_envelope(
                handoff="not-an-envelope",  # type: ignore[arg-type]
                worker_root=str(tmp_path / "worker"),
                launch_token=TOKEN,
            )


class TestSymlinkAndPermissions:
    """Symlinks and lax permissions fail closed on both write and read."""

    @pytest.mark.skipif(os.name != "posix", reason="symlink test requires POSIX")
    def test_symlink_worker_root_rejected(self, tmp_path: Path) -> None:
        real = tmp_path / "real-worker"
        real.mkdir()
        link = tmp_path / "linked-worker"
        os.symlink(str(real), str(link))
        with pytest.raises(HandoffError):
            write_handoff_envelope(
                handoff=sample_handoff(), worker_root=str(link), launch_token=TOKEN
            )

    @pytest.mark.skipif(os.name != "posix", reason="symlink test requires POSIX")
    def test_symlink_token_dir_rejected(self, tmp_path: Path) -> None:
        worker_root = str(tmp_path / "worker")
        inbox = os.path.join(worker_root, "inbox")
        os.makedirs(inbox, mode=0o700)
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(str(outside), os.path.join(inbox, TOKEN))
        with pytest.raises(HandoffError):
            write_handoff_envelope(
                handoff=sample_handoff(), worker_root=worker_root, launch_token=TOKEN
            )

    @pytest.mark.skipif(os.name != "posix", reason="symlink test requires POSIX")
    def test_symlink_file_rejected(self, tmp_path: Path) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        backup = path + ".real"
        os.rename(path, backup)
        try:
            os.symlink(backup, path)
            with pytest.raises(HandoffError):
                read_handoff_envelope(path=path, expected_run_id=RUN_ID)
        finally:
            if os.path.islink(path):
                os.unlink(path)
                os.rename(backup, path)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits required")
    @pytest.mark.skipif(
        getattr(os, "geteuid", lambda: -1)() == 0,
        reason="root bypasses file permissions",
    )
    def test_group_writable_file_rejected(self, tmp_path: Path) -> None:
        _, path = write_sample(str(tmp_path / "worker"))
        os.chmod(path, 0o660)
        try:
            with pytest.raises(HandoffError, match="group/world"):
                read_handoff_envelope(path=path, expected_run_id=RUN_ID)
        finally:
            os.chmod(path, 0o600)


class TestSchemaModule:
    """JSON Schema views are thin wrappers over the frozen models."""

    def test_schema_ids(self) -> None:
        assert SCHEMA_IDS == (HANDOFF_SCHEMA_V2, RESULT_SCHEMA_V2)
        assert SCHEMA_IDS == (
            "confflow.control.worker-handoff.v2",
            "confflow.control.worker-result.v2",
        )
        assert RESULT_SCHEMA_V2 == "confflow.control.worker-result.v2"

    def test_handoff_json_schema(self) -> None:
        schema = handoff_json_schema()
        assert schema == WorkerHandoffV2.model_json_schema()
        assert isinstance(schema, dict)
        assert schema["properties"]["run_id"]["type"] == "string"
        assert "manifest_digest" in schema["properties"]

    def test_result_json_schema(self) -> None:
        from confflow.remote.envelope import ResultBundle

        schema = result_json_schema()
        assert schema == ResultBundle.model_json_schema()
        assert isinstance(schema, dict)
        assert "bundle_digest" in schema["properties"]

    def test_no_duplicated_field_definitions(self) -> None:
        import confflow.remote.schema as schema_module

        source = Path(str(schema_module.__file__)).read_text(encoding="utf-8")
        for field in ("manifest_digest", "bundle_digest", "work_item_digest", "launch_token"):
            assert f'"{field}"' not in source and f"'{field}'" not in source, field


class TestModuleHygiene:
    """The file protocol stays minimal: stdlib plus canonical plus envelope."""

    LEGACY_TOKENS = ("workflow_config", "input_xyz", "output_path", "worker-handoff.v1")

    def test_no_legacy_tokens_in_implementation(self) -> None:
        import confflow.remote.handoff as handoff_module
        import confflow.remote.schema as schema_module

        for module in (handoff_module, schema_module):
            source = Path(str(module.__file__)).read_text(encoding="utf-8")
            lowered = source.lower()
            assert "yaml" not in lowered, module.__name__
            for token in self.LEGACY_TOKENS:
                assert token not in source, (module.__name__, token)

    def test_minimal_public_surface(self) -> None:
        import confflow.remote.handoff as handoff_module
        import confflow.remote.schema as schema_module

        assert set(handoff_module.__all__) == {
            "HandoffError",
            "read_handoff_envelope",
            "write_handoff_envelope",
        }
        assert set(schema_module.__all__) == {
            "SCHEMA_IDS",
            "handoff_json_schema",
            "result_json_schema",
        }

    def test_import_stays_minimal(self) -> None:
        script = (
            "import sys; import confflow.remote.handoff; import confflow.remote.schema; "
            "forbidden = [m for m in sys.modules "
            "if m.startswith('confflow.calc') or m.startswith('confflow.core') "
            "or m.startswith('confflow.config') or m.startswith('confflow.execution') "
            "or m.startswith('confflow.workflow') or m == 'confflow.worker_handoff']; "
            "assert not forbidden, forbidden"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
