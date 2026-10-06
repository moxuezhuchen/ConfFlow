#!/usr/bin/env python3

"""L1-A1 authoring projection over one execution authority (compile only).

Same-registry measurement: one explicit ``ExecutionRegistry`` instance flows
to both ``compile_intent`` (execution ``registry=``) and ``describe_step``
(``registry=``); A1 performs no real execution (full ``execute()`` is the A2
hard gate, never a class-instantiation check here).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping

from confflow.execution.registry import default_registry
from confflow.producer.authoring import describe_step
from confflow.producer.intent import INTENT_SCHEMA, IntentCompilationError, compile_intent
from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
from confflow.producer.intent.capabilities.registry import build_default_intent_registry
from confflow.producer.intent.capabilities.transform import REJECTED_STEP_KEYS
from confflow.workflow.v4.compiler import compile_workflow

DUMMY_KEY = "dummy_probe"

DUMMY_CARD: dict = {
    "executor": "structure_transform",
    "adapter": "standard",
    "profile": "standard",
    "checks": [],
    "check_params": {},
    "recovery": "none",
    "default_role": None,
    "requires_explicit_bindings": False,
    "description": "Test-local dummy: own intent contract, shared refine runtime.",
}

DUMMY_REJECTED = frozenset(
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
    }
)

DUMMY_SCALE = {"collapse": 1.15, "keep": 1.2}


def _dummy_wire(user, card, step_id):
    from confflow.producer.intent.common import _fail

    native = user.get("native")
    if not isinstance(native, Mapping):
        raise _fail(f"step {step_id!r}: dummy requires a native mapping", step_id=step_id)
    unknown = sorted(set(native) - {"dummy_scale_selector"})
    if unknown:
        raise _fail(
            f"step {step_id!r}: dummy carries unsupported native keys: {', '.join(unknown)}",
            step_id=step_id,
        )
    selector = native.get("dummy_scale_selector")
    if selector not in DUMMY_SCALE:
        raise _fail(
            f"step {step_id!r}: dummy_scale_selector must be one of {sorted(DUMMY_SCALE)}",
            step_id=step_id,
        )
    if user.get("preset") is not None:
        raise _fail(f"step {step_id!r}: dummy takes no preset", step_id=step_id)
    return {
        "transform": {
            "kind": "refine",
            "native": {
                "rmsd_threshold_angstrom": 0.25,
                "bond_scale": DUMMY_SCALE[selector],
                "heavy_only": False,
            },
        }
    }


def _dummy_registry():
    default = build_default_intent_registry()
    assert DUMMY_KEY not in default.entries
    dummy = CapabilityDescriptor(
        key=DUMMY_KEY,
        executor="structure_transform",
        intent_handler=_dummy_wire,
        card=copy.deepcopy(DUMMY_CARD),
        fragment_keys=("transform",),
        rejected_step_keys=tuple(sorted(DUMMY_REJECTED)),
        wire_block_key="transform",
        description=DUMMY_CARD["description"],
    )
    from confflow.producer.intent.capabilities.registry import build_intent_registry

    return build_intent_registry([*default.entries.values(), dummy])


def _intent(steps):
    return {"schema": INTENT_SCHEMA, "globals": {"charge": 0, "multiplicity": 1}, "steps": steps}


def test_dummy_rejected_matches_transform_module() -> None:
    assert DUMMY_REJECTED == set(REJECTED_STEP_KEYS)


def test_same_execution_registry_flows_to_both_seams() -> None:
    assert default_registry() is default_registry()
    reg = default_registry()
    custom = _dummy_registry()
    wire = compile_intent(
        _intent(
            [{"id": "d1", "card": f"{DUMMY_KEY}@v1", "native": {"dummy_scale_selector": "keep"}}]
        ),
        intent_registry=custom,
        registry=reg,
    )
    assert compile_workflow(copy.deepcopy(wire)).ok
    env = describe_step(copy.deepcopy(wire), "d1", registry=reg)
    assert env["ok"] is True
    assert env["result"]["capability"] == "structure_transform"


def test_dummy_compile_and_authoring_projection() -> None:
    reg = default_registry()
    custom = _dummy_registry()
    wire = compile_intent(
        _intent(
            [
                {
                    "id": "d1",
                    "card": f"{DUMMY_KEY}@v1",
                    "native": {"dummy_scale_selector": "collapse"},
                }
            ]
        ),
        intent_registry=custom,
        registry=reg,
    )
    assert compile_workflow(copy.deepcopy(wire)).ok
    native = wire["steps"][0]["transform"]["native"]
    assert native["bond_scale"] == 1.15
    env = describe_step(copy.deepcopy(wire), "d1", registry=reg)
    assert env["ok"] is True
    # Honest boundary disclosure: contract layer is shared with builtin refine.
    builtin_wire = compile_intent(_intent([{"id": "r1", "card": "refine@v1"}]), registry=reg)
    builtin_env = describe_step(copy.deepcopy(builtin_wire), "r1", registry=reg)
    assert builtin_env["ok"] is True
    assert env["result"]["contract_version"] == builtin_env["result"]["contract_version"]


def test_dummy_default_registry_failclosed_byte_identical() -> None:
    reg = default_registry()
    with __import__("pytest").raises(IntentCompilationError) as excinfo:
        compile_intent(
            _intent(
                [
                    {
                        "id": "d1",
                        "card": f"{DUMMY_KEY}@v1",
                        "native": {"dummy_scale_selector": "keep"},
                    }
                ]
            ),
            registry=reg,
        )
    first = str(excinfo.value)
    with __import__("pytest").raises(IntentCompilationError) as excinfo2:
        compile_intent(_intent([{"id": "x1", "card": "bogus@v1"}]), registry=reg)
    # Unknown-card wording is the legacy path (no auto-publication).
    assert "unknown card type" in first
    assert "unknown card type" in str(excinfo2.value)


def test_dummy_dedicated_rejections() -> None:
    custom = _dummy_registry()
    reg = default_registry()
    # Missing selector fails (builtin refine succeeds via preset default).
    import pytest

    with pytest.raises(IntentCompilationError, match="dummy_scale_selector"):
        compile_intent(
            _intent([{"id": "d1", "card": f"{DUMMY_KEY}@v1", "native": {}}]), intent_registry=custom
        )
    builtin_ok = compile_intent(_intent([{"id": "r1", "card": "refine@v1"}]), registry=reg)
    assert compile_workflow(copy.deepcopy(builtin_ok)).ok
    # Direct native write fails for dummy, builtin refine accepts it.
    with pytest.raises(IntentCompilationError, match="unsupported native keys"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "d1",
                        "card": f"{DUMMY_KEY}@v1",
                        "native": {"rmsd_threshold_angstrom": 0.3},
                    }
                ]
            ),
            intent_registry=custom,
        )
    direct_ok = compile_intent(
        _intent([{"id": "r1", "card": "refine@v1", "native": {"rmsd_threshold_angstrom": 0.3}}]),
        registry=reg,
    )
    assert compile_workflow(copy.deepcopy(direct_ok)).ok
    # Preset fails for dummy.
    with pytest.raises(IntentCompilationError, match="takes no preset"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "d1",
                        "card": f"{DUMMY_KEY}@v1",
                        "native": {"dummy_scale_selector": "keep"},
                        "preset": "refine_default",
                    }
                ]
            ),
            intent_registry=custom,
        )
