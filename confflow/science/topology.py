#!/usr/bin/env python3

"""Single working-topology authority for Phase 1 input simplification.

Every topology consumer (typed ConfGen planner, legacy ConfGen executor,
refine/dedup transform adjacency, calculation output profiles, and the run
import boundary) resolves the intended covalent graph through
:func:`resolve_working_adjacency` — never through a second ad-hoc
perception-plus-correction runtime.

Resolution order for one structure:

1. a persisted ``working_topology`` wins verbatim (the graph resolved once
   on the root input geometry; descendants never re-perceive moved
   geometry);
2. otherwise distance perception at ``bond_scale`` plus the record's
   ``topology_patch`` (one-based add/delete on top of perception);
3. a pure-legacy record (no patch, no persisted graph) resolves to plain
   perception — callers must not persist that result as new scientific
   content (see :func:`should_persist_working_graph`).

:func:`check_spec_patch_conflict` is the fail-closed gate between
spec-level corrections (``topology.bonds`` / ``add_bond`` / ``del_bond``)
and record-level patches: both present means two topology authorities and
fails closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.errors import DomainError
from ..domain.topology import TopologyPatch
from .bonds import perceive_adjacency

__all__ = [
    "DEFAULT_PERCEPTION_BOND_SCALE",
    "check_spec_patch_conflict",
    "inherit_topology_kwargs",
    "resolve_and_persist_kwargs",
    "resolve_working_adjacency",
    "should_persist_working_graph",
]

#: Perception scale used when a caller supplies none (the ConfGen default).
DEFAULT_PERCEPTION_BOND_SCALE: float = 1.15


def _record_patch(structure: Any) -> TopologyPatch | None:
    """Return the record's patch, or ``None`` when absent/empty."""
    patch = getattr(structure, "topology_patch", None)
    if patch is None:
        return None
    if isinstance(patch, dict):
        patch = TopologyPatch.from_dict(patch)
    if not isinstance(patch, TopologyPatch):
        raise DomainError(
            "topology_patch must be a TopologyPatch or None, " f"got {type(patch).__name__}"
        )
    if patch.is_empty:
        return None
    return patch


def should_persist_working_graph(structure: Any) -> bool:
    """Return whether *structure* carries intended topology worth persisting.

    Only a nonempty patch or an already-persisted graph is scientific
    content.  Pure-legacy records must not gain a persisted graph just to
    freeze one perception of their geometry.
    """
    if getattr(structure, "working_topology", None) is not None:
        return True
    return _record_patch(structure) is not None


def check_spec_patch_conflict(spec_topology: Any, structure: Any, *, where: str) -> None:
    """Fail closed when spec corrections meet a record-authoritative graph.

    Either record authority — a nonempty ``topology_patch`` or a persisted
    ``working_topology`` — combined with any spec graph declaration fails
    closed: an explicit ``topology["bonds"]`` key (even ``[]``, which
    defines the empty graph), or a nonempty ``add_bond``/``del_bond``
    list.  Empty native add/del arrays stay absent and never conflict.
    """
    has_authority = _record_patch(structure) is not None or (
        getattr(structure, "working_topology", None) is not None
    )
    if not has_authority:
        return
    declared = False
    if isinstance(spec_topology, Mapping):
        if "bonds" in spec_topology and spec_topology.get("bonds") is not None:
            declared = True
        for key in ("add_bond", "del_bond"):
            entries = spec_topology.get(key)
            if entries:
                declared = True
    else:
        for key in ("add_bond", "del_bond"):
            entries = getattr(spec_topology, key, None)
            if entries:
                declared = True
    if declared:
        patch = _record_patch(structure)
        detail = (
            f"(patch {patch.to_payload()})" if patch is not None else "(persisted working graph)"
        )
        raise DomainError(
            f"{where}: spec topology corrections and the record-authoritative graph "
            f"both declare the working graph; declare one authority {detail}"
        )


def _working_copy(structure: Any, n_atoms: int) -> list[list[int]] | None:
    """Return a validated copy of the persisted graph, or ``None``."""
    stored = getattr(structure, "working_topology", None)
    if stored is None:
        return None
    rows = [sorted(int(v) for v in row) for row in stored]
    if len(rows) != n_atoms:
        raise DomainError(
            "persisted working_topology holds "
            f"{len(rows)} rows for {n_atoms} atoms; refusing to guess"
        )
    return [list(row) for row in rows]


