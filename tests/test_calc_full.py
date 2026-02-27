#!/usr/bin/env python3
from __future__ import annotations

import pytest

from confflow import calc
from confflow.calc.components import executor
from confflow.calc.policies.gaussian import GaussianPolicy
from confflow.calc.policies.orca import OrcaPolicy


def test_memory_calculation_gaussian(tmp_path):
    task = {
        "job_name": "job",
        "coords": ["H 0 0 0"],
        "config": {
            "iprog": "g16",
            "total_memory": "8GB",
            "max_parallel_jobs": 2,
            "cores_per_task": 4,
            "keyword": "sp",
            "charge": 0,
            "multiplicity": 1,
        },
    }
    # mem_per_job = 8GB / 2 = 4GB
    out_path = tmp_path / "job.gjf"
    GaussianPolicy().generate_input(task, str(out_path))
    text = out_path.read_text(encoding="utf-8")
    assert "%mem=4GB" in text


def test_memory_calculation_orca(tmp_path):
    out = tmp_path / "job.inp"
    task = {
        "job_name": "job",
        "coords": ["H 0 0 0"],
        "config": {
            "iprog": "orca",
            "total_memory": "8GB",
            "max_parallel_jobs": 2,
            "cores_per_task": 4,
            "keyword": "sp",
        },
    }
    # mem_per_job = 8GB / 2 = 4096MB
    # mem_per_core = 4096 / 4 = 1024MB
    # rounded to hundreds = 1000
    OrcaPolicy().generate_input(task, str(out))
    text = out.read_text()
    assert "%maxcore 1000" in text


def test_memory_calculation_orca_explicit(tmp_path):
    out = tmp_path / "job.inp"
    task = {
        "job_name": "job",
        "coords": ["H 0 0 0"],
        "config": {"iprog": "orca", "orca_maxcore": "4500", "keyword": "sp"},
    }
    OrcaPolicy().generate_input(task, str(out))
    text = out.read_text()
    assert "%maxcore 4500" in text


def test_parse_output_gaussian(tmp_path):
    log = tmp_path / "job.log"
    log.write_text(
        """
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          6           0        0.000000    0.000000    0.000000
 ---------------------------------------------------------------------
 SCF Done:  E(RB3LYP) =  -1.23456789     A.U. after   10 cycles
 Normal termination of Gaussian 16
""",
        encoding="utf-8",
    )

    res = calc.parse_output(str(log), {}, prog_id=1)
    assert res["e_low"] == -1.23456789
    assert len(res["final_coords"]) == 1


def test_parse_output_gaussian_archive_hf(tmp_path):
    log = tmp_path / "job.log"
    # Simulate: no SCF Done, but archive section contains \HF=...\@ (this is how real Gaussian logs end)
    log.write_text(
        """
 Some header
 \\Version=ES64L-G16RevC.02\\HF=-3576.321253\\RMSD=0.000e+00\\@
 The archive entry for this job was punched.
 Normal termination of Gaussian 16
 """,
        encoding="utf-8",
    )
    res = calc.parse_output(str(log), {}, prog_id=1)
    assert res["e_low"] == -3576.321253


def test_parse_output_orca(tmp_path):
    log = tmp_path / "job.out"
    log.write_text(
        """
-----------------------
FINAL SINGLE POINT ENERGY      -1.234567891234
-----------------------
****ORCA TERMINATED NORMALLY****
""",
        encoding="utf-8",
    )

    # ORCA parser might need coordinates from log or xyz.
    # If log doesn't have CARTESIAN COORDINATES, it might return None for coords but energy should be there.
    res = calc.parse_output(str(log), {}, prog_id=2)
    assert res["e_low"] == -1.234567891234


def test_results_db(tmp_path):
    db_path = str(tmp_path / "results.db")
    db = calc.ResultsDB(db_path)

    job_name = "test_job"
    result = {
        "job_name": job_name,
        "index": 1,
        "status": "completed",
        "energy": -1.0,
        "final_gibbs_energy": -0.9,
        "final_coords": ["H 0 0 0", "H 0 0 0.74"],
    }
    db.insert_result(result)

    saved = db.get_result_by_job_name(job_name)
    assert saved["energy"] == -1.0
    assert saved["final_gibbs_energy"] == -0.9

    all_res = db.get_all_results()
    assert len(all_res) == 1


def test_resource_monitoring(monkeypatch):
    # Mock psutil
    class MockMem:
        percent = 50

    monkeypatch.setattr("psutil.virtual_memory", lambda: MockMem())
    monkeypatch.setattr("psutil.cpu_percent", lambda interval: 40)

    # Test ResourceMonitor directly
    monitor = calc.ResourceMonitor()
    # This should not raise or hang
    monitor.wait_for_resources()


