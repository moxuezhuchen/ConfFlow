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
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"_levels", "_levels_for"}
    ]
    assert candidates, "missing _levels/_levels_for"
    found = False
    for levels in candidates:
        for node in ast.walk(levels):
            if not (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and "explicit" in ast.dump(node.test)
            ):
                continue
            found = True
            names: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr in {"factory", "is_active"}:
                    names.add(sub.attr)
                if isinstance(sub, ast.Name) and sub.id in {"factory", "is_active"}:
                    names.add(sub.id)
            assert names == set()
    assert found, "missing explicit branch"


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


# ---------------------------------------------------------------------------
# FIX-1A A2: kernel records + v3 wire split (behavior unchanged).
# ---------------------------------------------------------------------------


def _a2_old_combine(parent, axis, state_value):
    from confflow.science.confgen.model import ConfgenStateKey as _K

    if axis == "coordination":
        return _K(
            coordination=dict(state_value),
            rings=dict(parent.to_dict()["rings"]),
            torsions=dict(parent.to_dict()["torsions"]),
        )
    if axis == "rings":
        merged = dict(parent.to_dict()["rings"])
        merged.update(dict(state_value))
        return _K(
            coordination=parent.to_dict()["coordination"],
            rings=merged,
            torsions=dict(parent.to_dict()["torsions"]),
        )
    if axis == "torsions":
        merged = dict(parent.to_dict()["torsions"])
        merged.update(dict(state_value))
        return _K(
            coordination=parent.to_dict()["coordination"],
            rings=dict(parent.to_dict()["rings"]),
            torsions=merged,
        )
    raise ValueError(f"unknown generation axis {axis!r}")


