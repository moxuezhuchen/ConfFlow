#!/usr/bin/env python3

"""Tests for input file generation snapshots and CHK artifact IO."""

from __future__ import annotations

import re

import pytest


class TestChkArtifactIO:
    """Tests for checkpoint file artifact IO."""

    def test_gaussian_chk_artifact_stage_and_link0(self, tmp_path):
        from confflow.calc.components import executor
        from confflow.calc.policies.gaussian import GaussianPolicy

        prev = tmp_path / "prev_backups"
        prev.mkdir()

        job = "A000001"
        (prev / f"{job}.chk").write_text("dummy-checkpoint", encoding="utf-8")

        work = tmp_path / "work" / job
        cfg = {
            "iprog": "g16",
            "itask": "sp",
            "keyword": "sp",
            "input_chk_dir": str(prev),
            "gaussian_write_chk": "true",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "charge": 0,
            "multiplicity": 1,
            "freeze": "0",
        }

        executor.prepare_task_inputs(str(work), job, cfg)

        assert (work / f"{job}.old.chk").exists()
        assert cfg.get("gaussian_oldchk") == f"{job}.old.chk"

        inp = tmp_path / "job.gjf"
        GaussianPolicy().generate_input(
            {"job_name": job, "coords": ["H 0 0 0"], "config": cfg}, str(inp)
        )
        text = inp.read_text(encoding="utf-8")

        assert f"%OldChk={job}.old.chk" in text
        assert f"%Chk={job}.chk" in text

    def test_gaussian_chk_stage_missing_source_is_noop(self, tmp_path):
        from confflow.calc.components import executor

        work = tmp_path / "work" / "A000001"
        cfg = {"input_chk_dir": str(tmp_path / "nope")}

        executor.prepare_task_inputs(str(work), "A000001", cfg)

        assert not work.exists() or not any(work.iterdir())
        assert "gaussian_oldchk" not in cfg


class TestInputGenerationSnapshot:
    """Tests for input file generation snapshots."""

    def test_gaussian_generate_input_semantic_snapshot(self, tmp_path):
        from confflow.calc.policies.gaussian import GaussianPolicy

        cfg = {
            "iprog": 1,
            "cores_per_task": 2,
            "max_parallel_jobs": 1,
            "total_memory": "2048MB",
            "keyword": "opt freq b3lyp/6-31g(d)",
            "charge": 0,
            "multiplicity": 1,
            "freeze": "2",
            "blocks": "SCRF=(SMD,Solvent=Water)\nIOp(3/33=1)",
            "gaussian_modredundant": ["B 1 2 F", "A 1 2 3 F"],
        }

        task_info = {
            "job_name": "job1",
            "coords": [
                "O 0.0000 0.0000 0.0000",
                "H 0.0000 0.0000 1.0000",
                "H 1.0000 0.0000 0.0000",
            ],
            "config": cfg,
        }

        out = tmp_path / "job1.gjf"
        GaussianPolicy().generate_input(task_info, str(out))
        text = out.read_text(encoding="utf-8")

        assert "%nproc=2" in text
        assert "%mem=2GB" in text
        assert "# opt freq b3lyp/6-31g(d)" in text
        assert "job1" in text
        assert "0 1" in text
        assert re.search(r"^\s*H\s+-1\b", text, flags=re.M) is not None
        assert "SCRF=(SMD,Solvent=Water)" in text
        assert "IOp(3/33=1)" in text
        assert "B 1 2 F" in text
        assert "A 1 2 3 F" in text

    def test_orca_generate_input_semantic_snapshot(self, tmp_path):
        from confflow.calc.policies.orca import OrcaPolicy

        cfg = {
            "iprog": 2,
            "cores_per_task": 4,
            "orca_maxcore": 512,
            "keyword": "opt",
            "charge": 0,
            "multiplicity": 1,
            "itask": "opt",
            "freeze": "1,3",
        }

        task_info = {
            "job_name": "job2",
            "coords": [
                "C 0.0 0.0 0.0",
                "H 0.0 0.0 1.0",
                "H 1.0 0.0 0.0",
            ],
            "config": cfg,
        }

        out = tmp_path / "job2.inp"
        OrcaPolicy().generate_input(task_info, str(out))
        text = out.read_text(encoding="utf-8")

        assert text.lstrip().startswith("! opt")
        assert "%pal nprocs 4 end" in text
        assert "%maxcore 512" in text
        assert "%geom" in text
        assert "Constraints" in text
        assert "{ C 0 C }" in text
        assert "{ C 2 C }" in text
        assert "* xyz 0 1" in text
        assert "C 0.0 0.0 0.0" in text
