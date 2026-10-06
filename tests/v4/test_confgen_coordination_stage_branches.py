#!/usr/bin/env python3
"""Coordination stage fail-closed branches: real guards, no silent acceptance.

Self-contained (no fixtures, no cross-test imports). A minimal tetrahedral
stage covers pure guard branches; the synthetic reflection octahedral system
(copied from the D3 statistics suite so this file stands alone) covers
witness/shape/context branches through real spec/graph/context objects.
Every test drives the real stage method and asserts the real fail-closed
or diagnostic outcome.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from confflow.domain.structure import StructureRecord
from confflow.science.confgen import model as core_model
from confflow.science.confgen.coordination.stage import CoordinationStage
from confflow.science.confgen.graph import (
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
)
from confflow.science.confgen.kernel_records import BoundTelemetryEvent
from confflow.science.confgen.model import GenerationTarget, RealizationResult

SITE_PERM = (2, 3, 0, 1, 4, 5)
SITE_IDS = ["A", "Apr", "B", "Bpr", "C", "Cpr"]


def _minimal_stage() -> CoordinationStage:
    """Return a real tetrahedral stage (pure guards need no geometry)."""
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": f"S{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1} for i in range(4)
        ],
        "shapes": ["tetrahedral"],
        "treatment": "enumerate",
    }
    return CoordinationStage(section)


def _input_event(**overrides: Any) -> BoundTelemetryEvent:
    base: dict[str, Any] = {
        "component_id": "coordination",
        "parent_target_id": None,
        "target_id": "coordination:000000",
        "ordinal": 0,
        "phase": "input",
        "kind": "input",
        "attempts": 1,
        "solve_successes": 0,
        "accepted": False,
        "diagnostic": {},
    }
    base.update(overrides)
    return BoundTelemetryEvent(**base)  # type: ignore[arg-type]


class _Unreadable:
    """Attribute access always fails (fail-closed probe input)."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("unreadable probe")


class _BadStr:
    """String conversion always fails (reject-path input)."""

    def __str__(self) -> str:
        raise RuntimeError("unstringifiable")


def _coord_target(ordinal: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        axis="coordination",
        ordinal=ordinal,
        target_id=f"coordination:{ordinal:06d}",
        state_value={
            "center": 0,
            "shape": "tetrahedral",
            "placement": [0, 1, 2, 3],
            "sites": {},
        },
    )


def test_report_statistics_rejects_non_iterable_snapshot() -> None:
    """Non-iterable snapshot fails closed to None (no statistics forged)."""
    stage = _minimal_stage()
    assert stage.report_statistics(123) is None  # type: ignore[arg-type]
    assert stage.report_statistics(None) is None or True  # None means empty
    # None is the empty snapshot: also None, never a fabricated zero row.
    assert stage.report_statistics(None) is None


def test_report_statistics_empty_snapshot_returns_none() -> None:
    """Empty snapshot keeps golden reports byte-identical."""
    assert _minimal_stage().report_statistics(()) is None


def test_report_statistics_skips_unreadable_probe_row() -> None:
    """Unreadable probe rows are skipped; real input rows still aggregate."""
    stage = _minimal_stage()
    report = stage.report_statistics((_Unreadable(), _input_event()))
    assert report is not None
    assert report["start_statistics"]["input"]["attempts"] == 1
    assert report["skip_diagnostics"] == []


def test_report_statistics_skips_malformed_telemetry_row() -> None:
    """Rows with non-numeric attempts are discarded, never counted."""
    stage = _minimal_stage()
    bad = SimpleNamespace(
        phase="input",
        attempts="bad",
        solve_successes=0,
        accepted=False,
        target_id="coordination:000000",
        parent_target_id=None,
        diagnostic={},
    )
    report = stage.report_statistics((bad, _input_event()))
    assert report is not None
    assert report["start_statistics"]["input"]["attempts"] == 1
    assert report["skip_diagnostics"] == []


