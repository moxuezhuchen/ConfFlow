"""Geometry-backed double-bond states, independent of embedding-only chemistry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class DoubleBondState:
    """An indexed substituent/alkene/substituent quartet and its reference side."""

    atoms: tuple[int, int, int, int]
    side: int


def double_bond_side(coords: np.ndarray, atoms: tuple[int, int, int, int]) -> int:
    """Return +1 (same side), -1 (opposite), or 0 (undefined/orthogonal)."""
    a, b, c, d = atoms
    axis = coords[c] - coords[b]
    length = float(np.linalg.norm(axis))
    if length < 1e-10:
        return 0
    axis = axis / length
    left, right = coords[a] - coords[b], coords[d] - coords[c]
    left = left - np.dot(left, axis) * axis
    right = right - np.dot(right, axis) * axis
    norm = float(np.linalg.norm(left) * np.linalg.norm(right))
    if norm < 1e-10:
        return 0
    cosine = float(np.dot(left, right) / norm)
    return 0 if abs(cosine) < 1e-3 else (1 if cosine > 0 else -1)


def double_bond_states(molecule: Any, reference: np.ndarray) -> tuple[DoubleBondState, ...]:
    """Identify genuinely stereogenic double bonds on a stable chemical molecule.

    RDKit decides which bonds have distinguishable substituents; the original atom order and
    reference coordinates define their targets. Aromatic and non-stereogenic bonds are excluded.
    Undefined reference states fail closed rather than silently allowing E/Z enumeration.
    """
    from rdkit import Chem

    mol = Chem.Mol(molecule)
    Chem.AssignStereochemistryFrom3D(mol, replaceExistingTags=True)
    states = []
    for info in Chem.FindPotentialStereo(mol):
        if info.type != Chem.StereoType.Bond_Double:
            continue
        bond = mol.GetBondWithIdx(info.centeredOn)
        if bond.GetBondType() != Chem.BondType.DOUBLE:
            continue
        b, c = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        a = min(n.GetIdx() for n in mol.GetAtomWithIdx(b).GetNeighbors() if n.GetIdx() != c)
        d = min(n.GetIdx() for n in mol.GetAtomWithIdx(c).GetNeighbors() if n.GetIdx() != b)
        atoms = (a, b, c, d)
        side = double_bond_side(reference, atoms)
        if not side:
            raise ValueError(f"double-bond stereo is undefined in reference for bond {b}-{c}")
        states.append(DoubleBondState(atoms, side))
    return tuple(states)


def preserves_double_bonds(coords: np.ndarray, states: tuple[DoubleBondState, ...]) -> bool:
    """Require every reference double-bond state, rejecting collapsed/undefined candidates."""
    return all(double_bond_side(coords, state.atoms) == state.side for state in states)


#: |unit neighbour-tetrahedron volume| (regular tetrahedron ~0.71) below which the reference
#: leaves a configuration undefined; such atoms are reported, not enforced
FLAT_TETRAHEDRON = 0.1


@dataclass(frozen=True, slots=True)
class TetrahedralState:
    """The reference hand of one four-neighbour atom's labelled neighbours.

    ``embeddable`` is False when the atom lies outside its neighbour tetrahedron, a reactive
    geometry ETKDG's centre-volume check cannot reproduce; such states are filtered only.
    """

    center: int
    neighbors: tuple[int, int, int, int]
    sign: int
    embeddable: bool


def tetrahedron_sign(coords: np.ndarray, neighbors: tuple[int, int, int, int]) -> int:
    """Return the hand (+1/-1) of four labelled neighbours, 0 when they are nearly coplanar."""
    first = coords[neighbors[0]]
    edges = [coords[i] - first for i in neighbors[1:]]
    norms = [float(np.linalg.norm(e)) for e in edges]
    if min(norms) < 1e-10:
        return 0
    unit = [e / n for e, n in zip(edges, norms)]
    volume = float(np.dot(unit[0], np.cross(unit[1], unit[2])))
    return 0 if abs(volume) < FLAT_TETRAHEDRON else (1 if volume > 0 else -1)


def tetrahedral_states(
    molecule: Any, reference: np.ndarray, reaction_pairs: Any = (), metal: int | None = None
) -> tuple[tuple[TetrahedralState, ...], tuple[int, ...]]:
    """Every configuration a four-neighbour atom carries in the reference, on the TS graph.

    The graph is the molecule's bonds plus the reaction pairs. No stereo perception is used:
    without breaking a bond, no conformational change can invert the labelled neighbours of a
    four-neighbour atom, so holding all of them loses no conformer. Atoms with two
    interchangeable terminal neighbours (CH2, CH3, CF2...) carry no configuration and are
    skipped. Returns the states and the atoms skipped as too flat to define one.
    """
    ref = np.asarray(reference, dtype=float)
    neighbors: dict[int, set[int]] = {
        atom.GetIdx(): {n.GetIdx() for n in atom.GetNeighbors()} for atom in molecule.GetAtoms()
    }
    for a, b in reaction_pairs:
        neighbors[int(a)].add(int(b))
        neighbors[int(b)].add(int(a))
    symbols = [atom.GetSymbol() for atom in molecule.GetAtoms()]
    states, flat = [], []
    for center, peers in sorted(neighbors.items()):
        if center == metal or len(peers) != 4:
            continue
        leaves = [symbols[i] for i in peers if len(neighbors[i]) == 1]
        if len(leaves) != len(set(leaves)):
            continue
        quad = tuple(sorted(peers))
        sign = tetrahedron_sign(ref, quad)  # type: ignore[arg-type]
        if not sign:
            flat.append(center)
            continue
        matrix = np.vstack((ref[list(quad)].T, np.ones(4)))
        try:
            weights = np.linalg.solve(matrix, np.append(ref[center], 1.0))
            embeddable = bool(np.min(weights) > 0.0)
        except np.linalg.LinAlgError:
            embeddable = False
        states.append(TetrahedralState(center, quad, sign, embeddable))  # type: ignore[arg-type]
    return tuple(states), tuple(flat)


def preserves_tetrahedra(coords: np.ndarray, states: tuple[TetrahedralState, ...]) -> bool:
    """Require the reference hand of every enforced four-neighbour atom."""
    return all(tetrahedron_sign(coords, state.neighbors) == state.sign for state in states)


def tag_tetrahedra(
    molecule: Any, states: tuple[TetrahedralState, ...], reference: np.ndarray
) -> Any:
    """Return a copy of ``molecule`` whose embeddable states carry their reference chiral tags.

    The tags are read from the reference geometry on an all-single-bond copy, so the neighbour
    order is the molecule's own and no stereo perception can drop a centre.
    """
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    wanted = [state.center for state in states if state.embeddable]
    if not wanted:
        return molecule
    for state in states:
        bonded = {n.GetIdx() for n in molecule.GetAtomWithIdx(state.center).GetNeighbors()}
        if state.embeddable and bonded != set(state.neighbors):
            # a reaction pair missing from the molecule would silently leave its centre untagged
            raise ValueError(f"atom {state.center} is not bonded to its state neighbours")
    probe = Chem.RWMol(molecule)
    for bond in probe.GetBonds():
        bond.SetBondType(Chem.BondType.SINGLE)
    probe.RemoveAllConformers()
    conformer = Chem.Conformer(probe.GetNumAtoms())
    for index, xyz in enumerate(np.asarray(reference, dtype=float)):
        conformer.SetAtomPosition(index, Point3D(*map(float, xyz)))
    probe.AddConformer(conformer, assignId=True)
    probe.UpdatePropertyCache(strict=False)
    Chem.AssignAtomChiralTagsFromStructure(probe, replaceExistingTags=True)
    tagged = Chem.RWMol(molecule)
    hands = (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW)
    for center in wanted:
        tag = probe.GetAtomWithIdx(center).GetChiralTag()
        if tag in hands:
            tagged.GetAtomWithIdx(center).SetChiralTag(tag)
    result = tagged.GetMol()
    result.UpdatePropertyCache(strict=False)
    return result


#: |sin| of the reference dihedral at or above which a declared torsion is a "hand" state
TORSION_HAND_SIN = 0.5
#: squared-length floor below which atoms are treated as collinear (dihedral undefined)
_TORSION_EPS = 1e-10


@dataclass(frozen=True, slots=True)
class TorsionState:
    """A declared dihedral (four atoms in the given order) and the reference side it must keep.

    ``kind`` is ``"hand"`` (sign of sin, chirality-type: helix, atropisomer, gauche +/-) when the
    reference has ``|sin| >= TORSION_HAND_SIN``, else ``"side"`` (sign of cos, cis/trans-type:
    amide, ester s-cis/s-trans). ``theta0`` is the reference dihedral in degrees.
    """

    atoms: tuple[int, int, int, int]
    kind: str  # "hand" or "side"
    sign: int
    theta0: float


def torsion_angle(coords: np.ndarray, atoms: tuple[int, int, int, int]) -> float:
    """Return the dihedral of four atoms in degrees, in (-180, 180].

    Raises ``ValueError`` when the dihedral is undefined (coincident or collinear atoms).
    """
    a, b, c, d = atoms
    x = np.asarray(coords, dtype=float)
    b0 = x[a] - x[b]
    b1 = x[c] - x[b]
    b2 = x[d] - x[c]
    length = float(np.linalg.norm(b1))
    if length < _TORSION_EPS:
        raise ValueError(f"dihedral {tuple(atoms)} is undefined: atoms {b} and {c} coincide")
    b1 = b1 / length
    v = b0 - np.dot(b0, b1) * b1
    w = b2 - np.dot(b2, b1) * b1
    if float(np.linalg.norm(v)) < _TORSION_EPS or float(np.linalg.norm(w)) < _TORSION_EPS:
        raise ValueError(f"dihedral {tuple(atoms)} is undefined: atoms are collinear")
    angle = float(np.degrees(np.arctan2(np.dot(np.cross(b1, v), w), np.dot(v, w))))
    return angle + 360.0 if angle <= -180.0 else angle


def torsion_states(reference: np.ndarray, quads: Any) -> tuple[TorsionState, ...]:
    """Classify each declared quad by its reference dihedral (raises on undefined geometry)."""
    ref = np.asarray(reference, dtype=float)
    states = []
    for quad in quads:
        atoms = tuple(int(i) for i in quad)
        theta0 = torsion_angle(ref, atoms)  # type: ignore[arg-type]
        radians = np.radians(theta0)
        if abs(np.sin(radians)) >= TORSION_HAND_SIN:
            kind, sign = "hand", 1 if np.sin(radians) > 0 else -1
        else:
            kind, sign = "side", 1 if np.cos(radians) > 0 else -1
        states.append(TorsionState(atoms, kind, sign, float(theta0)))  # type: ignore[arg-type]
    return tuple(states)


def torsion_state_kept(coords: np.ndarray, state: TorsionState) -> bool:
    """Return whether a candidate keeps the declared side; undefined or on-boundary fails."""
    try:
        angle = torsion_angle(coords, state.atoms)
    except ValueError:
        return False
    radians = np.radians(angle)
    value = float(np.sin(radians)) if state.kind == "hand" else float(np.cos(radians))
    if value == 0.0:
        return False
    return (1 if value > 0.0 else -1) == state.sign


def preserves_torsions(coords: np.ndarray, states: tuple[TorsionState, ...]) -> bool:
    """Require every declared torsion state (vacuously true when none are declared)."""
    return all(torsion_state_kept(coords, state) for state in states)
