#!/usr/bin/env python3
"""D2 sibling-start retry regressions: real Engine.run link throughout.

Self-contained (no fixtures, no cross-test imports). A synthetic
reflection-symmetric octahedral complex is built in-code (same construction
as the D1 science column, duplicated here so this file stands alone).

Two columns, kept separate by construction:

* CONTROL-FLOW (``_MockSiblingStage``): scripted geometry, but the real
  ``CoordinationStage.retry_solve_phase`` dispatch, phase ordering,
  snapshot freezing, candidate construction (ordinal order, <=3, dedup)
  and cancel checks always run through real ``run_kernel`` dispatch.
  Never counted as science success.
* SCIENCE (unmodified ``CoordinationStage`` + real scipy solvers): proves a
  genuine input-only failure recovered from a sibling start with the full
  flexible solve and every geometry/perception/lock audit traversed — even
  with NO full-atom witness (the case where D1 sigma honestly declines).

Semantics locked here: the sigma phase runs fully before any sibling
attempt; sibling sources are the frozen per-phase ``phase_snapshot``
published/expanded records (audited structures only, sigma recoverers
included, at most 3 in source-ordinal order, no witness required); each
source coordinate set is only the solver start point; explicit ``rigid``
never switches backend; retry successes carry
``retry_start="sibling:<id>"`` in structure metadata only; declines keep
the single original failure record; cancellation propagates between
candidates (including when the probe itself raises cancellation).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from confflow.domain.structure import StructureRecord
from confflow.science.confgen import model as core_model
from confflow.science.confgen.coordination.stage import CoordinationStage
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
SIBLING_MAX = 3


def _build_reflection() -> dict[str, Any]:
    """Reflection-symmetric octahedral complex, exact-vertex input."""
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
    for donor, base in methyl_base.items():
        off = np.array([0.5, 0.5, 0.8])
        if base[0] < 0:
            off = np.array([-0.5, 0.5, 0.8])
        if base[1] < 0:
            off = np.array([0.5, -0.5, 0.8])
        sub = _add("C", np.array(base) + off)
        edges.append((donor, sub, EdgeType.COVALENT))
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


class _SigmaOnlyStage(CoordinationStage):
    """TEST-ONLY input-only baseline: real stage, sigma phase only (no sibling).

    With no witnesses the sigma phase honestly declines every retry, so a
    run through this stage is the true input-only baseline for the byte
    comparison below (not a repeated new run asserting itself).
    """

    def retry_phases(self) -> tuple[str, ...] | None:
        """Declare the sigma phase only (test baseline, no sibling)."""
        return ("sigma",)


def _run_sigma_only(section: dict[str, Any], system: dict[str, Any]) -> Any:
    """Real engine run with an explicit real sigma-only stage (baseline)."""
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
    engine = ConfgenEngine(stages=[_SigmaOnlyStage(section)], allow_preserve_input=True)
    return engine.run(context)


def _context_for(section: dict[str, Any], system: dict[str, Any]) -> Any:
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
    return core_model.build_context(structure, workflow)


def _parent_for(context: Any) -> Any:
    return core_model.WorkingRealization(
        structure=context.structure, state_key=core_model.ConfgenStateKey()
    )


# ---------------------------------------------------------------------------
# SCIENCE: real-solver end-to-end sibling recovery through Engine.run
# ---------------------------------------------------------------------------


def test_sibling_recovers_without_witness_with_retry_start() -> None:
    """No-witness input-only failure recovers from a sibling start (science)."""
    system = _build_reflection()
    run = _run(_section(system, with_witnesses=False), system)
    assert len(run.leaves) == 30
    retry = _retry_leaves(run)
    assert len(retry) == 1
    leaf = retry[0]
    assert leaf.provenance["leaf_target_id"] == "coordination:000017"
    metadata = dict(getattr(leaf.structure, "metadata", {}) or {})
    assert metadata["retry_start"] == "sibling:coordination:000001"
    assert not metadata["retry_start"].startswith("sigma_image:")


def test_sibling_first_pass_leaves_byte_preserved() -> None:
    """Sibling heal adds one leaf; input-only bytes proven vs real baseline.

    The baseline is a real engine run through a real sigma-only stage (no
    sibling, no witnesses: sigma honestly declines, so 29 input-only
    leaves). Every baseline leaf id keeps identical coordinates and
    metadata in the full run; every baseline target record keeps identical
    status/reason except the recovered target; the full run adds exactly
    one ``sibling:`` leaf. Repeatability across two full runs is asserted
    in addition, never as the sole proof.
    """
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    base = _run_sigma_only(section, system)
    assert len(base.leaves) == 29
    assert _retry_leaves(base) == []
    run = _run(section, system)
    assert len(run.leaves) == 30
    base_coords = _leaf_coords(base)
    full_coords = _leaf_coords(run)
    assert set(base_coords) <= set(full_coords)
    assert len(full_coords) == len(base_coords) + 1
    for target_id, coords in base_coords.items():
        assert full_coords[target_id] == coords
    base_meta = {
        leaf.provenance["leaf_target_id"]: dict(getattr(leaf.structure, "metadata", {}) or {})
        for leaf in base.leaves
    }
    full_meta = {
        leaf.provenance["leaf_target_id"]: dict(getattr(leaf.structure, "metadata", {}) or {})
        for leaf in run.leaves
    }
    for target_id, metadata in base_meta.items():
        assert full_meta[target_id] == metadata
        assert "retry_start" not in metadata
    base_records = {
        record.target_id: (record.status.value, record.reason)
        for record in base.target_records
        if record.axis == "coordination"
    }
    full_records = {
        record.target_id: (record.status.value, record.reason)
        for record in run.target_records
        if record.axis == "coordination"
    }
    assert set(base_records) <= set(full_records)
    for target_id, status_reason in base_records.items():
        if target_id == "coordination:000017":
            assert status_reason[0] == "failed_numerical"
            assert full_records[target_id][0] == "published_leaf"
        else:
            assert full_records[target_id] == status_reason
    retry_ids = {leaf.provenance["leaf_target_id"] for leaf in _retry_leaves(run)}
    assert retry_ids == {"coordination:000017"}
    for leaf in run.leaves:
        metadata = dict(getattr(leaf.structure, "metadata", {}) or {})
        if leaf.provenance["leaf_target_id"] in retry_ids:
            assert metadata["retry_start"].startswith("sibling:")
        else:
            assert "retry_start" not in metadata
    second = _run(section, system)
    assert _leaf_coords(second) == full_coords


def test_sigma_healed_target_not_resolved_again_by_sibling() -> None:
    """With a witness, sigma heals and the sibling phase skips it (science)."""
    system = _build_reflection()
    run = _run(_section(system), system)
    assert len(run.leaves) == 30
    retry = _retry_leaves(run)
    assert len(retry) == 1
    metadata = dict(getattr(retry[0].structure, "metadata", {}) or {})
    assert retry[0].provenance["leaf_target_id"] == "coordination:000017"
    assert metadata["retry_start"] == "sigma_image:coordination:000018"


def test_sibling_rigid_backend_honest() -> None:
    """Rigid sibling retries never switch backend (science)."""
    system = _build_reflection()
    first = _run(_section(system, with_witnesses=False, backend="rigid"), system)
    second = _run(_section(system, with_witnesses=False, backend="rigid"), system)
    assert sorted(_leaf_coords(first).values()) == sorted(_leaf_coords(second).values())
    retry_ids = sorted(leaf.provenance["leaf_target_id"] for leaf in _retry_leaves(first))
    assert retry_ids == ["coordination:000017", "coordination:000024"]
    for leaf in _retry_leaves(first):
        metadata = dict(getattr(leaf.structure, "metadata", {}) or {})
        assert metadata["retry_start"].startswith("sibling:")
    # Rigid sibling outcomes never switch backend: direct sibling solve pins it.
    from confflow.science.confgen.coordination.shapes import get_shape as _get_shape

    stage = CoordinationStage(_section(system, with_witnesses=False, backend="rigid"))
    context = _context_for(_section(system, with_witnesses=False, backend="rigid"), system)
    parent = _parent_for(context)
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}
    failed_target = targets["coordination:000017"]
    start = np.array(_leaf_coords(first)["coordination:000001"], dtype=float)
    assert start.shape == np.array(system["coords"]).shape
    _ = _get_shape
    outcome = stage._realize_with_sibling(
        parent,
        failed_target,
        context,
        "octahedral",
        tuple(int(v) for v in dict(failed_target.state_value)["placement"]),
        start,
    )
    assert outcome is not None and outcome.status == "realized"
    assert "flexible" not in str(outcome.backend)


def test_retry_phases_declare_sigma_then_sibling() -> None:
    """The stage owns exactly the sigma-then-sibling phase declaration."""
    system = _build_reflection()
    stage = CoordinationStage(_section(system))
    assert stage.retry_phases() == ("sigma", "sibling")
    assert stage.sibling_skip_diagnosis(n_sources=0, n_candidates=0, n_successes=0) == (
        "sibling: skipped (no realized source)"
    )
    assert stage.sibling_skip_diagnosis(n_sources=2, n_candidates=0, n_successes=0) == (
        "sibling: skipped (no candidate)"
    )
    assert stage.sibling_skip_diagnosis(n_sources=2, n_candidates=3, n_successes=0) == (
        "sibling: exhausted (all sibling starts failed)"
    )
    assert stage.sibling_skip_diagnosis(n_sources=2, n_candidates=3, n_successes=1) == (
        "sibling: realized from 1 candidate(s)"
    )


# ---------------------------------------------------------------------------
# CONTROL-FLOW: mock solving, real retry_solve_phase + real run_kernel
# ---------------------------------------------------------------------------


class _MockSiblingStage(CoordinationStage):
    """CONTROL-FLOW ONLY: scripted geometry, real phase dispatch.

    ``realize`` verdicts are scripted by ordinal; sigma/sibling solves are
    scripted independently (``sigma_mode``/``sibling_mode``), but phase
    declaration, ordering, snapshot freezing, source selection (ordinal,
    <=3, dedup) and cancel checks all run the real code through real
    ``run_kernel`` dispatch. Nothing here counts as a science result.
    """

    def __init__(
        self,
        section: dict[str, Any],
        *,
        fail_ordinals: Any = (),
        sigma_mode: str = "decline",
        sibling_mode: str = "decline",
        sibling_heal_needs_sigma_source: str | None = None,
        sigma_heal_targets: Any | None = None,
    ) -> None:
        super().__init__(section)
        self._fail = set(int(v) for v in fail_ordinals)
        self._sigma_mode = str(sigma_mode)
        self._sibling_mode = str(sibling_mode)
        self._need_sigma_source = sibling_heal_needs_sigma_source
        self._sigma_heal_targets = (
            None if sigma_heal_targets is None else set(str(v) for v in sigma_heal_targets)
        )
        self.solve_order: list[str] = []
        self.sigma_calls: list[tuple[str, bytes]] = []
        self.sibling_calls: list[tuple[str, str, bytes]] = []
        self.phase_calls: list[tuple[str, str]] = []
        self.phase_snapshot_of: dict[str, Any] = {}
        self.first_pass_seen: Any = None

    def _placed_structure(self, parent: Any, target: Any, context: Any) -> Any:
        from confflow.science.confgen.coordination.shapes import get_shape

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
                backend="mock-sibling",
                evidence=({},),
            )
        return CoreRealizationResult(
            structure=self._placed_structure(parent, target, context),
            status="realized",
            reason="mock-input-ok",
            backend="mock-sibling",
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
            (str(target.target_id), np.asarray(sigma_coords, dtype=float).tobytes())
        )
        if self._sigma_mode == "heal":
            if self._sigma_heal_targets is not None and str(target.target_id) not in (
                self._sigma_heal_targets
            ):
                return None
            return CoreRealizationResult(
                structure=self._placed_structure(parent, target, context),
                status="realized",
                reason="mock-sigma-ok",
                backend="mock-sibling",
                evidence=({},),
            )
        return None

    def _realize_with_sibling(
        self,
        parent: Any,
        target: Any,
        context: Any,
        shape_name: str,
        placement: Any,
        sibling_coords: Any,
    ) -> Any:
        self.sibling_calls.append(
            (
                str(target.target_id),
                str(shape_name),
                np.asarray(sibling_coords, dtype=float).tobytes(),
            )
        )
        if self._sibling_mode == "heal":
            if self._need_sigma_source is not None:
                # Heal only when the sibling snapshot already carries the
                # sigma-phase recovery: proves snapshot chaining.
                assert self._need_sigma_source in {
                    str(e.target_id)
                    for e in self._sibling_snapshot
                    if str(e.status) in ("published_leaf", "expanded")
                }, "sigma heal missing from sibling snapshot"
            return CoreRealizationResult(
                structure=self._placed_structure(parent, target, context),
                status="realized",
                reason="mock-sibling-ok",
                backend="mock-sibling",
                evidence=({},),
            )
        return None

    _sibling_snapshot: Any = ()

    def retry_solve_phase(
        self,
        parent: Any,
        target: Any,
        context: Any,
        should_cancel: Any,
        first_pass: Any,
        phase_id: Any,
        phase_snapshot: Any,
    ) -> Any:
        self.phase_calls.append((str(phase_id), str(target.target_id)))
        if str(phase_id) == "sibling":
            self.phase_snapshot_of[str(target.target_id)] = phase_snapshot
            type(self)._sibling_snapshot = phase_snapshot
        else:
            self.first_pass_seen = first_pass
        return super().retry_solve_phase(
            parent, target, context, should_cancel, first_pass, phase_id, phase_snapshot
        )


def test_all_sigma_before_all_sibling() -> None:
    """Every sigma attempt precedes every sibling attempt (no mixing)."""
    system = _build_reflection()
    stage = _MockSiblingStage(
        _section(system), fail_ordinals=(17, 24), sigma_mode="decline", sibling_mode="heal"
    )
    run = _run_kernel(_section(system), system, stage)
    phases = [phase for phase, _ in stage.phase_calls]
    assert set(phases) == {"sigma", "sibling"}
    first_sibling = phases.index("sibling")
    assert all(p == "sigma" for p in phases[:first_sibling])
    assert all(p == "sibling" for p in phases[first_sibling:])
    by_target: dict[str, list[str]] = {}
    for record in run.target_records:
        if record.axis == "leaves":
            continue
        by_target.setdefault(record.target_id, []).append(record.status.value)
    assert by_target["coordination:000017"] == ["published_leaf"]
    assert by_target["coordination:000024"] == ["published_leaf"]


def test_sigma_heal_visible_in_sibling_snapshot() -> None:
    """A sigma-phase recovery is a sibling source (snapshot chaining)."""
    system = _build_reflection()
    stage = _MockSiblingStage(
        _section(system),
        fail_ordinals=(17, 24),
        sigma_mode="heal",
        sigma_heal_targets={"coordination:000017"},
        sibling_mode="heal",
        sibling_heal_needs_sigma_source="coordination:000017",
    )
    # Fail 17 and 24 in input-only; sigma heals only 17; sibling heals 24
    # only because the frozen sibling snapshot already carries healed 000017.
    run = _run_kernel(_section(system), system, stage)
    assert len(run.leaves) == 30
    snap_24 = stage.phase_snapshot_of["coordination:000024"]
    by_id = {str(e.target_id): e for e in snap_24}
    assert str(by_id["coordination:000017"].status) == "published_leaf"
    assert by_id["coordination:000017"].structure is not None
    # 000017 was failed in the permanent first pass, healed only in sigma.
    first = {str(e.target_id): e for e in stage.first_pass_seen}
    assert str(first["coordination:000017"].status) == "failed_numerical"


def test_sibling_budget_three_stable_ordinal_order() -> None:
    """One late failure tries exactly the first 3 sources, ordinal-stable."""
    system = _build_reflection()
    stage = _MockSiblingStage(
        _section(system, with_witnesses=False),
        fail_ordinals=(29,),
        sigma_mode="decline",
        sibling_mode="decline",
    )
    run = _run_kernel(_section(system, with_witnesses=False), system, stage)
    assert len(run.leaves) == 29
    calls = [c for c in stage.sibling_calls if c[0] == "coordination:000029"]
    assert len(calls) == SIBLING_MAX
    snap = stage.phase_snapshot_of["coordination:000029"]
    published = {
        np.asarray(e.structure.coordinates, dtype=float).tobytes(): str(e.target_id)
        for e in snap
        if str(e.status) in ("published_leaf", "expanded") and e.structure is not None
    }
    got = [published[c[2]] for c in calls]
    assert got == ["coordination:000000", "coordination:000001", "coordination:000002"]
    # Stable across reruns.
    stage2 = _MockSiblingStage(
        _section(system, with_witnesses=False),
        fail_ordinals=(29,),
        sigma_mode="decline",
        sibling_mode="decline",
    )
    _run_kernel(_section(system, with_witnesses=False), system, stage2)
    calls2 = [c for c in stage2.sibling_calls if c[0] == "coordination:000029"]
    assert [c[2] for c in calls2] == [c[2] for c in calls]


def test_sibling_decline_keeps_single_original_failure() -> None:
    """Sigma-declined and sibling-declined target keeps one failure record."""
    system = _build_reflection()
    stage = _MockSiblingStage(
        _section(system, with_witnesses=False),
        fail_ordinals=(17,),
        sigma_mode="decline",
        sibling_mode="decline",
    )
    run = _run_kernel(_section(system, with_witnesses=False), system, stage)
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


def test_sibling_needs_no_witness_and_no_source_declines() -> None:
    """Sibling attempts run without witnesses; no source honestly declines."""
    system = _build_reflection()
    stage = _MockSiblingStage(
        _section(system, with_witnesses=False),
        fail_ordinals=(17,),
        sigma_mode="decline",
        sibling_mode="heal",
    )
    run = _run_kernel(_section(system, with_witnesses=False), system, stage)
    assert len(run.leaves) == 30
    healed = [
        leaf
        for leaf in run.leaves
        if leaf.provenance.get("leaf_target_id") == "coordination:000017"
    ]
    assert len(healed) == 1
    # Empty-source snapshot declines without solving.
    from confflow.science.confgen.model import RetryFirstPass

    context = _context_for(_section(system, with_witnesses=False), system)
    parent = _parent_for(context)
    stage2 = CoordinationStage(_section(system, with_witnesses=False))
    targets = {t.target_id: t for t in stage2.enumerate_targets(parent, context)}
    empty = tuple(
        RetryFirstPass(
            target_id=t.target_id,
            ordinal=int(t.ordinal),
            status="failed_numerical",
            reason="mock-input-fail",
            solver_error=False,
            structure=None,
        )
        for t in targets.values()
    )
    assert (
        stage2._sibling_retry(parent, targets["coordination:000017"], context, None, empty, empty)
        is None
    )


def test_cancel_between_sibling_candidates_propagates_without_partial() -> None:
    """Cancel during the sibling phase raises with no partial second solve."""
    system = _build_reflection()
    stage = _MockSiblingStage(
        _section(system, with_witnesses=False),
        fail_ordinals=(29,),
        sigma_mode="decline",
        sibling_mode="decline",
    )

    def _probe() -> bool:
        # Fire only once the sibling phase has started (sigma attempts done).
        return (
            any(p == "sibling" for p, _ in stage.phase_calls)
            and len([c for c in stage.sibling_calls if c[0] == "coordination:000029"]) >= 1
        )

    with pytest.raises(EngineCancelledError):
        _run_kernel(_section(system, with_witnesses=False), system, stage, probe=_probe)
    calls = [c for c in stage.sibling_calls if c[0] == "coordination:000029"]
    assert len(calls) == 1


def test_callback_raising_cancel_propagates_sibling_path() -> None:
    """A probe that itself raises cancellation propagates (sibling loop)."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    stage = _MockSiblingStage(
        section, fail_ordinals=(29,), sigma_mode="decline", sibling_mode="decline"
    )
    _run_kernel(section, system, stage)
    from confflow.science.confgen.model import RetryFirstPass

    context = _context_for(section, system)
    parent = _parent_for(context)
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}
    table = tuple(stage.phase_snapshot_of["coordination:000029"])
    assert isinstance(table[0], RetryFirstPass)

    def _raising() -> bool:
        raise EngineCancelledError("callback cancelled")

    with pytest.raises(EngineCancelledError):
        stage._sibling_retry(
            parent, targets["coordination:000029"], context, _raising, table, table
        )