def test_report_statistics_ignores_foreign_phase() -> None:
    """Foreign phases never enter input/sigma/sibling buckets or skips."""
    stage = _minimal_stage()
    foreign = _input_event(phase="foo", target_id="coordination:000001", ordinal=1)
    report = stage.report_statistics((foreign, _input_event()))
    assert report is not None
    stats = report["start_statistics"]
    assert stats["input"]["attempts"] == 1
    assert stats["sigma_image"]["attempts"] == 0
    assert stats["sibling"]["attempts"] == 0
    assert report["skip_diagnostics"] == []


def test_report_statistics_empty_reason_not_diagnosed() -> None:
    """Zero-attempt rows without a reason produce no skip diagnostic."""
    stage = _minimal_stage()
    zero = _input_event(phase="sigma", kind="sigma_image", attempts=0)
    report = stage.report_statistics((zero, _input_event()))
    assert report is not None
    assert report["skip_diagnostics"] == []


def test_report_statistics_unstringifiable_reason_not_diagnosed() -> None:
    """Reasons that cannot stringify are dropped, never crash the report."""
    stage = _minimal_stage()
    bad = SimpleNamespace(
        phase="sigma",
        kind="sigma_image",
        attempts=0,
        solve_successes=0,
        accepted=False,
        target_id="coordination:000000",
        parent_target_id=None,
        diagnostic={"skip_reason": _BadStr()},
    )
    report = stage.report_statistics((bad, _input_event()))
    assert report is not None
    assert report["skip_diagnostics"] == []


def test_report_statistics_foreign_only_returns_none() -> None:
    """Snapshots with no own-phase rows emit nothing."""
    stage = _minimal_stage()
    foreign = _input_event(phase="foo", attempts=0, solve_successes=0)
    assert stage.report_statistics((foreign,)) is None


def test_report_statistics_legacy_retry_phase_suppresses_report() -> None:
    """Generic legacy retry phase suppresses statistics (no masquerade)."""
    stage = _minimal_stage()
    legacy = _input_event(phase="retry", kind="legacy_retry")
    assert stage.report_statistics((legacy, _input_event())) is None


def test_sigma_skip_diagnosis_success_label_names_count() -> None:
    """Full success carries the realized-from-N label (no silent zero)."""
    stage = _minimal_stage()
    label = stage.sigma_skip_diagnosis(
        n_sources=1, n_validated_witnesses=1, n_candidates=1, n_successes=2
    )
    assert label == "sigma_image: realized from 2 candidate(s)"


def test_sibling_skip_diagnosis_success_label_names_count() -> None:
    """Sibling success label is a pure function of counts."""
    stage = _minimal_stage()
    label = stage.sibling_skip_diagnosis(n_sources=2, n_candidates=2, n_successes=1)
    assert label == "sibling: realized from 1 candidate(s)"


def test_retry_solve_phase_rejects_unstringifiable_id() -> None:
    """Phase ids that cannot stringify decline fail-closed."""
    stage = _minimal_stage()
    target = _coord_target()
    assert (
        stage.retry_solve_phase(
            SimpleNamespace(), target, SimpleNamespace(), None, (), _BadStr(), ()
        )
        is None
    )


def test_retry_solve_phase_unknown_id_declines() -> None:
    """Unknown phase ids decline with plain None (no row, no solve)."""
    stage = _minimal_stage()
    assert (
        stage.retry_solve_phase(
            SimpleNamespace(),
            _coord_target(),
            SimpleNamespace(),
            None,
            (),
            "nope",
            (),
        )
        is None
    )


