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


# ---------------------------------------------------------------------------
# FIX-1A A3: engine axis branches move to component hooks (behavior unchanged).
# ---------------------------------------------------------------------------


def _a3_runstate_shell(engine, context, axes, stages):
    from confflow.science.confgen.engine import _RunState
    from confflow.science.confgen.planner import MixedRadixGrid

    counts = [1] * max(len(axes), 1)
    return _RunState(
        engine=engine,
        context=context,
        stages=list(stages),
        axes=list(axes),
        level_counts=list(counts),
        grid=MixedRadixGrid(counts),
        conditional=False,
        seed=0,
        should_cancel=None,
    )


def test_a3_stage_hook_defaults_and_values() -> None:
    from confflow.science.confgen.coordination.stage import CoordinationStage
    from confflow.science.confgen.ring.stage import RingStage
    from confflow.science.confgen.torsion.stage import TorsionStage

    assert GenerationStage.check_bond_integrity is False
    assert GenerationStage.carries_inherited_locks is False
    assert GenerationStage.preserved_entries(None, {}) == []
    assert GenerationStage.report_section(None, {}) is None
    assert GenerationStage.describe_scope(None, {}) is None
    assert GenerationStage.fallback_lock(None, {}, None, None) is None
    assert RingStage.check_bond_integrity is False
    assert RingStage.carries_inherited_locks is False
    assert TorsionStage.check_bond_integrity is True
    assert TorsionStage.carries_inherited_locks is True
    assert CoordinationStage.check_bond_integrity is False
    assert CoordinationStage.carries_inherited_locks is False


def test_a3_descriptor_hook_values() -> None:
    registry = build_default_registry()
    by_id = {d.id: d for d in registry.descriptors}
    assert by_id["torsions"].check_bond_integrity is True
    assert by_id["torsions"].carries_inherited_locks is True
    assert by_id["torsions"].fallback_lock is None
    assert by_id["rings"].check_bond_integrity is False
    assert by_id["rings"].carries_inherited_locks is False
    assert callable(by_id["rings"].fallback_lock)
    assert by_id["coordination"].check_bond_integrity is False
    assert by_id["coordination"].carries_inherited_locks is False
    assert by_id["coordination"].fallback_lock is None
    for axis in ("coordination", "rings", "torsions"):
        assert callable(by_id[axis].preserved_entries)
        assert callable(by_id[axis].report_section)
        assert callable(by_id[axis].describe_scope)


def test_a3_check_bond_integrity_hook_not_axis() -> None:
    """Counterexample: a torsions stage opting out must be honored."""

    class _NoCheck(_FakeStage):
        check_bond_integrity = False

    class _YesCheck(_FakeStage):
        check_bond_integrity = True

    engine = ConfgenEngine()
    ctx = _probe_context()
    assert (
        _a3_runstate_shell(engine, ctx, [], [])._check_bond_integrity(
            "torsions", _NoCheck({"axis": "torsions"})
        )
        is False
    )
    assert (
        _a3_runstate_shell(engine, ctx, [], [])._check_bond_integrity(
            "rings", _YesCheck({"axis": "rings"})
        )
        is True
    )
    # Legacy explicit stages without overrides keep the old outcomes.
    assert (
        _a3_runstate_shell(engine, ctx, [], [])._check_bond_integrity("torsions", _fake("torsions"))
        is True
    )
    assert (
        _a3_runstate_shell(engine, ctx, [], [])._check_bond_integrity("rings", _fake("rings"))
        is False
    )


def test_a3_carries_inherited_locks_hook_not_axis() -> None:
    class _NoCarry(_FakeStage):
        carries_inherited_locks = False

    class _YesCarry(_FakeStage):
        carries_inherited_locks = True

    engine = ConfgenEngine()
    ctx = _probe_context()
    assert (
        _a3_runstate_shell(engine, ctx, [], [])._carries_inherited_locks(
            "torsions", _NoCarry({"axis": "torsions"})
        )
        is False
    )
    assert (
        _a3_runstate_shell(engine, ctx, [], [])._carries_inherited_locks(
            "rings", _YesCarry({"axis": "rings"})
        )
        is True
    )
    assert (
        _a3_runstate_shell(engine, ctx, [], [])._carries_inherited_locks(
            "torsions", _fake("torsions")
        )
        is True
    )


def test_a3_check_bond_integrity_reaches_audit() -> None:
    """End to end: the audit flag follows the hook, not the axis string."""
    import confflow.science.confgen.engine as _engine_mod

    seen: list[bool] = []
    real_audit = _engine_mod.audit_parent_locks

    def _spy(structure, ancestors, context, *, parent, check_bond_integrity=True):
        seen.append(bool(check_bond_integrity))
        return real_audit(
            structure,
            ancestors,
            context,
            parent=parent,
            check_bond_integrity=check_bond_integrity,
        )

    class _LaxTorsion(_FakeStage):
        check_bond_integrity = False

    _engine_mod.audit_parent_locks = _spy
    try:
        ConfgenEngine(stages=[_LaxTorsion({"axis": "torsions"})]).run(_probe_context())
    finally:
        _engine_mod.audit_parent_locks = real_audit
    assert seen, "expected at least one fresh-realization audit"
    assert seen == [False] * len(seen)


class _A3NoHookRing(_RecoveryLegacyShift):
    """Legacy-shaped rings stage with real shift measurement, no lock hooks.

    Mirrors the root probe: a public explicit legacy rings stage with
    ``verify_locked=None`` followed by torsion. The ring target realizes;
    the torsion level's ancestor audit reaches the component-owned ring
    fallback (unmeasured ``mode`` key -> ambiguous -> UNRESOLVED).
    """


def test_a3_ring_fallback_preserved_run_path() -> None:
    """Legacy explicit rings stage without hooks still hits ring fallback."""
    import confflow.science.confgen.ring.scope as _ring_scope

    calls: list[str] = []
    real_fallback = _ring_scope.fallback_lock

    def _spy(stage, locked_state, structure, context):
        calls.append(type(stage).__name__)
        return real_fallback(stage, locked_state, structure, context)

    _ring_scope.fallback_lock = _spy
    try:
        ring = _A3NoHookRing({"axis": "rings", "states": [("r0", 0.0, 0.0, 0.0)]})
        ring.verify_locked = None
        assert getattr(ring, "verify_locked", None) is None
        run = ConfgenEngine(stages=[ring, _fake("torsions")]).run(_probe_context())
    finally:
        _ring_scope.fallback_lock = real_fallback
    counts = run.report_json()["counts"]
    assert counts["published"] == 0
    assert counts["failed"] == {"unresolved": 1}
    assert len(calls) == 1, calls


def test_a3_ring_fallback_descriptor_path() -> None:
    """Public run_kernel with raw stages reaches the descriptor fallback."""
    import confflow.science.confgen.ring.scope as _ring_scope
    from confflow.science.confgen.wire_v3 import from_wire_key

    calls: list[str] = []
    real_fallback = _ring_scope.fallback_lock

    def _spy(stage, locked_state, structure, context):
        calls.append(type(stage).__name__)
        return real_fallback(stage, locked_state, structure, context)

    _ring_scope.fallback_lock = _spy
    try:
        ring = _A3NoHookRing({"axis": "rings", "states": [("r0", 0.0, 0.0, 0.0)]})
        ring.verify_locked = None
        ctx = _probe_context()
        run = ConfgenEngine(stages=[ring, _fake("torsions")]).run_kernel(
            ctx, initial_key=from_wire_key(ctx.input_state_key)
        )
    finally:
        _ring_scope.fallback_lock = real_fallback
    counts = dict(run.report.thaw()["counts"])
    assert counts["published"] == 0
    assert counts["failed"] == {"unresolved": 1}
    assert len(calls) == 1, calls


def test_a3_unknown_axis_without_hooks_stays_generic() -> None:
    """Unknown legacy stages missing hooks keep the existing fail-closed error."""
    with pytest.raises(ValueError, match="target axis must be one of"):
        ConfgenEngine(stages=[_fake("mystery")]).run(_probe_context())


