"""Tetrahedral (sp3) stereo preservation: states, audit, embedding tags, flat references and stats.

Reaction-created centres are covered by a tetrahedral-intermediate-like TS whose stable graph has
three covalent neighbours on the centre and one FORMING partner, so the centre is a state only
when the reaction pair is declared.
"""

from __future__ import annotations

import numpy as np
from rdkit import Chem

from confflow.science.confgen import dg_seed, explore, search
from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph
from confflow.science.confgen.stereo import (
    FLAT_TETRAHEDRON,
    preserves_tetrahedra,
    tag_tetrahedra,
    tetrahedral_states,
    tetrahedron_sign,
)
from tests.v4._dg_search_helpers import _embed
from tests.v4.test_confgen_explore import _FakeCalc, _settings

_CHIRAL_TAGS = (Chem.ChiralType.CHI_TETRAHEDRAL_CW, Chem.ChiralType.CHI_TETRAHEDRAL_CCW)


def _graph_from_mol(mol, els, *, total_charge=0, reaction_pairs=()):
    """Typed graph of an RDKit molecule: covalent bonds at their RDKit orders plus FORMING pairs."""
    atoms = tuple(AtomRef(index=i, element=e) for i, e in enumerate(els))
    edges = [
        TypedEdge(
            b.GetBeginAtomIdx(), b.GetEndAtomIdx(), EdgeType.COVALENT, b.GetBondTypeAsDouble()
        )
        for b in mol.GetBonds()
    ]
    edges += [TypedEdge(a, b, EdgeType.FORMING) for a, b in reaction_pairs]
    return TypedGraph(
        atoms,
        tuple(edges),
        metal_center=None,
        reaction_pairs=tuple(tuple(p) for p in reaction_pairs),
        total_charge=total_charge,
        multiplicity=1,
    )


def _alcohol():
    """CC[C@H](O)Cl embedded with explicit hydrogens: stereocentre is atom 2."""
    mol, els, ref = _embed("CC[C@H](O)Cl")
    return mol, els, ref, _graph_from_mol(mol, els)


def _ts():
    """Tetrahedral-intermediate-like TS for CH3-C(F)(O-)-O-CH3 with C...O forming.

    Reference geometry comes from the tetrahedral CC(F)([O-])OC. In the stable graph the
    central carbon (atom 1) has CH3, F and =O (atom 3) covalently, while its fourth neighbour
    (methoxy O, atom 4) is only a FORMING partner. The methoxy fragment carries the -1 charge.
    """
    mol, els, ref = _embed("CC(F)([O-])OC")
    bonds = {tuple(sorted((b.GetBeginAtomIdx(), b.GetEndAtomIdx()))) for b in mol.GetBonds()}
    charges = {3: 0, 4: -1}
    atoms = tuple(
        AtomRef(index=i, element=e, formal_charge=charges.get(i, 0)) for i, e in enumerate(els)
    )
    edges = [
        TypedEdge(a, b, EdgeType.COVALENT, 2.0 if (a, b) == (1, 3) else 1.0)
        for a, b in sorted(bonds)
        if (a, b) != (1, 4)
    ]
    edges.append(TypedEdge(1, 4, EdgeType.FORMING))
    graph = TypedGraph(
        atoms,
        tuple(edges),
        None,
        ((1, 4),),
        "stereo_test",
        total_charge=-1,
        multiplicity=1,
    )
    return graph, ref


def test_states_keep_the_stereocentre_and_skip_ch2_and_ch3():
    mol, _els, ref, _graph = _alcohol()
    states, flat = tetrahedral_states(mol, ref, (), None)
    centers = [s.center for s in states]
    assert centers == [2]
    assert flat == ()
    # Heavy-atom neighbours of the stereocentre are C1, O3 and Cl4; the fourth is its hydrogen.
    assert len(states[0].neighbors) == 4
    assert {1, 3, 4} <= set(states[0].neighbors)
    assert states[0].embeddable
    assert 1 not in centers  # CH2: two interchangeable hydrogens
    assert 0 not in centers  # CH3: three interchangeable hydrogens


def test_hydrogen_swap_on_ch2_passes_and_mirror_image_fails_stereo():
    mol, els, ref, graph = _alcohol()
    assert search.audit_structure(ref, graph, None, ref, None).failed_checks == ()
    hydrogens = [n.GetIdx() for n in mol.GetAtomWithIdx(1).GetNeighbors() if n.GetSymbol() == "H"]
    assert len(hydrogens) == 2
    swapped = np.array(ref, copy=True)
    swapped[hydrogens] = swapped[hydrogens[::-1]]
    assert search.audit_structure(swapped, graph, None, ref, None).failed_checks == ()
    mirrored = np.array(ref, copy=True)
    mirrored[:, 0] *= -1.0
    assert search.audit_structure(mirrored, graph, None, ref, None).failed_checks == ("stereo",)


def test_reaction_created_centre_is_a_state_only_with_its_forming_pair():
    graph, ref = _ts()
    stable = dg_seed.stable_chemistry_molecule(graph, ref)[0]
    states, _flat = tetrahedral_states(stable, ref, graph.reaction_pairs, graph.metal_center)
    by_center = {s.center: s for s in states}
    assert 1 in by_center
    assert by_center[1].neighbors == (0, 2, 3, 4)
    assert by_center[1].sign == tetrahedron_sign(ref, (0, 2, 3, 4))
    assert by_center[1].sign != 0
    assert by_center[1].embeddable
    # Without the forming pair the stable centre has three neighbours: not a state.
    bare, _ = tetrahedral_states(stable, ref, (), graph.metal_center)
    assert 1 not in {s.center for s in bare}
    # The mirror image of the reaction centre fails the stereo audit.
    mirrored = np.array(ref, copy=True)
    mirrored[:, 0] *= -1.0
    assert search.audit_structure(mirrored, graph, None, ref, None).failed_checks == ("stereo",)
    assert search.audit_structure(ref, graph, None, ref, None).failed_checks == ()