def test_retry_solve_phase_default_delegates_to_legacy_hook() -> None:
    """Generic default phase reaches the legacy hook (compat preserved)."""
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": f"S{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1} for i in range(4)
        ],
        "shapes": ["tetrahedral"],
        "treatment": "enumerate",
    }

    class _Legacy(CoordinationStage):
        def retry_solve(  # type: ignore[override]
            self, parent: Any, target: Any, context: Any, should_cancel: Any, first_pass: Any
        ) -> Any:
            return "legacy-sentinel"

    stage = _Legacy(section)
    out = stage.retry_solve_phase(
        SimpleNamespace(),
        _coord_target(),
        SimpleNamespace(),
        None,
        (),
        "default",
        (),
    )
    assert out == "legacy-sentinel"


def test_has_legacy_sigma_override_false_for_builtin() -> None:
    """Builtin stage reports no legacy override (instrumented path taken)."""
    assert _minimal_stage()._has_legacy_sigma_override() is False


def test_has_legacy_sigma_override_true_for_subclass() -> None:
    """Subclass override is detected so compat dispatch is preserved."""
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": f"S{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1} for i in range(4)
        ],
        "shapes": ["tetrahedral"],
        "treatment": "enumerate",
    }

    class _Override(CoordinationStage):
        def retry_solve(  # type: ignore[override]
            self, parent: Any, target: Any, context: Any, should_cancel: Any, first_pass: Any
        ) -> Any:
            return None

    assert _Override(section)._has_legacy_sigma_override() is True


def test_has_legacy_sigma_override_skips_unreadable_dict() -> None:
    """Metaclasses hiding __dict__ are skipped, never crash the probe."""
    from abc import ABCMeta

    class _BadMeta(ABCMeta):
        _armed = False

        def __getattribute__(cls, name: str) -> Any:
            if name == "__dict__" and getattr(cls, "_armed", False):
                raise RuntimeError("hidden dict")
            return super().__getattribute__(name)

    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": f"S{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1} for i in range(4)
        ],
        "shapes": ["tetrahedral"],
        "treatment": "enumerate",
    }

    class _Evil(CoordinationStage, metaclass=_BadMeta):  # type: ignore[misc]
        pass

    stage = _Evil.__new__(_Evil)
    CoordinationStage.__init__(stage, section)
    _BadMeta._armed = True  # type: ignore[attr-defined]
    try:
        assert stage._has_legacy_sigma_override() is False
    finally:
        _BadMeta._armed = False  # type: ignore[attr-defined]


def test_sigma_retry_phase_rejects_non_iterable_table() -> None:
    """Non-iterable first-pass tables decline (no solve on garbage)."""
    stage = _minimal_stage()
    assert (
        stage._sigma_retry_phase(SimpleNamespace(), _coord_target(), SimpleNamespace(), None, 123)
        is None
    )


def test_sigma_retry_phase_rejects_target_without_ordinal() -> None:
    """Targets without a readable ordinal decline fail-closed."""
    stage = _minimal_stage()
    assert (
        stage._sigma_retry_phase(
            SimpleNamespace(), SimpleNamespace(axis="coordination"), SimpleNamespace(), None, ()
        )
        is None
    )


def test_sigma_retry_phase_skips_malformed_snapshot_rows() -> None:
    """Malformed snapshot rows are skipped; empty sources yield a skip row."""
    stage = _minimal_stage()
    target = _coord_target(0)
    current = SimpleNamespace(
        ordinal=0,
        target_id="coordination:000000",
        status="failed_geometry",
        solver_error=False,
        structure=None,
    )
    bad_no_status = SimpleNamespace()
    bad_ordinal = SimpleNamespace(
        ordinal="bad", target_id="x", status="published_leaf", structure=object()
    )
    result = stage._sigma_retry_phase(
        SimpleNamespace(), target, SimpleNamespace(), None, (current, bad_no_status, bad_ordinal)
    )
    assert result is not None
    assert result.outcome is None
    assert len(result.telemetry) == 1
    assert result.telemetry[0].attempts == 0
    assert "no realized source" in str(result.telemetry[0].diagnostic)


