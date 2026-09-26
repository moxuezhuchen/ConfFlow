#!/usr/bin/env python3

"""V4-6 reaction aggregation tests: Gibbs math, barriers, and fail-closed groups.

Covers :mod:`confflow.analysis.thermochemistry` and
:mod:`confflow.analysis.reaction` against exact-arithmetic fixtures with
``pytest.approx(abs=1e-9)`` tolerance: composite ``G_high = E_high + corr``,
direct Gibbs, the opt-in low-level fallback, missing-energy fail-closed
semantics, unit-mismatch typing, exact-kind selectors, and the
no-chemistry-assignment invariant (``forward``/``reverse`` are native path
directions only; ``assignment`` is always ``None``).
"""

from __future__ import annotations

import json

import pytest

from confflow.analysis.reaction import (
    KIND_BARRIER_FORWARD_ENDPOINT,
    KIND_BARRIER_REVERSE_ENDPOINT,
    KIND_ENDPOINT_ENERGY_DELTA,
    KIND_ENDPOINT_GIBBS_DELTA,
    KIND_REACTION_PROFILE,
    REFERENCE_REVERSE_MINUS_FORWARD,
    ReactionNodeGroup,
    assemble_reaction_result,
)
from confflow.analysis.thermochemistry import (
    FORMULA_COMPOSITE,
    FORMULA_DIRECT,
    FORMULA_FALLBACK_LOW_LEVEL,
    IMPLEMENTATION_VERSION,
    EnergyModel,
    composite_gibbs,
    direct_gibbs,
    gibbs_correction_value,
    resolve_node_gibbs,
    select_result,
)
from confflow.analysis.units import (
    AnalysisMathError,
    from_hartree,
    to_hartree,
    value_in_hartree,
)
from confflow.domain.result import ResultSet, ScientificResult
from confflow.domain.units import Unit

TS_ID = "ts-1"
FORWARD_ID = "fwd-1"
REVERSE_ID = "rev-1"
GROUP_KEY = "rxn-a"
ANALYSIS_STEP = "a-rxn"

#: Per-node ``(E_high, correction)`` composite fixtures.  Expected Gibbs:
#: TS -76.43, forward -76.47, reverse -76.455; barriers 0.04 / 0.025;
#: endpoint Gibbs delta 0.015; endpoint electronic delta 0.02.
NODE_FIXTURES = {
    TS_ID: (-76.45, 0.02),
    FORWARD_ID: (-76.50, 0.03),
    REVERSE_ID: (-76.48, 0.025),
}

EXPECTED_GIBBS = {TS_ID: -76.43, FORWARD_ID: -76.47, REVERSE_ID: -76.455}
EXPECTED_BARRIER_FORWARD = 0.04
EXPECTED_BARRIER_REVERSE = 0.025
EXPECTED_GIBBS_DELTA = 0.015
EXPECTED_ENERGY_DELTA = 0.02

COMPOSITE_MODEL = EnergyModel(
    mode="composite",
    electronic_selector="energy",
    correction_selector="gibbs_correction",
)
DIRECT_MODEL = EnergyModel(
    mode="direct",
    electronic_selector="energy",
    correction_selector="gibbs_correction",
)
FALLBACK_MODEL = EnergyModel(
    mode="composite",
    electronic_selector="energy",
    correction_selector="gibbs_correction",
    fallback="low_level",
)


def _result(
    kind: str,
    value: float,
    subject: str,
    *,
    unit: Unit = Unit.HARTREE,
    step: str = "s-low",
) -> ScientificResult:
    """Build one source result bound to *subject*."""
    return ScientificResult(
        kind=kind,
        value=value,
        unit=unit,
        subject_structure_id=subject,
        source_step_id=step,
    )


def _group() -> ReactionNodeGroup:
    """Build the single-group node seam shared by every test."""
    return ReactionNodeGroup(
        group_key=GROUP_KEY,
        ts_structure_id=TS_ID,
        forward_structure_id=FORWARD_ID,
        reverse_structure_id=REVERSE_ID,
    )


