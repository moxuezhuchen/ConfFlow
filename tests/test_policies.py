#!/usr/bin/env python3

"""Tests for quantum chemistry program policies (merged: Gaussian/ORCA)."""

from __future__ import annotations

import os

import pytest

from confflow.calc.policies.gaussian import GaussianPolicy
from confflow.calc.policies.orca import OrcaPolicy


@pytest.mark.parametrize(
    "policy_cls, name, input_ext, log_ext",
    [(GaussianPolicy, "gaussian", "gjf", "log"), (OrcaPolicy, "orca", "inp", "out")],
)
def test_policy_basic(policy_cls, name, input_ext, log_ext):
    policy = policy_cls()
    assert policy.name == name
    assert policy.input_ext == input_ext
    assert policy.log_ext == log_ext


def test_gaussian_generate_input(tmp_path):
    policy = GaussianPolicy()
    task_info = {
        "job_name": "test_job",
        "coords": ["C 0 0 0", "H 0 0 1"],
        "config": {
            "cores_per_task": 4,
            "maxcore": 4000,
            "keyword": "# B3LYP/6-31G(d) Opt",
            "charge": 0,
            "multiplicity": 1,
            "freeze": "1",
        },
    }
    inp = tmp_path / "test.gjf"
    policy.generate_input(task_info, str(inp))
    assert inp.exists()
    content = inp.read_text()
    assert "%nproc=4" in content
    assert "%mem=4GB" in content
    assert "# B3LYP/6-31G(d) Opt" in content
    assert "0 1" in content
    assert "C  -1" in content


@pytest.mark.parametrize(
    "policy_cls, content, expected",
    [
        (GaussianPolicy, "SCF Done:  E(RB3LYP) =  -1.23456789     A.U.\n", -1.23456789),
        (OrcaPolicy, "FINAL SINGLE POINT ENERGY      -1.23456789\n", -1.23456789),
    ],
)
def test_policy_parse_output_sp(policy_cls, content, expected, tmp_path):
    policy = policy_cls()
    log = tmp_path / "test.log"
    log.write_text(content)
    res = policy.parse_output(str(log), {}, is_sp_task=True)
    assert res["e_high"] == expected


def test_gaussian_parse_output_prefers_last_scf_done_over_archive_hf_real_log():
    log_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "gaussian_parse_output_minimal.log"
    )
    assert os.path.exists(log_path)

    parsed = GaussianPolicy().parse_output(log_path, config={}, is_sp_task=False)

    assert parsed["e_low"] == -3576.57992642
    assert parsed["g_corr"] == 0.946649


def test_gaussian_parse_output_opt(tmp_path):
    policy = GaussianPolicy()
    log = tmp_path / "test.log"
    content = (
        "Sum of electronic and thermal Free Energies=          -1.543210\n"
        "Thermal correction to Gibbs Free Energy=               0.123450\n"
        " 1 imaginary frequencies (negative Signs)\n"
        " Frequencies -- -100.0000   200.0000   300.0000\n"
        " Standard orientation:\n"
        " ---------------------------------------------------------------------\n"
        " Center     Atomic      Atomic             Coordinates (Angstroms)\n"
        " Number     Number       Type             X           Y           Z\n"
        " ---------------------------------------------------------------------\n"
        "      1          6           0        0.000000    0.000000    0.000000\n"
        "      2          1           0        0.000000    0.000000    1.000000\n"
        " ---------------------------------------------------------------------\n"
    )
    log.write_text(content)
    res = policy.parse_output(str(log), {}, is_sp_task=False)
    assert res["g_low"] == -1.54321
    assert res["g_corr"] == 0.12345
    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -100.0
    assert len(res["final_coords"]) == 2


@pytest.mark.parametrize(
    "policy_cls, cfg_key, cfg_val, filename, expected",
    [
        (GaussianPolicy, "gaussian_path", "g16", "test.gjf", ["g16", "test.gjf"]),
        (OrcaPolicy, "orca_path", "/usr/bin/orca", "test.inp", ["/usr/bin/orca", "test.inp"]),
    ],
)
def test_get_execution_command(policy_cls, cfg_key, cfg_val, filename, expected):
    policy = policy_cls()
    config = {cfg_key: cfg_val}
    cmd = policy.get_execution_command(config, filename)
    assert cmd == expected


