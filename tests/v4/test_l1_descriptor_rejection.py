#!/usr/bin/env python3

"""L1 descriptor fail-closed validation and freeze/thaw behavior.

Every test pins observable behavior of
``confflow.producer.intent.capabilities.descriptor``: invalid
declarations raise ``ValueError`` (never a half-built descriptor), the
stored card is deeply frozen, and ``card_dict`` returns an isolated
mutable copy.  No production code is touched; invalid internal states
are injected with ``object.__setattr__`` only to prove the defensive
fallbacks stay total.
"""

from __future__ import annotations

import copy
from collections import UserDict
from types import MappingProxyType

import pytest

from confflow.producer.intent.capabilities.descriptor import (
    CapabilityDescriptor,
    _freeze_value,
    _thaw_value,
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


def _make(**overrides):
    kw = {
        "key": "probe",
        "executor": "calculation",
        "intent_handler": _handler,
        "card": copy.deepcopy(BASE_CARD),
        "fragment_keys": ("calculation",),
    }
    kw.update(overrides)
    return CapabilityDescriptor(**kw)


def test_empty_key_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        _make(key="  ")


def test_empty_executor_rejected() -> None:
    with pytest.raises(ValueError, match="executor must be a non-empty string"):
        _make(executor="")


def test_non_callable_handler_rejected() -> None:
    with pytest.raises(ValueError, match="intent_handler must be callable or None"):
        _make(intent_handler="not-callable")


def test_non_mapping_card_rejected() -> None:
    with pytest.raises(ValueError, match="card must be a mapping"):
        _make(card=["not", "a", "mapping"])


def test_empty_fragment_keys_rejected() -> None:
    with pytest.raises(ValueError, match="fragment_keys must be non-empty"):
        _make(fragment_keys=())


def test_blank_fragment_key_item_rejected() -> None:
    with pytest.raises(ValueError, match="fragment_keys must be non-empty strings"):
        _make(fragment_keys=("calculation", " "))


def test_duplicate_fragment_keys_rejected() -> None:
    with pytest.raises(ValueError, match="carries duplicates"):
        _make(fragment_keys=("calculation", "calculation"))


def test_non_string_description_rejected() -> None:
    with pytest.raises(ValueError, match="description must be a string"):
        _make(description=123)


def test_bad_rejected_step_keys_type_rejected() -> None:
    with pytest.raises(ValueError, match="rejected_step_keys must be a tuple"):
        _make(rejected_step_keys=123)


def test_blank_rejected_step_key_rejected() -> None:
    with pytest.raises(ValueError, match="rejected_step_keys must be non-empty strings"):
        _make(rejected_step_keys=("preset", ""))


def test_duplicate_rejected_step_keys_rejected() -> None:
    with pytest.raises(ValueError, match="rejected_step_keys carries duplicates"):
        _make(rejected_step_keys=("preset", "preset"))


def test_non_string_wire_block_key_rejected() -> None:
    with pytest.raises(ValueError, match="wire_block_key must be a string"):
        _make(wire_block_key=123)


def test_bad_seed_block_keys_type_rejected() -> None:
    with pytest.raises(ValueError, match="seed_block_keys must be a tuple"):
        _make(seed_block_keys=123)


def test_blank_seed_block_key_rejected() -> None:
    with pytest.raises(ValueError, match="seed_block_keys must be non-empty strings"):
        _make(seed_block_keys=("calculation", " "))


def test_duplicate_seed_block_keys_rejected() -> None:
    with pytest.raises(ValueError, match="seed_block_keys carries duplicates"):
        _make(seed_block_keys=("calculation", "calculation"))


def test_seed_block_key_outside_fragment_keys_rejected() -> None:
    with pytest.raises(ValueError, match="not in fragment_keys"):
        _make(seed_block_keys=("bogus",))


def test_non_callable_recipe_hook_rejected() -> None:
    with pytest.raises(ValueError, match="must be callable or None"):
        _make(requires_assignment="not-callable")


def test_card_dict_rejects_non_dict_thaw() -> None:
    desc = _make()
    object.__setattr__(desc, "card", 123)
    with pytest.raises(ValueError, match="did not thaw to a dict"):
        desc.card_dict()


def test_freeze_covers_tuple_set_frozenset_shapes() -> None:
    card = copy.deepcopy(BASE_CARD)
    card["tuple_val"] = (1, 2)
    card["set_val"] = {1, 2}
    card["frozen_val"] = frozenset({3, 4})
    desc = _make(card=card)
    assert isinstance(desc.card, MappingProxyType)
    assert isinstance(desc.card["tuple_val"], tuple)
    assert isinstance(desc.card["set_val"], frozenset)
    assert isinstance(desc.card["frozen_val"], frozenset)
    thawed = desc.card_dict()
    assert thawed["tuple_val"] == [1, 2]
    assert sorted(thawed["set_val"]) == [1, 2]
    assert sorted(thawed["frozen_val"]) == [3, 4]


def test_freeze_value_direct_shapes() -> None:
    assert _freeze_value((1, [2])) == (1, (2,))
    assert _freeze_value({1, 2}) == frozenset({1, 2})
    assert _freeze_value(frozenset({5})) == frozenset({5})


def test_thaw_generic_mapping_list_dict_shapes() -> None:
    assert _thaw_value(UserDict({"a": 1})) == {"a": 1}
    assert _thaw_value([1, {"a": 2}]) == [1, {"a": 2}]
    assert _thaw_value({"a": (1,)}) == {"a": [1]}


def test_thaw_frozenset_with_unrepresentable_member_falls_back_to_set() -> None:
    class _BadRepr:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    member = _BadRepr()
    out = _thaw_value(frozenset({member}))
    assert isinstance(out, set)
    assert out == {member}


def test_effective_seed_block_keys_survives_corrupt_seed_keys() -> None:
    desc = _make()
    object.__setattr__(desc, "seed_block_keys", 123)
    assert desc.effective_seed_block_keys == ("calculation",)


def test_effective_seed_block_keys_survives_empty_fragment_keys() -> None:
    class _BadIter:
        def __iter__(self):
            raise RuntimeError("uniterable")

    desc = _make()
    object.__setattr__(desc, "fragment_keys", _BadIter())
    assert desc.effective_seed_block_keys == ()


def test_non_tuple_fragment_keys_rejected() -> None:
    with pytest.raises(ValueError, match="fragment_keys must be a tuple"):
        _make(fragment_keys="calculation")
