"""JobRunner wraps run_workflow with exception isolation and progress callbacks."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from ..application.execution.errors import ErrorCode, ExecutionServiceError
from ..application.execution.workflow_adapter import run_workflow_through_service
from ..core.exceptions import StopRequestedError
from ..workflow.engine import run_workflow
from .state import AgentStateDB, JobStatus

logger = logging.getLogger(__name__)


@dataclass
class JobContext:
    job_id: str
    config_file: str
    input_xyz: str
    work_dir: str
    pause_beacon_file: str
    state_db: AgentStateDB
    on_progress: Callable[[dict], None] | None = None
    on_pause_requested: Callable[[], None] | None = None
    on_step_started: Callable[[str, str, str], None] | None = None
    execution_state_root: str | None = None
    resume: bool = False


class JobRunner:
    """Runs a single ConfFlow job with full exception isolation."""

    def __init__(self, ctx: JobContext):
        self.ctx = ctx

    def run(self) -> None:
        """Execute the job, updating state DB and invoking callbacks."""
        job_id = self.ctx.job_id
        state_db = self.ctx.state_db

        try:
            state_db.set_status(job_id, JobStatus.RUNNING, work_dir=self.ctx.work_dir)
            self._emit({"event": "started", "job_id": job_id, "work_dir": self.ctx.work_dir})

            if self.ctx.execution_state_root is None:
                result = run_workflow(
                    input_xyz=[self.ctx.input_xyz],
                    config_file=self.ctx.config_file,
                    work_dir=self.ctx.work_dir,
                    original_input_files=None,
                    resume=False,
                    verbose=False,
                    pause_beacon_file=self.ctx.pause_beacon_file,
                    step_started_callback=self.ctx.on_step_started,
                )
            else:
                result = run_workflow_through_service(
                    input_xyz=[self.ctx.input_xyz],
                    config_file=self.ctx.config_file,
                    work_dir=self.ctx.work_dir,
                    state_root=self.ctx.execution_state_root,
                    run_id=self.ctx.job_id,
                    original_input_files=None,
                    resume=self.ctx.resume,
                    verbose=False,
                    pause_beacon_file=self.ctx.pause_beacon_file,
                    step_started_callback=self.ctx.on_step_started,
                    workflow_runner=run_workflow,
                )

            state_db.set_status(job_id, JobStatus.DONE)
            self._emit(
                {
                    "event": "completed",
                    "job_id": job_id,
                    "stats": result,
                }
            )
            logger.info("Job %s completed successfully", job_id)

        except StopRequestedError:
            # Pause was triggered — notify server, mark PAUSED, re-enqueue
            tb = traceback.format_exc()
            logger.info("Job %s paused by beacon: %s", job_id, tb)
            if self.ctx.on_pause_requested:
                self.ctx.on_pause_requested()
            state_db.set_status(job_id, JobStatus.PAUSED)
            self._emit(
                {
                    "event": "paused",
                    "job_id": job_id,
                }
            )
            logger.info("Job %s marked as paused", job_id)

        except ExecutionServiceError as error:
            if error.code is ErrorCode.TERMINAL_RUN and "cancel" in str(error).lower():
                state_db.set_status(job_id, JobStatus.CANCELLED)
                self._emit({"event": "cancelled", "job_id": job_id})
                logger.info("Job %s was cancelled by the execution service", job_id)
                return
            tb = traceback.format_exc()
            logger.exception("Job %s service failure: %s\n%s", job_id, error, tb)
            state_db.set_status(job_id, JobStatus.FAILED, error_message=str(error))
            self._emit({"event": "failed", "job_id": job_id, "error": str(error), "traceback": tb})

        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            logger.exception("Job %s failed: %s\n%s", job_id, e, tb)
            state_db.set_status(job_id, JobStatus.FAILED, error_message=str(e))
            self._emit(
                {
                    "event": "failed",
                    "job_id": job_id,
                    "error": str(e),
                    "traceback": tb,
                }
            )

    def _emit(self, event: dict) -> None:
        if self.ctx.on_progress:
            try:
                self.ctx.on_progress(event)
            except Exception as e:
                logger.warning("Progress callback raised: %s", e)