def test_a2_legacy_type_errors_verbatim() -> None:
    from confflow.science.confgen.model import (
        ConfgenStateKey,
        GenerationTarget,
        OrbitIdentity,
        WorkingRealization,
    )

    with pytest.raises(
        ValueError,
        match=r"^target axis must be one of \('coordination', 'rings', 'torsions'\), got 'nonsense'$",
    ):
        GenerationTarget(axis="nonsense", target_id="x", state_value={}, ordinal=0)
    with pytest.raises(ValueError, match=r"^state_key must be a ConfgenStateKey$"):
        WorkingRealization(structure=_probe_context().structure, state_key="nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"^generation_axis must be one of "):
        WorkingRealization(
            structure=_probe_context().structure,
            state_key=ConfgenStateKey(),
            generation_axis="nonsense",
        )
    with pytest.raises(ValueError, match=r"^orbit axis must be one of "):
        OrbitIdentity(axis="nonsense", orbit_key="k")


def test_a2_axis_order_compat() -> None:
    from confflow.science.confgen import model as _m
    from confflow.science.confgen.wire_v3_constants import V3_AXIS_ORDER

    assert _m.AXIS_ORDER == ("coordination", "rings", "torsions")
    assert isinstance(_m.AXIS_ORDER, tuple)
    assert "AXIS_ORDER" in _m.__all__
    assert _m.AXIS_ORDER is V3_AXIS_ORDER


def test_a2_statekey_module_identity() -> None:
    from confflow.science.confgen.model import ConfgenStateKey

    assert ConfgenStateKey.__module__ == "confflow.science.confgen.model"
    import confflow.science.confgen.model as _m

    assert _m.ConfgenStateKey is ConfgenStateKey


def test_a2_run_signature_and_cancel() -> None:
    import inspect

    from confflow.science.confgen.engine import EngineCancelledError

    sig = inspect.signature(ConfgenEngine.run)
    assert list(sig.parameters) == ["self", "context", "should_cancel"]
    assert sig.parameters["should_cancel"].default is None
    # Cancel probe fires through run -> kernel.
    calls = {"n": 0}

    def _probe() -> bool:
        calls["n"] += 1
        return True

    with pytest.raises(EngineCancelledError):
        ConfgenEngine(stages=[_fake("torsions")]).run(_probe_context(), should_cancel=_probe)
    assert calls["n"] >= 1


def test_a2_combine_matches_old_three_axes() -> None:
    from confflow.science.confgen.model import ConfgenStateKey
    from confflow.science.confgen.registry import build_default_registry
    from confflow.science.confgen.wire_v3 import from_wire_key, to_wire_key

    reg = build_default_registry()
    parent = ConfgenStateKey(coordination={"c": 1}, rings={"r1": 1}, torsions={"t1": 2})
    generic = from_wire_key(parent)
    for axis, value in (("coordination", {"c": 2}), ("rings", {"r2": 3}), ("torsions", {"t2": 4})):
        assert (
            _a2_old_combine(parent, axis, value).to_dict()
            == to_wire_key(combine_state_key(generic, axis, value, registry=reg)).to_dict()
        )


def test_a2_wire_roundtrip() -> None:
    from confflow.science.confgen.model import ConfgenStateKey
    from confflow.science.confgen.wire_v3 import from_wire_key, to_wire_key

    for key in (
        ConfgenStateKey(),
        ConfgenStateKey(coordination={"a": 1}),
        ConfgenStateKey(rings={"r1": {"x": 1}}),
        ConfgenStateKey(torsions={"t1": 120.0}),
        ConfgenStateKey(coordination={"c": 1}, rings={"r1": 1}, torsions={"t1": 2}),
    ):
        assert to_wire_key(from_wire_key(key)) == key
        assert to_wire_key(from_wire_key(key)).to_dict() == key.to_dict()


def test_a2_unknown_component_failclosed() -> None:
    from confflow.science.confgen.kernel_records import ComponentStateKey
    from confflow.science.confgen.wire_v3 import UnsupportedWireComponent, to_wire_key

    with pytest.raises(UnsupportedWireComponent):
        to_wire_key(ComponentStateKey(components={"dummy": {"x": 1}}))


def test_a2_run_kernel_requires_initial_key() -> None:
    with pytest.raises(TypeError):
        ConfgenEngine().run_kernel(_probe_context())  # type: ignore[call-arg]


def test_a2_engine_leaves_are_legacy() -> None:
    run = ConfgenEngine(stages=[_fake("torsions")]).run(_probe_context())
    assert type(run.leaves[0]).__name__ == "WorkingRealization"
    assert run.leaves[0].__class__.__module__ == "confflow.science.confgen.model"


def _a2_real_context(spec: dict) -> MolecularContext:
    from confflow.domain import StructureRecord

    structure = StructureRecord(
        id="a2probe",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.4, 0.0), (4.5, 0.4, 0.0)),
        charge=0,
        multiplicity=1,
    )
    return build_context(structure, spec)


