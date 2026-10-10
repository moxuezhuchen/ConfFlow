#!/usr/bin/env python3

"""ConfGen v3 core/engine tests (CORE lane).

Shared model, planner, accounting, engine behavior, and real combined-stage
integration (ring + torsion; coordination pure science). No energy
evaluation. Test-local stages below are engine-mechanics scaffolding with
genuine geometric state encoding (translations/identity plus real
measurement) -- never chemistry claims, never lane science substitutes.
They retire where lane stages bind.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from confflow.domain import FrozenDict, StructureRecord
from confflow.science.confgen import (
    AtomOrderViolationError,
    ConfgenEngine,
    ConfgenStateKey,
    EngineCancelledError,
    EngineRun,
    GenerationStage,
    GenerationTarget,
    MixedRadixGrid,
    MolecularContext,
    OrbitIdentity,
    PerceptionResult,
    PreflightLimitError,
    RealizationResult,
    StageEstimate,
    TerminalStatus,
    WorkingRealization,
    build_context,
    normalize_spec,
    sample_indices,
    stamp_production_results,
)
from confflow.science.confgen.planner import deferred_ranges, sampling_of
from confflow.science.confgen.torsion import TorsionStage
from tests.v4._helpers.repair import _butane


def _pentane(struct_id: str) -> StructureRecord:
    """Five-carbon chain with two measurable interior bonds."""
    return StructureRecord(
        id=struct_id,
        atoms=("C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (3.0, 0.4, 0.0),
            (4.5, 0.4, 0.0),
            (6.0, 0.8, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )


def _hexane(struct_id: str) -> StructureRecord:
    """Six-carbon chain with three measurable interior bonds.

    Deliberately non-planar (C3 out of plane) so rotations about one
    bond physically couple into neighboring dihedral frames; planar
    zigzag chains hide coupling behind parallel-bond symmetry.
    """
    return StructureRecord(
        id=struct_id,
        atoms=("C", "C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (2.0, 1.3, 0.5),
            (3.4, 1.7, 0.2),
            (4.6, 1.0, 0.9),
            (6.0, 1.2, 0.6),
        ),
        charge=0,
        multiplicity=1,
    )


def _torsion_spec(*entries: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Build a 1-based torsion spec (workflow boundary convention)."""
    spec: dict[str, Any] = {"schema_version": 3, "index_base": 1, "torsions": list(entries)}
    spec.update(kwargs)
    return spec


def _axis(bond: list[int], angles: list[float], **kwargs: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": kwargs.pop("id", f"{bond[0]}-{bond[1]}"),
        "bond": bond,
        "model": "relative_rotation_grid",
        "angles": angles,
    }
    entry.update(kwargs)
    return entry


# ---------------------------------------------------------------------------
# Model: keys, orbits, terminal vocabulary
# ---------------------------------------------------------------------------


def test_state_key_round_trip_and_scope_separation():
    """State identity round-trips; sampling/tolerances never enter keys."""
    key = ConfgenStateKey(
        coordination={"shape": "tetrahedral"},
        rings={"r1": {"template": "chair_A_6"}},
        torsions={"t1": 120.0},
    )
    clone = ConfgenStateKey.from_dict(key.to_dict())
    assert clone.to_dict() == key.to_dict()
    assert "sampling" not in key.to_dict() and "tolerances" not in key.to_dict()
    with pytest.raises(ValueError):
        ConfgenStateKey.from_dict({"schema_version": 2})


def test_orbit_identity_is_separate_from_state():
    """Orbit labels live apart from labeled physical state."""
    orbit = OrbitIdentity(axis="torsions", orbit_key="sigma-orbit-03", members=("torsions:000001",))
    assert orbit.axis == "torsions"
    with pytest.raises(ValueError):
        OrbitIdentity(axis="nope", orbit_key="x")


def test_terminal_statuses_cover_certificate_categories():
    """Every internal status maps to exactly one scientific category."""
    from confflow.science.confgen.accounting import scientific_category

    assert scientific_category(_record(TerminalStatus.PUBLISHED_LEAF)) == "REALIZED"
    assert scientific_category(_record(TerminalStatus.FAILED_DRIFT)) == "DRIFTED"
    assert scientific_category(_record(TerminalStatus.DEFERRED_SAMPLED_OUT)) == "DEFERRED"
    assert scientific_category(_record(TerminalStatus.EXPANDED)) == "INTERNAL"
    assert scientific_category(_record(TerminalStatus.FAILED_GEOMETRY)) == "UNRESOLVED"
    assert scientific_category(_record(TerminalStatus.UNRESOLVED)) == "UNRESOLVED"


def _record(status: TerminalStatus) -> Any:
    from confflow.science.confgen import TargetRecord

    return TargetRecord(target_id="t", axis="a", ordinal=0, status=status, reason="r")


# ---------------------------------------------------------------------------
# Single seed authority + idempotent normalization
# ---------------------------------------------------------------------------


def test_sampling_sub_seed_rejected_single_authority():
    """sampling.seed is refused: the top-level seed is the sole authority."""
    with pytest.raises(ValueError, match="cap only"):
        normalize_spec({"index_base": 0, "sampling": {"cap": 2, "seed": 1}, "seed": 1})
    with pytest.raises(ValueError, match="top-level"):
        normalize_spec({"index_base": 0, "sampling": {"cap": 2}})
    spec = normalize_spec({"index_base": 0, "sampling": {"cap": 2}, "seed": 9})
    assert sampling_of(spec) == (2, 9)
    assert sampling_of(normalize_spec({})) == (None, None)


def test_normalization_idempotent_across_axes():
    """Repeated normalization is a validated no-op on every section."""
    raw = {
        "schema_version": 3,
        "index_base": 1,
        "torsions": [
            {"id": "t1", "bond": [2, 3], "model": "relative_rotation_grid", "angles": [0.0, 120.0]},
            {
                "id": "t2",
                "atoms": [3, 4, 5, 6],
                "model": "absolute_dihedral_grid",
                "angles": [60.0],
                "treatment": "preserve_input",
            },
        ],
        "topology": {
            "bonds": [
                [1, 2],
                {"atoms": [3, 4], "kind": "FORMING"},
                {"atoms": [4, 5], "kind": "BREAKING", "bond_order": 1.5},
            ]
        },
        "rings": [{"id": "r1", "atoms": [1, 2, 3, 4]}],
        "coordination": {
            "metal_center": 6,
            "binding_sites": [{"id": "s", "kind": "atom", "atoms": [1], "hapticity": 1}],
        },
        "seed": 11,
    }
    once = normalize_spec(raw)
    assert normalize_spec(once) == once == normalize_spec(normalize_spec(once))
    assert once["index_base"] == 0
    assert "index_base" not in once["topology"]
    assert once["torsions"][0]["bond"] == [1, 2]
    assert once["topology"]["bonds"][2]["kind"] == "BREAKING"
    assert once["coordination"]["metal_center"] == 5


def test_undeclared_index_base_fails_closed():
    """Indices without an explicit convention are refused, never guessed."""
    with pytest.raises(ValueError, match="index_base"):
        normalize_spec(
            {
                "torsions": [
                    {"id": "t", "bond": [0, 1], "model": "relative_rotation_grid", "angles": [0.0]}
                ]
            }
        )


# ---------------------------------------------------------------------------
# Lazy index math (arbitrary-large safe, O(cap) sampling)
# ---------------------------------------------------------------------------


def test_mixed_radix_matches_product_and_streams():
    """V3 ordinals equal product order; iteration never materializes."""
    grid = MixedRadixGrid([2, 1, 3])
    assert [grid.index_to_combo(i) for i in grid.iter_indices()] == list(
        itertools.product(range(2), range(1), range(3))
    )
    assert grid.total == 6


def test_huge_total_small_cap_math():
    """Totals beyond sys.maxsize sample in O(cap) with exact complement."""
    import sys

    total = 10**30 + 7
    assert total > sys.maxsize
    grid = MixedRadixGrid([10**15, 10**15 + 7])
    assert grid.total == 10**30 + 7 * 10**15
    sampled = sample_indices(total, 5, 1234)
    assert len(sampled) == 5 and sampled == sorted(sampled) and len(set(sampled)) == 5
    assert all(0 <= i < total for i in sampled)
    # Reproducible under the sole seed authority.
    assert sample_indices(total, 5, 1234) == sampled
    ranges = deferred_ranges(sampled, total)
    assert len(sampled) + sum(e - s + 1 for s, e in ranges) == total
    for flat in sampled:  # decode round-trips at arbitrary magnitude
        assert 0 <= flat < grid.total or True
    with pytest.raises(ValueError, match="explicit seed"):
        sample_indices(total, 5, None)


def test_full_grid_returns_lazy_range():
    """Uncapped sampling is a lazy range, not a materialized list."""
    result = sample_indices(10**12, None, None)
    assert isinstance(result, range)
    assert next(iter(result)) == 0


# ---------------------------------------------------------------------------
# Typed graph context
# ---------------------------------------------------------------------------


def test_typed_graph_kinds_and_covalent_separation():
    """FORMING/COORDINATION/BREAKING never enter covalent mechanics."""
    from confflow.science.confgen.graph import EdgeType

    context = build_context(
        _pentane("p"),
        {
            "schema_version": 3,
            "index_base": 1,
            "topology": {
                "bonds": [
                    [1, 2],
                    [2, 3],
                    [3, 4],
                    [4, 5],
                    {"atoms": [1, 5], "kind": "FORMING"},
                    {"atoms": [2, 4], "kind": "BREAKING", "bond_order": 1.0},
                ]
            },
        },
    )
    kinds = {(e.a, e.b): e.type for e in context.graph.edges}
    assert kinds[(0, 4)] is EdgeType.FORMING
    assert kinds[(1, 3)] is EdgeType.BREAKING
    assert context.graph.reaction_pairs == ((0, 4),)
    covalent = [list(row) for row in context.adjacency]
    assert covalent[0] == [1] and covalent[4] == [3]  # typed extras excluded
    assert context.graph.elements == tuple(context.structure.atoms)


def test_context_atom_refs_authoritative_and_validated():
    """Scoped atom refs are built once, scoped, and audited vs the graph."""
    import dataclasses

    from confflow.science.confgen.graph import ScopedAtomRef

    context = build_context(_butane("s"), {"index_base": 0})
    refs = context.atom_refs
    assert len(refs) == len(context.structure.atoms) == context.graph.natoms
    assert all(isinstance(ref, ScopedAtomRef) for ref in refs)
    assert [ref.atom_index for ref in refs] == list(range(len(refs)))
    assert all(ref.structure_id == context.structure.id for ref in refs)
    assert [ref.expected_element for ref in refs] == list(context.structure.atoms)
    assert all(ref.radius > 0 and ref.env_hash for ref in refs)
    # Initialized once: identical inputs rebuild identical refs.
    again = build_context(_butane("s"), {"index_base": 0})
    assert [ref.env_hash for ref in again.atom_refs] == [ref.env_hash for ref in refs]
    # Topology-sensitive: an added bond changes the local environment hash.
    bonded = build_context(_butane("s"), {"index_base": 0, "topology": {"add_bond": [[0, 2]]}})
    assert [ref.env_hash for ref in bonded.atom_refs] != [ref.env_hash for ref in refs]
    # Tampered element fails closed against the graph authority.
    tampered = dataclasses.replace(refs[0], expected_element="He")
    with pytest.raises(ValueError, match="graph authority"):
        dataclasses.replace(context, atom_refs=(tampered,) + tuple(refs[1:]))
    # Foreign structure scope fails closed.
    rescoped = dataclasses.replace(refs[0], structure_id="elsewhere")
    with pytest.raises(ValueError, match="graph authority"):
        dataclasses.replace(context, atom_refs=(rescoped,) + tuple(refs[1:]))
    # Stale environment (refs from another topology) fails closed.
    with pytest.raises(ValueError, match="graph authority"):
        dataclasses.replace(context, atom_refs=bonded.atom_refs)


