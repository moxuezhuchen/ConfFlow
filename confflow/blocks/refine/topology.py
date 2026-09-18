#!/usr/bin/env python3

"""Element-labelled bonding graphs and budgeted exact graph mapping.

The graph layer is shared by topology classification and geometry comparison:

* bonds follow the existing rule ``d < bond_scale * (r_i + r_j)``;
* cheap invariants (element counts, degree sequence, colour refinement) are
  pre-filters only -- they can prove graphs differ, never that they match;
* exact matching enumerates complete bijections that preserve elements and all
  edges, under a deterministic node budget.  Exhausting the budget is reported
  as ``unresolved`` instead of being mistaken for a known result.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...core.bonding import build_adjacency
from ._compat import load_refine_data

_periodic_symbols, GV_COVALENT_RADII = load_refine_data()
PERIODIC_SYMBOLS: tuple[str, ...] = tuple(_periodic_symbols)

BOND_SCALE_FACTOR = 1.2
DEFAULT_MAPPING_NODE_BUDGET = 1_000
_WL_ROUNDS = 4

__all__ = [
    "BOND_SCALE_FACTOR",
    "DEFAULT_MAPPING_NODE_BUDGET",
    "Graph",
    "GraphBuild",
    "MappingBudgetExceeded",
    "MappingSearch",
    "MappingSearchResult",
    "MappingSearchStats",
    "TopologyCluster",
    "build_graph",
    "build_graph_from_atomic_numbers",
    "apply_bond_overrides",
    "parse_bond_override",
    "find_isomorphism",
    "fixed_index_isomorphism",
    "get_element_atomic_number",
    "graph_from_adjacency",
    "graphs_may_be_isomorphic",
    "group_frames_by_topology",
    "validate_mapping",
]


def get_element_atomic_number(symbol: str) -> int:
    """Get atomic number from element symbol (0 for unknown/empty)."""
    if not symbol:
        return 0
    s = str(symbol).capitalize()
    try:
        return PERIODIC_SYMBOLS.index(s)
    except ValueError:
        return 0


@dataclass(frozen=True)
class Graph:
    """Element-labelled undirected bonding graph over all atoms (including H)."""

    symbols: tuple[str, ...]
    atomic_numbers: tuple[int, ...]
    adjacency: tuple[tuple[int, ...], ...]
    fingerprint: bytes

    @property
    def n(self) -> int:
        return len(self.atomic_numbers)

    @property
    def edge_count(self) -> int:
        return sum(len(row) for row in self.adjacency) // 2

    @property
    def degree_sequence(self) -> tuple[int, ...]:
        return tuple(sorted(len(row) for row in self.adjacency))

    @property
    def element_counts(self) -> tuple[tuple[int, int], ...]:
        counts: dict[int, int] = {}
        for atomic_number in self.atomic_numbers:
            counts[atomic_number] = counts.get(atomic_number, 0) + 1
        return tuple(sorted(counts.items()))

    def has_edge(self, i: int, j: int) -> bool:
        return j in self.adjacency[i]


@dataclass(frozen=True)
class GraphBuild:
    """Result of building a graph from raw atoms/coordinates."""

    graph: Graph | None
    status: str
    reason: str = ""


def _wl_fingerprint(
    atomic_numbers: tuple[int, ...],
    adjacency: tuple[tuple[int, ...], ...],
) -> bytes:
    """Cross-graph comparable colour-refinement fingerprint (invariant).

    Equal fingerprints only mean "candidate"; different fingerprints prove the
    graphs are not isomorphic.  Because colour updates hash the previous colour
    plus the sorted neighbour colours, the final colour multiset is independent
    of any vertex numbering.
    """
    colors = [str(number) for number in atomic_numbers]
    rounds = min(_WL_ROUNDS, max(1, len(colors)))
    for _ in range(rounds):
        updated = []
        for i in range(len(colors)):
            neighbor_colors = sorted(colors[j] for j in adjacency[i])
            payload = f"{colors[i]}|{','.join(neighbor_colors)}"
            updated.append(hashlib.blake2b(payload.encode("ascii"), digest_size=8).hexdigest())
        colors = updated
    canonical = ";".join(sorted(colors))
    return hashlib.blake2b(canonical.encode("ascii"), digest_size=16).digest()


def _adjacency_from_coords(
    atomic_numbers: tuple[int, ...],
    coords: np.ndarray,
    bond_scale: float,
) -> tuple[tuple[int, ...], ...]:
    neighbors = build_adjacency(atomic_numbers, coords, bond_scale=bond_scale)
    return tuple(tuple(row) for row in neighbors)


def _graph_from_parts(
    symbols: Sequence[str],
    atomic_numbers: tuple[int, ...],
    adjacency: tuple[tuple[int, ...], ...],
) -> Graph:
    return Graph(
        symbols=tuple(str(symbol) for symbol in symbols),
        atomic_numbers=atomic_numbers,
        adjacency=adjacency,
        fingerprint=_wl_fingerprint(atomic_numbers, adjacency),
    )


def build_graph_from_atomic_numbers(
    atomic_numbers: Sequence[int],
    coords,
    *,
    bond_scale: float = BOND_SCALE_FACTOR,
) -> GraphBuild:
    """Build a graph from atomic numbers and coordinates."""
    numbers = tuple(int(z) for z in atomic_numbers)
    if not numbers:
        return GraphBuild(None, "empty", "no atoms")
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] != len(numbers) or arr.shape[1] != 3:
        return GraphBuild(None, "invalid_coordinates", "coordinate shape does not match atoms")
    if not np.all(np.isfinite(arr)):
        return GraphBuild(None, "non_finite_coordinates", "coordinates contain NaN or infinity")
    if any(z <= 0 for z in numbers):
        return GraphBuild(None, "unknown_element", "atom with unknown element")
    if any(z >= len(GV_COVALENT_RADII) or GV_COVALENT_RADII[z] <= 0.0 for z in numbers):
        return GraphBuild(None, "unknown_element", "atom without a covalent radius")

    symbols = [PERIODIC_SYMBOLS[z] if 0 <= z < len(PERIODIC_SYMBOLS) else "" for z in numbers]
    adjacency = _adjacency_from_coords(numbers, arr, bond_scale)
    return GraphBuild(
        _graph_from_parts(symbols, numbers, adjacency),
        "ok",
        "",
    )


def build_graph(
    atoms: Sequence[str],
    coords,
    *,
    bond_scale: float = BOND_SCALE_FACTOR,
) -> GraphBuild:
    """Build an element-labelled bonding graph from symbols and coordinates."""
    symbols = [str(atom) for atom in atoms]
    if not symbols:
        return GraphBuild(None, "empty", "no atoms")
    numbers = tuple(get_element_atomic_number(symbol) for symbol in symbols)
    if any(number <= 0 for number in numbers):
        return GraphBuild(None, "unknown_element", "atom with unknown element")
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[0] != len(symbols) or arr.shape[1] != 3:
        return GraphBuild(None, "invalid_coordinates", "coordinate shape does not match atoms")
    if not np.all(np.isfinite(arr)):
        return GraphBuild(None, "non_finite_coordinates", "coordinates contain NaN or infinity")
    if any(
        number >= len(GV_COVALENT_RADII) or GV_COVALENT_RADII[number] <= 0.0 for number in numbers
    ):
        return GraphBuild(None, "unknown_element", "atom without a covalent radius")

    adjacency = _adjacency_from_coords(numbers, arr, bond_scale)
    return GraphBuild(_graph_from_parts(symbols, numbers, adjacency), "ok", "")


def parse_bond_override(value: Any) -> list[tuple[int, int]]:
    """Parse an ``AddBond``/``DelBond`` metadata value into 1-based pairs.

    Accepts ``"1-2;3-4"`` (ConfGen output), a list of strings, or a list of
    ``[a, b]`` pairs. Invalid tokens are ignored.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        tokens: list[str] = []
        for item in value:
            if isinstance(item, str):
                tokens.extend(re.split(r"[;,\s]+", item))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                tokens.append(f"{item[0]}-{item[1]}")
            else:
                tokens.append(str(item))
    else:
        tokens = re.split(r"[;,\s]+", str(value))

    pairs: list[tuple[int, int]] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        parts = token.split("-")
        if len(parts) != 2:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if a != b:
            pairs.append((a, b))
    return pairs


