#!/usr/bin/env python3

"""V4-2 branch coverage: contract validation and pure-function edges.

Exercises constructor guards and pure helpers across the new execution
contracts and standard implementations.  All tests are in-memory and fast;
no subprocess is launched here.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.domain import (
    ArtifactSet,
    Diagnostic,
    DiagnosticSeverity,
    FrozenDict,
    Provenance,
    ResultSet,
    StructureSet,
    Unit,
)
from confflow.domain.resources import ResourceRequest
from confflow.execution.checks import CHECK_DEFAULTS, CheckContext, CheckOutcome
from confflow.execution.checks_standard import (
    CHECKS,
    bond_length_angstrom,
    kabsch_aligned_rmsd,
    parse_bond_atoms,
)
from confflow.execution.native import (
    CancelOutcome,
    GeometryOutput,
    InputFile,
    MaterializedNativeInput,
    NativeError,
    NativeErrorCode,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeHandle,
    NativeResult,
    NativeStatus,
    ParsedGeometry,
    ProducedFile,
    ProgramName,
    ResolvedCalculationInputs,
    StagedArtifact,
)
from confflow.execution.profiles import (
    GeometrySemantics,
    ProfileContext,
    ProfileOutput,
    passthrough_structure_id,
    produced_structure_id,
)
from confflow.execution.recovery import (
    RecoveryContext,
    RecoveryDecision,
    RecoveryExecution,
)
from confflow.execution.recovery_standard import (
    RECOVERIES,
    ensure_gaussian_modredundant_keyword,
    keyword_requests_freq,
    make_scan_keyword_from_ts_keyword,
    parse_ts_bond_atoms,
    scan_keyword_for_rescue,
)
from tests.v4._builders import structure


def _resources() -> ResourceRequest:
    """Return resolved test resources."""
    return ResourceRequest(cores_per_item=2, memory_per_item_bytes=2 * 1024**3)


def _resolved(**overrides: Any) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs with overrides."""
    fields: dict[str, Any] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": _resources(),
    }
    fields.update(overrides)
    return ResolvedCalculationInputs(**fields)


def _geometry() -> ParsedGeometry:
    """Return a parsed water geometry."""
    record = structure("s0")
    return ParsedGeometry(atoms=tuple(record.atoms), coordinates=tuple(record.coordinates))


def _native_result(**overrides: Any) -> NativeResult:
    """Build a terminated native result with produced geometry."""
    fields: dict[str, Any] = {
        "program": ProgramName.GAUSSIAN,
        "terminated_normally": True,
        "geometry_output": GeometryOutput.PRODUCED,
        "final_geometry": _geometry(),
        "log_file_name": "job.log",
    }
    fields.update(overrides)
    return NativeResult(**fields)


# ---------------------------------------------------------------------------
# Native contract validation
# ---------------------------------------------------------------------------


class TestParsedGeometry:
    """ParsedGeometry freezes its containers."""

    def test_sequences_become_tuples(self) -> None:
        parsed = ParsedGeometry(atoms=["O", "H"], coordinates=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        assert parsed.atoms == ("O", "H")
        assert parsed.coordinates == ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))


