"""Public execution-domain API backed by one atomic aggregate repository.

The full public surface stays importable, but it is resolved lazily (PEP 562):
importing :mod:`confflow.application.execution` no longer eagerly executes the
durable-service aggregate. Import the concrete submodule when you need the
implementation.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ErrorCode": (".errors", "ErrorCode"),
    "ExecutionServiceError": (".errors", "ExecutionServiceError"),
    "Artifact": (".models", "Artifact"),
    "ArtifactManifest": (".models", "ArtifactManifest"),
    "CancelReceipt": (".models", "CancelReceipt"),
    "CancelRequest": (".models", "CancelRequest"),
    "Checkpoint": (".models", "Checkpoint"),
    "EventPage": (".models", "EventPage"),
    "ExecutableIdentity": (".models", "ExecutableIdentity"),
    "ExecutionAggregate": (".models", "ExecutionAggregate"),
    "ExecutionEvent": (".models", "ExecutionEvent"),
    "LaunchReceipt": (".models", "LaunchReceipt"),
    "LaunchRequest": (".models", "LaunchRequest"),
    "PrepareRequest": (".models", "PrepareRequest"),
    "RunSnapshot": (".models", "RunSnapshot"),
    "RunState": (".models", "RunState"),
    "ExecutionLifecycle": (".service", "ExecutionLifecycle"),
    "ExecutionService": (".service", "ExecutionService"),
    "ApprovalVerifier": (".shared_fs_approval", "ApprovalVerifier"),
    "SharedFilesystemApproval": (".shared_fs_approval", "SharedFilesystemApproval"),
    "SQLiteExecutionRepository": (".sqlite", "SQLiteExecutionRepository"),
    "RunPaths": (".state_root", "RunPaths"),
    "StateRoot": (".state_root", "StateRoot"),
    "ServiceWorkflowExecutor": (".workflow_adapter", "ServiceWorkflowExecutor"),
    "WorkflowRunSpec": (".workflow_adapter", "WorkflowRunSpec"),
    "build_workflow_service": (".workflow_adapter", "build_workflow_service"),
    "open_control_service": (".workflow_adapter", "open_control_service"),
    "run_workflow_through_service": (".workflow_adapter", "run_workflow_through_service"),
}

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
    "WorkflowRunSpec",
    "build_workflow_service",
    "open_control_service",
    "run_workflow_through_service",
]


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(export[0], package=__name__)
    value = getattr(module, export[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
