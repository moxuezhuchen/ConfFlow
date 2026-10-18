"""E/Z preservation covers standalone audit, embedding, backend results and pool identity."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from confflow.science.confgen import dg_seed, explore, search
from confflow.science.confgen.calculator import DistanceRestraint, RelaxOutcome
from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph
from confflow.science.confgen.identity import Identity, MinimaPool
from confflow.science.confgen.stereo import double_bond_states, preserves_double_bonds
from confflow.workflow.v4.confgen_schema import ConfgenSearchModel
from tests.v4.test_confgen_explore import _FakeCalc, _settings


def _alkenes():
    molecules = []
    for smiles in ("C/C=C/C", "C/C=C\\C"):
        mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
        assert AllChem.EmbedMolecule(mol, randomSeed=12) == 0
        AllChem.MMFFOptimizeMolecule(mol)
        molecules.append(mol)
    mol = molecules[0]
    graph = TypedGraph(
        tuple(
            AtomRef(a.GetIdx(), a.GetSymbol(), formal_charge=a.GetFormalCharge())
            for a in mol.GetAtoms()
        ),
        tuple(
            TypedEdge(
                b.GetBeginAtomIdx(), b.GetEndAtomIdx(), EdgeType.COVALENT, b.GetBondTypeAsDouble()
            )
            for b in mol.GetBonds()
        ),
        total_charge=0,
        multiplicity=1,
    )
    return graph, *(np.array(m.GetConformer().GetPositions()) for m in molecules)


def test_standalone_audit_rejects_opposite_ez_without_tetrahedral_atom_tags():
    graph, reference, opposite = _alkenes()
    assert not search.audit_structure(reference, graph, None, reference, None).failed_checks
    assert search.audit_structure(opposite, graph, None, reference, None).failed_checks == (
        "double_bond_stereo",
    )
    collapsed = reference.copy()
    collapsed[0] = collapsed[1]  # undefined substituent direction is not an admissible state
    assert (
        "double_bond_stereo"
        in search.audit_structure(collapsed, graph, None, reference, None).failed_checks
    )


def test_global_and_local_generation_preserve_ez():
    graph, reference, _ = _alkenes()
    states = double_bond_states(dg_seed.stable_chemistry_molecule(graph, reference)[0], reference)
    out = dg_seed.generate_dg_seeds(
        graph,
        None,
        reference,
        (),
        None,
        dg_seed.DGSeedSettings(count=4, random_seed=12, timeout_seconds=5),
    )
    assert len(out.coords) == 4
    assert all(preserves_double_bonds(x, states) for x in out.coords)
    coords, error = explore._reembed_task(
        (graph, reference, frozenset(range(graph.natoms)), (), 12, (), 5)
    )
    assert error is None and preserves_double_bonds(coords, states)


@pytest.mark.parametrize("arm", ["single", "lowmode"])
def test_feedback_generation_rejects_ez_inversion_before_optimization(monkeypatch, arm):
    graph, reference, opposite = _alkenes()
    calc = _FakeCalc()
    ex = explore._Explorer(graph, None, reference, None, _settings(), calc, map, None)
    ex.pool.add(-1.0, reference, "t00", "test")
    monkeypatch.setattr(
        ex,
        "_single_kids" if arm == "single" else "_lowmode_kids",
        lambda parent: [("inverted", opposite.copy())],
    )
    assert not ex._moves(arm, 2)
    assert not calc.requests
    assert ex.fail["generated_double_bond_stereo"] == 1


def test_backend_inversion_is_rejected_during_search_and_release():
    graph, reference, opposite = _alkenes()

    class InvertingCalc(_FakeCalc):
        def relax_many(self, requests):
            return [RelaxOutcome(opposite.copy(), -1.0, True) for _ in requests]

    run = explore.explore(graph, None, reference, None, _settings(budget=1), InvertingCalc(), map)
    assert run.stats["failures"]["double_bond_stereo"] == 1
    assert not run.records
    ex = explore._Explorer(graph, None, reference, None, _settings(), InvertingCalc(), map, None)
    ex.restraints = (DistanceRestraint(0, 3, float(np.linalg.norm(reference[0] - reference[3]))),)
    ex.free_restraints = ()
    ex.pool.add(-1.0, reference, "t00", "test")
    final = ex._release()
    assert ex.release_status == "failed"
    assert ex.fail["double_bond_stereo"] == 1
    assert preserves_double_bonds(final.minima[0].coords, ex.double_bond_states)


def test_opposite_ez_cannot_merge_under_a_loose_rmsd_cutoff_serial_or_parallel():
    graph, reference, opposite = _alkenes()
    for parallel in (None, map):
        identity = Identity(graph, r_tol=10.0)
        pool = MinimaPool(identity)
        rows = [(-1.0, x, "t00", "test", "") for x in (reference, opposite) * 4]
        pool.add_many(rows, parallel, chunks=1)
        assert len(pool) == 2
        assert identity.rmsd(reference, opposite) == float("inf")
        np.testing.assert_allclose(identity.rmsd(reference, -reference), 0.0, atol=1e-6)


def test_ez_state_policy_is_explicit_and_unsupported_enumeration_fails_closed():
    assert ConfgenSearchModel().ez_policy == "preserve"
    with pytest.raises(ValueError):
        ConfgenSearchModel(ez_policy="enumerate")
    graph, reference, _ = _alkenes()
    with pytest.raises(search.SearchError, match="enumeration is not implemented"):
        explore.explore(
            graph,
            None,
            reference,
            None,
            replace(_settings(), ez_policy="enumerate"),
            _FakeCalc(),
            map,
        )