def resolve_working_adjacency(
    structure: Any,
    atomic_numbers: Sequence[int],
    coordinates: Sequence[Sequence[float]],
    *,
    bond_scale: float = DEFAULT_PERCEPTION_BOND_SCALE,
) -> list[list[int]]:
    """Return the intended covalent adjacency for *structure*.

    A persisted ``working_topology`` wins verbatim (validated row count
    only — shape was validated at record construction).  Otherwise the
    graph is perceived on *coordinates* at *bond_scale* and the record
    patch applied.  Raises :class:`DomainError` fail-closed on any defect.
    """
    n_atoms = len(list(structure.atoms))
    if n_atoms < 1:
        raise DomainError("working topology requires at least one atom")
    persisted = _working_copy(structure, n_atoms)
    if persisted is not None:
        return persisted
    try:
        scale = float(bond_scale)
    except (TypeError, ValueError) as exc:
        raise DomainError(f"bond_scale malformed: {exc}") from exc
    import math as _math

    if not _math.isfinite(scale) or scale <= 0:
        raise DomainError(f"bond_scale must be a positive number, got {bond_scale!r}")
    try:
        adjacency = perceive_adjacency(
            [int(item) for item in atomic_numbers], coordinates, bond_scale=scale
        )
    except ValueError as exc:
        raise DomainError(f"bond perception failed: {exc}") from exc
    if len(adjacency) != n_atoms:
        raise DomainError("perceived adjacency row count mismatches atom count")
    patch = _record_patch(structure)
    if patch is None:
        return [sorted(row) for row in adjacency]
    try:
        return patch.apply_to_adjacency([list(row) for row in adjacency])
    except Exception as exc:
        raise DomainError(f"topology patch rejected: {exc}") from exc


def inherit_topology_kwargs(source: Any, context_adjacency: Any) -> dict[str, Any]:
    """Return ``topology_patch``/``working_topology`` kwargs for a descendant.

    In-engine inheritance: the working graph is already resolved (the
    context adjacency, computed once on the run input geometry), so it is
    propagated verbatim — never re-perceived on moved realization
    geometry.  A source-persisted graph wins over the context adjacency
    (identical by construction when the context was built from that
    source).  Pure-legacy sources yield ``{}``.
    """
    if not should_persist_working_graph(source):
        return {}
    stored = getattr(source, "working_topology", None)
    rows = stored if stored is not None else context_adjacency
    try:
        graph = tuple(tuple(int(v) for v in row) for row in rows)
    except (TypeError, ValueError) as exc:
        raise DomainError(f"inherited working graph malformed: {exc}") from exc
    if not graph:
        raise DomainError("inherited working graph is empty")
    return {
        "topology_patch": getattr(source, "topology_patch", None),
        "working_topology": graph,
    }


def resolve_and_persist_kwargs(
    source: Any,
    coordinates: Any,
    *,
    bond_scale: float = DEFAULT_PERCEPTION_BOND_SCALE,
    atomic_numbers: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Return ``topology_patch``/``working_topology`` kwargs for a descendant.

    The graph resolves exactly once: a persisted source graph wins; a bare
    patch resolves on the *source* geometry passed in *coordinates* (callers
    pass the pre-change geometry, never re-perceived moved output); a
    pure-legacy source yields ``{}`` so no new scientific content is
    invented.  Charge/multiplicity handling stays with the caller (explicit
    calculation overrides retained).
    """
    if not should_persist_working_graph(source):
        return {}
    patch = getattr(source, "topology_patch", None)
    stored = getattr(source, "working_topology", None)
    if stored is not None:
        graph: Any = tuple(tuple(int(v) for v in row) for row in stored)
    else:
        if atomic_numbers is None:
            from ..domain.elements import atomic_number as _atomic_number

            try:
                atomic_numbers = [_atomic_number(s) for s in source.atoms]
            except Exception as exc:
                raise DomainError(f"working graph element lookup failed: {exc}") from exc
        graph = tuple(
            tuple(row)
            for row in resolve_working_adjacency(
                source, atomic_numbers, coordinates, bond_scale=bond_scale
            )
        )
    return {"topology_patch": patch, "working_topology": graph}
