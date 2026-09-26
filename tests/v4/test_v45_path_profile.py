#!/usr/bin/env python3

"""V4-5 path-endpoints result profile tests.

Covers :class:`PathEndpointsProfile` with in-memory fixtures only:

- profile name and the exact registry contract string;
- forward/reverse role correctness (direction only, never chemistry);
- deterministic ids across retry and shuffled parser endpoint order;
- lineage propagation (parents, root, group) with group fallback;
- per-endpoint energy results and provenance;
- termination transport plus the ``incomplete_path`` diagnostic, including
  single-endpoint emission and fail-closed duplicate directions;
- trajectory/artifact passthrough untouched.
"""

from __future__ import annotations

from typing import Any

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    FrozenDict,
    ResourceRequest,
    Unit,
)
from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.execution.native import (
    GeometryOutput,
    NativePathEndpoint,
    NativeResult,
    ParsedGeometry,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.output_identity import (
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
    endpoint_output_id,
)
from confflow.execution.profile_path_endpoints import (
    INCOMPLETE_PATH_CODE,
    NATIVE_TERMINATION_CODE,
    PATH_ENDPOINTS_PROFILE_CONTRACT,
    PathEndpointsProfile,
)
from confflow.execution.profiles import (
    GeometrySemantics,
    ProfileContext,
    ProfileOutput,
)

STEP_ID = "s_irc"
WORK_ITEM_ID = "wi:s_irc:item0"
LOGICAL_KEY = "s_irc:item0"
KEYWORD = "IRC B3LYP/6-31G*"

_WATER_ATOMS = ("O", "H", "H")
_DRIVING_COORDS = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0))
_FORWARD_COORDS = ((0.05, 0.0, 0.0), (0.81, 0.59, 0.0), (0.81, -0.59, 0.0))
_REVERSE_COORDS = ((-0.05, 0.0, 0.0), (0.71, 0.59, 0.0), (0.71, -0.59, 0.0))
_FORWARD_ENERGY = -76.401234
_REVERSE_ENERGY = -76.398765


def driving_structure(**overrides: Any) -> Any:
    """Build the deterministic driving (transition-state) structure."""
    from tests.v4._builders import structure

    params: dict[str, Any] = {
        "structure_id": "ts-0",
        "charge": 0,
        "multiplicity": 1,
        "lineage_root_id": "root-ts",
        "group_key": "grp-ts",
    }
    params.update(overrides)
    return structure(params.pop("structure_id"), **params)


