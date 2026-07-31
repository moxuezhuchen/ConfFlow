"""Public execution-domain API backed by one atomic aggregate repository."""

from .errors import ErrorCode, ExecutionServiceError
from .memory import InMemoryExecutionRepository
from .models import (
    Artifact,
    ArtifactManifest,
    CancelReceipt,
    CancelRequest,
    Checkpoint,
    EventPage,
    ExecutableIdentity,
    ExecutionAggregate,
    ExecutionEvent,
    LaunchReceipt,
    LaunchRequest,
    PrepareRequest,
    RunSnapshot,
    RunState,
)
from .service import ExecutionLifecycle, ExecutionService

__all__ = [
    "Artifact",
    "ArtifactManifest",
    "CancelReceipt",
    "CancelRequest",
    "Checkpoint",
    "ErrorCode",
    "EventPage",
    "ExecutableIdentity",
    "ExecutionAggregate",
    "ExecutionEvent",
    "ExecutionLifecycle",
    "ExecutionService",
    "ExecutionServiceError",
    "InMemoryExecutionRepository",
    "LaunchReceipt",
    "LaunchRequest",
    "PrepareRequest",
    "RunSnapshot",
    "RunState",
]
