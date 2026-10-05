#!/usr/bin/env python3

"""Tests for the explicit component registry (FIX-1A A1).

Covers: default registry shape/order, constructor guards, ``owner_of``,
explicit-stage compatibility (order/duplicate/unknown, no factory or
``is_active`` involvement), ``resolve`` vs the pre-A1 ``_levels``
behavior (old implementation copied below as reference), factory error
and message preservation, snapshot immutability, ``run()`` signature
stability, and the model-import purity probe.
"""

from __future__ import annotations

import ast
import copy
import inspect
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureRecord
from confflow.science.confgen import (
    ConfgenEngine,
    GenerationStage,
    GenerationTarget,
    PerceptionResult,
    RealizationResult,
    StageEstimate,
    UnsupportedAxisError,
    WorkingRealization,
    build_context,
)
from confflow.science.confgen.engine import combine_state_key, thaw_snapshot
from confflow.science.confgen.model import AXIS_ORDER, MolecularContext
from confflow.science.confgen.planner import normalize_spec
from confflow.science.confgen.registry import (
    ComponentDescriptor,
    ComponentRegistry,
    build_default_registry,
    default_registry,
    resolve_registry,
)

# ---------------------------------------------------------------------------
# Old-implementation reference (copied verbatim from the pre-A1 engine).
# ---------------------------------------------------------------------------


def _old_load_stage(axis: str, resolved: Mapping[str, Any]) -> Any:
    snapshot = thaw_snapshot(resolved)
    if axis == "torsions":
        from confflow.science.confgen.torsion.stage import TorsionStage

        return TorsionStage(snapshot)
    if axis == "rings":
        try:
            from confflow.science.confgen.ring.stage import RingStage
        except ImportError as exc:
            raise UnsupportedAxisError(
                "rings requested but the ring stage module is unavailable"
            ) from exc
        return RingStage(snapshot)
    if axis == "coordination":
        try:
            from confflow.science.confgen.coordination.stage import (
                CoordinationStage,
                adapt_to_core,
            )
        except ImportError as exc:
            raise UnsupportedAxisError(
                "coordination requested but the coordination stage module is unavailable"
            ) from exc
        section = snapshot.get("coordination")
        if not isinstance(section, Mapping):
            raise UnsupportedAxisError(
                "coordination requested but the resolved coordination section is missing"
            )
        try:
            stage = adapt_to_core(CoordinationStage(dict(section)))
        except UnsupportedAxisError:
            raise
        except Exception as exc:
            raise UnsupportedAxisError(
                f"coordination requested but the protocol binding failed: {exc}"
            ) from exc
        if getattr(stage, "axis", None) != "coordination":
            raise UnsupportedAxisError("coordination binding returned a foreign stage")
        return stage
    raise UnsupportedAxisError(f"unknown generation axis {axis!r}")


def _old_levels_default(resolved: Mapping[str, Any]) -> list[tuple[str, Any]]:
    levels = []
    coordination = resolved.get("coordination")
    if isinstance(coordination, Mapping) and coordination is not None:
        if coordination.get("treatment", "enumerate") != "preserve_input":
            levels.append(("coordination", _old_load_stage("coordination", resolved)))
    rings = resolved.get("rings", [])
    if isinstance(rings, (list, tuple)) and len(rings) > 0:
        levels.append(("rings", _old_load_stage("rings", resolved)))
    torsions = resolved.get("torsions", [])
    if isinstance(torsions, (list, tuple)) and len(torsions) > 0:
        levels.append(("torsions", _old_load_stage("torsions", resolved)))
    return levels


def _old_explicit_sort(axes: list[str]) -> list[str]:
    order = {axis: position for position, axis in enumerate(AXIS_ORDER)}
    ranked = list(axes)
    ranked.sort(key=lambda item: order.get(item, len(order)))
    return ranked


def _outcome(resolved: Mapping[str, Any], loader: Any) -> tuple[str, Any]:
    try:
        levels = loader(resolved)
    except Exception as exc:  # noqa: BLE001 - outcome comparison needs the full error
        return ("raise", (type(exc).__name__, str(exc)))
    return ("ok", [(axis, type(stage).__name__) for axis, stage in levels])


def _valid_torsion_resolved() -> dict[str, Any]:
    return normalize_spec(
        {
            "schema_version": 3,
            "index_base": 1,
            "torsions": [
                {
                    "id": "c",
                    "bond": [2, 3],
                    "model": "relative_rotation_grid",
                    "angles": [0.0, 120.0],
                }
            ],
        }
    )