def apply_bond_overrides(
    build: GraphBuild,
    *,
    add_bond: Sequence[tuple[int, int]],
    del_bond: Sequence[tuple[int, int]],
) -> GraphBuild:
    """Return *build* with topology overrides applied to its adjacency.

    ``add_bond``/``del_bond`` are 1-based pairs in the frame's own atom order.
    """
    graph = build.graph
    if graph is None or (not add_bond and not del_bond):
        return build
    n = graph.n
    adjacency = [set(row) for row in graph.adjacency]
    changed = False
    for a, b in del_bond:
        i, j = a - 1, b - 1
        if 0 <= i < n and 0 <= j < n and j in adjacency[i]:
            adjacency[i].discard(j)
            adjacency[j].discard(i)
            changed = True
    for a, b in add_bond:
        i, j = a - 1, b - 1
        if 0 <= i < n and 0 <= j < n and i != j and j not in adjacency[i]:
            adjacency[i].add(j)
            adjacency[j].add(i)
            changed = True
    if not changed:
        return build
    updated = tuple(tuple(sorted(row)) for row in adjacency)
    return GraphBuild(
        _graph_from_parts(graph.symbols, graph.atomic_numbers, updated),
        build.status,
        build.reason,
    )


def graph_from_adjacency(elements: Sequence[str], adjacency: Sequence[Sequence[int]]) -> Graph:
    """Build a graph from explicit symbol/adjacency data (fixtures, tests).

    The adjacency must be symmetric and free of self loops.
    """
    symbols = tuple(str(element) for element in elements)
    numbers = tuple(get_element_atomic_number(symbol) for symbol in symbols)
    n = len(symbols)
    rows: list[tuple[int, ...]] = []
    for i, row in enumerate(adjacency):
        neighbors = tuple(sorted({int(j) for j in row}))
        for j in neighbors:
            if j == i or not 0 <= j < n:
                raise ValueError(f"invalid adjacency entry {j} for atom {i}")
            if i not in tuple(int(x) for x in adjacency[j]):
                raise ValueError(f"adjacency must be symmetric: {i}->{j} missing reverse edge")
        rows.append(neighbors)
    if len(rows) != n:
        raise ValueError("adjacency row count does not match elements")
    return _graph_from_parts(symbols, numbers, tuple(rows))