class TestNativeResultValidation:
    """NativeResult rejects non-facts at construction."""

    @pytest.mark.parametrize(
        ("field", "value", "error"),
        [
            pytest.param("program", "gaussian", TypeError, id="program-not-enum"),
            pytest.param("terminated_normally", 1, TypeError, id="terminated-not-bool"),
            pytest.param("geometry_output", "produced", TypeError, id="geometry-not-enum"),
            pytest.param("final_geometry", "coords", TypeError, id="geometry-wrong-type"),
        ],
    )
    def test_wrong_types_rejected(self, field: str, value: Any, error: type) -> None:
        with pytest.raises(error):
            _native_result(**{field: value})

    def test_produced_without_geometry_rejected(self) -> None:
        with pytest.raises(ValueError, match="PRODUCED"):
            _native_result(final_geometry=None)

    def test_none_geometry_with_none_output_accepted(self) -> None:
        result = _native_result(geometry_output=GeometryOutput.NONE, final_geometry=None)
        assert result.geometry_output is GeometryOutput.NONE

    def test_plain_mappings_become_frozen(self) -> None:
        result = _native_result(energies_hartree={"electronic": -76.4}, native_metadata={"a": 1})
        assert isinstance(result.energies_hartree, FrozenDict)
        assert isinstance(result.native_metadata, FrozenDict)

    def test_sequences_become_tuples(self) -> None:
        result = _native_result(
            frequencies_cm=[100.0],
            produced_files=[ProducedFile(name="a.log", role="native_output")],
            parser_diagnostics=[
                Diagnostic(code="x", message="y", severity=DiagnosticSeverity.INFO)
            ],
        )
        assert result.frequencies_cm == (100.0,)
        assert len(result.produced_files) == 1
        assert len(result.parser_diagnostics) == 1

    def test_energy_property(self) -> None:
        assert _native_result(energies_hartree={"electronic": -76.4}).energy == -76.4
        assert _native_result().energy is None


class TestMaterializedNativeInput:
    """Materialized input validates program and main file."""

    def test_bad_program_rejected(self) -> None:
        with pytest.raises(TypeError):
            MaterializedNativeInput(program="gaussian", main_input_name="a.gjf")

    @pytest.mark.parametrize("name", ["", None, 123])
    def test_bad_main_input_rejected(self, name: Any) -> None:
        with pytest.raises(ValueError):
            MaterializedNativeInput(program=ProgramName.GAUSSIAN, main_input_name=name)

    def test_files_and_metadata_frozen(self) -> None:
        materialized = MaterializedNativeInput(
            program=ProgramName.ORCA,
            main_input_name="a.inp",
            files=[InputFile(name="a.inp", content="x")],
            metadata={"job": "a"},
        )
        assert materialized.files == (InputFile(name="a.inp", content="x"),)
        assert isinstance(materialized.metadata, FrozenDict)


class TestNativeExecutionRequest:
    """Launch requests validate the process boundary fields."""

    def _request(self, **overrides: Any) -> NativeExecutionRequest:
        fields: dict[str, Any] = {
            "executable": "/bin/true",
            "argv": ("/bin/true",),
            "work_dir": "/tmp",
        }
        fields.update(overrides)
        return NativeExecutionRequest(**fields)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            pytest.param("executable", "", id="empty-executable"),
            pytest.param("executable", None, id="none-executable"),
            pytest.param("argv", (), id="empty-argv"),
            pytest.param("work_dir", "", id="empty-work-dir"),
            pytest.param("walltime_seconds", 0, id="zero-walltime"),
            pytest.param("walltime_seconds", -1.0, id="negative-walltime"),
            pytest.param("walltime_seconds", "60", id="string-walltime"),
        ],
    )
    def test_invalid_fields_rejected(self, field: str, value: Any) -> None:
        with pytest.raises(ValueError):
            self._request(**{field: value})

    def test_containers_frozen(self) -> None:
        request = self._request(env={"A": "b"}, metadata={"k": 1})
        assert isinstance(request.env, FrozenDict)
        assert isinstance(request.metadata, FrozenDict)

    def test_exit_and_timeout_combinations(self) -> None:
        assert NativeExecutionResult(exit_code=0, wall_time_seconds=1.0).exited_cleanly
        assert not NativeExecutionResult(exit_code=1, wall_time_seconds=1.0).exited_cleanly
        assert not NativeExecutionResult(
            exit_code=0, wall_time_seconds=1.0, timed_out=True
        ).exited_cleanly
        assert not NativeExecutionResult(exit_code=None, wall_time_seconds=1.0).exited_cleanly


