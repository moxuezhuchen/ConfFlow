#!/usr/bin/env python3

"""V4-5 GOAT/ensemble capability: block rendering and member parsing.

All multi-line log fixtures below are crafted fixtures for the strict
``confflow-goat-v1`` dialect defined in
``confflow.programs.orca.ensemble_parse``; they are not native ORCA output
and make no claim to reproduce any ORCA release's GOAT report format.
"""

from __future__ import annotations

import pytest

from confflow.programs.orca import ensemble_parse as eparse
from confflow.programs.orca import goat

ATOMS = ("O", "H", "H")


def _member_block(
    index: int,
    points: tuple[tuple[float, float, float], ...],
    energy: float | None = ...,
) -> str:
    """Build one crafted-dialect member fixture block.

    Parameters
    ----------
    index : int
        Banner member number.
    points : tuple[tuple[float, float, float], ...]
        Coordinates zipped with ``ATOMS``.
    energy : float | None
        Energy line value; ``...`` (default) writes a derived placeholder,
        ``None`` omits the energy line (unknown energy).

    Returns
    -------
    str
        Crafted-dialect fixture text for one member.
    """
    lines = [f"GOAT CONFORMER {index}"]
    for symbol, (x, y, z) in zip(ATOMS, points):
        lines.append(f"{symbol} {x:.6f} {y:.6f} {z:.6f}")
    if energy is ...:
        lines.append(f"Conformer energy: {-76.0 - index:.6f} Hartree")
    elif energy is not None:
        lines.append(f"Conformer energy: {energy} Hartree")
    return "\n".join(lines) + "\n"


GEOM_A = ((0.0, 0.0, 0.0), (0.757, 0.586, 0.0), (-0.757, 0.586, 0.0))
GEOM_B = ((0.0, 0.0, 0.1), (0.8, 0.5, 0.0), (-0.8, 0.5, 0.0))


class TestRenderGoatBlocks:
    """``%goat`` rendering goldens and fail-closed rejections."""

    def test_golden_sorted_keys(self) -> None:
        text = goat.render_goat_blocks(
            {
                "goat": {
                    "Seed": 7,
                    "MaxIter": 50,
                    "MaxConformers": 10,
                    "EnergyWindow": 5.0,
                }
            }
        )
        assert text == (
            "%goat\n"
            "  EnergyWindow 5.0\n"
            "  MaxConformers 10\n"
            "  MaxIter 50\n"
            "  Seed 7\n"
            "end\n"
        )

    def test_golden_single_key(self) -> None:
        assert goat.render_goat_blocks({"goat": {"MaxIter": 3}}) == ("%goat\n  MaxIter 3\nend\n")

    def test_empty_mapping_renders_bare_block(self) -> None:
        assert goat.render_goat_blocks({"goat": {}}) == "%goat\nend\n"

    def test_key_order_deterministic(self) -> None:
        first = goat.render_goat_blocks({"goat": {"Seed": 1, "MaxIter": 2}})
        second = goat.render_goat_blocks({"goat": {"MaxIter": 2, "Seed": 1}})
        assert first == second

    def test_missing_mapping_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"GOAT requires native\['goat'\]"):
            goat.render_goat_blocks({})
        with pytest.raises(ValueError, match=r"GOAT requires native\['goat'\]"):
            goat.render_goat_blocks({"goat": None})
        with pytest.raises(ValueError, match=r"GOAT requires native\['goat'\]"):
            goat.render_goat_blocks({"goat": [("MaxIter", 3)]})

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"NConformers": 5}})
        try:
            goat.render_goat_blocks({"goat": {"MaxIter": 5, "Bogus": 1}})
        except ValueError as exc:
            assert "native_input_error" in str(exc)
            assert "Bogus" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError")

    def test_bad_types_rejected(self) -> None:
        for bad in ("50", 5.5, True, None):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"MaxIter": bad}})
        for bad in ("7", 7.5, True):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"Seed": bad}})
        for bad in ("5.0", True, None):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"EnergyWindow": bad}})

    def test_out_of_range_rejected(self) -> None:
        for key in ("MaxIter", "MaxConformers"):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {key: 0}})
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {key: -2}})
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"Seed": -1}})
        for bad in (0, -1.5, float("nan"), float("inf")):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"EnergyWindow": bad}})

    def test_allowlist_contents(self) -> None:
        assert goat.GOAT_BLOCK_KEYS == frozenset(
            {"MaxIter", "MaxConformers", "EnergyWindow", "Seed"}
        )