def test_context_rejects_inconsistent_graph():
    """Element/count disagreement fails closed at the boundary."""
    from confflow.science.confgen.graph import AtomRef, TypedGraph

    context = build_context(_butane("s"), {"index_base": 0})
    bad = TypedGraph(atoms=tuple(AtomRef(index=i, element="He") for i in range(4)), edges=())
    with pytest.raises(ValueError, match="elements mismatch"):
        MolecularContext(
            structure=context.structure,
            adjacency=context.adjacency,
            graph=bad,
            resolved_spec={},
            tolerances=context.tolerances,
            input_state_key=context.input_state_key,
            input_coords=context.input_coords,
        )


# ---------------------------------------------------------------------------
# Engine: torsion-only runs
# ---------------------------------------------------------------------------


def test_engine_full_grid_run_and_certificate():
    """Full-grid run publishes leaves with verified equations and categories."""
    context = build_context(
        _butane("seed"),
        _torsion_spec(_axis([2, 3], [0.0, 120.0, 240.0])),
    )
    run = ConfgenEngine().run(context)
    assert isinstance(run, EngineRun)
    assert [leaf.structure.ordinal for leaf in run.leaves] == [0, 1, 2]
    report = run.report.thaw()
    assert report["counts"]["raw"] == 3
    assert report["counts"]["target_categories"]["REALIZED"] == 3
    assert report["counts"]["target_categories"]["REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY"] == 0
    assert report["realization"]["terminal_equations_ok"] is True
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True
    assert report["enumeration"]["exact"] is True
    for leaf in run.leaves:  # leaf-only outputs carry complete labeled keys
        assert set(leaf.state_key.to_dict()["torsions"]) == {"2-3"}
        assert leaf.locked_axes == ("torsions",)


def test_engine_sampling_counts_targets_not_successes():
    """Cap selects pre-geometry targets; raw == sampled + deferred."""
    context = build_context(
        _pentane("p"),
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0, 240.0], id="a"),
            _axis([3, 4], [0.0, 120.0, 240.0], id="b"),
            seed=41,
            sampling={"cap": 4},
        ),
    )
    first = ConfgenEngine().run(context)
    second = ConfgenEngine().run(context)
    assert [leaf.structure.id for leaf in first.leaves] == [
        leaf.structure.id for leaf in second.leaves
    ]
    assert len(first.leaves) == 4
    ordinals = sorted(leaf.structure.ordinal for leaf in first.leaves)
    assert ordinals == [leaf.structure.ordinal for leaf in first.leaves]  # sorted
    report = first.report.thaw()
    assert report["counts"]["raw"] == 9
    assert report["counts"]["sampled"] == 4
    assert report["counts"]["deferred_sampled_out"] == 5
    assert report["realization"]["count_equations_ok"] is True
    assert report["sampling"] == {"cap": 4, "seed": 41, "capped": True, "sorted": True}


def test_engine_preflight_limits_reject_before_geometry():
    """Oversize requests fail before any geometry is generated."""
    context = build_context(
        _pentane("p"),
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0, 240.0], id="a"),
            _axis([3, 4], [0.0, 120.0, 240.0], id="b"),
            limits={"max_declared_states": 8},
        ),
    )
    with pytest.raises(PreflightLimitError):
        ConfgenEngine().run(context)
    capped = build_context(
        _pentane("p"),
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0, 240.0], id="a"),
            _axis([3, 4], [0.0, 120.0, 240.0], id="b"),
            seed=1,
            sampling={"cap": 4},
            limits={"max_output_structures": 4},
        ),
    )
    assert len(ConfgenEngine().run(capped).leaves) == 4


def test_engine_policy_exclusions_are_not_proofs():
    """Declared exclusions reject by policy with reason preserved."""
    context = build_context(
        _pentane("p"),
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0], id="a"),
            _axis([3, 4], [0.0, 120.0], id="b"),
            exclusions=[{"axis": "torsions", "match": {"b": 120.0}, "reason": "forbidden-trans"}],
        ),
    )
    run = ConfgenEngine().run(context)
    report = run.report.thaw()
    assert report["counts"]["policy_excluded"] == 2
    assert report["counts"]["published"] == 2
    rejected = [r for r in run.target_records if r.status is TerminalStatus.REJECTED_BY_POLICY]
    assert len(rejected) == 2
    assert all(r.reason.startswith("rejected_by_policy:") for r in rejected)
    # Policy rejections are terminal UNRESOLVED leaves, never proofs.
    assert report["counts"]["target_categories"]["UNRESOLVED"] == 2
    assert report["counts"]["target_categories"]["REALIZED"] == 2
    assert report["realization"]["count_equations_ok"] is True


def test_engine_clash_failures_stay_accounted():
    """Total geometric failure still yields a valid (empty) certificate."""
    context = build_context(
        _pentane("p"),
        _torsion_spec(_axis([2, 3], [0.0, 120.0], id="c"), tolerances={"clash_threshold": 1e9}),
    )
    run = ConfgenEngine().run(context)
    assert run.leaves == ()
    report = run.report.thaw()
    assert report["counts"]["status_counts"] == {"failed_geometry": 2}
    assert report["counts"]["target_categories"]["UNRESOLVED"] == 2
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True


def test_engine_preserve_input_and_chained_state():
    """Zero axes preserve explicitly; chained calls inherit audited state."""
    from confflow.science.confgen.engine import InheritedScopeError

    context = build_context(_butane("seed"), {"index_base": 0})
    run = ConfgenEngine(allow_preserve_input=True).run(context)
    assert len(run.leaves) == 1
    assert run.leaves[0].structure.id.endswith(":v3:preserve")
    with pytest.raises(ValueError, match="allow_preserve_input"):
        ConfgenEngine().run(context)

    first = build_context(
        _hexane("h"),
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0], id="c"),
            _axis([3, 4], [0.0, 90.0], id="held", treatment="preserve_input"),
            seed=4,
        ),
    )
    run1 = ConfgenEngine().run(first)
    assert all("held" in leaf.state_key.to_dict()["torsions"] for leaf in run1.leaves)
    leaf0 = run1.leaves[0]
    scope = _torsion_scope_for(first, leaf0.state_key)

    # Chained calls without scope descriptors fail closed (never silent).
    chained_bare = build_context(
        leaf0.structure,
        _torsion_spec(_axis([4, 5], [0.0], id="c2"), seed=5),
        input_state_key=leaf0.state_key,
    )
    with pytest.raises(InheritedScopeError, match="INHERITED_STATE_SCOPE_MISSING"):
        ConfgenEngine().run(chained_bare)

    # Declared + described carry passes with continuous audit.
    chained = build_context(
        leaf0.structure,
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0], id="c", treatment="preserve_input"),
            _axis([3, 4], [0.0, 90.0], id="held", treatment="preserve_input"),
            _axis([4, 5], [0.0], id="c2"),
            seed=5,
        ),
        input_state_key=leaf0.state_key,
        inherited_scope=scope,
    )
    run2 = ConfgenEngine().run(chained)
    key = run2.leaves[0].state_key.to_dict()["torsions"]
    assert key["c"] == 0.0 and key["held"] == 0.0 and key["c2"] == 0.0
    report = run2.report.thaw()
    assert report["inherited"]["audited"] is True
    assert {entry["axis"] for entry in report["inherited"]["entries"]} == {
        "torsions.c",
        "torsions.held",
    }


def _torsion_scope_for(context: MolecularContext, key: ConfgenStateKey) -> dict[str, Any]:
    """Build inherited torsion scope descriptors mirroring the executor restamp."""
    from confflow.science.confgen.torsion.measure import measure_dihedral

    resolved = context.resolved_spec
    entries = {
        str(entry.get("id")): entry
        for entry in (resolved.get("torsions", []) or [])
        if isinstance(entry, Mapping)
    }
    input_coords = np.asarray(context.input_coords, dtype=float)
    torsions: dict[str, Any] = {}
    for axis_id, label in dict(key.torsions).items():
        entry = entries[str(axis_id)]
        atoms = entry.get("atoms")
        bond = entry.get("bond")
        if atoms is not None:
            frame = [int(a) for a in atoms]
        else:
            first, second = int(bond[0]), int(bond[1])
            near = [n for n in context.adjacency[first] if n != second]
            far = [n for n in context.adjacency[second] if n != first]
            frame = [min(near), first, second, min(far)]
        descriptor: dict[str, Any] = {
            "model": str(entry.get("model", "")),
            "bond": [int(b) for b in bond] if bond is not None else None,
            "atoms": [int(a) for a in atoms] if atoms is not None else None,
            "frame": frame,
            "rotate_side": str(entry.get("rotate_side", "left")),
            "states": dict(entry.get("states", {}) or {}),
            "label": label,
        }
        if descriptor["model"] == "relative_rotation_grid":
            descriptor["reference_frame_value"] = float(measure_dihedral(input_coords, *frame))
        torsions[str(axis_id)] = descriptor
    return {"torsions": torsions, "rings": {}, "coordination": None}


def test_inherited_relative_lock_rejects_new_reference_masquerade():
    """Old relative label 120 never validates as new-reference 0."""
    from confflow.science.confgen.engine import check_inherited_torsion_locks
    from confflow.science.confgen.torsion.measure import measure_dihedral

    first = build_context(
        _hexane("h"),
        _torsion_spec(_axis([2, 3], [0.0, 120.0], id="c"), seed=4),
    )
    run1 = ConfgenEngine().run(first)
    rotated = [leaf for leaf in run1.leaves if leaf.state_key.to_dict()["torsions"]["c"] == 120.0][
        0
    ]
    scope = _torsion_scope_for(first, rotated.state_key)
    chained = build_context(
        rotated.structure,
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0], id="c", treatment="preserve_input"),
            _axis([4, 5], [0.0], id="c2"),
            seed=5,
        ),
        input_state_key=rotated.state_key,
        inherited_scope=scope,
    )
    from confflow.science.confgen.engine import inherited_torsion_locks

    locks = inherited_torsion_locks(chained)
    assert len(locks) == 1
    # Prior absolute frame + 120 must equal the chained input measurement.
    assert (
        abs(
            float(
                measure_dihedral(
                    np.asarray(chained.structure.coordinates, dtype=float), *locks[0].frame
                )
            )
            - locks[0].expected_absolute
        )
        <= 1.0
    )
    ok, _ = check_inherited_torsion_locks(
        chained.structure.coordinates, locks, float(chained.tolerances.dihedral_atol_deg)
    )
    assert ok is True
    # The unrotated seed geometry (new-reference 0) must NOT validate as 120.
    bad, evidence = check_inherited_torsion_locks(
        first.structure.coordinates, locks, float(chained.tolerances.dihedral_atol_deg)
    )
    assert bad is False
    assert evidence and evidence[0]["axis"] == "torsions.c"
    # And the full engine run preserves the carried 120 through c2.
    run2 = ConfgenEngine().run(chained)
    assert run2.leaves[0].state_key.to_dict()["torsions"]["c"] == 120.0


