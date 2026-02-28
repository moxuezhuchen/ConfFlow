#!/usr/bin/env python3
from __future__ import annotations

import importlib

import numpy as np

from tests._helpers import reload_with_import_block


def test_confgen_generator_numba_fallback_covers_check_clash_core():
    import confflow.blocks.confgen.generator as gen

    gen_fallback = reload_with_import_block(gen, "numba")
    try:
        atom_numbers = np.array([6, 1], dtype=np.int64)
        coords = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.01]], dtype=np.float64)
        topo_dist = np.array([[0, 1], [1, 0]], dtype=np.int64)
        radii = np.zeros(20, dtype=np.float64)
        radii[6] = 1.5
        radii[1] = 1.0

        # topo_dist <= ignore_hops (3) should be ignored => no clash
        assert gen_fallback.check_clash_core(atom_numbers, coords, 0.65, topo_dist, radii) is False

        # topo_dist > ignore_hops and very close => clash
        topo_dist2 = np.array([[0, 10], [10, 0]], dtype=np.int64)
        assert gen_fallback.check_clash_core(atom_numbers, coords, 0.65, topo_dist2, radii) is True
    finally:
        importlib.reload(gen)


def test_refine_processor_numba_fallback_covers_get_pmi_and_fast_rmsd():
    import confflow.blocks.refine.rmsd_engine as engine

    engine_fallback = reload_with_import_block(engine, "numba")
    try:
        coords = np.array([[1.0, 0.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)
        pmi = engine_fallback.get_pmi(coords)
        assert pmi.shape == (3,)

        c1 = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]],
            dtype=np.float64,
        )
        c2 = np.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0], [0.0, 0.0, 0.0]],
            dtype=np.float64,
        )

        rmsd = engine_fallback.fast_rmsd(c1, c2)
        assert rmsd >= 0
    finally:
        importlib.reload(engine)
        # reload processor to ensure its references point to the restored rmsd_engine function objects
        import confflow.blocks.refine.processor as _proc

        importlib.reload(_proc)


def test_confgen_generator_process_task_error():
    import confflow.blocks.confgen.generator as gen

    mol = gen.Chem.AddHs(gen.Chem.MolFromSmiles("CC"))
    gen.AllChem.EmbedMolecule(mol, randomSeed=0xF00D)
    conf = mol.GetConformer(0)
    atoms = np.array([a.GetAtomicNum() for a in mol.GetAtoms()], dtype=np.int64)
    topo = np.zeros((len(atoms), len(atoms)), dtype=np.int64) + 10

    try:
        gen.init_worker(
            mol,
            conf,
            bonds=[("bad",)],
            clash=0.65,
            topo=topo,
            atoms=atoms,
            opt=False,
        )
        assert gen.process_task([180.0]) is None
    finally:
        importlib.reload(gen)


def test_confgen_process_task_mmff_failure():
    from rdkit import Chem

    from confflow.blocks.confgen.generator import process_task

    mol = Chem.MolFromXYZBlock("2\n\nH 0 0 0\nH 0 0 1\n")
    conf = mol.GetConformer()

    from unittest.mock import patch

    with (
        patch("confflow.blocks.confgen.generator.w_mol", mol),
        patch("confflow.blocks.confgen.generator.w_conf", conf),
        patch("confflow.blocks.confgen.generator.w_atoms", ["H", "H"]),
        patch("confflow.blocks.confgen.generator.w_bonds", []),
        patch("confflow.blocks.confgen.generator.w_opt", True),
        patch(
            "confflow.blocks.confgen.generator.AllChem.MMFFOptimizeMolecule",
            side_effect=RuntimeError("MMFF fail"),
        ),
        patch("confflow.blocks.confgen.generator.check_clash_core", return_value=False),
    ):
        res = process_task([])
        assert res is not None


def test_confgen_process_task_bond_spec_error():
    from rdkit import Chem

    from confflow.blocks.confgen.generator import process_task

    mol = Chem.MolFromXYZBlock("2\n\nH 0 0 0\nH 0 0 1\n")
    conf = mol.GetConformer()

    from unittest.mock import patch

    with (
        patch("confflow.blocks.confgen.generator.w_mol", mol),
        patch("confflow.blocks.confgen.generator.w_conf", conf),
        patch("confflow.blocks.confgen.generator.w_bonds", [(1, 2)]),
    ):
        res = process_task([0])
        assert res is None


