#!/usr/bin/env python3

"""V4-5 Gaussian reaction-path (IRC) + QST helper tests.

Every log fixture below is a handcrafted string in the minimal dialect
owned by :mod:`confflow.programs.gaussian.path` (banner lines, endpoint
geometry blocks, energy/convergence lines). Nothing here is real
Gaussian output and no test shells out to Gaussian; route/QST coverage
is pure string-to-facts testing. Error assertions always require the
``native_input_error:`` prefix, mirroring
:mod:`confflow.programs.gaussian.rendering`.
"""

from __future__ import annotations

import pytest

from confflow.domain.structure import StructureRecord
from confflow.programs.gaussian import named as gaussian_named
from confflow.programs.gaussian import path as gaussian_path

WATER_ATOMS = ("O", "H", "H")

FORWARD_ROWS = (
    ("O", 0.0, 0.0, 0.0),
    ("H", 0.76, 0.59, 0.0),
    ("H", 0.76, -0.59, 0.0),
)

REVERSE_ROWS = (
    ("O", 0.05, 0.0, 0.0),
    ("H", -0.71, 0.59, 0.0),
    ("H", -0.71, -0.59, 0.0),
)

FORWARD_ENERGY = -76.123456789
REVERSE_ENERGY = -76.111111111


def _coordinate_lines(
    rows: tuple[tuple[str, float, float, float], ...],
) -> list[str]:
    """Format dialect geometry rows for a fixture block."""
    return [f"{symbol} {x!r} {y!r} {z!r}" for symbol, x, y, z in rows]


