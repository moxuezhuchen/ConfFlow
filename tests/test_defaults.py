#!/usr/bin/env python3

"""Tests for confflow.config.defaults — centralized default constants."""

from __future__ import annotations

from pathlib import Path

from confflow.config import defaults


class TestDefaults:
    """Verify default constants exist and have sane types/values."""

    def test_resource_defaults(self):
        assert defaults.DEFAULT_CORES_PER_TASK >= 1
        assert isinstance(defaults.DEFAULT_TOTAL_MEMORY, str)
        assert defaults.DEFAULT_MAX_PARALLEL_JOBS >= 1

    def test_chemistry_defaults(self):
        assert defaults.DEFAULT_CHARGE == 0
        assert defaults.DEFAULT_MULTIPLICITY >= 1

    def test_refine_defaults(self):
        assert 0 < defaults.DEFAULT_RMSD_THRESHOLD < 10

    def test_ts_defaults(self):
        assert isinstance(defaults.DEFAULT_TS_RESCUE_SCAN, bool)
        assert 0 < defaults.DEFAULT_SCAN_COARSE_STEP < 1
        assert 0 < defaults.DEFAULT_SCAN_FINE_STEP < defaults.DEFAULT_SCAN_COARSE_STEP
        assert defaults.DEFAULT_SCAN_UPHILL_LIMIT > 0
        assert defaults.DEFAULT_TS_BOND_DRIFT_THRESHOLD > 0
        assert defaults.DEFAULT_TS_RMSD_THRESHOLD > 0

    def test_example_config_ts_rescue_scan_default_matches_code(self):
        example = Path(__file__).resolve().parents[1] / "confflow.example.yaml"
        text = example.read_text(encoding="utf-8")
        assert "# ts_rescue_scan: false" in text

    def test_workflow_defaults(self):
        assert isinstance(defaults.DEFAULT_ENABLE_DYNAMIC_RESOURCES, bool)
        assert isinstance(defaults.DEFAULT_RESUME_FROM_BACKUPS, bool)
        assert defaults.DEFAULT_STOP_CHECK_INTERVAL_SECONDS >= 1
        assert isinstance(defaults.DEFAULT_FORCE_CONSISTENCY, bool)

    def test_boltzmann_cutoff(self):
        assert defaults.BOLTZMANN_ENERGY_CUTOFF > 0

    def test_all_exports(self):
        for name in defaults.__all__:
            assert hasattr(defaults, name), f"Missing exported symbol: {name}"