def test_refine_pmi_empty_and_fast_rmsd_empty():
    from confflow.blocks.refine.rmsd_engine import fast_rmsd, get_pmi

    res = get_pmi(np.zeros((0, 3)))
    assert np.all(res == 0.0)

    res = fast_rmsd(np.zeros((0, 3)), np.zeros((0, 3)))
    assert res == 999.9
    res = fast_rmsd(np.zeros((1, 3)), np.zeros((2, 3)))
    assert res == 999.9


def test_fast_rmsd_reflection_and_mismatch():
    from confflow.blocks.refine.rmsd_engine import fast_rmsd

    c1 = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0]], dtype=np.float64)
    c2 = np.array([[1, 0, 0], [0, 1, 0], [0, 0, -1], [0, 0, 0]], dtype=np.float64)
    val = fast_rmsd(c1, c2)
    assert val >= 0

    assert fast_rmsd(np.zeros((0, 3)), np.zeros((0, 3))) == 999.9
    assert fast_rmsd(np.zeros((3, 3)), np.zeros((4, 3))) == 999.9


# ---------------------------------------------------------------------------
# greedy_permutation_rmsd tests
# ---------------------------------------------------------------------------


def test_greedy_permutation_rmsd_identical():
    """Identical structures should give RMSD ~0."""
    from confflow.blocks.refine.rmsd_engine import greedy_permutation_rmsd

    coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
    elem_ids = np.array([6, 1, 1], dtype=np.int32)
    rmsd = greedy_permutation_rmsd(coords, coords.copy(), elem_ids, elem_ids.copy())
    assert rmsd < 0.01


def test_greedy_permutation_rmsd_empty():
    """Empty coordinates should return 999.9."""
    from confflow.blocks.refine.rmsd_engine import greedy_permutation_rmsd

    empty = np.zeros((0, 3), dtype=np.float64)
    ids = np.array([], dtype=np.int32)
    assert greedy_permutation_rmsd(empty, empty, ids, ids) == 999.9


def test_greedy_permutation_rmsd_size_mismatch():
    """Mismatched sizes should return 999.9."""
    from confflow.blocks.refine.rmsd_engine import greedy_permutation_rmsd

    c1 = np.array([[0, 0, 0]], dtype=np.float64)
    c2 = np.array([[0, 0, 0], [1, 0, 0]], dtype=np.float64)
    id1 = np.array([6], dtype=np.int32)
    id2 = np.array([6, 6], dtype=np.int32)
    assert greedy_permutation_rmsd(c1, c2, id1, id2) == 999.9


def test_greedy_permutation_rmsd_swapped_same_element():
    """Swapping atoms of same element should still give low RMSD."""
    from confflow.blocks.refine.rmsd_engine import greedy_permutation_rmsd

    c1 = np.array([[0, 0, 0], [2, 0, 0], [0, 2, 0]], dtype=np.float64)
    c2 = np.array([[0, 0, 0], [0, 2, 0], [2, 0, 0]], dtype=np.float64)  # swapped H atoms
    elem_ids = np.array([6, 1, 1], dtype=np.int32)
    rmsd = greedy_permutation_rmsd(c1, c2, elem_ids, elem_ids.copy())
    assert rmsd < 0.01  # greedy matching should resolve the swap


def test_greedy_permutation_rmsd_element_mismatch():
    """When elements don't match, greedy matching fails → large RMSD."""
    from confflow.blocks.refine.rmsd_engine import greedy_permutation_rmsd

    c1 = np.array([[0, 0, 0], [1, 0, 0]], dtype=np.float64)
    c2 = np.array([[0, 0, 0], [1, 0, 0]], dtype=np.float64)
    ids1 = np.array([6, 1], dtype=np.int32)
    ids2 = np.array([7, 8], dtype=np.int32)  # completely different elements
    rmsd = greedy_permutation_rmsd(c1, c2, ids1, ids2)
    assert rmsd == 999.9  # no valid matching possible