class TestValidateGoatInputs:
    """GOAT structure shape gates."""

    def test_valid_inputs_return_none(self) -> None:
        goat.validate_goat_inputs(["O", "H"], [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)], 0, 1)

    def test_charge_and_multiplicity_unchecked(self) -> None:
        goat.validate_goat_inputs(["O"], [(0.0, 0.0, 0.0)], None, None)
        goat.validate_goat_inputs(["O"], [(0.0, 0.0, 0.0)], "x", "y")

    def test_empty_structure_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty structure"):
            goat.validate_goat_inputs([], [], 0, 1)

    def test_count_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match="count"):
            goat.validate_goat_inputs(["O", "H"], [(0.0, 0.0, 0.0)], 0, 1)

    def test_bad_coordinates_rejected(self) -> None:
        with pytest.raises(ValueError, match="triples"):
            goat.validate_goat_inputs(["O"], [(0.0, 0.0)], 0, 1)
        with pytest.raises(ValueError, match="finite"):
            goat.validate_goat_inputs(["O"], [(float("nan"), 0.0, 0.0)], 0, 1)
        with pytest.raises(ValueError, match="finite"):
            goat.validate_goat_inputs(["O"], [(True, 0.0, 0.0)], 0, 1)


class TestGoatArtifactNames:
    """Deterministic GOAT artifact file names."""

    def test_names(self) -> None:
        assert goat.goat_artifact_names("s_opt_s0") == {
            "trajectory_xyz": "s_opt_s0.goat.xyz",
            "log": "s_opt_s0.goat.out",
        }

    def test_deterministic(self) -> None:
        assert goat.goat_artifact_names("job") == goat.goat_artifact_names("job")

    def test_no_directories(self) -> None:
        names = goat.goat_artifact_names("job")
        assert all("/" not in value and "\\" not in value for value in names.values())

    def test_bad_job_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            goat.goat_artifact_names("")
        with pytest.raises(ValueError, match="no directories"):
            goat.goat_artifact_names("a/b")
        with pytest.raises(ValueError, match="no directories"):
            goat.goat_artifact_names("..")


class TestParseGoatMembers:
    """Strict-dialect member parsing over crafted fixtures."""

    def test_multi_member(self) -> None:
        text = _member_block(0, GEOM_A) + _member_block(1, GEOM_B)
        members = eparse.parse_goat_members(text, atoms=ATOMS)
        assert len(members) == 2
        assert members[0].member_index == 0
        assert members[1].member_index == 1
        assert members[0].energy_hartree == pytest.approx(-76.0)
        assert members[1].energy_hartree == pytest.approx(-77.0)
        assert members[0].geometry.atoms == ATOMS
        assert members[0].geometry.coordinates[0] == pytest.approx((0.0, 0.0, 0.0))

    def test_shuffled_order_invariance(self) -> None:
        forward = _member_block(0, GEOM_A) + _member_block(1, GEOM_B)
        backward = _member_block(1, GEOM_B) + _member_block(0, GEOM_A)
        first = eparse.parse_goat_members(forward, atoms=ATOMS)
        second = eparse.parse_goat_members(backward, atoms=ATOMS)
        assert {m.member_index for m in first} == {m.member_index for m in second}
        assert eparse.ensemble_energy_table(first) == eparse.ensemble_energy_table(second)
        assert eparse.member_ordering(first) == eparse.member_ordering(second) == (0, 1)

    def test_missing_energy_is_none(self) -> None:
        text = _member_block(2, GEOM_A, energy=None)
        (member,) = eparse.parse_goat_members(text, atoms=ATOMS)
        assert member.member_index == 2
        assert member.energy_hartree is None
        assert member.energy_hartree != 0.0

    def test_scientific_energy(self) -> None:
        text = (
            "GOAT CONFORMER 4\n"
            "O 0.000000 0.000000 0.000000\n"
            "H 0.757000 0.586000 0.000000\n"
            "H -0.757000 0.586000 0.000000\n"
            "Conformer energy: -7.641000E+01 Hartree\n"
        )
        (member,) = eparse.parse_goat_members(text, atoms=ATOMS)
        assert member.energy_hartree == pytest.approx(-76.41)

    def test_duplicate_number_rejected(self) -> None:
        text = _member_block(1, GEOM_A) + _member_block(1, GEOM_B)
        with pytest.raises(ValueError, match="duplicate"):
            eparse.parse_goat_members(text, atoms=ATOMS)

    def test_atom_count_mismatch_rejected(self) -> None:
        text = _member_block(0, GEOM_A) + (
            "GOAT CONFORMER 1\n"
            "O 0.000000 0.000000 0.000000\n"
            "Conformer energy: -76.0 Hartree\n"
        )
        with pytest.raises(ValueError, match="atom count"):
            eparse.parse_goat_members(text, atoms=ATOMS)

    def test_identical_geometries_preserved(self) -> None:
        text = _member_block(5, GEOM_A) + _member_block(9, GEOM_A)
        members = eparse.parse_goat_members(text, atoms=ATOMS)
        assert len(members) == 2
        assert [m.member_index for m in members] == [5, 9]
        assert members[0].geometry.coordinates == members[1].geometry.coordinates

    def test_empty_text(self) -> None:
        assert eparse.parse_goat_members("", atoms=ATOMS) == ()
        assert eparse.parse_goat_members("  \n\n", atoms=ATOMS) == ()

    def test_banner_without_coordinates_rejected(self) -> None:
        with pytest.raises(ValueError, match="no coordinates"):
            eparse.parse_goat_members("GOAT CONFORMER 0\n", atoms=ATOMS)

    def test_text_before_banner_rejected(self) -> None:
        with pytest.raises(ValueError, match="before first banner"):
            eparse.parse_goat_members("chatter\n" + _member_block(0, GEOM_A), atoms=ATOMS)

    def test_garbage_line_rejected(self) -> None:
        with pytest.raises(ValueError, match="unexpected line"):
            eparse.parse_goat_members(
                "GOAT CONFORMER 0\n"
                "O 0.000000 0.000000 0.000000\n"
                "H 0.757000 0.586000 0.000000\n"
                "H -0.757000 0.586000 0.000000\n"
                "something prose-like\n",
                atoms=ATOMS,
            )

    def test_repeated_energy_rejected(self) -> None:
        with pytest.raises(ValueError, match="repeated energy"):
            eparse.parse_goat_members(
                "GOAT CONFORMER 0\n"
                "O 0.000000 0.000000 0.000000\n"
                "H 0.757000 0.586000 0.000000\n"
                "H -0.757000 0.586000 0.000000\n"
                "Conformer energy: -76.0 Hartree\n"
                "Conformer energy: -76.1 Hartree\n",
                atoms=ATOMS,
            )

    def test_non_string_rejected(self) -> None:
        with pytest.raises(TypeError):
            eparse.parse_goat_members(None, atoms=ATOMS)  # type: ignore[arg-type]

    def test_member_index_is_banner_number(self) -> None:
        text = _member_block(42, GEOM_A)
        (member,) = eparse.parse_goat_members(text, atoms=ATOMS)
        assert member.member_index == 42


