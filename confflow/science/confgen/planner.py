#!/usr/bin/env python3

"""ConfGen v3 symbolic planner (CORE lane).

Spec normalization, lazy mixed-radix full-grid enumeration, seeded
raw-space sampling, symbolic preflight, and fail-before-geometry limit
checks. No geometry is generated here; counts are exact only with a
complete declared-group basis, otherwise conservative upper bounds.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # Annotation only; runtime resolve via local import (avoid cycles).
    from confflow.science.confgen.registry import ComponentRegistry

from confflow.domain._immutable import FrozenDict, thaw_value
from confflow.domain.structure import StructureRecord
from confflow.domain.topology import TopologyPatch
from confflow.science.confgen.model import AXIS_ORDER, SCHEMA_VERSION, StageEstimate
from confflow.science.confgen.wire_v3_constants import (  # noqa: F401
    WIRE_TOP_LEVEL_KEYS as _TOP_LEVEL_KEYS,  # noqa: F401
)

__all__ = [
    "MixedRadixGrid",
    "PreflightLimitError",
    "PreflightReport",
    "TorsionAxis",
    "build_typed_graph",
    "check_limits",
    "deferred_ranges",
    "normalize_spec",
    "preflight",
    "resolve_torsion_axes",
    "sample_indices",
    "sampling_of",
]

#: Compat alias: historical v3 spec top-level whitelist, re-exported
#: from ``wire_v3_constants`` (pure constants only; no algorithm lives
#: in the wire file). Imported above as ``_TOP_LEVEL_KEYS``. The real
#: unknown-key gate is ``_GENERIC_TOP_LEVEL_KEYS | registry.spec_keys``.

#: Spec-facing topology edge kinds (uppercase, matching the fixture
#: convention). BREAKING is accepted and preserved losslessly in scope but
#: has no lane B graph record yet (see core-api-ready.md open requests).
TOPO_EDGE_KINDS: tuple[str, ...] = ("COVALENT", "COORDINATION", "FORMING", "BREAKING")

#: Generic (non-component) top-level spec keys. Component keys come from
#: ``registry`` descriptors (``spec_keys``); the union is the unknown-key
#: whitelist. For the default registry the union equals ``_TOP_LEVEL_KEYS``
#: exactly, so the unknown-key error text is byte-identical.
_GENERIC_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "index_base",
        "index_convention",
        "topology",
        "stereochemistry",
        "exclusions",
        "tolerances",
        "limits",
        "sampling",
        "seed",
    }
)

#: Historical builtin defaults are now declared per descriptor
#: (``ComponentDescriptor.spec_defaults`` in registry order). Custom
#: components leave it empty so missing custom keys stay missing (no
#: undeclared growth). ``coordination=None`` vs ``[]``/``False`` matches
#: the pre-A4b ``normalize_spec`` output exactly; each call gets a fresh
#: copy so runs never share mutable defaults.

_TORSION_MODELS = (
    "relative_rotation_grid",
    "absolute_dihedral_grid",
    "chemical",
)

_TREATMENTS = ("enumerate", "preserve_input")


class PreflightLimitError(ValueError):
    """Raised when symbolic preflight exceeds declared hard limits."""


# ---------------------------------------------------------------------------
# Index-convention normalization (single explicit base, internal 0-based)
# ---------------------------------------------------------------------------


def _spec_has_indices(raw: Mapping[str, Any], *, registry: ComponentRegistry | None = None) -> bool:
    """Return True when any index-bearing section carries content.

    AG1 generic: the topology section (bonds/add/del/atoms) stays here
    (generic keys, never component-owned). Component sections delegate
    to each descriptor's ``has_indices(raw)`` in
    ``index_detection_order`` (builtins: torsions=10, coordination=20,
    rings=30, preserving the historical T->C->R probe order after the
    generic topology check). Each hook never raises (illegal shapes are
    False) so the later normalize dispatch raises the first error
    verbatim in registry order.
    """
    topology = raw.get("topology")
    if isinstance(topology, Mapping):
        for key in ("bonds", "add_bond", "del_bond", "atoms"):
            entries = topology.get(key)
            if isinstance(entries, (list, tuple)) and len(entries) > 0:
                return True
    from confflow.science.confgen.registry import resolve_registry

    resolved_registry = resolve_registry(registry)
    ordered = sorted(
        resolved_registry.descriptors, key=lambda descriptor: descriptor.index_detection_order
    )
    for descriptor in ordered:
        hook = descriptor.has_indices
        if hook is None:
            continue
        if hook(raw):
            return True
    return False


def _convert_index(value: Any, *, base: int, path: str) -> int:
    """Validate one index against the declared base, return 0-based."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer atom index, got {value!r}")
    if value < base:
        raise ValueError(f"{path} index {value} below declared index_base {base}")
    return int(value) - base


