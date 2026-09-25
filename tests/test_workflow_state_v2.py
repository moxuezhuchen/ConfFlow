"""R4.1 — Workflow State V2: durable stable-ID identity, strict dispatch.

Terminology (deliberate): the *state* schema version is independent of the
*workflow configuration* version. ``confflow.workflow_state.v1`` is the
durable state of Workflow-Config-V2 documents; ``confflow.workflow_state.v2``
is the durable state of Workflow-Config-V3 documents. Both share the single
``.workflow_state.json`` filename and are recognised strictly by
``content_schema`` — never by heuristics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from confflow.config.canonical import (
    CAPABILITIES,
    WORKFLOW_SCHEMA_VERSION_V3,
    can_execute,
    workflow_definition_fingerprint_v3,
)
from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan
from confflow.workflow.state import (
    WORKFLOW_STATE_SCHEMA_V2,
    WorkflowStateCompatibilityError,
    WorkflowStateStore,
    WorkflowStateV2,
    WorkflowStateV2Store,
    detect_workflow_state_schema,
)

V3 = "confflow.workflow.v3"
BINDING_V2 = "confflow.workflow_binding.v2"


def _write_xyz(path: Path) -> Path:
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def _v3_plan(tmp_path: Path, steps: list[dict[str, Any]] | None = None) -> WorkflowV3Plan:
    xyz = _write_xyz(tmp_path / "input.xyz")
    document = {
        "schema": V3,
        "steps": (
            steps
            if steps is not None
            else [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ]
        ),
    }
    (tmp_path / "wf.yaml").write_text(yaml_or_json(document), encoding="utf-8")
    plan = build_workflow_plan([str(xyz)], str(tmp_path / "wf.yaml"))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


def yaml_or_json(document: dict[str, Any]) -> str:
    import yaml

    return yaml.safe_dump(document, sort_keys=False)


def _valid_binding() -> dict[str, Any]:
    """Return a schema-valid binding-v2 fixture.

    Real binding construction is R4.2.
    """
    return {
        "schema": BINDING_V2,
        "source_version": V3,
        "definition_fingerprint": "sha256:" + "a" * 64,
        "execution_fingerprint": "sha256:" + "b" * 64,
        "provenance": {
            "workflow_schema": V3,
            "workflow_schema_sha256": "c" * 64,
            "canonicalization_version": "v1",
            "producer_identity": "confflow",
            "producer_version": "0.0.0",
            "producer_commit": "test",
            "producer_dirty": False,
        },
    }


def _state(
    plan: WorkflowV3Plan,
    *,
    binding: dict[str, Any] | None = None,
    work_dir: str = "/tmp/work",
    **overrides: Any,
) -> WorkflowStateV2:
    from confflow.workflow.state import build_initial_state_v2

    return build_initial_state_v2(
        plan,
        run_id="run-1",
        work_dir=work_dir,
        config_file="/tmp/wf.yaml",
        binding=binding if binding is not None else _valid_binding(),
        **overrides,
    )


# ---------------------------------------------------------------------------
# SV1–SV7 — cross-version load dispatch (strict, content_schema only)
# ---------------------------------------------------------------------------
def _v1_payload() -> dict[str, Any]:
    return {
        "content_schema": "confflow.workflow_state.v1",
        "run_id": "legacy-run",
        "work_dir": "/tmp/legacy",
        "input_files": ["/tmp/in.xyz"],
        "original_inputs": ["/tmp/in.xyz"],
        "config_file": "/tmp/wf.yaml",
        "steps": {
            "gen": {
                "name": "gen",
                "type": "confgen",
                "status": "completed",
                "output_xyz": "/tmp/legacy/gen/search.xyz",
            }
        },
    }


def test_sv1_v1_loader_with_v1_file_passes(tmp_path: Path) -> None:
    store = WorkflowStateStore(str(tmp_path))
    store.save_payload = None  # type: ignore[attr-defined]  # guard against accidental use
    path = tmp_path / ".workflow_state.json"
    path.write_text(json.dumps(_v1_payload()), encoding="utf-8")
    state = WorkflowStateStore(str(tmp_path)).load()
    assert state is not None
    assert state.run_id == "legacy-run"
    assert path.exists()


def test_sv2_v1_loader_with_v2_file_errors(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    WorkflowStateV2Store(str(tmp_path)).save(_state(plan, work_dir=str(tmp_path)))
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateStore(str(tmp_path)).load()


def test_sv3_v2_loader_with_v2_file_passes(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    WorkflowStateV2Store(str(tmp_path)).save(state)
    loaded = WorkflowStateV2Store(str(tmp_path)).load()
    assert loaded is not None
    assert loaded.steps["s001"].id == "s001"
    assert loaded.definition_fingerprint == plan.definition_fingerprint


def test_sv4_v2_loader_with_v1_file_errors(tmp_path: Path) -> None:
    (tmp_path / ".workflow_state.json").write_text(json.dumps(_v1_payload()), encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).load()


def test_sv5_unknown_content_schema_errors(tmp_path: Path) -> None:
    payload = _v1_payload() | {"content_schema": "confflow.workflow_state.v9"}
    (tmp_path / ".workflow_state.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).load()
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateStore(str(tmp_path)).load()


def test_sv6_malformed_json_errors(tmp_path: Path) -> None:
    (tmp_path / ".workflow_state.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).load()


def test_sv7_v1_historical_missing_content_schema_is_legacy_v1(tmp_path: Path) -> None:
    """Keep the pre-schema-era v1 contract intact.

    A v1 file with no ``content_schema`` stays loadable by the V1 reader and
    is recognised as the v1 family by the detection helper.
    """
    payload = _v1_payload()
    payload.pop("content_schema")
    (tmp_path / ".workflow_state.json").write_text(json.dumps(payload), encoding="utf-8")
    assert WorkflowStateStore(str(tmp_path)).load() is not None
    assert detect_workflow_state_schema(tmp_path / ".workflow_state.json") == (
        "confflow.workflow_state.v1"
    )


def test_detection_helper_recognises_schemas(tmp_path: Path) -> None:
    path = tmp_path / ".workflow_state.json"
    assert detect_workflow_state_schema(path) is None  # missing file
    payload = _v1_payload() | {"content_schema": WORKFLOW_STATE_SCHEMA_V2}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert detect_workflow_state_schema(path) == WORKFLOW_STATE_SCHEMA_V2


# ---------------------------------------------------------------------------
# SS1–SS7 — cross-version save protection (PD-10, symmetric)
# ---------------------------------------------------------------------------
def _state_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_ss1_ss3_v1_save_normal(tmp_path: Path) -> None:
    store = WorkflowStateStore(str(tmp_path))
    store.save(_v1_state_object())
    store.save(_v1_state_object())  # SS3: overwrite existing v1 with v1
    assert detect_workflow_state_schema(tmp_path / ".workflow_state.json") == (
        "confflow.workflow_state.v1"
    )


def _v1_state_object():
    from confflow.workflow.state import StepRecord, WorkflowState

    return WorkflowState(
        run_id="legacy-run",
        work_dir="/tmp/legacy",
        input_files=["/tmp/in.xyz"],
        original_inputs=["/tmp/in.xyz"],
        config_file="/tmp/wf.yaml",
        steps={"gen": StepRecord(name="gen", type="confgen", status="completed")},
    )


def test_ss2_ss4_v2_save_normal(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(_state(plan, work_dir=str(tmp_path)))
    store.save(_state(plan, work_dir=str(tmp_path)))  # SS4: overwrite v2 with v2
    assert detect_workflow_state_schema(tmp_path / ".workflow_state.json") == (
        WORKFLOW_STATE_SCHEMA_V2
    )


def test_ss5_v2_save_over_v1_file_rejected_bytes_preserved(tmp_path: Path) -> None:
    path = tmp_path / ".workflow_state.json"
    path.write_text(json.dumps(_v1_payload()), encoding="utf-8")
    before = _state_bytes(path)
    plan = _v3_plan(tmp_path)
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).save(_state(plan, work_dir=str(tmp_path)))
    assert _state_bytes(path) == before
    assert not list(tmp_path.glob(".workflow_state.json.*tmp*"))  # SS7: no temp garbage


def test_ss6_v1_save_over_v2_file_rejected_bytes_preserved(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(_state(plan, work_dir=str(tmp_path)))
    path = tmp_path / ".workflow_state.json"
    before = _state_bytes(path)
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateStore(str(tmp_path)).save(_v1_state_object())
    assert _state_bytes(path) == before
    assert not list(tmp_path.glob(".workflow_state.json.*tmp*"))


def test_ss7_failed_v2_save_leaves_no_temp_files(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    path = tmp_path / ".workflow_state.json"
    path.write_text(json.dumps(_v1_payload()), encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).save(_state(plan, work_dir=str(tmp_path)))
    leftovers = [
        p
        for p in tmp_path.iterdir()
        if p.name != ".workflow_state.json" and p.name not in {"wf.yaml", "input.xyz"}
    ]
    assert leftovers == []


# ---------------------------------------------------------------------------
# ID1–ID10 — durable stable-ID identity
# ---------------------------------------------------------------------------
def test_id1_stable_id_keys_persisted(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    assert set(state.steps) == {"s001", "s002"}
    assert state.execution_order == ["s001", "s002"]
    loaded = _roundtrip(tmp_path, state)
    assert set(loaded.steps) == {"s001", "s002"}


def _roundtrip(tmp_path: Path, state: WorkflowStateV2) -> WorkflowStateV2:
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(state)
    loaded = store.load()
    assert loaded is not None
    return loaded


def test_id2_duplicate_labels_harmless(tmp_path: Path) -> None:
    plan = _v3_plan(
        tmp_path,
        [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "opt",
                "params": {"chains": ["1-2"]},
            },
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "label": "opt",
                "params": {"keyword": "HF"},
            },
        ],
    )
    state = _state(plan, work_dir=str(tmp_path))
    loaded = _roundtrip(tmp_path, state)
    assert loaded.steps["s001"].label == "opt"
    assert loaded.steps["s002"].label == "opt"
    assert set(loaded.steps) == {"s001", "s002"}


def test_id3_label_snapshot_change_does_not_alter_key(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    first = _state(plan, work_dir=str(tmp_path))
    first.steps["s001"].label = "renamed display label"
    loaded = _roundtrip(tmp_path, first)
    assert loaded.steps["s001"].id == "s001"
    assert loaded.steps["s001"].label == "renamed display label"
    # label is display metadata only: identity/keys/order untouched
    assert set(loaded.steps) == {"s001", "s002"}


def test_id4_step_array_reorder_does_not_alter_key_set(tmp_path: Path) -> None:
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
    ]
    first = _state(_v3_plan(tmp_path, [dict(s) for s in steps]), work_dir=str(tmp_path))
    second = _state(
        _v3_plan(tmp_path / "b" if False else tmp_path, list(reversed([dict(s) for s in steps]))),
        work_dir=str(tmp_path),
    )
    assert set(first.steps) == set(second.steps)
    assert first.execution_order == second.execution_order


def test_id5_opaque_stable_id_valid(tmp_path: Path) -> None:
    plan = _v3_plan(
        tmp_path,
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s_abcd2345",
                "type": "calc",
                "inputs": ["s001"],
                "params": {"keyword": "HF"},
            },
        ],
    )
    state = _state(plan, work_dir=str(tmp_path))
    loaded = _roundtrip(tmp_path, state)
    assert "s_abcd2345" in loaded.steps


def test_id6_fragment_handle_invalid(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    state.steps["#step1"] = state.steps.pop("s001")
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).save(state)


def test_id7_malformed_id_invalid(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    state.steps["../evil"] = state.steps.pop("s002")
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).save(state)


def test_id8_unknown_mutation_step_id_rejected(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    with pytest.raises(WorkflowStateCompatibilityError):
        state.step("s999")
    with pytest.raises(WorkflowStateCompatibilityError):
        state.update_step("s999", status="completed")


def test_id9_no_legacy_dirname_generated(tmp_path: Path) -> None:
    plan = _v3_plan(
        tmp_path,
        [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "join output!",
                "params": {"chains": ["1-2"]},
            },
        ],
    )
    state = _state(plan, work_dir=str(tmp_path))
    payload = _payload_of(state)
    # No sanitized dirname anywhere: the label survives verbatim as display.
    assert payload["steps"]["s001"]["label"] == "join output!"
    assert "join_output" not in json.dumps(payload)


def _payload_of(state: WorkflowStateV2) -> dict[str, Any]:
    from confflow.workflow.state import state_v2_payload

    return state_v2_payload(state)


def test_id10_no_lookup_by_label_api() -> None:
    import inspect

    from confflow.workflow import state as state_module

    for name, member in inspect.getmembers(state_module):
        if not inspect.isclass(member) or member.__module__ != state_module.__name__:
            continue
        assert "by_label" not in name and "label_lookup" not in name
    for name in ("step", "update_step"):
        function = getattr(WorkflowStateV2, name)
        source = inspect.getsource(function)
        # No durable lookup may resolve through the label snapshot.
        assert "label ==" not in source and ".label" not in source


# ---------------------------------------------------------------------------
# record / payload policy
# ---------------------------------------------------------------------------
def test_record_key_identity_mismatch_rejected(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(state)
    path = tmp_path / ".workflow_state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    record = payload["steps"]["s001"]
    record["id"] = "s999"
    payload["steps"]["s001"] = record
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        store.load()


def test_unknown_root_field_rejected(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(state)
    path = tmp_path / ".workflow_state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["surprise"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        store.load()


def test_unknown_record_field_rejected(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(state)
    path = tmp_path / ".workflow_state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["steps"]["s001"]["surprise"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        store.load()


def test_invalid_status_rejected(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    state.steps["s001"].status = "RUNNING_V3"
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).save(state)
    # and on load:
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(_state(plan, work_dir=str(tmp_path)))
    path = tmp_path / ".workflow_state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["steps"]["s001"]["status"] = "RUNNING_V3"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkflowStateCompatibilityError):
        store.load()


def test_c9_weird_label_is_display_only(tmp_path: Path) -> None:
    plan = _v3_plan(
        tmp_path,
        [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "../../danger / very weird 名称",
                "params": {"chains": ["1-2"]},
            },
        ],
    )
    state = _state(plan, work_dir=str(tmp_path))
    loaded = _roundtrip(tmp_path, state)
    assert loaded.steps["s001"].label == "../../danger / very weird 名称"
    # The state layer created no directories from the label.
    assert list(tmp_path.glob("*danger*")) == []
    assert set(loaded.steps) == {"s001"}


def test_execution_order_integrity_enforced(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    state.execution_order = ["s001"]  # incomplete order
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).save(state)
    state.execution_order = ["s002", "s001", "s002"]  # duplicate
    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateV2Store(str(tmp_path)).save(state)


def test_binding_shape_validated_structurally(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    binding = _valid_binding()
    binding["schema"] = "confflow.workflow_binding.v1"
    with pytest.raises(WorkflowStateCompatibilityError):
        _state(plan, work_dir=str(tmp_path), binding=binding)


def test_binding_is_not_rebuilt_by_step_progress(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    before = json.dumps(_payload_of(state)["binding"], sort_keys=True)
    state.update_step("s001", status="completed", output_xyz="/tmp/x.xyz")
    after = json.dumps(_payload_of(state)["binding"], sort_keys=True)
    assert before == after


# ---------------------------------------------------------------------------
# Atomicity (A1–A5) — reuse of the V1 atomic primitive
# ---------------------------------------------------------------------------
def _durable_shape(path: Path) -> dict[str, Any]:
    """Payload with volatile timestamps normalized out."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("last_updated_at")
    payload.pop("started_at")
    return payload


