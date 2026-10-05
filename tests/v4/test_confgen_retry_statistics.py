#!/usr/bin/env python3
"""D3 coordination start_statistics + skip wiring: real runtime reports.

Self-contained (no fixtures, no cross-test imports). The synthetic
reflection octahedral system is duplicated here so this file stands alone.
Every statistics/skip assertion reads the real engine report
``scope.component_statistics.coordination`` (never a pure-helper return).
Legacy ``retry_solve`` direct API stays plain outcome/``None``; only the
new phase path emits ``RetryResult`` (single solve, order/budget unchanged).
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
from confflow.science.confgen.kernel_records import RetryResult
from confflow.science.confgen.model import RealizationResult as CoreRealizationResult
from confflow.science.topology import inherit_topology_kwargs

SITE_PERM = (2, 3, 0, 1, 4, 5)
SITE_IDS = ["A", "Apr", "B", "Bpr", "C", "Cpr"]
NFEV_SMALL = 10
MAXITER_SMALL = 10


def _build_reflection() -> dict[str, Any]:
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
    perm = tuple(sigma[i] for i in range(count))
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


def _run(section: dict[str, Any], system: dict[str, Any]) -> Any:
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
    return engine.run(context)


def _stats(run: Any) -> dict[str, Any]:
    return dict(
        run.report_json()["scope"]["component_statistics"]["coordination"]["start_statistics"]
    )


def _skips(run: Any) -> list[dict[str, Any]]:
    return list(
        run.report_json()["scope"]["component_statistics"]["coordination"]["skip_diagnostics"]
    )


class _CountingStage(CoordinationStage):
    """Real stage counting actual realize entries (no retry override)."""

    def __init__(self, section: dict[str, Any]) -> None:
        super().__init__(section)
        self.realize_calls: list[str] = []

    def realize(self, parent: Any, target: Any, context: Any) -> Any:
        self.realize_calls.append(str(target.target_id))
        return super().realize(parent, target, context)


class _CaptureStage(CoordinationStage):
    """Real stage capturing the bound snapshot for binding checks."""

    def __init__(self, section: dict[str, Any]) -> None:
        super().__init__(section)
        self.snapshots: list[tuple[Any, ...]] = []

    def report_statistics(self, snapshot: Any) -> Any:
        self.snapshots.append(tuple(snapshot))
        return super().report_statistics(snapshot)


class _MockStatsStage(CoordinationStage):
    """Fast scripted geometry, real phase dispatch/report (control only)."""

    def __init__(
        self,
        section: dict[str, Any],
        *,
        fail_ordinals: Any = (),
        sibling_mode: str = "decline",
    ) -> None:
        super().__init__(section)
        self._fail = set(int(v) for v in fail_ordinals)
        self._sibling_mode = str(sibling_mode)
        self.sibling_calls: list[str] = []
        self.phase_calls: list[tuple[str, str]] = []

    def _placed(self, parent: Any, target: Any, context: Any) -> Any:
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
        if int(target.ordinal) in self._fail:
            return CoreRealizationResult(
                structure=None,
                status="numerical_failure",
                reason="mock-input-fail",
                backend="mock-stats",
                evidence=({},),
            )
        return CoreRealizationResult(
            structure=self._placed(parent, target, context),
            status="realized",
            reason="mock-input-ok",
            backend="mock-stats",
            evidence=({},),
        )

    def _realize_with_sibling(
        self,
        parent: Any,
        target: Any,
        context: Any,
        shape_name: str,
        placement: Any,
        sibling_coords: Any,
    ) -> Any:
        self.sibling_calls.append(str(target.target_id))
        if self._sibling_mode == "heal":
            return CoreRealizationResult(
                structure=self._placed(parent, target, context),
                status="realized",
                reason="mock-sibling-ok",
                backend="mock-stats",
                evidence=({},),
            )
        return None

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
        return super().retry_solve_phase(
            parent, target, context, should_cancel, first_pass, phase_id, phase_snapshot
        )


def _run_kernel(section: dict[str, Any], system: dict[str, Any], stage: Any) -> Any:
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
        stage_overrides=[stage],
    )


def test_input_counts_real_solver_entries() -> None:
    """Input attempts equal real realize calls (not first_pass length)."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
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
    stage = _CountingStage(section)
    engine = ConfgenEngine(stages=[stage], allow_preserve_input=True)
    run = engine.run(context)
    stats = _stats(run)
    assert stats["input"]["attempts"] == len(stage.realize_calls) == 30
    assert stats["input"]["solve_successes"] == 29
    assert stats["input"]["accepted"] == 29


