#!/usr/bin/env python3

"""L1-C2 capability handler + explicit descriptor registry (rehearsal).

Proves the compiler channel dispatches through a frozen explicit
``IntentRegistry``: default 14 cards byte-identical, custom test-local
handler/descriptor produces genuinely different observable output and
rejection via the real ``compile_intent -> compile_workflow`` path (no
mock dispatch, no ``cards.py`` dummy).  Production default never references
the probe key.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

import confflow.producer.intent.compiler as compiler
from confflow.producer.cards import CARD_TYPES, CARD_VERSION, get_card
from confflow.producer.intent import INTENT_SCHEMA, IntentCompilationError, compile_intent
from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
from confflow.producer.intent.capabilities.registry import (
    ALLOWED_EXECUTORS,
    IntentRegistry,
    build_default_intent_registry,
    build_intent_registry,
)
from confflow.workflow.v4.compiler import compile_workflow

CUSTOM_KEY = "custom_sp"

CUSTOM_CARD: dict = {
    "executor": "calculation",
    "adapter": "standard",
    "profile": "standard",
    "checks": ["normal_termination"],
    "check_params": {},
    "recovery": "none",
    "default_role": None,
    "requires_explicit_bindings": False,
    "description": "Test-local probe reusing calculation runtime.",
}


def _probe_wire(user, card, step_id):
    """Test-local handler returning the complete fragment (real dispatch)."""
    from confflow.producer.intent.capabilities.calculation import _wire_calculation
    from confflow.producer.intent.common import _fail

    if user.get("role") is None:
        raise _fail(
            f"step {step_id!r}: probe custom requires explicit role",
            step_id=step_id,
        )
    base = _wire_calculation(user, card, step_id)
    role = base.get("role")
    if isinstance(role, str) and role:
        base["role"] = f"probe_{role}"
    else:
        base["role"] = "probe_default"
    return {"calculation": base}


def _custom_registry() -> IntentRegistry:
    default = build_default_intent_registry()
    probe = CapabilityDescriptor(
        key=CUSTOM_KEY,
        executor="calculation",
        intent_handler=_probe_wire,
        card=copy.deepcopy(CUSTOM_CARD),
        fragment_keys=("calculation",),
        description="probe",
    )
    return build_intent_registry([*default.entries.values(), probe])


def _intent(steps):
    return {
        "schema": INTENT_SCHEMA,
        "globals": {"charge": 0, "multiplicity": 1},
        "steps": steps,
    }


def test_default_registry_has_exactly_14_no_dummy() -> None:
    reg = build_default_intent_registry()
    assert sorted(reg.entries.keys()) == sorted(CARD_TYPES)
    assert len(reg.entries) == 14
    assert CUSTOM_KEY not in reg.entries
    assert "dummy-probe" not in reg.entries
    assert "dummy" not in set(reg.entries)
    assert set(ALLOWED_EXECUTORS) == {
        "calculation",
        "confgen",
        "structure_transform",
    }


def test_default_handlers_are_real_capability_objects() -> None:
    import confflow.producer.intent.capabilities.calculation as calc
    import confflow.producer.intent.capabilities.confgen as cg
    import confflow.producer.intent.capabilities.transform as tr

    reg = build_default_intent_registry()
    # Default registry wires the fragment adapters (complete fragments).
    assert reg.resolve("sp", "calculation").intent_handler is calc.calculation_fragment
    assert reg.resolve("opt", "calculation").intent_handler is calc.calculation_fragment
    assert reg.resolve("confgen", "confgen").intent_handler is cg.confgen_fragment
    assert reg.resolve("refine", "structure_transform").intent_handler is (tr.transform_fragment)
    assert reg.resolve("deduplicate", "structure_transform").intent_handler is (
        tr.transform_fragment
    )
    # Declared fragment keys are capability-owned.
    assert reg.resolve("sp").fragment_keys == ("calculation",)
    assert reg.resolve("confgen").fragment_keys == ("confgen",)
    assert reg.resolve("refine").fragment_keys == ("transform", "_preset_ref")
    # Old payload builders keep verbatim return shape + compat identity.
    assert compiler._wire_calculation is calc._wire_calculation
    assert compiler._resolve_program is calc._resolve_program
    assert compiler._wire_confgen is cg._wire_confgen
    assert compiler._legacy_paths_to_v3 is cg._legacy_paths_to_v3
    assert compiler._wire_transform is tr._wire_transform
    assert calc._wire_calculation.__module__ == "confflow.producer.intent.compiler"
    # Fragment adapters are new and distinct from the payload builders.
    assert calc.calculation_fragment is not calc._wire_calculation
    assert cg.confgen_fragment is not cg._wire_confgen
    assert tr.transform_fragment is not tr._wire_transform


def test_default_dispatch_equals_implicit_for_all_executors() -> None:
    tors = [
        {
            "id": "t1",
            "bond": [1, 2],
            "model": "relative_rotation_grid",
            "angles": [0, 120, 240],
            "treatment": "enumerate",
        }
    ]
    doc = _intent(
        [
            {"card": "opt@v1", "program": "orca", "native": {"keyword": "B3LYP D3BJ Opt"}},
            {
                "card": "confgen@v1",
                "native": {"schema_version": 3, "torsions": copy.deepcopy(tors)},
            },
            {"card": "refine@v1"},
        ]
    )
    implicit = compile_intent(copy.deepcopy(doc))
    explicit = compile_intent(copy.deepcopy(doc), intent_registry=build_default_intent_registry())
    assert implicit == explicit
    assert compile_workflow(copy.deepcopy(implicit)).ok
    assert compile_workflow(copy.deepcopy(explicit)).ok
    assert [s["id"] for s in implicit["steps"]] == ["opt_1", "confgen_1", "refine_1"]
    # CompiledStep IDs and executors preserved.
    assert [s["executor"] for s in implicit["steps"]] == [
        "calculation",
        "confgen",
        "structure_transform",
    ]


def test_custom_handler_content_difference_real_workflow() -> None:
    custom = _custom_registry()
    base_step = {"program": "orca", "native": {"keyword": "B3LYP D3BJ SP"}, "role": "myrole"}
    sp_doc = _intent([{"id": "s1", **base_step, "card": "sp@v1"}])
    probe_doc = _intent([{"id": "s1", **base_step, "card": f"{CUSTOM_KEY}@v1"}])
    sp_out = compile_intent(copy.deepcopy(sp_doc))
    probe_out = compile_intent(copy.deepcopy(probe_doc), intent_registry=custom)
    assert compile_workflow(copy.deepcopy(sp_out)).ok
    assert compile_workflow(copy.deepcopy(probe_out)).ok
    sp_role = sp_out["steps"][0]["calculation"]["role"]
    probe_role = probe_out["steps"][0]["calculation"]["role"]
    assert sp_role == "myrole"
    assert probe_role == "probe_myrole"
    assert sp_role != probe_role
    assert probe_out["steps"][0]["executor"] == "calculation"
    # Same input with old key lacks the probe marker (no global pollution).
    assert "probe_myrole" not in str(sp_out)


def test_custom_handler_rejection_difference_same_input() -> None:
    custom = _custom_registry()
    bare = {"program": "orca", "native": {"keyword": "B3LYP D3BJ SP"}}
    sp_doc = _intent([{"id": "s1", **bare, "card": "sp@v1"}])
    probe_doc = _intent([{"id": "s1", **bare, "card": f"{CUSTOM_KEY}@v1"}])
    sp_out = compile_intent(copy.deepcopy(sp_doc))
    assert compile_workflow(copy.deepcopy(sp_out)).ok
    with pytest.raises(IntentCompilationError) as excinfo:
        compile_intent(copy.deepcopy(probe_doc), intent_registry=custom)
    assert "probe custom requires explicit role" in str(excinfo.value)
    assert excinfo.value.step_id == "s1"


def test_custom_reuses_existing_calculation_runtime() -> None:
    from confflow.execution.registry import default_registry

    exec_reg = default_registry()
    probe = CapabilityDescriptor(
        key=CUSTOM_KEY,
        executor="calculation",
        intent_handler=_probe_wire,
        card=copy.deepcopy(CUSTOM_CARD),
        fragment_keys=("calculation",),
    )
    assert probe.executor == "calculation"
    first = exec_reg.resolve_executor(probe.executor)
    second = exec_reg.resolve_executor("calculation")
    assert first is second
    from confflow.execution.registry import RegistryLookupError

    with pytest.raises(RegistryLookupError):
        exec_reg.resolve_executor(CUSTOM_KEY)


def test_registry_locally_immutable_and_nested_frozen() -> None:
    reg = _custom_registry()
    with pytest.raises(TypeError):
        reg.entries["injected"] = reg.entries["sp"]  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        reg.entries = {}  # type: ignore[misc]
    desc = reg.resolve("sp", "calculation")
    assert desc is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        desc.key = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        desc.card["executor"] = "mutated"  # type: ignore[typeddict-item]
    with pytest.raises((TypeError, AttributeError)):
        desc.card["checks"].append("mutated")  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        desc.card["check_params"]["injected"] = {}  # type: ignore[typeddict-item]
    # Isolated copies: mutating the thawed copy does not affect the registry.
    thawed = desc.card_dict()
    thawed["executor"] = "mutated"
    assert reg.resolve("sp", "calculation").card["executor"] == "calculation"


def test_builder_rejects_conflict_unknown_no_handler() -> None:
    default = build_default_intent_registry()
    dup = CapabilityDescriptor(
        key="sp",
        executor="calculation",
        intent_handler=default.resolve("sp").intent_handler,
        card=copy.deepcopy(CUSTOM_CARD),
        fragment_keys=("calculation",),
    )
    with pytest.raises(ValueError, match="conflicting"):
        build_intent_registry([*default.entries.values(), dup])
    bad_exec = CapabilityDescriptor(
        key="badkey",
        executor="analysis",
        intent_handler=_probe_wire,
        card=copy.deepcopy(CUSTOM_CARD),
        fragment_keys=("calculation",),
    )
    with pytest.raises(ValueError, match="unknown executor"):
        build_intent_registry([*default.entries.values(), bad_exec])
    no_handler = CapabilityDescriptor(
        key="nokey",
        executor="calculation",
        intent_handler=None,
        card=copy.deepcopy(CUSTOM_CARD),
        fragment_keys=("calculation",),
    )
    with pytest.raises(ValueError, match="no intent handler"):
        build_intent_registry([*default.entries.values(), no_handler])
    with pytest.raises(ValueError, match="fragment"):
        build_intent_registry(
            [
                *default.entries.values(),
                CapabilityDescriptor(
                    key="emptyfrag",
                    executor="calculation",
                    intent_handler=_probe_wire,
                    card=copy.deepcopy(CUSTOM_CARD),
                    fragment_keys=(),
                ),
            ]
        )


def test_fragment_validation_rejects_undeclared_and_overwrite() -> None:
    default = build_default_intent_registry()

    def _bad_undeclared(user, card, step_id):
        return {"calculation": {}, "bogus_slot": {}}

    bad = CapabilityDescriptor(
        key="badfrag",
        executor="calculation",
        intent_handler=_bad_undeclared,
        card=copy.deepcopy(CUSTOM_CARD),
        fragment_keys=("calculation",),
    )
    custom = build_intent_registry([*default.entries.values(), bad])
    with pytest.raises(IntentCompilationError, match="undeclared"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "s1",
                        "card": "badfrag@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ SP"},
                    }
                ]
            ),
            intent_registry=custom,
        )

    def _bad_overwrite(user, card, step_id):
        return {"calculation": {}, "label": "x"}

    bad2 = CapabilityDescriptor(
        key="badfrag2",
        executor="calculation",
        intent_handler=_bad_overwrite,
        card=copy.deepcopy(CUSTOM_CARD),
        fragment_keys=("calculation", "label"),
    )
    custom2 = build_intent_registry([*default.entries.values(), bad2])
    with pytest.raises(IntentCompilationError, match="generic fields"):
        compile_intent(
            _intent(
                [
                    {
                        "id": "s1",
                        "card": "badfrag2@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ SP"},
                    }
                ]
            ),
            intent_registry=custom2,
        )


def test_unknown_capability_failclosed_and_no_catalog_growth() -> None:
    reg = build_default_intent_registry()
    assert reg.resolve("bogus", "calculation") is None
    assert reg.card_of("bogus") is None
    with pytest.raises(IntentCompilationError) as excinfo:
        compile_intent(_intent([{"card": "bogus@v1"}]))
    assert "unknown card type 'bogus'" in str(excinfo.value)
    # Default catalog still exactly the 14 builtin cards.
    from confflow.producer.intent import intent_catalog

    catalog = intent_catalog()
    assert [c["type"] for c in catalog["cards"]] == sorted(CARD_TYPES)
    assert len(catalog["cards"]) == 14
    assert CUSTOM_KEY not in [c["type"] for c in catalog["cards"]]
    with pytest.raises(ValueError):
        get_card("bogus")
    assert CARD_VERSION == "v1"


def test_no_production_dummy_reference() -> None:
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
        text = (root / rel).read_text().lower()
        assert "dummy" not in text, rel
    # intent_catalog has no dummy key.
    from confflow.producer.intent import intent_catalog

    assert "dummy" not in str(intent_catalog()).lower()


def test_light_toplevel_no_science_no_handler_side_effect() -> None:
    root = Path(__file__).resolve().parents[2]
    for rel in (
        "confflow/producer/intent/compiler.py",
        "confflow/producer/intent/capabilities/descriptor.py",
        "confflow/producer/intent/capabilities/registry.py",
        "confflow/producer/intent/capabilities/calculation.py",
        "confflow/producer/intent/capabilities/confgen.py",
        "confflow/producer/intent/capabilities/transform.py",
    ):
        tree = ast.parse((root / rel).read_text())
        mods = {
            "." * n.level + (n.module or "")
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and (n.module or "") != "__future__"
        }
        mods |= {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
        assert not any(
            m.startswith("confflow.science")
            or m.startswith("...science")
            or m.startswith("....science")
            or m == "confflow.science"
            for m in mods
        ), (rel, mods)
    # No back-import of compiler from helpers/capabilities at top level.
    for rel in (
        "confflow/producer/intent/capabilities/calculation.py",
        "confflow/producer/intent/capabilities/confgen.py",
        "confflow/producer/intent/capabilities/transform.py",
        "confflow/producer/intent/capabilities/descriptor.py",
        "confflow/producer/intent/capabilities/registry.py",
    ):
        tree = ast.parse((root / rel).read_text())
        mods = {
            "." * n.level + (n.module or "")
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)
        }
        assert ".compiler" not in mods and "..compiler" not in mods, (rel, mods)
    code = (
        "import sys, confflow.producer.intent as I; "
        "bad=[m for m in sys.modules if m=='confflow.science' or m.startswith('confflow.science.')]; "
        "print('BAD:', bad); sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    code2 = (
        "import sys, confflow.producer.intent.capabilities.descriptor as D; "
        "bad=[m for m in sys.modules if 'capabilities.calculation' in m or 'capabilities.confgen' in m or 'capabilities.transform' in m]; "
        "print('BAD:', bad); sys.exit(1 if bad else 0)"
    )
    proc2 = subprocess.run([sys.executable, "-c", code2], capture_output=True, text=True)
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