def fixed_index_isomorphism(graph_a: Graph, graph_b: Graph) -> bool:
    """Check whether the fixed atom numbering already proves graph equality."""
    return (
        graph_a.atomic_numbers == graph_b.atomic_numbers and graph_a.adjacency == graph_b.adjacency
    )


def graphs_may_be_isomorphic(graph_a: Graph, graph_b: Graph) -> bool:
    """Cheap invariant pre-filter.  False proves non-isomorphism."""
    if graph_a.n != graph_b.n or graph_a.edge_count != graph_b.edge_count:
        return False
    if graph_a.element_counts != graph_b.element_counts:
        return False
    if graph_a.degree_sequence != graph_b.degree_sequence:
        return False
    return graph_a.fingerprint == graph_b.fingerprint


def validate_mapping(graph_a: Graph, graph_b: Graph, mapping: Sequence[int]) -> bool:
    """Validate that *mapping* is a complete element/edge-preserving bijection."""
    n = graph_a.n
    if graph_b.n != n or len(mapping) != n:
        return False
    seen = [False] * n
    for i, j in enumerate(mapping):
        if not 0 <= j < n or seen[j]:
            return False
        seen[j] = True
        if graph_a.atomic_numbers[i] != graph_b.atomic_numbers[j]:
            return False
        if len(graph_a.adjacency[i]) != len(graph_b.adjacency[j]):
            return False
    adjacency_b = graph_b.adjacency
    for i, row in enumerate(graph_a.adjacency):
        mapped_i = mapping[i]
        for neighbor in row:
            if mapped_i not in adjacency_b[mapping[neighbor]]:
                return False
    return True


@dataclass(frozen=True)
class MappingSearchStats:
    """Deterministic work accounting for one mapping search."""

    nodes: int
    mappings: int
    node_budget: int
    complete: bool
    reason: str
    pruned: int = 0


@dataclass(frozen=True)
class MappingSearchResult:
    """Outcome of looking for a single isomorphism."""

    status: str  # isomorphic | non_isomorphic | unresolved
    mapping: tuple[int, ...] | None
    stats: MappingSearchStats


class MappingBudgetExceeded(Exception):
    """Raised internally when the node budget is exhausted mid-search."""