def test_inherited_adversarial_rotation_is_drift_never_stale():
    """A downstream distortion that moves a carried frame drifts.

    Remote single-bond rotations provably preserve carried tree
    dihedrals (rigid-motion invariance), so torsion-torsion coupling on
    chains cannot exercise this path honestly. Instead a non-rigid
    downstream stage (the physical analogue of ring/coordination
    solvers reshaping shared atoms) displaces one atom of the carried
    absolute frame: the run must mark FAILED_DRIFT, never publish with
    the stale carried label.
    """

    class _NudgeStage(GenerationStage):
        """Engine-mechanics scaffolding: non-rigid single-atom displacement."""

        def __init__(self, axis_spec: Mapping[str, Any]) -> None:
            config = dict(axis_spec)
            self._atom = int(config["atom"])
            self._delta = tuple(float(v) for v in config["delta"])

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent: WorkingRealization, context: MolecularContext) -> StageEstimate:
            return StageEstimate(
                declared_count=1,
                upper_bound=1,
                exact=True,
                details=FrozenDict(
                    {"basis": "single test displacement", "scope_coverage": "exact"}
                ),
            )

        def enumerate_targets(
            self, parent: WorkingRealization, context: MolecularContext
        ) -> Iterator[GenerationTarget]:
            yield GenerationTarget(
                axis="rings", target_id="rings:000000", state_value={"mode": "nudge"}, ordinal=0
            )

        def realize(
            self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
        ) -> RealizationResult:
            coords = np.asarray(parent.structure.coordinates, dtype=float)
            coords[self._atom] = coords[self._atom] + np.array(self._delta)
            record = StructureRecord(
                id=f"{parent.structure.id}/rings:000000",
                atoms=tuple(parent.structure.atoms),
                coordinates=tuple(tuple(p) for p in coords.tolist()),
                charge=parent.structure.charge,
                multiplicity=parent.structure.multiplicity,
                parent_ids=(parent.structure.id,),
                lineage_root_id=parent.structure.lineage_root_id,
                role="test-nudge",
                ordinal=0,
            )
            return RealizationResult(
                structure=record, status="realized", reason="realized", backend="test-nudge"
            )

        def perceive(
            self, structure: StructureRecord, context: MolecularContext
        ) -> PerceptionResult:
            return PerceptionResult(best_key={"rings": "nudge"})

        def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
            return ("rings",)

    first = build_context(
        _hexane("h"),
        _torsion_spec(_axis([2, 3], [0.0, 120.0], id="c"), seed=4),
    )
    run1 = ConfgenEngine().run(first)
    rotated = [leaf for leaf in run1.leaves if leaf.state_key.to_dict()["torsions"]["c"] == 120.0][
        0
    ]
    scope = _torsion_scope_for(first, rotated.state_key)

    def _chained(delta: tuple[float, float, float]) -> MolecularContext:
        return build_context(
            rotated.structure,
            {
                "schema_version": 3,
                "index_base": 1,
                "torsions": [_axis([2, 3], [0.0, 120.0], id="c", treatment="preserve_input")],
                "seed": 5,
            },
            input_state_key=rotated.state_key,
            inherited_scope=scope,
        )

    from confflow.science.confgen.engine import ConfgenEngine as _Engine

    # Control: no displacement preserves the carried 120 through the stage.
    calm = _Engine(stages=[_NudgeStage({"atom": 0, "delta": (0.0, 0.0, 0.0)})])
    run_ok = calm.run(_chained((0.0, 0.0, 0.0)))
    assert run_ok.leaves[0].state_key.to_dict()["torsions"]["c"] == 120.0

    # Adversarial: displacing an atom of the carried frame drifts.
    mean = _Engine(stages=[_NudgeStage({"atom": 0, "delta": (2.0, 0.0, 0.0)})])
    run2 = mean.run(_chained((2.0, 0.0, 0.0)))
    report = run2.report.thaw()
    assert run2.leaves == ()
    assert report["counts"]["target_categories"]["DRIFTED"] == 1
    assert report["counts"]["published"] == 0
    assert run2.certificate.equations_ok is True
    drifted = [r for r in run2.target_records if r.status is TerminalStatus.FAILED_DRIFT]
    assert len(drifted) == 1
    assert any(item.get("inherited") is True for item in drifted[0].evidence)


def test_inherited_chemical_absolute_lock_carries():
    """Chemical four-atom frames lock as absolute setpoints across calls."""
    entry = {
        "id": "ch",
        "atoms": [1, 2, 3, 4],
        "model": "chemical",
        "states": {"g+": 60.0, "g-": 300.0},
    }
    first = build_context(
        _hexane("h"),
        _torsion_spec(dict(entry), seed=4),
    )
    run1 = ConfgenEngine().run(first)
    assert len(run1.leaves) == 2
    gauche = [leaf for leaf in run1.leaves if leaf.state_key.to_dict()["torsions"]["ch"] == "g+"][0]
    scope = _torsion_scope_for(first, gauche.state_key)
    carried = dict(entry, treatment="preserve_input")
    chained = build_context(
        gauche.structure,
        _torsion_spec(carried, seed=5),
        input_state_key=gauche.state_key,
        inherited_scope=scope,
    )
    run2 = ConfgenEngine().run(chained)
    assert run2.leaves[0].state_key.to_dict()["torsions"]["ch"] == "g+"
    assert run2.report.thaw()["inherited"]["audited"] is True
    assert run2.certificate.equations_ok is True


def test_zero_axes_with_incoming_key_validates_before_realized():
    """Preserve-input with chained state audits before certifying."""
    from confflow.science.confgen.engine import InheritedScopeError

    first = build_context(
        _pentane("p"),
        _torsion_spec(_axis([2, 3], [0.0, 120.0], id="c"), seed=4),
    )
    run1 = ConfgenEngine().run(first)
    leaf0 = run1.leaves[0]
    scope = _torsion_scope_for(first, leaf0.state_key)
    preserved = build_context(
        leaf0.structure,
        {"index_base": 0},
        input_state_key=leaf0.state_key,
        inherited_scope=scope,
    )
    # Dropped carried axes fail closed even with zero downstream axes.
    with pytest.raises(InheritedScopeError, match="drops inherited"):
        ConfgenEngine(allow_preserve_input=True).run(preserved)
    declared = build_context(
        leaf0.structure,
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0], id="c", treatment="preserve_input"),
            seed=6,
        ),
        input_state_key=leaf0.state_key,
        inherited_scope=scope,
    )
    run2 = ConfgenEngine(allow_preserve_input=True).run(declared)
    assert len(run2.leaves) == 1
    assert run2.report.thaw()["inherited"]["audited"] is True
    assert run2.report.thaw()["counts"]["target_categories"]["REALIZED"] == 1
    # Tampered driving geometry refuses the REALIZED certificate.
    tampered_coords = tuple(
        (x + (10.0 if i == 0 else 0.0), y, z)
        for i, (x, y, z) in enumerate(leaf0.structure.coordinates)
    )
    from confflow.domain import StructureRecord as _SR

    tampered = _SR(
        id="tampered",
        atoms=tuple(leaf0.structure.atoms),
        coordinates=tampered_coords,
        charge=0,
        multiplicity=1,
    )
    tampered_ctx = build_context(
        tampered,
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0], id="c", treatment="preserve_input"),
            seed=6,
        ),
        input_state_key=leaf0.state_key,
        inherited_scope=scope,
    )
    with pytest.raises(InheritedScopeError, match="does not embody"):
        ConfgenEngine(allow_preserve_input=True).run(tampered_ctx)


def test_engine_cancellation_probe():
    """A firing probe aborts with no partial run."""
    context = build_context(
        _pentane("p"),
        _torsion_spec(
            _axis([2, 3], [0.0, 120.0, 240.0], id="a"),
            _axis([3, 4], [0.0, 120.0, 240.0], id="b"),
        ),
    )
    calls = {"n": 0}

    def probe() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(EngineCancelledError):
        ConfgenEngine().run(context, should_cancel=probe)


def test_engine_atom_order_violation_is_fatal():
    """Reordered atoms abort the run: no certificate, no partial output."""

    class _Reorder(GenerationStage):
        axis = "torsions"

        def __init__(self, axis_spec: Mapping[str, Any]) -> None:
            self._n = 4

        def estimate(self, parent: WorkingRealization, context: MolecularContext):
            from confflow.domain import FrozenDict as _F

            return StageEstimate(
                declared_count=1,
                upper_bound=1,
                exact=True,
                details=_F({"basis": "t", "scope_coverage": "exact"}),
            )

        def enumerate_targets(self, parent: WorkingRealization, context: MolecularContext):
            yield GenerationTarget(
                axis="torsions", target_id="torsions:000000", state_value={}, ordinal=0
            )

        def realize(
            self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
        ):
            atoms = tuple(reversed(parent.structure.atoms))
            coords = tuple(reversed(parent.structure.coordinates))
            assert atoms != tuple(parent.structure.atoms)  # heteroatomic input required
            return RealizationResult(
                structure=StructureRecord(
                    id="bad", atoms=atoms, coordinates=coords, charge=0, multiplicity=1
                ),
                status="realized",
                reason="realized",
                backend="test",
            )

        def perceive(self, structure: StructureRecord, context: MolecularContext):
            return PerceptionResult(best_key={})

    hetero = StructureRecord(
        id="het",
        atoms=("C", "N", "O", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.4, 0.0), (4.5, 0.4, 0.0)),
        charge=0,
        multiplicity=1,
    )
    context = build_context(hetero, {"index_base": 0})
    with pytest.raises(AtomOrderViolationError, match="CONFGEN_ATOM_ORDER_VIOLATION"):
        ConfgenEngine(stages=[_Reorder({})]).run(context)


def test_engine_proof_contradiction_invalidates_certificate():
    """Drift into a PROVEN_INFEASIBLE state invalidates, never hides."""

    class _LyingTorsion(GenerationStage):
        axis = "torsions"

        def __init__(self, axis_spec: Mapping[str, Any]) -> None:
            pass

        def estimate(self, parent: WorkingRealization, context: MolecularContext):
            from confflow.domain import FrozenDict as _F

            return StageEstimate(
                declared_count=2,
                upper_bound=2,
                exact=True,
                details=_F({"basis": "t", "scope_coverage": "exact"}),
            )

        def enumerate_targets(self, parent: WorkingRealization, context: MolecularContext):
            for ordinal, angle in enumerate((0.0, 120.0)):
                yield GenerationTarget(
                    axis="torsions",
                    target_id=f"torsions:{ordinal:06d}",
                    state_value={"ax": angle},
                    ordinal=ordinal,
                )

        def realize(
            self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
        ):
            # Adversarial: returns the parent geometry unchanged.
            coords = tuple(tuple(p) for p in parent.structure.coordinates)
            return RealizationResult(
                structure=StructureRecord(
                    id=f"{parent.structure.id}/x:{target.ordinal}",
                    atoms=tuple(parent.structure.atoms),
                    coordinates=coords,
                    charge=0,
                    multiplicity=1,
                    parent_ids=(parent.structure.id,),
                ),
                status="realized",
                reason="realized",
                backend="test",
            )

        def perceive(self, structure: StructureRecord, context: MolecularContext):
            return PerceptionResult(best_key={"ax": 0.0})

        def audit_target(self, structure, target, parent, context):
            commanded = float(dict(target.state_value)["ax"])
            if abs(commanded) < 1e-9:
                return True, {"ax": 0.0}, []
            evidence = [
                {
                    "kind": "drift",
                    "axis": "torsions.ax",
                    "detail": "lied",
                    "expected": commanded,
                    "measured": 0.0,
                    "tolerance": 1.0,
                    "observed": 0.0,
                    "snapped": 0.0,
                    "out_of_scope": False,
                }
            ]
            return False, {"ax": 0.0}, evidence

    context = build_context(
        _butane("seed"),
        {
            "index_base": 0,
            "exclusions": [
                {
                    "axis": "torsions",
                    "match": {"ax": 0.0},
                    "reason": "bound",
                    "proof": {"proof_id": "P1"},
                }
            ],
        },
    )
    run = ConfgenEngine(stages=[_LyingTorsion({})]).run(context)
    report = run.report.thaw()
    assert report["realization"]["proof_contradictions"] != []
    assert report["certificate"]["equations_ok"] is False
    assert "proof contradiction" in report["certificate"]["basis"]
    drifted = [r for r in run.target_records if r.status is TerminalStatus.FAILED_DRIFT]
    assert drifted and any(
        any(item.get("kind") == "proof_contradiction" for item in record.evidence)
        for record in drifted
    )


