#!/usr/bin/env python3

"""Tests for confflow package integration — config, package exports, low energy trace."""

from __future__ import annotations

import importlib
import json

import pytest
import yaml

import confflow.blocks.confgen as confgen
import confflow.blocks.viz as viz
import confflow.calc as calc


class TestConfig:
    """Tests for config module."""

    def test_config_schema_normalize_global(self):
        """Test global config normalization."""
        from confflow.config.schema import ConfigSchema

        raw = {
            "cores_per_task": 8,
            "freeze": [1, 2, 3],
            "gaussian_path": "/opt/g16/g16",
        }

        normalized = ConfigSchema.normalize_global_config(raw)
        assert normalized["cores_per_task"] == 8
        assert normalized["freeze"] == [1, 2, 3]
        assert normalized["gaussian_path"] == "/opt/g16/g16"
        assert normalized["charge"] == 0
        assert normalized["multiplicity"] == 1
        assert normalized["rmsd_threshold"] == 0.25

    def test_config_schema_validate_calc(self):
        """Test calc config validation."""
        from confflow.config.schema import ConfigSchema

        valid = {"iprog": "orca", "itask": "opt", "keyword": "xTB2 Opt"}
        ConfigSchema.validate_calc_config(valid)

        with pytest.raises(ValueError, match="iprog"):
            ConfigSchema.validate_calc_config({"itask": "opt", "keyword": "test"})

        with pytest.raises(ValueError, match="itask"):
            ConfigSchema.validate_calc_config(
                {"iprog": "orca", "itask": "invalid", "keyword": "test"}
            )


class TestConfflowPackage:
    """Tests for confflow package."""

    def test_confflow_package_exports(self):
        """Test package-level exports."""
        import confflow

        assert hasattr(confflow, "__version__")
        assert hasattr(confflow, "RDKIT_AVAILABLE")
        assert hasattr(confflow, "PSUTIL_AVAILABLE")
        assert hasattr(confflow, "NUMBA_AVAILABLE")
        assert hasattr(confflow, "read_xyz_file")
        assert hasattr(confflow, "ConfigSchema")

    def test_main_entrypoint_callable(self):
        main_mod = importlib.import_module("confflow.main")
        assert callable(main_mod.main)

    def test_main_entrypoint_non_integer_result_maps_to_runtime_error(self):
        main_mod = importlib.import_module("confflow.main")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(main_mod, "_cli_main", lambda _args=None: None)
            assert main_mod.main([]) == 2

    def test_confgen_key_symbols_present(self):
        import confflow.blocks.confgen as confgen

        assert hasattr(confgen, "run_generation")
        assert hasattr(confgen, "check_clash_core")

    def test_logger_available(self):
        import confflow.core.utils as utils

        lg = utils.get_logger()
        assert lg is not None

    def test_refine_core_functions(self):
        import numpy as np

        import confflow.blocks.refine as refine

        assert refine.get_element_atomic_number("Cl") == 17
        coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        assert refine.fast_rmsd(coords, coords) < 1e-6

    def test_calc_resultsdb_roundtrip(self, tmp_path):
        import confflow.calc as calc

        db = calc.ResultsDB(str(tmp_path / "res.db"))
        job_id = db.insert_result({"job_name": "j", "index": 1, "status": "success"})
        assert job_id == 1
        got = db.get_result_by_job_name("j")
        assert got is not None and got["status"] == "success"
        db.close()


@pytest.mark.integration
class TestLowEnergyTrace:
    """Tests for low energy conformer tracing."""

    def test_low_energy_trace_tracks_top6_across_steps(self, monkeypatch, tmp_path):
        import confflow.workflow.engine as engine

        def fake_run_generation(input_files, **kwargs):
            with open("search.xyz", "w", encoding="utf-8") as f:
                for i in range(6):
                    cid = f"A{i + 1:06d}"
                    f.write("2\n")
                    f.write(f"Conformer {i + 1} | CID={cid}\n")
                    f.write("H 0 0 0\n")
                    f.write("H 0 0 0.74\n")

        class FakeManager:
            def __init__(self, settings_file: str):
                self.config = {}
                self.work_dir = ""

            def run(self, input_xyz_file: str):
                from pathlib import Path

                confs = engine.io_xyz.read_xyz_file(input_xyz_file, parse_metadata=True)
                for i, c in enumerate(confs):
                    meta = c.get("metadata") or {}
                    cid = meta.get("CID")
                    c["comment"] = f"Energy={-(i + 1)} CID={cid}"
                    c["metadata"] = engine.io_xyz.parse_comment_metadata(c["comment"])
                out_dir = Path(self.work_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                out = out_dir / "output.xyz"
                engine.io_xyz.write_xyz_file(str(out), confs, atomic=False)

        monkeypatch.setattr(confgen, "run_generation", fake_run_generation)
        monkeypatch.setattr(calc, "ChemTaskManager", FakeManager)
        monkeypatch.setattr(viz, "parse_xyz_file", lambda p: [])
        monkeypatch.setattr(viz, "generate_text_report", lambda *a, **k: "")

        inp = tmp_path / "a.xyz"
        inp.write_text("2\nA\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

        cfg = {
            "global": {
                "gaussian_path": "g16",
                "orca_path": "orca",
                "cores_per_task": 1,
                "total_memory": "1GB",
                "max_parallel_jobs": 1,
            },
            "steps": [
                {"name": "step_01", "type": "confgen", "params": {"chains": ["1-2"]}},
                {
                    "name": "step_02",
                    "type": "calc",
                    "params": {"iprog": "orca", "itask": "sp", "keyword": "x"},
                },
            ],
        }
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

        work_dir = tmp_path / "work"
        stats = engine.run_workflow(
            input_xyz=[str(inp)],
            config_file=str(cfg_path),
            work_dir=str(work_dir),
            resume=False,
            verbose=False,
        )

        assert "low_energy_trace" in stats
        trace = stats["low_energy_trace"]
        assert trace["top_k"] == 6
        assert len(trace["conformers"]) == 6

        for item in trace["conformers"]:
            assert "cid" in item
            assert "trace" in item
            assert len(item["trace"]) == 2
            assert all(x["status"] == "found" for x in item["trace"])

        stats_path = work_dir / "workflow_stats.json"
        data = json.loads(stats_path.read_text(encoding="utf-8"))
        assert "low_energy_trace" in data
