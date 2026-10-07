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
from tests.v4._builders import structure_set
from tests.v4._helpers.v42_adapters import orca_inputs
from tests.v4._helpers.v42_executors import (
    FAKE_ORCA,
    ORCA_NATIVE,
    assemble_items,
    calculation_doc,
    compile_plan,
    item_context,
)


@pytest.mark.parametrize("token", ["-1.2345E+02", "-1.2345D+02", "-123.45"])
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
        # Representative for the whole refused family (same "ConfFlow cannot
        # extract" reason; other spellings removed in T1 b2, see REPORT).
        "CCSD(T)",
    ],
)
def test_gaussian_unsupported_method_families_are_refused(method):
    from confflow.programs.gaussian import GaussianProgramAdapter
    from tests.v4._helpers.v42_adapters import gaussian_inputs

    adapter = GaussianProgramAdapter()
    native = FrozenDict({"keyword": f"{method}/6-31G* SP"})
    errors = adapter.validate_native_definition(native)
    assert any("ConfFlow cannot extract" in message for message in errors)
    with pytest.raises(ValueError, match="ConfFlow cannot extract"):
        adapter.materialize_native_input(gaussian_inputs(native=native))


@pytest.mark.parametrize(
    "keyword",
    [
        "HF/6-31G*",
        "UHF/6-31G*",
        "ROHF/6-31G*",
        "B3LYP/6-31G* Opt Freq",
        "M062X/def2TZVP SP",
        "B3LYP D3BJ def2-SVP Opt",
        "IRC B3LYP D3BJ",
        "QST2 B3LYP",
        "wB97XD/def2-TZVP SP",
        "PBE1PBE/6-31G* SP",
        "TPSSh/def2-SVP SP",
        "SCAN/def2-TZVP SP",
    ],
)
def test_gaussian_scf_methods_remain_renderable(keyword):
    from confflow.programs.gaussian import GaussianProgramAdapter

    assert GaussianProgramAdapter().validate_native_definition({"keyword": keyword}) == ()


def _gaussian_log(*tail_lines: str, scf_line: str | None = None) -> str:
    parts = [
        " Entering Gaussian System, Link 0=g16",
        scf_line or " SCF Done:  E(RB3LYP) =  -76.5000000000     A.U. after    5 cycles",
        *tail_lines,
        " Normal termination of Gaussian 16",
    ]
    return "\n".join(parts) + "\n"


def _parse_synthetic_gaussian_log(tmp_path, text: str):
    from confflow.programs.gaussian import GaussianProgramAdapter
    from tests.v4._helpers.v42_adapters import gaussian_inputs

    adapter = GaussianProgramAdapter()
    materialized = adapter.materialize_native_input(gaussian_inputs())
    log = tmp_path / "s_opt_item0.log"
    log.write_text(text, encoding="utf-8")
    return adapter.parse_native_result(
        work_dir=str(tmp_path), log_file_name=log.name, materialized=materialized
    )


def test_gaussian_runtime_energy_semantics_accepts_plain_scf(tmp_path):
    result = _parse_synthetic_gaussian_log(tmp_path, _gaussian_log())
    assert result.parser_diagnostics == ()
    assert result.native_metadata["energy_semantics"] == "verified"
    assert result.energy == pytest.approx(-76.5)


@pytest.mark.parametrize(
    ("tail", "reason"),
    [
        (
            " E2(B2PLYPD) =    -0.1218848356D-01 E(B2PLYPD) =    -0.75211960915171D+02",
            "post_scf_final_energy_marker:double_hybrid_e2",
        ),
        (
            " G4MP2(0 K)=               -76.355851 G4MP2 Energy=                -76.353015",
            "post_scf_final_energy_marker:composite_summary",
        ),
        (
            " E2 =    -0.3582368263D-01 EUMP2 =    -0.74999454504245D+02",
            "post_scf_final_energy_marker:second_order_energy",
        ),
        (
            " CCSD(T)= -0.75013635474D+02",
            "post_scf_final_energy_marker:post_scf_cc",
        ),
        (
            " E(CI)=   -75.0129607",
            "post_scf_final_energy_marker:method_energy_assignment",
        ),
    ],
)
def test_gaussian_runtime_energy_semantics_rejects_foreign_final_energy(tmp_path, tail, reason):
    result = _parse_synthetic_gaussian_log(tmp_path, _gaussian_log(tail))
    assert len(result.parser_diagnostics) == 1
    diagnostic = result.parser_diagnostics[0]
    assert diagnostic.code == "native_energy_semantics_error"
    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert diagnostic.details["reason"] == reason


def test_gaussian_archive_only_energy_is_not_publishable(tmp_path):
    text = (
        " Entering Gaussian System, Link 0=g16\n"
        " 1\\1\\GINC-QC\\HF=-76.123456\\@\n"
        " Normal termination of Gaussian 16\n"
    )
    result = _parse_synthetic_gaussian_log(tmp_path, text)
    assert result.parser_diagnostics[0].details["reason"] == (
        "electronic_energy_source_not_scf_final"
    )
    # Parser facts stay available for diagnostics; the ERROR diagnostic is
    # what prevents the executor from publishing them.
    assert result.energy == pytest.approx(-76.123456)


