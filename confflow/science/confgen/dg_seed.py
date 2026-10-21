"""Distance-geometry seed starts for one commanded coordination class.

Builds RDKit ETKDGv3 starting geometries biased toward a commanded
shape-class placement from the typed graph and a trusted reference geometry.
The shape is either a registered ideal polyhedron or ``"input"``, whose template
is the reference's own metal-donor core (identity placement only).
Embedding returns whatever RDKit realizes (possibly fewer than requested).

The returned starts are starts only: they are not audited, not labeled
REALIZED, and carry no validity claim.

Known limitation: redox-ambiguous ligands can be misperceived (catecholate
is perceived as charge 0 o-quinone, nitrate as charge -3); use the
``fragment_charges`` override for such fragments.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

import numpy as np

from .coordination.shapes import get_shape
from .graph import INPUT_SHAPE, CoordinationSpec, EdgeType, TypedGraph

__all__ = [
    "DGSeedError",
    "DGSeedResult",
    "DGSeedSettings",
    "generate_dg_seeds",
    "stable_chemistry_molecule",
]

#: Charge trial order for automatic fragment bond perception.
_CHARGE_ORDER: tuple[int, ...] = (0, -1, 1, -2, 2, -3, 3, -4)

#: Bounds slack (Angstrom) that admits the input geometry for ``INPUT_SHAPE``. With the metal
#: removed, RDKit applies free-ligand priors (s-trans conjugated chelates such as bipyridine,
#: van der Waals contacts between different ligands, short X-ray C-H) that can exclude the real
#: structure and make the fixed input core unsmoothable; the input is a real structure, so the
#: bounds must contain it.
_INPUT_ADMIT_TOL: float = 0.25


@dataclass(frozen=True, slots=True, eq=False)
class _InputTemplate:
    """Template taken from the input's own metal-donor core (``shape="input"``)."""

    vertices: np.ndarray
    coordination_number: int


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
        RDKit ETKDGv3 ``useSmallRingTorsions`` switch. On concentrates on
        low-energy ring forms and suppresses others; off gives broader ring
        coverage with fewer low-energy starts.
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
        Maximum time in seconds per start (one conformer per RDKit call;
        0 means no limit). A timed-out start is skipped and the other starts
        are kept; a timeout never raises.
    first_start : int
        Index of the first start.  Start ``k`` always gets the seed derived for ``k``, so
        consecutive slices of one request reproduce that request exactly.
    free_arrangement : bool
        Keep the metal-donor distances but drop the donor-donor core bounds and the core
        coordinate map, so the start may realise any donor arrangement (template unused).
        Each such start is then re-embedded on its own core with the donors put back at
        their metal-donor distances (kept as embedded if that fails).
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
    first_start: int = 0
    freeze_core: tuple[int, ...] = ()
    free_arrangement: bool = False


#: Shared default settings (frozen, safe to share across calls).
_DEFAULT_SETTINGS = DGSeedSettings()


@dataclass(frozen=True, slots=True)
class DGSeedResult:
    """DG seed embedding outcome (starts only, no validity claim)."""

    coords: tuple[np.ndarray, ...]
    requested: int
    fragment_charges: tuple[tuple[tuple[int, ...], int], ...]
    stereo_centers: tuple[int, ...] = ()
    chemistry_mode: str = "perceived_stable_graph"
    chemistry_note: str = ""
    embedding_relaxed_centers: tuple[int, ...] = ()

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
            "stereo_centers": [int(c) for c in self.stereo_centers],
            "chemistry_mode": self.chemistry_mode,
            "chemistry_note": self.chemistry_note,
            "embedding_relaxed_centers": list(self.embedding_relaxed_centers),
        }


def _candidate_bonds(graph: TypedGraph) -> set[tuple[int, int]]:
    """Return only stable covalent connectivity for valence/stereo perception."""
    bonds: set[tuple[int, int]] = set()
    for edge in graph.edges:
        if edge.type is EdgeType.COVALENT:
            bonds.add((min(edge.a, edge.b), max(edge.a, edge.b)))
    return bonds