def test_a1_roundtrip_and_a4_deterministic_bytes(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(state)
    first = _durable_shape(tmp_path / ".workflow_state.json")
    state.steps["s002"].status = "completed"
    store.save(state)
    second = _durable_shape(tmp_path / ".workflow_state.json")
    assert first != second  # progress is persisted
    store.save(state)
    assert _durable_shape(tmp_path / ".workflow_state.json") == second  # deterministic repeat


def test_a2_a3_a5_failed_replace_retains_original(tmp_path: Path, monkeypatch) -> None:
    plan = _v3_plan(tmp_path)
    state = _state(plan, work_dir=str(tmp_path))
    store = WorkflowStateV2Store(str(tmp_path))
    store.save(state)
    path = tmp_path / ".workflow_state.json"
    original = path.read_bytes()

    import confflow.artifact_json as artifact_json

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(artifact_json.os, "replace", _boom)
    state.steps["s002"].status = "completed"
    with pytest.raises(OSError):
        store.save(state)
    monkeypatch.undo()
    # The durable state is intact and still loadable.
    assert path.read_bytes() == original
    loaded = store.load()
    assert loaded is not None
    assert loaded.steps["s002"].status == "pending"


def test_payload_is_deterministic_across_instances(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    first = _payload_of(_state(plan, work_dir="/tmp/w"))
    second = _payload_of(_state(plan, work_dir="/tmp/w"))
    for payload in (first, second):
        payload.pop("last_updated_at")
        payload.pop("started_at")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ---------------------------------------------------------------------------
# Capability: V3 execution enabled post-flip, future schemas fail closed
# ---------------------------------------------------------------------------
def test_v3_execution_capability_allows_execution() -> None:
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True
    assert not can_execute("confflow.workflow.v4")
    assert not can_execute("confflow.workflow.v99")


def test_v1_state_payload_has_no_source_version_leakage(tmp_path: Path) -> None:
    """Pin the R3.5 ``source_version`` out of the V1 durable formats.

    R3.5 added ``source_version`` to the planning object; it must never reach
    the V1 state, binding or manifest payloads.
    """
    store = WorkflowStateStore(str(tmp_path))
    store.save(_v1_state_object())
    payload = json.loads((tmp_path / ".workflow_state.json").read_text(encoding="utf-8"))
    assert "source_version" not in payload
    assert "source_version" not in json.dumps(payload)


def test_state_v2_payload_schema_constant() -> None:
    from confflow.contract import WORKFLOW_STATE_SCHEMA_V2 as CONTRACT_V2

    assert CONTRACT_V2 == "confflow.workflow_state.v2"
    assert WORKFLOW_STATE_SCHEMA_V2 == "confflow.workflow_state.v2"


def test_definition_fingerprint_round_trips(tmp_path: Path) -> None:
    plan = _v3_plan(tmp_path)
    assert plan.definition_fingerprint.startswith("sha256:")
    # the fingerprint is the real frozen A digest of the plan's definition
    assert plan.definition_fingerprint == workflow_definition_fingerprint_v3(plan.definition)
    loaded = _roundtrip(tmp_path, _state(plan, work_dir=str(tmp_path)))
    assert loaded.definition_fingerprint == plan.definition_fingerprint
