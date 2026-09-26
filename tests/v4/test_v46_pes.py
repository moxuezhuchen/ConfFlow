#!/usr/bin/env python3

"""V4-6 PES tests: multi-group profile assembly, digests, and unit projections.

Covers :mod:`confflow.analysis.pes` (group-key sorting, shuffled-input
digest determinism, typed assembly errors) and
:mod:`confflow.analysis.units` display projections (Hartree to kJ/mol and
kcal/mol round-trips verified against :mod:`confflow.domain.units`).
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.analysis.pes import (
    PES_DIGEST_KIND,
    PROFILE_DIGEST_KIND,
    assemble_pes_profile,
    pes_digest,
    reaction_profile_digest,
)
from confflow.analysis.reaction import (
    KIND_REACTION_PROFILE,
    ReactionNodeGroup,
    assemble_reaction_result,
)
from confflow.analysis.thermochemistry import EnergyModel
from confflow.analysis.units import (
    HARTREE_TO_KCAL_PER_MOL,
    HARTREE_TO_KJ_PER_MOL,
    AnalysisMathError,
    from_hartree,
    to_hartree,
)
from confflow.domain.result import ResultSet, ScientificResult
from confflow.domain.units import CANONICAL_UNITS, UNIT_QUANTITIES, QuantityKind, Unit

MODEL = EnergyModel(
    mode="composite",
    electronic_selector="energy",
    correction_selector="gibbs_correction",
)


def _pool(subject: str, electronic: float, correction: float) -> ResultSet:
    """Build one node's ``energy`` + ``gibbs_correction`` pool."""
    return ResultSet.of(
        ScientificResult(
            kind="energy",
            value=electronic,
            unit=Unit.HARTREE,
            subject_structure_id=subject,
            source_step_id="s-high",
        ),
        ScientificResult(
            kind="gibbs_correction",
            value=correction,
            unit=Unit.HARTREE,
            subject_structure_id=subject,
            source_step_id="s-low",
        ),
    )


def _profile_payload(
    group_key: str,
    prefix: str,
    electronic_base: float,
) -> dict[str, Any]:
    """Assemble one real group and return its ``reaction_profile`` value."""
    ts_id, forward_id, reverse_id = f"{prefix}-ts", f"{prefix}-fwd", f"{prefix}-rev"
    group = ReactionNodeGroup(
        group_key=group_key,
        ts_structure_id=ts_id,
        forward_structure_id=forward_id,
        reverse_structure_id=reverse_id,
    )
    lookup = {
        ts_id: _pool(ts_id, electronic_base, 0.02),
        forward_id: _pool(forward_id, electronic_base - 0.05, 0.03),
        reverse_id: _pool(reverse_id, electronic_base - 0.03, 0.025),
    }
    analysis = assemble_reaction_result(group, MODEL, lookup, analysis_step_id="a-pes")
    assert analysis.ok
    for result in analysis.results:
        if result.kind == KIND_REACTION_PROFILE:
            return dict(result.value)
    raise AssertionError("reaction_profile result missing")


def _two_profiles() -> tuple[dict[str, Any], dict[str, Any]]:
    """Build two group payloads with deliberately unsorted keys."""
    profile_b = _profile_payload("rxn-b", "b", -80.0)
    profile_a = _profile_payload("rxn-a", "a", -76.0)
    return profile_a, profile_b


