#!/usr/bin/env python3

"""V4-5 Gaussian output-parser helper tests (IRC/QST retired).

Fixtures are synthetic in content but grammatical in form;
no test shells out to Gaussian.  Error assertions always require the
``native_input_error:`` prefix, mirroring
:mod:`confflow.programs.gaussian.rendering`.
"""

from __future__ import annotations

WATER_ATOMS = ("O", "H", "H")

FORWARD_ROWS = (
    ("O", 0.0, 0.0, 0.0),
    ("H", 0.76, 0.59, 0.0),
    ("H", 0.76, -0.59, 0.0),
)

_Z_BY_SYMBOL = {"H": 1, "He": 2, "C": 6, "N": 7, "O": 8, "F": 9}


def _expected_coords(
    rows: tuple[tuple[str, float, float, float], ...],
) -> tuple[tuple[float, float, float], ...]:
    """Return the coordinate triples of handcrafted fixture rows."""
    return tuple((x, y, z) for _, x, y, z in rows)


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