def _composite_lookup() -> dict[str, ResultSet]:
    """Build per-node pools of ``energy`` + ``gibbs_correction``."""
    lookup: dict[str, ResultSet] = {}
    for subject, (electronic, correction) in NODE_FIXTURES.items():
        lookup[subject] = ResultSet.of(
            _result("energy", electronic, subject),
            _result("gibbs_correction", correction, subject),
        )
    return lookup


def _direct_lookup() -> dict[str, ResultSet]:
    """Build per-node pools of ``gibbs_energy`` + ``energy``."""
    lookup: dict[str, ResultSet] = {}
    for subject in (TS_ID, FORWARD_ID, REVERSE_ID):
        lookup[subject] = ResultSet.of(
            _result("gibbs_energy", EXPECTED_GIBBS[subject], subject),
            _result("energy", NODE_FIXTURES[subject][0], subject),
        )
    return lookup


def _results_by_kind(analysis: object) -> dict[str, ScientificResult]:
    """Index an analysis outcome's results by kind."""
    from confflow.analysis.reaction import ReactionAnalysis

    assert isinstance(analysis, ReactionAnalysis)
    return {result.kind: result for result in analysis.results}


class TestGibbsCorrectionValue:
    """The low-level correction is ``G_low - E_low`` with both legs required."""

    def test_exact_correction(self) -> None:
        gibbs_low = _result("gibbs_energy", -76.38, TS_ID)
        electronic_low = _result("energy", -76.40, TS_ID)
        assert gibbs_correction_value(gibbs_low, electronic_low) == pytest.approx(0.02, abs=1e-9)

    def test_missing_gibbs_leg_fails(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            gibbs_correction_value(None, _result("energy", -76.40, TS_ID))
        assert excinfo.value.code == "energy_missing"

    def test_missing_electronic_leg_fails(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            gibbs_correction_value(_result("gibbs_energy", -76.38, TS_ID), None)
        assert excinfo.value.code == "energy_missing"

    def test_unit_mismatch_before_arithmetic(self) -> None:
        bad = _result("energy", -76.40, TS_ID, unit=Unit.ANGSTROM)
        with pytest.raises(AnalysisMathError) as excinfo:
            gibbs_correction_value(_result("gibbs_energy", -76.38, TS_ID), bad)
        assert excinfo.value.code == "unit_mismatch"


class TestCompositeGibbs:
    """``composite_gibbs`` adds a Hartree correction to a unit-checked energy."""

    def test_exact_composite(self) -> None:
        electronic = _result("energy", -76.45, TS_ID)
        assert composite_gibbs(electronic, 0.02) == pytest.approx(-76.43, abs=1e-9)

    def test_correction_from_legs_round_trip(self) -> None:
        correction = gibbs_correction_value(
            _result("gibbs_energy", -76.38, TS_ID),
            _result("energy", -76.40, TS_ID),
        )
        gibbs = composite_gibbs(_result("energy", -76.45, TS_ID), correction)
        assert gibbs == pytest.approx(-76.43, abs=1e-9)

    def test_non_energy_unit_rejected(self) -> None:
        bad = _result("energy", -76.45, TS_ID, unit=Unit.CM_INVERSE)
        with pytest.raises(AnalysisMathError) as excinfo:
            composite_gibbs(bad, 0.02)
        assert excinfo.value.code == "unit_mismatch"

    def test_non_finite_correction_rejected(self) -> None:
        with pytest.raises(AnalysisMathError):
            composite_gibbs(_result("energy", -76.45, TS_ID), float("nan"))


class TestDirectGibbs:
    """``direct_gibbs`` consumes the parsed Gibbs energy with unit safety."""

    def test_exact_direct(self) -> None:
        assert direct_gibbs(_result("gibbs_energy", -76.43, TS_ID)) == pytest.approx(
            -76.43, abs=1e-9
        )

    def test_wrong_quantity_rejected(self) -> None:
        bad = _result("lowest_frequency", 100.0, TS_ID, unit=Unit.CM_INVERSE)
        with pytest.raises(AnalysisMathError) as excinfo:
            direct_gibbs(bad)
        assert excinfo.value.code == "unit_mismatch"


class TestResolveNodeGibbs:
    """Node resolution follows the explicit policy and never substitutes."""

    def test_composite_resolution(self) -> None:
        resolved = resolve_node_gibbs(_composite_lookup()[TS_ID], TS_ID, COMPOSITE_MODEL)
        assert resolved.value_hartree == pytest.approx(-76.43, abs=1e-9)
        assert resolved.formula == FORMULA_COMPOSITE
        assert resolved.fallback_used is False
        assert resolved.electronic_source_id is not None
        assert resolved.correction_source_id is not None

    def test_direct_resolution(self) -> None:
        resolved = resolve_node_gibbs(_direct_lookup()[TS_ID], TS_ID, DIRECT_MODEL)
        assert resolved.value_hartree == pytest.approx(-76.43, abs=1e-9)
        assert resolved.formula == FORMULA_DIRECT
        assert resolved.gibbs_source_id is not None

    def test_composite_missing_correction_reports_selector(self) -> None:
        pool = ResultSet.of(_result("energy", -76.45, TS_ID))
        with pytest.raises(AnalysisMathError) as excinfo:
            resolve_node_gibbs(pool, TS_ID, COMPOSITE_MODEL)
        assert excinfo.value.code == "correction_missing"
        assert excinfo.value.details["selector"] == "gibbs_correction"

    def test_composite_missing_electronic_no_fallback(self) -> None:
        pool = ResultSet.of(_result("gibbs_correction", 0.02, TS_ID))
        with pytest.raises(AnalysisMathError) as excinfo:
            resolve_node_gibbs(pool, TS_ID, COMPOSITE_MODEL)
        assert excinfo.value.code == "energy_missing"


class TestExactSelectors:
    """Selectors match exact kinds only and never guess across kinds or subjects."""

    def test_selector_kind_mismatch_returns_none(self) -> None:
        pool = ResultSet.of(_result("gibbs_energy", -76.43, TS_ID))
        assert select_result(pool, TS_ID, "energy", "energy") is None

    def test_selector_must_equal_kind(self) -> None:
        pool = ResultSet.of(_result("gibbs_energy", -76.43, TS_ID))
        assert select_result(pool, TS_ID, "gibbs_energy", "energy") is None

    def test_subject_is_never_guessed(self) -> None:
        pool = ResultSet.of(_result("gibbs_energy", -76.43, TS_ID))
        assert select_result(pool, FORWARD_ID, "gibbs_energy", "gibbs_energy") is None

    def test_first_pool_order_wins(self) -> None:
        pool = ResultSet.of(
            _result("gibbs_energy", -76.43, TS_ID, step="s-first"),
            _result("gibbs_energy", -76.44, TS_ID, step="s-second"),
        )
        selected = select_result(pool, TS_ID, "gibbs_energy", "gibbs_energy")
        assert selected is not None
        assert selected.source_step_id == "s-first"


class TestCompositeAssembly:
    """Full per-group assembly: barriers, deltas, profile, provenance."""

    def test_five_results_with_exact_kinds(self) -> None:
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, _composite_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        assert analysis.ok
        assert analysis.diagnostics == ()
        assert [result.kind for result in analysis.results] == [
            KIND_BARRIER_FORWARD_ENDPOINT,
            KIND_BARRIER_REVERSE_ENDPOINT,
            KIND_ENDPOINT_GIBBS_DELTA,
            KIND_ENDPOINT_ENERGY_DELTA,
            KIND_REACTION_PROFILE,
        ]

    def test_barrier_and_delta_values(self) -> None:
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, _composite_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        by_kind = _results_by_kind(analysis)
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER_FORWARD, abs=1e-9
        )
        assert by_kind[KIND_BARRIER_REVERSE_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER_REVERSE, abs=1e-9
        )
        assert by_kind[KIND_ENDPOINT_GIBBS_DELTA].value == pytest.approx(
            EXPECTED_GIBBS_DELTA, abs=1e-9
        )
        assert by_kind[KIND_ENDPOINT_ENERGY_DELTA].value == pytest.approx(
            EXPECTED_ENERGY_DELTA, abs=1e-9
        )

    def test_computed_results_carry_hartree_provenance(self) -> None:
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, _composite_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        by_kind = _results_by_kind(analysis)
        barrier = by_kind[KIND_BARRIER_FORWARD_ENDPOINT]
        assert barrier.unit is Unit.HARTREE
        assert barrier.subject_structure_id == TS_ID
        assert barrier.source_step_id == ANALYSIS_STEP
        assert barrier.provenance is not None
        assert barrier.provenance.adapter == IMPLEMENTATION_VERSION
        metadata = dict(barrier.provenance.metadata)
        assert metadata["formula"] == "barrier_forward_endpoint=G_TS-G_forward"
        assert metadata["electronic_source_id"] is not None
        assert metadata["correction_source_id"] is not None
        assert metadata["implementation_version"] == IMPLEMENTATION_VERSION
        assert metadata["energy_model"]["mode"] == "composite"
        assert metadata["energy_model"]["fallback"] == "none"

    def test_profile_payload_shape(self) -> None:
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, _composite_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        profile = _results_by_kind(analysis)[KIND_REACTION_PROFILE]
        payload = dict(profile.value)
        assert payload["group_key"] == GROUP_KEY
        assert payload["nodes"] == {"ts": TS_ID, "forward": FORWARD_ID, "reverse": REVERSE_ID}
        assert payload["gibbs_energy"]["ts"]["value"] == pytest.approx(-76.43, abs=1e-9)
        assert payload["gibbs_energy"]["forward"]["value"] == pytest.approx(-76.47, abs=1e-9)
        assert payload["gibbs_energy"]["reverse"]["value"] == pytest.approx(-76.455, abs=1e-9)
        assert payload["electronic_energy"]["reverse"]["value"] == pytest.approx(-76.48, abs=1e-9)
        assert payload["relative"]["reference"] == REFERENCE_REVERSE_MINUS_FORWARD
        assert payload["relative"]["endpoint_gibbs_delta"]["value"] == pytest.approx(
            EXPECTED_GIBBS_DELTA, abs=1e-9
        )
        assert payload["barriers"]["forward_endpoint"]["value"] == pytest.approx(
            EXPECTED_BARRIER_FORWARD, abs=1e-9
        )
        assert payload["barriers"]["reverse_endpoint"]["value"] == pytest.approx(
            EXPECTED_BARRIER_REVERSE, abs=1e-9
        )
        assert payload["assignment"] is None
        assert list(payload["source_result_ids"]) == sorted(payload["source_result_ids"])
        assert len(payload["source_result_ids"]) == 6
        assert payload["fallback_used"] == {"ts": False, "forward": False, "reverse": False}


class TestDirectAssembly:
    """Direct policy consumes ``gibbs_energy`` while electronics feed the delta."""

    def test_direct_barriers_match_composite(self) -> None:
        analysis = assemble_reaction_result(
            _group(), DIRECT_MODEL, _direct_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        assert analysis.ok
        by_kind = _results_by_kind(analysis)
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER_FORWARD, abs=1e-9
        )
        assert by_kind[KIND_BARRIER_REVERSE_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER_REVERSE, abs=1e-9
        )
        profile = by_kind[KIND_REACTION_PROFILE]
        assert dict(profile.value)["energy_model"]["mode"] == "direct"


class TestLowLevelFallback:
    """Opt-in fallback uses low-level Gibbs explicitly marked, never silently."""

    def _fallback_lookup(self) -> dict[str, ResultSet]:
        return {
            subject: ResultSet.of(_result("gibbs_energy", gibbs, subject))
            for subject, gibbs in EXPECTED_GIBBS.items()
        }

    def test_fallback_marks_formula_and_provenance(self) -> None:
        analysis = assemble_reaction_result(
            _group(), FALLBACK_MODEL, self._fallback_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        assert not analysis.ok  # endpoint electronic delta still needs "energy"
        codes = [item.code for item in analysis.diagnostics]
        assert "analysis_energy_missing" in codes
        assert analysis.results == ()

    def test_fallback_gibbs_resolution_is_marked(self) -> None:
        pool = ResultSet.of(_result("gibbs_energy", -76.43, TS_ID))
        resolved = resolve_node_gibbs(pool, TS_ID, FALLBACK_MODEL)
        assert resolved.value_hartree == pytest.approx(-76.43, abs=1e-9)
        assert resolved.formula == FORMULA_FALLBACK_LOW_LEVEL
        assert resolved.fallback_used is True
        assert resolved.electronic_source_id is None

    def test_fallback_without_any_gibbs_fails_closed(self) -> None:
        pool = ResultSet.of(_result("gibbs_correction", 0.02, TS_ID))
        with pytest.raises(AnalysisMathError) as excinfo:
            resolve_node_gibbs(pool, TS_ID, FALLBACK_MODEL)
        assert excinfo.value.code == "energy_missing"


class TestFailClosed:
    """Missing pieces fail the group closed: no results, typed diagnostics."""

    def test_missing_node_pool(self) -> None:
        lookup = _composite_lookup()
        del lookup[REVERSE_ID]
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, lookup, analysis_step_id=ANALYSIS_STEP
        )
        assert not analysis.ok
        assert analysis.results == ()
        assert analysis.diagnostics[0].code == "analysis_energy_missing"

    def test_missing_correction_leg(self) -> None:
        lookup = _composite_lookup()
        lookup[FORWARD_ID] = ResultSet.of(_result("energy", -76.50, FORWARD_ID))
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, lookup, analysis_step_id=ANALYSIS_STEP
        )
        assert not analysis.ok
        assert analysis.results == ()
        codes = [item.code for item in analysis.diagnostics]
        assert "analysis_correction_missing" in codes

    def test_missing_electronic_leg_no_fallback(self) -> None:
        lookup = _composite_lookup()
        lookup[TS_ID] = ResultSet.of(_result("gibbs_correction", 0.02, TS_ID))
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, lookup, analysis_step_id=ANALYSIS_STEP
        )
        assert not analysis.ok
        assert analysis.results == ()
        codes = [item.code for item in analysis.diagnostics]
        assert "analysis_energy_missing" in codes

    def test_empty_group_key_fails(self) -> None:
        group = ReactionNodeGroup(
            group_key="  ",
            ts_structure_id=TS_ID,
            forward_structure_id=FORWARD_ID,
            reverse_structure_id=REVERSE_ID,
        )
        analysis = assemble_reaction_result(
            group, COMPOSITE_MODEL, _composite_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        assert not analysis.ok
        assert analysis.results == ()
        assert analysis.diagnostics[0].code == "analysis_group_incomplete"


class TestUnitSafetyAtGroupLevel:
    """Incompatible units surface as typed diagnostics, never bare floats."""

    def test_angstrom_energy_fails_with_unit_mismatch(self) -> None:
        lookup = _composite_lookup()
        lookup[TS_ID] = ResultSet.of(
            _result("energy", -76.45, TS_ID, unit=Unit.ANGSTROM),
            _result("gibbs_correction", 0.02, TS_ID),
        )
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, lookup, analysis_step_id=ANALYSIS_STEP
        )
        assert not analysis.ok
        assert analysis.results == ()
        codes = [item.code for item in analysis.diagnostics]
        assert "analysis_unit_mismatch" in codes

    def test_mixed_energy_units_convert_exactly(self) -> None:
        kilojoule = to_hartree(1.0, Unit.KILOJOULE_PER_MOLE)
        lookup = _composite_lookup()
        lookup[TS_ID] = ResultSet.of(
            _result("energy", -76.45 - kilojoule, TS_ID),
            _result("gibbs_correction", 0.02 + kilojoule, TS_ID),
        )
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, lookup, analysis_step_id=ANALYSIS_STEP
        )
        assert analysis.ok
        by_kind = _results_by_kind(analysis)
        assert by_kind[KIND_BARRIER_FORWARD_ENDPOINT].value == pytest.approx(
            EXPECTED_BARRIER_FORWARD, abs=1e-9
        )

    def test_value_in_hartree_rejects_bare_floats(self) -> None:
        bad = ScientificResult(
            kind="energy",
            value="not-a-number",
            unit=Unit.HARTREE,
            subject_structure_id=TS_ID,
        )
        with pytest.raises(AnalysisMathError) as excinfo:
            value_in_hartree(bad)
        assert excinfo.value.code == "non_numeric_value"

    def test_from_hartree_display_leaves_result_identity_untouched(self) -> None:
        result = _result("energy", -76.45, TS_ID)
        before = result.value_digest
        assert from_hartree(float(result.value), Unit.KILOJOULE_PER_MOLE) == pytest.approx(
            -76.45 * 2625.4996394799, abs=1e-6
        )
        assert result.value_digest == before


