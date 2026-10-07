#!/usr/bin/env python3

"""Immutable molecular-topology correction patch (Phase 1 input simplification).

A :class:`TopologyPatch` declares user-intended bond corrections on top of
distance perception: one-based ``add_edges`` / ``delete_edges`` plus a
nonsemantic ``provenance`` label.  Validation is fail-closed: strict
integers (``type(value) is int``; bools rejected), one-based bounds
(``>= 1``), distinct endpoints, canonical unordered pairs, no duplicates
within a list, and no pair declared both added and deleted.  Upper bounds
(``<= n_atoms``) are checked by :meth:`validate_for`, which knows the atom
count; :class:`TopologyPatch` itself never guesses it.

Provenance is stored and round-tripped but never enters semantic payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from .errors import InvalidStructureError

__all__ = [
    "TopologyPatch",
]

#: Digest payload key used when a patch rides in scientific/reuse payloads.
TOPOLOGY_PAYLOAD_KEY: Final[str] = "topology"

#: Digest payload key for the persisted working adjacency.
WORKING_TOPOLOGY_PAYLOAD_KEY: Final[str] = "working_topology"


def _require_edge_list(value: Any, field_name: str) -> tuple[tuple[int, int], ...]:
    """Validate one edge list into canonical sorted one-based pairs."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise InvalidStructureError(f"{field_name} must be a list of [a, b] index pairs")
    pairs: list[tuple[int, int]] = []
    for position, item in enumerate(value):
        path = f"{field_name}[{position}]"
        if isinstance(item, (str, bytes)) or not isinstance(item, (list, tuple)):
            raise InvalidStructureError(f"{path} must hold exactly two atom indices")
        entries = list(item)
        if len(entries) != 2:
            raise InvalidStructureError(
                f"{path} must hold exactly two atom indices, got {len(entries)}"
            )
        checked: list[int] = []
        for axis, endpoint in enumerate(entries):
            if type(endpoint) is not int:
                raise InvalidStructureError(
                    f"{path}[{axis}] must be a strict integer atom index, " f"got {endpoint!r}"
                )
            if endpoint < 1:
                raise InvalidStructureError(
                    f"{path}[{axis}] is one-based and must be >= 1, got {endpoint}"
                )
            checked.append(endpoint)
        if checked[0] == checked[1]:
            raise InvalidStructureError(f"{path} must name two distinct atoms")
        pairs.append((min(checked), max(checked)))
    ordered = tuple(sorted(pairs))
    if len(set(ordered)) != len(ordered):
        raise InvalidStructureError(f"{field_name} holds a duplicate edge")
    return ordered