RESOLVED_BATTERY: dict[str, Mapping[str, Any]] = {
    "empty": {},
    "coordination-default-active-but-unbuildable": {"coordination": {}},
    "coordination-preserve-input": {"coordination": {"treatment": "preserve_input"}},
    "coordination-non-mapping": {"coordination": 42},
    "coordination-missing-section": {"coordination": None},
    "rings-empty": {"rings": []},
    "rings-wrong-type": {"rings": "x"},
    "rings-unbuildable": {"rings": [{"atoms": [1]}]},
    "torsions-empty": {"torsions": []},
    "torsions-wrong-type": {"torsions": "x"},
    "torsions-unbuildable": {"torsions": [{"id": "bad"}]},
}


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_default_registry_ids_order_and_declarations() -> None:
    registry = build_default_registry()
    assert registry.ids() == ("coordination", "rings", "torsions")
    by_id = {descriptor.id: descriptor for descriptor in registry.descriptors}
    assert by_id["coordination"].order == 10
    assert by_id["rings"].order == 20
    assert by_id["torsions"].order == 30
    assert by_id["coordination"].spec_keys == ("coordination",)
    assert by_id["rings"].spec_keys == ("rings",)
    assert by_id["torsions"].spec_keys == ("torsions", "paths", "strict_path_bond_check")
    assert by_id["coordination"].state_merge == "replace"
    assert by_id["rings"].state_merge == "merge"
    assert by_id["torsions"].state_merge == "merge"


def test_default_registry_is_shared_immutable_instance() -> None:
    assert default_registry() is default_registry()
    assert resolve_registry(None) is default_registry()
    custom = build_default_registry().with_component(
        ComponentDescriptor(
            id="probe",
            order=40,
            spec_keys=("probe_section",),
            state_merge="merge",
            is_active=lambda resolved: False,
            factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        )
    )
    assert custom.ids() == ("coordination", "rings", "torsions", "probe")
    assert resolve_registry(custom) is custom


def test_with_component_does_not_mutate_original() -> None:
    registry = build_default_registry()
    before = registry.ids()
    extra = ComponentDescriptor(
        id="probe",
        order=5,
        spec_keys=("probe_section",),
        state_merge="merge",
        is_active=lambda resolved: True,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
    )
    extended = registry.with_component(extra)
    assert registry.ids() == before == ("coordination", "rings", "torsions")
    assert len(registry.descriptors) == 3
    assert extended.ids() == ("probe", "coordination", "rings", "torsions")


def _descriptor(id: str, order: int, spec_keys: tuple[str, ...]) -> ComponentDescriptor:
    return ComponentDescriptor(
        id=id,
        order=order,
        spec_keys=spec_keys,
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
    )


def test_registry_rejects_duplicate_id() -> None:
    with pytest.raises(ValueError, match="duplicate component id"):
        ComponentRegistry(descriptors=(_descriptor("a", 1, ("ka",)), _descriptor("a", 2, ("kb",))))


def test_registry_rejects_duplicate_order() -> None:
    with pytest.raises(ValueError, match="duplicate component order"):
        ComponentRegistry(descriptors=(_descriptor("a", 1, ("ka",)), _descriptor("b", 1, ("kb",))))


def test_registry_rejects_overlapping_spec_keys() -> None:
    with pytest.raises(ValueError, match="overlapping spec_keys"):
        ComponentRegistry(
            descriptors=(_descriptor("a", 1, ("shared",)), _descriptor("b", 2, ("shared",)))
        )


def test_owner_of() -> None:
    registry = build_default_registry()
    assert registry.owner_of("coordination") == "coordination"
    assert registry.owner_of("rings") == "rings"
    assert registry.owner_of("torsions") == "torsions"
    assert registry.owner_of("paths") == "torsions"
    assert registry.owner_of("strict_path_bond_check") == "torsions"
    assert registry.owner_of("topology") is None
    assert registry.owner_of("nope") is None


# ---------------------------------------------------------------------------
# Explicit-stage compatibility
# ---------------------------------------------------------------------------