@pytest.mark.parametrize(
    "policy_cls, filename, log_text",
    [
        (GaussianPolicy, "job1.log", "Error termination via Lnk1e\nConvergence failure\n"),
        (OrcaPolicy, "job1.out", "ORCA finished by error\nSCF NOT CONVERGED\n"),
    ],
)
def test_get_error_details(policy_cls, filename, log_text, tmp_path):
    policy = policy_cls()
    log = tmp_path / filename
    log.write_text(log_text)
    details = policy.get_error_details(str(tmp_path), "job1", {})
    assert "Abnormal program termination" in details
    assert "SCF not converged" in details


def test_orca_policy_basic():
    policy = OrcaPolicy()
    assert policy.name == "orca"
    assert policy.input_ext == "inp"
    assert policy.log_ext == "out"


def test_orca_generate_input(tmp_path):
    policy = OrcaPolicy()
    task_info = {
        "coords": ["C 0 0 0", "H 0 0 1"],
        "config": {
            "cores_per_task": 2,
            "maxcore": 2000,
            "keyword": "! B3LYP def2-SVP",
            "charge": 0,
            "multiplicity": 1,
            "freeze": "1",
        },
    }
    inp = tmp_path / "test.inp"
    policy.generate_input(task_info, str(inp))
    assert inp.exists()
    content = inp.read_text()
    assert "pal nprocs 2 end" in content
    assert "maxcore 2000" in content
    assert "! B3LYP def2-SVP" in content
    assert "C 0 0 0" in content
    assert "%geom" in content
    assert "Constraints" in content
    assert "{ C 0 C }" in content


def test_orca_parse_output_sp(tmp_path):
    policy = OrcaPolicy()
    log = tmp_path / "test.out"
    log.write_text("FINAL SINGLE POINT ENERGY      -1.23456789\n")
    res = policy.parse_output(str(log), {}, is_sp_task=True)
    assert res["e_high"] == -1.23456789


def test_orca_parse_output_opt(tmp_path):
    policy = OrcaPolicy()
    log = tmp_path / "test.out"
    content = (
        "G-E(el) ... 0.12345 Eh\n"
        "Final Gibbs free energy ... -1.54321 Eh\n"
        "VIBRATIONAL FREQUENCIES\n"
        "-----------------------\n"
        "0: 0.00 cm-1\n"
        "1: 0.00 cm-1\n"
        "2: 0.00 cm-1\n"
        "3: 0.00 cm-1\n"
        "4: 0.00 cm-1\n"
        "5: 0.00 cm-1\n"
        "6: -100.00 cm-1\n"
        "7: 200.00 cm-1\n"
        "CARTESIAN COORDINATES (ANGSTROEM)\n"
        "---------------------------------\n"
        "C 0.0 0.0 0.0\n"
        "H 0.0 0.0 1.0\n"
        "\n"
        " \n"
    )
    log.write_text(content)
    res = policy.parse_output(str(log), {}, is_sp_task=False)
    assert res["g_corr"] == 0.12345
    assert res["g_low"] == -1.54321
    assert res["num_imag_freqs"] == 1
    assert res["lowest_freq"] == -100.0
    assert len(res["final_coords"]) == 2


def test_orca_get_execution_command():
    policy = OrcaPolicy()
    config = {"orca_path": "/usr/bin/orca"}
    cmd = policy.get_execution_command(config, "test.inp")
    assert cmd == ["/usr/bin/orca", "test.inp"]


def test_orca_get_error_details(tmp_path):
    policy = OrcaPolicy()
    log = tmp_path / "job1.out"
    log.write_text("ORCA finished by error\nSCF NOT CONVERGED\n")
    details = policy.get_error_details(str(tmp_path), "job1", {})
    assert "Abnormal program termination" in details
    assert "SCF not converged" in details