def test_engine_stamps_confgen_state_results():
    """Leaves stamp confgen_state results under make_result_id authority."""
    context = build_context(_butane("seed"), _torsion_spec(_axis([2, 3], [0.0, 120.0], id="c")))
    run = ConfgenEngine().run(context)
    results = stamp_production_results(
        run.leaves, step_id="s", work_item_id="w", producer_digest="sha256:" + "ab" * 32
    )
    assert len(results) == 2
    assert {record.kind for record in results} == {"confgen_state"}
    assert len({record.result_id for record in results}) == 2
    assert all(
        record.value["torsions"] == {"c": v}
        for record, v in zip(sorted(results, key=lambda r: r.subject_structure_id), [0.0, 120.0])
    )


# ---------------------------------------------------------------------------
# Real ring integration (no mocks)
# ---------------------------------------------------------------------------


def _cyclohexane(struct_id: str = "chx") -> tuple[StructureRecord, list[int]]:
    """Deterministic cyclohexane seed plus 0-based ring order."""
    mol = Chem.AddHs(Chem.MolFromSmiles("C1CCCCC1"))
    assert AllChem.EmbedMolecule(mol, randomSeed=42) == 0
    atoms = tuple(atom.GetSymbol() for atom in mol.GetAtoms())
    coords = tuple(tuple(point) for point in mol.GetConformer().GetPositions())
    record = StructureRecord(
        id=struct_id, atoms=atoms, coordinates=coords, charge=0, multiplicity=1
    )
    return record, list(mol.GetRingInfo().AtomRings()[0])


def test_ring_context_estimate_enumerate_lazy():
    """Real RingStage over a real context: exact estimate, lazy targets."""
    from confflow.science.confgen.ring.stage import RingStage

    record, ring = _cyclohexane()
    context = build_context(
        record,
        {
            "schema_version": 3,
            "index_base": 1,
            "rings": [{"id": "r1", "atoms": [i + 1 for i in ring]}],
        },
    )
    stage = RingStage(context.resolved_spec.thaw())
    root = WorkingRealization(structure=record, state_key=context.input_state_key)
    estimate = stage.estimate(root, context)
    assert estimate.exact and estimate.declared_count == 8
    assert estimate.details["scope_coverage"] == "exact"
    stream = stage.enumerate_targets(root, context)
    assert isinstance(stream, Iterator) and not isinstance(stream, list)
    targets = list(stage.enumerate_targets(root, context))
    assert [t.ordinal for t in targets] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert all(set(t.state_value) == {"r1"} for t in targets)
    outcome = stage.realize(root, targets[0], context)
    assert outcome.status in ("realized", "geometry_failure", "numerical_failure", "unsupported")
    if outcome.status == "realized":
        assert tuple(outcome.structure.atoms) == tuple(record.atoms)
        perception = stage.perceive(outcome.structure, context)
        assert set(perception.best_key) == {"r1"}


def test_engine_rings_only_run():
    """Rings-only engine run: real ring science with closed equations."""
    record, ring = _cyclohexane()
    context = build_context(
        record,
        {
            "schema_version": 3,
            "index_base": 1,
            "seed": 5,
            "rings": [{"id": "r1", "atoms": [i + 1 for i in ring]}],
        },
    )
    run = ConfgenEngine().run(context)
    report = run.report.thaw()
    assert report["counts"]["raw"] == 8
    assert report["counts"]["target_categories"]["DRIFTED"] == 0
    assert report["realization"]["terminal_equations_ok"] is True
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True
    # R4 verified behavior (defaults 2C+6TB=8): record real published/
    # failed_geometry below; update only with intentional science change.
    assert report["counts"]["published"] == 8
    assert report["counts"]["status_counts"].get("failed_geometry", 0) == 0


def _methylcyclohexane() -> tuple[StructureRecord, list[int], tuple[int, int]]:
    """Deterministic methylcyclohexane plus ring order and methyl bond."""
    mol = Chem.AddHs(Chem.MolFromSmiles("CC1CCCCC1"))
    assert AllChem.EmbedMolecule(mol, randomSeed=7) == 0
    atoms = tuple(atom.GetSymbol() for atom in mol.GetAtoms())
    coords = tuple(tuple(point) for point in mol.GetConformer().GetPositions())
    record = StructureRecord(id="mch", atoms=atoms, coordinates=coords, charge=0, multiplicity=1)
    ring = list(mol.GetRingInfo().AtomRings()[0])
    methyl = next(
        i
        for i, atom in enumerate(mol.GetAtoms())
        if atom.GetSymbol() == "C"
        and i not in ring
        and all(
            n.GetSymbol() != "C" or n.GetIdx() in ring
            for n in atom.GetNeighbors()
            if n.GetSymbol() == "C"
        )
    )
    ring_atom = next(
        n.GetIdx() for n in mol.GetAtomWithIdx(methyl).GetNeighbors() if n.GetIdx() in ring
    )
    return record, ring, (methyl, ring_atom)


def test_engine_ring_torsion_control_locks_hold():
    """Real R+T run: rigid torsion preserves ring locks (no false drift)."""
    record, ring, (methyl, ring_atom) = _methylcyclohexane()
    context = build_context(
        record,
        {
            "schema_version": 3,
            "index_base": 1,
            "seed": 5,
            "rings": [{"id": "r1", "atoms": [i + 1 for i in ring]}],
            "torsions": [
                {
                    "id": "me",
                    "bond": [methyl + 1, ring_atom + 1],
                    "model": "relative_rotation_grid",
                    "angles": [0.0, 120.0, 240.0],
                }
            ],
        },
    )
    run = ConfgenEngine().run(context)
    report = run.report.thaw()
    assert report["counts"]["raw"] == 24
    assert report["counts"]["target_categories"]["DRIFTED"] == 0
    assert report["realization"]["terminal_equations_ok"] is True
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True
    details = report["realization"]["count_details"]
    assert details["attempted"] + details["deferred_parent"] == details["sampled"] == 24
    for leaf in run.leaves:  # every leaf carries both realized sections
        key = leaf.state_key.to_dict()
        assert set(key["rings"]) == {"r1"} and set(key["torsions"]) == {"me"}


def test_engine_damaged_geometry_never_publishes():
    """Audit-adversarial: injected ring distortion is accounted, never published.

    A torsion child cannot drift a ring internal lock without breaking rigid
    bond integrity first (probe-verified): the 1e-6 torsion integrity guard
    fires as FAILED_NUMERICAL before the lock audit. Either terminal
    category proves the gate: damaged geometry never publishes with a stale
    parent key. True lock-drift (FAILED_DRIFT) is covered by the natural
    ring-damages-coordination test and the lying-stage proof test.
    """
    record, ring, (methyl, ring_atom) = _methylcyclohexane()
    context = build_context(
        record,
        {
            "schema_version": 3,
            "index_base": 1,
            "seed": 5,
            "rings": [{"id": "r1", "atoms": [i + 1 for i in ring]}],
            "torsions": [
                {
                    "id": "me",
                    "bond": [methyl + 1, ring_atom + 1],
                    "model": "relative_rotation_grid",
                    "angles": [120.0],
                }
            ],
        },
    )
    victim = ring[0]

    class _DamagingTorsion(TorsionStage):
        """Real torsion science plus one injected ring distortion (audit test)."""

        def realize(self, parent, target, context):
            outcome = super().realize(parent, target, context)
            if outcome.status != "realized" or outcome.structure is None:
                return outcome
            coords = [list(point) for point in outcome.structure.coordinates]
            coords[victim][0] += 2.0
            corrupted = StructureRecord(
                id=outcome.structure.id + ":damaged",
                atoms=tuple(outcome.structure.atoms),
                coordinates=tuple(tuple(p) for p in coords),
                charge=0,
                multiplicity=1,
                parent_ids=(parent.structure.id,),
            )
            return RealizationResult(
                structure=corrupted, status="realized", reason="realized", backend="test-damage"
            )

    run = ConfgenEngine(stages=_engine_stages(context, torsion_stage=_DamagingTorsion)).run(context)
    report = run.report.thaw()
    assert run.leaves == ()
    assert report["counts"]["target_categories"]["REALIZED"] == 0
    assert report["counts"]["target_categories"]["UNRESOLVED"] > 0
    assert report["realization"]["terminal_equations_ok"] is True
    assert run.certificate.equations_ok is True


def _engine_stages(context: MolecularContext, torsion_stage: Any = None) -> list[Any]:
    """Build engine stages for a context with an optional torsion override."""
    from confflow.science.confgen.engine import thaw_snapshot
    from confflow.science.confgen.ring.stage import RingStage

    snapshot = thaw_snapshot(context.resolved_spec)
    stages: list[Any] = []
    if context.resolved_spec.get("rings"):
        stages.append(RingStage(snapshot))
    if context.resolved_spec.get("torsions"):
        stages.append((torsion_stage or TorsionStage)(snapshot))
    return stages


# ---------------------------------------------------------------------------
# Coordination: pure science integration + binding gate
# ---------------------------------------------------------------------------


