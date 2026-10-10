#!/usr/bin/env python3
"""DG seed starts for commanded coordination classes (TS1 + synthetic control)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from confflow.science.confgen.coordination import enumeration, perception, shapes
from confflow.science.confgen.dg_seed import (
    DGSeedError,
    DGSeedSettings,
    _candidate_bonds,
    _perceive_fragment,
    generate_dg_seeds,
)
from confflow.science.confgen.graph import (
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
    load_typed_topology,
    load_xyz_frame,
)

FIXTURE = Path("tests/fixtures/confgen/coordination/ts1")
OCTAHEDRAL = shapes.proper_rotation_group("octahedral")
OTHER_PLACEMENTS = ((0, 2, 3, 4, 1, 5), (0, 2, 3, 4, 5, 1))


def _load_ts1():
    graph, spec, _ = load_typed_topology(FIXTURE / "topology" / "typed_topology.json")
    _, xyz = load_xyz_frame(FIXTURE / "structures" / "ts1_original.xyz")
    return graph, spec, np.array(xyz, dtype=float)


def _perceived_class(coords, spec, shape="octahedral"):
    args = (spec.metal_center, spec.donor_indices, spec.site_ids, shape)
    return perception.perceive_donors(np.asarray(coords, dtype=float), *args).best_class


_SHARED = {}


def _shared(small_ring_torsions):
    if small_ring_torsions not in _SHARED:
        graph, spec, ref = _load_ts1()
        _SHARED[small_ring_torsions] = generate_dg_seeds(
            graph,
            spec,
            ref,
            list(_perceived_class(ref, spec)),
            "octahedral",
            DGSeedSettings(count=8, random_seed=11, small_ring_torsions=small_ring_torsions),
        )
    return _SHARED[small_ring_torsions]


def _dihedral(p0, p1, p2, p3):
    b0, b1, b2 = p1 - p0, p2 - p1, p3 - p2
    n0, n1 = np.cross(b0, b1), np.cross(b1, b2)
    axis = b1 / np.linalg.norm(b1)
    return float(np.degrees(np.arctan2(np.dot(np.cross(n0, n1), axis), np.dot(n0, n1))))


def _is_chair(coords, ring):
    torsions = [_dihedral(*(coords[ring[(k + j) % 6]] for j in range(4))) for k in range(6)]
    return all(abs(t) > 25.0 for t in torsions) and all(
        torsions[k] * torsions[(k + 1) % 6] < 0.0 for k in range(6)
    )


def _cyclohexane_ring(graph):
    adjacency = [set(graph.neighbors(i, EdgeType.COVALENT)) for i in range(graph.natoms)]
    rings = set()

    def _walk(start, node, path, seen):
        if len(path) == 6:
            if start in adjacency[node]:
                rings.add(tuple(sorted(path)))
            return
        for peer in adjacency[node] - seen - {start}:
            if peer > start:
                _walk(start, peer, path + [peer], seen | {peer})

    for start in [i for i in range(graph.natoms) if graph.atoms[i].element == "C"]:
        _walk(start, start, [start], {start})
    good = [
        ring
        for ring in rings
        if all(graph.atoms[i].element == "C" and len(adjacency[i]) == 4 for i in ring)
    ]
    assert len(good) == 1
    return good[0]


def _stereo_centres(graph, ref, charges):
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    bonds = _candidate_bonds(graph)
    elements = [atom.element for atom in graph.atoms]
    builder = Chem.RWMol()
    for index in range(graph.natoms):
        atom = Chem.Atom(elements[index])
        atom.SetNoImplicit(True)
        builder.AddAtom(atom)
    for indices, charge in charges:
        out = _perceive_fragment(list(indices), bonds, elements, ref, (charge,), explicit=True)
        orders, formal = out[0], out[1]
        for ends, order in orders.items():
            builder.AddBond(*ends, order)
        for index, value in formal.items():
            builder.GetAtomWithIdx(index).SetFormalCharge(value)
    conf = Chem.Conformer(graph.natoms)
    for index in range(graph.natoms):
        conf.SetAtomPosition(index, Point3D(*ref[index]))
    builder.AddConformer(conf, assignId=True)
    mol = builder.GetMol()
    flags = Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_PROPERTIES
    Chem.SanitizeMol(mol, sanitizeOps=flags)
    Chem.AssignStereochemistryFrom3D(mol)
    tagged = [a.GetIdx() for a in mol.GetAtoms() if "TETRAHEDRAL" in str(a.GetChiralTag())]
    order = {i: [n.GetIdx() for n in mol.GetAtomWithIdx(i).GetNeighbors()] for i in tagged}
    return tagged, order


def _signed_volume(coords, neighbours):
    points = [np.asarray(coords[i], dtype=float) for i in neighbours]
    return float(
        np.dot(points[1] - points[0], np.cross(points[2] - points[0], points[3] - points[0]))
    )


def test_reference_placement_starts_hold_commanded_geometry() -> None:
    graph, spec, ref = _load_ts1()
    commanded = list(_perceived_class(ref, spec))
    result = _shared(True)
    assert len(result.coords) > 0
    first, second = (int(v) for v in graph.reaction_pairs[0])
    expected_pair = float(np.linalg.norm(ref[first] - ref[second]))
    expected = [
        (d, float(np.linalg.norm(ref[spec.metal_center] - ref[d]))) for d in spec.donor_indices
    ]
    wanted = enumeration.canonical_representative(commanded, OCTAHEDRAL)
    for start in result.coords:
        got = enumeration.canonical_representative(_perceived_class(start, spec), OCTAHEDRAL)
        assert got == wanted
        assert abs(float(np.linalg.norm(start[first] - start[second])) - expected_pair) <= 0.25
        for donor, want in expected:
            gap = abs(float(np.linalg.norm(start[spec.metal_center] - start[donor])) - want)
            assert gap <= 0.05


def test_rearranged_placements_embed_into_commanded_class() -> None:
    graph, spec, ref = _load_ts1()
    reps = {
        c.representative for c in enumeration.enumerate_targets(spec, "octahedral")["shape_classes"]
    }
    assert set(OTHER_PLACEMENTS) <= reps
    fast = DGSeedSettings(count=2, random_seed=5, small_ring_torsions=False)
    for placement in OTHER_PLACEMENTS:
        result = generate_dg_seeds(graph, spec, ref, list(placement), "octahedral", fast)
        assert len(result.coords) > 0
        wanted = enumeration.canonical_representative(placement, OCTAHEDRAL)
        for start in result.coords:
            assert _perceived_class(start, spec) == wanted


def test_tetrahedral_carbon_stereo_preserved_in_every_start() -> None:
    graph, _, ref = _load_ts1()
    result = _shared(True)
    centres, order = _stereo_centres(graph, ref, result.fragment_charges)
    assert centres
    sign = {c: np.sign(_signed_volume(ref, order[c])) for c in centres}
    for centre in centres:
        assert len(order[centre]) == 4
        assert sign[centre] != 0.0
    for start in result.coords:
        for centre in centres:
            assert np.sign(_signed_volume(start, order[centre])) == sign[centre]


def test_small_ring_torsions_raise_chair_share() -> None:
    graph, _, ref = _load_ts1()
    ring = _cyclohexane_ring(graph)
    assert _is_chair(ref, ring)
    with_on = [_is_chair(s, ring) for s in _shared(True).coords]
    with_off = [_is_chair(s, ring) for s in _shared(False).coords]
    assert sum(with_on) / len(with_on) >= 0.5
    assert sum(with_on) / len(with_on) > sum(with_off) / len(with_off)


def test_deterministic_across_calls_and_sensitive_to_seed() -> None:
    graph, spec, ref = _load_ts1()
    commanded = list(_perceived_class(ref, spec))
    fast = DGSeedSettings(count=4, random_seed=11, small_ring_torsions=False)
    first = generate_dg_seeds(graph, spec, ref, commanded, "octahedral", fast)
    second = generate_dg_seeds(graph, spec, ref, commanded, "octahedral", fast)
    assert len(first.coords) == len(second.coords) > 0
    assert all(map(np.array_equal, first.coords, second.coords))
    next_seed = DGSeedSettings(count=4, random_seed=12, small_ring_torsions=False)
    other = generate_dg_seeds(graph, spec, ref, commanded, "octahedral", next_seed)
    assert any(not np.array_equal(one, two) for one, two in zip(other.coords, first.coords))


def test_fragment_charges_reported_and_overridable() -> None:
    graph, spec, ref = _load_ts1()
    commanded = list(_perceived_class(ref, spec))
    shared = _shared(True)
    auto = dict(shared.fragment_charges)
    assert dict(_shared(False).fragment_charges) == auto
    donors_n = [d for d in spec.donor_indices if graph.atoms[d].element == "N"]
    host = next(ix for ix, _ in shared.fragment_charges if all(d in ix for d in donors_n))
    assert auto[host] == -2
    assert sum(1 for d in spec.donor_indices if d in host and graph.atoms[d].element == "O") == 2
    pinned = ((host[0], 0),)
    override = DGSeedSettings(2, 11, False, fragment_charges=pinned)
    forced = generate_dg_seeds(graph, spec, ref, commanded, "octahedral", override)
    assert dict(forced.fragment_charges)[host] == 0
    assert len(forced.coords) > 0
    summary = forced.to_dict()
    assert summary["requested"] == 2 and summary["returned"] == len(forced.coords)
    json.dumps(summary)


def test_invalid_inputs_fail_closed() -> None:
    graph, spec, ref = _load_ts1()
    commanded = list(_perceived_class(ref, spec))
    bad_ref = ref.copy()
    bad_ref[0, 0] = np.nan
    standin = SimpleNamespace(id="X", atoms=(16, 18), hapticity=2)
    scope = SimpleNamespace(metal_center=spec.metal_center, binding_sites=(standin,))
    far_metal = CoordinationSpec(
        metal_center=999, binding_sites=spec.binding_sites, shapes=("octahedral",)
    )
    bad_sites = tuple(
        BindingSite(id=f"Q{i}", kind="atom", atoms=(d,), hapticity=1)
        for i, d in enumerate((1, 2, 3, 999))
    )
    far_donor = CoordinationSpec(metal_center=0, binding_sites=bad_sites, shapes=("tetrahedral",))
    calls = [
        lambda: generate_dg_seeds(graph, spec, ref[:10], commanded, "octahedral"),
        lambda: generate_dg_seeds(graph, spec, bad_ref, commanded, "octahedral"),
        lambda: generate_dg_seeds(graph, spec, ref, commanded, "tetrahedral"),
        lambda: generate_dg_seeds(graph, spec, ref, [0, 2, 4, 3, 1], "octahedral"),
        lambda: generate_dg_seeds(graph, spec, ref, [0, 2, 4, 3, 1, 1], "octahedral"),
        lambda: generate_dg_seeds(graph, spec, ref, [0, 2, 4, 3, 1, 6], "octahedral"),
        lambda: generate_dg_seeds(graph, spec, ref, [0, 2, 4, 3, 1, True], "octahedral"),
        lambda: generate_dg_seeds(graph, scope, ref, [0], "octahedral"),
        lambda: generate_dg_seeds(graph, far_metal, ref, commanded, "octahedral"),
        lambda: generate_dg_seeds(graph, far_donor, ref, [0, 1, 2, 3], "tetrahedral"),
        # Smoothing-infeasible commands fail closed before any embedding.
        lambda: generate_dg_seeds(graph, spec, ref, [0, 1, 2, 3, 4, 5], "octahedral"),
    ]
    settings_cases = [
        DGSeedSettings(count=0),
        DGSeedSettings(fragment_charges=((1,),)),
        DGSeedSettings(fragment_charges=((0, "x"),)),
        DGSeedSettings(fragment_charges=((9999, 0),)),
        DGSeedSettings(fragment_charges=((5, 0), (5, 1))),
        DGSeedSettings(fragment_charges=((0, 0), (1, 1))),
        DGSeedSettings(fragment_charges=((73, 3),)),
    ]
    for call in calls:
        with pytest.raises(DGSeedError):
            call()
    for settings in settings_cases:
        with pytest.raises(DGSeedError):
            generate_dg_seeds(graph, spec, ref, commanded, "octahedral", settings)


def test_missing_rdkit_fails_closed(monkeypatch) -> None:
    graph, spec, ref = _load_ts1()
    monkeypatch.setitem(sys.modules, "rdkit", None)
    with pytest.raises(DGSeedError):
        generate_dg_seeds(graph, spec, ref, list(_perceived_class(ref, spec)), "octahedral")


def _zinc_tetraammine():
    verts = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], dtype=float)
    verts /= np.linalg.norm(verts, axis=1, keepdims=True)
    elements, positions, links = ["Zn"], [np.zeros(3)], []
    tilt, span = np.cos(np.radians(112.0)) * 1.02, np.sin(np.radians(112.0)) * 1.02
    for vertex in verts:
        donor, across = vertex * 2.0, -vertex
        helper = np.eye(3)[np.argmin(np.abs(across))]
        first = helper - across * float(helper @ across)
        first /= np.linalg.norm(first)
        second = np.cross(across, first)
        angles = np.arange(3) * 2.094
        turn = np.cos(angles)[:, None] * first + np.sin(angles)[:, None] * second
        hydros = donor + tilt * across + span * turn
        index = len(elements)
        elements += ["N", "H", "H", "H"]
        positions += [donor, *hydros]
        links.append((0, index, EdgeType.COORDINATION))
        links += [(index, index + k, EdgeType.COVALENT) for k in (1, 2, 3)]
    atoms = tuple(AtomRef(index=i, element=e) for i, e in enumerate(elements))
    edges = tuple(TypedEdge(a=a, b=b, type=t) for a, b, t in links)
    graph = TypedGraph(atoms=atoms, edges=edges, metal_center=0)
    donors = sorted(graph.neighbors(0, EdgeType.COORDINATION))
    spec = CoordinationSpec(
        metal_center=0,
        binding_sites=tuple(
            BindingSite(id=f"D{i}", kind="atom", atoms=(d,), hapticity=1)
            for i, d in enumerate(donors)
        ),
        shapes=("tetrahedral",),
    )
    return graph, spec, np.array(positions)


def test_hand_built_tetrahedral_control_embeds_commanded_class() -> None:
    graph, spec, ref = _zinc_tetraammine()
    commanded = [0, 1, 2, 3]
    settings = DGSeedSettings(count=4, random_seed=3)
    result = generate_dg_seeds(graph, spec, ref, commanded, "tetrahedral", settings)
    assert len(result.coords) > 0
    group = shapes.proper_rotation_group("tetrahedral")
    wanted = enumeration.canonical_representative(commanded, group)
    for start in result.coords:
        assert _perceived_class(start, spec, "tetrahedral") == wanted