class TestPesAssembly:
    """Groups sort by ``group_key`` so input order never affects the view."""

    def test_groups_sorted_by_key(self) -> None:
        profile_a, profile_b = _two_profiles()
        combined = assemble_pes_profile([profile_b, profile_a])
        assert combined["group_keys"] == ["rxn-a", "rxn-b"]
        assert combined["count"] == 2
        assert [group["group_key"] for group in combined["groups"]] == ["rxn-a", "rxn-b"]

    def test_shuffled_inputs_give_identical_payload_and_digest(self) -> None:
        profile_a, profile_b = _two_profiles()
        first = assemble_pes_profile([profile_a, profile_b])
        second = assemble_pes_profile([profile_b, profile_a])
        assert first == second
        assert pes_digest(first) == pes_digest(second)

    def test_missing_group_key_fails_closed(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            assemble_pes_profile([{"nodes": {}}])
        assert excinfo.value.code == "missing_group_key"

    def test_blank_group_key_fails_closed(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            assemble_pes_profile([{"group_key": "  "}])
        assert excinfo.value.code == "missing_group_key"

    def test_duplicate_group_key_fails_closed(self) -> None:
        profile_a, _ = _two_profiles()
        with pytest.raises(AnalysisMathError) as excinfo:
            assemble_pes_profile([profile_a, dict(profile_a)])
        assert excinfo.value.code == "duplicate_group_key"
        assert "rxn-a" in str(excinfo.value.details.get("duplicate_keys"))

    def test_empty_assembly_is_count_zero(self) -> None:
        combined = assemble_pes_profile([])
        assert combined == {"groups": [], "group_keys": [], "count": 0}


class TestProfileDigests:
    """Digests are domain-separated and stable under mapping reordering."""

    def test_digest_stable_under_key_reordering(self) -> None:
        profile_a, _ = _two_profiles()
        reordered = dict(reversed(list(profile_a.items())))
        assert reaction_profile_digest(reordered) == reaction_profile_digest(profile_a)

    def test_digest_format(self) -> None:
        profile_a, _ = _two_profiles()
        digest = reaction_profile_digest(profile_a)
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_reaction_and_pes_digest_kinds_are_disjoint(self) -> None:
        assert PROFILE_DIGEST_KIND != PES_DIGEST_KIND
        assert PROFILE_DIGEST_KIND == "confflow.analysis.reaction_profile.v1"
        assert PES_DIGEST_KIND == "confflow.analysis.pes.v1"

    def test_distinct_groups_give_distinct_profile_digests(self) -> None:
        profile_a, profile_b = _two_profiles()
        assert reaction_profile_digest(profile_a) != reaction_profile_digest(profile_b)


class TestDisplayProjections:
    """Hartree display projections round-trip and match domain unit policy."""

    def test_constants_match_documented_codadata_values(self) -> None:
        assert HARTREE_TO_KJ_PER_MOL == pytest.approx(2625.4996394799, abs=1e-9)
        assert HARTREE_TO_KCAL_PER_MOL == pytest.approx(627.5094740631, abs=1e-9)

    def test_from_hartree_display_values(self) -> None:
        assert from_hartree(1.0, Unit.KILOJOULE_PER_MOLE) == pytest.approx(
            2625.4996394799, abs=1e-9
        )
        assert from_hartree(1.0, Unit.KILOCALORIE_PER_MOLE) == pytest.approx(
            627.5094740631, abs=1e-9
        )

    def test_round_trip_kj_and_kcal(self) -> None:
        for unit in (Unit.KILOJOULE_PER_MOLE, Unit.KILOCALORIE_PER_MOLE):
            assert to_hartree(from_hartree(-76.43, unit), unit) == pytest.approx(-76.43, abs=1e-9)

    def test_domain_declares_energy_units_with_hartree_canonical(self) -> None:
        for unit in (
            Unit.HARTREE,
            Unit.KILOJOULE_PER_MOLE,
            Unit.KILOCALORIE_PER_MOLE,
            Unit.ELECTRONVOLT,
        ):
            assert UNIT_QUANTITIES[unit] is QuantityKind.ENERGY
        assert CANONICAL_UNITS[QuantityKind.ENERGY] is Unit.HARTREE

    def test_non_energy_projection_rejected(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            to_hartree(1.0, Unit.ANGSTROM)
        assert excinfo.value.code == "unit_mismatch"
        with pytest.raises(AnalysisMathError) as excinfo:
            from_hartree(1.0, Unit.BOHR)
        assert excinfo.value.code == "unit_mismatch"

    def test_non_finite_projection_rejected(self) -> None:
        with pytest.raises(AnalysisMathError) as excinfo:
            from_hartree(float("inf"), Unit.KILOJOULE_PER_MOLE)
        assert excinfo.value.code == "non_numeric_value"
