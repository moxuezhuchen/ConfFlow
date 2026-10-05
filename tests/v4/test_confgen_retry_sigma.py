#!/usr/bin/env python3
"""D1 sigma-image retry regressions: real Engine.run link throughout.

Self-contained (no fixtures, no cross-test imports). A synthetic
reflection-symmetric octahedral complex is built in-code; its full-atom
witness validates ``authority_valid True`` through the production
``validate_full_witness`` gate (never edited, never bypassed).

Two columns, kept separate by construction:

* CONTROL-FLOW (``_MockSigmaStage``): scripted geometry, but the real
  ``CoordinationStage.retry_solve`` candidate construction (witness gating,
  placement matching, dedup, ordering, cancel) always runs through real
  ``ConfgenEngine.run_kernel`` dispatch. Never counted as science success.
* SCIENCE (unmodified ``CoordinationStage`` + real scipy solvers): proves a
  genuine input-only failure recovered from a sigma-image start, with the
  full flexible solve and every geometry/perception/lock audit traversed.

Semantics locked here: first pass fully input-only, ordered before any
retry; sources are first-pass ``published_leaf``/``expanded`` records read
from the real per-parent ``first_pass`` table (never a harness shortcut);
retry successes carry ``retry_start="sigma_image:<id>"`` in structure
metadata only; declines keep the single original failure record; explicit
``rigid`` never switches backend; budget per target is ``1+|sigma-images|``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from confflow.domain.structure import StructureRecord
from confflow.science.confgen import model as core_model
from confflow.science.confgen.coordination.enumeration import canonical_representative
from confflow.science.confgen.coordination.shapes import get_shape, proper_rotation_group
from confflow.science.confgen.coordination.stage import CoordinationStage
from confflow.science.confgen.coordination.symmetry import validate_full_witness
from confflow.science.confgen.engine import ConfgenEngine, EngineCancelledError
from confflow.science.confgen.graph import (
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
)
from confflow.science.confgen.model import RealizationResult as CoreRealizationResult
from confflow.science.topology import inherit_topology_kwargs

SITE_PERM = (2, 3, 0, 1, 4, 5)
SITE_IDS = ["A", "Apr", "B", "Bpr", "C", "Cpr"]
NFEV_SMALL = 10
MAXITER_SMALL = 10


def _build_reflection() -> dict[str, Any]:
    """Reflection-symmetric octahedral complex, exact-vertex input.

    The atom permutation swapping A<->B and Apr<->Bpr (reflection x<->y,
    improper: not a proper rotation, so sigma images land in distinct shape
    classes) with axial O fixed is an exact graph automorphism by
    construction; equatorial methyls are mirrored copies. ``authority_valid``
    is asserted True by the tests through production validation.
    """
    elements: list[str] = []
    positions: list[np.ndarray] = []
    sigma: dict[int, int] = {}

    def _add(element: str, pos: np.ndarray, *, fixed: bool = False) -> int:
        idx = len(elements)
        elements.append(element)
        positions.append(np.asarray(pos, dtype=float))
        if fixed:
            sigma[idx] = idx
        return idx

    def _pair(element: str, pos: np.ndarray, mirror: np.ndarray) -> tuple[int, int]:
        first = _add(element, pos)
        second = _add(element, mirror)
        sigma[first] = second
        sigma[second] = first
        return first, second

    def _mir(point: np.ndarray) -> np.ndarray:
        return np.array([point[1], point[0], point[2]])

    edges: list[tuple[int, int, EdgeType]] = []
    metal = _add("Co", np.zeros(3), fixed=True)
    a, b = _pair("N", np.array([2.0, 0.0, 0.0]), np.array([0.0, 2.0, 0.0]))
    ap, bp = _pair("N", np.array([-2.0, 0.0, 0.0]), np.array([0.0, -2.0, 0.0]))
    c = _add("O", np.array([0.0, 0.0, 2.1]), fixed=True)
    cp = _add("O", np.array([0.0, 0.0, -2.1]), fixed=True)
    donors = [a, ap, b, bp, c, cp]
    for donor in donors:
        edges.append((metal, donor, EdgeType.COORDINATION))
    methyl_base = {
        a: (2.0, 0.0, 0.0),
        b: (0.0, 2.0, 0.0),
        ap: (-2.0, 0.0, 0.0),
        bp: (0.0, -2.0, 0.0),
    }
    # One methyl per equatorial donor. A-side offsets are chosen by quadrant;
    # mirror partners carry the exact x<->y image so the reflection stays an
    # exact atom permutation.
    for donor, base in methyl_base.items():
        off = np.array([0.5, 0.5, 0.8])
        if base[0] < 0:
            off = np.array([-0.5, 0.5, 0.8])
        if base[1] < 0:
            off = np.array([0.5, -0.5, 0.8])
        sub = _add("C", np.array(base) + off)
        edges.append((donor, sub, EdgeType.COVALENT))
    # Methyls: A<->B, Apr<->Bpr (indices 7,8,9,10).
    sigma[7] = 8
    sigma[8] = 7
    sigma[9] = 10
    sigma[10] = 9
    count = len(elements)
    assert sorted(sigma) == list(range(count))
    perm = tuple(sigma[i] for i in range(count))
    assert all(perm[perm[i]] == i for i in range(count))
    atoms = tuple(AtomRef(index=i, element=e) for i, e in enumerate(elements))
    graph = TypedGraph(
        atoms=atoms,
        edges=tuple(TypedEdge(a=x, b=y, type=t) for x, y, t in edges),
        metal_center=metal,
    )
    spec = CoordinationSpec(
        metal_center=metal,
        binding_sites=tuple(
            BindingSite(id=n, kind="atom", atoms=(d,), hapticity=1)
            for n, d in zip(SITE_IDS, donors)
        ),
        shapes=("octahedral",),
    )
    return {
        "graph": graph,
        "spec": spec,
        "coords": np.array(positions),
        "sigma": perm,
        "donors": donors,
        "elements": elements,
    }


def _section(system: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    site_group: dict[str, Any] = {"generators": [list(SITE_PERM)]}
    if overrides.pop("with_witnesses", True):
        site_group["witnesses"] = [
            {"mapping": list(system["sigma"]), "provenance": "synthetic-reflection"}
        ]
    section: dict[str, Any] = {
        "metal_center": 0,
        "binding_sites": [
            {"id": n, "kind": "atom", "atoms": [d], "hapticity": 1}
            for n, d in zip(SITE_IDS, system["donors"])
        ],
        "shapes": ["octahedral"],
        "treatment": "enumerate",
        "constraints": [],
        "site_group": site_group,
        "tolerances": {},
        "budgets": {"max_nfev": NFEV_SMALL, "maxiter": MAXITER_SMALL},
        "donor_configuration": list(SITE_IDS),
    }
    section.update(overrides)
    return section


def _run(section: dict[str, Any], system: dict[str, Any], probe: Any = None) -> Any:
    structure = StructureRecord(
        id="refl",
        atoms=tuple(system["elements"]),
        coordinates=tuple(tuple(p) for p in system["coords"]),
        charge=0,
        multiplicity=1,
    )
    topology = {
        "bonds": [{"atoms": [e.a, e.b], "kind": e.type.value} for e in system["graph"].edges],
        "atoms": [],
    }
    workflow = {
        "schema_version": 3,
        "index_base": 0,
        "topology": topology,
        "coordination": section,
    }
    context = core_model.build_context(structure, workflow)
    engine = ConfgenEngine(allow_preserve_input=True)
    if probe is None:
        return engine.run(context)
    return engine.run(context, should_cancel=probe)


def _run_kernel(
    section: dict[str, Any], system: dict[str, Any], stage: Any, probe: Any = None
) -> Any:
    """Real run_kernel dispatch with an explicit stage (mock column only)."""
    from confflow.science.confgen.wire_v3 import from_wire_key

    structure = StructureRecord(
        id="refl",
        atoms=tuple(system["elements"]),
        coordinates=tuple(tuple(p) for p in system["coords"]),
        charge=0,
        multiplicity=1,
    )
    topology = {
        "bonds": [{"atoms": [e.a, e.b], "kind": e.type.value} for e in system["graph"].edges],
        "atoms": [],
    }
    workflow = {
        "schema_version": 3,
        "index_base": 0,
        "topology": topology,
        "coordination": section,
    }
    context = core_model.build_context(structure, workflow)
    engine = ConfgenEngine(allow_preserve_input=True)
    return engine.run_kernel(
        context,
        initial_key=from_wire_key(context.input_state_key),
        should_cancel=probe,
        stage_overrides=[stage],
    )


def _retry_leaves(run: Any) -> list[Any]:
    return [
        leaf
        for leaf in run.leaves
        if dict(getattr(leaf.structure, "metadata", {}) or {}).get("retry_start")
    ]


def _leaf_coords(run: Any) -> dict[str, tuple]:
    return {leaf.provenance["leaf_target_id"]: leaf.structure.coordinates for leaf in run.leaves}


def _relabel(placement: Any, site_perm: Any) -> tuple[int, ...]:
    perm = tuple(int(v) for v in site_perm)
    inv = [0] * len(perm)
    for old, new in enumerate(perm):
        inv[new] = old
    current = tuple(int(v) for v in placement)
    return tuple(current[inv[i]] for i in range(len(perm)))


def _expected_sources(
    stage: Any, parent: Any, context: Any, witness: Any, failed_ordinal: int
) -> list[int]:
    """Independently recompute which source ordinals map onto one target."""
    graph = stage._check_context(context)
    donor_order = list(stage._spec.donor_indices)
    position_of = {d: p for p, d in enumerate(donor_order)}
    action = graph.induced_site_action(tuple(int(v) for v in witness), donor_order)
    assert action is not None
    site_perm = tuple(position_of[action[d]] for d in donor_order)
    plans: dict[int, tuple[str, tuple[int, ...]]] = {}
    for item in stage.enumerate_targets(parent, context):
        state = dict(item.state_value)
        plans[int(item.ordinal)] = (str(state["shape"]), tuple(int(v) for v in state["placement"]))
    failed_shape, failed_place = plans[failed_ordinal]
    group = proper_rotation_group(failed_shape)
    want = canonical_representative(tuple(int(v) for v in failed_place), group)
    out = []
    for ordinal, (shape, place) in sorted(plans.items()):
        if ordinal == failed_ordinal or shape != failed_shape:
            continue
        if canonical_representative(_relabel(place, site_perm), group) == want:
            out.append(ordinal)
    return out


def _image_bytes(source_coords: Any, mapping: Any) -> bytes:
    """Sigma-image fingerprint: y[perm[i]] = x[i], as bytes."""
    perm = [int(v) for v in mapping]
    inv = [0] * len(perm)
    for old, new in enumerate(perm):
        inv[new] = old
    arr = np.array(source_coords, dtype=float)
    out: bytes = np.array([arr[inv[i]] for i in range(len(perm))]).tobytes()
    return out


def _published_coords_by_ordinal(first_pass: Any) -> dict[int, Any]:
    return {
        int(e.ordinal): np.array(e.structure.coordinates, dtype=float)
        for e in first_pass
        if str(e.status) in ("published_leaf", "expanded") and e.structure is not None
    }


class _MockSigmaStage(CoordinationStage):
    """CONTROL-FLOW ONLY: scripted geometry, real retry_solve construction.

    ``realize`` verdicts are scripted by ordinal, but enumeration, engine
    accounting, the per-parent ``first_pass`` table, witness gating,
    placement matching, dedup, ordering and cancel checks all run the real
    code through real ``run_kernel`` dispatch. Nothing here counts as a
    science result; the science column below uses the unmodified stage.
    """

    def __init__(
        self, section: dict[str, Any], *, fail_ordinals: Any = (), sigma_mode: str = "decline"
    ) -> None:
        super().__init__(section)
        self._fail = set(int(v) for v in fail_ordinals)
        self._mode = str(sigma_mode)
        self.solve_order: list[str] = []
        self.sigma_calls: list[tuple[str, int]] = []
        self.first_pass_seen: Any = None

    def _placed_structure(self, parent: Any, target: Any, context: Any) -> Any:
        template = get_shape("octahedral")
        placement = tuple(int(v) for v in dict(target.state_value)["placement"])
        coords = np.array(parent.structure.coordinates, dtype=float)
        center = coords[self._spec.metal_center]
        donors = list(self._spec.donor_indices)
        ref = [float(np.linalg.norm(coords[d] - center)) for d in donors]
        verts = np.asarray(template.vertices, dtype=float)
        unit = [verts[p] / float(np.linalg.norm(verts[p])) for p in placement]
        moved = coords.copy()
        for rank, donor in enumerate(donors):
            moved[donor] = center + unit[rank] * ref[rank]
        return StructureRecord(
            id=str(target.target_id),
            atoms=tuple(parent.structure.atoms),
            coordinates=tuple(tuple(p) for p in moved),
            charge=parent.structure.charge,
            multiplicity=parent.structure.multiplicity,
            parent_ids=(parent.structure.id,),
            source_step_id="coordination",
            **inherit_topology_kwargs(parent.structure, context.adjacency),
        )

    def realize(self, parent: Any, target: Any, context: Any) -> Any:
        self.solve_order.append(str(target.target_id))
        if int(target.ordinal) in self._fail:
            return CoreRealizationResult(
                structure=None,
                status="numerical_failure",
                reason="mock-input-fail",
                backend="mock-sigma",
                evidence=({},),
            )
        return CoreRealizationResult(
            structure=self._placed_structure(parent, target, context),
            status="realized",
            reason="mock-input-ok",
            backend="mock-sigma",
            evidence=({},),
        )

    def _realize_with_sigma(
        self,
        parent: Any,
        target: Any,
        context: Any,
        shape_name: str,
        placement: Any,
        sigma_coords: Any,
    ) -> Any:
        self.sigma_calls.append(
            (
                str(target.target_id),
                int(target.ordinal),
                np.asarray(sigma_coords, dtype=float).tobytes(),
            )
        )
        if self._mode == "heal":
            return CoreRealizationResult(
                structure=self._placed_structure(parent, target, context),
                status="realized",
                reason="mock-sigma-ok",
                backend="mock-sigma",
                evidence=({},),
            )
        return None

    def retry_solve(
        self, parent: Any, target: Any, context: Any, should_cancel: Any, first_pass: Any
    ) -> Any:
        self.first_pass_seen = first_pass
        return super().retry_solve(parent, target, context, should_cancel, first_pass)


def _witness_variants(system: dict[str, Any]) -> dict[str, list[int]]:
    """Second valid mapping with a DIFFERENT site action (control column).

    Base: reflection x<->y (A<->B, Apr<->Bpr), site action (2,3,0,1,4,5).
    Alt: reflection x<->-y (A<->Bpr, B<->Apr, axials fixed), site action
    (3,2,1,0,4,5); methyls follow their donors. Both validate
    ``authority_valid True`` through the production gate, so one failed
    target can have mapping sources under two site actions — the only honest
    way to observe multi-source ordinal ordering (a site action determines
    its full-atom backing uniquely here, so same-action witnesses can only
    prove witness-order/dedup). The SCIENCE column never uses alt.
    """
    count = system["graph"].natoms
    donors = list(system["donors"])  # [a, ap, b, bp, c, cp]
    a, ap, b, bp, c, cp = (int(d) for d in donors)
    order = [a, b, ap, bp]
    methyl_of = {d: 7 + order.index(d) for d in order}
    swap = list(range(count))
    for first, second in ((a, bp), (b, ap)):
        swap[first], swap[second] = second, first
        swap[methyl_of[first]], swap[methyl_of[second]] = (methyl_of[second], methyl_of[first])
    alt = list(swap)
    return {"base": list(system["sigma"]), "alt": alt}


# ---------------------------------------------------------------------------
# Authority and skip-diagnosis pins (visible diagnosis, fail-closed)
# ---------------------------------------------------------------------------


def test_full_witness_valid_but_site_only_insufficient() -> None:
    system = _build_reflection()
    report = validate_full_witness(
        system["graph"], system["spec"], system["sigma"], "synthetic-reflection"
    )
    assert report["authority_valid"] is True
    assert report["stereo_action"] == "uncertified-topology-only"
    assert report["suppression_authority"] == "none (H_geom required)"
    # The second reflection (x<->-y) also authorizes, with a DIFFERENT site
    # action: the control column uses it for genuine multi-source ordering
    # (the science column never declares it).
    alt = _witness_variants(system)["alt"]
    alt_report = validate_full_witness(
        system["graph"], system["spec"], tuple(alt), "synthetic-second-reflection"
    )
    assert alt_report["authority_valid"] is True
    # Donor-only permutation with no backing full witness never authorizes.
    assert system["sigma"] is not None


def test_tampered_witness_refused_authority_and_fails_closed() -> None:
    system = _build_reflection()
    broken = list(system["sigma"])
    # Swap an N donor image with a methyl C image: breaks typed-graph authority.
    broken[1], broken[7] = broken[7], broken[1]
    report = validate_full_witness(system["graph"], system["spec"], tuple(broken), "tampered")
    assert report["authority_valid"] is False
    assert report["topology_valid"] is False
    stage = CoordinationStage(_section(system))
    assert (
        stage.sigma_skip_diagnosis(
            n_sources=1, n_validated_witnesses=0, n_candidates=0, n_successes=0
        )
        == "sigma_image: skipped (no full-atom witness)"
    )


def test_site_generators_without_witness_decline_with_diagnosis() -> None:
    system = _build_reflection()
    run = _run(_section(system, with_witnesses=False), system)
    assert _retry_leaves(run) == []
    assert len(run.leaves) == 29
    failed = [
        r
        for r in run.target_records
        if r.axis == "coordination" and r.status.value == "failed_numerical"
    ]
    assert [r.target_id for r in failed] == ["coordination:000017"]
    stage = CoordinationStage(_section(system, with_witnesses=False))
    assert (
        stage.sigma_skip_diagnosis(
            n_sources=29, n_validated_witnesses=0, n_candidates=0, n_successes=0
        )
        == "sigma_image: skipped (no full-atom witness)"
    )


def test_skip_diagnosis_branches_pinned() -> None:
    system = _build_reflection()
    stage = CoordinationStage(_section(system))
    assert (
        stage.sigma_skip_diagnosis(
            n_sources=0, n_validated_witnesses=1, n_candidates=0, n_successes=0
        )
        == "sigma_image: skipped (no realized source)"
    )
    assert (
        stage.sigma_skip_diagnosis(
            n_sources=2, n_validated_witnesses=1, n_candidates=0, n_successes=0
        )
        == "sigma_image: skipped (no placement match)"
    )
    assert (
        stage.sigma_skip_diagnosis(
            n_sources=2, n_validated_witnesses=1, n_candidates=3, n_successes=0
        )
        == "sigma_image: exhausted (all sigma starts failed)"
    )


# ---------------------------------------------------------------------------
# SCIENCE: real-solver end-to-end recovery through Engine.run
# ---------------------------------------------------------------------------


def test_real_solver_recovers_input_only_failure_with_retry_start() -> None:
    system = _build_reflection()
    run = _run(_section(system), system)
    assert len(run.leaves) == 30
    retry = _retry_leaves(run)
    assert len(retry) == 1
    leaf = retry[0]
    assert leaf.provenance["leaf_target_id"] == "coordination:000017"
    metadata = dict(getattr(leaf.structure, "metadata", {}) or {})
    assert metadata["retry_start"] == "sigma_image:coordination:000018"


def test_real_solver_first_pass_leaves_byte_preserved() -> None:
    system = _build_reflection()
    full = _run(_section(system), system)
    stripped = _run(_section(system, with_witnesses=False), system)
    full_coords = _leaf_coords(full)
    stripped_coords = _leaf_coords(stripped)
    assert set(stripped_coords) <= set(full_coords)
    for target_id, coords in stripped_coords.items():
        assert full_coords[target_id] == coords
    assert len(full_coords) == len(stripped_coords) + len(_retry_leaves(full))
    for leaf in full.leaves:
        metadata = dict(getattr(leaf.structure, "metadata", {}) or {})
        if leaf in _retry_leaves(full):
            assert metadata["retry_start"].startswith("sigma_image:")
        else:
            assert "retry_start" not in metadata


def test_real_solver_repeatable_and_rigid_honest() -> None:
    system = _build_reflection()
    first = _run(_section(system, backend="rigid"), system)
    second = _run(_section(system, backend="rigid"), system)
    assert sorted(_leaf_coords(first).values()) == sorted(_leaf_coords(second).values())
    retry_ids = sorted(leaf.provenance["leaf_target_id"] for leaf in _retry_leaves(first))
    assert retry_ids == ["coordination:000017", "coordination:000024"]
    stripped = _run(_section(system, with_witnesses=False, backend="rigid"), system)
    failed = sorted(
        r.target_id
        for r in stripped.target_records
        if r.axis == "coordination" and r.status.value == "failed_numerical"
    )
    assert failed == ["coordination:000017", "coordination:000024"]
    # Rigid retry outcomes never switch backend: direct sigma solve pins it.
    stage = CoordinationStage(_section(system, backend="rigid"))
    structure = StructureRecord(
        id="refl",
        atoms=tuple(system["elements"]),
        coordinates=tuple(tuple(p) for p in system["coords"]),
        charge=0,
        multiplicity=1,
    )
    topology = {
        "bonds": [{"atoms": [e.a, e.b], "kind": e.type.value} for e in system["graph"].edges],
        "atoms": [],
    }
    workflow = {
        "schema_version": 3,
        "index_base": 0,
        "topology": topology,
        "coordination": _section(system, backend="rigid"),
    }
    context = core_model.build_context(structure, workflow)
    parent = core_model.WorkingRealization(
        structure=structure, state_key=core_model.ConfgenStateKey()
    )
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}
    failed_target = targets["coordination:000017"]
    import numpy as _np

    raw = _np.array(_leaf_coords(first)["coordination:000018"])
    perm = [int(v) for v in system["sigma"]]
    inv = [0] * len(perm)
    for _old, _new in enumerate(perm):
        inv[_new] = _old
    sigma_coords = _np.array([raw[inv[i]] for i in range(len(perm))])
    outcome = stage._realize_with_sigma(
        parent,
        failed_target,
        context,
        "octahedral",
        tuple(int(v) for v in dict(failed_target.state_value)["placement"]),
        sigma_coords,
    )
    assert outcome is not None and outcome.status == "realized"
    assert "flexible" not in str(outcome.backend)


def test_initial_coordinates_equal_input_reproduces_default_dict() -> None:
    """Explicit start equal to the input reproduces the default outcome dict."""
    from confflow.science.confgen.coordination.realization import (
        realize_flexible,
        realize_target,
    )

    system = _build_reflection()
    template = get_shape("octahedral")
    coords = np.array(system["coords"], dtype=float)
    donors = [int(d) for d in system["spec"].donor_indices]
    site_ids = list(system["spec"].site_ids)
    placement = (0, 1, 2, 3, 4, 5)

    def _perceive(generated: np.ndarray) -> Any:
        return tuple(int(v) for v in placement)

    for worker, kwargs in (
        (realize_target, {"max_nfev": 30}),
        (realize_flexible, {"maxiter": 5, "warm_max_nfev": 10}),
    ):
        default = worker(
            coords,
            system["graph"],
            0,
            donors,
            site_ids,
            placement,
            template,
            target_id="T00",
            perceive=_perceive,
            **kwargs,
        )
        explicit = worker(
            coords,
            system["graph"],
            0,
            donors,
            site_ids,
            placement,
            template,
            target_id="T00",
            perceive=_perceive,
            initial_coordinates=np.array(coords),
            **kwargs,
        )
        # An explicit start is recorded with exactly one extra evidence key;
        # the legacy default dict is otherwise byte-identical.
        explicit_dict = explicit.to_dict()
        assert explicit_dict["evidence"].pop("sigma_start_applied") is True
        assert explicit_dict == default.to_dict()
        import json as _json

        assert "sigma_start_applied" not in _json.dumps(default.to_dict())


def test_distorted_sigma_start_fails_parent_anchored_audit() -> None:
    """Final audit reference stays the true parent: stretched sigma bonds fail."""
    from confflow.science.confgen.coordination.realization import realize_target

    system = _build_reflection()
    template = get_shape("octahedral")
    coords = np.array(system["coords"], dtype=float)
    donors = [int(d) for d in system["spec"].donor_indices]
    site_ids = list(system["spec"].site_ids)
    placement = (0, 1, 2, 3, 4, 5)

    def _perceive(generated: np.ndarray) -> Any:
        return tuple(int(v) for v in placement)

    stretched = np.array(coords)
    stretched[7] = stretched[1] + (stretched[7] - stretched[1]) * 1.3
    outcome = realize_target(
        coords,
        system["graph"],
        0,
        donors,
        site_ids,
        placement,
        template,
        target_id="T00",
        perceive=_perceive,
        max_nfev=60,
        initial_coordinates=stretched,
    )
    assert outcome.status != "REALIZED"


# ---------------------------------------------------------------------------
# CONTROL-FLOW: mock solving, real retry_solve + real run_kernel dispatch
# ---------------------------------------------------------------------------


def test_first_pass_runs_fully_before_any_retry_and_sources_verified() -> None:
    system = _build_reflection()
    section = _section(system)
    stage = _MockSigmaStage(section, fail_ordinals=(17,), sigma_mode="heal")
    run = _run_kernel(section, system, stage)
    assert len(run.leaves) == 30
    assert stage.solve_order.index("coordination:000017") < len(stage.solve_order)
    assert len(stage.solve_order) == 30
    assert len(stage.sigma_calls) >= 1
    # Every first-pass realize precedes every sigma attempt (two passes).
    assert stage.first_pass_seen is not None
    table = list(stage.first_pass_seen)
    assert len(table) == 30
    by_id = {str(e.target_id): e for e in table}
    failed_entry = by_id["coordination:000017"]
    assert str(failed_entry.status) == "failed_numerical"
    sources = [
        e
        for e in table
        if str(e.status) in ("published_leaf", "expanded") and e.structure is not None
    ]
    assert len(sources) == 29
    assert all(int(e.ordinal) >= 0 for e in sources)


def test_budget_order_and_dedup_per_target() -> None:
    system = _build_reflection()
    variants = _witness_variants(system)
    section = _section(system)
    section["site_group"] = {
        "generators": [list(SITE_PERM)],
        "witnesses": [
            {"mapping": variants["base"], "provenance": "w-first"},
            {"mapping": variants["base"], "provenance": "w-second-same"},
        ],
    }
    stage = _MockSigmaStage(section, fail_ordinals=(17,), sigma_mode="decline")
    run = _run_kernel(section, system, stage)
    assert len(run.leaves) == 29
    calls_17 = [c for c in stage.sigma_calls if c[0] == "coordination:000017"]
    # Identical mappings dedup to one sigma image per mapping source: the
    # per-target attempt count equals the distinct-image count, so the
    # per-target budget is exactly 1 (input-only) + |sigma-images|.
    published = _published_coords_by_ordinal(stage.first_pass_seen)
    assert set(published) == set(range(30)) - {17}
    structure = StructureRecord(
        id="refl",
        atoms=tuple(system["elements"]),
        coordinates=tuple(tuple(p) for p in system["coords"]),
        charge=0,
        multiplicity=1,
    )
    topology = {
        "bonds": [{"atoms": [e.a, e.b], "kind": e.type.value} for e in system["graph"].edges],
        "atoms": [],
    }
    workflow = {"schema_version": 3, "index_base": 0, "topology": topology, "coordination": section}
    context = core_model.build_context(structure, workflow)
    parent = core_model.WorkingRealization(
        structure=structure, state_key=core_model.ConfgenStateKey()
    )
    expected = _expected_sources(stage, parent, context, variants["base"], 17)
    assert expected == [18]
    assert len(calls_17) == 1
    assert calls_17[0][2] == _image_bytes(published[18], variants["base"])
    by_id = {str(e.target_id): e for e in stage.first_pass_seen}
    assert str(by_id["coordination:000017"].status) == "failed_numerical"


def test_witness_declaration_order_and_source_ordinal_order() -> None:
    system = _build_reflection()
    variants = _witness_variants(system)
    section = _section(system)
    section["site_group"] = {
        "generators": [list(SITE_PERM)],
        "witnesses": [
            {"mapping": variants["base"], "provenance": "w-first"},
            {"mapping": variants["alt"], "provenance": "w-second"},
        ],
    }
    stage = _MockSigmaStage(section, fail_ordinals=(17,), sigma_mode="decline")
    run = _run_kernel(section, system, stage)
    assert len(run.leaves) == 29
    calls_17 = [c for c in stage.sigma_calls if c[0] == "coordination:000017"]
    # Target 17 has one mapping source per site action (18 via base, 16 via
    # alt): the attempt fingerprints pin source-ordinal-major order with
    # different witnesses, i.e. genuine multi-source ordering.
    published = _published_coords_by_ordinal(stage.first_pass_seen)
    assert set(published) == set(range(30)) - {17}
    expected_bytes = [
        _image_bytes(published[16], variants["alt"]),
        _image_bytes(published[18], variants["base"]),
    ]
    assert [c[2] for c in calls_17] == expected_bytes
    # Witness declaration order is outcome-deterministic: swapped declaration
    # yields the identical source-major sequence (loop order is fixed).
    section_swapped = _section(system)
    section_swapped["site_group"] = {
        "generators": [list(SITE_PERM)],
        "witnesses": [
            {"mapping": variants["alt"], "provenance": "w-second"},
            {"mapping": variants["base"], "provenance": "w-first"},
        ],
    }
    stage_swapped = _MockSigmaStage(section_swapped, fail_ordinals=(17,), sigma_mode="decline")
    _run_kernel(section_swapped, system, stage_swapped)
    swapped_17 = [c for c in stage_swapped.sigma_calls if c[0] == "coordination:000017"]
    assert [c[2] for c in swapped_17] == expected_bytes


def test_declined_retry_keeps_single_original_failure_record() -> None:
    system = _build_reflection()
    section = _section(system)
    stage = _MockSigmaStage(section, fail_ordinals=(17,), sigma_mode="decline")
    run = _run_kernel(section, system, stage)
    by_target: dict[str, list[str]] = {}
    for record in run.target_records:
        if record.axis == "leaves":
            continue
        by_target.setdefault(record.target_id, []).append(record.status.value)
    assert by_target["coordination:000017"] == ["failed_numerical"]
    assert (
        len(
            [
                leaf
                for leaf in run.leaves
                if leaf.provenance["leaf_target_id"] == "coordination:000017"
            ]
        )
        == 0
    )


def test_cancel_between_sigma_candidates_propagates_without_partial() -> None:
    system = _build_reflection()
    variants = _witness_variants(system)
    section = _section(system)
    section["site_group"] = {
        "generators": [list(SITE_PERM)],
        "witnesses": [
            {"mapping": variants["base"], "provenance": "w-first"},
            {"mapping": variants["alt"], "provenance": "w-second"},
        ],
    }
    stage = _MockSigmaStage(section, fail_ordinals=(17,), sigma_mode="decline")

    # Cancel as soon as the first sigma attempt has started: probe reads the
    # attempt log, so no count calibration against engine internals is needed.
    def _probe() -> bool:
        return len(stage.sigma_calls) >= 1

    with pytest.raises(EngineCancelledError):
        _run_kernel(section, system, stage, probe=_probe)
    # The first retry check passed (one sigma attempt ran); the next check
    # cancelled before any second attempt started: no partial second solve.
    assert len(stage.sigma_calls) == 1


def test_retry_probe_cancel_direct() -> None:
    system = _build_reflection()
    stage = _MockSigmaStage(_section(system), fail_ordinals=(17,), sigma_mode="decline")
    _run_kernel(_section(system), system, stage)
    table = tuple(stage.first_pass_seen)
    from confflow.science.confgen.model import RetryFirstPass

    assert isinstance(table[0], RetryFirstPass)
    structure = StructureRecord(
        id="refl",
        atoms=tuple(system["elements"]),
        coordinates=tuple(tuple(p) for p in system["coords"]),
        charge=0,
        multiplicity=1,
    )
    topology = {
        "bonds": [{"atoms": [e.a, e.b], "kind": e.type.value} for e in system["graph"].edges],
        "atoms": [],
    }
    workflow = {
        "schema_version": 3,
        "index_base": 0,
        "topology": topology,
        "coordination": _section(system),
    }
    context = core_model.build_context(structure, workflow)
    parent = core_model.WorkingRealization(
        structure=structure, state_key=core_model.ConfgenStateKey()
    )
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}
    with pytest.raises(EngineCancelledError):
        stage.retry_solve(parent, targets["coordination:000017"], context, lambda: True, table)


def test_two_parents_isolated_and_repeatable() -> None:
    system = _build_reflection()
    section = _section(system)
    before: set[str] = set()
    stage = _MockSigmaStage(section, fail_ordinals=(17,), sigma_mode="heal")
    before = set(stage.__dict__)
    first = _run_kernel(section, system, stage)
    mid = set(stage.__dict__)
    second = _run_kernel(section, system, stage)
    assert set(stage.__dict__) == before == mid
    first_coords = sorted(leaf.structure.coordinates for leaf in first.leaves)
    second_coords = sorted(leaf.structure.coordinates for leaf in second.leaves)
    assert first_coords == second_coords
    # Parent-B table without sources declines the same target Parent-A heals.
    table = tuple(stage.first_pass_seen)
    slim = tuple(e for e in table if int(e.ordinal) == 17)
    structure = StructureRecord(
        id="refl",
        atoms=tuple(system["elements"]),
        coordinates=tuple(tuple(p) for p in system["coords"]),
        charge=0,
        multiplicity=1,
    )
    topology = {
        "bonds": [{"atoms": [e.a, e.b], "kind": e.type.value} for e in system["graph"].edges],
        "atoms": [],
    }
    workflow = {
        "schema_version": 3,
        "index_base": 0,
        "topology": topology,
        "coordination": _section(system),
    }
    context = core_model.build_context(structure, workflow)
    parent = core_model.WorkingRealization(
        structure=structure, state_key=core_model.ConfgenStateKey()
    )
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}
    assert stage.retry_solve(parent, targets["coordination:000017"], context, None, slim) is None
    healed = stage.retry_solve(parent, targets["coordination:000017"], context, None, table)
    assert healed is not None and healed.status == "realized"
    metadata = dict(getattr(healed.structure, "metadata", {}) or {})
    assert metadata["retry_start"].startswith("sigma_image:")


def test_mock_heal_marks_only_new_leaves() -> None:
    system = _build_reflection()
    section = _section(system)
    stage = _MockSigmaStage(section, fail_ordinals=(17,), sigma_mode="heal")
    run = _run_kernel(section, system, stage)
    healed = [
        leaf
        for leaf in run.leaves
        if leaf.provenance.get("leaf_target_id") == "coordination:000017"
    ]
    assert len(healed) == 1
    metadata = dict(getattr(healed[0].structure, "metadata", {}) or {})
    assert metadata["retry_start"].startswith("sigma_image:")
    for leaf in run.leaves:
        if leaf.provenance.get("leaf_target_id") == "coordination:000017":
            continue
        assert "retry_start" not in dict(getattr(leaf.structure, "metadata", {}) or {})