def _reaction_pairs(graph: TypedGraph) -> set[tuple[int, int]]:
    """Collect reaction geometry relations independently of stable connectivity."""
    pairs = {(min(int(a), int(b)), max(int(a), int(b))) for a, b in graph.reaction_pairs}
    for edge in graph.edges:
        if edge.type in (EdgeType.FORMING, EdgeType.BREAKING):
            pairs.add((min(edge.a, edge.b), max(edge.a, edge.b)))
    return pairs


def embedding_molecule(
    graph: TypedGraph, molecule: Any, reference: np.ndarray, fixed: Sequence[int] = ()
) -> tuple[Any, tuple[int, ...]]:
    """Copy stable chemistry for ETKDG, preserving all non-conflicting stereo constraints.

    Reaction ZERO edges are embedding hints only. A reactive tetrahedral center outside its
    neighbor tetrahedron cannot satisfy ETKDG's ordinary center-volume condition. Its atom tag
    is redundant only when the center and all four stable neighbors are exactly pinned; remove
    that tag on this copy alone and keep the independent output stereo audit.
    """
    from rdkit import Chem

    builder = Chem.RWMol(molecule)
    reactions = _reaction_pairs(graph)
    reactive = {i for pair in reactions for i in pair}
    pinned = set(fixed)
    relaxed = []
    for atom in builder.GetAtoms():
        center = atom.GetIdx()
        peers = [n.GetIdx() for n in atom.GetNeighbors()]
        if (
            center not in reactive
            or center not in pinned
            or len(peers) != 4
            or not set(peers).issubset(pinned)
            or atom.GetChiralTag()
            not in (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW)
        ):
            continue
        matrix = np.vstack((np.asarray(reference)[peers].T, np.ones(4)))
        try:
            weights = np.linalg.solve(matrix, np.append(reference[center], 1.0))
            nonstandard = bool(np.min(weights) < -1e-6)
        except np.linalg.LinAlgError:
            nonstandard = True
        if nonstandard:
            atom.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
            relaxed.append(center)
    for first, second in sorted(reactions):
        if builder.GetBondBetweenAtoms(first, second) is None:
            builder.AddBond(first, second, Chem.BondType.ZERO)
    result = builder.GetMol()
    result.UpdatePropertyCache(strict=False)
    # every four-neighbour atom of the TS graph keeps its reference hand, so DG cannot
    # generate the other face of a reacting centre or a relabelled copy of a symmetric group
    from .stereo import tag_tetrahedra, tetrahedral_states

    states, _flat = tetrahedral_states(result, reference, (), graph.metal_center)
    result = tag_tetrahedra(result, states, reference)
    Chem.FastFindRings(result)
    return result, tuple(relaxed)


def _split_fragments(
    natoms: int, metal: int | None, bonds: set[tuple[int, int]]
) -> list[list[int]]:
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
    spec: CoordinationSpec | None,
    reference: np.ndarray,
    placement: Sequence[int],
    shape: str,
    settings: DGSeedSettings,
) -> tuple[np.ndarray, list[int], list[int], int | None, Any]:
    """Validate inputs fail-closed, returning reference/donors/order/metal/template.

    ``shape`` is either a registered ideal polyhedron or ``INPUT_SHAPE``; the latter
    needs the identity placement and takes its template from ``reference``.
    """
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
    fixed = tuple(settings.freeze_core)
    if len(set(fixed)) != len(fixed):
        raise DGSeedError("freeze_core contains duplicate atom indices")
    for index in fixed:
        if isinstance(index, bool) or not isinstance(index, int):
            raise DGSeedError(f"freeze_core index {index!r} is not an integer")
        if not 0 <= index < graph.natoms:
            raise DGSeedError(f"freeze_core index {index!r} is out of range")
    if spec is None:
        if graph.metal_center is not None:
            raise DGSeedError("graph holds a metal center but no coordination spec was given")
        if list(placement):
            raise DGSeedError("placement must be empty when no coordination spec is given")
        return ref, [], [], None, None
    donors: list[int] = []
    for site in spec.binding_sites:
        if site.hapticity != 1 or len(site.atoms) != 1:
            raise DGSeedError(f"binding site {site.id!r} must hold exactly one atom")
        donors.append(int(site.atoms[0]))
    template: Any = None
    if shape != INPUT_SHAPE:
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
    if shape == INPUT_SHAPE and order != list(range(len(donors))):
        raise DGSeedError("shape 'input' takes the identity placement")
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
    if shape == INPUT_SHAPE:
        # indices are validated above, so the template reads only real atoms
        template = _InputTemplate(
            vertices=np.asarray([ref[donor] - ref[metal] for donor in donors], dtype=float),
            coordination_number=len(donors),
        )
    return ref, donors, [int(value) for value in order], int(metal), template