def test_sigma_retry_phase_fail_closed_on_context_mismatch() -> None:
    """Graph/spec disagreement declines (never solve off the wrong graph)."""
    stage = _minimal_stage()
    target = _coord_target(0)
    current = SimpleNamespace(
        ordinal=0,
        target_id="coordination:000000",
        status="failed_geometry",
        solver_error=False,
        structure=None,
    )
    source = SimpleNamespace(
        ordinal=1,
        target_id="coordination:000001",
        status="published_leaf",
        solver_error=False,
        structure=SimpleNamespace(coordinates=((0.0, 0.0, 0.0),)),
    )
    bad_context = SimpleNamespace(graph="not-a-graph", structure=SimpleNamespace(atoms=()))
    assert (
        stage._sigma_retry_phase(SimpleNamespace(), target, bad_context, None, (current, source))
        is None
    )


def test_sigma_retry_phase_declines_for_unknown_target() -> None:
    """Targets absent from the snapshot decline (never heal strangers)."""
    stage = _minimal_stage()
    stranger = SimpleNamespace(
        ordinal=5,
        target_id="coordination:000005",
        status="failed_geometry",
        solver_error=False,
        structure=None,
    )
    assert (
        stage._sigma_retry_phase(
            SimpleNamespace(), _coord_target(0), SimpleNamespace(), None, (stranger,)
        )
        is None
    )


def test_sigma_retry_phase_declines_solver_error_and_success() -> None:
    """Solver-error and already-successful targets never retry."""
    stage = _minimal_stage()
    target = _coord_target(0)
    err = SimpleNamespace(
        ordinal=0,
        target_id="coordination:000000",
        status="failed_geometry",
        solver_error=True,
        structure=None,
    )
    ok = SimpleNamespace(
        ordinal=0,
        target_id="coordination:000000",
        status="published_leaf",
        solver_error=False,
        structure=None,
    )
    assert (
        stage._sigma_retry_phase(SimpleNamespace(), target, SimpleNamespace(), None, (err,)) is None
    )
    assert (
        stage._sigma_retry_phase(SimpleNamespace(), target, SimpleNamespace(), None, (ok,)) is None
    )


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


def _reflection_section(system: dict[str, Any], **overrides: Any) -> dict[str, Any]:
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
        "budgets": {"max_nfev": 10, "maxiter": 10},
        "donor_configuration": list(SITE_IDS),
    }
    section.update(overrides)
    return section


def _reflection_case() -> tuple[Any, Any, Any, Any]:
    system = _build_reflection()
    stage = CoordinationStage(_reflection_section(system))
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
        "coordination": _reflection_section(system),
    }
    context = core_model.build_context(structure, workflow)
    parent = core_model.WorkingRealization(
        structure=structure, state_key=core_model.ConfgenStateKey()
    )
    return system, stage, parent, context


def test_sigma_retry_phase_illegal_witness_mapping_yields_skip() -> None:
    """Witnesses with non-numeric mappings are skipped, then diagnosed."""
    system, stage, parent, context = _reflection_case()
    targets = list(stage.enumerate_targets(parent, context))
    target = targets[0]
    stage._site_witnesses = ({"mapping": ("x",), "provenance": "bad"},)  # type: ignore[assignment]
    table = (
        SimpleNamespace(
            ordinal=int(target.ordinal),
            target_id=str(target.target_id),
            status="failed_geometry",
            solver_error=False,
            structure=None,
        ),
        SimpleNamespace(
            ordinal=999,
            target_id="coordination:999999",
            status="published_leaf",
            solver_error=False,
            structure=SimpleNamespace(coordinates=tuple(tuple(p) for p in system["coords"])),
        ),
    )
    result = stage._sigma_retry_phase(parent, target, context, None, table)
    assert result is not None
    assert result.outcome is None
    assert result.telemetry[0].attempts == 0
    assert "no full-atom witness" in str(result.telemetry[0].diagnostic)