def test_cleanup_logic(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "test.tmp").write_text("temp")
    (work_dir / "test.out").write_text("output")
    (work_dir / "test.gbw").write_text("binary")
    (work_dir / "test.chk").write_text("checkpoint")

    # Test handle_backups directly
    # handle_backups(work_dir, config, success, cleanup_work_dir=True)
    config = {"ibkout": 1, "backup_dir": str(tmp_path / "backups")}
    calc.handle_backups(str(work_dir), config, success=True, cleanup_work_dir=True)

    # work_dir should be deleted (cleanup_work_dir=True)
    assert not work_dir.exists()
    # backups should have .out
    assert (tmp_path / "backups" / "test.out").exists()
    # .chk should be in backups (g16 checkpoint)
    assert (tmp_path / "backups" / "test.chk").exists()
    # .tmp and .gbw should NOT be in backups (they are not in backup_exts)
    assert not (tmp_path / "backups" / "test.tmp").exists()


def test_chem_task_manager_skip_existing(tmp_path, monkeypatch):
    # Create a dummy xyz
    xyz = tmp_path / "search.xyz"
    xyz.write_text("1\nTest\nH 0 0 0\n", encoding="utf-8")

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    db_path = work_dir / "results.db"

    # Pre-insert a result
    db = calc.ResultsDB(str(db_path))
    db.insert_result(
        {
            "job_name": "A000001",
            "index": 1,
            "status": "success",
            "energy": -1.0,
            "final_coords": ["H 0 0 0"],
        }
    )

    # Mock ChemTaskManager to use this work_dir
    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(work_dir)
    manager.config.update({"iprog": "orca", "itask": "sp", "auto_clean": "false"})

    # Mock run_single_task to fail if called
    def error_run(*args, **kwargs):
        pytest.fail("run_single_task should not be called for existing results")

    import confflow.calc.manager as manager_mod

    monkeypatch.setattr(manager_mod, "_run_task", error_run)

    # Run manager - it should skip A000001
    manager.run(str(xyz))


