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
from .shared_fs_approval import ApprovalVerifier, SharedFilesystemApproval
from .sqlite import SQLiteExecutionRepository
from .state_root import RunPaths, StateRoot
from .synthetic_producer import (
    SYNTHETIC_ARTIFACT,
    SYNTHETIC_ARTIFACT_CONTENT,
    SYNTHETIC_ARTIFACT_PATH,
    SYNTHETIC_ARTIFACT_SCHEMA,
    SYNTHETIC_ARTIFACT_TERMINAL,
    SYNTHETIC_CHECKPOINT_ID,
    SyntheticProducerExecutor,
    open_synthetic_service,
)
from .workflow_adapter import (
    ServiceWorkflowExecutor,
    WorkflowRunSpec,
    build_workflow_service,
    open_control_service,
    run_workflow_through_service,
)

__all__ = [
    "Artifact",
    "ArtifactManifest",
    "ApprovalVerifier",
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
    "RunPaths",
    "ServiceWorkflowExecutor",
    "SQLiteExecutionRepository",
    "SharedFilesystemApproval",
    "StateRoot",
    "SYNTHETIC_ARTIFACT",
    "SYNTHETIC_ARTIFACT_CONTENT",
    "SYNTHETIC_ARTIFACT_PATH",
    "SYNTHETIC_ARTIFACT_SCHEMA",
    "SYNTHETIC_ARTIFACT_TERMINAL",
    "SYNTHETIC_CHECKPOINT_ID",
    "SyntheticProducerExecutor",
    "WorkflowRunSpec",
    "build_workflow_service",
    "open_control_service",
    "open_synthetic_service",
    "run_workflow_through_service",
]
