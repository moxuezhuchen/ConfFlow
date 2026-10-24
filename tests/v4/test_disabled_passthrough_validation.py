#!/usr/bin/env python3
"""Validation sees disabled passthrough steps through their effective upstream.

Validation runs before binding-graph rewriting, so the provenance and source
checks must follow disabled pure-passthrough steps themselves.  A disabled
passthrough is transparent: consumers bound to it are judged by the upstream
producer the graph rewires them to.  Genuine mismatches and disabled
non-passthrough producers keep failing closed.
"""

from __future__ import annotations

from typing import Any

from tests.v4._builders import (
    calc_step,
    compile_doc,
    passthrough_registry,
    reasons,
    transform_step,
    v4_doc,
)

PROVENANCE_INPUTS = {
    "structures": {"kind": "structure", "cardinality": "many", "charge": 0, "multiplicity": 1}
}
PLAIN_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def _without_defaults(document: dict[str, Any]) -> dict[str, Any]:
    """Drop the builder's run-level scientific defaults so provenance is required."""
    document["global"].pop("scientific_defaults", None)
    return document


def _describe(result: Any) -> list[tuple[Any, Any, str]]:
    return [(d.code, d.details.get("reason"), d.message) for d in result.errors]


def _provenance_doc(*, passthrough_enabled: bool) -> dict[str, Any]:
    return _without_defaults(
        v4_doc(
            [
                transform_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                transform_step(
                    "s_b",
                    enabled=passthrough_enabled,
                    bindings={"structure": {"source": {"step": "s_a", "port": "structures"}}},
                ),
                calc_step(
                    "s_c",
                    bindings={"structure": {"source": {"step": "s_b", "port": "structures"}}},
                ),
            ],
            inputs=PROVENANCE_INPUTS,
        )
    )


def test_calc_behind_disabled_passthrough_is_not_metadata_unavailable() -> None:
    result = compile_doc(
        _provenance_doc(passthrough_enabled=False), registry=passthrough_registry()
    )
    assert result.ok, _describe(result)


def test_calc_behind_enabled_passthrough_is_not_metadata_unavailable() -> None:
    result = compile_doc(_provenance_doc(passthrough_enabled=True), registry=passthrough_registry())
    assert result.ok, _describe(result)


def test_disabled_non_passthrough_producer_still_fails_closed() -> None:
    doc = _without_defaults(
        v4_doc(
            [
                transform_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_b",
                    enabled=False,
                    bindings={"structure": {"source": {"step": "s_a", "port": "structures"}}},
                ),
                calc_step(
                    "s_c",
                    bindings={"structure": {"source": {"step": "s_b", "port": "structures"}}},
                ),
            ],
            inputs=PROVENANCE_INPUTS,
        )
    )
    result = compile_doc(doc, registry=passthrough_registry())
    assert not result.ok
    assert "metadata_unavailable" in reasons(result.errors)


def _filter_doc(*, structure_from: str) -> dict[str, Any]:
    """Filter the structures of ``structure_from`` while binding results to ``s_calc``.

    ``structure_from`` is ``"disabled"`` (structures pass through a disabled
    passthrough fed by ``s_calc``), or ``"other"`` (structures come from a
    second, unrelated calculation).
    """
    steps: list[dict[str, Any]] = [
        calc_step("s_calc", bindings={"structure": {"source": {"run": "structures"}}}),
    ]
    if structure_from == "disabled":
        steps.append(
            transform_step(
                "s_dis",
                enabled=False,
                bindings={"structure": {"source": {"step": "s_calc", "port": "structures"}}},
            )
        )
        structure_source = {"step": "s_dis", "port": "structures"}
    else:
        steps.append(
            calc_step("s_other", bindings={"structure": {"source": {"run": "structures"}}})
        )
        structure_source = {"step": "s_other", "port": "structures"}
    steps.append(
        transform_step(
            "s_filter",
            kind="filter",
            bindings={
                "structure": {"source": structure_source},
                "results": {"source": {"step": "s_calc", "port": "results"}},
            },
        )
    )
    return v4_doc(steps, inputs=PLAIN_INPUTS)


def test_results_source_matches_through_disabled_passthrough() -> None:
    result = compile_doc(_filter_doc(structure_from="disabled"), registry=passthrough_registry())
    assert result.ok, _describe(result)


def test_results_source_mismatch_is_still_reported() -> None:
    result = compile_doc(_filter_doc(structure_from="other"), registry=passthrough_registry())
    assert "filter_results_source_mismatch" in reasons(result.errors), _describe(result)
