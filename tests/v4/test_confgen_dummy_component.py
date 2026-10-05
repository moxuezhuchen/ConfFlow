#!/usr/bin/env python3
"""A6 DummyComponent acceptance (test-only, no production change).

Proves adding a component needs no kernel change: the dummy descriptor is
added via ``registry.with_component`` and driven from the real
``build_context`` entry through ``run_kernel``. The wire adapter stays
fail-closed for the new axis.
"""

from __future__ import annotations

import dataclasses as _dc
import inspect
from typing import Any

import pytest

from confflow.domain.structure import StructureRecord
from confflow.science.confgen.engine import ConfgenEngine
from confflow.science.confgen.kernel_records import ComponentStateKey
from confflow.science.confgen.model import GenerationTarget, build_context
from confflow.science.confgen.registry import ComponentDescriptor, build_default_registry
from tests.v4.confgen_dummy_component import descriptor as dummy_descriptor


def _structure() -> StructureRecord:
    # Five atoms: torsion quartet 0-3 plus dummy atom 4 outside it, so the
    # torsion x dummy combination is uncoupled (torsion parent locks hold).
    return StructureRecord(
        id="dummy-in",
        atoms=("C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (3.0, 0.4, 0.0),
            (4.5, 0.4, 0.0),
            (6.0, 0.4, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )


def _torsion_spec() -> dict[str, Any]:
    return {
        "id": "c",
        "bond": [2, 3],
        "model": "relative_rotation_grid",
        "angles": [0.0, 120.0],
    }


def _breaker_descriptor(atom: int, extra: float) -> ComponentDescriptor:
    """Downstream component that tampers the dummy atom during realize."""

    class _BreakerStage:
        axis = "breaker"

        def is_conditional(self, context: Any) -> bool:
            return False

        def axis_ids(self, context: Any) -> Any:
            return None

        def estimate(self, parent: Any, context: Any) -> Any:
            from confflow.domain._immutable import FrozenDict
            from confflow.science.confgen.model import StageEstimate

            return StageEstimate(
                declared_count=1,
                upper_bound=1,
                exact=True,
                details=FrozenDict({"basis": "breaker", "scope_coverage": "exact"}),
            )

        def enumerate_targets(self, parent: Any, context: Any) -> Any:
            from confflow.domain._immutable import FrozenDict
            from confflow.science.confgen.kernel_records import KernelGenerationTarget

            yield KernelGenerationTarget(
                axis="breaker",
                target_id="breaker:000000",
                state_value={"tamper": extra},
                ordinal=0,
                provenance=FrozenDict({}),
            )

        def realize(self, parent: Any, target: Any, context: Any) -> Any:
            from confflow.science.confgen.model import RealizationResult

            base = parent.structure
            coords = [list(map(float, row)) for row in base.coordinates]
            coords[atom][0] += float(extra)
            struct = StructureRecord(
                id=f"{base.id}:b",
                atoms=tuple(base.atoms),
                coordinates=tuple(tuple(row) for row in coords),
                charge=int(base.charge),
                multiplicity=int(base.multiplicity),
            )
            return RealizationResult(
                structure=struct, status="realized", reason="breaker", backend="breaker"
            )

        def perceive(self, structure: Any, context: Any) -> Any:
            from confflow.science.confgen.model import PerceptionResult

            return PerceptionResult(best_key={"tamper": extra}, confidence="reported")

    return ComponentDescriptor(
        id="breaker",
        order=50,
        spec_keys=("breaker",),
        state_merge="merge",
        is_active=lambda resolved: bool(resolved.get("breaker")),
        factory=lambda resolved: _BreakerStage(),
    )


def test_dummy_registry_is_independent_and_ordered() -> None:
    base = build_default_registry()
    extended = base.with_component(dummy_descriptor())
    assert extended.ids() == ("coordination", "rings", "torsions", "dummy")
    assert "dummy" not in base.ids()
    assert extended.owner_of("dummy") is not None


def test_dummy_normalize_and_context_through_real_entry() -> None:
    reg = build_default_registry().with_component(dummy_descriptor())
    ctx = build_context(
        _structure(), {"index_base": 1, "dummy": [{"atom": 5}], "seed": 1}, registry=reg
    )
    assert ctx.registry is reg
    assert ctx.active_components == ("dummy",)
    assert ctx.resolved_spec["dummy"][0]["atom"] == 4


def test_dummy_run_kernel_leaves_and_torsion_product() -> None:
    reg = build_default_registry().with_component(dummy_descriptor())
    engine = ConfgenEngine(registry=reg)
    ctx = build_context(
        _structure(), {"index_base": 1, "dummy": [{"atom": 5}], "seed": 1}, registry=reg
    )
    fresh = engine.run_kernel(ctx, initial_key=ComponentStateKey(components={}))
    assert len(fresh.leaves) == 2
    for leaf in fresh.leaves:
        assert "dummy" in dict(leaf.state_key.components)

    combined = build_context(
        _structure(),
        {"index_base": 1, "torsions": [_torsion_spec()], "dummy": [{"atom": 5}], "seed": 1},
        registry=reg,
    )
    assert combined.active_components == ("torsions", "dummy")
    both = engine.run_kernel(combined, initial_key=ComponentStateKey(components={}))
    torsion_only_ctx = build_context(
        _structure(), {"index_base": 1, "torsions": [_torsion_spec()], "seed": 1}
    )
    torsion_only = ConfgenEngine().run_kernel(
        torsion_only_ctx, initial_key=ComponentStateKey(components={})
    )
    assert len(both.leaves) == 2 * len(torsion_only.leaves) == 4


def test_dummy_parent_lock_tamper_detected_through_run_kernel() -> None:
    # A downstream breaker tampers the dummy atom; the engine must report
    # the dummy ancestor lock as drift through the real run_kernel path
    # (not a direct verify_locked unit call).
    reg = (
        build_default_registry()
        .with_component(dummy_descriptor())
        .with_component(_breaker_descriptor(atom=4, extra=0.5))
    )
    engine = ConfgenEngine(registry=reg)
    ctx = build_context(
        _structure(),
        {"index_base": 1, "dummy": [{"atom": 5}], "breaker": [{}], "seed": 1},
        registry=reg,
    )
    assert ctx.active_components == ("dummy", "breaker")
    run = engine.run_kernel(ctx, initial_key=ComponentStateKey(components={}))
    drifted = [t for t in run.target_records if "DRIFT" in str(t.status).upper()]
    assert drifted, "breaker tamper must drift at least one leaf"
    assert len(run.leaves) < 2, "tampered dummy locks must not all publish"
    detail = " ".join(str(e) for t in drifted for e in t.evidence)
    assert "dummy" in detail and "drift" in detail


def test_dummy_inherited_state_carries_through_run_kernel() -> None:
    reg = build_default_registry().with_component(dummy_descriptor())
    engine = ConfgenEngine(registry=reg)
    ctx = build_context(
        _structure(), {"index_base": 1, "dummy": [{"atom": 5}], "seed": 1}, registry=reg
    )
    fresh = engine.run_kernel(ctx, initial_key=ComponentStateKey(components={}))
    dummy_state = dict(dict(fresh.leaves[0].state_key.components)["dummy"])
    assert dummy_state["direction"] in ("+", "-")
    payload = None
    for desc in reg.descriptors:
        if desc.id == "dummy":
            payload = desc.serialize_inherited_state(ctx.resolved_spec, dummy_state, ctx)
    assert payload == {"echo": dummy_state}
    chained = build_context(
        fresh.leaves[0].structure,
        {"dummy": [{"atom": 5}], "index_base": 1, "seed": 2},
        registry=reg,
    )
    chained = _dc.replace(chained, inherited_scope={"dummy": payload})
    carried = engine.run_kernel(
        chained, initial_key=ComponentStateKey(components={"dummy": dummy_state})
    )
    assert len(carried.leaves) == 2
    for leaf in carried.leaves:
        assert "dummy" in dict(leaf.state_key.components)
    with pytest.raises(TypeError):
        engine.run_kernel(chained)  # type: ignore[call-arg]


def test_dummy_wire_is_fail_closed_and_legacy_types_hold() -> None:
    reg = build_default_registry().with_component(dummy_descriptor())
    engine = ConfgenEngine(registry=reg)
    ctx = build_context(
        _structure(), {"index_base": 1, "dummy": [{"atom": 5}], "seed": 1}, registry=reg
    )
    with pytest.raises(Exception, match="(?i)dummy|UnsupportedWireComponent|unknown component"):
        engine.run(ctx)
    with pytest.raises(ValueError):
        GenerationTarget(axis="dummy", target_id="dummy:000000", state_value={}, ordinal=0)


def test_dummy_engine_signatures_unchanged() -> None:
    sig_run = inspect.signature(ConfgenEngine.run)
    assert "should_cancel" in sig_run.parameters
    assert list(sig_run.parameters)[1] == "context"
    sig_kernel = inspect.signature(ConfgenEngine.run_kernel)
    assert "initial_key" in sig_kernel.parameters
    assert "should_cancel" in sig_kernel.parameters