class TestNativeError:
    """Native errors carry stable codes."""

    def test_bad_code_rejected(self) -> None:
        with pytest.raises(TypeError):
            NativeError(code="nope", message="x")

    @pytest.mark.parametrize("message", ["", None])
    def test_bad_message_rejected(self, message: Any) -> None:
        with pytest.raises(ValueError):
            NativeError(code=NativeErrorCode.NATIVE_INPUT_ERROR, message=message)

    def test_bad_program_rejected(self) -> None:
        with pytest.raises(TypeError):
            NativeError(code=NativeErrorCode.NATIVE_INPUT_ERROR, message="x", program="g16")

    def test_details_frozen(self) -> None:
        error = NativeError(
            code=NativeErrorCode.NATIVE_INPUT_ERROR,
            message="x",
            program=ProgramName.ORCA,
            details={"a": 1},
        )
        assert isinstance(error.details, FrozenDict)
        assert error.program is ProgramName.ORCA


class TestResolvedCalculationInputs:
    """Resolved inputs validate structure and resources."""

    def test_bad_structure_rejected(self) -> None:
        with pytest.raises(TypeError):
            _resolved(structure="s0")

    def test_bad_resources_rejected(self) -> None:
        with pytest.raises(TypeError):
            _resolved(resources={"cores_per_item": 1})

    def test_containers_frozen_and_freeze_tupled(self) -> None:
        resolved = _resolved(
            native={"keyword": "x"},
            freeze=[2, 1],
            checkpoints=[StagedArtifact(local_name="a", role="checkpoint")],
        )
        assert isinstance(resolved.native, FrozenDict)
        assert isinstance(resolved.extra_structures, FrozenDict)
        assert resolved.freeze == (2, 1)
        assert len(resolved.checkpoints) == 1


class TestNativeValueObjects:
    """Handles, statuses, and outcomes are plain values."""

    def test_handle_defaults(self) -> None:
        handle = NativeHandle(key="k")
        assert handle.pid is None
        assert handle.submitted_at == 0.0

    def test_status_exit_code(self) -> None:
        assert NativeStatus(is_terminal=True, exit_code=0).exit_code == 0
        assert NativeStatus(is_terminal=False).exit_code is None

    def test_cancel_outcome(self) -> None:
        assert CancelOutcome(confirmed=True).detail == ""
        assert CancelOutcome(confirmed=False, detail="live").detail == "live"

    def test_staged_artifact(self) -> None:
        staged = StagedArtifact(local_name="a.chk", role="checkpoint")
        assert staged.subject_structure_id is None
        assert staged.checksum is None


# ---------------------------------------------------------------------------
# Profile / check / recovery contract validation
# ---------------------------------------------------------------------------


def _profile_output(**overrides: Any) -> ProfileOutput:
    """Build an empty profile output with overrides."""
    return ProfileOutput(**overrides)


class TestProfileContracts:
    """Profile contexts and outputs validate their members."""

    def _context(self, **overrides: Any) -> ProfileContext:
        fields: dict[str, Any] = {
            "work_item_id": "wi:s:a",
            "step_id": "s",
            "logical_key": "s:a",
            "profile_name": "standard",
            "profile_version": "v1",
            "native_result": _native_result(),
            "inputs": _resolved(),
        }
        fields.update(overrides)
        return ProfileContext(**fields)

    def test_bad_native_result_rejected(self) -> None:
        with pytest.raises(TypeError):
            self._context(native_result={})

    def test_bad_inputs_rejected(self) -> None:
        with pytest.raises(TypeError):
            self._context(inputs={})

    def test_bad_discovered_rejected(self) -> None:
        with pytest.raises(TypeError):
            self._context(discovered_artifacts=[])

    def test_output_bad_members_rejected(self) -> None:
        with pytest.raises(TypeError):
            _profile_output(structures=[])
        with pytest.raises(TypeError):
            _profile_output(results=[])
        with pytest.raises(TypeError):
            _profile_output(artifacts=[])
        with pytest.raises(TypeError):
            _profile_output(geometry_semantics="produced")

    def test_output_diagnostics_tupled(self) -> None:
        output = _profile_output(
            diagnostics=[Diagnostic(code="x", message="y", severity=DiagnosticSeverity.INFO)]
        )
        assert len(output.diagnostics) == 1

    def test_structure_id_helpers(self) -> None:
        assert passthrough_structure_id("s:a") == "s:a:structure:passthrough"
        assert produced_structure_id("s:a") == "s:a:structure:0"
        assert produced_structure_id("s:a", 2) == "s:a:structure:2"

    def test_geometry_semantics_values(self) -> None:
        assert GeometrySemantics.PRODUCED.value == "produced"
        assert GeometrySemantics.PASSTHROUGH.value == "passthrough"


