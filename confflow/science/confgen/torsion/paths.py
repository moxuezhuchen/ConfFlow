#!/usr/bin/env python3

"""Phase 0 input simplification: path-based rotor declarations (CORE lane).

A *path* declares two endpoint atoms (user-facing 1-based numbers) plus the
explicitly chosen moving endpoint (``move`` is REQUIRED and exactly
``start``/``end``)::

    paths: [{start: 81, end: 92, move: end}]

Every consecutive bond along the resolved atom path becomes a rotor. The
resolver is pure: it takes the final working topology (adjacency *after*
existing add/del_bond corrections) and returns ordered rotors with oriented
axes, moving/fixed atom sets, angle grids, and source provenance. No geometry
is generated here and no scientific intent is ever inferred:

- branch atoms move rigidly inside their selected component, but their
  internal bonds never become extra DOFs;
- ring atoms may be endpoints, but no cycle edge may occur in a path;
- unrelated disconnected components stay unmoved;
- the original output atom sequence is always preserved.

Resolution order for one endpoint pair on the undirected working graph:

1. classify connectivity on the FULL graph first (disconnected ->
   ``PATH_DISCONNECTED``);
2. remove cycle edges (keep graph bridges) and test connectivity there:
   connected -> the unique legal path (a forest holds at most one simple
   path between two nodes, so no ambiguity can arise);
3. full-graph connected but bridge-only disconnected ->
   ``PATH_CROSSES_RING``.

``PATH_AMBIGUOUS`` is reserved for future graph semantics and is never
emitted by this resolver: every path is resolved inside the bridge-only
forest, where two nodes admit at most one simple path, so no valid ambiguity
fixture exists. ``waypoint`` (multi-endpoint paths) is deferred; only
``start``/``end`` pairs are supported.

Cut rule: each rotor bond is cut and the component containing the explicitly
chosen endpoint moves. Axis atoms themselves remain unmoved. Canonical
unordered bond keys deduplicate path rotors and mixed path/chain
declarations; canonical bond identity is distinct from the oriented rotation
axis (traversal order matters for the signed-angle meaning).

Ordinal policy (deterministic): rotors are ordered by first declaration --
paths in listed order (bonds start->end traversal order), then chains in
listed order (chain order). Output ordinals are row-major over the ordered
rotors with the last rotor fastest (matching ``itertools.product``,
``MixedRadixGrid``, and the legacy grid ordinals).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from confflow.domain.canonical import canonical_sha256

__all__ = [
    "PATH_AMBIGUOUS",
    "PATH_CROSSES_RING",
    "PATH_DIRECTION_CONFLICT",
    "PATH_DISCONNECTED",
    "PATH_INVALID_ENDPOINT",
    "PATH_SHORT_BOND",
    "ROTOR_SAMPLING_CONFLICT",
    "WARNING_SHORT_BOND",
    "PATH_SHORT_BOND_RATIO",
    "CanonicalRotor",
    "ParsedPath",
    "PathDeclarationError",
    "PathResolution",
    "PathResolutionError",
    "ResolvedPath",
    "canonical_grid_size",
    "canonicalize_rotors",
    "parse_path_declarations",
    "resolve_paths",
    "topology_digest_of",
]

#: Reserved code: kept for future graph semantics, never emitted (see module
#: docstring for why no valid ambiguity fixture exists for bridge-only paths).
PATH_AMBIGUOUS = "PATH_AMBIGUOUS"

#: Endpoints are valid atoms but no bridge-only route joins them: every
#: full-graph route crosses a ring bond.
PATH_CROSSES_RING = "PATH_CROSSES_RING"

#: The same canonical bond is declared with opposite moving components.
PATH_DIRECTION_CONFLICT = "PATH_DIRECTION_CONFLICT"

#: Endpoints lie in different disconnected components of the working graph.
PATH_DISCONNECTED = "PATH_DISCONNECTED"

#: An endpoint reference is malformed, out of range, or identical to its pair.
PATH_INVALID_ENDPOINT = "PATH_INVALID_ENDPOINT"

#: A newly path-expanded bond is suspiciously short (strict mode error).
PATH_SHORT_BOND = "PATH_SHORT_BOND"

#: The same oriented rotor is declared with incompatible sampling (different
#: angle grids, reversed traversal with asymmetric meaning, or
#: absolute/relative/chemical model mismatch). Grids are never unioned.
ROTOR_SAMPLING_CONFLICT = "ROTOR_SAMPLING_CONFLICT"

#: Element-aware short-bond heuristic: a newly path-expanded bond whose
#: measured length falls below this fraction of the summed covalent radii
#: is flagged ``WARNING_SHORT_BOND`` (warning only by default, error under
#: ``strict_path_bond_check``). Equilibrium covalent lengths sit near 1.0x
#: the radii sum, so 0.92x flags genuinely compressed bonds (e.g. 1.34 A C-C
#: or 1.33 A C-N against ~1.5 A radii sums) while 1.52 A C-C stays quiet.
#: This is a cheap distance heuristic, not chemistry: legitimately short
#: bonds (multiple/aromatic character, strained rings) can also trip it, so
#: the finding never skips, reorders, or reinterprets any bond -- there is
#: no bond-order inference anywhere in this module.
PATH_SHORT_BOND_RATIO = 0.92

#: Stable message prefix for short-bond findings (warnings and strict errors).
WARNING_SHORT_BOND = "WARNING_SHORT_BOND"

#: Declaration keys accepted on one path entry. Anything else fails closed.
_PATH_KEYS = frozenset({"start", "end", "move", "angles", "step", "id"})

#: Move vocabulary: the explicitly chosen moving endpoint. No default.
_MOVE_CHOICES = ("start", "end")


class PathDeclarationError(ValueError):
    """Structural path declaration defect (fail closed, before resolution)."""


class PathResolutionError(ValueError):
    """Graph/semantic path resolution failure with a stable machine code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path_index: int | None = None,
        bond: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.path_index = path_index
        self.bond = bond


