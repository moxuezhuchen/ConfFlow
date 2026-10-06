#!/usr/bin/env python3

"""L1-A2b1 recipe assignment hooks (rehearsal freeze, b1-only).

b1 scope only: recipe hooks + explicit assignment lane.  b2 boundary
(``_apply_role_cards``/``_expand_named_step`` bodies) intentionally untouched;
no claim of overall zeroing.  Old 12-cap / C0 / A1 / A2a tests unchanged.
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


def test_b1_require_missing_program_and_native_atomically() -> None:
    for bad in (_u("ts"), _u("ts", program="orca"), _u("ts", native={"keyword": "X"})):
        with pytest.raises(Exception) as ei:
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "recipe": "tspes",
                    "globals": dict(GLOBALS),
                    "steps": [bad],
                }
            )
        cls, mod, msg, sid, fp = _quintuple(ei.value)
        assert cls == "IntentCompilationError"
        assert mod == "confflow.producer.intent"
        assert msg == (
            "step 'ts': recipe assignments require explicit program and native "
            "(demo recipe science must not leak into production)"
        )
        assert sid == "ts"
        assert fp is None


def test_b1_patch_order_unknown_program_before_native_nonmapping() -> None:
    doc = {
        "schema": INTENT_SCHEMA,
        "recipe": "tspes",
        "globals": dict(GLOBALS),
        "steps": [
            _u("ts", program="no_such_program_xyz", native={"keyword": "B3LYP"}),
            _u("ts_freq", program="orca", native="NOT_A_MAPPING"),
        ],
    }
    with pytest.raises(Exception) as ei:
        compile_intent(doc)
    cls, mod, msg, sid, _ = _quintuple(ei.value)
    assert sid == "ts"
    assert "names unknown program 'no_such_program_xyz'" in msg
    # Second step alone fails on native mapping.
    doc2 = {
        "schema": INTENT_SCHEMA,
        "recipe": "tspes",
        "globals": dict(GLOBALS),
        "steps": [
            _u("ts", program="orca", native={"keyword": "B3LYP"}),
            _u("ts_freq", program="orca", native="NOT_A_MAPPING"),
        ],
    }
    with pytest.raises(Exception) as ei2:
        compile_intent(doc2)
    _, _, msg2, sid2, _ = _quintuple(ei2.value)
    assert sid2 == "ts_freq"
    assert msg2 == "step 'ts_freq' native must be a mapping"


def test_b1_missing_gate_sorted_and_complete() -> None:
    with pytest.raises(Exception) as ei:
        compile_intent(
            {"schema": INTENT_SCHEMA, "recipe": "tspes", "globals": dict(GLOBALS), "steps": []}
        )
    assert str(ei.value) == (
        "recipe requires explicit program/native assignments for every "
        "calculation step (no demo science); missing: "
        "endpoint_freq, endpoint_opt, endpoint_sp, irc, ts, ts_freq, ts_sp"
    )
    with pytest.raises(Exception) as ei2:
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "recipe": "tspes",
                "globals": dict(GLOBALS),
                "steps": [_u("ts", program="orca", native={"keyword": "B3LYP"})],
            }
        )
    assert "missing: endpoint_freq, endpoint_opt, endpoint_sp, irc, ts_freq, ts_sp" in str(
        ei2.value
    )
    assert "reaction_profile" not in str(ei2.value)


def test_b1_valid_full_wire_bytes_stable() -> None:
    steps = [
        _u(
            sid,
            program="orca",
            native={"keyword": f"B3LYP {sid}"},
            resources={"cores_per_item": 2},
            seed=7,
        )
        for sid in CALC_IDS
    ]
    wire = compile_intent(
        {"schema": INTENT_SCHEMA, "recipe": "tspes", "globals": dict(GLOBALS), "steps": steps}
    )
    digest = "sha256:" + hashlib.sha256(canonical_json_bytes(wire)).hexdigest()
    assert digest == "sha256:2b29b03b489acd2249b325967f3e1b25b7d263822da0756e1462d0e0fde9bf12"
    by_id = {s["id"]: s for s in wire["steps"]}
    for sid in CALC_IDS:
        res = by_id[sid]["annotations"]["producer_resolution"]
        assert res["recipe_assignment"] is True
        assert by_id[sid]["calculation"]["seed"] == 7
        assert "resources" in by_id[sid]
    assert (
        by_id["reaction_profile"]["annotations"]["producer_resolution"].get("recipe_assignment")
        is None
    )


def test_b1_catalog_bytes_unchanged() -> None:
    from confflow.producer.intent.compiler import intent_catalog
    from confflow.producer.recipes import RECIPE_IDS_V4, recipe_catalog_sha256_v4

    assert list(RECIPE_IDS_V4) == [
        "optimize",
        "single_point",
        "frequency",
        "opt_freq",
        "transition_state",
        "irc",
        "qst2",
        "qst3",
        "neb",
        "goat",
        "tspes",
        "confgen_torsion",
        "monomer_conformers",
    ]
    assert (
        recipe_catalog_sha256_v4()
        == "51c1483ffc5b114f34ca49c50e5e75d3cadbe6a2ea31a44281746fc714504f19"
    )
    cat = intent_catalog()
    assert "sha256:" + hashlib.sha256(canonical_json_bytes(cat)).hexdigest() == (
        "sha256:28225e1a432b6308230638d4166659b758bb73b29844da34de411081bdd23e96"
    )


def test_b1_contract_boundary_bytes_unchanged() -> None:
    from confflow.producer.boundary import boundary_document
    from confflow.producer.contract import build_configuration_contract_v4

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


def test_b1_custom_registry_spy_enters_hooks() -> None:
    from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
    from confflow.producer.intent.capabilities.registry import (
        build_default_intent_registry,
        build_intent_registry,
        recipe_hooks_for_executor,
    )

    default = build_default_intent_registry()
    calc_req, calc_patch = recipe_hooks_for_executor(default, "calculation")
    assert callable(calc_req) and callable(calc_patch)
    assert recipe_hooks_for_executor(default, "confgen") is None
    assert recipe_hooks_for_executor(default, "structure_transform") is None
    assert recipe_hooks_for_executor(default, "bogus_executor_xyz") is None

    calls: list[tuple] = []

    def spy_require(user, base, step_id):
        calls.append(("require", step_id, str(base.get("executor"))))
        return calc_req(user, base, step_id)

    def spy_patch(block, user, step_id):
        calls.append(("patch", step_id))
        return calc_patch(block, user, step_id)

    # Same custom registry instance flows through the lane (keyword-explicit).
    from confflow.producer.cards import CARD_TYPES, get_card
    from confflow.producer.intent.capabilities.registry import (
        FRAGMENT_KEYS_BY_EXECUTOR,
        SEED_BLOCK_KEYS_BY_EXECUTOR,
    )

    descriptors: list[CapabilityDescriptor] = []
    for card_type in CARD_TYPES:
        card = get_card(card_type)
        executor = str(card["executor"])
        frag = FRAGMENT_KEYS_BY_EXECUTOR[executor]
        from confflow.producer.intent.capabilities.calculation import REJECTED_STEP_KEYS as _CR
        from confflow.producer.intent.capabilities.calculation import calculation_fragment
        from confflow.producer.intent.capabilities.confgen import REJECTED_STEP_KEYS as _GR
        from confflow.producer.intent.capabilities.confgen import confgen_fragment
        from confflow.producer.intent.capabilities.transform import REJECTED_STEP_KEYS as _TR
        from confflow.producer.intent.capabilities.transform import transform_fragment

        if executor == "calculation":
            handler, rej, req, pat = (
                calculation_fragment,
                tuple(sorted(set(_CR))),
                spy_require,
                spy_patch,
            )
        elif executor == "confgen":
            handler, rej, req, pat = confgen_fragment, tuple(sorted(set(_GR))), None, None
        else:
            handler, rej, req, pat = transform_fragment, tuple(sorted(set(_TR))), None, None
        descriptors.append(
            CapabilityDescriptor(
                key=card_type,
                executor=executor,
                intent_handler=handler,
                card=card,
                fragment_keys=frag,
                description=str(card.get("description", "")),
                rejected_step_keys=rej,
                wire_block_key=str(frag[0]),
                seed_block_keys=tuple(SEED_BLOCK_KEYS_BY_EXECUTOR[executor]),
                requires_assignment=req,
                patch_recipe_step=pat,
            )
        )
    custom = build_intent_registry(descriptors)
    # Querying with the same instance returns the spy pair (identity).
    got = recipe_hooks_for_executor(custom, "calculation")
    assert got is not None and got[0] is spy_require and got[1] is spy_patch

    steps = [_u(sid, program="orca", native={"keyword": f"B3LYP {sid}"}) for sid in CALC_IDS]
    wire = compile_intent(
        {"schema": INTENT_SCHEMA, "recipe": "tspes", "globals": dict(GLOBALS), "steps": steps},
        intent_registry=custom,
    )
    assert wire["schema"] == "confflow.workflow.v4"
    assert {c[1] for c in calls if c[0] == "require"} == set(CALC_IDS)
    assert {c[1] for c in calls if c[0] == "patch"} == set(CALC_IDS)
    # No fake executor final verification: TransformExecutor.execute never invoked here.


def test_b1_wrappers_keep_signature_and_error_identity() -> None:
    import confflow.producer.intent.compiler as compiler
    from confflow.producer.intent.capabilities import calculation as cap

    # Same 3-param shape (names/kinds; string forms differ under future-annotations).
    assert list(inspect.signature(compiler._require_recipe_assignment).parameters) == [
        "user",
        "base",
        "step_id",
    ]
    assert list(inspect.signature(cap.require_recipe_assignment).parameters) == [
        "user",
        "base",
        "step_id",
    ]
    assert list(inspect.signature(compiler._patch_recipe_step).parameters) == [
        "patched",
        "user",
        "step_id",
    ]
    assert str(inspect.signature(compiler._require_recipe_assignment)) == str(
        inspect.signature(cap.require_recipe_assignment)
    )
    # Not the same objects (no `is` promise); real modules kept.
    assert compiler._require_recipe_assignment is not cap.require_recipe_assignment
    assert (
        cap.require_recipe_assignment.__module__
        == "confflow.producer.intent.capabilities.calculation"
    )
    assert cap.patch_recipe_block.__module__ == "confflow.producer.intent.capabilities.calculation"
    # Wrapper error equals capability error (unwrapped propagation).
    base = {"id": "ts", "executor": "calculation", "calculation": {"program": "demo", "role": "ts"}}
    with pytest.raises(Exception) as e1:
        compiler._require_recipe_assignment({"id": "ts"}, base, "ts")
    with pytest.raises(Exception) as e2:
        cap.require_recipe_assignment({"id": "ts"}, base, "ts")
    assert _quintuple(e1.value) == _quintuple(e2.value)
    # Legal None hook skips (confgen base analogue).
    conf_base = {"id": "c1", "executor": "confgen", "confgen": {}}
    compiler._require_recipe_assignment({}, conf_base, "c1")


def test_b1_registry_conflict_and_bad_hook_fail_closed() -> None:
    from confflow.producer.cards import get_card
    from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
    from confflow.producer.intent.capabilities.registry import build_intent_registry

    card = get_card("ts@v1".split("@")[0])
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            key="x",
            executor="calculation",
            intent_handler=lambda u, c, s: {},
            card=card,
            fragment_keys=("calculation",),
            requires_assignment="not-callable",
        )
    # Same-executor divergent non-None hooks fail at assembly (never silent None).
    # (None coexists with set for C2-era compat, so conflict needs two distinct sets.)
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
    # Partial (one set / one None) also fails closed.
    d3 = CapabilityDescriptor(
        key="sp",
        executor="calculation",
        intent_handler=calculation_fragment,
        card=get_card("sp"),
        fragment_keys=("calculation",),
        requires_assignment=require_recipe_assignment,
        patch_recipe_step=None,
    )
    with pytest.raises(ValueError, match="conflicting recipe hooks"):
        build_intent_registry([d1, d3])


def test_b1_ast_only_b1_lane_cleared_no_grep_false_positive() -> None:
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "confflow/producer/intent/compiler.py").read_text())
    # Scoped: only the recipe lane + missing region (run_recipe_lane call site
    # and missing helper site) must carry no executor-literal Compare/If.
    # b2 bodies (_apply_role_cards/_expand_named_step) intentionally still
    # carry them -- do not claim overall zeroing.
    src = (root / "confflow/producer/intent/compiler.py").read_text()
    lane_start = src.index("run_recipe_lane(")
    lane_end = src.index("if base_wire_steps and role_cards:")
    lane_src = src[lane_start:lane_end]
    lane_tree = ast.parse(lane_src)
    for node in ast.walk(lane_tree):
        if isinstance(node, (ast.Compare, ast.If)):
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    assert child.value not in (
                        "calculation",
                        "confgen",
                        "structure_transform",
                    ), "b1 lane still branches on executor literal"
    # ImportFrom(programs.registry) must be zero in compiler (kept).
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "programs.registry" not in node.module
    # Positive prose example must not trip a naive text grep: the missing
    # message legitimately contains "calculation step".
    assert "calculation step (no demo science)" in src
    # New module stays light (stdlib + .common only at top level).
    new_tree = ast.parse((root / "confflow/producer/intent/recipes.py").read_text())
    top = {
        ("." * n.level + (n.module or "")) for n in new_tree.body if isinstance(n, ast.ImportFrom)
    }
    for mod in top:
        assert mod in ("__future__", "copy", "collections.abc", "typing", ".common"), mod
    for node in new_tree.body:
        if isinstance(node, ast.ImportFrom):
            assert ".compiler" not in ("." * node.level + (node.module or ""))
    # Descriptor stays stdlib-only at top level.
    desc_tree = ast.parse(
        (root / "confflow/producer/intent/capabilities/descriptor.py").read_text()
    )
    desc_top = {
        ("." * n.level + (n.module or "")) for n in desc_tree.body if isinstance(n, ast.ImportFrom)
    }
    for mod in desc_top:
        assert mod in (
            "__future__",
            "copy",
            "dataclasses",
            "collections.abc",
            "types",
            "typing",
        ), mod


def test_b1_no_cycle_and_catalog_module_untouched() -> None:
    root = Path(__file__).resolve().parents[2]
    before = (root / "confflow/producer/recipes.py").read_text()
    assert "requires_assignment" not in before
    assert "patch_recipe_block" not in before
    import subprocess
    import sys

    code = (
        "import sys; "
        "import confflow.producer.intent.recipes as r; "
        "bad=[m for m in sys.modules if m=='confflow.science' or m.startswith('confflow.science.')]; "
        "print('BAD:'+str(bad)); "
        "sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
