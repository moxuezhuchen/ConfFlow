"""Typed V4 artifact projection for service/control_worker.

COMPLETED-only projection of run_result.json (confflow.run_result_manifest.v1, content_schema=RESULT_MANIFEST_SCHEMA,
requires content_schema/run_id/status/definition_digest/provenance/steps/analyses/artifacts; entry {role,checksum,locator})
onto Artifact(terminal=role,path=locator,sha256 sans prefix lower-cased,size from disk,schema=RESULT_MANIFEST_SCHEMA).
Generation (confflow.run_generation.v1 statuses {running,completed,partial,failed,cancelled}): absent -> None, corrupt -> CorruptStateError, never absent.
Invariant: arbitration.current_terminal_status is sole terminal authority; mismatch is corrupt.
Binding fail-closed: run_id/definition_digest/generation_id must agree with caller and generation;
completed manifest behind non-completed generation fails; empty artifact list refused.
Safety fail-closed: locator run-relative POSIX canonical (no absolute/drive/NUL/.././empty), stays under
work root, regular file, sha256:<hex> bytes match; terminal meets identifier grammar.
State mapping: failed/partial/cancelled never projected (INVALID_STATE_TRANSITION); running/not-published/
absent is INVALID_STATE_TRANSITION (`No run_result.json published yet`), never ARTIFACT_INTEGRITY_FAILED; corrupt JSON/schema/checksum/binding mismatches are ARTIFACT_INTEGRITY_FAILED; explicit-missing-completed with terminal expected is integrity, plain absent stays not-published.
"""

from __future__ import annotations

import json
from pathlib import Path

from confflow.producer.boundary import RESULT_MANIFEST_SCHEMA

from .errors import ErrorCode, ExecutionServiceError
from .models import Artifact

V4_RUN_RESULT_FILENAME = "run_result.json"
V4_RUN_RESULT_SCHEMA = RESULT_MANIFEST_SCHEMA
V4_RUN_GENERATION_FILENAME = "run_generation.json"
V4_RUN_GENERATION_SCHEMA = "confflow.run_generation.v1"

_V4_TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})
_GENERATION_STATUSES = frozenset({"running", "completed", "partial", "failed", "cancelled"})
_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
_HEX = frozenset("0123456789abcdef")


def _is_identifier(value: str) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 128
        and value[0].isalnum()
        and all(char in _ID_CHARS for char in value)
    )


def _is_hex_digest(value: str) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in _HEX for c in value)


def _integrity(message: str) -> ExecutionServiceError:
    return ExecutionServiceError(ErrorCode.ARTIFACT_INTEGRITY_FAILED, message)


def _not_published(work_dir: str) -> ExecutionServiceError:
    return ExecutionServiceError(
        ErrorCode.INVALID_STATE_TRANSITION,
        f"No run_result.json published yet: {work_dir}",
    )


def _not_completed(status: str) -> ExecutionServiceError:
    return ExecutionServiceError(
        ErrorCode.INVALID_STATE_TRANSITION,
        f"V4 manifest status is {status!r}, not 'completed'; "
        "use the terminal projection, not the completed-artifact projection",
    )


def _path_invalid(message: str) -> ExecutionServiceError:
    return ExecutionServiceError(ErrorCode.ARTIFACT_PATH_INVALID, message)


def v4_publication_state(work_dir: str) -> str:
    """Return the durable V4 publication state without raising for absence."""
    manifest_path = Path(work_dir) / V4_RUN_RESULT_FILENAME
    if not manifest_path.is_file():
        try:
            from confflow.persistence.generation import load_run_generation
        except Exception:  # pragma: no cover - import-time safety
            return "not-published"
        try:
            generation = load_run_generation(work_dir)
        except Exception:
            return "corrupt"
        if generation is not None and generation.status == "running":
            return "running"
        return "not-published"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "corrupt"
    if not isinstance(payload, dict):
        return "corrupt"
    status = payload.get("status")
    if not isinstance(status, str):
        return "corrupt"
    normalized = status.strip().lower()
    if normalized in _V4_TERMINAL_STATUSES:
        return normalized
    return "corrupt"


