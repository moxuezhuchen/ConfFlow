#!/usr/bin/env python3

"""Export calculation results from an existing ConfFlow work directory."""

from __future__ import annotations

import csv
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from confflow.core.path_policy import validate_managed_path
from confflow.workflow.step_naming import sanitize_step_dir_name

__all__ = [
    "EXPORT_FIELDS",
    "ExportResult",
    "NoExportableResultsError",
    "export_results",
]

EXPORT_FIELDS = [
    "step_name",
    "step_dir",
    "job_name",
    "status",
    "energy",
    "final_gibbs_energy",
    "final_sp_energy",
    "g_corr",
    "num_imag_freqs",
    "lowest_freq",
    "ts_bond_atoms",
    "ts_bond_length",
    "error",
    "source_db",
]

_RESULT_COLUMNS = [
    "job_name",
    "status",
    "energy",
    "final_gibbs_energy",
    "final_sp_energy",
    "g_corr",
    "num_imag_freqs",
    "lowest_freq",
    "ts_bond_atoms",
    "ts_bond_length",
    "error",
]


class NoExportableResultsError(RuntimeError):
    """Raised when a work directory contains no exportable result rows."""

    def __init__(self, message: str, warnings: list[str] | None = None) -> None:
        super().__init__(message)
        self.warnings = list(warnings or [])


@dataclass(frozen=True)
class ExportResult:
    output_path: str
    row_count: int
    warnings: list[str]


@dataclass(frozen=True)
class _StepMeta:
    order: dict[str, int]
    names: dict[str, str]


def _default_output_path(work_dir: str, output_format: str) -> str:
    suffix = "csv" if output_format == "csv" else "json"
    return os.path.join(work_dir, f"confflow_results.{suffix}")


def _iter_step_dirs(work_dir: str) -> list[Path]:
    return sorted(
        (path for path in Path(work_dir).iterdir() if path.is_dir()),
        key=lambda path: path.name,
    )


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_step_meta(work_dir: str) -> _StepMeta:
    for filename in ("workflow_stats.json", "run_summary.json"):
        data = _load_json_file(Path(work_dir) / filename)
        if not data:
            continue
        steps = data.get("steps")
        if not isinstance(steps, list):
            continue

        used: dict[str, int] = {}
        order: dict[str, int] = {}
        names: dict[str, str] = {}
        for fallback_index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            raw_name = str(step.get("name") or "").strip()
            dirname = sanitize_step_dir_name(raw_name, fallback=f"step_{fallback_index:02d}")
            duplicate_count = used.get(dirname, 0)
            used[dirname] = duplicate_count + 1
            dirname = dirname if duplicate_count == 0 else f"{dirname}_{duplicate_count + 1}"

            raw_index = step.get("index", fallback_index)
            index = raw_index if isinstance(raw_index, int) else fallback_index
            order[dirname] = index
            names[dirname] = raw_name or dirname
        if order:
            return _StepMeta(order=order, names=names)

    return _StepMeta(order={}, names={})


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(task_results)")}


def _read_rows_from_db(db_path: Path, step_dir: Path, step_name: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = _table_columns(conn)
        if not columns:
            return []

        selected = ["task_id"] if "task_id" in columns else []
        selected.extend(col for col in _RESULT_COLUMNS if col in columns)
        if not selected:
            return []

        if "job_name" in columns and "task_id" in columns:
            select_exprs = [f"tr.{col}" if col in columns else col for col in selected]
            query = f"""
                SELECT {", ".join(select_exprs)}
                FROM task_results tr
                JOIN (
                    SELECT job_name, MAX(task_id) AS max_task_id
                    FROM task_results
                    GROUP BY job_name
                ) latest
                    ON latest.job_name = tr.job_name
                   AND latest.max_task_id = tr.task_id
            """
        else:
            query = f"SELECT {', '.join(selected)} FROM task_results"

        rows = []
        for row in conn.execute(query):
            row_dict = dict(row)
            exported = {field: None for field in EXPORT_FIELDS}
            exported["step_name"] = step_name
            exported["step_dir"] = str(step_dir)
            exported["source_db"] = str(db_path)
            for col in _RESULT_COLUMNS:
                if col in row_dict:
                    exported[col] = row_dict[col]
            rows.append(exported)
        return rows
    finally:
        conn.close()


def _sort_key(row: dict[str, Any], step_order: dict[str, int]) -> tuple[int, str, str]:
    step_dirname = Path(str(row.get("step_dir") or "")).name
    return (
        step_order.get(step_dirname, 1_000_000),
        step_dirname,
        str(row.get("job_name") or ""),
    )


def _write_csv(path: str, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in EXPORT_FIELDS})


def _write_json(path: str, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def export_results(
    work_dir: str,
    *,
    output_format: str,
    output_path: str | None = None,
) -> ExportResult:
    """Export all available step-level calculation results to CSV or JSON."""
    if output_format not in {"csv", "json"}:
        raise ValueError(f"Unsupported export format: {output_format}")

    safe_work_dir = validate_managed_path(work_dir, label="work_dir")
    if not os.path.isdir(safe_work_dir):
        raise FileNotFoundError(f"Work directory does not exist: {safe_work_dir}")

    raw_output = output_path or _default_output_path(safe_work_dir, output_format)
    safe_output = validate_managed_path(
        raw_output,
        label="output",
        sandbox_root=safe_work_dir,
        base_dir=safe_work_dir,
    )

    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    step_meta = _load_step_meta(safe_work_dir)

    step_dirs = _iter_step_dirs(safe_work_dir)
    if not step_dirs:
        warnings.append(f"No step directories found in work directory: {safe_work_dir}")

    for step_dir in step_dirs:
        db_path = step_dir / "results.db"
        if not db_path.exists():
            warnings.append(f"Skipping step without results.db: {step_dir}")
            continue
        try:
            rows.extend(
                _read_rows_from_db(
                    db_path,
                    step_dir,
                    step_name=step_meta.names.get(step_dir.name, step_dir.name),
                )
            )
        except sqlite3.Error as exc:
            warnings.append(f"Skipping unreadable results.db {db_path}: {exc}")

    rows.sort(key=lambda row: _sort_key(row, step_meta.order))
    if not rows:
        raise NoExportableResultsError(
            f"No exportable results found under work directory: {safe_work_dir}",
            warnings=warnings,
        )

    os.makedirs(os.path.dirname(safe_output), exist_ok=True)
    if output_format == "csv":
        _write_csv(safe_output, rows)
    else:
        _write_json(safe_output, rows)

    return ExportResult(output_path=safe_output, row_count=len(rows), warnings=warnings)
