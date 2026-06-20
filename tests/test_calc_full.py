#!/usr/bin/env python3

"""Integration-style tests for calc policies and typed config."""

from __future__ import annotations

from confflow import calc
from confflow.calc.policies.gaussian import GaussianPolicy
from confflow.calc.policies.orca import OrcaPolicy
from confflow.config.models import CalcStepParams, GlobalOptions


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
    out_path = tmp_path / "job.gjf"
    GaussianPolicy().generate_input(task, str(out_path))
    assert "%mem=4GB" in out_path.read_text(encoding="utf-8")


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
    OrcaPolicy().generate_input(task, str(out))
    assert "%maxcore 1000" in out.read_text(encoding="utf-8")


def test_parse_output_gaussian_archive_hf(tmp_path):
    log = tmp_path / "job.log"
    log.write_text(
        "Some header\n"
        "\\Version=ES64L-G16RevC.02\\HF=-3576.321253\\RMSD=0.000e+00\\@\n"
        "Normal termination of Gaussian 16\n",
        encoding="utf-8",
    )
    res = calc.parse_output(str(log), {}, prog_id=1)
    assert res["e_low"] == -3576.321253


def test_parse_output_orca(tmp_path):
    log = tmp_path / "job.out"
    log.write_text(
        "FINAL SINGLE POINT ENERGY      -1.234567891234\n"
        "CARTESIAN COORDINATES (ANGSTROEM)\n"
        "---------------------------------\n"
        "H      0.000000    0.000000    0.000000\n",
        encoding="utf-8",
    )
    res = calc.parse_output(str(log), {}, prog_id=2)
    assert res["e_low"] == -1.234567891234


def test_typed_calc_config_generates_policy_runtime_dict():
    config = CalcStepParams.from_params(
        {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "HF",
            "cores_per_task": 2,
            "total_memory": "8GB",
            "blocks": {"pal": "nprocs 2 end"},
        },
        GlobalOptions.from_mapping({}),
    )
    runtime = config.to_runtime_dict()
    assert runtime["iprog"] == "orca"
    assert runtime["itask"] == "sp"
    assert runtime["cores_per_task"] == 2
    assert "%pal" in runtime["blocks"]