def _check_context(**overrides: Any) -> CheckContext:
    """Build a check context over an empty profile output."""
    fields: dict[str, Any] = {
        "check_name": "geometry_required",
        "work_item_id": "wi:s:a",
        "step_id": "s",
        "logical_key": "s:a",
        "profile_output": _profile_output(),
        "inputs": _resolved(),
    }
    fields.update(overrides)
    return CheckContext(**fields)


class TestCheckContracts:
    """Check contexts validate members and expose params."""

    def test_bad_profile_output_rejected(self) -> None:
        with pytest.raises(TypeError):
            _check_context(profile_output={})

    def test_bad_inputs_rejected(self) -> None:
        with pytest.raises(TypeError):
            _check_context(inputs={})

    def test_params_frozen_and_accessible(self) -> None:
        context = _check_context(params={"expected": 1})
        assert isinstance(context.params, FrozenDict)
        assert context.param("expected") == 1
        assert context.param("missing", "dflt") == "dflt"
        assert context.param("missing") is None

    def test_outcome_requires_diagnostic_on_failure(self) -> None:
        with pytest.raises(ValueError):
            CheckOutcome(passed=False)
        with pytest.raises(TypeError):
            CheckOutcome(passed="yes")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            CheckOutcome(passed=False, diagnostic="bad")  # type: ignore[arg-type]
        ok = CheckOutcome(passed=True)
        assert ok.diagnostic is None

    def test_check_defaults_single_source(self) -> None:
        assert CHECK_DEFAULTS["imaginary_frequency_count"] == {"expected": 0}
        assert CHECK_DEFAULTS["max_rmsd_from_input"] == {"threshold_angstrom": 1.0}
        assert CHECK_DEFAULTS["bond_drift"] == {"threshold_angstrom": 0.4}
        assert set(CHECKS) == set(CHECK_DEFAULTS)


class TestRecoveryContracts:
    """Recovery contexts, decisions, and executions validate members."""

    def _context(self, **overrides: Any) -> RecoveryContext:
        fields: dict[str, Any] = {
            "profile_name": "ts_rescue_scan",
            "work_item_id": "wi:s:a",
            "step_id": "s",
            "logical_key": "s:a",
            "inputs": _resolved(),
        }
        fields.update(overrides)
        return RecoveryContext(**fields)

    def test_bad_inputs_rejected(self) -> None:
        with pytest.raises(TypeError):
            self._context(inputs={})

    def test_bad_native_result_rejected(self) -> None:
        with pytest.raises(TypeError):
            self._context(failed_native_result={})

    def test_diagnostics_tupled_and_params_frozen(self) -> None:
        context = self._context(
            failed_native_result=_native_result(),
            failure_diagnostics=[Diagnostic(code="x", message="y")],
            params={"bond_atoms": [1, 2]},
        )
        assert len(context.failure_diagnostics) == 1
        assert isinstance(context.params, FrozenDict)
        assert context.attempt == 0
        assert context.cancellation_confirmed is True

    def test_decision_validation(self) -> None:
        with pytest.raises(TypeError):
            RecoveryDecision(attempt="yes", reason="x")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            RecoveryDecision(attempt=True, reason="")
        decision = RecoveryDecision(attempt=True, reason="ok", details={"a": 1})
        assert isinstance(decision.details, FrozenDict)

    def test_execution_validation(self) -> None:
        materialized = MaterializedNativeInput(
            program=ProgramName.GAUSSIAN, main_input_name="a.gjf"
        )
        execution_result = NativeExecutionResult(exit_code=0, wall_time_seconds=1.0)
        with pytest.raises(TypeError):
            RecoveryExecution(
                native_result={},  # type: ignore[arg-type]
                execution_result=execution_result,
                materialized=materialized,
            )
        with pytest.raises(TypeError):
            RecoveryExecution(
                native_result=_native_result(),
                execution_result={},  # type: ignore[arg-type]
                materialized=materialized,
            )
        with pytest.raises(TypeError):
            RecoveryExecution(
                native_result=_native_result(),
                execution_result=execution_result,
                materialized={},  # type: ignore[arg-type]
            )
        execution = RecoveryExecution(
            native_result=_native_result(),
            execution_result=execution_result,
            materialized=materialized,
            diagnostics=[Diagnostic(code="x", message="y", severity=DiagnosticSeverity.INFO)],
            metadata={"rescued": True},
        )
        assert len(execution.diagnostics) == 1
        assert isinstance(execution.metadata, FrozenDict)

    def test_recovery_registry_names(self) -> None:
        assert set(RECOVERIES) == {"none", "ts_rescue_scan"}
        assert RECOVERIES["none"].name == "none"
        assert RECOVERIES["ts_rescue_scan"].name == "ts_rescue_scan"


