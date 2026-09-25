#!/usr/bin/env python3

"""V4-2 branch coverage: TS rescue scan engine via a scripted driver.

A parabolic-energy stub driver plus a canned stub adapter drive the full
rescue pipeline — point scan, peak selection, reoptimization gates — with no
subprocess.  Fast and deterministic.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.execution.native import (
    GeometryOutput,
    MaterializedNativeInput,
    NativeExecutionResult,
    NativeResult,
    ParsedGeometry,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.recovery import RecoveryContext
from confflow.execution.recovery_standard import (
    SCAN_COARSE_STEP,
    TsRescueScanPolicy,
    _bond_length_of,
    _count_imaginary,
    _find_local_max,
    _positive_float,
    _scan_energy,
    _set_bond_length,
    parse_ts_bond_atoms,
)
from tests.v4._builders import structure


def _resolved(**overrides: Any) -> ResolvedCalculationInputs:
    """Build resolved inputs for the water structure."""
    from confflow.domain.resources import ResourceRequest

    fields: dict[str, Any] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": ResourceRequest(cores_per_item=1, memory_per_item_bytes=1024**3),
        "native": FrozenDict({"keyword": "opt=(ts,calcfc)"}),
        "step_id": "s_ts",
        "work_item_id": "wi:s_ts:s0",
        "logical_key": "s_ts:s0",
    }
    fields.update(overrides)
    return ResolvedCalculationInputs(**fields)


class StubRescueAdapter:
    """Adapter returning canned materialized input."""

    def __init__(self, program: ProgramName = ProgramName.GAUSSIAN) -> None:
        self._program = program

    @property
    def program_name(self) -> ProgramName:
        return self._program

    @property
    def adapter_version(self) -> str:
        return "test.adapter.v1"

    @property
    def parser_version(self) -> str:
        return "test.parser.v1"

    @property
    def input_extension(self) -> str:
        return "gjf"

    @property
    def log_extension(self) -> str:
        return "log"

    @property
    def default_executable(self) -> str:
        return "stub"

    def materialize_native_input(
        self, inputs: ResolvedCalculationInputs
    ) -> MaterializedNativeInput:
        return MaterializedNativeInput(
            program=self._program,
            main_input_name="job.gjf",
            metadata=FrozenDict({"keyword": str(inputs.native.get("keyword"))}),
        )

    def build_execution_request(
        self,
        materialized: MaterializedNativeInput,
        *,
        executable: str,
        work_dir: str,
        env: dict[str, str],
        walltime_seconds: float | None,
    ):
        from confflow.execution.native import NativeExecutionRequest

        return NativeExecutionRequest(
            executable=executable or "stub",
            argv=(executable or "stub", "job.gjf"),
            work_dir=work_dir or "/tmp/stub",
            env=FrozenDict(dict(env)),
            walltime_seconds=walltime_seconds,
        )

    def parse_native_result(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("driver returns parsed results in these tests")

    def discover_artifacts(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no discovery in these tests")

    def environment_probe(self, executable: str) -> dict[str, Any]:
        return {}


def _coords(structure_id: str = "s0"):
    """Return the water coordinates."""
    return tuple(structure(structure_id).coordinates)


class ParabolicDriver:
    """Stub driver with parabolic scan energies around a peak radius."""

    def __init__(
        self,
        *,
        peak_offset: float = 0.0,
        fail_stages: tuple[str, ...] = (),
        reopt_geometry: str = "peak",
        reopt_frequencies: tuple[float, ...] = (100.0, -50.0),
        reopt_energy: float | None = -76.4,
        reopt_terminated: bool = True,
    ) -> None:
        self.peak_offset = peak_offset
        self.fail_stages = set(fail_stages)
        self.reopt_geometry = reopt_geometry
        self.reopt_frequencies = reopt_frequencies
        self.reopt_energy = reopt_energy
        self.reopt_terminated = reopt_terminated
        self.stages: list[str] = []

    def _radius_of(self, stage: str) -> float | None:
        match = re.search(r"rescue-scan-r(-?\d+\.\d+)", stage)
        return float(match.group(1)) if match else None

    def run_native(
        self, materialized: MaterializedNativeInput, request: Any, *, stage: str
    ) -> tuple[NativeExecutionResult, NativeResult | None]:
        self.stages.append(stage)
        execution = NativeExecutionResult(exit_code=0, wall_time_seconds=0.1)
        if stage in self.fail_stages:
            return execution, None
        if stage == "rescue-reopt":
            return execution, self._reopt_result()
        radius = self._radius_of(stage)
        assert radius is not None
        base = _bond_length_of(_coords(), 1, 2)
        assert base is not None
        energy = -((radius - (base + self.peak_offset)) ** 2)
        adjusted = _set_bond_length(_coords(), 1, 2, radius)
        assert adjusted is not None
        atoms = tuple(structure("s0").atoms)
        parsed = NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=True,
            geometry_output=GeometryOutput.PRODUCED,
            final_geometry=ParsedGeometry(atoms=atoms, coordinates=tuple(adjusted)),
            energies_hartree=FrozenDict({"electronic": energy}),
            log_file_name="scan.log",
        )
        return execution, parsed

    def _reopt_result(self) -> NativeResult | None:
        if self.reopt_geometry == "none":
            return NativeResult(
                program=ProgramName.GAUSSIAN,
                terminated_normally=self.reopt_terminated,
                geometry_output=GeometryOutput.NONE,
                energies_hartree=(
                    FrozenDict({"electronic": self.reopt_energy})
                    if self.reopt_energy is not None
                    else FrozenDict({})
                ),
                frequencies_cm=self.reopt_frequencies,
                log_file_name="reopt.log",
            )
        base = _coords()
        if self.reopt_geometry == "distorted":
            coords = tuple(tuple(component * 3.0 for component in point) for point in base)
        else:
            coords = base
        atoms = tuple(structure("s0").atoms)
        return NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=self.reopt_terminated,
            geometry_output=GeometryOutput.PRODUCED,
            final_geometry=ParsedGeometry(atoms=atoms, coordinates=coords),
            energies_hartree=(
                FrozenDict({"electronic": self.reopt_energy})
                if self.reopt_energy is not None
                else FrozenDict({})
            ),
            frequencies_cm=self.reopt_frequencies,
            log_file_name="reopt.log",
        )


def _failed_result() -> NativeResult:
    """Build an abnormally terminated Gaussian result."""
    return NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=False,
        geometry_output=GeometryOutput.NONE,
        log_file_name="ts.log",
    )


def _context(**overrides: Any) -> RecoveryContext:
    """Build a rescue context for the water TS."""
    from confflow.domain import Diagnostic

    fields: dict[str, Any] = {
        "profile_name": "ts_rescue_scan",
        "work_item_id": "wi:s_ts:s0",
        "step_id": "s_ts",
        "logical_key": "s_ts:s0",
        "inputs": _resolved(),
        "failed_native_result": _failed_result(),
        "failure_diagnostics": (Diagnostic(code="scientific_check_error", message="imag"),),
        "params": FrozenDict({"bond_atoms": [1, 2]}),
    }
    fields.update(overrides)
    return RecoveryContext(**fields)


class TestScanMath:
    """Pure scan helpers behave on edges."""

    def test_find_local_max_needs_three_points(self) -> None:
        assert _find_local_max([]) is None
        assert _find_local_max([(0.0, 1.0, _coords())]) is None
        assert (
            _find_local_max([(0.0, 1.0, _coords()), (0.1, 2.0, _coords()), (0.2, 1.5, _coords())])
            is not None
        )

    def test_find_local_max_prefers_highest(self) -> None:
        peak = _find_local_max(
            [
                (0.0, 1.0, _coords()),
                (0.1, 3.0, _coords()),
                (0.2, 2.0, _coords()),
                (0.3, 2.5, _coords()),
                (0.4, 1.0, _coords()),
            ]
        )
        assert peak is not None
        assert peak[0] == pytest.approx(0.1)

    def test_find_local_max_monotone_is_none(self) -> None:
        assert (
            _find_local_max([(0.0, 1.0, _coords()), (0.1, 2.0, _coords()), (0.2, 3.0, _coords())])
            is None
        )

    def test_count_imaginary_edges(self) -> None:
        assert _count_imaginary(()) is None
        assert _count_imaginary((100.0, 200.0)) == 0
        assert _count_imaginary((100.0, -50.0)) == 1
        assert _count_imaginary((5.0, -5.0)) == 0
        assert _count_imaginary(("bad",)) is None
        assert _count_imaginary((float("inf"),)) == 0

    def test_scan_energy_prefers_gibbs(self) -> None:
        assert _scan_energy(_terminated_none()) is None
        result = NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=True,
            geometry_output=GeometryOutput.NONE,
            energies_hartree=FrozenDict({"electronic": -1.0, "gibbs": -0.9}),
            log_file_name="x.log",
        )
        assert _scan_energy(result) == pytest.approx(-0.9)

    def test_positive_float_edges(self) -> None:
        assert _positive_float("bogus", 0.0) is None
        assert _positive_float(-1.0, 0.0) is None
        assert _positive_float(float("nan"), 0.0) is None
        assert _positive_float(0.0, 0.0) is None
        assert _positive_float("0.5", 0.0) == pytest.approx(0.5)
        assert _positive_float(None, 0.0) is None

    def test_set_bond_length_degenerate(self) -> None:
        assert _set_bond_length(_coords(), 1, 1, 1.0) is None
        assert _set_bond_length(_coords(), 1, 99, 1.0) is None
        moved = _set_bond_length(_coords(), 1, 2, 1.5)
        assert moved is not None
        assert _bond_length_of(moved, 1, 2) == pytest.approx(1.5)

    def test_bond_length_of_bad_index(self) -> None:
        assert _bond_length_of(_coords(), 1, 99) is None

    def test_parse_ts_bond_atoms_forms(self) -> None:
        assert parse_ts_bond_atoms("1-2") == (1, 2)


def _terminated_none() -> NativeResult:
    """Build a native result with no energies."""
    return NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=True,
        geometry_output=GeometryOutput.NONE,
        log_file_name="x.log",
    )


class TestRescueEvaluate:
    """Evaluate gates every precondition explicitly."""

    def test_unconfirmed_decline(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        decision = policy.evaluate(_context(cancellation_confirmed=False))
        assert decision.attempt is False
        assert decision.reason == "cancellation_unconfirmed"

    def test_attempt_cap(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        decision = policy.evaluate(_context(attempt=1))
        assert decision.attempt is False
        assert decision.reason == "max_attempts_reached"
        assert decision.details["attempt"] == 1

    def test_non_gaussian_decline(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        orca_failed = NativeResult(
            program=ProgramName.ORCA,
            terminated_normally=False,
            geometry_output=GeometryOutput.NONE,
            log_file_name="x.out",
        )
        decision = policy.evaluate(_context(failed_native_result=orca_failed))
        assert decision.attempt is False
        assert decision.reason == "recovery_disabled_for_program"

    def test_unbound_policy_uses_failed_program(self) -> None:
        policy = TsRescueScanPolicy()
        decision = policy.evaluate(_context())
        assert decision.attempt is True
        assert tuple(decision.details["bond_atoms"]) == (1, 2)

    def test_missing_bond_atoms_decline(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        decision = policy.evaluate(_context(params=FrozenDict({})))
        assert decision.attempt is False
        assert decision.reason == "bond_atoms_missing"

    def test_cancellation_signal_decline(self) -> None:
        from confflow.domain import Diagnostic

        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        context = _context(
            failure_diagnostics=(Diagnostic(code="cancellation_error", message="stop"),)
        )
        decision = policy.evaluate(context)
        assert decision.attempt is False
        assert decision.reason == "failure_not_recoverable"

    def test_unrecoverable_reason_decline(self) -> None:
        from confflow.domain import Diagnostic

        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        terminated_ok = NativeResult(
            program=ProgramName.GAUSSIAN,
            terminated_normally=True,
            geometry_output=GeometryOutput.NONE,
            log_file_name="x.log",
        )
        context = _context(
            failed_native_result=terminated_ok,
            failure_diagnostics=(
                Diagnostic(
                    code="native_input_error",
                    message="bad",
                    details=FrozenDict({"reason": "bad_keyword"}),
                ),
            ),
        )
        decision = policy.evaluate(context)
        assert decision.attempt is False
        assert decision.reason == "failure_not_recoverable"

    def test_recoverable_attempt(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        decision = policy.evaluate(_context())
        assert decision.attempt is True
        assert decision.reason == "ts_failure_recoverable"


class TestRescueExecute:
    """End-to-end rescue through the scripted driver."""

    def test_full_success_no_freq(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver()
        execution = policy.execute(_context(), driver)
        assert execution is not None
        assert execution.metadata["rescued_by_scan"] is True
        assert execution.metadata["scan_peak_bond"] == pytest.approx(
            _bond_length_of(_coords(), 1, 2)
        )
        assert len(driver.stages) > 5

    def test_full_success_with_freq(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver(reopt_frequencies=(120.0, -65.0))
        context = _context(
            inputs=_resolved(native=FrozenDict({"keyword": "opt=(ts,calcfc) freq"})),
        )
        execution = policy.execute(context, driver)
        assert execution is not None
        assert execution.metadata["rescued_by_scan"] is True

    def test_initial_point_failure(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        base = _bond_length_of(_coords(), 1, 2)
        assert base is not None
        driver = ParabolicDriver(fail_stages=(f"rescue-scan-r{base:.3f}",))
        assert policy.execute(_context(), driver) is None

    def test_coarse_extension_finds_offset_peak(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver(peak_offset=0.25)
        execution = policy.execute(_context(), driver)
        assert execution is not None
        assert execution.metadata["rescued_by_scan"] is True
        peak = float(execution.metadata["scan_peak_bond"])
        base = _bond_length_of(_coords(), 1, 2)
        assert base is not None
        assert peak == pytest.approx(base + 0.25, abs=0.03)
        coarse_stages = [stage for stage in driver.stages if stage.startswith("rescue-scan-")]
        assert len(coarse_stages) > 6

    def test_strictly_rising_scan_aborts(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())

        class RisingDriver(ParabolicDriver):
            def run_native(self, materialized, request, *, stage):  # type: ignore[no-untyped-def]
                execution, parsed = super().run_native(materialized, request, stage=stage)
                if parsed is None or stage == "rescue-reopt":
                    return execution, parsed
                rising = NativeResult(
                    program=parsed.program,
                    terminated_normally=True,
                    geometry_output=parsed.geometry_output,
                    final_geometry=parsed.final_geometry,
                    energies_hartree=FrozenDict({"electronic": float(len(self.stages))}),
                    log_file_name="x.log",
                )
                return execution, rising

        assert policy.execute(_context(), RisingDriver()) is None

    def test_flat_scan_has_no_peak(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())

        class FlatDriver(ParabolicDriver):
            def run_native(self, materialized, request, *, stage):  # type: ignore[no-untyped-def]
                execution, parsed = super().run_native(materialized, request, stage=stage)
                if parsed is None:
                    return execution, None
                flat = NativeResult(
                    program=parsed.program,
                    terminated_normally=True,
                    geometry_output=parsed.geometry_output,
                    final_geometry=parsed.final_geometry,
                    energies_hartree=FrozenDict({"electronic": -1.0}),
                    log_file_name="x.log",
                )
                return execution, flat

        assert policy.execute(_context(), FlatDriver()) is None

    def test_bad_scan_params_decline(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver()
        context = _context(params=FrozenDict({"bond_atoms": [1, 2], "scan_coarse_step": "bogus"}))
        assert policy.execute(context, driver) is None
        context = _context(params=FrozenDict({"bond_atoms": [1, 2], "scan_max_steps": "bogus"}))
        assert policy.execute(context, driver) is None
        context = _context(params=FrozenDict({"bond_atoms": [1, 2], "scan_max_steps": 0}))
        assert policy.execute(context, driver) is None

    def test_empty_keyword_decline(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        context = _context(inputs=_resolved(native=FrozenDict({"keyword": ""})))
        assert policy.execute(context, DriverNoStages()) is None

    def test_reopt_drift_failure(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver(reopt_geometry="distorted")
        assert policy.execute(_context(), driver) is None

    def test_reopt_imag_failure(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver(reopt_frequencies=(100.0, 200.0))
        context = _context(
            inputs=_resolved(native=FrozenDict({"keyword": "opt=(ts,calcfc) freq"})),
        )
        assert policy.execute(context, driver) is None

    def test_reopt_no_energy_failure(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver(reopt_energy=None)
        assert policy.execute(_context(), driver) is None

    def test_reopt_abnormal_failure(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver(reopt_terminated=False)
        assert policy.execute(_context(), driver) is None

    def test_reopt_no_geometry_failure(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver(reopt_geometry="none")
        assert policy.execute(_context(), driver) is None

    def test_unbound_execute_declines(self) -> None:
        policy = TsRescueScanPolicy()
        assert policy.execute(_context(), ParabolicDriver()) is None

    def test_bad_thresholds_decline(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        driver = ParabolicDriver()
        context = _context(
            params=FrozenDict({"bond_atoms": [1, 2], "rmsd_threshold_angstrom": "bogus"})
        )
        assert policy.execute(context, driver) is None
        context = _context(
            params=FrozenDict({"bond_atoms": [1, 2], "bond_drift_threshold_angstrom": -1.0})
        )
        assert policy.execute(context, driver) is None

    def test_request_kwargs_edges(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        assert (
            policy._request_kwargs(
                _context(params=FrozenDict({"bond_atoms": [1, 2], "work_dir": ""}))
            )
            is None
        )
        kwargs = policy._request_kwargs(
            _context(
                params=FrozenDict(
                    {
                        "bond_atoms": [1, 2],
                        "executable": "g16",
                        "work_dir": "/tmp/x",
                        "env": {"A": "b"},
                        "walltime_seconds": 60,
                    }
                )
            )
        )
        assert kwargs is not None
        assert kwargs["executable"] == "g16"
        assert kwargs["walltime_seconds"] == 60.0
        assert (
            policy._request_kwargs(
                _context(params=FrozenDict({"bond_atoms": [1, 2], "walltime_seconds": "bogus"}))
            )["walltime_seconds"]
            is None
        )


class DriverNoStages:
    """Driver that records stages but returns nothing usable."""

    def run_native(self, materialized, request, *, stage):  # type: ignore[no-untyped-def]
        return NativeExecutionResult(exit_code=1, wall_time_seconds=0.1), None


class TestRescueModifiedInputs:
    """Modified input rendering preserves identity and merges directives."""

    def test_directives_merged(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        context = _context(
            inputs=_resolved(native=FrozenDict({"keyword": "opt", "modredundant": ["X 1 F"]}))
        )
        modified = policy._modified_inputs(context, _coords(), "opt=modredundant", ("B 1 2 F",))
        assert modified is not None
        assert "B 1 2 F" in modified.native["modredundant"]
        assert "X 1 F" in modified.native["modredundant"]

    def test_legacy_key_folded(self) -> None:
        policy = TsRescueScanPolicy(adapter=StubRescueAdapter())
        context = _context(
            inputs=_resolved(
                native=FrozenDict({"keyword": "opt", "gaussian_modredundant": ["Y 1 F"]})
            )
        )
        modified = policy._modified_inputs(context, _coords(), "opt=modredundant", ("B 1 2 F",))
        assert modified is not None
        assert "gaussian_modredundant" not in modified.native
        assert "Y 1 F" in modified.native["modredundant"]

    def test_scan_constants(self) -> None:
        assert SCAN_COARSE_STEP == pytest.approx(0.1)
        assert len({SCAN_COARSE_STEP}) == 1
