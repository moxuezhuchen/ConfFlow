"""Gaussian ``External`` calculations publish the Recovered energy, with proof.

A Gaussian job routed through ``external="..."`` prints no ``SCF Done`` line.
Each evaluation of the external program is echoed as ``Recovered energy=``
at full precision.  ConfFlow publishes that value only when the log proves it
is the final energy of the requested External calculation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.programs.gaussian import GaussianProgramAdapter
from confflow.programs.gaussian import energy_semantics as semantics
from confflow.programs.gaussian import parsing as gaussian
from tests.v4._helpers.v42_adapters import gaussian_inputs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SP_FIXTURE = FIXTURES / "gaussian_external_uma_sp.log"
OPT_FIXTURE = FIXTURES / "gaussian_external_uma_opt.log"
MINIMAL_FIXTURE = FIXTURES / "gaussian_parse_output_minimal.log"

SP_ENERGY = -76.4335927551
OPT_RECOVERED_LINES = [
    line
    for line in OPT_FIXTURE.read_text(encoding="utf-8").splitlines()
    if "Recovered energy=" in line
]


def _opt_expected_energy() -> float:
    last = OPT_RECOVERED_LINES[-1]
    return float(last.split("Recovered energy=")[1].split()[0])


def _parse_log(tmp_path: Path, text: str, name: str = "s_opt_item0.log"):
    adapter = GaussianProgramAdapter()
    materialized = adapter.materialize_native_input(gaussian_inputs())
    (tmp_path / materialized.main_input_name).write_text("fake input", encoding="utf-8")
    (tmp_path / name).write_text(text, encoding="utf-8")
    return adapter.parse_native_result(
        work_dir=str(tmp_path), log_file_name=name, materialized=materialized
    )


def _fixture_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _insert_after(text: str, anchor: str, line: str) -> str:
    out: list[str] = []
    for existing in text.splitlines():
        out.append(existing)
        if anchor in existing:
            out.append(line)
    return "\n".join(out) + "\n"


def _delete_lines(text: str, needle: str) -> str:
    return "\n".join(line for line in text.splitlines() if needle not in line) + "\n"


def _append_after_last(text: str, needle: str, line: str) -> str:
    lines = text.splitlines()
    index = max(i for i, existing in enumerate(lines) if needle in existing)
    lines.insert(index + 1, line)
    return "\n".join(lines) + "\n"


# (a) parser and proof on both real fixtures


def test_sp_fixture_publishes_full_precision_recovered_energy():
    text = _fixture_text(SP_FIXTURE)
    energies, sources = gaussian.parse_energies(text)
    assert energies["electronic"] == pytest.approx(SP_ENERGY, abs=0.0)
    assert sources["electronic"] == "external_recovered"
    assert semantics.final_energy_semantics_problem(text, sources) is None


def test_opt_fixture_publishes_last_recovered_energy():
    text = _fixture_text(OPT_FIXTURE)
    expected = _opt_expected_energy()
    energies, sources = gaussian.parse_energies(text)
    assert energies["electronic"] == pytest.approx(expected, abs=0.0)
    assert sources["electronic"] == "external_recovered"
    assert semantics.final_energy_semantics_problem(text, sources) is None


# (b) end to end through the adapter


@pytest.mark.parametrize(
    ("fixture", "log_name"),
    [(SP_FIXTURE, "s_opt_item0.log"), (OPT_FIXTURE, "s_opt_item0.log")],
)
def test_external_fixture_parses_end_to_end(tmp_path, fixture, log_name):
    result = _parse_log(tmp_path, _fixture_text(fixture), log_name)
    assert not [d for d in result.parser_diagnostics if d.severity.name == "ERROR"]
    assert result.native_metadata["energy_semantics"] == "verified"
    assert result.native_metadata["electronic_source"] == "external_recovered"
    expected = SP_ENERGY if fixture == SP_FIXTURE else _opt_expected_energy()
    assert result.energies_hartree["electronic"] == pytest.approx(expected, abs=0.0)


def test_external_optimization_produces_geometry_and_converges(tmp_path):
    result = _parse_log(tmp_path, _fixture_text(OPT_FIXTURE))
    assert result.geometry_output.name == "PRODUCED"
    assert result.native_metadata["optimization_converged"] is True
    assert result.terminated_normally


# (c) negatives on modified fixture text


def test_external_energy_inside_oniom_is_refused(tmp_path):
    text = _insert_after(
        _fixture_text(OPT_FIXTURE),
        "External calculation of energy",
        " ONIOM: extrapolated energy = -76.0",
    )
    result = _parse_log(tmp_path, text)
    assert result.native_metadata["energy_semantics"] == "external_energy_in_oniom"
    assert any(d.code == "native_energy_semantics_error" for d in result.parser_diagnostics)


def test_external_energy_without_external_marker_is_refused(tmp_path):
    text = _delete_lines(_fixture_text(SP_FIXTURE), "External calculation of energy")
    result = _parse_log(tmp_path, text)
    assert (
        result.native_metadata["energy_semantics"] == "external_energy_without_external_calculation"
    )
    assert any(d.code == "native_energy_semantics_error" for d in result.parser_diagnostics)


def test_method_energy_after_last_recovered_line_is_refused(tmp_path):
    text = _append_after_last(_fixture_text(OPT_FIXTURE), "Recovered energy=", " MP2= -76.1")
    result = _parse_log(tmp_path, text)
    assert result.native_metadata["energy_semantics"].startswith(
        "post_external_final_energy_marker:"
    )
    assert any(d.code == "native_energy_semantics_error" for d in result.parser_diagnostics)


# (d) existing SCF Done fixture is unchanged


def test_scf_done_fixture_keeps_scf_source_and_energy():
    text = _fixture_text(MINIMAL_FIXTURE)
    scf_values = []
    for raw in text.splitlines():
        if "SCF Done" in raw:
            scf_values.append(float(raw.replace("D", "E").split()[4]))
    expected = scf_values[-1]
    energies, sources = gaussian.parse_energies(text)
    assert sources["electronic"] == "scf_done"
    assert energies["electronic"] == pytest.approx(expected, abs=0.0)
    assert semantics.final_energy_semantics_problem(text, sources) is None