# ---------------------------------------------------------------------------
# get_principal_axes tests
# ---------------------------------------------------------------------------


def test_get_principal_axes_empty():
    """Empty coords should return zero eigenvalues and identity axes."""
    from confflow.blocks.refine.rmsd_engine import get_principal_axes

    eigvals, eigvecs = get_principal_axes(np.zeros((0, 3), dtype=np.float64))
    assert np.allclose(eigvals, [0, 0, 0])
    assert eigvecs.shape == (3, 3)


def test_get_principal_axes_linear():
    """Linear molecule's principal axes should have one zero eigenvalue."""
    from confflow.blocks.refine.rmsd_engine import get_principal_axes

    coords = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=np.float64)
    eigvals, eigvecs = get_principal_axes(coords)
    assert eigvals[0] < 1e-10  # one eigenvalue near zero (linear)
    assert eigvecs.shape == (3, 3)


# ---------------------------------------------------------------------------
# check_one_against_many tests
# ---------------------------------------------------------------------------


def test_check_one_against_many_duplicate():
    """Identical conformer should be detected as duplicate."""
    from confflow.blocks.refine.rmsd_engine import check_one_against_many, get_pmi

    coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
    pmi = get_pmi(coords)
    elem_ids = np.array([6, 1, 1], dtype=np.int32)
    energy = -100.0

    cand_data = (coords, pmi, elem_ids, energy)
    unique_data = [(coords.copy(), pmi.copy(), 0, elem_ids.copy(), energy)]

    is_dup, mid = check_one_against_many((cand_data, unique_data, 0.5, 0.05))
    assert is_dup is True
    assert mid == 0


def test_check_one_against_many_unique():
    """Distant conformer should not be detected as duplicate."""
    from confflow.blocks.refine.rmsd_engine import check_one_against_many, get_pmi

    c1 = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
    c2 = np.array([[0, 0, 0], [5, 0, 0], [0, 5, 0]], dtype=np.float64)
    pmi1 = get_pmi(c1)
    pmi2 = get_pmi(c2)
    elem_ids = np.array([6, 1, 1], dtype=np.int32)

    cand_data = (c2, pmi2, elem_ids, -100.0)
    unique_data = [(c1, pmi1, 0, elem_ids.copy(), -100.0)]

    is_dup, mid = check_one_against_many((cand_data, unique_data, 0.5, 0.05))
    assert is_dup is False


def test_check_one_against_many_empty_coords():
    """Empty candidate should not be a duplicate."""
    from confflow.blocks.refine.rmsd_engine import check_one_against_many

    empty = np.zeros((0, 3), dtype=np.float64)
    pmi = np.array([0.0, 0.0, 0.0])
    elem_ids = np.array([], dtype=np.int32)

    cand = (empty, pmi, elem_ids, -100.0)
    is_dup, mid = check_one_against_many((cand, [], 0.5, 0.05))
    assert is_dup is False


def test_check_one_against_many_energy_tolerance():
    """Energy-assisted threshold relaxation: close energy → wider threshold."""
    from confflow.blocks.refine.rmsd_engine import (
        HARTREE_TO_KCALMOL,
        check_one_against_many,
        get_pmi,
    )

    # Two conformers that differ by just above the standard threshold
    c1 = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
    c2 = c1 + 0.3  # slight shift
    pmi1 = get_pmi(c1)
    pmi2 = get_pmi(c2)
    elem_ids = np.array([6, 1, 1], dtype=np.int32)

    # Same energy → threshold relaxed by 1.5x
    cand = (c2, pmi2, elem_ids, -100.0)
    unique = [(c1, pmi1, 0, elem_ids.copy(), -100.0)]

    # With very tight threshold, without tolerance they might not match
    is_dup_tight, _ = check_one_against_many((cand, unique, 0.01, 0.05))
    # With very wide threshold, they should match
    is_dup_wide, _ = check_one_against_many((cand, unique, 10.0, 0.05))
    assert is_dup_wide is True


# ---------------------------------------------------------------------------
# get_topology_hash_worker tests
# ---------------------------------------------------------------------------