@dataclass(frozen=True, slots=True)
class ParsedPath:
    """One structurally valid path declaration (internal 0-based indices)."""

    start: int
    end: int
    move: str
    angles: tuple[float, ...]
    source: str
    raw_start: int
    raw_end: int

    @property
    def move_atom(self) -> int:
        """Return the explicitly chosen moving endpoint (0-based)."""
        return self.end if self.move == "end" else self.start


@dataclass(frozen=True, slots=True)
class CanonicalRotor:
    """One canonicalized rotor: canonical bond plus oriented rotation axis.

    ``bond`` is the canonical unordered pair (min, max). ``ordered`` is the
    traversal orientation (first, second) whose signed-angle meaning the
    ``angles`` grid is expressed in. ``moving_atom`` names the canonical
    endpoint whose cut component moves. ``moving``/``fixed`` are sorted
    0-based tuples; axis atoms are always in ``fixed``. ``sources`` audits
    every declaration that produced this rotor.
    """

    bond: tuple[int, int]
    ordered: tuple[int, int]
    moving_atom: int
    moving: tuple[int, ...] = ()
    fixed: tuple[int, ...] = ()
    angles: tuple[float, ...] = ()
    sources: tuple[str, ...] = ()
    model: str = "relative_rotation_grid"


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """One declared path and its resolved ordered atom route.

    The single authority for per-source route audits: ``route`` is the
    0-based atom sequence from ``start`` to ``end`` along bridge-only bonds.
    Reporting layers must read routes here, never re-derive them.
    """

    source: str
    raw_start: int
    raw_end: int
    move: str
    route: tuple[int, ...]
    angles: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PathResolution:
    """Resolved rotors plus audit metadata (no geometry generated).

    ``raw_cartesian_size`` is the DECLARATION-space product (pre-dedup, one
    entry per path bond); the executed task count is the canonicalized size
    (see :func:`canonical_grid_size`), recorded separately by callers.
    """

    rotors: tuple[CanonicalRotor, ...] = ()
    declared_paths: tuple[ResolvedPath, ...] = ()
    warnings: tuple[str, ...] = ()
    topology_digest: str = ""
    raw_cartesian_size: int = 0