@dataclass(frozen=True, slots=True)
class TopologyPatch:
    """One immutable bond-correction declaration (one-based atom numbers)."""

    add_edges: tuple[tuple[int, int], ...] = ()
    delete_edges: tuple[tuple[int, int], ...] = ()
    provenance: str = ""

    def __post_init__(self) -> None:
        add_edges = _require_edge_list(self.add_edges, "add_edges")
        object.__setattr__(self, "add_edges", add_edges)
        delete_edges = _require_edge_list(self.delete_edges, "delete_edges")
        object.__setattr__(self, "delete_edges", delete_edges)
        if not isinstance(self.provenance, str):
            raise InvalidStructureError("provenance must be a string")
        shared = set(add_edges) & set(delete_edges)
        if shared:
            raise InvalidStructureError(
                "topology patch declares the same edge as added and deleted: "
                + ", ".join(f"{a}-{b}" for a, b in sorted(shared))
            )

    @property
    def is_empty(self) -> bool:
        """Return whether this patch carries no edge corrections."""
        return not self.add_edges and not self.delete_edges

    def validate_for(self, n_atoms: int) -> None:
        """Fail closed when any edge exceeds the atom count of *n_atoms*."""
        if type(n_atoms) is not int or n_atoms < 1:
            raise InvalidStructureError(f"n_atoms must be a positive integer, got {n_atoms!r}")
        for field_name in ("add_edges", "delete_edges"):
            for pair in getattr(self, field_name):
                if pair[1] > n_atoms:
                    raise InvalidStructureError(
                        f"{field_name} edge {pair[0]}-{pair[1]} is out of range "
                        f"for {n_atoms} atoms"
                    )

    def same_semantics(self, other: TopologyPatch | None) -> bool:
        """Return whether *other* declares identical edge corrections."""
        if other is None:
            return self.is_empty
        if not isinstance(other, TopologyPatch):
            raise InvalidStructureError(
                "same_semantics requires a TopologyPatch or None, " f"got {type(other).__name__}"
            )
        return self.add_edges == other.add_edges and self.delete_edges == other.delete_edges

    def to_payload(self) -> dict[str, Any]:
        """Return the semantic digest contribution (provenance excluded)."""
        return {
            "add_edges": [[int(a), int(b)] for a, b in self.add_edges],
            "delete_edges": [[int(a), int(b)] for a, b in self.delete_edges],
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        payload = self.to_payload()
        payload["provenance"] = str(self.provenance)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> TopologyPatch | None:
        """Rebuild a patch from :meth:`to_dict` output, failing closed."""
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise InvalidStructureError("topology patch payload must be a mapping or None")
        unknown = sorted(set(payload) - {"add_edges", "delete_edges", "provenance"})
        if unknown:
            raise InvalidStructureError(f"topology patch holds unknown keys {unknown}")
        provenance = payload.get("provenance", "")
        if not isinstance(provenance, str):
            raise InvalidStructureError("topology patch provenance must be a string")
        return cls(
            add_edges=_require_edge_list(payload.get("add_edges", ()), "add_edges"),
            delete_edges=_require_edge_list(payload.get("delete_edges", ()), "delete_edges"),
            provenance=provenance,
        )

    def apply_to_adjacency(self, adjacency: list[list[int]]) -> list[list[int]]:
        """Return *adjacency* (zero-based rows) with this patch applied.

        The input graph is strictly validated (strict integer neighbours,
        in-range indices, no self loops, symmetric rows) and never
        mutated; malformed input fails closed rather than being repaired.
        Added pairs union silently (legacy ``add_bond`` behavior); deleted
        pairs discard silently (legacy ``del_bond`` behavior).  Patch edge
        bounds are validated against ``len(adjacency)``.
        """
        if not isinstance(adjacency, list):
            raise InvalidStructureError("adjacency must hold one neighbour row per atom")
        n_atoms = len(adjacency)
        if n_atoms < 1:
            raise InvalidStructureError("adjacency must not be empty")
        for index, row in enumerate(adjacency):
            if isinstance(row, (str, bytes)) or not isinstance(row, (list, tuple)):
                raise InvalidStructureError(f"adjacency[{index}] must be a neighbour list")
            seen: set[int] = set()
            for position, entry in enumerate(row):
                if type(entry) is not int:
                    raise InvalidStructureError(
                        f"adjacency[{index}][{position}] must be a strict integer "
                        f"atom index, got {entry!r}"
                    )
                if entry < 0 or entry >= n_atoms:
                    raise InvalidStructureError(
                        f"adjacency[{index}][{position}] index {entry} out of range "
                        f"for {n_atoms} atoms"
                    )
                if entry == index:
                    raise InvalidStructureError(f"adjacency[{index}] holds a self loop")
                if entry in seen:
                    raise InvalidStructureError(
                        f"adjacency[{index}] holds a duplicate neighbour {entry}"
                    )
                seen.add(entry)
        for index, row in enumerate(adjacency):
            for other in row:
                if index not in adjacency[other]:
                    raise InvalidStructureError(
                        f"adjacency is not symmetric: {index} lists {other} " "but not vice versa"
                    )
        rows = [set(row) for row in adjacency]
        for field_name in ("add_edges", "delete_edges"):
            for pair in getattr(self, field_name):
                if pair[1] > n_atoms:
                    raise InvalidStructureError(
                        f"{field_name} edge {pair[0]}-{pair[1]} is out of range "
                        f"for {n_atoms} atoms"
                    )
        out = [set(row) for row in rows]
        for first_one, second_one in self.add_edges:
            first, second = first_one - 1, second_one - 1
            out[first].add(second)
            out[second].add(first)
        for first_one, second_one in self.delete_edges:
            first, second = first_one - 1, second_one - 1
            out[first].discard(second)
            out[second].discard(first)
        for index, neighbours in enumerate(out):
            neighbours.discard(index)
        return [sorted(row) for row in out]
