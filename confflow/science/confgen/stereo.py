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