class MappingSearch:
    """Budgeted exact enumeration of element/edge-preserving bijections.

    ``iter_mappings`` yields complete mappings of ``graph_a`` onto ``graph_b``.
    Exhausting the generator means all mappings have been visited; hitting the
    node budget raises :class:`MappingBudgetExceeded`, which callers must treat
    as an incomplete (unresolved) search rather than a negative result.
    """

    def __init__(
        self,
        graph_a: Graph,
        graph_b: Graph,
        node_budget: int,
        *,
        rmsd_cutoff: float | None = None,
        coords_a=None,
        coords_b=None,
        candidate_priority: dict[int, tuple[int, ...]] | None = None,
    ) -> None:
        self.graph_a = graph_a
        self.graph_b = graph_b
        self.node_budget = max(0, int(node_budget))
        self.nodes = 0
        self.mappings = 0
        self.pruned = 0
        self.complete = False
        self.reason = ""
        self._started = False
        self.candidate_priority = candidate_priority or {}
        self._pair_error_threshold: float | None = None
        self._dist_a = None
        self._dist_b = None
        if rmsd_cutoff is not None and coords_a is not None and coords_b is not None:
            n = graph_a.n
            if n > 1 and rmsd_cutoff > 0.0:
                coords_a = np.asarray(coords_a, dtype=np.float64)
                coords_b = np.asarray(coords_b, dtype=np.float64)
                if coords_a.shape == (n, 3) and coords_b.shape == (n, 3):
                    self._dist_a = np.linalg.norm(
                        coords_a[:, None, :] - coords_a[None, :, :], axis=-1
                    )
                    self._dist_b = np.linalg.norm(
                        coords_b[:, None, :] - coords_b[None, :, :], axis=-1
                    )
                    # |d_ij - d'_ij| <= e_i + e_j for alignment residuals e, so
                    # sum_pairs (delta d)^2 <= 2 * (n - 1) * sum_i e_i^2
                    #                        = 2 * (n - 1) * n * rmsd^2.
                    self._pair_error_threshold = 2.0 * (n - 1) * n * float(rmsd_cutoff) ** 2

    def stats(self) -> MappingSearchStats:
        return MappingSearchStats(
            nodes=self.nodes,
            mappings=self.mappings,
            node_budget=self.node_budget,
            complete=self.complete,
            reason=self.reason,
            pruned=self.pruned,
        )

    def _iter(self) -> Iterator[tuple[int, ...]]:
        graph_a = self.graph_a
        graph_b = self.graph_b
        n = graph_a.n
        if n != graph_b.n:
            self.reason = "size_mismatch"
            return
        if not graphs_may_be_isomorphic(graph_a, graph_b):
            self.reason = "invariant_mismatch"
            return

        adjacency_a = graph_a.adjacency
        adjacency_a_sets = [frozenset(row) for row in adjacency_a]
        adjacency_b_sets = [frozenset(row) for row in graph_b.adjacency]
        degree_a = [len(row) for row in adjacency_a]
        numbers_a = graph_a.atomic_numbers
        numbers_b = graph_b.atomic_numbers

        candidates_by_key: dict[tuple[int, int], tuple[int, ...]] = {}
        grouped: dict[tuple[int, int], list[int]] = {}
        for other in range(n):
            grouped.setdefault((numbers_b[other], len(adjacency_b_sets[other])), []).append(other)
        candidates_by_key = {key: tuple(value) for key, value in grouped.items()}

        used = [False] * n
        mapping = [-1] * n
        mapped_neighbor_count = [0] * n
        unmapped = set(range(n))
        assigned_stack: list[int] = []

        priority = self.candidate_priority

        def candidate_vertices(vertex: int) -> Iterator[int]:
            neighbors_a = adjacency_a_sets[vertex]
            base = priority.get(vertex)
            if base is None:
                base = candidates_by_key.get((numbers_a[vertex], degree_a[vertex]), ())
            for other in base:
                if used[other]:
                    continue
                if numbers_b[other] != numbers_a[vertex]:
                    continue
                if len(adjacency_b_sets[other]) != degree_a[vertex]:
                    continue
                neighbors_b = adjacency_b_sets[other]
                valid = True
                for assigned in assigned_stack:
                    assigned_to = mapping[assigned]
                    if (assigned in neighbors_a) != (assigned_to in neighbors_b):
                        valid = False
                        break
                if valid:
                    yield other

        dist_a = self._dist_a
        dist_b = self._dist_b
        error_threshold = self._pair_error_threshold

        def backtrack(remaining: int, pair_error: float) -> Iterator[tuple[int, ...]]:
            if remaining == 0:
                self.mappings += 1
                yield tuple(mapping)
                return
            if not assigned_stack:
                vertex = max(unmapped, key=lambda v: (degree_a[v], -v))
            else:
                vertex = max(
                    unmapped,
                    key=lambda v: (mapped_neighbor_count[v], degree_a[v], -v),
                )
            for other in candidate_vertices(vertex):
                self.nodes += 1
                if self.nodes > self.node_budget:
                    raise MappingBudgetExceeded
                mapping[vertex] = other
                used[other] = True
                unmapped.discard(vertex)
                next_error = pair_error
                if dist_a is not None and dist_b is not None:
                    for assigned in assigned_stack:
                        delta = dist_a[vertex, assigned] - dist_b[other, mapping[assigned]]
                        next_error += delta * delta
                assigned_stack.append(vertex)
                for neighbor in adjacency_a[vertex]:
                    mapped_neighbor_count[neighbor] += 1
                if error_threshold is None or next_error < error_threshold:
                    yield from backtrack(remaining - 1, next_error)
                else:
                    # Pairwise-distance lower bound proves no completion of
                    # this branch can reach the cutoff.
                    self.pruned += 1
                for neighbor in adjacency_a[vertex]:
                    mapped_neighbor_count[neighbor] -= 1
                assigned_stack.pop()
                unmapped.add(vertex)
                used[other] = False
                mapping[vertex] = -1

        yield from backtrack(n, 0.0)

    def iter_mappings(self) -> Iterator[tuple[int, ...]]:
        if self._started:
            raise RuntimeError("MappingSearch instances are single-use")
        self._started = True
        try:
            yield from self._iter()
        except MappingBudgetExceeded:
            self.reason = "budget_exhausted"
            raise
        else:
            self.complete = True
            if not self.reason:
                self.reason = "exhausted"

    def find_one(self) -> MappingSearchResult:
        try:
            for mapping in self.iter_mappings():
                self.reason = "found"
                return MappingSearchResult("isomorphic", mapping, self.stats())
        except MappingBudgetExceeded:
            return MappingSearchResult("unresolved", None, self.stats())
        if self.reason == "invariant_mismatch" or self.reason == "size_mismatch":
            return MappingSearchResult("non_isomorphic", None, self.stats())
        return MappingSearchResult("non_isomorphic", None, self.stats())


