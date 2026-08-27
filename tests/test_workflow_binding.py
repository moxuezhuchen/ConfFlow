"""Acceptance tests for strict workflow configuration bindings."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.config.canonical import build_workflow_binding, workflow_fingerprint
from confflow.workflow.engine import run_workflow
from confflow.workflow.plan import build_workflow_plan
from confflow.workflow.state import (
    StepRecord,
    WorkflowState,
    WorkflowStateCompatibilityError,
    WorkflowStateStore,
)


def _write_input(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "input.xyz"
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=True), encoding="utf-8")


def _base_config() -> dict[str, Any]:
    return {
        "global": {"iprog": "orca", "itask": "sp", "total_memory": "4GB"},
        "steps": [
            {
                "name": "calc",
                "type": "calc",
                "params": {"keyword": "HF"},
            }
        ],
    }


def _plan(tmp_path: Path, mapping: dict[str, Any]):
    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    _write_yaml(config, mapping)
    return input_xyz, config, build_workflow_plan([str(input_xyz)], str(config))


def _minimal_state_payload(config: Path, work_dir: Path) -> dict[str, Any]:
    return {
        "run_id": "legacy-run",
        "work_dir": str(work_dir),
        "input_files": [str(work_dir / "input.xyz")],
        "original_inputs": [str(work_dir / "input.xyz")],
        "config_file": str(config),
        "steps": {
            "gen": {
                "name": "gen",
                "type": "confgen",
                "status": "completed",
                "output_xyz": str(work_dir / "gen" / "search.xyz"),
            }
        },
    }


def test_alias_default_and_mapping_order_have_same_binding_digest(tmp_path: Path) -> None:
    first = {
        "global": {
            "iprog": "gaussian",
            "itask": 0,
            "maxcore": 4096,
            "total_memory": "4GB",
        },
        "steps": [
            {
                "name": "calc",
                "type": "task",
                "enabled": 1,
                "params": {"iprog": 2, "itask": 1, "keyword": "HF"},
            }
        ],
    }
    second = {
        "steps": [
            {
                "params": {"keyword": "HF", "itask": "sp", "iprog": "orca"},
                "type": "calc",
                "name": "calc",
            }
        ],
        "global": {
            "total_memory": "4GB",
            "orca_maxcore": 4096,
            "itask": "opt",
            "iprog": "g16",
        },
    }

    _, _, first_plan = _plan(tmp_path / "first", first)
    _, _, second_plan = _plan(tmp_path / "second", second)

    assert workflow_fingerprint(first_plan) == workflow_fingerprint(second_plan)
    assert (
        build_workflow_binding(first_plan).to_dict()
        == build_workflow_binding(second_plan).to_dict()
    )


@pytest.mark.parametrize(
    "change",
    (
        lambda value: value["global"].update({"force_consistency": True}),
        lambda value: value["steps"][0]["params"].update({"keyword": "B3LYP"}),
        lambda value: value["steps"][0].update({"enabled": False}),
        lambda value: value["steps"][0].update({"inputs": []}),
    ),
)
def test_semantic_global_step_enabled_or_dag_change_changes_digest(tmp_path: Path, change) -> None:
    first = _base_config()
    second = copy.deepcopy(first)
    change(second)

    _, _, first_plan = _plan(tmp_path / "first", first)
    _, _, second_plan = _plan(tmp_path / "second", second)

    assert workflow_fingerprint(first_plan) != workflow_fingerprint(second_plan)


def test_bound_state_round_trip_preserves_binding(tmp_path: Path) -> None:
    input_xyz, config, plan = _plan(tmp_path, _base_config())
    work_dir = tmp_path / "work"
    binding = build_workflow_binding(plan)
    state = WorkflowState(
        run_id="bound-run",
        work_dir=str(work_dir),
        input_files=[str(input_xyz)],
        original_inputs=[str(input_xyz)],
        config_file=str(config),
        steps={"calc": StepRecord(name="calc", type="calc")},
        config_binding=binding,
    )

    store = WorkflowStateStore(str(work_dir))
    store.save(state)
    raw = json.loads((work_dir / ".workflow_state.json").read_text(encoding="utf-8"))
    loaded = store.load()

    assert raw["content_schema"] == "confflow.workflow_state.v1"
    assert raw["config_binding"] == binding.to_dict()
    assert loaded is not None
    assert loaded.config_binding == binding


def test_legacy_no_binding_loads_for_diagnostics_but_resume_rejects(
    tmp_path: Path,
) -> None:
    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    _write_yaml(
        config,
        {
            "global": {},
            "steps": [{"name": "gen", "type": "confgen", "params": {}}],
        },
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "input.xyz").write_bytes(input_xyz.read_bytes())
    state_path = work_dir / ".workflow_state.json"
    state_path.write_text(
        json.dumps(_minimal_state_payload(config, work_dir), sort_keys=True),
        encoding="utf-8",
    )
    sentinel = work_dir / "sentinel.xyz"
    sentinel.write_text("sentinel\n", encoding="utf-8")
    before_state = state_path.read_bytes()
    before_sentinel = sentinel.read_bytes()

    loaded = WorkflowStateStore(str(work_dir)).load()
    assert loaded is not None
    assert loaded.config_binding is None

    with pytest.raises(RuntimeError, match="no config binding"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert state_path.read_bytes() == before_state
    assert sentinel.read_bytes() == before_sentinel


@pytest.mark.parametrize(
    "binding_mutation",
    (
        lambda binding: {**binding, "schema": "confflow.workflow_binding.v99"},
        lambda binding: {**binding, "fingerprint": "sha256:not-a-digest"},
        lambda binding: {**binding, "future": "unknown"},
        lambda binding: {key: value for key, value in binding.items() if key != "fingerprint"},
        lambda binding: [],
    ),
)
def test_unknown_or_malformed_binding_fails_closed_without_mutation(
    tmp_path: Path, binding_mutation
) -> None:
    _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    _write_yaml(config, {"global": {}, "steps": [{"name": "gen", "type": "confgen"}]})
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    sentinel = work_dir / "sentinel.xyz"
    sentinel.write_text("sentinel\n", encoding="utf-8")
    payload = _minimal_state_payload(config, work_dir)
    payload["config_binding"] = binding_mutation(
        {
            "schema": "confflow.workflow_binding.v1",
            "workflow_schema": "confflow.workflow.v2",
            "workflow_schema_sha256": "0" * 64,
            "fingerprint": "sha256:" + "1" * 64,
        }
    )
    state_path = work_dir / ".workflow_state.json"
    state_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    before_state = state_path.read_bytes()
    before_sentinel = sentinel.read_bytes()

    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateStore(str(work_dir)).load()

    assert state_path.read_bytes() == before_state
    assert sentinel.read_bytes() == before_sentinel


def test_binding_mismatch_rejects_before_calc_prepare_and_preserves_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_xyz, config, plan = _plan(tmp_path, _base_config())
    work_dir = tmp_path / "work"
    output = work_dir / "calc" / "output.xyz"
    output.parent.mkdir(parents=True)
    output.write_text("1\nsentinel\nH 0 0 0\n", encoding="utf-8")
    state = WorkflowState(
        run_id="bound-run",
        work_dir=str(work_dir),
        input_files=[str(input_xyz)],
        original_inputs=[str(input_xyz)],
        config_file=str(config),
        steps={
            "calc": StepRecord(
                name="calc",
                type="calc",
                status="completed",
                output_xyz=str(output),
            )
        },
        config_binding=build_workflow_binding(plan),
    )
    store = WorkflowStateStore(str(work_dir))
    store.save(state)
    state_path = work_dir / ".workflow_state.json"
    before_state = state_path.read_bytes()
    before_output = output.read_bytes()

    changed = _base_config()
    changed["steps"][0]["params"]["keyword"] = "B3LYP"
    _write_yaml(config, changed)
    prepare_calls: list[str] = []

    def forbidden_prepare(*args: Any, **kwargs: Any):
        del args, kwargs
        prepare_calls.append("called")
        raise AssertionError("artifact preparation must not run on binding mismatch")

    monkeypatch.setattr("confflow.workflow.engine.CalcArtifactManager.prepare", forbidden_prepare)

    with pytest.raises(RuntimeError, match="config binding"):
        run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert prepare_calls == []
    assert state_path.read_bytes() == before_state
    assert output.read_bytes() == before_output
