"""Boundary tests for the neutral refinement result type."""

from __future__ import annotations

from confflow.blocks.refine.result import RefineResult as LegacyRefineResult
from confflow.calc.result import RefineResult


def test_refine_result_has_one_neutral_owner_with_legacy_reexport() -> None:
    result = RefineResult(True, "out.xyz", 2, "ok")
    assert LegacyRefineResult is RefineResult
    assert result.output_path == "out.xyz"
    assert result.kept_count == 2
