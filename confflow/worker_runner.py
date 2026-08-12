"""Focused workflow invocation adapter for the external control worker."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .application.execution.models import ExecutableIdentity
from .core.contracts import cli_output_to_txt
from .worker_sidecar import WorkerSidecarPublisher
from .workflow.engine import run_workflow as default_workflow_runner


@dataclass(frozen=True)
class VerifiedWorkerHandoff:
    """A handoff envelope after schema, path, and digest validation."""

    run_id: str
    digest: str

    def __post_init__(self) -> None:
        if not self.run_id or not _is_sha256(self.digest):
            raise ValueError("worker runner requires a verified handoff digest")


@dataclass(frozen=True)
class VerifiedWorkerLaunch:
    """A queued launch token after the service verified executable identity."""

    run_id: str
    token: str
    expected_identity: ExecutableIdentity

    def __post_init__(self) -> None:
        if not self.run_id or not self.token:
            raise ValueError("worker runner requires a non-empty launch binding")
        identity = self.expected_identity
        if not identity.sha256 or identity.realpath is None or identity.device_inode is None:
            raise ValueError("worker runner requires a complete executable identity")


class WorkerWorkflowRunnerAdapter:
    """Invoke the legacy engine and publish sidecars before returning.

    The service executor owns the surrounding lifecycle callbacks.  This
    adapter has one fixed order: redirect the engine report, invoke the engine,
    publish fixed sidecars, then return so the executor can commit ``completed``.
    """

    def __init__(
        self,
        runner: Callable[..., dict[str, Any] | None],
        *,
        handoff: VerifiedWorkerHandoff,
        launch: VerifiedWorkerLaunch,
        original_input: str,
        work_dir: str,
        sidecar_publisher: WorkerSidecarPublisher,
        publish_sidecars: Callable[..., None] | None = None,
    ) -> None:
        if not isinstance(handoff, VerifiedWorkerHandoff):
            raise TypeError("worker runner requires a verified handoff")
        if not isinstance(launch, VerifiedWorkerLaunch):
            raise TypeError("worker runner requires a verified launch")
        if handoff.run_id != launch.run_id:
            raise ValueError("worker handoff and launch binding refer to different runs")
        if not original_input or not work_dir:
            raise ValueError("worker runner requires input and work directory")
        self._runner = runner
        self._handoff = handoff
        self._launch = launch
        self._original_input = original_input
        self._work_dir = work_dir
        self._sidecar_publisher = sidecar_publisher
        self._publish_sidecars = publish_sidecars

    def __call__(self, **kwargs: Any) -> dict[str, Any] | None:
        """Run the engine, publish sidecars, and only then return to lifecycle."""
        with cli_output_to_txt(self._original_input):
            result = self._runner(**kwargs)
        if self._publish_sidecars is None:
            self._sidecar_publisher.publish(
                staged_input=self._original_input,
                work_dir=self._work_dir,
            )
        else:
            self._publish_sidecars(
                self._sidecar_publisher,
                staged_input=self._original_input,
                work_dir=self._work_dir,
            )
        return result


def _is_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))


__all__ = [
    "VerifiedWorkerHandoff",
    "VerifiedWorkerLaunch",
    "WorkerWorkflowRunnerAdapter",
    "default_workflow_runner",
]
