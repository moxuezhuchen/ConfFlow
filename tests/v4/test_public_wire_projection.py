#!/usr/bin/env python3
"""Public-boundary hardening: v3 wire projection (FIX-1A A2 + FIX-1D L-D3).

PLAN mapping and missing risk:
- FIX-1A A2 wire fail-closed: to_wire_key already pins unknown ids, but
  project_v3 complete_key with an unknown component and _generic_from_payload
  with non-generic payloads were unpinned. Risk: unknown component silently
  carried into EngineRun state keys.
- FIX-1A A2 boundary types: project_v3/to_legacy_* must reject old/wrong
  types visibly; to_legacy_target must pass a legacy target through
  untouched. Risk: mis-projection accepted as science.
- FIX-1D L-D3/inherited-report: project_v3 must keep certificate identity,
  convert generic complete_key/evidence parent_key to v3 dicts, leave v3
  payloads and None complete_key untouched, copy the report (no aliasing),
  and restore audited/basis from entries. Risk: certificate drift, scope
  bytes drift, aliasing mutation.
All cases use real public constructors; expected literals are hardcoded.
"""

from __future__ import annotations

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.structure import StructureRecord
from confflow.science.confgen.accounting import TargetRecord, TerminalStatus
from confflow.science.confgen.kernel_records import (
    ComponentStateKey,
    KernelGenerationTarget,
    KernelRun,
    KernelWorkingRealization,
)