# ---------------------------------------------------------------------------
# Standard check helpers
# ---------------------------------------------------------------------------


class TestKabschAndBonds:
    """Numeric helpers fail closed on unusable input."""

    def test_kabsch_rejects_mismatched_shapes(self) -> None:
        import numpy as np

        assert kabsch_aligned_rmsd(np.zeros((2, 3)), np.zeros((3, 3))) is None
        assert kabsch_aligned_rmsd(np.zeros((0, 3)), np.zeros((0, 3))) is None
        bad = np.zeros((2, 3))
        bad[0, 0] = float("inf")
        assert kabsch_aligned_rmsd(bad, np.zeros((2, 3))) is None

    def test_kabsch_identical_is_zero(self) -> None:
        import numpy as np

        array = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        assert kabsch_aligned_rmsd(array, array.copy()) == pytest.approx(0.0)

    def test_bond_length_none_cases(self) -> None:
        record = structure("s0")
        assert bond_length_angstrom(record, 1, 99) is None
        assert bond_length_angstrom(record, 0, 1) is None
        assert bond_length_angstrom(record, 1, 1) is None

    def test_bond_length_water(self) -> None:
        assert bond_length_angstrom(structure("s0"), 1, 2) == pytest.approx(0.957, abs=0.01)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param([1, 2], (1, 2), id="list"),
            pytest.param((2, 1), (2, 1), id="tuple"),
            pytest.param("1,2", (1, 2), id="csv"),
            pytest.param("atoms 1 and 3", (1, 3), id="sentence"),
            pytest.param([1], None, id="single"),
            pytest.param([0, 2], None, id="zero"),
            pytest.param([2, 2], None, id="equal"),
            pytest.param(None, None, id="none"),
            pytest.param("no digits", None, id="no-digits"),
            pytest.param([1, "x", 2], (1, 2), id="skip-bad"),
        ],
    )
    def test_parse_bond_atoms(self, value: Any, expected: Any) -> None:
        assert parse_bond_atoms(value) == expected


class TestRecoveryPureHelpers:
    """Keyword and bond helpers behave like the legacy originals."""

    @pytest.mark.parametrize(
        ("keyword", "expected"),
        [
            pytest.param("opt freq", True, id="opt-freq"),
            pytest.param("OPT(ts,calcfc) FREQ", True, id="upper"),
            pytest.param("opt", False, id="opt-only"),
            pytest.param("", False, id="empty"),
            pytest.param("frequency", False, id="substring"),
        ],
    )
    def test_keyword_requests_freq(self, keyword: str, expected: bool) -> None:
        assert keyword_requests_freq(keyword) is expected

    def test_scan_keyword_derivation(self) -> None:
        assert make_scan_keyword_from_ts_keyword("") == ""
        scan = make_scan_keyword_from_ts_keyword("opt=(ts,calcfc) freq")
        assert "freq" not in scan.lower()
        assert "ts" not in scan.lower().replace("opt", " ")
        rescued = scan_keyword_for_rescue("opt=(ts,calcfc) freq")
        assert "modredundant" in rescued.lower()
        assert scan_keyword_for_rescue("") == ""

    def test_ensure_modredundant(self) -> None:
        assert "modredundant" in ensure_gaussian_modredundant_keyword("opt").lower()
        keyword = ensure_gaussian_modredundant_keyword("opt=modredundant")
        assert keyword.lower().count("modredundant") == 1

    def test_scan_keyword_for_rescue(self) -> None:
        assert "opt" in scan_keyword_for_rescue("opt=(ts,calcfc)").lower()

    def test_parse_ts_bond_atoms_alias(self) -> None:
        assert parse_ts_bond_atoms([1, 2]) == (1, 2)
        assert parse_ts_bond_atoms(None) is None