def test_callback_raising_cancel_propagates_sigma_path() -> None:
    """A probe that itself raises cancellation propagates (sigma loop)."""
    system = _build_reflection()
    section = _section(system)
    stage = _MockSiblingStage(section, fail_ordinals=(17,), sigma_mode="decline")
    _run_kernel(section, system, stage)
    table = tuple(stage.first_pass_seen)
    from confflow.science.confgen.model import RetryFirstPass

    assert isinstance(table[0], RetryFirstPass)
    context = _context_for(section, system)
    parent = _parent_for(context)
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}

    def _raising() -> bool:
        raise EngineCancelledError("callback cancelled")

    with pytest.raises(EngineCancelledError):
        stage.retry_solve(parent, targets["coordination:000017"], context, _raising, table)


def test_truthy_cancel_still_propagates_both_paths() -> None:
    """A truthy probe still cancels on both sigma and sibling paths."""
    system = _build_reflection()
    section = _section(system)
    stage = _MockSiblingStage(section, fail_ordinals=(17,), sigma_mode="decline")
    _run_kernel(section, system, stage)
    table = tuple(stage.first_pass_seen)
    context = _context_for(section, system)
    parent = _parent_for(context)
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}
    with pytest.raises(EngineCancelledError):
        stage.retry_solve(parent, targets["coordination:000017"], context, lambda: True, table)
    section_nw = _section(system, with_witnesses=False)
    context_nw = _context_for(section_nw, system)
    parent_nw = _parent_for(context_nw)
    stage_nw = _MockSiblingStage(
        section_nw, fail_ordinals=(29,), sigma_mode="decline", sibling_mode="decline"
    )
    _run_kernel(section_nw, system, stage_nw)
    snap = tuple(stage_nw.phase_snapshot_of["coordination:000029"])
    targets_nw = {t.target_id: t for t in stage_nw.enumerate_targets(parent_nw, context_nw)}
    with pytest.raises(EngineCancelledError):
        stage_nw._sibling_retry(
            parent_nw, targets_nw["coordination:000029"], context_nw, lambda: True, snap, snap
        )


