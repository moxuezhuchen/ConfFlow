#!/usr/bin/env python3

"""Ring component descriptor (moved from the engine dispatch, A1).

The ``factory`` body preserves the ``engine._load_stage`` rings branch
verbatim (snapshot handling, lazy import with fail-closed
``UnsupportedAxisError``, original message); ``is_active`` preserves
the ``engine._levels`` rings predicate.

A5 lightweight: top level imports only this component's stdlib-only
``constants.py`` plus the registry type. All runtime imports happen
inside hook bodies when called, never at ``descriptor()`` build time.
``schema_constants``/``contract_options`` read only ``constants.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confflow.science.confgen.registry import ComponentDescriptor

from .constants import TEMPLATES_BY_SIZE

__all__ = ["descriptor"]


def _fallback_lock(
    stage: Any,
    locked_state: Mapping[str, Any],
    structure: Any,
    context: Any,
) -> Any:
    """Descriptor fallback: ring tolerance-aware matcher (owns behavior)."""
    from confflow.science.confgen.ring.scope import fallback_lock as _impl

    return _impl(stage, locked_state, structure, context)


def _preserved_entries(resolved: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Descriptor fallback: ring ``preserve_input`` entries."""
    from confflow.science.confgen.ring.scope import preserved_entries as _impl

    return _impl(resolved)


def _report_section(resolved: Mapping[str, Any]) -> dict[str, Any] | None:
    """Descriptor fallback: ring contributes no report section."""
    from confflow.science.confgen.ring.scope import report_section as _impl

    return _impl(resolved)


def _describe_scope(resolved: Mapping[str, Any]) -> Any:
    """Descriptor fallback: ring scope slice."""
    from confflow.science.confgen.ring.scope import describe_scope as _impl

    return _impl(resolved)


def _normalize_spec(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
    """Lazy proxy: normalize owned ``rings`` key only when called."""
    from confflow.science.confgen.ring.spec import normalize_spec as _impl

    return _impl(raw, index_base=index_base)


def _validate_context(resolved: Mapping[str, Any], context: Any) -> None:
    """Lazy proxy: ring context check (none at A4b)."""
    from confflow.science.confgen.ring.spec import validate_context as _impl

    return _impl(resolved, context)


def _has_indices(raw: Mapping[str, Any]) -> bool:
    """Lazy proxy: rings index probe only when called."""
    from confflow.science.confgen.ring.spec import has_indices as _impl

    return _impl(raw)


def _serialize_inherited_state(
    resolved: Mapping[str, Any], state_value: Any, context: Any
) -> Mapping[str, Any]:
    """Build the public rings scope payload (byte-identical to legacy)."""
    ring_entries = {
        str(entry.get("id")): entry
        for entry in (resolved.get("rings", []) or [])
        if isinstance(entry, Mapping)
    }
    out: dict[str, Any] = {}
    for ring_id, label in dict(state_value or {}).items():
        out[str(ring_id)] = {
            "atoms": [int(a) for a in (ring_entries.get(str(ring_id), {}).get("atoms") or [])],
            "label": (dict(label) if isinstance(label, Mapping) else label),
        }
    return out


def _verify_inherited_state(structure: Any, state_value: Any, payload: Any, context: Any) -> Any:
    """Verify the carried rings slice (scope completeness only)."""
    from confflow.science.confgen.kernel_records import InheritedScopeError, VerificationResult

    rings_value = dict(state_value or {}) if isinstance(state_value, Mapping) else {}
    if not rings_value:
        return VerificationResult(ok=True, evidence=())
    if not isinstance(payload, Mapping):
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited scope has no ring section"
        )
    resolved = context.resolved_spec
    downstream_rings = {
        str(entry.get("id"))
        for entry in (resolved.get("rings", []) or [])
        if isinstance(entry, Mapping)
    }
    for ring_id in rings_value:
        if str(ring_id) in downstream_rings:
            continue
        if not isinstance(payload.get(str(ring_id)), Mapping):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: no scope descriptor for rings.{ring_id}"
            )
        raise InheritedScopeError(
            f"INHERITED_STATE_SCOPE_MISSING: inherited rings.{ring_id} is "
            "lane-owned state with no active downstream ring stage; "
            "re-enumerate it (carried lane audit hooks pending)"
        )
    return VerificationResult(ok=True, evidence=())