class _FakeStage(GenerationStage):
    """Engine-mechanics scaffolding with a configurable axis label."""

    def __init__(self, axis_spec: Mapping[str, Any] = {}) -> None:  # noqa: B006
        self._axis_label = str(dict(axis_spec).get("axis", "torsions"))

    @property
    def axis(self) -> str:
        return self._axis_label

    def estimate(self, parent: WorkingRealization, context: MolecularContext) -> StageEstimate:
        return StageEstimate(
            declared_count=1,
            upper_bound=1,
            exact=True,
            details=FrozenDict({"basis": "registry-probe", "scope_coverage": "exact"}),
        )

    def enumerate_targets(
        self, parent: WorkingRealization, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        yield GenerationTarget(
            axis=self._axis_label,
            target_id=f"{self._axis_label}:000000",
            state_value={},
            ordinal=0,
        )

    def realize(
        self,
        parent: WorkingRealization,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> RealizationResult:
        return RealizationResult(
            structure=parent.structure, status="realized", reason="probe", backend="probe"
        )

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        return PerceptionResult(best_key={})


def _fake(axis: str) -> _FakeStage:
    return _FakeStage({"axis": axis})


def test_explicit_duplicate_error_is_verbatim() -> None:
    with pytest.raises(ValueError, match=r"^duplicate stage axes in explicit stage list$"):
        ConfgenEngine(stages=[_fake("rings"), _fake("rings")])._levels({})
    with pytest.raises(ValueError, match=r"^duplicate stage axes in explicit stage list$"):
        ConfgenEngine(stages=[_fake("mystery"), _fake("mystery")])._levels({})


def test_explicit_order_matches_old_reference() -> None:
    scrambled = ["torsions", "coordination", "rings"]
    engine = ConfgenEngine(stages=[_fake(axis) for axis in scrambled])
    assert [axis for axis, _ in engine._levels({})] == ["coordination", "rings", "torsions"]
    assert _old_explicit_sort(scrambled) == ["coordination", "rings", "torsions"]


def test_explicit_unknown_axes_stable_last() -> None:
    axes = ["zzz-b", "torsions", "zzz-a", "rings"]
    engine = ConfgenEngine(stages=[_fake(axis) for axis in axes])
    assert [axis for axis, _ in engine._levels({})] == ["rings", "torsions", "zzz-b", "zzz-a"]
    assert _old_explicit_sort(axes) == ["rings", "torsions", "zzz-b", "zzz-a"]


def test_explicit_path_does_not_call_factory_or_is_active() -> None:
    calls: list[str] = []

    def _is_active(resolved: Mapping[str, Any]) -> bool:
        calls.append("is_active")
        return True

    def _factory(resolved: Mapping[str, Any]) -> Any:
        calls.append("factory")
        raise AssertionError("explicit path must not call factory")

    registry = ComponentRegistry(
        descriptors=(
            ComponentDescriptor(
                id="torsions",
                order=30,
                spec_keys=("torsions",),
                state_merge="merge",
                is_active=_is_active,
                factory=_factory,
            ),
        )
    )
    engine = ConfgenEngine(stages=[_fake("torsions")], registry=registry)
    assert [axis for axis, _ in engine._levels({})] == ["torsions"]
    assert calls == []


def test_explicit_branch_has_no_factory_or_is_active_ast() -> None:
    source = Path(__file__).parent.parent.parent / "confflow" / "science" / "confgen" / "engine.py"
    tree = ast.parse(source.read_text())
    levels = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_levels"
    )
    explicit = next(
        node
        for node in ast.walk(levels)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and "explicit" in ast.dump(node.test)
    )
    names: set[str] = set()
    for node in ast.walk(explicit):
        if isinstance(node, ast.Attribute) and node.attr in {"factory", "is_active"}:
            names.add(node.attr)
        if isinstance(node, ast.Name) and node.id in {"factory", "is_active"}:
            names.add(node.id)
    assert names == set()


# ---------------------------------------------------------------------------
# resolve vs old _levels equivalence (parametrized, outcomes incl. errors)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(RESOLVED_BATTERY))
def test_resolve_matches_old_levels(name: str) -> None:
    resolved = RESOLVED_BATTERY[name]
    registry = build_default_registry()
    assert _outcome(resolved, registry.resolve) == _outcome(resolved, _old_levels_default)


def test_resolve_matches_old_levels_valid_torsion() -> None:
    resolved = _valid_torsion_resolved()
    registry = build_default_registry()
    assert _outcome(resolved, registry.resolve) == _outcome(resolved, _old_levels_default)
    assert _outcome(resolved, registry.resolve) == (
        "ok",
        [("torsions", "TorsionStage")],
    )


def test_resolve_does_not_mutate_snapshot() -> None:
    resolved = _valid_torsion_resolved()
    before = copy.deepcopy(resolved)
    levels = build_default_registry().resolve(resolved)
    assert resolved == before
    assert [(axis, type(stage).__name__) for axis, stage in levels] == [
        ("torsions", "TorsionStage")
    ]


# ---------------------------------------------------------------------------
# Factory errors and message preservation
# ---------------------------------------------------------------------------


def test_load_stage_unknown_axis_error() -> None:
    engine = ConfgenEngine()
    with pytest.raises(UnsupportedAxisError) as caught:
        engine._load_stage("nonsense", {})
    assert type(caught.value) is UnsupportedAxisError
    assert str(caught.value) == "unknown generation axis 'nonsense'"


def test_load_stage_and_combine_unknown_axis_are_distinct() -> None:
    from confflow.science.confgen.model import ConfgenStateKey

    engine = ConfgenEngine()
    with pytest.raises(UnsupportedAxisError) as load_caught:
        engine._load_stage("nonsense", {})
    with pytest.raises(ValueError) as combine_caught:
        combine_state_key(ConfgenStateKey(coordination={}, rings={}, torsions={}), "nonsense", {})
    assert type(combine_caught.value) is ValueError
    assert type(load_caught.value) is UnsupportedAxisError
    assert str(combine_caught.value) == "unknown generation axis 'nonsense'"
    assert str(load_caught.value) == "unknown generation axis 'nonsense'"


def test_coordination_missing_section_error() -> None:
    engine = ConfgenEngine()
    with pytest.raises(
        UnsupportedAxisError,
        match=r"^coordination requested but the resolved coordination section is missing$",
    ):
        engine._load_stage("coordination", {"coordination": None})


def test_ring_unavailable_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "confflow.science.confgen.ring.stage", None)
    engine = ConfgenEngine()
    with pytest.raises(
        UnsupportedAxisError,
        match=r"^rings requested but the ring stage module is unavailable$",
    ):
        engine._load_stage("rings", {"rings": [{"atoms": [0]}]})


