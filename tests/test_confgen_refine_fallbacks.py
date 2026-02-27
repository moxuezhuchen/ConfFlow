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
            side_effect=Exception("MMFF fail"),
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
