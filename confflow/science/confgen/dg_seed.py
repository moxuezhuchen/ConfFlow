"""Distance-geometry seed starts for one commanded coordination class.

Builds RDKit ETKDGv3 starting geometries biased toward a commanded
shape-class placement from the typed graph and a trusted reference geometry.
Embedding returns whatever RDKit realizes (possibly fewer than requested).

The returned starts are starts only: they are not audited, not labeled
REALIZED, and carry no validity claim.

Known limitation: redox-ambiguous ligands can be misperceived (catecholate
is perceived as charge 0 o-quinone, nitrate as charge -3); use the
``fragment_charges`` override for such fragments.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .coordination.shapes import get_shape
from .graph import CoordinationSpec, EdgeType, TypedGraph

__all__ = [
    "DGSeedError",
    "DGSeedResult",
    "DGSeedSettings",
    "generate_dg_seeds",
]

#: Charge trial order for automatic fragment bond perception.
_CHARGE_ORDER: tuple[int, ...] = (0, -1, 1, -2, 2, -3, 3, -4)


class DGSeedError(ValueError):
    """Fail-closed error for DG seed generation."""


@dataclass(frozen=True, slots=True)
class DGSeedSettings:
    """Settings for DG seed embedding.

    Parameters
    ----------
    count : int
        Starts requested (returned starts may be fewer).
    random_seed : int
        RDKit embedding seed (deterministic for fixed inputs).
    small_ring_torsions : bool
        RDKit ETKDGv3 ``useSmallRingTorsions`` switch.
    max_iterations : int
        RDKit embedding iteration budget.
    core_tolerance : float
        Bounds tolerance for metal/donor polyhedron pair distances.
    forming_tolerance : float
        Bounds tolerance for FORMING/BREAKING pair distances.
    forming_neighbor_tolerance : float
        Bounds tolerance for reaction-pair neighbor distances.
    donor_neighbor_tolerance : float
        Bounds tolerance for metal to donor-substituent distances.
    fragment_charges : tuple[tuple[int, int], ...]
        ``(atom index of the fragment, charge)`` overrides.
    timeout_seconds : int
        Maximum time in seconds to generate one conformer of one molecule
        fragment (RDKit ETKDG ``params.timeout`` scope: per conformer per
        fragment; 0 means no limit). A timeout never raises: embedding
        returns the starts obtained so far (possibly none).
    """

    count: int = 20
    random_seed: int = 1
    small_ring_torsions: bool = True
    max_iterations: int = 200
    core_tolerance: float = 0.01
    forming_tolerance: float = 0.02
    forming_neighbor_tolerance: float = 0.15
    donor_neighbor_tolerance: float = 0.25
    fragment_charges: tuple[tuple[int, int], ...] = ()
    timeout_seconds: int = 0


#: Shared default settings (frozen, safe to share across calls).
_DEFAULT_SETTINGS = DGSeedSettings()


@dataclass(frozen=True, slots=True)
class DGSeedResult:
    """DG seed embedding outcome (starts only, no validity claim)."""

    coords: tuple[np.ndarray, ...]
    requested: int
    fragment_charges: tuple[tuple[tuple[int, ...], int], ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible summary (no arrays).

        Returns
        -------
        dict[str, object]
            Requested/returned counts plus per-fragment charges.
        """
        return {
            "requested": int(self.requested),
            "returned": len(self.coords),
            "fragment_charges": [
                [list(indices), int(charge)] for indices, charge in self.fragment_charges
            ],
        }


def _candidate_bonds(graph: TypedGraph) -> set[tuple[int, int]]:
    """Collect single-bond candidates (covalent plus reaction edges/pairs)."""
    bonds: set[tuple[int, int]] = set()
    for edge in graph.edges:
        if edge.type in (EdgeType.COVALENT, EdgeType.FORMING, EdgeType.BREAKING):
            bonds.add((min(edge.a, edge.b), max(edge.a, edge.b)))
    for first, second in graph.reaction_pairs:
        bonds.add((min(int(first), int(second)), max(int(first), int(second))))
    return bonds


def _split_fragments(natoms: int, metal: int, bonds: set[tuple[int, int]]) -> list[list[int]]:
    """Split non-metal atoms into connected components over candidate bonds."""
    adjacency: dict[int, set[int]] = {i: set() for i in range(natoms) if i != metal}
    for first, second in bonds:
        if first == metal or second == metal or first not in adjacency:
            continue
        if second not in adjacency:
            continue
        adjacency[first].add(second)
        adjacency[second].add(first)
    fragments: list[list[int]] = []
    seen: set[int] = set()
    for start in sorted(adjacency):
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        component = [start]
        while stack:
            for peer in adjacency[stack.pop()]:
                if peer not in seen:
                    seen.add(peer)
                    component.append(peer)
                    stack.append(peer)
        fragments.append(sorted(component))
    fragments.sort(key=lambda frag: frag[0] if frag else natoms)
    return fragments


