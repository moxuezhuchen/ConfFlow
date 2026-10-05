#!/usr/bin/env python3
"""AG1 v2 independent regression: shared-default deep freeze/thaw.

Root counterexample (/tmp/fix1a-ag1-root-default-repro.log): the cached
``default_registry`` claimed immutable, but ``ring.spec_defaults[0][1].append``
mutated the next ``normalize_spec`` run. This file proves the fix
independently of the 6 legacy AG1 tests:

- external source mutation cannot alter a built descriptor;
- descriptor stored defaults are immutable (tuple/FrozenDict);
- normalize output nested mutation cannot leak into the next run nor the
  cached registry (deep thaw per run, builtin + custom nested);
- non-owned and duplicate defaults fail closed at construction;
- custom defaults cannot overwrite another owner's key;
- builtin normalized output keeps list types and C/R/T/default order,
  unsorted JSON byte-stable (full byte equality is proven by the
  root 207 + extended 19 differentials, not re-asserted here).
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.science.confgen.planner import normalize_spec
from confflow.science.confgen.registry import (
    ComponentDescriptor,
    ComponentRegistry,
    build_default_registry,
    default_registry,
)


def _probe_descriptor(
    *,
    spec_keys: tuple[str, ...] = ("probe_section",),
    spec_defaults: tuple[tuple[str, Any], ...] = (),
    order: int = 40,
    descriptor_id: str = "probe",
) -> ComponentDescriptor:
    return ComponentDescriptor(
        id=descriptor_id,
        order=order,
        spec_keys=spec_keys,
        state_merge="merge",
        is_active=lambda resolved: False,
        factory=lambda resolved: (_ for _ in ()).throw(AssertionError("unused")),
        spec_defaults=spec_defaults,
    )


def test_ag1v2_source_mutation_does_not_alter_descriptor() -> None:
    src: list[Any] = []
    src_dict: dict[str, Any] = {"items": [1, 2]}
    desc = _probe_descriptor(
        spec_defaults=(("probe_section", src),),
    )
    src.append({"leaked": True})
    stored = dict(desc.spec_defaults)["probe_section"]
    # Frozen form is a tuple, never the source list object.
    assert stored == ()
    assert stored is not src

    desc2 = _probe_descriptor(
        spec_keys=("probe2",),
        spec_defaults=(("probe2", src_dict),),
        order=41,
        descriptor_id="probe2",
    )
    src_dict["items"].append(99)
    src_dict["new"] = 1
    stored2 = dict(desc2.spec_defaults)["probe2"]
    # Frozen mapping: nested list became an immutable tuple.
    assert dict(stored2) == {"items": (1, 2)}


def test_ag1v2_descriptor_value_immutable() -> None:
    registry = build_default_registry()
    by_id = {desc.id: desc for desc in registry.descriptors}
    rings_default = dict(by_id["rings"].spec_defaults)["rings"]
    # Builtin list default is now a frozen tuple: no .append attribute.
    with pytest.raises(AttributeError):
        rings_default.append({"root_probe": "mutable_shared_registry"})  # type: ignore[attr-defined]
    torsions_default = dict(by_id["torsions"].spec_defaults)["torsions"]
    with pytest.raises(AttributeError):
        torsions_default.append(1)  # type: ignore[attr-defined]

    custom = _probe_descriptor(
        spec_keys=("probe3",),
        spec_defaults=(("probe3", {"items": [1]}),),
        order=42,
        descriptor_id="probe3",
    )
    frozen_map = dict(custom.spec_defaults)["probe3"]
    with pytest.raises(TypeError):
        frozen_map["items"] = []  # type: ignore[index]


def test_ag1v2_normalize_output_nested_mutation_isolated() -> None:
    # Builtin: mutate the returned lists, next run must be pristine and the
    # cached registry must be unchanged.
    reg = default_registry()
    before = dict(next(d for d in reg.descriptors if d.id == "rings").spec_defaults)["rings"]
    out1 = normalize_spec({"index_base": 0}, registry=reg)
    assert type(out1["rings"]) is list
    out1["rings"].append({"root_probe": "mutable_shared_registry"})
    out1["torsions"].append({"x": 1})
    out1["paths"].append({"y": 1})
    out2 = normalize_spec({"index_base": 0}, registry=reg)
    assert out2["rings"] == []
    assert out2["torsions"] == []
    assert out2["paths"] == []
    after = dict(next(d for d in reg.descriptors if d.id == "rings").spec_defaults)["rings"]
    assert after == before

    # Custom nested: shallow dict/list copy would share the inner list.
    custom = _probe_descriptor(
        spec_keys=("probe_nested",),
        spec_defaults=(("probe_nested", {"items": []}),),
        order=40,
        descriptor_id="probe_nested",
    )
    reg2 = ComponentRegistry(descriptors=tuple(build_default_registry().descriptors) + (custom,))
    c1 = normalize_spec({"index_base": 0}, registry=reg2)
    assert c1["probe_nested"] == {"items": []}
    assert type(c1["probe_nested"]) is dict
    assert type(c1["probe_nested"]["items"]) is list
    c1["probe_nested"]["items"].append({"leak": 1})
    c2 = normalize_spec({"index_base": 0}, registry=reg2)
    assert c2["probe_nested"] == {"items": []}
    # Cached registry default itself never gained the nested entry.
    cached = dict(next(d for d in reg2.descriptors if d.id == "probe_nested").spec_defaults)[
        "probe_nested"
    ]
    assert dict(cached) == {"items": ()}


def test_ag1v2_nonowned_and_duplicate_defaults_fail_closed() -> None:
    with pytest.raises(ValueError, match="unowned"):
        _probe_descriptor(
            spec_keys=("owned_a",),
            spec_defaults=(("not_owned", []),),
            order=43,
            descriptor_id="bad_owner",
        )
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        _probe_descriptor(
            spec_keys=("owned_b", "owned_c"),
            spec_defaults=(("owned_b", []), ("owned_b", [])),
            order=44,
            descriptor_id="bad_dup",
        )


def test_ag1v2_custom_defaults_cannot_overwrite_other_owner() -> None:
    # A custom descriptor claiming a builtin key as its default must be
    # rejected at construction (key not in its own spec_keys).
    with pytest.raises(ValueError, match="unowned"):
        _probe_descriptor(
            spec_keys=("probe_other",),
            spec_defaults=(("rings", []),),
            order=45,
            descriptor_id="hijack",
        )


def test_ag1v2_builtin_output_types_and_order_stable() -> None:
    out = normalize_spec({"index_base": 0})
    # Historical C/R/T/default insertion order for the component slice.
    keys = list(out.keys())
    assert keys.index("coordination") < keys.index("rings")
    assert keys.index("rings") < keys.index("torsions")
    assert keys.index("torsions") < keys.index("paths")
    assert keys.index("paths") < keys.index("strict_path_bond_check")
    assert out["coordination"] is None
    assert type(out["rings"]) is list
    assert type(out["torsions"]) is list
    assert type(out["paths"]) is list
    assert type(out["strict_path_bond_check"]) is bool