class TestRolePreservation:
    """Endpoints are never relabelled: no reactant/product chemistry assignment."""

    def test_nodes_and_kinds_use_endpoint_vocabulary(self) -> None:
        analysis = assemble_reaction_result(
            _group(), COMPOSITE_MODEL, _composite_lookup(), analysis_step_id=ANALYSIS_STEP
        )
        by_kind = _results_by_kind(analysis)
        payload = dict(by_kind[KIND_REACTION_PROFILE].value)
        assert set(payload["nodes"]) == {"ts", "forward", "reverse"}
        text = json.dumps(payload, sort_keys=True)
        assert "reactant" not in text
        assert "product" not in text
        assert "forward reaction barrier" not in text
        kinds = {result.kind for result in analysis.results}
        assert "forward reaction barrier" not in kinds
        assert KIND_BARRIER_FORWARD_ENDPOINT in kinds
        assert KIND_BARRIER_REVERSE_ENDPOINT in kinds

    def test_assignment_is_always_none(self) -> None:
        for lookup, model in (
            (_composite_lookup(), COMPOSITE_MODEL),
            (_direct_lookup(), DIRECT_MODEL),
        ):
            analysis = assemble_reaction_result(
                _group(), model, lookup, analysis_step_id=ANALYSIS_STEP
            )
            payload = dict(_results_by_kind(analysis)[KIND_REACTION_PROFILE].value)
            assert payload["assignment"] is None


class TestEnergyModelPolicy:
    """The policy object validates every field explicitly at construction."""

    def test_invalid_mode_rejected(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            EnergyModel(
                mode="auto", electronic_selector="energy", correction_selector="gibbs_correction"
            )
        assert excinfo.value.code == "invalid_energy_model"

    def test_invalid_fallback_rejected(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            EnergyModel(
                mode="composite",
                electronic_selector="energy",
                correction_selector="gibbs_correction",
                fallback="sometimes",
            )
        assert excinfo.value.code == "invalid_energy_model"

    def test_empty_selector_rejected(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            EnergyModel(
                mode="composite",
                electronic_selector="  ",
                correction_selector="gibbs_correction",
            )
        assert excinfo.value.code == "invalid_energy_model"

    def test_policy_round_trips_through_digest_payload(self) -> None:
        assert FALLBACK_MODEL.to_dict() == {
            "mode": "composite",
            "electronic_selector": "energy",
            "correction_selector": "gibbs_correction",
            "fallback": "low_level",
        }