class TestOrderingHelpers:
    """Energy-table and canonical-ordering conveniences."""

    def test_energy_table(self) -> None:
        members = eparse.parse_goat_members(
            _member_block(3, GEOM_A) + _member_block(7, GEOM_B, energy=None),
            atoms=ATOMS,
        )
        table = eparse.ensemble_energy_table(members)
        assert table[3] == pytest.approx(-79.0)
        assert table[7] is None

    def test_energy_table_duplicate_rejected(self) -> None:
        members = eparse.parse_goat_members(_member_block(1, GEOM_A), atoms=ATOMS)
        with pytest.raises(ValueError, match="duplicate"):
            eparse.ensemble_energy_table(tuple(members) + tuple(members))

    def test_member_ordering_sorted(self) -> None:
        members = eparse.parse_goat_members(
            _member_block(9, GEOM_A) + _member_block(2, GEOM_B), atoms=ATOMS
        )
        assert eparse.member_ordering(members) == (2, 9)
        assert eparse.member_ordering(()) == ()


class TestGoatTrajectoryFacts:
    """Best-effort trajectory facts over crafted fixtures."""

    def test_facts(self) -> None:
        text = _member_block(0, GEOM_A) + _member_block(1, GEOM_B)
        facts = eparse.goat_trajectory_facts(text)
        assert facts["member_count"] == 2
        assert facts["member_indices"] == [0, 1]
        assert facts["energy_min_hartree"] == pytest.approx(-77.0)
        assert facts["energy_max_hartree"] == pytest.approx(-76.0)
        assert facts["truncated"] is False

    def test_no_energies(self) -> None:
        text = _member_block(0, GEOM_A, energy=None)
        facts = eparse.goat_trajectory_facts(text)
        assert facts["member_count"] == 1
        assert facts["energy_min_hartree"] is None
        assert facts["energy_max_hartree"] is None

    def test_truncated_flag(self) -> None:
        facts = eparse.goat_trajectory_facts("GOAT CONFORMER 0\n... truncated ...\n")
        assert facts["truncated"] is True
        assert facts["member_count"] == 1

    def test_empty_and_garbage_tolerant(self) -> None:
        facts = eparse.goat_trajectory_facts("")
        assert facts["member_count"] == 0
        assert facts["member_indices"] == []
        assert facts["truncated"] is False
        garbage = eparse.goat_trajectory_facts("total garbage ((( \n")
        assert garbage["member_count"] == 0