def _validate_inputs(
    graph: TypedGraph,
    spec: CoordinationSpec,
    reference: np.ndarray,
    placement: Sequence[int],
    shape: str,
    settings: DGSeedSettings,
) -> tuple[np.ndarray, list[int], list[int], int, Any]:
    """Validate inputs fail-closed, returning reference/donors/order/metal/shape."""
    donors: list[int] = []
    for site in spec.binding_sites:
        if site.hapticity != 1 or len(site.atoms) != 1:
            raise DGSeedError(f"binding site {site.id!r} must hold exactly one atom")
        donors.append(int(site.atoms[0]))
    count = settings.count
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise DGSeedError(f"count must be a positive int, got {settings.count!r}")
    timeout = settings.timeout_seconds
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0:
        raise DGSeedError(f"timeout_seconds must be a non-negative int, got {timeout!r}")
    ref = np.asarray(reference, dtype=float)
    if ref.shape != (graph.natoms, 3):
        raise DGSeedError(f"reference shape {ref.shape} needs {(graph.natoms, 3)}")
    if not bool(np.all(np.isfinite(ref))):
        raise DGSeedError("reference geometry must hold only finite values")
    try:
        template = get_shape(shape)
    except (ValueError, KeyError, TypeError) as exc:
        raise DGSeedError(f"unknown coordination shape {shape!r}") from exc
    if template.coordination_number != len(donors):
        raise DGSeedError(f"shape {shape!r} needs {template.coordination_number} sites")
    order = list(placement)
    if len(order) != len(donors):
        raise DGSeedError(f"placement length {len(order)} needs {len(donors)}")
    for value in order:
        if isinstance(value, bool) or not isinstance(value, int):
            raise DGSeedError(f"placement holds non-int vertex {value!r}")
        if not 0 <= value < len(donors):
            raise DGSeedError(f"placement holds out-of-range vertex {value!r}")
    if len(set(order)) != len(order):
        raise DGSeedError("placement holds repeated vertex indices")
    metal = spec.metal_center
    if isinstance(metal, bool) or not isinstance(metal, int):
        raise DGSeedError(f"metal center {metal!r} is not an int")
    if not 0 <= metal < graph.natoms:
        raise DGSeedError(f"metal center {metal!r} is out of range")
    for donor in donors:
        if not 0 <= donor < graph.natoms:
            raise DGSeedError(f"donor atom {donor!r} is out of range")
        if donor == metal:
            raise DGSeedError("metal center must not be a donor atom")
    return ref, donors, [int(value) for value in order], int(metal), template


def _charge_overrides(natoms: int, entries: tuple[tuple[int, int], ...]) -> dict[int, int]:
    """Normalize fragment charge overrides fail-closed."""
    override: dict[int, int] = {}
    try:
        pairs = [(entry[0], entry[1]) for entry in entries]
    except (TypeError, IndexError) as exc:
        raise DGSeedError(f"malformed fragment_charges entry: {exc}") from exc
    for atom_index, charge in pairs:
        if isinstance(atom_index, bool) or not isinstance(atom_index, int):
            raise DGSeedError(f"fragment_charges holds non-int atom {atom_index!r}")
        if not 0 <= atom_index < natoms:
            raise DGSeedError(f"fragment_charges holds atom {atom_index!r} out of range")
        if isinstance(charge, bool) or not isinstance(charge, int):
            raise DGSeedError(f"fragment_charges holds non-int charge {charge!r}")
        if atom_index in override and override[atom_index] != charge:
            raise DGSeedError(f"fragment_charges conflicts on atom {atom_index}")
        override[atom_index] = charge
    return override


