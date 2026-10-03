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
    assert not hasattr(confflow, "CalcStepRunner")

    assert "CalcStepRunner" not in confflow.__all__
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
        assert reloaded.__version__ == "2.1.6"

    importlib.reload(confflow)


def test_main_entrypoint_callable_and_non_integer_mapping():
    main_mod = importlib.import_module("confflow.main")
    assert callable(main_mod.main)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(main_mod, "_cli_main", lambda _args=None: None)
        assert main_mod.main([]) == 2