def _is_int(value: Any) -> bool:
    """Return True for real ints (bool is never an atom index)."""
    return type(value) is int


def _check_finite_angles(values: Any, *, path: str) -> tuple[float, ...]:
    """Validate an explicit angle grid (finite, non-empty, no wraparounds)."""
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise PathDeclarationError(f"{path} angles must be a list of finite numbers")
    angles: list[float] = []
    for position, item in enumerate(values):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise PathDeclarationError(
                f"{path} angles[{position}] must be a finite number, got {item!r}"
            )
        number = float(item)
        if not math.isfinite(number):
            raise PathDeclarationError(f"{path} angles[{position}] must be finite, got {item!r}")
        angles.append(number)
    if not angles:
        raise PathDeclarationError(f"{path} angles must hold at least one angle")
    for index, value in enumerate(angles):
        for previous in angles[:index]:
            if (value - previous) % 360.0 == 0.0:
                raise PathDeclarationError(
                    f"{path} angles holds a periodic duplicate: {value!r} names "
                    "the identical physical state (one state, one key)"
                )
    return tuple(angles)


def _expand_step(step: Any, *, path: str) -> tuple[float, ...]:
    """Expand an integer step into a ``range(0, 360, step)`` grid."""
    if not _is_int(step) or not 1 <= step <= 360:
        raise PathDeclarationError(f"{path} step must be an integer in 1..360, got {step!r}")
    return tuple(float(angle) for angle in range(0, 360, int(step)))


