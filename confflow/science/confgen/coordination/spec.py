#!/usr/bin/env python3

"""Coordination contributions to the typed topology graph (search lane).

The ``coordination`` section declares one metal center and its binding-site
donor atoms. This module supplies the two hooks the graph builder needs:
the metal-center metadata (:func:`graph_metadata`) and the COORDINATION
typing of declared metal-donor pairs (:func:`contribute_topology`). Spec
normalization and validation of the section live in ``search_spec``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["contribute_topology", "graph_metadata"]


def graph_metadata(resolved: Mapping[str, Any], n_atoms: int) -> Mapping[str, Any] | None:
    """Return generic typed-graph metadata owned by coordination.

    Called by ``topology.build_typed_graph`` before the explicit-bonds branch. Body mirrors that function verbatim
    (single ``out of range`` message for every bad shape, including
    bool/non-int); the overlay ``contribute_topology`` keeps its own
    malformed/out-of-range checks at the later overlay site (never
    moved early here).
    """
    coordination = resolved.get("coordination") if isinstance(resolved, Mapping) else None
    if not isinstance(coordination, Mapping):
        return None
    metal = coordination.get("metal_center")
    if metal is None:
        return None
    if isinstance(metal, bool) or not isinstance(metal, int) or metal < 0 or metal >= n_atoms:
        raise ValueError(f"resolved coordination metal_center out of range: {metal!r}")
    return {"metal_center": int(metal)}


def contribute_topology(resolved: Mapping[str, Any], build: Any) -> None:
    """Authoritatively type declared metal-site edges (perceived path only).

    Called by ``topology.build_typed_graph`` after the structure patch, using
    the ``n_atoms``/``adjacency``/``typed``/``explicit_covalent`` fields of
    ``build``.

    Distance perception guesses every close pair COVALENT, including
    metal-ligand contacts. The declared coordination scope (metal_center plus
    binding-site donor atoms, internal 0-based) overrides the guess: each
    metal-donor pair loses its guessed COVALENT edge/adjacency and gains a
    COORDINATION record, so fragment decomposition
    never sees metal-ligand pseudo-bonds. An explicit COVALENT
    declaration for a declared metal-donor pair is a contradiction and
    fails closed; perception guesses are overridden silently. Pairs never
    declared stay exactly as perceived/corrected.
    """
    from confflow.science.confgen.graph import EdgeType, TypedEdge

    n_atoms = build.n_atoms
    adjacency = build.adjacency
    typed = build.typed
    explicit_covalent = build.explicit_covalent
    coordination = resolved.get("coordination") if isinstance(resolved, Mapping) else None
    if not isinstance(coordination, Mapping):
        return
    metal = coordination.get("metal_center")
    if metal is None:
        return
    if isinstance(metal, bool) or not isinstance(metal, int):
        raise ValueError(f"resolved coordination metal_center malformed: {metal!r}")
    if metal < 0 or metal >= n_atoms:
        raise ValueError(f"resolved coordination metal_center out of range: {metal!r}")
    sites = coordination.get("binding_sites")
    if sites is None:
        return
    if not isinstance(sites, (list, tuple)):
        raise ValueError("$.coordination.binding_sites must be a list")
    donors: set[int] = set()
    for position, site in enumerate(sites):
        if not isinstance(site, Mapping):
            raise ValueError(f"$.coordination.binding_sites[{position}] must be a mapping")
        for atom in site.get("atoms") or []:
            if isinstance(atom, bool) or not isinstance(atom, int):
                raise ValueError(
                    f"$.coordination.binding_sites[{position}].atoms holds "
                    f"a non-integer index {atom!r}"
                )
            if atom < 0 or atom >= n_atoms:
                raise ValueError(
                    f"$.coordination.binding_sites[{position}] index {atom} "
                    f"out of range for {n_atoms} atoms"
                )
            donors.add(int(atom))
    for donor in sorted(donors):
        if donor == metal:
            raise ValueError("coordination scope binds the metal to itself")
        pair = (min(metal, donor), max(metal, donor))
        if pair in explicit_covalent:
            raise ValueError(
                f"topology declares COVALENT for pair {pair} inside the declared "
                "coordination scope; contradictory kinds for one pair fail closed"
            )
        typed.pop((pair[0], pair[1], EdgeType.COVALENT), None)
        adjacency[metal].discard(donor)
        adjacency[donor].discard(metal)
        typed[(pair[0], pair[1], EdgeType.COORDINATION)] = TypedEdge(
            a=pair[0],
            b=pair[1],
            type=EdgeType.COORDINATION,
            provenance="declared-binding-site",
        )
