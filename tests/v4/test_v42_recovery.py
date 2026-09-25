#!/usr/bin/env python3

"""V4-2 recovery policies: decline rules and the TS rescue scan.

Covers the ``none`` policy and the ``ts_rescue_scan`` policy from
``RECOVERIES`` using in-memory fixtures:

- ``none`` always declines evaluation and never executes,
- the ``ts_rescue_scan`` evaluate matrix (unconfirmed cancellation,
  prior attempts, non-Gaussian programs, missing bond atoms,
  non-recoverable failures, and the recoverable path),
- the execute success path through a fake ``RescueDriver`` (no real
  processes) plus the ``None`` execution path,
- recovery diagnostic and provenance shape,
- the scan keyword and bond-atom parsing helpers.

The execute success path runs the policy against a test-owned fake Gaussian
adapter so the scan engine is exercised deterministically; a companion test
pins the same path through the real :class:`GaussianProgramAdapter`.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from confflow.domain import Diagnostic, DiagnosticSeverity, FrozenDict, ResourceRequest
from confflow.execution.native import (
    GeometryOutput,
    InputFile,
    MaterializedNativeInput,
    NativeErrorCode,
    NativeExecutionRequest,
    NativeExecutionResult,
    NativeResult,
    ParsedGeometry,
    ProgramAdapter,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.recovery import RecoveryContext, RecoveryDecision
from confflow.execution.recovery_standard import (
    RECOVERIES,
    TS_RESCUE_SCAN_CONTRACT,
    TsRescueScanPolicy,
    ensure_gaussian_modredundant_keyword,
    make_scan_keyword_from_ts_keyword,
    parse_ts_bond_atoms,
)
from confflow.programs.gaussian import GaussianProgramAdapter
from confflow.programs.orca import OrcaProgramAdapter
from tests.v4._builders import structure

STEP_ID = "s_ts"
WORK_ITEM_ID = "wi:s_ts:item0"
LOGICAL_KEY = "s_ts:item0"
TS_KEYWORD = "B3LYP/6-31G* Opt(TS,CalcFC)"
SCAN_ENERGY = -76.45


def recovery_inputs(**overrides: object) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs for a TS step."""
    params: dict[str, object] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        "native": FrozenDict({"keyword": TS_KEYWORD}),
        "checkpoints": (),
        "step_id": STEP_ID,
        "work_item_id": WORK_ITEM_ID,
        "logical_key": LOGICAL_KEY,
    }
    params.update(overrides)
    return ResolvedCalculationInputs(**params)  # type: ignore[arg-type]


def failed_result(
    program: ProgramName = ProgramName.GAUSSIAN, *, terminated: bool = False
) -> NativeResult:
    """Build a failed native result for recovery evaluation."""
    return NativeResult(
        program=program,
        terminated_normally=terminated,
        geometry_output=GeometryOutput.NONE,
        final_geometry=None,
        energies_hartree=FrozenDict({}),
        frequencies_cm=(),
        native_metadata=FrozenDict({}),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="job.log",
    )


def failure(reason: str, code: str = "check_failed") -> Diagnostic:
    """Build a check-failure diagnostic carrying *reason*."""
    return Diagnostic(
        code=code,
        message=f"check failed: {reason}",
        severity=DiagnosticSeverity.ERROR,
        step_id=STEP_ID,
        work_item_id=WORK_ITEM_ID,
        logical_key=LOGICAL_KEY,
        details=FrozenDict({"check": "imaginary_frequency_count", "reason": reason}),
    )


def recovery_context(
    policy_params: dict[str, object] | None = None,
    *,
    failed: NativeResult | None = None,
    diagnostics: tuple[Diagnostic, ...] = (failure("imaginary_count_mismatch"),),
    attempt: int = 0,
    confirmed: bool = True,
    inputs: ResolvedCalculationInputs | None = None,
) -> RecoveryContext:
    """Build a recovery context with merged binding params."""
    return RecoveryContext(
        profile_name="ts_rescue_scan",
        work_item_id=WORK_ITEM_ID,
        step_id=STEP_ID,
        logical_key=LOGICAL_KEY,
        inputs=inputs if inputs is not None else recovery_inputs(),
        failed_native_result=failed if failed is not None else failed_result(ProgramName.GAUSSIAN),
        failure_diagnostics=diagnostics,
        attempt=attempt,
        params=FrozenDict(
            {
                "bond_atoms": [1, 2],
                "executable": "g16",
                "work_dir": "rescue",
                **(policy_params or {}),
            }
        ),
        cancellation_confirmed=confirmed,
    )


