"""Minimal R6 — execution-critical effective-dataflow validation (DF1–DF10).

The disabled-step bypass semantics are frozen (RFC §12): a disabled step is a
pass-through, and successors see the effective upstream sources. This suite
pins the *single* resolver shared by the preflight and the runtime, the
preflight's zero-side-effect rejection ordering, and V1 immunity.
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
from tests.test_workflow_v3_runtime import _FakeHandlers, _run_steps, _write_xyz

# Hermetic CI: fake orca/g16 entrypoints on PATH (real files, real identity).
pytestmark = pytest.mark.usefixtures("fake_qc_executables_on_path")

V3 = "confflow.workflow.v3"


def _plan_with_steps(
    tmp_path: Path, steps: list[dict[str, Any]], inputs: int = 1
) -> WorkflowV3Plan:
    for index in range(inputs):
        _write_xyz(tmp_path / f"input_{index}.xyz", note=f"slot {index}")
    config = tmp_path / "wf.yaml"
    document: dict[str, Any] = {"schema": V3, "steps": steps}
    config.write_text(json.dumps(document), encoding="utf-8")
    plan = build_workflow_plan(
        [str(tmp_path / f"input_{index}.xyz") for index in range(inputs)], str(config)
    )
    assert isinstance(plan, WorkflowV3Plan)
    return plan


_STEPS = [
    {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
]


# ---------------------------------------------------------------------------
# Pure resolver — the single bypass authority
# ---------------------------------------------------------------------------
class TestEffectiveSourceResolver:
    def test_df1_valid_disabled_pass_through(self, tmp_path: Path) -> None:
        plan = _plan_with_steps(
            tmp_path,
            _STEPS
            + [
                {
                    "id": "s003",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s002"],
                    "params": {"keyword": "HF", "itask": "opt"},
                },
                {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
            ],
        )
        sources = resolve_effective_sources_v3(plan, external_input_count=1)
        assert sources["s001"] == (("external", 1),)
        assert sources["s002"] == (("step", "s001"),)
        # disabled s003 forwards s002's effective source unchanged
        assert sources["s003"] == (("step", "s002"),)
        assert sources["s004"] == (("step", "s002"),)

    def test_df2_disabled_chain(self, tmp_path: Path) -> None:
        plan = _plan_with_steps(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s002",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s001"],
                    "params": {"itask": "opt"},
                },
                {
                    "id": "s003",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s002"],
                    "params": {"keyword": "HF", "itask": "opt"},
                },
                {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
            ],
        )
        sources = resolve_effective_sources_v3(plan, external_input_count=1)
        assert sources["s004"] == (("step", "s001"),)

    def test_df3_disabled_branch_bypass_multiplicity(self, tmp_path: Path) -> None:
        plan = _plan_with_steps(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s003",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s001", "s002"],
                    "params": {"itask": "opt"},
                },
                {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
            ],
        )
        sources = resolve_effective_sources_v3(plan, external_input_count=1)
        # multiplicity preserved through the bypass (runtime forwarding parity)
        assert sources["s004"] == (("step", "s001"), ("step", "s002"))
        issues = validate_effective_dataflow_v3(plan, external_input_count=1)
        assert [issue.code for issue in issues] == ["effective_cardinality_unsupported"]
        assert issues[0].step_id == "s004"

    def test_df9_no_label_or_index_semantics(self, tmp_path: Path) -> None:
        steps = [
            {
                "id": "s001",
                "label": "Generate",
                "type": "confgen",
                "inputs": [],
                "params": {"chains": ["1-2"]},
            },
            {
                "id": "s002",
                "label": "Generate",
                "type": "calc",
                "enabled": False,
                "inputs": ["s001"],
                "params": {"itask": "opt"},
            },
            {
                "id": "s003",
                "label": "Generate",
                "type": "calc",
                "inputs": ["s002"],
                "params": {"keyword": "HF"},
            },
        ]
        plan = _plan_with_steps(tmp_path, steps)
        sources = resolve_effective_sources_v3(plan, external_input_count=1)
        assert set(sources) == {"s001", "s002", "s003"}
        assert sources["s003"] == (("step", "s001"),)


# ---------------------------------------------------------------------------
# Preflight diagnostics
# ---------------------------------------------------------------------------
class TestPreflight:
    def test_df5_zero_effective_input_rejected(self, tmp_path: Path) -> None:
        plan = _plan_with_steps(tmp_path, _STEPS)
        issues = validate_effective_dataflow_v3(plan, external_input_count=0)
        assert [issue.code for issue in issues] == ["zero_effective_input"]
        assert issues[0].step_id == "s001"

    def test_df5_disabled_root_empty_bypass_rejected(self, tmp_path: Path) -> None:
        plan = _plan_with_steps(
            tmp_path,
            [
                {
                    "id": "s001",
                    "type": "calc",
                    "enabled": False,
                    "inputs": [],
                    "params": {"keyword": "HF", "itask": "opt"},
                },
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ],
        )
        issues = validate_effective_dataflow_v3(plan, external_input_count=0)
        assert [issue.code for issue in issues] == [
            "invalid_bypass_result",
            "zero_effective_input",
        ]
        assert issues[0].step_id == "s001"
        assert issues[1].step_id == "s002"

    def test_df6_valid_multiple_roots(self, tmp_path: Path) -> None:
        plan = _plan_with_steps(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s003", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ],
            inputs=2,
        )
        assert validate_effective_dataflow_v3(plan, external_input_count=2) == ()
        sources = resolve_effective_sources_v3(plan, external_input_count=2)
        assert sources["s001"] == (("external", 1), ("external", 2))

    def test_df4_preflight_rejects_before_any_side_effect(self, tmp_path: Path) -> None:
        _plan_with_steps(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s003",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s001", "s002"],
                    "params": {"itask": "opt"},
                },
                {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
            ],
        )
        work = tmp_path / "work"
        with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
            run_v3_workflow(
                input_xyz=[str(tmp_path / "input_0.xyz")],
                config_file=str(tmp_path / "wf.yaml"),
                work_dir=str(work),
            )
        # DF7: zero side effects — the work directory was never even created.
        assert not work.exists()

    def test_df7_preflight_itself_is_pure(self, tmp_path: Path) -> None:
        plan = _plan_with_steps(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s002",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s001"],
                    "params": {"itask": "opt"},
                },
                {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
            ],
        )
        first = validate_effective_dataflow_v3(plan, external_input_count=1)
        second = validate_effective_dataflow_v3(plan, external_input_count=1)
        assert first == second == ()
        assert not (tmp_path / "work").exists()

    def test_df8_runtime_consumes_the_same_resolver(self, tmp_path: Path, monkeypatch) -> None:
        import confflow.workflow.v3_dataflow as v3_dataflow
        import confflow.workflow.v3_runtime as v3_runtime
        from confflow.workflow.v3_dataflow import resolve_effective_sources_v3 as real

        calls: list[dict[str, Any]] = []

        def spy(plan: WorkflowV3Plan, *, external_input_count: int):
            calls.append({"ids": [step.id for step in plan.steps], "n": external_input_count})
            return real(plan, external_input_count=external_input_count)

        # both consumers patch through to the one spy: the preflight
        # (v3_dataflow) and the runtime resolver (v3_runtime).
        monkeypatch.setattr(v3_dataflow, "resolve_effective_sources_v3", spy)
        monkeypatch.setattr(v3_runtime, "resolve_effective_sources_v3", spy)
        handlers = _FakeHandlers(monkeypatch)
        _run_steps(
            tmp_path,
            _STEPS
            + [
                {
                    "id": "s003",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s002"],
                    "params": {"keyword": "HF", "itask": "opt"},
                },
                {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
            ],
        )
        # preflight, runtime and finalize all resolved through the one helper
        assert len(calls) == 3
        assert calls[0] == calls[1] == calls[2]
        # the disabled s003 forwarded s002's output to s004
        assert handlers.calc_calls[-1]["current_input"].endswith("steps/s002/result.xyz")


# ---------------------------------------------------------------------------
# V1 immunity
# ---------------------------------------------------------------------------
def test_df10_v1_engine_unaffected(tmp_path: Path, monkeypatch) -> None:
    """The V2 engine keeps its own bypass branch; the V3 preflight is absent."""
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
            {"name": "gen", "type": "confgen", "params": {"chains": "1-2"}},
            {"name": "skipped", "type": "calc", "enabled": False, "params": {"keyword": "HF"}},
            {"name": "final", "type": "calc", "inputs": ["skipped"], "params": {"keyword": "HF"}},
        ]
    }
    config.write_text(json.dumps(document), encoding="utf-8")
    work = tmp_path / "v1work"
    run_workflow([str(tmp_path / "input.xyz")], str(config), str(work))
    # V1 layout by dirname — untouched by the V3 dataflow module.
    assert (work / "gen").is_dir()
    assert (work / "final").is_dir()