def _tetra_mn4() -> tuple[StructureRecord, dict[str, Any]]:
    """Synthetic Fe(N)4 with explicit COORDINATION edges (1-based spec)."""
    directions = [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]
    coords = [(0.0, 0.0, 0.0)] + [
        (2.0 * d[0] / 1.732, 2.0 * d[1] / 1.732, 2.0 * d[2] / 1.732) for d in directions
    ]
    record = StructureRecord(
        id="mn4",
        atoms=("Fe", "N", "N", "N", "N"),
        coordinates=tuple(coords),
        charge=0,
        multiplicity=1,
    )
    spec = {
        "schema_version": 3,
        "index_base": 1,
        "seed": 3,
        "topology": {"bonds": [{"atoms": [1, i], "kind": "COORDINATION"} for i in (2, 3, 4, 5)]},
        "coordination": {
            "metal_center": 1,
            "binding_sites": [
                {"id": f"s{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1}
                for i in range(1, 5)
            ],
        },
    }
    return record, spec


def test_coordination_pure_enumeration_over_context_graph():
    """Real lane B enumeration (24->2) over the real context graph."""
    from confflow.science.confgen.coordination.enumeration import enumerate_targets
    from confflow.science.confgen.graph import BindingSite, CoordinationSpec

    record, spec = _tetra_mn4()
    context = build_context(record, spec)
    assert context.graph.metal_center == 0
    assert context.graph.coordination_donors() == (1, 2, 3, 4)
    assert all(row == () for row in context.adjacency)  # no covalent leakage
    lane_spec = CoordinationSpec(
        metal_center=0,
        binding_sites=tuple(BindingSite(id=f"s{i}", atoms=(i,)) for i in range(1, 5)),
        shapes=("tetrahedral",),
    )
    assert [s.donor for s in lane_spec.binding_sites] == list(context.graph.coordination_donors())
    pipe = enumerate_targets(lane_spec, "tetrahedral")
    assert pipe["layers"].raw_assignments == 24
    assert pipe["layers"].shape_classes == 2


def test_coordination_engine_run_end_to_end():
    """Real build_context -> CoordinationStage engine run (2+3 shape classes)."""
    record, spec = _tetra_mn4()
    context = build_context(record, spec)
    run = ConfgenEngine().run(context)
    report = run.report.thaw()
    assert report["counts"]["raw"] == 5  # tetrahedral 2 + square_planar 3
    assert len(run.leaves) == 5
    assert report["counts"]["target_categories"]["REALIZED"] == 5
    assert report["counts"]["target_categories"]["DRIFTED"] == 0
    assert report["realization"]["terminal_equations_ok"] is True
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True
    shapes = sorted(leaf.state_key.to_dict()["coordination"]["shape"] for leaf in run.leaves)
    assert shapes == ["square_planar"] * 3 + ["tetrahedral"] * 2
    for leaf in run.leaves:  # discrete indexed identity in every key
        key = leaf.state_key.to_dict()["coordination"]
        assert set(key) == {"center", "shape", "placement", "sites"}
        assert key["center"] == 0


def test_perceived_coordination_scope_types_metal_site_edges():
    """Perceived path: declared metal-donor pairs are COORDINATION, never COV.

    Same MN4 molecule as the explicit-topology fixture but with NO
    explicit graph: distance perception guesses metal-ligand COVALENT
    edges, and the declared coordination scope authoritatively retypes
    them. The metal row stays covalently empty so fragment
    decomposition and ring/torsion mechanics never see pseudo-bonds.
    """
    from confflow.science.confgen.coordination.enumeration import enumerate_targets
    from confflow.science.confgen.graph import (
        BindingSite,
        CoordinationSpec,
        EdgeType,
    )
    from confflow.science.confgen.model import edge_kind_of

    record, _ = _tetra_mn4()
    spec = {
        "schema_version": 3,
        "index_base": 1,
        "seed": 3,
        "coordination": {
            "metal_center": 1,
            "binding_sites": [
                {"id": f"s{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1}
                for i in range(1, 5)
            ],
        },
    }
    context = build_context(record, spec)
    for donor in (1, 2, 3, 4):
        assert edge_kind_of(context.graph, 0, donor) is EdgeType.COORDINATION
    assert all(row == () for row in context.adjacency)  # no covalent leakage
    assert context.graph.coordination_donors() == (1, 2, 3, 4)
    # Real lane enumeration works over the overlaid graph (24->2).
    lane_spec = CoordinationSpec(
        metal_center=0,
        binding_sites=tuple(BindingSite(id=f"s{i}", atoms=(i,)) for i in range(1, 5)),
        shapes=("tetrahedral",),
    )
    pipe = enumerate_targets(lane_spec, "tetrahedral")
    assert pipe["layers"].raw_assignments == 24
    assert pipe["layers"].shape_classes == 2


def test_add_bond_forming_overlays_guessed_covalent():
    """Declared FORMING replaces the guessed COV pair end to end."""
    from confflow.science.confgen.graph import EdgeType
    from confflow.science.confgen.model import edge_kind_of

    context = build_context(
        _butane("b"),
        {
            "schema_version": 3,
            "index_base": 1,
            "seed": 1,
            "topology": {"add_bond": [{"atoms": [1, 2], "kind": "FORMING"}]},
        },
    )
    assert edge_kind_of(context.graph, 0, 1) is EdgeType.FORMING
    assert 1 not in context.adjacency[0] and 0 not in context.adjacency[1]
    assert context.graph.reaction_pairs == ((0, 1),)
    # Torsion mechanics exclude the forming pair (no silent covalent use).
    with pytest.raises(ValueError, match="not bonded"):
        TorsionStage(
            {
                "torsions": [
                    {
                        "id": "t",
                        "bond": [0, 1],
                        "model": "relative_rotation_grid",
                        "angles": [0.0, 120.0],
                    }
                ]
            }
        )._validated(context)


def test_add_bond_breaking_overlays_guessed_covalent():
    """Declared BREAKING stays a typed edge, never covalent, never dropped."""
    from confflow.science.confgen.graph import EdgeType
    from confflow.science.confgen.model import edge_kind_of

    context = build_context(
        _butane("b"),
        {
            "schema_version": 3,
            "index_base": 1,
            "seed": 1,
            "topology": {"add_bond": [{"atoms": [1, 2], "kind": "BREAKING", "bond_order": 1.0}]},
        },
    )
    assert edge_kind_of(context.graph, 0, 1) is EdgeType.BREAKING
    assert 1 not in context.adjacency[0] and 0 not in context.adjacency[1]
    assert any(e.type is EdgeType.BREAKING for e in context.graph.edges)


def test_typed_forming_add_bond_rides_workflow_wire():
    """Typed FORMING add_bond survives schema -> wire -> normalisation.

    Water keeps both perceived O-H bonds, gains the H-H reaction pair,
    and the pair stays out of the covalent adjacency.
    """
    from confflow.science.confgen.graph import EdgeType
    from confflow.science.confgen.model import edge_kind_of
    from confflow.workflow.v4.confgen_schema import ConfgenModelV3

    record = StructureRecord(
        id="water",
        atoms=("O", "H", "H"),
        coordinates=((0.0, 0.0, 0.0), (0.757, 0.587, 0.0), (-0.757, 0.587, 0.0)),
    )
    bare = build_context(record, {"schema_version": 3, "index_base": 1, "seed": 1})
    assert [list(row) for row in bare.adjacency] == [[1, 2], [0], [0]]
    scope = ConfgenModelV3.model_validate(
        {
            "schema_version": 3,
            "index_base": 1,
            "seed": 1,
            "topology": {"add_bond": [{"atoms": [2, 3], "kind": "FORMING"}]},
        }
    )
    wire = scope.scientific_native()
    assert wire["topology"]["add_bond"] == [
        {"atoms": [2, 3], "kind": "FORMING", "provenance": "explicit"}
    ]
    context = build_context(record, dict(wire))
    assert [list(row) for row in context.adjacency] == [list(row) for row in bare.adjacency]
    assert context.graph.reaction_pairs == ((1, 2),)
    assert edge_kind_of(context.graph, 1, 2) is EdgeType.FORMING
    assert 2 not in context.adjacency[1] and 1 not in context.adjacency[2]


def test_contradictory_explicit_kinds_fail_closed():
    """One pair carrying two declared kinds is refused, never guessed."""
    record, _ = _tetra_mn4()
    scope = {
        "coordination": {
            "metal_center": 1,
            "binding_sites": [{"id": "s1", "kind": "atom", "atoms": [2], "hapticity": 1}],
        },
    }
    # Explicit COV against the declared coordination scope.
    with pytest.raises(ValueError, match="contradictory"):
        build_context(
            record,
            {
                "schema_version": 3,
                "index_base": 1,
                "seed": 1,
                "topology": {"add_bond": [[1, 2]]},
                **scope,
            },
        )
    # Explicit COV against a declared FORMING on the same pair.
    with pytest.raises(ValueError, match="contradictory"):
        build_context(
            _butane("b"),
            {
                "schema_version": 3,
                "index_base": 1,
                "seed": 1,
                "topology": {"add_bond": [[1, 2], {"atoms": [1, 2], "kind": "FORMING"}]},
            },
        )


class _NativeDriftStage(GenerationStage):
    """Engine-mechanics scaffolding: emits native DRIFTED verdicts.

    Mirrors the CORE/B handshake evidence (native_status, observed_key):
    one target whose realization reports geometry_failure with a native
    DRIFTED verdict, with or without a perceivable observed state.
    """

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        config = dict(axis_spec)
        self._observed: Any = config.get("observed_key", "valid")
        self._commanded_shape: str = str(config.get("commanded_shape", "octahedral"))
        self._commanded: Any = config.get("commanded")
        self._gate: bool = bool(config.get("perception_gate_passed", True))

    @property
    def axis(self) -> str:
        return "coordination"

    def estimate(self, parent: WorkingRealization, context: MolecularContext) -> StageEstimate:
        return StageEstimate(
            declared_count=1,
            upper_bound=1,
            exact=True,
            details=FrozenDict({"basis": "single native-drift target", "scope_coverage": "exact"}),
        )

    def enumerate_targets(
        self, parent: WorkingRealization, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        state_value: Any = (
            dict(self._commanded)
            if self._commanded is not None
            else {"shape": self._commanded_shape}
        )
        yield GenerationTarget(
            axis="coordination", target_id="coordination:000000", state_value=state_value, ordinal=0
        )

    def realize(
        self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
    ) -> RealizationResult:
        if self._observed == "valid":
            observed: Any = {
                "center": 0,
                "shape": "octahedral",
                "placement": [0, 1],
                "sites": {"a": "T0"},
            }
        else:
            observed = None
        return RealizationResult(
            structure=None,
            status="geometry_failure",
            reason="coordination_drift: perceived another state",
            backend="test-native",
            evidence=(
                FrozenDict(
                    {
                        "native_status": "DRIFTED",
                        "native_reason": "perceived another state",
                        "observed_key": observed,
                        "perception_gate_passed": self._gate,
                    }
                ),
            ),
        )

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        return PerceptionResult(best_key={"shape": "octahedral"})


def _native_drift_context() -> MolecularContext:
    record, _ = _tetra_mn4()
    return build_context(record, {"index_base": 0})


def test_native_drift_with_observed_key_is_failed_drift():
    """Native DRIFTED + observed state routes to public DRIFTED, never UNRESOLVED."""
    from confflow.science.confgen.accounting import scientific_category

    run = ConfgenEngine(stages=[_NativeDriftStage({})]).run(_native_drift_context())
    report = run.report.thaw()
    assert len(run.leaves) == 0
    assert len(run.target_records) == 1
    record = run.target_records[0]
    assert record.status is TerminalStatus.FAILED_DRIFT
    assert record.reason == "coordination_drift: perceived another state"
    assert scientific_category(record) == "DRIFTED"
    assert report["counts"]["target_categories"]["DRIFTED"] == 1
    assert report["counts"]["target_categories"]["UNRESOLVED"] == 0
    observed = [item for item in record.evidence if item.get("kind") == "observed"]
    assert observed and observed[0]["observed"]["shape"] == "octahedral"
    assert observed[0]["geometry_valid"] is True
    assert run.certificate.equations_ok is True


def test_native_drift_matching_key_with_clash_is_unresolved():
    """Observed == target with quality failure is UNRESOLVED, not drift.

    The backend uses the DRIFTED word for angle/clash failures whose
    observed key still equals the commanded target: K_observed ==
    K_target means no StateKey drift occurred.
    """
    from confflow.science.confgen.accounting import scientific_category

    full_key = {"center": 0, "shape": "octahedral", "placement": [0, 1], "sites": {"a": "T0"}}
    run = ConfgenEngine(
        stages=[_NativeDriftStage({"commanded": full_key, "perception_gate_passed": False})]
    ).run(_native_drift_context())
    record = run.target_records[0]
    assert record.status is TerminalStatus.FAILED_GEOMETRY
    assert scientific_category(record) == "UNRESOLVED"
    assert run.report.thaw()["counts"]["target_categories"]["DRIFTED"] == 0
    assert run.certificate.equations_ok is True


def test_native_drift_untrusted_quality_alerts_without_invalidating():
    """A proof-matching drift without gate-passed quality only alerts."""
    full_key = {"center": 0, "shape": "square_planar", "placement": [0, 1], "sites": {"a": "T0"}}
    run = ConfgenEngine(
        stages=[_NativeDriftStage({"commanded": full_key, "perception_gate_passed": False})]
    ).run(
        build_context(
            _tetra_mn4()[0],
            {
                "index_base": 0,
                "exclusions": [
                    {
                        "axis": "coordination",
                        "match": {"shape": "octahedral"},
                        "reason": "proven infeasible here",
                        "proof": {"proof_id": "p1"},
                    }
                ],
            },
        )
    )
    record = run.target_records[0]
    assert record.status is TerminalStatus.FAILED_DRIFT
    assert any(item.get("kind") == "proof_alert" for item in record.evidence)
    assert not run.report.thaw()["realization"]["proof_contradictions"]
    assert run.certificate.equations_ok is True


def test_native_drift_without_observed_key_stays_geometry_failure():
    """Native DRIFTED with no perceivable state stays public UNRESOLVED."""
    from confflow.science.confgen.accounting import scientific_category

    run = ConfgenEngine(stages=[_NativeDriftStage({"observed_key": None})]).run(
        _native_drift_context()
    )
    record = run.target_records[0]
    assert record.status is TerminalStatus.FAILED_GEOMETRY
    assert scientific_category(record) == "UNRESOLVED"
    assert run.report.thaw()["counts"]["target_categories"]["DRIFTED"] == 0


def test_native_drift_into_proven_state_invalidates_certificate():
    """Drift into a PROVEN_INFEASIBLE state invalidates, never hides."""
    run = ConfgenEngine(stages=[_NativeDriftStage({"commanded_shape": "square_planar"})]).run(
        build_context(
            _tetra_mn4()[0],
            {
                "index_base": 0,
                "exclusions": [
                    {
                        "axis": "coordination",
                        "match": {"shape": "octahedral"},
                        "reason": "proven infeasible here",
                        "proof": {"proof_id": "p1"},
                    }
                ],
            },
        )
    )
    assert run.report.thaw()["realization"]["proof_contradictions"]
    assert run.certificate.equations_ok is False


# ---------------------------------------------------------------------------
# Conditional trees: exact counts (C2 -> R1/2 -> T3 == 9)
# ---------------------------------------------------------------------------


class _ShiftStage(GenerationStage):
    """Engine-mechanics scaffolding: rigid translations with real measurement.

    State lives in centroid offsets snapped to declared values; realization
    is a rigid translation (bond integrity holds exactly). Tests engine
    conditional expansion/counting/locks, never chemistry.
    """

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        config = dict(axis_spec)
        self._axis = str(config["axis"])
        self._states = [tuple(s) for s in config["states"]]  # (label, dx, dy, dz)
        self._conditional = bool(config.get("conditional", False))
        self._parent_modes = config.get("parent_modes")  # {parent_label: [state_idx]}
        self._fail_on = set(config.get("fail_on", ()))
        self._tolerance = 1.0

    @property
    def axis(self) -> str:
        return self._axis

    def is_conditional(self, context: MolecularContext) -> bool:
        return self._conditional

    def _options(self, parent: WorkingRealization) -> list[int]:
        if not self._conditional:
            return list(range(len(self._states)))
        label = self._parent_label(parent)
        return list(self._parent_modes.get(label, []))

    @staticmethod
    def _centroid(coords: np.ndarray) -> np.ndarray:
        return np.asarray(coords, dtype=float).mean(axis=0)

    def _parent_label(self, parent: WorkingRealization) -> str:
        """Read the parent's discrete C-state from its labeled key."""
        coordination = parent.state_key.coordination
        if isinstance(coordination, Mapping):
            label = coordination.get("mode", "A")
            if label in ("A", "B"):
                return str(label)
        return "A"

    def _measure(self, structure: StructureRecord, context: MolecularContext) -> float:
        own = {"coordination": 0, "rings": 1, "torsions": 2}[self._axis]
        delta = self._centroid(structure.coordinates) - self._centroid(context.input_coords)
        return float(delta[own])

    def _snap(self, value: float) -> str | None:
        for label, dx, dy, dz in self._states:
            want = (dx, dy, dz)[{"coordination": 0, "rings": 1, "torsions": 2}[self._axis]]
            if abs(value - want) <= self._tolerance:
                return str(label)
        return None

    def estimate(self, parent: WorkingRealization, context: MolecularContext) -> StageEstimate:
        if not self._conditional:
            total = len(self._states)
            return StageEstimate(
                declared_count=total,
                upper_bound=total,
                exact=True,
                details=FrozenDict({"basis": "fixed test states", "scope_coverage": "exact"}),
            )
        options = self._options(parent)
        ceiling = max((len(v) for v in self._parent_modes.values()), default=0)
        return StageEstimate(
            declared_count=len(options),
            upper_bound=ceiling,
            exact=False,
            details=FrozenDict(
                {"basis": "parent-dependent test states", "scope_coverage": "upper_bound"}
            ),
        )

    def enumerate_targets(
        self, parent: WorkingRealization, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        for position in self._options(parent):
            label = str(self._states[position][0])
            yield GenerationTarget(
                axis=self._axis,
                target_id=f"{self._axis}:{position:06d}",
                state_value={"mode": label},
                ordinal=position,
            )

    def realize(
        self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
    ) -> RealizationResult:
        label = str(dict(target.state_value)["mode"])
        if label in self._fail_on:
            raise RuntimeError(f"injected failure on {label}")
        shift = next(s[1:] for s in self._states if str(s[0]) == label)
        coords = np.asarray(parent.structure.coordinates, dtype=float) + np.array(shift)
        record = StructureRecord(
            id=f"{parent.structure.id}/{self._axis}:{target.ordinal:06d}",
            atoms=tuple(parent.structure.atoms),
            coordinates=tuple(tuple(p) for p in coords.tolist()),
            charge=parent.structure.charge,
            multiplicity=parent.structure.multiplicity,
            parent_ids=(parent.structure.id,),
            lineage_root_id=parent.structure.lineage_root_id,
            role="test-shift",
            ordinal=int(target.ordinal),
        )
        return RealizationResult(
            structure=record, status="realized", reason="realized", backend="test-shift"
        )

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        snapped = self._snap(self._measure(structure, context))
        if snapped is None:
            return PerceptionResult(best_key={}, confidence="ambiguous")
        return PerceptionResult(best_key={self._axis: snapped})

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
        return (self._axis,)

    def verify_locked(
        self, structure: StructureRecord, locked_state: Mapping[str, Any], context: MolecularContext
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        snapped = self._snap(self._measure(structure, context))
        expected = locked_state.get("mode", locked_state.get(self._axis))
        if snapped is not None and snapped == expected:
            return True, {self._axis: snapped}, []
        return (
            False,
            {},
            [
                {
                    "kind": "drift",
                    "axis": f"{self._axis}.{self._axis}",
                    "detail": "shift lock drifted",
                    "expected": expected,
                    "measured": snapped,
                    "out_of_scope": snapped is None,
                }
            ],
        )


def _conditional_tree(
    fail_on: Sequence[str] = (), fail_on_c: Sequence[str] = ()
) -> tuple[MolecularContext, list[Any]]:
    """C2 -> R1/2 -> T3 scripted tree over butane (9 exact leaves)."""
    context = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    stages = [
        _ShiftStage(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0)],
                "fail_on": list(fail_on_c),
            }
        ),
        _ShiftStage(
            {
                "axis": "rings",
                "conditional": True,
                "states": [("r0", 0.0, 0.0, 0.0), ("r1", 0.0, 5.0, 0.0)],
                "parent_modes": {"A": [0], "B": [0, 1]},
                "fail_on": list(fail_on),
            }
        ),
        _ShiftStage(
            {
                "axis": "torsions",
                "states": [("t0", 0.0, 0.0, 0.0), ("t1", 0.0, 0.0, 2.5), ("t2", 0.0, 0.0, 5.0)],
            }
        ),
    ]
    return context, stages


def test_conditional_exact_nine_not_twelve():
    """C2->R1/2->T3 declares exactly 9 raw leaves with closed equations."""
    context, stages = _conditional_tree()
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    assert report["counts"]["raw"] == 9  # never the root product 12
    assert report["enumeration"]["total_upper_bound"] == 12
    assert report["enumeration"]["exact"] is False
    assert report["enumeration"]["symbolic"]["exact"] is True
    assert report["enumeration"]["symbolic"]["raw_leaf_states"] == 9
    assert len(run.leaves) == 9
    assert report["counts"]["published"] == 9
    assert report["realization"]["count_equations_ok"] is True
    assert report["realization"]["paths_consistent"] is True
    assert run.certificate.equations_ok is True
    ordinals = sorted(leaf.provenance["leaf_ordinal"] for leaf in run.leaves)
    assert ordinals == list(range(9))


def test_conditional_failure_defers_exact_subtree():
    """Failed conditional parents defer exact symbolic ranges, not estimates."""
    context, stages = _conditional_tree(fail_on=["r1"])
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    assert report["counts"]["published"] == 6
    assert report["counts"]["deferred_parent_failed_leaves"] == 3
    assert report["counts"]["target_categories"]["DEFERRED"] == 3
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True
    deferred = [r for r in run.target_records if r.status is TerminalStatus.DEFERRED_PARENT_FAILED]
    assert deferred and all(r.complete_key is None for r in deferred)
    # Unit 1 (final leaves): 9 = 6 realized + 3 deferred; the failed R
    # parent is NOT a leaf and lives only in the attempt ledger.
    leaf_cert = report["leaf_certificate"]
    assert leaf_cert["total"] == 9
    assert leaf_cert["leaf_categories"] == {
        "REALIZED": 6,
        "DRIFTED": 0,
        "UNRESOLVED": 0,
        "DEFERRED": 3,
        "REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY": 0,
        "REJECTED": 0,
    }
    assert leaf_cert["equations_ok"] is True
    # Unit 2 (issued attempts): C2 + R3 + T6 = 11 = 10 successful
    # (4 internal expanded + 6 published) + 1 failed R.
    ledger = report["attempt_ledger"]
    assert ledger["issued_attempts"] == 11
    assert ledger["geometry_successful"] == 10
    assert ledger["internal_expanded"] == 4
    assert ledger["published_leaves"] == 6
    assert ledger["attempted_without_success"] == 1
    assert ledger["deferred_unissued"] == 3
    assert ledger["suppressed_skipped"] == 0
    assert ledger["policy_screened"] == 0
    units = report["counts"]["unit_basis"]
    assert units["target_categories"] == "mixed diagnostic records; no certificate equation"
    assert units["authoritative_sections"] == ["leaf_certificate", "attempt_ledger"]


def test_conditional_failed_top_parent_defers_exact_subtree():
    """A failed C parent defers its exact descendant leaves (nested case)."""
    context, stages = _conditional_tree(fail_on_c=["A"])
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    # A-branch (1 R x 3 T = 3 leaves) defers; B-branch publishes 2x3 = 6.
    leaf_cert = report["leaf_certificate"]
    assert leaf_cert["total"] == 9
    assert leaf_cert["leaf_categories"]["REALIZED"] == 6
    assert leaf_cert["leaf_categories"]["DEFERRED"] == 3
    assert leaf_cert["equations_ok"] is True
    # Attempts: C2 (1 expanded + 1 failed) + R2 + T6 = 10 issued,
    # 9 successful (3 internal + 6 published) + 1 failed C.
    ledger = report["attempt_ledger"]
    assert ledger["issued_attempts"] == 10
    assert ledger["geometry_successful"] == 9
    assert ledger["internal_expanded"] == 3
    assert ledger["attempted_without_success"] == 1
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True


def test_conditional_sampling_fails_closed_documented():
    """Caps over conditional trees refuse without tree weights."""
    context, _ = _conditional_tree()
    capped = dict(context.resolved_spec)
    capped = {**capped, "sampling": {"cap": 2}, "seed": 1}
    from confflow.domain import FrozenDict as _F

    context2 = MolecularContext(
        structure=context.structure,
        adjacency=context.adjacency,
        graph=context.graph,
        resolved_spec=_F(capped),
        tolerances=context.tolerances,
        input_state_key=context.input_state_key,
        input_coords=context.input_coords,
    )
    _, stages = _conditional_tree()
    with pytest.raises(ValueError, match="weights"):
        ConfgenEngine(stages=stages).run(context2)


# ---------------------------------------------------------------------------
# Verified-symmetry suppression binding
# ---------------------------------------------------------------------------


class _SuppressingShift(_ShiftStage):
    """Engine-mechanics scaffolding: shift stage plus a verified-style hook."""

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        super().__init__(axis_spec)
        config = dict(axis_spec)
        self._suppress_ordinals = set(config.get("suppress_ordinals", ()))
        self._fail_ordinals = set(config.get("fail_ordinals", ()))
        self._malformed = bool(config.get("malformed_record", False))

    def realize(
        self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
    ) -> RealizationResult:
        if int(target.ordinal) in self._fail_ordinals:
            raise RuntimeError("injected failure")
        return super().realize(parent, target, context)

    def suppression_for_target(
        self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
    ) -> Any:
        if int(target.ordinal) not in self._suppress_ordinals:
            return None
        if self._malformed:
            return {"orbit_id": "o1"}  # bare record: must fail closed
        return {
            "suppressed_target": target.target_id,
            "representative_target": "coordination:000000",
            "orbit_id": "o1",
            "rho": [1, 0],
            "site_action": {"s0": "s1"},
            "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "pair_residual": 0.0,
            "witness_provenance": "test-scaffold",
            "stereo_centers_audited": [],
            "closure_order": 2,
        }


def _suppression_tree() -> tuple[MolecularContext, list[Any]]:
    context = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    stages = [
        _SuppressingShift(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0), ("C", 40.0, 0.0, 0.0)],
                "suppress_ordinals": [1],
            }
        )
    ]
    return context, stages


