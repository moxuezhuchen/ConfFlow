#!/usr/bin/env python3

"""L1-A3a analysis capability: real handler + explicit runtime binding.

Framework count A (this file proves the new production handler file is
real, not an empty placeholder).  Dummy experiment count B is separate
and is not implemented here (A3b test-only follows).

Proves, through the real ``compile_intent -> compile_workflow`` path
plus a real ``AnalysisItemAdapter`` execution (no class/spy/direct
handler shortcut, no hand-made native):

- explicit ``ExecutionRegistry`` binding allows the already-registered
  ``analysis`` capability; the default 14-card registry stays
  fail-closed with byte-identical unknown-card errors;
- the same ``ExecutionRegistry`` instance flows through
  card/compile/authoring/compiled-plan/runtime (instance mismatch
  fails closed; omitted ``registry=`` reuses the bound instance);
- ``describe_step`` projects ``capability == "analysis"`` with the
  runtime contract version;
- negatives: missing/empty native, preset, unknown native key,
  assignment conflict, native pollution, step unknown key,
  misplaced-field order, descriptor conflict, instance mismatch.

Production default (14 cards, catalog/contract/wire bytes) is unchanged;
no new production default registration is added.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from confflow.producer.cards import CARD_TYPES
from confflow.producer.intent import INTENT_SCHEMA, IntentCompilationError, compile_intent
from confflow.producer.intent.capabilities.analysis import (
    REJECTED_STEP_KEYS as ANALYSIS_REJECTED,
)
from confflow.producer.intent.capabilities.analysis import (
    _wire_analysis,
    analysis_fragment,
)
from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
from confflow.producer.intent.capabilities.registry import (
    ALLOWED_EXECUTORS,
    build_default_intent_registry,
    build_intent_registry,
)
from confflow.workflow.v4.compiler import compile_workflow

ANALYSIS_KEY = "dummy_analysis_probe"

ANALYSIS_CARD: dict = {
    "executor": "analysis",
    "adapter": None,
    "profile": None,
    "checks": [],
    "check_params": {},
    "recovery": "none",
    "default_role": None,
    "requires_explicit_bindings": False,
    "description": "Test-local analysis probe reusing analysis runtime.",
}

# This card's own intent semantics: explicit non-empty native, no preset.
# The strict V4 AnalysisModel allows an empty default; the runtime is
# unchanged (empty stays legal over direct V4, only this card is strict).
GOOD_NATIVE = {
    "energy_mode": "composite",
    "electronic_result_kind": "energy",
    "correction_result_kind": "gibbs_correction",
    "energy_fallback": "none",
}


def _analysis_registry(exec_reg):
    default = build_default_intent_registry()
    assert ANALYSIS_KEY not in default.entries
    probe = CapabilityDescriptor(
        key=ANALYSIS_KEY,
        executor="analysis",
        intent_handler=analysis_fragment,
        card=copy.deepcopy(ANALYSIS_CARD),
        fragment_keys=("analysis",),
        description=ANALYSIS_CARD["description"],
        rejected_step_keys=tuple(sorted(ANALYSIS_REJECTED)),
        wire_block_key="analysis",
        seed_block_keys=(),
        requires_assignment=None,
        patch_recipe_step=None,
        apply_role_card_block=None,
    )
    return build_intent_registry([*default.entries.values(), probe], execution_registry=exec_reg)


def _intent(steps):
    return {"schema": INTENT_SCHEMA, "globals": {"charge": 0, "multiplicity": 1}, "steps": steps}


def test_default_registry_has_no_analysis_and_old_bytes() -> None:
    reg = build_default_intent_registry()
    assert sorted(reg.entries.keys()) == sorted(CARD_TYPES)
    assert len(reg.entries) == 14
    assert ANALYSIS_KEY not in reg.entries
    assert reg.resolve(ANALYSIS_KEY) is None
    assert reg.execution_registry is None
    assert set(ALLOWED_EXECUTORS) == {"calculation", "confgen", "structure_transform"}
    # Old omission path still rejects analysis with the legacy bytes.
    with pytest.raises(ValueError, match="unknown executor"):
        build_intent_registry(
            [
                *reg.entries.values(),
                CapabilityDescriptor(
                    key="bad_analysis",
                    executor="analysis",
                    intent_handler=analysis_fragment,
                    card=copy.deepcopy(ANALYSIS_CARD),
                    fragment_keys=("analysis",),
                ),
            ]
        )
    try:
        build_intent_registry(
            [
                *reg.entries.values(),
                CapabilityDescriptor(
                    key="bad_analysis",
                    executor="analysis",
                    intent_handler=analysis_fragment,
                    card=copy.deepcopy(ANALYSIS_CARD),
                    fragment_keys=("analysis",),
                ),
            ]
        )
    except ValueError as exc:
        assert str(exc) == (
            "unknown executor 'analysis' for key 'bad_analysis'; "
            "expected one of ['calculation', 'confgen', 'structure_transform']"
        )
    else:  # pragma: no cover
        raise AssertionError("expected unknown-executor rejection")
    from confflow.producer.intent import intent_catalog

    catalog = intent_catalog()
    assert len(catalog["cards"]) == 14
    assert ANALYSIS_KEY not in [c["type"] for c in catalog["cards"]]


def test_explicit_binding_uses_registered_contract_no_second_table() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    contract = exec_reg.resolve_executor("analysis")
    assert contract.contract_version == "confflow.contract.executor.analysis.v1"
    custom = _analysis_registry(exec_reg)
    assert custom.execution_registry is exec_reg
    entry = custom.resolve(ANALYSIS_KEY, "analysis")
    assert entry is not None
    assert entry.executor == "analysis"
    assert entry.fragment_keys == ("analysis",)
    assert entry.effective_wire_block_key == "analysis"
    # Same registry resolves the same contract object.
    assert exec_reg.resolve_executor(entry.executor) is contract
    # executor_implementation is the real adapter (not a spy).
    from confflow.analysis.item_adapter import AnalysisItemAdapter

    assert exec_reg.executor_implementation("analysis") is AnalysisItemAdapter
    # Honest boundary: arbitrary new runtime enums are still unsupported.
    with pytest.raises(ValueError, match="unknown executor"):
        build_intent_registry(
            [
                *build_default_intent_registry().entries.values(),
                CapabilityDescriptor(
                    key="bad_runtime",
                    executor="no_such_runtime",
                    intent_handler=analysis_fragment,
                    card=copy.deepcopy(ANALYSIS_CARD),
                    fragment_keys=("analysis",),
                ),
            ],
            execution_registry=exec_reg,
        )


def test_positive_compile_workflow_authoring_same_instance() -> None:
    from confflow.execution.registry import build_default_registry
    from confflow.producer.authoring import describe_step

    exec_reg = build_default_registry()
    custom = _analysis_registry(exec_reg)
    intent = _intent([{"id": "a1", "card": f"{ANALYSIS_KEY}@v1", "native": dict(GOOD_NATIVE)}])
    wire = compile_intent(copy.deepcopy(intent), intent_registry=custom, registry=exec_reg)
    assert wire["steps"][0]["executor"] == "analysis"
    assert wire["steps"][0]["analysis"]["native"] == GOOD_NATIVE
    compiled = compile_workflow(copy.deepcopy(wire), registry=exec_reg)
    assert compiled.ok, [(d.code, d.message) for d in compiled.errors]
    assert compiled.plan is not None
    assert dict(compiled.plan.steps[0].scientific.native) == GOOD_NATIVE
    env = describe_step(copy.deepcopy(wire), "a1", registry=exec_reg)
    assert env["ok"] is True
    assert env["result"]["capability"] == "analysis"
    assert env["result"]["contract_version"] == exec_reg.executor("analysis").contract_version
    # Omitted registry= reuses the bound instance (never a silent default).
    wire2 = compile_intent(copy.deepcopy(intent), intent_registry=custom)
    assert wire2 == wire


def test_real_adapter_execution_from_compiled_plan() -> None:
    from confflow.analysis.item_adapter import AnalysisItemAdapter
    from confflow.domain._immutable import FrozenDict
    from confflow.domain.completion import WorkItemStatus
    from confflow.domain.resources import ResourceRequest
    from confflow.domain.result import ResultSet, ScientificResult, make_result_id
    from confflow.domain.structure import StructureRecord, StructureSet
    from confflow.domain.units import Unit
    from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
    from confflow.execution.output_identity import (
        PATH_ENDPOINT_FORWARD_ROLE,
        PATH_ENDPOINT_REVERSE_ROLE,
    )
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    custom = _analysis_registry(exec_reg)
    intent = _intent([{"id": "a1", "card": f"{ANALYSIS_KEY}@v1", "native": dict(GOOD_NATIVE)}])
    wire = compile_intent(copy.deepcopy(intent), intent_registry=custom, registry=exec_reg)
    plan = compile_workflow(copy.deepcopy(wire), registry=exec_reg).plan
    assert plan is not None
    sci = plan.steps[0].scientific
    # The execution native comes from the compiled plan (never hand-made).
    assert dict(sci.native) == GOOD_NATIVE

    atoms = ("O", "H", "H")
    base = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0))

    def _coords(off: float):
        return tuple((x + off, y + off, z + off) for x, y, z in base)

    def _struct(rid, *, group_key=None, role=None, parents=(), off=0.0):
        return StructureRecord(
            id=rid,
            atoms=atoms,
            coordinates=_coords(off),
            group_key=group_key,
            role=role,
            parent_ids=parents,
        )

    def _res(kind, val, subj):
        pd = "sha256:" + hashlib.sha256(f"s-test:{subj}:{kind}".encode()).hexdigest()
        return ScientificResult(
            kind=kind,
            value=val,
            unit=Unit.HARTREE,
            subject_structure_id=subj,
            result_id=make_result_id(
                step_id="s-test",
                kind=kind,
                subject_structure_id=subj,
                producer_digest=pd,
            ),
        )

    ts = _struct("t-ts", group_key="rxn-0", off=0.0)
    fwd = _struct(
        "t-fwd",
        group_key="rxn-0",
        role=PATH_ENDPOINT_FORWARD_ROLE,
        parents=(ts.id,),
        off=1.0,
    )
    rev = _struct(
        "t-rev",
        group_key="rxn-0",
        role=PATH_ENDPOINT_REVERSE_ROLE,
        parents=(ts.id,),
        off=2.0,
    )
    results = (
        _res("energy", -100.0, ts.id),
        _res("energy", -100.5, fwd.id),
        _res("energy", -100.2, rev.id),
        _res("gibbs_correction", 0.05, ts.id),
        _res("gibbs_correction", 0.04, fwd.id),
        _res("gibbs_correction", 0.03, rev.id),
    )
    item = WorkItem(
        id=make_work_item_id("a1:agg"),
        logical_key="a1:agg",
        step_id="a1",
        named_inputs=WorkItemInputs(
            structures=FrozenDict({"structures": StructureSet((ts, fwd, rev))}),
            results=FrozenDict({"results": ResultSet(results)}),
        ),
        resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=1024**3),
        semantic_digest="sha256:" + "a" * 64,
    )
    ctx = SimpleNamespace(step_id="a1", scientific=sci)
    out = AnalysisItemAdapter().execute(item, ctx)
    assert out.status is WorkItemStatus.COMPLETED
    kinds = {r.kind for r in out.results}
    assert {
        "barrier_forward_endpoint",
        "barrier_reverse_endpoint",
        "endpoint_gibbs_delta",
        "endpoint_energy_delta",
        "reaction_profile",
    } <= kinds
    assert all(r.source_step_id == "a1" for r in out.results)


def test_negatives_unknown_conflict_pollution_mismatch() -> None:
    from confflow.execution.registry import build_default_registry

    exec_reg = build_default_registry()
    custom = _analysis_registry(exec_reg)
    # Missing native.
    with pytest.raises(IntentCompilationError, match="non-empty native"):
        compile_intent(
            _intent([{"id": "a1", "card": f"{ANALYSIS_KEY}@v1"}]),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Empty native (this card is strict; runtime empty default is unchanged).
    with pytest.raises(IntentCompilationError, match="non-empty native"):
        compile_intent(
            _intent([{"id": "a1", "card": f"{ANALYSIS_KEY}@v1", "native": {}}]),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Preset has no analysis semantics (rejected at placement, before handler).
    with pytest.raises(IntentCompilationError, match="cannot consume fields: preset"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "a1",
                        "card": f"{ANALYSIS_KEY}@v1",
                        "native": dict(GOOD_NATIVE),
                        "preset": "refine_default",
                    }
                ]
            ),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Unknown native key (real runtime authority, no second table).
    with pytest.raises(IntentCompilationError, match="invalid analysis native"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "a1",
                        "card": f"{ANALYSIS_KEY}@v1",
                        "native": {**GOOD_NATIVE, "bogus_key": 1},
                    }
                ]
            ),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Assignment conflict.
    with pytest.raises(IntentCompilationError, match="invalid analysis native"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "a1",
                        "card": f"{ANALYSIS_KEY}@v1",
                        "native": {
                            **GOOD_NATIVE,
                            "endpoint_assignment": {"forward": "reactant", "reverse": "reactant"},
                        },
                    }
                ]
            ),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Native pollution (seed inside native is not an analysis key).
    with pytest.raises(IntentCompilationError, match="invalid analysis native"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "a1",
                        "card": f"{ANALYSIS_KEY}@v1",
                        "native": {**GOOD_NATIVE, "seed": 1},
                    }
                ]
            ),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Step unknown key.
    with pytest.raises(IntentCompilationError, match="unknown members"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "a1",
                        "card": f"{ANALYSIS_KEY}@v1",
                        "native": dict(GOOD_NATIVE),
                        "bogus_step_key": 1,
                    }
                ]
            ),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Misplaced field order: program is rejected before handler runs.
    with pytest.raises(IntentCompilationError, match="cannot consume fields: program"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "a1",
                        "card": f"{ANALYSIS_KEY}@v1",
                        "native": {},
                        "program": "orca",
                    }
                ]
            ),
            intent_registry=custom,
            registry=exec_reg,
        )
    # Descriptor conflict (duplicate key).
    with pytest.raises(ValueError, match="conflicting"):
        build_intent_registry(
            [*custom.entries.values(), custom.resolve(ANALYSIS_KEY)],
            execution_registry=exec_reg,
        )
    # Instance mismatch fails closed.
    other = build_default_registry()
    assert other is not exec_reg
    with pytest.raises(IntentCompilationError, match="different execution registry"):
        compile_intent(
            _intent([{"id": "a1", "card": f"{ANALYSIS_KEY}@v1", "native": dict(GOOD_NATIVE)}]),
            intent_registry=custom,
            registry=other,
        )
    # Default registry never auto-publishes the probe (old bytes).
    with pytest.raises(IntentCompilationError) as excinfo:
        compile_intent(
            _intent([{"id": "a1", "card": f"{ANALYSIS_KEY}@v1", "native": dict(GOOD_NATIVE)}]),
            registry=exec_reg,
        )
    assert "unknown card type 'dummy_analysis_probe'" in str(excinfo.value)
    with pytest.raises(IntentCompilationError) as excinfo2:
        compile_intent(_intent([{"id": "x1", "card": "bogus@v1"}]), registry=exec_reg)
    assert "unknown card type" in str(excinfo2.value)


def test_handler_is_real_not_passthrough() -> None:
    # A passthrough/empty handler would accept empty/unknown native; the
    # real handler must reject both (design self-check).
    with pytest.raises(IntentCompilationError):
        _wire_analysis({}, {}, "s1")
    # Direct handler preset branch (compile covers placement first).
    with pytest.raises(IntentCompilationError, match="no preset"):
        _wire_analysis({"native": dict(GOOD_NATIVE), "preset": "x"}, {}, "s1")
    assert analysis_fragment({"native": dict(GOOD_NATIVE)}, {}, "s1") == {
        "analysis": {"native": dict(GOOD_NATIVE)}
    }
    assert ANALYSIS_REJECTED == frozenset(
        {
            "program",
            "role",
            "adapter",
            "profile",
            "checks",
            "check_params",
            "recovery",
            "recovery_params",
            "seed",
            "preset",
        }
    )


def test_no_science_import_anywhere_in_intent() -> None:
    root = Path(__file__).resolve().parents[2]
    rels = [
        "confflow/producer/intent/compiler.py",
        "confflow/producer/intent/capabilities/descriptor.py",
        "confflow/producer/intent/capabilities/registry.py",
        "confflow/producer/intent/capabilities/calculation.py",
        "confflow/producer/intent/capabilities/confgen.py",
        "confflow/producer/intent/capabilities/transform.py",
        "confflow/producer/intent/capabilities/analysis.py",
    ]
    for rel in rels:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("confflow.science"), (rel, alias.name)
            elif isinstance(node, ast.ImportFrom):
                mod = "." * node.level + (node.module or "")
                assert "confflow.science" not in mod, (rel, mod)
                assert mod.strip(".") != "confflow.science", (rel, mod)