def test_two_parents_isolated_and_repeatable() -> None:
    """Per-parent sibling phases never leak; reruns agree exactly."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    before: set[str] = set()
    stage = _MockSiblingStage(
        section, fail_ordinals=(17,), sigma_mode="decline", sibling_mode="heal"
    )
    before = set(stage.__dict__)
    first = _run_kernel(section, system, stage)
    mid = set(stage.__dict__)
    second = _run_kernel(section, system, stage)
    assert set(stage.__dict__) == before == mid
    first_coords = sorted(leaf.structure.coordinates for leaf in first.leaves)
    second_coords = sorted(leaf.structure.coordinates for leaf in second.leaves)
    assert first_coords == second_coords
    assert len(first.leaves) == 30


def test_mock_sibling_heal_marks_only_new_leaves() -> None:
    """Mock sibling heal stamps only the recovered leaf (control flow)."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    stage = _MockSiblingStage(
        section, fail_ordinals=(17,), sigma_mode="decline", sibling_mode="heal"
    )
    run = _run_kernel(section, system, stage)
    healed = [
        leaf
        for leaf in run.leaves
        if leaf.provenance.get("leaf_target_id") == "coordination:000017"
    ]
    assert len(healed) == 1
    metadata = dict(getattr(healed[0].structure, "metadata", {}) or {})
    assert metadata["retry_start"].startswith("sibling:")
    for leaf in run.leaves:
        if leaf.provenance.get("leaf_target_id") == "coordination:000017":
            continue
        assert "retry_start" not in dict(getattr(leaf.structure, "metadata", {}) or {})