class FakeGaussianAdapter(ProgramAdapter):
    """Permissive test-only Gaussian adapter for the rescue scan engine."""

    @property
    def program_name(self) -> ProgramName:
        return ProgramName.GAUSSIAN

    @property
    def adapter_version(self) -> str:
        return "test.adapter.gaussian.v1"

    @property
    def parser_version(self) -> str:
        return "test.parser.gaussian.v1"

    @property
    def input_extension(self) -> str:
        return "gjf"

    @property
    def log_extension(self) -> str:
        return "log"

    @property
    def default_executable(self) -> str:
        return "g16"

    def materialize_native_input(
        self, inputs: ResolvedCalculationInputs
    ) -> MaterializedNativeInput:
        job = re.sub(r"[^A-Za-z0-9_.\-]+", "_", inputs.logical_key or "job")
        lines = [str(inputs.native.get("keyword", "B3LYP Opt")), ""]
        lines.append(f"{inputs.charge} {inputs.multiplicity}")
        for symbol, point in zip(inputs.structure.atoms, inputs.structure.coordinates):
            lines.append(f"{symbol} {point[0]:.6f} {point[1]:.6f} {point[2]:.6f}")
        content = "\n".join(lines) + "\n"
        return MaterializedNativeInput(
            program=ProgramName.GAUSSIAN,
            main_input_name=f"{job}.gjf",
            files=(InputFile(name=f"{job}.gjf", content=content),),
            metadata=FrozenDict({}),
        )

    def build_execution_request(
        self,
        materialized: MaterializedNativeInput,
        *,
        executable: str,
        work_dir: str,
        env: dict[str, str],
        walltime_seconds: float | None,
    ) -> NativeExecutionRequest:
        return NativeExecutionRequest(
            executable=executable,
            argv=(executable, materialized.main_input_name),
            work_dir=work_dir,
            env=FrozenDict(dict(env)),
            walltime_seconds=walltime_seconds,
        )

    def parse_native_result(
        self, *, work_dir: str, log_file_name: str, materialized: MaterializedNativeInput
    ) -> NativeResult:
        return NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=False,
            geometry_output=GeometryOutput.NONE,
            energies_hartree=FrozenDict({}),
            log_file_name=log_file_name,
        )

    def discover_artifacts(
        self,
        *,
        work_dir: str,
        run_relative_prefix: str,
        native_result: NativeResult,
        step_id: str,
        work_item_id: str,
        subject_structure_id: str | None,
    ) -> Any:
        from confflow.domain import ArtifactSet

        return ArtifactSet()

    def environment_probe(self, executable: str) -> dict[str, Any]:
        return {}


def gjf_coordinates(content: str) -> list[tuple[str, tuple[float, float, float]]]:
    """Parse coordinate lines back out of fake rendered input."""
    rows: list[tuple[str, tuple[float, float, float]]] = []
    lines = content.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if len(line.split()) == 2 and all(part.lstrip("-").isdigit() for part in line.split())
    )
    for line in lines[start + 1 :]:
        tokens = line.split()
        if len(tokens) != 4:
            continue
        rows.append((tokens[0], (float(tokens[1]), float(tokens[2]), float(tokens[3]))))
    return rows


class FakeRescueDriver:
    """Test rescue driver with a parabolic scan energy around the first point."""

    def __init__(self, *, parseable: bool = True) -> None:
        self.calls: list[str] = []
        self._origin: float | None = None
        self._parseable = parseable

    def run_native(
        self,
        materialized: MaterializedNativeInput,
        request: NativeExecutionRequest,
        *,
        stage: str,
    ) -> tuple[NativeExecutionResult, NativeResult | None]:
        self.calls.append(stage)
        if not self._parseable:
            return NativeExecutionResult(exit_code=0, wall_time_seconds=0.01), None
        match = re.search(r"rescue-scan-r(-?\d+\.\d+)", stage)
        if match is not None:
            radius = float(match.group(1))
            if self._origin is None:
                self._origin = radius
            energy = SCAN_ENERGY - 10.0 * (radius - self._origin) ** 2
            geometry_output = GeometryOutput.NONE
            final_geometry = None
        else:
            energy = SCAN_ENERGY
            rows = gjf_coordinates(materialized.files[0].content)
            geometry_output = GeometryOutput.PRODUCED
            final_geometry = ParsedGeometry(
                atoms=tuple(symbol for symbol, _ in rows),
                coordinates=tuple(point for _, point in rows),
            )
        execution = NativeExecutionResult(exit_code=0, wall_time_seconds=0.01)
        parsed = NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=True,
            geometry_output=geometry_output,
            final_geometry=final_geometry,
            energies_hartree=FrozenDict({"electronic": energy}),
            frequencies_cm=(),
            native_metadata=FrozenDict({}),
            produced_files=(),
            parser_diagnostics=(),
            log_file_name="rescue.log",
        )
        return execution, parsed


