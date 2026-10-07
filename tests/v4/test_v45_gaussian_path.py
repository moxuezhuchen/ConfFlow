#!/usr/bin/env python3

"""V4-5 Gaussian reaction-path (IRC) helper tests.

Every IRC log fixture below speaks real Gaussian 16 grammar — direction
announcements (``Point Number N in FORWARD/REVERSE path direction.``),
point headers (``Point Number: N  Path Number: M``), ``Input orientation``
tables with atomic numbers, ``SCF Done`` energies, and the
``Reaction path calculation complete.`` marker — verified line-for-line
against vendor IRC logs (``/opt/g16/tests/amd64/test0313.log`` and
siblings).  Fixtures are synthetic in content but grammatical in form;
no test shells out to Gaussian.  Error assertions always require the
``native_input_error:`` prefix, mirroring
:mod:`confflow.programs.gaussian.rendering`.
"""

from __future__ import annotations

import pytest

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


_Z_BY_SYMBOL = {"H": 1, "He": 2, "C": 6, "N": 7, "O": 8, "F": 9}


def _orientation_block(
    rows: tuple[tuple[str, float, float, float], ...],
) -> list[str]:
    """Format a real ``Input orientation`` table for fixture rows."""
    lines = [
        "Input orientation:",
        " ---------------------------------------------------------------------",
        " Center     Atomic      Atomic             Coordinates (Angstroms)",
        " Number     Number       Type             X           Y           Z",
        " ---------------------------------------------------------------------",
    ]
    for center, (symbol, x, y, z) in enumerate(rows, start=1):
        lines.append(
            f"      {center}          {_Z_BY_SYMBOL[symbol]}           0"
            f"        {x:.6f}    {y:.6f}   {z:.6f}"
        )
    lines.append(" ---------------------------------------------------------------------")
    return lines


def _point_block(
    direction: str,
    number: int,
    rows: tuple[tuple[str, float, float, float], ...],
    energy: float | None,
) -> list[str]:
    """Format one real IRC point: announcement, header, table, energy."""
    lines = [
        f" Point Number  {number} in {direction.upper()} path direction.",
        f" Point Number:   {number}          Path Number:   1",
    ]
    lines.extend(_orientation_block(rows))
    if energy is not None:
        lines.append(f" SCF Done:  E(RHF) =  {energy!r}     A.U. after   13 cycles")
    return lines


def _irc_log(
    points: dict[str, list[tuple[int, tuple, float | None]]],
    *,
    complete: bool = True,
) -> str:
    """Assemble a real-grammar IRC log from per-direction point lists."""
    lines = [
        " Preamble SCF of the transition-state guess (not endpoint data).",
        " SCF Done:  E(RHF) =  -75.0000000000     A.U. after   13 cycles",
    ]
    for direction in ("forward", "reverse"):
        for number, rows, energy in points.get(direction, []):
            lines.extend(_point_block(direction, number, rows, energy))
        if direction == "forward" and "forward" in points:
            lines.append(" Calculation of FORWARD path complete.")
            if "reverse" in points:
                lines.append(" Beginning calculation of the REVERSE path.")
    if complete:
        if "reverse" in points:
            lines.append(" Calculation of REVERSE path complete.")
        lines.append(" Reaction path calculation complete.")
    lines.append(" Normal termination of Gaussian 16.")
    return "\n".join(lines)


LOG_BOTH = _irc_log(
    {
        "forward": [(1, FORWARD_ROWS, -76.2), (12, FORWARD_ROWS, FORWARD_ENERGY)],
        "reverse": [(3, REVERSE_ROWS, REVERSE_ENERGY)],
    }
)
LOG_FORWARD_ONLY = _irc_log({"forward": [(12, FORWARD_ROWS, FORWARD_ENERGY)]})


