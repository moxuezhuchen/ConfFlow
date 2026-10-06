#!/usr/bin/env python3

"""L1 registry fail-closed assembly and query behavior.

Pins the observable contract of
``confflow.producer.intent.capabilities.registry``: bad declarations
raise ``ValueError`` at assembly (never a half-built registry), and
every query helper fails closed on corrupt metadata instead of
guessing.  Duck-typed probes exercise the documented ``Any``-typed
defensive paths; divergent descriptors are combined through the raw
``IntentRegistry`` constructor (which, unlike the builder, does not
pre-check hook consistency) to prove the query-time conflict errors.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from confflow.producer.intent.capabilities.descriptor import CapabilityDescriptor
from confflow.producer.intent.capabilities.registry import (
    IntentRegistry,
    _effective_seed_block_keys,
    _effective_wire_block_key,
    _recipe_hook_pair,
    _role_block_of,
    build_default_intent_registry,
    build_intent_registry,
    recipe_hooks_for_executor,
    rejected_step_keys_for_legacy_fallback,
    role_block_for_executor,
    seed_block_keys_for_registry,
    wire_block_key_for_executor,
)


def _handler(user, card, step_id):
    return {"calculation": {}}


BASE_CARD = {
    "executor": "calculation",
    "adapter": "standard",
    "profile": "standard",
    "checks": ["normal_termination"],
    "check_params": {},
    "recovery": "none",
    "default_role": None,
    "requires_explicit_bindings": False,
    "description": "probe",
}


def _desc(key="probe", **overrides):
    kw = {
        "key": key,
        "executor": "calculation",
        "intent_handler": _handler,
        "card": copy.deepcopy(BASE_CARD),
        "fragment_keys": ("calculation",),
    }
    kw.update(overrides)
    return CapabilityDescriptor(**kw)


def test_registry_rejects_non_mapping_entries() -> None:
    with pytest.raises(ValueError, match="entries must be a mapping"):
        IntentRegistry(entries=[])  # type: ignore[arg-type]


def test_registry_rejects_blank_and_foreign_and_mismatched_entries() -> None:
    good = _desc("k")
    with pytest.raises(ValueError, match="non-empty strings"):
        IntentRegistry(entries={"": good})
    with pytest.raises(ValueError, match="must be a CapabilityDescriptor"):
        IntentRegistry(entries={"k": "nope"})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="!="):
        IntentRegistry(entries={"other": good})


def test_resolve_mismatch_returns_none_and_card_of_is_isolated() -> None:
    reg = build_default_intent_registry()
    assert reg.resolve("sp", "confgen") is None
    assert reg.resolve("sp", "calculation") is not None
    first = reg.card_of("sp")
    assert first is not None and first["executor"] == "calculation"
    first["executor"] = "mutated"
    assert reg.card_of("sp")["executor"] == "calculation"


def test_builder_rejects_non_descriptor_item() -> None:
    with pytest.raises(ValueError, match="needs CapabilityDescriptor"):
        build_intent_registry(["nope"])  # type: ignore[list-item]


def test_builder_rejects_unreadable_runtime_registry() -> None:
    class _Unreadable:
        @property
        def resolve_executor(self):  # type: ignore[no-redef]
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="runtime registry unreadable"):
        build_intent_registry([_desc("a")], execution_registry=_Unreadable())


def test_builder_rejects_non_callable_runtime_resolver() -> None:
    with pytest.raises(ValueError, match="runtime registry unreadable"):
        build_intent_registry(
            [_desc("a")], execution_registry=SimpleNamespace(resolve_executor=123)
        )


def test_builder_reports_known_names_when_runtime_resolve_fails() -> None:
    from confflow.execution.registry import default_registry

    exec_reg = default_registry()
    bad = _desc("badkey", executor="bogus_exec")
    with pytest.raises(ValueError, match="unknown executor") as excinfo:
        build_intent_registry([bad], execution_registry=exec_reg)
    assert "calculation" in str(excinfo.value)


def test_builder_runtime_fallback_names_empty_when_names_unreadable() -> None:
    class _Failing:
        def resolve_executor(self, name):
            raise KeyError(name)

    bad = _desc("badkey", executor="analysis")
    with pytest.raises(ValueError, match="expected one of \\[\\]"):
        build_intent_registry([bad], execution_registry=_Failing())


def test_builder_rejects_empty_fragment_keys_injected() -> None:
    desc = _desc("a")
    object.__setattr__(desc, "fragment_keys", ())
    with pytest.raises(ValueError, match="declares no fragment keys"):
        build_intent_registry([desc])


def test_builder_rejects_bad_rejected_keys_injected() -> None:
    desc = _desc("a")
    object.__setattr__(desc, "rejected_step_keys", (123,))
    with pytest.raises(ValueError, match="bad rejected keys"):
        build_intent_registry([desc])


def test_builder_rejects_bad_and_outside_seed_keys_injected() -> None:
    bad = _desc("a")
    object.__setattr__(bad, "seed_block_keys", ("ok", 123))
    with pytest.raises(ValueError, match="bad seed block keys"):
        build_intent_registry([bad])
    outside = _desc("b")
    object.__setattr__(outside, "seed_block_keys", ("bogus",))
    with pytest.raises(ValueError, match="not in fragment_keys"):
        build_intent_registry([outside])


def test_builder_rejects_non_callable_hook_injected() -> None:
    desc = _desc("a")
    object.__setattr__(desc, "requires_assignment", 123)
    with pytest.raises(ValueError, match="must be callable or None"):
        build_intent_registry([desc])


def test_effective_wire_block_key_unreadable_raises() -> None:
    class _Evil:
        key = "k"

        @property
        def wire_block_key(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="wire_block_key unreadable"):
        _effective_wire_block_key(_Evil())


def test_effective_wire_block_key_rejects_non_string_and_outside() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        _effective_wire_block_key(
            SimpleNamespace(key="k", wire_block_key=123, fragment_keys=("a",))
        )
    with pytest.raises(ValueError, match="not in fragment_keys"):
        _effective_wire_block_key(
            SimpleNamespace(key="k", wire_block_key="bogus", fragment_keys=("a",))
        )


def test_effective_wire_block_key_rejects_unreadable_and_bad_fragments() -> None:
    class _BadFrag:
        key = "k"
        wire_block_key = ""

        @property
        def fragment_keys(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="fragment_keys unreadable"):
        _effective_wire_block_key(_BadFrag())
    with pytest.raises(ValueError, match="must start with a non-empty string"):
        _effective_wire_block_key(SimpleNamespace(key="k", wire_block_key="", fragment_keys=(123,)))


def test_effective_seed_keys_derive_for_missing_attribute() -> None:
    assert _effective_seed_block_keys(
        SimpleNamespace(executor="calculation", fragment_keys=("calculation",))
    ) == ("calculation",)
    assert (
        _effective_seed_block_keys(
            SimpleNamespace(executor="structure_transform", fragment_keys=("transform",))
        )
        == ()
    )


def test_effective_seed_keys_reject_bad_derived_first_key() -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        _effective_seed_block_keys(SimpleNamespace(executor="calculation", fragment_keys=(123,)))


def test_effective_seed_keys_none_derives_builtin_default() -> None:
    assert _effective_seed_block_keys(
        SimpleNamespace(seed_block_keys=None, executor="confgen", fragment_keys=("confgen",))
    ) == ("confgen",)
    assert (
        _effective_seed_block_keys(
            SimpleNamespace(
                seed_block_keys=None,
                executor="structure_transform",
                fragment_keys=("transform",),
            )
        )
        == ()
    )


def test_effective_seed_keys_none_rejects_bad_derived_first_key() -> None:
    with pytest.raises(ValueError, match="must be a non-empty string"):
        _effective_seed_block_keys(
            SimpleNamespace(seed_block_keys=None, executor="calculation", fragment_keys=(123,))
        )


def test_effective_seed_keys_unreadable_metadata() -> None:
    class _Unreadable:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="seed_block_keys unreadable"):
        _effective_seed_block_keys(_Unreadable())


def test_effective_seed_keys_reject_non_iterable_and_duplicates() -> None:
    with pytest.raises(ValueError, match="must be an iterable of strings"):
        _effective_seed_block_keys(SimpleNamespace(seed_block_keys=123))
    with pytest.raises(ValueError, match="carries duplicates"):
        _effective_seed_block_keys(SimpleNamespace(seed_block_keys=("a", "a")))


def test_effective_seed_keys_reject_outside_and_unreadable_fragments() -> None:
    with pytest.raises(ValueError, match="not in fragment_keys"):
        _effective_seed_block_keys(
            SimpleNamespace(seed_block_keys=("bogus",), fragment_keys=("a",))
        )

    class _BadFrag:
        seed_block_keys = ("a",)

        @property
        def fragment_keys(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="fragment_keys unreadable"):
        _effective_seed_block_keys(_BadFrag())


def test_effective_seed_keys_reject_bad_executor_metadata_and_first_key() -> None:
    class _BadExecutor:
        seed_block_keys = ()

        @property
        def executor(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="metadata unreadable"):
        _effective_seed_block_keys(_BadExecutor())
    with pytest.raises(ValueError, match="must be a non-empty string"):
        _effective_seed_block_keys(
            SimpleNamespace(seed_block_keys=(), executor="calculation", fragment_keys=(123,))
        )


def test_effective_seed_keys_missing_attr_metadata_unreadable() -> None:
    class _NoSeed:
        @property
        def executor(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="seed descriptor metadata unreadable"):
        _effective_seed_block_keys(_NoSeed())

    class _NoSeedBadExec:
        fragment_keys = ("calculation",)

        @property
        def executor(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="seed descriptor metadata unreadable"):
        _effective_seed_block_keys(_NoSeedBadExec())


def test_recipe_hook_pair_unreadable_and_invalid() -> None:
    class _Unreadable:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="metadata unreadable"):
        _recipe_hook_pair(_Unreadable())
    with pytest.raises(ValueError, match="requires_assignment must be callable"):
        _recipe_hook_pair(SimpleNamespace(requires_assignment=123, patch_recipe_step=None))
    with pytest.raises(ValueError, match="patch_recipe_step must be callable"):
        _recipe_hook_pair(SimpleNamespace(requires_assignment=None, patch_recipe_step=123))


def test_role_block_unreadable_and_invalid() -> None:
    class _Unreadable:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="metadata unreadable"):
        _role_block_of(_Unreadable())
    with pytest.raises(ValueError, match="must be callable or None"):
        _role_block_of(SimpleNamespace(apply_role_card_block=123))


def test_role_block_query_unreadable_none_and_bad_registry() -> None:
    class _Unreadable:
        @property
        def entries(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="intent registry unreadable"):
        role_block_for_executor(_Unreadable(), "calculation")
    assert role_block_for_executor(None, "calculation") is None
    with pytest.raises(ValueError, match="entries must be a mapping"):
        role_block_for_executor(SimpleNamespace(entries=[]), "calculation")


def test_role_block_query_rejects_unreadable_executor_and_conflicts() -> None:
    class _EvilDesc:
        @property
        def executor(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="executor unreadable"):
        role_block_for_executor(SimpleNamespace(entries={"e": _EvilDesc()}), "nope")

    def _hook_one(*args, **kwargs):
        return None

    def _hook_two(*args, **kwargs):
        return None

    reg2 = IntentRegistry(
        entries={
            "a": _desc("a", apply_role_card_block=_hook_one),
            "b": _desc("b", apply_role_card_block=_hook_two),
        }
    )
    with pytest.raises(ValueError, match="conflicting role-card hooks"):
        role_block_for_executor(reg2, "calculation")


def test_recipe_hooks_query_unreadable_none_bad_and_conflict() -> None:
    class _Unreadable:
        @property
        def entries(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="intent registry unreadable"):
        recipe_hooks_for_executor(_Unreadable(), "calculation")
    assert recipe_hooks_for_executor(None, "calculation") is None
    with pytest.raises(ValueError, match="entries must be a mapping"):
        recipe_hooks_for_executor(SimpleNamespace(entries=[]), "calculation")

    class _EvilDesc:
        @property
        def executor(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="executor unreadable"):
        recipe_hooks_for_executor(SimpleNamespace(entries={"e": _EvilDesc()}), "nope")

    def _req(user, base, step_id):
        return None

    def _patch(block, user, step_id):
        return block

    def _other_patch(block, user, step_id):
        return block

    reg = IntentRegistry(
        entries={
            "a": _desc("a", requires_assignment=_req, patch_recipe_step=_patch),
            "b": _desc("b", requires_assignment=_req, patch_recipe_step=_other_patch),
        }
    )
    with pytest.raises(ValueError, match="conflicting recipe hooks"):
        recipe_hooks_for_executor(reg, "calculation")


def test_recipe_hooks_query_rejects_partial_pair() -> None:
    def _req(user, base, step_id):
        return None

    reg = IntentRegistry(
        entries={"a": _desc("a", requires_assignment=_req, patch_recipe_step=None)}
    )
    with pytest.raises(ValueError, match="must be set together"):
        recipe_hooks_for_executor(reg, "calculation")


def test_builtin_rejected_metadata_duplicates_fail_closed(monkeypatch) -> None:
    import confflow.producer.intent.capabilities.calculation as calc

    monkeypatch.setattr(calc, "REJECTED_STEP_KEYS", ("a", "a"))
    with pytest.raises(ValueError, match="carries duplicates"):
        rejected_step_keys_for_legacy_fallback("calculation")


def test_seed_keys_for_registry_empty_falls_back_to_builtin_union() -> None:
    assert seed_block_keys_for_registry(None) == ("calculation", "confgen")
    assert seed_block_keys_for_registry(SimpleNamespace()) == (
        "calculation",
        "confgen",
    )


def test_seed_keys_for_registry_rejects_bad_registry_and_executor() -> None:
    class _Unreadable:
        @property
        def entries(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="intent registry unreadable"):
        seed_block_keys_for_registry(_Unreadable())
    with pytest.raises(ValueError, match="entries must be a mapping"):
        seed_block_keys_for_registry(SimpleNamespace(entries=[]))

    class _EvilDesc:
        @property
        def executor(self):
            raise RuntimeError("boom")

        seed_block_keys = ("a",)
        fragment_keys = ("a",)

    with pytest.raises(ValueError, match="executor unreadable"):
        seed_block_keys_for_registry(SimpleNamespace(entries={"e": _EvilDesc()}))


def test_seed_keys_for_registry_rejects_divergence() -> None:
    reg = IntentRegistry(
        entries={
            "a": _desc("a", seed_block_keys=("calculation",)),
            "b": _desc(
                "b",
                seed_block_keys=("extra",),
                fragment_keys=("calculation", "extra"),
            ),
        }
    )
    with pytest.raises(ValueError, match="conflicting seed_block_keys"):
        seed_block_keys_for_registry(reg)


def test_wire_block_for_executor_unreadable_bad_and_conflict() -> None:
    class _Unreadable:
        @property
        def entries(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="intent registry unreadable"):
        wire_block_key_for_executor(_Unreadable(), "calculation")
    with pytest.raises(ValueError, match="entries must be a mapping"):
        wire_block_key_for_executor(SimpleNamespace(entries=[]), "calculation")

    class _EvilDesc:
        @property
        def executor(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="executor unreadable"):
        wire_block_key_for_executor(SimpleNamespace(entries={"e": _EvilDesc()}), "nope")


def test_build_default_rejects_unknown_executor_card(monkeypatch) -> None:
    import confflow.producer.cards as cards

    real_get = cards.get_card

    def _evil(name, version=None):
        card = dict(real_get(name, version) if version else real_get(name))
        card["executor"] = "bogus_executor"
        return card

    monkeypatch.setattr(cards, "get_card", _evil)
    with pytest.raises(ValueError, match="unknown executor"):
        build_default_intent_registry()


def test_effective_wire_block_key_rejects_outside_explicit() -> None:
    class _BadFrag:
        key = "k"
        wire_block_key = "bogus"

        @property
        def fragment_keys(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="fragment_keys unreadable"):
        _effective_wire_block_key(_BadFrag())


def test_effective_seed_keys_none_with_unreadable_fragments() -> None:
    class _BadFrag:
        seed_block_keys = None

        @property
        def fragment_keys(self):
            raise RuntimeError("boom")

    with pytest.raises(ValueError, match="seed descriptor metadata unreadable"):
        _effective_seed_block_keys(_BadFrag())


def test_effective_seed_keys_flaky_read_fails_closed() -> None:
    class _Flaky:
        executor = "calculation"
        fragment_keys = ("a",)
        _reads = 0

        @property
        def seed_block_keys(self):
            type(self)._reads += 1
            if type(self)._reads > 1:
                raise RuntimeError("gone")
            return ("a",)

    _Flaky._reads = 0
    with pytest.raises(ValueError, match="seed_block_keys unreadable"):
        _effective_seed_block_keys(_Flaky())


def test_recipe_hook_pair_flaky_requires_assignment_fails_closed() -> None:
    class _Flaky:
        def __init__(self):
            self._seen = False

        def __getattribute__(self, name):
            if name == "requires_assignment":
                seen = object.__getattribute__(self, "_seen")
                object.__setattr__(self, "_seen", True)
                if seen:
                    raise RuntimeError("gone")
                return None
            if name == "patch_recipe_step":
                return None
            return object.__getattribute__(self, name)

    with pytest.raises(ValueError, match="requires_assignment unreadable"):
        _recipe_hook_pair(_Flaky())


def test_recipe_hook_pair_flaky_patch_step_fails_closed() -> None:
    class _Flaky:
        _reads = 0

        @property
        def requires_assignment(self):
            return None

        @property
        def patch_recipe_step(self):
            type(self)._reads += 1
            if type(self)._reads > 1:
                raise RuntimeError("gone")
            return None

    _Flaky._reads = 0
    with pytest.raises(ValueError, match="patch_recipe_step unreadable"):
        _recipe_hook_pair(_Flaky())


def test_role_block_flaky_hook_fails_closed() -> None:
    class _Flaky:
        _reads = 0

        @property
        def apply_role_card_block(self):
            type(self)._reads += 1
            if type(self)._reads > 1:
                raise RuntimeError("gone")
            return None

    _Flaky._reads = 0
    with pytest.raises(ValueError, match="apply_role_card_block unreadable"):
        _role_block_of(_Flaky())