def parse_path_declarations(
    raw_paths: Any,
    *,
    index_base: int = 1,
    default_step: int | None = 120,
    source_prefix: str = "paths",
    internal: bool = False,
) -> tuple[ParsedPath, ...]:
    """Parse raw path declarations into validated 0-based records.

    Parameters
    ----------
    raw_paths :
        List of ``{start, end, move, angles?, step?}`` mappings with
        user-facing ``index_base``-based atom numbers.
    index_base :
        Declared numbering base of the atom references (0 or 1).
    default_step :
        Grid step used when a declaration carries neither ``angles`` nor
        ``step``. ``None`` fails closed (typed scopes must declare sampling
        explicitly; the bare ``{start, end, move}`` spelling only defaults
        in the legacy native mode).
    source_prefix :
        Provenance stem for source labels (``paths`` or ``native.paths``).
    internal :
        Re-normalization mode for entries already carrying the explicit
        ``internal-0-based:normalized`` convention: ``start``/``end`` are
        validated as internal 0-based references (no base shift, so parsing
        is idempotent) and an existing string ``source`` is preserved
        verbatim instead of re-derived. Fresh user input must never set
        ``source`` (unknown key, fail closed).

    Range checks against the atom count are NOT performed here (the count is
    unknown at parse time); out-of-range endpoints fail at resolution with
    ``PATH_INVALID_ENDPOINT``.
    """
    if index_base not in (0, 1):
        raise PathDeclarationError(f"index_base must be 0 or 1, got {index_base!r}")
    if internal and index_base != 0:
        raise PathDeclarationError("internal re-parsing requires index_base 0")
    allowed_keys = set(_PATH_KEYS) | ({"source"} if internal else set())
    if not isinstance(raw_paths, (list, tuple)):
        raise PathDeclarationError(f"{source_prefix} must be a list of path declarations")
    parsed: list[ParsedPath] = []
    for position, entry in enumerate(raw_paths):
        path = f"{source_prefix}[{position}]"
        if not isinstance(entry, Mapping):
            raise PathDeclarationError(f"{path} must be a mapping")
        unknown = sorted(set(entry) - allowed_keys)
        if unknown:
            raise PathDeclarationError(
                f"{path} holds unknown keys {unknown}; allowed {sorted(allowed_keys)}"
            )
        raw_start = entry.get("start")
        raw_end = entry.get("end")
        for name, value in (("start", raw_start), ("end", raw_end)):
            if not _is_int(value):
                raise PathDeclarationError(
                    f"{path}.{name} must be an integer atom number, got {value!r}"
                )
            if int(value) < index_base:
                raise PathDeclarationError(
                    f"{path}.{name} index {value!r} below declared index_base " f"{index_base}"
                )
        start = int(raw_start) - index_base
        end = int(raw_end) - index_base
        if start == end:
            raise PathResolutionError(
                PATH_INVALID_ENDPOINT,
                f"{path} start and end name the same atom {raw_start!r}",
                path_index=position,
            )
        move = entry.get("move")
        if move not in _MOVE_CHOICES:
            raise PathDeclarationError(
                f"{path}.move is REQUIRED and must be exactly 'start' or 'end', "
                f"got {move!r}; scientific intent is never inferred"
            )
        angles = entry.get("angles")
        step = entry.get("step")
        if angles is not None and step is not None:
            raise PathDeclarationError(
                f"{path} declares both 'angles' and 'step'; declare exactly one"
            )
        if angles is not None:
            grid = _check_finite_angles(angles, path=path)
        elif step is not None:
            grid = _expand_step(step, path=path)
        elif default_step is not None:
            grid = _expand_step(default_step, path=f"{path} (default step)")
        else:
            raise PathDeclarationError(
                f"{path} declares no sampling: give explicit 'angles' or 'step' "
                "(bare paths only default in the legacy native mode)"
            )
        label = entry.get("id")
        if label is not None and (not isinstance(label, str) or not label):
            raise PathDeclarationError(f"{path}.id must be a non-empty string")
        kept_source = entry.get("source")
        if internal:
            if kept_source is not None and (not isinstance(kept_source, str) or not kept_source):
                raise PathDeclarationError(f"{path}.source must be a non-empty string")
            source = kept_source or f"{source_prefix}[{position}]"
        else:
            source = f"{source_prefix}[{position}]"
        parsed.append(
            ParsedPath(
                start=start,
                end=end,
                move=str(move),
                angles=grid,
                source=str(source),
                raw_start=int(raw_start),
                raw_end=int(raw_end),
            )
        )
    return tuple(parsed)


def _bfs_path(adjacency: Sequence[Sequence[int]], start: int, end: int) -> list[int] | None:
    """Return the deterministic shortest atom path, or None when detached."""
    n_atoms = len(adjacency)
    prev: list[int | None] = [None] * n_atoms
    prev[start] = start
    queue = [start]
    head = 0
    while head < len(queue):
        current = queue[head]
        head += 1
        if current == end:
            break
        for neighbor in sorted(adjacency[current]):
            if prev[neighbor] is None and 0 <= neighbor < n_atoms:
                prev[neighbor] = current
                queue.append(neighbor)
    if prev[end] is None:
        return None
    route = [end]
    while route[-1] != start:
        parent = prev[route[-1]]
        assert parent is not None
        route.append(parent)
    route.reverse()
    return route


def _cut_component(
    adjacency: Sequence[Sequence[int]], keep: int, cut_a: int, cut_b: int
) -> set[int]:
    """Return the cut-bond component containing *keep* (0-based)."""
    seen = {keep}
    stack = [keep]
    n_atoms = len(adjacency)
    while stack:
        current = stack.pop()
        for neighbor in adjacency[current]:
            if (current == cut_a and neighbor == cut_b) or (current == cut_b and neighbor == cut_a):
                continue
            if neighbor < 0 or neighbor >= n_atoms or neighbor in seen:
                continue
            seen.add(neighbor)
            stack.append(neighbor)
    return seen


