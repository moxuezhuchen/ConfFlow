"""Agent server: main serve loop wiring queue → slots → runner → progress."""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path

from .queue import JobQueue, JobSpec
from .runner import JobContext, JobRunner
from .slots import SlotManager
from .state import AgentStateDB, JobStatus
from .progress import ProgressTracker

logger = logging.getLogger(__name__)

RUNS_DIR = "confflow-runs"


class AgentServer:
    """Main agent server that orchestrates queue, slots, and runners."""

    def __init__(
        self,
        queue_dir: str,
        state_db: AgentStateDB,
        num_slots: int = 2,
        runs_base_dir: str | None = None,
    ):
        self.queue = JobQueue(queue_dir)
        self.state_db = state_db
        self.slots = SlotManager(num_slots)
        self.progress = ProgressTracker(queue_dir)
        self.queue_dir = Path(queue_dir)
        self.runs_base = Path(runs_base_dir or str(self.queue_dir.parent / RUNS_DIR))
        self.runs_base.mkdir(parents=True, exist_ok=True)

        self._workers: list[threading.Thread] = []
        self._running = False
        self._lock = threading.Lock()
        # job_id → step_dir of the currently-running step (for STOP beacon injection)
        self._running_steps: dict[str, str] = {}
        self._running_steps_lock = threading.Lock()

        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)

    def _on_signal(self, signum: int, frame) -> None:
        logger.info("Received signal %d, initiating graceful shutdown", signum)
        self.stop()

    def serve(self) -> None:
        """Start the server and block until shutdown."""
        logger.info("ConfFlow Agent starting (slots=%d, queue=%s)", self.slots.num_slots, self.queue_dir)
        self._running = True

        for i in range(self.slots.num_slots):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True, name=f"agent-worker-{i}")
            t.start()
            self._workers.append(t)

        try:
            while self._running:
                for job in self.queue.iter_new_jobs():
                    self._enqueue_job(job)
                self._sleep(1.0)
        except Exception:
            logger.exception("Server loop crashed")
        finally:
            self._join_workers()

    def _enqueue_job(self, spec: JobSpec) -> None:
        """Register a job in the state DB and enqueue it for a worker."""
        job_id = spec.job_id
        work_dir = str(self.runs_base / f"run_{job_id}")

        self.state_db.add_job(
            job_id=job_id,
            config_file=spec.config_file,
            input_xyz=spec.input_xyz,
            submitted_at=spec.submitted_at,
            submitted_by=spec.submitted_by,
        )
        self.state_db.set_status(job_id, JobStatus.PENDING, work_dir=work_dir)
        self.progress.emit(job_id, {"event": "pending", "work_dir": work_dir})
        logger.info("Registered job %s, work_dir=%s", job_id, work_dir)

    def _trigger_stop_beacon(self, job_id: str) -> None:
        """Touch the calc step STOP beacon for a running job (if any)."""
        with self._running_steps_lock:
            step_dir = self._running_steps.get(job_id)
        if step_dir:
            stop_file = Path(step_dir) / "STOP"
            stop_file.touch()
            logger.info("Triggered STOP beacon for job %s at %s", job_id, stop_file)

    def _make_pause_callback(self, job_id: str) -> callable:
        """Return the on_pause_requested callback bound to this job_id."""
        def callback() -> None:
            self._trigger_stop_beacon(job_id)
        return callback

    def _worker_loop(self, worker_id: int) -> None:
        """Worker thread: acquire slot, pick up pending jobs, run them."""
        logger.debug("Worker %d started", worker_id)
        while self._running:
            pending = self.queue.get_pending()
            if not pending:
                self._sleep(0.5)
                continue

            reservation = self.slots.acquire(timeout=5.0)
            if reservation is None:
                self._sleep(0.5)
                continue

            slot = reservation.slot
            job = pending[0]
            job_id = job.job_id

            try:
                state = self.state_db.get_job(job_id)
                if state and state["status"] not in (JobStatus.PENDING.value, JobStatus.PAUSED.value):
                    self.slots.release(slot)
                    continue

                logger.info("Worker %d picking up job %s on slot %d", worker_id, job_id, slot.id)
                slot.job_id = job_id
                self.state_db.set_status(job_id, JobStatus.RUNNING, slot_id=slot.id)

                work_dir_str = str(self.runs_base / f"run_{job_id}")
                ctx = JobContext(
                    job_id=job_id,
                    config_file=job.config_file,
                    input_xyz=job.input_xyz,
                    work_dir=work_dir_str,
                    pause_beacon_file=str(Path(work_dir_str) / "PAUSE"),
                    state_db=self.state_db,
                    on_progress=lambda e: self._on_progress(job_id, e),
                    on_pause_requested=self._make_pause_callback(job_id),
                    on_step_started=lambda name, step_type, step_dir: self._on_step_started(job_id, step_type, step_dir),
                )
                runner = JobRunner(ctx)
                runner.run()

            except Exception:
                logger.exception("Worker %d error on job %s", worker_id, job_id)
            finally:
                with self._running_steps_lock:
                    self._running_steps.pop(job_id, None)
                self.slots.release(slot)

    def _on_progress(self, job_id: str, event: dict) -> None:
        self.progress.emit(job_id, event)
        ev = event.get("event", "")
        if ev in ("completed", "failed"):
            self.queue.mark_done(job_id)

    def _on_step_started(self, job_id: str, step_type: str, step_dir: str) -> None:
        """Track the current step_dir for STOP beacon injection during calc/task steps."""
        if step_type in ("calc", "task"):
            with self._running_steps_lock:
                self._running_steps[job_id] = step_dir
            logger.debug("Tracking step %s for job %s at %s", step_type, job_id, step_dir)

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        logger.info("Stopping agent server...")
        self.queue.stop()
        self._join_workers()
        logger.info("Agent server stopped")

    def _join_workers(self) -> None:
        for t in self._workers:
            t.join(timeout=5.0)
        self._workers.clear()

    @staticmethod
    def _sleep(duration: float) -> None:
        import time
        time.sleep(duration)
