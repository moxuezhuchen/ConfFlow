"""Pure execution-domain validation and policy helpers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import PurePosixPath

from .errors import ErrorCode, ExecutionServiceError
from .models import Artifact, ExecutableIdentity, PrepareRequest

_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def terminal_error(run_id: str) -> ExecutionServiceError:
    """Construct the stable terminal-run error."""
    return ExecutionServiceError(ErrorCode.TERMINAL_RUN, f"Run is terminal: {run_id}")


def validate_prepare(request: PrepareRequest) -> None:
    """Validate frozen v1 identifiers and digests before durable creation."""
    for value in (request.run_id, request.idempotency_key):
        if not is_identifier(value):
            raise ExecutionServiceError(
                ErrorCode.INVALID_REQUEST, "Invalid run ID or idempotency key"
            )
    for digest in (
        request.request_digest,
        request.workflow_config_digest,
        request.input_manifest_digest,
    ):
        if not is_digest(digest):
            raise ExecutionServiceError(ErrorCode.INVALID_REQUEST, "Invalid request digest")
    if not is_digest(request.expected_executable_identity.sha256):
        raise ExecutionServiceError(ErrorCode.INVALID_REQUEST, "Invalid executable identity digest")


def is_identifier(value: str) -> bool:
    """Match the frozen v1 identifier grammar."""
    return (
        bool(value)
        and len(value) <= 128
        and value[0].isalnum()
        and all(char in _ID_CHARS for char in value)
    )


def is_digest(value: str) -> bool:
    """Match a lower-case SHA-256 hex digest."""
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def identities_match(expected: ExecutableIdentity, measured: ExecutableIdentity) -> bool:
    """Require the hash and every supplied optional identity dimension to agree."""
    return (
        expected.sha256 == measured.sha256
        and (expected.realpath is None or expected.realpath == measured.realpath)
        and (expected.device_inode is None or expected.device_inode == measured.device_inode)
    )


def parse_cursor(cursor: str) -> int:
    """Decode the only stable cursor format emitted by this aggregate."""
    if len(cursor) != 21 or not cursor.startswith("r") or not cursor[1:].isdigit():
        raise ExecutionServiceError(
            ErrorCode.INVALID_REQUEST, f"Unknown or expired cursor: {cursor}"
        )
    return int(cursor[1:])


def validated_artifacts(artifacts: Sequence[Artifact]) -> tuple[Artifact, ...]:
    """Validate canonical relative targets inside the terminal aggregate mutation."""
    seen: set[tuple[str, str]] = set()
    result: list[Artifact] = []
    for artifact in artifacts:
        normalized = canonical_path(artifact.path)
        key = (artifact.terminal, normalized)
        if (
            not is_identifier(artifact.terminal)
            or key in seen
            or not is_digest(artifact.sha256)
            or artifact.size < 0
            or not artifact.content_schema
        ):
            raise ExecutionServiceError(
                ErrorCode.ARTIFACT_PATH_INVALID, "Invalid artifact manifest"
            )
        seen.add(key)
        result.append(replace(artifact, path=normalized))
    return tuple(sorted(result, key=lambda item: (item.terminal, item.path)))


def canonical_path(path: str) -> str:
    """Enforce a canonical regular-file relative POSIX path."""
    if not path or path.startswith("/") or path.endswith("/") or "//" in path:
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_PATH_INVALID, f"Invalid artifact path: {path}"
        )
    segments = path.split("/")
    if any(
        not segment
        or segment in {".", ".."}
        or not segment[0].isalnum()
        or any(char not in _ID_CHARS for char in segment)
        for segment in segments
    ):
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_PATH_INVALID, f"Invalid artifact path: {path}"
        )
    normalized = PurePosixPath(path).as_posix()
    if normalized != path:
        raise ExecutionServiceError(
            ErrorCode.ARTIFACT_PATH_INVALID, f"Invalid artifact path: {path}"
        )
    return normalized


# Keep the old private spellings available to callers that imported service helpers.
_terminal_error = terminal_error
_validate_prepare = validate_prepare
_is_identifier = is_identifier
_is_digest = is_digest
_identities_match = identities_match
_parse_cursor = parse_cursor
_validated_artifacts = validated_artifacts
_canonical_path = canonical_path