def load_v4_completed_artifacts(
    work_dir: str,
    *,
    expected_run_id: str | None = None,
    expected_definition_digest: str | None = None,
    expected_generation_id: str | None = None,
) -> tuple[Artifact, ...]:
    """Project a COMPLETED V4 manifest onto service :class:`Artifact` tuples."""
    root = Path(work_dir).resolve(strict=False)
    manifest_path = Path(work_dir) / V4_RUN_RESULT_FILENAME
    if not manifest_path.is_file():
        # Distinguish "not yet published" from "completed but missing".
        # A terminal generation/manifest winner that claims completion
        # without bytes is integrity failure; a running/absent generation
        # is simply not-published.  Check the durable sources best-effort.
        try:
            from confflow.persistence import arbitration as _arbitration
            from confflow.persistence.generation import load_run_generation as _load_gen
        except Exception:  # pragma: no cover - import-time safety
            raise _not_published(str(work_dir)) from None
        try:
            generation = _load_gen(work_dir)
        except Exception as error:
            raise _integrity(f"run generation record is corrupt: {error}") from error
        try:
            winner = _arbitration.current_terminal_status(work_dir)
        except Exception:
            winner = None
        if winner == "completed" or (generation is not None and generation.status == "completed"):
            raise _integrity(f"Completed V4 run is missing its manifest: {manifest_path}")
        raise _not_published(str(work_dir)) from None

    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as error:
        raise _integrity(f"cannot read V4 manifest: {manifest_path}: {error}") from error
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise _integrity(f"V4 manifest is not valid JSON: {manifest_path}: {error}") from error
    if not isinstance(payload, dict):
        raise _integrity(f"V4 manifest must be an object: {manifest_path}")

    if payload.get("content_schema") != V4_RUN_RESULT_SCHEMA:
        raise _integrity(
            f"unsupported V4 content_schema {payload.get('content_schema')!r}; "
            f"expected {V4_RUN_RESULT_SCHEMA!r}"
        )
    for field in (
        "run_id",
        "status",
        "definition_digest",
        "provenance",
        "steps",
        "analyses",
        "artifacts",
    ):
        if field not in payload:
            raise _integrity(f"V4 manifest is missing required field {field!r}: {manifest_path}")

    status = payload.get("status")
    normalized = str(status).strip().lower() if isinstance(status, str) else ""
    if normalized not in _V4_TERMINAL_STATUSES:
        raise _integrity(f"V4 manifest carries an unknown status {status!r}: {manifest_path}")
    if normalized != "completed":
        raise _not_completed(normalized)

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise _integrity(f"V4 manifest run_id must be a non-empty string: {manifest_path}")
    if expected_run_id is not None and run_id != expected_run_id:
        raise _integrity(
            f"V4 manifest run_id {run_id!r} does not match expected {expected_run_id!r}"
        )

    definition_digest = payload.get("definition_digest")
    if (
        not isinstance(definition_digest, str)
        or not definition_digest.startswith("sha256:")
        or not _is_hex_digest(definition_digest.split(":", 1)[1].lower())
    ):
        raise _integrity(f"V4 manifest definition_digest must be a sha256 digest: {manifest_path}")
    if expected_definition_digest is not None and definition_digest != expected_definition_digest:
        raise _integrity(
            f"V4 manifest definition_digest {definition_digest!r} does not match "
            f"expected {expected_definition_digest!r}"
        )

    manifest_generation_id = payload.get("generation_id")
    if manifest_generation_id is not None and (
        not isinstance(manifest_generation_id, str) or not manifest_generation_id.strip()
    ):
        raise _integrity(f"V4 manifest generation_id must be a string or null: {manifest_path}")
    if expected_generation_id is not None and manifest_generation_id is not None:
        if manifest_generation_id != expected_generation_id:
            raise _integrity(
                f"V4 manifest generation_id {manifest_generation_id!r} does not match "
                f"expected {expected_generation_id!r}"
            )

    # Binding against the durable generation record when it exists.
    try:
        from confflow.persistence.generation import load_run_generation
    except Exception as error:  # pragma: no cover - import-time safety
        raise _integrity(f"cannot load generation record: {error}") from error
    try:
        generation = load_run_generation(work_dir)
    except Exception as error:
        raise _integrity(f"run generation record is corrupt: {error}") from error
    if generation is not None:
        if generation.run_id != run_id:
            raise _integrity(
                f"V4 manifest run_id {run_id!r} does not match "
                f"generation run_id {generation.run_id!r}"
            )
        if (
            generation.definition_digest is not None
            and generation.definition_digest != definition_digest
        ):
            raise _integrity(
                f"V4 manifest definition_digest {definition_digest!r} does not match "
                f"generation {generation.definition_digest!r}"
            )
        if (
            manifest_generation_id is not None
            and generation.generation_id != manifest_generation_id
        ):
            raise _integrity(
                f"V4 manifest generation_id {manifest_generation_id!r} does not match "
                f"generation {generation.generation_id!r}"
            )
        if (
            expected_generation_id is not None
            and generation.generation_id != expected_generation_id
        ):
            raise _integrity(
                f"generation {generation.generation_id!r} does not match "
                f"expected {expected_generation_id!r}"
            )
        if generation.status != "completed":
            raise _integrity(
                f"V4 manifest reports completed but generation is {generation.status!r}"
            )

    # Durable winner agreement (same rule as _commit_v4_terminal).
    try:
        from confflow.persistence import arbitration

        winner = arbitration.current_terminal_status(work_dir)
    except Exception as error:
        raise _integrity(f"cannot read terminal arbitration: {error}") from error
    if winner is not None and winner != "completed":
        raise _integrity(
            f"terminal arbitration mismatch: durable winner is {winner!r} "
            "but the published status is 'completed'"
        )

    entries = payload.get("artifacts")
    if not isinstance(entries, list) or not entries:
        raise _integrity(f"V4 completed manifest carries no artifacts: {manifest_path}")

    artifacts: list[Artifact] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _integrity(f"V4 artifact entry must be an object: {entry!r}")
        unknown = sorted(set(entry) - {"role", "checksum", "locator", "subject", "fetch"})
        if unknown:
            raise _integrity(f"V4 artifact entry has unknown fields: {', '.join(unknown)}")
        role = entry.get("role")
        checksum = entry.get("checksum")
        locator = entry.get("locator")
        if not isinstance(role, str) or not role.strip():
            raise _integrity(f"V4 artifact role must be a non-empty string: {entry!r}")
        if not _is_identifier(role):
            raise _integrity(
                f"V4 artifact role {role!r} is not a valid service terminal identifier"
            )
        if (
            not isinstance(checksum, str)
            or not checksum.lower().startswith("sha256:")
            or not _is_hex_digest(checksum.split(":", 1)[1].lower())
        ):
            raise _integrity(f"V4 artifact checksum must be a sha256 digest: {entry!r}")
        if not isinstance(locator, str) or not locator or locator.strip() != locator:
            raise _path_invalid(f"V4 artifact locator must be a non-empty string: {entry!r}")
        if locator.startswith("/") or ".." in locator.split("/") or "\x00" in locator:
            raise _path_invalid(f"V4 artifact locator is not run-relative: {locator!r}")
        if ":" in locator.split("/")[0] and len(locator.split("/")[0]) == 1:
            raise _path_invalid(f"V4 artifact locator carries a drive prefix: {locator!r}")
        for segment in locator.split("/"):
            if not segment or segment in {".", ".."}:
                raise _path_invalid(f"V4 artifact locator has an empty/dot segment: {locator!r}")
        candidate = (root / locator).resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            raise _path_invalid(f"V4 artifact locator escapes the work root: {locator!r}") from None
        if relative != locator:
            raise _path_invalid(
                f"V4 artifact locator is not canonical: {locator!r} (resolves to {relative!r})"
            )
        # Service canonical-path grammar (same as ExecutionService).
        if relative.startswith("/") or relative.endswith("/") or "//" in relative:
            raise _path_invalid(f"V4 artifact path is not canonical: {locator!r}")
        for segment in relative.split("/"):
            if (
                not segment
                or segment in {".", ".."}
                or not segment[0].isalnum()
                or any(char not in _ID_CHARS for char in segment)
            ):
                raise _path_invalid(f"V4 artifact path is not canonical: {locator!r}")
        if not candidate.is_file():
            raise _integrity(f"V4 artifact is missing: {locator!r}")
        claimed = str(checksum).lower()
        digest = __import__("hashlib").sha256(candidate.read_bytes()).hexdigest()
        actual = f"sha256:{digest}"
        if actual != claimed:
            raise _integrity(
                f"V4 artifact {locator!r} checksum does not match its bytes "
                f"(claimed {claimed!r}, computed {actual!r})"
            )
        artifacts.append(
            Artifact(
                terminal=role,
                path=relative,
                sha256=digest,
                size=candidate.stat().st_size,
                content_schema=V4_RUN_RESULT_SCHEMA,
            )
        )
    artifacts.sort(key=lambda item: (item.terminal, item.path))
    return tuple(artifacts)


__all__ = [
    "V4_RUN_GENERATION_FILENAME",
    "V4_RUN_GENERATION_SCHEMA",
    "V4_RUN_RESULT_FILENAME",
    "V4_RUN_RESULT_SCHEMA",
    "load_v4_completed_artifacts",
    "v4_publication_state",
]