def test_gaussian_foreign_final_energy_is_not_published(tmp_path):
    """The executor refuses the item even with no declared checks."""
    from tests.v4._helpers.v42_executors import FAKE_G16, GAUSSIAN_NATIVE

    plan = compile_plan(calculation_doc("gaussian", GAUSSIAN_NATIVE, str(FAKE_G16), checks=[]))
    item = assemble_items(plan, structure_set("water"))[0]
    context = item_context(
        plan,
        "gaussian",
        str(FAKE_G16),
        str(tmp_path / "run"),
        str(tmp_path / "run" / "work"),
        NativeProcessSupervisor(),
        [],
    )
    original = context.adapter.parse_native_result

    def parse_with_foreign_energy(**kwargs):
        result = original(**kwargs)
        diagnostic = Diagnostic(
            code="native_energy_semantics_error",
            message=(
                "Gaussian final-energy semantics could not be verified "
                "(post_scf_final_energy_marker:double_hybrid_e2); refusing to "
                "publish the SCF value as the requested method's energy"
            ),
            severity=DiagnosticSeverity.ERROR,
            details=FrozenDict({"reason": "post_scf_final_energy_marker:double_hybrid_e2"}),
        )
        return replace(result, parser_diagnostics=(diagnostic,))

    context.adapter.parse_native_result = parse_with_foreign_energy
    try:
        result = WorkItemExecutor().execute(item, context)
    finally:
        context.adapter.parse_native_result = original
    assert not result.is_completed
    assert result.results.is_empty
    assert result.error.details["reason"] == ("post_scf_final_energy_marker:double_hybrid_e2")
    assert any(
        diagnostic.code == "native_energy_semantics_error" for diagnostic in result.diagnostics
    )


# --------------------------------------------------------------------------
# ORCA parser-version identity: reuse, provenance, and remote handoff.
# --------------------------------------------------------------------------


def _provenance(adapter_version, parser_version):
    from confflow.persistence.reuse import build_producer_provenance

    return build_producer_provenance(
        adapter_version=adapter_version,
        profile_version="confflow.contract.result_profile.standard.v1",
        check_versions={"normal_termination": "confflow.contract.check.normal_termination.v1"},
        recovery_version="confflow.contract.recovery.none.v1",
        parser_version=parser_version,
    )


def test_orca_parser_version_is_part_of_producer_provenance():
    adapter = OrcaProgramAdapter()
    assert adapter.adapter_version == "confflow.program.orca.v2"
    assert adapter.parser_version == "confflow.program.orca.parser.v2"
    provenance = _provenance(adapter.adapter_version, adapter.parser_version)
    assert provenance["parser_version"] == "confflow.program.orca.parser.v2"
    # Pure executors (no native parser) keep the previous shape.
    from confflow.persistence.reuse import build_producer_provenance

    pure = build_producer_provenance(
        adapter_version="confflow.confgen",
        profile_version="confflow.contract.executor.confgen.v2",
        check_versions={},
        recovery_version="none",
    )
    assert "parser_version" not in pure


@pytest.mark.parametrize(
    ("stored_adapter", "stored_parser"),
    [
        ("confflow.program.orca.v1", "confflow.program.orca.parser.v1"),
        ("confflow.program.orca.v2", "confflow.program.orca.parser.v1"),
    ],
)
def test_orca_stored_results_with_older_parser_identity_are_not_reused(
    stored_adapter, stored_parser
):
    from confflow.persistence.reuse import ReuseInputs, evaluate_reuse

    adapter = OrcaProgramAdapter()
    current_provenance = _provenance(adapter.adapter_version, adapter.parser_version)
    stored_provenance = _provenance(stored_adapter, stored_parser)
    digest = "sha256:" + "a" * 64
    current = ReuseInputs(
        work_item_digest=digest,
        step_semantic_digest=digest,
        environment_digest=digest,
        producer_provenance=current_provenance,
    )
    stored = ReuseInputs(
        work_item_digest=digest,
        step_semantic_digest=digest,
        environment_digest=digest,
        producer_provenance=stored_provenance,
    )
    decision = evaluate_reuse(current=current, stored=stored, stored_status="completed")
    assert decision.decision.value == "invalidate_provenance"


def test_orca_current_identity_still_reuses():
    from confflow.persistence.reuse import ReuseInputs, evaluate_reuse

    adapter = OrcaProgramAdapter()
    provenance = _provenance(adapter.adapter_version, adapter.parser_version)
    digest = "sha256:" + "b" * 64
    inputs = ReuseInputs(
        work_item_digest=digest,
        step_semantic_digest=digest,
        environment_digest=digest,
        producer_provenance=provenance,
    )
    decision = evaluate_reuse(current=inputs, stored=inputs, stored_status="completed")
    assert decision.decision.value == "reuse"