def _charge_overrides(natoms: int, entries: Sequence[tuple[int, int]]) -> dict[int, int]:
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


def _embed_per_start(
    mol: Any,
    requested: int,
    params: Any,
    first: int = 0,
    refine: Callable[[np.ndarray, int], np.ndarray] | None = None,
) -> tuple[np.ndarray, ...]:
    """Embed the starts one conformer per RDKit call, each with its own seed.

    ``params.timeout`` bounds a single call, so a timed-out, unembeddable, or
    raising start is skipped and the others are kept. Each start is copied out
    of the molecule immediately, before the next call adds a conformer, and passed
    through ``refine(start, start_index)`` when given. Only when every attempt
    raised is the failure reported.
    """
    from rdkit.Chem import rdDistGeom

    base = int(params.randomSeed)
    found: list[np.ndarray] = []
    raised: list[Exception] = []
    for offset in range(first, first + requested):
        params.randomSeed = (base * (offset + 1) + offset) % (2**31 - 1)
        tick = time.monotonic()
        try:
            conf_id = int(rdDistGeom.EmbedMolecule(mol, params))
            spent = time.monotonic() - tick
            timed_out = params.timeout > 0 and spent >= 0.8 * params.timeout
            if conf_id < 0 and params.useBasicKnowledge and not timed_out:
                # a donor perceived with the wrong hybridisation once the metal is stripped
                # (e.g. a carbene carbon read as sp) defeats the knowledge terms: retry without
                params.useBasicKnowledge = False
                try:
                    conf_id = int(rdDistGeom.EmbedMolecule(mol, params))
                finally:
                    params.useBasicKnowledge = True
        except Exception as exc:
            raised.append(exc)
            continue
        if conf_id >= 0:
            start = np.array(mol.GetConformer(conf_id).GetPositions(), dtype=float)
            found.append(start if refine is None else refine(start, offset))
    if requested and len(raised) == requested:
        raise DGSeedError(f"RDKit embedding failed: {raised[-1]}") from raised[-1]
    return tuple(found)


def _declared_bond_type(Chem: Any, order: float) -> Any:
    """Translate supported explicit stable bond orders to RDKit bond types."""
    mapping = {
        1.0: Chem.BondType.SINGLE,
        1.5: Chem.BondType.AROMATIC,
        2.0: Chem.BondType.DOUBLE,
        3.0: Chem.BondType.TRIPLE,
    }
    try:
        return mapping[float(order)]
    except KeyError as exc:
        raise DGSeedError(
            f"stable graph has unsupported explicit bond order {order!r}; "
            "DG supports 1, 1.5, 2, and 3"
        ) from exc


