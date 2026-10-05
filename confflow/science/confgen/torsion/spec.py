#!/usr/bin/env python3

"""Torsion spec normalization (FIX-1A A4b, moved from planner).

Owns ``torsions``, ``paths``, ``strict_path_bond_check``. Blocks moved
verbatim from ``planner.normalize_spec`` (torsion loop + ``paths`` +
``strict``), preserving error strings, validation order (torsions, then
paths, then strict), and the paths idempotency rule.

A4b-v2: ``resolve_torsion_axes`` and its exclusive private closure
(``_require_finite_angles``, ``_reject_periodic_duplicates``,
``_require_index_list``) now live here (moved verbatim from planner;
only imports adapted to lazy planner imports, as with
``coordination/spec._convert_coordination``). ``planner.resolve_torsion_axes``
is a lazy delegation wrapper preserving signature and ``__all__``.
``torsion/spec.normalize_spec`` calls the local ``resolve_torsion_axes``
(no ``spec -> planner -> spec`` call cycle).

Dependency record (non-trivial shared stays in planner, explicitly):
- ``TorsionAxis``, ``_TORSION_MODELS``, ``_TREATMENTS``, ``_convert_index``
  stay in ``planner`` (``TorsionAxis`` is public API used by
  ``torsion/stage.py`` without whitelist change; constants validate it;
  ``_convert_index`` is used broadly for rings/topology). This module
  lazily imports them inside functions (no top-level kernel import; keeps
  ``component.py`` light).
- Exclusive helpers moved here use the shared ``_convert_index`` via lazy
  import; no duplication, single source stays in planner.
- A4c overlay (``_overlay_declared_coordination_scope``) stays in planner.

Paths base rule (verbatim): fresh path declarations are ALWAYS user-facing
1-based, independent of the top-level ``index_base``; entries already carrying
the ``internal-0-based:normalized`` marker re-validate as internal 0-based
with preserved source metadata (no double shift). The marker is read from the
whole raw spec (component receives the whole raw mapping).

Interface note (root compat ruling): returns ONLY the owned keys that were
present in the input (``paths=None`` normalizes to ``[]`` and counts as
present). Missing builtin keys are filled with historical defaults by the
planner (``torsions -> []``, ``paths -> []``, ``strict -> False``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # Annotation only; runtime uses lazy planner import (no top-level cycle).
    from confflow.science.confgen.planner import TorsionAxis

__all__ = [
    "expand_context_spec",
    "has_indices",
    "normalize_spec",
    "resolve_torsion_axes",
    "validate_context",
]


def has_indices(raw: Mapping[str, Any]) -> bool:
    """Return True when raw torsions/paths carry index-bearing content.

    Mirrors the pre-AG1 ``planner._spec_has_indices`` torsions+paths
    branches exactly in order (torsions then paths; never raises;
    illegal shapes are False).
    """
    torsions = raw.get("torsions")
    if isinstance(torsions, (list, tuple)) and len(torsions) > 0:
        return True
    paths = raw.get("paths")
    if isinstance(paths, (list, tuple)) and len(paths) > 0:
        return True
    return False


def _require_finite_angles(values: Any, *, path: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{path} must be a list of finite angles in degrees")
    angles: list[float] = []
    for index, item in enumerate(values):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError(f"{path}[{index}] must be a finite number, got {item!r}")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError(f"{path}[{index}] must be finite, got {item!r}")
        angles.append(number)
    if not angles:
        raise ValueError(f"{path} must hold at least one angle")
    return tuple(angles)


def _reject_periodic_duplicates(values: Sequence[float], *, path: str) -> None:
    """Reject identical circular grid points without merging nearby states."""
    for index, value in enumerate(values):
        for previous in values[:index]:
            if (value - previous) % 360.0 == 0.0:
                raise ValueError(f"{path}: periodic duplicate torsion angle {value}")


def _require_index_list(
    values: Any, *, path: str, count: int, n_atoms: int | None, index_base: int = 0
) -> tuple[int, ...]:
    """Validate indices in the declared base, return internal 0-based."""
    from confflow.science.confgen.planner import _convert_index

    if index_base not in (0, 1):
        raise ValueError(f"{path}: index_base must be 0 or 1")
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{path} must be a list of {count} atom indices")
    items = list(values)
    if len(items) != count:
        raise ValueError(f"{path} must hold exactly {count} atom indices, got {len(items)}")
    out: list[int] = []
    for index, item in enumerate(items):
        out.append(_convert_index(item, base=index_base, path=f"{path}[{index}]"))
        if n_atoms is not None and out[-1] >= n_atoms:
            raise ValueError(f"{path}[{index}] index {item} out of range for {n_atoms} atoms")
    if len(set(out)) != len(out):
        raise ValueError(f"{path} must hold distinct atoms, got {list(values)!r}")
    return tuple(out)  # type: ignore[return-value]


# v2 move note (kept out of docstring so function-body AST stays verbatim):
# Implementation moved verbatim from planner.resolve_torsion_axes; only
# shared imports below adapted to lazy planner imports. Shared
# TorsionAxis/_TORSION_MODELS/_TREATMENTS stay in planner for API compat.
def resolve_torsion_axes(
    entries: Sequence[Mapping[str, Any]],
    *,
    n_atoms: int | None = None,
    index_base: int = 0,
) -> tuple[TorsionAxis, ...]:
    """Resolve torsion axis entries into :class:`TorsionAxis` records.

    Entries use the declared ``index_base`` (0 internal, 1 workflow) and are
    stored 0-based internally. Fail-closed checks: unique non-empty ids,
    known models/treatments, finite angles, distinct in-range indices,
    model/index-shape consistency, opt-in chemical ``states`` (explicit
    map, no defaults), and duplicate bond axes (same unordered rotating
    pair twice).
    """
    from confflow.science.confgen.planner import (
        _TORSION_MODELS,
        _TREATMENTS,
        TorsionAxis,
    )

    axes: list[TorsionAxis] = []
    seen_ids: set[str] = set()
    seen_bonds: dict[tuple[int, int], str] = {}
    for position, entry in enumerate(entries):
        path = f"$.torsions[{position}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path} must be a mapping")
        unknown = sorted(
            set(entry)
            - {"id", "bond", "atoms", "model", "angles", "states", "treatment", "rotate_side"}
        )
        if unknown:
            raise ValueError(f"{path} holds unknown keys {unknown}")
        axis_id = entry.get("id", f"torsion-{position}")
        if not isinstance(axis_id, str) or not axis_id:
            raise ValueError(f"{path}.id must be a non-empty string")
        if axis_id in seen_ids:
            raise ValueError(f"duplicate torsion axis id {axis_id!r}")
        seen_ids.add(axis_id)
        model = entry.get("model", "relative_rotation_grid")
        if model not in _TORSION_MODELS:
            raise ValueError(f"{path}.model must be one of {_TORSION_MODELS}, got {model!r}")
        treatment = entry.get("treatment", "enumerate")
        if treatment not in _TREATMENTS:
            raise ValueError(
                f"{path}.treatment must be one of {_TREATMENTS}, got {treatment!r}; "
                "unspecified/unsupported treatments fail closed"
            )
        rotate_side = entry.get("rotate_side", "left")
        if rotate_side not in ("left", "right"):
            raise ValueError(f"{path}.rotate_side must be 'left' or 'right'")
        raw_bond = entry.get("bond")
        raw_atoms = entry.get("atoms")
        bond: tuple[int, int] | None = None
        frame: tuple[int, int, int, int] | None = None
        if model == "relative_rotation_grid":
            if raw_bond is None:
                raise ValueError(f"{path}: relative_rotation_grid requires 'bond'")
            if raw_atoms is not None:
                raise ValueError(f"{path}: relative_rotation_grid takes 'bond', not 'atoms'")
            pair = _require_index_list(
                raw_bond, path=f"{path}.bond", count=2, n_atoms=n_atoms, index_base=index_base
            )
            bond = (min(pair), max(pair)) if pair[0] != pair[1] else None
            if bond is None:
                raise ValueError(f"{path}.bond must name two distinct atoms")
            # Preserve declaration order for the rotation axis direction.
            bond = (pair[0], pair[1])
        elif model == "absolute_dihedral_grid":
            if raw_atoms is None:
                raise ValueError(f"{path}: absolute_dihedral_grid requires 'atoms' (four indices)")
            if raw_bond is not None:
                raise ValueError(f"{path}: absolute_dihedral_grid takes 'atoms', not 'bond'")
            frame = _require_index_list(
                raw_atoms, path=f"{path}.atoms", count=4, n_atoms=n_atoms, index_base=index_base
            )  # type: ignore[assignment]
            assert frame is not None and len(frame) == 4
            bond = (frame[1], frame[2])
        else:  # chemical: opt-in named states, bond or four-atom frame
            states = entry.get("states")
            if not isinstance(states, Mapping) or not states:
                raise ValueError(
                    f"{path}: chemical model is opt-in and requires an explicit "
                    "non-empty 'states' map of name -> angle"
                )
            names: list[str] = []
            angles: list[float] = []
            for name, angle in states.items():
                if not isinstance(name, str) or not name:
                    raise ValueError(f"{path}.states holds a non-string state name {name!r}")
                if isinstance(angle, bool) or not isinstance(angle, (int, float)):
                    raise ValueError(f"{path}.states[{name!r}] must be a finite angle")
                if not math.isfinite(float(angle)):
                    raise ValueError(f"{path}.states[{name!r}] must be finite")
                names.append(name)
                angles.append(float(angle))
            _reject_periodic_duplicates(angles, path=f"{path}.states")
            if raw_bond is not None and raw_atoms is not None:
                raise ValueError(f"{path}: chemical takes 'bond' or 'atoms', not both")
            if raw_bond is not None:
                pair = _require_index_list(
                    raw_bond, path=f"{path}.bond", count=2, n_atoms=n_atoms, index_base=index_base
                )
                if pair[0] == pair[1]:
                    raise ValueError(f"{path}.bond must name two distinct atoms")
                bond = (pair[0], pair[1])
            elif raw_atoms is not None:
                frame = _require_index_list(
                    raw_atoms, path=f"{path}.atoms", count=4, n_atoms=n_atoms, index_base=index_base
                )  # type: ignore[assignment]
                assert frame is not None and len(frame) == 4
                bond = (frame[1], frame[2])
            else:
                raise ValueError(f"{path}: chemical requires 'bond' or 'atoms'")
            axes.append(
                TorsionAxis(
                    axis_id=axis_id,
                    bond=bond,
                    frame=frame,
                    model=model,
                    values=tuple(angles),
                    state_names=tuple(names),
                    treatment=treatment,
                    rotate_side=rotate_side,
                )
            )
            key = (min(bond), max(bond))
            if key in seen_bonds:
                raise ValueError(
                    f"duplicate torsion bond axis {bond} "
                    f"(axes {seen_bonds[key]!r} and {axis_id!r}); "
                    "duplicate bond axes fail closed"
                )
            seen_bonds[key] = axis_id
            continue
        raw_angles = entry.get("angles")
        if raw_angles is None:
            raise ValueError(f"{path}: model {model!r} requires explicit 'angles'")
        values = _require_finite_angles(raw_angles, path=f"{path}.angles")
        _reject_periodic_duplicates(values, path=f"{path}.angles")
        assert bond is not None
        key = (min(bond), max(bond))
        if key in seen_bonds:
            raise ValueError(
                f"duplicate torsion bond axis {bond} "
                f"(axes {seen_bonds[key]!r} and {axis_id!r}); "
                "duplicate bond axes fail closed"
            )
        seen_bonds[key] = axis_id
        axes.append(
            TorsionAxis(
                axis_id=axis_id,
                bond=bond,
                frame=frame,
                model=model,
                values=values,
                treatment=treatment,
                rotate_side=rotate_side,
            )
        )
    return tuple(axes)


def normalize_spec(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
    """Normalize owned torsion keys (structure-independent)."""
    owned = ("torsions", "paths", "strict_path_bond_check")
    if not any(key in raw for key in owned):
        return {}
    from confflow.science.confgen.planner import _convert_index_list

    out: dict[str, Any] = {}
    if "torsions" in raw:
        torsions = raw.get("torsions", [])
        if not isinstance(torsions, (list, tuple)):
            raise ValueError("spec torsions must be a list of torsion declarations")
        converted_torsions: list[dict[str, Any]] = []
        for position, entry in enumerate(torsions):
            if not isinstance(entry, Mapping):
                raise ValueError(f"$.torsions[{position}] must be a mapping")
            if "index_base" in entry:
                raise ValueError(
                    "index convention is top-level only; torsion entries must not declare index_base"
                )
            converted = dict(entry)
            if entry.get("bond") is not None:
                converted["bond"] = _convert_index_list(
                    entry["bond"], base=index_base, path=f"$.torsions[{position}].bond"
                )
                if len(converted["bond"]) != 2:
                    raise ValueError(f"$.torsions[{position}].bond must hold exactly two indices")
            if entry.get("atoms") is not None:
                converted["atoms"] = _convert_index_list(
                    entry["atoms"], base=index_base, path=f"$.torsions[{position}].atoms"
                )
                if len(converted["atoms"]) != 4:
                    raise ValueError(f"$.torsions[{position}].atoms must hold exactly four indices")
            converted_torsions.append(converted)
        # Full torsion validation on internal 0-based entries
        # (structure-independent part); index ranges re-checked at stage.
        # v2: call local implementation (no spec -> planner -> spec cycle).
        resolve_torsion_axes(tuple(converted_torsions), index_base=0)
        out["torsions"] = converted_torsions
    if "paths" in raw:
        from confflow.science.confgen.torsion.paths import parse_path_declarations

        marker = raw.get("index_convention")
        raw_paths = raw.get("paths", [])
        if raw_paths is None:
            raw_paths = []
        if not isinstance(raw_paths, (list, tuple)):
            raise ValueError("spec paths must be a list of path declarations")
        if raw_paths:
            internal_paths = marker == "internal-0-based:normalized"
            try:
                typed_parsed = parse_path_declarations(
                    list(raw_paths),
                    index_base=0 if internal_paths else 1,
                    default_step=None,
                    source_prefix="$.paths",
                    internal=internal_paths,
                )
            except ValueError as exc:
                raise ValueError(f"spec paths rejected: {exc}") from exc
            out["paths"] = [
                {
                    "start": int(item.start),
                    "end": int(item.end),
                    "move": item.move,
                    "angles": [float(angle) for angle in item.angles],
                    "source": item.source,
                }
                for item in typed_parsed
            ]
        else:
            out["paths"] = []
    if "strict_path_bond_check" in raw:
        strict_check = raw.get("strict_path_bond_check", False)
        if type(strict_check) is not bool:
            raise ValueError(f"$.strict_path_bond_check must be a boolean, got {strict_check!r}")
        out["strict_path_bond_check"] = bool(strict_check)
    return out


def validate_context(resolved: Mapping[str, Any], context: Any) -> None:
    """Context-stage validation owned by torsion (none at A4b; always passes).

    Path expansion (``_expand_typed_paths``) and typed-graph scope stay where
    they are until A4c/A4d; no normalize-stage check is moved later here.
    """
    return None


def expand_context_spec(
    resolved: dict[str, Any],
    structure: Any,
    adjacency: Sequence[Sequence[int]],
) -> None:
    """Expand Phase 0 path declarations into torsion axes (deferred).

    AG1 move of ``model._expand_typed_paths``: body verbatim (only the
    function name changed). Runs at context build time -- on the final
    working topology after perception plus add/del_bond corrections --
    with the SAME pure resolver the legacy executor uses. Path-expanded
    rotors use the relative-rotation grid model with ``rotate_side``
    derived from the explicitly chosen moving endpoint (``move=start``
    behaves like the ``left`` side of the traversal-ordered bond,
    ``move=end`` like ``right``). Any canonical-bond collision with an
    explicitly declared torsion axis (any model) fails closed with
    ``ROTOR_SAMPLING_CONFLICT``; identical path duplicates deduplicate
    with merged provenance. The expansion record (``paths_resolved``)
    audits rotors, warnings, and the topology digest.
    """
    from confflow.domain.elements import atomic_number
    from confflow.science.bonds import covalent_radii
    from confflow.science.confgen.planner import resolve_torsion_axes
    from confflow.science.confgen.torsion.paths import (
        ROTOR_SAMPLING_CONFLICT,
        ParsedPath,
        PathResolutionError,
        canonical_grid_size,
        canonicalize_rotors,
        resolve_paths,
    )

    entries = resolved.get("paths") or []
    parsed = [
        ParsedPath(
            start=int(item["start"]),
            end=int(item["end"]),
            move=str(item["move"]),
            angles=tuple(float(a) for a in item["angles"]),
            source=str(item.get("source", f"$.paths[{i}]")),
            raw_start=int(item["start"]) + 1,
            raw_end=int(item["end"]) + 1,
        )
        for i, item in enumerate(entries)
    ]
    n_atoms = len(structure.atoms)
    try:
        numbers = [atomic_number(symbol) for symbol in structure.atoms]
        radii = covalent_radii(numbers)
    except ValueError as exc:
        raise ValueError(f"path short-bond assessment failed: {exc}") from exc
    strict = bool(resolved.get("strict_path_bond_check", False))
    try:
        resolution = resolve_paths(
            parsed,
            [list(row) for row in adjacency],
            n_atoms=n_atoms,
            coords=[tuple(p) for p in structure.coordinates],
            radii=list(radii),
            strict_bond_check=strict,
        )
        rotors = canonicalize_rotors(resolution.rotors)
    except PathResolutionError as exc:
        raise ValueError(str(exc)) from exc
    explicit: dict[tuple[int, int], str] = {}
    for position, entry in enumerate(resolved.get("torsions", []) or []):
        bond = entry.get("bond")
        atoms = entry.get("atoms")
        if bond is not None:
            key = (min(int(bond[0]), int(bond[1])), max(int(bond[0]), int(bond[1])))
        elif atoms is not None:
            key = (min(int(atoms[1]), int(atoms[2])), max(int(atoms[1]), int(atoms[2])))
        else:
            continue
        explicit[key] = str(entry.get("id", f"$.torsions[{position}]"))
    typed_torsions = list(resolved.get("torsions", []) or [])
    records: list[dict[str, Any]] = []
    for ordinal, rotor in enumerate(rotors):
        if rotor.bond in explicit:
            raise ValueError(
                f"{ROTOR_SAMPLING_CONFLICT}: path-expanded bond "
                f"{rotor.bond[0] + 1}-{rotor.bond[1] + 1} "
                f"({', '.join(rotor.sources)}) collides with explicitly "
                f"declared torsion {explicit[rotor.bond]!r}; declare one "
                "sampling per bond (grids are never unioned)"
            )
        first, second = rotor.ordered
        axis_id = f"path{ordinal + 1}:{rotor.bond[0] + 1}-{rotor.bond[1] + 1}"
        typed_torsions.append(
            {
                "id": axis_id,
                "bond": [int(first), int(second)],
                "model": "relative_rotation_grid",
                "angles": [float(a) for a in rotor.angles],
                "treatment": "enumerate",
                "rotate_side": "left" if rotor.moving_atom == first else "right",
            }
        )
        records.append(
            {
                "id": axis_id,
                "bond": [rotor.bond[0] + 1, rotor.bond[1] + 1],
                "ordered": [first + 1, second + 1],
                "moving_atom": rotor.moving_atom + 1,
                "moving": [a + 1 for a in rotor.moving],
                "fixed": [a + 1 for a in rotor.fixed],
                "angles": [float(a) for a in rotor.angles],
                "sources": list(rotor.sources),
                "index_base": 1,
            }
        )
    resolve_torsion_axes(tuple(typed_torsions), index_base=0)
    resolved["torsions"] = typed_torsions
    resolved["paths_resolved"] = {
        "driving_id": structure.id,
        "driving_geometry_digest": structure.geometry_digest,
        "atom_symbols": list(structure.atoms),
        "declared_paths": [
            {
                "source": item.source,
                "start": item.raw_start,
                "end": item.raw_end,
                "move": item.move,
                "route": [atom + 1 for atom in item.route],
                "angles": list(item.angles),
                "index_base": 1,
            }
            for item in resolution.declared_paths
        ],
        "rotors": records,
        "warnings": list(resolution.warnings),
        "topology_digest": resolution.topology_digest,
        "declared_cartesian_size": int(resolution.raw_cartesian_size),
        "raw_cartesian_size": int(canonical_grid_size(rotors)),
    }
