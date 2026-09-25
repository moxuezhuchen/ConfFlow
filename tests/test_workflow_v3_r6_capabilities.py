"""Comprehensive R6 Effective Dataflow & Artifact Capability tests (R6-1 to R6-13).

Validates:
- Single source of truth for effective-dataflow resolution (confflow/workflow/v3_dataflow.py);
- Unified semantics across validator, runtime (confflow/workflow/v3_runtime.py), and
  finalizer (confflow/workflow/finalize.py);
- Bypassed disabled chains, disabled branching, duplicate effective source deduplication;
- Root cardinality and step type capabilities (calc consumes exactly 1 geometry;
  confgen consumes 1 or more);
- Static checkpoint capability validation (calc step, enabled ancestor, compatible program)
  with runtime existence checks as defense-in-depth;
- Terminal effective producers and output_manifest.v2 publication;
- V1 engine immunity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from confflow.core.exceptions import ConfFlowError
from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan
from confflow.workflow.v3_dataflow import (
    resolve_effective_sources_v3,
    validate_effective_dataflow_v3,
)
from confflow.workflow.v3_runtime import run_v3_workflow
from tests.test_workflow_v3_runtime import _FakeHandlers, _write_config, _write_xyz

# Hermetic CI: fake orca/g16 entrypoints on PATH (real files, real identity).
pytestmark = pytest.mark.usefixtures("fake_qc_executables_on_path")

V3_SCHEMA = "confflow.workflow.v3"


def _plan(tmp_path: Path, steps: list[dict[str, Any]], inputs: int = 1) -> WorkflowV3Plan:
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_files = []
    for idx in range(inputs):
        f = tmp_path / f"input_{idx}.xyz"
        _write_xyz(f, note=f"slot {idx}")
        input_files.append(str(f))
    config = _write_config(tmp_path / "wf.yaml", steps)
    plan = build_workflow_plan(input_files, str(config))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


def _manifest(work_dir: Path) -> dict[str, Any]:
    return json.loads((work_dir / "output_manifest.json").read_text(encoding="utf-8"))


def _stats(work_dir: Path) -> dict[str, Any]:
    return json.loads((work_dir / "workflow_stats.json").read_text(encoding="utf-8"))


def _load_state(work_dir: Path) -> dict[str, Any]:
    """Read the durable workflow state file and return it as a plain dict."""
    return json.loads((work_dir / ".workflow_state.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# R6-1: Single enabled chain
# ---------------------------------------------------------------------------
def test_r6_1_single_enabled_chain(tmp_path: Path, monkeypatch) -> None:
    """R6-1: Single enabled chain executes sequentially and forwards outputs."""
    handlers = _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
    ]
    plan = _plan(tmp_path, steps)
    issues = validate_effective_dataflow_v3(plan, external_input_count=1)
    assert issues == ()

    sources = resolve_effective_sources_v3(plan, external_input_count=1)
    assert sources["s001"] == (("external", 1),)
    assert sources["s002"] == (("step", "s001"),)
    assert sources["s003"] == (("step", "s002"),)

    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    assert _load_state(work)["final_status"] == "completed"
    assert len(handlers.confgen_calls) == 1
    assert len(handlers.calc_calls) == 2
    assert handlers.calc_calls[0]["step_name"] == "s002"
    assert handlers.calc_calls[0]["current_input"].endswith("steps/s001/search.xyz")
    assert handlers.calc_calls[1]["step_name"] == "s003"
    assert handlers.calc_calls[1]["current_input"].endswith("steps/s002/result.xyz")

    manifest = _manifest(work)
    assert manifest["content_schema"] == "confflow.output_manifest.v2"
    assert manifest["terminals"] == [
        {"id": "s003", "label": None, "artifacts": ["steps/s003/result.xyz"]}
    ]


# ---------------------------------------------------------------------------
# R6-2: Disabled chain bypass
# ---------------------------------------------------------------------------
def test_r6_2_disabled_chain_bypass(tmp_path: Path, monkeypatch) -> None:
    """R6-2: Disabled step in a chain is bypassed, downstream consumes upstream output."""
    handlers = _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {
            "id": "s002",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
    ]
    plan = _plan(tmp_path, steps)
    issues = validate_effective_dataflow_v3(plan, external_input_count=1)
    assert issues == ()

    sources = resolve_effective_sources_v3(plan, external_input_count=1)
    assert sources["s001"] == (("external", 1),)
    assert sources["s002"] == (("step", "s001"),)
    assert sources["s003"] == (("step", "s001"),)

    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    assert _load_state(work)["final_status"] == "completed"
    assert len(handlers.confgen_calls) == 1
    assert len(handlers.calc_calls) == 1
    assert handlers.calc_calls[0]["step_name"] == "s003"
    assert handlers.calc_calls[0]["current_input"].endswith("steps/s001/search.xyz")

    stats = _stats(work)
    assert stats["steps"][1]["id"] == "s002"
    assert stats["steps"][1]["status"] == "skipped"
    assert stats["steps"][2]["id"] == "s003"
    assert stats["steps"][2]["status"] == "completed"


# ---------------------------------------------------------------------------
# R6-3: Multiple disabled chain
# ---------------------------------------------------------------------------
def test_r6_3_multiple_disabled_chain(tmp_path: Path, monkeypatch) -> None:
    """R6-3: s001 (calc) -> s002 (disabled) -> s003 (disabled) -> s004 (calc).

    s004 consumes s001's effective output after hopping through multiple disabled steps.
    """
    handlers = _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
        {
            "id": "s002",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s003",
            "type": "calc",
            "enabled": False,
            "inputs": ["s002"],
            "params": {"keyword": "HF"},
        },
        {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
    ]
    plan = _plan(tmp_path, steps)
    issues = validate_effective_dataflow_v3(plan, external_input_count=1)
    assert issues == ()

    sources = resolve_effective_sources_v3(plan, external_input_count=1)
    assert sources["s001"] == (("external", 1),)
    assert sources["s002"] == (("step", "s001"),)
    assert sources["s003"] == (("step", "s001"),)
    assert sources["s004"] == (("step", "s001"),)

    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    assert _load_state(work)["final_status"] == "completed"
    assert len(handlers.calc_calls) == 2
    assert handlers.calc_calls[0]["step_name"] == "s001"
    assert handlers.calc_calls[1]["step_name"] == "s004"
    assert handlers.calc_calls[1]["current_input"].endswith("steps/s001/result.xyz")

    manifest = _manifest(work)
    assert manifest["terminals"] == [
        {"id": "s004", "label": None, "artifacts": ["steps/s004/result.xyz"]}
    ]


# ---------------------------------------------------------------------------
# R6-4: Disabled branch
# ---------------------------------------------------------------------------
def test_r6_4_disabled_branch(tmp_path: Path, monkeypatch) -> None:
    """R6-4: Branching where one branch contains a disabled step.

    s001 -> s002 (calc, enabled)
    s001 -> s003 (calc, disabled) -> s004 (calc, enabled)
    """
    handlers = _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {
            "id": "s003",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
    ]
    plan = _plan(tmp_path, steps)
    issues = validate_effective_dataflow_v3(plan, external_input_count=1)
    assert issues == ()

    sources = resolve_effective_sources_v3(plan, external_input_count=1)
    assert sources["s002"] == (("step", "s001"),)
    assert sources["s003"] == (("step", "s001"),)
    assert sources["s004"] == (("step", "s001"),)

    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    assert _load_state(work)["final_status"] == "completed"
    assert len(handlers.calc_calls) == 2
    calc_step_names = [call["step_name"] for call in handlers.calc_calls]
    assert calc_step_names == ["s002", "s004"]
    for call in handlers.calc_calls:
        assert call["current_input"].endswith("steps/s001/search.xyz")

    manifest = _manifest(work)
    terminals = {t["id"]: t["artifacts"] for t in manifest["terminals"]}
    assert terminals == {
        "s002": ["steps/s002/result.xyz"],
        "s004": ["steps/s004/result.xyz"],
    }


# ---------------------------------------------------------------------------
# R6-5: Duplicate effective source de-duplication
# ---------------------------------------------------------------------------
def test_r6_5_duplicate_effective_source_deduplication(tmp_path: Path, monkeypatch) -> None:
    """R6-5: Multiple disabled paths converging from the same upstream producer.

    s001 (calc, enabled)
    s002 (calc, disabled, inputs: [s001])
    s003 (calc, disabled, inputs: [s001])
    s004 (confgen, enabled, inputs: [s002, s003])   <- note: confgen accepts fan-in
    s005 (calc, enabled, inputs: [s004])

    Both s002 and s003 resolve to s001. De-duplication collapses identical effective
    sources into exactly 1 effective input, so s004's confgen executes with 1 geometry.

    Note: a *calc* step with 2 declared inputs is rejected at schema level (calc_fan_in
    validation rule). This test therefore uses confgen for the convergence step, which
    legitimately accepts multiple declared inputs. The effective-source deduplication
    ensures the confgen receives exactly 1 geometry despite the 2 bypass paths.
    """
    handlers = _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
        {
            "id": "s002",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s003",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s004",
            "type": "confgen",
            "inputs": ["s002", "s003"],
            "params": {"chains": ["1-2"]},
        },
        {"id": "s005", "type": "calc", "inputs": ["s004"], "params": {"keyword": "HF"}},
    ]
    plan = _plan(tmp_path, steps)
    # Without de-duplication, s004 would receive (('step', 's001'), ('step', 's001')).
    # With de-duplication it is collapsed to exactly 1 effective source.
    sources = resolve_effective_sources_v3(plan, external_input_count=1)
    assert sources["s004"] == (("step", "s001"),)
    assert sources["s005"] == (("step", "s004"),)

    issues = validate_effective_dataflow_v3(plan, external_input_count=1)
    assert issues == ()

    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    assert _load_state(work)["final_status"] == "completed"
    assert len(handlers.calc_calls) == 2
    assert handlers.calc_calls[0]["step_name"] == "s001"
    assert handlers.calc_calls[1]["step_name"] == "s005"


def test_r6_5b_external_slot_deduplication(tmp_path: Path) -> None:
    """Duplicate external slot sources through multiple disabled roots are deduplicated.

    Two disabled calc roots (s001, s002) both consume the single external slot.
    A confgen step (s003) declares both as inputs. After disabled-step bypass,
    s003's effective sources contain ("external", 1) twice — deduplicated to once,
    confirming the confgen will receive exactly 1 input file despite 2 declared routes.

    Note: a *calc* step with 2 declared inputs is rejected at schema level by the
    calc_fan_in validation rule (calc steps accept exactly 1 declared input). This
    scenario therefore uses confgen, which legitimately accepts multiple inputs.
    """
    steps = [
        {
            "id": "s001",
            "type": "calc",
            "enabled": False,
            "inputs": [],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s002",
            "type": "calc",
            "enabled": False,
            "inputs": [],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s003",
            "type": "confgen",
            "inputs": ["s001", "s002"],
            "params": {"chains": ["1-2"]},
        },
    ]
    plan = _plan(tmp_path, steps, inputs=1)
    sources = resolve_effective_sources_v3(plan, external_input_count=1)
    # Both disabled roots resolve to the same external slot; dedup collapses to 1.
    assert sources["s003"] == (("external", 1),)
    issues = validate_effective_dataflow_v3(plan, external_input_count=1)
    assert issues == ()


# ---------------------------------------------------------------------------
# R6-6: Unsupported fan-in rejected at preflight
# ---------------------------------------------------------------------------
def test_r6_6_unsupported_fan_in_rejected_at_preflight(tmp_path: Path) -> None:
    """R6-6: Calc step receiving multiple DISTINCT effective sources is rejected.

    Conversely, confgen step receiving multiple effective sources is accepted.
    """
    # A. Calc fan-in rejected
    calc_steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {
            "id": "s003",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001", "s002"],
            "params": {"keyword": "HF"},
        },
        {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
    ]
    plan_calc = _plan(tmp_path / "calc_test", calc_steps)
    sources_calc = resolve_effective_sources_v3(plan_calc, external_input_count=1)
    assert sources_calc["s004"] == (("step", "s001"), ("step", "s002"))

    issues_calc = validate_effective_dataflow_v3(plan_calc, external_input_count=1)
    assert len(issues_calc) == 1
    assert issues_calc[0].code == "effective_cardinality_unsupported"
    assert issues_calc[0].step_id == "s004"

    # Preflight fails closed before runtime directory creation
    work_calc = tmp_path / "calc_test" / "work"
    with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
        run_v3_workflow(
            input_xyz=[str(tmp_path / "calc_test" / "input_0.xyz")],
            config_file=str(tmp_path / "calc_test" / "wf.yaml"),
            work_dir=str(work_calc),
        )
    assert not work_calc.exists()

    # B. Confgen fan-in accepted
    confgen_steps = [
        {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
        {"id": "s002", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
        {
            "id": "s003",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001", "s002"],
            "params": {"keyword": "HF"},
        },
        {"id": "s004", "type": "confgen", "inputs": ["s003"], "params": {"chains": ["1-2"]}},
    ]
    plan_confgen = _plan(tmp_path / "confgen_test", confgen_steps)
    issues_confgen = validate_effective_dataflow_v3(plan_confgen, external_input_count=1)
    assert issues_confgen == ()


# ---------------------------------------------------------------------------
# R6-7: Root cardinality validation
# ---------------------------------------------------------------------------
def test_r6_7_root_cardinality_validation(tmp_path: Path) -> None:
    """R6-7: External input set cardinality checked against root step capabilities."""
    # A. Root calc with 2 external inputs -> rejected
    calc_root_steps = [
        {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
    ]
    plan_calc_root = _plan(tmp_path / "root_calc", calc_root_steps, inputs=2)
    issues_calc_root = validate_effective_dataflow_v3(plan_calc_root, external_input_count=2)
    assert len(issues_calc_root) == 1
    assert issues_calc_root[0].code == "effective_cardinality_unsupported"
    assert issues_calc_root[0].step_id == "s001"
    assert "calc root step receives 2 external inputs" in issues_calc_root[0].message

    # B. Root confgen with 2 external inputs -> accepted
    confgen_root_steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
    ]
    plan_confgen_root = _plan(tmp_path / "root_confgen", confgen_root_steps, inputs=2)
    issues_confgen_root = validate_effective_dataflow_v3(plan_confgen_root, external_input_count=2)
    assert issues_confgen_root == ()

    # C. Root calc with 1 external input -> accepted
    plan_calc_single = _plan(tmp_path / "root_calc_single", calc_root_steps, inputs=1)
    assert validate_effective_dataflow_v3(plan_calc_single, external_input_count=1) == ()

    # D. Zero external inputs on enabled step -> zero_effective_input
    issues_zero = validate_effective_dataflow_v3(plan_confgen_root, external_input_count=0)
    assert len(issues_zero) == 1
    assert issues_zero[0].code == "zero_effective_input"
    assert issues_zero[0].step_id == "s001"

    # E. Zero external inputs on disabled root -> invalid_bypass_result
    disabled_root_steps = [
        {
            "id": "s001",
            "type": "calc",
            "enabled": False,
            "inputs": [],
            "params": {"keyword": "HF"},
        },
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
    ]
    plan_disabled_root = _plan(tmp_path / "disabled_root", disabled_root_steps, inputs=0)
    issues_disabled = validate_effective_dataflow_v3(plan_disabled_root, external_input_count=0)
    assert [i.code for i in issues_disabled] == ["invalid_bypass_result", "zero_effective_input"]


# ---------------------------------------------------------------------------
# R6-8: Checkpoint capability static rejection
# ---------------------------------------------------------------------------
def test_r6_8_checkpoint_capability_static_rejection(tmp_path: Path) -> None:
    """R6-8: Checkpoint capability rejected statically at preflight.

    Catches disabled ancestors and incompatible programs before any runtime directory
    is created.
    """
    # A. Checkpoint from disabled ancestor
    disabled_anc_steps = [
        {
            "id": "s001",
            "type": "calc",
            "enabled": False,
            "inputs": [],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s002",
            "type": "calc",
            "inputs": ["s001"],
            "checkpoint": {"from_step": "s001"},
            "params": {"keyword": "HF"},
        },
    ]
    plan_disabled_anc = _plan(tmp_path / "chk_disabled", disabled_anc_steps)
    issues_disabled_anc = validate_effective_dataflow_v3(plan_disabled_anc, external_input_count=1)
    assert any(
        i.code == "checkpoint_capability_unsupported" and "disabled" in i.message
        for i in issues_disabled_anc
    )

    work_disabled = tmp_path / "chk_disabled" / "work"
    with pytest.raises(ConfFlowError, match="checkpoint"):
        run_v3_workflow(
            input_xyz=[str(tmp_path / "chk_disabled" / "input_0.xyz")],
            config_file=str(tmp_path / "chk_disabled" / "wf.yaml"),
            work_dir=str(work_disabled),
        )
    assert not work_disabled.exists()

    # B. Checkpoint program mismatch (g16 vs orca)
    prog_mismatch_steps = [
        {
            "id": "s001",
            "type": "calc",
            "inputs": [],
            "params": {"keyword": "HF", "iprog": "g16"},
        },
        {
            "id": "s002",
            "type": "calc",
            "inputs": ["s001"],
            "checkpoint": {"from_step": "s001"},
            "params": {"keyword": "HF", "iprog": "orca"},
        },
    ]
    plan_prog = _plan(tmp_path / "chk_prog", prog_mismatch_steps)
    issues_prog = validate_effective_dataflow_v3(plan_prog, external_input_count=1)
    assert any(
        i.code == "checkpoint_capability_unsupported" and "program" in i.message
        for i in issues_prog
    )

    work_prog = tmp_path / "chk_prog" / "work"
    with pytest.raises(ConfFlowError, match="program"):
        run_v3_workflow(
            input_xyz=[str(tmp_path / "chk_prog" / "input_0.xyz")],
            config_file=str(tmp_path / "chk_prog" / "wf.yaml"),
            work_dir=str(work_prog),
        )
    assert not work_prog.exists()

    # C. Disabled consuming step is exempt from checkpoint resolution
    disabled_consumer_steps = [
        {
            "id": "s001",
            "type": "calc",
            "enabled": False,
            "inputs": [],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s002",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "checkpoint": {"from_step": "s001"},
            "params": {"keyword": "HF"},
        },
    ]
    plan_disabled_consumer = _plan(tmp_path / "chk_consumer_off", disabled_consumer_steps)
    issues_consumer_off = validate_effective_dataflow_v3(
        plan_disabled_consumer, external_input_count=1
    )
    # No checkpoint issues reported because s002 is disabled
    assert not any(i.code == "checkpoint_capability_unsupported" for i in issues_consumer_off)


# ---------------------------------------------------------------------------
# R6-9: Checkpoint runtime existence still checked (defense-in-depth)
# ---------------------------------------------------------------------------
def test_r6_9_checkpoint_runtime_existence_still_checked(tmp_path: Path, monkeypatch) -> None:
    """R6-9: Statically valid checkpoints still verify runtime existence as defense-in-depth."""
    handlers = _FakeHandlers(monkeypatch)
    handlers.no_chk = True  # ancestor completes without writing checkpoint files
    steps = [
        {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
        {
            "id": "s002",
            "type": "calc",
            "inputs": ["s001"],
            "checkpoint": {"from_step": "s001"},
            "params": {"keyword": "HF"},
        },
    ]
    plan = _plan(tmp_path, steps)
    # Statically compatible programs and enabled ancestor
    assert validate_effective_dataflow_v3(plan, external_input_count=1) == ()

    work = tmp_path / "work"
    with pytest.raises(ConfFlowError, match="checkpoint artifacts for step 's002' are missing"):
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input_0.xyz")],
            config_file=str(tmp_path / "wf.yaml"),
            work_dir=str(work),
        )
    # Execution reached s001 before failing on s002
    assert (work / "steps" / "s001" / "result.xyz").exists()


# ---------------------------------------------------------------------------
# R6-10: Disabled terminal effective output
# ---------------------------------------------------------------------------
def test_r6_10_disabled_terminal_effective_output(tmp_path: Path, monkeypatch) -> None:
    """R6-10: Disabled terminal references effective producer for output_manifest.v2."""
    _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {
            "id": "s003",
            "type": "calc",
            "enabled": False,
            "inputs": ["s002"],
            "params": {"keyword": "HF"},
        },
    ]
    _plan(tmp_path, steps)
    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    manifest = _manifest(work)
    # Terminal s003 publishes s002's output artifact
    assert manifest["terminals"] == [
        {"id": "s003", "label": None, "artifacts": ["steps/s002/result.xyz"]}
    ]


def test_r6_10b_chained_disabled_terminal_effective_output(tmp_path: Path, monkeypatch) -> None:
    """Disabled terminal at the end of a multi-disabled chain publishes effective producer."""
    _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
        {
            "id": "s002",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s003",
            "type": "calc",
            "enabled": False,
            "inputs": ["s002"],
            "params": {"keyword": "HF"},
        },
    ]
    _plan(tmp_path, steps)
    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    manifest = _manifest(work)
    assert manifest["terminals"] == [
        {"id": "s003", "label": None, "artifacts": ["steps/s001/result.xyz"]}
    ]


# ---------------------------------------------------------------------------
# R6-11: Multiple terminals
# ---------------------------------------------------------------------------
def test_r6_11_multiple_terminals(tmp_path: Path, monkeypatch) -> None:
    """R6-11: Workflows with multiple terminals publish all terminal outputs unambiguously."""
    _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {"id": "s003", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {
            "id": "s004",
            "type": "calc",
            "enabled": False,
            "inputs": ["s003"],
            "params": {"keyword": "HF"},
        },
    ]
    _plan(tmp_path, steps)
    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    manifest = _manifest(work)
    terminals = {t["id"]: t["artifacts"] for t in manifest["terminals"]}
    # s002 is enabled terminal; s004 is disabled terminal bypassing to s003
    assert terminals == {
        "s002": ["steps/s002/result.xyz"],
        "s004": ["steps/s003/result.xyz"],
    }

    stats = _stats(work)
    assert stats["terminal_outputs"]["s002"] == [str((work / "steps/s002/result.xyz").resolve())]
    assert stats["terminal_outputs"]["s004"] == [str((work / "steps/s003/result.xyz").resolve())]


# ---------------------------------------------------------------------------
# R6-12: Unified effective sources across preflight, runtime, and finalizer
# ---------------------------------------------------------------------------
def test_r6_12_preflight_runtime_finalizer_identical_sources(tmp_path: Path, monkeypatch) -> None:
    """R6-12: Preflight, runtime, and finalizer produce byte-identical effective source results."""
    import confflow.workflow.v3_dataflow as v3_dataflow
    import confflow.workflow.v3_runtime as v3_runtime
    from confflow.workflow.v3_dataflow import resolve_effective_sources_v3 as real_resolver

    recorded_sources: list[dict[str, Any]] = []

    def spy_resolver(plan: WorkflowV3Plan, *, external_input_count: int):
        result = real_resolver(plan, external_input_count=external_input_count)
        recorded_sources.append(dict(result))
        return result

    monkeypatch.setattr(v3_dataflow, "resolve_effective_sources_v3", spy_resolver)
    monkeypatch.setattr(v3_runtime, "resolve_effective_sources_v3", spy_resolver)

    _FakeHandlers(monkeypatch)
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {
            "id": "s002",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
    ]
    _plan(tmp_path, steps)
    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input_0.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    # Invoked exactly 3 times: 1 preflight, 1 runtime execution, 1 finalizer stats
    assert len(recorded_sources) == 3
    preflight_sources, runtime_sources, finalizer_sources = recorded_sources
    assert preflight_sources == runtime_sources == finalizer_sources
    assert preflight_sources["s003"] == (("step", "s001"),)


# ---------------------------------------------------------------------------
# R6-13: V1 unchanged
# ---------------------------------------------------------------------------
def test_r6_13_v1_unchanged(tmp_path: Path, monkeypatch) -> None:
    """R6-13: V1 engine and historical execution remain completely unaffected."""
    from confflow.workflow.engine import run_workflow

    def fake_confgen(step_dir, *args, **kwargs):
        output = Path(step_dir) / "search.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            reused_existing = False
            copied_multi_frame = False

        return _Result()

    def fake_calc(*args, **kwargs):
        step_dir = kwargs.get("step_dir", args[0] if args else ".")
        output = Path(step_dir) / "result.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake calc\nH 0 0 0\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            reused_existing = False
            copied_multi_frame = False

        return _Result()

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
    monkeypatch.setattr("confflow.workflow.engine._run_calc_step", fake_calc)

    _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "v2.yaml"
    document = {
        "steps": [
            {"name": "gen_step", "type": "confgen", "params": {"chains": "1-2"}},
            {"name": "calc_bypass", "type": "calc", "enabled": False, "params": {"keyword": "HF"}},
            {
                "name": "calc_final",
                "type": "calc",
                "inputs": ["calc_bypass"],
                "params": {"keyword": "HF"},
            },
        ]
    }
    config.write_text(json.dumps(document), encoding="utf-8")
    work = tmp_path / "v1_work"
    run_workflow([str(tmp_path / "input.xyz")], str(config), str(work))

    # V1 directory structure uses step names, untouched by V3 stable IDs
    assert (work / "gen_step" / "search.xyz").exists()
    assert (work / "calc_final" / "result.xyz").exists()
    assert not (work / "calc_bypass" / "result.xyz").exists()

    # V1 output manifest format
    manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
    assert manifest["content_schema"] == "confflow.output_manifest.v1"
    assert "calc_final" in manifest["terminals"]