def test_get_error_details(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    log = work_dir / "test.log"
    log.write_text("Error termination in Gaussian\nSCF NOT CONVERGED\n", encoding="utf-8")

    config = {"iprog": "g16"}
    policy = GaussianPolicy()
    details = executor._get_error_details(
        str(work_dir), "test", config, Exception("test error"), policy
    )
    assert "Abnormal program termination" in details
    assert "SCF not converged" in details


def test_config_hash(tmp_path):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    config = {"itask": "opt", "iprog": "orca"}
    executor._save_config_hash(str(work_dir), config)
    assert (work_dir / ".config_hash").exists()


def test_ts_without_freq_treated_like_opt(cd_tmp, monkeypatch):
    work_dir = cd_tmp / "A000001"
    job_name = "A000001"

    # mock: do not run external programs, directly return a result with only energy and no frequency info
    def fake_run(*args, **kwargs):
        return {
            "e_low": -1.23,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "final_coords": ["H 0 0 0"],
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    task_info = {
        "job_name": job_name,
        "work_dir": str(work_dir),
        "coords": ["H 0 0 0"],
        "config": {
            "iprog": "g16",
            "itask": "ts",
            # key: does not contain freq
            "keyword": "opt=(ts,calcfc,noeigen)",
        },
    }

    result = calc.TaskRunner().run(task_info)
    assert result["status"] == "success"
    assert result["energy"] == -1.23
    assert result.get("num_imag_freqs") is None


def test_ts_with_freq_still_requires_one_imag(monkeypatch, cd_tmp):
    work_dir = cd_tmp / "A000001"
    job_name = "A000001"

    def fake_run(*args, **kwargs):
        return {
            "e_low": -1.23,
            "g_low": None,
            "g_corr": None,
            # returns 0 imaginary frequencies -> should fail
            "num_imag_freqs": 0,
            "lowest_freq": 12.3,
            "final_coords": ["H 0 0 0"],
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    task_info = {
        "job_name": job_name,
        "work_dir": str(work_dir),
        "coords": ["H 0 0 0"],
        "config": {
            "iprog": "g16",
            "itask": "ts",
            # key: contains freq
            "keyword": "opt=(ts,calcfc,noeigen) freq",
        },
    }

    result = calc.TaskRunner().run(task_info)
    assert result["status"] == "failed"
    assert "imaginary frequenc" in result.get("error", "")


def test_ts_without_freq_fails_when_ts_bond_drift_too_large(cd_tmp, monkeypatch):
    work_dir = cd_tmp / "A000001"
    job_name = "A000001"

    # initial bond length 1.0 Å
    initial_coords = ["H 0 0 0", "H 0 0 1.0"]
    # final bond length 1.6 Å -> |ΔR|=0.6 > default threshold 0.4
    final_coords = ["H 0 0 0", "H 0 0 1.6"]

    def fake_run(*args, **kwargs):
        return {
            "e_low": -1.23,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "final_coords": final_coords,
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    task_info = {
        "job_name": job_name,
        "work_dir": str(work_dir),
        "coords": initial_coords,
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "keyword": "opt=(ts,calcfc,noeigen)",
            "ts_bond_atoms": "1,2",
            "ts_rescue_scan": "false",
        },
    }

    result = calc.TaskRunner().run(task_info)
    assert result["status"] == "failed"
    assert "bond drift" in result.get("error", "")


def test_ts_without_freq_allows_large_rmsd_when_no_bond_drift(cd_tmp, monkeypatch):
    work_dir = cd_tmp / "A000001"
    job_name = "A000001"

    # two atoms: final structure stretched to 5.0 Å (RMSD is no longer used as TS criterion)
    initial_coords = ["H 0 0 0", "H 0 0 1.0"]
    final_coords = ["H 0 0 0", "H 0 0 5.0"]

    def fake_run(*args, **kwargs):
        return {
            "e_low": -1.23,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "final_coords": final_coords,
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    task_info = {
        "job_name": job_name,
        "work_dir": str(work_dir),
        "coords": initial_coords,
        "config": {
            "iprog": "g16",
            "itask": "ts",
            "keyword": "opt=(ts,calcfc,noeigen)",
            "ts_rescue_scan": "false",
        },
    }

    result = calc.TaskRunner().run(task_info)
    assert result["status"] == "success"


def test_ts_bond_length_computed_and_written(cd_tmp, monkeypatch):
    # trigger single-task path to write result.xyz
    inp = cd_tmp / "search.xyz"
    inp.write_text(
        "2\ncomment\nH 0 0 0\nH 0 0 0.74\n",
        encoding="utf-8",
    )

    # mock external calculation: return final_coords (distance between two atoms is 0.74)
    def fake_run(*args, **kwargs):
        return {
            "e_low": -1.0,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "final_coords": ["H 0 0 0", "H 0 0 0.74"],
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(cd_tmp / "work")
    manager.config.update(
        {
            "iprog": "g16",
            "itask": "ts",
            "keyword": "opt=(ts,calcfc,noeigen)",
            "auto_clean": "false",
            "max_parallel_jobs": 1,
            "ts_bond_atoms": "1,2",
        }
    )

    manager.run(str(inp))
    out_file = cd_tmp / "work" / "result.xyz"
    assert out_file.exists()
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert any("TSAtoms=1,2" in ln for ln in lines)
    assert any("TSBond=" in ln for ln in lines)


def test_manager_writes_isomers_failed_when_tasks_fail(cd_tmp, monkeypatch):
    inp = cd_tmp / "search.xyz"
    inp.write_text(
        "2\ncomment CID=abc\nH 0 0 0\nH 0 0 0.74\n",
        encoding="utf-8",
    )

    def fake_run(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(cd_tmp / "work")
    manager.config.update(
        {
            "iprog": "g16",
            "itask": "opt",
            "keyword": "opt",
            "auto_clean": "false",
            "max_parallel_jobs": 1,
        }
    )

    manager.run(str(inp))
    failed_file = cd_tmp / "work" / "failed.xyz"
    assert failed_file.exists()
    txt = failed_file.read_text(encoding="utf-8")
    assert "Failed=1" in txt
    assert "Job=abc" in txt
    assert "Error=boom" in txt


def test_sp_task_also_writes_tsbond_when_config_provides_ts_bond_atoms(cd_tmp, monkeypatch):
    inp = cd_tmp / "search.xyz"
    inp.write_text(
        "2\ncomment\nH 0 0 0\nH 0 0 0.74\n",
        encoding="utf-8",
    )

    # SP should not change geometry; final_coords directly returns input coordinates
    def fake_run(*args, **kwargs):
        return {
            "e_low": -1.0,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "final_coords": ["H 0 0 0", "H 0 0 0.74"],
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(cd_tmp / "work")
    manager.config.update(
        {
            "iprog": "g16",
            "itask": "sp",
            "keyword": "sp",
            "auto_clean": "false",
            "max_parallel_jobs": 1,
            "ts_bond_atoms": "1,2",
        }
    )

    manager.run(str(inp))
    out_file = cd_tmp / "work" / "result.xyz"
    assert out_file.exists()
    text = out_file.read_text(encoding="utf-8")
    assert "TSAtoms=1,2" in text
    assert "TSBond=" in text


def test_sp_inherits_g_corr_and_outputs_final_gibbs_energy(cd_tmp, monkeypatch):
    # input XYZ comment carries thermal correction from previous freq/opt_freq stage
    inp = cd_tmp / "search.xyz"
    inp.write_text(
        "2\nRank=1 | E=-1.00000000 | G_corr=0.123 | Imag=0\nH 0 0 0\nH 0 0 0.74\n",
        encoding="utf-8",
    )

    # mock SP: return single-point energy -2.0
    def fake_run(*args, **kwargs):
        return {
            "e_low": -2.0,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "final_coords": ["H 0 0 0", "H 0 0 0.74"],
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(cd_tmp / "work")
    manager.config.update(
        {
            "iprog": "g16",
            "itask": "sp",
            "keyword": "sp",
            "auto_clean": "false",
            "max_parallel_jobs": 1,
        }
    )
    manager.run(str(inp))

    out_file = cd_tmp / "work" / "result.xyz"
    text = out_file.read_text(encoding="utf-8")
    # Energy/G should be E_sp + G_corr = -2.0 + 0.123
    assert ("Energy=-1.877" in text) or ("G=-1.877" in text)
    assert ("G_corr=0.123" in text) or ("G=-1.877" in text)


def test_ts_inherits_g_corr_and_outputs_final_gibbs_energy(cd_tmp, monkeypatch):
    # input XYZ comment carries G_corr from previous stage
    inp = cd_tmp / "search.xyz"
    inp.write_text(
        "2\nRank=1 | E=-1.00000000 | G_corr=0.123 | CID=abc\nH 0 0 0\nH 0 0 0.74\n",
        encoding="utf-8",
    )

    # mock TS: return energy -1.5, no g_low
    def fake_run(*args, **kwargs):
        return {
            "e_low": -1.5,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": None,
            "lowest_freq": None,
            "final_coords": ["H 0 0 0", "H 0 0 0.74"],
        }

    monkeypatch.setattr(executor, "_run_calculation_step", fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *a, **k: None)

    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(cd_tmp / "work")
    manager.config.update(
        {
            "iprog": "g16",
            "itask": "ts",
            "keyword": "opt=(ts,calcfc,noeigen)",
            "auto_clean": "false",
            "max_parallel_jobs": 1,
        }
    )
    manager.run(str(inp))

    out_file = cd_tmp / "work" / "result.xyz"
    text = out_file.read_text(encoding="utf-8")
    # Energy should be E_ts + G_corr = -1.5 + 0.123 = -1.377
    assert "Energy=-1.377" in text
    assert "G_corr=0.123" in text


def test_resume_from_backups_skips_completed(tmp_path, monkeypatch):
    inp = tmp_path / "search.xyz"
    inp.write_text(
        "1\ncomment\nH 0 0 0\n",
        encoding="utf-8",
    )

    work_dir = tmp_path / "work"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # prepare a "completed" Gaussian log and xyz backup (job_name=A000001)
    (backup_dir / "A000001.log").write_text(
        "SCF Done:  E(RB3LYP) =  -1.23456789     A.U. after   10 cycles\n"
        "Normal termination of Gaussian 16\n",
        encoding="utf-8",
    )
    (backup_dir / "A000001.xyz").write_text(
        "1\nEnergy=-1.23456789\nH 0 0 0\n",
        encoding="utf-8",
    )

    # if actual calculation is reached, fail immediately: means it was not skipped
    def boom(*args, **kwargs):
        pytest.fail("run_single_task should be skipped when backups are available")

    import confflow.calc.manager as manager_mod

    monkeypatch.setattr(manager_mod, "_run_task", boom)

    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(work_dir)
    manager.config.update(
        {
            "iprog": "g16",
            "itask": "opt",
            "keyword": "opt",
            "auto_clean": "false",
            "max_parallel_jobs": 1,
            "backup_dir": str(backup_dir),
            "resume_from_backups": "true",
        }
    )

    manager.run(str(inp))
    out_file = work_dir / "result.xyz"
    assert out_file.exists()
    text = out_file.read_text(encoding="utf-8")
    assert "Energy=-1.23456789" in text
