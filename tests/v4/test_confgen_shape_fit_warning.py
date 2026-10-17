#!/usr/bin/env python3

"""Input shape-fit warning: pure fit, explorer stats hook, and search result diagnostic."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.execution import confgen_search_run as search_run
from confflow.science.confgen.coordination.perception import shape_fit
from tests.v4.test_confgen_search_step import _driving_metal, _native, _run, _write_fake

_TETRA = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], dtype=float)
_TETRA = 2.0 * _TETRA / np.linalg.norm(_TETRA, axis=1)[:, None]  # 2.0 Å metal-donor distance
_SQUARE = 2.0 * np.array([[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], dtype=float)
_SITES = ("s0", "s1", "s2", "s3")


def _geometry(donors: np.ndarray) -> np.ndarray:
    return np.vstack([np.zeros((1, 3)), donors])


def test_shape_fit_prefers_tetrahedral_for_tetrahedral_donors() -> None:
    fit = shape_fit(_geometry(_TETRA), 0, (1, 2, 3, 4), _SITES, "square_planar")
    assert fit is not None
    assert fit["declared"] == "square_planar" and fit["best"] == "tetrahedral"
    assert fit["declared_rmsd"] - fit["best_rmsd"] >= 0.2
    assert set(fit["rmsd"]) == {"tetrahedral", "square_planar"}
    assert all(type(v) is float for v in fit["rmsd"].values())


def test_shape_fit_keeps_declared_for_square_planar_donors() -> None:
    fit = shape_fit(_geometry(_SQUARE), 0, (1, 2, 3, 4), _SITES, "square_planar")
    assert fit is not None and fit["best"] == "square_planar"
    assert fit["best_rmsd"] == pytest.approx(fit["declared_rmsd"])


def test_shape_fit_none_outside_cn_table() -> None:
    coords = _geometry(_TETRA[:3])
    assert shape_fit(coords, 0, (1, 2, 3), ("s0", "s1", "s2"), "trigonal_planar") is None


def _fit(declared: str, best: str, gap: float) -> dict[str, Any]:
    return {
        "declared": declared,
        "declared_rmsd": 1.14,
        "best": best,
        "best_rmsd": round(1.14 - gap, 2),
        "rmsd": {declared: 1.14, best: round(1.14 - gap, 2)},
    }


def test_shape_fit_diagnostic_warns_only_on_clear_mismatch() -> None:
    item = _item()
    warn = search_run._shape_fit_diagnostic(_fit("square_planar", "tetrahedral", 1.0), "s", item)
    assert warn is not None and warn.code == "confgen_input_shape_mismatch"
    assert warn.severity is DiagnosticSeverity.WARNING
    assert "tetrahedral" in warn.message and "square_planar" in warn.message
    assert "coordination.shapes" in warn.message
    assert dict(warn.details)["best"] == "tetrahedral"
    small = _fit("square_planar", "tetrahedral", 0.1)
    assert search_run._shape_fit_diagnostic(small, "s", item) is None
    same = _fit("square_planar", "square_planar", 0.0)
    assert search_run._shape_fit_diagnostic(same, "s", item) is None
    assert search_run._shape_fit_diagnostic(None, "s", item) is None


def _item() -> SimpleNamespace:
    return SimpleNamespace(id="cg-item:id", logical_key="cg-item")


def test_search_result_carries_shape_fit_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = search_run._read_validated

    def _with_fit(fit: dict[str, Any]) -> Any:
        def _patched(*args: Any) -> Any:
            totals, payload, ordered, frames = real(*args)
            payload["search"]["input_shape_fit"] = fit
            return totals, payload, ordered, frames

        return _patched

    mismatch = _fit("square_planar", "tetrahedral", 1.0)
    monkeypatch.setattr(search_run, "_read_validated", _with_fit(mismatch))
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed"
    warn = [d for d in out.diagnostics if d.code == "confgen_input_shape_mismatch"]
    assert len(warn) == 1 and warn[0].severity is DiagnosticSeverity.WARNING

    matching = _fit("square_planar", "square_planar", 0.0)
    monkeypatch.setattr(search_run, "_read_validated", _with_fit(matching))
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed"
    assert not [d for d in out.diagnostics if d.code == "confgen_input_shape_mismatch"]
