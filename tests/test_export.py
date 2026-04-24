#!/usr/bin/env python3

"""Tests for exporting existing workflow results."""

from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from confflow.calc.db.database import ResultsDB
from confflow.workflow.export import (
    EXPORT_FIELDS,
    NoExportableResultsError,
    export_results,
)


def _write_result_db(path, rows):
    db = ResultsDB(str(path))
    try:
        for row in rows:
            db.insert_result(row)
    finally:
        db.close()


def test_export_csv_writes_default_output(tmp_path):
    work_dir = tmp_path / "work"
    step_dir = work_dir / "calc_step"
    step_dir.mkdir(parents=True)
    _write_result_db(
        step_dir / "results.db",
        [
            {
                "job_name": "job_b",
                "status": "success",
                "energy": -2.0,
                "final_gibbs_energy": -1.8,
            },
            {"job_name": "job_a", "status": "failed", "error": "boom"},
        ],
    )

    result = export_results(str(work_dir), output_format="csv")

    assert result.row_count == 2
    assert result.output_path == str(work_dir / "confflow_results.csv")
    with open(result.output_path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["job_name"] == "job_a"
    assert rows[1]["job_name"] == "job_b"
    assert rows[1]["energy"] == "-2.0"
    assert rows[1]["step_name"] == "calc_step"
    assert rows[1]["source_db"] == str(step_dir / "results.db")


def test_export_json_writes_explicit_output(tmp_path):
    work_dir = tmp_path / "work"
    step_dir = work_dir / "task_step"
    step_dir.mkdir(parents=True)
    _write_result_db(
        step_dir / "results.db",
        [{"job_name": "job_1", "status": "success", "final_sp_energy": -3.2}],
    )
    output = work_dir / "nested" / "results.json"

    result = export_results(str(work_dir), output_format="json", output_path=str(output))

    assert result.output_path == str(output)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data == [
        {
            "step_name": "task_step",
            "step_dir": str(step_dir),
            "job_name": "job_1",
            "status": "success",
            "energy": None,
            "final_gibbs_energy": None,
            "final_sp_energy": -3.2,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "ts_bond_atoms": None,
            "ts_bond_length": None,
            "error": None,
            "source_db": str(step_dir / "results.db"),
        }
    ]


def test_export_missing_work_dir_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError, match="Work directory does not exist"):
        export_results(str(tmp_path / "missing"), output_format="csv")


def test_export_without_results_db_reports_warning(tmp_path):
    work_dir = tmp_path / "work"
    (work_dir / "calc_without_db").mkdir(parents=True)

    with pytest.raises(NoExportableResultsError) as excinfo:
        export_results(str(work_dir), output_format="csv")

    assert "No exportable results found" in str(excinfo.value)
    assert excinfo.value.warnings == [
        f"Skipping step without results.db: {work_dir / 'calc_without_db'}"
    ]


def test_export_missing_columns_uses_null_values(tmp_path):
    work_dir = tmp_path / "work"
    step_dir = work_dir / "old_calc"
    step_dir.mkdir(parents=True)
    db_path = step_dir / "results.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE task_results (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO task_results (job_name, status) VALUES (?, ?)",
        ("old_job", "success"),
    )
    conn.commit()
    conn.close()

    output = work_dir / "confflow_results.json"
    export_results(str(work_dir), output_format="json", output_path=str(output))

    data = json.loads(output.read_text(encoding="utf-8"))
    assert list(data[0]) == EXPORT_FIELDS
    assert data[0]["job_name"] == "old_job"
    assert data[0]["energy"] is None
    assert data[0]["final_gibbs_energy"] is None
    assert data[0]["error"] is None


def test_export_uses_workflow_step_order_when_available(tmp_path):
    work_dir = tmp_path / "work"
    first_step = work_dir / "z_second_alphabetically"
    second_step = work_dir / "a_first_alphabetically"
    first_step.mkdir(parents=True)
    second_step.mkdir(parents=True)
    _write_result_db(
        first_step / "results.db",
        [{"job_name": "job_z", "status": "success", "energy": -1.0}],
    )
    _write_result_db(
        second_step / "results.db",
        [{"job_name": "job_a", "status": "success", "energy": -2.0}],
    )
    (work_dir / "workflow_stats.json").write_text(
        json.dumps(
            {
                "steps": [
                    {"index": 1, "name": "z_second_alphabetically", "type": "calc"},
                    {"index": 2, "name": "a_first_alphabetically", "type": "calc"},
                ]
            }
        ),
        encoding="utf-8",
    )

    output = work_dir / "ordered.json"
    export_results(str(work_dir), output_format="json", output_path=str(output))

    data = json.loads(output.read_text(encoding="utf-8"))
    assert [row["job_name"] for row in data] == ["job_z", "job_a"]
    assert [row["step_name"] for row in data] == [
        "z_second_alphabetically",
        "a_first_alphabetically",
    ]
