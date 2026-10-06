#!/usr/bin/env python3
"""Public-boundary hardening: kernel/model records (FIX-1A A2/A4d + CORE).

PLAN mapping and missing risk (why these were weak):
- FIX-1A A2 generic kernel records: ComponentStateKey/KernelGenerationTarget/
  KernelWorkingRealization must reject non-JSON/deep-mutable/empty inputs
  fail-closed. Prior 26 tests pinned TelemetryRow/Bound/RetryResult only;
  a caller could smuggle NaN/objects/non-string keys or mutate the input
  dict afterwards and change engine identity. Risk: silent state drift.
- FIX-1A A4d opaque inherited payload: ComponentInheritedState/
  VerificationResult must freeze JSON-only and keep bool discipline.
  Prior tests pinned bad-object payloads once; list->tuple vs dict->Frozen
  and empty-id/bool-ok branches were unpinned. Risk: lock objects leak.
- CORE frozen model: ConfgenStateKey schema_version + freeze + roundtrip,
  as_kernel_target conversion. Risk: schema drift accepted, aliasing.
Positive and negative cases all use real public constructors; expected
values are literals, never computed by calling the implementation.
Legacy path check asserts the package re-export is the same object.
"""

from __future__ import annotations

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.structure import StructureRecord


def _struct(tag: str = "k") -> StructureRecord:
    return StructureRecord(
        id=tag,
        atoms=("C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0)),
        charge=0,
        multiplicity=1,
    )


def test_legacy_import_paths_are_same_objects() -> None:
    import confflow.science.confgen as pkg
    import confflow.science.confgen.kernel_records as kr
    import confflow.science.confgen.model as model

    assert pkg.ComponentStateKey is kr.ComponentStateKey
    assert pkg.KernelGenerationTarget is kr.KernelGenerationTarget
    assert pkg.KernelWorkingRealization is kr.KernelWorkingRealization
    assert pkg.KernelRun is kr.KernelRun
    assert pkg.ConfgenStateKey is model.ConfgenStateKey
    assert pkg.GenerationTarget is model.GenerationTarget


def test_component_state_key_rejects_non_json_and_freezes_deep() -> None:
    from confflow.science.confgen.kernel_records import ComponentStateKey

    with pytest.raises(ValueError):
        ComponentStateKey(components={1: "v"})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        ComponentStateKey(components={"a": float("nan")})
    with pytest.raises(ValueError):
        ComponentStateKey(components={"a": object()})
    with pytest.raises(ValueError):
        ComponentStateKey(components={"a": {"nested": {1, 2}}})  # type: ignore[dict-item]
    raw: dict = {"a": {"x": [1, 2]}}
    key = ComponentStateKey(components=raw)
    assert isinstance(key.components, FrozenDict)
    raw["a"]["x"].append(99)
    raw["new"] = 1
    assert dict(key.components) == {"a": {"x": [1, 2]}}
    assert key.to_dict() == {"components": {"a": {"x": [1, 2]}}}
    rebuilt = ComponentStateKey.from_dict(key.to_dict())
    assert rebuilt == key
    with pytest.raises(ValueError):
        ComponentStateKey.from_dict([])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ComponentStateKey.from_dict({"components": []})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        ComponentStateKey.from_dict({"components": {"a": object()}})


def test_kernel_generation_target_rejects_empty_and_non_json() -> None:
    from confflow.science.confgen.kernel_records import KernelGenerationTarget

    with pytest.raises(ValueError):
        KernelGenerationTarget(axis="", target_id="d:000000", state_value={}, ordinal=0)
    with pytest.raises(ValueError):
        KernelGenerationTarget(axis="d", target_id="", state_value={}, ordinal=0)
    with pytest.raises(ValueError):
        KernelGenerationTarget(axis="d", target_id="d:000000", state_value={}, ordinal=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelGenerationTarget(axis="d", target_id="d:000000", state_value={}, ordinal=-1)
    with pytest.raises(ValueError):
        KernelGenerationTarget(axis="d", target_id="d:000000", state_value={1: 2}, ordinal=0)  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        KernelGenerationTarget(
            axis="d", target_id="d:000000", state_value={"x": float("inf")}, ordinal=0
        )
    good = KernelGenerationTarget(
        axis="d",
        target_id="d:000000",
        state_value={"slot": 1},
        ordinal=0,
        provenance={"src": "t"},
    )
    assert isinstance(good.state_value, FrozenDict)
    assert isinstance(good.provenance, FrozenDict)
    assert dict(good.state_value) == {"slot": 1}


def test_as_kernel_target_passthrough_and_legacy_conversion() -> None:
    from confflow.science.confgen.kernel_records import (
        KernelGenerationTarget,
        as_kernel_target,
    )
    from confflow.science.confgen.model import GenerationTarget

    kernel = KernelGenerationTarget(axis="d", target_id="d:000001", state_value={}, ordinal=1)
    assert as_kernel_target(kernel) is kernel
    legacy = GenerationTarget(
        axis="rings", target_id="rings:000002", state_value={"slot": 2}, ordinal=2
    )
    converted = as_kernel_target(legacy)
    assert isinstance(converted, KernelGenerationTarget)
    assert converted.axis == "rings"
    assert converted.target_id == "rings:000002"
    assert dict(converted.state_value) == {"slot": 2}
    assert converted.ordinal == 2


def test_kernel_working_realization_rejects_bad_identity_and_locks() -> None:
    from confflow.science.confgen.kernel_records import (
        ComponentStateKey,
        KernelWorkingRealization,
    )

    key = ComponentStateKey(components={"d": {"x": 1}})
    with pytest.raises(ValueError):
        KernelWorkingRealization(structure=None, state_key=key)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelWorkingRealization(structure=_struct(), state_key={"d": 1})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        KernelWorkingRealization(structure=_struct(), state_key=key, generation_axis="")
    with pytest.raises(ValueError):
        KernelWorkingRealization(structure=_struct(), state_key=key, locked_axes=("",))
    with pytest.raises(ValueError):
        KernelWorkingRealization(structure=_struct(), state_key=key, locked_axes=(7,))  # type: ignore[list-item]
    good = KernelWorkingRealization(
        structure=_struct("w"),
        state_key=key,
        parent_realization_id=None,
        generation_axis="d",
        locked_axes=["d"],
        provenance={"p": 1},
    )
    assert good.locked_axes == ("d",)
    assert isinstance(good.provenance, FrozenDict)
    assert good.structure.id == "w"


def test_component_inherited_state_freeze_shapes_and_id_guards() -> None:
    from confflow.science.confgen.kernel_records import ComponentInheritedState

    with pytest.raises(ValueError):
        ComponentInheritedState(component_id="", payload={})
    with pytest.raises(ValueError):
        ComponentInheritedState(component_id=7, payload={})  # type: ignore[arg-type]
    as_list = ComponentInheritedState(component_id="d", payload=[1, 2])
    assert as_list.payload == (1, 2)
    as_dict = ComponentInheritedState(component_id="d", payload={"a": 1})
    assert isinstance(as_dict.payload, FrozenDict)
    assert dict(as_dict.payload) == {"a": 1}
    as_none = ComponentInheritedState(component_id="d", payload=None)
    assert as_none.payload is None
    with pytest.raises(ValueError):
        ComponentInheritedState(component_id="d", payload={"bad": {1, 2}})
    with pytest.raises(ValueError):
        ComponentInheritedState(component_id="d", payload={"bad": (object(),)})


def test_verification_result_bool_and_evidence_tuple() -> None:
    from confflow.science.confgen.kernel_records import VerificationResult

    for bad in (1, "true", None, 0):
        with pytest.raises(ValueError):
            VerificationResult(ok=bad)  # type: ignore[arg-type]
    good = VerificationResult(ok=True, evidence=[{"drift": 1}])
    assert good.ok is True
    assert good.evidence == ({"drift": 1},)
    empty = VerificationResult(ok=False)
    assert empty.ok is False and empty.evidence == ()


def test_kernel_run_report_json_thaws_without_aliasing() -> None:
    from confflow.science.confgen.kernel_records import KernelRun

    run = KernelRun(report={"a": {"b": 1}}, certificate="cert-obj")
    plain = run.report_json()
    assert plain == {"a": {"b": 1}}
    plain["a"]["b"] = 99
    assert run.report_json() == {"a": {"b": 1}}
    assert run.certificate == "cert-obj"
    assert isinstance(run.leaves, tuple) and isinstance(run.target_records, tuple)


def test_confgen_state_key_schema_and_roundtrip() -> None:
    from confflow.science.confgen.model import ConfgenStateKey

    with pytest.raises(ValueError):
        ConfgenStateKey(schema_version=999)
    with pytest.raises(ValueError):
        ConfgenStateKey(coordination=float("nan"))
    with pytest.raises(ValueError):
        ConfgenStateKey(rings={"a": object()})
    key = ConfgenStateKey(coordination={"m": 0}, rings={"r": 1}, torsions={})
    assert isinstance(key.rings, FrozenDict)
    assert key.to_dict()["schema_version"] == 3
    rebuilt = ConfgenStateKey.from_dict(key.to_dict())
    assert rebuilt == key
    with pytest.raises(ValueError):
        ConfgenStateKey.from_dict({"schema_version": 2})
    with pytest.raises(ValueError):
        ConfgenStateKey.from_dict([])  # type: ignore[arg-type]


def test_build_context_rejects_wrong_types_fail_closed() -> None:
    from confflow.science.confgen.model import ConfgenStateKey, build_context

    with pytest.raises(ValueError):
        build_context("not-a-struct", {})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_context(_struct("c"), [])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_context(_struct("c"), {}, input_state_key=object())  # type: ignore[arg-type]
    good = build_context(_struct("c2"), {"index_base": 0, "seed": 7})
    assert isinstance(good.input_state_key, ConfgenStateKey)
