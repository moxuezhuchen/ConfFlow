"""Unit tests for ``confflow.science.confgen.identity``: symmetry RMSD and the minima pool.

Toy molecules come from the shared DG-search helpers (RDKit embedding, typed graph builder).
Everything here is pure NumPy/RDKit work and runs in well under a second.
"""

from __future__ import annotations

import numpy as np
import pytest

from confflow.science.confgen.identity import E_TOL, HARTREE_TO_KCAL, R_TOL, Identity, MinimaPool
from confflow.science.confgen.rotors import Rotor, rotate
from tests.v4._dg_search_helpers import _embed, build_topology

_KCAL = 1.0 / HARTREE_TO_KCAL  # one kcal/mol in Eh


def _graph(smiles: str):
    """Return (elements, reference, typed graph, Identity) for a metal-free molecule."""
    _, els, ref = _embed(smiles)
    graph, _, _ = build_topology(els, ref, (None, [], [], [], 1.25), None)
    return els, ref, graph, Identity(graph)


def _proper_rotation(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1.0
    return q


@pytest.fixture(scope="module")
def butane():
    return _graph("CCCC")


@pytest.fixture(scope="module")
def ammonia():
    return _graph("N")


def test_self_rmsd_is_zero(butane) -> None:
    _, ref, _, idn = butane
    assert idn.rmsd(ref, ref) == pytest.approx(0.0, abs=1e-6)


def test_rigid_motion_is_zero(butane) -> None:
    _, ref, _, idn = butane
    moved = ref @ _proper_rotation(1) + np.array([1.5, -2.0, 0.25])
    assert idn.rmsd(ref, moved, mirror=False) == pytest.approx(0.0, abs=1e-5)


def test_carbon_chain_reversal_is_an_automorphism(butane) -> None:
    els, ref, _, idn = butane
    assert len(idn.automorphisms()) == 2  # identity and the chain reversal
    # Reverse the chain: C0<->C3, C1<->C2 with their hydrogens (indices from the embedding).
    sigma = [3, 2, 1, 0, 11, 12, 13, 9, 10, 7, 8, 4, 5, 6]
    assert [els[i] for i in sigma] == els
    assert idn.rmsd(ref, ref[sigma]) == pytest.approx(0.0, abs=1e-5)


def test_heteroatom_hydrogen_permutation_is_free(ammonia) -> None:
    els, ref, _, idn = ammonia
    h = [i for i, e in enumerate(els) if e == "H"]
    assert len(h) == 3
    # Cyclic relabelling of the three N-H hydrogens is an automorphism of ammonia.
    sigma = list(range(len(els)))
    sigma[h[0]], sigma[h[1]], sigma[h[2]] = h[1], h[2], h[0]
    assert idn.rmsd(ref, ref[sigma]) == pytest.approx(0.0, abs=1e-5)


def test_non_equivalent_permutation_is_not_free(butane) -> None:
    _, ref, _, idn = butane
    swapped = ref.copy()
    swapped[[0, 1]] = ref[[1, 0]]  # exchanging a terminal and an inner carbon is no symmetry
    assert idn.rmsd(ref, swapped) >= R_TOL


def test_mirror_image_merges_only_when_mirror_is_allowed() -> None:
    els, ref, _, idn = _graph("CC[C@H](O)Cl")
    flipped = ref * np.array([-1.0, 1.0, 1.0])
    assert idn.rmsd(ref, flipped, mirror=True) == pytest.approx(0.0, abs=1e-5)
    assert idn.rmsd(ref, flipped, mirror=False) >= R_TOL


def test_fingerprint_is_rigid_invariant(butane) -> None:
    _, ref, _, idn = butane
    moved = ref @ _proper_rotation(3) + 4.0
    np.testing.assert_allclose(idn.fingerprint(ref), idn.fingerprint(moved), atol=1e-6)


def _distinct_geometries(ref: np.ndarray) -> list[np.ndarray]:
    """Return three butane geometries with different central dihedrals (all pairwise distinct)."""
    return [ref, rotate_about_central(ref, 60.0), rotate_about_central(ref, 180.0)]


def rotate_about_central(ref: np.ndarray, angle: float) -> np.ndarray:
    """Turn the C3 side of butane about the C1-C2 bond (atom indices from the embedding)."""
    rotor = Rotor((1, 2), (1, 2), (2, 3, 9, 10, 11, 12, 13), (angle,), "test", 360.0, (0, 1, 2, 3))
    return rotate(ref, rotor, angle)


def test_pool_dedups_same_minimum_and_keeps_lower_energy(butane) -> None:
    _, ref, _, idn = butane
    pool = MinimaPool(idn)
    assert pool.add(-100.0, ref, "t00", "dg:0") == 0
    # Same energy, rigidly moved copy: a rediscovery, not a new minimum.
    assert pool.add(-100.0, ref @ _proper_rotation(2), "t00", "dg:1") is None
    # Same geometry, lower energy within E_TOL: rediscovery keeps the lower energy.
    assert pool.add(-100.0 - 1e-6, ref, "t00", "dg:2") is None
    assert len(pool) == 1
    assert pool.hits == [3]
    assert pool.minima[0].energy == pytest.approx(-100.0 - 1e-6)
    assert pool.minima[0].origin == "dg:0"  # the first label survives a rediscovery


def test_pool_rejects_merge_when_energy_differs(butane) -> None:
    _, ref, _, idn = butane
    assert E_TOL * _KCAL < 1.0 * _KCAL  # the 1 kcal/mol gap below is beyond E_TOL
    pool = MinimaPool(idn)
    pool.add(-100.0, ref, "t00", "dg:0")
    assert pool.add(-100.0 + 1.0 * _KCAL, ref, "t00", "dg:1") == 1
    assert len(pool) == 2


def test_pool_separates_distinct_geometries(butane) -> None:
    _, ref, _, idn = butane
    geoms = _distinct_geometries(ref)
    for i in range(len(geoms)):
        for j in range(i + 1, len(geoms)):
            assert idn.rmsd(geoms[i], geoms[j]) >= R_TOL
    pool = MinimaPool(idn)
    for k, g in enumerate(geoms):
        assert pool.add(-100.0, g, "t00", f"dg:{k}") == k
    assert len(pool) == 3


def test_within_filters_window_and_orders_lowest_first(butane) -> None:
    _, ref, _, idn = butane
    geoms = _distinct_geometries(ref)
    pool = MinimaPool(idn)
    assert pool.within(5.0) == []
    pool.add(-100.0 + 7.0 * _KCAL, geoms[1], "t00", "a")
    pool.add(-100.0 + 3.0 * _KCAL, geoms[2], "t00", "b")
    pool.add(-100.0, geoms[0], "t00", "c")
    rows = pool.within(5.0)
    assert [m.origin for m in rows] == ["c", "b"]
    assert [round((m.energy + 100.0) * HARTREE_TO_KCAL, 6) for m in rows] == [0.0, 3.0]
    assert [m.origin for m in pool.within(10.0)] == ["c", "b", "a"]
    assert pool.lowest() == pytest.approx(-100.0)
    assert [m.origin for m in pool.ordered()] == ["c", "b", "a"]


def test_identity_classes_group_symmetric_carbons(butane) -> None:
    _, _, _, idn = butane
    assert idn.classes[0] == idn.classes[3]
    assert idn.classes[1] == idn.classes[2]
    assert idn.classes[0] != idn.classes[1]