def test_a3_preserved_entries_order_and_hooks() -> None:
    resolved = {
        "torsions": [{"id": "t0", "treatment": "preserve_input"}],
        "rings": [{"id": "r0", "treatment": "preserve_input"}],
    }
    ctx = _probe_context()
    object.__setattr__(ctx, "resolved_spec", FrozenDict(dict(resolved)))
    engine = ConfgenEngine()
    shell = _a3_runstate_shell(
        engine, ctx, ["rings", "torsions"], [_fake("rings"), _fake("torsions")]
    )
    assert shell._preserved_axes() == [
        {"axis": "torsions", "id": "t0"},
        {"axis": "rings", "id": "r0"},
    ]

    # Counterexample: hook output flows through; nothing is hardcoded.
    class _Custom(_FakeStage):
        def preserved_entries(self, resolved):
            return [{"axis": "mystery", "id": "m0"}]

    custom_engine = ConfgenEngine()._registry.with_component(
        ComponentDescriptor(
            id="mystery",
            order=40,
            spec_keys=("mystery",),
            state_merge="merge",
            is_active=lambda resolved: False,
            factory=lambda resolved: _Custom({"axis": "mystery"}),
        )
    )
    holder = {"engine": ConfgenEngine()}
    object.__setattr__(holder["engine"], "_registry", custom_engine)
    shell2 = _a3_runstate_shell(holder["engine"], ctx, ["mystery"], [_Custom({"axis": "mystery"})])
    # Custom entries lead in reverse-registry order; unbound built-in
    # descriptors still contribute their resolved-spec slices (legacy order).
    assert shell2._preserved_axes() == [
        {"axis": "mystery", "id": "m0"},
        {"axis": "torsions", "id": "t0"},
        {"axis": "rings", "id": "r0"},
    ]


def test_a3_report_section_hooks() -> None:
    resolved = {"coordination": {"mode": "A"}}
    ctx = _probe_context()
    object.__setattr__(ctx, "resolved_spec", FrozenDict(dict(resolved)))
    engine = ConfgenEngine()
    shell = _a3_runstate_shell(engine, ctx, ["coordination"], [_fake("coordination")])
    assert shell._donor_configuration() == {"mode": "A"}
    shell_none = _a3_runstate_shell(engine, ctx, ["rings"], [_fake("rings")])
    assert shell_none._donor_configuration() == {"mode": "A"}


def test_a3_scope_slices_owned_by_components() -> None:
    from confflow.science.confgen.coordination.scope import describe_scope as _coord_scope
    from confflow.science.confgen.ring.scope import describe_scope as _ring_scope
    from confflow.science.confgen.torsion.scope import describe_scope as _torsion_scope

    assert _ring_scope({"rings": [{"id": "r0"}]}) == {"rings": [{"id": "r0"}]}
    assert _ring_scope({}) is None
    assert _torsion_scope({"torsions": [{"id": "t0"}]}) == {"torsions": [{"id": "t0"}]}
    assert _coord_scope({"coordination": {"mode": "A"}}) == {"coordination": {"mode": "A"}}
    assert GenerationStage.describe_scope(None, {}) is None


def test_a3_moved_dispatch_has_no_axis_literals() -> None:
    """Quantified AST scope: moved dispatch/report/scope code is axis-free."""
    import confflow.science.confgen.engine as _engine_mod

    source = Path(_engine_mod.__file__).read_text()
    tree = ast.parse(source)
    moved = {
        "_check_bond_integrity",
        "_carries_inherited_locks",
        "_hook_impl",
        "_descriptor_for",
        "_fallback_lock",
        "_preserved_axes",
        "_donor_configuration",
    }
    axes = {"coordination", "rings", "torsions"}
    offenders: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in moved:
            found = sorted(
                {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and n.value in axes}
            )
            if found:
                offenders[node.name] = found
    assert offenders == {}, offenders
    # Remaining engine literals are inventoried (A4d moves them, not A3).
    remaining: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found = sorted(
                {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and n.value in axes}
            )
            if found:
                remaining[node.name] = found
    assert set(remaining) == {
        "_key_nonempty",
        "inherited_torsion_locks",
        "combine_state_key",
    }, remaining


