"""Symmetry-aware comparison and de-duplication of arbitrary structures (``confgen.compare``)."""

from __future__ import annotations

import numpy as np
import pytest

from confflow.science.confgen import compare
from confflow.science.confgen.graph import EdgeType
from confflow.science.confgen.rotors import dihedral
from tests.v4._dg_search_helpers import _embed


def _tbu_benzene():
    """tert-butylbenzene: elements, coordinates and the three methyl groups (C plus its H)."""
    mol, els, xyz = _embed("CC(C)(C)c1ccccc1")
    quaternary = next(a.GetIdx() for a in mol.GetAtoms() if a.GetSymbol() == "C" and a.GetDegree() == 4
                      and all(n.GetSymbol() == "C" for n in a.GetNeighbors()))  # fmt: skip
    methyls = []
    for nb in mol.GetAtomWithIdx(quaternary).GetNeighbors():
        hs = [h.GetIdx() for h in nb.GetNeighbors() if h.GetSymbol() == "H"]
        if len(hs) == 3:
            methyls.append((nb.GetIdx(), hs))
    assert len(methyls) == 3
    return els, xyz, methyls


def _swap_methyls(xyz: np.ndarray, methyls) -> np.ndarray:
    """Relabel: methyl 0 takes methyl 1's coordinates and vice versa (same structure)."""
    out = xyz.copy()
    (c0, h0), (c1, h1) = methyls[0], methyls[1]
    for a, b in [(c0, c1), *zip(h0, h1)]:
        out[[a, b]] = out[[b, a]]
    return out


def test_relabelled_tbu_is_the_same_structure_but_far_by_plain_rmsd() -> None:
    els, xyz, methyls = _tbu_benzene()
    other = _swap_methyls(xyz, methyls)
    idn = compare.structure_identity(els, xyz)
    sym, plain = compare.pair_rmsd(idn, other, xyz)
    assert sym < 1e-6
    assert plain > 0.5
    rows = compare.dedupe(idn, [xyz, other], [-100.0, -100.0 + 1e-6])
    assert [(r.index, r.representative) for r in rows] == [(0, 0), (1, 0)]
    assert rows[1].rmsd == pytest.approx(0.0, abs=1e-6)
    assert rows[1].delta_kcal == pytest.approx(1e-6 * compare.HARTREE_TO_KCAL)
    assert rows[0].rmsd is None and rows[0].delta_kcal is None


def _twisted(xyz: np.ndarray, els, degrees: float) -> np.ndarray:
    """Rotate the tBu group (and its H) about the ring-tBu bond by ``degrees``."""
    mol, _, _ = _embed("CC(C)(C)c1ccccc1")
    ring = next(a.GetIdx() for a in mol.GetAtoms() if a.GetIsAromatic() and a.GetDegree() == 3)
    quaternary = next(
        n.GetIdx() for n in mol.GetAtomWithIdx(ring).GetNeighbors() if not n.GetIsAromatic()
    )
    side = {quaternary}
    stack = [quaternary]
    while stack:
        for n in mol.GetAtomWithIdx(stack.pop()).GetNeighbors():
            if n.GetIdx() not in side and n.GetIdx() != ring:
                side.add(n.GetIdx())
                stack.append(n.GetIdx())
    axis = xyz[quaternary] - xyz[ring]
    axis /= np.linalg.norm(axis)
    t = np.radians(degrees)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rot = np.eye(3) + np.sin(t) * k + (1 - np.cos(t)) * (k @ k)
    out = xyz.copy()
    idx = sorted(side)
    out[idx] = (xyz[idx] - xyz[ring]) @ rot.T + xyz[ring]
    return out