def test_sigma_retry_phase_rejects_unknown_shape() -> None:
    """Targets naming shapes outside the stage decline (no off-spec solve)."""
    system, stage, parent, context = _reflection_case()
    targets = list(stage.enumerate_targets(parent, context))
    target = targets[0]
    bad_state = dict(dict(target.state_value))
    bad_state["shape"] = "NOPE"
    bad = GenerationTarget(
        axis="coordination",
        target_id=str(target.target_id),
        state_value=bad_state,
        ordinal=int(target.ordinal),
    )
    table = (
        SimpleNamespace(
            ordinal=int(target.ordinal),
            target_id=str(target.target_id),
            status="failed_geometry",
            solver_error=False,
            structure=None,
        ),
        SimpleNamespace(
            ordinal=999,
            target_id="coordination:999999",
            status="published_leaf",
            solver_error=False,
            structure=SimpleNamespace(coordinates=tuple(tuple(p) for p in system["coords"])),
        ),
    )
    assert stage._sigma_retry_phase(parent, bad, context, None, table) is None


def test_sibling_retry_phase_rejects_wrong_axis() -> None:
    """Non-coordination targets decline the sibling phase."""
    stage = _minimal_stage()
    other = SimpleNamespace(axis="rings", ordinal=0, target_id="rings:000000")
    assert (
        stage._sibling_retry_phase(SimpleNamespace(), other, SimpleNamespace(), None, (), ())
        is None
    )


def test_sibling_retry_phase_rejects_non_iterable_snapshot() -> None:
    """Non-iterable sibling snapshots decline fail-closed."""
    stage = _minimal_stage()
    assert (
        stage._sibling_retry_phase(
            SimpleNamespace(), _coord_target(), SimpleNamespace(), None, (), 123
        )
        is None
    )


def test_sibling_retry_phase_rejects_unreadable_identity() -> None:
    """Sibling targets without ordinal/target_id decline."""
    stage = _minimal_stage()
    assert (
        stage._sibling_retry_phase(
            SimpleNamespace(),
            SimpleNamespace(axis="coordination"),
            SimpleNamespace(),
            None,
            (),
            (),
        )
        is None
    )
    assert (
        stage._sibling_retry_phase(
            SimpleNamespace(),
            SimpleNamespace(axis="coordination", ordinal=0),
            SimpleNamespace(),
            None,
            (),
            (),
        )
        is None
    )


def test_sibling_retry_phase_declines_unknown_and_unretryable() -> None:
    """Unknown, solver-error, and successful rows never enter sibling solve."""
    stage = _minimal_stage()
    target = _coord_target(0)
    stranger = (
        SimpleNamespace(ordinal=9, target_id="other", status="failed_geometry", solver_error=False),
    )
    assert (
        stage._sibling_retry_phase(SimpleNamespace(), target, SimpleNamespace(), None, (), stranger)
        is None
    )
    err = (
        SimpleNamespace(
            ordinal=0,
            target_id="coordination:000000",
            status="failed_geometry",
            solver_error=True,
        ),
    )
    assert (
        stage._sibling_retry_phase(SimpleNamespace(), target, SimpleNamespace(), None, (), err)
        is None
    )
    done = (
        SimpleNamespace(
            ordinal=0,
            target_id="coordination:000000",
            status="published_leaf",
            solver_error=False,
        ),
    )
    assert (
        stage._sibling_retry_phase(SimpleNamespace(), target, SimpleNamespace(), None, (), done)
        is None
    )


