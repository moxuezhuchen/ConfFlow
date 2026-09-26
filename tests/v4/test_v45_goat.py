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


def _real_log(lowest: float, rel_kcals: tuple[float, ...]) -> str:
    """Build a real-grammar final-ensemble table plus lowest-energy line."""
    lines = [
        "         # Final ensemble info #",
        "         Conformer     Energy     Degen.   % total   % cumul.",
        "                       (kcal/mol)",
        "         ------------------------------------------------------",
    ]
    for index, rel in enumerate(rel_kcals):
        lines.append(f"                 {index}     {rel:.3f}         1      50.00      50.00")
    lines.append("")
    lines.append(f"         Lowest energy conformer    : {lowest:.6f} Eh")
    lines.append("         Writing final ensemble to job.finalensemble.xyz")
    lines.append("****ORCA TERMINATED NORMALLY****")
    return "\n".join(lines) + "\n"


def _real_xyz(members: tuple[tuple[float, tuple], ...]) -> str:
    """Build a real-grammar multi-structure ensemble XYZ document.

    Each member is ``(comment_energy_or_None, ((symbol, x, y, z), ...))``.
    """
    blocks = []
    for energy, rows in members:
        comment = f"{energy:.6f} converged=true" if energy is not None else "no energy here"
        body = "\n".join(f"{symbol}  {x:.6f}  {y:.6f}  {z:.6f}" for symbol, x, y, z in rows)
        blocks.append(f"{len(rows)}\n{comment}\n{body}")
    return "\n".join(blocks) + "\n"


def _water_rows(shift: float = 0.0) -> tuple[tuple[str, float, float, float], ...]:
    """Water coordinates with a deterministic x-shift."""
    return tuple(
        (symbol, x + shift, y, z)
        for symbol, x, y, z in (
            ("O", 0.0, 0.0, 0.0),
            ("H", 0.757, 0.586, 0.0),
            ("H", -0.757, 0.586, 0.0),
        )
    )


def _two_member_pair() -> tuple[str, str]:
    """Real-grammar log plus XYZ for two conformers."""
    lowest = -76.41
    rel = (0.0, ( -76.40 - lowest) * 627.5094740631)
    return (
        _real_log(lowest, rel),
        _real_xyz(((-76.41, _water_rows(0.0)), (-76.40, _water_rows(0.03)))),
    )


