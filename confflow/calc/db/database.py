#!/usr/bin/env python3

"""
Database module.

Manages SQLite storage of calculation task results, supporting task status
tracking and checkpoint-based resumption.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from typing import Any

logger = logging.getLogger("confflow.calc.database")

__all__ = [
    "ResultsDB",
]


class ResultsDB:
    """Task results database manager.

    Uses SQLite to store calculation task results.  Supports:

    - Task status tracking (success / failed / skipped / canceled / pending).
    - Energy and frequency data storage.
    - Final structure coordinate persistence.
    - TS bond length and thermodynamic correction storage.

    Attributes
    ----------
    db_path : str
        Path to the database file.
    conn : sqlite3.Connection
        SQLite connection object.
    """

    def __init__(self, db_path: str):
        """Initialize the database connection.

        Parameters
        ----------
        db_path : str
            Path to the SQLite database file.
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self) -> None:
        """Create the task results table if it does not exist."""
        # Enable WAL mode for better concurrency
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS task_results (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                task_index INTEGER,
                status TEXT NOT NULL,
                energy REAL,
                final_gibbs_energy REAL,
                final_sp_energy REAL,
                num_imag_freqs INTEGER,
                lowest_freq REAL,
                g_corr REAL,
                ts_bond_atoms TEXT,
                ts_bond_length REAL,
                final_coords TEXT,
                error TEXT,
                error_kind TEXT,
                error_details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_results_job_name ON task_results(job_name)"
        )

        # Add missing columns when opening databases created by older versions.
        try:
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(task_results)")}
            if "ts_bond_atoms" not in cols:
                self.conn.execute("ALTER TABLE task_results ADD COLUMN ts_bond_atoms TEXT")
            if "ts_bond_length" not in cols:
                self.conn.execute("ALTER TABLE task_results ADD COLUMN ts_bond_length REAL")
            if "error_kind" not in cols:
                self.conn.execute("ALTER TABLE task_results ADD COLUMN error_kind TEXT")
            if "error_details" not in cols:
                self.conn.execute("ALTER TABLE task_results ADD COLUMN error_details TEXT")
        except sqlite3.OperationalError as e:
            logger.warning(f"Database column check failed (operational error): {e}")
        except sqlite3.DatabaseError as e:
            logger.warning(f"Database column check failed (database error): {e}")

        self.conn.commit()

    def insert_result(self, task_info: dict[str, Any]) -> int:
        """Insert a task result.

        Parameters
        ----------
        task_info : dict[str, Any]
            Result dictionary containing job_name, status, energy, etc.

        Returns
        -------
        int
            The inserted record ID.
        """
        payload = (
            task_info.get("job_name"),
            task_info.get("index"),
            task_info.get("status"),
            task_info.get("energy"),
            task_info.get("final_gibbs_energy"),
            task_info.get("final_sp_energy"),
            task_info.get("num_imag_freqs"),
            task_info.get("lowest_freq"),
            task_info.get("g_corr"),
            task_info.get("ts_bond_atoms"),
            task_info.get("ts_bond_length"),
            json.dumps(task_info.get("final_coords")) if task_info.get("final_coords") else None,
            task_info.get("error"),
            task_info.get("error_kind"),
            task_info.get("error_details"),
        )
        existing = self.conn.execute(
            "SELECT task_id FROM task_results WHERE job_name = ? ORDER BY task_id DESC LIMIT 1",
            (task_info.get("job_name"),),
        ).fetchone()
        if existing is not None:
            cursor = self.conn.execute(
                """
                UPDATE task_results
                SET task_index = ?, status = ?, energy = ?, final_gibbs_energy = ?,
                    final_sp_energy = ?, num_imag_freqs = ?, lowest_freq = ?, g_corr = ?,
                    ts_bond_atoms = ?, ts_bond_length = ?, final_coords = ?, error = ?,
                    error_kind = ?, error_details = ?,
                    timestamp = CURRENT_TIMESTAMP
                WHERE task_id = ?
            """,
                payload[1:] + (existing["task_id"],),
            )
            row_id = int(existing["task_id"])
        else:
            cursor = self.conn.execute(
                """
                INSERT INTO task_results (
                    job_name, task_index, status, energy,
                    final_gibbs_energy, final_sp_energy, num_imag_freqs,
                    lowest_freq, g_corr, ts_bond_atoms, ts_bond_length,
                    final_coords, error, error_kind, error_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                payload,
            )
            row_id = int(cursor.lastrowid or 0)
        self.conn.commit()
        return row_id

    def get_all_results(self) -> list[dict[str, Any]]:
        """Retrieve all task results.

        Returns
        -------
        list[dict[str, Any]]
            List of results sorted by task index.
        """
        cursor = self.conn.execute("""
            SELECT tr.*
            FROM task_results tr
            JOIN (
                SELECT job_name, MAX(task_id) AS max_task_id
                FROM task_results
                GROUP BY job_name
            ) latest
                ON latest.job_name = tr.job_name
               AND latest.max_task_id = tr.task_id
            ORDER BY tr.task_index, tr.task_id
        """)
        return [self._row_to_dict(row) for row in cursor]

    def get_result_by_job_name(self, job_name: str) -> dict[str, Any] | None:
        """Query a result by job name.

        Parameters
        ----------
        job_name : str
            The job name (e.g. ``geom_0001``).

        Returns
        -------
        dict or None
            Result dict, or None if not found.
        """
        cursor = self.conn.execute(
            "SELECT * FROM task_results WHERE job_name = ? ORDER BY task_id DESC LIMIT 1",
            (job_name,),
        )
        row = cursor.fetchone()
        return self._row_to_dict(row) if row else None

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a database row to a dictionary."""
        return {
            "index": row["task_index"],
            "job_name": row["job_name"],
            "status": row["status"],
            "energy": row["energy"],
            "final_gibbs_energy": row["final_gibbs_energy"],
            "final_sp_energy": row["final_sp_energy"],
            "num_imag_freqs": row["num_imag_freqs"],
            "lowest_freq": row["lowest_freq"],
            "g_corr": row["g_corr"],
            "ts_bond_atoms": row["ts_bond_atoms"] if "ts_bond_atoms" in row.keys() else None,
            "ts_bond_length": row["ts_bond_length"] if "ts_bond_length" in row.keys() else None,
            "final_coords": json.loads(row["final_coords"]) if row["final_coords"] else None,
            "error": row["error"],
            "error_kind": row["error_kind"] if "error_kind" in row.keys() else None,
            "error_details": row["error_details"] if "error_details" in row.keys() else None,
        }

    def backup(self, backup_path: str | None = None) -> str:
        """Back up the database to the specified path (atomic operation).

        Parameters
        ----------
        backup_path : str or None, optional
            Backup file path. Defaults to ``db_path + '.backup'``.

        Returns
        -------
        str
            The backup file path.
        """
        if backup_path is None:
            backup_path = self.db_path + ".backup"

        # Use a temp file + atomic rename for integrity
        fd, tmp_path = tempfile.mkstemp(suffix=".db", dir=os.path.dirname(self.db_path))
        os.close(fd)

        try:
            backup_conn = sqlite3.connect(tmp_path)
            with backup_conn:
                self.conn.backup(backup_conn)
            backup_conn.close()

            # Atomic rename
            shutil.move(tmp_path, backup_path)
            logger.debug(f"Wrote the database backup to: {backup_path}")
            return backup_path
        except (OSError, sqlite3.Error) as e:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            logger.warning(f"Failed to back up the database: {e}")
            raise

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()

    def __enter__(self) -> ResultsDB:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.conn.close()
        except (AttributeError, OSError, sqlite3.Error):
            pass
