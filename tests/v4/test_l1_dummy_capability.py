#!/usr/bin/env python3
"""L1-A3b test-only dummy capability with a genuinely new intent shape.

Test-local handler owns a new native key (``dummy_bond_scale``): the
explicit value is renamed to the existing ``bond_scale`` vocabulary and
then delegated to the existing ``transform_fragment`` boundary. No
scientific computation, perception rule, or threshold table is copied
here; values come from explicit user input and illegal values fail via
the existing runtime. Production ``confflow/`` is untouched (B=0).

Real chain only: ``compile_intent -> compile_workflow ->
plan.steps[0].scientific -> TransformExecutor.execute`` on the same
``ExecutionRegistry`` instance with legal multi-structure input and a
real ``ItemExecutionContext``.
"""

from __future__ import annotations

import copy
import tempfile
from collections.abc import Mapping
from pathlib import Path

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import WorkItemStatus
from confflow.domain.resources import ResourceRequest
from confflow.domain.structure import StructureRecord
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.transform_executor import TransformExecutor
from confflow.execution.work_item_executor import ItemExecutionContext
from confflow.producer.cards import CARD_TYPES
from confflow.producer.intent import INTENT_SCHEMA, IntentCompilationError, compile_intent
from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
from confflow.producer.intent.capabilities.registry import (
    ALLOWED_EXECUTORS,
    build_default_intent_registry,
    build_intent_registry,
)
from confflow.producer.intent.capabilities.transform import (
    REJECTED_STEP_KEYS as TRANSFORM_REJECTED,
)
from confflow.workflow.v4.compiler import compile_workflow

DUMMY_KEY = "dummy_bond"

DUMMY_CARD: dict = {
    "executor": "structure_transform",
    "adapter": None,
    "profile": None,
    "checks": [],
    "check_params": {},
    "recovery": "none",
    "default_role": None,
    "transform_kind": "refine",
    "requires_explicit_bindings": False,
    "description": "Test-local dummy with explicit dummy_bond_scale shape.",
}

MARGINAL_DISTANCE = 1.81
STRETCHED_DISTANCE = 1.95


def _dummy_fragment(step, card, step_id):
    """Rename显式 dummy_bond_scale to bond_scale, then delegate."""
    from confflow.producer.intent.capabilities.transform import transform_fragment
    from confflow.producer.intent.common import _fail

    native = step.get("native", {}) if isinstance(step, Mapping) else {}
    if native is None:
        native = {}
    if not isinstance(native, Mapping):
        raise _fail(f"step {step_id!r} native must be a mapping", step_id=step_id)
    if "dummy_bond_scale" not in native:
        raise _fail(
            f"step {step_id!r} dummy requires explicit dummy_bond_scale",
            step_id=step_id,
        )
    if "bond_scale" in native:
        raise _fail(
            f"step {step_id!r} dummy carries both dummy_bond_scale and bond_scale",
            step_id=step_id,
        )
    renamed = dict(native)
    renamed["bond_scale"] = renamed.pop("dummy_bond_scale")
    patched = dict(step)
    patched["native"] = renamed
    return transform_fragment(patched, card, step_id)


def _dummy_registry(exec_reg):
    default = build_default_intent_registry()
    assert DUMMY_KEY not in default.entries
    dummy = CapabilityDescriptor(
        key=DUMMY_KEY,
        executor="structure_transform",
        intent_handler=_dummy_fragment,
        card=copy.deepcopy(DUMMY_CARD),
        fragment_keys=("transform", "_preset_ref"),
        description=str(DUMMY_CARD["description"]),
        rejected_step_keys=tuple(sorted(set(TRANSFORM_REJECTED))),
        wire_block_key="transform",
        seed_block_keys=(),
        requires_assignment=None,
        patch_recipe_step=None,
        apply_role_card_block=None,
    )
    return build_intent_registry([*default.entries.values(), dummy], execution_registry=exec_reg)


def _intent(steps):
    return {"schema": INTENT_SCHEMA, "globals": {"charge": 0, "multiplicity": 1}, "steps": steps}


def _record(ident: str, dist: float) -> StructureRecord:
    return StructureRecord(
        id=ident,
        atoms=("C", "C"),
        coordinates=((0.0, 0.0, 0.0), (dist, 0.0, 0.0)),
        charge=0,
        multiplicity=1,
    )


def _run_execute(plan, scale_label: str):
    pair = [_record("a", MARGINAL_DISTANCE), _record("b", STRETCHED_DISTANCE)]
    sci = plan.steps[0].scientific
    item = WorkItem(
        id=make_work_item_id(f"d1:{scale_label}"),
        logical_key=f"d1:{scale_label}",
        step_id=plan.steps[0].step_id,
        named_inputs=WorkItemInputs(
            structures=FrozenDict({"structure": StructureSet(tuple(pair))}),
        ),
        resources=ResourceRequest(cores_per_item=2, memory_per_item_bytes=2**30),
        semantic_digest="sha256:" + "00" * 32,
    )
    with tempfile.TemporaryDirectory(prefix="a3b-dummy-") as run_root:
        ctx = ItemExecutionContext(
            step_id=plan.steps[0].step_id,
            scientific=sci,
            scientific_defaults=plan.scientific_defaults,
            adapter=None,  # type: ignore[arg-type]
            profile=None,  # type: ignore[arg-type]
            checks=(),  # type: ignore[arg-type]
            recovery=None,  # type: ignore[arg-type]
            run_root=run_root,
        )
        out = TransformExecutor().execute(item, ctx)
    return out


