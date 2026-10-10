#!/usr/bin/env python3
"""ConfGen coordination-geometry tests (TS1 fixture and synthetic controls).

Surviving surface: typed-edge and graph-contract authority, ideal shape groups
and class counts, the policy-filtered enumeration on the TS1 fixture (site-group
closure, Burnside counts, exclusions as policy), typed-graph fixture integrity,
SCINE F1-F6 donor perception with its source-provenanced convention gap, the
declared-proof donor bounds, and the no-legacy/no-energy import boundary of the
science modules that remain. The rigid/flexible realization, H_geom, symmetry
search, staged coordination engine and their fixtures' tests were deleted with
those engines.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from confflow.science.confgen.coordination import enumeration, perception, shapes
from confflow.science.confgen.coordination.enumeration import (
    close_site_group,
    declared_site_group,
    enumerate_targets,
    molecular_burnside_count,
    site_group_orbits,
)
from confflow.science.confgen.graph import (
    AtomRef,
    BindingSite,
    CoordinationSpec,
    DonorBoundProof,
    EdgeType,
    ForbiddenTrans,
    TypedEdge,
    TypedGraph,
    UnsupportedTopologyError,
    extract_coordination_map,
    load_typed_topology,
    load_xyz_frame,
    normalize_edge_kind,
)

FIXTURE = Path("tests/fixtures/confgen/coordination/ts1")
TOPOLOGY = FIXTURE / "topology" / "typed_topology.json"
BENCHMARK = FIXTURE / "benchmark" / "expected_coordination_benchmark.json"
CONSTRAINTS = FIXTURE / "benchmark" / "coordination_constraints.json"
SCINE_META = FIXTURE / "benchmark" / "scine_F1-F6_expected.json"
SCINE_GENERATION_META = FIXTURE / "source" / "scine_generation_metadata.json"
SCINE_REPORT = FIXTURE / "source" / "scine_F1-F6_report.txt"
STRUCTURES = FIXTURE / "structures"

EXPECTED_GROUP_ORDERS = {
    "tetrahedral": 12,
    "square_planar": 8,
    "trigonal_bipyramidal": 6,
    "square_pyramidal": 4,
    "octahedral": 24,
    "trigonal_prismatic": 6,
}

EXPECTED_CLASS_COUNTS = {
    "tetrahedral": 2,
    "square_planar": 3,
    "trigonal_bipyramidal": 20,
    "square_pyramidal": 30,
    "octahedral": 30,
    "trigonal_prismatic": 120,
}


def _load_spec() -> tuple[TypedGraph, CoordinationSpec]:
    graph, base, _ = load_typed_topology(TOPOLOGY)
    raw_constraints = json.loads(CONSTRAINTS.read_text())["constraints"]
    constraints = tuple(
        ForbiddenTrans(
            id=entry["id"],
            sites=(entry["sites"][0], entry["sites"][1]),
            classification=entry["classification"],
            provenance=entry["source"],
        )
        for entry in raw_constraints
    )
    spec = CoordinationSpec(
        metal_center=base.metal_center,
        binding_sites=base.binding_sites,
        shapes=("octahedral",),
        constraints=constraints,
    )
    return graph, spec


def _sigma_site_perm() -> tuple[int, ...]:
    return tuple(json.loads(BENCHMARK.read_text())["sigma_site_permutation_0based"])


def _original_coords() -> np.ndarray:
    _, xyz = load_xyz_frame(STRUCTURES / "ts1_original.xyz")
    return np.array(xyz, dtype=float)


# ---------------------------------------------------------------------------
# Graph contract: single typed-graph authority
# ---------------------------------------------------------------------------


def test_edge_kinds_uppercase_with_breaking_and_normalization() -> None:
    assert normalize_edge_kind("COVALENT") is EdgeType.COVALENT
    assert normalize_edge_kind("covalent") is EdgeType.COVALENT
    assert normalize_edge_kind("BREAKING") is EdgeType.BREAKING
    assert normalize_edge_kind("breaking") is EdgeType.BREAKING
    with pytest.raises(UnsupportedTopologyError):
        normalize_edge_kind("aromatic")


def test_typed_edge_bond_order_lossless_and_provenance() -> None:
    edge = TypedEdge(a=0, b=1, type="covalent", bond_order=1.5, provenance="perceived")
    assert edge.type is EdgeType.COVALENT
    assert edge.bond_order == 1.5
    assert edge.pair == (0, 1)
    with pytest.raises(UnsupportedTopologyError):
        TypedEdge(a=0, b=1, type="covalent", bond_order=9.0)
    with pytest.raises(UnsupportedTopologyError):
        TypedEdge(a=0, b=1, type="covalent", provenance="")


def test_atom_roles_and_stereo_declarations_lossless() -> None:
    atom = AtomRef(index=3, element="C", label="C79", role="forming_atom", stereo="R")
    assert atom.role == "forming_atom"
    assert atom.stereo == "R"
    graph, _ = _load_spec()
    assert graph.atoms[46].role == "metal_center"
    assert graph.atoms[16].role == "binding_site:N17"
    assert graph.atoms[73].role == "binding_site:O74"
    assert graph.atoms[78].role == "forming_atom"


def test_covalent_adjacency_excludes_non_covalent_kinds() -> None:
    graph, _ = _load_spec()
    adjacency = graph.covalent_adjacency()
    assert len(adjacency) == graph.n_atoms == graph.natoms
    for edge in graph.edges:
        if edge.type is EdgeType.COVALENT:
            assert edge.b in adjacency[edge.a] and edge.a in adjacency[edge.b]
        else:
            assert edge.b not in adjacency[edge.a]
    assert graph.edge_kind(73, 78) is EdgeType.FORMING
    assert sorted(graph.pairs_of_kind("COORDINATION")) == sorted(
        (min(46, d), max(46, d)) for d in (16, 18, 44, 45, 73, 79)
    )


def test_mapping_constructor_both_conventions() -> None:
    graph, _ = _load_spec()
    elements = list(graph.elements)
    mapping1 = graph.to_mapping(convention="fixture1")
    rebuilt = TypedGraph.from_mapping(mapping1, elements, convention="fixture1")
    assert rebuilt.edge_set() == graph.edge_set()
    assert rebuilt.elements == graph.elements
    assert [a.role for a in rebuilt.atoms] == [a.role for a in graph.atoms]
    mapping0 = graph.to_mapping(convention="internal0")
    rebuilt0 = TypedGraph.from_mapping(mapping0, elements, convention="internal0")
    assert rebuilt0.edge_set() == graph.edge_set()
    with pytest.raises(UnsupportedTopologyError):
        TypedGraph.from_mapping(mapping1, elements, convention="mystery")
    with pytest.raises(UnsupportedTopologyError):
        TypedGraph.from_mapping({"bonds": [["a", "b"]]}, elements, convention="fixture1")


# ---------------------------------------------------------------------------
# Template groups
# ---------------------------------------------------------------------------


def test_shape_groups_have_expected_orders_and_axioms() -> None:
    for name, order in EXPECTED_GROUP_ORDERS.items():
        group = shapes.proper_rotation_group(name)
        assert len(group) == order, name
        axioms = shapes.verify_group_axioms(group)
        assert axioms["has_identity"] and axioms["closed"] and axioms["has_inverses"], name


def test_shape_class_counts_match_gates() -> None:
    for name, count in EXPECTED_CLASS_COUNTS.items():
        assert shapes.shape_class_count(name) == count, name


def test_octahedral_group_matches_fixture_convention() -> None:
    expected = json.loads(BENCHMARK.read_text())
    fixture_group = {
        tuple(perm)
        for perm in expected["vertex_convention"]["proper_rotations_vertex_permutations"]
    }
    assert set(shapes.proper_rotation_group("octahedral")) == fixture_group


def test_spec_auto_expands_shapes_per_cn() -> None:
    def _spec(count: int) -> CoordinationSpec:
        sites = tuple(
            BindingSite(id=f"S{i}", kind="atom", atoms=(100 + i,), hapticity=1)
            for i in range(count)
        )
        return CoordinationSpec(metal_center=0, binding_sites=sites, shapes=("auto",))

    assert _spec(4).shapes == ("tetrahedral", "square_planar")
    assert _spec(5).shapes == ("trigonal_bipyramidal", "square_pyramidal")
    assert _spec(6).shapes == ("octahedral", "trigonal_prismatic")


def test_spec_fail_closed_outside_scope() -> None:
    with pytest.raises(UnsupportedTopologyError):
        BindingSite(id="chel", kind="atom", atoms=(1, 2), hapticity=2)
    with pytest.raises(UnsupportedTopologyError):
        CoordinationSpec(
            metal_center=0,
            binding_sites=tuple(
                BindingSite(id=f"S{i}", kind="atom", atoms=(10 + i,), hapticity=1) for i in range(3)
            ),
        )
    _, base = _load_spec()
    with pytest.raises(UnsupportedTopologyError):
        CoordinationSpec(
            metal_center=base.metal_center,
            binding_sites=base.binding_sites,
            shapes=("tetrahedral",),
        )


# ---------------------------------------------------------------------------
# TS1 count layers 720 -> 288 -> 12 -> 6 with declared site group
# ---------------------------------------------------------------------------


def test_ts1_count_layers_match_fixture() -> None:
    _, spec = _load_spec()
    pipeline = enumerate_targets(spec, "octahedral", _sigma_site_perm())
    layers = pipeline["layers"]
    assert layers.raw_assignments == 720
    assert layers.admissible == 288
    assert layers.shape_classes_before_policy == 30
    assert layers.shape_classes == 12
    assert layers.molecular_orbits == 6
    assert layers.exact is True
    assert pipeline["burnside"]["orbit_count"] == 12
    assert pipeline["burnside"]["fixed_point_sum"] == 288
    molecular = pipeline["molecular_burnside"]
    assert molecular["orbit_count"] == 6
    assert molecular["fixed_point_counts"] == [12, 0]
    assert "DECLARED site group" in pipeline["orbit_audit"]["certification_scope"]


def test_ts1_representatives_and_sigma_orbits_match_fixture() -> None:
    _, spec = _load_spec()
    expected = json.loads(BENCHMARK.read_text())
    pipeline = enumerate_targets(spec, "octahedral", _sigma_site_perm())
    expected_reps = sorted(
        tuple(rep["placement_vertex_indices"]) for rep in expected["shape_orbit_representatives"]
    )
    assert sorted(cls.representative for cls in pipeline["shape_classes"]) == expected_reps
    expected_orbits = sorted(sorted(orbit["members"]) for orbit in expected["sigma_orbits"])
    assert (
        sorted(sorted(orbit.members) for orbit in pipeline["molecular_orbits"]) == expected_orbits
    )


def test_ts1_exclusions_are_policy_not_proof() -> None:
    _, spec = _load_spec()
    pipeline = enumerate_targets(spec, "octahedral")
    layers = pipeline["layers"]
    assert layers.shape_classes_before_policy - layers.shape_classes == 18
    assert layers.proof_excluded == 0
    assert layers.policy_excluded == 720 - 288
    verdicts = {item.verdict for item in pipeline["excluded"]}
    assert verdicts == {"REJECTED_BY_POLICY"}


def test_site_group_closure_and_completeness() -> None:
    sigma_group = declared_site_group(_load_spec()[1], site_permutation=_sigma_site_perm())
    assert sigma_group.complete is True
    assert sigma_group.order == 2
    general = close_site_group([(1, 0, 2), (0, 2, 1)], 3)
    assert general.complete is True
    assert general.order == 6
    axioms = shapes.verify_group_axioms(general.elements)
    assert axioms["closed"] and axioms["has_inverses"]
    tiny = close_site_group([(1, 0, 3, 2, 4, 5)], 6, budget=1)
    assert tiny.complete is False
    assert tiny.order is None


def test_molecular_orbits_refuse_incomplete_group() -> None:
    _, spec = _load_spec()
    pipeline = enumerate_targets(spec, "octahedral")
    group = shapes.proper_rotation_group("octahedral")
    tiny = close_site_group([(1, 0, 3, 2, 4, 5)], 6, budget=1)
    with pytest.raises(ValueError, match="[Cc]omplete|incomplete"):
        site_group_orbits(pipeline["shape_classes"], group, tiny, spec)
    with pytest.raises(ValueError, match="[Cc]omplete|incomplete"):
        molecular_burnside_count(pipeline["shape_classes"], group, tiny)


def test_noninvariant_constraints_refuse_group_accounting() -> None:
    _, spec = _load_spec()
    reduced = CoordinationSpec(
        metal_center=spec.metal_center,
        binding_sites=spec.binding_sites,
        shapes=("octahedral",),
        constraints=tuple(c for c in spec.constraints if c.id != "C03"),
    )
    invariant, reasons = enumeration.check_constraint_invariance(reduced, _sigma_site_perm())
    assert invariant is False and reasons
    pipeline = enumerate_targets(reduced, "octahedral")
    site_group = declared_site_group(reduced, site_permutation=_sigma_site_perm())
    with pytest.raises(ValueError, match="[Ii]nvariant"):
        site_group_orbits(
            pipeline["shape_classes"],
            shapes.proper_rotation_group("octahedral"),
            site_group,
            reduced,
        )


# ---------------------------------------------------------------------------
# Budgeted automorphism search
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Typed graph loading and pure mapper
# ---------------------------------------------------------------------------


def test_typed_graph_fixture_integrity_and_map() -> None:
    graph, spec = _load_spec()
    assert graph.natoms == 122
    assert len(graph.edges) == 131
    assert sum(1 for e in graph.edges if e.type is EdgeType.COORDINATION) == 6
    assert sum(1 for e in graph.edges if e.type is EdgeType.FORMING) == 1
    assert graph.metal_center == 46
    assert spec.donor_indices == (16, 18, 44, 45, 73, 79)
    assert graph.reaction_pairs == ((73, 78),)
    cmap = extract_coordination_map(graph, spec)
    assert cmap.metal_center == 46
    assert cmap.site_donors["N17"] == 16
    assert cmap.site_donors["O80"] == 79
    assert 78 not in cmap.covalent_adjacency[73]
    assert 73 not in cmap.covalent_adjacency[78]


def test_atom_order_fixed_across_frames() -> None:
    reference: tuple[str, ...] | None = None
    for name in ["ts1_original", *[f"scine_F{i}" for i in range(1, 7)]]:
        elements, _ = load_xyz_frame(STRUCTURES / f"{name}.xyz")
        if reference is None:
            reference = elements
        assert elements == reference
    with pytest.raises(UnsupportedTopologyError):
        TypedGraph(
            atoms=(AtomRef(index=1, element="H"), AtomRef(index=0, element="H")),
            edges=(),
        )


# ---------------------------------------------------------------------------
# SCINE F1-F6 perception audit with source-provenanced convention gap
# ---------------------------------------------------------------------------


def _perceive(name: str) -> perception.PerceptionResult:
    _, spec = _load_spec()
    _, xyz = load_xyz_frame(STRUCTURES / f"{name}.xyz")
    return perception.perceive_donors(
        np.array(xyz, dtype=float),
        spec.metal_center,
        spec.donor_indices,
        spec.site_ids,
        "octahedral",
    )


def test_scine_frames_perceive_unambiguously_with_margins() -> None:
    for index in range(1, 7):
        result = _perceive(f"scine_F{index}")
        assert result.best_rmsd <= 0.35, (index, result.best_rmsd)
        assert result.margin >= 0.5, (index, result.margin)
        assert result.unambiguous is True, (index, result.boundary_flags)


def test_scine_perceived_classes_are_policy_admissible() -> None:
    _, spec = _load_spec()
    template = shapes.get_shape("octahedral")
    for index in range(1, 7):
        result = _perceive(f"scine_F{index}")
        assert enumeration._placement_violations(result.best_class, spec, template) == []


def test_scine_declared_maps_convention_gap_is_source_provenanced() -> None:
    """SCINE vertex maps cannot be compared label-for-label with the fixture.

    Provenance: ``source/scine_generation_metadata.json`` records SCINE's own
    ``vertex_map``/``index_of_permutation`` assignments (seven records, note:
    "approximate rigid-body embeddings ... not re-optimized") and
    ``source/scine_F1-F6_report.txt`` repeats them, but neither source
    declares the fixture's vertex-axis convention
    (``benchmark/expected_coordination_benchmark.json`` vertex_convention with
    opposite_pairs).  The bundled six frames skip metadata record 0 (the
    identity map).  Canonicalized SCINE maps land on policy-rejected classes
    while independent geometric perception lands on admissible classes with
    large margins, so the audit reports the gap instead of forcing agreement.
    Goldens are never mutated.
    """
    assert SCINE_GENERATION_META.exists() and SCINE_REPORT.exists()
    generation = json.loads(SCINE_GENERATION_META.read_text())
    assert "approximate rigid-body embeddings" in generation["note"]
    assert len(generation["frames"]) == 7
    bundled = json.loads(SCINE_META.read_text())["frames"]
    assert len(bundled) == 6
    bundled_maps = [tuple(entry["scine_vertex_map"]) for entry in bundled]
    assert tuple(generation["frames"][0]["vertex_map"]) not in bundled_maps
    _, spec = _load_spec()
    template = shapes.get_shape("octahedral")
    group = shapes.proper_rotation_group("octahedral")
    for entry in bundled:
        perceived = _perceive(entry["file"].replace(".xyz", ""))
        scine_canon = enumeration.canonical_representative(tuple(entry["scine_vertex_map"]), group)
        assert scine_canon != perceived.best_class, entry["file"]
        assert enumeration._placement_violations(scine_canon, spec, template) != [], entry["file"]


def test_ts1_original_perceives_to_own_class() -> None:
    _, spec = _load_spec()
    pipeline = enumerate_targets(spec, "octahedral")
    class_ids = {cls.representative: cls.id for cls in pipeline["shape_classes"]}
    result = _perceive("ts1_original")
    assert result.best_class == (0, 2, 4, 3, 1, 5)
    assert class_ids[result.best_class] == "L04"


# ---------------------------------------------------------------------------
# Rigid-fragment realization: TS1 honest spike
# ---------------------------------------------------------------------------

SPIKE_OPTIONS = {
    "max_nfev": 120,
    "realize_tol": 0.5,
    "reaction_tol": 0.25,
}


# ---------------------------------------------------------------------------
# Synthetic monodentate + bidentate rigid-fragment successes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# H_geom: physical C2 control (proper spatial rotation, no averaging)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Donor-distance proof discipline
# ---------------------------------------------------------------------------


def _ts1_pair_proof(site_a: str, site_b: str, realize_tol: float = 0.5) -> DonorBoundProof | None:
    """Build an evidence-backed proof for a TS1 donor pair, or ``None``.

    Computes the same-fragment separation interval and the trans separation
    requirement from the input geometry; returns a proof only when the
    interval soundly excludes trans placement under the rigid+flex model.
    """
    from confflow.science.confgen.graph import (
        BoundEvidence,
        fragment_distance_interval,
    )

    graph, spec = _load_spec()
    donors = dict(zip(spec.site_ids, spec.donor_indices))
    coords = _original_coords()
    interval = fragment_distance_interval(graph, coords, donors[site_a], donors[site_b])
    if interval is None:
        return None
    center = coords[spec.metal_center]
    radius_a = float(np.linalg.norm(coords[donors[site_a]] - center))
    radius_b = float(np.linalg.norm(coords[donors[site_b]] - center))
    trans = (radius_a + radius_b - 2 * realize_tol, radius_a + radius_b + 2 * realize_tol)
    evidence = BoundEvidence(
        pair=(site_a, site_b),
        interval=interval,
        trans_required=trans,
        basis="same rigid fragment separation plus frozen bond/angle flex slop",
    )
    if not evidence.excludes_trans():
        return None
    return DonorBoundProof(
        pair=(site_a, site_b),
        max_distance=interval[1],
        declared_bounds=(("max_separation", interval[1]),),
        enforced_by="scipy-lbfgsb-flexible-internal-ls",
        audit_ref="realization-audit:fragment-interval",
        evidence=evidence,
    )


def test_bound_evidence_excludes_trans_for_rigid_pairs() -> None:
    from confflow.science.confgen.graph import BoundEvidence, verify_bound_evidence

    n17_n19 = _ts1_pair_proof("N17", "N19")
    assert n17_n19 is not None and n17_n19.is_enforced() is True
    assert verify_bound_evidence(n17_n19.evidence) is True
    o74_o80 = _ts1_pair_proof("O74", "O80")
    assert o74_o80 is not None and o74_o80.is_enforced() is True
    # Overlapping intervals prove nothing: C02/C03 stay policy.
    assert _ts1_pair_proof("N17", "O46") is None
    assert _ts1_pair_proof("N19", "O45") is None
    # Cross-fragment pairs have no computable interval: unsupported.
    assert _ts1_pair_proof("N17", "O74") is None
    # Tampered thresholds and non-finite values fail verification.
    assert n17_n19.evidence is not None
    tampered = BoundEvidence(
        pair=n17_n19.evidence.pair,
        interval=n17_n19.evidence.interval,
        trans_required=(0.0, 100.0),
        basis=n17_n19.evidence.basis,
    )
    assert verify_bound_evidence(tampered) is False
    assert verify_bound_evidence("not-evidence") is False  # type: ignore[arg-type]


def test_donor_bound_proof_requires_verified_evidence() -> None:
    proof = _ts1_pair_proof("N17", "N19")
    assert proof is not None and proof.is_enforced() is True
    # String-only records (no evidence) never constitute proof.
    assert DonorBoundProof(pair=("N17", "N19")).is_enforced() is False
    stringy = DonorBoundProof(
        pair=("N17", "N19"),
        max_distance=3.0,
        declared_bounds=(("max_distance", 3.0),),
        enforced_by="some-backend",
        audit_ref="some-audit",
    )
    assert stringy.is_enforced() is False
    with pytest.raises(UnsupportedTopologyError):
        ForbiddenTrans(id="CX", sites=("N17", "N19"), classification="PROVEN_INFEASIBLE")
    with pytest.raises(UnsupportedTopologyError):
        ForbiddenTrans(
            id="CY",
            sites=("N17", "N19"),
            classification="PROVEN_INFEASIBLE",
            provenance="test",
            proof=stringy,
        )


def test_proven_infeasible_filter_with_sound_evidence() -> None:
    _, spec = _load_spec()
    template = shapes.get_shape("octahedral")
    proof_c01 = _ts1_pair_proof("N17", "N19")
    proof_c04 = _ts1_pair_proof("O74", "O80")
    assert proof_c01 is not None and proof_c04 is not None
    upgraded = CoordinationSpec(
        metal_center=spec.metal_center,
        binding_sites=spec.binding_sites,
        shapes=("octahedral",),
        constraints=(
            ForbiddenTrans(
                id="C01",
                sites=("N17", "N19"),
                classification="PROVEN_INFEASIBLE",
                provenance="fragment-interval proof",
                proof=proof_c01,
            ),
            ForbiddenTrans(
                id="C04",
                sites=("O74", "O80"),
                classification="PROVEN_INFEASIBLE",
                provenance="fragment-interval proof",
                proof=proof_c04,
            ),
        ),
    )
    admissible, excluded = enumeration.apply_policy_filters(
        iter([(0, 1, 2, 3, 4, 5)]), upgraded, template
    )
    assert admissible == []
    assert excluded[0].verdict == "PROVEN_INFEASIBLE"
    # Full upgrade keeps the same count layers: C02/C03 stay policy (intervals overlap).
    full = CoordinationSpec(
        metal_center=spec.metal_center,
        binding_sites=spec.binding_sites,
        shapes=("octahedral",),
        constraints=tuple(
            ForbiddenTrans(
                id=c.id,
                sites=c.sites,
                classification=(
                    "PROVEN_INFEASIBLE" if c.id in ("C01", "C04") else "REJECTED_BY_POLICY"
                ),
                provenance=c.provenance,
                proof=({"C01": proof_c01, "C04": proof_c04}.get(c.id)),
            )
            for c in spec.constraints
        ),
    )
    pipe = enumerate_targets(full, "octahedral", _sigma_site_perm())
    assert pipe["layers"].shape_classes == 12
    assert pipe["layers"].proof_excluded > 0
    verdicts = {item.verdict for item in pipe["excluded"]}
    assert verdicts == {"PROVEN_INFEASIBLE", "REJECTED_BY_POLICY"}


# ---------------------------------------------------------------------------
# Stage adapter integrated against core build_context (single graph authority)
# ---------------------------------------------------------------------------


def spec_site_ids() -> list[str]:
    """Return the TS1 declared site order."""
    return ["N17", "N19", "O45", "O46", "O74", "O80"]


def test_science_has_no_legacy_or_energy_imports() -> None:
    roots = [
        Path("confflow/science/confgen/graph.py"),
        *sorted(Path("confflow/science/confgen/coordination").glob("*.py")),
    ]
    assert sorted(path.name for path in roots) == [
        "__init__.py",
        "enumeration.py",
        "graph.py",
        "perception.py",
        "shapes.py",
        "spec.py",
    ]
    banned = (
        "confflow.blocks",
        "confflow.execution",
        "confflow.workflow",
        "rdkit",
        "MMFF",
        "from science.",
        "import science",
    )
    for path in roots:
        text = path.read_text()
        for marker in banned:
            assert marker not in text, (path, marker)


# ---------------------------------------------------------------------------
# Unified COMMAND/OBSERVED keys, audit_target/state_matches, engine binding
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scoped atom references, E/Z refusal, flexible re-spike, alkene stereo
# ---------------------------------------------------------------------------


def test_scoped_refs_validate_graph_authority() -> None:
    from confflow.science.confgen.graph import build_scoped_refs, check_scoped_refs

    graph, _ = _load_spec()
    refs = build_scoped_refs(graph, "ts1")
    assert len(refs) == 122
    assert [r.atom_index for r in refs] == list(range(122))
    assert all(r.structure_id == "ts1" and r.radius > 0 and r.env_hash for r in refs)
    assert check_scoped_refs(graph, refs, "ts1") == []
    # Environment hash distinguishes donor environments.
    by_index = {r.atom_index: r for r in refs}
    assert by_index[16].env_hash != by_index[44].env_hash
    tampered = tuple(
        (
            r
            if r.atom_index != 16
            else r.__class__(
                atom_index=r.atom_index,
                structure_id=r.structure_id,
                expected_element="O",
                radius=r.radius,
                env_hash=r.env_hash,
            )
        )
        for r in refs
    )
    problems = check_scoped_refs(graph, tampered, "ts1")
    assert any("expected O vs graph N" in problem for problem in problems)
    assert any("scope mismatch" in problem for problem in check_scoped_refs(graph, refs, "other"))
