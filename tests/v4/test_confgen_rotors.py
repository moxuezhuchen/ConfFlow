"""Unit tests for ``confflow.science.confgen.rotors``: rotor perception, moves, clash test, rings."""

from __future__ import annotations

import math

import numpy as np
import pytest

from confflow.science.confgen import search
from confflow.science.confgen.identity import Identity
from confflow.science.confgen.rotors import Topology, dihedral, rotate
from tests.v4._dg_search_helpers import _embed, build_topology


def _topology(smiles: str):
    """Return (elements, reference, Topology) for a metal-free molecule."""
    _, els, ref = _embed(smiles)
    graph, _, bonds = build_topology(els, ref, (None, [], [], [], 1.25), None)
    radii = np.array([search._radius(e) for e in els])
    return els, ref, Topology(Identity(graph), ref, bonds, radii)


@pytest.fixture(scope="module")
def butane():
    return _topology("CCCC")


def test_butane_has_exactly_one_rotor(butane) -> None:
    _, _, topo = butane
    # The two methyl groups are symmetric tops and are skipped; only the central C-C turns.
    assert len(topo.rotors) == 1
    rotor = topo.rotors[0]
    assert set(rotor.bond) == {1, 2}
    assert set(rotor.axis) == {1, 2}
    assert rotor.moves == pytest.approx((120.0, 240.0))
    assert rotor.period == pytest.approx(360.0)
    assert rotor.kind == "COVALENT"


def test_rotor_moving_side_is_one_half_of_the_molecule(butane) -> None:
    _, _, topo = butane
    rotor = topo.rotors[0]
    heavy_left = {0, 1, 4, 5, 6, 7, 8}  # C0, C1 and their hydrogens
    heavy_right = {2, 3, 9, 10, 11, 12, 13}
    assert set(rotor.moving) in (heavy_left, heavy_right)
    # The torsion quadruple is (neighbour, axis0, axis1, neighbour) across the rotatable bond.
    na, a0, a1, nb = rotor.torsion
    assert (a0, a1) == rotor.axis
    assert topo.id.full[na].keys() >= {a0} and topo.id.full[nb].keys() >= {a1}


def test_rotate_full_turn_is_identity(butane) -> None:
    _, ref, topo = butane
    np.testing.assert_allclose(rotate(ref, topo.rotors[0], 360.0), ref, atol=1e-9)


@pytest.mark.parametrize("angle", [120.0, 240.0])
def test_rotate_changes_dihedral_and_keeps_fixed_side(butane, angle: float) -> None:
    _, ref, topo = butane
    rotor = topo.rotors[0]
    moved = rotate(ref, rotor, angle)
    delta = (topo.torsion(moved, rotor) - topo.torsion(ref, rotor)) % 360.0
    # Turning by +120 or -120 (= +240) both move the central dihedral by 120 degrees.
    assert min(abs(delta - 120.0), abs(delta - 240.0)) == pytest.approx(0.0, abs=1e-6)
    fixed = [i for i in range(len(ref)) if i not in rotor.moving]
    np.testing.assert_array_equal(moved[fixed], ref[fixed])
    # Rigid turn about the axis: bond lengths of the moving side are preserved.
    d_before = np.linalg.norm(ref[list(rotor.moving)] - ref[rotor.axis[1]], axis=1)
    d_after = np.linalg.norm(moved[list(rotor.moving)] - moved[rotor.axis[1]], axis=1)
    np.testing.assert_allclose(d_after, d_before, atol=1e-9)


def test_dihedral_matches_rdkit() -> None:
    from rdkit.Chem import rdMolTransforms

    mol, els, ref = _embed("CCCC")
    _, _, topo = _topology("CCCC")
    a, b, c, d = topo.rotors[0].torsion
    ours = dihedral(ref, a, b, c, d)
    theirs = rdMolTransforms.GetDihedralDeg(mol.GetConformer(), a, b, c, d)
    assert ours == pytest.approx(theirs, abs=1e-6)


def test_clashes_false_when_relaxed_true_when_squashed(butane) -> None:
    _, ref, topo = butane
    assert topo.clashes(ref) is False
    squashed = ref.copy()
    # Atoms 0 and 3 are 1,4 related (not excluded) and placed 0.5 A apart.
    direction = (ref[3] - ref[0]) / np.linalg.norm(ref[3] - ref[0])
    squashed[3] = ref[0] + 0.5 * direction
    assert topo.clashes(squashed) is True


def _chair() -> np.ndarray:
    """Ideal cyclohexane chair: ring carbons alternate +/-0.25 A out of their mean plane."""
    xyz = np.zeros((6, 3))
    for i in range(6):
        theta = math.radians(60.0 * i)
        xyz[i] = [1.45 * math.cos(theta), 1.45 * math.sin(theta), 0.25 * (-1) ** i]
    return xyz


def test_ring_signature_of_cyclohexane_has_six_symbols() -> None:
    _, ref, topo = _topology("C1CCCCC1")
    assert len(topo.rings) == 1 and len(topo.rings[0]) == 6
    sig = topo.ring_signature(ref)
    assert len(sig) == 6
    assert set(sig) <= set("+-0")


def test_ring_signature_of_chair_alternates() -> None:
    _, ref, topo = _topology("C1CCCCC1")
    xyz = ref.copy()
    xyz[:6] = _chair()
    # Chair: torsions alternate in sign around the ring (ring order from perception).
    sig = topo.ring_signature(xyz)
    assert sig in ("+-+-+-", "-+-+-+")