class TestNoneRecovery:
    """The none profile declines and never executes."""

    def test_registry_entry(self) -> None:
        policy = RECOVERIES["none"]
        assert policy.name == "none"
        assert policy.contract_version == "confflow.contract.recovery.none.v1"

    def test_evaluate_declines(self) -> None:
        decision = RECOVERIES["none"].evaluate(recovery_context())
        assert decision.attempt is False
        assert decision.reason == "recovery_disabled"

    def test_execute_returns_none(self) -> None:
        assert RECOVERIES["none"].execute(recovery_context(), FakeRescueDriver()) is None


class TestTsRescueEvaluate:
    """Evaluate matrix for the TS rescue scan."""

    def test_registry_entry(self) -> None:
        policy = RECOVERIES["ts_rescue_scan"]
        assert isinstance(policy, TsRescueScanPolicy)
        assert policy.name == "ts_rescue_scan"
        assert policy.contract_version == TS_RESCUE_SCAN_CONTRACT

    def test_unconfirmed_cancellation_declines(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        decision = policy.evaluate(recovery_context(confirmed=False))
        assert decision.attempt is False
        assert decision.reason == "cancellation_unconfirmed"

    def test_attempted_declines(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        decision = policy.evaluate(recovery_context(attempt=1))
        assert decision.attempt is False
        assert decision.reason == "max_attempts_reached"
        assert isinstance(decision, RecoveryDecision)

    def test_non_gaussian_failed_result_declines(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        context = recovery_context(failed=failed_result(ProgramName.ORCA))
        decision = policy.evaluate(context)
        assert decision.attempt is False
        assert decision.reason == "recovery_disabled_for_program"
        assert decision.details["failed_program"] == ProgramName.ORCA.value

    def test_orca_adapter_declines(self) -> None:
        policy = TsRescueScanPolicy(OrcaProgramAdapter())
        decision = policy.evaluate(recovery_context())
        assert decision.attempt is False
        assert decision.reason == "recovery_disabled_for_program"
        assert decision.details["adapter_program"] == ProgramName.ORCA.value

    def test_missing_bond_atoms_declines(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        context = recovery_context({"bond_atoms": None, "atoms": None})
        decision = policy.evaluate(context)
        assert decision.attempt is False
        assert decision.reason == "bond_atoms_missing"

    def test_malformed_bond_atoms_declines(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        context = recovery_context({"bond_atoms": [1, 1], "atoms": None})
        decision = policy.evaluate(context)
        assert decision.attempt is False
        assert decision.reason == "bond_atoms_missing"

    def test_non_recoverable_failure_declines(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        context = recovery_context(
            failed=failed_result(ProgramName.GAUSSIAN, terminated=True),
            diagnostics=(failure("calculation_error"),),
        )
        decision = policy.evaluate(context)
        assert decision.attempt is False
        assert decision.reason == "failure_not_recoverable"

    def test_cancellation_signal_vetoes(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        context = recovery_context(diagnostics=(failure("x", code="cancellation_error"),))
        decision = policy.evaluate(context)
        assert decision.attempt is False
        assert decision.reason == "failure_not_recoverable"

    def test_recoverable_attempts(self) -> None:
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        decision = policy.evaluate(recovery_context())
        assert decision.attempt is True
        assert decision.reason == "ts_failure_recoverable"
        assert list(decision.details["bond_atoms"]) == [1, 2]
        assert decision.details["failed_program"] == ProgramName.GAUSSIAN.value

    def test_unbound_policy_uses_failed_program(self) -> None:
        policy = TsRescueScanPolicy()
        assert policy.evaluate(recovery_context()).attempt is True
        orca_context = recovery_context(failed=failed_result(ProgramName.ORCA))
        assert policy.evaluate(orca_context).attempt is False


class TestTsRescueExecute:
    """Scan execution through a fake rescue driver (no real processes)."""

    def test_execute_success_path(self) -> None:
        policy = TsRescueScanPolicy(FakeGaussianAdapter())
        driver = FakeRescueDriver()
        execution = policy.execute(recovery_context(), driver)
        assert execution is not None
        assert execution.native_result.terminated_normally is True
        assert execution.native_result.energy == pytest.approx(SCAN_ENERGY)
        assert len(driver.calls) > 3
        assert driver.calls[0].startswith("rescue-scan-r")
        assert driver.calls[-1] == "rescue-reopt"
        assert execution.execution_result.exit_code == 0

    def test_success_diagnostic_shape(self) -> None:
        policy = TsRescueScanPolicy(FakeGaussianAdapter())
        execution = policy.execute(recovery_context(), FakeRescueDriver())
        assert execution is not None
        assert len(execution.diagnostics) == 1
        diagnostic = execution.diagnostics[0]
        assert diagnostic.code == "ts_rescue_scan"
        assert diagnostic.details["reason"] == "rescued_by_scan"
        assert list(diagnostic.details["bond_atoms"]) == [1, 2]
        assert diagnostic.step_id == STEP_ID
        assert diagnostic.work_item_id == WORK_ITEM_ID
        assert diagnostic.logical_key == LOGICAL_KEY
        assert not diagnostic.is_error
        assert len(diagnostic.details["scan_points"]) > 3
        assert execution.metadata["rescued_by_scan"] is True
        assert execution.metadata["scan_peak_bond"] == pytest.approx(
            diagnostic.details["peak_bond_length_angstrom"]
        )

    def test_none_path_when_driver_yields_nothing(self) -> None:
        policy = TsRescueScanPolicy(FakeGaussianAdapter())
        execution = policy.execute(recovery_context(), FakeRescueDriver(parseable=False))
        assert execution is None

    def test_none_path_without_bond_atoms(self) -> None:
        policy = TsRescueScanPolicy(FakeGaussianAdapter())
        context = recovery_context({"bond_atoms": None, "atoms": None})
        assert policy.execute(context, FakeRescueDriver()) is None

    def test_none_path_without_keyword(self) -> None:
        policy = TsRescueScanPolicy(FakeGaussianAdapter())
        inputs = recovery_inputs(native=FrozenDict({"keyword": ""}))
        context = recovery_context(inputs=inputs)
        assert policy.execute(context, FakeRescueDriver()) is None

    def test_execute_with_real_gaussian_adapter(self) -> None:
        # Pins the specified success path through the real adapter. Currently
        # fails in production: _modified_inputs writes the native key
        # "gaussian_modredundant", which GaussianProgramAdapter rejects
        # (it allows "modredundant"), so every scan point returns None.
        policy = TsRescueScanPolicy(GaussianProgramAdapter())
        execution = policy.execute(recovery_context(), FakeRescueDriver())
        assert execution is not None
        assert execution.native_result.energy == pytest.approx(SCAN_ENERGY)


class TestScanHelpers:
    """Keyword rewriting and bond parsing used by the rescue scan."""

    def test_parse_ts_bond_atoms(self) -> None:
        assert parse_ts_bond_atoms([1, 2]) == (1, 2)
        assert parse_ts_bond_atoms("1,2") == (1, 2)
        assert parse_ts_bond_atoms(None) is None
        assert parse_ts_bond_atoms([1, 1]) is None
        assert parse_ts_bond_atoms([0, 2]) is None

    def test_scan_keyword_drops_ts_items(self) -> None:
        rewritten = make_scan_keyword_from_ts_keyword("B3LYP/6-31G* Opt(TS,CalcFC) Freq")
        assert "TS" not in rewritten
        assert "Freq" not in rewritten
        assert "opt" in rewritten.lower()

    def test_modredundant_ensured_once(self) -> None:
        keyword = ensure_gaussian_modredundant_keyword("B3LYP Opt")
        assert keyword.count("modredundant") == 1
        assert ensure_gaussian_modredundant_keyword(keyword) == keyword

    def test_native_error_code_vocabulary(self) -> None:
        assert NativeErrorCode.NATIVE_EXECUTION_ERROR.value == "native_execution_error"