def test_gaussian_policy_freeze_marks_frozen_atom(tmp_path):
    import re

    out = tmp_path / "job.gjf"
    task = {
        "job_name": "job",
        "coords": [
            "H 0.0 0.0 0.0",
            "H 0.0 0.0 0.74",
            "H 0.0 0.74 0.0",
        ],
        "config": {
            "iprog": "g16",
            "keyword": "opt(nomicro)",
            "charge": 0,
            "multiplicity": 1,
            "cores_per_task": 1,
            "total_memory": "1GB",
            "max_parallel_jobs": 1,
            "freeze": "2",
        },
    }
    GaussianPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")
    assert re.search(r"^H\s+-1\s+0\.000000", text, flags=re.M)
    assert re.search(r"^H\s+0\s+0\.000000", text, flags=re.M)


def test_gaussian_policy_keyword_prefix_and_memory_alias(tmp_path):
    out = tmp_path / "job.gjf"
    task = {
        "job_name": "job",
        "coords": [
            "H 0.0 0.0 0.0",
            "H 0.0 0.0 0.74",
        ],
        "config": {
            "iprog": "g16",
            "keyword": "#p opt(nomicro)",
            "charge": 0,
            "multiplicity": 1,
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "memory": "2GB",
        },
    }
    GaussianPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")
    assert "#p #p" not in text
    assert "#p opt(nomicro)" in text
    assert "%mem=2GB" in text


def test_gaussian_policy_keyword_plain_p_prefix_is_preserved(tmp_path):
    out = tmp_path / "job.gjf"
    task = {
        "job_name": "job",
        "coords": [
            "H 0.0 0.0 0.0",
            "H 0.0 0.0 0.74",
        ],
        "config": {
            "iprog": "g16",
            "keyword": "p opt(rcfc,tight,ts,noeigentest) freq b3lyp 6-31g(d) em=gd3bj",
            "charge": 0,
            "multiplicity": 1,
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "memory": "2GB",
        },
    }
    GaussianPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")
    assert "#p opt(rcfc,tight,ts,noeigentest) freq b3lyp 6-31g(d) em=gd3bj" in text


def test_orca_policy_freeze_constraint(tmp_path):
    out = tmp_path / "job.inp"
    task = {
        "job_name": "job",
        "coords": [
            "H 0.0 0.0 0.0",
            "H 0.0 0.0 0.74",
        ],
        "config": {
            "iprog": "orca",
            "keyword": "r2SCAN-3c",
            "charge": 0,
            "multiplicity": 1,
            "cores_per_task": 1,
            "total_memory": "1000MB",
            "max_parallel_jobs": 1,
            "freeze": "1",
        },
    }
    OrcaPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")
    assert "%geom" in text
    assert "{ C 0 C }" in text


def test_chem_task_manager_run_smoke_without_external_program(tmp_path, monkeypatch):
    import os

    from confflow import calc
    from confflow.calc.components import executor

    xyz = tmp_path / "search.xyz"
    xyz.write_text("""2\nTest\nH 0 0 0\nH 0 0 0.74\n""", encoding="utf-8")

    def _fake_run(work_dir, job_name, prog_id, coords, config, is_sp_task=False):
        inp = os.path.join(work_dir, f"{job_name}.inp")
        os.makedirs(work_dir, exist_ok=True)
        OrcaPolicy().generate_input({"job_name": job_name, "coords": coords, "config": config}, inp)
        return {
            "e_low": -1.0,
            "g_low": None,
            "g_corr": None,
            "num_imag_freqs": 0,
            "lowest_freq": None,
            "final_coords": coords,
        }

    monkeypatch.setattr(executor, "_run_calculation_step", _fake_run)
    monkeypatch.setattr(executor, "handle_backups", lambda *args, **kwargs: None)

    manager = calc.ChemTaskManager(settings_file=None)
    manager.work_dir = str(tmp_path / "work")
    manager.config.update(
        {
            "iprog": "orca",
            "orca_path": "orca",
            "gaussian_path": "g16",
            "keyword": "r2SCAN-3c",
            "itask": "sp",
            "cores_per_task": "1",
            "total_memory": "1000MB",
            "max_parallel_jobs": "1",
            "auto_clean": "false",
            "freeze": "1",
        }
    )
    manager.run(str(xyz))

    out = tmp_path / "work" / "result.xyz"
    assert out.exists()
    assert "Energy=" in out.read_text(encoding="utf-8")
