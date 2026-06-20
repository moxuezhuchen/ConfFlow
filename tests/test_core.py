#!/usr/bin/env python3

"""Tests for package integration and public exports."""

from __future__ import annotations

import importlib
import importlib.metadata

import pytest


def test_confflow_package_exports_current_public_api():
    import confflow

    assert isinstance(confflow.__version__, str)
    assert confflow.__version__
    assert hasattr(confflow, "RDKIT_AVAILABLE")
    assert hasattr(confflow, "PSUTIL_AVAILABLE")
    assert hasattr(confflow, "NUMBA_AVAILABLE")
    assert hasattr(confflow, "read_xyz_file")
    assert hasattr(confflow, "run_workflow")
    assert hasattr(confflow, "CalcStepRunner")
    assert hasattr(confflow, "CalcStepRequest")
    assert hasattr(confflow, "CalcStepResult")

    assert "CalcStepRunner" in confflow.__all__
    assert "run_workflow" in confflow.__all__
    assert "read_xyz_file" in confflow.__all__
    assert "ChemTaskManager" not in confflow.__all__
    assert "run_calc_workflow_step" not in confflow.__all__


def test_confflow_version_falls_back_when_package_metadata_missing(monkeypatch):
    import confflow

    def raise_missing(_name):
        raise importlib.metadata.PackageNotFoundError

    with monkeypatch.context() as mp:
        mp.setattr(importlib.metadata, "version", raise_missing)
        reloaded = importlib.reload(confflow)
        assert reloaded.__version__ == "1.0.10"

    importlib.reload(confflow)


def test_main_entrypoint_callable_and_non_integer_mapping():
    main_mod = importlib.import_module("confflow.main")
    assert callable(main_mod.main)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main_mod, "_cli_main", lambda _args=None: None)
        assert main_mod.main([]) == 2


def test_confgen_and_refine_key_symbols_present():
    import numpy as np

    import confflow.blocks.confgen as confgen
    import confflow.blocks.refine as refine

    assert hasattr(confgen, "run_generation")
    assert hasattr(confgen, "check_clash_core")
    assert refine.get_element_atomic_number("Cl") == 17
    coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
    assert refine.fast_rmsd(coords, coords) < 1e-6


def test_calc_resultsdb_roundtrip(tmp_path):
    import confflow.calc as calc

    db = calc.ResultsDB(str(tmp_path / "res.db"))
    job_id = db.insert_result({"job_name": "j", "index": 1, "status": "success"})
    assert job_id == 1
    got = db.get_result_by_job_name("j")
    assert got is not None and got["status"] == "success"
    db.close()
