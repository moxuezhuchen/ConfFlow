#!/usr/bin/env python3
"""R2: ring rigid planar units + local orientation locks (topology-first).

Topology decides sp2; geometry only audits (angle sum) and measures
signed volumes for locks.  Never uses input planarity as an sp2 criterion.

Conventions (frozen by R2 card):
- Internal indices are global 0-based atom indices.
- ``pinned_bonds`` are canonical int pairs (min, max), sorted.
- ``V(c;p,n,s) = dot(p-c, cross(n-c, s-c))`` (ring four-point triple product,
  no division by 6).  This differs by a factor of 6 from the coordination
  tetrahedral ``signed_volume`` (/6); the sign agrees, magnitude thresholds
  are calibrated to this (ring) convention.
- Thresholds (frozen): ``CO_DOUBLE_MAX=1.35``, ``CN_DOUBLE_MAX=1.35``,
  ``CC_DOUBLE_MAX=1.45`` (angstrom); ``ABS_V_MIN=0.3`` (A^3);
  ``ANGLE_SUM_MIN=350.0`` (deg).
- Explicit ``bond_order`` always wins over distance.  Distance only assists
  when order is unknown AND element/degree hints unsaturation AND the
  candidate partner is unique.  Explicit single vs short distance is kept
  as single plus an ambiguity (never silently upgraded).
- Aromatic/fused edges require explicit bond_order 1.4-1.6 or aromatic type;
  distance alone never yields ``aromatic_fused``.
- No imports from R1 math, producer, or coordination.  stdlib + numpy
  plus confflow.domain.elements (single element authority) only.
- Pure read-only: inputs are copied/normalized, never mutated.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from confflow.domain.elements import canonical_element_symbol

__all__ = [
    "ABS_V_MIN",
    "ANGLE_SUM_MIN",
    "CC_DOUBLE_MAX",
    "CN_DOUBLE_MAX",
    "CO_DOUBLE_MAX",
    "Ambiguity",
    "DistortedInput",
    "LocalOrientationLock",
    "RigidUnit",
    "RigidUnitAnalysis",
    "analyze_rigid_units",
]

CO_DOUBLE_MAX = 1.35
CN_DOUBLE_MAX = 1.35
CC_DOUBLE_MAX = 1.45
ABS_V_MIN = 0.3
ANGLE_SUM_MIN = 350.0

_AROMATIC_ORDER_LO = 1.4
_AROMATIC_ORDER_HI = 1.6
_DOUBLE_ORDER_MIN = 1.9
_DOUBLE_ORDER_MAX = 2.1
_SINGLE_ORDER_MAX = 1.1
_TRIPLE_ORDER_MIN = 2.5

_VALID_KINDS = ("ester", "amide", "alkene", "imine", "aromatic_fused", "sp2")

# NOTE: element authority is confflow.domain.elements (single source).
# No local periodic table is kept here by intent (R2 root fix).


@dataclass(frozen=True)
class RigidUnit:
    ring_atoms: tuple[int, ...]
    pinned_bonds: tuple[tuple[int, int], ...]
    centers: tuple[int, ...]
    kind: str


@dataclass(frozen=True)
class LocalOrientationLock:
    center: int
    prev: int
    next: int
    subst: int
    sign: int
    magnitude: float


@dataclass(frozen=True)
class DistortedInput:
    atom: int
    observed: float
    expected: str


@dataclass(frozen=True)
class Ambiguity:
    atom: int
    reason: str
    detail: str


@dataclass(frozen=True)
class RigidUnitAnalysis:
    units: tuple[RigidUnit, ...]
    local_orientation_locks: tuple[LocalOrientationLock, ...]
    sp2_centers: tuple[int, ...]
    distorted_input: tuple[DistortedInput, ...]
    ambiguities: tuple[Ambiguity, ...]


def _norm_element(sym: Any, idx: int) -> str:
    # Single authority: confflow.domain.elements.  Non-strict inputs fail.
    if not isinstance(sym, str):
        raise ValueError(f"unknown element at index {idx}: {sym!r}")
    try:
        canon = canonical_element_symbol(sym)
    except Exception as exc:
        raise ValueError(f"unknown element at index {idx}: {sym!r}") from exc
    return canon


def _edge_kind_is_covalent(kind: Any) -> bool | None:
    """Classify an edge kind: True=covalent, False=non-covalent, None=absent."""
    if kind is None or (isinstance(kind, str) and not kind.strip()):
        return None
    try:
        from confflow.science.confgen.graph import EdgeType

        _ET: Any = EdgeType
    except Exception:
        _ET = None
    if _ET is not None and isinstance(kind, _ET):
        return kind is _ET.COVALENT
    if isinstance(kind, str):
        low = kind.strip().lower()
        if "aromat" in low:
            return True
        if low in ("covalent",):
            return True
        if low in ("coordination", "forming", "breaking"):
            return False
        up = kind.strip()
        if up in ("COVALENT",):
            return True
        if up in ("COORDINATION", "FORMING", "BREAKING"):
            return False
        raise ValueError(f"unknown edge kind {kind!r}")
    # Non-str, non-EdgeType kind (e.g. enum-like): compare by name/value.
    name = getattr(kind, "value", getattr(kind, "name", None))
    if isinstance(name, str):
        return _edge_kind_is_covalent(name)
    raise ValueError(f"unknown edge kind {kind!r}")


def _parse_order_map(typed_edges: Any, n: int) -> tuple[
    dict[tuple[int, int], float | None],
    set[tuple[int, int]],
    set[tuple[int, int]],
]:
    """Parse typed_edges into (order_map, aromatic_set, invalid_set).

    Only COVALENT edges enter the maps; COORDINATION/FORMING/BREAKING are
    authority context and are ignored for covalent bond-order purposes.
    Duplicate pairs with conflicting orders/aromatic flags fail closed.
    Out-of-range/non-finite orders are recorded in invalid_set and never
    fall back to distance (callers mark ambiguity and skip distance).
    """
    order_map: dict[tuple[int, int], float | None] = {}
    aromatic: set[tuple[int, int]] = set()
    invalid: set[tuple[int, int]] = set()
    if typed_edges is None:
        return order_map, aromatic, invalid
    try:
        items = list(typed_edges)
    except TypeError as exc:
        raise ValueError(f"typed_edges not iterable: {exc}") from exc
    seen: dict[tuple[int, int], tuple[float | None, bool]] = {}
    for item in items:
        a: Any = None
        b: Any = None
        order: Any = None
        is_aromatic = False
        kind_val: Any = None
        has_kind = False
        if isinstance(item, Mapping):
            a = item.get("a", item.get("u", item.get("i")))
            b = item.get("b", item.get("v", item.get("j")))
            order = item.get("bond_order", item.get("order"))
            for kk in ("type", "edge_type", "kind"):
                if kk in item:
                    kind_val = item.get(kk)
                    has_kind = True
                    break
            if isinstance(kind_val, str) and "aromat" in kind_val.lower():
                is_aromatic = True
        elif isinstance(item, (list, tuple)):
            if len(item) == 2:
                a, b = item
                order = None
            elif len(item) == 3:
                a, b, order = item
            else:
                raise ValueError(f"bad typed_edge entry: {item!r}")
        elif hasattr(item, "a") and hasattr(item, "b"):
            a = item.a
            b = item.b
            order = getattr(item, "bond_order", getattr(item, "order", None))
            for attr in ("type", "edge_type", "kind"):
                if hasattr(item, attr):
                    kind_val = getattr(item, attr)
                    has_kind = True
                    if isinstance(kind_val, str) and "aromat" in kind_val.lower():
                        is_aromatic = True
                    break
        elif hasattr(item, "u") and hasattr(item, "v"):
            a = item.u
            b = item.v
            order = getattr(item, "bond_order", getattr(item, "order", None))
            for attr in ("type", "edge_type", "kind"):
                if hasattr(item, attr):
                    kind_val = getattr(item, attr)
                    has_kind = True
                    break
        else:
            raise ValueError(f"bad typed_edge entry: {item!r}")
        if (
            isinstance(a, bool)
            or isinstance(b, bool)
            or not isinstance(a, (int, np.integer))
            or not isinstance(b, (int, np.integer))
        ):
            raise ValueError(f"bad typed_edge indices: {item!r}")
        ai, bi = int(a), int(b)
        if ai < 0 or ai >= n or bi < 0 or bi >= n or ai == bi:
            raise ValueError(f"typed_edge index out of range: {(ai, bi)}")
        key = (ai, bi) if ai < bi else (bi, ai)
        # Typed-type gate: non-covalent edges never enter covalent maps.
        if has_kind:
            cov = _edge_kind_is_covalent(kind_val)
            if cov is False:
                continue
        if order is None:
            val: float | None = None
            bad = False
        else:
            if isinstance(order, bool):
                raise ValueError(f"bad bond_order {order!r} for edge {key}")
            try:
                f = float(order)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"bad bond_order {order!r} for edge {key}") from exc
            if not (math.isfinite(f) and 0.0 < f <= 4.0):
                val = None
                bad = True
            else:
                val = f
                bad = False
        arom_here = bool(
            is_aromatic or (val is not None and _AROMATIC_ORDER_LO <= val <= _AROMATIC_ORDER_HI)
        )
        if key in seen:
            prev_val, prev_arom = seen[key]
            same_val = (prev_val == val) or (prev_val is None and val is None)
            if not same_val or prev_arom != arom_here:
                raise ValueError(f"conflicting typed_edge entries for pair {key}")
            if bad:
                invalid.add(key)
            continue
        seen[key] = (val, arom_here)
        if bad:
            invalid.add(key)
            continue
        order_map[key] = val
        if arom_here:
            aromatic.add(key)
    return order_map, aromatic, invalid


def _get_order(order_map: dict[tuple[int, int], float | None], a: int, b: int) -> float | None:
    key = (a, b) if a < b else (b, a)
    return order_map.get(key)


def _is_explicit_double(order: float | None) -> bool:
    # Triple (>=2.5, e.g. 3.0) is never a double (R2 root fix).
    return order is not None and _DOUBLE_ORDER_MIN <= order <= _DOUBLE_ORDER_MAX


def _is_explicit_single(order: float | None) -> bool:
    return order is not None and order <= _SINGLE_ORDER_MAX


def _is_explicit_triple(order: float | None) -> bool:
    return order is not None and order >= _TRIPLE_ORDER_MIN


def _is_explicit_other(order: float | None) -> bool:
    # Any explicit order that is not single/double/aromatic (e.g. 1.2,
    # 1.7-1.8, 2.3, triple) must not silently fall back to distance.
    if order is None:
        return False
    if _is_explicit_double(order) or _is_explicit_single(order):
        return False
    if _AROMATIC_ORDER_LO <= order <= _AROMATIC_ORDER_HI:
        return False
    return True


def _bond_distance(coords: np.ndarray, a: int, b: int) -> float:
    return float(np.linalg.norm(coords[a] - coords[b]))


def _angle_sum_deg(coords: np.ndarray, center: int, neighbors: list[int]) -> float:
    total = 0.0
    m = len(neighbors)
    for i in range(m):
        for j in range(i + 1, m):
            v1 = coords[neighbors[i]] - coords[center]
            v2 = coords[neighbors[j]] - coords[center]
            n1 = float(np.linalg.norm(v1))
            n2 = float(np.linalg.norm(v2))
            if not (math.isfinite(n1) and math.isfinite(n2)) or n1 == 0.0 or n2 == 0.0:
                raise ValueError(f"degenerate geometry at center {center}")
            c = float(np.dot(v1, v2) / (n1 * n2))
            c = max(-1.0, min(1.0, c))
            total += math.degrees(math.acos(c))
    return total


def _signed_triple(coords: np.ndarray, c: int, p: int, n: int, s: int) -> float:
    return float(
        np.dot(coords[p] - coords[c], np.cross(coords[n] - coords[c], coords[s] - coords[c]))
    )


def analyze_rigid_units(
    coords: Any,
    elements: Any,
    adjacency: Any,
    ring_atoms: Any,
    *,
    typed_edges: Any = None,
) -> RigidUnitAnalysis:
    # ---- validate coords ----
    try:
        xyz = np.array(coords, dtype=float, copy=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bad coords: {exc}") from exc
    if xyz.ndim != 2 or xyz.shape[1] != 3:
        raise ValueError(f"coords must be (n,3), got shape {xyz.shape}")
    n = int(xyz.shape[0])
    if n == 0:
        raise ValueError("coords empty")
    if not np.all(np.isfinite(xyz)):
        raise ValueError("non-finite coordinates")

    # ---- validate elements ----
    try:
        el_list = list(elements)
    except TypeError as exc:
        raise ValueError(f"bad elements: {exc}") from exc
    if len(el_list) != n:
        raise ValueError(f"elements length {len(el_list)} != n {n}")
    els: list[str] = [_norm_element(sym, i) for i, sym in enumerate(el_list)]

    # ---- validate adjacency (Mapping or real-context tuple/list) ----
    neigh: dict[int, set[int]] = {}
    for k in range(n):
        neigh[k] = set()
    if isinstance(adjacency, Mapping):
        _adj_items: list[tuple[Any, Any]] = list(adjacency.items())
        _is_seq_form = False
    elif isinstance(adjacency, (list, tuple)):
        # Real MolecularContext.adjacency is tuple[tuple[int,...],...].
        if len(adjacency) != n:
            raise ValueError(f"adjacency row count {len(adjacency)} != n {n}")
        _adj_items = list(enumerate(adjacency))
        _is_seq_form = True
    else:
        raise ValueError("adjacency must be a mapping int -> iterable[int] or a sequence of rows")
    for k, v in _adj_items:
        if isinstance(k, bool) or not isinstance(k, (int, np.integer)):
            raise ValueError(f"bad adjacency key: {k!r}")
        ki = int(k)
        if ki < 0 or ki >= n:
            raise ValueError(f"adjacency key out of range: {ki}")
        if _is_seq_form and ki != int(k):
            raise ValueError(f"bad adjacency key: {k!r}")
        try:
            vs = list(v)
        except TypeError as exc:
            raise ValueError(f"bad adjacency[{ki}]: {exc}") from exc
        for u in vs:
            if isinstance(u, bool) or not isinstance(u, (int, np.integer)):
                raise ValueError(f"bad adjacency neighbor {u!r} for {ki}")
            ui = int(u)
            if ui < 0 or ui >= n:
                raise ValueError(f"adjacency neighbor out of range: {ui} for {ki}")
            if ui == ki:
                # self-loop: keep for ambiguity detection, but record edge
                neigh[ki].add(ui)
            else:
                neigh[ki].add(ui)
    if not _is_seq_form:
        # Mapping form may omit isolated atoms (treated as empty); but
        # extra range checks already done. Missing keys stay empty.
        pass
    # ---- validate ring_atoms (strict: real ints only, length 4/5/6) ----
    try:
        _ring_raw = list(ring_atoms)
    except TypeError as exc:
        raise ValueError(f"bad ring_atoms: {exc}") from exc
    ring_list: list[int] = []
    for x in _ring_raw:
        if isinstance(x, bool) or not isinstance(x, (int, np.integer)):
            raise ValueError(f"bad ring_atoms entry: {x!r}")
        ring_list.append(int(x))
    if len(ring_list) not in (4, 5, 6):
        raise ValueError(f"ring_atoms length must be 4/5/6, got {len(ring_list)}")
    for x in ring_list:
        if x < 0 or x >= n:
            raise ValueError(f"ring_atoms index out of range: {x}")
    if len(set(ring_list)) != len(ring_list):
        raise ValueError("ring_atoms has duplicates")
    ring_set = set(ring_list)
    nr = len(ring_list)
    pos_of = {a: i for i, a in enumerate(ring_list)}

    order_map, aromatic_edges, invalid_edges = _parse_order_map(typed_edges, n)

    def _edge_is_invalid(a: int, b: int) -> bool:
        key = (a, b) if a < b else (b, a)
        return key in invalid_edges

    def _edge_is_other(a: int, b: int) -> bool:
        return _is_explicit_other(_get_order(order_map, a, b))

    def heavy_count(c: int) -> int:
        return sum(1 for u in neigh[c] if u != c and els[u] != "H")

    ambiguities: list[Ambiguity] = []
    sp2: set[int] = set()
    # Track why sp2 (for unit building): partner info per center.
    # carbonyl O partner (exocyclic) per ring C
    carbonyl_o: dict[int, int] = {}
    # For generic exocyclic double (C=N exo etc.)
    exo_double_partner: dict[int, int] = {}
    explicit_double_partner: dict[int, list[int]] = {}

    def add_ambiguity(atom: int, reason: str, detail: str) -> None:
        ambiguities.append(Ambiguity(atom=atom, reason=reason, detail=detail))

    # ---- pass 1: explicit-double / explicit-aromatic sp2 ----
    for c in ring_list:
        e = els[c]
        if e not in ("C", "N"):
            continue
        nbrs = sorted(u for u in neigh[c] if u != c)
        # self-loop => unclear
        if c in neigh[c]:
            add_ambiguity(c, "topology_unclear", "self-loop in adjacency")
            continue
        # Invalid / triple / unsupported explicit orders never fall back
        # to distance: mark ambiguity and block sp2 for this center.
        _bad_inv = [u for u in nbrs if _edge_is_invalid(c, u)]
        if _bad_inv:
            add_ambiguity(c, "invalid_bond_order", f"invalid bond_order at {_bad_inv}")
            continue
        _bad_trip = [u for u in nbrs if _is_explicit_triple(_get_order(order_map, c, u))]
        if _bad_trip:
            add_ambiguity(
                c, "explicit_triple_unsupported", f"triple bond at {_bad_trip} is not sp2 double"
            )
            continue
        _bad_other = [u for u in nbrs if _edge_is_other(c, u)]
        if _bad_other:
            add_ambiguity(
                c, "unsupported_bond_order", f"unsupported explicit order at {_bad_other}"
            )
            continue
        has_double = False
        dbl_partners: list[int] = []
        has_arom_ring = False
        for u in nbrs:
            o = _get_order(order_map, c, u)
            if _is_explicit_double(o):
                has_double = True
                dbl_partners.append(u)
            if (min(c, u), max(c, u)) in aromatic_edges and u in ring_set:
                has_arom_ring = True
        if has_double:
            sp2.add(c)
            explicit_double_partner[c] = dbl_partners
            # record exocyclic carbonyl O if applicable
            for u in dbl_partners:
                if u not in ring_set and els[u] == "O" and e == "C":
                    carbonyl_o[c] = u
                elif u not in ring_set:
                    exo_double_partner.setdefault(c, u)
            continue
        if has_arom_ring:
            sp2.add(c)
            continue

    # ---- pass 2: distance-assisted sp2 (only where no explicit verdict) ----
    for c in ring_list:
        if c in sp2:
            continue
        e = els[c]
        if e not in ("C", "N"):
            continue
        if c in neigh[c]:
            continue  # already ambiguous
        nbrs = sorted(u for u in neigh[c] if u != c)
        # Explicit invalid/triple/other blocks any distance fallback.
        if any(_edge_is_invalid(c, u) for u in nbrs):
            if not any(a.atom == c for a in ambiguities):
                add_ambiguity(c, "invalid_bond_order", "invalid bond_order blocks distance")
            continue
        if any(_is_explicit_triple(_get_order(order_map, c, u)) for u in nbrs):
            if not any(a.atom == c for a in ambiguities):
                add_ambiguity(c, "explicit_triple_unsupported", "triple blocks distance")
            continue
        if any(_edge_is_other(c, u) for u in nbrs):
            if not any(a.atom == c for a in ambiguities):
                add_ambiguity(c, "unsupported_bond_order", "unsupported order blocks distance")
            continue
        if len(nbrs) < 2:
            add_ambiguity(c, "topology_unclear", f"only {len(nbrs)} neighbors")
            continue
        # asymmetry check: every claimed neighbor must link back
        asym = [u for u in nbrs if c not in neigh[u]]
        if asym:
            add_ambiguity(c, "topology_unclear", f"asymmetric adjacency with {asym}")
            continue
        tot = len(nbrs)
        hv = heavy_count(c)
        # unsaturation hint
        if e == "C":
            if hv > 3:
                continue  # saturated heavy degree: never sp2 by distance
            if tot != 3:
                if tot < 3:
                    add_ambiguity(c, "degree_unsupported", f"C with {tot} neighbors")
                # tot > 3 (e.g. CH2 with 4): saturated, silent non-sp2
                continue
        else:  # N
            if hv > 2:
                continue
            if tot == 2:
                # Two-coordinate ring N (imine / pyridine-like, no H):
                # distance-assisted sp2 if exactly one ring-C short bond.
                ring_c = [u for u in nbrs if u in ring_set and els[u] == "C"]
                if len(ring_c) != 2:
                    # Not a ring-ring N (e.g. missing neighbor): unclear.
                    add_ambiguity(c, "topology_unclear", f"N with {tot} neighbors")
                    continue
                shorts: list[int] = []
                for u in ring_c:
                    if _is_explicit_single(_get_order(order_map, c, u)):
                        shorts.append(u)  # will be treated as conflict below
                        continue
                    dd = _bond_distance(xyz, c, u)
                    if dd <= CN_DOUBLE_MAX:
                        shorts.append(u)
                # conflict check
                conflict2 = [u for u in shorts if _is_explicit_single(_get_order(order_map, c, u))]
                if conflict2:
                    add_ambiguity(
                        c,
                        "explicit_single_vs_short_distance",
                        f"explicit single conflicts with short distance at {conflict2}",
                    )
                    continue
                if len(shorts) == 1:
                    sp2.add(c)
                    exo_double_partner.setdefault(c, shorts[0])
                # zero shorts: silent non-sp2; two shorts: ambiguous
                # (two short CN in a 2-coordinate N cannot both be double).
                elif len(shorts) > 1:
                    add_ambiguity(
                        c,
                        "multiple_double_candidates",
                        f"multiple short-distance candidates {shorts}",
                    )
                continue
            if tot != 3:
                if tot < 3:
                    add_ambiguity(c, "degree_unsupported", f"N with {tot} neighbors")
                continue
        # collect short-distance candidates
        o_cands: list[int] = []
        n_exo_cands: list[int] = []
        cc_ring_cands: list[int] = []
        cn_ring_cands: list[int] = []
        for u in nbrs:
            o = _get_order(order_map, c, u)
            if _is_explicit_single(o):
                # explicit single on this edge forbids double via distance;
                # handled per-candidate below (conflict => ambiguity if short).
                pass
            d = _bond_distance(xyz, c, u)
            if not math.isfinite(d):
                raise ValueError(f"non-finite distance for ({c},{u})")
            eu = els[u]
            if e == "C":
                if u not in ring_set and eu == "O" and d <= CO_DOUBLE_MAX:
                    o_cands.append(u)
                elif u not in ring_set and eu == "N" and d <= CN_DOUBLE_MAX:
                    n_exo_cands.append(u)
                elif u in ring_set and eu == "C" and d <= CC_DOUBLE_MAX:
                    cc_ring_cands.append(u)
                elif u in ring_set and eu == "N" and d <= CN_DOUBLE_MAX:
                    cn_ring_cands.append(u)
            else:  # e == N
                if u in ring_set and eu == "C" and d <= CN_DOUBLE_MAX:
                    cn_ring_cands.append(u)
                elif u not in ring_set and eu == "C" and d <= CN_DOUBLE_MAX:
                    # exocyclic C=N (N side); marks N sp2
                    n_exo_cands.append(u)
        # conflict: explicit single + short distance on same edge.
        # Carbonyl (exocyclic O/N double) takes priority over ring
        # single-bond shorts: an amide C-N (~1.33 A) or ester C-O
        # single may itself fall below the double threshold, but it is
        # the conjugated single, not a second double.  Hence when a
        # unique exocyclic carbonyl candidate exists, ring shorts are
        # ignored for candidate counting.
        if e == "C" and len(o_cands) == 1:
            u0 = o_cands[0]
            if _is_explicit_single(_get_order(order_map, c, u0)):
                add_ambiguity(
                    c,
                    "explicit_single_vs_short_distance",
                    f"explicit single conflicts with short distance at {[u0]}",
                )
                continue
            sp2.add(c)
            carbonyl_o[c] = u0
            continue
        if e == "C" and len(o_cands) > 1:
            # check conflict first for a precise reason
            conflict_o = [u for u in o_cands if _is_explicit_single(_get_order(order_map, c, u))]
            if conflict_o:
                add_ambiguity(
                    c,
                    "explicit_single_vs_short_distance",
                    f"explicit single conflicts with short distance at {conflict_o}",
                )
                continue
            add_ambiguity(
                c,
                "multiple_double_candidates",
                f"multiple short-distance candidates {o_cands}",
            )
            continue
        if e == "C" and len(n_exo_cands) == 1 and not o_cands:
            u0 = n_exo_cands[0]
            if _is_explicit_single(_get_order(order_map, c, u0)):
                add_ambiguity(
                    c,
                    "explicit_single_vs_short_distance",
                    f"explicit single conflicts with short distance at {[u0]}",
                )
                continue
            sp2.add(c)
            exo_double_partner.setdefault(c, u0)
            continue
        conflict_edges: list[int] = []
        for u in o_cands + n_exo_cands + cc_ring_cands + cn_ring_cands:
            if _is_explicit_single(_get_order(order_map, c, u)):
                conflict_edges.append(u)
        if conflict_edges:
            add_ambiguity(
                c,
                "explicit_single_vs_short_distance",
                f"explicit single conflicts with short distance at {conflict_edges}",
            )
            continue
        # total unique candidates
        all_cands = o_cands + n_exo_cands + cc_ring_cands + cn_ring_cands
        if len(all_cands) == 0:
            continue  # silent non-sp2
        if len(all_cands) > 1:
            # More than one short candidate: ambiguous (do not guess).
            # Exception: ester C has exactly one exo O; ring C/N shorts
            # would be additional candidates -> ambiguous is correct.
            add_ambiguity(
                c,
                "multiple_double_candidates",
                f"multiple short-distance candidates {all_cands}",
            )
            continue
        # exactly one -> sp2
        sp2.add(c)
        u0 = all_cands[0]
        if u0 in o_cands:
            carbonyl_o[c] = u0
        elif u0 in n_exo_cands or u0 in cc_ring_cands or u0 in cn_ring_cands:
            exo_double_partner.setdefault(c, u0)

    # ---- pass 3: build units ----
    units: list[RigidUnit] = []
    # 3a. ring-ring double / aromatic bonds from cycle edges + any ring-ring adjacency
    # Cycle edges (ordered ring) are the authoritative ring bonds.
    cycle_bonds: list[tuple[int, int]] = []
    for i in range(nr):
        ra = ring_list[i]
        rb = ring_list[(i + 1) % nr]
        cycle_bonds.append((ra, rb) if ra < rb else (rb, ra))
    # aromatic collection
    arom_bonds = [b for b in cycle_bonds if b in aromatic_edges]
    # Also catch explicit aromatic ring-ring edges not on the cycle order
    # (e.g. fused systems where adjacency has chord)? Only cycle bonds pin.
    if arom_bonds:
        centers = sorted({aa for bb in arom_bonds for aa in bb})
        for arc in centers:
            sp2.add(arc)
        units.append(
            RigidUnit(
                ring_atoms=tuple(sorted({aa for bb in arom_bonds for aa in bb})),
                pinned_bonds=tuple(sorted(arom_bonds)),
                centers=tuple(centers),
                kind="aromatic_fused",
            )
        )
    # ring-ring double bonds (non-aromatic)
    pinned_ring_bonds: set[tuple[int, int]] = set()
    for bond in arom_bonds:
        pinned_ring_bonds.add(bond)
    for bond in cycle_bonds:
        if bond in aromatic_edges:
            continue
        a1, a2 = bond
        # both must be in ring (by construction) ; check adjacency exists
        if a2 not in neigh[a1] or a1 not in neigh[a2]:
            continue  # missing edge handled in lock pass as unclear
        oa = _get_order(order_map, a1, a2)
        e1, e2 = els[a1], els[a2]
        is_cc = e1 == "C" and e2 == "C"
        is_cn = (e1 == "C" and e2 == "N") or (e1 == "N" and e2 == "C")
        if _is_explicit_double(oa):
            if is_cc:
                units.append(
                    RigidUnit(
                        ring_atoms=tuple(sorted(bond)),
                        pinned_bonds=(bond,),
                        centers=tuple(sorted(bond)),
                        kind="alkene",
                    )
                )
                sp2.add(a1)
                sp2.add(a2)
                pinned_ring_bonds.add(bond)
            elif is_cn:
                units.append(
                    RigidUnit(
                        ring_atoms=tuple(sorted(bond)),
                        pinned_bonds=(bond,),
                        centers=tuple(sorted(bond)),
                        kind="imine",
                    )
                )
                sp2.add(a1)
                sp2.add(a2)
                pinned_ring_bonds.add(bond)
            else:
                # explicit double between other elements: mark sp2 generically
                if e1 in ("C", "N"):
                    sp2.add(a1)
                if e2 in ("C", "N"):
                    sp2.add(a2)
            continue
        if _is_explicit_single(oa):
            continue
        # unknown order: distance-assisted only if both ends already sp2
        # via a ring-ring candidate (avoids lone short distance creating
        # a bond unit when only one end is unsaturated).
        if is_cc:
            d = _bond_distance(xyz, a1, a2)
            if d <= CC_DOUBLE_MAX and a1 in sp2 and a2 in sp2:
                # both ends independently chose this bond as their unique
                # candidate (exo_double_partner points at each other)
                if exo_double_partner.get(a1) == a2 and exo_double_partner.get(a2) == a1:
                    units.append(
                        RigidUnit(
                            ring_atoms=tuple(sorted(bond)),
                            pinned_bonds=(bond,),
                            centers=tuple(sorted(bond)),
                            kind="alkene",
                        )
                    )
                    pinned_ring_bonds.add(bond)
        elif is_cn:
            d = _bond_distance(xyz, a1, a2)
            if d <= CN_DOUBLE_MAX and a1 in sp2 and a2 in sp2:
                if exo_double_partner.get(a1) == a2 and exo_double_partner.get(a2) == a1:
                    units.append(
                        RigidUnit(
                            ring_atoms=tuple(sorted(bond)),
                            pinned_bonds=(bond,),
                            centers=tuple(sorted(bond)),
                            kind="imine",
                        )
                    )
                    pinned_ring_bonds.add(bond)

    # 3b. ester / amide / generic sp2 per center
    for c in sorted(sp2):
        # skip centers already covered as alkene/imine/aromatic bond centers?
        # Ester/amide carbonyls are not part of ring-ring double units, so
        # they still need units.  Alkene/imine centers already have a bond
        # unit; do not add a second generic unit for them.
        covered_by_bond_unit = False
        for u in units:
            if u.kind in ("alkene", "imine", "aromatic_fused") and c in u.centers:
                covered_by_bond_unit = True
                break
        if covered_by_bond_unit:
            continue
        e = els[c]
        if e not in ("C", "N"):
            continue
        nbrs = [u for u in neigh[c] if u != c]
        ring_o = [u for u in nbrs if u in ring_set and els[u] == "O"]
        ring_n = [u for u in nbrs if u in ring_set and els[u] == "N"]
        has_carbonyl = c in carbonyl_o
        if e == "C" and has_carbonyl and len(ring_o) == 1:
            o_ring = ring_o[0]
            # the pinned ester bond must exist topologically
            if o_ring in neigh[c] and c in neigh[o_ring]:
                key = (c, o_ring) if c < o_ring else (o_ring, c)
                # explicit single on the pinned C-O does not forbid pinning
                # (it is a single bond with partial-double character).
                units.append(
                    RigidUnit(
                        ring_atoms=tuple(sorted((c, o_ring))),
                        pinned_bonds=(key,),
                        centers=(c,),
                        kind="ester",
                    )
                )
                pinned_ring_bonds.add(key)
            else:
                add_ambiguity(c, "topology_unclear", "ester O_ring not connected")
                units.append(RigidUnit(ring_atoms=(c,), pinned_bonds=(), centers=(c,), kind="sp2"))
            continue
        if e == "C" and has_carbonyl and len(ring_n) == 1 and len(ring_o) == 0:
            n_ring = ring_n[0]
            if n_ring in neigh[c] and c in neigh[n_ring]:
                key = (c, n_ring) if c < n_ring else (n_ring, c)
                units.append(
                    RigidUnit(
                        ring_atoms=tuple(sorted((c, n_ring))),
                        pinned_bonds=(key,),
                        centers=(c,),
                        kind="amide",
                    )
                )
                pinned_ring_bonds.add(key)
            else:
                add_ambiguity(c, "topology_unclear", "amide N_ring not connected")
                units.append(RigidUnit(ring_atoms=(c,), pinned_bonds=(), centers=(c,), kind="sp2"))
            continue
        if e == "C" and has_carbonyl and (len(ring_o) != 1):
            # carbonyl but ring hetero context unclear -> ambiguity + generic mark
            if len(ring_o) > 1:
                add_ambiguity(c, "multiple_double_candidates", "multiple ring O neighbors")
            else:
                # no ring O/N (e.g. ketone): generic sp2, no pin
                pass
            # fall through to generic sp2 (no pin) unless already ambiguous-locked?
            # Generic unit records the rigid center without pinning.
            already = any(c in u.centers for u in units)
            if not already:
                units.append(RigidUnit(ring_atoms=(c,), pinned_bonds=(), centers=(c,), kind="sp2"))
            continue
        # amide N-side or other N sp2 without carbonyl C role: generic
        already = any(c in u.centers for u in units)
        if not already:
            units.append(RigidUnit(ring_atoms=(c,), pinned_bonds=(), centers=(c,), kind="sp2"))

    # ---- pass 4: angle-sum audit (distorted_input only) ----
    distorted: list[DistortedInput] = []
    for c in sorted(sp2):
        nbrs = sorted(u for u in neigh[c] if u != c)
        if len(nbrs) == 2 and els[c] == "N":
            # Two-coordinate sp2 N (imine / pyridine-like): angle-sum
            # audit needs 3 neighbors, so it is N/A.  Skip silently
            # (no distorted, no new ambiguity).
            continue
        if len(nbrs) != 3:
            # cannot audit; if not already ambiguous, record it (no distorted)
            if not any(a.atom == c for a in ambiguities):
                add_ambiguity(
                    c, "degree_unsupported", f"angle-sum needs 3 neighbors, got {len(nbrs)}"
                )
            continue
        try:
            asum = _angle_sum_deg(xyz, c, nbrs)
        except ValueError:
            raise
        if not math.isfinite(asum):
            raise ValueError(f"non-finite angle sum at {c}")
        if asum < ANGLE_SUM_MIN:
            distorted.append(DistortedInput(atom=c, observed=float(asum), expected=">=350"))

    # ---- pass 5: local orientation locks ----
    locks: list[LocalOrientationLock] = []
    amb_atoms = {a.atom for a in ambiguities}
    for c in ring_list:
        if c in sp2:
            continue
        if c in amb_atoms:
            continue
        if c in neigh[c]:
            continue
        nbr_set = set(u for u in neigh[c] if u != c)
        # prev/next from ring order
        idx = pos_of[c]
        p = ring_list[(idx - 1) % nr]
        nn = ring_list[(idx + 1) % nr]
        if p not in nbr_set or nn not in nbr_set:
            continue  # ring connectivity unclear -> no lock, no new ambiguity
            # (already covered if asymmetric; silent here to avoid noise)
        if c not in neigh[p] or c not in neigh[nn]:
            continue
        # external substituents
        for sub in sorted(nbr_set - ring_set):
            if c not in neigh[sub]:
                add_ambiguity(sub, "topology_unclear", f"asymmetric edge ({c},{sub})")
                continue
            try:
                v = _signed_triple(xyz, c, p, nn, sub)
            except IndexError as exc:
                raise ValueError(f"bad index in lock geometry: {exc}") from exc
            if not math.isfinite(v):
                raise ValueError("non-finite signed volume")
            if abs(v) <= ABS_V_MIN:
                continue
            sign = 1 if v > 0 else -1
            locks.append(
                LocalOrientationLock(
                    center=c,
                    prev=p,
                    next=nn,
                    subst=sub,
                    sign=sign,
                    magnitude=float(abs(v)),
                )
            )

    # ---- canonical ordering ----
    def _unit_key(u: RigidUnit) -> tuple:
        return (u.kind, u.centers, u.pinned_bonds, u.ring_atoms)

    units_sorted = tuple(sorted(units, key=_unit_key))
    locks_sorted = tuple(sorted(locks, key=lambda L: (L.center, L.subst, L.prev, L.next)))
    distorted_sorted = tuple(sorted(distorted, key=lambda d: d.atom))
    amb_sorted = tuple(sorted(ambiguities, key=lambda a: (a.atom, a.reason, a.detail)))

    # validate kinds
    for u in units_sorted:
        if u.kind not in _VALID_KINDS:
            raise ValueError(f"bad kind {u.kind!r}")

    return RigidUnitAnalysis(
        units=units_sorted,
        local_orientation_locks=locks_sorted,
        sp2_centers=tuple(sorted(sp2)),
        distorted_input=distorted_sorted,
        ambiguities=amb_sorted,
    )
