#!/usr/bin/env python3

"""V4-5 ensemble profile and multi-output helper tests.

Covers :class:`EnsembleProfile` plus the pure helpers in
:mod:`confflow.execution.multi_output` with in-memory fixtures only:

- ensemble name and the exact registry contract string;
- deterministic member identity (``member_index``, never insertion order);
- fail-closed duplicate indexes and the empty-ensemble diagnostic;
- pinned non-dedup of identical geometries under distinct ids;
- per-member energy subjects, lineage, and artifact passthrough;
- multi-parent lineage propagation and disagreement rules;
- structure ordering, uniqueness validation, and the restart-subject
  classifier including the never-copy-to-all-outputs rule.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    FrozenDict,
    ResourceRequest,
    Unit,
)
from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.domain.errors import DomainError
from confflow.domain.structure import StructureRecord, StructureSet
from confflow.execution.multi_output import (
    order_item_structures,
    resolve_multi_output_restart_subjects,
    validate_multi_output_uniqueness,
)
from confflow.execution.native import (
    GeometryOutput,
    NativeEnsembleMember,
    NativeResult,
    ParsedGeometry,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.output_identity import (
    CONFORMER_ROLE,
    conformer_output_id,
    multi_parent_lineage,
)
from confflow.execution.profile_ensemble import (
    DUPLICATE_ENSEMBLE_MEMBER_CODE,
    EMPTY_ENSEMBLE_CODE,
    ENSEMBLE_PROFILE_CONTRACT,
    NATIVE_TERMINATION_CODE,
    EnsembleProfile,
)
from confflow.execution.profiles import (
    GeometrySemantics,
    ProfileContext,
    ProfileOutput,
)

STEP_ID = "s_goat"
WORK_ITEM_ID = "wi:s_goat:item0"
LOGICAL_KEY = "s_goat:item0"
KEYWORD = "GOAT conformer search"

_WATER_ATOMS = ("O", "H", "H")
_SEED_COORDS = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0))


def seed_structure(**overrides: Any) -> StructureRecord:
    """Build the deterministic ensemble seed structure."""
    from tests.v4._builders import structure

    params: dict[str, Any] = {
        "structure_id": "seed-0",
        "charge": 0,
        "multiplicity": 1,
        "lineage_root_id": "root-seed",
        "group_key": "grp-seed",
    }
    params.update(overrides)
    return structure(params.pop("structure_id"), **params)


def member_coords(offset: float) -> tuple[tuple[float, float, float], ...]:
    """Shift the seed geometry rigidly by *offset* on x."""
    return tuple((x + offset, y, z) for x, y, z in _SEED_COORDS)


def member(
    index: int,
    *,
    offset: float = 0.0,
    energy: float | None = None,
) -> NativeEnsembleMember:
    """Build one parsed native ensemble member."""
    return NativeEnsembleMember(
        member_index=index,
        geometry=ParsedGeometry(atoms=_WATER_ATOMS, coordinates=member_coords(offset)),
        energy_hartree=energy,
    )


def resolved_inputs(**overrides: Any) -> ResolvedCalculationInputs:
    """Build resolved calculation inputs over the seed fixture."""
    params: dict[str, Any] = {
        "structure": seed_structure(),
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
    members: tuple[NativeEnsembleMember, ...],
    *,
    terminated: bool = True,
) -> NativeResult:
    """Build parser facts carrying ensemble members and no final geometry."""
    return NativeResult(
        program=ProgramName.ORCA,
        terminated_normally=terminated,
        geometry_output=GeometryOutput.NONE,
        final_geometry=None,
        energies_hartree=FrozenDict({}),
        frequencies_cm=(),
        native_metadata=FrozenDict({}),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="goat.log",
        ensemble_members=members,
    )


def apply_profile(
    result: NativeResult,
    *,
    inputs: ResolvedCalculationInputs | None = None,
    artifacts: ArtifactSet | None = None,
) -> ProfileOutput:
    """Apply the ensemble profile to parser facts."""
    profile = EnsembleProfile()
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


def three_members() -> tuple[NativeEnsembleMember, ...]:
    """Build three members with distinct geometries and energies."""
    return (
        member(0, offset=0.01, energy=-76.40),
        member(1, offset=0.02, energy=-76.41),
        member(2, offset=0.03, energy=-76.42),
    )


def artifact(artifact_id: str, role: str, subject: str | None) -> ArtifactRef:
    """Build a discovered artifact bound to *subject*."""
    return ArtifactRef(
        id=artifact_id,
        role=role,
        locator=ArtifactLocator.run_relative(f"steps/s_goat/item0/{artifact_id}.dat"),
        subject_structure_id=subject,
        producer_step_id=STEP_ID,
        producer_work_item_id=WORK_ITEM_ID,
    )


def diagnostics_by_code(output: ProfileOutput, code: str) -> list[Any]:
    """Return output diagnostics matching *code*."""
    return [item for item in output.diagnostics if item.code == code]


class TestProfileContract:
    """Profile name and exact contract string."""

    def test_name_and_contract(self) -> None:
        profile = EnsembleProfile()
        assert profile.name == "ensemble"
        assert profile.contract_version == ENSEMBLE_PROFILE_CONTRACT
        assert ENSEMBLE_PROFILE_CONTRACT == "confflow.contract.result_profile.ensemble.v1"

    def test_contract_matches_registry(self) -> None:
        from confflow.execution import default_registry

        spec = default_registry().profile("ensemble")
        assert spec.contract_version == ENSEMBLE_PROFILE_CONTRACT

    def test_geometry_semantics_is_produced(self) -> None:
        output = apply_profile(native_result(three_members()))
        assert output.geometry_semantics is GeometrySemantics.PRODUCED


class TestMemberIdentity:
    """Native member index drives identity and order, never insertion."""

    def test_roles_ordinals_and_ids(self) -> None:
        output = apply_profile(native_result(three_members()))
        assert output.structures.ids == (
            conformer_output_id(LOGICAL_KEY, 0),
            conformer_output_id(LOGICAL_KEY, 1),
            conformer_output_id(LOGICAL_KEY, 2),
        )
        for position, record in enumerate(output.structures):
            assert record.role == CONFORMER_ROLE == "conformer"
            assert record.ordinal == position
            assert record.metadata["member_index"] == position
            assert record.id == f"{LOGICAL_KEY}:structure:{CONFORMER_ROLE}:{position}"

    def test_order_follows_member_index_not_insertion(self) -> None:
        members = (member(2, offset=0.03), member(0, offset=0.01), member(1, offset=0.02))
        output = apply_profile(native_result(members))
        assert [record.ordinal for record in output.structures] == [0, 1, 2]
        assert [record.metadata["member_index"] for record in output.structures] == [0, 1, 2]
        assert output.structures[0].coordinates == member_coords(0.01)
        assert output.structures[1].coordinates == member_coords(0.02)
        assert output.structures[2].coordinates == member_coords(0.03)

    def test_deterministic_ids_across_retry(self) -> None:
        first = apply_profile(native_result(three_members()))
        second = apply_profile(native_result(three_members()))
        assert first.structures.ids == second.structures.ids

    def test_no_chemistry_classification_in_roles(self) -> None:
        output = apply_profile(native_result(three_members()))
        for record in output.structures:
            assert record.role is not None
            assert "reactant" not in record.role
            assert "product" not in record.role

    def test_lineage_and_charge_from_seed(self) -> None:
        output = apply_profile(native_result(three_members()))
        for record in output.structures:
            assert record.parent_ids == ("seed-0",)
            assert record.lineage_root_id == "root-seed"
            assert record.group_key == "grp-seed"
            assert record.source_step_id == STEP_ID
            assert record.source_work_item_id == WORK_ITEM_ID
            assert record.charge == 0
            assert record.multiplicity == 1

    def test_group_key_falls_back_to_seed_id(self) -> None:
        seed = seed_structure(group_key=None)
        output = apply_profile(
            native_result(three_members()), inputs=resolved_inputs(structure=seed)
        )
        for record in output.structures:
            assert record.group_key == "seed-0"

    def test_identical_geometries_are_not_deduped(self) -> None:
        members = (member(0, offset=0.05), member(1, offset=0.05))
        output = apply_profile(native_result(members))
        assert len(output.structures) == 2
        assert output.structures[0].id != output.structures[1].id
        assert output.structures[0].geometry_digest == output.structures[1].geometry_digest


class TestMemberEnergies:
    """Per-member energy subjects and provenance."""

    def test_energy_subject_is_member_id(self) -> None:
        output = apply_profile(native_result(three_members()))
        assert len(output.results) == 3
        for position, item in enumerate(output.results):
            assert item.kind == "energy"
            assert item.unit is Unit.HARTREE
            assert item.subject_structure_id == conformer_output_id(LOGICAL_KEY, position)

    def test_missing_energy_skips_only_that_member(self) -> None:
        members = (member(0, offset=0.01, energy=-76.40), member(1, offset=0.02))
        output = apply_profile(native_result(members))
        assert len(output.results) == 1
        assert output.results[0].subject_structure_id == conformer_output_id(LOGICAL_KEY, 0)

    def test_result_provenance(self) -> None:
        output = apply_profile(native_result(three_members()))
        for item in output.results:
            assert item.source_step_id == STEP_ID
            assert item.source_work_item_id == WORK_ITEM_ID
            assert item.provenance is not None
            assert item.provenance.program == ProgramName.ORCA.value
            assert item.provenance.method == KEYWORD
            assert item.provenance.adapter == ENSEMBLE_PROFILE_CONTRACT


class TestEnsembleDiagnostics:
    """Termination transport, empty ensemble, and duplicate fail-closed."""

    def test_terminated_true_is_info(self) -> None:
        output = apply_profile(native_result(three_members()))
        facts = diagnostics_by_code(output, NATIVE_TERMINATION_CODE)
        assert len(facts) == 1
        assert facts[0].details["terminated"] is True
        assert facts[0].severity is DiagnosticSeverity.INFO

    def test_terminated_false_is_error(self) -> None:
        output = apply_profile(native_result(three_members(), terminated=False))
        facts = diagnostics_by_code(output, NATIVE_TERMINATION_CODE)
        assert len(facts) == 1
        assert facts[0].is_error

    def test_empty_ensemble_fails_closed(self) -> None:
        output = apply_profile(native_result(()))
        assert output.structures.is_empty
        assert output.results.is_empty
        errors = diagnostics_by_code(output, EMPTY_ENSEMBLE_CODE)
        assert len(errors) == 1
        assert errors[0].is_error
        assert errors[0].code == "empty_ensemble"

    def test_duplicate_member_index_fails_closed(self) -> None:
        members = (member(1, offset=0.01), member(1, offset=0.02))
        output = apply_profile(native_result(members))
        assert output.structures.is_empty
        assert output.results.is_empty
        errors = diagnostics_by_code(output, DUPLICATE_ENSEMBLE_MEMBER_CODE)
        assert len(errors) == 1
        assert errors[0].is_error
        assert errors[0].code == "duplicate_ensemble_member"

    def test_artifact_passthrough_on_failure_paths(self) -> None:
        discovered = ArtifactSet((artifact("rep-0", "ensemble_report", None),))
        output = apply_profile(native_result(()), artifacts=discovered)
        assert output.artifacts.ids == ("rep-0",)


class TestArtifactPassthrough:
    """Discovered artifacts cross the profile untouched."""

    def test_report_and_trajectory_untouched(self) -> None:
        discovered = ArtifactSet(
            (
                artifact("rep-0", "ensemble_report", None),
                artifact("traj-0", "trajectory", "seed-0"),
            )
        )
        output = apply_profile(native_result(three_members()), artifacts=discovered)
        assert output.artifacts.ids == discovered.ids
        assert tuple(output.artifacts) == tuple(discovered)


class TestMultiParentLineage:
    """Agreement propagates; disagreement yields no root or group."""

    def test_shared_root_and_group_propagate(self) -> None:
        parents = (
            seed_structure(structure_id="r-0", lineage_root_id="root-x", group_key="g-x"),
            seed_structure(structure_id="p-0", lineage_root_id="root-x", group_key="g-x"),
        )
        parent_ids, root, group = multi_parent_lineage(parents)
        assert parent_ids == ("r-0", "p-0")
        assert root == "root-x"
        assert group == "g-x"

    def test_disagreeing_roots_yield_none(self) -> None:
        parents = (
            seed_structure(structure_id="r-0", lineage_root_id="root-a", group_key="g-x"),
            seed_structure(structure_id="p-0", lineage_root_id="root-b", group_key="g-x"),
        )
        parent_ids, root, group = multi_parent_lineage(parents)
        assert parent_ids == ("r-0", "p-0")
        assert root is None
        assert group == "g-x"

    def test_disagreeing_groups_yield_none(self) -> None:
        parents = (
            seed_structure(structure_id="r-0", lineage_root_id="root-x", group_key="g-a"),
            seed_structure(structure_id="p-0", lineage_root_id="root-x", group_key="g-b"),
        )
        _, root, group = multi_parent_lineage(parents)
        assert root == "root-x"
        assert group is None

    def test_empty_parents_raise(self) -> None:
        with pytest.raises(ValueError):
            multi_parent_lineage(())


class TestOrderItemStructures:
    """Presentation ordering by role then ordinal, stable."""

    def test_forward_sorts_before_reverse(self) -> None:
        from confflow.execution.output_identity import (
            PATH_ENDPOINT_FORWARD_ROLE,
            PATH_ENDPOINT_REVERSE_ROLE,
        )

        seed = seed_structure()
        reverse = StructureRecord(
            id="item:reverse",
            atoms=seed.atoms,
            coordinates=seed.coordinates,
            role=PATH_ENDPOINT_REVERSE_ROLE,
            ordinal=0,
        )
        forward = StructureRecord(
            id="item:forward",
            atoms=seed.atoms,
            coordinates=seed.coordinates,
            role=PATH_ENDPOINT_FORWARD_ROLE,
            ordinal=0,
        )
        ordered = order_item_structures(StructureSet((reverse, forward)))
        assert ordered.ids == ("item:forward", "item:reverse")

    def test_conformers_sort_by_member_index(self) -> None:
        seed = seed_structure()
        members = tuple(
            StructureRecord(
                id=f"item:c{index}",
                atoms=seed.atoms,
                coordinates=member_coords(0.01 * index),
                role=CONFORMER_ROLE,
                ordinal=index,
            )
            for index in (2, 0, 1)
        )
        ordered = order_item_structures(StructureSet(members))
        assert ordered.ids == ("item:c0", "item:c1", "item:c2")


class TestValidateMultiOutputUniqueness:
    """Duplicate ids fail closed with DomainError."""

    def test_unique_set_passes(self) -> None:
        output = apply_profile(native_result(three_members()))
        assert validate_multi_output_uniqueness(output.structures) is None

    def test_duplicate_ids_raise(self) -> None:
        seed = seed_structure()
        first = StructureRecord(
            id="dup", atoms=seed.atoms, coordinates=seed.coordinates, role=CONFORMER_ROLE
        )
        second = StructureRecord(
            id="dup",
            atoms=seed.atoms,
            coordinates=member_coords(0.09),
            role=CONFORMER_ROLE,
        )
        with pytest.raises(DomainError, match="duplicate multi-output structure id"):
            validate_multi_output_uniqueness((first, second))


class TestResolveMultiOutputRestartSubjects:
    """The restart-subject classifier, including the no-copy rule."""

    def test_subject_already_on_output_is_kept(self) -> None:
        forward_id = conformer_output_id(LOGICAL_KEY, 0)
        reverse_id = conformer_output_id(LOGICAL_KEY, 1)
        found = ArtifactSet(
            (
                artifact("chk-f", "checkpoint", forward_id),
                artifact("chk-r", "checkpoint", reverse_id),
            )
        )
        resolved = resolve_multi_output_restart_subjects(
            artifacts=found,
            output_ids=(forward_id, reverse_id),
            input_subject="seed-0",
        )
        assert resolved.by_id("chk-f").subject_structure_id == forward_id
        assert resolved.by_id("chk-r").subject_structure_id == reverse_id

    def test_multi_output_checkpoint_keeps_input_subject_without_copy(self) -> None:
        forward_id = conformer_output_id(LOGICAL_KEY, 0)
        reverse_id = conformer_output_id(LOGICAL_KEY, 1)
        found = ArtifactSet(
            (
                artifact("chk-0", "checkpoint", "seed-0"),
                artifact("log-0", "native_output", "seed-0"),
            )
        )
        resolved = resolve_multi_output_restart_subjects(
            artifacts=found,
            output_ids=(forward_id, reverse_id),
            input_subject="seed-0",
        )
        # Never fanned out to every output: same count, input subject kept.
        assert resolved.ids == ("chk-0", "log-0")
        assert resolved.by_id("chk-0").subject_structure_id == "seed-0"
        assert resolved.by_id("log-0").subject_structure_id == "seed-0"

    def test_single_output_checkpoint_resubjects_to_output(self) -> None:
        only_id = conformer_output_id(LOGICAL_KEY, 0)
        found = ArtifactSet((artifact("chk-0", "checkpoint", "seed-0"),))
        resolved = resolve_multi_output_restart_subjects(
            artifacts=found,
            output_ids=(only_id,),
            input_subject="seed-0",
        )
        assert resolved.by_id("chk-0").subject_structure_id == only_id

    def test_unrelated_artifacts_pass_through(self) -> None:
        only_id = conformer_output_id(LOGICAL_KEY, 0)
        found = ArtifactSet(
            (
                artifact("chk-9", "checkpoint", "other-0"),
                artifact("free-0", "native_output", None),
            )
        )
        resolved = resolve_multi_output_restart_subjects(
            artifacts=found,
            output_ids=(only_id,),
            input_subject="seed-0",
        )
        assert resolved.by_id("chk-9").subject_structure_id == "other-0"
        assert resolved.by_id("free-0").subject_structure_id is None

    def test_empty_outputs_keep_everything(self) -> None:
        found = ArtifactSet((artifact("chk-0", "checkpoint", "seed-0"),))
        resolved = resolve_multi_output_restart_subjects(
            artifacts=found,
            output_ids=(),
            input_subject="seed-0",
        )
        assert resolved.by_id("chk-0").subject_structure_id == "seed-0"
