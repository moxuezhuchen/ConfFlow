#!/usr/bin/env python3

"""Tests for confgen module (merged)."""

from __future__ import annotations

import os
from unittest.mock import patch

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from confflow.blocks import confgen
from confflow.blocks.confgen.collision import check_clash_core
from confflow.blocks.confgen.generator import (
    get_rotatable_bonds,
    init_worker,
    load_mol_from_xyz,
    main,
    run_generation,
    write_xyz,
)
from confflow.blocks.confgen.rotations import (
    _bfs_distances,
    _bfs_distances_multi,
    _component_nodes,
    _edge_in_cycle,
    _parse_angles,
    _parse_chain,
    _parse_steps,
    _rotate_atoms_around_bond,
)
from confflow.core.data import GV_COVALENT_RADII
from confflow.core.io import read_xyz_file


def _write_butane_xyz(path: str) -> None:
    content = """14
butane
C      0.000000    0.000000    0.000000
C      1.540000    0.000000    0.000000
C      3.080000    0.000000    0.000000
C      4.620000    0.000000    0.000000
H     -0.630000    0.900000    0.000000
H     -0.630000   -0.900000    0.000000
H      0.000000    0.000000    1.000000
H      1.540000    1.000000    0.900000
H      1.540000   -1.000000   -0.900000
H      3.080000    1.000000   -0.900000
H      3.080000   -1.000000    0.900000
H      5.250000    0.900000    0.000000
H      5.250000   -0.900000    0.000000
H      4.620000    0.000000    1.000000
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_confgen_chain_mode_generates_traj_xyz(cd_tmp) -> None:
    xyz_path = cd_tmp / "butane.xyz"
    _write_butane_xyz(str(xyz_path))

    confgen.run_generation(
        input_files=str(xyz_path),
        angle_step=120,
        bond_threshold=1.15,
        clash_threshold=0.65,
        optimize=False,
        confirm=False,
        chains=["1-2-3-4"],
        chain_steps=["360,360,360"],
        chain_angles=None,
        rotate_side="left",
    )

    out = cd_tmp / "search.xyz"
    assert out.exists(), "confgen did not produce search.xyz"
    first = out.read_text(encoding="utf-8").splitlines()[0].strip()
    assert first.isdigit() and int(first) > 0


def test_check_clash_core_no_clash():
    atom_numbers = np.array([6, 6])
    coords = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    clash_threshold = 0.5
    topo_dist_matrix = np.array([[0, 1], [1, 0]], dtype=np.int64)
    radii_array = np.array(GV_COVALENT_RADII)
    assert not check_clash_core(
        atom_numbers, coords, clash_threshold, topo_dist_matrix, radii_array
    )


def test_check_clash_core_with_clash():
    atom_numbers = np.array([6, 6])
    coords = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]])
    clash_threshold = 0.5
    topo_dist_matrix = np.array([[0, 10], [10, 0]], dtype=np.int64)
    radii_array = np.array(GV_COVALENT_RADII)
    assert check_clash_core(atom_numbers, coords, clash_threshold, topo_dist_matrix, radii_array)


def test_rotate_atoms_around_bond():
    coords = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [1.5, 1.0, 0.0]])
    _rotate_atoms_around_bond(coords, 0, 1, np.array([2]), 180.0)
    assert np.allclose(coords[2], [1.5, -1.0, 0.0])


def test_rotate_atoms_around_bond_edge_cases():
    coords = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    _rotate_atoms_around_bond(coords, 0, 1, np.array([1]), 90.0)
    assert np.allclose(coords[1], [0.0, 0.0, 0.0])

    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    _rotate_atoms_around_bond(coords, 0, 1, np.array([]), 90.0)
    assert np.allclose(coords[1], [1.0, 0.0, 0.0])


def test_parse_chain_steps_angles():
    assert _parse_chain("1-2-3") == [0, 1, 2]
    assert _parse_chain("1,2,3") == [0, 1, 2]
    assert _parse_steps("3", 1) == [3]
    assert _parse_steps("3,4", 2) == [3, 4]
    assert _parse_angles("0,180", 1) == [[0.0, 180.0]]
    assert _parse_angles("0,180;90", 2) == [[0.0, 180.0], [90.0]]


def test_parse_helpers_errors():
    with pytest.raises(ValueError, match="chain format error"):
        _parse_chain("1")
    with pytest.raises(ValueError, match="chain must be a list of integers"):
        _parse_chain("1-a")
    with pytest.raises(ValueError, match="positive"):
        _parse_chain("1-0")
    with pytest.raises(ValueError, match="duplicate"):
        _parse_chain("1-2-1")

    with pytest.raises(ValueError, match="steps requires 2 values"):
        _parse_steps("120", 2)
    with pytest.raises(ValueError, match="steps must be in 1..360"):
        _parse_steps("0,400", 2)

    with pytest.raises(ValueError, match="angles requires 2 segments"):
        _parse_angles("0,120", 2)
    with pytest.raises(ValueError, match="angles requires 2 segments"):
        _parse_angles("0,120;", 2)
    with pytest.raises(ValueError, match="angles segment cannot be empty"):
        _parse_angles("0,120; , ", 2)


def test_run_generation_basic(cd_tmp):
    xyz_content = "3\n\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\nH 1.5 1.0 0.0\n"
    in_xyz = cd_tmp / "in.xyz"
    in_xyz.write_text(xyz_content)
    run_generation(str(in_xyz), chains=["1-2-3"], chain_steps=["180,180"], confirm=False)
    assert os.path.exists(cd_tmp / "search.xyz")


def test_load_mol_from_xyz_basic(tmp_path):
    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\n\nC 0 0 0\nC 1.5 0 0\n")
    mol = load_mol_from_xyz(str(xyz), 1.2)
    assert mol.GetNumAtoms() == 2
    assert mol.GetNumBonds() == 1


def test_load_mol_from_xyz_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_mol_from_xyz(str(tmp_path / "nonexistent.xyz"), 1.15)

    with pytest.raises(ValueError, match="path is not a file"):
        load_mol_from_xyz(str(tmp_path), 1.15)

    empty = tmp_path / "empty.xyz"
    empty.write_text("")
    with pytest.raises(ValueError, match="file is empty"):
        load_mol_from_xyz(str(empty), 1.15)

    f1 = tmp_path / "f1.xyz"
    f1.write_text("1\n")
    with pytest.raises(ValueError, match="insufficient lines"):
        load_mol_from_xyz(str(f1), 1.15)

    f2 = tmp_path / "f2.xyz"
    f2.write_text("abc\ntest\nC 0 0 0\n")
    with pytest.raises(ValueError, match="cannot parse atom count"):
        load_mol_from_xyz(str(f2), 1.15)


def test_get_rotatable_bonds_raises():
    mol = Chem.MolFromSmiles("CCCC")
    mol = Chem.AddHs(mol)
    with pytest.raises(RuntimeError, match="automatic flexible bond detection has been removed"):
        get_rotatable_bonds(mol, None, None)


def test_bfs_distances():
    adj = [{1}, {0, 2}, {1}]
    dist = _bfs_distances(adj, 0)
    assert dist == [0, 1, 2]


def test_bfs_distances_multi():
    adj = [{1}, {0, 2}, {1, 3}, {2}]
    dist = _bfs_distances_multi(adj, [0, 3])
    assert dist == [0, 1, 1, 0]


def test_component_nodes():
    adj = [{1}, {0, 2}, {1, 3}, {2}]
    nodes = _component_nodes(adj, 0, 2)
    assert nodes == {0, 1, 2, 3}


def test_edge_in_cycle_triangle_line():
    adj = [{1, 2}, {0, 2}, {0, 1}]
    assert _edge_in_cycle(adj, 0, 1) is True
    adj2 = [{1}, {0, 2}, {1}]
    assert _edge_in_cycle(adj2, 0, 1) is False


def test_edge_in_cycle_complex():
    adj = [set([1, 2]), set([0, 2]), set([0, 1, 3]), set([2])]
    assert _edge_in_cycle(adj, 0, 1) is True
    assert _edge_in_cycle(adj, 1, 2) is True
    assert _edge_in_cycle(adj, 2, 3) is False


def test_write_xyz(tmp_path):
    mol = Chem.MolFromSmiles("C")
    coords = [np.array([[0, 0, 0]]), np.array([[1, 1, 1]])]
    out = tmp_path / "out.xyz"
    write_xyz(mol, coords, str(out))
    content = out.read_text()
    assert "Conformer 1" in content
    assert "Conformer 2" in content
    assert content.count("C ") == 2


def test_write_xyz_preserves_per_conformer_atoms(tmp_path):
    mol = Chem.MolFromSmiles("CO")
    conformers = [
        {
            "coords": np.array([[0.0, 0.0, 0.0], [1.2, 0.0, 0.0]]),
            "atoms": ["O", "C"],
            "cid": "B000001",
        }
    ]
    out = tmp_path / "out.xyz"

    write_xyz(mol, conformers, str(out))

    frames = read_xyz_file(str(out), parse_metadata=False)
    assert frames[0]["atoms"] == ["O", "C"]


def test_init_worker():
    import confflow.blocks.confgen.generator as gen

    init_worker("mol", "conf", "bonds", "clash", "topo", "atoms", "opt")
    assert gen.w_mol == "mol"
    assert gen.w_conf == "conf"
    assert gen.w_bonds == "bonds"
    assert gen.w_clash == "clash"
    assert gen.w_topo == "topo"
    assert gen.w_atoms == "atoms"
    assert gen.w_opt == "opt"


def test_run_generation_with_chains(cd_tmp):
    xyz = cd_tmp / "test.xyz"
    xyz.write_text("4\n\nC 0 0 0\nC 1.5 0 0\nC 3.0 0 0\nC 4.5 0 0\n")
    res = run_generation(
        str(xyz),
        chains=["1-2-3-4"],
        chain_angles=["0,120,240;0,120,240;0,120,240"],
    )
    assert res is not None
    assert len(res) > 0


def test_run_generation_with_bond_overrides(cd_tmp):
    xyz = cd_tmp / "test.xyz"
    xyz.write_text("2\n\nC 0 0 0\nH 0 0 1.5\n")
    res = run_generation(
        str(xyz),
        add_bond=["1-2"],
        bond_threshold=1.0,
        chains=["1-2"],
    )
    assert res is not None
    assert len(res) > 0


def test_run_generation_advanced(cd_tmp):
    xyz = cd_tmp / "test.xyz"
    xyz.write_text("4\ntest\nC 0 0 0\nC 1.5 0 0\nC 3.0 0 0\nC 4.5 0 0\n")
    res1 = run_generation(str(xyz), chains=["1-2"], add_bond=[[1, 4]], del_bond=[[2, 3]])
    assert isinstance(res1, list), "run_generation should return a list"
    res2 = run_generation(str(xyz), chains=["1-2-3"], rotate_side="right")
    assert isinstance(res2, list)
    res3 = run_generation(str(xyz), chains=["1-2"], optimize=True)
    assert isinstance(res3, list)


def test_run_generation_multi_input(cd_tmp):
    f1 = cd_tmp / "f1.xyz"
    f1.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    f2 = cd_tmp / "f2.xyz"
    f2.write_text("2\ntest\nC 0 0 0\nH 0 0 1.1\n")
    res = run_generation([str(f1), str(f2)], chains=["1-2"])
    assert isinstance(res, list)
    assert len(res) > 0, "multi-input generation should produce at least one result"


def test_run_generation_multi_input_preserves_atom_order_in_output(cd_tmp):
    f1 = cd_tmp / "f1.xyz"
    f1.write_text("3\nfirst\nC 0 0 0\nO 1.2 0 0\nH 2.2 0 0\n", encoding="utf-8")
    f2 = cd_tmp / "f2.xyz"
    f2.write_text("3\nsecond\nH 2.2 0 0\nO 1.2 0 0\nC 0 0 0\n", encoding="utf-8")

    run_generation([str(f1), str(f2)], chains=["1-2"], confirm=False)

    frames = read_xyz_file(str(cd_tmp / "search.xyz"), parse_metadata=False)
    assert any(frame["atoms"] == ["H", "O", "C"] for frame in frames)


def test_run_generation_edge_cases(cd_tmp):
    mol = Chem.MolFromSmiles("CCC")
    mol = Chem.AddHs(mol)
    AllChem.EmbedMolecule(mol)

    xyz_path = cd_tmp / "propane.xyz"
    with open(xyz_path, "w") as f:
        f.write(f"{mol.GetNumAtoms()}\n\n")
        for i in range(mol.GetNumAtoms()):
            pos = mol.GetConformer().GetAtomPosition(i)
            sym = mol.GetAtomWithIdx(i).GetSymbol()
            f.write(f"{sym} {pos.x} {pos.y} {pos.z}\n")

    res = run_generation(
        [str(xyz_path)],
        angle_step=120,
        chains=["1-2"],
        rotate_side="right",
        confirm=False,
    )
    assert len(res) > 0

    run_generation(
        [str(xyz_path)],
        chains=["1-2", "2-3"],
        chain_angles=["0,120", "0,120", "0,120"],
    )

    run_generation(
        [str(xyz_path)],
        chains=["1-2", "2-3"],
        chain_steps=["120", "120", "120"],
    )

    res = run_generation(
        [str(xyz_path)],
        chains=["1-2"],
        no_rotate=[[1, 2]],
        confirm=False,
    )
    assert len(res) == 1

    run_generation(
        [str(xyz_path)],
        chains=["1-2"],
        rotate_side="invalid",
    )

    with patch("builtins.input", return_value="n"):
        run_generation([str(xyz_path)], chains=["1-2"], confirm=True)


def test_main_cli(tmp_path):
    xyz_path = tmp_path / "test.xyz"
    with open(xyz_path, "w") as f:
        f.write("3\n\nC 0 0 0\nC 1.5 0 0\nC 3.0 0 0\n")

    with patch("confflow.blocks.confgen.generator.run_generation") as mock_run:
        with patch("sys.argv", ["confgen", str(xyz_path), "60", "--chain", "1-2-3", "-y"]):
            main()
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert kwargs["angle_step"] == 60
            assert kwargs["chains"] == ["1-2-3"]
            assert kwargs["confirm"] is False

    with patch("confflow.blocks.confgen.generator.run_generation") as mock_run:
        with patch("sys.argv", ["confgen", str(xyz_path), str(xyz_path), "90", "--chain", "1-2"]):
            main()
            assert mock_run.call_args[1]["angle_step"] == 90
            assert len(mock_run.call_args[1]["input_files"]) == 2

    with patch("sys.argv", ["confgen"]):
        with pytest.raises(SystemExit):
            main()


def test_run_generation_wrapper(cd_tmp):
    xyz_path = cd_tmp / "test.xyz"
    with open(xyz_path, "w") as f:
        f.write("3\n\nC 0 0 0\nC 1.5 0 0\nC 3.0 0 0\n")

    with patch("confflow.blocks.confgen.generator.load_mol_from_xyz") as mock_load:
        mol = Chem.MolFromSmiles("CCC")
        mol = Chem.AddHs(mol)
        AllChem.EmbedMolecule(mol)
        mock_load.return_value = mol

        res = run_generation([str(xyz_path)], chains=["1-2"], confirm=False)
        assert len(res) > 0


def test_sanitize_fallback(cd_tmp):
    xyz_path = cd_tmp / "bad.xyz"
    with open(xyz_path, "w") as f:
        f.write("2\n\nC 0 0 0\nC 0.5 0 0\n")

    with patch("rdkit.Chem.SanitizeMol", side_effect=Exception("Sanitize failed")):
        res = run_generation(
            [str(xyz_path)],
            chains=["1-2"],
            add_bond=[[1, 2]],
            confirm=False,
        )
        assert len(res) > 0
