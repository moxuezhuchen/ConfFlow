"""File-based job queue with watchdog support."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Iterator

logger = logging.getLogger(__name__)

INCOMING_DIR = "incoming"
PENDING_DIR = "pending"
DONE_DIR = "done"


class JobSpec:
    """Specification for a job submitted to the agent."""

    def __init__(
        self,
        job_id: str,
        config_file: str,
        input_xyz: str,
        submitted_at: str,
        submitted_by: str = "unknown",
    ):
        self.job_id = job_id
        self.config_file = config_file
        self.input_xyz = input_xyz
        self.submitted_at = submitted_at
        self.submitted_by = submitted_by

    @classmethod
    def from_file(cls, path: str) -> "JobSpec":
        """Load a JobSpec from a YAML/JSON file written by JobDesk."""
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return cls(
            job_id=raw["job_id"],
            config_file=raw["config_file"],
            input_xyz=raw["input_xyz"],
            submitted_at=raw.get("submitted_at", ""),
            submitted_by=raw.get("submitted_by", "unknown"),
        )

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "config_file": self.config_file,
            "input_xyz": self.input_xyz,
            "submitted_at": self.submitted_at,
            "submitted_by": self.submitted_by,
        }


class JobQueue:
    """File-based job queue that watches the incoming directory.

    Files written to ``incoming/`` are picked up, moved to ``pending/``,
    and then processed.  On completion they are moved to ``done/``.
    """

    def __init__(self, queue_dir: str, poll_interval: float = 2.0):
        self.queue_dir = Path(queue_dir).expanduser().resolve()
        self.poll_interval = poll_interval
        self._running = False

        for sub in (INCOMING_DIR, PENDING_DIR, DONE_DIR):
            (self.queue_dir / sub).mkdir(parents=True, exist_ok=True)

    def enqueue(self, job_spec: JobSpec) -> str:
        """Write a job spec to the incoming directory. Returns the job file path."""
        path = self.queue_dir / INCOMING_DIR / f"{job_spec.job_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(job_spec.to_dict(), f, indent=2)
        logger.info("Enqueued job %s at %s", job_spec.job_id, path)
        return str(path)

    def iter_new_jobs(self) -> Iterator[JobSpec]:
        """Yield new jobs waiting in ``incoming/`` and move them to ``pending/``."""
        incoming = self.queue_dir / INCOMING_DIR
        pending = self.queue_dir / PENDING_DIR

        for item in sorted(incoming.glob("*.json")):
            try:
                spec = JobSpec.from_file(str(item))
                dest = pending / item.name
                item.rename(dest)
                logger.debug("Moved job %s from incoming to pending", spec.job_id)
                yield spec
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to process incoming job %s: %s", item, e)

    def mark_done(self, job_id: str) -> None:
        """Move a job from pending to done."""
        pending = self.queue_dir / PENDING_DIR / f"{job_id}.json"
        done = self.queue_dir / DONE_DIR / f"{job_id}.json"
        if pending.exists():
            pending.rename(done)
            logger.info("Job %s marked as done", job_id)

    def mark_failed(self, job_id: str) -> None:
        """Leave the job file in pending but log failure."""
        logger.warning("Job %s failed", job_id)

    def get_pending(self) -> list[JobSpec]:
        """Return all jobs currently in pending."""
        pending = self.queue_dir / PENDING_DIR
        specs = []
        for item in sorted(pending.glob("*.json")):
            try:
                specs.append(JobSpec.from_file(str(item)))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Corrupt job file %s: %s", item, e)
        return specs

    def watch(self, callback: Callable[[JobSpec], None]) -> None:
        """Watch for new jobs and call ``callback`` for each one.

        This is a blocking loop.  Call it in a thread or use the
        ``serve`` module which wraps it.
        """
        self._running = True
        logger.info("JobQueue watching %s (poll_interval=%.1fs)", self.queue_dir, self.poll_interval)

        while self._running:
            for job in self.iter_new_jobs():
                try:
                    callback(job)
                except Exception as e:
                    logger.exception("Error in job callback for %s: %s", job.job_id, e)
            time.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the watch loop on the next iteration."""
        self._running = False
