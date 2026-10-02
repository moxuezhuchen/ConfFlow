#!/usr/bin/env python3

"""CID and TS metadata provenance across DB, export and refine."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from confflow.blocks.refine import processor
from confflow.workflow.export import export_results
from tests.results_db_fixture import ResultsDB


def test_db_persists_cid_from_metadata(tmp_path):
    db = ResultsDB(str(tmp_path / "results.db"))
    db.insert_result(
        {
            "job_name": "j1",
            "index": 0,
            "status": "success",
            "energy": -1.0,
            "metadata": {"CID": "CID1"},
        }
    )

    record = db.get_result_by_job_name("j1")

    assert record is not None
    assert record["cid"] == "CID1"


def test_db_persists_explicit_cid(tmp_path):
    db = ResultsDB(str(tmp_path / "results.db"))
    db.insert_result({"job_name": "j1", "status": "success", "energy": -1.0, "cid": "X9"})

    record = db.get_result_by_job_name("j1")

    assert record is not None
    assert record["cid"] == "X9"


def test_export_includes_cid(tmp_path):
    work_dir = tmp_path / "work"
    step_dir = work_dir / "step_01_calc"
    step_dir.mkdir(parents=True)
    db = ResultsDB(str(step_dir / "results.db"))
    db.insert_result({"job_name": "j1", "status": "success", "energy": -1.0, "cid": "CID1"})
    db.close()

    result = export_results(str(work_dir), output_format="json")
    payload = json.loads(Path(result.output_path).read_text(encoding="utf-8"))
    rows = payload["results"] if isinstance(payload, dict) and "results" in payload else payload

    assert any(row.get("cid") == "CID1" for row in rows)


def test_refine_output_keeps_ts_metadata(tmp_path):
    frames = [
        {
            "natoms": 2,
            "energy": -100.0,
            "energy_key": "G",
            "num_imag_freqs": 1,
            "extra_data": {"CID": "A000001", "G_corr": 1.0, "TSAtoms": "1,2", "TSBond": 0.74},
            "original_atoms": ["H", "H"],
            "coords": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]], dtype=np.float64),
        }
    ]

    out = tmp_path / "out.xyz"
    processor._write_refine_output(str(out), frames, global_min=-100.0)
    text = out.read_text(encoding="utf-8")

    assert "TSAtoms=1,2" in text
    assert "TSBond=0.74" in text
    assert "G_corr=1.0" in text