def _schema_constants() -> Mapping[str, Any]:
    """Return lightweight ring vocabulary (no solver import)."""
    sizes: dict[str, int] = {}
    for size, names in TEMPLATES_BY_SIZE.items():
        for name in names:
            sizes[str(name)] = int(size)
    # R4: forms vocabulary (authoritative regular forms) without importing
    # solvers: sizes are fixed by R1 tables (4:3, 5:20, 6:38).
    # v2 fix: name -> supporting size SET (5/6 share E_0..E_9 etc; a flat
    # name->single-size map lets 6 overwrite 5). Explicit-P specials P/P_0
    # for n=5/6 live outside the 20/38 catalogs but stay authorized.
    from .forms import FORM_NAMES_BY_SIZE

    _acc: dict[str, set[int]] = {}
    for size, names in FORM_NAMES_BY_SIZE.items():
        for name in names:
            _acc.setdefault(str(name), set()).add(int(size))
    for _p in ("P", "P_0"):
        _acc.setdefault(_p, set()).update([4, 5, 6] if _p == "P" else [4, 5, 6])
    form_sizes: dict[str, list[int]] = {name: sorted(sizes) for name, sizes in sorted(_acc.items())}
    return {
        "template_sizes": sizes,
        "templates_by_size": {str(k): tuple(v) for k, v in TEMPLATES_BY_SIZE.items()},
        "form_sizes": form_sizes,
        "forms_by_size": {str(k): tuple(v) for k, v in FORM_NAMES_BY_SIZE.items()},
    }


def _contract_options() -> Mapping[str, Any]:
    """Return the ring-owned slice of the producer contract."""
    from .forms import FORM_NAMES_BY_SIZE

    return {
        "ring_templates_by_size": {
            str(size): sorted(names) for size, names in sorted(TEMPLATES_BY_SIZE.items())
        },
        "ring_forms_by_size": {
            str(size): list(names) for size, names in sorted(FORM_NAMES_BY_SIZE.items())
        },
    }


def descriptor() -> ComponentDescriptor:
    """Return the rings component descriptor (lightweight build)."""

    def is_active(resolved: Mapping[str, Any]) -> bool:
        rings = resolved.get("rings", [])
        if isinstance(rings, (list, tuple)) and len(rings) > 0:
            return True
        return False

    def factory(resolved: Mapping[str, Any]) -> Any:
        from confflow.science.confgen.engine import UnsupportedAxisError, thaw_snapshot

        snapshot = thaw_snapshot(resolved)
        try:
            from confflow.science.confgen.ring.stage import RingStage
        except ImportError as exc:
            raise UnsupportedAxisError(
                "rings requested but the ring stage module is unavailable"
            ) from exc
        return RingStage(snapshot)

    return ComponentDescriptor(
        id="rings",
        order=20,
        spec_keys=("rings",),
        state_merge="merge",
        is_active=is_active,
        factory=factory,
        check_bond_integrity=False,
        carries_inherited_locks=False,
        fallback_lock=_fallback_lock,
        preserved_entries=_preserved_entries,
        report_section=_report_section,
        describe_scope=_describe_scope,
        normalize_spec=_normalize_spec,
        validate_context=_validate_context,
        spec_defaults=(("rings", []),),
        has_indices=_has_indices,
        index_detection_order=30,
        serialize_inherited_state=_serialize_inherited_state,
        verify_inherited_state=_verify_inherited_state,
        schema_constants=_schema_constants,
        contract_options=_contract_options,
    )
