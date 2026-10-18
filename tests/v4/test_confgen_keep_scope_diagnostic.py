#!/usr/bin/env python3

"""Keep-scope diagnostic: ``keep`` widened to every coordination class is reported, not silent."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.execution import confgen_search_run as search_run


def _item() -> SimpleNamespace:
    return SimpleNamespace(id="cg-item:id", logical_key="cg-item")


def _stats(scope: str | None, template: str | None = "square_planar") -> dict[str, Any]:
    return {
        "template": template,
        "arrangement_scope": scope,
        "targets_searched": ["t00", "t01"],
    }


def test_keep_scope_warns_when_input_fits_no_class() -> None:
    warn = search_run._keep_scope_diagnostic(
        _stats("all_classes_input_unclassified", "tetrahedral"), "s", _item()
    )
    assert warn is not None and warn.code == "confgen_keep_scope_widened"
    assert warn.severity is DiagnosticSeverity.WARNING
    assert "tetrahedral" in warn.message
    assert "coordination.shapes: [input]" in warn.message
    assert "all admissible coordination classes" in warn.message
    assert "targets: t00, t01" in warn.message
    details = dict(warn.details)
    assert details == {
        "template": "tetrahedral",
        "arrangement_scope": "all_classes_input_unclassified",
        "targets_searched": ("t00", "t01"),
    }


@pytest.mark.parametrize("scope", ["input_arrangement", "all_classes", None])
def test_keep_scope_silent_for_other_scopes(scope: str | None) -> None:
    assert search_run._keep_scope_diagnostic(_stats(scope), "s", _item()) is None


def test_keep_scope_silent_when_keys_missing() -> None:
    assert search_run._keep_scope_diagnostic({}, "s", _item()) is None
    assert search_run._keep_scope_diagnostic({"template": "octahedral"}, "s", _item()) is None
    assert search_run._keep_scope_diagnostic(None, "s", _item()) is None