def _forward_section() -> list[str]:
    """Build the handcrafted forward endpoint section lines."""
    return (
        [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
        + _coordinate_lines(FORWARD_ROWS)
        + [
            f"{gaussian_path.POINT_PREFIX} 12",
            f"{gaussian_path.ENERGY_PREFIX} {FORWARD_ENERGY!r}",
            f"{gaussian_path.CONVERGED_PREFIX} YES",
        ]
    )


def _reverse_section() -> list[str]:
    """Build the handcrafted reverse endpoint section lines."""
    return (
        [gaussian_path.REVERSE_BANNER, gaussian_path.GEOMETRY_HEADER]
        + _coordinate_lines(REVERSE_ROWS)
        + [
            f"{gaussian_path.ENERGY_PREFIX} {REVERSE_ENERGY!r}",
            f"{gaussian_path.CONVERGED_PREFIX} NO",
        ]
    )


def _wrap_log(body: list[str]) -> str:
    """Wrap fixture sections with neutral preamble/trailer lines."""
    return "\n".join(
        ["DIALECT FIXTURE PREAMBLE (not real Gaussian output)"]
        + body
        + ["DIALECT FIXTURE TRAILER (not real Gaussian output)"]
    )


LOG_BOTH = _wrap_log(_forward_section() + _reverse_section())
LOG_REVERSED = _wrap_log(_reverse_section() + _forward_section())


def _expected_coords(
    rows: tuple[tuple[str, float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    """Return the coordinate triples of handcrafted fixture rows."""
    return tuple((x, y, z) for _, x, y, z in rows)


def _assert_native_input_error(excinfo: pytest.ExceptionInfo[BaseException]) -> None:
    """Require the ``native_input_error:`` prefix on an error message."""
    assert "native_input_error:" in str(excinfo.value)


def _slot(
    slot_id: str,
    atoms: tuple[str, ...] = WATER_ATOMS,
    coords: tuple[tuple[float, float, float], ...] = (
        (0.0, 0.0, 0.0),
        (0.76, 0.59, 0.0),
        (-0.76, 0.59, 0.0),
    ),
    charge: int | None = 0,
    multiplicity: int | None = 1,
) -> StructureRecord:
    """Build a QST slot structure record."""
    return StructureRecord(
        id=slot_id,
        atoms=atoms,
        coordinates=coords,
        charge=charge,
        multiplicity=multiplicity,
    )


REACTANT_ATOMS = ("O", "H", "H")
REACTANT_COORDS = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (-0.76, 0.59, 0.0))
PRODUCT_COORDS = ((0.1, 0.0, -0.0), (0.86, 0.59, 0.0), (-0.66, 0.59, 0.0))
GUESS_COORDS = ((0.05, 0.0, 0.0), (0.8, 0.6, 0.1), (-0.7, 0.55, -0.05))

EXPECTED_QST2 = "\n".join(
    [
        "reactant",
        "0 1",
        "O 0.00000000 0.00000000 0.00000000",
        "H 0.76000000 0.59000000 0.00000000",
        "H -0.76000000 0.59000000 0.00000000",
        "",
        "product",
        "0 1",
        "O 0.10000000 0.00000000 0.00000000",
        "H 0.86000000 0.59000000 0.00000000",
        "H -0.66000000 0.59000000 0.00000000",
    ]
    + [""]
)

EXPECTED_QST3 = "\n".join(
    [
        "reactant",
        "0 1",
        "O 0.00000000 0.00000000 0.00000000",
        "H 0.76000000 0.59000000 0.00000000",
        "H -0.76000000 0.59000000 0.00000000",
        "",
        "product",
        "0 1",
        "O 0.10000000 0.00000000 0.00000000",
        "H 0.86000000 0.59000000 0.00000000",
        "H -0.66000000 0.59000000 0.00000000",
        "",
        "guess",
        "0 1",
        "O 0.05000000 0.00000000 0.00000000",
        "H 0.80000000 0.60000000 0.10000000",
        "H -0.70000000 0.55000000 -0.05000000",
    ]
    + [""]
)


class TestParseIrcRoute:
    """IRC route-line direction matrix."""

    def test_rcfc_votes_both(self) -> None:
        assert gaussian_path.parse_irc_route("B3LYP/6-31G* IRC(RCFC)") == {
            "mode": "both",
            "raw_options": ("RCFC",),
        }

    def test_forward_vote(self) -> None:
        assert gaussian_path.parse_irc_route("#p B3LYP IRC(Forward) Opt") == {
            "mode": "forward",
            "raw_options": ("Forward",),
        }

    def test_reverse_vote_case_insensitive(self) -> None:
        assert gaussian_path.parse_irc_route("b3lyp irc(reverse) opt") == {
            "mode": "reverse",
            "raw_options": ("reverse",),
        }

    def test_bare_irc_defaults_both(self) -> None:
        assert gaussian_path.parse_irc_route("B3LYP/6-31G* IRC Opt Freq") == {
            "mode": "both",
            "raw_options": (),
        }

    def test_empty_parens_equal_bare(self) -> None:
        assert gaussian_path.parse_irc_route("B3LYP IRC() Opt") == {
            "mode": "both",
            "raw_options": (),
        }

    def test_key_value_options_pass_through(self) -> None:
        assert gaussian_path.parse_irc_route("B3LYP IRC(MaxPoints=20, StepSize=30)") == {
            "mode": "both",
            "raw_options": ("MaxPoints=20", "StepSize=30"),
        }

    def test_documented_bare_flags_pass_through(self) -> None:
        assert gaussian_path.parse_irc_route("B3LYP IRC(RCFC,RecalcFC)") == {
            "mode": "both",
            "raw_options": ("RCFC", "RecalcFC"),
        }
        assert gaussian_path.parse_irc_route("B3LYP IRC(CalcFC,Forward)") == {
            "mode": "forward",
            "raw_options": ("CalcFC", "Forward"),
        }

    def test_whitespace_folded_variant(self) -> None:
        assert gaussian_path.parse_irc_route("B3LYP IRC(RCF C)") == {
            "mode": "both",
            "raw_options": ("RCFC",),
        }

    def test_equals_form_single_token(self) -> None:
        assert gaussian_path.parse_irc_route("B3LYP IRC=RCFC") == {
            "mode": "both",
            "raw_options": ("RCFC",),
        }

    def test_non_irc_route_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_route("B3LYP/6-31G* Opt Freq")
        _assert_native_input_error(excinfo)

    def test_unknown_bare_token_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_route("B3LYP IRC(Frobnicate)")
        _assert_native_input_error(excinfo)

    def test_conflicting_directions_raise(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_route("B3LYP IRC(Forward,Reverse)")
        _assert_native_input_error(excinfo)
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_route("B3LYP IRC(RCFC,Forward)")
        _assert_native_input_error(excinfo)

    def test_empty_keyword_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_route("   ")
        _assert_native_input_error(excinfo)

    def test_unclosed_group_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_route("B3LYP IRC(RCFC")
        _assert_native_input_error(excinfo)

    def test_dangling_equals_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_route("B3LYP IRC=")
        _assert_native_input_error(excinfo)


class TestParseIrcEndpoints:
    """Endpoint parsing over handcrafted dialect fixtures."""

    def test_both_endpoints(self) -> None:
        endpoints = gaussian_path.parse_irc_endpoints(LOG_BOTH, atoms=WATER_ATOMS)
        assert [endpoint.direction for endpoint in endpoints] == ["forward", "reverse"]
        forward, reverse = endpoints
        assert forward.geometry.atoms == WATER_ATOMS
        assert forward.geometry.coordinates == _expected_coords(FORWARD_ROWS)
        assert forward.energy_hartree == FORWARD_ENERGY
        assert forward.converged is True
        assert forward.point_ordinal == 12
        assert reverse.geometry.atoms == WATER_ATOMS
        assert reverse.geometry.coordinates == _expected_coords(REVERSE_ROWS)
        assert reverse.energy_hartree == REVERSE_ENERGY
        assert reverse.converged is False
        assert reverse.point_ordinal is None

    def test_shuffled_section_order_invariance(self) -> None:
        first = gaussian_path.parse_irc_endpoints(LOG_BOTH, atoms=WATER_ATOMS)
        second = gaussian_path.parse_irc_endpoints(LOG_REVERSED, atoms=WATER_ATOMS)
        assert [endpoint.direction for endpoint in second] == ["reverse", "forward"]
        by_direction_first = {endpoint.direction: endpoint for endpoint in first}
        by_direction_second = {endpoint.direction: endpoint for endpoint in second}
        assert set(by_direction_first) == {"forward", "reverse"}
        for direction in ("forward", "reverse"):
            assert (
                by_direction_first[direction].geometry.coordinates
                == by_direction_second[direction].geometry.coordinates
            )
            assert (
                by_direction_first[direction].energy_hartree
                == by_direction_second[direction].energy_hartree
            )
            assert (
                by_direction_first[direction].converged == by_direction_second[direction].converged
            )

    def test_missing_banner_raises(self) -> None:
        log = _wrap_log(
            [gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
            + [f"{gaussian_path.ENERGY_PREFIX} -76.0"]
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_duplicate_direction_raises(self) -> None:
        log = _wrap_log(_forward_section() + _forward_section())
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_malformed_energy_is_none(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
            + [
                f"{gaussian_path.ENERGY_PREFIX} not-a-number",
                f"{gaussian_path.CONVERGED_PREFIX} YES",
            ]
        )
        (endpoint,) = gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        assert endpoint.energy_hartree is None
        assert endpoint.converged is True

    def test_absent_energy_is_none(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
            + [f"{gaussian_path.CONVERGED_PREFIX} YES"]
        )
        (endpoint,) = gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        assert endpoint.energy_hartree is None

    def test_converged_defaults_true(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
        )
        (endpoint,) = gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        assert endpoint.converged is True

    def test_empty_log_returns_empty(self) -> None:
        assert gaussian_path.parse_irc_endpoints("", atoms=WATER_ATOMS) == ()
        assert (
            gaussian_path.parse_irc_endpoints("no dialect markers here\n", atoms=WATER_ATOMS) == ()
        )

    def test_atom_count_mismatch_raises(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS[:2])
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_symbol_mismatch_raises(self) -> None:
        rows = (("O", 0.0, 0.0, 0.0), ("H", 0.76, 0.59, 0.0), ("He", 0.76, -0.59, 0.0))
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER] + _coordinate_lines(rows)
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_unknown_symbol_raises(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER, "Xx 0.0 0.0 0.0"]
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_section_without_geometry_raises(self) -> None:
        log = _wrap_log([gaussian_path.FORWARD_BANNER, f"{gaussian_path.ENERGY_PREFIX} -76.0"])
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_second_geometry_block_raises(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
            + [gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_bad_convergence_flag_raises(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
            + [f"{gaussian_path.CONVERGED_PREFIX} MAYBE"]
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_bad_point_ordinal_raises(self) -> None:
        for token in ("-1", "1.5", "twelve"):
            log = _wrap_log(
                [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
                + _coordinate_lines(FORWARD_ROWS)
                + [f"{gaussian_path.POINT_PREFIX} {token}"]
            )
            with pytest.raises(ValueError) as excinfo:
                gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
            _assert_native_input_error(excinfo)

    def test_ambiguous_banner_raises(self) -> None:
        log = _wrap_log(
            [
                f"{gaussian_path.FORWARD_BANNER} {gaussian_path.REVERSE_BANNER}",
                gaussian_path.GEOMETRY_HEADER,
            ]
            + _coordinate_lines(FORWARD_ROWS)
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_non_string_log_raises(self) -> None:
        with pytest.raises(TypeError):
            gaussian_path.parse_irc_endpoints(None, atoms=WATER_ATOMS)  # type: ignore[arg-type]


class TestIrcTrajectoryFacts:
    """Best-effort trajectory summaries over dialect fixtures."""

    def test_full_log_facts(self) -> None:
        assert gaussian_path.irc_trajectory_facts(LOG_BOTH) == {
            "forward_points": 1,
            "reverse_points": 1,
            "energies_hartree": [FORWARD_ENERGY, REVERSE_ENERGY],
            "units": "angstrom",
            "truncated": False,
            "directions": ["forward", "reverse"],
        }

    def test_reversed_log_facts_follow_encounter_order(self) -> None:
        facts = gaussian_path.irc_trajectory_facts(LOG_REVERSED)
        assert facts["forward_points"] == 1
        assert facts["reverse_points"] == 1
        assert facts["energies_hartree"] == [REVERSE_ENERGY, FORWARD_ENERGY]
        assert facts["truncated"] is False
        assert facts["directions"] == ["reverse", "forward"]

    def test_log_cut_mid_block_is_truncated(self) -> None:
        log = "\n".join(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS[:1])
        )
        facts = gaussian_path.irc_trajectory_facts(log)
        assert facts["forward_points"] == 0
        assert facts["reverse_points"] == 0
        assert facts["energies_hartree"] == []
        assert facts["truncated"] is True

    def test_malformed_energy_skipped_without_raise(self) -> None:
        log = _wrap_log(
            [gaussian_path.FORWARD_BANNER, gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
            + [f"{gaussian_path.ENERGY_PREFIX} garbage"]
        )
        facts = gaussian_path.irc_trajectory_facts(log)
        assert facts["forward_points"] == 1
        assert facts["energies_hartree"] == []
        assert facts["truncated"] is True

    def test_empty_log_facts(self) -> None:
        assert gaussian_path.irc_trajectory_facts("") == {
            "forward_points": 0,
            "reverse_points": 0,
            "energies_hartree": [],
            "units": "angstrom",
            "truncated": False,
            "directions": [],
        }

    def test_unrelated_text_never_raises(self) -> None:
        facts = gaussian_path.irc_trajectory_facts("SCF Done: garbage\n###\n")
        assert facts["truncated"] is False
        assert facts["forward_points"] == 0

    def test_duplicate_banner_marks_truncated(self) -> None:
        facts = gaussian_path.irc_trajectory_facts(_wrap_log(_forward_section() * 2))
        assert facts["truncated"] is True
        assert facts["forward_points"] == 2

    def test_markers_without_banner_mark_truncated(self) -> None:
        log = _wrap_log(
            [gaussian_path.GEOMETRY_HEADER]
            + _coordinate_lines(FORWARD_ROWS)
            + [f"{gaussian_path.ENERGY_PREFIX} -76.0"]
        )
        facts = gaussian_path.irc_trajectory_facts(log)
        assert facts["truncated"] is True
        assert facts["forward_points"] == 0
        assert facts["energies_hartree"] == []

    def test_non_string_facts_raise(self) -> None:
        with pytest.raises(TypeError):
            gaussian_path.irc_trajectory_facts(None)  # type: ignore[arg-type]


class TestValidateQstSlots:
    """QST slot charge/multiplicity/count gates."""

    def test_matching_slots_pass(self) -> None:
        gaussian_named.validate_qst_slots(
            reactant=_slot("r"),
            product=_slot("p"),
            charge=0,
            multiplicity=1,
        )

    def test_undeclared_slot_values_inherit(self) -> None:
        gaussian_named.validate_qst_slots(
            reactant=_slot("r", charge=None, multiplicity=None),
            product=_slot("p", charge=None, multiplicity=None),
            guess=_slot("g", charge=None, multiplicity=None),
            charge=0,
            multiplicity=1,
        )

    def test_missing_reactant_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=None, product=_slot("p"), charge=0, multiplicity=1
            )
        _assert_native_input_error(excinfo)

    def test_charge_conflict_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r", charge=0),
                product=_slot("p", charge=1),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_multiplicity_conflict_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"),
                product=_slot("p"),
                guess=_slot("g", multiplicity=3),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_atom_count_mismatch_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"),
                product=_slot(
                    "p",
                    atoms=("O", "H"),
                    coords=((0.0, 0.0, 0.0), (0.76, 0.59, 0.0)),
                ),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_guess_count_mismatch_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"),
                product=_slot("p"),
                guess=_slot(
                    "g",
                    atoms=("O", "H", "H", "H"),
                    coords=(
                        (0.0, 0.0, 0.0),
                        (0.76, 0.59, 0.0),
                        (-0.76, 0.59, 0.0),
                        (0.0, 0.0, 1.0),
                    ),
                ),
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_unresolved_charge_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.validate_qst_slots(
                reactant=_slot("r"), product=_slot("p"), charge=None, multiplicity=1
            )
        _assert_native_input_error(excinfo)


class TestRenderQstMoleculeSpecs:
    """Golden QST2/QST3 section rendering."""

    def test_qst2_golden(self) -> None:
        assert (
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
            == EXPECTED_QST2
        )

    def test_qst3_golden(self) -> None:
        assert (
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                guess_atoms=REACTANT_ATOMS,
                guess_coords=GUESS_COORDS,
                charge=0,
                multiplicity=1,
            )
            == EXPECTED_QST3
        )

    def test_half_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                guess_atoms=REACTANT_ATOMS,
                guess_coords=None,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_unresolved_charge_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=None,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_coordinate_count_mismatch_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=REACTANT_COORDS[:2],
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_unknown_symbol_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=("O", "H", "Xx"),
                reactant_coords=REACTANT_COORDS,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)

    def test_non_finite_coordinate_raises(self) -> None:
        bad = ((0.0, 0.0, 0.0), (0.76, 0.59, float("inf")), (-0.76, 0.59, 0.0))
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.render_qst_molecule_specs(
                reactant_atoms=REACTANT_ATOMS,
                reactant_coords=bad,
                product_atoms=REACTANT_ATOMS,
                product_coords=PRODUCT_COORDS,
                charge=0,
                multiplicity=1,
            )
        _assert_native_input_error(excinfo)


class TestParseQstRoute:
    """QST flavor detection and route/slot mismatch gates."""

    def test_qst2_detected(self) -> None:
        assert gaussian_named.parse_qst_route("B3LYP/6-31G* QST2 Opt", has_guess=False) == "qst2"

    def test_qst3_detected_case_insensitive(self) -> None:
        assert gaussian_named.parse_qst_route("#p b3lyp qst3 opt", has_guess=True) == "qst3"

    def test_qst2_with_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST2 Opt", has_guess=True)
        _assert_native_input_error(excinfo)

    def test_qst3_without_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST3 Opt", has_guess=False)
        _assert_native_input_error(excinfo)

    def test_non_qst_route_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP Opt Freq", has_guess=False)
        _assert_native_input_error(excinfo)

    def test_bare_qst_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST Opt", has_guess=False)
        _assert_native_input_error(excinfo)

    def test_both_flavors_raise(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST2 QST3 Opt", has_guess=True)
        _assert_native_input_error(excinfo)

    def test_non_boolean_has_guess_raises(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            gaussian_named.parse_qst_route("B3LYP QST2 Opt", has_guess="yes")  # type: ignore[arg-type]
        _assert_native_input_error(excinfo)
