"""R4.3 — Workflow V3 runtime projection, staging and internal execution.

The internal seam ``run_v3_workflow`` executes real V3 runs through the
existing step handlers (faked here at the seam's indirection points — the
same pattern the V1 engine tests use). ``CAPABILITIES[V3].execute`` stays
False: nothing here goes through the public engine.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.config.canonical import (
    CAPABILITIES,
    WORKFLOW_SCHEMA_VERSION_V3,
    can_execute,
)
from confflow.core.exceptions import ConfFlowError, StopRequestedError
from confflow.workflow.binding_v2 import BindingProvenanceV2
from confflow.workflow.execution_context import (
    resolve_execution_context_v3,
    workflow_execution_fingerprint_v3,
)
from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan
from confflow.workflow.state import WorkflowStateV2Store
from confflow.workflow.v3_runtime import (
    project_v3_runtime_plan,
    run_v3_workflow,
    stage_v3_external_inputs,
)

V3 = "confflow.workflow.v3"


def _write_xyz(path: Path, note: str = "seed") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n{note}\nH 0 0 0\n", encoding="utf-8")
    return path


def _clean_provenance():
    from confflow.workflow.binding_v2 import PRODUCER_IDENTITY, authoritative_provenance

    provenance = authoritative_provenance()
    return BindingProvenanceV2(
        workflow_schema=provenance.workflow_schema,
        workflow_schema_sha256=provenance.workflow_schema_sha256,
        canonicalization_version=provenance.canonicalization_version,
        producer_identity=PRODUCER_IDENTITY,
        producer_version="1.0.0",
        producer_commit="abc123",
        producer_dirty=False,
    )


def _write_config(path: Path, steps: list[dict[str, Any]], **root: Any) -> Path:
    document: dict[str, Any] = {"schema": V3, "steps": steps}
    document.update(root)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _plan(tmp_path: Path, steps: list[dict[str, Any]]) -> WorkflowV3Plan:
    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", steps)
    plan = build_workflow_plan([str(tmp_path / "input.xyz")], str(config))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


class _FakeHandlers:
    """Records handler invocations and writes standard step outputs."""

    def __init__(self, monkeypatch) -> None:
        self.calc_calls: list[dict[str, Any]] = []
        self.confgen_calls: list[dict[str, Any]] = []
        self._monkeypatch = monkeypatch
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_calc_step", self._calc)
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_confgen_step", self._confgen)

    no_chk = False

    def _calc(self, **kwargs: Any):
        self.calc_calls.append(kwargs)
        step_dir = Path(kwargs["step_dir"])
        output = step_dir / "result.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake calc\nH 0 0 0\n", encoding="utf-8")
        if not self.no_chk:
            # a successful calc writes its checkpoint backups (V1 convention)
            backups = step_dir / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            (backups / f"{kwargs['step_name']}.chk").write_bytes(b"chk")

        class _Result:
            output_path = str(output)
            failed_path = None
            reused_existing = False
            copied_multi_frame = False

        return _Result()

    def _confgen(self, **kwargs: Any):
        self.confgen_calls.append(kwargs)
        output = Path(kwargs["step_dir"]) / "search.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("2\nfake confgen\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            failed_path = None
            reused_existing = False
            copied_multi_frame = False

        return _Result()


def _run_steps(tmp_path: Path, steps: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", steps)
    return run_v3_workflow(
        input_xyz=[str(tmp_path / "input.xyz")],
        config_file=str(config),
        work_dir=str(tmp_path / "work"),
        **kw,
    )


def _steps_linear():
    return [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
    ]


# ---------------------------------------------------------------------------
# Capability: V3 execution enabled post-flip, future schemas fail closed
# ---------------------------------------------------------------------------
def test_v3_execution_capability_allows_execution() -> None:
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True
    assert not can_execute("confflow.workflow.v4")
    assert not can_execute("confflow.workflow.v99")


def test_seam_refuses_v2_documents(tmp_path: Path) -> None:
    _write_xyz(tmp_path / "input.xyz")
    config_path = tmp_path / "wf.yaml"
    config_path.write_text(
        json.dumps({"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}),
        encoding="utf-8",
    )
    with pytest.raises(ConfFlowError, match="Workflow V3 documents only"):
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input.xyz")],
            config_file=str(config_path),
            work_dir=str(tmp_path / "work"),
        )


# ---------------------------------------------------------------------------
# E1–E9 — fresh runs
# ---------------------------------------------------------------------------
class TestFreshRuns:
    def _run(self, tmp_path: Path, steps: list[dict[str, Any]], handlers: _FakeHandlers, **kw):
        return _run_steps(tmp_path, steps, **kw)

    def test_e1_single_calc(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        result = self._run(
            tmp_path,
            [{"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}}],
            handlers,
        )
        assert [call["step_name"] for call in handlers.calc_calls] == ["s001"]
        assert result["step_outputs"]["s001"].endswith("steps/s001/result.xyz")

    def test_e2_confgen_then_calc(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        result = self._run(tmp_path, _steps_linear()[:2], handlers)
        assert handlers.confgen_calls and handlers.calc_calls
        # the calc consumed the confgen output through the stable-ID graph
        assert handlers.calc_calls[0]["current_input"].endswith("steps/s001/search.xyz")
        assert result["final_output"].endswith("steps/s002/result.xyz")

    def test_e3_linear_three_steps(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        result = self._run(tmp_path, _steps_linear(), handlers)
        assert result["step_outputs"]["s003"].endswith("steps/s003/result.xyz")
        assert handlers.calc_calls[1]["current_input"].endswith("steps/s002/result.xyz")

    def test_e4_declared_calc_fan_in_rejected_at_planning(self, tmp_path: Path) -> None:
        """A declared calc fan-in never reaches the runtime (R3.4 owns it)."""
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(
            tmp_path / "wf.yaml",
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                {"id": "s003", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                {
                    "id": "s004",
                    "type": "calc",
                    "inputs": ["s002", "s003"],
                    "params": {"keyword": "HF"},
                },
            ],
        )
        with pytest.raises(ConfFlowError):
            build_workflow_plan([str(tmp_path / "input.xyz")], str(config))

    def test_e4b_branch_outputs_flow_by_id(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            {"id": "s003", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ]
        result = self._run(tmp_path, steps, handlers)
        assert handlers.calc_calls[0]["current_input"].endswith("steps/s001/search.xyz")
        assert handlers.calc_calls[1]["current_input"].endswith("steps/s001/search.xyz")
        assert set(result["step_outputs"]) == {"s001", "s002", "s003"}

    def test_e5_multiple_roots_consume_external_inputs(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        _write_xyz(tmp_path / "a.xyz", "A")
        _write_xyz(tmp_path / "b.xyz", "B")
        config = _write_config(
            tmp_path / "wf.yaml",
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            ],
        )
        run_v3_workflow(
            input_xyz=[str(tmp_path / "a.xyz"), str(tmp_path / "b.xyz")],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
        )
        for call in handlers.confgen_calls:
            # every root consumes the whole staged external input set
            assert sorted(call["current_input"]) == sorted(
                [
                    str(tmp_path / "work" / "external_inputs" / "input_0001.xyz"),
                    str(tmp_path / "work" / "external_inputs" / "input_0002.xyz"),
                ]
            )

    def test_e6_duplicate_labels(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "same",
                "params": {"chains": ["1-2"]},
            },
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "label": "same",
                "params": {"keyword": "HF"},
            },
        ]
        result = self._run(tmp_path, steps, handlers)
        assert set(result["step_outputs"]) == {"s001", "s002"}

    def test_e7_disabled_pass_through(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s002",
                "type": "calc",
                "enabled": False,
                "inputs": ["s001"],
                "params": {"itask": "opt"},
            },
            {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
        ]
        result = self._run(tmp_path, steps, handlers)
        # s002 never invoked a handler, is skipped, and forwards its input.
        assert [call["step_name"] for call in handlers.calc_calls] == ["s003"]
        assert handlers.calc_calls[0]["current_input"].endswith("steps/s001/search.xyz")
        store = WorkflowStateV2Store(str(tmp_path / "work"))
        state = store.load()
        assert state is not None
        assert state.steps["s002"].status == "skipped"
        assert result["step_outputs"]["s003"].endswith("steps/s003/result.xyz")

    def test_e8_unsupported_effective_cardinality_rejected_before_side_effects(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
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
        ]
        # Minimal R6: the known structural error is a preflight rejection —
        # no executor invocation, no state file, no staging, no directories.
        with pytest.raises(ConfFlowError, match="effective cardinality"):
            _run_steps(tmp_path, steps)
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []
        assert not (tmp_path / "work" / ".workflow_state.json").exists()
        assert not (tmp_path / "work" / "external_inputs").exists()
        assert not (tmp_path / "work" / "steps").exists()

    def test_e8b_runtime_cardinality_guard_survives_preflight_drift(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Defensive fail-closed (item 70).

        A caller that bypasses the R6 preflight still hits the runtime guard
        before any handler runs.
        """
        handlers = _FakeHandlers(monkeypatch)
        import confflow.workflow.v3_runtime as v3_runtime

        monkeypatch.setattr(v3_runtime, "validate_effective_dataflow_v3", lambda *a, **kw: ())
        steps = [
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
        ]
        with pytest.raises(ConfFlowError, match="effective inputs"):
            _run_steps(tmp_path, steps)
        # The defensive guard fires at the consuming step, before s004's
        # executor is invoked; upstream steps already ran.
        assert [call["step_name"] for call in handlers.calc_calls] == []

    def test_e9_deterministic_stable_id_schedule(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
            {"id": "s010", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002b", "type": "calc", "inputs": ["s010"], "params": {"keyword": "HF"}},
        ]
        self._run(tmp_path, steps, handlers)
        confgen_ids = [Path(call["step_dir"]).name for call in handlers.confgen_calls]
        assert confgen_ids == ["s002", "s010"]

    def test_e10_failure_updates_state_by_id(self, tmp_path: Path, monkeypatch) -> None:
        class _Failing(_FakeHandlers):
            def _calc(self, **kwargs: Any):
                if kwargs["step_name"] == "s002":
                    raise RuntimeError("boom")

                class _Result:
                    output_path = str(Path(kwargs["step_dir"]) / "result.xyz")

                return _Result()

        handlers = _Failing(monkeypatch)
        with pytest.raises(RuntimeError, match="boom"):
            self._run(tmp_path, _steps_linear(), handlers)
        state = WorkflowStateV2Store(str(tmp_path / "work")).load()
        assert state is not None
        assert state.steps["s001"].status == "completed"
        assert state.steps["s002"].status == "failed"
        assert state.steps["s002"].error == "boom"
        assert state.steps["s003"].status == "pending"
        assert state.final_status == "failed"

    def test_e11_no_label_dirname(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "join output!",
                "params": {"chains": ["1-2"]},
            },
        ]
        self._run(tmp_path, steps, handlers)
        assert (tmp_path / "work" / "steps" / "s001").is_dir()
        assert not (tmp_path / "work" / "join_output").exists()
        assert not list((tmp_path / "work").glob("*join*"))

    def test_e12_v1_engine_unchanged_regression(self, tmp_path: Path, monkeypatch) -> None:
        """The seam must not have touched the V1 engine path."""
        from types import SimpleNamespace

        from confflow.workflow.engine import run_workflow

        def fake_confgen(step_dir, current_input, params, input_files, global_config=None):
            output = Path(step_dir) / "search.xyz"
            output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")
            return SimpleNamespace(
                output_path=str(output), reused_existing=False, copied_multi_frame=False
            )

        monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(
            tmp_path / "v2.yaml",
            [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}],
        )
        # strip the schema key: a genuine V2 document
        document = json.loads(config.read_text(encoding="utf-8"))
        document.pop("schema")
        config.write_text(json.dumps(document), encoding="utf-8")
        run_workflow([str(tmp_path / "input.xyz")], str(config), str(tmp_path / "v1work"))
        assert (tmp_path / "v1work" / "gen").is_dir()


