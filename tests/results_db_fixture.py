"""A minimal writer for the legacy ``results.db`` that ``workflow.export`` reads.

``confflow.calc`` (the only producer of ``results.db``) is gone; the export
reader still accepts such databases, so its tests build one directly.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_results (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    task_index INTEGER,
    cid TEXT,
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
"""

_COLUMNS = (
    "job_name",
    "task_index",
    "status",
    "energy",
    "final_gibbs_energy",
    "final_sp_energy",
    "num_imag_freqs",
    "lowest_freq",
    "g_corr",
    "ts_bond_atoms",
    "ts_bond_length",
    "final_coords",
    "error",
    "error_kind",
    "error_details",
    "cid",
)


class ResultsDB:
    """Insert and read back task rows in the legacy ``task_results`` layout."""

    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def insert_result(self, task_info: dict[str, Any]) -> int:
        coords = task_info.get("final_coords")
        values = (
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
            json.dumps(coords) if coords else None,
            task_info.get("error"),
            task_info.get("error_kind"),
            task_info.get("error_details"),
            task_info.get("cid") or (task_info.get("metadata") or {}).get("CID"),
        )
        cursor = self.conn.execute(
            f"INSERT INTO task_results ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
            values,
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def get_result_by_job_name(self, job_name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM task_results WHERE job_name = ? ORDER BY task_id DESC LIMIT 1",
            (job_name,),
        ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["index"] = result.pop("task_index")
        return result

    def close(self) -> None:
        self.conn.close()