def _declared_stable_chemistry(
    graph: TypedGraph,
    bonds: set[tuple[int, int]],
    elements: list[str],
    ref: np.ndarray,
    Chem: Any,
    Point3D: Any,
    override: dict[int, int],
) -> (
    tuple[
        Any,
        dict[tuple[int, int], Any],
        dict[int, int],
        tuple[tuple[tuple[int, ...], int], ...],
        str,
    ]
    | None
):
    """Build RDKit chemistry only from complete, explicit stable-graph declarations.

    Partial bond-order or formal-charge annotations fail closed. With no such annotations the
    legacy geometry perception path remains available, but its mode is reported to the caller.
    """
    stable_edges = [edge for edge in graph.edges if edge.type is EdgeType.COVALENT]
    declared_charges = [atom.formal_charge for atom in graph.atoms]
    has_atom_charges = any(value is not None for value in declared_charges)
    complete_atom_charges = all(value is not None for value in declared_charges)
    if not has_atom_charges:
        return None
    if not complete_atom_charges:
        raise DGSeedError(
            "stable chemistry declares formal charges for only part of the atom set; "
            "provide a formal charge for every atom or remove the partial declarations"
        )

    missing: list[str] = []
    if any(atom.formal_charge is None for atom in graph.atoms):
        missing.append("formal_charge for every atom")
    if any(edge.bond_order is None for edge in stable_edges):
        missing.append("bond_order for every stable covalent edge")
    if graph.total_charge is None:
        missing.append("total_charge")
    if graph.multiplicity is None:
        missing.append("multiplicity")
    if missing:
        raise DGSeedError(
            "stable chemistry declarations are incomplete; provide " + ", ".join(missing)
        )
    if graph.multiplicity != 1:
        raise DGSeedError(
            f"explicit stable-graph DG chemistry supports singlet multiplicity 1, "
            f"got {graph.multiplicity}"
        )
    charges = {atom.index: cast(int, atom.formal_charge) for atom in graph.atoms}
    if sum(charges.values()) != graph.total_charge:
        raise DGSeedError(
            "stable-graph formal charges sum to "
            f"{sum(charges.values())}, not declared total_charge {graph.total_charge}"
        )
    orders = {
        edge.pair: _declared_bond_type(Chem, cast(float, edge.bond_order)) for edge in stable_edges
    }
    for fragment in _split_fragments(graph.natoms, graph.metal_center, bonds):
        named = {override[index] for index in fragment if index in override}
        fragment_charge = sum(charges[index] for index in fragment)
        if named and (len(named) != 1 or next(iter(named)) != fragment_charge):
            raise DGSeedError(
                f"fragment {fragment} charge override conflicts with explicit formal charges"
            )

    builder = Chem.RWMol()
    for index, element in enumerate(elements):
        atom = Chem.Atom(element)
        atom.SetFormalCharge(charges[index])
        atom.SetNoImplicit(True)
        builder.AddAtom(atom)
    for pair, bond_type in sorted(orders.items()):
        builder.AddBond(pair[0], pair[1], bond_type)
        if bond_type == Chem.BondType.AROMATIC:
            builder.GetAtomWithIdx(pair[0]).SetIsAromatic(True)
            builder.GetAtomWithIdx(pair[1]).SetIsAromatic(True)
            builder.GetBondBetweenAtoms(*pair).SetIsAromatic(True)
    conformer = Chem.Conformer(graph.natoms)
    for index in range(graph.natoms):
        conformer.SetAtomPosition(index, Point3D(*ref[index]))
    builder.AddConformer(conformer, assignId=True)
    mol = builder.GetMol()
    try:
        mol.UpdatePropertyCache(strict=True)
        flags = Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_PROPERTIES
        Chem.SanitizeMol(mol, sanitizeOps=flags)
    except Exception as exc:
        raise DGSeedError(f"explicit stable chemistry is not RDKit-sanitizable: {exc}") from exc
    radicals = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetNumRadicalElectrons() != 0]
    if radicals:
        raise DGSeedError(
            "explicit stable chemistry leaves unassigned radical electrons on atoms "
            f"{radicals}; provide a supported closed-shell singlet graph"
        )
    Chem.AssignStereochemistryFrom3D(mol)
    used = tuple(
        (tuple(fragment), sum(charges[index] for index in fragment))
        for fragment in _split_fragments(graph.natoms, graph.metal_center, bonds)
    )
    return mol, orders, charges, used, "explicit_stable_graph"