def test_energy_order_picks_the_lowest_representative_and_tolerances_apply() -> None:
    els, xyz, methyls = _tbu_benzene()
    twisted = _twisted(xyz, els, 30.0)
    idn = compare.structure_identity(els, xyz)
    assert compare.pair_rmsd(idn, twisted, xyz)[0] > idn.r_tol
    frames = [xyz, _swap_methyls(xyz, methyls), twisted]
    rows = compare.dedupe(idn, frames, [-100.0 + 0.0004, -100.0, -98.0])  # frame 1 is the lowest
    assert [r.representative for r in rows] == [1, 1, 2]
    assert rows[0].delta_kcal == pytest.approx(0.0004 * compare.HARTREE_TO_KCAL)
    # the energy test splits geometrically identical frames beyond e_tol ...
    strict = compare.structure_identity(els, xyz, e_tol=0.1)
    rows = compare.dedupe(strict, frames[:2], [-100.0, -100.0 + 0.001])  # 0.63 kcal/mol apart
    assert [r.representative for r in rows] == [0, 1]
    # ... and is skipped when an energy is unknown; input order then decides
    rows = compare.dedupe(strict, frames[:2], [None, -100.0])
    assert [r.representative for r in rows] == [0, 0] and rows[1].delta_kcal is None
    rows = compare.dedupe(strict, frames[:2])
    assert [r.representative for r in rows] == [0, 0]


def test_metal_edges_are_coordination_and_pairs_are_forming() -> None:
    els = ["Zr", "O", "O", "C", "H", "H", "H"]
    xyz = np.array([[0.0, 0.0, 0.0], [2.1, 0.0, 0.0], [-2.1, 0.0, 0.0], [3.5, 0.0, 0.0],
                    [3.9, 1.0, 0.0], [3.9, -0.5, 0.9], [3.9, -0.5, -0.9]])  # fmt: skip
    idn = compare.structure_identity(els, xyz, reaction_pairs=[(2, 0)])
    kinds = {(e.a, e.b): e.type for e in idn.graph.edges}
    assert idn.metal == 0
    assert kinds[(0, 1)] is EdgeType.COORDINATION
    assert kinds[(0, 2)] is EdgeType.FORMING
    assert kinds[(1, 3)] is EdgeType.COVALENT
    assert idn.graph.reaction_pairs == ((0, 2),)
    assert compare.structure_identity(els, xyz, metal=None).metal == 0
    assert compare.structure_identity(["C", "Zr", "Pd"], np.eye(3) * 3).metal is None  # two metals
    assert compare.structure_identity(els, xyz, metal=3).metal == 3  # explicit choice wins


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"metal": 99}, "metal index"),
        ({"reaction_pairs": [(1, 1)]}, "reaction pair"),
        ({"reaction_pairs": [(0, 99)]}, "reaction pair"),
    ],
)
def test_invalid_arguments_are_refused(kwargs: dict, message: str) -> None:
    els, xyz, _ = _tbu_benzene()
    with pytest.raises(ValueError, match=message):
        compare.structure_identity(els, xyz, **kwargs)


def test_shape_and_length_mismatches_are_refused() -> None:
    els, xyz, _ = _tbu_benzene()
    with pytest.raises(ValueError, match="shape"):
        compare.structure_identity(els, xyz[:-1])
    idn = compare.structure_identity(els, xyz)
    with pytest.raises(ValueError, match="length"):
        compare.dedupe(idn, [xyz, xyz], [-1.0])


def test_mirror_policy_follows_the_flag() -> None:
    mol, els, xyz = _embed("C[C@H](N)C(=O)O")  # alanine: the mirror image is the enantiomer
    mirrored = xyz * np.array([-1.0, 1.0, 1.0])
    assert compare.pair_rmsd(compare.structure_identity(els, xyz), mirrored, xyz)[0] < 1e-6
    no_mirror = compare.structure_identity(els, xyz, mirror=False)
    assert compare.pair_rmsd(no_mirror, mirrored, xyz)[0] > 0.1
    assert dihedral(xyz, 0, 1, 3, 4) == pytest.approx(-dihedral(mirrored, 0, 1, 3, 4))