# ---------------------------------------------------------------------------
# Standard profile results
# ---------------------------------------------------------------------------


class TestStandardProfileResults:
    """Result kinds, units, subjects, and provenance are explicit."""

    def _output(self, **overrides: Any):
        from confflow.execution.profile_standard import PROFILES

        profile = PROFILES["standard"]
        context = ProfileContext(
            work_item_id="wi:s:a",
            step_id="s",
            logical_key="s:a",
            profile_name="standard",
            profile_version=profile.contract_version,
            native_result=_native_result(**overrides.get("native", {})),
            inputs=_resolved(),
        )
        return profile.apply(context)

    def test_energy_result_shape(self) -> None:
        output = self._output(native={"energies_hartree": {"electronic": -76.4}})
        energies = [item for item in output.results if item.kind == "energy"]
        assert len(energies) == 1
        assert energies[0].value == -76.4
        assert energies[0].unit is Unit.HARTREE
        assert energies[0].subject_structure_id == "s0"
        assert energies[0].source_step_id == "s"
        assert energies[0].source_work_item_id == "wi:s:a"
        assert isinstance(energies[0].provenance, Provenance)

    def test_gibbs_preferred_and_correction_derived(self) -> None:
        output = self._output(native={"energies_hartree": {"electronic": -76.4, "gibbs": -76.3}})
        kinds = {item.kind: item.value for item in output.results}
        assert kinds["gibbs_energy"] == pytest.approx(-76.3)
        assert kinds["gibbs_correction"] == pytest.approx(0.1)
        assert kinds["energy"] == pytest.approx(-76.3)

    def test_explicit_correction_wins(self) -> None:
        output = self._output(
            native={
                "energies_hartree": {
                    "electronic": -76.4,
                    "gibbs": -76.3,
                    "gibbs_correction": 0.05,
                }
            }
        )
        kinds = {item.kind: item.value for item in output.results}
        assert kinds["gibbs_correction"] == pytest.approx(0.05)

    def test_frequencies_and_counts(self) -> None:
        output = self._output(native={"frequencies_cm": (100.0, -50.0, 200.0)})
        kinds = {item.kind: item for item in output.results}
        assert list(kinds["frequencies"].value) == [100.0, -50.0, 200.0]
        assert kinds["frequencies"].unit is Unit.CM_INVERSE
        assert kinds["num_imaginary_frequencies"].value == 1
        assert kinds["lowest_frequency"].value == pytest.approx(-50.0)

    def test_artifacts_flow_through(self) -> None:
        from confflow.domain import ArtifactLocator, ArtifactRef

        discovered = ArtifactSet.of(
            ArtifactRef(
                id="a1",
                role="native_output",
                locator=ArtifactLocator.run_relative("items/s/a.log"),
                checksum="sha256:" + "a" * 64,
            )
        )
        from confflow.execution.profile_standard import PROFILES

        profile = PROFILES["standard"]
        context = ProfileContext(
            work_item_id="wi:s:a",
            step_id="s",
            logical_key="s:a",
            profile_name="standard",
            profile_version=profile.contract_version,
            native_result=_native_result(),
            inputs=_resolved(),
            discovered_artifacts=discovered,
        )
        output = profile.apply(context)
        assert output.artifacts.ids == ("a1",)

    def test_empty_sets(self) -> None:
        assert StructureSet().is_empty
        assert ResultSet().is_empty
