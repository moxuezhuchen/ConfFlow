#!/usr/bin/env python3
"""L1-A2b2 root-review fix (rehearsal, b2-only).

b2 scope: named/role-card/family bodies in producer/intent/recipes.py,
calculation science in capabilities/calculation.apply_role_card_block owned by
the shared CapabilityDescriptor/IntentRegistry as the optional
``apply_role_card_block`` hook; compiler + recipes dispatch per actual wire
executor via ``role_block_for_executor`` (no uniform calc hook, no second
mapping, no executor literal or science in orchestration).  Default None
preserves the original non-calculation skip; unknown/conflict/non-callable
fail closed.  b1 bodies untouched.  Old tests unchanged.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

from confflow.domain.canonical import canonical_json_bytes
from confflow.producer.intent import INTENT_SCHEMA, compile_intent

GLOBALS = {"charge": 0, "multiplicity": 1}
CARD_FOR = {
    "ts": "ts@v1",
    "ts_freq": "ts_freq@v1",
    "ts_sp": "sp@v1",
    "irc": "irc@v1",
    "endpoint_opt": "opt@v1",
    "endpoint_freq": "freq@v1",
    "endpoint_sp": "sp@v1",
}
CALC_IDS = ["ts", "ts_freq", "ts_sp", "irc", "endpoint_opt", "endpoint_freq", "endpoint_sp"]


def _u(sid, **kw):
    base = {"id": sid, "card": CARD_FOR[sid]}
    base.update(kw)
    return base


def _quintuple(exc):
    return (
        type(exc).__name__,
        type(exc).__module__,
        str(exc),
        getattr(exc, "step_id", None),
        getattr(exc, "field_path", None),
    )


def test_b2_role_cover_uncovered_sp_bytes() -> None:
    user = [
        _u(s, program="orca", native={"keyword": "B3LYP"}) for s in CALC_IDS if s != "endpoint_sp"
    ]
    wire = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "recipe": "tspes",
            "globals": dict(GLOBALS),
            "cards": {
                "fam_sp": {"card": "sp@v1", "program": "orca", "native": {"keyword": "FAMSP"}}
            },
            "role_cards": {"sp": "fam_sp"},
            "steps": user,
        }
    )
    by_id = {s["id"]: s for s in wire["steps"]}
    assert by_id["endpoint_sp"]["calculation"]["native"] == {"keyword": "FAMSP"}
    assert by_id["endpoint_sp"]["annotations"]["producer_resolution"]["role_card"] == "fam_sp"
    assert by_id["ts"]["calculation"]["native"] == {"keyword": "B3LYP"}


def test_b2_matched_skip_role_ignored() -> None:
    user = [_u(s, program="orca", native={"keyword": "B3LYP"}) for s in CALC_IDS]
    wire = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "recipe": "tspes",
            "globals": dict(GLOBALS),
            "cards": {"fam": {"card": "sp@v1", "program": "orca", "native": {"keyword": "F"}}},
            "role_cards": {"ts": "fam"},
            "steps": user,
        }
    )
    by_id = {s["id"]: s for s in wire["steps"]}
    assert by_id["ts"]["calculation"]["native"] == {"keyword": "B3LYP"}


def test_b2_family_step_id_wins_and_variant_missing() -> None:
    user = [
        _u(s, program="orca", native={"keyword": "B3LYP"})
        for s in CALC_IDS
        if s not in ("ts_freq", "endpoint_freq")
    ]
    fam = {
        "card": "freq@v1",
        "program": "orca",
        "native_by_role": {"ts_freq": {"keyword": "F-TS_FREQ"}, "freq": {"keyword": "F-FREQ"}},
    }
    wire = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "recipe": "tspes",
            "globals": dict(GLOBALS),
            "cards": {"fam_low": fam},
            "role_cards": {"freq": "fam_low"},
            "steps": user,
        }
    )
    by_id = {s["id"]: s for s in wire["steps"]}
    assert by_id["ts_freq"]["calculation"]["native"] == {"keyword": "F-TS_FREQ"}
    assert by_id["endpoint_freq"]["calculation"]["native"] == {"keyword": "F-FREQ"}
    with pytest.raises(Exception) as ei:
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "recipe": "tspes",
                "globals": dict(GLOBALS),
                "cards": {
                    "fam_low": {
                        "card": "freq@v1",
                        "program": "orca",
                        "native_by_role": {"opt": {"keyword": "O"}},
                    }
                },
                "role_cards": {"freq": "fam_low"},
                "steps": user,
            }
        )
    cls, mod, msg, sid, _ = _quintuple(ei.value)
    assert cls == "IntentCompilationError"
    assert sid == "ts_freq"
    assert "declares no native_by_role variant for 'freq'" in msg


def test_b2_plain_purpose_mismatch_via_role_vs_step_id() -> None:
    user = [_u(s, program="orca", native={"keyword": "B3LYP"}) for s in CALC_IDS if s != "ts_freq"]
    with pytest.raises(Exception) as ei:
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "recipe": "tspes",
                "globals": dict(GLOBALS),
                "cards": {
                    "plain_opt": {"card": "opt@v1", "program": "orca", "native": {"keyword": "O"}}
                },
                "role_cards": {"freq": "plain_opt"},
                "steps": user,
            }
        )
    _, _, msg, sid, _ = _quintuple(ei.value)
    assert sid == "ts_freq"
    assert "needs purpose 'ts_freq' but card 'plain_opt' is 'opt'" in msg
    wire = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "recipe": "tspes",
            "globals": dict(GLOBALS),
            "cards": {
                "plain_opt": {"card": "opt@v1", "program": "orca", "native": {"keyword": "O"}}
            },
            "role_cards": {"ts_freq": "plain_opt"},
            "steps": user,
        }
    )
    by_id = {s["id"]: s for s in wire["steps"]}
    assert by_id["ts_freq"]["calculation"]["native"] == {"keyword": "O"}


def test_b2_named_cards_chain_and_expand() -> None:
    wire = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "cards": {
                "base": {"card": "sp@v1", "program": "orca", "native": {"keyword": "BASE"}},
                "mid": {"card": "base", "native": {"keyword": "MID"}},
            },
            "steps": [{"id": "s1", "card": "mid"}],
        }
    )
    by_id = {s["id"]: s for s in wire["steps"]}
    assert by_id["s1"]["calculation"]["native"] == {"keyword": "MID"}
    with pytest.raises(Exception) as ei:
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "steps": [{"id": "s1", "card": "no_such_named"}],
            }
        )
    _, _, msg, sid, _ = _quintuple(ei.value)
    assert sid == "s1"
    assert "references unknown card" in msg
    with pytest.raises(Exception) as ei2:
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {
                    "bad": {
                        "card": "sp@v1",
                        "native": {"keyword": "X"},
                        "native_by_role": {"opt": {"keyword": "O"}},
                    }
                },
                "steps": [{"id": "s1", "card": "bad"}],
            }
        )
    assert "declares both 'native' and 'native_by_role'" in str(ei2.value)


def test_b2_recipe_cards_normal_mode_and_front_doors() -> None:
    user = [_u(s, program="orca", native={"keyword": "B3LYP"}) for s in CALC_IDS]
    with pytest.raises(Exception) as ei:
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "recipe": "tspes",
                "globals": dict(GLOBALS),
                "cards": {"fam": {"card": "sp@v1", "program": "orca", "native": {"keyword": "F"}}},
                "role_cards": {"bogus": "no_card"},
                "steps": user,
            }
        )
    assert "role_cards reference unknown cards" in str(ei.value)
    with pytest.raises(Exception) as ei2:
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "recipe": "tspes",
                "globals": {"charge": 0},
                "recipe_cards": {"low_level": "c1"},
                "steps": [_u("ts", program="orca", native={"keyword": "X"})],
            }
        )
    assert "needs both 'low_level' and 'single_point'" in str(ei2.value)


def test_b2_resources_seed_provenance_preserved() -> None:
    user = [
        dict(
            _u(
                s,
                program="orca",
                native={"keyword": "B3LYP"},
                resources={"cores_per_item": 2},
                seed=7,
            )
        )
        for s in CALC_IDS
        if s != "endpoint_sp"
    ]
    wire = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "recipe": "tspes",
            "globals": dict(GLOBALS),
            "cards": {
                "fam_sp": {"card": "sp@v1", "program": "orca", "native": {"keyword": "FAMSP"}}
            },
            "role_cards": {"sp": "fam_sp"},
            "steps": user,
        }
    )
    by_id = {s["id"]: s for s in wire["steps"]}
    assert by_id["ts"]["calculation"]["seed"] == 7
    assert by_id["ts"]["resources"] == {"cores_per_item": 2}
    res = by_id["endpoint_sp"]["annotations"]["producer_resolution"]
    assert res["recipe_assignment"] is True
    assert res["role_card"] == "fam_sp"


def test_b2_wrappers_keep_signature_and_error_identity() -> None:
    import confflow.producer.intent.compiler as compiler
    import confflow.producer.intent.recipes as recipes
    from confflow.producer.intent.capabilities import calculation as cap

    assert list(inspect.signature(compiler._expand_named_step).parameters) == [
        "step",
        "resolved_cards",
    ]
    assert list(inspect.signature(recipes.expand_named_step).parameters) == [
        "step",
        "resolved_cards",
    ]
    assert compiler._expand_named_step is not recipes.expand_named_step
    assert list(inspect.signature(compiler._select_family_native).parameters) == [
        "template",
        "role",
        "context",
    ]
    assert compiler._select_family_native is not recipes.select_family_native
    assert list(inspect.signature(compiler._apply_role_cards).parameters) == [
        "wire_steps",
        "role_cards",
        "resolved_cards",
        "skip_ids",
        "recipe_id",
    ]
    assert compiler._apply_role_cards is not recipes.apply_role_cards
    assert (
        cap.apply_role_card_block.__module__ == "confflow.producer.intent.capabilities.calculation"
    )
    base_calc = {"program": "demo", "role": "sp", "native": {"keyword": "D"}}
    purpose = {
        "adapter": "standard",
        "profile": "standard",
        "checks": [],
        "check_params": {},
        "recovery": "none",
    }
    with pytest.raises(Exception) as e1:
        cap.apply_role_card_block(
            dict(base_calc),
            {"program": "  ", "native": {"keyword": "N"}},
            purpose,
            {"keyword": "N"},
            "s1",
            card_name="c1",
        )
    assert "must declare an explicit program" in str(e1.value)
    with pytest.raises(Exception) as e2:
        compiler._normalize_cards(
            {"bad": {"card": "sp@v1", "native": {"k": "v"}, "native_by_role": {"a": {"k": "v"}}}}
        )
    with pytest.raises(Exception) as e3:
        recipes.normalize_cards(
            {"bad": {"card": "sp@v1", "native": {"k": "v"}, "native_by_role": {"a": {"k": "v"}}}}
        )
    assert _quintuple(e2.value) == _quintuple(e3.value)


def test_b2_custom_registry_identity_and_conflicts() -> None:
    from confflow.producer.cards import get_card
    from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
    from confflow.producer.intent.capabilities.registry import (
        build_default_intent_registry,
        build_intent_registry,
        recipe_hooks_for_executor,
    )

    default = build_default_intent_registry()
    assert recipe_hooks_for_executor(default, "calculation") is not None
    assert recipe_hooks_for_executor(default, "confgen") is None
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            key="x",
            executor="calculation",
            intent_handler=lambda u, c, s: {},
            card=get_card("ts"),
            fragment_keys=("calculation",),
            requires_assignment="not-callable",
        )
    from confflow.producer.intent.capabilities.calculation import (
        calculation_fragment,
        patch_recipe_block,
        require_recipe_assignment,
    )

    def _other_require(user, base, step_id):
        return None

    def _other_patch(block, user, step_id):
        return dict(block)

    d1 = CapabilityDescriptor(
        key="ts",
        executor="calculation",
        intent_handler=calculation_fragment,
        card=get_card("ts"),
        fragment_keys=("calculation",),
        requires_assignment=require_recipe_assignment,
        patch_recipe_step=patch_recipe_block,
    )
    d2 = CapabilityDescriptor(
        key="sp",
        executor="calculation",
        intent_handler=calculation_fragment,
        card=get_card("sp"),
        fragment_keys=("calculation",),
        requires_assignment=_other_require,
        patch_recipe_step=_other_patch,
    )
    with pytest.raises(ValueError, match="conflicting recipe hooks"):
        build_intent_registry([d1, d2])


def test_b2_catalog_contract_bytes_unchanged() -> None:
    from confflow.producer.boundary import boundary_document
    from confflow.producer.contract import build_configuration_contract_v4
    from confflow.producer.intent.compiler import intent_catalog
    from confflow.producer.recipes import recipe_catalog_sha256_v4

    assert (
        recipe_catalog_sha256_v4()
        == "51c1483ffc5b114f34ca49c50e5e75d3cadbe6a2ea31a44281746fc714504f19"
    )
    cat = intent_catalog()
    assert "sha256:" + hashlib.sha256(canonical_json_bytes(cat)).hexdigest() == (
        "sha256:28225e1a432b6308230638d4166659b758bb73b29844da34de411081bdd23e96"
    )
    assert len(cat["cards"]) == 14
    env = build_configuration_contract_v4(
        producer_version="test", producer_commit="test", producer_dirty=False
    )
    bdoc = boundary_document()
    # J1 声明新增（叠加 E1 recipe 目录）：authoring request/response operation enum 增加
    # "structure_preview"（boundary + contract 派生 sha 随之更新）。
    assert "sha256:" + hashlib.sha256(canonical_json_bytes(env)).hexdigest() == (
        "sha256:d811e5e2feb1954728ae4468cc78887764e6535e7b7a47681e78fb2f881111c5"
    )
    assert "sha256:" + hashlib.sha256(canonical_json_bytes(bdoc)).hexdigest() == (
        "sha256:d9b5282bb8d6d3b3694a76bea6931b3099902f36fc74e6fe0f46727a9905e0b2"
    )


def test_b2_ast_central_branches_zero_no_grep() -> None:
    import textwrap

    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "confflow/producer/intent/compiler.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "programs.registry" not in node.module
    src = (root / "confflow/producer/intent/compiler.py").read_text()
    lane_start = src.index("    from .recipes import missing_recipe_assignments")
    lane_end = src.index("        matched = set(matched) | set(covered)")
    lane_src = textwrap.dedent(src[lane_start:lane_end])
    lane_tree = ast.parse(lane_src)
    for node in ast.walk(lane_tree):
        if isinstance(node, (ast.Compare, ast.If)):
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    assert child.value not in ("calculation", "confgen", "structure_transform")
    new_tree = ast.parse((root / "confflow/producer/intent/recipes.py").read_text())
    top = {
        (("." * n.level) + (n.module or "")) for n in new_tree.body if isinstance(n, ast.ImportFrom)
    }
    for mod in top:
        assert mod in ("__future__", "copy", "re", "collections.abc", "typing", ".common"), mod
    for node in new_tree.body:
        if isinstance(node, ast.ImportFrom):
            assert ".compiler" not in (("." * node.level) + (node.module or ""))
    assert "calculation step (no demo science)" in src


def test_b2_role_block_default_wiring_and_failclosed() -> None:
    from confflow.producer.cards import get_card
    from confflow.producer.intent.capabilities.calculation import (
        apply_role_card_block as calc_hook,
    )
    from confflow.producer.intent.capabilities.calculation import (
        calculation_fragment,
        patch_recipe_block,
        require_recipe_assignment,
    )
    from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
    from confflow.producer.intent.capabilities.registry import (
        build_default_intent_registry,
        build_intent_registry,
        role_block_for_executor,
    )

    default = build_default_intent_registry()
    assert role_block_for_executor(default, "calculation") is calc_hook
    assert role_block_for_executor(default, "confgen") is None
    assert role_block_for_executor(default, "structure_transform") is None
    assert role_block_for_executor(default, "no_such_executor") is None
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            key="x",
            executor="calculation",
            intent_handler=calculation_fragment,
            card=get_card("ts"),
            fragment_keys=("calculation",),
            apply_role_card_block="not-callable",
        )

    def _other_block(base, template, purpose, native, step_id, *, card_name):
        return dict(base)

    d1 = CapabilityDescriptor(
        key="ts",
        executor="calculation",
        intent_handler=calculation_fragment,
        card=get_card("ts"),
        fragment_keys=("calculation",),
        requires_assignment=require_recipe_assignment,
        patch_recipe_step=patch_recipe_block,
        apply_role_card_block=calc_hook,
    )
    d2 = CapabilityDescriptor(
        key="sp",
        executor="calculation",
        intent_handler=calculation_fragment,
        card=get_card("sp"),
        fragment_keys=("calculation",),
        requires_assignment=require_recipe_assignment,
        patch_recipe_step=patch_recipe_block,
        apply_role_card_block=_other_block,
    )
    with pytest.raises(ValueError, match="conflicting role-card hooks"):
        build_intent_registry([d1, d2])


def test_b2_default_confgen_role_skips_calc_science() -> None:
    import copy

    import confflow.producer.intent.recipes as recipes
    from confflow.producer.cards import CARD_TYPES, CARD_VERSION, get_card, parse_card_ref
    from confflow.producer.intent import INTENT_SCHEMA
    from confflow.producer.intent.capabilities import calculation as cap
    from confflow.producer.intent.capabilities.registry import (
        build_default_intent_registry,
        role_block_for_executor,
        wire_block_key_for_executor,
    )

    registry = build_default_intent_registry()
    card_access = {
        "parse_ref": parse_card_ref,
        "get_card": get_card,
        "card_types": CARD_TYPES,
        "card_version": CARD_VERSION,
        "intent_schema": INTENT_SCHEMA,
    }
    wire_steps = [{"id": "cg1", "executor": "confgen", "confgen": {"role": None}}]
    snapshot = copy.deepcopy(wire_steps)
    covered = recipes.apply_role_cards(
        wire_steps,
        {"cg1": "c_conf"},
        {"c_conf": {"card": "confgen@v1", "program": "orca", "native": {"keyword": "X"}}},
        skip_ids=set(),
        recipe_id=None,
        role_block_of=lambda ex: role_block_for_executor(registry, ex),
        card_access=card_access,
        wire_key_of=lambda ex: wire_block_key_for_executor(registry, ex),
    )
    assert covered == set()
    assert wire_steps == snapshot
    assert cap.apply_role_card_block is role_block_for_executor(registry, "calculation")


def test_b2_custom_noncalc_role_block_via_pipeline() -> None:
    import copy

    import confflow.producer.intent.recipes as recipes
    from confflow.producer.cards import CARD_TYPES, CARD_VERSION, get_card, parse_card_ref
    from confflow.producer.intent import INTENT_SCHEMA
    from confflow.producer.intent.capabilities import calculation as cap
    from confflow.producer.intent.capabilities.calculation import (
        calculation_fragment,
        patch_recipe_block,
        require_recipe_assignment,
    )
    from confflow.producer.intent.capabilities.confgen import confgen_fragment
    from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
    from confflow.producer.intent.capabilities.registry import (
        build_default_intent_registry,
        build_intent_registry,
        role_block_for_executor,
        wire_block_key_for_executor,
    )

    calc_hook = cap.apply_role_card_block
    calc_calls: list = []

    def _counting_calc(base, template, purpose, native, step_id, *, card_name):
        calc_calls.append(step_id)
        return calc_hook(base, template, purpose, native, step_id, card_name=card_name)

    custom_calls: list = []

    def _custom_confgen_block(base_block, template, purpose, native, step_id, *, card_name):
        custom_calls.append((step_id, card_name))
        out = copy.deepcopy(dict(base_block))
        out["custom_role_applied"] = card_name
        out["native"] = copy.deepcopy(dict(native))
        return out

    custom_registry = build_intent_registry(
        [
            CapabilityDescriptor(
                key="ts",
                executor="calculation",
                intent_handler=calculation_fragment,
                card=get_card("ts"),
                fragment_keys=("calculation",),
                requires_assignment=require_recipe_assignment,
                patch_recipe_step=patch_recipe_block,
                apply_role_card_block=_counting_calc,
            ),
            CapabilityDescriptor(
                key="confgen",
                executor="confgen",
                intent_handler=confgen_fragment,
                card=get_card("confgen"),
                fragment_keys=("confgen",),
                apply_role_card_block=_custom_confgen_block,
            ),
        ]
    )
    assert role_block_for_executor(custom_registry, "confgen") is _custom_confgen_block
    assert role_block_for_executor(custom_registry, "calculation") is _counting_calc
    card_access = {
        "parse_ref": parse_card_ref,
        "get_card": get_card,
        "card_types": CARD_TYPES,
        "card_version": CARD_VERSION,
        "intent_schema": INTENT_SCHEMA,
    }
    wire_steps = [{"id": "cg1", "executor": "confgen", "confgen": {"role": None}}]
    covered = recipes.apply_role_cards(
        wire_steps,
        {"cg1": "c_conf"},
        {"c_conf": {"card": "confgen@v1", "program": "orca", "native": {"keyword": "X"}}},
        skip_ids=set(),
        recipe_id=None,
        role_block_of=lambda ex: role_block_for_executor(custom_registry, ex),
        card_access=card_access,
        wire_key_of=lambda ex: wire_block_key_for_executor(custom_registry, ex),
    )
    assert covered == {"cg1"}
    assert custom_calls == [("cg1", "c_conf")]
    assert calc_calls == []
    assert wire_steps[0]["confgen"]["custom_role_applied"] == "c_conf"
    assert wire_steps[0]["confgen"]["native"] == {"keyword": "X"}
    assert "program" not in wire_steps[0]["confgen"]
    # Isolation: the default registry still skips the same non-calc mapping
    # (None hook) and never calls the calc hook for it.
    default = build_default_intent_registry()
    wire_default = [{"id": "cg1", "executor": "confgen", "confgen": {"role": None}}]
    covered_default = recipes.apply_role_cards(
        wire_default,
        {"cg1": "c_conf"},
        {"c_conf": {"card": "confgen@v1", "program": "orca", "native": {"keyword": "X"}}},
        skip_ids=set(),
        recipe_id=None,
        role_block_of=lambda ex: role_block_for_executor(default, ex),
        card_access=card_access,
        wire_key_of=lambda ex: wire_block_key_for_executor(default, ex),
    )
    assert covered_default == set()
    assert wire_default == [{"id": "cg1", "executor": "confgen", "confgen": {"role": None}}]


def test_b2_no_uniform_calc_hook_in_pipeline() -> None:
    import inspect

    import confflow.producer.intent.compiler as compiler
    import confflow.producer.intent.recipes as recipes

    assert "role_block_of" in inspect.signature(recipes.apply_role_cards).parameters
    assert "apply_block_fn" not in inspect.signature(recipes.apply_role_cards).parameters
    assert list(inspect.signature(compiler._apply_role_cards).parameters) == [
        "wire_steps",
        "role_cards",
        "resolved_cards",
        "skip_ids",
        "recipe_id",
    ]
    root = Path(__file__).resolve().parents[2]
    compiler_src = (root / "confflow/producer/intent/compiler.py").read_text()
    assert "from .capabilities.calculation import apply_role_card_block" not in compiler_src
    assert "role_block_for_executor" in compiler_src
    recipes_src = (root / "confflow/producer/intent/recipes.py").read_text()
    assert "role_block_of" in recipes_src
    assert "apply_role_card_block" not in recipes_src
