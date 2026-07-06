"""Agent's internal state database (SQLite)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias

logger = logging.getLogger(__name__)


# Sentinel value to explicitly clear a nullable column
class _CLEAR_TYPE:
    __slots__ = ()

    def __repr__(self):
        return "CLEAR"


CLEAR = _CLEAR_TYPE()

# Type alias so mypy accepts _Unset in parameter unions.
_Unset: TypeAlias = _CLEAR_TYPE

_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id        TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'pending',
    work_dir      TEXT,
    slot_id       INTEGER,
    submitted_at   TEXT NOT NULL,
    started_at    TEXT,
    completed_at   TEXT,
    error_message TEXT,
    progress_pct   REAL DEFAULT 0.0,
    current_step  TEXT,
    config_file   TEXT NOT NULL,
    input_xyz     TEXT NOT NULL,
    submitted_by  TEXT DEFAULT 'unknown',
    extra         TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStateDB:
    """Lightweight SQLite database tracking agent job state."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript(_DB_SCHEMA)
        self._conn.commit()

    def add_job(
        self,
        job_id: str,
        config_file: str,
        input_xyz: str,
        submitted_at: str,
        submitted_by: str = "unknown",
    ) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO jobs
               (job_id, config_file, input_xyz, submitted_at, submitted_by)
               VALUES (?, ?, ?, ?, ?)""",
            (job_id, config_file, input_xyz, submitted_at, submitted_by),
        )
        self._conn.commit()

    def claim(
        self,
        job_id: str,
        slot_id: int,
        work_dir: str,
    ) -> bool:
        """Atomically transition a job to RUNNING iff it is currently PENDING or PAUSED.

        Uses an SQL ``UPDATE ... WHERE status IN (...)`` so that exactly one worker
        wins the race when multiple workers pick up the same job. Returns True iff
        this caller successfully claimed the job.
        """
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cursor = self._conn.execute(
            """UPDATE jobs
               SET status = 'running',
                   slot_id = ?,
                   work_dir = COALESCE(work_dir, ?),
                   started_at = COALESCE(started_at, ?)
               WHERE job_id = ?
                 AND status IN ('pending', 'paused')""",
            (slot_id, work_dir, now, job_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        work_dir: str | None = None,
        slot_id: int | None = None,
        error_message: str | _Unset | None = CLEAR,
        progress_pct: float | _Unset | None = CLEAR,
        current_step: str | _Unset | None = CLEAR,
        completed_at: str | _Unset | None = CLEAR,
        extra: dict | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        fields = ["status = ?"]
        vals: list[Any] = [status.value]

        if status == JobStatus.RUNNING:
            fields.append("started_at = COALESCE(started_at, ?)")
            vals.append(now)
        elif status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED):
            fields.append("completed_at = ?")
            vals.append(now)

        if work_dir is not None:
            fields.append("work_dir = ?")
            vals.append(work_dir)
        if slot_id is not None:
            fields.append("slot_id = ?")
            vals.append(slot_id)
        if error_message is not CLEAR:
            fields.append("error_message = ?")
            vals.append(None if error_message is None else error_message)
        if progress_pct is not CLEAR:
            fields.append("progress_pct = ?")
            vals.append(0.0 if progress_pct is None else progress_pct)
        if current_step is not CLEAR:
            fields.append("current_step = ?")
            vals.append(None if current_step is None else current_step)
        if completed_at is not CLEAR:
            fields.append("completed_at = ?")
            vals.append(None if completed_at is None else completed_at)
        if extra is not None:
            fields.append("extra = ?")
            vals.append(json.dumps(extra))

        vals.append(job_id)
        self._conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", vals)
        self._conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, status: JobStatus | None = None) -> list[dict]:
        if status is None:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY submitted_at DESC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY submitted_at DESC",
                (status.value,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