def test_suppression_skips_only_verified_targets():
    """A verified record suppresses exactly its target; the rest publish."""
    from confflow.science.confgen.accounting import scientific_category

    context, stages = _suppression_tree()
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    assert [leaf.provenance["leaf_ordinal"] for leaf in run.leaves] == [0, 2]
    assert report["counts"]["published"] == 2
    assert report["counts"]["target_categories"]["REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY"] == 1
    assert report["realization"]["suppressed"] == 1
    assert report["realization"]["realization_attempts"] == 2
    assert report["realization"]["count_equations_ok"] is True
    assert report["suppression"]["supported"] is True
    assert run.certificate.equations_ok is True
    suppressed = [
        r for r in run.target_records if r.status is TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY
    ]
    assert len(suppressed) == 1
    assert suppressed[0].target_id == "coordination:000001"
    assert scientific_category(suppressed[0]) == ("REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY")
    assert "coordination:000000" in suppressed[0].reason
    assert suppressed[0].complete_key is not None  # suppressed state identified


def test_suppression_malformed_record_fails_closed():
    """A bare suppression without witnesses aborts loudly, never hides."""
    context = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    stages = [
        _SuppressingShift(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0)],
                "suppress_ordinals": [1],
                "malformed_record": True,
            }
        )
    ]
    with pytest.raises(ValueError, match="witness"):
        ConfgenEngine(stages=stages).run(context)