def topology_digest_of(
    adjacency: Sequence[Sequence[int]],
    *,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Return the deterministic digest of the working graph (bridges input).

    Covers the atom count plus the sorted canonical edge list (and optional
    correction metadata such as add/del_bond spellings and bond_scale), so
    resolution identity is auditable without touching scientific digests.
    """
    edges = sorted(
        (min(int(a), int(b)), max(int(a), int(b)))
        for a, row in enumerate(adjacency)
        for b in row
        if int(b) > int(a)
    )
    payload: dict[str, Any] = {"atoms": len(adjacency), "edges": [list(e) for e in edges]}
    if extra:
        payload["corrections"] = dict(extra)
    return "sha256:" + canonical_sha256(payload)


def _check_endpoint_range(value: int, n_atoms: int, *, path: str, position: int) -> None:
    """Fail with PATH_INVALID_ENDPOINT on out-of-range references."""
    if value < 0 or value >= n_atoms:
        raise PathResolutionError(
            PATH_INVALID_ENDPOINT,
            f"{path} endpoint {value + 1} out of range for {n_atoms} atoms "
            "(endpoints are user-facing 1-based atom numbers)",
            path_index=position,
        )


def _short_bond_warnings(
    bonds: Sequence[tuple[int, int]],
    coords: Sequence[Sequence[float]] | None,
    radii: Sequence[float] | None,
    n_atoms: int,
    *,
    strict: bool,
    path: str,
    position: int,
) -> tuple[str, ...]:
    """Assess newly path-expanded bonds against the radii criterion."""
    if coords is None or radii is None:
        return ()
    import numpy as np

    arr = np.asarray(coords, dtype=np.float64)
    if arr.shape != (n_atoms, 3) or not np.all(np.isfinite(arr)):
        raise PathResolutionError(
            PATH_INVALID_ENDPOINT,
            f"{path} coordinates are not finite {n_atoms}x3",
            path_index=position,
        )
    findings: list[str] = []
    for first, second in bonds:
        try:
            radius_sum = float(radii[first]) + float(radii[second])
        except (IndexError, TypeError, ValueError):
            findings.append(
                f"{path}: bond {first + 1}-{second + 1} has no usable covalent "
                "radii; short-bond check skipped (no inference made)"
            )
            continue
        if not math.isfinite(radius_sum) or radius_sum <= 0:
            findings.append(
                f"{path}: bond {first + 1}-{second + 1} has no usable covalent "
                "radii; short-bond check skipped (no inference made)"
            )
            continue
        delta = arr[first] - arr[second]
        dist = float(np.linalg.norm(delta))
        limit = PATH_SHORT_BOND_RATIO * radius_sum
        if dist < limit:
            note = (
                f"{WARNING_SHORT_BOND}: {path} bond {first + 1}-{second + 1} "
                f"measures {dist:.3f} A, below {PATH_SHORT_BOND_RATIO}x summed "
                f"covalent radii ({limit:.3f} A); kept as declared (warning "
                "only, no skipping or bond-order inference)"
            )
            if strict:
                raise PathResolutionError(
                    PATH_SHORT_BOND, note, path_index=position, bond=(first, second)
                )
            findings.append(note)
    return tuple(findings)


def resolve_paths(
    parsed: Sequence[ParsedPath],
    adjacency: Sequence[Sequence[int]],
    *,
    n_atoms: int | None = None,
    coords: Sequence[Sequence[float]] | None = None,
    radii: Sequence[float] | None = None,
    strict_bond_check: bool = False,
    topology_extra: Mapping[str, Any] | None = None,
) -> PathResolution:
    """Resolve parsed paths on the final working topology (pure, no geometry).

    Returns ordered :class:`CanonicalRotor` records with empty moving/fixed
    sets filled per the cut rule, plus warnings and the topology digest.
    Raises :class:`PathResolutionError` with a stable code
    (``PATH_INVALID_ENDPOINT`` / ``PATH_DISCONNECTED`` /
    ``PATH_CROSSES_RING`` / ``PATH_SHORT_BOND``) before any geometry call.
    """
    from confflow.science.torsion import edge_in_cycle

    count = len(adjacency) if n_atoms is None else int(n_atoms)
    if count != len(adjacency):
        raise PathResolutionError(
            PATH_INVALID_ENDPOINT,
            f"adjacency rows {len(adjacency)} disagree with atom count {count}",
        )
    digest = topology_digest_of(adjacency, extra=topology_extra)
    rotors: list[CanonicalRotor] = []
    declared: list[ResolvedPath] = []
    warnings: list[str] = []
    for position, decl in enumerate(parsed):
        path = decl.source
        _check_endpoint_range(decl.start, count, path=path, position=position)
        _check_endpoint_range(decl.end, count, path=path, position=position)
        if _bfs_path(adjacency, decl.start, decl.end) is None:
            raise PathResolutionError(
                PATH_DISCONNECTED,
                f"{path} endpoints {decl.raw_start}-{decl.raw_end} lie in "
                "different disconnected components of the working graph",
                path_index=position,
            )
        bridges: list[set[int]] = [set() for _ in range(count)]
        for first in range(count):
            for second in adjacency[first]:
                if second > first and not edge_in_cycle(adjacency, first, second):
                    bridges[first].add(second)
                    bridges[second].add(first)
        route = _bfs_path(bridges, decl.start, decl.end)
        if route is None:
            raise PathResolutionError(
                PATH_CROSSES_RING,
                f"{path} endpoints {decl.raw_start}-{decl.raw_end} are connected "
                "in the full graph but every route crosses a ring bond; ring "
                "bonds cannot rotate independently (ring atoms may still be "
                "endpoints of a bridge-only path)",
                path_index=position,
            )
        bonds = [(route[i], route[i + 1]) for i in range(len(route) - 1)]
        declared.append(
            ResolvedPath(
                source=decl.source,
                raw_start=decl.raw_start,
                raw_end=decl.raw_end,
                move=decl.move,
                route=tuple(route),
                angles=tuple(decl.angles),
            )
        )
        warnings.extend(
            _short_bond_warnings(
                bonds,
                coords,
                radii,
                count,
                strict=bool(strict_bond_check),
                path=path,
                position=position,
            )
        )
        move_atom = decl.move_atom
        for first, second in bonds:
            component = _cut_component(adjacency, move_atom, first, second)
            moving_atom = first if first in component else second
            moving = tuple(sorted(a for a in component if a not in (first, second)))
            fixed = tuple(sorted(a for a in range(count) if a not in set(moving)))
            rotors.append(
                CanonicalRotor(
                    bond=(min(first, second), max(first, second)),
                    ordered=(first, second),
                    moving_atom=moving_atom,
                    moving=moving,
                    fixed=fixed,
                    angles=tuple(decl.angles),
                    sources=(decl.source,),
                    model="relative_rotation_grid",
                )
            )
    size = 1
    for rotor in rotors:
        size *= len(rotor.angles)
    return PathResolution(
        rotors=tuple(rotors),
        declared_paths=tuple(declared),
        warnings=tuple(warnings),
        topology_digest=digest,
        raw_cartesian_size=size,
    )


@dataclass
class _RotorAccumulator:
    """Mutable canonicalization accumulator (one mixed declaration set)."""

    bond: tuple[int, int]
    ordered: tuple[int, int]
    moving_atom: int
    moving: tuple[int, ...]
    fixed: tuple[int, ...]
    angles: tuple[float, ...]
    sources: list[str] = field(default_factory=list)
    model: str = "relative_rotation_grid"
    excluded: bool = False


def canonicalize_rotors(
    rotors: Sequence[CanonicalRotor],
    *,
    excluded_bonds: Sequence[tuple[int, int]] = (),
) -> tuple[CanonicalRotor, ...]:
    """Canonicalize mixed path/chain rotors (deterministic, fail closed).

    First declaration wins position; identical duplicates (same canonical
    bond, traversal orientation, moving component, angle grid, and model)
    merge with all source provenance. Opposite moving components raise
    ``PATH_DIRECTION_CONFLICT``; anything else incompatible (angle grids,
    reversed traversal with asymmetric signed-angle meaning,
    absolute/relative/chemical model mismatch) raises
    ``ROTOR_SAMPLING_CONFLICT``. Grids are never unioned. Conflict checks
    run on the DECLARED grids first; only afterwards do bonds in
    *excluded_bonds* (canonical pairs, e.g. legacy ``no_rotate``) collapse
    to the single ``(0.0,)`` grid point with the exclusion recorded in
    sources. Callers must therefore pass declared (not pre-collapsed) angle
    grids so identical declarations under a shared exclusion still merge.
    """
    excluded = {(min(a, b), max(a, b)) for a, b in excluded_bonds}
    order: list[tuple[int, int]] = []
    table: dict[tuple[int, int], _RotorAccumulator] = {}
    for rotor in rotors:
        key = (min(rotor.bond[0], rotor.bond[1]), max(rotor.bond[0], rotor.bond[1]))
        if key not in table:
            order.append(key)
            table[key] = _RotorAccumulator(
                bond=key,
                ordered=rotor.ordered,
                moving_atom=rotor.moving_atom,
                moving=tuple(rotor.moving),
                fixed=tuple(rotor.fixed),
                angles=tuple(rotor.angles),
                sources=list(rotor.sources),
                model=rotor.model,
            )
            continue
        known = table[key]
        known.sources.extend(rotor.sources)
        if rotor.moving_atom != known.moving_atom or tuple(rotor.moving) != tuple(known.moving):
            raise PathResolutionError(
                PATH_DIRECTION_CONFLICT,
                f"bond {key[0] + 1}-{key[1] + 1} is declared with opposite "
                f"moving components ({' vs '.join(sorted(set(known.sources)))}); "
                "declare one moving side explicitly",
                bond=key,
            )
        if (
            rotor.ordered != known.ordered
            or tuple(rotor.angles) != tuple(known.angles)
            or rotor.model != known.model
        ):
            raise PathResolutionError(
                ROTOR_SAMPLING_CONFLICT,
                f"bond {key[0] + 1}-{key[1] + 1} carries incompatible sampling "
                f"({' vs '.join(sorted(set(known.sources)))}): oriented "
                "signed-angle meaning, angle grids, and absolute/relative "
                "models must match exactly; grids are never unioned",
                bond=key,
            )
    resolved: list[CanonicalRotor] = []
    for key in order:
        known = table[key]
        angles = known.angles
        sources = list(known.sources)
        if key in excluded:
            angles = (0.0,)
            sources.append("excluded:no_rotate")
        resolved.append(
            CanonicalRotor(
                bond=known.bond,
                ordered=known.ordered,
                moving_atom=known.moving_atom,
                moving=known.moving,
                fixed=known.fixed,
                angles=angles,
                sources=tuple(sources),
                model=known.model,
            )
        )
    return tuple(resolved)


def canonical_grid_size(rotors: Sequence[CanonicalRotor]) -> int:
    """Return the exact task-space size of canonicalized rotors.

    Arbitrary-precision product of per-rotor grid lengths AFTER dedup,
    conflict checks, and exclusions -- the actual number of geometric
    realizations attempted. Declaration-space products (pre-dedup) are
    recorded separately and never used as the execution count.
    """
    size = 1
    for rotor in rotors:
        size *= len(rotor.angles)
    return size