def test_closed_enum_boundary_truthful() -> None:
    from confflow.execution.contracts import ExecutorCapability

    caps = sorted(m.value for m in ExecutorCapability)
    # R2.2: analysis retired.
    # N2 声明新增 script 外部脚本能力（静态 contract 只描述能力本身；
    # intent authoring 仍只支持原三能力，脚本步骤仅手写，故 ALLOWED_EXECUTORS 不变）。
    assert caps == ["calculation", "confgen", "script", "structure_transform"]
    assert "dummy" not in caps
    assert DUMMY_KEY not in caps
    assert set(ALLOWED_EXECUTORS) == {"calculation", "confgen", "structure_transform"}


def test_default_registry_has_no_dummy_and_catalog_stable() -> None:
    from confflow.producer.intent import intent_catalog

    reg = build_default_intent_registry()
    assert sorted(reg.entries.keys()) == sorted(CARD_TYPES)
    assert len(reg.entries) == 9
    assert DUMMY_KEY not in reg.entries
    assert reg.resolve(DUMMY_KEY) is None
    assert reg.execution_registry is None
    catalog = intent_catalog()
    assert len(catalog["cards"]) == 9
    assert [c["type"] for c in catalog["cards"]] == sorted(CARD_TYPES)
    assert DUMMY_KEY not in [c["type"] for c in catalog["cards"]]


def test_same_instance_real_chain_grouping_difference() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    assert exec_reg.executor_implementation("structure_transform") is TransformExecutor
    custom = _dummy_registry(exec_reg)
    assert custom.execution_registry is exec_reg
    results: dict[float, list[str]] = {}
    for scale in (1.15, 1.2):
        intent = _intent(
            [
                {
                    "id": "d1",
                    "card": f"{DUMMY_KEY}@v1",
                    "native": {"dummy_bond_scale": scale},
                }
            ]
        )
        wire = compile_intent(copy.deepcopy(intent), intent_registry=custom, registry=exec_reg)
        assert wire["steps"][0]["executor"] == "structure_transform"
        assert wire["steps"][0]["transform"]["kind"] == "refine"
        assert float(wire["steps"][0]["transform"]["native"]["bond_scale"]) == scale
        assert "dummy_bond_scale" not in wire["steps"][0]["transform"]["native"]
        wire2 = compile_intent(copy.deepcopy(intent), intent_registry=custom)
        assert wire2 == wire
        other = build_default_registry()
        with pytest.raises(IntentCompilationError):
            compile_intent(copy.deepcopy(intent), intent_registry=custom, registry=other)
        compiled = compile_workflow(copy.deepcopy(wire), registry=exec_reg)
        assert compiled.ok, [(d.code, d.message) for d in compiled.errors]
        assert compiled.plan is not None
        plan = compiled.plan
        sci = plan.steps[0].scientific
        assert sci.transform == "refine"
        assert float(dict(sci.native).get("bond_scale")) == scale
        out = _run_execute(plan, str(scale))
        assert out.status is WorkItemStatus.COMPLETED
        kept = [r.id for r in out.structures]
        results[scale] = kept
    assert results[1.15] == ["a"]
    assert results[1.2] == ["a", "b"]
    assert results[1.15] != results[1.2]


def test_dummy_missing_and_dual_authority_reject() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    custom = _dummy_registry(exec_reg)
    with pytest.raises(IntentCompilationError, match="dummy_bond_scale"):
        compile_intent(
            _intent([{"id": "d1", "card": f"{DUMMY_KEY}@v1", "native": {}}]),
            intent_registry=custom,
            registry=exec_reg,
        )
    with pytest.raises(IntentCompilationError, match="dummy_bond_scale"):
        compile_intent(
            _intent([{"id": "d1", "card": f"{DUMMY_KEY}@v1"}]),
            intent_registry=custom,
            registry=exec_reg,
        )
    with pytest.raises(IntentCompilationError, match="both dummy_bond_scale and bond_scale"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "d1",
                        "card": f"{DUMMY_KEY}@v1",
                        "native": {"dummy_bond_scale": 1.15, "bond_scale": 1.15},
                    }
                ]
            ),
            intent_registry=custom,
            registry=exec_reg,
        )