def stable_chemistry_molecule(
    graph: TypedGraph,
    reference: np.ndarray,
    fragment_charges: Sequence[tuple[int, int]] = (),
) -> tuple[Any, tuple[tuple[tuple[int, ...], int], ...], str, str]:
    """Build the shared stable-graph molecule used for DG and stereo perception.

    Complete formal-charge, stable bond-order, total-charge, and multiplicity declarations are
    authoritative. Older inputs without per-atom charges retain geometry-based perception and
    return an explicit note describing the inferred path.
    """
    ref = np.asarray(reference, dtype=float)
    if ref.shape != (graph.natoms, 3) or not bool(np.all(np.isfinite(ref))):
        raise DGSeedError("reference geometry must hold one finite coordinate triple per atom")
    try:
        from rdkit import Chem
        from rdkit.Geometry import Point3D
    except ImportError as exc:
        raise DGSeedError("RDKit is unavailable, cannot perceive stable chemistry") from exc

    bonds = _candidate_bonds(graph)
    elements = [atom.element for atom in graph.atoms]
    override = _charge_overrides(graph.natoms, fragment_charges)
    declared = _declared_stable_chemistry(graph, bonds, elements, ref, Chem, Point3D, override)
    if declared is not None:
        mol, _orders, _charges, declared_used, mode = declared
        return mol, declared_used, mode, ""

    orders: dict[tuple[int, int], Any] = {}
    charges: dict[int, int] = {}
    used: list[tuple[tuple[int, ...], int]] = []
    missing: list[str] = []
    if any(edge.type is EdgeType.COVALENT and edge.bond_order is None for edge in graph.edges):
        missing.append("stable covalent bond orders")
    if any(atom.formal_charge is None for atom in graph.atoms):
        missing.append(
            "complete per-atom formal charges (bond-order metadata alone is insufficient)"
        )
    if graph.total_charge is None:
        missing.append("total charge")
    if graph.multiplicity is None:
        missing.append("multiplicity")
    note = (
        "geometry-based bond-order perception used on the stable covalent graph; missing "
        + ", ".join(missing)
    )
    for frag in _split_fragments(graph.natoms, graph.metal_center, bonds):
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
        for index, element in enumerate(elements):
            atom = Chem.Atom(element)
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
    except Exception as exc:
        raise DGSeedError(f"Failed to assemble the RDKit molecule: {exc}") from exc
    return mol, tuple(used), "perceived_stable_graph", note


