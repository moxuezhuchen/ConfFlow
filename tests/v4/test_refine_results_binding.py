"""Refine steps may optionally bind 'results'; a bound port follows the filter source rule."""

from __future__ import annotations

from tests.v4._builders import calc_step, compile_doc, reasons, transform_step, v4_doc

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def _freq_step(step_id: str = "s_freq") -> dict:
    return calc_step(
        step_id,
        bindings={"structure": {"source": {"run": "structures"}}},
        program="orca",
        role="freq",
        native={"keyword": "B3LYP Freq"},
        checks=["normal_termination", "frequencies_required"],
    )


def _refine_step(bindings: dict) -> dict:
    return transform_step("s_refine", kind="refine", bindings=bindings)


def test_refine_without_results_is_valid() -> None:
    doc = v4_doc(
        [
            _freq_step(),
            _refine_step({"structure": {"source": {"step": "s_freq", "port": "structures"}}}),
        ],
        inputs=STRUCTURE_INPUTS,
    )
    result = compile_doc(doc)
    assert result.ok, [(d.code, d.details.get("reason")) for d in result.errors]


def test_refine_with_results_from_structure_calculation_is_valid() -> None:
    doc = v4_doc(
        [
            _freq_step(),
            _refine_step(
                {
                    "structure": {"source": {"step": "s_freq", "port": "structures"}},
                    "results": {"source": {"step": "s_freq", "port": "results"}},
                }
            ),
        ],
        inputs=STRUCTURE_INPUTS,
    )
    result = compile_doc(doc)
    assert result.ok, [(d.code, d.details.get("reason")) for d in result.errors]


def test_refine_with_results_from_other_calculation_fails() -> None:
    doc = v4_doc(
        [
            _freq_step(),
            _freq_step("s_other"),
            _refine_step(
                {
                    "structure": {"source": {"step": "s_freq", "port": "structures"}},
                    "results": {"source": {"step": "s_other", "port": "results"}},
                }
            ),
        ],
        inputs=STRUCTURE_INPUTS,
    )
    result = compile_doc(doc)
    assert not result.ok
    assert "refine_results_source_mismatch" in reasons(result.errors)
    assert "filter_results_source_mismatch" not in reasons(result.errors)