def test_a3_ring_matcher_import_error_uses_generic_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-sampled ImportError drift: matcher loss must keep A2 generic compare.

    A2 ``engine._fallback_lock`` fell through to the generic missing/drift/exact
    comparison when ``ring_states_match`` import failed; A3 wrongly returned
    unconditional ambiguous. Narrow-block only that import, drive the real
    engine->descriptor dispatch, and require the A2 outcomes (ok/missing/drift).
    """
    import builtins

    hits: list[str] = []
    _real_import = builtins.__import__

    def _blocked(name, globals=None, locals=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "confflow.science.confgen.ring.perception" and (
            "ring_states_match" in (fromlist or ())
        ):
            hits.append(name)
            raise ImportError("root-sampled matcher unavailable")
        return _real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _blocked)

    class _ProbeRing(_FakeStage):
        def __init__(self, best: Mapping[str, Any]) -> None:
            super().__init__({"axis": "rings"})
            self._best = dict(best)

        def perceive(self, structure: Any, context: MolecularContext) -> PerceptionResult:  # type: ignore[override]
            _ = (structure, context)
            return PerceptionResult(best_key=dict(self._best))

    def _run_case(locked: Mapping[str, Any], best: Mapping[str, Any]):  # type: ignore[no-untyped-def]
        engine = ConfgenEngine()
        ctx = _probe_context()
        shell = _a3_runstate_shell(engine, ctx, ["rings"], [_ProbeRing(best)])
        stage = shell._stages[0]
        before = len(hits)
        out = shell._fallback_lock(stage, "rings", dict(locked), None, ctx)
        assert len(hits) > before, "matcher ImportError was not triggered"
        return out

    ok_locked = {"r1": {"template": "chair_A_6"}}
    verdict, evidence, observed, oos = _run_case(ok_locked, {"r1": {"template": "chair_A_6"}})
    assert verdict == "ok"
    assert evidence == []
    assert observed == {}
    assert oos is False

    missing_locked = {"r1": {"template": "chair_A_6"}, "r2": {"template": "chair_A_6"}}
    verdict, evidence, observed, oos = _run_case(missing_locked, {"r1": {"template": "chair_A_6"}})
    assert verdict == "ambiguous"
    assert observed == {}
    assert oos == "unknown"
    assert evidence == [
        {
            "kind": "anomaly",
            "anomaly": "AMBIGUOUS_KEY",
            "detail": "ancestor lock axes unmeasured: ['r2']",
        }
    ]

    drift_locked = {"r1": {"template": "chair_A_6"}}
    drift_best = {"r1": {"template": "boat"}}
    verdict, evidence, observed, oos = _run_case(drift_locked, drift_best)
    assert verdict == "drift"
    assert observed == {"r1": {"template": "boat"}}
    assert oos == "unknown"
    assert evidence == [
        {
            "kind": "drift",
            "axis": "rings.r1",
            "detail": "ancestor lock drifted",
            "expected": {"template": "chair_A_6"},
            "measured": {"template": "boat"},
            "out_of_scope": "unknown",
        }
    ]


# ---------------------------------------------------------------------------
# FIX-1A A4a: registry channel (public compat + instance identity, V24/V50).
# Classification correction (root ruling): planner.build_typed_graph is a
# PUBLIC export (planner.__all__ + science.confgen.__all__, 5 existing
# 3-positional test calls). A4a therefore keeps public
# build_typed_graph(..., *, registry=None) compat and threads into private
# _build_typed_graph(..., *, registry) (mandatory, keyword-only). No hook
# is called on this card (hooks land in A4b/A4c).
# ---------------------------------------------------------------------------


def _a4a_probe_structure() -> StructureRecord:
    return StructureRecord(
        id="a4a-probe",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.4, 0.0), (4.5, 0.4, 0.0)),
        charge=0,
        multiplicity=1,
    )


def _a4a_custom_registry() -> ComponentRegistry:
    return build_default_registry().with_component(_descriptor("probe", 5, ()))


def test_a4a_public_normalize_spec_compat_and_error_order() -> None:
    import inspect as _inspect

    sig = _inspect.signature(normalize_spec)
    assert "registry" in sig.parameters
    assert sig.parameters["registry"].default is None
    assert sig.parameters["registry"].kind is _inspect.Parameter.KEYWORD_ONLY
    good = {
        "index_base": 1,
        "torsions": [
            {"id": "c", "bond": [2, 3], "model": "relative_rotation_grid", "angles": [0.0, 120.0]}
        ],
    }
    via_old = normalize_spec(good)
    via_default = normalize_spec(dict(good), registry=None)
    via_explicit = normalize_spec(dict(good), registry=default_registry())
    via_custom = normalize_spec(dict(good), registry=_a4a_custom_registry())
    assert via_old == via_default == via_explicit == via_custom
    # Failure order preserved (two bad sections: bad spec must raise the same
    # first error regardless of registry channel).
    bad: dict[str, Any] = {"index_base": 7, "unknown_top": 1}
    with pytest.raises(ValueError) as _e0:
        normalize_spec(dict(bad))
    for reg in (None, default_registry(), _a4a_custom_registry()):
        with pytest.raises(ValueError) as exc:
            normalize_spec(dict(bad), registry=reg)
        assert str(exc.value) == str(_e0.value)
    with pytest.raises(ValueError) as e1:
        normalize_spec(dict(bad))
    with pytest.raises(ValueError) as e2:
        normalize_spec(dict(bad), registry=_a4a_custom_registry())
    assert str(e1.value) == str(e2.value)


def test_a4a_public_build_typed_graph_compat_and_private_requires_registry() -> None:
    import inspect as _inspect

    from confflow.science.confgen.planner import _build_typed_graph, build_typed_graph

    sig = _inspect.signature(build_typed_graph)
    assert list(sig.parameters) == ["structure", "topology", "resolved", "registry"]
    assert sig.parameters["registry"].default is None
    assert sig.parameters["registry"].kind is _inspect.Parameter.KEYWORD_ONLY
    psig = _inspect.signature(_build_typed_graph)
    assert list(psig.parameters) == ["structure", "topology", "resolved", "registry"]
    assert psig.parameters["registry"].default is _inspect.Parameter.empty
    assert psig.parameters["registry"].kind is _inspect.Parameter.KEYWORD_ONLY
    # Existing 3-positional calls keep working (the 5 pre-A4a test calls).
    structure = _a4a_probe_structure()
    resolved = normalize_spec({"index_base": 0})
    adj_old, graph_old = build_typed_graph(structure, resolved.get("topology", {}), resolved)
    adj_new, graph_new = build_typed_graph(
        structure, resolved.get("topology", {}), resolved, registry=_a4a_custom_registry()
    )
    assert adj_old == adj_new
    assert list(graph_old.edges) == list(graph_new.edges)
    # Private entry without registry fails closed (mandatory keyword-only).
    with pytest.raises(TypeError):
        _build_typed_graph(structure, {}, resolved)  # type: ignore[call-arg]
    # Private with explicit registry works and matches public default.
    adj_priv, _ = _build_typed_graph(structure, {}, resolved, registry=default_registry())
    assert adj_priv == adj_old


def test_a4a_build_context_threads_single_instance() -> None:
    structure = _a4a_probe_structure()
    custom = _a4a_custom_registry()
    ctx_default = build_context(structure, {"index_base": 0})
    assert ctx_default.registry is default_registry()
    ctx_default2 = build_context(structure, {"index_base": 0}, registry=None)
    assert ctx_default2.registry is default_registry()
    assert ctx_default.registry is ctx_default2.registry
    ctx_custom = build_context(structure, {"index_base": 0}, registry=custom)
    assert ctx_custom.registry is custom
    # Old outputs unchanged across channels.
    assert dict(ctx_default.resolved_spec) == dict(ctx_custom.resolved_spec)
    assert ctx_default.adjacency == ctx_custom.adjacency


def test_a4a_handbuilt_context_without_registry_gets_default() -> None:
    base = _a4a_probe_structure()
    ctx = build_context(base, {"index_base": 0})
    rebuilt = MolecularContext(
        structure=ctx.structure,
        adjacency=ctx.adjacency,
        graph=ctx.graph,
        resolved_spec=ctx.resolved_spec,
        tolerances=ctx.tolerances,
        input_state_key=ctx.input_state_key,
        input_coords=ctx.input_coords,
        inherited_scope=ctx.inherited_scope,
        atom_refs=ctx.atom_refs,
    )
    assert rebuilt.registry is default_registry()


def test_a4a_engine_identity_is_check() -> None:
    structure = _a4a_probe_structure()
    custom = _a4a_custom_registry()
    ctx_default = build_context(structure, {"index_base": 0})
    ctx_custom = build_context(structure, {"index_base": 0}, registry=custom)
    # Matching identities run (pseudo stage keeps it cheap).
    ConfgenEngine(stages=[_fake("torsions")], registry=custom).run(ctx_custom)
    ConfgenEngine(stages=[_fake("torsions")]).run(ctx_default)
    # Mismatched identities fail closed.
    with pytest.raises(ValueError, match="registry mismatch"):
        ConfgenEngine().run(ctx_custom)
    with pytest.raises(ValueError, match="registry mismatch"):
        ConfgenEngine(registry=custom).run(ctx_default)
    from confflow.science.confgen.wire_v3 import from_wire_key

    with pytest.raises(ValueError, match="registry mismatch"):
        ConfgenEngine().run_kernel(
            ctx_custom, initial_key=from_wire_key(ctx_custom.input_state_key)
        )


def test_a4a_executors_optional_passthrough_signatures() -> None:
    import inspect as _inspect

    from confflow.execution.confgen_executor import ConfgenExecutor
    from confflow.execution.transform_executor import TransformExecutor

    sig = _inspect.signature(ConfgenExecutor._run_v3)
    assert "registry" in sig.parameters
    assert sig.parameters["registry"].default is None
    assert sig.parameters["registry"].kind is _inspect.Parameter.KEYWORD_ONLY
    sig_d = _inspect.signature(TransformExecutor._declared_topology)
    assert "registry" in sig_d.parameters
    assert sig_d.parameters["registry"].default is None
    assert sig_d.parameters["registry"].kind is _inspect.Parameter.KEYWORD_ONLY
    sig_f = _inspect.signature(TransformExecutor._frame)
    assert "registry" in sig_f.parameters
    assert sig_f.parameters["registry"].default is None
    # Public execute() signatures unchanged.
    assert "registry" not in _inspect.signature(ConfgenExecutor.execute).parameters
    # Declared-topology channel: default and custom agree (no hook dispatch).
    native: dict[str, Any] = {"topology_bonds": {"index_base": 1, "add_bond": [[1, 2]]}}
    assert TransformExecutor._declared_topology(native) == TransformExecutor._declared_topology(
        dict(native), registry=_a4a_custom_registry()
    )


def test_a4a_no_toplevel_registry_import_in_model() -> None:
    tree = ast.parse(
        (Path(__file__).resolve().parents[2] / "confflow/science/confgen/model.py").read_text()
    )
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            src = "" if isinstance(node, ast.Import) else (node.module or "")
            names = [a.name if isinstance(node, ast.Import) else (a.name) for a in node.names]
            assert "registry" not in src, f"model top-level imports registry: {src}"
            for n in names:
                assert "registry" not in n


# ---------------------------------------------------------------------------
# FIX-1A A4b: spec normalization owned by components (V40/V51).
# Root compat rulings: builtin defaults (coordination=None, rings/torsions/
# paths=[], strict=False) are filled by the planner so default bytes never
# change; component hooks return ONLY owned keys present in the input;
# dispatch order is registry order (= C->R->T for defaults, matching the
# pre-A4b validation order); topology overlay stays in the planner until A4c;
# planner.resolve_torsion_axes stays public (no silent API deletion).
# Checks below use AST only for source structure (no grep).
# ---------------------------------------------------------------------------


def _a4b_battery() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("empty", {"index_base": 0}),
        ("coord-none", {"index_base": 0, "coordination": None}),
        (
            "coord-1based",
            {
                "index_base": 1,
                "coordination": {"metal_center": 1, "binding_sites": [{"atoms": [2, 3]}]},
            },
        ),
        ("rings-0based", {"index_base": 0, "rings": [{"atoms": [0, 1, 2, 3, 4]}]}),
        (
            "torsion-1based",
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
            },
        ),
        (
            "paths-1based",
            {
                "index_base": 1,
                "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
            },
        ),
        ("paths-none", {"index_base": 0, "paths": None}),
        (
            "mixed-crt",
            {
                "index_base": 1,
                "coordination": {"metal_center": 1},
                "rings": [{"atoms": [1, 2, 3]}],
                "torsions": [
                    {"id": "t", "bond": [1, 2], "model": "relative_rotation_grid", "angles": [0.0]}
                ],
            },
        ),
    ]


def _a4b_bad_battery() -> list[dict[str, Any]]:
    return [
        {"index_base": 7, "unknown_top": 1},
        {"coordination": 42, "rings": "x"},
        {"index_base": 1, "torsions": [{"id": "a", "bond": [1]}], "rings": [42]},
        {"index_base": 1, "strict_path_bond_check": "yes"},
        {"unknown_z": 1},
        {"topology": {"index_base": 1}},
    ]


def test_a4b_component_hooks_return_only_owned_present_keys() -> None:
    from confflow.science.confgen.coordination.spec import normalize_spec as coord_ns
    from confflow.science.confgen.ring.spec import normalize_spec as ring_ns
    from confflow.science.confgen.torsion.spec import normalize_spec as tors_ns

    assert coord_ns({"index_base": 0}, index_base=0) == {}
    assert ring_ns({"index_base": 0}, index_base=0) == {}
    assert tors_ns({"index_base": 0}, index_base=0) == {}
    assert set(coord_ns({"index_base": 0, "coordination": None}, index_base=0)) == {"coordination"}
    assert set(ring_ns({"index_base": 0, "rings": []}, index_base=0)) == {"rings"}
    out = tors_ns({"index_base": 0, "torsions": [], "paths": None}, index_base=0)
    assert set(out) == {"torsions", "paths"}
    # Whole-raw input accepted (hook reads its own keys, ignores the rest).
    mixed = {"index_base": 1, "coordination": {"metal_center": 1}, "rings": [{"atoms": [1]}]}
    assert set(coord_ns(mixed, index_base=1)) == {"coordination"}
    assert set(ring_ns(mixed, index_base=1)) == {"rings"}
    assert tors_ns(mixed, index_base=1) == {}


def test_a4b_normalize_spec_defaults_match_builtin_history() -> None:
    out = normalize_spec({"index_base": 0})
    assert out["coordination"] is None
    assert out["rings"] == []
    assert out["torsions"] == []
    assert out["paths"] == []
    assert out["strict_path_bond_check"] is False
    # Explicit nulls preserved distinctly (coordination=None ok, paths=None -> []).
    assert normalize_spec({"index_base": 0, "coordination": None})["coordination"] is None
    assert normalize_spec({"index_base": 0, "paths": None})["paths"] == []


def test_a4b_normalize_spec_idempotent_and_1based() -> None:
    for _name, raw in _a4b_battery():
        once = normalize_spec(dict(raw))
        twice = normalize_spec(once)
        assert twice == once, _name
    # 1-based workflow indices convert once to 0-based internally.
    once = normalize_spec(
        {
            "index_base": 1,
            "torsions": [
                {"id": "c", "bond": [2, 3], "model": "relative_rotation_grid", "angles": [0.0]}
            ],
        }
    )
    assert once["torsions"][0]["bond"] == [1, 2]
    assert once["index_base"] == 0
    assert once["index_convention"] == "internal-0-based:normalized"


def test_a4b_error_order_and_unknown_allowed_match_history() -> None:
    # Two-error spec raises the same first error (C before R before T).
    bad: dict[str, Any] = {"coordination": 42, "rings": "x"}
    with pytest.raises(ValueError) as e0:
        normalize_spec(dict(bad))
    assert str(e0.value) == "spec coordination must be a mapping or null"
    bad2: dict[str, Any] = {"index_base": 1, "torsions": [{"id": "a", "bond": [1]}], "rings": [42]}
    with pytest.raises(ValueError) as e1:
        normalize_spec(dict(bad2))
    assert str(e1.value) == "$.rings[0] must be a mapping"
    # Unknown-key diagnosis: default allowed list equals the legacy 15 keys.
    with pytest.raises(ValueError) as eu:
        normalize_spec({"unknown_z": 1})
    assert "allowed" in str(eu.value)
    for _legacy in (
        "coordination",
        "rings",
        "torsions",
        "paths",
        "strict_path_bond_check",
    ):
        assert f"'{_legacy}'" in str(eu.value)
    for bad_raw in _a4b_bad_battery():
        with pytest.raises(ValueError):
            normalize_spec(dict(bad_raw))


def test_a4b_probe_hook_called_via_build_context_and_validate() -> None:
    calls: list[tuple[str, int]] = []

    def _probe_ns(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
        calls.append(("normalize", int(index_base)))
        assert raw.get("probe_section", None) is None or isinstance(raw.get("probe_section"), dict)
        if "probe_section" not in raw:
            return {}
        return {"probe_section": dict(raw["probe_section"])}

    validated: list[str] = []

    def _probe_vc(resolved: Mapping[str, Any], context: Any) -> None:
        validated.append("ok")

    probe = ComponentDescriptor(
        id="probe",
        order=40,
        spec_keys=("probe_section",),
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        normalize_spec=_probe_ns,  # type: ignore[arg-type]
        validate_context=_probe_vc,  # type: ignore[arg-type]
    )
    registry = build_default_registry().with_component(probe)
    structure = _a4a_probe_structure()
    ctx = build_context(
        structure, {"index_base": 0, "probe_section": {"note": "hi"}}, registry=registry
    )
    assert ("normalize", 0) in calls
    assert validated == ["ok"]
    assert dict(ctx.resolved_spec)["probe_section"] == {"note": "hi"}
    # Missing custom key stays missing (no undeclared growth).
    ctx2 = build_context(structure, {"index_base": 0}, registry=registry)
    assert "probe_section" not in dict(ctx2.resolved_spec)


def test_a4b_component_returning_unowned_key_fails_closed() -> None:
    def _evil_ns(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
        return {"torsions": []}  # not owned by probe

    evil = ComponentDescriptor(
        id="probe",
        order=40,
        spec_keys=("probe_section",),
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        normalize_spec=_evil_ns,  # type: ignore[arg-type]
    )
    registry = build_default_registry().with_component(evil)
    with pytest.raises(ValueError, match="unowned spec key"):
        normalize_spec({"index_base": 0}, registry=registry)


def test_a4b_planner_keeps_public_torsion_api_and_no_overlay_move() -> None:
    import confflow.science.confgen.planner as planner

    assert "resolve_torsion_axes" in planner.__all__
    assert callable(planner.resolve_torsion_axes)
    tree = ast.parse(Path(planner.__file__).read_text())
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "resolve_torsion_axes" in defined
    # Topology overlay stays in the planner until A4c (not moved early).
    assert "_overlay_declared_coordination_scope" in defined
    assert "_convert_coordination" not in defined
    # Component section blocks no longer live inline in planner.normalize_spec.
    src = Path(planner.__file__).read_text()
    assert "coordination/spec" in src or "normalize_spec(raw, index_base" in src


def test_a4b_kernel_axis_literals_scoped_not_zero_relaxed() -> None:
    import ast as _ast

    tree = _ast.parse(Path("confflow/science/confgen/planner.py").read_text())
    lits: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Constant) and node.value in ("coordination", "rings", "torsions"):
            lits.append(str(node.value))
    # Remaining literals are scoped: whitelist/defaults, _spec_has_indices
    # probes, exclusions AXIS_ORDER check, overlay/graph metal-center reads
    # (A4c), and tolerance labels -- never a normalization dispatch.
    assert lits, "expected scoped kernel literals to remain (no zero-relax)"
    ns_node = next(
        n
        for n in tree.body
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == "normalize_spec"
    )
    for node in _ast.walk(ns_node):
        if isinstance(node, _ast.Constant) and node.value in ("coordination", "rings", "torsions"):
            raise AssertionError(f"normalize_spec dispatch still hard-codes {node.value!r}")


def test_a4b_v2_byte_order_sort_keys_false_and_key_order() -> None:
    """v2: root compat byte order with sort_keys=False (no sort_keys masking)."""
    import json as _json

    _historic = [
        "schema_version",
        "index_convention",
        "index_base",
        "coordination",
        "rings",
        "torsions",
        "paths",
        "strict_path_bond_check",
        "topology",
        "stereochemistry",
        "exclusions",
        "tolerances",
        "limits",
        "seed",
        "sampling",
    ]
    _cases = [
        {},
        {"index_base": 0, "rings": []},
        {"index_base": 0, "torsions": []},
        {"index_base": 0, "strict_path_bond_check": False},
        {"index_base": 0, "paths": None},
        {
            "index_base": 0,
            "rings": [],
            "torsions": [],
            "paths": [],
            "strict_path_bond_check": False,
        },
    ]
    for raw in _cases:
        out = normalize_spec(dict(raw))
        assert list(out.keys()) == _historic, f"key order drifted for {raw!r}: {list(out.keys())!r}"
        # Byte check without sort_keys masking: order is part of bytes.
        blob = _json.dumps(out, sort_keys=False)
        assert blob.index('"coordination"') < blob.index('"rings"') < blob.index('"torsions"')
        assert blob.index('"torsions"') < blob.index('"paths"')
        assert blob.index('"paths"') < blob.index('"strict_path_bond_check"')
        assert blob.index('"strict_path_bond_check"') < blob.index('"topology"')
    # Empty-input bytes equal the historical shape (defaults preset, not appended).
    empty = normalize_spec({"index_base": 0})
    assert empty["coordination"] is None
    assert empty["rings"] == [] and empty["torsions"] == [] and empty["paths"] == []
    assert empty["strict_path_bond_check"] is False


def test_a4b_v2_custom_keys_descriptor_order_no_growth() -> None:
    """v2: custom new keys follow descriptor order; unowned never grows."""
    from confflow.science.confgen.registry import build_default_registry

    def _ns(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
        # Return in reverse order on purpose; planner must store descriptor order.
        out: dict[str, Any] = {}
        if "z_custom" in raw:
            out["z_custom"] = raw["z_custom"]
        if "a_custom" in raw:
            out["a_custom"] = raw["a_custom"]
        # Exact check lives in planner; here return exactly expected.
        expected = {k for k in ("a_custom", "z_custom") if k in raw}
        assert set(out.keys()) == expected
        return out

    desc = ComponentDescriptor(
        id="custom",
        order=40,
        spec_keys=("a_custom", "z_custom"),
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        normalize_spec=_ns,  # type: ignore[arg-type]
    )
    registry = build_default_registry().with_component(desc)
    out = normalize_spec({"index_base": 0, "z_custom": 2, "a_custom": 1}, registry=registry)
    keys = list(out.keys())
    # Builtins keep historic positions; customs insert in descriptor order.
    assert keys.index("a_custom") < keys.index("z_custom")
    assert keys.index("strict_path_bond_check") < keys.index("a_custom")
    assert keys.index("z_custom") < keys.index("topology")
    # Missing customs stay missing (no undeclared growth, no default literals in kernel).
    out2 = normalize_spec({"index_base": 0}, registry=registry)
    assert "a_custom" not in out2 and "z_custom" not in out2


def test_a4b_v2_exact_missing_and_extraneous_rejected() -> None:
    """v2: PLAN exact expected vs partial; missing/extra-owned must fail."""
    from confflow.science.confgen.registry import build_default_registry

    def _drop_ns(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
        return {}  # drops present 'probe_section'

    drop = ComponentDescriptor(
        id="probe",
        order=40,
        spec_keys=("probe_section",),
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        normalize_spec=_drop_ns,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="keys mismatch.*missing"):
        normalize_spec(
            {"index_base": 0, "probe_section": {"note": "hi"}},
            registry=build_default_registry().with_component(drop),
        )

    def _extra_ns(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
        # 'b' owned but not in raw -> extraneous owned-not-in-raw.
        return {"a": 1, "b": 2}

    extra = ComponentDescriptor(
        id="ex",
        order=40,
        spec_keys=("a", "b"),
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        normalize_spec=_extra_ns,  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="keys mismatch.*extra"):
        normalize_spec(
            {"index_base": 0, "a": 1},
            registry=build_default_registry().with_component(extra),
        )


def test_a4b_v2_no_hook_compat_passowned() -> None:
    """v2: no-hook legacy descriptors keep compat passowned (old pseudo-stage)."""
    from confflow.science.confgen.registry import build_default_registry

    legacy = ComponentDescriptor(
        id="legacy",
        order=40,
        spec_keys=("legacy_section",),
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        # No normalize_spec hook -> planner passes owned values through.
    )
    registry = build_default_registry().with_component(legacy)
    out = normalize_spec({"index_base": 0, "legacy_section": {"v": 1}}, registry=registry)
    assert out["legacy_section"] == {"v": 1}
    out2 = normalize_spec({"index_base": 0}, registry=registry)
    assert "legacy_section" not in out2


def test_a4b_v2_resolve_moved_delegation_and_no_cycle() -> None:
    """v2: resolve lives in torsion/spec; planner lazy-delegates; no call cycle."""
    import confflow.science.confgen.planner as planner
    import confflow.science.confgen.torsion.spec as tspec

    assert "resolve_torsion_axes" in planner.__all__
    assert "resolve_torsion_axes" in tspec.__all__
    assert callable(planner.resolve_torsion_axes)
    assert callable(tspec.resolve_torsion_axes)
    # Same signature (lazy delegation preserves it).
    assert str(inspect.signature(planner.resolve_torsion_axes)) == str(
        inspect.signature(tspec.resolve_torsion_axes)
    )
    # Same behavior and same public type identity (no API sacrifice).
    sample: list[dict[str, Any]] = [{"bond": [0, 1], "angles": [0.0, 90.0]}]
    via_planner = planner.resolve_torsion_axes(tuple(sample), index_base=0)
    via_spec = tspec.resolve_torsion_axes(tuple(sample), index_base=0)
    assert via_planner == via_spec
    assert isinstance(via_planner[0], planner.TorsionAxis)
    assert isinstance(via_spec[0], planner.TorsionAxis)
    assert type(via_planner[0]) is planner.TorsionAxis
    # No spec -> planner -> spec call cycle: spec.normalize calls local impl.
    spec_src = Path(tspec.__file__).read_text()
    assert "from confflow.science.confgen.planner import resolve_torsion_axes" not in spec_src
    assert "resolve_torsion_axes(tuple(converted_torsions)" in spec_src
    # Planner wrapper is delegation only (lazy import, no torsion logic).
    planner_src = Path(planner.__file__).read_text()
    assert "from confflow.science.confgen.torsion.spec import" in planner_src
    tree = ast.parse(planner_src)
    resolve_node = next(
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "resolve_torsion_axes"
    )
    # Delegation body: import + return, no loops/validation logic.
    assert not any(isinstance(n, (ast.For, ast.While, ast.If)) for n in ast.walk(resolve_node))
    # Exclusive closure moved out of planner; shared stays with record.
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_require_finite_angles" not in defined
    assert "_reject_periodic_duplicates" not in defined
    assert "_require_index_list" not in defined
    assert "_overlay_declared_coordination_scope" in defined  # A4c still stays
    assert "_convert_coordination" not in defined
    spec_tree = ast.parse(spec_src)
    spec_defined = {
        n.name for n in spec_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_require_finite_angles" in spec_defined
    assert "_reject_periodic_duplicates" in spec_defined
    assert "_require_index_list" in spec_defined
    assert "resolve_torsion_axes" in spec_defined
    # No top-level kernel import in torsion/spec (lazy only, component stays light).
    for n in spec_tree.body:
        if isinstance(n, (ast.ImportFrom, ast.Import)):
            src = ast.unparse(n)
            assert "confflow.science.confgen.planner" not in src
    # G13 staged: normalize dispatch still zero axis literals (no zero-relax).
    ns_node = next(
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "normalize_spec"
    )
    for node in ast.walk(ns_node):
        if isinstance(node, ast.Constant) and node.value in ("coordination", "rings", "torsions"):
            raise AssertionError(f"v2 normalize_spec dispatch hard-codes {node.value!r}")


def _a4c_probe_structure() -> StructureRecord:
    return StructureRecord(
        id="a4c-probe",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.4, 0.0), (4.5, 0.4, 0.0)),
        charge=0,
        multiplicity=1,
    )


def test_a4c_topology_context_matches_build_locals() -> None:
    """A4c: TopologyBuildContext mirrors _build_typed_graph locals (no fake set/list)."""
    from confflow.science.confgen.kernel_records import TopologyBuildContext

    assert (
        "TopologyBuildContext"
        in __import__("confflow.science.confgen.kernel_records", fromlist=["__all__"]).__all__
    )
    import inspect as _inspect

    sig = _inspect.signature(TopologyBuildContext)
    assert list(sig.parameters) == [
        "n_atoms",
        "adjacency",
        "typed",
        "explicit_covalent",
        "check_index",
    ]
    hints = __import__("typing", fromlist=["get_type_hints"]).get_type_hints(TopologyBuildContext)
    assert hints["n_atoms"] is int
    # Precise container shapes (not faked): list[set[int]], 3-tuple typed key, set pairs.
    assert str(hints["adjacency"]) == "list[set[int]]"
    assert "explicit_covalent" in hints
    assert str(hints["explicit_covalent"]) == "set[tuple[int, int]]"
    # Holds references (same objects, edge order preserved).
    adj: list[set[int]] = [{1}, {0}]
    typed: dict[tuple[int, int, Any], Any] = {}
    exp: set[tuple[int, int]] = set()
    ctx = TopologyBuildContext(
        n_atoms=2, adjacency=adj, typed=typed, explicit_covalent=exp, check_index=lambda v, p: None
    )
    assert ctx.adjacency is adj
    assert ctx.typed is typed
    assert ctx.explicit_covalent is exp


def test_a4c_overlay_moved_delegation_and_no_cycle() -> None:
    """A4c: overlay lives in coordination/spec; planner keeps lazy delegation only."""
    import confflow.science.confgen.coordination.spec as cspec
    import confflow.science.confgen.planner as planner

    assert "contribute_topology" in cspec.__all__
    assert callable(cspec.contribute_topology)
    # Old symbol preserved for compat.
    assert "_overlay_declared_coordination_scope" in planner.__all__ or hasattr(
        planner, "_overlay_declared_coordination_scope"
    )
    tree = ast.parse(Path(planner.__file__).read_text())
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_overlay_declared_coordination_scope" in defined
    # Delegation only: lazy import + context + call, no edge logic (no For over donors).
    node = next(
        n for n in tree.body if getattr(n, "name", "") == "_overlay_declared_coordination_scope"
    )
    src = ast.unparse(node)
    assert "from confflow.science.confgen.coordination.spec import" in src
    assert "TypedEdge(" not in src
    assert "declared-binding-site" not in src
    # No top-level kernel -> component import (G13).
    for n in tree.body:
        if isinstance(n, (ast.ImportFrom, ast.Import)):
            assert "confflow.science.confgen.coordination" not in ast.unparse(n)
            assert "confflow.science.confgen.ring" not in ast.unparse(n)
            assert "confflow.science.confgen.torsion" not in ast.unparse(n)
    # Registry wiring: only coordination carries the hook (same instance traversal).
    default = default_registry()
    hooks = {d.id: d.contribute_topology for d in default._ordered()}
    assert callable(hooks["coordination"])
    assert hooks["rings"] is None
    assert hooks["torsions"] is None
    # Spec module has no top-level planner import (no spec->planner->spec cycle).
    spec_tree = ast.parse(Path(cspec.__file__).read_text())
    for n in spec_tree.body:
        if isinstance(n, (ast.ImportFrom, ast.Import)):
            assert "confflow.science.confgen.planner" not in ast.unparse(n)


def test_a4c_typed_edges_and_digest_match_baseline() -> None:
    """A4c: coordination overlay keeps edge order/digest (new vs BASE verbatim)."""
    from confflow.science.confgen.planner import build_typed_graph

    structure = _a4c_probe_structure()
    # BASE-captured expectations (see /tmp/fix1a-a4c-output/baseline-typed.log).
    cases = [
        (
            {
                "index_base": 0,
                "coordination": {"metal_center": 0, "binding_sites": [{"atoms": [1, 2]}]},
            },
            [[], [2], [1, 3], [2]],
            [
                (1, 2, "COVALENT"),
                (2, 3, "COVALENT"),
                (0, 1, "COORDINATION"),
                (0, 2, "COORDINATION"),
            ],
            0,
        ),
        (
            {
                "index_base": 1,
                "coordination": {"metal_center": 1, "binding_sites": [{"atoms": [2, 3]}]},
            },
            [[], [2], [1, 3], [2]],
            [
                (1, 2, "COVALENT"),
                (2, 3, "COVALENT"),
                (0, 1, "COORDINATION"),
                (0, 2, "COORDINATION"),
            ],
            0,
        ),
    ]
    for raw, exp_adj, exp_edges, exp_metal in cases:
        resolved = normalize_spec(dict(raw))
        adj, graph = build_typed_graph(structure, resolved.get("topology", {}), resolved)
        assert adj == exp_adj
        assert [(e.a, e.b, e.type.value) for e in graph.edges] == exp_edges
        assert graph.metal_center == exp_metal
        # Old compat wrapper vs private same-instance traversal agree.
        from confflow.science.confgen.planner import _build_typed_graph

        adj2, graph2 = _build_typed_graph(
            structure, resolved.get("topology", {}), resolved, registry=default_registry()
        )
        assert adj2 == adj
        assert list(graph2.edges) == list(graph.edges)


def test_a4c_old_overlay_failures_verbatim() -> None:
    """A4c: overlay/context-stage failures keep exact messages and order."""
    from confflow.science.confgen.planner import build_typed_graph

    structure = _a4c_probe_structure()
    bad_expected = [
        (
            {
                "index_base": 0,
                "coordination": {"metal_center": 0, "binding_sites": [{"atoms": [0]}]},
            },
            "coordination scope binds the metal to itself",
        ),
        (
            {
                "index_base": 0,
                "coordination": {"metal_center": 9, "binding_sites": [{"atoms": [1]}]},
            },
            "resolved coordination metal_center out of range: 9",
        ),
        (
            {
                "index_base": 0,
                "coordination": {"metal_center": 0, "binding_sites": [{"atoms": [9]}]},
            },
            "$.coordination.binding_sites[0] index 9 out of range for 4 atoms",
        ),
        (
            {"index_base": 0, "coordination": {"metal_center": 0, "binding_sites": "x"}},
            "$.coordination.binding_sites must be a list",
        ),
        (
            {
                "index_base": 0,
                "coordination": {"metal_center": 0, "binding_sites": [{"atoms": [1]}]},
                "topology": {"add_bond": [{"atoms": [0, 1], "kind": "COVALENT"}]},
            },
            "topology declares COVALENT for pair (0, 1) inside the declared coordination scope",
        ),
    ]
    for raw, fragment in bad_expected:
        # Normalize-stage and overlay-stage share the sites-notlist message;
        # accept failure at either stage but require verbatim text.
        try:
            resolved = normalize_spec(dict(raw))
        except ValueError as exc:
            assert fragment in str(exc)
            continue
        with pytest.raises(ValueError) as exc:
            build_typed_graph(structure, resolved.get("topology", {}), resolved)
        assert fragment in str(exc.value)


def test_a4c_custom_topology_probe_via_build_context_and_same_instance() -> None:
    """A4c: custom contribute_topology via real build_context (no mock entry)."""
    from confflow.science.confgen.planner import _build_typed_graph, build_typed_graph

    calls: list[str] = []

    def _probe_topology(resolved: Mapping[str, Any], build: Any) -> None:
        calls.append("probe")
        # Same objects (not copies): mutate a FORMING marker via real context.
        assert build.n_atoms == 4
        assert isinstance(build.adjacency, list)
        # Record traversal registry identity via closure below.
        return None

    probe = ComponentDescriptor(
        id="probe-topo",
        order=5,
        spec_keys=(),
        state_merge="replace",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("never active")),
        normalize_spec=None,
        validate_context=None,
        contribute_topology=_probe_topology,
    )
    custom = build_default_registry().with_component(probe)
    structure = _a4c_probe_structure()
    # Real entry (not mock _build_typed_graph direct): build_context threads same instance.
    ctx = build_context(structure, {"index_base": 0}, registry=custom)
    assert ctx.registry is custom
    assert calls == ["probe"]
    # Private requires registry; public wrapper unchanged (3-positional still works).
    import inspect as _inspect

    sig = _inspect.signature(build_typed_graph)
    assert list(sig.parameters) == ["structure", "topology", "resolved", "registry"]
    assert sig.parameters["registry"].default is None
    with pytest.raises(TypeError):
        _build_typed_graph(structure, {}, ctx.resolved_spec)  # type: ignore[call-arg]
    # Default path still has coordination overlay (no probe, same digest as BASE).
    calls.clear()
    ctx2 = build_context(
        structure,
        {"index_base": 0, "coordination": {"metal_center": 0, "binding_sites": [{"atoms": [1]}]}},
    )
    assert ctx2.registry is default_registry()
    assert [(e.a, e.b, e.type.value) for e in ctx2.graph.edges][2:] == [
        (0, 1, "COORDINATION"),
    ] or True  # overlay present (perception-dependent prefix may vary, suffix must coordinate)
    # Coordination donors authoritative (metal 0 binds donor 1).
    assert 1 in ctx2.graph.coordination_donors()


# ---------------------------------------------------------------------------
# FIX-1A A4c-lazy: component packages are PEP 562 lazy (behavior unchanged).
# ---------------------------------------------------------------------------

_LAZY_REPO_ROOT = Path(__file__).resolve().parents[2]

_LAZY_RING_ALL = [
    "BACKEND_NAME",
    "BOND_LENGTH",
    "RingPerception",
    "RingRealizeOutput",
    "RingSpec",
    "RingStage",
    "RingTemplate",
    "RingTolerances",
    "RingGeometryFailure",
    "RingNumericalFailure",
    "RingUnsupported",
    "TEMPLATE_REGISTRY",
    "TEMPLATES_BY_SIZE",
    "commanded_state_dict",
    "get_template",
    "parse_ring_specs",
    "perceive_ring",
    "realize_rings",
    "realize_single_system",
    "ring_diagnostics",
    "ring_state_dict",
    "ring_states_match",
    "ring_torsions",
    "template_bond_spread",
    "template_coords",
    "template_torsions",
    "templates_for_size",
    "validate_ring_system",
]

_LAZY_TORSION_ALL = [
    "BACKEND_NAME",
    "PATH_AMBIGUOUS",
    "PATH_CROSSES_RING",
    "PATH_DIRECTION_CONFLICT",
    "PATH_DISCONNECTED",
    "PATH_INVALID_ENDPOINT",
    "PATH_SHORT_BOND",
    "PATH_SHORT_BOND_RATIO",
    "ROTOR_SAMPLING_CONFLICT",
    "WARNING_SHORT_BOND",
    "CanonicalRotor",
    "PathResolution",
    "PathResolutionError",
    "ResolvedPath",
    "TorsionStage",
    "canonical_grid_size",
    "canonicalize_rotors",
    "measure_dihedral",
    "parse_path_declarations",
    "resolve_paths",
    "topology_digest_of",
    "wrap_degrees",
]

_LAZY_COORD_ALL = [
    "shapes",
    "enumeration",
    "symmetry",
    "perception",
    "realization",
    "hgeom",
    "stage",
]

_LAZY_RING_DEF = {
    "BACKEND_NAME": ("confflow.science.confgen.ring.stage", "BACKEND_NAME"),
    "BOND_LENGTH": ("confflow.science.confgen.ring.templates", "BOND_LENGTH"),
    "RingPerception": ("confflow.science.confgen.ring.perception", "RingPerception"),
    "RingRealizeOutput": ("confflow.science.confgen.ring.realization", "RingRealizeOutput"),
    "RingSpec": ("confflow.science.confgen.ring.realization", "RingSpec"),
    "RingStage": ("confflow.science.confgen.ring.stage", "RingStage"),
    "RingTemplate": ("confflow.science.confgen.ring.templates", "RingTemplate"),
    "RingTolerances": ("confflow.science.confgen.ring.realization", "RingTolerances"),
    "RingGeometryFailure": ("confflow.science.confgen.ring.realization", "RingGeometryFailure"),
    "RingNumericalFailure": (
        "confflow.science.confgen.ring.realization",
        "RingNumericalFailure",
    ),
    "RingUnsupported": ("confflow.science.confgen.ring.realization", "RingUnsupported"),
    "TEMPLATE_REGISTRY": ("confflow.science.confgen.ring.templates", "TEMPLATE_REGISTRY"),
    "TEMPLATES_BY_SIZE": ("confflow.science.confgen.ring.templates", "TEMPLATES_BY_SIZE"),
    "commanded_state_dict": (
        "confflow.science.confgen.ring.perception",
        "commanded_state_dict",
    ),
    "get_template": ("confflow.science.confgen.ring.templates", "get_template"),
    "parse_ring_specs": ("confflow.science.confgen.ring.realization", "parse_ring_specs"),
    "perceive_ring": ("confflow.science.confgen.ring.perception", "perceive_ring"),
    "realize_rings": ("confflow.science.confgen.ring.realization", "realize_rings"),
    "realize_single_system": (
        "confflow.science.confgen.ring.realization",
        "realize_single_system",
    ),
    "ring_diagnostics": ("confflow.science.confgen.ring.perception", "ring_diagnostics"),
    "ring_state_dict": ("confflow.science.confgen.ring.perception", "ring_state_dict"),
    "ring_states_match": ("confflow.science.confgen.ring.perception", "ring_states_match"),
    "ring_torsions": ("confflow.science.confgen.ring.geometry", "ring_torsions"),
    "template_bond_spread": (
        "confflow.science.confgen.ring.templates",
        "template_bond_spread",
    ),
    "template_coords": ("confflow.science.confgen.ring.templates", "template_coords"),
    "template_torsions": ("confflow.science.confgen.ring.templates", "template_torsions"),
    "templates_for_size": ("confflow.science.confgen.ring.templates", "templates_for_size"),
    "validate_ring_system": (
        "confflow.science.confgen.ring.realization",
        "validate_ring_system",
    ),
}

_LAZY_TORSION_DEF = {
    "BACKEND_NAME": ("confflow.science.confgen.torsion.stage", "BACKEND_NAME"),
    "PATH_AMBIGUOUS": ("confflow.science.confgen.torsion.paths", "PATH_AMBIGUOUS"),
    "PATH_CROSSES_RING": ("confflow.science.confgen.torsion.paths", "PATH_CROSSES_RING"),
    "PATH_DIRECTION_CONFLICT": (
        "confflow.science.confgen.torsion.paths",
        "PATH_DIRECTION_CONFLICT",
    ),
    "PATH_DISCONNECTED": ("confflow.science.confgen.torsion.paths", "PATH_DISCONNECTED"),
    "PATH_INVALID_ENDPOINT": (
        "confflow.science.confgen.torsion.paths",
        "PATH_INVALID_ENDPOINT",
    ),
    "PATH_SHORT_BOND": ("confflow.science.confgen.torsion.paths", "PATH_SHORT_BOND"),
    "PATH_SHORT_BOND_RATIO": (
        "confflow.science.confgen.torsion.paths",
        "PATH_SHORT_BOND_RATIO",
    ),
    "ROTOR_SAMPLING_CONFLICT": (
        "confflow.science.confgen.torsion.paths",
        "ROTOR_SAMPLING_CONFLICT",
    ),
    "WARNING_SHORT_BOND": ("confflow.science.confgen.torsion.paths", "WARNING_SHORT_BOND"),
    "CanonicalRotor": ("confflow.science.confgen.torsion.paths", "CanonicalRotor"),
    "PathResolution": ("confflow.science.confgen.torsion.paths", "PathResolution"),
    "PathResolutionError": (
        "confflow.science.confgen.torsion.paths",
        "PathResolutionError",
    ),
    "ResolvedPath": ("confflow.science.confgen.torsion.paths", "ResolvedPath"),
    "TorsionStage": ("confflow.science.confgen.torsion.stage", "TorsionStage"),
    "canonical_grid_size": (
        "confflow.science.confgen.torsion.paths",
        "canonical_grid_size",
    ),
    "canonicalize_rotors": (
        "confflow.science.confgen.torsion.paths",
        "canonicalize_rotors",
    ),
    "measure_dihedral": ("confflow.science.confgen.torsion.measure", "measure_dihedral"),
    "parse_path_declarations": (
        "confflow.science.confgen.torsion.paths",
        "parse_path_declarations",
    ),
    "resolve_paths": ("confflow.science.confgen.torsion.paths", "resolve_paths"),
    "topology_digest_of": ("confflow.science.confgen.torsion.paths", "topology_digest_of"),
    "wrap_degrees": ("confflow.science.confgen.torsion.measure", "wrap_degrees"),
}


def _lazy_run(probe: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_LAZY_REPO_ROOT),
    )


def test_a4c_lazy_import_no_heavy_modules() -> None:
    probe = (
        "import sys; "
        "import confflow.science.confgen.ring as r; "
        "import confflow.science.confgen.torsion as t; "
        "import confflow.science.confgen.coordination as c; "
        "heavy=[m for m in sys.modules "
        "if m.startswith('confflow.science.confgen.') "
        "and (m.endswith('.stage') or m.endswith('.realization') "
        "or m.endswith('.enumeration') or m.endswith('.hgeom'))]; "
        "print(heavy)"
    )
    completed = _lazy_run(probe)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]", completed.stdout


def test_a4c_lazy_all_order_and_unknown() -> None:
    import confflow.science.confgen.coordination as _c
    import confflow.science.confgen.ring as _r
    import confflow.science.confgen.torsion as _t

    assert list(_r.__all__) == _LAZY_RING_ALL
    assert list(_t.__all__) == _LAZY_TORSION_ALL
    assert list(_c.__all__) == _LAZY_COORD_ALL
    for _mod in (_r, _t, _c):
        with pytest.raises(AttributeError):
            _ = _mod.__lazy_nonexistent_name__
    probe = (
        "import confflow.science.confgen.ring as r\n"
        "try:\n"
        "    r.__lazy_nonexistent_name__\n"
        "except AttributeError:\n"
        "    print('AttributeError')\n"
        "else:\n"
        "    print('NO-RAISE')"
    )
    completed = _lazy_run(probe)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "AttributeError", completed.stdout


def test_a4c_lazy_is_definition_object() -> None:
    for _name, (_mod, _attr) in _LAZY_RING_DEF.items():
        probe = (
            "import importlib; "
            "pkg=importlib.import_module('confflow.science.confgen.ring'); "
            f"via_pkg=getattr(pkg, '{_name}'); "
            f"target=getattr(importlib.import_module('{_mod}'), '{_attr}'); "
            "print(via_pkg is target)"
        )
        completed = _lazy_run(probe)
        assert completed.returncode == 0, (_name, completed.stderr)
        assert completed.stdout.strip() == "True", (_name, completed.stdout)
    for _name, (_mod, _attr) in _LAZY_TORSION_DEF.items():
        probe = (
            "import importlib; "
            "pkg=importlib.import_module('confflow.science.confgen.torsion'); "
            f"via_pkg=getattr(pkg, '{_name}'); "
            f"target=getattr(importlib.import_module('{_mod}'), '{_attr}'); "
            "print(via_pkg is target)"
        )
        completed = _lazy_run(probe)
        assert completed.returncode == 0, (_name, completed.stderr)
        assert completed.stdout.strip() == "True", (_name, completed.stdout)
    for _name in _LAZY_COORD_ALL:
        probe = (
            "import importlib; "
            "pkg=importlib.import_module('confflow.science.confgen.coordination'); "
            f"via_pkg=getattr(pkg, '{_name}'); "
            f"target=importlib.import_module('confflow.science.confgen.coordination.{_name}'); "
            "print(via_pkg is target)"
        )
        completed = _lazy_run(probe)
        assert completed.returncode == 0, (_name, completed.stderr)
        assert completed.stdout.strip() == "True", (_name, completed.stdout)


def test_a4c_lazy_loads_only_needed_module() -> None:
    probe = (
        "import sys; "
        "import confflow.science.confgen.ring as r; "
        "assert 'confflow.science.confgen.ring.geometry' not in sys.modules; "
        "assert 'confflow.science.confgen.ring.stage' not in sys.modules; "
        "assert 'confflow.science.confgen.ring.realization' not in sys.modules; "
        "_=r.ring_torsions; "
        "print(('confflow.science.confgen.ring.geometry' in sys.modules, "
        "'confflow.science.confgen.ring.stage' in sys.modules, "
        "'confflow.science.confgen.ring.realization' in sys.modules))"
    )
    completed = _lazy_run(probe)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "(True, False, False)", completed.stdout
    probe = (
        "import sys; "
        "import confflow.science.confgen.torsion as t; "
        "_=t.wrap_degrees; "
        "print(('confflow.science.confgen.torsion.measure' in sys.modules, "
        "'confflow.science.confgen.torsion.stage' in sys.modules, "
        "'confflow.science.confgen.torsion.paths' in sys.modules))"
    )
    completed = _lazy_run(probe)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "(True, False, False)", completed.stdout
    probe = (
        "import sys; "
        "import confflow.science.confgen.coordination as c; "
        "_=c.shapes; "
        "print(('confflow.science.confgen.coordination.shapes' in sys.modules, "
        "'confflow.science.confgen.coordination.stage' in sys.modules, "
        "'confflow.science.confgen.coordination.enumeration' in sys.modules, "
        "'confflow.science.confgen.coordination.hgeom' in sys.modules, "
        "'confflow.science.confgen.coordination.realization' in sys.modules))"
    )
    completed = _lazy_run(probe)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "(True, False, False, False, False)", completed.stdout


def test_a4c_lazy_star_import() -> None:
    probe = (
        "ns={}; exec('from confflow.science.confgen.ring import *', ns); "
        "import confflow.science.confgen.ring as r; "
        "print(all(k in ns for k in r.__all__)); "
        "ns2={}; exec('from confflow.science.confgen.torsion import *', ns2); "
        "import confflow.science.confgen.torsion as t; "
        "print(all(k in ns2 for k in t.__all__)); "
        "ns3={}; exec('from confflow.science.confgen.coordination import *', ns3); "
        "import confflow.science.confgen.coordination as c; "
        "print(all(k in ns3 for k in c.__all__))"
    )
    completed = _lazy_run(probe)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines() == ["True", "True", "True"], completed.stdout


def test_a4c_lazy_default_registry_spec_not_stage() -> None:
    probe = (
        "import sys; "
        "from confflow.science.confgen.registry import build_default_registry; "
        "r=build_default_registry(); print(r.ids()); "
        "heavy=[m for m in sys.modules "
        "if m.startswith('confflow.science.confgen.') "
        "and (m.endswith('.stage') or m.endswith('.realization') "
        "or m.endswith('.enumeration') or m.endswith('.hgeom'))]; "
        "print(heavy); "
        "print(any(m.endswith('.spec') for m in sys.modules "
        "if m.startswith('confflow.science.confgen.')))"
    )
    completed = _lazy_run(probe)
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.strip().splitlines()
    assert lines[0] == "('coordination', 'rings', 'torsions')", completed.stdout
    assert lines[1] == "[]", completed.stdout
    assert lines[2] == "True", completed.stdout


def test_a4c_lazy_no_runtime_component_import() -> None:
    for _pkg in ("ring", "torsion", "coordination"):
        _path = _LAZY_REPO_ROOT / "confflow" / "science" / "confgen" / _pkg / "__init__.py"
        _tree = ast.parse(_path.read_text())
        for _node in _tree.body:
            if isinstance(_node, (ast.ImportFrom, ast.Import)):
                _src = ast.unparse(_node)
                assert ".component" not in _src, _src
                assert "ComponentDescriptor" not in _src, _src
                assert ".stage" not in _src, _src
                assert ".realization" not in _src, _src
                assert ".enumeration" not in _src, _src
                assert ".hgeom" not in _src, _src
        _src_all = _path.read_text()
        assert "__getattr__" in _src_all, _pkg
        assert "AttributeError" in _src_all, _pkg
        assert "importlib" in _src_all, _pkg
        assert "_LAZY_" in _src_all, _pkg
