"""Regression gates for trustworthy native energies and completion facts."""

from dataclasses import replace

import pytest

from confflow.domain import FrozenDict
from confflow.domain.diagnostics import Diagnostic, DiagnosticSeverity
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.programs.gaussian import parsing as gaussian
from confflow.programs.orca import OrcaProgramAdapter
from confflow.programs.orca import parsing as orca
from tests.v4.test_v42_adapters import orca_inputs
from tests.v4.test_v42_executors import (
    FAKE_ORCA,
    ORCA_NATIVE,
    assemble_items,
    calculation_doc,
    compile_plan,
    item_context,
    structure_set,
)


@pytest.mark.parametrize(
    "token", ["-1.2345E+02", "-1.2345D+02", "-1.2345e+02", "-1.2345d+02", "-123.45"]
)
def test_orca_energy_tokens_are_parsed_in_full(token):
    energies = orca.parse_energies(
        f"FINAL SINGLE POINT ENERGY {token}\n"
        f"Final Gibbs free energy ... {token} Eh\n"
        f"G-E(el) ... {token} Eh\n"
    )
    assert energies == dict.fromkeys(("electronic", "gibbs", "gibbs_correction"), -123.45)


@pytest.mark.parametrize("token", ["-12.3garbage", "-12.3E+", "nan", "inf", "1E999"])
def test_orca_malformed_energy_cannot_be_read_as_a_valid_prefix(token):
    assert orca.parse_energies(f"FINAL SINGLE POINT ENERGY {token}") == {}


@pytest.mark.parametrize(
    "parser,normal,error",
    [
        (gaussian, "Normal termination of Gaussian 16", "Error termination via Lnk1e"),
        (orca, orca.TERMINATION_MARKER, "ORCA finished by error termination in SCF"),
    ],
)
def test_later_error_overrides_earlier_normal_termination(parser, normal, error, tmp_path):
    text = f"{normal}\n{error}\n"
    assert not parser.termination_reached(text)
    assert parser.termination_reached(f"{error}\n{normal}\n")
    if parser is gaussian:
        log = tmp_path / "job.log"
        log.write_text(text)
        assert not gaussian.check_termination(str(log))


@pytest.mark.parametrize("count", [0, -1])
def test_empty_or_negative_xyz_atom_count_is_not_geometry(tmp_path, count):
    path = tmp_path / "job.xyz"
    path.write_text(f"{count}\ncomment\n")
    assert orca.parse_xyz_companion(str(path)) is None


@pytest.mark.parametrize(
    "warning,reason",
    [
        (
            "The optimization did not converge but reached the maximum number of\noptimization cycles.",
            "geometry_not_converged",
        ),
        ("SCF NOT CONVERGED", "scf_not_converged"),
        ("SCF DID NOT CONVERGE", "scf_not_converged"),
    ],
)
def test_orca_normal_exit_with_nonconvergence_has_error_fact(tmp_path, warning, reason):
    adapter = OrcaProgramAdapter()
    materialized = adapter.materialize_native_input(orca_inputs())
    log = tmp_path / "job.out"
    log.write_text(f"FINAL SINGLE POINT ENERGY -74.5\n{warning}\n{orca.TERMINATION_MARKER}\n")
    result = adapter.parse_native_result(
        work_dir=str(tmp_path), log_file_name=log.name, materialized=materialized
    )
    assert result.terminated_normally
    assert any(
        item.severity is DiagnosticSeverity.ERROR and item.details["reason"] == reason
        for item in result.parser_diagnostics
    )


def test_parser_error_prevents_completed_results_without_optional_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    executable = str(FAKE_ORCA)
    plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, executable, checks=[]))
    items = assemble_items(plan, structure_set("water"))
    context = item_context(
        plan,
        "orca",
        executable,
        str(tmp_path),
        str(tmp_path / "work"),
        NativeProcessSupervisor(),
        [],
    )
    original_parse = context.adapter.parse_native_result

    def parse_with_error(**kwargs):
        return replace(
            original_parse(**kwargs),
            parser_diagnostics=(
                Diagnostic(
                    code="scientific_check_error",
                    message="ORCA reported geometry not converged.",
                    severity=DiagnosticSeverity.ERROR,
                    details=FrozenDict({"reason": "geometry_not_converged"}),
                ),
            ),
        )

    monkeypatch.setattr(context.adapter, "parse_native_result", parse_with_error)
    result = WorkItemExecutor().execute(items[0], context)
    assert not result.is_completed
    assert result.results.is_empty
    assert any(
        item.details.get("reason") == "geometry_not_converged" for item in result.diagnostics
    )


@pytest.mark.parametrize("atoms", [("C", "H", "H"), ("O", "H")])
def test_output_atom_changes_cannot_be_published_as_input_energy(tmp_path, monkeypatch, atoms):
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    executable = str(FAKE_ORCA)
    plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, executable, checks=[]))
    item = assemble_items(plan, structure_set("water"))[0]
    context = item_context(
        plan,
        "orca",
        executable,
        str(tmp_path),
        str(tmp_path / "work"),
        NativeProcessSupervisor(),
        [],
    )
    original_parse = context.adapter.parse_native_result

    def parse_wrong_atoms(**kwargs):
        native = original_parse(**kwargs)
        return replace(
            native,
            final_geometry=replace(
                native.final_geometry,
                atoms=atoms,
                coordinates=native.final_geometry.coordinates[: len(atoms)],
            ),
        )

    monkeypatch.setattr(context.adapter, "parse_native_result", parse_wrong_atoms)
    result = WorkItemExecutor().execute(item, context)
    assert not result.is_completed
    assert result.results.is_empty
    assert result.error.details["reason"] == "geometry_atoms_mismatch"


@pytest.mark.parametrize(
    "method",
    [
        "MP2",
        "RMP2",
        "UMP2",
        "ROMP2",
        "MP4(SDQ)",
        "CCSD(T)",
        "UCCSD",
        "QCISD",
        "CASSCF(2,2)",
        "B2PLYP",
        "DSD-PBEP86",
        "CBS-QB3",
        "G4",
    ],
)
def test_gaussian_unsupported_final_energy_methods_are_refused(method):
    from confflow.programs.gaussian import GaussianProgramAdapter
    from tests.v4.test_v42_adapters import gaussian_inputs

    adapter = GaussianProgramAdapter()
    native = FrozenDict({"keyword": f"{method}/6-31G* SP"})
    errors = adapter.validate_native_definition(native)
    assert any("final-energy parsing" in message for message in errors)
    with pytest.raises(ValueError, match="final-energy parsing"):
        adapter.materialize_native_input(gaussian_inputs(native=native))


@pytest.mark.parametrize(
    "keyword", ["HF/6-31G*", "UHF/6-31G*", "B3LYP/6-31G* Opt Freq", "M062X/def2TZVP SP"]
)
def test_gaussian_scf_methods_remain_renderable(keyword):
    from confflow.programs.gaussian import GaussianProgramAdapter

    assert GaussianProgramAdapter().validate_native_definition({"keyword": keyword}) == ()
