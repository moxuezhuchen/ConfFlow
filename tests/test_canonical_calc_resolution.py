"""Compatibility tests for canonical calc-step resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.config.canonical.issues import ConfigValidationError
from confflow.config.canonical.resolve import resolve_calc_step, resolve_global_options
from confflow.config.models import CalcStepParams, GlobalOptions


def test_resolve_global_options_preserves_v2_mapping():
    raw = {"iprog": "orca", "itask": "sp", "keyword": "HF", "cores_per_task": 4}

    assert resolve_global_options(raw) == GlobalOptions.from_mapping(raw)


def test_resolve_global_options_wraps_v2_rule_errors_with_global_path():
    with pytest.raises(ConfigValidationError, match="cores_per_task") as caught:
        resolve_global_options({"cores_per_task": 0})

    assert caught.value.issue.path == "global"


def test_resolve_calc_step_preserves_v2_runtime_payload():
    global_options = GlobalOptions.from_mapping({"iprog": "orca", "itask": "sp", "keyword": "HF"})
    params = {"itask": "sp", "keyword": "HF", "cores_per_task": 4}

    assert (
        resolve_calc_step(params, global_options).to_runtime_dict()
        == CalcStepParams.from_params(params, global_options).to_runtime_dict()
    )


def test_resolve_calc_step_forwards_runtime_input_checkpoint_dir():
    result = resolve_calc_step(
        {"iprog": "orca", "itask": "sp", "keyword": "HF"},
        GlobalOptions.from_mapping({}),
        input_chk_dir="previous/chks",
    )

    assert result.execution.input_chk_dir == "previous/chks"


def test_resolve_calc_step_wraps_v2_rule_errors_with_structured_path():
    with pytest.raises(ConfigValidationError, match="Unsupported calc task") as caught:
        resolve_calc_step(
            {"iprog": "orca", "itask": "bad", "keyword": "HF"},
            GlobalOptions.from_mapping({}),
        )

    assert caught.value.issue.path == "steps.calc"


def test_production_calc_resolution_uses_only_the_canonical_seam():
    """No workflow consumer may bypass the compatibility-preserving seam."""
    root = Path("confflow")
    seam = root / "config" / "canonical" / "resolve.py"
    bypasses = [
        path
        for path in root.rglob("*.py")
        if path != seam and "CalcStepParams.from_params" in path.read_text(encoding="utf-8")
    ]

    assert bypasses == []

    allowed_global_resolution = {root / "config" / "models.py"}
    allowed_global_resolution.update((root / "config" / "canonical").rglob("*.py"))
    global_bypasses = [
        path
        for path in root.rglob("*.py")
        if "GlobalOptions.from_mapping" in path.read_text(encoding="utf-8")
        and path not in allowed_global_resolution
    ]

    assert global_bypasses == []