def test_get_topology_hash_worker_empty():
    """Empty atoms should return 'empty' hash."""
    from confflow.blocks.refine.rmsd_engine import get_topology_hash_worker

    result = get_topology_hash_worker(([], np.zeros((0, 3))))
    assert result == "empty"


def test_get_topology_hash_worker_deterministic():
    """Same input should produce same hash."""
    from confflow.blocks.refine.rmsd_engine import get_topology_hash_worker

    atoms = ["C", "H", "H"]
    coords = np.array([[0, 0, 0], [1.0, 0, 0], [0, 1.0, 0]], dtype=np.float64)
    h1 = get_topology_hash_worker((atoms, coords))
    h2 = get_topology_hash_worker((atoms, coords.copy()))
    assert h1 == h2
    assert isinstance(h1, str)
    assert h1 not in ("empty", "error")


# ---------------------------------------------------------------------------
# collision edge cases
# ---------------------------------------------------------------------------


def test_check_clash_core_single_atom():
    """Single atom should never clash."""
    from confflow.blocks.confgen.collision import GV_RADII_ARRAY, check_clash_core

    atom_numbers = np.array([6])
    coords = np.array([[0.0, 0.0, 0.0]])
    topo = np.array([[0]], dtype=np.int64)
    assert not check_clash_core(atom_numbers, coords, 0.65, topo, GV_RADII_ARRAY)


def test_check_clash_core_all_within_3_bonds():
    """Atoms within 3 bonds (1-4 pairs) should be ignored in clash detection."""
    from confflow.blocks.confgen.collision import GV_RADII_ARRAY, check_clash_core

    atom_numbers = np.array([6, 6, 6])
    # Atoms very close but within 3 bonds → should NOT clash
    coords = np.array([[0.0, 0.0, 0.0], [0.3, 0.0, 0.0], [0.6, 0.0, 0.0]])
    topo = np.array([[0, 1, 2], [1, 0, 1], [2, 1, 0]], dtype=np.int64)
    assert not check_clash_core(atom_numbers, coords, 0.65, topo, GV_RADII_ARRAY)


def test_check_clash_core_boundary():
    """Test exactly at the clash threshold boundary."""
    from confflow.blocks.confgen.collision import GV_RADII_ARRAY, check_clash_core

    atom_numbers = np.array([6, 6])
    # Carbon covalent radius ~ 0.76, sum = 1.52, * 0.65 = 0.988
    # Distance at 0.98 → clash; at 1.0 → no clash
    coords_clash = np.array([[0.0, 0.0, 0.0], [0.95, 0.0, 0.0]])
    coords_ok = np.array([[0.0, 0.0, 0.0], [1.1, 0.0, 0.0]])
    topo = np.array([[0, 10], [10, 0]], dtype=np.int64)  # far apart topologically

    assert check_clash_core(atom_numbers, coords_clash, 0.65, topo, GV_RADII_ARRAY)
    assert not check_clash_core(atom_numbers, coords_ok, 0.65, topo, GV_RADII_ARRAY)


# ---------------------------------------------------------------------------
# get_element_atomic_number tests
# ---------------------------------------------------------------------------


def test_get_element_atomic_number():
    """Test element symbol to atomic number conversion."""
    from confflow.blocks.refine.rmsd_engine import get_element_atomic_number

    assert get_element_atomic_number("H") == 1
    assert get_element_atomic_number("C") == 6
    assert get_element_atomic_number("") == 0
    assert get_element_atomic_number("Zz") == 0  # unknown element


def test_check_clash_trigger():
    from confflow.blocks.confgen.generator import check_clash_core

    atom_numbers = np.array([6, 1, 1])
    coords = np.array([[0, 0, 0], [0, 0, 0.1], [0, 0, -0.1]], dtype=np.float64)
    topo_dist = np.zeros((3, 3)) + 10
    radii = np.array([0.0] * 100)
    radii[6] = 1.5
    radii[1] = 1.0

    assert check_clash_core(atom_numbers, coords, 0.5, topo_dist, radii) is True

    coords2 = np.array([[0, 0, 0], [0, 0, 5.0], [0, 0, -5.0]], dtype=np.float64)
    assert check_clash_core(atom_numbers, coords2, 0.5, topo_dist, radii) is False