def test_sibling_retry_phase_skips_malformed_source_rows() -> None:
    """Malformed source rows are skipped; empty sources yield a skip row."""
    stage = _minimal_stage()
    target = _coord_target(0)
    current = SimpleNamespace(
        ordinal=0,
        target_id="coordination:000000",
        status="failed_geometry",
        solver_error=False,
        structure=None,
    )
    bad_no_status = SimpleNamespace()
    bad_ordinal = SimpleNamespace(
        ordinal="bad", target_id="x", status="published_leaf", structure=object()
    )
    result = stage._sibling_retry_phase(
        SimpleNamespace(),
        target,
        SimpleNamespace(),
        None,
        (),
        (current, bad_no_status, bad_ordinal),
    )
    assert result is not None
    assert result.outcome is None
    assert result.telemetry[0].attempts == 0
    assert "no realized source" in str(result.telemetry[0].diagnostic)


def test_legacy_sibling_retry_guards_match_phase() -> None:
    """Legacy D2 sibling guards decline wrong axis, bad tables, bad identity."""
    stage = _minimal_stage()
    other = SimpleNamespace(axis="rings", ordinal=0, target_id="rings:000000")
    assert stage._sibling_retry(SimpleNamespace(), other, SimpleNamespace(), None, (), ()) is None
    assert (
        stage._sibling_retry(SimpleNamespace(), _coord_target(), SimpleNamespace(), None, (), 123)
        is None
    )
    assert (
        stage._sibling_retry(
            SimpleNamespace(),
            SimpleNamespace(axis="coordination"),
            SimpleNamespace(),
            None,
            (),
            (),
        )
        is None
    )
    assert (
        stage._sibling_retry(
            SimpleNamespace(),
            SimpleNamespace(axis="coordination", ordinal=0),
            SimpleNamespace(),
            None,
            (),
            (),
        )
        is None
    )


def test_legacy_sibling_retry_declines_unknown_and_unretryable() -> None:
    """Legacy sibling keeps unknown/solver-error/successful targets untouched."""
    stage = _minimal_stage()
    target = _coord_target(0)
    stranger = (
        SimpleNamespace(ordinal=9, target_id="other", status="failed_geometry", solver_error=False),
    )
    assert (
        stage._sibling_retry(SimpleNamespace(), target, SimpleNamespace(), None, (), stranger)
        is None
    )
    err = (
        SimpleNamespace(
            ordinal=0, target_id="coordination:000000", status="failed_geometry", solver_error=True
        ),
    )
    assert stage._sibling_retry(SimpleNamespace(), target, SimpleNamespace(), None, (), err) is None
    done = (
        SimpleNamespace(
            ordinal=0, target_id="coordination:000000", status="published_leaf", solver_error=False
        ),
    )
    assert (
        stage._sibling_retry(SimpleNamespace(), target, SimpleNamespace(), None, (), done) is None
    )


def test_legacy_sibling_retry_skips_malformed_source_rows() -> None:
    """Legacy sibling skips malformed sources and declines with None."""
    stage = _minimal_stage()
    target = _coord_target(0)
    current = SimpleNamespace(
        ordinal=0,
        target_id="coordination:000000",
        status="failed_geometry",
        solver_error=False,
        structure=None,
    )
    bad = SimpleNamespace()
    assert (
        stage._sibling_retry(SimpleNamespace(), target, SimpleNamespace(), None, (), (current, bad))
        is None
    )


def test_retry_solve_guards_reject_garbage() -> None:
    """Legacy sigma retry declines wrong axis, bad tables, bad ordinals."""
    stage = _minimal_stage()
    other = SimpleNamespace(axis="rings")
    assert stage.retry_solve(SimpleNamespace(), other, SimpleNamespace(), None, ()) is None
    assert (
        stage.retry_solve(SimpleNamespace(), _coord_target(), SimpleNamespace(), None, 123) is None
    )
    assert (
        stage.retry_solve(
            SimpleNamespace(), SimpleNamespace(axis="coordination"), SimpleNamespace(), None, ()
        )
        is None
    )