def test_generated_starts_all_keep_the_reference_hand_of_the_reaction_centre():
    graph, ref = _ts()
    expected = tetrahedron_sign(ref, (0, 2, 3, 4))
    settings = dg_seed.DGSeedSettings(count=8, random_seed=7, timeout_seconds=10)
    out = dg_seed.generate_dg_seeds(graph, None, ref, [], "bogus", settings)
    assert len(out.coords) >= 1
    for coords in out.coords:
        assert tetrahedron_sign(coords, (0, 2, 3, 4)) == expected
    stable = dg_seed.stable_chemistry_molecule(graph, ref)[0]
    states, _ = tetrahedral_states(stable, ref, graph.reaction_pairs, None)
    assert all(preserves_tetrahedra(coords, states) for coords in out.coords)


def test_tag_tetrahedra_tags_every_embeddable_centre():
    mol, _els, ref, _graph = _alcohol()
    states, _ = tetrahedral_states(mol, ref, (), None)
    tagged = tag_tetrahedra(mol, states, ref)
    embeddable = [s.center for s in states if s.embeddable]
    assert embeddable == [2]
    for center in embeddable:
        assert tagged.GetAtomWithIdx(center).GetChiralTag() in _CHIRAL_TAGS
    # Untagged CH2/CH3 carbons are not given configurations.
    assert tagged.GetAtomWithIdx(1).GetChiralTag() not in _CHIRAL_TAGS
    assert tagged.GetAtomWithIdx(0).GetChiralTag() not in _CHIRAL_TAGS


def test_embedding_molecule_tags_the_reaction_centre():
    graph, ref = _ts()
    stable = dg_seed.stable_chemistry_molecule(graph, ref)[0]
    states, _ = tetrahedral_states(stable, ref, graph.reaction_pairs, None)
    embedding, _relaxed = dg_seed.embedding_molecule(graph, stable, ref, ())
    for state in states:
        if state.embeddable:
            assert embedding.GetAtomWithIdx(state.center).GetChiralTag() in _CHIRAL_TAGS
    # Re-tagging the embedding copy from the reference (after clearing the tag) reproduces it.
    # The stable molecule alone cannot carry the centre's tag: its forming bond is absent there.
    cleared = Chem.RWMol(embedding)
    cleared.GetAtomWithIdx(1).SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    retagged = tag_tetrahedra(cleared.GetMol(), states, ref)
    assert retagged.GetAtomWithIdx(1).GetChiralTag() == embedding.GetAtomWithIdx(1).GetChiralTag()


def _planar_centre(z_of_br: float):
    """FC(Cl)(Br)I: central carbon 1 with F0, Cl2, Br3, I4; Br3 is lifted out of the plane."""
    mol = Chem.MolFromSmiles("FC(Cl)(Br)I")
    ref = np.array(
        [
            [1.0, 0.0, 0.0],  # F
            [0.0, 0.0, 0.0],  # C
            [0.0, 1.0, 0.0],  # Cl
            [-1.0, 0.0, z_of_br],  # Br
            [0.0, -1.0, 0.0],  # I
        ]
    )
    return mol, ref


def test_flat_reference_centre_is_reported_not_enforced():
    mol, flat_ref = _planar_centre(0.0)
    states, flat = tetrahedral_states(mol, flat_ref, (), None)
    assert states == ()
    assert flat == (1,)
    # Control: the same centre pulled out of the plane is an enforced state.
    mol, puckered = _planar_centre(1.0)
    states, flat = tetrahedral_states(mol, puckered, (), None)
    assert [s.center for s in states] == [1]
    assert flat == ()


def test_tetrahedron_sign_values_and_mirror():
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    sign = tetrahedron_sign(coords, (0, 1, 2, 3))
    assert sign in (1, -1)
    mirrored = coords.copy()
    mirrored[:, 0] *= -1.0
    assert tetrahedron_sign(mirrored, (0, 1, 2, 3)) == -sign
    # Swapping two labelled neighbours inverts the hand.
    assert tetrahedron_sign(coords, (0, 1, 3, 2)) == -sign
    # Coincident neighbours and coplanar neighbours are undefined.
    coincident = coords.copy()
    coincident[1] = coincident[0]
    assert tetrahedron_sign(coincident, (0, 1, 2, 3)) == 0
    coplanar = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0, -1.0, 0.0]])
    assert tetrahedron_sign(coplanar, (0, 1, 2, 3)) == 0
    assert FLAT_TETRAHEDRON > 0.0


def test_explorer_reports_tetrahedral_stereo_stats_with_the_stereocentre_enforced():
    mol, _els, ref, graph = _alcohol()
    run = explore.explore(graph, None, ref, None, _settings(budget=20), _FakeCalc(), map)
    block = run.stats["tetrahedral_stereo"]
    assert set(block) == {"states", "embedding_enforced", "filter_only", "flat_reference"}
    assert 2 in block["embedding_enforced"]
    assert block["filter_only"] == []
    assert block["flat_reference"] == []
    assert [state["center"] for state in block["states"]] == [2]