def test_coordination_unavailable_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "confflow.science.confgen.coordination.stage", None)
    engine = ConfgenEngine()
    with pytest.raises(
        UnsupportedAxisError,
        match=r"^coordination requested but the coordination stage module is unavailable$",
    ):
        engine._load_stage("coordination", {"coordination": {"treatment": "enumerate"}})


# ---------------------------------------------------------------------------
# run() stability: signature, pseudo-stage chain, unknown-axis failure site
# ---------------------------------------------------------------------------


def test_run_signature_unchanged() -> None:
    signature = inspect.signature(ConfgenEngine.run)
    assert list(signature.parameters) == ["self", "context", "should_cancel"]
    assert signature.parameters["should_cancel"].default is None


def _probe_context() -> MolecularContext:
    structure = StructureRecord(
        id="probe",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.4, 0.0), (4.5, 0.4, 0.0)),
        charge=0,
        multiplicity=1,
    )
    return build_context(structure, {"index_base": 0})


def test_run_chain_with_pseudo_stage() -> None:
    run = ConfgenEngine(stages=[_fake("torsions")]).run(_probe_context())
    assert len(run.leaves) == 1
    assert len(run.target_records) == 1


def test_run_unknown_axis_fails_at_target_validation() -> None:
    with pytest.raises(
        ValueError,
        match=r"^target axis must be one of \('coordination', 'rings', 'torsions'\), got 'mystery'$",
    ):
        ConfgenEngine(stages=[_FakeStage({"axis": "mystery"})]).run(_probe_context())


# ---------------------------------------------------------------------------
# Import purity: importing model must not load registry/component modules.
# ---------------------------------------------------------------------------


def test_model_import_loads_no_registry_or_components() -> None:
    probe = (
        "import sys, confflow.science.confgen.model; "
        "mods=sorted(m for m in sys.modules "
        "if m == 'confflow.science.confgen.registry' "
        "or m.startswith('confflow.science.confgen.coordination.component') "
        "or m.startswith('confflow.science.confgen.ring.component') "
        "or m.startswith('confflow.science.confgen.torsion.component')); "
        "print(mods)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd="/tmp/fix1a-a1-proto",
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]", completed.stdout