class TestRenderGoatBlocks:
    """``%goat`` rendering goldens and fail-closed rejections."""

    def test_golden_sorted_keys(self) -> None:
        text = goat.render_goat_blocks(
            {
                "goat": {
                    "RANDOMSEED": 7,
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
            "  RANDOMSEED 7\n"
            "end\n"
        )

    def test_golden_single_key(self) -> None:
        assert goat.render_goat_blocks({"goat": {"MaxIter": 3}}) == ("%goat\n  MaxIter 3\nend\n")

    def test_empty_mapping_renders_bare_block(self) -> None:
        assert goat.render_goat_blocks({"goat": {}}) == "%goat\nend\n"

    def test_key_order_deterministic(self) -> None:
        first = goat.render_goat_blocks({"goat": {"RANDOMSEED": 1, "MaxIter": 2}})
        second = goat.render_goat_blocks({"goat": {"MaxIter": 2, "RANDOMSEED": 1}})
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
        for bad in ("7", 7.5, True, None):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"RANDOMSEED": bad}})
        for bad in ("5.0", True, None):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"EnergyWindow": bad}})

    def test_out_of_range_rejected(self) -> None:
        for key in ("MaxIter", "MaxConformers"):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {key: 0}})
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {key: -2}})
        # Seeds carry no positivity bound (the binary parses them).
        for bad in (0, -1.5, float("nan"), float("inf")):
            with pytest.raises(ValueError, match="native_input_error"):
                goat.render_goat_blocks({"goat": {"EnergyWindow": bad}})

    def test_allowlist_contents(self) -> None:
        assert goat.GOAT_BLOCK_KEYS == frozenset(
            {"MaxIter", "MaxConformers", "EnergyWindow", "RANDOMSEED"}
        )

    def test_invented_seed_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="native_input_error"):
            goat.render_goat_blocks({"goat": {"Seed": 7}})


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
    """Real-format member parsing over grammatical fixtures."""

    def test_multi_member(self) -> None:
        log, xyz = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        assert len(members) == 2
        assert members[0].member_index == 0
        assert members[1].member_index == 1
        assert members[0].energy_hartree == pytest.approx(-76.41)
        assert members[1].energy_hartree == pytest.approx(-76.40)
        assert members[0].geometry.atoms == ATOMS
        assert members[0].geometry.coordinates[0] == pytest.approx((0.0, 0.0, 0.0))

    def test_count_mismatch_rejected(self) -> None:
        log, xyz = _two_member_pair()
        lines = xyz.split("\n")
        single = "\n".join(lines[:5]) + "\n"
        with pytest.raises(ValueError, match="XYZ holds"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=single, atoms=ATOMS)

    def test_broken_index_sequence_rejected(self) -> None:
        log = _real_log(-76.41, (0.0, 0.627)).replace("                 1     ", "                 5     ", 1)
        _, xyz = _two_member_pair()
        with pytest.raises(ValueError, match="0..n-1"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_missing_lowest_line_rejected(self) -> None:
        log, xyz = _two_member_pair()
        log = "\n".join(line for line in log.split("\n") if "Lowest energy" not in line)
        with pytest.raises(ValueError, match="lowest-energy"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_energy_disagreement_rejected(self) -> None:
        log, xyz = _two_member_pair()
        xyz = xyz.replace("-76.400000 converged=true", "-75.000000 converged=true", 1)
        with pytest.raises(ValueError, match="disagrees"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_missing_comment_energy_rejected(self) -> None:
        xyz = _real_xyz(((None, _water_rows(0.0)),))
        log = _real_log(-76.41, (0.0,))
        with pytest.raises(ValueError, match="no energy"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)

    def test_atom_mismatch_rejected(self) -> None:
        log, xyz = _two_member_pair()
        with pytest.raises(ValueError, match="symbols"):
            eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=("O", "H"))

    def test_identical_geometries_preserved(self) -> None:
        log = _real_log(-76.41, (0.0, 0.0))
        xyz = _real_xyz(((-76.41, _water_rows(0.0)), (-76.41, _water_rows(0.0))))
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        assert len(members) == 2
        assert [m.member_index for m in members] == [0, 1]
        assert members[0].geometry.coordinates == members[1].geometry.coordinates

    def test_non_string_rejected(self) -> None:
        with pytest.raises(TypeError):
            eparse.parse_goat_ensemble(None, ensemble_xyz_text="x", atoms=ATOMS)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            eparse.parse_goat_ensemble("x", ensemble_xyz_text=None, atoms=ATOMS)  # type: ignore[arg-type]

    def test_empty_table_parses_empty(self) -> None:
        log = (
            "         # Final ensemble info #\n"
            "         Conformer     Energy     Degen.   % total   % cumul.\n"
            "                       (kcal/mol)\n"
            "         ------------------------------------------------------\n"
            "         Lowest energy conformer    : -76.410000 Eh\n"
        )
        assert eparse.parse_goat_ensemble(log, ensemble_xyz_text="", atoms=ATOMS) == ()


class TestOrderingHelpers:
    """Energy-table and canonical-ordering conveniences."""

    def test_energy_table(self) -> None:
        _, xyz = _two_member_pair()
        log, _ = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        table = eparse.ensemble_energy_table(members)
        assert table[0] == pytest.approx(-76.41)
        assert table[1] == pytest.approx(-76.40)

    def test_energy_table_duplicate_rejected(self) -> None:
        log, xyz = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        with pytest.raises(ValueError, match="duplicate"):
            eparse.ensemble_energy_table(tuple(members) + tuple(members))

    def test_member_ordering_sorted(self) -> None:
        log, xyz = _two_member_pair()
        members = eparse.parse_goat_ensemble(log, ensemble_xyz_text=xyz, atoms=ATOMS)
        assert eparse.member_ordering(members) == (0, 1)
        assert eparse.member_ordering(()) == ()


class TestGoatTrajectoryFacts:
    """Best-effort trajectory facts over real-grammar fixtures."""

    def test_facts(self) -> None:
        log, _ = _two_member_pair()
        facts = eparse.goat_trajectory_facts(log)
        assert facts["member_count"] == 2
        assert facts["member_indices"] == [0, 1]
        assert facts["energy_min_hartree"] == pytest.approx(-76.41)
        assert facts["energy_max_hartree"] == pytest.approx(-76.40)
        assert facts["truncated"] is False

    def test_no_table_tolerant(self) -> None:
        facts = eparse.goat_trajectory_facts("")
        assert facts["member_count"] == 0
        assert facts["member_indices"] == []
        assert facts["truncated"] is True
        garbage = eparse.goat_trajectory_facts("total garbage ((( \n")
        assert garbage["member_count"] == 0
