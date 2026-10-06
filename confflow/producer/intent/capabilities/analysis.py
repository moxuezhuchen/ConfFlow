#!/usr/bin/env python3

"""Analysis capability handler (L1-A3a, real shape/placement only).

Test-local cards reuse this handler with an explicit ``ExecutionRegistry``
binding; production ``build_default_intent_registry()`` never references
it so the 14-card catalog stays byte-identical.  The handler owns only
intent shape and field placement:

- ``native`` must be an explicit non-empty mapping (this card's own
  intent semantics; the strict V4 ``AnalysisModel`` allows an empty
  default and the runtime stays unchanged);
- ``preset`` has no analysis semantics and is rejected;
- unknown/invalid native keys are validated by delegating to the real
  runtime authority
  ``confflow.analysis.item_adapter.build_analysis_definition``
  (no second key table, no energy math copied here).

Top-level imports stay light (stdlib + ``..common``); the analysis
runtime is imported lazily inside :func:`_wire_analysis` with a
fail-closed ``ImportError`` fallback, mirroring
``transform.py:65``.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from ..common import _fail

#: Step keys this executor cannot consume (this card's own placement
#: authority: analysis carries no program/role/adapter/profile/checks
#: science, no seed slot, and no preset vocabulary).
#: Wire block: ``"analysis"`` (= ``fragment_keys[0]`` for analysis cards;
#: compiler derives via the descriptor effective key, no executor branch).
REJECTED_STEP_KEYS: frozenset = frozenset(
    {
        "program",
        "role",
        "adapter",
        "profile",
        "checks",
        "check_params",
        "recovery",
        "recovery_params",
        "seed",
        "preset",
    }
)


def _wire_analysis(
    step: Mapping[str, Any], card: dict[str, Any] | Mapping[str, Any], step_id: str
) -> dict[str, Any]:
    """Build the analysis payload (shape/placement only, real validation)."""
    _ = card
    native = step.get("native")
    if not isinstance(native, Mapping) or not native:
        raise _fail(
            f"step {step_id!r} requires an explicit non-empty native mapping",
            step_id=step_id,
        )
    if step.get("preset") is not None:
        raise _fail(
            f"step {step_id!r}: analysis takes no preset",
            step_id=step_id,
        )
    try:
        from ....analysis.item_adapter import build_analysis_definition
    except ImportError as exc:
        raise _fail(
            f"step {step_id!r}: analysis runtime unavailable: {exc}",
            step_id=step_id,
        ) from exc
    try:
        build_analysis_definition(dict(native), analysis_step_id=str(step_id))
    except Exception as exc:
        raise _fail(
            f"step {step_id!r} carries invalid analysis native: {exc}",
            step_id=step_id,
        ) from exc
    return {"native": copy.deepcopy(dict(native))}


def analysis_fragment(
    step: Mapping[str, Any], card: dict[str, Any] | Mapping[str, Any], step_id: str
) -> dict[str, Any]:
    """Complete capability-owned fragment for analysis cards.

    Wraps :func:`_wire_analysis` as ``{"analysis": payload}``.
    Declared fragment keys: ``("analysis",)``.  No ``_preset_ref``
    (no preset vocabulary, honestly absent), no seed block, no
    recipe/role hooks (``(None, None)`` / ``None`` at the descriptor).
    """
    return {"analysis": _wire_analysis(step, card, step_id)}