def test_no_witness_skip_in_runtime_report() -> None:
    """Real no-witness decline lands in the runtime skip list."""
    system = _build_reflection()
    run = _run(_section(system, with_witnesses=False), system)
    stats = _stats(run)
    assert stats["input"] == {"attempts": 30, "solve_successes": 29, "accepted": 29}
    assert stats["sigma_image"] == {"attempts": 0, "solve_successes": 0, "accepted": 0}
    assert stats["sibling"]["attempts"] == 2
    assert stats["sibling"]["solve_successes"] == 1
    assert stats["sibling"]["accepted"] == 1
    skips = _skips(run)
    assert {
        "parent_target_id": None,
        "target_id": "coordination:000017",
        "phase": "sigma",
        "reason": "sigma_image: skipped (no full-atom witness)",
    } in skips
    stage = CoordinationStage(_section(system, with_witnesses=False))
    assert (
        stage.sigma_skip_diagnosis(
            n_sources=3, n_validated_witnesses=0, n_candidates=0, n_successes=0
        )
        == "sigma_image: skipped (no full-atom witness)"
    )


def test_sigma_success_names_candidate_with_index() -> None:
    """With-witness sigma heal carries one success with explicit index."""
    system = _build_reflection()
    run = _run(_section(system), system)
    stats = _stats(run)
    assert stats["sigma_image"] == {"attempts": 1, "solve_successes": 1, "accepted": 1}
    assert stats["sibling"] == {"attempts": 0, "solve_successes": 0, "accepted": 0}
    assert _skips(run) == []
    retry = [
        leaf
        for leaf in run.leaves
        if dict(getattr(leaf.structure, "metadata", {}) or {}).get("retry_start")
    ]
    assert len(retry) == 1
    assert retry[0].provenance["leaf_target_id"] == "coordination:000017"
    assert (
        dict(getattr(retry[0].structure, "metadata", {}) or {})["retry_start"]
        == "sigma_image:coordination:000018"
    )


def test_failure_candidate_kept_and_diag_never_accepted() -> None:
    """Failed starts count; zero-attempt rows stay unaccepted."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    stage = _CaptureStage(section)
    _run_kernel(section, system, stage)
    snap = stage.snapshots[-1]
    sibling_rows = [e for e in snap if str(e.phase) == "sibling"]
    assert len(sibling_rows) == 2
    cand = [e for e in sibling_rows if int(e.attempts) == 1]
    diag = [e for e in sibling_rows if int(e.attempts) == 0]
    assert len(cand) == 2 and len(diag) == 0
    assert sorted(int(e.solve_successes) for e in cand) == [0, 1]
    accepted = [e for e in cand if bool(e.accepted)]
    assert len(accepted) == 1 and int(accepted[0].solve_successes) == 1
    assert all(not bool(e.accepted) for e in snap if int(e.attempts) == 0)
    sigma_diag = [e for e in snap if str(e.phase) == "sigma" and int(e.attempts) == 0]
    assert len(sigma_diag) == 1 and not bool(sigma_diag[0].accepted)


def test_exhausted_sibling_in_runtime_report() -> None:
    """All-fail sibling run reports exhausted with attempts kept."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    stage = _MockStatsStage(section, fail_ordinals=(17,), sibling_mode="decline")
    run = _run_kernel(section, system, stage)
    stats = dict(
        run.report_json()["scope"]["component_statistics"]["coordination"]["start_statistics"]
    )
    assert stats["sibling"]["attempts"] >= 1
    assert stats["sibling"]["solve_successes"] == 0
    assert stats["sibling"]["accepted"] == 0
    skips = [
        item
        for item in run.report_json()["scope"]["component_statistics"]["coordination"][
            "skip_diagnostics"
        ]
        if item["phase"] == "sibling"
    ]
    assert any(item["reason"] == "sibling: exhausted (all sibling starts failed)" for item in skips)


