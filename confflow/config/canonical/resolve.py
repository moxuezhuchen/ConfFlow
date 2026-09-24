"""Canonical entry points for resolving typed configuration values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .issues import ConfigIssue, ConfigValidationError
from .types import CalcStepParams, GlobalOptions


def resolve_global_options(raw: Mapping[str, Any] | None) -> GlobalOptions:
    """Resolve a legacy global mapping through the canonical typed boundary."""
    try:
        mapping = None if raw is None else dict(raw)
        return GlobalOptions.from_mapping(mapping)
    except (TypeError, ValueError) as exc:
        raise ConfigValidationError(ConfigIssue("global", str(exc))) from exc


def resolve_calc_step(
    params: Mapping[str, Any],
    global_options: GlobalOptions,
    *,
    input_chk_dir: str | None = None,
    require_keyword: bool = True,
) -> CalcStepParams:
    """Resolve one calc step through the preserved v2 typed rules.

    ``require_keyword=False`` is used only by the V3 profile-aware path for a
    disabled step, which RFC §12 exempts from the keyword presence rule.
    """
    try:
        return CalcStepParams.from_params(
            dict(params),
            global_options,
            input_chk_dir=input_chk_dir,
            require_keyword=require_keyword,
        )
    except ValueError as exc:
        raise ConfigValidationError(ConfigIssue("steps.calc", str(exc))) from exc
