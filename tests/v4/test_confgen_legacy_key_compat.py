"""Keep legacy key semantics separate from custom component merge policies."""

from dataclasses import replace

import pytest

from confflow.science.confgen.engine import combine_state_key
from confflow.science.confgen.kernel_records import ComponentStateKey
from confflow.science.confgen.model import ConfgenStateKey
from confflow.science.confgen.registry import (
    ComponentDescriptor,
    ComponentRegistry,
    build_default_registry,
)


@pytest.mark.parametrize("registry_kind", ["empty", "changed_modes"])
def test_legacy_keys_keep_frozen_merge_semantics(registry_kind):
    registry = ComponentRegistry(descriptors=())
    if registry_kind == "changed_modes":
        registry = ComponentRegistry(
            descriptors=tuple(
                replace(d, state_merge="merge" if d.state_merge == "replace" else "replace")
                for d in build_default_registry().descriptors
            )
        )
    parent = ConfgenStateKey(coordination={"old": 1}, rings={"r": 2}, torsions={"t": 3})
    actual = combine_state_key(parent, "coordination", {"new": 4}, registry=registry)
    assert actual.to_dict() == {
        "schema_version": 3,
        "coordination": {"new": 4},
        "rings": {"r": 2},
        "torsions": {"t": 3},
    }
    with pytest.raises(ValueError, match="unknown generation axis 'unknown'"):
        combine_state_key(parent, "unknown", {}, registry=registry)


@pytest.mark.parametrize("mode", ["replace", "merge"])
def test_generic_keys_follow_custom_component_merge_policy(mode):
    descriptor = ComponentDescriptor(
        id="custom",
        order=40,
        spec_keys=("custom",),
        state_merge=mode,
        is_active=lambda resolved: False,
        factory=lambda resolved: None,
    )
    registry = ComponentRegistry(descriptors=(descriptor,))
    parent = ComponentStateKey(components={"custom": {"old": 1}})
    actual = combine_state_key(parent, "custom", {"new": 4}, registry=registry)
    expected = {"new": 4} if mode == "replace" else {"old": 1, "new": 4}
    assert dict(actual.components["custom"]) == expected


def test_target_conversion_is_exported_only_by_kernel_records():
    import confflow.science.confgen as facade
    from confflow.science.confgen import engine, kernel_records, wire_v3

    assert "as_kernel_target" in kernel_records.__all__
    assert "as_kernel_target" not in facade.__all__
    assert "as_kernel_target" not in engine.__all__
    assert "as_kernel_target" not in wire_v3.__all__
    assert engine.as_kernel_target is kernel_records.as_kernel_target
    assert wire_v3.as_kernel_target is kernel_records.as_kernel_target
    with pytest.raises(AttributeError):
        _ = facade.as_kernel_target
