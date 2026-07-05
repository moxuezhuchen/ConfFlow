"""Progress event handling: writes status JSON files consumed by JobDesk."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ProgressTracker:
    """Writes job status events to ``status/<job_id>.json`` in the queue dir."""

    def __init__(self, queue_dir: str):
        self.queue_dir = Path(queue_dir).expanduser().resolve()
        self.status_dir = self.queue_dir / "status"
        self.status_dir.mkdir(parents=True, exist_ok=True)

    def emit(self, job_id: str, event: dict) -> None:
        """Write a status JSON snapshot for a job."""
        path = self.status_dir / f"{job_id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "job_id": job_id,
                        "updated_at": _now_iso(),
                        "event": event,
                    },
                    f,
                    indent=2,
                )
        except OSError as e:
            logger.warning("Failed to write status file for %s: %s", job_id, e)

    def emit_progress(self, job_id: str, pct: float, step: str | None = None) -> None:
        self.emit(job_id, {"event": "progress", "pct": pct, "step": step})

    def emit_error(self, job_id: str, error: str, traceback: str | None = None) -> None:
        payload: dict[str, Any] = {"event": "failed", "error": error}
        if traceback:
            payload["traceback"] = traceback
        self.emit(job_id, payload)

    def emit_completed(self, job_id: str, stats: dict | None = None) -> None:
        payload: dict[str, Any] = {"event": "completed"}
        if stats:
            payload["stats"] = stats
        self.emit(job_id, payload)

    def emit_paused(self, job_id: str) -> None:
        self.emit(job_id, {"event": "paused"})

    def emit_cancelled(self, job_id: str) -> None:
        self.emit(job_id, {"event": "cancelled"})


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