def find_isomorphism(
    graph_a: Graph,
    graph_b: Graph,
    node_budget: int = DEFAULT_MAPPING_NODE_BUDGET,
) -> MappingSearchResult:
    """Find one exact isomorphism or prove none exists within the budget."""
    if fixed_index_isomorphism(graph_a, graph_b):
        stats = MappingSearchStats(
            nodes=0,
            mappings=1,
            node_budget=max(0, int(node_budget)),
            complete=False,
            reason="fixed_index",
        )
        return MappingSearchResult("isomorphic", tuple(range(graph_a.n)), stats)
    return MappingSearch(graph_a, graph_b, node_budget).find_one()


@dataclass
class TopologyCluster:
    """Frames proven (or not yet provable) to share one bonding topology."""

    cluster_id: str
    status: str  # confirmed | unresolved | invalid
    frames: list[dict]
    first_index: int
    reason: str = ""


def group_frames_by_topology(
    frames: Sequence[dict],
    node_budget: int = DEFAULT_MAPPING_NODE_BUDGET,
) -> list[TopologyCluster]:
    """Partition frames into exact topology classes.

    Frames without a valid graph become ``invalid`` singletons.  Frames whose
    exact match cannot be completed within the budget become ``unresolved``
    singletons; they are never merged into a confirmed class.
    """
    clusters: list[TopologyCluster] = []
    for frame in frames:
        graph = frame.get("graph")
        first_index = int(frame.get("original_index", 0))
        if graph is None:
            clusters.append(
                TopologyCluster(
                    cluster_id=f"T{len(clusters)}",
                    status="invalid",
                    frames=[frame],
                    first_index=first_index,
                    reason=str(frame.get("graph_reason") or "invalid graph"),
                )
            )
            continue

        placed = False
        unresolved_reason: str | None = None
        for cluster in clusters:
            if cluster.status != "confirmed":
                continue
            representative = cluster.frames[0].get("graph")
            if representative is None:
                continue
            if fixed_index_isomorphism(representative, graph):
                cluster.frames.append(frame)
                placed = True
                break
            if not graphs_may_be_isomorphic(representative, graph):
                continue
            result = find_isomorphism(representative, graph, node_budget)
            if result.status == "isomorphic":
                cluster.frames.append(frame)
                placed = True
                break
            if result.status == "unresolved":
                unresolved_reason = result.stats.reason
        if placed:
            continue
        if unresolved_reason is not None:
            clusters.append(
                TopologyCluster(
                    cluster_id=f"T{len(clusters)}",
                    status="unresolved",
                    frames=[frame],
                    first_index=first_index,
                    reason=unresolved_reason,
                )
            )
        else:
            clusters.append(
                TopologyCluster(
                    cluster_id=f"T{len(clusters)}",
                    status="confirmed",
                    frames=[frame],
                    first_index=first_index,
                    reason="",
                )
            )
    return clusters