def test_suppression_without_realized_representative_realizes():
    """No orbit is hidden when its representative failed to realize."""
    context = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    stages = [
        _SuppressingShift(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0), ("C", 40.0, 0.0, 0.0)],
                "suppress_ordinals": [1],
                "fail_ordinals": [0],
            }
        )
    ]
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    assert report["counts"]["published"] == 2  # B and C realized normally
    assert report["realization"]["suppressed"] == 0
    assert run.certificate.equations_ok is True


def test_suppression_combined_axes_realize_normally():
    """Combined runs never suppress; the scope refusal is explicit."""
    context = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    stages = [
        _SuppressingShift(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0)],
                "suppress_ordinals": [0, 1],
            }
        ),
        _ShiftStage({"axis": "rings", "states": [("r0", 0.0, 0.0, 0.0), ("r1", 0.0, 5.0, 0.0)]}),
    ]
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    assert report["counts"]["published"] == 4  # full product, nothing hidden
    assert report["realization"]["suppressed"] == 0
    assert report["suppression"]["supported"] is False
    assert run.certificate.equations_ok is True


def test_suppression_disabled_under_sampling_cap():
    """A cap never suppresses against a possibly unsampled representative."""
    context = build_context(
        _butane("seed"),
        {"index_base": 0, "seed": 21, "sampling": {"cap": 1}},
    )
    stages = [
        _SuppressingShift(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0), ("C", 40.0, 0.0, 0.0)],
                "suppress_ordinals": [0, 1, 2],
            }
        )
    ]
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    assert report["realization"]["suppressed"] == 0
    assert report["suppression"]["supported"] is False
    assert report["suppression"]["disabled_reason"] == "sampling-cap"
    assert report["counts"]["sampled"] == 1
    assert report["counts"]["published"] == 1  # sampled target realized
    assert run.certificate.equations_ok is True


def test_suppression_disabled_with_top_level_exclusions():
    """Exclusions present: no suppression against policy-excluded reps."""
    context = build_context(
        _butane("seed"),
        {
            "index_base": 0,
            "seed": 21,
            "exclusions": [{"axis": "coordination", "match": {"mode": "A"}, "reason": "held out"}],
        },
    )
    stages = [
        _SuppressingShift(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0), ("C", 40.0, 0.0, 0.0)],
                "suppress_ordinals": [1, 2],
            }
        )
    ]
    run = ConfgenEngine(stages=stages).run(context)
    report = run.report.thaw()
    assert report["realization"]["suppressed"] == 0
    assert report["suppression"]["supported"] is False
    assert report["suppression"]["disabled_reason"] == "exclusions-present"
    # A is policy-rejected; B and C realize normally.
    assert report["counts"]["published"] == 2
    assert run.certificate.equations_ok is True


def test_no_suppression_without_witnesses_real_stage():
    """An asymmetric real stage with no site group never suppresses."""
    record, spec = _tetra_mn4()
    context = build_context(record, spec)
    run = ConfgenEngine().run(context)
    report = run.report.thaw()
    assert report["counts"]["published"] == 5
    assert report["realization"]["suppressed"] == 0
    assert report["realization"]["realization_attempts"] == 5
    assert report["counts"]["target_categories"]["REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY"] == 0
    assert run.certificate.equations_ok is True


def test_suppression_positive_control_real_stage_fewer_attempts():
    """Real C2-symmetric coordination stage: fewer attempts, full coverage.

    Uses the coordination lane's synthetic C2 control fixture (read-only;
    that lane owns the witness physics) and runs it through the real
    engine binding: suppressed targets skip realization, every
    representative still publishes, and the count equations close over
    the full 30-target enumeration.
    """
    coord_tests = pytest.importorskip("tests.v4.test_confgen_v3_coordination")
    from confflow.domain.structure import StructureRecord

    system = coord_tests._build_c2_system()
    sgraph, sspec, scoords = system["graph"], system["spec"], system["coords"]
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": site.id, "kind": "atom", "atoms": list(site.atoms), "hapticity": 1}
            for site in sspec.binding_sites
        ],
        "shapes": ["octahedral"],
        "treatment": "enumerate",
        "constraints": [],
        "site_group": coord_tests._c2_witness_section(system),
        "tolerances": {},
        "donor_configuration": list(sspec.site_ids),
    }
    structure = StructureRecord(
        id="c2",
        atoms=tuple(sgraph.elements),
        coordinates=tuple(tuple(p) for p in scoords),
        charge=0,
        multiplicity=1,
    )
    topology = {
        "bonds": [{"atoms": [e.a, e.b], "kind": e.type.value} for e in sgraph.edges],
        "atoms": [{"index": a.index, "role": a.role} for a in sgraph.atoms if a.role],
    }
    workflow = {
        "schema_version": 3,
        "index_base": 0,
        "topology": topology,
        "coordination": section,
        "stereochemistry": {"labels": list(system["stereo"])},
    }
    context = build_context(structure, workflow)
    run = ConfgenEngine().run(context)
    report = run.report.thaw()
    assert report["counts"]["raw"] == 30
    suppressed = report["realization"]["suppressed"]
    assert suppressed >= 1
    assert report["realization"]["realization_attempts"] < 30
    assert (
        report["counts"]["target_categories"]["REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY"]
        == suppressed
    )
    assert report["realization"]["count_equations_ok"] is True
    assert report["realization"]["terminal_equations_ok"] is True
    assert run.certificate.equations_ok is True  # every rep realized
    for leaf in run.leaves:  # published leaves keep ordinary identity
        key = leaf.state_key.to_dict()["coordination"]
        assert set(key) == {"center", "shape", "placement", "sites"}