def _perceive_fragment(
    frag: list[int],
    bonds: set[tuple[int, int]],
    elements: list[str],
    ref: np.ndarray,
    trials: tuple[int, ...],
    *,
    explicit: bool,
) -> tuple[dict[tuple[int, int], Any], dict[int, int], int]:
    """Perceive bond orders for one fragment over the trial charges.

    Every trial is evaluated (no early exit). A trial is valid when
    ``DetermineBondOrders`` succeeds and no atom keeps radical electrons.
    The winner minimizes ``(charged carbon mass, charged mass - |q|,
    |q|, rank)`` where each mass is the sum of ``abs(formal charge)``
    over carbons (first element) or all atoms (second element), which
    rejects zwitterionic structures in favour of charges on
    heteroatoms. An explicit override bypasses the search with one trial.
    """
    from rdkit import Chem
    from rdkit.Chem import rdDetermineBonds
    from rdkit.Geometry import Point3D

    ranked: list[tuple[tuple[int, int, int, int], Any, int]] = []
    for rank, trial in enumerate(trials):
        probe = Chem.RWMol()
        for atom_index in frag:
            probe.AddAtom(Chem.Atom(elements[atom_index]))
        slots = {atom_index: slot for slot, atom_index in enumerate(frag)}
        for first, second in sorted(bonds):
            if first in slots and second in slots:
                probe.AddBond(slots[first], slots[second], Chem.BondType.SINGLE)
        conformer = Chem.Conformer(len(frag))
        for atom_index in frag:
            conformer.SetAtomPosition(slots[atom_index], Point3D(*ref[atom_index]))
        probe.AddConformer(conformer, assignId=True)
        trial_mol = probe.GetMol()
        try:
            rdDetermineBonds.DetermineBondOrders(trial_mol, charge=int(trial))
        except Exception:
            if explicit:
                raise DGSeedError(
                    f"fragment {frag} failed perception with charge {trial}"
                ) from None
            continue
        if any(a.GetNumRadicalElectrons() != 0 for a in trial_mol.GetAtoms()):
            if explicit:
                raise DGSeedError(f"fragment {frag} leaves radicals with charge {trial}")
            continue
        if explicit:
            ranked.append(((0, 0, 0, 0), trial_mol, int(trial)))
            break
        charged = [a for a in trial_mol.GetAtoms() if a.GetFormalCharge() != 0]
        carbon_mass = sum(abs(a.GetFormalCharge()) for a in charged if a.GetSymbol() == "C")
        total_mass = sum(abs(a.GetFormalCharge()) for a in charged)
        key = (carbon_mass, total_mass - abs(int(trial)), abs(int(trial)), rank)
        ranked.append((key, trial_mol, int(trial)))
    if not ranked:
        raise DGSeedError(f"fragment {frag} admits no workable charge")
    ranked.sort(key=lambda item: item[0])
    _, picked_mol, picked_charge = ranked[0]
    orders: dict[tuple[int, int], Any] = {}
    for bond in picked_mol.GetBonds():
        ends = (frag[bond.GetBeginAtomIdx()], frag[bond.GetEndAtomIdx()])
        orders[(min(ends), max(ends))] = bond.GetBondType()
    charges = {frag[atom.GetIdx()]: atom.GetFormalCharge() for atom in picked_mol.GetAtoms()}
    return orders, charges, picked_charge