def _struct(tag: str = "w") -> StructureRecord:
    return StructureRecord(
        id=tag,
        atoms=("C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0)),
        charge=0,
        multiplicity=1,
    )


def _generic_key() -> ComponentStateKey:
    return ComponentStateKey(components={"rings": {"r": 2}})


def _leaf(key: ComponentStateKey | None = None) -> KernelWorkingRealization:
    return KernelWorkingRealization(structure=_struct(), state_key=key or _generic_key())


def _record(
    complete: object = "sentinel",
    evidence: tuple = (),
    target_id: str = "rings:000001",
) -> TargetRecord:
    if complete == "sentinel":
        complete = FrozenDict(_generic_key().to_dict())
    return TargetRecord(
        target_id=target_id,
        axis="rings",
        ordinal=1,
        state_value={},
        complete_key=complete,  # type: ignore[arg-type]
        status=TerminalStatus.EXPANDED,
        reason="probe",
        evidence=evidence,
        parent_target_id=None,
    )


def test_generic_from_payload_rejects_non_generic() -> None:
    from confflow.science.confgen.wire_v3 import _generic_from_payload

    with pytest.raises(ValueError):
        _generic_from_payload([])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _generic_from_payload("x")  # type: ignore[arg-type]
    from confflow.science.confgen.wire_v3 import UnsupportedWireComponent

    with pytest.raises(UnsupportedWireComponent):
        _generic_from_payload({"schema_version": 3, "rings": {}})


def test_project_v3_rejects_unknown_component_in_complete_key() -> None:
    from confflow.science.confgen.wire_v3 import UnsupportedWireComponent, project_v3

    bad_key = FrozenDict(ComponentStateKey(components={"nope": {"x": 1}}).to_dict())
    run = KernelRun(leaves=(), target_records=(_record(complete=bad_key),), report={})
    with pytest.raises(UnsupportedWireComponent):
        project_v3(run)


def test_project_v3_rejects_wrong_run_type() -> None:
    from confflow.science.confgen.wire_v3 import project_v3

    with pytest.raises(ValueError):
        project_v3(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        project_v3({"leaves": ()})  # type: ignore[arg-type]


def test_legacy_converters_reject_old_types_and_pass_through() -> None:
    from confflow.science.confgen.model import GenerationTarget, WorkingRealization
    from confflow.science.confgen.wire_v3 import to_legacy_realization, to_legacy_target

    with pytest.raises(ValueError):
        to_legacy_realization(object())  # type: ignore[arg-type]
    legacy_leaf = WorkingRealization(
        structure=_struct("leg"),
        state_key=__import__(
            "confflow.science.confgen.model", fromlist=["ConfgenStateKey"]
        ).ConfgenStateKey(),
    )
    with pytest.raises(ValueError):
        to_legacy_realization(legacy_leaf)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        to_legacy_target(object())  # type: ignore[arg-type]
    legacy_target = GenerationTarget(
        axis="rings", target_id="rings:000003", state_value={}, ordinal=3
    )
    assert to_legacy_target(legacy_target) is legacy_target
    converted = to_legacy_target(
        KernelGenerationTarget(axis="rings", target_id="rings:000004", state_value={}, ordinal=4)
    )
    assert isinstance(converted, GenerationTarget)
    assert converted.target_id == "rings:000004"


def test_project_v3_keeps_certificate_and_converts_keys() -> None:
    from confflow.science.confgen.wire_v3 import project_v3

    cert = object()
    parent_generic = FrozenDict(_generic_key().to_dict())
    evidence = (FrozenDict({"parent_key": parent_generic, "note": "e"}),)
    run = KernelRun(
        leaves=(_leaf(),),
        target_records=(_record(evidence=evidence),),
        report={"counts": {"n": 1}},
        certificate=cert,
    )
    projected = project_v3(run)
    assert projected.certificate is cert
    assert projected.leaves[0].structure.id == "w"
    complete = dict(projected.target_records[0].complete_key)
    assert complete["schema_version"] == 3
    assert complete["rings"] == {"r": 2}
    ev = dict(projected.target_records[0].evidence[0])
    assert dict(ev["parent_key"])["rings"] == {"r": 2}
    assert ev["note"] == "e"


def test_project_v3_leaves_none_and_v3_passthrough_untouched() -> None:
    from confflow.science.confgen.wire_v3 import project_v3

    v3_payload = FrozenDict(
        {"schema_version": 3, "coordination": None, "rings": {"r": 1}, "torsions": {}}
    )
    evidence = (FrozenDict({"parent_key": v3_payload}),)
    run = KernelRun(
        leaves=(),
        target_records=(_record(complete=None, evidence=evidence),),
        report={},
    )
    projected = project_v3(run)
    assert projected.target_records[0].complete_key is None
    assert dict(projected.target_records[0].evidence[0]["parent_key"]) == dict(v3_payload)


def test_project_v3_report_copied_and_inherited_restored() -> None:
    from confflow.science.confgen.wire_v3 import project_v3

    run = KernelRun(
        leaves=(),
        target_records=(),
        report={"inherited": {"entries": [], "audited": True, "basis": "stale"}},
    )
    projected = project_v3(run)
    assert projected.report["inherited"]["audited"] is False
    assert projected.report["inherited"]["basis"] == "no incoming chained state"
    assert projected.report is not run.report
    assert dict(projected.report) == {
        "inherited": {
            "entries": [],
            "audited": False,
            "basis": "no incoming chained state",
        }
    }
    run2 = KernelRun(
        leaves=(),
        target_records=(),
        report={"inherited": {"entries": [{"x": 1}], "audited": False, "basis": "stale"}},
    )
    projected2 = project_v3(run2)
    assert projected2.report["inherited"]["audited"] is True
    assert (
        projected2.report["inherited"]["basis"]
        == "carried torsion locks re-measured on every fresh geometry against prior absolute frames"
    )


def test_is_legacy_stage_routing_is_real() -> None:
    from confflow.science.confgen.model import GenerationStage
    from confflow.science.confgen.wire_v3 import is_legacy_stage

    class _LegacyRings(GenerationStage):
        def __init__(self) -> None:
            pass

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def enumerate_targets(self, parent, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def realize(self, parent, context=None, target=None):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def perceive(self, structure, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    class _GenericDummy(GenerationStage):
        def __init__(self) -> None:
            pass

        @property
        def axis(self) -> str:
            return "dummy"

        def estimate(self, parent, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def enumerate_targets(self, parent, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def realize(self, parent, context=None, target=None):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def perceive(self, structure, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    assert is_legacy_stage(_LegacyRings()) is True
    assert is_legacy_stage(_GenericDummy()) is False
    assert is_legacy_stage(_GenericDummy) is False
