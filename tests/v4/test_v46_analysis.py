#!/usr/bin/env python3

"""V4-6 analysis runtime: executor, assignment, policies, registry.

Covers the Agent A runtime surface: models, the pure
:class:`AnalysisExecutor` (driven by a stub energy model implementing
the ``EnergyModel`` compute seam), explicit endpoint assignment plus
conflict handling, partial policies, the capability registry, and the
no-subprocess/no-adapter AST self-check.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from confflow.analysis.executor import AnalysisExecutor, EnergyModel
from confflow.analysis.grouping import build_reaction_groups
from confflow.analysis.models import (
    ANALYSIS_FAILURE_CODES,
    AnalysisDefinition,
    AnalysisError,
    AnalysisInputs,
    AnalysisStepResult,
    ComputedGroup,
    ReactionGroup,
)
from confflow.analysis.registry import (
    ANALYSIS_CAPABILITIES,
    ANALYSIS_CAPABILITY_INDEX,
    REACTION_PROFILE_CAPABILITY,
    REACTION_PROFILE_CONTRACT_VERSION,
    AnalysisCapabilitySpec,
    capabilities,
    find_analysis_capability,
    require_analysis_capability,
    validate_analysis_definition,
)
from confflow.domain._immutable import FrozenDict
from confflow.domain.artifact import ArtifactSet
from confflow.domain.binding import PartialConsumption
from confflow.domain.diagnostics import Diagnostic, DiagnosticSeverity
from confflow.domain.result import Provenance, ResultSet, ScientificResult
from confflow.domain.structure import StructureRecord, StructureSet
from confflow.domain.units import Unit
from confflow.execution.contracts import ExecutorCapability
from confflow.execution.output_identity import (
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
)

_ATOMS = ("O", "H", "H")
_BASE_COORDS = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0))

ANALYSIS_ROOT = Path(__file__).resolve().parents[2] / "confflow" / "analysis"
OWNED_MODULES = ("models.py", "executor.py", "grouping.py", "registry.py")


def _coords(offset: float = 0.0) -> tuple[tuple[float, float, float], ...]:
    """Return deterministic test coordinates shifted by *offset*."""
    return tuple((x + offset, y + offset, z + offset) for x, y, z in _BASE_COORDS)


def _structure(
    record_id: str,
    *,
    group_key: str | None = None,
    role: str | None = None,
    parent_ids: tuple[str, ...] = (),
    offset: float = 0.0,
) -> StructureRecord:
    """Build one deterministic test structure record."""
    return StructureRecord(
        id=record_id,
        atoms=_ATOMS,
        coordinates=_coords(offset),
        group_key=group_key,
        role=role,
        parent_ids=parent_ids,
    )


def _triple(
    prefix: str, group_key: str, *, base_offset: float = 0.0
) -> tuple[StructureRecord, StructureRecord, StructureRecord]:
    """Build one transition-state plus endpoint triple."""
    transition = _structure(f"{prefix}-ts", group_key=group_key, role=None, offset=base_offset)
    forward = _structure(
        f"{prefix}-fwd",
        group_key=group_key,
        role=PATH_ENDPOINT_FORWARD_ROLE,
        parent_ids=(transition.id,),
        offset=base_offset + 1.0,
    )
    reverse = _structure(
        f"{prefix}-rev",
        group_key=group_key,
        role=PATH_ENDPOINT_REVERSE_ROLE,
        parent_ids=(transition.id,),
        offset=base_offset + 2.0,
    )
    return (transition, forward, reverse)


def _energy(value: float, subject: str | None) -> ScientificResult:
    """Build one Hartree energy result bound to *subject*."""
    return ScientificResult(
        kind="energy",
        value=value,
        unit=Unit.HARTREE,
        subject_structure_id=subject,
    )


class StubEnergyModel:
    """Test-only energy model implementing the compute seam in Hartree.

    Resolves ``energy``-kind results per triple subject and emits two
    barriers plus a ``reaction_profile`` payload.  Missing legs fail
    the group closed with ``analysis_energy_missing``; nothing is ever
    guessed across subjects or kinds.
    """

    def __init__(self, *, electronic_kind: str = "energy") -> None:
        """Record configuration and the groups seen across calls."""
        self.electronic_kind = electronic_kind
        self.seen: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        """Return the stub policy payload."""
        return {"mode": "stub_direct_hartree", "electronic_selector": self.electronic_kind}

    def compute(
        self,
        group: ReactionGroup,
        lookup: Mapping[str, ResultSet],
        *,
        analysis_step_id: str | None = None,
        endpoint_assignment: Mapping[str, str] | None = None,
    ) -> ComputedGroup:
        """Aggregate one group into barriers and a profile payload."""
        self.seen.append(
            {
                "group_key": group.group_key,
                "assignment": group.assignment,
                "subjects": sorted(lookup),
                "endpoint_assignment": dict(endpoint_assignment or {}),
            }
        )
        diagnostics: list[Diagnostic] = []
        values: dict[str, float] = {}
        slots = (
            ("ts", group.ts_structure_id),
            ("forward", group.forward_endpoint_id),
            ("reverse", group.reverse_endpoint_id),
        )
        for slot, subject in slots:
            pool = lookup.get(subject) if subject is not None else None
            match: ScientificResult | None = None
            if pool is not None:
                for candidate in pool:
                    if (
                        candidate.kind == self.electronic_kind
                        and candidate.subject_structure_id == subject
                    ):
                        match = candidate
                        break
            if match is None:
                diagnostics.append(
                    Diagnostic(
                        code="analysis_energy_missing",
                        message=f"stub is missing {self.electronic_kind} for {slot}",
                        severity=DiagnosticSeverity.ERROR,
                        details=FrozenDict(
                            {
                                "reason": "energy_missing",
                                "node": slot,
                                "subject_structure_id": subject,
                                "group_key": group.group_key,
                            }
                        ),
                    )
                )
            else:
                values[slot] = float(match.value)
        if diagnostics or len(values) != 3:
            return ComputedGroup(results=(), diagnostics=tuple(diagnostics))
        barrier_forward = values["ts"] - values["forward"]
        barrier_reverse = values["ts"] - values["reverse"]
        nodes = {
            "ts": group.ts_structure_id,
            "forward": group.forward_endpoint_id,
            "reverse": group.reverse_endpoint_id,
        }
        provenance = Provenance(
            adapter="test.stub_energy_model.v1",
            step_id=analysis_step_id,
            metadata=FrozenDict(
                {
                    "formula": "stub_barrier=G_TS-G_endpoint",
                    "group_key": group.group_key,
                    "energy_model": self.to_dict(),
                }
            ),
        )
        assert group.ts_structure_id is not None
        results = (
            ScientificResult(
                kind="barrier_forward_endpoint",
                value=barrier_forward,
                unit=Unit.HARTREE,
                subject_structure_id=group.ts_structure_id,
                provenance=provenance,
            ),
            ScientificResult(
                kind="barrier_reverse_endpoint",
                value=barrier_reverse,
                unit=Unit.HARTREE,
                subject_structure_id=group.ts_structure_id,
                provenance=provenance,
            ),
            ScientificResult(
                kind="reaction_profile",
                value={
                    "group_key": group.group_key,
                    "nodes": dict(nodes),
                    "barriers": {
                        "forward_endpoint": barrier_forward,
                        "reverse_endpoint": barrier_reverse,
                    },
                    "assignment": dict(endpoint_assignment or {}),
                    "source_result_ids": list(group.source_result_ids),
                    "energy_model": self.to_dict(),
                },
                subject_structure_id=group.ts_structure_id,
                provenance=provenance,
            ),
        )
        return ComputedGroup(results=results, diagnostics=())


def _definition(model: EnergyModel, **overrides: Any) -> AnalysisDefinition:
    """Build a reaction-profile definition around *model*."""
    fields: dict[str, Any] = {
        "kind": "reaction_profile",
        "energy_model": model,
        "params": FrozenDict({}),
    }
    fields.update(overrides)
    return AnalysisDefinition(**fields)


def _inputs(
    structures: tuple[StructureRecord, ...],
    results: tuple[ScientificResult, ...],
    definition: AnalysisDefinition,
) -> AnalysisInputs:
    """Build single-port analysis inputs."""
    return AnalysisInputs(
        structures=FrozenDict({"structures": StructureSet(structures)}),
        results=FrozenDict({"results": ResultSet(results)}),
        definition=definition,
    )


def _codes(diagnostics: tuple[Diagnostic, ...]) -> list[str]:
    """Return diagnostic codes in order."""
    return [item.code for item in diagnostics]


class TestExecutorIdentity:
    """The executor exposes its stable capability contract."""

    def test_name_contract_capability(self) -> None:
        assert AnalysisExecutor.name == "analysis"
        assert AnalysisExecutor.contract_version == "confflow.contract.executor.analysis.v1"
        assert AnalysisExecutor.capability is ExecutorCapability.ANALYSIS

    def test_rejects_untyped_inputs(self) -> None:
        executor = AnalysisExecutor()
        with pytest.raises(AnalysisError) as excinfo:
            executor.execute("nope")  # type: ignore[arg-type]
        assert excinfo.value.code == "analysis_invalid_definition"

    def test_rejects_unknown_kind(self) -> None:
        executor = AnalysisExecutor()
        definition = _definition(StubEnergyModel(), kind="no-such-capability")
        with pytest.raises(AnalysisError) as excinfo:
            executor.execute(_inputs((), (), definition))
        assert excinfo.value.code == "analysis_unknown_kind"


class TestReactionGroupModel:
    """ReactionGroup defaults, validation, and payload shape."""

    def test_defaults(self) -> None:
        group = ReactionGroup(
            group_key="rxn-0",
            ts_structure_id="ts-0",
            forward_endpoint_id="fwd-0",
            reverse_endpoint_id="rev-0",
        )
        assert group.source_result_ids == ()
        assert group.assignment == "unassigned"
        assert group.diagnostics == ()
        assert group.is_complete
        assert group.ok

    def test_invalid_assignment_rejected(self) -> None:
        with pytest.raises(AnalysisError) as excinfo:
            ReactionGroup(
                group_key="rxn-0",
                ts_structure_id=None,
                forward_endpoint_id=None,
                reverse_endpoint_id=None,
                assignment="reactant",
            )
        assert excinfo.value.code == "analysis_invalid_definition"

    def test_to_dict_shape(self) -> None:
        group = ReactionGroup(
            group_key="rxn-0",
            ts_structure_id="ts-0",
            forward_endpoint_id="fwd-0",
            reverse_endpoint_id="rev-0",
            source_result_ids=("sha256:abc",),
        )
        payload = group.to_dict()
        assert payload["group_key"] == "rxn-0"
        assert payload["assignment"] == "unassigned"
        assert payload["source_result_ids"] == ["sha256:abc"]
        assert payload["diagnostics"] == []


class TestAnalysisDefinition:
    """Definitions carry explicit assignment, policy, and seam object."""

    def test_default_assignment_is_unassigned(self) -> None:
        definition = _definition(StubEnergyModel())
        assert dict(definition.endpoint_assignment) == {
            "forward": "unassigned",
            "reverse": "unassigned",
        }
        assert definition.assignment == "unassigned"
        assert definition.partial_policy is PartialConsumption.REQUIRE_COMPLETE

    def test_explicit_assignment(self) -> None:
        definition = _definition(
            StubEnergyModel(),
            endpoint_assignment=FrozenDict({"forward": "reactant", "reverse": "product"}),
        )
        assert definition.assignment == "explicit"

    def test_conflicting_assignment_raises_typed_error(self) -> None:
        with pytest.raises(AnalysisError) as excinfo:
            _definition(
                StubEnergyModel(),
                endpoint_assignment={"forward": "reactant", "reverse": "reactant"},
            )
        assert excinfo.value.code == "analysis_assignment_conflict"

    def test_both_unassigned_is_not_a_conflict(self) -> None:
        definition = _definition(
            StubEnergyModel(),
            endpoint_assignment={"forward": "unassigned", "reverse": "unassigned"},
        )
        assert definition.assignment == "unassigned"

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(AnalysisError) as excinfo:
            _definition(
                StubEnergyModel(),
                endpoint_assignment={"forward": "catalyst", "reverse": "product"},
            )
        assert excinfo.value.code == "analysis_invalid_definition"

    def test_unknown_slot_rejected(self) -> None:
        with pytest.raises(AnalysisError) as excinfo:
            _definition(
                StubEnergyModel(),
                endpoint_assignment={"sideways": "reactant"},
            )
        assert excinfo.value.code == "analysis_invalid_definition"

    def test_partial_policy_string_normalized(self) -> None:
        definition = _definition(StubEnergyModel(), partial_policy="accept_subset")
        assert definition.partial_policy is PartialConsumption.ACCEPT_SUBSET

    def test_invalid_partial_policy_rejected(self) -> None:
        with pytest.raises(AnalysisError) as excinfo:
            _definition(StubEnergyModel(), partial_policy="sometimes")
        assert excinfo.value.code == "analysis_invalid_definition"

    def test_missing_energy_model_rejected(self) -> None:
        with pytest.raises(AnalysisError) as excinfo:
            _definition(None)  # type: ignore[arg-type]
        assert excinfo.value.code == "analysis_invalid_definition"

    def test_semantic_digest_deterministic(self) -> None:
        first = _definition(StubEnergyModel())
        second = _definition(StubEnergyModel())
        assert first.semantic_digest() == second.semantic_digest()
        changed = _definition(
            StubEnergyModel(),
            endpoint_assignment={"forward": "reactant", "reverse": "product"},
        )
        assert changed.semantic_digest() != first.semantic_digest()


class TestAnalysisInputsModel:
    """Input ports are validated eagerly with typed errors."""

    def test_wrong_port_type_rejected(self) -> None:
        definition = _definition(StubEnergyModel())
        with pytest.raises(AnalysisError) as excinfo:
            AnalysisInputs(
                structures=FrozenDict({"structures": ResultSet()}),  # type: ignore[dict-item]
                results=FrozenDict({}),
                definition=definition,
            )
        assert excinfo.value.code == "analysis_invalid_definition"

    def test_missing_definition_rejected(self) -> None:
        with pytest.raises(AnalysisError) as excinfo:
            AnalysisInputs(
                structures=FrozenDict({}),
                results=FrozenDict({}),
                definition=None,  # type: ignore[arg-type]
            )
        assert excinfo.value.code == "analysis_invalid_definition"


class TestExecutorEndToEnd:
    """The stub model drives barriers and profiles through the executor."""

    def _complete_inputs(
        self, model: StubEnergyModel, definition: AnalysisDefinition
    ) -> AnalysisInputs:
        transition, forward, reverse = _triple("t", "rxn-0")
        return _inputs(
            (transition, forward, reverse),
            (
                _energy(-100.0, transition.id),
                _energy(-100.5, forward.id),
                _energy(-100.2, reverse.id),
            ),
            definition,
        )

    def test_barriers_and_profile(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model)
        step = AnalysisExecutor().execute(self._complete_inputs(model, definition))
        assert step.ok
        assert step.diagnostics == ()
        assert step.structures.is_empty
        assert step.artifacts == ArtifactSet()
        assert isinstance(step, AnalysisStepResult)
        by_kind = {result.kind: result for result in step.results}
        assert set(by_kind) == {
            "barrier_forward_endpoint",
            "barrier_reverse_endpoint",
            "reaction_profile",
        }
        assert by_kind["barrier_forward_endpoint"].value == pytest.approx(0.5)
        assert by_kind["barrier_reverse_endpoint"].value == pytest.approx(0.2)
        for kind in ("barrier_forward_endpoint", "barrier_reverse_endpoint"):
            assert by_kind[kind].unit is Unit.HARTREE
            assert by_kind[kind].subject_structure_id == "t-ts"
        for result in step.results:
            assert result.subject_structure_id == "t-ts"
        profile = by_kind["reaction_profile"].value
        assert profile["group_key"] == "rxn-0"
        assert profile["nodes"] == {"ts": "t-ts", "forward": "t-fwd", "reverse": "t-rev"}
        assert len(profile["source_result_ids"]) == 3

    def test_results_sort_deterministically(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model)
        first: list[StructureRecord] = []
        second: list[ScientificResult] = []
        for prefix, offset in (("a", 0.0), ("b", 10.0)):
            transition, forward, reverse = _triple(prefix, f"rxn-{prefix}", base_offset=offset)
            first.extend((transition, forward, reverse))
            second.extend(
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                )
            )
        step = AnalysisExecutor().execute(_inputs(tuple(first), tuple(second), definition))
        assert step.ok
        keys = [(result.subject_structure_id or "", result.kind) for result in step.results]
        assert keys == sorted(keys)

    def test_explicit_definition_overrides_inputs(self) -> None:
        model = StubEnergyModel()
        stored = _definition(model, partial_policy="require_complete")
        transition, forward, _ = _triple("t", "rxn-0")
        partial = _inputs(
            (transition, forward),
            (_energy(-100.0, transition.id), _energy(-100.5, forward.id)),
            stored,
        )
        override = _definition(model, partial_policy="accept_subset")
        step = AnalysisExecutor().execute(partial, override)
        assert step.results.is_empty
        assert any(item.is_error for item in step.diagnostics)


class TestAssignmentSemantics:
    """Forward/reverse become chemistry only through explicit mapping."""

    def test_default_groups_stay_unassigned(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model)
        transition, forward, reverse = _triple("t", "rxn-0")
        step = AnalysisExecutor().execute(
            _inputs(
                (transition, forward, reverse),
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                ),
                definition,
            )
        )
        assert step.ok
        assert [seen["assignment"] for seen in model.seen] == ["unassigned"]

    def test_explicit_mapping_marks_groups_explicit(self) -> None:
        model = StubEnergyModel()
        definition = _definition(
            model,
            endpoint_assignment={"forward": "reactant", "reverse": "product"},
        )
        transition, forward, reverse = _triple("t", "rxn-0")
        step = AnalysisExecutor().execute(
            _inputs(
                (transition, forward, reverse),
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                ),
                definition,
            )
        )
        assert step.ok
        assert [seen["assignment"] for seen in model.seen] == ["explicit"]
        assert [seen["endpoint_assignment"] for seen in model.seen] == [
            {"forward": "reactant", "reverse": "product"}
        ]
        profile = step.results.by_kind("reaction_profile")[0]
        assert profile.value["assignment"] == {"forward": "reactant", "reverse": "product"}

    def test_grouping_never_assigns(self) -> None:
        transition, forward, reverse = _triple("t", "rxn-0")
        groups, _ = build_reaction_groups(StructureSet((transition, forward, reverse)), ResultSet())
        assert [group.assignment for group in groups] == ["unassigned"]


class TestPartialPolicies:
    """Partial groups fail the step or are omitted, never backfilled."""

    def _mixed_inputs(self, definition: AnalysisDefinition) -> AnalysisInputs:
        good = _triple("g", "rxn-good", base_offset=0.0)
        bad = _triple("b", "rxn-bad", base_offset=10.0)[:2]
        structures = (*good, *bad)
        results = (
            _energy(-100.0, good[0].id),
            _energy(-100.5, good[1].id),
            _energy(-100.2, good[2].id),
            _energy(-99.0, bad[0].id),
            _energy(-99.5, bad[1].id),
        )
        return _inputs(structures, results, definition)

    def test_require_complete_discards_everything(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model, partial_policy="require_complete")
        step = AnalysisExecutor().execute(self._mixed_inputs(definition))
        assert not step.ok
        assert step.results.is_empty
        assert "analysis_endpoint_missing" in _codes(step.diagnostics)

    def test_accept_subset_omits_failed_group(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model, partial_policy="accept_subset")
        step = AnalysisExecutor().execute(self._mixed_inputs(definition))
        assert not step.ok  # omissions stay visible as diagnostics
        assert "analysis_endpoint_missing" in _codes(step.diagnostics)
        subjects = {result.subject_structure_id for result in step.results}
        assert subjects == {"g-ts"}
        assert len(model.seen) == 1
        assert model.seen[0]["group_key"] == "rxn-good"

    def test_missing_energy_fails_group_closed(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model, partial_policy="accept_subset")
        transition, forward, reverse = _triple("t", "rxn-0")
        step = AnalysisExecutor().execute(
            _inputs(
                (transition, forward, reverse),
                (_energy(-100.0, transition.id), _energy(-100.5, forward.id)),
                definition,
            )
        )
        assert step.results.is_empty
        assert "analysis_energy_missing" in _codes(step.diagnostics)


class TestSubjectMismatchExecutor:
    """Unmatched subjects surface without corrupting matched groups."""

    def test_require_complete_fails_on_stray_result(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model, partial_policy="require_complete")
        transition, forward, reverse = _triple("t", "rxn-0")
        step = AnalysisExecutor().execute(
            _inputs(
                (transition, forward, reverse),
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                    _energy(-99.0, "ghost-structure"),
                ),
                definition,
            )
        )
        assert not step.ok
        assert step.results.is_empty
        assert "analysis_subject_mismatch" in _codes(step.diagnostics)

    def test_accept_subset_keeps_matched_groups(self) -> None:
        model = StubEnergyModel()
        definition = _definition(model, partial_policy="accept_subset")
        transition, forward, reverse = _triple("t", "rxn-0")
        step = AnalysisExecutor().execute(
            _inputs(
                (transition, forward, reverse),
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                    _energy(-99.0, "ghost-structure"),
                ),
                definition,
            )
        )
        assert not step.results.is_empty
        assert "analysis_subject_mismatch" in _codes(step.diagnostics)


class TestExecutorDeterminism:
    """Shuffled ports and positions give identical semantic payloads."""

    def test_shuffled_inputs_match(self) -> None:
        first_structures: list[StructureRecord] = []
        first_results: list[ScientificResult] = []
        for prefix, offset in (("a", 0.0), ("b", 10.0)):
            transition, forward, reverse = _triple(prefix, f"rxn-{prefix}", base_offset=offset)
            first_structures.extend((transition, forward, reverse))
            first_results.extend(
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                )
            )

        def _run(
            structures: tuple[StructureRecord, ...], results: tuple[ScientificResult, ...]
        ) -> AnalysisStepResult:
            model = StubEnergyModel()
            return AnalysisExecutor().execute(_inputs(structures, results, _definition(model)))

        ordered = _run(tuple(first_structures), tuple(first_results))
        shuffled = _run(tuple(reversed(first_structures)), tuple(reversed(first_results)))
        assert ordered.ok and shuffled.ok
        assert [item.value_digest for item in ordered.results] == [
            item.value_digest for item in shuffled.results
        ]
        assert _codes(ordered.diagnostics) == _codes(shuffled.diagnostics)


class TestComputeSeamFailures:
    """A crashing or dishonest model fails the group, never the process."""

    def test_raising_model_is_wrapped(self) -> None:
        class _Raising:
            def to_dict(self) -> dict[str, Any]:
                return {"mode": "raising"}

            def compute(self, *args: Any, **kwargs: Any) -> ComputedGroup:
                raise RuntimeError("boom")

        model = _Raising()
        assert isinstance(model, EnergyModel)
        definition = _definition(model)  # type: ignore[arg-type]
        transition, forward, reverse = _triple("t", "rxn-0")
        step = AnalysisExecutor().execute(
            _inputs(
                (transition, forward, reverse),
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                ),
                definition,
            )
        )
        assert step.results.is_empty
        assert "analysis_compute_failed" in _codes(step.diagnostics)

    def test_garbage_return_is_wrapped(self) -> None:
        class _Garbage:
            def to_dict(self) -> dict[str, Any]:
                return {"mode": "garbage"}

            def compute(self, *args: Any, **kwargs: Any) -> Any:
                return "not-a-computed-group"

        definition = _definition(_Garbage())  # type: ignore[arg-type]
        transition, forward, reverse = _triple("t", "rxn-0")
        step = AnalysisExecutor().execute(
            _inputs(
                (transition, forward, reverse),
                (
                    _energy(-100.0, transition.id),
                    _energy(-100.5, forward.id),
                    _energy(-100.2, reverse.id),
                ),
                definition,
            )
        )
        assert step.results.is_empty
        assert "analysis_compute_failed" in _codes(step.diagnostics)


class TestRegistry:
    """The capability registry advertises the closed vocabulary."""

    def test_reaction_profile_capability(self) -> None:
        assert REACTION_PROFILE_CAPABILITY == "reaction_profile"
        assert REACTION_PROFILE_CONTRACT_VERSION == "confflow.contract.analysis.reaction_profile.v1"
        assert len(ANALYSIS_CAPABILITIES) == 1
        spec = ANALYSIS_CAPABILITIES[0]
        assert spec.name == "reaction_profile"
        assert spec.contract_version == REACTION_PROFILE_CONTRACT_VERSION
        assert set(spec.energy_model_modes) == {"direct", "composite"}
        assert set(spec.energy_model_fallbacks) == {"none", "low_level"}
        assert set(spec.assignment_roles) == {"reactant", "product", "unassigned"}
        assert set(spec.partial_policies) == {"require_complete", "accept_subset"}
        schema = spec.params_schema.thaw()
        assert set(schema) == {"kind", "energy_model", "endpoint_assignment", "partial_policy"}

    def test_lookup_helpers(self) -> None:
        assert find_analysis_capability("reaction_profile") is not None
        assert find_analysis_capability("nope") is None
        assert (
            require_analysis_capability("reaction_profile").contract_version
            == REACTION_PROFILE_CONTRACT_VERSION
        )
        with pytest.raises(AnalysisError) as excinfo:
            require_analysis_capability("nope")
        assert excinfo.value.code == "analysis_unknown_kind"
        assert ANALYSIS_CAPABILITY_INDEX["reaction_profile"].name == "reaction_profile"

    def test_validate_definition(self) -> None:
        assert validate_analysis_definition(_definition(StubEnergyModel())) == ()
        diagnostics = validate_analysis_definition(_definition(StubEnergyModel(), kind="nope"))
        assert len(diagnostics) == 1
        assert diagnostics[0].code == "analysis_unknown_kind"

    def test_no_task_enums(self) -> None:
        tree = ast.parse((ANALYSIS_ROOT / "registry.py").read_text(encoding="utf-8"))
        enum_bases = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(getattr(base, "attr", getattr(base, "id", "")) == "Enum" for base in node.bases)
        ]
        assert enum_bases == []
        for marker in ("IRC", "QST", "NEB", "GOAT"):
            assert marker not in (ANALYSIS_ROOT / "registry.py").read_text(encoding="utf-8")

    def test_spec_to_dict(self) -> None:
        payload = ANALYSIS_CAPABILITIES[0].to_dict()
        assert payload["name"] == "reaction_profile"
        assert isinstance(payload["params_schema"], dict)

    def test_capabilities_seam_shape(self) -> None:
        assert capabilities() == (
            {
                "capability": "reaction_profile",
                "contract_version": "confflow.contract.analysis.reaction_profile.v1",
            },
        )


class TestFailureCodeCoverage:
    """Every frozen failure code is reachable through the runtime."""

    def test_all_six_codes_surface(self) -> None:
        seen: set[str] = set()
        transition = _structure("ts-0", group_key="rxn-0")
        groups, _ = build_reaction_groups(StructureSet((transition,)), ResultSet())
        seen.update(_codes(groups[0].diagnostics))
        forward = _structure(
            "fwd-0", group_key="rxn-1", role=PATH_ENDPOINT_FORWARD_ROLE, parent_ids=("x",)
        )
        reverse = _structure(
            "rev-0", group_key="rxn-1", role=PATH_ENDPOINT_REVERSE_ROLE, parent_ids=("y",)
        )
        groups, _ = build_reaction_groups(StructureSet((forward, reverse)), ResultSet())
        seen.update(_codes(groups[0].diagnostics))
        lone = _structure("lone-0", group_key=None)
        _, global_diagnostics = build_reaction_groups(
            StructureSet((lone,)), ResultSet((_energy(-1.0, "ghost"),))
        )
        seen.update(_codes(global_diagnostics))
        extra_a = _structure("ts-a", group_key="rxn-2")
        extra_b = _structure("ts-b", group_key="rxn-2")
        fwd = _structure(
            "fwd-2",
            group_key="rxn-2",
            role=PATH_ENDPOINT_FORWARD_ROLE,
            parent_ids=("ts-a", "ts-b"),
            offset=1.0,
        )
        rev = _structure(
            "rev-2",
            group_key="rxn-2",
            role=PATH_ENDPOINT_REVERSE_ROLE,
            parent_ids=("ts-a", "ts-b"),
            offset=2.0,
        )
        groups, _ = build_reaction_groups(StructureSet((extra_a, extra_b, fwd, rev)), ResultSet())
        seen.update(_codes(groups[0].diagnostics))
        dup_ts = _structure("dup-ts", group_key="rxn-3")
        dup_fwd_a = _structure(
            "dup-fwd-a",
            group_key="rxn-3",
            role=PATH_ENDPOINT_FORWARD_ROLE,
            parent_ids=(dup_ts.id,),
            offset=1.0,
        )
        dup_fwd_b = _structure(
            "dup-fwd-b",
            group_key="rxn-3",
            role=PATH_ENDPOINT_FORWARD_ROLE,
            parent_ids=(dup_ts.id,),
            offset=2.0,
        )
        dup_rev = _structure(
            "dup-rev",
            group_key="rxn-3",
            role=PATH_ENDPOINT_REVERSE_ROLE,
            parent_ids=(dup_ts.id,),
            offset=3.0,
        )
        groups, _ = build_reaction_groups(
            StructureSet((dup_ts, dup_fwd_a, dup_fwd_b, dup_rev)), ResultSet()
        )
        seen.update(_codes(groups[0].diagnostics))
        try:
            _definition(
                StubEnergyModel(),
                endpoint_assignment={"forward": "product", "reverse": "product"},
            )
        except AnalysisError as exc:
            seen.add(exc.code)
        else:  # pragma: no cover - must raise
            raise AssertionError("expected AnalysisError")
        assert set(ANALYSIS_FAILURE_CODES) <= seen, set(ANALYSIS_FAILURE_CODES) - seen


class TestNoSubprocessNoAdapter:
    """AST self-check: the runtime never shells out, adapts, or reads files."""

    _FORBIDDEN_MODULE_ROOTS = frozenset(
        {
            "subprocess",
            "os",
            "sys",
            "pathlib",
            "shutil",
            "multiprocessing",
            "threading",
            "concurrent",
        }
    )
    _FORBIDDEN_NAMES = frozenset(
        {
            "ProgramAdapter",
            "Popen",
            "check_output",
            "check_call",
            "open",
            "__import__",
            "eval",
            "exec",
            "system",
            "popen",
            "spawn",
            "fork",
        }
    )

    def _tokens(self, path: Path) -> tuple[set[str], set[str]]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules: set[str] = set()
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0])
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    modules.add("confflow")
                elif node.module:
                    modules.add(node.module.split(".")[0])
                for alias in node.names:
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
        return (modules, names)

    def test_owned_modules_are_pure(self) -> None:
        offenders: list[str] = []
        for filename in OWNED_MODULES:
            modules, names = self._tokens(ANALYSIS_ROOT / filename)
            for module in sorted(modules & self._FORBIDDEN_MODULE_ROOTS):
                offenders.append(f"{filename}: imports {module}")
            for name in sorted(names & self._FORBIDDEN_NAMES):
                offenders.append(f"{filename}: uses {name}")
        assert offenders == []

    def test_owned_modules_expose_no_task_enums(self) -> None:
        offenders: list[str] = []
        for filename in OWNED_MODULES:
            tree = ast.parse((ANALYSIS_ROOT / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    getattr(base, "attr", getattr(base, "id", "")) == "Enum" for base in node.bases
                ):
                    offenders.append(f"{filename}: defines Enum {node.name}")
        assert offenders == []

    def test_stub_model_satisfies_seam(self) -> None:
        assert isinstance(StubEnergyModel(), EnergyModel)

    def test_capability_spec_shape(self) -> None:
        assert isinstance(ANALYSIS_CAPABILITIES[0], AnalysisCapabilitySpec)