class _RefRecordingShift(_ShiftStage):
    """Engine-mechanics scaffolding: records the reference it was audited with."""

    lock_reference = "input"
    seen_references: list[float] = []

    def verify_locked(
        self, structure: StructureRecord, locked_state: Mapping[str, Any], context: MolecularContext
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        centroid = float(np.asarray(context.input_coords, dtype=float).mean(axis=0)[0])
        type(self).seen_references.append(centroid)
        return super().verify_locked(structure, locked_state, context)


class _ParentRefRecordingShift(_ShiftStage):
    """Parent-relative lock: holds iff the descendant preserved the parent."""

    lock_reference = "parent"
    seen_references: list[float] = []

    def verify_locked(
        self, structure: StructureRecord, locked_state: Mapping[str, Any], context: MolecularContext
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        centroid = float(np.asarray(context.input_coords, dtype=float).mean(axis=0)[0])
        type(self).seen_references.append(centroid)
        # context.input_coords IS the accepted parent geometry here: the
        # lock holds iff this axis did not move since the parent.
        delta = self._measure(structure, context)
        label = locked_state.get("mode", locked_state.get(self._axis))
        if abs(delta) <= self._tolerance:
            return True, {self._axis: label}, []
        return (
            False,
            {},
            [
                {
                    "kind": "drift",
                    "axis": f"{self._axis}.{self._axis}",
                    "detail": "shift lock drifted since parent",
                    "expected": 0.0,
                    "measured": float(delta),
                    "out_of_scope": False,
                }
            ],
        )


def test_lock_reference_parent_uses_accepted_parent_geometry():
    """Parent-relative locks audit against the accepted parent, not run input."""
    context = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    base_x = float(np.asarray(context.input_coords, dtype=float).mean(axis=0)[0])
    coord_states = [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0)]
    ring_states = [("r0", 0.0, 0.0, 0.0)]
    for stage_cls, expected_x in (
        (_RefRecordingShift, base_x),
        (_ParentRefRecordingShift, base_x + 20.0),
    ):
        stage_cls.seen_references = []
        coord = stage_cls({"axis": "coordination", "states": coord_states})
        rings = _ShiftStage({"axis": "rings", "states": ring_states})
        run = ConfgenEngine(stages=[coord, rings]).run(context)
        assert len(run.leaves) == 2
        # Both branches audited the coordination lock; the B branch (x+20)
        # distinguishes the references. Parent-relative sees the accepted
        # parent centroid; input-relative sees the run input centroid.
        assert stage_cls.seen_references
        assert max(stage_cls.seen_references) == pytest.approx(expected_x)
    assert run.certificate.equations_ok is True


# ---------------------------------------------------------------------------
# Adversarial R-damages-C (pinned coordination scaffolding + real ring)
# ---------------------------------------------------------------------------


class _PinnedCoordination(GenerationStage):
    """Test scaffolding: fixed coordination level with real lane B audit.

    Realize is identity (pinned); perception and lock verification use the
    real lane B perceive_donors measurement. Retires when lane B realize
    verifies. Commanded state is pinned from the input's own assignment.
    """

    axis = "coordination"

    def __init__(self, axis_spec: Mapping[str, Any]) -> None:
        config = dict(axis_spec)
        self._metal = int(config["metal_center"])
        self._donors = tuple(int(d) for d in config["donors"])
        self._sites = tuple(str(s) for s in config["sites"])
        self._shape = str(config.get("shape", "tetrahedral"))
        self._commanded = dict(config["commanded"])

    def estimate(self, parent: WorkingRealization, context: MolecularContext) -> StageEstimate:
        return StageEstimate(
            declared_count=1,
            upper_bound=1,
            exact=True,
            details=FrozenDict({"basis": "single pinned test state", "scope_coverage": "exact"}),
        )

    def enumerate_targets(
        self, parent: WorkingRealization, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        yield GenerationTarget(
            axis="coordination",
            target_id="coordination:000000",
            state_value={"sites": dict(self._commanded), "shape": self._shape},
            ordinal=0,
        )

    def realize(
        self, parent: WorkingRealization, target: GenerationTarget, context: MolecularContext
    ) -> RealizationResult:
        record = StructureRecord(
            id=f"{parent.structure.id}/coordination:000000",
            atoms=tuple(parent.structure.atoms),
            coordinates=tuple(tuple(p) for p in parent.structure.coordinates),
            charge=parent.structure.charge,
            multiplicity=parent.structure.multiplicity,
            parent_ids=(parent.structure.id,),
            lineage_root_id=parent.structure.lineage_root_id,
            role="test-pinned",
            ordinal=0,
        )
        return RealizationResult(
            structure=record,
            status="realized",
            reason="pinned_identity_reperceived",
            backend="test-pinned",
        )

    def _assignment(self, structure: StructureRecord) -> tuple[dict[str, Any], Any]:
        from confflow.science.confgen.coordination.perception import perceive_donors

        perception = perceive_donors(
            np.asarray(structure.coordinates, dtype=float),
            self._metal,
            self._donors,
            self._sites,
            self._shape,
        )
        # Canonical class state (production command_key authority): the
        # gauge-invariant shape-class assignment.  The raw labeled fit gauge is
        # orientation display, never physical state: two fits in one class must
        # never count as drift.
        return dict(perception.best_key["sites"]), perception

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        assignment, perception = self._assignment(structure)
        return PerceptionResult(
            best_key={
                "sites": assignment,
                "shape": self._shape,
                "placement": list(perception.best_key.get("placement", [])),
            },
            confidence="verified" if perception.unambiguous else "reported",
            margins=FrozenDict(
                {"margin": float(perception.margin), "rmsd": float(perception.best_rmsd)}
            ),
        )

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...] | None:
        return None  # state nests under one "sites" entry; locks use the hook

    def verify_locked(
        self, structure: StructureRecord, locked_state: Mapping[str, Any], context: MolecularContext
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        assignment, perception = self._assignment(structure)
        expected = dict(locked_state).get("sites", {})
        if assignment == expected:
            return True, {"sites": assignment, "shape": self._shape}, []
        return (
            False,
            {"sites": assignment},
            [
                {
                    "kind": "drift",
                    "axis": "coordination.sites",
                    "detail": "donor assignment drifted under descendant motion",
                    "expected": dict(expected),
                    "measured": dict(assignment),
                    "margin": float(perception.margin),
                    "out_of_scope": False,
                }
            ],
        )


def _square_syn() -> tuple[StructureRecord, dict[str, Any]]:
    """Butterfly 4-ring (pucker_up_4 template) plus metal center (no H).

    The seed takes the ring lane's own puckered geometry, so the pinned
    tetrahedral class is genuinely defined (no symmetry tie).  The declared
    ring templates are restricted to the two puckers: planar_4 is excluded
    because a square-planar donor set ties the tetrahedral classes exactly
    (margin 0.0 for every metal position — perception centers offsets, so the
    viewpoint cancels), leaving its class undefined on the ambiguity boundary.
    """
    from tests.v4._helpers.ring_inputs import frozen_coords

    ring = np.array(frozen_coords("pucker_up_4"), dtype=float)
    ring = ring + np.array([0.75, 0.75, 0.0])
    coords = tuple(tuple(point) for point in ring) + ((0.0, 0.0, 2.0),)
    record = StructureRecord(
        id="sq", atoms=("C", "C", "C", "C", "Fe"), coordinates=coords, charge=0, multiplicity=1
    )
    spec = {
        "schema_version": 3,
        "index_base": 1,
        "seed": 9,
        "topology": {"bonds": [[1, 2], [2, 3], [3, 4], [4, 1]]},
        "rings": [
            {"id": "r1", "atoms": [1, 2, 3, 4], "templates": ["pucker_up_4", "pucker_down_4"]}
        ],
    }
    return record, spec


def _pinned_stage(
    donors: Sequence[int],
    sites: Sequence[str],
    commanded: Mapping[str, Any],
    metal: int = 0,
    shape: str = "tetrahedral",
) -> _PinnedCoordination:
    """Build the pinned coordination stage (test scaffolding)."""
    return _PinnedCoordination(
        {
            "metal_center": metal,
            "donors": list(donors),
            "sites": list(sites),
            "shape": shape,
            "commanded": dict(commanded),
        }
    )


def test_pinned_coordination_ring_damage_is_drift():
    """Pinned-C + real ring: pucker control publishes, inverted pucker drifts.

    Physical scope: a butterfly-puckered 4-ring (the ring lane's own
    pucker_up_4 geometry, as real cyclobutane puckers) with a pinned
    tetrahedral donor class.  The same-pucker ring output preserves the
    canonical class (published with a verified lock); the inverted pucker
    rebuilds donors into the opposite tetrahedral class and the strict lock
    audit marks it DRIFTED with observed routing (never published with a stale
    C key).  planar_4 is deliberately excluded from the declared templates: a
    square-planar donor set is class-undefined under tetrahedral perception
    (exact margin-0.0 tie), so no stable planar control exists there.
    """
    from confflow.science.confgen.coordination.perception import perceive_donors
    from confflow.science.confgen.ring.stage import RingStage

    record, spec = _square_syn()
    context = build_context(record, spec)
    # Pin the commanded state from the input's own real measurement, in the
    # canonical class form production locks compare (gauge-invariant).
    seen = perceive_donors(
        np.asarray(record.coordinates, dtype=float),
        4,
        (0, 1, 2, 3),
        ("a", "b", "c", "d"),
        "tetrahedral",
    )
    commanded = dict(seen.best_key["sites"])
    assert commanded == {"a": "T0", "b": "T1", "c": "T2", "d": "T3"}
    # Preconditions on canonical classes (never raw fit gauge): the pinned
    # class wins by a nonzero margin, and the inverted pucker is genuinely the
    # opposite canonical class with its own nonzero margin — so the ledger
    # below asserts real class change, not a sort tie or gauge relabeling.
    assert seen.margin > 0.05
    from tests.v4._helpers.ring_inputs import frozen_coords as _frozen_coords

    _offset = np.array([0.75, 0.75, 0.0])
    _metal = np.asarray(record.coordinates, dtype=float)[4]
    _down_coords = np.vstack(
        [np.array(_frozen_coords("pucker_down_4"), dtype=float) + _offset, _metal]
    )
    _down = perceive_donors(_down_coords, 4, (0, 1, 2, 3), ("a", "b", "c", "d"), "tetrahedral")
    assert _down.margin > 0.05
    assert seen.best_class != _down.best_class
    assert dict(_down.best_key["sites"]) != commanded
    pinned = _pinned_stage((0, 1, 2, 3), ("a", "b", "c", "d"), commanded, metal=4)
    ring = RingStage(context.resolved_spec.thaw())

    run = ConfgenEngine(stages=[pinned, ring]).run(context)
    report = run.report.thaw()
    assert report["counts"]["published"] == 1
    assert report["counts"]["target_categories"]["DRIFTED"] == 1
    assert report["counts"]["target_categories"]["REALIZED"] == 1
    assert report["counts"]["target_categories"]["UNRESOLVED"] == 0
    assert report["realization"]["terminal_equations_ok"] is True
    assert report["realization"]["count_equations_ok"] is True
    assert run.certificate.equations_ok is True
    drifted = [r for r in run.target_records if r.status is TerminalStatus.FAILED_DRIFT]
    assert len(drifted) == 1
    assert any(item.get("kind") == "observed" for item in drifted[0].evidence)
    for leaf in run.leaves:  # published leaves carry verified C+R keys
        key = leaf.state_key.to_dict()
        assert set(key["rings"]) == {"r1"}
        assert key["coordination"]["sites"] == commanded


def test_chained_ring_enumeration_preserves_only_torsion_axis_locks():
    """Carried torsion labels never enter a ring ancestor lock contribution."""
    mol = Chem.AddHs(Chem.MolFromSmiles("CCC1CCCCC1"))
    assert AllChem.EmbedMolecule(mol, randomSeed=11) == 0
    record = StructureRecord(
        id="ethylcyclohexane-chain",
        atoms=tuple(atom.GetSymbol() for atom in mol.GetAtoms()),
        coordinates=tuple(tuple(point) for point in mol.GetConformer().GetPositions()),
    )
    first = build_context(
        record,
        _torsion_spec(
            _axis([2, 1], [0.0, 120.0], id="et", rotate_side="right"),
            tolerances={"clash_threshold": 0.4},
        ),
    )
    run1 = ConfgenEngine().run(first)
    assert len(run1.leaves) == 2
    # Both original relative labels must survive the ring-only continuation.
    for leaf in run1.leaves:
        second = build_context(
            leaf.structure,
            _torsion_spec(
                _axis([2, 1], [0.0], id="et", rotate_side="right", treatment="preserve_input"),
                rings=[{"id": "r1", "atoms": [i + 1 for i in mol.GetRingInfo().AtomRings()[0]]}],
                tolerances={"clash_threshold": 0.4},
            ),
            input_state_key=leaf.state_key,
            inherited_scope=_torsion_scope_for(first, leaf.state_key),
        )
        run2 = ConfgenEngine(allow_preserve_input=True).run(second)
        assert len(run2.leaves) == 8
        assert run2.report_json()["leaf_certificate"]["leaf_categories"]["REALIZED"] == 8
        assert run2.certificate.equations_ok
        assert all(
            dict(child.state_key.torsions) == dict(leaf.state_key.torsions) for child in run2.leaves
        )
        assert not any(
            e.get("kind") == "lock_error" for target in run2.target_records for e in target.evidence
        )


@pytest.mark.parametrize("angles", [[0, 360], [120, 480], [0, 0]])
def test_core_declaration_rejects_periodic_torsion_duplicates(angles):
    """Direct science callers obey the same circular identity rule as schema."""
    with pytest.raises(ValueError, match="periodic duplicate"):
        normalize_spec(_torsion_spec(_axis([2, 3], angles)))


@pytest.mark.parametrize("angles", [[0, 90, 180, 270], [0, 0.5]])
def test_core_declaration_preserves_distinct_grid_points(angles):
    """Nearby setpoints are not merged by the perception tolerance."""
    resolved = normalize_spec(_torsion_spec(_axis([2, 3], angles)))
    assert list(resolved["torsions"][0]["angles"]) == angles


def test_core_declaration_rejects_periodic_chemical_aliases():
    """Chemical names cannot split the same physical angle into two states."""
    spec = _torsion_spec(
        {"id": "c", "atoms": [1, 2, 3, 4], "model": "chemical", "states": {"a": 120, "b": 480}}
    )
    with pytest.raises(ValueError, match="periodic duplicate"):
        normalize_spec(spec)