# ---------------------------------------------------------------------------
# P1–P9 — stable-ID runtime layout
# ---------------------------------------------------------------------------
class TestRuntimeLayout:
    def _run(self, tmp_path: Path, steps: list[dict[str, Any]], handlers: _FakeHandlers, **kw):
        return _run_steps(tmp_path, steps, **kw)

    def test_p1_p2_paths(self, tmp_path: Path) -> None:
        plan = _plan(
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
        from confflow.workflow.binding_v2 import build_workflow_binding_v2

        context = resolve_execution_context_v3(plan, input_files=plan.input_files)
        binding = build_workflow_binding_v2(plan, context, provenance=_clean_provenance())
        staged = stage_v3_external_inputs(
            str(tmp_path / "work"), list(plan.input_files), context.input_digests
        )
        runtime = project_v3_runtime_plan(
            plan, context, binding, work_dir=str(tmp_path / "work"), staged_inputs=staged
        )
        dirs = dict((step.id, step.step_dir) for step in runtime.steps)
        assert dirs["s001"] == str(tmp_path / "work" / "steps" / "s001")
        assert dirs["s_abcd2345"] == str(tmp_path / "work" / "steps" / "s_abcd2345")

    def test_p3_p4_p5_label_rename_and_reorder_and_duplicates(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "old",
                "params": {"chains": ["1-2"]},
            },
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "label": "old",
                "params": {"keyword": "HF"},
            },
        ]
        self._run(tmp_path, steps, handlers)
        assert (tmp_path / "work" / "steps" / "s001").is_dir()
        assert (tmp_path / "work" / "steps" / "s002").is_dir()
        assert not list((tmp_path / "work" / "steps").glob("*old*"))

    def test_p6_weird_label_never_touches_paths(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "../../danger / 名称",
                "params": {"chains": ["1-2"]},
            },
        ]
        self._run(tmp_path, steps, handlers)
        assert list((tmp_path / "work" / "steps").iterdir()) == [
            tmp_path / "work" / "steps" / "s001"
        ]

    def test_p7_invalid_id_rejected_before_mkdir(self, tmp_path: Path) -> None:
        plan = _plan(
            tmp_path,
            [{"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}],
        )
        # Force an illegal id into the projection input (simulating a boundary
        # bypass); the filesystem trust boundary must refuse before mkdir.
        (
            object.__setattr__(plan.steps[0], "id", "../evil")
            if dataclasses.is_dataclass(plan.steps[0])
            else None
        )
        from confflow.workflow.binding_v2 import build_workflow_binding_v2

        context = resolve_execution_context_v3(plan, input_files=plan.input_files)
        binding = build_workflow_binding_v2(plan, context, provenance=_clean_provenance())
        with pytest.raises(ConfFlowError, match="not a legal persisted"):
            project_v3_runtime_plan(
                plan,
                context,
                binding,
                work_dir=str(tmp_path / "work"),
                staged_inputs=stage_v3_external_inputs(
                    str(tmp_path / "work"), list(plan.input_files), context.input_digests
                ),
            )
        assert not (tmp_path / "work" / "steps").exists()

    def test_p8_no_legacy_dirname_allocator(self, tmp_path: Path, monkeypatch) -> None:
        import confflow.workflow.step_naming as step_naming

        def _boom(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("V3 runtime must not use the legacy dirname allocator")

        monkeypatch.setattr(step_naming, "build_step_dir_name_map", _boom)
        handlers = _FakeHandlers(monkeypatch)
        self._run(tmp_path, _steps_linear(), handlers)
        assert (tmp_path / "work" / "steps" / "s001").is_dir()

    def test_p9_v2_layout_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        from confflow.workflow.engine import run_workflow

        def fake_confgen(step_dir, current_input, params, input_files, global_config=None):
            output = Path(step_dir) / "search.xyz"
            output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")
            return SimpleNamespace(
                output_path=str(output), reused_existing=False, copied_multi_frame=False
            )

        monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(
            tmp_path / "v2.yaml",
            [{"name": "my step!", "type": "confgen", "params": {"chains": "1-2"}}],
        )
        document = json.loads(config.read_text(encoding="utf-8"))
        document.pop("schema")
        config.write_text(json.dumps(document), encoding="utf-8")
        run_workflow([str(tmp_path / "input.xyz")], str(config), str(tmp_path / "v1work"))
        # V2 keeps its flat sanitized-name layout (no steps/ grouping).
        assert (tmp_path / "v1work" / "my_step").is_dir()
        assert not (tmp_path / "v1work" / "steps").exists()


# ---------------------------------------------------------------------------
# ST1–ST8 — external input staging
# ---------------------------------------------------------------------------
class TestInputStaging:
    def _staged(self, work: Path, sources: list[Path]):
        digests = tuple("sha256:" + hashlib.sha256(p.read_bytes()).hexdigest() for p in sources)
        return stage_v3_external_inputs(str(work), [str(p) for p in sources], digests)

    def test_st1_slot_paths_deterministic(self, tmp_path: Path) -> None:
        a = _write_xyz(tmp_path / "a.xyz", "A")
        b = _write_xyz(tmp_path / "b.xyz", "B")
        staged = self._staged(tmp_path / "work", [a, b])
        assert staged == (
            str(tmp_path / "work" / "external_inputs" / "input_0001.xyz"),
            str(tmp_path / "work" / "external_inputs" / "input_0002.xyz"),
        )

    def test_st2_st3_rename_source_same_staged_path_and_bytes(self, tmp_path: Path) -> None:
        original = _write_xyz(tmp_path / "foo.xyz", "payload")
        first = self._staged(tmp_path / "work1", [original])
        renamed = _write_xyz(tmp_path / "bar" / "bar.xyz", "payload")
        second = self._staged(tmp_path / "work2", [renamed])
        # same RELATIVE staged path (slot-based) and identical staged bytes
        assert [Path(p).relative_to(tmp_path / "work1") for p in first] == [
            Path(p).relative_to(tmp_path / "work2") for p in second
        ]
        assert Path(first[0]).read_bytes() == Path(second[0]).read_bytes()

    def test_st4_reorder_changes_slot_assignment(self, tmp_path: Path) -> None:
        a = _write_xyz(tmp_path / "a.xyz", "A")
        b = _write_xyz(tmp_path / "b.xyz", "B")
        forward = self._staged(tmp_path / "forward", [a, b])
        backward = self._staged(tmp_path / "backward", [b, a])
        assert "A" in Path(forward[0]).read_text()
        assert "B" in Path(backward[0]).read_text()

    def test_st5_st6_no_source_stem_leakage(self, tmp_path: Path) -> None:
        weird = _write_xyz(tmp_path / "my.weird-stem_NAME.xyz", "payload")
        staged = self._staged(tmp_path / "work", [weird])
        assert Path(staged[0]).name == "input_0001.xyz"
        assert "my.weird-stem_NAME" not in staged[0]

    def test_st7_source_modified_after_resolution_fails_closed(self, tmp_path: Path) -> None:
        source = _write_xyz(tmp_path / "in.xyz", "v1")
        digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
        source.write_text("1\nseed\nH 9 9 9\n", encoding="utf-8")  # changed after resolution
        with pytest.raises(ConfFlowError, match="changed after"):
            stage_v3_external_inputs(str(tmp_path / "work"), [str(source)], (digest,))

    def test_st8_count_mismatch_fails_closed(self, tmp_path: Path) -> None:
        a = _write_xyz(tmp_path / "a.xyz", "A")
        with pytest.raises(ConfFlowError, match="changed during resolution"):
            stage_v3_external_inputs(str(tmp_path / "work"), [str(a)], ())

    def test_execution_consumes_staged_copies(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        source = _write_xyz(tmp_path / "source.xyz", "seed")
        config = _write_config(
            tmp_path / "wf.yaml",
            [{"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}],
        )
        run_v3_workflow(
            input_xyz=[str(source)],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
        )
        staged_input = tmp_path / "work" / "external_inputs" / "input_0001.xyz"
        assert handlers.confgen_calls[0]["current_input"] == str(staged_input)
        # After the run, mutating the source cannot affect the staged copy.
        source.write_text("1\nseed\nH 9 9 9\n", encoding="utf-8")
        assert "seed" in staged_input.read_text()


# ---------------------------------------------------------------------------
# CHK1–CHK9 — checkpoint runtime resolution
# ---------------------------------------------------------------------------
class TestCheckpointRuntime:
    def _checkpoint_steps(self, *, enabled_second: bool = True):
        return [
            {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
            {
                "id": "s002",
                "type": "calc",
                "enabled": enabled_second,
                "inputs": ["s001"],
                "params": {"keyword": "HF"},
                "checkpoint": {"from_step": "s001"},
            },
        ]

    def test_chk1_resolves_by_id(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        _run_steps(tmp_path, self._checkpoint_steps())
        chk_call = next(call for call in handlers.calc_calls if call["step_name"] == "s002")
        assert chk_call["input_chk_dir"] == str(tmp_path / "work" / "steps" / "s001" / "backups")

    def test_chk2_chk3_duplicate_labels_and_reorder_same_lookup(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = self._checkpoint_steps()
        steps = [
            dict(steps[0], label="same"),
            dict(steps[1], label="same"),
        ]
        reversed_steps = list(reversed([dict(s) for s in steps]))
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", steps)
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input.xyz")],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
        )
        chk_call = next(call for call in handlers.calc_calls if call["step_name"] == "s002")
        assert chk_call["input_chk_dir"].endswith("steps/s001/backups")
        # reordered document resolves identically
        (tmp_path / "reordered").mkdir()
        _write_xyz(tmp_path / "reordered" / "input.xyz")
        _write_config(tmp_path / "reordered" / "wf.yaml", reversed_steps)
        run_v3_workflow(
            input_xyz=[str(tmp_path / "reordered" / "input.xyz")],
            config_file=str(tmp_path / "reordered" / "wf.yaml"),
            work_dir=str(tmp_path / "work2"),
        )
        assert handlers.calc_calls[-1]["input_chk_dir"].endswith("steps/s001/backups")

    def test_chk4_missing_checkpoint_artifacts_fail_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        handlers = _FakeHandlers(monkeypatch)
        handlers.no_chk = True  # a calc that completed without writing checkpoints
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", self._checkpoint_steps())
        with pytest.raises(ConfFlowError, match="checkpoint artifacts"):
            run_v3_workflow(
                input_xyz=[str(tmp_path / "input.xyz")],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
            )

    def test_chk5_wrong_program_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        _FakeHandlers(monkeypatch)
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(
            tmp_path / "wf.yaml",
            [
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
                    "params": {"keyword": "HF", "iprog": "orca"},
                    "checkpoint": {"from_step": "s001"},
                },
            ],
        )
        # the ancestor produces a backups dir (so artifact presence passes);
        # the program mismatch is what must fail
        with pytest.raises(ConfFlowError, match="program"):
            run_v3_workflow(
                input_xyz=[str(tmp_path / "input.xyz")],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
            )

    def test_chk6_checkpoint_path_stays_within_work_root(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", self._checkpoint_steps())
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input.xyz")],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
        )
        chk_call = next(call for call in handlers.calc_calls if call["step_name"] == "s002")
        assert chk_call["input_chk_dir"].startswith(str(tmp_path / "work" / "steps"))

    def test_chk7_no_name_or_index_lookup(self, tmp_path: Path, monkeypatch) -> None:
        import confflow.workflow.step_handlers as step_handlers

        def _boom(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("V3 must not use the V2 chk_from_step resolver")

        monkeypatch.setattr(step_handlers, "_resolve_chk_input_dir", _boom)
        handlers = _FakeHandlers(monkeypatch)
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", self._checkpoint_steps())
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input.xyz")],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
        )
        assert any("input_chk_dir" in call for call in handlers.calc_calls)

    def test_chk8_chk9_label_rename_reorder_checkpoint_lookup(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        handlers = _FakeHandlers(monkeypatch)
        steps = [dict(s, label="same") for s in self._checkpoint_steps()]
        reordered = list(reversed([dict(s) for s in steps]))
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", reordered)
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input.xyz")],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
        )
        chk_call = next(call for call in handlers.calc_calls if call["step_name"] == "s002")
        assert chk_call["input_chk_dir"].endswith("steps/s001/backups")

    def test_disabled_calc_checkpoint_is_not_required(self, tmp_path: Path, monkeypatch) -> None:
        _FakeHandlers(monkeypatch)
        _write_xyz(tmp_path / "input.xyz")
        config = _write_config(
            tmp_path / "wf.yaml",
            [
                {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
                {
                    "id": "s002",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s001"],
                    "params": {"keyword": "HF"},
                    "checkpoint": {"from_step": "s001"},
                },
            ],
        )
        # s002 disabled: no checkpoints required, no error even though s001's
        # fake handler produces no backups directory.
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input.xyz")],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
        )


# ---------------------------------------------------------------------------
# I7 — the strong rename-invariance test (staging + downstream artifact)
# ---------------------------------------------------------------------------
def test_i7_strong_rename_invariance(tmp_path: Path, monkeypatch) -> None:
    handlers = _FakeHandlers(monkeypatch)
    # same bytes, different directories and basenames
    first_source = _write_xyz(tmp_path / "a" / "foo.xyz", "payload-bytes")
    second_source = _write_xyz(tmp_path / "b" / "bar.xyz", "payload-bytes")
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
    ]
    config = _write_config(tmp_path / "wf.yaml", steps)

    def _run_for(source: Path, work: Path):
        plan = build_workflow_plan([str(source)], str(config))
        assert isinstance(plan, WorkflowV3Plan)
        context = resolve_execution_context_v3(plan, input_files=plan.input_files)
        fingerprint = workflow_execution_fingerprint_v3(plan, context)
        result = run_v3_workflow(
            input_xyz=[str(source)], config_file=str(config), work_dir=str(work)
        )
        return fingerprint, result

    fingerprint_a, result_a = _run_for(first_source, tmp_path / "work_a")
    fingerprint_b, result_b = _run_for(second_source, tmp_path / "work_b")
    assert fingerprint_a == fingerprint_b
    # staged relative paths and bytes identical
    staged_a = sorted(
        p.relative_to(tmp_path / "work_a")
        for p in (tmp_path / "work_a" / "external_inputs").iterdir()
    )
    staged_b = sorted(
        p.relative_to(tmp_path / "work_b")
        for p in (tmp_path / "work_b" / "external_inputs").iterdir()
    )
    assert [str(p) for p in staged_a] == [str(p) for p in staged_b]
    # downstream execution artifacts have identical bytes
    artifact_a = Path(result_a["step_outputs"]["s002"])
    artifact_b = Path(result_b["step_outputs"]["s002"])
    assert artifact_a.read_bytes() == artifact_b.read_bytes()
    # and the same downstream job identity (stable step IDs)
    assert [call["step_name"] for call in handlers.calc_calls] == ["s002", "s002"]


# ---------------------------------------------------------------------------
# Binding / identity invariants that must hold through a run
# ---------------------------------------------------------------------------
def test_run_state_snapshot_is_diagnostic_only(tmp_path: Path, monkeypatch) -> None:
    _FakeHandlers(monkeypatch)
    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", _steps_linear())
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input.xyz")],
        config_file=str(config),
        work_dir=str(tmp_path / "work"),
    )
    state = WorkflowStateV2Store(str(tmp_path / "work")).load()
    assert state is not None
    # the snapshot came from the same resolved context as C (single source)
    assert state.input_digests
    assert all(d.startswith("sha256:") for d in state.input_digests)
    # and it is NOT a second authority: the binding C is what compares
    assert state.binding.execution_fingerprint.startswith("sha256:")


def test_events_carry_stable_step_ids(tmp_path: Path, monkeypatch) -> None:
    _FakeHandlers(monkeypatch)
    events: list[tuple[str, str, str]] = []
    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", _steps_linear())
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input.xyz")],
        config_file=str(config),
        work_dir=str(tmp_path / "work"),
        step_started_callback=lambda step_id, step_type, step_dir: events.append(
            (step_id, step_type, step_dir)
        ),
    )
    assert [event[0] for event in events] == ["s001", "s002", "s003"]
    assert all(event[2].startswith(str(tmp_path / "work" / "steps")) for event in events)


def test_pause_beacon_honored_at_step_boundary(tmp_path: Path, monkeypatch) -> None:
    handlers = _FakeHandlers(monkeypatch)
    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", _steps_linear())
    pause = tmp_path / "PAUSE"
    pause.write_text("", encoding="utf-8")  # already paused before step 1
    with pytest.raises(StopRequestedError):
        run_v3_workflow(
            input_xyz=[str(tmp_path / "input.xyz")],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
            pause_beacon_file=str(pause),
        )
    assert handlers.confgen_calls == []  # nothing executed after the boundary
    # removing the beacon lets the run complete
    pause.unlink()
    result = run_v3_workflow(
        input_xyz=[str(tmp_path / "input.xyz")],
        config_file=str(config),
        work_dir=str(tmp_path / "work"),
    )
    assert set(result["step_outputs"]) == {"s001", "s002", "s003"}