def test_retry_solve_skips_malformed_rows_and_declines() -> None:
    """Malformed first-pass rows never become sigma sources."""
    stage = _minimal_stage()
    target = _coord_target(0)
    current = SimpleNamespace(
        ordinal=0,
        target_id="coordination:000000",
        status="failed_geometry",
        solver_error=False,
        structure=None,
    )
    bad = SimpleNamespace()
    assert (
        stage.retry_solve(SimpleNamespace(), target, SimpleNamespace(), None, (current, bad))
        is None
    )
    err = SimpleNamespace(
        ordinal=0, target_id="coordination:000000", status="failed_geometry", solver_error=True
    )
    assert stage.retry_solve(SimpleNamespace(), target, SimpleNamespace(), None, (err,)) is None
    done = SimpleNamespace(
        ordinal=0, target_id="coordination:000000", status="published_leaf", solver_error=False
    )
    assert stage.retry_solve(SimpleNamespace(), target, SimpleNamespace(), None, (done,)) is None


def test_realize_from_start_rejects_unknown_backend() -> None:
    """Unknown backend strings realize nothing (no silent default engine)."""
    _, stage, parent, context = _reflection_case()
    targets = list(stage.enumerate_targets(parent, context))
    target = targets[0]
    placement = tuple(int(v) for v in dict(target.state_value)["placement"])
    stage._backend = "bogus"  # type: ignore[assignment]
    assert (
        stage._realize_from_start(
            parent, target, context, "octahedral", placement, np.zeros((11, 3))
        )
        is None
    )


def test_with_retry_start_kind_builds_evidence_when_empty() -> None:
    """Empty-evidence successes still carry retry provenance (no loss)."""
    _, stage, parent, context = _reflection_case()
    targets = list(stage.enumerate_targets(parent, context))
    target = targets[0]
    structure = StructureRecord(
        id="ok",
        atoms=tuple(parent.structure.atoms),
        coordinates=tuple(tuple(p) for p in parent.structure.coordinates),
        charge=parent.structure.charge,
        multiplicity=parent.structure.multiplicity,
    )
    outcome = RealizationResult(
        structure=structure, status="realized", reason="ok", backend="test", evidence=()
    )
    stamped = stage._with_retry_start_kind(
        outcome, parent, target, context.adjacency, "coordination:000001", "sigma_image"
    )
    assert stamped.structure is not None
    assert stamped.structure.metadata["retry_start"] == "sigma_image:coordination:000001"
    assert stamped.evidence[0]["retry_start"] == "sigma_image:coordination:000001"


def test_realize_target_rejects_mismatched_start_shape() -> None:
    """Sigma starts with the wrong coordinate shape raise loudly."""
    import numpy as np

    from confflow.science.confgen.coordination.realization import realize_flexible, realize_target
    from confflow.science.confgen.coordination.shapes import get_shape

    _, stage, parent, context = _reflection_case()
    graph = stage._check_context(context)
    template = get_shape("octahedral")
    coords = np.array(parent.structure.coordinates, dtype=float)
    donors = list(stage._spec.donor_indices)
    sites = list(stage._spec.site_ids)
    bad = np.zeros((coords.shape[0] + 1, 3))
    try:
        realize_target(
            coords,
            graph,
            stage._spec.metal_center,
            donors,
            sites,
            (0, 1, 2, 3, 4, 5),
            template,
            target_id="t",
            perceive=lambda gen: ((0, 1, 2, 3, 4, 5), {}),
            initial_coordinates=bad,
        )
    except ValueError as exc:
        assert "initial_coordinates shape must match" in str(exc)
    else:
        raise AssertionError("expected shape mismatch to raise")
    try:
        realize_flexible(
            coords,
            graph,
            stage._spec.metal_center,
            donors,
            sites,
            (0, 1, 2, 3, 4, 5),
            template,
            target_id="t",
            perceive=lambda gen: ((0, 1, 2, 3, 4, 5), {}),
            initial_coordinates=bad,
        )
    except ValueError as exc:
        assert "initial_coordinates shape must match" in str(exc)
    else:
        raise AssertionError("expected flexible shape mismatch to raise")