def resolved_inputs(**overrides: Any) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs over the driving fixture."""
    params: dict[str, Any] = {
        "structure": driving_structure(),
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


def endpoint(
    direction: str,
    coords: tuple[tuple[float, float, float], ...] | None = None,
    *,
    energy: float | None = None,
    point_ordinal: int | None = None,
    converged: bool = True,
) -> NativePathEndpoint:
    """Build one parsed native path endpoint."""
    geometry = ParsedGeometry(atoms=_WATER_ATOMS, coordinates=coords or _DRIVING_COORDS)
    return NativePathEndpoint(
        direction=direction,
        geometry=geometry,
        point_ordinal=point_ordinal,
        energy_hartree=energy,
        converged=converged,
    )


def native_result(
    endpoints: tuple[NativePathEndpoint, ...],
    *,
    terminated: bool = True,
) -> NativeResult:
    """Build parser facts carrying path endpoints and no final geometry."""
    return NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=terminated,
        geometry_output=GeometryOutput.NONE,
        final_geometry=None,
        energies_hartree=FrozenDict({}),
        frequencies_cm=(),
        native_metadata=FrozenDict({}),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="irc.log",
        path_endpoints=endpoints,
    )


def apply_profile(
    result: NativeResult,
    *,
    inputs: ResolvedCalculationInputs | None = None,
    artifacts: ArtifactSet | None = None,
) -> ProfileOutput:
    """Apply the path-endpoints profile to parser facts."""
    profile = PathEndpointsProfile()
    return profile.apply(
        ProfileContext(
            work_item_id=WORK_ITEM_ID,
            step_id=STEP_ID,
            logical_key=LOGICAL_KEY,
            profile_name=profile.name,
            profile_version=profile.contract_version,
            native_result=result,
            inputs=inputs if inputs is not None else resolved_inputs(),
            discovered_artifacts=artifacts if artifacts is not None else ArtifactSet(),
        )
    )


def full_result() -> NativeResult:
    """Build parser facts with both endpoints and energies."""
    return native_result(
        (
            endpoint("forward", _FORWARD_COORDS, energy=_FORWARD_ENERGY, point_ordinal=12),
            endpoint("reverse", _REVERSE_COORDS, energy=_REVERSE_ENERGY, point_ordinal=3),
        )
    )


def diagnostics_by_code(output: ProfileOutput, code: str) -> list[Any]:
    """Return output diagnostics matching *code*."""
    return [item for item in output.diagnostics if item.code == code]


class TestProfileContract:
    """Profile name and exact contract string."""

    def test_name_and_contract(self) -> None:
        profile = PathEndpointsProfile()
        assert profile.name == "path_endpoints"
        assert profile.contract_version == PATH_ENDPOINTS_PROFILE_CONTRACT
        assert PATH_ENDPOINTS_PROFILE_CONTRACT == (
            "confflow.contract.result_profile.path_endpoints.v1"
        )

    def test_contract_matches_registry(self) -> None:
        from confflow.execution import default_registry

        spec = default_registry().profile("path_endpoints")
        assert spec.contract_version == PATH_ENDPOINTS_PROFILE_CONTRACT

    def test_geometry_semantics_is_produced(self) -> None:
        assert apply_profile(full_result()).geometry_semantics is GeometrySemantics.PRODUCED


class TestEndpointRoles:
    """Direction-only roles and deterministic identity."""

    def test_forward_reverse_roles(self) -> None:
        output = apply_profile(full_result())
        assert output.structures.ids == (
            endpoint_output_id(LOGICAL_KEY, "forward"),
            endpoint_output_id(LOGICAL_KEY, "reverse"),
        )
        forward = output.structures[0]
        reverse = output.structures[1]
        assert forward.role == PATH_ENDPOINT_FORWARD_ROLE == "path_endpoint_forward"
        assert reverse.role == PATH_ENDPOINT_REVERSE_ROLE == "path_endpoint_reverse"
        assert forward.ordinal == 0
        assert reverse.ordinal == 0
        assert forward.metadata["direction"] == "forward"
        assert reverse.metadata["direction"] == "reverse"
        assert forward.metadata["point_ordinal"] == 12
        assert reverse.metadata["point_ordinal"] == 3
        assert forward.metadata["converged"] is True
        assert reverse.metadata["converged"] is True

    def test_no_chemistry_classification_in_roles(self) -> None:
        output = apply_profile(full_result())
        for record in output.structures:
            assert record.role is not None
            assert "reactant" not in record.role
            assert "product" not in record.role

    def test_deterministic_ids_across_retry(self) -> None:
        first = apply_profile(full_result())
        second = apply_profile(full_result())
        assert first.structures.ids == second.structures.ids
        assert first.structures.ids == (
            f"{LOGICAL_KEY}:structure:{PATH_ENDPOINT_FORWARD_ROLE}:0",
            f"{LOGICAL_KEY}:structure:{PATH_ENDPOINT_REVERSE_ROLE}:0",
        )

    def test_shuffled_parser_order_gives_identical_outputs(self) -> None:
        ordered = apply_profile(full_result())
        shuffled = apply_profile(
            native_result(
                (
                    endpoint("reverse", _REVERSE_COORDS, energy=_REVERSE_ENERGY, point_ordinal=3),
                    endpoint("forward", _FORWARD_COORDS, energy=_FORWARD_ENERGY, point_ordinal=12),
                )
            )
        )
        assert shuffled.structures.ids == ordered.structures.ids
        assert [record.role for record in shuffled.structures] == [
            record.role for record in ordered.structures
        ]
        assert shuffled.structures[0].coordinates == _FORWARD_COORDS
        assert shuffled.structures[1].coordinates == _REVERSE_COORDS
        assert [item.subject_structure_id for item in shuffled.results] == [
            item.subject_structure_id for item in ordered.results
        ]

    def test_geometries_come_from_endpoints(self) -> None:
        output = apply_profile(full_result())
        assert output.structures[0].coordinates == _FORWARD_COORDS
        assert output.structures[1].coordinates == _REVERSE_COORDS
        assert output.structures[0].atoms == _WATER_ATOMS


class TestLineage:
    """Parentage, lineage root, and group-key propagation."""

    def test_lineage_from_driving_input(self) -> None:
        output = apply_profile(full_result())
        for record in output.structures:
            assert record.parent_ids == ("ts-0",)
            assert record.lineage_root_id == "root-ts"
            assert record.group_key == "grp-ts"
            assert record.source_step_id == STEP_ID
            assert record.source_work_item_id == WORK_ITEM_ID

    def test_group_key_falls_back_to_driving_id(self) -> None:
        seed = driving_structure(group_key=None)
        output = apply_profile(full_result(), inputs=resolved_inputs(structure=seed))
        for record in output.structures:
            assert record.group_key == "ts-0"

    def test_charge_and_multiplicity_from_inputs(self) -> None:
        output = apply_profile(full_result(), inputs=resolved_inputs(charge=1, multiplicity=2))
        for record in output.structures:
            assert record.charge == 1
            assert record.multiplicity == 2


class TestEnergyResults:
    """Per-endpoint energy emission and provenance."""

    def test_energy_per_endpoint(self) -> None:
        output = apply_profile(full_result())
        assert len(output.results) == 2
        forward_id = endpoint_output_id(LOGICAL_KEY, "forward")
        reverse_id = endpoint_output_id(LOGICAL_KEY, "reverse")
        assert output.results[0].kind == "energy"
        assert output.results[0].value == _FORWARD_ENERGY
        assert output.results[0].unit is Unit.HARTREE
        assert output.results[0].subject_structure_id == forward_id
        assert output.results[1].subject_structure_id == reverse_id
        assert output.results[1].value == _REVERSE_ENERGY

    def test_missing_energy_emits_no_result_for_subject(self) -> None:
        output = apply_profile(
            native_result(
                (
                    endpoint("forward", _FORWARD_COORDS, energy=_FORWARD_ENERGY),
                    endpoint("reverse", _REVERSE_COORDS, energy=None),
                )
            )
        )
        assert len(output.results) == 1
        assert output.results[0].subject_structure_id == endpoint_output_id(LOGICAL_KEY, "forward")

    def test_result_provenance(self) -> None:
        output = apply_profile(full_result())
        for item in output.results:
            assert item.source_step_id == STEP_ID
            assert item.source_work_item_id == WORK_ITEM_ID
            assert item.provenance is not None
            assert item.provenance.program == ProgramName.GAUSSIAN.value
            assert item.provenance.method == KEYWORD
            assert item.provenance.adapter == PATH_ENDPOINTS_PROFILE_CONTRACT
            assert item.provenance.step_id == STEP_ID
            assert item.provenance.work_item_id == WORK_ITEM_ID


class TestDiagnostics:
    """Termination transport and the incomplete-path rule."""

    def test_terminated_true_is_info(self) -> None:
        output = apply_profile(full_result())
        facts = diagnostics_by_code(output, NATIVE_TERMINATION_CODE)
        assert len(facts) == 1
        assert facts[0].details["terminated"] is True
        assert facts[0].severity is DiagnosticSeverity.INFO

    def test_terminated_false_is_error(self) -> None:
        output = apply_profile(native_result(full_result().path_endpoints, terminated=False))
        facts = diagnostics_by_code(output, NATIVE_TERMINATION_CODE)
        assert len(facts) == 1
        assert facts[0].details["terminated"] is False
        assert facts[0].is_error

    def test_complete_path_has_no_incomplete_diagnostic(self) -> None:
        assert diagnostics_by_code(apply_profile(full_result()), INCOMPLETE_PATH_CODE) == []

    def test_single_endpoint_emits_one_structure_plus_error(self) -> None:
        output = apply_profile(
            native_result((endpoint("forward", _FORWARD_COORDS, energy=_FORWARD_ENERGY),))
        )
        assert len(output.structures) == 1
        assert output.structures[0].role == PATH_ENDPOINT_FORWARD_ROLE
        assert output.structures[0].id == endpoint_output_id(LOGICAL_KEY, "forward")
        assert len(output.results) == 1
        errors = diagnostics_by_code(output, INCOMPLETE_PATH_CODE)
        assert len(errors) == 1
        assert errors[0].is_error
        assert errors[0].code == "incomplete_path"
        assert errors[0].step_id == STEP_ID
        assert errors[0].work_item_id == WORK_ITEM_ID

    def test_empty_endpoints_emit_error(self) -> None:
        output = apply_profile(native_result(()))
        assert output.structures.is_empty
        assert output.results.is_empty
        errors = diagnostics_by_code(output, INCOMPLETE_PATH_CODE)
        assert len(errors) == 1
        assert errors[0].is_error

    def test_duplicate_direction_fails_closed(self) -> None:
        output = apply_profile(
            native_result(
                (
                    endpoint("forward", _FORWARD_COORDS, energy=_FORWARD_ENERGY),
                    endpoint("forward", _REVERSE_COORDS, energy=_REVERSE_ENERGY),
                )
            )
        )
        assert output.structures.is_empty
        assert output.results.is_empty
        errors = diagnostics_by_code(output, INCOMPLETE_PATH_CODE)
        assert len(errors) == 1
        assert errors[0].is_error


class TestArtifactPassthrough:
    """Discovered artifacts cross the profile untouched."""

    def test_trajectory_and_checkpoint_untouched(self) -> None:
        discovered = ArtifactSet(
            (
                ArtifactRef(
                    id="traj-0",
                    role="trajectory",
                    locator=ArtifactLocator.run_relative("steps/s_irc/item0/traj.h5"),
                    subject_structure_id="ts-0",
                    producer_step_id=STEP_ID,
                    producer_work_item_id=WORK_ITEM_ID,
                ),
                ArtifactRef(
                    id="chk-0",
                    role="checkpoint",
                    locator=ArtifactLocator.run_relative("steps/s_irc/item0/job.chk"),
                    subject_structure_id="ts-0",
                    producer_step_id=STEP_ID,
                ),
            )
        )
        output = apply_profile(full_result(), artifacts=discovered)
        assert output.artifacts.ids == discovered.ids
        assert tuple(output.artifacts) == tuple(discovered)
        assert output.artifacts.by_id("traj-0").subject_structure_id == "ts-0"
        assert output.artifacts.by_id("chk-0").subject_structure_id == "ts-0"
