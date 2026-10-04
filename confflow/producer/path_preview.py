"""Read-only path estimates using the same typed runtime context build."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from ..domain.structure import StructureRecord
from ..domain.topology import TopologyPatch
from ..science.topology import resolve_and_persist_kwargs
from ..workflow.v4.confgen_schema import ConfgenModelV3


def preview_paths_request(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the preview request vocabulary beside its resolver."""
    structure = parameters.get("structure")
    native = parameters.get("native")
    if not isinstance(structure, Mapping) or not isinstance(native, Mapping):
        raise ValueError("structure and native must be objects")
    return preview_paths(structure, native)


def preview_paths(structure: Mapping[str, Any], native: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve endpoint-only grids without generating conformers.

    The ``native`` block is the typed v3 confgen scope
    (:class:`ConfgenModelV3`): the typed model is the sole authority and
    legacy legacy-shaped values (``angle_step``/``bond_scale``) fail closed
    as unknown members.  Only a paths-only scope is supported here; active
    torsions/rings/coordination (or any other scope that changes the count
    or the result) are refused structurally instead of silently cropped to
    a misleading partial count.

    The estimate reuses the runtime context build (``build_context``) --
    the same working-graph priority, path expansion and topology digest the
    executor sees -- then re-runs the torsion stage's pure validation
    (``TorsionStage.estimate``) so an unmeasurable endpoint (e.g. a
    terminal hydrogen) is refused with the exact runtime diagnostic,
    naming ``confgen.paths[i].start``/``end``, the atom and the bond.
    """
    if set(structure) - {"id", "atoms", "coordinates", "charge", "multiplicity", "topology"}:
        raise ValueError("path preview structure has unsupported fields")
    patch = None
    raw_topology = structure.get("topology")
    if raw_topology is not None:
        if not isinstance(raw_topology, Mapping) or set(raw_topology) - {
            "add",
            "delete",
            "provenance",
        }:
            raise ValueError("structure topology must contain add/delete/provenance")
        patch = TopologyPatch(
            add_edges=raw_topology.get("add", ()),
            delete_edges=raw_topology.get("delete", ()),
            provenance=raw_topology.get("provenance", ""),
        )
    record = StructureRecord(
        id=structure.get("id", "preview"),
        atoms=structure.get("atoms", ()),
        coordinates=structure.get("coordinates", ()),
        charge=structure.get("charge"),
        multiplicity=structure.get("multiplicity"),
        topology_patch=patch,
    )
    # A declared patch freezes the working graph exactly once on the picked
    # geometry, the same way the runtime assembly persists a driving input;
    # build_context then owns graph priority (persisted graph > perception
    # > typed topology) and never re-perceives behind the caller's back.
    persist_kwargs = resolve_and_persist_kwargs(record, record.coordinates)
    if persist_kwargs:
        record = replace(record, **persist_kwargs)
    model = ConfgenModelV3.model_validate(dict(native))
    _refuse_non_paths_scopes(model)
    from ..science.confgen.model import WorkingRealization, build_context

    context = build_context(record, model.scientific_native())
    resolved = context.resolved_spec.get("paths_resolved")
    if not isinstance(resolved, Mapping) or not resolved.get("rotors"):
        raise ValueError("path preview selected no rotatable bonds")
    rotors = resolved["rotors"]
    # Pure framework validation (no engine.run, no target enumeration, no
    # geometry): a terminal/unmeasurable bond raises the runtime wording.
    from ..science.confgen.torsion.stage import TorsionStage

    stage = TorsionStage(context.resolved_spec)
    stage.estimate(
        WorkingRealization(structure=context.structure, state_key=context.input_state_key),
        context,
    )
    raw = int(resolved["raw_cartesian_size"])
    hard_limit = int(model.limits.max_declared_states)
    return {
        "raw_conformers": raw,
        "resolved_rotors": len(rotors),
        "hard_limit": hard_limit,
        "exceeds_limit": raw > hard_limit,
        "topology_digest": resolved["topology_digest"],
        "warnings": list(resolved["warnings"]),
        "paths": [
            {
                "source": path["source"],
                "chain": list(path["route"]),
                "move": path["move"],
            }
            for path in resolved["declared_paths"]
        ],
        "rotors": [
            {
                "bond": list(rotor["bond"]),
                "moving_atoms": list(rotor["moving"]),
                "angles": list(rotor["angles"]),
            }
            for rotor in rotors
        ],
    }


def _refuse_non_paths_scopes(model: ConfgenModelV3) -> None:
    """Refuse every active scope the endpoint-only estimate cannot show."""
    active = [
        name
        for name, present in (
            ("coordination", model.coordination is not None),
            ("rings", bool(model.rings)),
            ("torsions", bool(model.torsions)),
            ("exclusions", bool(model.exclusions)),
            ("sampling", model.sampling is not None),
        )
        if present
    ]
    if active:
        raise ValueError(
            "path preview supports endpoint-only scopes; advanced scopes need "
            f"planning: {', '.join(active)}"
        )
