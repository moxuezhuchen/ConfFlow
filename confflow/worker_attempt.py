"""Execute one already-leased producer worker attempt.

The control-worker process owns handoff validation, staging, lease lifetime,
recovery, and cancellation.  This module owns only the narrow adapter handoff
after a lease has been acquired: constructing the typed workflow specification,
building the service, consuming the existing queued launch, and waiting for
that one executor attempt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from .application.execution.models import TERMINAL_STATES, RunSnapshot, RunState
from .application.execution.state_root import StateRoot
from .application.execution.workflow_adapter import WorkflowRunner, WorkflowRunSpec


class AttemptService(Protocol):
    """Service surface needed to consume one existing launch intent."""

    def consume_queued_launch(self, run_id: str) -> RunSnapshot:
        """Consume the producer-owned queued launch intent."""


class AttemptExecutor(Protocol):
    """Executor surface needed to wait for one consumed launch."""

    def wait(self) -> None:
        """Wait for the attempt and re-raise its original failure."""


ServiceBuilder = Callable[..., tuple[AttemptService, AttemptExecutor]]


def run_worker_attempt(
    *,
    root: StateRoot,
    run_id: str,
    staged_config: str,
    staged_tasks: Sequence[Mapping[str, str]],
    resume: bool,
    workflow_runner: WorkflowRunner,
    service_builder: ServiceBuilder,
) -> RunState | None:
    """Consume and wait for one queued attempt after the caller acquired its lease.

    None means that the executor wait completed and the caller should read
    the durable repository projection.  A terminal snapshot is returned
    without waiting, because a terminal queued call is an attach-only result.
    Builder, consumer, and executor errors deliberately escape unchanged so
    the surrounding lifecycle boundary retains its existing failure handling.
    """
    run_paths = root.ensure_run_paths(run_id)
    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=tuple(item["input_xyz"] for item in staged_tasks),
        config_file=staged_config,
        work_dir=staged_tasks[0]["work_dir"],
        original_input_files=tuple(item["input_xyz"] for item in staged_tasks),
        resume=resume,
        pause_beacon_file=str(run_paths.work / "PAUSE"),
        cancel_beacon_file=str(run_paths.work / "CANCEL"),
    )
    service, executor = service_builder(
        spec,
        state_root=root.path,
        workflow_runner=workflow_runner,
    )
    snapshot = service.consume_queued_launch(run_id)
    if snapshot.state in TERMINAL_STATES:
        return snapshot.state
    executor.wait()
    return None


__all__ = ["run_worker_attempt"]
