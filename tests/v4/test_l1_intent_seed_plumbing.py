#!/usr/bin/env python3
"""L1-A2a seed/resource plumbing (rehearsal, no recipe long-chain).

Covers only the A2a whitelist: compiler reject-fallback delegation,
provisional checkpoint seed block tables, seed_scope relocation, and
resources program extraction.  Recipe long-chain (A2b) and analysis/Dummy
final verification (A3) are explicitly out of scope here.
"""

from __future__ import annotations

import ast
import copy
import inspect
import textwrap

import pytest

import confflow.producer.intent.compiler as compiler
from confflow.producer.intent import INTENT_SCHEMA, IntentCompilationError, compile_intent
from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
from confflow.producer.intent.capabilities.registry import (
    build_default_intent_registry,
    build_intent_registry,
    rejected_step_keys_for_legacy_fallback,
    seed_block_keys_for_registry,
)


def _func_tree_without_docstring(func) -> ast.Module:
    """Parse one function ignoring docstring/comments (AST scope)."""
    src = inspect.getsource(func)
    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]
    return tree


def _string_constants(tree: ast.AST) -> set[str]:
    return {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _compared_strings(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for part in [node.left, *node.comparators]:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    out.add(part.value)
    return out


def _import_targets(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            out.add(f"{node.module or ''}:{','.join(n.name for n in node.names)}")
        elif isinstance(node, ast.Import):
            out.update(n.name for n in node.names)
    return out


def _call_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                out.add(func.id)
            elif isinstance(func, ast.Attribute):
                out.add(func.attr)
    return out


def _intent(steps):
    return {
        "schema": INTENT_SCHEMA,
        "globals": {"charge": 0, "multiplicity": 1},
        "steps": steps,
    }


_SEARCH_NATIVE = {"schema_version": 4, "search": {"starts": 8}}


def test_a2a_reject_fallback_delegates_no_branches() -> None:
    tree = _func_tree_without_docstring(compiler._reject_misplaced_fields)
    compared = _compared_strings(tree)
    assert "calculation" not in compared
    assert "confgen" not in compared
    assert "structure_transform" not in compared
    imports = _import_targets(tree)
    assert not any("REJECTED_STEP_KEYS" in target for target in imports)
    assert any("rejected_step_keys_for_legacy_fallback" in target for target in imports)
    assert "rejected_step_keys_for_legacy_fallback" in _call_names(tree) or any(
        "rejected_step_keys_for_legacy_fallback" in target for target in imports
    )
    # Old 4-arg form still rejects with identical text/sort/step.
    with pytest.raises(IntentCompilationError) as excinfo:
        compiler._reject_misplaced_fields({"program": "x"}, "confgen", "confgen", "s1")
    assert str(excinfo.value) == "step 's1' (confgen) cannot consume fields: program"
    assert excinfo.value.step_id == "s1"
    # Unknown executor stays empty (no raise).
    compiler._reject_misplaced_fields({"program": "x"}, "bogus", "analysis", "s9")
    # Multi-key sorting preserved.
    with pytest.raises(IntentCompilationError) as excinfo2:
        compiler._reject_misplaced_fields(
            {"seed": 1, "program": "x"}, "bogus2", "structure_transform", "t9"
        )
    assert "cannot consume fields: program, seed" in str(excinfo2.value)


def test_a2a_reject_assembly_values_match_legacy() -> None:
    assert rejected_step_keys_for_legacy_fallback("calculation") == frozenset({"preset"})
    assert rejected_step_keys_for_legacy_fallback("confgen") == frozenset(
        {
            "program",
            "role",
            "adapter",
            "profile",
            "checks",
            "check_params",
            "recovery",
            "recovery_params",
            "preset",
        }
    )
    assert rejected_step_keys_for_legacy_fallback("structure_transform") == frozenset(
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
    assert rejected_step_keys_for_legacy_fallback("analysis") == frozenset()
    assert rejected_step_keys_for_legacy_fallback("bogus-executor") == frozenset()


def test_a2a_reject_invalid_builtin_fails_closed(monkeypatch) -> None:
    import confflow.producer.intent.capabilities.calculation as calc_mod

    monkeypatch.setattr(calc_mod, "REJECTED_STEP_KEYS", frozenset({123}))
    with pytest.raises(ValueError, match="invalid"):
        rejected_step_keys_for_legacy_fallback("calculation")


def test_a2a_seed_block_registry_defaults() -> None:
    reg = build_default_intent_registry()
    assert seed_block_keys_for_registry(reg) == ("calculation", "confgen")
    assert reg.resolve("sp").effective_seed_block_keys == ("calculation",)
    assert reg.resolve("confgen").effective_seed_block_keys == ("confgen",)
    assert reg.resolve("refine").effective_seed_block_keys == ()
    # C2-era custom without the new field derives compat defaults.
    probe = CapabilityDescriptor(
        key="a2a_probe_calc",
        executor="calculation",
        intent_handler=reg.resolve("sp").intent_handler,
        card={"executor": "calculation"},
        fragment_keys=("calculation",),
    )
    assert probe.seed_block_keys == ()
    assert probe.effective_seed_block_keys == ("calculation",)
    tprobe = CapabilityDescriptor(
        key="a2a_probe_tr",
        executor="structure_transform",
        intent_handler=reg.resolve("refine").intent_handler,
        card={"executor": "calculation"},
        fragment_keys=("transform", "_preset_ref"),
    )
    assert tprobe.effective_seed_block_keys == ()


def test_a2a_seed_block_conflict_fails_closed() -> None:
    reg = build_default_intent_registry()
    a = CapabilityDescriptor(
        key="a2a_div_a",
        executor="calculation",
        intent_handler=reg.resolve("sp").intent_handler,
        card={"executor": "calculation"},
        fragment_keys=("calculation",),
        seed_block_keys=("calculation",),
    )
    b = CapabilityDescriptor(
        key="a2a_div_b",
        executor="calculation",
        intent_handler=reg.resolve("sp").intent_handler,
        card={"executor": "calculation"},
        fragment_keys=("calculation", "extra"),
        seed_block_keys=("extra",),
    )
    with pytest.raises(ValueError, match="seed_block_keys"):
        build_intent_registry([a, b])


# R2.2 (G18): test_a2a_goat_explicit_derived_scope retired with the
# goat card and the workflow_identity_only seed scope.


def test_a2a_typed_confgen_seed_scope_and_explicit() -> None:
    capped = _intent(
        [
            {
                "id": "c1",
                "card": "confgen@v1",
                "native": {
                    "schema_version": 4,
                    "search": {"starts": 8},
                },
            }
        ]
    )
    out = compile_intent(copy.deepcopy(capped))
    assert isinstance(out["steps"][0]["confgen"]["seed"], int)
    assert out["steps"][0]["annotations"]["producer_resolution"]["seed_source"] == "derived"
    assert out["steps"][0]["annotations"]["producer_resolution"]["seed_scope"] == "native_sampling"
    explicit = _intent(
        [
            {
                "id": "c1",
                "card": "confgen@v1",
                "seed": 4242,
                "native": {
                    "schema_version": 4,
                    "search": {"starts": 8},
                },
            }
        ]
    )
    out2 = compile_intent(copy.deepcopy(explicit))
    assert out2["steps"][0]["confgen"]["seed"] == 4242


def test_a2a_seed_scope_moved_to_seeds() -> None:
    from confflow.producer import seeds as seeds_mod

    assert hasattr(seeds_mod, "seed_scope_for_step")
    assert "seed_scope_for_step" in seeds_mod.__all__
    seeds_tree = _func_tree_without_docstring(seeds_mod.seed_scope_for_step)
    seeds_consts = _string_constants(seeds_tree)
    # R2.2: the goat "workflow_identity_only" scope is retired with GOAT;
    # confgen "native_sampling" stays the seeds-owned rule.
    assert "native_sampling" in seeds_consts
    # Compiler keeps only a delegating compat wrapper, no science branches.
    mod_tree = _func_tree_without_docstring(compiler._seed_scope)
    mod_consts = _string_constants(mod_tree)
    assert "native_sampling" not in mod_consts
    assert "seed_scope_for_step" in _call_names(mod_tree) or any(
        "seed_scope_for_step" in target for target in _import_targets(mod_tree)
    )
    # Behavior: none source yields None.
    assert seeds_mod.seed_scope_for_step({"executor": "confgen"}, "none") is None


def test_a2a_checkpoint_provisional_preserves_explicit() -> None:
    intent = {
        "schema": INTENT_SCHEMA,
        "globals": {"charge": 0, "multiplicity": 1},
        "steps": [
            {
                "id": "s_freq",
                "card": "opt@v1",
                "program": "gaussian",
                "native": {"keyword": "B3LYP/6-31G* freq"},
            },
            {
                "id": "s_opt",
                "card": "opt@v1",
                "program": "gaussian",
                "native": {"keyword": "B3LYP/6-31G* opt"},
                "reuse_checkpoint": {"step": "s_freq", "mode": "checkpoint"},
            },
            {
                "id": "capped",
                "card": "confgen@v1",
                "from": "run:structures",
                "seed": 12345,
                "native": {
                    "schema_version": 4,
                    "search": {"starts": 8},
                },
            },
        ],
    }
    out = compile_intent(copy.deepcopy(intent))
    by_id = {s["id"]: s for s in out["steps"]}
    assert by_id["capped"]["confgen"]["seed"] == 12345
    assert by_id["capped"]["annotations"]["producer_resolution"]["seed_source"] == "explicit"
    assert by_id["s_opt"]["bindings"]["checkpoint"]["cardinality"] == "one"


def test_a2a_checkpoint_derived_seed_survives_edges() -> None:
    intent = {
        "schema": INTENT_SCHEMA,
        "globals": {"charge": 0, "multiplicity": 1},
        "steps": [
            {
                "id": "s_freq",
                "card": "opt@v1",
                "program": "gaussian",
                "native": {"keyword": "B3LYP/6-31G* freq"},
            },
            {
                "id": "s_opt",
                "card": "opt@v1",
                "program": "gaussian",
                "native": {"keyword": "B3LYP/6-31G* opt"},
                "reuse_checkpoint": {"step": "s_freq", "mode": "checkpoint"},
            },
            {
                "id": "capped",
                "card": "confgen@v1",
                "from": "run:structures",
                "native": {
                    "schema_version": 4,
                    "search": {"starts": 8},
                },
            },
        ],
    }
    out = compile_intent(copy.deepcopy(intent))
    by_id = {s["id"]: s for s in out["steps"]}
    assert isinstance(by_id["capped"]["confgen"]["seed"], int)
    assert by_id["capped"]["annotations"]["producer_resolution"]["seed_source"] == "derived"


def test_a2a_machine_program_selection_declarative() -> None:
    doc = _intent(
        [
            {
                "id": "s1",
                "card": "sp@v1",
                "program": "orca",
                "native": {"keyword": "B3LYP SP"},
            }
        ]
    )
    profile = {
        "name": "m1",
        "total_cores": 8,
        "total_memory": "16GB",
        "executable": {"orca": "/opt/orca", "gaussian": "/opt/g16"},
    }
    out = compile_intent(copy.deepcopy(doc), machine_profile=profile)
    assert out["steps"][0]["execution"]["executable"] == "/opt/orca"
    doc2 = _intent(
        [
            {
                "id": "s1",
                "card": "sp@v1",
                "program": "gaussian",
                "native": {"keyword": "B3LYP SP"},
            }
        ]
    )
    out2 = compile_intent(copy.deepcopy(doc2), machine_profile=profile)
    assert out2["steps"][0]["execution"]["executable"] == "/opt/g16"
    # Resources helper keeps two-positional compatibility with an optional
    # keyword-only registry channel, and compiler reexport identity.
    import confflow.producer.intent.resources as res_mod

    params = inspect.signature(res_mod._apply_machine_profile).parameters
    assert list(params)[:2] == ["wire_steps", "machine_profile"]
    assert params["intent_registry"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["intent_registry"].default is None
    assert res_mod._apply_machine_profile is compiler._apply_machine_profile
    # Two-positional call stays valid (builtin defaults).
    steps = [
        {
            "id": "s1",
            "executor": "calculation",
            "calculation": {"program": "orca"},
            "resources": {"cores_per_item": 1, "memory_per_item": "1GiB"},
        }
    ]
    res_mod._apply_machine_profile(
        copy.deepcopy(steps),
        {"name": "m1", "total_cores": 8, "total_memory": "16GB"},
    )


def test_a2a_same_execution_registry_instance() -> None:
    from confflow.execution.registry import default_registry

    assert default_registry() is default_registry()
    reg = default_registry()
    doc = _intent(
        [
            {
                "id": "s1",
                "card": "sp@v1",
                "program": "orca",
                "native": {"keyword": "B3LYP SP"},
            }
        ]
    )
    wire = compile_intent(copy.deepcopy(doc), registry=reg)
    from confflow.producer.authoring import describe_step

    env = describe_step(copy.deepcopy(wire), "s1", registry=reg)
    assert env["ok"] is True
    assert env["result"]["capability"] == "calculation"
    assert default_registry() is reg


def test_a2a_v2_machine_binding_broken_fails_closed(monkeypatch) -> None:
    """Root frozen counterexample: broken assembly must not drop executable."""
    import confflow.producer.intent.capabilities.registry as reg_mod
    import confflow.producer.intent.resources as res_mod

    def _broken(_registry, _executor):
        raise ValueError("descriptor binding broken")

    monkeypatch.setattr(reg_mod, "wire_block_key_for_executor", _broken)
    steps = [{"id": "s", "executor": "calculation", "calculation": {"program": "g16"}}]
    with pytest.raises(IntentCompilationError) as excinfo:
        res_mod._apply_machine_profile(
            steps, {"name": "probe", "total_cores": 8, "total_memory": "16GB"}
        )
    assert excinfo.value.step_id == "s"
    assert "binding broken" in str(excinfo.value)


def test_a2a_v2_custom_descriptor_machine_probe() -> None:
    """Real compile with the same custom registry instance (not mock-only)."""
    from confflow.producer.intent.capabilities.calculation import calculation_fragment

    reg = build_default_intent_registry()
    custom_card = {
        "executor": "calculation",
        "adapter": "standard",
        "profile": "standard",
        "checks": ["normal_termination"],
        "check_params": {},
        "recovery": "none",
        "default_role": None,
        "requires_explicit_bindings": False,
        "description": "v2 machine probe",
    }

    def _probe_wire(user, card, step_id):
        from confflow.producer.intent.capabilities.calculation import _wire_calculation

        return {"calculation": _wire_calculation(user, card, step_id)}

    probe = CapabilityDescriptor(
        key="a2a_machine_probe",
        executor="calculation",
        intent_handler=_probe_wire,
        card=copy.deepcopy(custom_card),
        fragment_keys=("calculation",),
        description="v2 machine probe",
        seed_block_keys=("calculation",),
    )
    custom = build_intent_registry([*reg.entries.values(), probe])
    assert calculation_fragment is not None
    doc = _intent(
        [
            {
                "id": "m1",
                "card": "a2a_machine_probe@v1",
                "program": "orca",
                "role": "sp",
                "native": {"keyword": "B3LYP SP"},
            }
        ]
    )
    profile = {
        "name": "m1",
        "total_cores": 8,
        "total_memory": "16GB",
        "executable": {"orca": "/opt/orca", "gaussian": "/opt/g16"},
    }
    out = compile_intent(
        copy.deepcopy(doc),
        machine_profile=profile,
        intent_registry=custom,
    )
    assert out["steps"][0]["execution"]["executable"] == "/opt/orca"
    # Same descriptor instance flows through compile (resolve identity).
    assert custom.resolve("a2a_machine_probe") is probe


def test_a2a_v2_wire_conflict_fails_closed() -> None:
    from confflow.producer.intent.capabilities.registry import wire_block_key_for_executor

    reg = build_default_intent_registry()
    a = CapabilityDescriptor(
        key="a2a_v2_div_a",
        executor="calculation",
        intent_handler=reg.resolve("sp").intent_handler,
        card={"executor": "calculation"},
        fragment_keys=("calculation",),
        wire_block_key="calculation",
    )
    b = CapabilityDescriptor(
        key="a2a_v2_div_b",
        executor="calculation",
        intent_handler=reg.resolve("sp").intent_handler,
        card={"executor": "calculation"},
        fragment_keys=("calculation", "extra"),
        wire_block_key="extra",
    )
    with pytest.raises(ValueError, match="conflicting wire_block_key"):
        build_intent_registry([a, b])
    # Direct unvalidated registry with divergence also fails closed at query.
    from confflow.producer.intent.capabilities.registry import IntentRegistry

    direct = IntentRegistry(entries={"a2a_v2_div_a": a, "a2a_v2_div_b": b})
    with pytest.raises(ValueError, match="conflicting wire_block_key"):
        wire_block_key_for_executor(direct, "calculation")
    # Legal default (no entry) still returns builtin; unknown still None.
    only_t = build_intent_registry([reg.resolve("refine")])
    assert wire_block_key_for_executor(only_t, "calculation") == "calculation"
    assert wire_block_key_for_executor(only_t, "analysis") is None


def test_a2a_v2_seed_declared_bad_fails_closed() -> None:
    from confflow.producer.intent.capabilities.registry import IntentRegistry

    reg = build_default_intent_registry()
    bad = CapabilityDescriptor(
        key="a2a_v2_seed_bad",
        executor="calculation",
        intent_handler=reg.resolve("sp").intent_handler,
        card={"executor": "calculation"},
        fragment_keys=("calculation",),
        wire_block_key="calculation",
    )
    object.__setattr__(bad, "seed_block_keys", (123,))
    direct = IntentRegistry(entries={"a2a_v2_seed_bad": bad})
    with pytest.raises(ValueError, match="seed_block_keys"):
        seed_block_keys_for_registry(direct)