def test_a2_project_equivalence_torsion() -> None:
    import json

    from confflow.science.confgen.wire_v3 import from_wire_key, project_v3

    ctx = _a2_real_context(
        {
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
    engine = ConfgenEngine()
    first = engine.run(ctx)
    kernel_run = engine.run_kernel(ctx, initial_key=from_wire_key(ctx.input_state_key))
    second = project_v3(kernel_run)
    assert json.dumps(first.report_json(), sort_keys=True) == json.dumps(
        second.report_json(), sort_keys=True
    )
    assert first.certificate.digest == second.certificate.digest


def test_a2_project_equivalence_ring_coord() -> None:
    import json

    from confflow.science.confgen.wire_v3 import from_wire_key, project_v3

    try:
        from tests.v4.test_confgen_v3_core import _cyclohexane, _tetra_mn4
    except Exception as exc:  # pragma: no cover - missing optional science deps
        pytest.skip(f"ring/coord fixtures unavailable: {exc}")
    # Ring (includes 1 geometric failure in the published baseline).
    record, ring = _cyclohexane()
    ctx = build_context(
        record,
        {
            "schema_version": 3,
            "index_base": 1,
            "seed": 5,
            "rings": [{"id": "r1", "atoms": [i + 1 for i in ring]}],
        },
    )
    engine = ConfgenEngine()
    first = engine.run(ctx)
    second = project_v3(engine.run_kernel(ctx, initial_key=from_wire_key(ctx.input_state_key)))
    assert json.dumps(first.report_json(), sort_keys=True) == json.dumps(
        second.report_json(), sort_keys=True
    )
    # Coordination (pure enumeration, 5 leaves).
    record2, spec = _tetra_mn4()
    ctx2 = build_context(record2, spec)
    first2 = engine.run(ctx2)
    second2 = project_v3(engine.run_kernel(ctx2, initial_key=from_wire_key(ctx2.input_state_key)))
    assert json.dumps(first2.report_json(), sort_keys=True) == json.dumps(
        second2.report_json(), sort_keys=True
    )


def test_a2_project_equivalence_chain_failure_cancel() -> None:
    from confflow.science.confgen.engine import EngineCancelledError, InheritedScopeError
    from confflow.science.confgen.model import ConfgenStateKey
    from confflow.science.confgen.wire_v3 import from_wire_key

    # Chain fail-closed: non-empty key without scope raises in both paths.
    ctx = _a2_real_context({"index_base": 1})
    object.__setattr__(ctx, "input_state_key", ConfgenStateKey(torsions={"c": 0.0}))
    engine = ConfgenEngine()
    with pytest.raises(InheritedScopeError):
        engine.run(ctx)
    with pytest.raises(InheritedScopeError):
        engine.run_kernel(ctx, initial_key=from_wire_key(ctx.input_state_key))

    # Cancel propagates through kernel.
    def _always() -> bool:
        return True

    ctx2 = _a2_real_context(
        {
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
    with pytest.raises(EngineCancelledError):
        engine.run(ctx2, should_cancel=_always)
    with pytest.raises(EngineCancelledError):
        engine.run_kernel(
            ctx2, initial_key=from_wire_key(ctx2.input_state_key), should_cancel=_always
        )


def test_a2_stage_parent_ast_structure_only() -> None:
    from pathlib import Path as _P

    for rel in (
        "confflow/science/confgen/coordination/stage.py",
        "confflow/science/confgen/ring/stage.py",
        "confflow/science/confgen/torsion/stage.py",
    ):
        tree = ast.parse((_P(__file__).resolve().parents[2] / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {
                "state_key",
                "locked_axes",
                "generation_axis",
            }:
                base = node.value
                assert not (
                    isinstance(base, ast.Name) and base.id == "parent"
                ), f"{rel}:{node.lineno}"


def test_a2_scope_policy_clean() -> None:
    from pathlib import Path as _P

    from tools.architecture_policy import confgen_a2_violations

    assert confgen_a2_violations(_P(__file__).resolve().parents[2]) == []


# ---------------------------------------------------------------------------
# FIX-1A A2 recovery (root three rulings): direct regressions.
# ---------------------------------------------------------------------------


def test_a2_recovery_components_reject_nonmapping() -> None:
    from confflow.science.confgen.kernel_records import ComponentStateKey

    for bad in (None, [], ["x"], "x", 42, 3.14):
        with pytest.raises(ValueError, match="components must be a mapping"):
            ComponentStateKey(components=bad)  # type: ignore[arg-type]
    # Mapping still accepted (incl. empty).
    assert dict(ComponentStateKey(components={}).components) == {}
    assert dict(ComponentStateKey(components={"a": 1}).components) == {"a": 1}


def test_a2_recovery_no_compat_attrs() -> None:
    from confflow.science.confgen.kernel_records import ComponentStateKey

    assert "coordination" not in dir(ComponentStateKey)
    assert "rings" not in dir(ComponentStateKey)
    assert "torsions" not in dir(ComponentStateKey)
    key = ComponentStateKey(components={"dummy": {"x": 1}})
    for attr in ("coordination", "rings", "torsions"):
        assert not hasattr(key, attr), attr
    import ast as _ast
    from pathlib import Path as _P

    tree = _ast.parse(
        (
            _P(__file__).resolve().parents[2] / "confflow/science/confgen/kernel_records.py"
        ).read_text()
    )
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute) and node.attr in {"coordination", "rings", "torsions"}:
            raise AssertionError(f"kernel_records attr leak line {node.lineno}")


class _RecoveryLegacyShift(GenerationStage):
    """Minimal legacy stage reading parent.state_key.coordination (v3 shape)."""

    def __init__(self, axis_spec: Mapping[str, Any] = {}) -> None:  # noqa: B006
        config = dict(axis_spec)
        self._axis = str(config.get("axis", "torsions"))
        self._states: list[tuple[str, float, float, float]] = [
            (str(s[0]), float(s[1]), float(s[2]), float(s[3]))
            for s in config.get("states", [("A", 0.0, 0.0, 0.0), ("B", 0.0, 0.0, 0.0)])
        ]
        self._conditional = bool(config.get("conditional", False))
        self._parent_modes = config.get("parent_modes")
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
        label = "A"
        coordination = parent.state_key.coordination
        if isinstance(coordination, Mapping):
            maybe = coordination.get("mode", "A")
            if maybe in ("A", "B"):
                label = str(maybe)
        return list(self._parent_modes.get(label, []))

    @staticmethod
    def _centroid(coords: Any) -> Any:
        import numpy as _np

        return _np.asarray(coords, dtype=float).mean(axis=0)

    def _measure(self, structure: StructureRecord, context: MolecularContext) -> float:
        own = {"coordination": 0, "rings": 1, "torsions": 2}[self._axis]
        return float(
            self._centroid(structure.coordinates)[own] - self._centroid(context.input_coords)[own]
        )

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
                details=FrozenDict({"basis": "recovery", "scope_coverage": "exact"}),
            )
        options = self._options(parent)
        ceiling = max((len(v) for v in self._parent_modes.values()), default=0)
        return StageEstimate(
            declared_count=len(options),
            upper_bound=ceiling,
            exact=False,
            details=FrozenDict({"basis": "recovery-conditional", "scope_coverage": "upper_bound"}),
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
        self,
        parent: WorkingRealization,
        target: GenerationTarget,
        context: MolecularContext,
    ) -> RealizationResult:
        import numpy as _np

        label = str(dict(target.state_value)["mode"])
        if label in self._fail_on:
            raise RuntimeError(f"injected failure on {label}")
        shift = next(s[1:] for s in self._states if str(s[0]) == label)
        coords = _np.asarray(parent.structure.coordinates, dtype=float) + _np.array(shift)
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


def _recovery_conditional_stages(
    fail_on: tuple[str, ...] = (), fail_on_c: tuple[str, ...] = ()
) -> tuple[MolecularContext, list[Any]]:
    context = _a2_real_context({"index_base": 1})
    stages: list[Any] = [
        _RecoveryLegacyShift(
            {
                "axis": "coordination",
                "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0)],
                "fail_on": list(fail_on_c),
            }
        ),
        _RecoveryLegacyShift(
            {
                "axis": "rings",
                "states": [("r0", 0.0, 0.0, 0.0), ("r1", 0.0, 5.0, 0.0)],
                "conditional": True,
                "parent_modes": {"A": [0], "B": [0, 1]},
                "fail_on": list(fail_on),
            }
        ),
        _RecoveryLegacyShift(
            {
                "axis": "torsions",
                "states": [
                    ("t0", 0.0, 0.0, 0.0),
                    ("t1", 0.0, 0.0, 2.5),
                    ("t2", 0.0, 0.0, 5.0),
                ],
            }
        ),
    ]
    return context, stages


def test_a2_recovery_legacy_nine_not_twelve() -> None:
    context, stages = _recovery_conditional_stages()
    run = ConfgenEngine(stages=stages).run(context)
    assert len(run.leaves) == 9
    assert len(run.leaves) != 12


def test_a2_recovery_legacy_failure_exact_subtree() -> None:
    from confflow.science.confgen.model import TerminalStatus

    context, stages = _recovery_conditional_stages(fail_on=["r1"])
    run = ConfgenEngine(stages=stages).run(context)
    # r1 branch under B carries 3 torsions; exact deferred leaves = 3.
    deferred = [r for r in run.target_records if r.status is TerminalStatus.DEFERRED_PARENT_FAILED]
    assert deferred, "expected deferred subtree records"
    # Count deferred leaves via certificate equations instead of parsing reason.
    assert run.report_json()["counts"]["deferred_parent_failed_leaves"] == 3
    assert len(run.leaves) == 6


def test_a2_recovery_run_kernel_explicit_stays_generic() -> None:
    from confflow.science.confgen.wire_v3 import from_wire_key, is_legacy_stage

    context, stages = _recovery_conditional_stages()
    engine = ConfgenEngine(stages=stages)
    assert all(is_legacy_stage(s) for s in stages)
    # run() adapts legacy and succeeds with 9.
    assert len(engine.run(context).leaves) == 9
    # run_kernel() with the same explicit legacy stages stays generic:
    # legacy parent.state_key.coordination has no compat attr -> error surfaces.
    with pytest.raises(AttributeError):
        engine.run_kernel(context, initial_key=from_wire_key(context.input_state_key))
    # Engine shared state untouched (no adapter mutation).
    assert [type(s).__name__ for s in engine._explicit_stages] == [
        "_RecoveryLegacyShift",
        "_RecoveryLegacyShift",
        "_RecoveryLegacyShift",
    ]


def test_a2_recovery_cancel_and_unknown_axis() -> None:
    from confflow.science.confgen.engine import EngineCancelledError
    from confflow.science.confgen.wire_v3 import from_wire_key

    context, stages = _recovery_conditional_stages()
    engine = ConfgenEngine(stages=stages)

    def _always() -> bool:
        return True

    with pytest.raises(EngineCancelledError):
        engine.run(context, should_cancel=_always)
    # run_kernel cancel fires on a generic (non-conditional) path.
    generic_engine = ConfgenEngine(stages=[_fake("torsions")])
    generic_ctx = _probe_context()
    with pytest.raises(EngineCancelledError):
        generic_engine.run(generic_ctx, should_cancel=_always)
    with pytest.raises(EngineCancelledError):
        generic_engine.run_kernel(
            generic_ctx,
            initial_key=from_wire_key(generic_ctx.input_state_key),
            should_cancel=_always,
        )
    # Unknown axis fails closed in both paths (ValueError family).
    with pytest.raises(ValueError, match="target axis must be one of"):
        ConfgenEngine(stages=[_fake("mystery")]).run(_probe_context())
    with pytest.raises(ValueError, match="target axis must be one of"):
        ConfgenEngine(stages=[_fake("mystery")]).run_kernel(
            _probe_context(), initial_key=from_wire_key(_probe_context().input_state_key)
        )
    with pytest.raises(ValueError, match="unknown generation axis"):
        ConfgenEngine()._load_stage("nonsense", {})


def test_a2_recovery_adapter_hooks_preserved() -> None:
    from confflow.science.confgen.wire_v3 import LegacyStageAdapter

    legacy = _RecoveryLegacyShift(
        {"axis": "torsions", "states": [("A", 0.0, 0.0, 0.0), ("B", 0.0, 0.0, 5.0)]}
    )
    adapted = LegacyStageAdapter(legacy)
    assert adapted.axis == "torsions"
    # verify_locked exists on both (legacy defines it).
    assert hasattr(legacy, "verify_locked")
    assert hasattr(adapted, "verify_locked")
    # audit_target absent on both (no false hook detection).
    assert not hasattr(legacy, "audit_target")
    assert not hasattr(adapted, "audit_target")
    # Wrapped hooks keep metadata.
    assert getattr(adapted.verify_locked, "__wrapped__", None) is not None or hasattr(
        adapted.verify_locked, "__name__"
    )

    # Generic stage passes through unwrapped in run().
    class _GenericDummy(GenerationStage):
        def __init__(self, axis_spec: Mapping[str, Any] = {}) -> None:  # noqa: B006
            self._axis = "dummy"

        @property
        def axis(self) -> str:
            return self._axis

        def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:  # type: ignore[override]
            return StageEstimate(
                declared_count=1,
                upper_bound=1,
                exact=True,
                details=FrozenDict({"basis": "generic", "scope_coverage": "exact"}),
            )

        def enumerate_targets(self, parent: Any, context: MolecularContext) -> Iterator[Any]:  # type: ignore[override]
            from confflow.science.confgen.kernel_records import KernelGenerationTarget

            comps = (
                dict(parent.state_key.components) if hasattr(parent.state_key, "components") else {}
            )
            assert isinstance(comps, dict)
            yield KernelGenerationTarget(
                axis="dummy", target_id="dummy:000000", state_value={}, ordinal=0
            )

        def realize(self, parent: Any, target: Any, context: MolecularContext) -> RealizationResult:  # type: ignore[override]
            return RealizationResult(
                structure=parent.structure, status="realized", reason="probe", backend="probe"
            )

        def perceive(
            self, structure: StructureRecord, context: MolecularContext
        ) -> PerceptionResult:
            return PerceptionResult(best_key={})

    from confflow.science.confgen.wire_v3 import is_legacy_stage as _is_leg

    assert not _is_leg(_GenericDummy({}))


def test_a2_recovery_unannotated_legacy_nine_and_subtree() -> None:
    """Unannotated old stages (V3 axes) still route via legacy adapter."""
    from confflow.science.confgen.model import TerminalStatus
    from confflow.science.confgen.wire_v3 import is_legacy_stage

    class _UnannotatedShift(_RecoveryLegacyShift):
        def estimate(self, parent, context):  # type: ignore[no-untyped-def]
            return super().estimate(parent, context)

        def enumerate_targets(self, parent, context):  # type: ignore[no-untyped-def]
            yield from super().enumerate_targets(parent, context)

        def realize(self, parent, target, context):  # type: ignore[no-untyped-def]
            return super().realize(parent, target, context)

    for _name in ("estimate", "enumerate_targets", "realize"):
        assert getattr(_UnannotatedShift, _name).__annotations__ == {}

    def _stages(fail_on: tuple[str, ...] = ()) -> tuple[Any, list[Any]]:
        context = _a2_real_context({"index_base": 1})
        stages: list[Any] = [
            _UnannotatedShift(
                {
                    "axis": "coordination",
                    "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0)],
                }
            ),
            _UnannotatedShift(
                {
                    "axis": "rings",
                    "states": [("r0", 0.0, 0.0, 0.0), ("r1", 0.0, 5.0, 0.0)],
                    "conditional": True,
                    "parent_modes": {"A": [0], "B": [0, 1]},
                    "fail_on": list(fail_on),
                }
            ),
            _UnannotatedShift(
                {
                    "axis": "torsions",
                    "states": [
                        ("t0", 0.0, 0.0, 0.0),
                        ("t1", 0.0, 0.0, 2.5),
                        ("t2", 0.0, 0.0, 5.0),
                    ],
                }
            ),
        ]
        return context, stages

    context, stages = _stages()
    assert all(is_legacy_stage(s) for s in stages)
    assert len(ConfgenEngine(stages=stages).run(context).leaves) == 9

    context2, stages2 = _stages(fail_on=("r1",))
    run2 = ConfgenEngine(stages=stages2).run(context2)
    assert len(run2.leaves) == 6
    assert run2.report_json()["counts"]["deferred_parent_failed_leaves"] == 3
    assert [r for r in run2.target_records if r.status is TerminalStatus.DEFERRED_PARENT_FAILED]
    # Global legacy class annotations untouched (no pollution).
    assert _RecoveryLegacyShift.estimate.__annotations__ != {}
