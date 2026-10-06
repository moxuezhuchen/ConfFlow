#!/usr/bin/env python3
"""Public-boundary hardening: registry/context/stats + coordination retry (FIX-1A/1D).

PLAN mapping and missing risk:
- FIX-1A A1 explicit registry: duplicate ids/orders, overlapping spec_keys,
  hijacked/unowned/duplicated defaults, unowned topo inputs, malformed
  compat triples must fail closed at construction. Prior tests pin happy
  paths; a hijacked default would silently change insertion order/bytes.
- FIX-1A AG2 compat: resolve_compat unknown/missing must fail closed with
  the original error chained (timing preserved via `from exc`). Risk:
  swallowed ImportError/AttributeError hides wiring bugs.
- FIX-1A A4b/AG1 context: build_context wrong types fail closed before any
  graph work. Risk: late confusing errors.
- FIX-1D L-D3 scope statistics: engine groups by bound id (caller cannot
  choose), forwards truthful snapshots, wraps hook errors as
  TelemetryError, drops empty/None without golden drift. Risk: forged
  counts overwrite scope or silent swallow.
- Coordination molecular authority + D0/D0.2/D1/D2 retry: site_group
  witnesses must be full-atom permutations with provenance; generators
  without backing witnesses stay restricted; retry dispatch declines
  wrong-axis/solver-error/non-retryable/missing/unknown-phase fail-closed;
  skip diagnoses are literal labels; production declares sigma+sibling.
  Risk: donor-only perm becomes suppression authority, or wrong target
  retried, or budget/cancel mishandled.
All cases use real public APIs with literal expected values; small real
fixtures (2-atom structures, 4-site coordination sections, 1-slot engine
runs) keep runs fast.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.structure import StructureRecord
from confflow.science.confgen.engine import ConfgenEngine
from confflow.science.confgen.model import (
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    PerceptionResult,
    RealizationResult,
    RetryFirstPass,
    StageEstimate,
    build_context,
)
from confflow.science.confgen.wire_v3 import from_wire_key


def _root(tag: str = "pub") -> StructureRecord:
    return StructureRecord(
        id=tag,
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (2.0, 1.3, 0.5), (3.4, 1.7, 0.2)),
        charge=0,
        multiplicity=1,
    )


def _ctx(extra: Mapping[str, Any] | None = None) -> MolecularContext:
    spec = {"index_base": 0, "seed": 7}
    if extra:
        spec.update(dict(extra))
    return build_context(_root(), spec)


def _desc(
    *,
    cid: str = "zz",
    order: int = 99,
    keys: tuple = ("zz_key",),
    defaults: tuple = (),
    topo: tuple = (),
    compat: tuple = (),
) -> Any:
    from confflow.science.confgen.registry import ComponentDescriptor

    return ComponentDescriptor(
        id=cid,
        order=order,
        spec_keys=tuple(keys),
        state_merge="replace",
        is_active=lambda resolved: False,
        factory=lambda resolved: None,  # type: ignore[return-value]
        spec_defaults=tuple(defaults),
        topology_input_keys=tuple(topo),
        legacy_compat=tuple(compat),
    )


def test_registry_construction_fails_closed() -> None:
    from confflow.science.confgen.registry import ComponentRegistry, build_default_registry

    default = build_default_registry()
    assert default.ids() == ("coordination", "rings", "torsions")
    with pytest.raises(ValueError, match="duplicate component id"):
        ComponentRegistry(descriptors=(default.descriptors[0], default.descriptors[0]))
    with pytest.raises(ValueError, match="duplicate component order"):
        ComponentRegistry(
            descriptors=(
                _desc(cid="a", order=1, keys=("ka",)),
                _desc(cid="b", order=1, keys=("kb",)),
            )
        )
    with pytest.raises(ValueError, match="overlapping spec_keys"):
        ComponentRegistry(
            descriptors=(
                _desc(cid="a", order=1, keys=("same",)),
                _desc(cid="b", order=2, keys=("same",)),
            )
        )
    assert default.owner_of("coordination") == "coordination"
    assert default.owner_of("nope") is None
    added = default.with_component(_desc())
    assert added is not default and len(added.descriptors) == len(default.descriptors) + 1


def test_registry_defaults_topo_compat_fail_closed_with_cause() -> None:
    with pytest.raises(ValueError, match="malformed spec default"):
        _desc(defaults=(("only-key",),))
    with pytest.raises(ValueError, match="unowned spec key"):
        _desc(keys=("owned",), defaults=(("other", 1),))
    with pytest.raises(ValueError, match="duplicate spec default"):
        _desc(keys=("k",), defaults=(("k", 1), ("k", 2)))
    bad_default = None
    try:
        _desc(keys=("k",), defaults=(("k", object()),))
    except ValueError as exc:
        bad_default = exc
    assert bad_default is not None and bad_default.__cause__ is not None
    with pytest.raises(ValueError, match="unowned key"):
        _desc(keys=("owned",), topo=("other",))
    with pytest.raises(ValueError, match="malformed legacy compat"):
        _desc(compat=(("only-name",),))
    with pytest.raises(ValueError, match="non-empty strings"):
        _desc(compat=(("", "m", "a"),))
    from confflow.science.confgen.registry import ComponentRegistry

    with pytest.raises(ValueError, match="duplicate compat api"):
        ComponentRegistry(
            descriptors=(
                _desc(cid="a", order=1, keys=("ka",), compat=(("api", "m", "a"),)),
                _desc(cid="b", order=2, keys=("kb",), compat=(("api", "m", "a"),)),
            )
        )


def test_resolve_compat_truthful_and_chained() -> None:
    import confflow.science.confgen.coordination.spec as cspec
    from confflow.science.confgen.registry import resolve_compat, resolve_registry

    assert resolve_compat("overlay_declared_scope") is cspec.contribute_topology
    assert resolve_registry(None).ids() == ("coordination", "rings", "torsions")
    with pytest.raises(ValueError, match="unknown compat api"):
        resolve_compat("missing-api-xyz")
    from confflow.science.confgen.registry import ComponentRegistry

    reg = ComponentRegistry(
        descriptors=(_desc(cid="z", order=5, keys=("zk",), compat=(("gone", "no.such.mod", "a"),)),)
    )
    with pytest.raises(Exception, match="gone|No module|unknown"):
        resolve_compat("gone", reg)
    reg2 = ComponentRegistry(
        descriptors=(
            _desc(
                cid="z",
                order=5,
                keys=("zk",),
                compat=(("bad-attr", "confflow.science.confgen.registry", "no_such_attr"),),
            ),
        )
    )
    try:
        resolve_compat("bad-attr", reg2)
        raise AssertionError("must raise")
    except ValueError as exc:
        assert "missing" in str(exc) and exc.__cause__ is not None


class _OneSlot(GenerationStage):
    def __init__(self, stats: Any = "unset") -> None:
        self._stats = stats
        self.calls: list = []

    @property
    def axis(self) -> str:
        return "rings"

    def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
        return StageEstimate(
            declared_count=1,
            upper_bound=1,
            exact=True,
            details=FrozenDict({"basis": "pub", "scope_coverage": "exact"}),
        )

    def enumerate_targets(
        self, parent: Any, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        yield GenerationTarget(axis="rings", target_id="rings:000000", state_value={}, ordinal=0)

    def realize(self, parent: Any, target: Any, context: MolecularContext) -> RealizationResult:
        return RealizationResult(
            structure=StructureRecord(
                id="leaf",
                atoms=tuple(parent.structure.atoms),
                coordinates=tuple(
                    tuple(float(v) for v in row) for row in parent.structure.coordinates
                ),
                charge=parent.structure.charge,
                multiplicity=parent.structure.multiplicity,
                parent_ids=(parent.structure.id,),
            ),
            status="realized",
            reason="pub-ok",
            backend="pub",
            evidence=(),
        )

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        return PerceptionResult(best_key={"rings": "pub"})

    def report_statistics(self, snapshot: Any) -> Any:  # type: ignore[override]
        self.calls.append(tuple(snapshot))
        if self._stats == "unset":
            return super().report_statistics(snapshot)
        if callable(self._stats):
            return self._stats(snapshot)
        return self._stats


def _run_stats(stats: Any) -> Any:
    stage = _OneSlot(stats)
    engine = ConfgenEngine(stages=[stage])
    context = _ctx()
    run = engine.run_kernel(context, initial_key=from_wire_key(context.input_state_key))
    return run, stage


def test_scope_statistics_truthful_and_not_forgeable() -> None:
    run, stage = _run_stats({"events": 0})
    assert run.report_json()["scope"]["component_statistics"] == {"rings": {"events": 0}}
    # Snapshot is the engine-bound truthful slice: one legacy input event.
    assert len(stage.calls[0]) == 1
    assert stage.calls[0][0].component_id == "rings"
    assert stage.calls[0][0].accepted is True
    # Engine forwards verbatim (does not recompute 0 -> 1).
    run_truth, _ = _run_stats(lambda snap: {"events": len(tuple(snap))})
    assert run_truth.report_json()["scope"]["component_statistics"] == {"rings": {"events": 1}}
    run2, _ = _run_stats(None)
    assert "component_statistics" not in run2.report_json()["scope"]
    run3, _ = _run_stats({})
    assert "component_statistics" not in run3.report_json()["scope"]


def test_scope_statistics_errors_fail_closed_visibly() -> None:
    from confflow.science.confgen.kernel_records import TelemetryError

    with pytest.raises(TelemetryError, match="must return a mapping"):
        _run_stats("bad-string")
    try:
        _run_stats("bad-string")
    except TelemetryError as exc:
        assert exc.__cause__ is None

    def _boom(snapshot: Any) -> Any:
        raise RuntimeError("boom-x")

    with pytest.raises(TelemetryError, match="boom-x"):
        _run_stats(_boom)
    try:
        _run_stats(_boom)
    except TelemetryError as exc:
        assert isinstance(exc.__cause__, RuntimeError)


def _coord_section(**over: Any) -> dict[str, Any]:
    sites = [{"id": f"S{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1} for i in range(4)]
    section: dict[str, Any] = {"metal_center": 0, "binding_sites": sites}
    section.update(over)
    return section


def test_coordination_site_group_spec_fails_closed() -> None:
    from confflow.science.confgen.coordination.stage import CoordinationStage

    with pytest.raises(ValueError, match="unknown keys"):
        CoordinationStage({**_coord_section(), "bogus": 1})
    with pytest.raises(ValueError, match="metal_center"):
        CoordinationStage({"metal_center": "bad", "binding_sites": []})
    try:
        CoordinationStage({"metal_center": "bad", "binding_sites": []})
    except ValueError as exc:
        assert exc.__cause__ is not None
    with pytest.raises(ValueError, match="neither a coordination section"):
        CoordinationStage({"binding_sites": []})
    with pytest.raises(ValueError, match="site_group must be a mapping"):
        CoordinationStage({**_coord_section(), "site_group": []})
    with pytest.raises(ValueError, match="must permute all site positions"):
        CoordinationStage(
            {**_coord_section(), "site_group": {"generators": [[0, 1, 2, 9]], "witnesses": []}}
        )
    with pytest.raises(ValueError, match="must map full atom permutations"):
        CoordinationStage(
            {**_coord_section(), "site_group": {"generators": [[0, 1, 2, 3]], "witnesses": [{}]}}
        )
    with pytest.raises(ValueError, match="must be a permutation"):
        CoordinationStage(
            {
                **_coord_section(),
                "site_group": {
                    "generators": [[0, 1, 2, 3]],
                    "witnesses": [{"mapping": [0, 0, 1, 2], "provenance": "p"}],
                },
            }
        )
    with pytest.raises(ValueError, match="requires provenance"):
        CoordinationStage(
            {
                **_coord_section(),
                "site_group": {
                    "generators": [[0, 1, 2, 3]],
                    "witnesses": [{"mapping": [0, 1, 2, 3], "provenance": ""}],
                },
            }
        )


def test_coordination_retry_dispatch_declines_fail_closed() -> None:
    from confflow.science.confgen.coordination.stage import CoordinationStage

    stage = CoordinationStage(_coord_section())
    assert stage.retry_phases() == ("sigma", "sibling")
    assert (
        stage.sigma_skip_diagnosis(
            n_sources=0, n_validated_witnesses=0, n_candidates=0, n_successes=0
        )
        == "sigma_image: skipped (no realized source)"
    )
    assert (
        stage.sibling_skip_diagnosis(n_sources=0, n_candidates=0, n_successes=0)
        == "sibling: skipped (no realized source)"
    )
    assert (
        stage.sibling_skip_diagnosis(n_sources=1, n_candidates=0, n_successes=0)
        == "sibling: skipped (no candidate)"
    )
    assert (
        stage.sibling_skip_diagnosis(n_sources=1, n_candidates=2, n_successes=0)
        == "sibling: exhausted (all sibling starts failed)"
    )
    assert (
        stage.sibling_skip_diagnosis(n_sources=1, n_candidates=2, n_successes=1)
        == "sibling: realized from 1 candidate(s)"
    )
    good = GenerationTarget(
        axis="coordination",
        target_id="coordination:000000",
        state_value={"placement": [0, 1, 2, 3], "shape": "tetrahedral"},
        ordinal=0,
    )
    rings = GenerationTarget(axis="rings", target_id="rings:000000", state_value={}, ordinal=0)
    assert stage.retry_solve_phase(None, rings, None, None, (), "sigma", ()) is None
    assert stage.retry_solve_phase(None, good, None, None, (), "no-such-phase", ()) is None
    assert stage.retry_solve_phase(None, good, None, None, "bad", "sigma", ()) is None
    missing: tuple = ()
    assert stage.retry_solve_phase(None, good, None, None, missing, "sigma", missing) is None
    solved = (
        RetryFirstPass(
            target_id="coordination:000000",
            ordinal=0,
            status="published_leaf",
            reason="ok",
            solver_error=False,
            structure=None,
        ),
    )
    assert stage.retry_solve_phase(None, good, None, None, solved, "sigma", solved) is None
    err = (
        RetryFirstPass(
            target_id="coordination:000000",
            ordinal=0,
            status="failed_numerical",
            reason="bug",
            solver_error=True,
            structure=None,
        ),
    )
    assert stage.retry_solve_phase(None, good, None, None, err, "sigma", err) is None
    assert stage.retry_solve_phase(None, good, None, None, err, "sibling", err) is None


def test_coordination_sigma_decline_carries_literal_skip_reason() -> None:
    from confflow.science.confgen.coordination.stage import CoordinationStage
    from confflow.science.confgen.kernel_records import RetryResult

    stage = CoordinationStage(_coord_section())
    target = GenerationTarget(
        axis="coordination",
        target_id="coordination:000000",
        state_value={"placement": [0, 1, 2, 3], "shape": "tetrahedral"},
        ordinal=0,
    )
    failed = (
        RetryFirstPass(
            target_id="coordination:000000",
            ordinal=0,
            status="failed_numerical",
            reason="probe",
            solver_error=False,
            structure=None,
        ),
    )
    out = stage.retry_solve_phase(None, target, None, None, failed, "sigma", failed)
    assert isinstance(out, RetryResult)
    assert out.outcome is None
    assert out.telemetry[0].diagnostic["skip_reason"] == "sigma_image: skipped (no realized source)"
    out2 = stage.retry_solve_phase(None, target, None, None, failed, "sibling", failed)
    assert isinstance(out2, RetryResult)
    assert out2.telemetry[0].diagnostic["skip_reason"] == "sibling: skipped (no realized source)"
