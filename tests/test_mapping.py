#!/usr/bin/env python3
"""Tests for confgen.mapping MCS and chain-transfer edge cases."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Geometry import Point3D

import confflow.blocks.confgen.mapping as mapping


def _embed(smiles: str, seed: int = 0xC0FFEE) -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0
    return mol


def _rigidly_transform(mol: Chem.Mol, seed: int = 7) -> Chem.Mol:
    """Return a copy of *mol* under a proper rotation plus translation."""
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(3, 3))
    q, _ = np.linalg.qr(a)
    if np.linalg.det(q) < 0:
        q[:, 0] *= -1.0
    shift = rng.normal(size=3) * 25.0
    out = Chem.Mol(mol)
    conf = out.GetConformer()
    for i in range(out.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        vec = np.array([p.x, p.y, p.z])
        new = q @ vec + shift
        conf.SetAtomPosition(i, Point3D(float(new[0]), float(new[1]), float(new[2])))
    return out


def test_run_mcs_timeout_without_match_raises():
    ref = Chem.MolFromSmiles("CC")
    target = Chem.MolFromSmiles("CC")
    fake_res = SimpleNamespace(canceled=True, numAtoms=0, numBonds=0, smartsString="")

    with patch.object(mapping.rdFMCS, "FindMCS", return_value=fake_res):
        with pytest.raises(ValueError, match="timed out"):
            mapping._run_mcs(ref, target, timeout=1, min_coverage=0.7, verbose=False)


def test_run_mcs_partial_timeout_fails_closed():
    """A timed-out search must not fall back to the partial substructure."""
    ref = Chem.MolFromSmiles("CCC")
    target = Chem.MolFromSmiles("CCC")
    fake_res = SimpleNamespace(canceled=True, numAtoms=2, numBonds=1, smartsString="[#6]-[#6]")

    with patch.object(mapping.rdFMCS, "FindMCS", return_value=fake_res):
        with pytest.raises(ValueError, match="timed out"):
            mapping._run_mcs(ref, target, timeout=1, min_coverage=0.5, verbose=False)


def test_run_mcs_low_coverage_raises():
    ref = Chem.MolFromSmiles("CCCC")
    target = Chem.MolFromSmiles("CC")
    fake_res = SimpleNamespace(canceled=False, numAtoms=2, numBonds=1, smartsString="[#6]-[#6]")

    with patch.object(mapping.rdFMCS, "FindMCS", return_value=fake_res):
        with pytest.raises(ValueError, match="coverage too low"):
            mapping._run_mcs(ref, target, timeout=1, min_coverage=0.9, verbose=False)


def test_run_mcs_is_element_aware():
    """Element-blind matching would report a 1-atom match; element-aware must not."""
    ref = Chem.MolFromSmiles("C")
    target = Chem.MolFromSmiles("O")

    with pytest.raises(ValueError, match="no common substructure"):
        mapping._run_mcs(ref, target, timeout=5, min_coverage=1.0, verbose=False)


def test_get_mcs_mapping_success():
    ref = Chem.MolFromSmiles("CCO")
    target = Chem.MolFromSmiles("CCO")
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None

    with patch.object(mapping, "_run_mcs", return_value=patt):
        got = mapping.get_mcs_mapping(ref, target)

    assert got == {0: 0, 1: 1}


def test_get_mcs_mapping_missing_match_raises():
    patt = Chem.MolFromSmarts("[#6]")
    assert patt is not None
    ref = MagicMock()
    target = MagicMock()
    ref.GetSubstructMatch.return_value = ()
    target.GetSubstructMatch.return_value = (0,)

    with patch.object(mapping, "_run_mcs", return_value=patt):
        with pytest.raises(ValueError, match="cannot map MCS back"):
            mapping.get_mcs_mapping(ref, target)


def test_aligned_rmsd_is_rigid_invariant():
    ref = _embed("CCO")
    target = _rigidly_transform(ref)
    identity = {i: i for i in range(ref.GetNumAtoms())}

    assert mapping._aligned_rmsd(ref, target, identity) < 1e-6


def test_best_mapping_for_chain_selects_aligned_mapping():
    """Selection must be based on aligned RMSD, robust to rigid transforms."""
    ref = _embed("FCCF")
    target = _rigidly_transform(ref)
    patt = mapping._run_mcs(ref, target, timeout=10, min_coverage=1.0, verbose=False)

    got = mapping._best_mapping_for_chain(ref, target, patt, [0])

    # Every reference atom must map to a target atom of the same element, and the
    # selected correspondence must superpose (near-zero aligned RMSD).
    for ref_idx, target_idx in got.items():
        assert (
            ref.GetAtomWithIdx(ref_idx).GetSymbol() == target.GetAtomWithIdx(target_idx).GetSymbol()
        )
    assert mapping._aligned_rmsd(ref, target, got) < 1e-3


def test_best_mapping_for_chain_without_coords_multiple_fails_closed():
    """Ambiguous mapping without coordinates must not silently pick the first."""
    ref = Chem.MolFromSmiles("CCC")  # no conformer
    target = Chem.MolFromSmiles("CCC")
    patt = Chem.MolFromSmarts("[#6]")
    assert patt is not None

    with pytest.raises(ValueError, match="no 3-D coordinates"):
        mapping._best_mapping_for_chain(ref, target, patt, [0])


def test_best_mapping_for_chain_without_coords_single_match_ok():
    ref = Chem.MolFromSmiles("CC")
    target = Chem.MolFromSmiles("CC")
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None

    # A single, chain-covering match is unambiguous even without a conformer.
    got = mapping._best_mapping_for_chain(ref, target, patt, [0, 1])
    assert sorted(got.items()) == [(0, 0), (1, 1)]


def test_transfer_chain_indices_missing_mapping_raises():
    ref = Chem.MolFromSmiles("CC")
    target = Chem.MolFromSmiles("CC")
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None

    with (
        patch.object(mapping, "_run_mcs", return_value=patt),
        patch.object(mapping, "_best_mapping_for_chain", return_value={0: 9}),
    ):
        with pytest.raises(ValueError, match="could not be mapped"):
            mapping.transfer_chain_indices(ref, target, [0, 1])


def test_transfer_chain_indices_success():
    ref = Chem.MolFromSmiles("CC")
    target = Chem.MolFromSmiles("CC")
    patt = Chem.MolFromSmarts("[#6]-[#6]")
    assert patt is not None

    with (
        patch.object(mapping, "_run_mcs", return_value=patt),
        patch.object(mapping, "_best_mapping_for_chain", return_value={0: 5, 1: 6}),
    ):
        got = mapping.transfer_chain_indices(ref, target, [0, 1])

    assert got == [5, 6]


def test_transfer_chain_indices_requires_full_coverage():
    """Main path must reject a 75% MCS that the old 70% default accepted."""
    ref = Chem.MolFromSmiles("CCCC")
    target = Chem.MolFromSmiles("CCC")

    with pytest.raises(ValueError, match="coverage too low"):
        mapping.transfer_chain_indices(ref, target, [0])


def test_transfer_chain_indices_requires_full_topology():
    """Same atom coverage is not enough: the full bond topology must match."""
    ref = Chem.MolFromSmiles("CCCC")  # butane: 4 atoms, 3 bonds
    target = Chem.MolFromSmiles("C1CCC1")  # cyclobutane: 4 atoms, 4 bonds

    with pytest.raises(ValueError, match="full molecular topology"):
        mapping.transfer_chain_indices(ref, target, [0])


def test_transfer_chain_indices_element_consistent():
    ref = _embed("OCCO")
    target = _rigidly_transform(ref)

    got = mapping.transfer_chain_indices(ref, target, [0, 1, 2, 3])

    assert sorted(got) == [0, 1, 2, 3]
    for ref_idx, target_idx in enumerate(got):
        assert (
            ref.GetAtomWithIdx(ref_idx).GetSymbol() == target.GetAtomWithIdx(target_idx).GetSymbol()
        )