def test_builtin_refine_rejects_dummy_key() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    with pytest.raises(IntentCompilationError, match="unknown refine native keys"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "r1",
                        "card": "refine@v1",
                        "native": {"dummy_bond_scale": 1.15},
                    }
                ]
            ),
            registry=exec_reg,
        )
    direct_ok = compile_intent(
        _intent([{"id": "r1", "card": "refine@v1", "native": {"bond_scale": 1.15}}]),
        registry=exec_reg,
    )
    assert compile_workflow(copy.deepcopy(direct_ok)).ok


def test_negative_rmsd_real_execute_failed() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    custom = _dummy_registry(exec_reg)
    intent = _intent(
        [
            {
                "id": "d1",
                "card": f"{DUMMY_KEY}@v1",
                "native": {"dummy_bond_scale": 1.15, "rmsd_threshold_angstrom": -1},
            }
        ]
    )
    wire = compile_intent(copy.deepcopy(intent), intent_registry=custom, registry=exec_reg)
    compiled = compile_workflow(copy.deepcopy(wire), registry=exec_reg)
    assert compiled.ok, [(d.code, d.message) for d in compiled.errors]
    assert compiled.plan is not None
    assert float(dict(compiled.plan.steps[0].scientific.native)["rmsd_threshold_angstrom"]) == -1
    out = _run_execute(compiled.plan, "neg")
    assert out.status is WorkItemStatus.FAILED
    assert "rmsd_threshold_angstrom" in str(out.diagnostics)


def test_preset_allowed_per_existing_transform() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    custom = _dummy_registry(exec_reg)
    wire = compile_intent(
        _intent(
            [
                {
                    "id": "d1",
                    "card": f"{DUMMY_KEY}@v1",
                    "preset": "refine_default@v1",
                    "native": {"dummy_bond_scale": 1.15},
                }
            ]
        ),
        intent_registry=custom,
        registry=exec_reg,
    )
    assert compile_workflow(copy.deepcopy(wire)).ok
    assert wire["steps"][0]["transform"]["native"]["bond_scale"] == 1.15
    builtin = compile_intent(
        _intent([{"id": "r1", "card": "refine@v1", "preset": "refine_default@v1"}]),
        registry=exec_reg,
    )
    assert compile_workflow(copy.deepcopy(builtin)).ok


def test_authoring_projection_capability() -> None:
    from confflow.execution.registry import build_default_registry
    from confflow.producer.authoring import describe_step

    exec_reg = build_default_registry()
    custom = _dummy_registry(exec_reg)
    wire = compile_intent(
        _intent([{"id": "d1", "card": f"{DUMMY_KEY}@v1", "native": {"dummy_bond_scale": 1.2}}]),
        intent_registry=custom,
        registry=exec_reg,
    )
    assert compile_workflow(copy.deepcopy(wire)).ok
    env = describe_step(copy.deepcopy(wire), "d1", registry=exec_reg)
    assert env["ok"] is True
    assert env["result"]["capability"] == "structure_transform"
    builtin_wire = compile_intent(_intent([{"id": "r1", "card": "refine@v1"}]), registry=exec_reg)
    builtin_env = describe_step(copy.deepcopy(builtin_wire), "r1", registry=exec_reg)
    assert builtin_env["ok"] is True
    assert env["result"]["contract_version"] == builtin_env["result"]["contract_version"]


def test_misplaced_before_handler_order() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    custom = _dummy_registry(exec_reg)
    both = _intent(
        [
            {
                "id": "d1",
                "card": f"{DUMMY_KEY}@v1",
                "program": "should-be-rejected",
                "native": {},
            }
        ]
    )
    with pytest.raises(IntentCompilationError) as excinfo:
        compile_intent(copy.deepcopy(both), intent_registry=custom, registry=exec_reg)
    assert "cannot consume fields: program" in str(excinfo.value)
    assert excinfo.value.step_id == "d1"


def test_duplicate_descriptor_reject() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    custom = _dummy_registry(exec_reg)
    with pytest.raises(ValueError, match="conflicting"):
        build_intent_registry(
            [*custom.entries.values(), custom.resolve(DUMMY_KEY)],
            execution_registry=exec_reg,
        )


def test_no_production_dummy_reference_and_no_science_copy() -> None:
    import inspect

    root = Path(__file__).resolve().parents[2]
    for rel in (
        "confflow/producer/cards.py",
        "confflow/producer/intent/capabilities/descriptor.py",
        "confflow/producer/intent/capabilities/registry.py",
        "confflow/producer/intent/capabilities/calculation.py",
        "confflow/producer/intent/capabilities/confgen.py",
        "confflow/producer/intent/capabilities/transform.py",
        "confflow/producer/intent/compiler.py",
    ):
        text = (root / rel).read_text(encoding="utf-8").lower()
        assert "dummy" not in text, rel
    from confflow.producer.intent import intent_catalog

    assert "dummy" not in str(intent_catalog()).lower()
    src = inspect.getsource(_dummy_fragment)
    assert "transform_fragment" in src
    assert "REFINE_NATIVE_KEYS" not in src
    assert "REFINE_DEFAULT" not in src
    assert "confflow.science" not in src
