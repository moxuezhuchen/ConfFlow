"""Typed errors for the frozen control-protocol v1 registry."""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Machine-readable v1 errors; adapters map these directly to the wire."""

    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    UNKNOWN_RUN = "unknown_run"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    INVALID_CHECKPOINT = "invalid_checkpoint"
    ALREADY_RUNNING = "already_running"
    TERMINAL_RUN = "terminal_run"
    EXECUTABLE_IDENTITY_MISMATCH = "executable_identity_mismatch"
    ARTIFACT_PATH_INVALID = "artifact_path_invalid"
    ARTIFACT_INTEGRITY_FAILED = "artifact_integrity_failed"
    INTERNAL = "internal"


class ExecutionServiceError(RuntimeError):
    """A typed application error with a stable machine-readable code."""

    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class RepositoryConflict(RuntimeError):
    """Internal optimistic-CAS conflict; callers must reread and retry."""


class RepositoryMutationError(RuntimeError):
    """Internal atomic-write failure before an aggregate is committed."""
