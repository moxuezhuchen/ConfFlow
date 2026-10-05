#!/usr/bin/env python3
"""Torsion inherited-state ownership (FIX-1A A4d).

Moved from ``engine.py`` verbatim (24-raise legacy messages preserved);
new descriptor hooks ``serialize_inherited_state`` /
``verify_inherited_state`` own the torsion slice. Internal lock objects
stay here; the kernel only sees the public JSON wire payload via
``ComponentInheritedState``. Legacy ``inherited_torsion_locks`` /
``check_inherited_torsion_locks`` remain available via lazy compat
(``__module__`` kept as the old engine path for pickle/import queries).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from confflow.science.confgen.kernel_records import (
    InheritedScopeError,
    VerificationResult,
)

__all__ = [
    "InheritedTorsionLock",
    "check_inherited_torsion_locks",
    "inherited_torsion_locks",
    "serialize_inherited_state",
    "verify_inherited_state",
]


@dataclass(frozen=True, slots=True)
class InheritedTorsionLock:
    """One carried torsion axis audited against its prior absolute frame.

    Relative-grid labels are reference-dependent: the lock stores the
    absolute frame value snapshotted on the prior run's input reference
    plus the carried label, so ``expected_absolute`` is the physical
    dihedral the driving geometry must still embody. A new-reference
    delta of 0 never masquerades as the old label (e.g. old 120).
    Absolute/chemical labels are absolute setpoints directly.
    """

    axis_id: str
    frame: tuple[int, int, int, int]
    expected_absolute: float
    label: Any
    model: str


InheritedTorsionLock.__module__ = "confflow.science.confgen.engine"


def _resolve_inherited_frame(
    descriptor: Mapping[str, Any],
    entry: Mapping[str, Any],
    adjacency: Sequence[Sequence[int]],
) -> tuple[int, int, int, int]:
    """Resolve the absolute 4-atom frame for one carried torsion axis.

    The descriptor's snapshotted frame wins when it is four distinct
    in-range ints (the reference value was measured with it); otherwise
    the core frame rule (minimum-index neighbors, mirroring
    ``TorsionStage._frame_for``) derives it from the declared bond.
    Anything else fails closed.
    """
    frame = descriptor.get("frame")
    n_atoms = len(adjacency)
    if frame is not None:
        atoms = list(frame)
        if (
            len(atoms) == 4
            and all(isinstance(a, bool) is False and isinstance(a, int) for a in atoms)
            and len(set(int(a) for a in atoms)) == 4
            and all(0 <= int(a) < n_atoms for a in atoms)
        ):
            return (int(atoms[0]), int(atoms[1]), int(atoms[2]), int(atoms[3]))
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited torsion frame "
            f"{frame!r} is not four distinct in-range atom indices"
        )
    bond = entry.get("bond")
    if bond is None:
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: carried torsion needs an explicit "
            "four-atom frame descriptor (chemical/absolute) or a bond for "
            "frame resolution"
        )
    first, second = int(bond[0]), int(bond[1])
    near = [n for n in adjacency[first] if n != second]
    far = [n for n in adjacency[second] if n != first]
    if not near or not far:
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: carried torsion bond has no "
            "measurable dihedral frame (terminal pair)"
        )
    return (min(near), first, second, min(far))


def _build_torsion_locks(
    state_value: Mapping[str, Any],
    torsion_scope: Mapping[str, Any],
    resolved: Mapping[str, Any],
    adjacency: Sequence[Sequence[int]],
) -> list[InheritedTorsionLock]:
    """Build torsion locks from a state slice plus its scope descriptors.

    Verbatim torsion-scope messages (engine L240-345); raises
    ``InheritedScopeError`` with identical text/interpolation. Never reads
    ``context.input_state_key``; ``state_value`` comes from
    ``ComponentStateKey``.
    """
    from confflow.science.confgen.torsion.measure import wrap_degrees

    downstream_torsions = {
        str(entry.get("id")): entry
        for entry in (resolved.get("torsions", []) or [])
        if isinstance(entry, Mapping)
    }
    locks: list[InheritedTorsionLock] = []
    for axis_id, label in dict(state_value or {}).items():
        axis = str(axis_id)
        wounded = f"torsions.{axis}"
        descriptor = torsion_scope.get(axis)
        if not isinstance(descriptor, Mapping):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: no scope descriptor for {wounded}"
            )
        entry = downstream_torsions.get(axis)
        if entry is None:
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: downstream spec drops inherited "
                f"{wounded}; declare it (enumerate or preserve_input) or refuse "
                "the chain"
            )
        if str(entry.get("treatment", "enumerate")) == "enumerate":
            continue
        if str(entry.get("model")) != str(descriptor.get("model")):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: downstream {wounded} redefines "
                f"model {entry.get('model')!r} over inherited "
                f"{descriptor.get('model')!r}"
            )
        for ref in ("bond", "atoms"):
            expected = descriptor.get(ref)
            observed = entry.get(ref)
            if expected is None and observed is None:
                continue
            if (
                expected is None
                or observed is None
                or [int(a) for a in observed] != [int(a) for a in expected]
            ):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: downstream {wounded} {ref} "
                    f"{observed!r} differs from the inherited {expected!r}"
                )
        if str(entry.get("rotate_side", "left")) != str(descriptor.get("rotate_side", "left")):
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: downstream {wounded} rotate_side "
                "differs from the inherited descriptor"
            )
        model = str(descriptor.get("model"))
        frame = _resolve_inherited_frame(descriptor, entry, adjacency)
        if model == "chemical":
            states = descriptor.get("states") or {}
            if not isinstance(states, Mapping) or label not in states:
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} label "
                    f"{label!r} is not a declared chemical state"
                )
            if entry.get("atoms") is not None:
                expected_absolute = float(states[label])
            else:
                reference = descriptor.get("reference_frame_value")
                if (
                    reference is None
                    or isinstance(reference, bool)
                    or not isinstance(reference, (int, float))
                ):
                    raise InheritedScopeError(
                        f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} "
                        "is reference-dependent (bond-only chemical) with no "
                        "snapshotted absolute reference_frame_value; "
                        "re-enumerate it"
                    )
                import math as _math_chem

                if not _math_chem.isfinite(float(reference)):
                    raise InheritedScopeError(
                        f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} "
                        "reference_frame_value is not finite"
                    )
                expected_absolute = float(wrap_degrees(float(reference) + float(states[label])))
        elif model == "absolute_dihedral_grid":
            if isinstance(label, bool) or not isinstance(label, (int, float)):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} label "
                    f"{label!r} is not a measured angle"
                )
            expected_absolute = float(label)
        elif model == "relative_rotation_grid":
            reference = descriptor.get("reference_frame_value")
            if (
                reference is None
                or isinstance(reference, bool)
                or not isinstance(reference, (int, float))
            ):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} is "
                    "reference-dependent (relative grid) with no snapshotted "
                    "absolute reference_frame_value; re-enumerate it"
                )
            if isinstance(label, bool) or not isinstance(label, (int, float)):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} label "
                    f"{label!r} is not a rotation angle"
                )
            import math as _math

            if not _math.isfinite(float(reference)):
                raise InheritedScopeError(
                    f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} "
                    "reference_frame_value is not finite"
                )
            expected_absolute = float(wrap_degrees(float(reference) + float(label)))
        else:
            raise InheritedScopeError(
                f"INHERITED_STATE_SCOPE_MISSING: inherited {wounded} names unknown model {model!r}"
            )
        locks.append(
            InheritedTorsionLock(
                axis_id=axis,
                frame=frame,
                expected_absolute=float(expected_absolute),
                label=label,
                model=model,
            )
        )
    return locks


def check_inherited_torsion_locks(
    coords: Any, locks: Sequence[InheritedTorsionLock], tolerance_deg: float
) -> tuple[bool, list[dict[str, Any]]]:
    """Verify carried torsion locks against one geometry (absolute frames).

    Returns (ok, evidence). Any deviation outside tolerance is lock drift;
    the caller routes it to FAILED_DRIFT (never a publication with a stale
    parent key) or fails the chain closed when no target context exists.
    """
    import numpy as np

    from confflow.science.confgen.torsion.measure import measure_dihedral, wrap_degrees

    evidence: list[dict[str, Any]] = []
    ok = True
    for lock in locks:
        try:
            measured = float(measure_dihedral(np.asarray(coords, dtype=float), *lock.frame))
        except ValueError as exc:
            ok = False
            evidence.append(
                {
                    "kind": "drift",
                    "axis": f"torsions.{lock.axis_id}",
                    "detail": "inherited torsion frame unmeasurable",
                    "expected": float(lock.expected_absolute),
                    "measured": None,
                    "out_of_scope": True,
                    "inherited": True,
                    "frame": list(lock.frame),
                    "diagnostic": str(exc)[:200],
                }
            )
            continue
        deviation = abs(wrap_degrees(measured - lock.expected_absolute))
        if deviation <= float(tolerance_deg):
            continue
        ok = False
        evidence.append(
            {
                "kind": "drift",
                "axis": f"torsions.{lock.axis_id}",
                "detail": "inherited torsion lock drifted",
                "expected": float(lock.expected_absolute),
                "measured": float(measured),
                "deviation_deg": float(deviation),
                "tolerance_deg": float(tolerance_deg),
                "out_of_scope": "unknown",
                "inherited": True,
                "frame": list(lock.frame),
            }
        )
    return ok, evidence


check_inherited_torsion_locks.__module__ = "confflow.science.confgen.engine"


def serialize_inherited_state(
    resolved: Mapping[str, Any], state_value: Any, context: Any
) -> Mapping[str, Any]:
    """Build the public torsion scope payload (byte-identical to legacy).

    ``state_value`` is this component's slice from ``ComponentStateKey``
    (``{axis: label}``); never reads ``context.input_state_key``.
    """
    from confflow.science.confgen.torsion.measure import measure_dihedral

    adjacency = context.adjacency
    input_coords = context.input_coords
    import numpy as np

    coords = np.asarray(input_coords, dtype=float)
    torsion_entries = {
        str(entry.get("id")): entry
        for entry in (resolved.get("torsions", []) or [])
        if isinstance(entry, Mapping)
    }

    def _relative_frame(bond: Any) -> list[int] | None:
        first, second = int(bond[0]), int(bond[1])
        near = [n for n in adjacency[first] if n != second]
        far = [n for n in adjacency[second] if n != first]
        if not near or not far:
            return None
        return [min(near), first, second, min(far)]

    out: dict[str, Any] = {}
    for axis_id, label in dict(state_value or {}).items():
        entry = torsion_entries.get(str(axis_id), {})
        model = str(entry.get("model", ""))
        bond = entry.get("bond")
        atoms = entry.get("atoms")
        frame: Any = None
        reference_value: Any = None
        if atoms is not None:
            frame = [int(a) for a in atoms]
        elif bond is not None:
            frame = _relative_frame(bond)
            if frame is not None:
                try:
                    reference_value = float(measure_dihedral(coords, *frame))
                except ValueError:
                    reference_value = None
        descriptor: dict[str, Any] = {
            "model": model,
            "bond": [int(b) for b in bond] if bond is not None else None,
            "atoms": [int(a) for a in atoms] if atoms is not None else None,
            "frame": frame,
            "rotate_side": str(entry.get("rotate_side", "left")),
            "states": dict(entry.get("states", {}) or {}),
            "label": label,
        }
        if model == "relative_rotation_grid":
            descriptor["frame_rule"] = "torsion-stage-_frames-mirror(interim)"
            descriptor["reference_frame_value"] = reference_value
        out[str(axis_id)] = descriptor
    return out


def verify_inherited_state(
    structure: Any, state_value: Any, payload: Any, context: Any
) -> VerificationResult:
    """Verify the carried torsion slice on one geometry.

    Scope failures raise ``InheritedScopeError`` verbatim; geometry drift
    returns ``VerificationResult(ok=False, evidence)`` (empty when ok).
    Never reads ``context.input_state_key``.
    """
    if not isinstance(payload, Mapping):
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited scope has no torsion section"
        )
    resolved = context.resolved_spec
    adjacency = context.adjacency
    locks = _build_torsion_locks(
        dict(state_value or {}) if isinstance(state_value, Mapping) else {},
        payload,
        resolved,
        adjacency,
    )
    tolerance = float(context.tolerances.dihedral_atol_deg)
    coords = structure.coordinates if hasattr(structure, "coordinates") else structure
    ok, evidence = check_inherited_torsion_locks(coords, locks, tolerance)
    return VerificationResult(ok=bool(ok), evidence=tuple(dict(e) for e in evidence))


def inherited_torsion_locks(context: Any) -> list[InheritedTorsionLock]:
    """Legacy compat: build audited locks for carried torsion axes.

    Preserves the 24-raise legacy behavior verbatim (torsions plus
    rings/coordination completeness via delegated component verifies).
    New code must use ``verify_inherited_state`` via the registry.
    """
    key = context.input_state_key
    from confflow.science.confgen.kernel_records import ComponentStateKey as _CSK

    if isinstance(key, _CSK):
        generic = key
    else:
        payload = key.to_dict()
        comps: dict[str, Any] = {}
        if payload.get("coordination") is not None:
            comps["coordination"] = payload.get("coordination")
        if dict(payload.get("rings", {}) or {}):
            comps["rings"] = dict(payload.get("rings", {}) or {})
        if dict(payload.get("torsions", {}) or {}):
            comps["torsions"] = dict(payload.get("torsions", {}) or {})
        from confflow.science.confgen.kernel_records import ComponentStateKey as _CSK2

        generic = _CSK2(components=comps)
    if not bool(dict(generic.components)):
        return []
    scope = context.inherited_scope
    if not isinstance(scope, Mapping) or not scope:
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: non-empty incoming confgen_state "
            "carries no inherited scope descriptors; chained state cannot "
            "be audited"
        )
    resolved = context.resolved_spec
    adjacency = context.adjacency
    torsion_scope = scope.get("torsions", {})
    if not isinstance(torsion_scope, Mapping):
        raise InheritedScopeError(
            "INHERITED_STATE_SCOPE_MISSING: inherited scope has no torsion section"
        )
    locks = _build_torsion_locks(
        dict(dict(generic.components).get("torsions", {}) or {}),
        torsion_scope,
        resolved,
        adjacency,
    )
    # Legacy completeness: delegate to owning components via registry
    # (generic, no cross-component imports, messages stay verbatim).
    try:
        _reg = getattr(context, "registry", None)
    except Exception:
        _reg = None
    if _reg is None:
        try:
            from confflow.science.confgen.registry import default_registry as _dr

            _reg = _dr()
        except Exception:
            _reg = None
    if _reg is not None:
        try:
            _ordered = list(_reg._ordered())
        except Exception:
            _ordered = []
        for _d in reversed(_ordered):
            _cid = str(getattr(_d, "id", ""))
            if not _cid or _cid == "torsions":
                continue
            _sv = dict(generic.components).get(_cid)
            if _sv is None:
                continue
            if isinstance(_sv, Mapping) and not dict(_sv):
                continue
            _pay = scope.get(_cid) if isinstance(scope, Mapping) else None
            _hook = getattr(_d, "verify_inherited_state", None)
            if callable(_hook):
                _hook(context.structure, _sv, _pay, context)
    return locks


inherited_torsion_locks.__module__ = "confflow.science.confgen.engine"