def _expected_coords(
    rows: tuple[tuple[str, float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    """Return the coordinate triples of handcrafted fixture rows."""
    return tuple((x, y, z) for _, x, y, z in rows)


def _assert_native_input_error(excinfo: pytest.ExceptionInfo[BaseException]) -> None:
    """Require the ``native_input_error:`` prefix on an error message."""
    assert "native_input_error:" in str(excinfo.value)


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
    """Endpoint parsing over real-grammar fixtures."""

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
        assert reverse.converged is True
        assert reverse.point_ordinal == 3

    def test_endpoint_is_max_point_with_geometry(self) -> None:
        # Forward spans points 1 and 12: the endpoint is point 12 with
        # its own table and energy, never an earlier point.
        (forward,) = [
            endpoint
            for endpoint in gaussian_path.parse_irc_endpoints(LOG_BOTH, atoms=WATER_ATOMS)
            if endpoint.direction == "forward"
        ]
        assert forward.point_ordinal == 12
        assert forward.energy_hartree == FORWARD_ENERGY

    def test_forward_only_direction_parses_singleton(self) -> None:
        # A forward-only path parses one endpoint; the missing reverse
        # direction is the result profile's fail-closed concern, not the
        # parser's (no direction is ever inferred).
        (endpoint,) = gaussian_path.parse_irc_endpoints(LOG_FORWARD_ONLY, atoms=WATER_ATOMS)
        assert endpoint.direction == "forward"
        assert endpoint.point_ordinal == 12

    def test_opt_freq_spillover_never_leaks_in(self) -> None:
        # The preamble SCF (-75.0, transition-state guess section) is
        # outside the IRC scope and must not become an endpoint energy.
        (endpoint,) = gaussian_path.parse_irc_endpoints(LOG_FORWARD_ONLY, atoms=WATER_ATOMS)
        assert endpoint.energy_hartree == FORWARD_ENERGY

    def test_malformed_energy_is_none(self) -> None:
        log = _irc_log({"forward": [(4, FORWARD_ROWS, None)]})
        log = log.replace(f" SCF Done:  E(RHF) =  {None!r}     A.U. after   13 cycles", "")
        (endpoint,) = gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        assert endpoint.energy_hartree is None
        assert endpoint.converged is True

    def test_absent_energy_is_none(self) -> None:
        (endpoint,) = gaussian_path.parse_irc_endpoints(
            _irc_log({"forward": [(4, FORWARD_ROWS, None)]}), atoms=WATER_ATOMS
        )
        assert endpoint.energy_hartree is None

    def test_incomplete_path_endpoints_unconverged(self) -> None:
        log = _irc_log({"forward": [(4, FORWARD_ROWS, FORWARD_ENERGY)]}, complete=False)
        (endpoint,) = gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        assert endpoint.converged is False
        assert endpoint.energy_hartree == FORWARD_ENERGY

    def test_empty_log_returns_empty(self) -> None:
        assert gaussian_path.parse_irc_endpoints("", atoms=WATER_ATOMS) == ()
        assert gaussian_path.parse_irc_endpoints("no markers here\n", atoms=WATER_ATOMS) == ()

    def test_atom_count_mismatch_raises(self) -> None:
        log = _irc_log({"forward": [(4, FORWARD_ROWS[:2], FORWARD_ENERGY)]})
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_symbol_mismatch_raises(self) -> None:
        rows = (("O", 0.0, 0.0, 0.0), ("H", 0.76, 0.59, 0.0), ("He", 0.76, -0.59, 0.0))
        log = _irc_log({"forward": [(4, rows, FORWARD_ENERGY)]})
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_unknown_atomic_number_raises(self) -> None:
        lines = _orientation_block(FORWARD_ROWS)
        lines[5] = "      1          0           0        0.000000    0.000000    0.000000"
        log = "\n".join(
            [
                " Point Number  4 in FORWARD path direction.",
                " Point Number:   4          Path Number:   1",
                *lines,
                f" SCF Done:  E(RHF) =  {FORWARD_ENERGY!r}     A.U. after   13 cycles",
                " Reaction path calculation complete.",
            ]
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_second_path_number_raises(self) -> None:
        base = _irc_log({"forward": [(4, FORWARD_ROWS, FORWARD_ENERGY)]})
        base = base.replace(
            " Point Number:   4          Path Number:   1",
            " Point Number:   4          Path Number:   2",
        )
        extra = "\n".join(
            [
                " Point Number  5 in FORWARD path direction.",
                " Point Number:   5          Path Number:   1",
                *_orientation_block(FORWARD_ROWS),
                f" SCF Done:  E(RHF) =  {FORWARD_ENERGY!r}     A.U. after   13 cycles",
            ]
        )
        marker = " Reaction path calculation complete."
        assert marker in base
        log = base.replace(marker, extra + "\n" + marker)
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(log, atoms=WATER_ATOMS)
        _assert_native_input_error(excinfo)

    def test_unannounced_point_header_raises(self) -> None:
        log = "\n".join(
            [
                " Point Number:   7          Path Number:   1",
                *_orientation_block(FORWARD_ROWS),
            ]
        )
        with pytest.raises(ValueError) as excinfo:
            gaussian_path.parse_irc_endpoints(
                " Point Number  1 in FORWARD path direction.\n" + log, atoms=WATER_ATOMS
            )
        _assert_native_input_error(excinfo)

    def test_non_string_log_raises(self) -> None:
        with pytest.raises(TypeError):
            gaussian_path.parse_irc_endpoints(None, atoms=WATER_ATOMS)  # type: ignore[arg-type]


class TestIrcTrajectoryFacts:
    """Best-effort trajectory summaries over real-grammar fixtures."""

    def test_full_log_facts(self) -> None:
        facts = gaussian_path.irc_trajectory_facts(LOG_BOTH)
        assert facts["forward_points"] == 2
        assert facts["reverse_points"] == 1
        assert facts["energies_hartree"][0] == -75.0
        assert facts["energies_hartree"][-2:] == [FORWARD_ENERGY, REVERSE_ENERGY]
        assert facts["units"] == "angstrom"
        assert facts["truncated"] is False
        assert facts["path_complete"] is True
        assert facts["directions"] == ["forward", "reverse"]

    def test_incomplete_log_is_truncated(self) -> None:
        facts = gaussian_path.irc_trajectory_facts(
            _irc_log({"forward": [(4, FORWARD_ROWS, FORWARD_ENERGY)]}, complete=False)
        )
        assert facts["forward_points"] == 1
        assert facts["truncated"] is True
        assert facts["path_complete"] is False

    def test_empty_log_facts(self) -> None:
        assert gaussian_path.irc_trajectory_facts("") == {
            "forward_points": 0,
            "reverse_points": 0,
            "energies_hartree": [],
            "units": "angstrom",
            "truncated": True,
            "directions": [],
            "path_complete": False,
        }

    def test_unrelated_text_never_raises(self) -> None:
        facts = gaussian_path.irc_trajectory_facts("SCF Done: garbage\n###\n")
        assert facts["truncated"] is True
        assert facts["forward_points"] == 0

    def test_non_string_facts_raise(self) -> None:
        with pytest.raises(TypeError):
            gaussian_path.irc_trajectory_facts(None)  # type: ignore[arg-type]


QST_SUCCESS_ENERGY = -76.0580000000


def _qst_orientation_block(
    rows: tuple[tuple[str, float, float, float], ...],
) -> list[str]:
    """Format a real ``Standard orientation`` table for fixture rows."""
    lines = [
        "                         Standard orientation:",
        " ---------------------------------------------------------------------",
        " Center     Atomic      Atomic             Coordinates (Angstroms)",
        " Number     Number       Type             X           Y           Z",
        " ---------------------------------------------------------------------",
    ]
    for center, (symbol, x, y, z) in enumerate(rows, start=1):
        lines.append(
            f"      {center}          {_Z_BY_SYMBOL[symbol]}           0"
            f"        {x:.6f}    {y:.6f}   {z:.6f}"
        )
    lines.append(" ---------------------------------------------------------------------")
    return lines


def _qst_log(*, converged: bool = True) -> str:
    """Assemble a minimal vendor-credible QST2 output log.

    Grammar-faithful to real Gaussian 16 QST2 output (vendor
    ``test1048.log``: ``Standard orientation`` tables with atomic
    numbers, ``SCF Done`` energies, ``-- Stationary point found.``,
    ``Normal termination``); content is synthetic, never copied vendor
    text.  The error shape mirrors a live adapter-rendered QST2 run
    (real g16 enters ``Berny optimization ... Search for a saddle
    point ... LST/QST climbing`` then ``Error termination via Lnk1e``).
    """
    lines = [
        " Entering Gaussian System, Link 0=g16",
        " Gaussian Test Job: synthetic QST2 opt",
        *_qst_orientation_block(FORWARD_ROWS),
        f" SCF Done:  E(RHF) =  {QST_SUCCESS_ENERGY!r}     A.U. after   12 cycles",
    ]
    if converged:
        lines.append("    -- Stationary point found.")
        lines.append(" Normal termination of Gaussian 16")
    else:
        lines.append(" LST/QST climbing along tangent vector")
        lines.append(" Error termination via Lnk1e")
    return "\n".join(lines) + "\n"


class TestQstOutputParsing:
    """QST-flavored OUTPUT parsing against real Gaussian 16 log grammar.

    R2.3d retired QST rendering; these two tests stay because they
    exercise the retained generic output parser (termination, energies,
    final geometry) on QST-flavored vendor grammar, not the deleted
    renderer.  Adapter-level QST materialization tests are retired
    with the renderer (G18).
    """

    def test_successful_qst2_output_parses(self) -> None:
        from confflow.programs.gaussian import parsing as gaussian_parsing

        text = _qst_log(converged=True)
        assert gaussian_parsing.termination_reached(text) is True
        energies, sources = gaussian_parsing.parse_energies(text)
        assert energies["electronic"] == QST_SUCCESS_ENERGY
        assert sources["electronic"] == "scf_done"
        geometry = gaussian_parsing.parse_final_geometry(text)
        assert geometry is not None
        atoms, coords = geometry
        assert atoms == WATER_ATOMS
        assert coords == _expected_coords(FORWARD_ROWS)

    def test_error_terminated_qst2_output_is_not_terminated(self) -> None:
        from confflow.programs.gaussian import parsing as gaussian_parsing

        text = _qst_log(converged=False)
        assert gaussian_parsing.termination_reached(text) is False
        energies, _ = gaussian_parsing.parse_energies(text)
        assert energies["electronic"] == QST_SUCCESS_ENERGY