def generate_dg_seeds(
    graph: TypedGraph,
    spec: CoordinationSpec | None,
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
    spec : CoordinationSpec or None
        Coordination scope carrying the metal center and binding sites. None
        means metal-free: ``placement`` must be empty, ``shape`` is ignored,
        fragments are perceived over all atoms, and only reaction-pair bounds
        apply (no polyhedron bounds, no coordinate map).
    reference : numpy.ndarray
        Trusted ``(n_atoms, 3)`` reference geometry.
    placement : Sequence[int]
        ``placement[i]`` is the shape vertex index of ``binding_sites[i]``.
    shape : str
        Shape name accepted by ``coordination.shapes.get_shape``, or ``"input"``
        (template = the reference's own metal-donor core; identity placement only).
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
    try:
        from rdkit import Chem
        from rdkit.Chem import rdDistGeom
        from rdkit.DistanceGeometry import DoTriangleSmoothing
        from rdkit.Geometry import Point3D
    except ImportError as exc:
        raise DGSeedError("RDKit is unavailable, cannot generate DG seeds") from exc

    mol, used, chemistry_mode, chemistry_note = stable_chemistry_molecule(
        graph, ref, settings.fragment_charges
    )

    Chem.AssignStereochemistryFrom3D(mol)
    stereo = tuple(
        sorted(
            index
            for index in range(graph.natoms)
            if mol.GetAtomWithIdx(index).GetChiralTag()
            in (
                Chem.rdchem.ChiralType.CHI_TETRAHEDRAL_CW,
                Chem.rdchem.ChiralType.CHI_TETRAHEDRAL_CCW,
            )
        )
    )

    targets: dict[int, np.ndarray] = {}
    if metal is not None and template is not None:
        vertices = np.asarray(template.vertices, dtype=float)
        unit = vertices / np.linalg.norm(vertices, axis=1, keepdims=True)
        targets[metal] = ref[metal].copy()
        for slot, donor in enumerate(donors):
            radius = float(np.linalg.norm(ref[metal] - ref[donor]))
            targets[donor] = targets[metal] + radius * unit[order[slot]]
    reactions = _reaction_pairs(graph)
    geometry_ref = ref.copy()
    rotation = np.eye(3)
    if targets:
        core_indices = np.asarray(sorted(targets), dtype=int)
        source = ref[core_indices]
        destination = np.asarray([targets[int(index)] for index in core_indices])
        source_center = source.mean(axis=0)
        destination_center = destination.mean(axis=0)
        u, _singular, vt = np.linalg.svd(
            (source - source_center).T @ (destination - destination_center)
        )
        correction = np.eye(3)
        correction[2, 2] = 1.0 if np.linalg.det(u @ vt) >= 0 else -1.0
        rotation = u @ correction @ vt
        geometry_ref = (ref - source_center) @ rotation + destination_center
        for index, xyz in targets.items():
            geometry_ref[index] = xyz
    neighbors: dict[int, set[int]] = {i: set() for i in range(graph.natoms)}
    for atom in mol.GetAtoms():
        for peer in atom.GetNeighbors():
            neighbors[atom.GetIdx()].add(peer.GetIdx())
    bounds = np.asarray(rdDistGeom.GetMoleculeBoundsMatrix(mol), dtype=float)
    if shape == INPUT_SHAPE and metal is not None:
        for peer in range(graph.natoms):
            if peer == metal:
                continue
            low, high = (metal, peer) if metal < peer else (peer, metal)
            bounds[low, high] = 1000.0
            bounds[high, low] = 0.0
        dists = np.linalg.norm(ref[:, None, :] - ref[None, :, :], axis=-1)
        rows, cols = np.triu_indices(graph.natoms, k=1)
        upper = dists[rows, cols] + _INPUT_ADMIT_TOL
        lower = np.maximum(0.0, dists[rows, cols] - _INPUT_ADMIT_TOL)
        bounds[rows, cols] = np.maximum(bounds[rows, cols], upper)
        bounds[cols, rows] = np.minimum(bounds[cols, rows], lower)

    def _set_pair(first: int, second: int, dist: float, tol: float) -> None:
        low, high = (first, second) if first < second else (second, first)
        bounds[low, high] = dist + tol
        bounds[high, low] = max(0.0, dist - tol)

    def _ref_dist(first: int, second: int) -> float:
        return float(np.linalg.norm(geometry_ref[first] - geometry_ref[second]))

    for first, second in sorted(reactions):
        _set_pair(
            first,
            second,
            float(np.linalg.norm(ref[first] - ref[second])),
            settings.forming_tolerance,
        )
    for first, second in sorted(reactions):
        for peer in sorted(neighbors[second]):
            if peer != first:
                _set_pair(
                    first,
                    peer,
                    float(np.linalg.norm(ref[first] - ref[peer])),
                    settings.forming_neighbor_tolerance,
                )
        for peer in sorted(neighbors[first]):
            if peer != second:
                dist = float(np.linalg.norm(ref[second] - ref[peer]))
                _set_pair(second, peer, dist, settings.forming_neighbor_tolerance)
    donor_set = set(donors)
    for donor in donors:
        if metal is None:
            break
        for peer in sorted(neighbors[donor]):
            if peer == metal or peer in donor_set:
                continue
            pair = (min(donor, peer), max(donor, peer))
            tol = settings.donor_neighbor_tolerance
            if pair in reactions:
                tol = settings.forming_neighbor_tolerance
            _set_pair(metal, peer, _ref_dist(metal, peer), tol)
    core = [] if metal is None else [metal] + list(donors)
    free = settings.free_arrangement and metal is not None
    base_bounds = bounds.copy()

    def _pin_core(matrix: np.ndarray, pos: dict[int, np.ndarray], metal_only: bool) -> None:
        for rank, first in enumerate(core):
            for second in core[rank + 1 :]:
                if metal_only and first != metal:
                    continue  # a free start keeps only the metal-donor distances
                dist = float(np.linalg.norm(pos[first] - pos[second]))
                low, high = (first, second) if first < second else (second, first)
                matrix[low, high] = dist + settings.core_tolerance
                matrix[high, low] = max(0.0, dist - settings.core_tolerance)

    def _params(matrix: np.ndarray, seed: int) -> Any:
        made = rdDistGeom.ETKDGv3()
        made.useRandomCoords = True
        made.embedFragmentsSeparately = False
        made.numThreads = 1
        made.randomSeed = seed
        made.maxIterations = settings.max_iterations
        made.useSmallRingTorsions = settings.small_ring_torsions
        made.timeout = settings.timeout_seconds
        made.SetBoundsMat(matrix)
        return made

    if metal is not None:
        _pin_core(bounds, targets, free)
    if not DoTriangleSmoothing(bounds):
        raise DGSeedError("triangle smoothing of the DG bounds matrix failed")

    params = _params(bounds, settings.random_seed)
    coord_map = {} if free else {i: np.asarray(xyz, dtype=float) for i, xyz in targets.items()}
    for index in settings.freeze_core:
        point = ref[index]
        if index in coord_map and not np.allclose(coord_map[index], point, rtol=0.0, atol=1.0e-4):
            raise DGSeedError(
                f"freeze_core atom {index} conflicts with the commanded coordination placement"
            )
        coord_map[index] = point
    if coord_map:
        params.SetCoordMap({i: Point3D(*map(float, xyz)) for i, xyz in coord_map.items()})
    embedding_mol, relaxed_centers = embedding_molecule(graph, mol, ref, settings.freeze_core)

    def _snap(start: np.ndarray, index: int) -> np.ndarray:
        # The free embedding only roughly meets the metal-donor bounds (no force field for
        # the metal), which costs the relaxation ~70 % more steps. Keep the arrangement it
        # found, put each donor back at its target distance, and re-embed start ``index`` on
        # that core the way a fixed start is embedded; keep the free start if that fails.
        assert metal is not None
        pos = {metal: start[metal]}
        for donor in donors:
            way = start[donor] - start[metal]
            radius = float(np.linalg.norm(targets[donor] - targets[metal]))
            pos[donor] = start[metal] + radius * way / np.linalg.norm(way)
        matrix = base_bounds.copy()
        _pin_core(matrix, pos, False)
        if not DoTriangleSmoothing(matrix):
            return start
        pinned = _params(matrix, settings.random_seed)
        pinned.SetCoordMap({i: Point3D(*map(float, xyz)) for i, xyz in pos.items()})
        try:
            again = _embed_per_start(embedding_mol, 1, pinned, index)
        except DGSeedError:
            return start
        return again[0] if again else start

    starts = _embed_per_start(
        embedding_mol, requested, params, int(settings.first_start), _snap if free else None
    )
    from .stereo import (
        double_bond_states,
        preserves_double_bonds,
        preserves_tetrahedra,
        tetrahedral_states,
    )

    try:
        states = double_bond_states(mol, ref)
    except ValueError as exc:
        raise DGSeedError(str(exc)) from exc
    tet, _flat = tetrahedral_states(embedding_mol, ref, (), graph.metal_center)
    starts = tuple(
        x for x in starts if preserves_double_bonds(x, states) and preserves_tetrahedra(x, tet)
    )
    return DGSeedResult(
        coords=starts,
        requested=requested,
        fragment_charges=tuple(used),
        stereo_centers=stereo,
        chemistry_mode=chemistry_mode,
        chemistry_note=chemistry_note,
        embedding_relaxed_centers=relaxed_centers,
    )
