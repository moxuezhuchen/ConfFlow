#!/usr/bin/env python3

"""L1 orchestration rejection branches: recipes, resources, bindings.

Pins fail-closed behavior of the generic recipe lane
(``confflow.producer.intent.recipes``), the resources lane
(``confflow.producer.intent.resources``), the bindings lane, and the
small capability-handler rejection branches.  All collaborators are
real except explicitly blocked optional imports (proving the
fail-closed ``ImportError`` paths); evil mappings prove the defensive
``except`` seams stay total.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from confflow.producer.cards import CARD_TYPES, CARD_VERSION, get_card
from confflow.producer.intent import INTENT_SCHEMA
from confflow.producer.intent.common import IntentCompilationError
from confflow.producer.intent.recipes import (
    apply_recipe_assignment,
    apply_role_cards,
    missing_recipe_assignments,
    normalize_cards,
    run_recipe_lane,
    select_family_native,
)


class _EvilGet(dict):
    """Dict raising on one key to prove defensive ``except`` seams."""

    _fail_key = ""

    def get(self, key, default=None):
        if key == self._fail_key:
            raise RuntimeError("unreadable")
        return super().get(key, default)


def _hooks_none(executor):
    return None


def _wire_key_of(executor):
    return {"calculation": "calculation"}.get(executor)


def _card_access():
    from confflow.producer.cards import parse_card_ref
    from confflow.producer.intent.bindings import _auto_bindings  # noqa: F401

    return {
        "parse_ref": parse_card_ref,
        "get_card": get_card,
        "card_types": CARD_TYPES,
        "card_version": CARD_VERSION,
        "intent_schema": INTENT_SCHEMA,
    }


def test_apply_assignment_survives_unreadable_block() -> None:
    evil = _EvilGet({"id": "b1"})
    evil._fail_key = "calculation"

    def _patch(block, user, step_id):
        raise AssertionError("must not run without a block")

    patched = dict(evil)
    patched["id"] = "b1"
    evil2 = _EvilGet(patched)
    evil2._fail_key = "calculation"
    apply_recipe_assignment(evil2, {}, "b1", patch_block_fn=_patch, wire_block_key="calculation")
    assert evil2["_expanded"] is True


def test_apply_assignment_no_hook_marks_expanded_only() -> None:
    patched: dict = {"id": "b1", "executor": "confgen"}
    apply_recipe_assignment(patched, {}, "b1", patch_block_fn=None, wire_block_key=None)
    assert patched == {"id": "b1", "executor": "confgen", "_expanded": True}


def test_missing_assignments_skips_foreign_and_broken_steps() -> None:
    assert missing_recipe_assignments([123], set(), hooks_of=_hooks_none) == []  # type: ignore[list-item]

    class _EvilId(dict):
        def get(self, key, default=None):
            if key == "executor":
                raise RuntimeError("boom")
            return super().get(key, default)

    assert missing_recipe_assignments([_EvilId({"id": "b1"})], set(), hooks_of=_hooks_none) == []
    assert (
        missing_recipe_assignments([{"id": "b1", "executor": 123}], set(), hooks_of=_hooks_none)
        == []
    )


def test_run_recipe_lane_survives_unreadable_base_executor() -> None:
    class _EvilBase(dict):
        def get(self, key, default=None):
            if key == "executor":
                raise RuntimeError("boom")
            return super().get(key, default)

    wire, _, matched, appended = run_recipe_lane(
        [_EvilBase({"id": "b1"})],
        [{"id": "b1"}],
        hooks_of=_hooks_none,
        wire_key_of=_wire_key_of,
        select_family_native_fn=select_family_native,
    )
    assert matched == {"b1"}
    assert appended == []
    assert wire[0]["_expanded"] is True


def test_run_recipe_lane_survives_unreadable_base_block() -> None:
    class _EvilBlock(dict):
        def get(self, key, default=None):
            if key == "calculation":
                raise RuntimeError("boom")
            return super().get(key, default)

    wire, _, matched, _ = run_recipe_lane(
        [_EvilBlock({"id": "b1", "executor": "calculation"})],
        [{"id": "b1", "program": "orca", "native": {"k": "v"}}],
        hooks_of=_hooks_none,
        wire_key_of=_wire_key_of,
        select_family_native_fn=select_family_native,
    )
    assert matched == {"b1"}
    assert wire[0]["_expanded"] is True


def test_run_recipe_lane_selects_family_role_variant() -> None:
    wire, _, matched, _ = run_recipe_lane(
        [{"id": "b1", "executor": "calculation", "calculation": {"role": "r"}}],
        [{"id": "b1", "program": "orca", "native_by_role": {"r": {"k": "v"}}}],
        hooks_of=_hooks_none,
        wire_key_of=_wire_key_of,
        select_family_native_fn=select_family_native,
    )
    assert matched == {"b1"}
    assert wire[0]["_expanded"] is True


def test_run_recipe_lane_no_hook_executor_marks_expanded() -> None:
    wire, _, matched, _ = run_recipe_lane(
        [{"id": "c1", "executor": "confgen", "confgen": {"a": 1}}],
        [{"id": "c1"}],
        hooks_of=_hooks_none,
        wire_key_of=_wire_key_of,
        select_family_native_fn=select_family_native,
    )
    assert matched == {"c1"}
    assert wire[0]["_expanded"] is True


def test_normalize_cards_rejects_missing_base_ref() -> None:
    with pytest.raises(IntentCompilationError, match="must declare a base card"):
        normalize_cards({"my": {}})


def test_select_family_native_returns_native_copy() -> None:
    template = {"native": {"a": 1}}
    out = select_family_native(template, None, context="t")
    assert out == {"a": 1}
    assert out is not template["native"]


def test_select_family_native_rejects_bad_shapes() -> None:
    with pytest.raises(IntentCompilationError, match="must be a mapping"):
        select_family_native({"native_by_role": []}, "r", context="t")
    with pytest.raises(IntentCompilationError, match="must be a mapping"):
        select_family_native({"native_by_role": {"r": 123}}, "r", context="t")


def test_apply_role_cards_survives_unreadable_base() -> None:
    class _EvilId(dict):
        def get(self, key, default=None):
            if key == "executor":
                raise RuntimeError("boom")
            return super().get(key, default)

    covered = apply_role_cards(
        [_EvilId({"id": "b1"})],
        {"r": "mycard"},
        {},
        skip_ids=set(),
        recipe_id=None,
        role_block_of=_hooks_none,
        card_access=_card_access(),
        wire_key_of=_wire_key_of,
    )
    assert covered == set()

    class _EvilBlock(dict):
        def get(self, key, default=None):
            if key == "calculation":
                raise RuntimeError("boom")
            return super().get(key, default)

    covered2 = apply_role_cards(
        [_EvilBlock({"id": "b2", "executor": "calculation"})],
        {"r": "mycard"},
        {},
        skip_ids=set(),
        recipe_id=None,
        role_block_of=_hooks_none,
        card_access=_card_access(),
        wire_key_of=_wire_key_of,
    )
    assert covered2 == set()


def _role_base():
    return {
        "id": "b",
        "executor": "calculation",
        "calculation": {"role": "r", "program": "demo", "native": {"old": 1}},
    }


def test_apply_role_cards_rejects_non_callable_hook() -> None:
    with pytest.raises(IntentCompilationError, match="is not callable"):
        apply_role_cards(
            [_role_base()],
            {"r": "mycard"},
            {"mycard": {"card": "sp@v1", "program": "orca", "native": {"k": "v"}}},
            skip_ids=set(),
            recipe_id=None,
            role_block_of=lambda executor: 123,
            card_access=_card_access(),
            wire_key_of=_wire_key_of,
        )


def test_apply_role_cards_rejects_unknown_card_name() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    with pytest.raises(IntentCompilationError, match="unknown card"):
        apply_role_cards(
            [_role_base()],
            {"r": "missing"},
            {},
            skip_ids=set(),
            recipe_id=None,
            role_block_of=lambda executor: apply_role_card_block,
            card_access=_card_access(),
            wire_key_of=_wire_key_of,
        )


def test_apply_role_cards_rejects_bad_card_reference() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    with pytest.raises(IntentCompilationError, match="bad card reference"):
        apply_role_cards(
            [_role_base()],
            {"r": "bad"},
            {"bad": {"card": "bogus@v1"}},
            skip_ids=set(),
            recipe_id=None,
            role_block_of=lambda executor: apply_role_card_block,
            card_access=_card_access(),
            wire_key_of=_wire_key_of,
        )


def test_apply_role_cards_rejects_family_without_variant() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    with pytest.raises(IntentCompilationError, match="must be a non-empty mapping"):
        apply_role_cards(
            [_role_base()],
            {"r": "fam"},
            {"fam": {"card": "sp@v1", "native_by_role": {"r": {}}}},
            skip_ids=set(),
            recipe_id=None,
            role_block_of=lambda executor: apply_role_card_block,
            card_access=_card_access(),
            wire_key_of=_wire_key_of,
        )


def test_apply_role_cards_accepts_family_step_id_variant() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    wire = [
        {
            "id": "b",
            "executor": "calculation",
            "calculation": {"role": "sp", "program": "demo", "native": {"old": 1}},
        }
    ]
    covered = apply_role_cards(
        wire,
        {"b": "fam"},
        {"fam": {"card": "sp@v1", "program": "orca", "native_by_role": {"b": {"k": "v"}}}},
        skip_ids=set(),
        recipe_id="tspes",
        role_block_of=lambda executor: apply_role_card_block,
        card_access=_card_access(),
        wire_key_of=_wire_key_of,
    )
    assert covered == {"b"}
    assert wire[0]["calculation"]["native"] == {"k": "v"}


def test_apply_role_cards_rejects_missing_native() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    with pytest.raises(IntentCompilationError, match="must declare an explicit"):
        apply_role_cards(
            [_role_base()],
            {"b": "plain"},
            {"plain": {"card": "sp@v1", "program": "orca"}},
            skip_ids=set(),
            recipe_id=None,
            role_block_of=lambda executor: apply_role_card_block,
            card_access=_card_access(),
            wire_key_of=_wire_key_of,
        )


def test_checkpoints_strip_private_keys_without_edges() -> None:
    from confflow.producer.intent.resources import _apply_checkpoints

    doc: dict = {"steps": [{"id": "a", "_secret": 1, "executor": "calculation"}]}
    _apply_checkpoints(doc, {})
    assert doc["steps"] == [{"id": "a", "executor": "calculation"}]


def test_checkpoints_missing_lane_fails_closed(monkeypatch) -> None:
    import sys

    from confflow.producer.intent.resources import _apply_checkpoints

    monkeypatch.setitem(sys.modules, "confflow.producer.checkpoints", None)
    with pytest.raises(IntentCompilationError, match="not available"):
        _apply_checkpoints({"steps": []}, {"s1": {"step": "s0", "mode": "checkpoint"}})


def test_machine_profile_rejects_non_mapping_resolver_result(monkeypatch) -> None:
    import confflow.producer.machine as machine
    from confflow.producer.intent.resources import _apply_machine_profile

    monkeypatch.setattr(machine, "resolve_machine_resources", lambda *a, **k: [])
    with pytest.raises(IntentCompilationError, match="non-mapping"):
        _apply_machine_profile(
            [{"id": "s1", "executor": "calculation", "calculation": {}}],
            {"name": "m"},
        )


def test_bindings_require_adapter_and_missing_run_input() -> None:
    from confflow.producer.intent.bindings import _auto_bindings, _registry_input_ports

    capability = SimpleNamespace(requires_adapter=True, input_ports=[])
    with pytest.raises(IntentCompilationError, match="execution_adapter is required"):
        _registry_input_ports(
            "calculation", None, SimpleNamespace(resolve_executor=lambda e: capability)
        )

    port = SimpleNamespace(
        name="structures", kind=SimpleNamespace(value="structure"), is_required=True
    )
    registry = SimpleNamespace(
        resolve_executor=lambda e: SimpleNamespace(requires_adapter=False, input_ports=[port])
    )
    with pytest.raises(IntentCompilationError, match="no run input to bind"):
        _auto_bindings(["s1"], {"s1": {}}, {"s1": "calculation"}, {"s1": None}, {}, registry)


def test_calculation_recipe_assignment_skips_non_calculation() -> None:
    from confflow.producer.intent.capabilities.calculation import require_recipe_assignment

    assert require_recipe_assignment({}, {"executor": "confgen"}, "s1") is None


def test_calculation_role_block_rejects_missing_program() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    with pytest.raises(IntentCompilationError, match="must declare an explicit program"):
        apply_role_card_block(
            {"program": "demo"},
            {},
            get_card("sp"),
            {"keyword": "X"},
            "s1",
            card_name="c",
        )


def test_calculation_role_block_applies_recovery_params() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    patched = apply_role_card_block(
        copy.deepcopy({"program": "demo", "recovery": {"profile": "none"}}),
        {"program": "orca", "native": {"keyword": "X"}, "recovery_params": {"a": 1}},
        get_card("sp"),
        {"keyword": "X"},
        "s1",
        card_name="c",
    )
    assert patched["recovery"]["params"] == {"a": 1}
    assert patched["program"] == "orca"


def test_confgen_rejects_non_mapping_overrides() -> None:
    from confflow.producer.intent.capabilities.confgen import _wire_confgen

    with pytest.raises(IntentCompilationError, match="overrides must be a mapping"):
        _wire_confgen({"native": {"schema_version": 3}, "overrides": "x"}, "s1")


def test_transform_rejects_kindless_card() -> None:
    from confflow.producer.intent.capabilities.transform import _wire_transform

    with pytest.raises(IntentCompilationError, match="lacks a kind"):
        _wire_transform({}, {}, "s1")


def test_transform_accepts_explicit_none_native() -> None:
    from confflow.producer.intent.capabilities.transform import _wire_transform

    out = _wire_transform({"native": None}, {"transform_kind": "refine"}, "s1")
    assert out["kind"] == "refine"
    assert isinstance(out["native"], dict)


def test_transform_survives_missing_native_table(monkeypatch) -> None:
    import sys

    from confflow.producer.intent.capabilities.transform import _wire_transform

    monkeypatch.setitem(sys.modules, "confflow.execution.transform_executor", None)
    out = _wire_transform({"native": None}, {"transform_kind": "refine"}, "s1")
    assert out["kind"] == "refine"


# R2.3a (G18): test_analysis_missing_runtime_fails_closed retired with
# confflow/producer/intent/capabilities/analysis.py and confflow.analysis.


def test_calculation_role_block_rejects_missing_native() -> None:
    from confflow.producer.intent.capabilities.calculation import apply_role_card_block

    with pytest.raises(IntentCompilationError, match="must declare an explicit non-empty"):
        apply_role_card_block(
            {"program": "demo"},
            {"program": "orca"},
            get_card("sp"),
            None,
            "s1",
            card_name="c",
        )


def test_machine_profile_missing_lane_fails_closed(monkeypatch) -> None:
    import sys

    from confflow.producer.intent.resources import _apply_machine_profile

    monkeypatch.setitem(sys.modules, "confflow.producer.machine", None)
    with pytest.raises(IntentCompilationError, match="not available yet"):
        _apply_machine_profile(
            [{"id": "s1", "executor": "calculation", "calculation": {}}],
            {"name": "m"},
        )


def test_machine_profile_uses_schema_fallback_defaults(monkeypatch) -> None:
    import sys

    import confflow.producer.machine as machine
    from confflow.producer.intent.resources import _apply_machine_profile

    monkeypatch.setitem(sys.modules, "confflow.workflow.v4.schema", None)
    monkeypatch.setattr(
        machine,
        "resolve_machine_resources",
        lambda *a, **k: {"resources": {}, "provenance": {}},
    )
    prov = _apply_machine_profile(
        [{"id": "s1", "executor": "calculation", "calculation": {"program": "orca"}}],
        {"name": "m"},
    )
    assert prov == {"s1": {}}


def test_machine_profile_merges_executable_and_explicit_wins(monkeypatch) -> None:
    import confflow.producer.machine as machine
    from confflow.producer.intent.resources import _apply_machine_profile

    def _fake_resolve(profile, request, scheduler):
        return {
            "resources": {"cores_per_item": 2},
            "provenance": {
                "operational": {
                    "binding_id": "op-bind",
                    "executable": "/bin/prog",
                    "env": {"A": "1"},
                    "target": "t",
                }
            },
        }

    monkeypatch.setattr(machine, "resolve_machine_resources", _fake_resolve)
    steps = [
        {"id": "s1", "executor": "calculation", "calculation": {"program": "orca"}},
        {
            "id": "s2",
            "executor": "calculation",
            "calculation": {"program": "orca"},
            "execution": {"binding_id": "mine", "sandbox": {"root": "/tmp/x"}},
        },
    ]
    _apply_machine_profile(steps, {"name": "m"})
    assert steps[0]["execution"]["executable"] == "/bin/prog"
    assert steps[0]["execution"]["binding_id"] == "op-bind"
    assert steps[1]["execution"]["binding_id"] == "mine"
    assert steps[1]["execution"]["sandbox"] == {"root": "/tmp/x"}


def test_machine_profile_reraises_assembly_failure(monkeypatch) -> None:
    import confflow.producer.intent.capabilities.registry as regmod
    import confflow.producer.machine as machine
    from confflow.producer.intent.common import IntentCompilationError as _ICE
    from confflow.producer.intent.resources import _apply_machine_profile

    sentinel = _ICE("sentinel")

    def _raise(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(
        machine,
        "resolve_machine_resources",
        lambda *a, **k: {"resources": {}, "provenance": {}},
    )
    monkeypatch.setattr(regmod, "wire_block_key_for_executor", _raise)
    with pytest.raises(IntentCompilationError) as excinfo:
        _apply_machine_profile(
            [{"id": "s1", "executor": "calculation", "calculation": {}}],
            {"name": "m"},
        )
    assert excinfo.value is sentinel


def test_machine_profile_assembly_import_failure_wrapped(monkeypatch) -> None:
    import sys

    import confflow.producer.machine as machine
    from confflow.producer.intent.resources import _apply_machine_profile

    monkeypatch.setattr(
        machine,
        "resolve_machine_resources",
        lambda *a, **k: {"resources": {}, "provenance": {}},
    )
    monkeypatch.setitem(sys.modules, "confflow.producer.intent.capabilities.registry", None)
    with pytest.raises(IntentCompilationError, match="binding unavailable"):
        _apply_machine_profile(
            [{"id": "s1", "executor": "calculation", "calculation": {}}],
            {"name": "m"},
        )


def test_checkpoints_reraise_helper_failure(monkeypatch) -> None:
    import confflow.producer.checkpoints as checkpoints
    from confflow.producer.intent.common import IntentCompilationError as _ICE
    from confflow.producer.intent.resources import _apply_checkpoints

    sentinel = _ICE("sentinel")

    def _boom(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(checkpoints, "wire_checkpoint_reuse", _boom)
    with pytest.raises(IntentCompilationError) as excinfo:
        _apply_checkpoints(
            {"steps": [{"id": "s1"}]},
            {"s1": {"step": "s0", "mode": "checkpoint"}},
        )
    assert excinfo.value is sentinel


def test_checkpoints_wrap_helper_failure(monkeypatch) -> None:
    import confflow.producer.checkpoints as checkpoints
    from confflow.producer.intent.resources import _apply_checkpoints

    def _boom(*args, **kwargs):
        raise RuntimeError("lane down")

    monkeypatch.setattr(checkpoints, "wire_checkpoint_reuse", _boom)
    with pytest.raises(IntentCompilationError, match="checkpoint wiring failed"):
        _apply_checkpoints(
            {"steps": [{"id": "s1"}]},
            {"s1": {"step": "s0", "mode": "checkpoint"}},
        )


def test_checkpoints_reject_non_mapping_helper_result(monkeypatch) -> None:
    import confflow.producer.checkpoints as checkpoints
    from confflow.producer.intent.resources import _apply_checkpoints

    monkeypatch.setattr(checkpoints, "wire_checkpoint_reuse", lambda *a, **k: [])
    with pytest.raises(IntentCompilationError, match="non-mapping"):
        _apply_checkpoints(
            {"steps": [{"id": "s1"}]},
            {"s1": {"step": "s0", "mode": "checkpoint"}},
        )
