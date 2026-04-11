#!/usr/bin/env python3

"""Hotspot tests for Gaussian and ORCA policies."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from confflow.calc.policies.gaussian import GaussianPolicy
from confflow.calc.policies.orca import OrcaPolicy


def test_gaussian_generate_input_supports_dict_blocks_and_link0_options(tmp_path):
    task = {
        "job_name": "job",
        "coords": ["C 0 0 0", "H 0 0 1"],
        "config": {
            "cores_per_task": 2,
            "total_memory": "2GB",
            "keyword": "opt b3lyp/6-31g(d)",
            "blocks": {"geom": {"MaxIter": 50, "UseSymmetry": False}},
            "gaussian_modredundant": ["B 1 2 F", "", "A 1 2 3 90.0 F"],
            "gaussian_oldchk": "prev.chk",
            "gaussian_chk": "next.chk",
            "gaussian_link0": ["%NoSave", " ", "%Mem=extra"],
        },
    }

    out = tmp_path / "job.gjf"
    GaussianPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")

    assert "%Chk=next.chk" in text
    assert "%OldChk=prev.chk" in text
    assert "%NoSave" in text
    assert "%Mem=extra" in text
    assert "%geom" in text
    assert "UseSymmetry false" in text
    assert "B 1 2 F" in text
    assert "A 1 2 3 90.0 F" in text


def test_gaussian_generate_input_supports_string_link0_and_disabling_chk(tmp_path):
    task = {
        "job_name": "job",
        "coords": ["C 0 0 0"],
        "config": {
            "keyword": "#p sp",
            "gaussian_write_chk": "false",
            "gaussian_link0": "%NoSave\n\n%KJob L9999",
            "gaussian_modredundant": "B 1 2 F",
        },
    }

    out = tmp_path / "job.gjf"
    GaussianPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")

    assert "%Chk=" not in text
    assert "%NoSave" in text
    assert "%KJob L9999" in text
    assert "B 1 2 F" in text


def test_gaussian_parse_output_returns_empty_for_missing_file(tmp_path):
    assert GaussianPolicy().parse_output(str(tmp_path / "missing.log"), {}, False) == {}


def test_gaussian_parse_output_falls_back_to_archive_energy_and_gibbs(tmp_path, monkeypatch):
    log = tmp_path / "job.log"
    log.write_text(
        "\\HF=-123.456\\Gibbs=-122.111\n"
        " Standard orientation:\n"
        " ---------------------------------------------------------------------\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "confflow.calc.policies.gaussian.parse_last_geometry",
        lambda *_args, **_kwargs: ["C 0 0 0"],
    )

    parsed = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert parsed["e_low"] == -123.456
    assert parsed["g_low"] == -122.111
    assert parsed["g_corr"] is None
    assert parsed["final_coords"] == ["C 0 0 0"]


def test_gaussian_parse_output_ignores_malformed_scf_and_keeps_explicit_gibbs(
    tmp_path, monkeypatch
):
    log = tmp_path / "job.log"
    log.write_text(
        "SCF Done: malformed entry\n"
        "Sum of electronic and thermal Free Energies=          -9.250000\n"
        "Thermal correction to Gibbs Free Energy=               0.500000\n"
        "\\HF=-8.000\\Gibbs=-7.500\n"
        "Frequencies -- 100.0 200.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "confflow.calc.policies.gaussian.parse_last_geometry",
        lambda *_args, **_kwargs: [],
    )

    parsed = GaussianPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert parsed["e_low"] == -8.0
    assert parsed["g_low"] == -9.25
    assert parsed["g_corr"] == 0.5
    assert parsed["num_imag_freqs"] == 0
    assert parsed["lowest_freq"] == 100.0


def test_gaussian_get_environment_sets_gauss_exedir_for_absolute_binary():
    env = GaussianPolicy().get_environment({}, ["/opt/gaussian/g16", "job.gjf"])
    assert env["GAUSS_EXEDIR"] == "/opt/gaussian"


def test_gaussian_get_environment_skips_non_absolute_binary():
    with patch.dict("confflow.calc.policies.gaussian.os.environ", {}, clear=True):
        env = GaussianPolicy().get_environment({}, ["g16", "job.gjf"])
    assert "GAUSS_EXEDIR" not in env


def test_gaussian_get_error_details_logs_debug_on_oserror(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("Error termination", encoding="utf-8")

    with (
        patch("confflow.calc.policies.gaussian.open", side_effect=OSError("boom")),
        patch("confflow.calc.policies.gaussian.logger.debug") as mock_debug,
    ):
        details = GaussianPolicy().get_error_details(str(tmp_path), "job", {})

    assert details == ""
    mock_debug.assert_called_once()


def test_gaussian_get_error_details_detects_memory_failure(tmp_path):
    log = tmp_path / "job.log"
    log.write_text(
        "Error termination\nConvergence failure\nMemory request failed\n", encoding="utf-8"
    )
    details = GaussianPolicy().get_error_details(str(tmp_path), "job", {})
    assert "Abnormal program termination" in details
    assert "SCF not converged" in details
    assert "Insufficient memory" in details


def test_gaussian_check_termination_delegates(monkeypatch):
    monkeypatch.setattr("confflow.calc.policies.gaussian._check_termination", lambda *args: True)
    assert GaussianPolicy().check_termination("job.log") is True


def test_gaussian_cleanup_lingering_processes_terminates_targets_and_logs_failures():
    target = MagicMock()
    target.info = {"pid": 10, "name": "g16"}
    failing = MagicMock()
    failing.info = {"pid": 11, "name": "l9999.exe"}
    failing.terminate.side_effect = RuntimeError("stop")
    other = MagicMock()
    other.info = {"pid": 12, "name": "python"}

    current = MagicMock()
    current.children.return_value = [target, failing, other]
    fake_psutil = SimpleNamespace(Process=lambda: current)

    with (
        patch("confflow.calc.policies.gaussian.psutil", fake_psutil),
        patch("confflow.calc.policies.gaussian.logger.debug") as mock_debug,
    ):
        GaussianPolicy().cleanup_lingering_processes({})

    target.terminate.assert_called_once()
    failing.terminate.assert_called_once()
    other.terminate.assert_not_called()
    mock_debug.assert_called_once()


def test_gaussian_cleanup_lingering_processes_noops_without_psutil():
    with patch("confflow.calc.policies.gaussian.psutil", None):
        GaussianPolicy().cleanup_lingering_processes({})


def test_orca_generate_input_merges_constraints_into_dict_blocks(tmp_path):
    task = {
        "job_name": "job",
        "coords": ["C 0 0 0", "H 0 0 1"],
        "config": {
            "cores_per_task": 2,
            "total_memory": "2GB",
            "keyword": "! r2SCAN-3c",
            "freeze": "1,3",
            "itask": "opt",
            "blocks": {
                "geom": {
                    "Constraints": ["{ C 0 C }"],
                    "MaxIter": 50,
                }
            },
        },
    }

    out = tmp_path / "job.inp"
    OrcaPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")

    assert text.count("{ C 0 C }") == 1
    assert "{ C 2 C }" in text
    assert "MaxIter 50" in text


def test_orca_generate_input_adds_constraints_when_geom_missing(tmp_path):
    task = {
        "job_name": "job",
        "coords": ["C 0 0 0"],
        "config": {
            "keyword": "! sp",
            "freeze": "2",
            "itask": "opt",
            "blocks": {"scf": {"MaxIter": 200}},
        },
    }

    out = tmp_path / "job.inp"
    OrcaPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")

    assert "%geom" in text
    assert "{ C 1 C }" in text
    assert "%scf" in text


def test_orca_generate_input_extends_string_constraints_in_dict_mode(tmp_path):
    task = {
        "job_name": "job",
        "coords": ["C 0 0 0"],
        "config": {
            "keyword": "! sp",
            "freeze": "2",
            "itask": "opt",
            "blocks": {"geom": {"Constraints": "{ C 0 C }"}},
        },
    }

    out = tmp_path / "job.inp"
    OrcaPolicy().generate_input(task, str(out))
    text = out.read_text(encoding="utf-8")

    assert "{ C 0 C }" in text
    assert "{ C 1 C }" in text


def test_orca_parse_output_returns_empty_for_missing_file(tmp_path):
    assert OrcaPolicy().parse_output(str(tmp_path / "missing.out"), {}, False) == {}


def test_orca_parse_output_uses_sp_energy_fallback_and_filters_near_zero_freqs(
    tmp_path, monkeypatch
):
    log = tmp_path / "job.out"
    log.write_text(
        "FINAL SINGLE POINT ENERGY      -10.5\n"
        "VIBRATIONAL FREQUENCIES\n"
        "0: 0.00 cm-1\n"
        "1: 0.00 cm-1\n"
        "2: 0.00 cm-1\n"
        "3: 0.00 cm-1\n"
        "4: 0.00 cm-1\n"
        "5: 0.00 cm-1\n"
        "6: 0.05 cm-1\n"
        "7: 12.50 cm-1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "confflow.calc.policies.orca.parse_last_geometry",
        lambda *_args, **_kwargs: ["H 0 0 0"],
    )

    parsed = OrcaPolicy().parse_output(str(log), {}, is_sp_task=False)

    assert parsed["e_low"] == -10.5
    assert parsed["g_low"] is None
    assert parsed["num_imag_freqs"] == 0
    assert parsed["lowest_freq"] == 12.5
    assert parsed["final_coords"] == ["H 0 0 0"]


def test_orca_check_termination_delegates(monkeypatch):
    monkeypatch.setattr("confflow.calc.policies.orca._check_termination", lambda *args: True)
    assert OrcaPolicy().check_termination("job.out") is True


def test_orca_get_error_details_logs_debug_on_oserror(tmp_path):
    log = tmp_path / "job.out"
    log.write_text("ORCA finished by error", encoding="utf-8")

    with (
        patch("confflow.calc.policies.orca.open", side_effect=OSError("boom")),
        patch("confflow.calc.policies.orca.logger.debug") as mock_debug,
    ):
        details = OrcaPolicy().get_error_details(str(tmp_path), "job", {})

    assert details == ""
    mock_debug.assert_called_once()


def test_orca_get_error_details_detects_scf_failure(tmp_path):
    log = tmp_path / "job.out"
    log.write_text("ORCA finished by error\nSCF NOT CONVERGED\n", encoding="utf-8")
    details = OrcaPolicy().get_error_details(str(tmp_path), "job", {})
    assert "Abnormal program termination" in details
    assert "SCF not converged" in details


def test_orca_cleanup_lingering_processes_terminates_targets_and_logs_failures():
    target = MagicMock()
    target.info = {"pid": 20, "name": "orca"}
    failing = MagicMock()
    failing.info = {"pid": 21, "name": "otool_xtb"}
    failing.terminate.side_effect = RuntimeError("stop")
    other = MagicMock()
    other.info = {"pid": 22, "name": "python"}

    current = MagicMock()
    current.children.return_value = [target, failing, other]
    fake_psutil = SimpleNamespace(Process=lambda: current)

    with (
        patch("confflow.calc.policies.orca.psutil", fake_psutil),
        patch("confflow.calc.policies.orca.logger.debug") as mock_debug,
    ):
        OrcaPolicy().cleanup_lingering_processes({})

    target.terminate.assert_called_once()
    failing.terminate.assert_called_once()
    other.terminate.assert_not_called()
    mock_debug.assert_called_once()


def test_orca_cleanup_lingering_processes_noops_without_psutil():
    with patch("confflow.calc.policies.orca.psutil", None):
        OrcaPolicy().cleanup_lingering_processes({})