def _convert_index_list(values: Any, *, base: int, path: str) -> list[int]:
    """Validate/convert an index list to internal 0-based."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{path} must be a list of atom indices")
    return [
        _convert_index(item, base=base, path=f"{path}[{position}]")
        for position, item in enumerate(values)
    ]


def _convert_topo_entry(item: Any, *, base: int, path: str) -> Any:
    """Convert one validated topology entry to internal 0-based."""
    if isinstance(item, Mapping):
        converted: dict[str, Any] = {
            "atoms": _convert_index_list(item["atoms"], base=base, path=f"{path}.atoms"),
            "kind": str(item.get("kind", "COVALENT")),
        }
        if item.get("bond_order") is not None:
            converted["bond_order"] = float(item["bond_order"])
        if item.get("provenance") is not None:
            converted["provenance"] = str(item["provenance"])
        return converted
    return _convert_index_list(item, base=base, path=path)


# ---------------------------------------------------------------------------
# Torsion axes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TorsionAxis:
    """One resolved torsion generation axis.

    ``bond`` is the rotating unordered pair (0-based). ``frame`` holds the
    four dihedral atoms for absolute/chemical-four-atom axes (``bond`` is
    then ``frame[1:3]``). ``values`` are grid angles in degrees for the
    grid models; for ``chemical`` they are the resolved state angles in
    declaration order with ``state_names`` carrying the labels.
    """

    axis_id: str
    bond: tuple[int, int]
    frame: tuple[int, int, int, int] | None
    model: str
    values: tuple[float, ...]
    state_names: tuple[str, ...] = ()
    treatment: str = "enumerate"
    rotate_side: str = "left"

    def __post_init__(self) -> None:
        if not isinstance(self.axis_id, str) or not self.axis_id:
            raise ValueError("torsion axis id must be a non-empty string")
        if self.model not in _TORSION_MODELS:
            raise ValueError(f"unknown torsion model {self.model!r}")
        if self.treatment not in _TREATMENTS:
            raise ValueError(f"unknown torsion treatment {self.treatment!r}")
        if self.rotate_side not in ("left", "right"):
            raise ValueError(f"rotate_side must be 'left' or 'right', got {self.rotate_side!r}")


# NOTE (A4b-v2 move): ``_require_finite_angles``,
# ``_reject_periodic_duplicates`` and ``_require_index_list`` now live in
# ``torsion/spec.py`` (exclusive closure for ``resolve_torsion_axes``).
# Shared ``_convert_index``/``_TORSION_MODELS``/``_TREATMENTS``/``TorsionAxis``
# stay here for public-API compat (``torsion/stage.py`` imports from planner
# with no whitelist change); torsion/spec lazily imports the shared names.


def resolve_torsion_axes(
    entries: Sequence[Mapping[str, Any]],
    *,
    n_atoms: int | None = None,
    index_base: int = 0,
) -> tuple[TorsionAxis, ...]:
    """Resolve torsion axis entries into :class:`TorsionAxis` records.

    Compat delegation (AG2 generic): implementation is declared by the
    owning component via ``registry.legacy_compat`` and loaded lazily
    through ``resolve_compat`` (no kernel component import, no top-level
    cost, no call cycle). Signature, ``__all__`` and public import path
    are unchanged.

    Entries use the declared ``index_base`` (0 internal, 1 workflow) and are
    stored 0-based internally. Fail-closed checks: unique non-empty ids,
    known models/treatments, finite angles, distinct in-range indices,
    model/index-shape consistency, opt-in chemical ``states`` (explicit
    map, no defaults), and duplicate bond axes (same unordered rotating
    pair twice).
    """
    from confflow.science.confgen.registry import resolve_compat

    _impl = resolve_compat("resolve_torsion_axes")

    return cast("tuple[TorsionAxis, ...]", _impl(entries, n_atoms=n_atoms, index_base=index_base))


# ---------------------------------------------------------------------------
# Spec normalization
# ---------------------------------------------------------------------------


def _check_int(value: Any, *, path: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{path} must be an integer >= {minimum}, got {value!r}")
    return value


def normalize_spec(
    raw: Mapping[str, Any], *, registry: ComponentRegistry | None = None
) -> dict[str, Any]:
    """Normalize a raw v3 spec into a JSON-compatible resolved spec.

    Unknown top-level keys fail closed. Torsion entries are fully resolved
    (finite angles, duplicate bonds rejected); coordination/ring sections
    pass through structurally -- semantic validation is owned by those
    lanes. Missing ``schema_version`` defaults to 3; any other value fails.

    A4b component dispatch (V51): generic keys stay here; component-owned
    keys (``coordination``/``rings``/``torsions``/``paths``/
    ``strict_path_bond_check`` plus any custom ``spec_keys``) are normalized
    by each descriptor's ``normalize_spec(raw, *, index_base)`` in registry
    order. For the default registry that order is coordination(10) ->
    rings(20) -> torsions(30), matching the pre-A4b C->R->T validation
    order, so multi-error specs raise the same first error. Component
    results are merged; a component returning an unowned key fails closed,
    and a component missing an expected owned-present key or returning an
    owned-but-not-in-raw key fails closed (exact expected vs partial).
    Missing builtin keys are filled with historical defaults (None/[]/False)
    so default output bytes never change; missing custom keys stay missing.
    """
    from confflow.science.confgen.registry import resolve_registry

    resolved_registry = resolve_registry(registry)
    if not isinstance(raw, Mapping):
        raise ValueError("spec must be a mapping")
    owned_keys: set[str] = set()
    for _descriptor in resolved_registry.descriptors:
        owned_keys.update(_descriptor.spec_keys)
    allowed = set(_GENERIC_TOP_LEVEL_KEYS) | owned_keys
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"spec holds unknown keys {unknown}; allowed {sorted(allowed)}")
    version = raw.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError(f"spec schema_version must be {SCHEMA_VERSION}, got {version!r}")

    spec: dict[str, Any] = {"schema_version": SCHEMA_VERSION}

    # Single explicit index convention across topology, coordination, rings,
    # and torsions. The workflow boundary may declare index_base 1 (fixture/
    # workflow 1-based) or 0 (internal); normalize converts every reference
    # to internal 0-based exactly once. Re-normalization sees index_base 0
    # and is a validated no-op (idempotent). Undeclared bases with indices
    # present fail closed -- never guess from whether atom 0 appears.
    declared_base = raw.get("index_base")
    if declared_base is not None and declared_base not in (0, 1):
        raise ValueError("spec index_base must be 0 or 1 when declared")
    if declared_base is None and _spec_has_indices(raw, registry=resolved_registry):
        raise ValueError(
            "spec carries atom indices but declares no index_base; "
            "declare index_base: 0 or 1 explicitly"
        )
    base = int(declared_base) if declared_base is not None else 0
    marker = raw.get("index_convention")
    if marker is not None and marker != "internal-0-based:normalized":
        raise ValueError(f"spec index_convention marker {marker!r} is not recognized")
    spec["index_convention"] = "internal-0-based:normalized"
    spec["index_base"] = 0

    # Root compat (v2): preset builtin defaults in historical order first,
    # so output key order never depends on input presence. Owned partials
    # update existing keys without moving position; custom new keys insert
    # in descriptor order. Order comes from each descriptor's
    # ``spec_defaults`` declaration in registry order (= C->R->T for the
    # default registry, matching history); no default literals live here.
    # AG1 v2: defaults are stored frozen (tuple/FrozenDict) and thawed here
    # into a fresh deep copy per run, so nested custom defaults never leak
    # across runs and mutating one output never touches the cached
    # registry. Thawed lists keep the legacy list type; insertion order is
    # unchanged. A default for an unowned key or a repeated default key
    # fails closed (customs cannot overwrite another owner).
    _seen_defaults: set[str] = set()
    for _descriptor in resolved_registry._ordered():
        _owned = set(_descriptor.spec_keys)
        for _key, _default in _descriptor.spec_defaults:
            if _key not in _owned:
                raise ValueError(
                    f"component {_descriptor.id!r} declares default for unowned spec key "
                    f"{_key!r}; owned {sorted(_owned)}"
                )
            if _key in _seen_defaults:
                raise ValueError(
                    f"duplicate spec default {_key!r} from component {_descriptor.id!r}; "
                    "already filled"
                )
            _seen_defaults.add(_key)
            spec[_key] = thaw_value(_default)

    # Component-owned keys: dispatch in registry order (= C->R->T for the
    # default registry). Each hook sees the whole raw spec plus the resolved
    # top-level base and returns exactly its owned keys present in the input.
    _filled_by_component: set[str] = set()
    for _descriptor in resolved_registry._ordered():
        hook = _descriptor.normalize_spec
        if hook is None:
            # No hook (e.g. legacy custom descriptors from A4a tests):
            # pass owned values through unchanged when present (compat
            # passowned, preserves old explicit pseudo-stage).
            for _key in _descriptor.spec_keys:
                if _key in raw:
                    spec[_key] = raw[_key]
                    _filled_by_component.add(_key)
            continue
        partial = hook(raw, index_base=base)
        if not isinstance(partial, Mapping):
            raise ValueError(f"component {_descriptor.id!r} normalize_spec must return a mapping")
        for _key in partial.keys():
            if _key not in _descriptor.spec_keys:
                raise ValueError(
                    f"component {_descriptor.id!r} returned unowned spec key {_key!r}; "
                    f"owned {sorted(_descriptor.spec_keys)}"
                )
        _expected = {_k for _k in _descriptor.spec_keys if _k in raw}
        _actual = set(partial.keys())
        if _actual != _expected:
            _missing = sorted(_expected - _actual)
            _extra = sorted(_actual - _expected)
            raise ValueError(
                f"component {_descriptor.id!r} normalize_spec keys mismatch: "
                f"expected {sorted(_expected)}, got {sorted(_actual)}; "
                f"missing {_missing}; extra {_extra}"
            )
        for _key in _descriptor.spec_keys:
            if _key in partial:
                if _key in _filled_by_component:
                    raise ValueError(
                        f"component {_descriptor.id!r} returned duplicate spec key {_key!r}"
                    )
                spec[_key] = partial[_key]
                _filled_by_component.add(_key)

    topology = raw.get("topology", {})
    if not isinstance(topology, Mapping):
        raise ValueError("spec topology must be a mapping")
    allowed_topo = {"bonds", "add_bond", "del_bond", "atoms", "index_base"}
    unknown_topo = sorted(set(topology) - allowed_topo)
    if unknown_topo:
        raise ValueError(f"spec topology holds unknown keys {unknown_topo}")
    if topology.get("index_base") is not None:
        raise ValueError("index convention is top-level only; topology must not declare index_base")
    topo: dict[str, Any] = {}
    for key in ("bonds", "add_bond", "del_bond"):
        if topology.get(key) is not None:
            pairs = topology[key]
            if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
                raise ValueError(f"spec topology.{key} must be a list of index entries")
            validated: list[Any] = []
            for index, item in enumerate(pairs):
                shape = _validate_topo_entry(item, path=f"spec topology.{key}[{index}]")
                validated.append(
                    _convert_topo_entry(shape, base=base, path=f"spec topology.{key}[{index}]")
                )
            topo[key] = validated
    if topology.get("atoms") is not None:
        declarations = topology["atoms"]
        if isinstance(declarations, (str, bytes)) or not isinstance(declarations, Sequence):
            raise ValueError("spec topology.atoms must be a list of atom declarations")
        converted_decls: list[dict[str, Any]] = []
        for index, item in enumerate(declarations):
            shape = _validate_atom_declaration(item, path=f"spec topology.atoms[{index}]")
            converted_decls.append(
                {
                    "index": _convert_index(
                        shape["index"], base=base, path=f"spec topology.atoms[{index}].index"
                    ),
                    "label": shape.get("label"),
                    "role": shape.get("role", ""),
                    "stereo": shape.get("stereo"),
                }
            )
        topo["atoms"] = converted_decls
    if "bonds" in topo and ("add_bond" in topo or "del_bond" in topo):
        raise ValueError(
            "explicit topology.bonds wins outright and cannot combine with add/del_bond"
        )
    # BREAKING stays a typed edge in the lane B graph (never covalent, never
    # discarded to scope). No partition: bonds entries keep their kinds.
    spec["topology"] = topo

    stereo = raw.get("stereochemistry", {})
    if stereo is not None and not isinstance(stereo, Mapping):
        raise ValueError("spec stereochemistry must be a mapping or null")
    # Recorded only: core claims no stereo authority.
    spec["stereochemistry"] = dict(stereo) if stereo is not None else {}

    exclusions = raw.get("exclusions", [])
    if not isinstance(exclusions, (list, tuple)):
        raise ValueError("spec exclusions must be a list")
    exclusion_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(exclusions):
        path = f"$.exclusions[{index}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path} must be a mapping")
        axis = entry.get("axis")
        if axis not in AXIS_ORDER:
            raise ValueError(f"{path}.axis must be one of {AXIS_ORDER}")
        match = entry.get("match")
        if not isinstance(match, Mapping) or not match:
            raise ValueError(f"{path}.match must be a non-empty mapping")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"{path}.reason must be a non-empty string")
        record = {"axis": axis, "match": dict(match), "reason": reason}
        proof = entry.get("proof")
        if proof is not None:
            if not isinstance(proof, Mapping):
                raise ValueError(f"{path}.proof must be a mapping")
            proof_id = proof.get("proof_id")
            if not isinstance(proof_id, str) or not proof_id:
                raise ValueError(f"{path}.proof must carry a non-empty proof_id")
            record["proof"] = dict(proof)
        exclusion_entries.append(record)
    spec["exclusions"] = exclusion_entries

    tolerances = raw.get("tolerances", {})
    if tolerances is None:
        tolerances = {}
    from confflow.science.confgen.tolerances import resolve_tolerances

    resolved_tol = resolve_tolerances(tolerances)
    spec["tolerances"] = {
        "bond_length_atol": resolved_tol.bond_length_atol,
        "dihedral_atol_deg": resolved_tol.dihedral_atol_deg,
        "clash_threshold": resolved_tol.clash_threshold,
        "bond_scale": resolved_tol.bond_scale,
        "parent_lock_atol_deg": resolved_tol.parent_lock_atol_deg,
        "ring_bond_atol": resolved_tol.ring_bond_atol,
        "substituent_bond_atol": resolved_tol.substituent_bond_atol,
        "frame_det_min": resolved_tol.frame_det_min,
        "ring_angle_atol_deg": resolved_tol.ring_angle_atol_deg,
        "ring_torsion_atol_deg": resolved_tol.ring_torsion_atol_deg,
        "coordination_bond_atol": resolved_tol.coordination_bond_atol,
        "coordination_angle_atol_deg": resolved_tol.coordination_angle_atol_deg,
    }

    limits = raw.get("limits", {})
    if limits is None:
        limits = {}
    if not isinstance(limits, Mapping):
        raise ValueError("spec limits must be a mapping")
    allowed_limits = {"max_declared_states", "max_output_structures"}
    unknown_limits = sorted(set(limits) - allowed_limits)
    if unknown_limits:
        raise ValueError(f"spec limits holds unknown keys {unknown_limits}")
    resolved_limits: dict[str, Any] = {}
    for key in allowed_limits:
        if limits.get(key) is not None:
            resolved_limits[key] = _check_int(limits[key], path=f"$.limits.{key}")
    spec["limits"] = resolved_limits

    seed = raw.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError(f"$.seed must be an integer or null, got {seed!r}")
    spec["seed"] = seed

    # Single seed authority: the top-level seed is the SOLE stochastic
    # authority. The sampling section carries the cap only; any sub-seed
    # (sampling.seed) fails closed -- declare top-level seed only. A cap
    # without a top-level seed fails closed (no silent stochasticity).
    sampling = raw.get("sampling", {})
    if sampling is None:
        sampling = {}
    if not isinstance(sampling, Mapping):
        raise ValueError("spec sampling must be a mapping")
    allowed_sampling = {"cap"}
    unknown_sampling = sorted(set(sampling) - allowed_sampling)
    if unknown_sampling:
        raise ValueError(
            f"spec sampling holds unknown keys {unknown_sampling}; "
            "the sampling section carries cap only (top-level seed is the "
            "sole stochastic authority)"
        )
    cap = sampling.get("cap")
    if cap is not None:
        _check_int(cap, path="$.sampling.cap")
    if cap is not None and seed is None:
        raise ValueError("$.sampling.cap requires an explicit top-level $.seed")
    spec["sampling"] = {"cap": cap}
    return spec


def sampling_of(spec: Mapping[str, Any]) -> tuple[int | None, int | None]:
    """Return ``(cap, seed)`` with the SOLE top-level seed authority.

    The sampling section carries the cap only; the seed always resolves
    from the top-level ``seed`` (workflow schema exposes sampling.cap plus
    top-level seed). Conflicting sub-seeds fail closed at normalization.
    """
    sampling = spec.get("sampling", {}) or {}
    seed = spec.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError(f"$.seed must be an integer or null, got {seed!r}")
    return sampling.get("cap"), seed


def _validate_topo_entry(item: Any, *, path: str) -> Any:
    """Validate one topology entry shape (base-agnostic; see conversion).

    Plain pairs default to COVALENT. Typed mappings carry an uppercase kind
    (COVALENT/COORDINATION/FORMING/BREAKING), optional bond_order metadata,
    and optional provenance. Lowercase kinds fail closed (lane B normalizes
    them, but the spec boundary requires the uppercase fixture spelling).
    """
    if isinstance(item, Mapping):
        unknown = sorted(set(item) - {"atoms", "kind", "bond_order", "provenance"})
        if unknown:
            raise ValueError(f"{path} holds unknown keys {unknown}")
        atoms = item.get("atoms")
        if isinstance(atoms, (str, bytes)) or not isinstance(atoms, Sequence):
            raise ValueError(f"{path}.atoms must be an index pair")
        pair = list(atoms)
        if len(pair) != 2:
            raise ValueError(f"{path}.atoms must hold exactly two indices")
        kind = item.get("kind", "COVALENT")
        if kind not in TOPO_EDGE_KINDS:
            raise ValueError(f"{path}.kind must be one of {TOPO_EDGE_KINDS}, got {kind!r}")
        for name, value in (("first", pair[0]), ("second", pair[1])):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{path}.atoms {name} must be an integer, got {value!r}")
        if pair[0] == pair[1]:
            raise ValueError(f"{path}.atoms must name two distinct atoms")
        record: dict[str, Any] = {"atoms": [int(pair[0]), int(pair[1])], "kind": str(kind)}
        if item.get("bond_order") is not None:
            order = item["bond_order"]
            if isinstance(order, bool) or not isinstance(order, (int, float)):
                raise ValueError(f"{path}.bond_order must be a number or null")
            record["bond_order"] = float(order)
        if item.get("provenance") is not None:
            if not isinstance(item["provenance"], str):
                raise ValueError(f"{path}.provenance must be a string")
            record["provenance"] = str(item["provenance"])
        return record
    if isinstance(item, (str, bytes)) or not isinstance(item, Sequence):
        raise ValueError(f"{path} must be an index pair or typed edge mapping")
    pair = list(item)
    if len(pair) != 2:
        raise ValueError(f"{path} must hold exactly two indices")
    for name, value in (("first", pair[0]), ("second", pair[1])):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{path} {name} must be an integer, got {value!r}")
    if pair[0] == pair[1]:
        raise ValueError(f"{path} must name two distinct atoms")
    return [int(pair[0]), int(pair[1])]


def _validate_atom_declaration(item: Any, *, path: str) -> dict[str, Any]:
    """Validate one topology atom declaration (roles/stereo/labels)."""
    if not isinstance(item, Mapping):
        raise ValueError(f"{path} must be a mapping")
    unknown = sorted(set(item) - {"index", "label", "role", "stereo"})
    if unknown:
        raise ValueError(f"{path} holds unknown keys {unknown}")
    if "index" not in item:
        raise ValueError(f"{path} must carry 'index'")
    shape: dict[str, Any] = {"index": item["index"]}
    if item.get("label") is not None:
        if not isinstance(item["label"], str):
            raise ValueError(f"{path}.label must be a string")
        shape["label"] = item["label"]
    shape["role"] = str(item.get("role", ""))
    if not isinstance(shape["role"], str):
        raise ValueError(f"{path}.role must be a string")
    stereo = item.get("stereo")
    if stereo is not None and not isinstance(stereo, str):
        raise ValueError(f"{path}.stereo must be a string or null")
    shape["stereo"] = stereo
    return shape


# NOTE (AG2 generic): the overlay implementation is declared by the
# owning component via ``registry.legacy_compat``. The planner keeps the
# old symbol/signature and loads it through ``resolve_compat`` (no kernel
# component import); ``_build_typed_graph`` calls components via the
# registry instance instead.


def _overlay_declared_coordination_scope(
    resolved: Mapping[str, Any],
    n_atoms: int,
    adjacency: list[set[int]],
    typed: dict[tuple[int, int, Any], Any],
    explicit_covalent: set[tuple[int, int]],
) -> None:
    """Compat delegation for the moved overlay (AG2 generic).

    Implementation is declared by the owning component and loaded through
    the registry compat lookup; this wrapper preserves the old
    symbol/signature. New code should call the registry
    ``contribute_topology`` hook via ``TopologyBuildContext``.
    """
    from confflow.science.confgen.kernel_records import TopologyBuildContext
    from confflow.science.confgen.registry import resolve_compat

    def _check(value: int, path: str) -> None:
        if value < 0 or value >= n_atoms:
            raise ValueError(f"{path} index {value} out of range for {n_atoms} atoms")

    build = TopologyBuildContext(
        n_atoms=n_atoms,
        adjacency=adjacency,
        typed=typed,
        explicit_covalent=explicit_covalent,
        check_index=_check,
    )
    _impl = resolve_compat("overlay_declared_scope")
    _impl(resolved, build)


def _apply_structure_patch(
    structure: StructureRecord,
    n_atoms: int,
    adjacency: list[set[int]],
    typed: dict[tuple[int, int, Any], Any],
    _check: Any,
) -> None:
    """Apply the record's TopologyPatch on top of perception + corrections.

    Runs after ``add_bond``/``del_bond`` so the declared correction intent
    wins over raw perception.  Simultaneous spec corrections and a record
    patch are rejected earlier by :func:`check_spec_patch_conflict`, so a
    patch here never meets spec add/del entries.  Patched covalent edges
    are recorded as lane B records with ``topology_patch`` provenance.
    """
    from confflow.science.confgen.graph import EdgeType, TypedEdge

    # A captured working graph already incorporates the root patch and any
    # explicit scientific overlays. Never replay historical corrections on it.
    if structure.working_topology is not None:
        return
    patch = getattr(structure, "topology_patch", None)
    if patch is None:
        return
    if isinstance(patch, dict):
        patch = TopologyPatch.from_dict(patch)
    if not isinstance(patch, TopologyPatch) or patch.is_empty:
        return
    for first_one, second_one in patch.add_edges:
        first, second = first_one - 1, second_one - 1
        _check(first, "topology_patch.add_edges")
        _check(second, "topology_patch.add_edges")
        pair = (min(first, second), max(first, second))
        for _a, _b, kind in list(typed):
            if (_a, _b) == pair and kind is not EdgeType.COVALENT:
                raise ValueError(
                    f"topology_patch adds {pair} already carrying typed role "
                    f"{kind.value}; contradictory kinds for one pair fail closed"
                )
        adjacency[first].add(second)
        adjacency[second].add(first)
        typed[(pair[0], pair[1], EdgeType.COVALENT)] = TypedEdge(
            a=pair[0],
            b=pair[1],
            type=EdgeType.COVALENT,
            provenance="topology_patch",
        )
    for first_one, second_one in patch.delete_edges:
        first, second = first_one - 1, second_one - 1
        _check(first, "topology_patch.delete_edges")
        _check(second, "topology_patch.delete_edges")
        adjacency[first].discard(second)
        adjacency[second].discard(first)
        typed.pop((min(first, second), max(first, second), EdgeType.COVALENT), None)


def _topo_edge(item: Any) -> tuple[tuple[int, int], Any, dict[str, Any]]:
    """Normalize a converted (0-based) topology entry to (pair, EdgeType, extra)."""
    from confflow.science.confgen.graph import EdgeType, normalize_edge_kind

    if isinstance(item, Mapping):
        first, second = item["atoms"]
        extra: dict[str, Any] = {}
        if item.get("bond_order") is not None:
            extra["bond_order"] = float(item["bond_order"])
        if item.get("provenance") is not None:
            extra["provenance"] = str(item["provenance"])
        return (int(first), int(second)), normalize_edge_kind(item.get("kind", "COVALENT")), extra
    first, second = item
    return (int(first), int(second)), EdgeType.COVALENT, {}


def _collect_graph_metadata(
    resolved: Mapping[str, Any], n_atoms: int, *, registry: ComponentRegistry
) -> dict[str, Any]:
    """Collect generic typed-graph metadata at the old metal-center site.

    Loops descriptors in registry order; each ``graph_metadata`` returns
    a mapping (e.g. ``{"metal_center": int}``) or None for no
    contribution. Duplicate keys across components fail closed; a new
    component with no contribution leaves the old graph unchanged.
    Validation (range/type) lives in the owning component (coordination)
    and raises here, at the pre-AG1 ``_graph_metal_center`` position
    (before the explicit-bonds branch), never moved early to the overlay.
    """
    merged: dict[str, Any] = {}
    for descriptor in registry._ordered():
        hook = descriptor.graph_metadata
        if hook is None:
            continue
        part = hook(resolved, n_atoms)
        if part is None:
            continue
        if not isinstance(part, Mapping):
            raise ValueError(
                f"component {descriptor.id!r} graph_metadata must return a mapping or None"
            )
        for key, value in part.items():
            if key in merged:
                raise ValueError(
                    f"duplicate graph metadata key {key!r} from component {descriptor.id!r}"
                )
            merged[key] = value
    return merged


def build_typed_graph(
    structure: StructureRecord,
    topology: Mapping[str, Any],
    resolved: Mapping[str, Any],
    *,
    registry: ComponentRegistry | None = None,
) -> tuple[list[list[int]], Any]:
    """Build covalent adjacency plus the lane B typed-graph authority.

    Public compat wrapper (A4a root ruling, V24): existing 3-positional-arg
    calls keep working. ``registry`` defaults to ``None`` (resolved to the
    shared immutable default); the resolved instance is threaded into the
    private ``_build_typed_graph``. No component hook is called here (V50;
    hooks land in A4b/A4c).
    """
    from confflow.science.confgen.registry import resolve_registry

    resolved_registry = resolve_registry(registry)
    return _build_typed_graph(structure, topology, resolved, registry=resolved_registry)


def _build_typed_graph(
    structure: StructureRecord,
    topology: Mapping[str, Any],
    resolved: Mapping[str, Any],
    *,
    registry: ComponentRegistry,
) -> tuple[list[list[int]], Any]:
    """Private typed-graph implementation (registry mandatory, keyword-only).

    A4c: ``registry`` is the same instance threaded from ``build_context``
    (never re-resolved); ``contribute_topology`` hooks run in registry order
    at the pre-A4c overlay site (after structure patch, before graph
    assembly) via a shared ``TopologyBuildContext`` (same objects, edge
    order preserved). Only coordination contributes; generic topology
    (bonds/add/del/patch) stays here.

    All entries arrive internal 0-based (normalize_spec converts under the
    single explicit top-level ``index_base``). Explicit ``topology["bonds"]``
    wins outright and is built through the lane B workflow parser
    (``TypedGraph.from_mapping`` with ``convention="internal0"``): records
    from config mappings only, never file paths. Otherwise legacy covalent
    perception plus ``add_bond``/``del_bond`` corrections, recorded as the
    same lane B records. FORMING/COORDINATION/BREAKING edges are authority
    context and never enter the covalent adjacency used by torsion
    mechanics. ``tolerances`` are read from the resolved spec.
    """
    from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph
    from confflow.science.confgen.model import covalent_adjacency_of

    if not isinstance(topology, Mapping):
        raise ValueError("topology must be a mapping")
    n_atoms = len(structure.atoms)
    elements = list(structure.atoms)
    from confflow.science.topology import check_spec_patch_conflict as _check_patch_conflict

    try:
        _check_patch_conflict(topology, structure, where="confgen v3 topology")
    except Exception as exc:
        raise ValueError(str(exc)) from exc
    # AG1 generic: collect typed-graph metadata at the pre-AG1
    # _graph_metal_center site (same position, same timing, before the
    # explicit-bonds branch). Only "metal_center" feeds the graph; other
    # keys are ignored so new components without contribution leave the
    # old graph unchanged.
    _metadata = _collect_graph_metadata(resolved, n_atoms, registry=registry)
    metal_center = _metadata.get("metal_center")
    tolerances = resolved.get("tolerances", {}) if isinstance(resolved, Mapping) else {}
    bond_scale = float(tolerances.get("bond_scale", 1.15))

    def _check(value: int, path: str) -> None:
        if value < 0 or value >= n_atoms:
            raise ValueError(f"{path} index {value} out of range for {n_atoms} atoms")

    if topology.get("bonds") is not None:
        pairs = topology["bonds"]
        if isinstance(pairs, (str, bytes)) or not isinstance(pairs, Sequence):
            raise ValueError("topology.bonds must be a list of entries")
        forming: list[list[int]] = []
        for index, item in enumerate(pairs):
            (first, second), kind, _extra = _topo_edge(item)
            _check(first, f"topology.bonds[{index}]")
            _check(second, f"topology.bonds[{index}]")
            if kind is EdgeType.FORMING:
                forming.append([first, second])
        mapping = {
            "bonds": list(pairs),
            "atoms": list(topology.get("atoms", []) or []),
            "metal_center": metal_center,
            "reaction_pairs": forming,
            "source": "v3-spec-explicit",
        }
        graph = TypedGraph.from_mapping(
            mapping, elements, convention="internal0", source="v3-spec-explicit"
        )
        if graph.natoms != n_atoms:  # defensive; from_mapping builds from elements
            raise ValueError("typed graph atom count mismatches structure")
        return [list(row) for row in covalent_adjacency_of(graph)], graph

    from confflow.domain.elements import atomic_number
    from confflow.science.bonds import perceive_adjacency

    stored_graph = getattr(structure, "working_topology", None)
    if stored_graph is not None:
        # Authoritative persisted graph: never re-perceive moved geometry.
        perceived = [[int(v) for v in row] for row in stored_graph]
        if len(perceived) != n_atoms:
            raise ValueError("persisted working_topology row count mismatches atom count")
    else:
        try:
            numbers = [atomic_number(symbol) for symbol in structure.atoms]
        except Exception as exc:
            raise ValueError(f"bond perception failed: {exc}") from exc
        try:
            perceived = perceive_adjacency(
                numbers,
                [tuple(point) for point in structure.coordinates],
                bond_scale=bond_scale,
            )
        except ValueError as exc:
            raise ValueError(f"bond perception failed: {exc}") from exc
    if len(perceived) != n_atoms:
        raise ValueError("perceived adjacency row count mismatches atom count")
    adjacency: list[set[int]] = [set(row) for row in perceived]
    typed: dict[tuple[int, int, Any], TypedEdge] = {}
    for first in range(n_atoms):
        for second in adjacency[first]:
            if second > first:
                edge = TypedEdge(a=first, b=second, type=EdgeType.COVALENT)
                typed[(first, second, EdgeType.COVALENT)] = edge
    reaction_pairs = []
    explicit_covalent: set[tuple[int, int]] = set()
    typed_non_covalent: set[tuple[int, int]] = set()
    for key in ("add_bond", "del_bond"):
        entries = topology.get(key) or []
        if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
            raise ValueError(f"topology.{key} must be a list of entries")
        for index, item in enumerate(entries):
            (first, second), kind, extra = _topo_edge(item)
            _check(first, f"topology.{key}[{index}]")
            _check(second, f"topology.{key}[{index}]")
            pair = (min(first, second), max(first, second))
            if key == "add_bond":
                if kind is EdgeType.COVALENT:
                    if pair in typed_non_covalent:
                        raise ValueError(
                            f"topology.add_bond[{index}] declares COVALENT for pair "
                            f"{pair} already given a typed non-covalent role; "
                            "contradictory kinds for one pair fail closed"
                        )
                    explicit_covalent.add(pair)
                    adjacency[first].add(second)
                    adjacency[second].add(first)
                else:
                    if pair in explicit_covalent:
                        raise ValueError(
                            f"topology.add_bond[{index}] declares {kind.value} for pair "
                            f"{pair} already declared COVALENT; contradictory kinds "
                            "for one pair fail closed"
                        )
                    # Authoritative typed overlay: a declared non-covalent
                    # role replaces the distance-guessed COVALENT edge, so
                    # the pair leaves the covalent-only adjacency used by
                    # ring/torsion mechanics.
                    typed.pop((pair[0], pair[1], EdgeType.COVALENT), None)
                    adjacency[first].discard(second)
                    adjacency[second].discard(first)
                    typed_non_covalent.add(pair)
                edge = TypedEdge(
                    a=pair[0],
                    b=pair[1],
                    type=kind,
                    bond_order=extra.get("bond_order"),
                    provenance=extra.get("provenance", "add_bond"),
                )
                typed[(pair[0], pair[1], kind)] = edge
                if kind is EdgeType.FORMING:
                    reaction_pairs.append(pair)
            else:
                if kind is not EdgeType.COVALENT:
                    raise ValueError(
                        f"topology.del_bond[{index}] must name a covalent pair; "
                        "typed-edge removal is not a perception correction"
                    )
                # Legacy-compatible silent discard of absent pairs.
                adjacency[first].discard(second)
                adjacency[second].discard(first)
                typed.pop((pair[0], pair[1], EdgeType.COVALENT), None)
    _apply_structure_patch(structure, n_atoms, adjacency, typed, _check)
    from confflow.science.confgen.kernel_records import TopologyBuildContext

    build = TopologyBuildContext(
        n_atoms=n_atoms,
        adjacency=adjacency,
        typed=typed,
        explicit_covalent=explicit_covalent,
        check_index=_check,
    )
    for _descriptor in registry._ordered():
        _hook = _descriptor.contribute_topology
        if _hook is not None:
            _hook(resolved, build)
    atoms = [AtomRef(index=position, element=symbol) for position, symbol in enumerate(elements)]
    for decl in topology.get("atoms", []) or []:
        position = int(decl["index"])
        _check(position, "topology.atoms declaration")
        atoms[position] = AtomRef(
            index=position,
            element=atoms[position].element,
            label=decl.get("label"),
            role=str(decl.get("role", "")),
            stereo=decl.get("stereo"),
        )
    graph = TypedGraph(
        atoms=tuple(atoms),
        edges=tuple(typed.values()),
        metal_center=metal_center,
        reaction_pairs=tuple(reaction_pairs),
        source="v3-spec-perceived",
    )
    resolved_adjacency = [sorted(row) for row in adjacency]
    if covalent_adjacency_of(graph) != tuple(tuple(row) for row in resolved_adjacency):
        raise ValueError("covalent adjacency diverged from typed graph authority")
    return resolved_adjacency, graph


# ---------------------------------------------------------------------------
# Lazy mixed-radix grids and seeded sampling
# ---------------------------------------------------------------------------


class MixedRadixGrid:
    """Lazy full-grid enumeration over per-axis value counts.

    ``index_to_combo`` decodes a flat ordinal into per-axis indices in
    row-major order (last axis fastest), exactly matching
    ``itertools.product`` order, so v3 ordinals equal legacy grid ordinals.
    The grid is never materialized (no ``list(product)``).
    """

    def __init__(self, sizes: Sequence[int]) -> None:
        counts = tuple(sizes)
        for size in counts:
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise ValueError(f"grid sizes must be integers >= 1, got {size!r}")
        self._sizes = counts
        total = 1
        for size in counts:
            total *= size
        self._total = total

    @property
    def sizes(self) -> tuple[int, ...]:
        """Return the per-axis value counts."""
        return self._sizes

    @property
    def total(self) -> int:
        """Return the total grid size (product of counts)."""
        return self._total

    def index_to_combo(self, index: int) -> tuple[int, ...]:
        """Decode a flat ordinal into per-axis indices (lazy, O(axes))."""
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"grid index must be an integer, got {index!r}")
        if index < 0 or index >= self._total:
            raise ValueError(f"grid index {index} out of range for total {self._total}")
        combo = [0] * len(self._sizes)
        rest = index
        for position in range(len(self._sizes) - 1, -1, -1):
            combo[position] = rest % self._sizes[position]
            rest //= self._sizes[position]
        return tuple(combo)

    def iter_indices(self) -> Iterator[int]:
        """Yield flat ordinals lazily in stable declared order."""
        return iter(range(self._total))


def sample_indices(total: int, cap: int | None, seed: int | None) -> Sequence[int]:
    """Sample the raw index space without replacement, BEFORE geometry.

    ``cap=None`` returns a lazy ``range`` over the full grid in ordinal
    order (deterministic no-seed default; never materialized, so arbitrary
    large totals are supported). A cap without an explicit seed fails
    closed. A capped sample materializes only O(cap) indices via Floyd's
    algorithm (uniform without replacement, no population scan), then
    sorted ordinal presentation. No first-N bias. The cap counts attempted
    TARGETS, never guaranteed successes.
    """
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError(f"total must be an integer >= 0, got {total!r}")
    if cap is None:
        return range(total)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError(f"cap must be an integer >= 1, got {cap!r}")
    if seed is None:
        raise ValueError("sampling cap requires an explicit seed (no silent stochasticity)")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"seed must be an integer, got {seed!r}")
    if cap >= total:
        return range(total)
    rng = random.Random(int(seed))
    selected: set[int] = set()
    for upper in range(total - cap, total):
        candidate = rng.randrange(upper + 1)
        selected.add(upper if candidate in selected else candidate)
    return sorted(selected)


def deferred_ranges(sampled: Sequence[int], total: int) -> tuple[tuple[int, int], ...]:
    """Compress the unsampled complement into ascending inclusive ranges.

    Invariant: ``len(sampled) + sum(end - start + 1) == total``, i.e. the
    raw space equals sampled targets plus deferred ranges.
    """
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError(f"total must be an integer >= 0, got {total!r}")
    chosen = sorted(int(index) for index in sampled)
    if any(index < 0 or index >= total for index in chosen):
        raise ValueError("sampled indices out of range")
    if len(set(chosen)) != len(chosen):
        raise ValueError("sampled indices must be unique")
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for index in chosen:
        if cursor < index:
            ranges.append((cursor, index - 1))
        cursor = index + 1
    if cursor < total:
        ranges.append((cursor, total - 1))
    return tuple(ranges)


# ---------------------------------------------------------------------------
# Symbolic preflight and limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Symbolic enumeration counts independent of realized geometry."""

    per_axis: Mapping[str, StageEstimate] = field(default_factory=FrozenDict)
    total_declared: int = 0
    total_upper_bound: int = 0
    exact: bool = False
    basis: str = "empty"

    def __post_init__(self) -> None:
        if not isinstance(self.per_axis, FrozenDict):
            object.__setattr__(self, "per_axis", FrozenDict(dict(self.per_axis)))
        for name in ("total_declared", "total_upper_bound"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an integer >= 0")
        if self.total_upper_bound < self.total_declared:
            raise ValueError("total_upper_bound must cover total_declared")
        if not isinstance(self.exact, bool):
            raise ValueError("exact must be a bool")
        if not isinstance(self.basis, str) or not self.basis:
            raise ValueError("basis must be a non-empty string")


def preflight(context: Any, stages: Sequence[Any]) -> PreflightReport:
    """Symbolically enumerate requested generation WITHOUT geometry.

    Exact only when every stage reports ``exact=True`` with
    ``scope_coverage="exact"`` (parent-independent symbolic enumeration
    covering the requested scope). Conditional stages whose later
    enumeration can change counts must report ``upper_bound`` coverage, and
    the total is then a conservative upper bound. No unknown count
    masquerades as exact. Exact raw counts never imply complete symmetry
    accounting (no Burnside claim without a verified complete group).
    Ring refusal and other fail-closed stage errors propagate here, before
    any geometry.
    """
    from confflow.science.confgen.model import WorkingRealization

    root = WorkingRealization(structure=context.structure, state_key=context.input_state_key)
    per_axis: dict[str, StageEstimate] = {}
    declared = 1
    upper = 1
    exact = True
    basis_parts: list[str] = []
    for stage in stages:
        estimate = stage.estimate(root, context)
        per_axis[stage.axis] = estimate
        declared *= estimate.declared_count
        upper *= estimate.upper_bound
        coverage = estimate.details.get("scope_coverage")
        exact = exact and estimate.exact and coverage == "exact"
        basis_parts.append(f"{stage.axis}:{estimate.details.get('basis', 'unstated')}")
    basis = "; ".join(basis_parts) if basis_parts else "no generation axes requested"
    return PreflightReport(
        per_axis=FrozenDict(per_axis),
        total_declared=declared,
        total_upper_bound=upper,
        exact=exact,
        basis=basis,
    )


def check_limits(report: PreflightReport, limits: Mapping[str, Any]) -> None:
    """Enforce hard limits BEFORE any geometry (fail closed).

    ``max_declared_states`` rejects when the conservative upper bound
    exceeds it (equal to declared when exact). ``max_output_structures``
    rejects when the expected leaf output count (upper bound trimmed by an
    explicit sampling cap) exceeds it.
    """
    if not isinstance(limits, Mapping):
        raise ValueError("limits must be a mapping")
    max_declared = limits.get("max_declared_states")
    if max_declared is not None:
        _check_int(max_declared, path="limits.max_declared_states")
        if report.total_upper_bound > max_declared:
            basis = "exact" if report.exact else "conservative upper bound"
            raise PreflightLimitError(
                f"requested enumeration ({basis} {report.total_upper_bound}) exceeds "
                f"max_declared_states={max_declared}; basis: {report.basis}"
            )
    max_output = limits.get("max_output_structures")
    if max_output is not None:
        _check_int(max_output, path="limits.max_output_structures")
        expected = report.total_upper_bound
        cap = limits.get("_sampling_cap")
        if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1:
            expected = min(expected, cap)
        if expected > max_output:
            raise PreflightLimitError(
                f"expected leaf outputs ({expected}) exceed "
                f"max_output_structures={max_output}; basis: {report.basis}"
            )
