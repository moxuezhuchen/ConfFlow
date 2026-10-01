"""Read-only path estimates using the same working graph and rotor resolver."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.elements import atomic_number
from ..domain.structure import StructureRecord
from ..domain.topology import TopologyPatch
from ..execution.confgen_executor import (
    _DEFAULT_ANGLE_STEP,
    _DEFAULT_BOND_SCALE,
    _LEGACY_MAX_DECLARED_STATES,
)
from ..science.bonds import covalent_radii
from ..science.confgen.torsion.paths import (
    canonical_grid_size,
    canonicalize_rotors,
    parse_path_declarations,
    resolve_paths,
)
from ..science.topology import resolve_working_adjacency


def preview_paths_request(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the preview request vocabulary beside its resolver."""
    structure = parameters.get("structure")
    native = parameters.get("native")
    if not isinstance(structure, Mapping) or not isinstance(native, Mapping):
        raise ValueError("structure and native must be objects")
    return preview_paths(structure, native)


def preview_paths(structure: Mapping[str, Any], native: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve endpoint-only grids without generating conformers.

    Advanced mixed scopes require execution's full planner; this preview
    refuses them rather than showing an incomplete Cartesian estimate.
    """
    if set(native) - {"paths", "angle_step", "bond_scale", "strict_path_bond_check"}:
        raise ValueError(
            "path preview supports endpoint-only scopes; advanced scopes need planning"
        )
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
    strict = native.get("strict_path_bond_check", False)
    if type(strict) is not bool:
        raise ValueError("strict_path_bond_check must be boolean")
    numbers = [atomic_number(atom) for atom in record.atoms]
    adjacency = resolve_working_adjacency(
        record,
        numbers,
        record.coordinates,
        bond_scale=native.get("bond_scale", _DEFAULT_BOND_SCALE),
    )
    parsed = parse_path_declarations(
        native.get("paths"), default_step=native.get("angle_step", _DEFAULT_ANGLE_STEP)
    )
    resolved = resolve_paths(
        parsed,
        adjacency,
        coords=record.coordinates,
        radii=covalent_radii(numbers),
        strict_bond_check=strict,
        topology_extra={
            "add_bond": [],
            "del_bond": [],
            "bond_scale": native.get("bond_scale", _DEFAULT_BOND_SCALE),
        },
    )
    rotors = canonicalize_rotors(resolved.rotors)
    if not rotors:
        raise ValueError("path preview selected no rotatable bonds")
    count = canonical_grid_size(rotors)
    return {
        "raw_conformers": count,
        "resolved_rotors": len(rotors),
        "hard_limit": _LEGACY_MAX_DECLARED_STATES,
        "exceeds_limit": count > _LEGACY_MAX_DECLARED_STATES,
        "topology_digest": resolved.topology_digest,
        "warnings": list(resolved.warnings),
        "paths": [
            {"source": path.source, "chain": [atom + 1 for atom in path.route], "move": path.move}
            for path in resolved.declared_paths
        ],
        "rotors": [
            {
                "bond": [atom + 1 for atom in rotor.bond],
                "moving_atoms": [atom + 1 for atom in rotor.moving],
                "angles": list(rotor.angles),
            }
            for rotor in rotors
        ],
    }