def test_cancel_between_candidates_propagates() -> None:
    """Probe fire mid-retry cancels with no partial wrapper."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    stage = _MockStatsStage(section, fail_ordinals=(17,), sibling_mode="decline")

    def _probe() -> bool:
        return len(stage.sibling_calls) >= 1

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
    with pytest.raises(EngineCancelledError):
        engine.run_kernel(
            context,
            initial_key=from_wire_key(context.input_state_key),
            should_cancel=_probe,
            stage_overrides=[stage],
        )
    assert len(stage.sibling_calls) == 1


def test_two_parent_snapshots_isolated_and_repeatable() -> None:
    """Two sequential runs agree; parent/target binding is exact."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    first = _run(section, system)
    second = _run(section, system)
    assert (
        first.report_json()["scope"]["component_statistics"]
        == second.report_json()["scope"]["component_statistics"]
    )
    assert [leaf.structure.id for leaf in first.leaves] == [
        leaf.structure.id for leaf in second.leaves
    ]
    stage = CoordinationStage(section)
    assert not hasattr(stage, "first_pass_seen")
    assert stage._has_legacy_sigma_override() is False


def test_sigma_before_sibling_phase_order() -> None:
    """Every sigma phase call precedes every sibling phase call."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    stage = _MockStatsStage(section, fail_ordinals=(17,), sibling_mode="heal")
    run = _run_kernel(section, system, stage)
    phases = [phase for phase, _ in stage.phase_calls]
    assert "sigma" in phases and "sibling" in phases
    first_sibling = phases.index("sibling")
    assert all(p == "sigma" for p in phases[:first_sibling])
    assert all(p == "sibling" for p in phases[first_sibling:])
    assert any(leaf.provenance["leaf_target_id"] == "coordination:000017" for leaf in run.leaves)


def test_two_candidate_success_selection_binding() -> None:
    """Second-of-two success is the accepted row (explicit index)."""
    system = _build_reflection()
    section = _section(system, with_witnesses=False)
    stage = _CaptureStage(section)
    run = _run_kernel(section, system, stage)
    snap = stage.snapshots[-1]
    rows = [e for e in snap if str(e.phase) == "sibling" and int(e.attempts) == 1]
    assert len(rows) == 2
    assert [int(e.solve_successes) for e in rows] == [0, 1]
    assert [bool(e.accepted) for e in rows] == [False, True]
    assert dict(getattr(rows[1], "diagnostic", {}) or {}).get("candidate_index") == 1
    assert (
        run.report_json()["scope"]["component_statistics"]["coordination"]["start_statistics"][
            "sibling"
        ]["accepted"]
        == 1
    )


def test_legacy_retry_solve_direct_api_plain() -> None:
    """Direct retry_solve stays plain; phase wraps once without re-solve."""
    system = _build_reflection()
    section = _section(system)
    stage = CoordinationStage(section)
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
    parent = core_model.WorkingRealization(
        structure=structure, state_key=core_model.ConfgenStateKey()
    )
    targets = {t.target_id: t for t in stage.enumerate_targets(parent, context)}
    target = targets["coordination:000000"]
    calls = {"n": 0}
    orig = stage._realize_with_sigma

    def _counted(parent: Any, target: Any, ctx: Any, *args: Any) -> Any:
        calls["n"] += 1
        return orig(parent, target, ctx, *args)

    stage._realize_with_sigma = _counted  # type: ignore[method-assign]
    try:
        table = (
            core_model.RetryFirstPass(
                target_id="coordination:000000",
                ordinal=0,
                status="published_leaf",
                reason="ok",
                solver_error=False,
                structure=None,
            ),
        )
        plain = stage.retry_solve(parent, target, context, None, table)
        assert not isinstance(plain, RetryResult)
        assert plain is None
        assert calls["n"] == 0
    finally:
        stage._realize_with_sigma = orig  # type: ignore[method-assign]
