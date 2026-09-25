#!/usr/bin/env python3

"""V4-2 standard profile and scientific checks.

Covers the standard result profile (:class:`StandardResultProfile`) and all
six built-in checks from ``CHECKS`` using only ``tmp_path``-free in-memory
fixtures:

- produced versus passthrough geometry (id rule, parentage, lineage),
- energy selection (Gibbs preferred, electronic-plus-correction fallback,
  correction derivation) with units, subjects, and provenance,
- the native-termination transport diagnostic,
- every check with its stable failure reasons, default thresholds from
  :data:`CHECK_DEFAULTS`, imaginary expected/observed accounting, Kabsch RMSD
  behavior, bond drift, and the frequencies-required versus imaginary-count
  interplay.
"""

from __future__ import annotations

import pytest

from confflow.domain import (
    ArtifactSet,
    FrozenDict,
    ResourceRequest,
    StructureSet,
    Unit,
)
from confflow.execution import (
    CHECK_DEFAULTS,
    CheckContext,
    CheckOutcome,
    GeometryOutput,
    GeometrySemantics,
    NativeResult,
    ParsedGeometry,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.checks_standard import CHECK_FAILED_CODE, CHECKS
from confflow.execution.profile_standard import (
    NATIVE_TERMINATION_CODE,
    PROFILES,
    STANDARD_PROFILE_CONTRACT,
    StandardResultProfile,
    count_imaginary_frequencies,
)
from confflow.execution.profiles import (
    ProfileContext,
    passthrough_structure_id,
    produced_structure_id,
)
from tests.v4._builders import structure

STEP_ID = "s_opt"
WORK_ITEM_ID = "wi:s_opt:item0"
LOGICAL_KEY = "s_opt:item0"
KEYWORD = "B3LYP/6-31G* Opt"

REAL_FREQUENCIES = (100.1234, 200.2345, 300.3456, 1500.0, 1700.0, 3800.0)
TS_FREQUENCIES = (-500.1234, 200.2345, 300.3456, 1500.0, 1700.0, 3800.0)

ELECTRONIC = -76.4589123456
GIBBS_CORRECTION = 0.0149
GIBBS = ELECTRONIC + GIBBS_CORRECTION


def resolved_inputs(**overrides: object) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs over the water fixture."""
    params: dict[str, object] = {
        "structure": structure("s0"),
        "charge": 0,
        "multiplicity": 1,
        "freeze": None,
        "resources": ResourceRequest.from_values(cores_per_item=4, memory_per_item="16GB"),
        "native": FrozenDict({"keyword": KEYWORD}),
        "checkpoints": (),
        "step_id": STEP_ID,
        "work_item_id": WORK_ITEM_ID,
        "logical_key": LOGICAL_KEY,
    }
    params.update(overrides)
    return ResolvedCalculationInputs(**params)  # type: ignore[arg-type]


def native_result(
    *,
    terminated: bool = True,
    geometry: tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]] | None = None,
    energies: dict[str, float] | None = None,
    frequencies: tuple[float, ...] = (),
) -> NativeResult:
    """Build parser facts for profile and check tests."""
    if geometry is None:
        output = GeometryOutput.NONE
        final = None
    else:
        atoms, coords = geometry
        output = GeometryOutput.PRODUCED
        final = ParsedGeometry(atoms=atoms, coordinates=coords)
    return NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=terminated,
        geometry_output=output,
        final_geometry=final,
        energies_hartree=FrozenDict(dict(energies or {})),
        frequencies_cm=frequencies,
        native_metadata=FrozenDict({}),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="job.log",
    )


def shifted_water(
    delta: float = 0.01,
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Return water coordinates shifted rigidly by *delta* on x."""
    record = structure("s0")
    return record.atoms, tuple((x + delta, y, z) for x, y, z in record.coordinates)


def apply_profile(result: NativeResult) -> object:
    """Apply the standard profile to parser facts."""
    profile = PROFILES["standard"]
    return profile.apply(
        ProfileContext(
            work_item_id=WORK_ITEM_ID,
            step_id=STEP_ID,
            logical_key=LOGICAL_KEY,
            profile_name=profile.name,
            profile_version=profile.contract_version,
            native_result=result,
            inputs=resolved_inputs(),
            discovered_artifacts=ArtifactSet(),
        )
    )


def run_check(
    name: str, result: NativeResult, params: dict[str, object] | None = None
) -> CheckOutcome:
    """Apply the profile then run check *name* with *params*."""
    output = apply_profile(result)
    return CHECKS[name].run(
        CheckContext(
            check_name=name,
            work_item_id=WORK_ITEM_ID,
            step_id=STEP_ID,
            logical_key=LOGICAL_KEY,
            profile_output=output,  # type: ignore[arg-type]
            inputs=resolved_inputs(),
            params=FrozenDict(dict(params or {})),
        )
    )


class TestCheckDefaults:
    """Single-source default parameters."""

    def test_all_six_checks_have_defaults(self) -> None:
        assert set(CHECK_DEFAULTS) == set(CHECKS)
        assert set(CHECK_DEFAULTS) == {
            "normal_termination",
            "geometry_required",
            "frequencies_required",
            "imaginary_frequency_count",
            "max_rmsd_from_input",
            "bond_drift",
        }

    def test_threshold_values(self) -> None:
        assert CHECK_DEFAULTS["imaginary_frequency_count"] == {"expected": 0}
        assert CHECK_DEFAULTS["max_rmsd_from_input"] == {"threshold_angstrom": 1.0}
        assert CHECK_DEFAULTS["bond_drift"] == {"threshold_angstrom": 0.4}
        assert CHECK_DEFAULTS["normal_termination"] == {}
        assert CHECK_DEFAULTS["geometry_required"] == {}
        assert CHECK_DEFAULTS["frequencies_required"] == {}

    def test_check_contract_versions(self) -> None:
        assert CHECKS["normal_termination"].contract_version.endswith("normal_termination.v1")
        assert CHECKS["geometry_required"].contract_version.endswith("geometry_required.v1")
        assert CHECKS["frequencies_required"].contract_version.endswith("frequencies_required.v1")
        assert CHECKS["imaginary_frequency_count"].contract_version.endswith(
            "imaginary_frequency_count.v1"
        )
        assert CHECKS["max_rmsd_from_input"].contract_version.endswith("max_rmsd_from_input.v1")
        assert CHECKS["bond_drift"].contract_version.endswith("bond_drift.v1")


class TestProfileIdentity:
    """Profile registration and contract."""

    def test_standard_profile_registered(self) -> None:
        profile = PROFILES["standard"]
        assert isinstance(profile, StandardResultProfile)
        assert profile.name == "standard"
        assert profile.contract_version == STANDARD_PROFILE_CONTRACT
        assert STANDARD_PROFILE_CONTRACT == "confflow.contract.result_profile.standard.v1"


class TestProducedVsPassthrough:
    """Geometry semantics, ids, parentage, and lineage."""

    def test_produced_geometry(self) -> None:
        atoms, coords = shifted_water()
        output = apply_profile(native_result(geometry=(atoms, coords)))
        assert output.geometry_semantics is GeometrySemantics.PRODUCED
        assert len(output.structures) == 1
        record = output.structures[0]
        assert record.id == produced_structure_id(LOGICAL_KEY, 0)
        assert record.id == f"{LOGICAL_KEY}:structure:0"
        assert record.parent_ids == ("s0",)
        assert record.lineage_root_id == structure("s0").lineage_root_id
        assert record.coordinates == coords
        assert record.source_step_id == STEP_ID
        assert record.source_work_item_id == WORK_ITEM_ID

    def test_passthrough_geometry(self) -> None:
        output = apply_profile(native_result(geometry=None))
        assert output.geometry_semantics is GeometrySemantics.PASSTHROUGH
        assert len(output.structures) == 1
        record = output.structures[0]
        assert record.id == passthrough_structure_id(LOGICAL_KEY)
        assert record.id == f"{LOGICAL_KEY}:structure:passthrough"
        assert record.parent_ids == ("s0",)
        assert record.lineage_root_id == structure("s0").lineage_root_id
        assert record.geometry_digest == structure("s0").geometry_digest

    def test_passthrough_id_rule(self) -> None:
        assert passthrough_structure_id("step:key") == "step:key:structure:passthrough"
        assert produced_structure_id("step:key", 0) == "step:key:structure:0"

    def test_charge_from_inputs_override(self) -> None:
        inputs = resolved_inputs(charge=1, multiplicity=2)
        profile = PROFILES["standard"]
        output = profile.apply(
            ProfileContext(
                work_item_id=WORK_ITEM_ID,
                step_id=STEP_ID,
                logical_key=LOGICAL_KEY,
                profile_name=profile.name,
                profile_version=profile.contract_version,
                native_result=native_result(geometry=None),
                inputs=inputs,
                discovered_artifacts=ArtifactSet(),
            )
        )
        assert output.structures[0].charge == 1
        assert output.structures[0].multiplicity == 2


class TestEnergySelection:
    """Gibbs preferred, electronic-plus-correction fallback, derivation."""

    def test_gibbs_preferred(self) -> None:
        output = apply_profile(native_result(energies={"electronic": ELECTRONIC, "gibbs": GIBBS}))
        energy = output.results.first("energy")
        assert energy is not None
        assert energy.value == pytest.approx(GIBBS)
        assert energy.unit is Unit.HARTREE
        gibbs = output.results.first("gibbs_energy")
        assert gibbs is not None and gibbs.value == pytest.approx(GIBBS)
        correction = output.results.first("gibbs_correction")
        assert correction is not None
        assert correction.value == pytest.approx(GIBBS - ELECTRONIC)

    def test_electronic_plus_correction(self) -> None:
        output = apply_profile(
            native_result(energies={"electronic": ELECTRONIC, "gibbs_correction": GIBBS_CORRECTION})
        )
        energy = output.results.first("energy")
        assert energy is not None
        assert energy.value == pytest.approx(ELECTRONIC + GIBBS_CORRECTION)
        assert output.results.first("gibbs_energy") is None
        correction = output.results.first("gibbs_correction")
        assert correction is not None
        assert correction.value == pytest.approx(GIBBS_CORRECTION)

    def test_electronic_only(self) -> None:
        output = apply_profile(native_result(energies={"electronic": ELECTRONIC}))
        energy = output.results.first("energy")
        assert energy is not None
        assert energy.value == pytest.approx(ELECTRONIC)
        assert output.results.first("gibbs_energy") is None
        assert output.results.first("gibbs_correction") is None

    def test_no_energies_no_results(self) -> None:
        output = apply_profile(native_result(energies={}))
        assert output.results.first("energy") is None
        assert output.results.is_empty

    def test_result_subjects_and_provenance(self) -> None:
        output = apply_profile(native_result(energies={"electronic": ELECTRONIC, "gibbs": GIBBS}))
        for item in output.results:
            assert item.subject_structure_id == "s0"
            assert item.source_step_id == STEP_ID
            assert item.source_work_item_id == WORK_ITEM_ID
            assert item.provenance is not None
            assert item.provenance.program == ProgramName.GAUSSIAN.value
            assert item.provenance.method == KEYWORD
            assert item.provenance.adapter == STANDARD_PROFILE_CONTRACT
            assert item.provenance.step_id == STEP_ID
            assert item.provenance.work_item_id == WORK_ITEM_ID

    def test_frequency_results(self) -> None:
        output = apply_profile(
            native_result(energies={"electronic": ELECTRONIC}, frequencies=TS_FREQUENCIES)
        )
        frequencies = output.results.first("frequencies")
        assert frequencies is not None
        assert frequencies.unit is Unit.CM_INVERSE
        assert list(frequencies.value) == list(TS_FREQUENCIES)
        count = output.results.first("num_imaginary_frequencies")
        assert count is not None
        assert count.value == 1
        lowest = output.results.first("lowest_frequency")
        assert lowest is not None
        assert lowest.value == pytest.approx(-500.1234)
        assert lowest.unit is Unit.CM_INVERSE

    def test_no_frequency_results_without_modes(self) -> None:
        output = apply_profile(native_result(energies={"electronic": ELECTRONIC}))
        assert output.results.first("frequencies") is None
        assert output.results.first("num_imaginary_frequencies") is None
        assert output.results.first("lowest_frequency") is None


class TestTerminationDiagnostic:
    """Native termination transport for the normal_termination check."""

    def test_terminated_true_is_info(self) -> None:
        output = apply_profile(native_result(terminated=True))
        facts = [item for item in output.diagnostics if item.code == NATIVE_TERMINATION_CODE]
        assert len(facts) == 1
        assert facts[0].details["terminated"] is True
        assert not facts[0].is_error

    def test_terminated_false_is_error(self) -> None:
        output = apply_profile(native_result(terminated=False))
        facts = [item for item in output.diagnostics if item.code == NATIVE_TERMINATION_CODE]
        assert len(facts) == 1
        assert facts[0].details["terminated"] is False
        assert facts[0].is_error


class TestNormalTerminationCheck:
    """Termination gate pass and failure."""

    def test_pass(self) -> None:
        outcome = run_check("normal_termination", native_result(terminated=True))
        assert outcome.passed is True
        assert outcome.diagnostic is None

    def test_abnormal_termination(self) -> None:
        outcome = run_check("normal_termination", native_result(terminated=False))
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.code == CHECK_FAILED_CODE
        assert outcome.diagnostic.details["check"] == "normal_termination"
        assert outcome.diagnostic.details["reason"] == "abnormal_termination"


class TestGeometryRequiredCheck:
    """Produced geometry gate."""

    def test_pass(self) -> None:
        atoms, coords = shifted_water()
        outcome = run_check("geometry_required", native_result(geometry=(atoms, coords)))
        assert outcome.passed is True

    def test_passthrough_fails(self) -> None:
        outcome = run_check("geometry_required", native_result(geometry=None))
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "geometry_missing"


class TestFrequenciesRequiredCheck:
    """Parsed frequency-set gate."""

    def test_pass(self) -> None:
        outcome = run_check(
            "frequencies_required",
            native_result(energies={"electronic": ELECTRONIC}, frequencies=REAL_FREQUENCIES),
        )
        assert outcome.passed is True

    def test_missing_fails(self) -> None:
        outcome = run_check(
            "frequencies_required", native_result(energies={"electronic": ELECTRONIC})
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "frequencies_missing"


class TestImaginaryFrequencyCountCheck:
    """Expected versus observed imaginary counts."""

    def test_no_data_passes_for_zero_expectation(self) -> None:
        outcome = run_check(
            "imaginary_frequency_count", native_result(energies={"electronic": ELECTRONIC})
        )
        assert outcome.passed is True

    def test_no_data_fails_for_nonzero_expectation(self) -> None:
        outcome = run_check(
            "imaginary_frequency_count",
            native_result(energies={"electronic": ELECTRONIC}),
            {"expected": 1},
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "frequencies_missing"
        assert outcome.diagnostic.details["expected"] == 1

    def test_ts_candidate_passes_for_expected_one(self) -> None:
        outcome = run_check(
            "imaginary_frequency_count",
            native_result(energies={"electronic": ELECTRONIC}, frequencies=TS_FREQUENCIES),
            {"expected": 1},
        )
        assert outcome.passed is True

    def test_ts_candidate_fails_for_expected_zero(self) -> None:
        outcome = run_check(
            "imaginary_frequency_count",
            native_result(energies={"electronic": ELECTRONIC}, frequencies=TS_FREQUENCIES),
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "imaginary_count_mismatch"
        assert outcome.diagnostic.details["expected"] == 0
        assert outcome.diagnostic.details["observed"] == 1
        assert outcome.diagnostic.details["lowest"] == pytest.approx(-500.1234)

    def test_invalid_expected_parameter(self) -> None:
        outcome = run_check(
            "imaginary_frequency_count",
            native_result(energies={"electronic": ELECTRONIC}, frequencies=REAL_FREQUENCIES),
            {"expected": "many"},
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "invalid_check_parameter"

    def test_frequencies_required_vs_imag_interplay(self) -> None:
        bare = native_result(energies={"electronic": ELECTRONIC})
        assert run_check("frequencies_required", bare).passed is False
        assert run_check("imaginary_frequency_count", bare).passed is True
        assert run_check("imaginary_frequency_count", bare, {"expected": 1}).passed is False


class TestMaxRmsdFromInputCheck:
    """Kabsch-aligned RMSD against the input structure."""

    def test_identical_coords_pass(self) -> None:
        record = structure("s0")
        outcome = run_check(
            "max_rmsd_from_input", native_result(geometry=(record.atoms, record.coordinates))
        )
        assert outcome.passed is True

    def test_rigid_shift_passes(self) -> None:
        atoms, coords = shifted_water(0.01)
        outcome = run_check("max_rmsd_from_input", native_result(geometry=(atoms, coords)))
        assert outcome.passed is True

    def test_deformation_fails(self) -> None:
        record = structure("s0")
        moved = tuple(
            (x + 3.0, y, z) if index == 1 else (x, y, z)
            for index, (x, y, z) in enumerate(record.coordinates)
        )
        outcome = run_check("max_rmsd_from_input", native_result(geometry=(record.atoms, moved)))
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "rmsd_exceeded"
        assert outcome.diagnostic.details["threshold"] == pytest.approx(1.0)
        assert outcome.diagnostic.details["rmsd"] > 1.0

    def test_default_threshold_is_one_angstrom(self) -> None:
        assert CHECK_DEFAULTS["max_rmsd_from_input"]["threshold_angstrom"] == 1.0

    def test_custom_threshold_applies(self) -> None:
        record = structure("s0")
        moved = tuple(
            (x + 3.0, y, z) if index == 1 else (x, y, z)
            for index, (x, y, z) in enumerate(record.coordinates)
        )
        outcome = run_check(
            "max_rmsd_from_input",
            native_result(geometry=(record.atoms, moved)),
            {"threshold_angstrom": 5.0},
        )
        assert outcome.passed is True

    def test_invalid_threshold_parameter(self) -> None:
        record = structure("s0")
        outcome = run_check(
            "max_rmsd_from_input",
            native_result(geometry=(record.atoms, record.coordinates)),
            {"threshold_angstrom": "wide"},
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "invalid_check_parameter"


class TestBondDriftCheck:
    """Critical bond drift with default and declared parameters."""

    def test_identical_coords_pass(self) -> None:
        record = structure("s0")
        outcome = run_check(
            "bond_drift",
            native_result(geometry=(record.atoms, record.coordinates)),
            {"atoms": [1, 2]},
        )
        assert outcome.passed is True

    def test_drift_exceeded(self) -> None:
        record = structure("s0")
        moved = tuple(
            (x + 0.5, y, z) if index == 1 else (x, y, z)
            for index, (x, y, z) in enumerate(record.coordinates)
        )
        outcome = run_check(
            "bond_drift", native_result(geometry=(record.atoms, moved)), {"atoms": [1, 2]}
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "bond_drift_exceeded"
        assert outcome.diagnostic.details["threshold"] == pytest.approx(0.4)
        assert outcome.diagnostic.details["observed"] == pytest.approx(
            abs(outcome.diagnostic.details["r_final"] - outcome.diagnostic.details["r_initial"])
        )

    def test_default_threshold_is_point_four_angstrom(self) -> None:
        assert CHECK_DEFAULTS["bond_drift"]["threshold_angstrom"] == 0.4

    def test_missing_atoms_error(self) -> None:
        record = structure("s0")
        outcome = run_check(
            "bond_drift", native_result(geometry=(record.atoms, record.coordinates))
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "bond_atoms_missing"

    def test_unresolvable_atoms_error(self) -> None:
        record = structure("s0")
        outcome = run_check(
            "bond_drift",
            native_result(geometry=(record.atoms, record.coordinates)),
            {"atoms": [1, 99]},
        )
        assert outcome.passed is False
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.details["reason"] == "bond_atoms_missing"


class TestImaginaryCountHelper:
    """Noise-floor accounting shared by the profile."""

    def test_empty_modes(self) -> None:
        assert count_imaginary_frequencies(()) == (0, None)

    def test_noise_floor_excluded(self) -> None:
        assert count_imaginary_frequencies((0.0, 5.0, -5.0, 100.0)) == (0, 100.0)

    def test_imaginary_counted(self) -> None:
        assert count_imaginary_frequencies(TS_FREQUENCIES) == (1, -500.1234)


class TestStructureSetImport:
    """Structure collection import used across profile tests."""

    def test_structure_set_round_trip(self) -> None:
        records = StructureSet.of(structure("s0"))
        assert records.ids == ("s0",)