def generate_dg_seeds(
    graph: TypedGraph,
    spec: CoordinationSpec,
    reference: np.ndarray,
    placement: Sequence[int],
    shape: str,
    settings: DGSeedSettings = _DEFAULT_SETTINGS,
) -> DGSeedResult:
    """Generate DG starting geometries for one commanded placement.

    Parameters
    ----------
    graph : TypedGraph
        Typed topology authority (bonds read from its edges, never distances).
    spec : CoordinationSpec
        Coordination scope carrying the metal center and binding sites.
    reference : numpy.ndarray
        Trusted ``(n_atoms, 3)`` reference geometry.
    placement : Sequence[int]
        ``placement[i]`` is the shape vertex index of ``binding_sites[i]``.
    shape : str
        Shape name accepted by ``coordination.shapes.get_shape``.
    settings : DGSeedSettings
        Embedding settings.

    Returns
    -------
    DGSeedResult
        Embedded starts (possibly fewer than requested, possibly none).

    Raises
    ------
    DGSeedError
        Raised fail-closed for invalid inputs or RDKit failures.
    """
    ref, donors, order, metal, template = _validate_inputs(
        graph, spec, reference, placement, shape, settings
    )
    requested = int(settings.count)
    override = _charge_overrides(graph.natoms, settings.fragment_charges)
    try:
        from rdkit import Chem
        from rdkit.Chem import rdDistGeom
        from rdkit.DistanceGeometry import DoTriangleSmoothing
        from rdkit.Geometry import Point3D
    except ImportError as exc:
        raise DGSeedError("RDKit is unavailable, cannot generate DG seeds") from exc

    bonds = _candidate_bonds(graph)
    elements = [atom.element for atom in graph.atoms]
    orders: dict[tuple[int, int], Any] = {}
    charges: dict[int, int] = {}
    used: list[tuple[tuple[int, ...], int]] = []
    for frag in _split_fragments(graph.natoms, metal, bonds):
        named = {override[a] for a in frag if a in override}
        if len(named) > 1:
            raise DGSeedError(f"fragment {frag} holds conflicting charge overrides")
        explicit = bool(named)
        trials = (next(iter(named)),) if named else _CHARGE_ORDER
        piece_orders, piece_charges, picked = _perceive_fragment(
            frag, bonds, elements, ref, trials, explicit=explicit
        )
        orders.update(piece_orders)
        charges.update(piece_charges)
        used.append((tuple(frag), picked))

    try:
        builder = Chem.RWMol()
        for index in range(graph.natoms):
            atom = Chem.Atom(elements[index])
            atom.SetFormalCharge(charges.get(index, 0))
            atom.SetNoImplicit(True)
            builder.AddAtom(atom)
        for first, second in sorted(orders):
            builder.AddBond(first, second, orders[(first, second)])
        reference_conf = Chem.Conformer(graph.natoms)
        for index in range(graph.natoms):
            reference_conf.SetAtomPosition(index, Point3D(*ref[index]))
        builder.AddConformer(reference_conf, assignId=True)
        mol = builder.GetMol()
        mol.UpdatePropertyCache(strict=False)
        flags = Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_PROPERTIES
        Chem.SanitizeMol(mol, sanitizeOps=flags)
        Chem.AssignStereochemistryFrom3D(mol)
    except Exception as exc:
        raise DGSeedError(f"Failed to assemble the RDKit molecule: {exc}") from exc

    vertices = np.asarray(template.vertices, dtype=float)
    unit = vertices / np.linalg.norm(vertices, axis=1, keepdims=True)
    targets: dict[int, np.ndarray] = {metal: np.zeros(3)}
    for slot, donor in enumerate(donors):
        radius = float(np.linalg.norm(ref[metal] - ref[donor]))
        targets[donor] = radius * unit[order[slot]]
    neighbors: dict[int, set[int]] = {i: set() for i in range(graph.natoms)}
    for atom in mol.GetAtoms():
        for peer in atom.GetNeighbors():
            neighbors[atom.GetIdx()].add(peer.GetIdx())
    reactions = {(min(int(a), int(b)), max(int(a), int(b))) for a, b in graph.reaction_pairs}
    for edge in graph.edges:
        if edge.type in (EdgeType.FORMING, EdgeType.BREAKING):
            reactions.add((min(edge.a, edge.b), max(edge.a, edge.b)))

    bounds = np.asarray(rdDistGeom.GetMoleculeBoundsMatrix(mol), dtype=float)

    def _set_pair(first: int, second: int, dist: float, tol: float) -> None:
        low, high = (first, second) if first < second else (second, first)
        bounds[low, high] = dist + tol
        bounds[high, low] = max(0.0, dist - tol)

    def _ref_dist(first: int, second: int) -> float:
        return float(np.linalg.norm(ref[first] - ref[second]))

    for first, second in sorted(reactions):
        _set_pair(first, second, _ref_dist(first, second), settings.forming_tolerance)
    for first, second in sorted(reactions):
        for peer in sorted(neighbors[second]):
            if peer != first:
                _set_pair(first, peer, _ref_dist(first, peer), settings.forming_neighbor_tolerance)
        for peer in sorted(neighbors[first]):
            if peer != second:
                dist = _ref_dist(second, peer)
                _set_pair(second, peer, dist, settings.forming_neighbor_tolerance)
    donor_set = set(donors)
    for donor in donors:
        for peer in sorted(neighbors[donor]):
            if peer == metal or peer in donor_set:
                continue
            pair = (min(donor, peer), max(donor, peer))
            tol = settings.donor_neighbor_tolerance
            if pair in reactions:
                tol = settings.forming_neighbor_tolerance
            _set_pair(metal, peer, _ref_dist(metal, peer), tol)
    core = [metal] + list(donors)
    for rank, first in enumerate(core):
        for second in core[rank + 1 :]:
            dist = float(np.linalg.norm(targets[first] - targets[second]))
            _set_pair(first, second, dist, settings.core_tolerance)
    if not DoTriangleSmoothing(bounds):
        raise DGSeedError("triangle smoothing of the DG bounds matrix failed")

    params = rdDistGeom.ETKDGv3()
    params.useRandomCoords = True
    params.embedFragmentsSeparately = False
    params.numThreads = 1
    params.randomSeed = settings.random_seed
    params.maxIterations = settings.max_iterations
    params.useSmallRingTorsions = settings.small_ring_torsions
    params.timeout = settings.timeout_seconds
    params.SetBoundsMat(bounds)
    params.SetCoordMap({i: Point3D(*targets[i]) for i in core})
    try:
        conf_ids = rdDistGeom.EmbedMultipleConfs(mol, requested, params)
    except Exception as exc:
        raise DGSeedError(f"RDKit embedding failed: {exc}") from exc
    starts = tuple(
        np.array(mol.GetConformer(int(conf_id)).GetPositions(), dtype=float)
        for conf_id in conf_ids
        if int(conf_id) >= 0
    )
    return DGSeedResult(coords=starts, requested=requested, fragment_charges=tuple(used))
