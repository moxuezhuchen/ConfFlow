"""R4.4 — Workflow V3 resume, rerun-failed, pause/cancel and state durability.

All tests drive the internal seam directly; ``CAPABILITIES[V3].execute``
stays False and the public engine/CLI/service/worker paths stay blocked.
Every binding-mismatch case must reject with ZERO side effects: the state
file byte-identical, no staging, no cleanup, no handler calls.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from confflow.config.canonical import CAPABILITIES, WORKFLOW_SCHEMA_VERSION_V3, can_execute
from confflow.core.exceptions import ConfFlowError, StopRequestedError
from confflow.workflow.state import WorkflowStateCompatibilityError, WorkflowStateV2Store
from confflow.workflow.v3_runtime import run_v3_workflow

V3 = "confflow.workflow.v3"
STATE_FILE = ".workflow_state.json"

# Hermetic CI: resolve bare "orca"/"g16" defaults against fake executables on
# PATH (no system QC programs required). Explicit fail-closed spellings never
# touch PATH and stay fail-closed.
pytestmark = pytest.mark.usefixtures("fake_qc_executables_on_path")


def _write_xyz(path: Path, note: str = "seed") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n{note}\nH 0 0 0\n", encoding="utf-8")
    return path


def _linear():
    return [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
    ]


class _FakeHandlers:
    """Fake handlers with per-step failure and call recording."""

    def __init__(self, monkeypatch, *, fail_ids: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_ids = fail_ids or set()
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_calc_step", self._calc)
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_confgen_step", self._confgen)

    def _calc(self, **kwargs: Any):
        self.calls.append(kwargs["step_name"])
        if kwargs["step_name"] in self.fail_ids:
            raise RuntimeError(f"calc failed: {kwargs['step_name']}")
        return self._finish(kwargs, "result.xyz")

    def _confgen(self, **kwargs: Any):
        self.calls.append(Path(kwargs["step_dir"]).name)
        return self._finish(kwargs, "search.xyz")

    def _finish(self, kwargs: Any, name: str):
        output = Path(kwargs["step_dir"]) / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            failed_path = None
            reused_existing = False
            copied_multi_frame = False

        return _Result()


def _write_config(
    path: Path, steps: list[dict[str, Any]], root: dict[str, Any] | None = None
) -> Path:
    document: dict[str, Any] = {"schema": V3, "steps": steps}
    if root:
        document.update(root)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _setup(tmp_path: Path, steps: list[dict[str, Any]] | None = None):
    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", steps or _linear())
    return [str(tmp_path / "input.xyz")], str(config)


def _state_bytes(work: Path) -> bytes:
    return (work / STATE_FILE).read_bytes()


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _assert_intact(work: Path, before: bytes, handlers: _FakeHandlers) -> None:
    assert _state_bytes(work) == before
    assert handlers.calls == []
    assert (work / "external_inputs").exists()  # untouched, not removed
    assert not list(work.rglob("*.tmp"))


# ---------------------------------------------------------------------------
# R1–R6 — resume successes
# ---------------------------------------------------------------------------
class TestResumeSuccess:
    def _interrupted(self, tmp_path: Path, handlers: _FakeHandlers, work: Path):
        inputs, config = _setup(tmp_path)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        assert handlers.calls == ["s001", "s002"]
        return inputs, config

    def test_r1_interrupt_then_resume_continues(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        handlers = _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = self._interrupted(tmp_path, handlers, work)

        resume_handlers = _FakeHandlers(monkeypatch)
        result = run_v3_workflow(
            input_xyz=inputs, config_file=config, work_dir=str(work), resume=True
        )
        # s001 was completed and is NOT re-run; s002/s003 re-execute.
        assert resume_handlers.calls == ["s002", "s003"]
        assert set(result["step_outputs"]) == {"s001", "s002", "s003"}

    def test_r2_completed_step_not_rerun(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        handlers = _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = self._interrupted(tmp_path, handlers, work)
        completed_bytes = (work / "steps" / "s001" / "search.xyz").read_bytes()
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert resume_handlers.calls == ["s002", "s003"]
        assert (work / "steps" / "s001" / "search.xyz").read_bytes() == completed_bytes

    def test_r3_label_rename_resume(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        handlers = _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = self._interrupted(tmp_path, handlers, work)
        steps = _linear()
        steps[0]["label"] = "renamed display"
        config = _write_config(tmp_path / "wf.yaml", steps)
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert resume_handlers.calls == ["s002", "s003"]
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        assert state.steps["s001"].label == "renamed display"  # snapshot updated
        assert state.steps["s001"].id == "s001"  # identity untouched

    def test_r4_array_reorder_resume(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        handlers = _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = self._interrupted(tmp_path, handlers, work)
        steps = list(reversed(_linear()))
        config = _write_config(tmp_path / "wf.yaml", steps)
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert resume_handlers.calls == ["s002", "s003"]
        assert (work / "steps" / "s001").is_dir()  # runtime dirs untouched

    def test_r5_annotations_change_resume(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        handlers = _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = self._interrupted(tmp_path, handlers, work)
        steps = _linear()
        steps[1]["annotations"] = {"confflow.migration.v2": {"x": 1}}
        config = _write_config(tmp_path / "wf.yaml", steps)
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert resume_handlers.calls == ["s002", "s003"]

    def test_r6_input_rename_resume(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        handlers = _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = self._interrupted(tmp_path, handlers, work)
        renamed = _write_xyz(tmp_path / "renamed-input.xyz")
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(
            input_xyz=[str(renamed)], config_file=config, work_dir=str(work), resume=True
        )
        assert resume_handlers.calls == ["s002", "s003"]
        # staged slot path is unchanged by the source rename
        assert (work / "external_inputs" / "input_0001.xyz").exists()


# ---------------------------------------------------------------------------
# R7–R18 — binding mismatches: zero side effects
# ---------------------------------------------------------------------------
class TestResumeMismatches:
    def _resume_rejected(
        self, tmp_path: Path, monkeypatch, mutate_config
    ) -> tuple[bytes, _FakeHandlers]:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = _setup(tmp_path)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        before = _state_bytes(work)
        mutated = mutate_config(tmp_path)
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch|extension_unknown"):
            run_v3_workflow(input_xyz=inputs, config_file=mutated, work_dir=str(work), resume=True)
        assert _state_bytes(work) == before
        assert bad_handlers.calls == []
        return before, bad_handlers

    def test_r7_r8_id_and_graph_change_reject(self, tmp_path: Path, monkeypatch) -> None:
        def _mutate(tmp_path: Path):
            steps = _linear()
            steps[1]["id"] = "s099"  # a delete+create: identity change
            steps[2]["inputs"] = ["s099"]
            return _write_config(tmp_path / "mutated.yaml", steps)

        self._resume_rejected(tmp_path, monkeypatch, _mutate)

    def test_r8b_graph_edge_change_reject(self, tmp_path: Path, monkeypatch) -> None:
        def _mutate(tmp_path: Path):
            steps = _linear()
            steps[2]["inputs"] = ["s001"]  # re-wire s003 to the root
            return _write_config(tmp_path / "mutated.yaml", steps)

        self._resume_rejected(tmp_path, monkeypatch, _mutate)

    def test_r9_scientific_param_reject(self, tmp_path: Path, monkeypatch) -> None:
        def _mutate(tmp_path: Path):
            steps = _linear()
            steps[1]["params"]["keyword"] = "B3LYP"
            return _write_config(tmp_path / "mutated.yaml", steps)

        self._resume_rejected(tmp_path, monkeypatch, _mutate)

    def test_r10_extension_reject(self, tmp_path: Path, monkeypatch) -> None:
        def _mutate(tmp_path: Path):
            steps = _linear()
            steps[1]["extensions"] = {"vendor.unknown": {"x": 1}}
            return _write_config(tmp_path / "mutated.yaml", steps)

        self._resume_rejected(tmp_path, monkeypatch, _mutate)

    def test_r11_resource_change_reject_c(self, tmp_path: Path, monkeypatch) -> None:
        def _mutate(tmp_path: Path):
            steps = _linear()
            return _write_config(
                tmp_path / "mutated.yaml", steps, root={"global": {"cores_per_task": 8}}
            )

        before, handlers = self._resume_rejected(tmp_path, monkeypatch, _mutate)
        del before, handlers

    def test_r12_input_content_reject_c(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = _setup(tmp_path)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        before = _state_bytes(work)
        changed = _write_xyz(tmp_path / "input.xyz", "changed-bytes")
        del changed
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(
                input_xyz=[str(tmp_path / "input.xyz")],
                config_file=config,
                work_dir=str(work),
                resume=True,
            )
        assert _state_bytes(work) == before
        assert bad_handlers.calls == []

    def test_r13_input_order_reject_c(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            ],
        )
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        before = _state_bytes(work)
        second = _write_xyz(tmp_path / "second.xyz", "second")
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(
                input_xyz=[second, inputs[0]],
                config_file=config,
                work_dir=str(work),
                resume=True,
            )
        del second
        assert _state_bytes(work) == before
        assert bad_handlers.calls == []

    def test_r14_executable_identity_reject_c(self, tmp_path: Path, monkeypatch) -> None:
        exe = tmp_path / "orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s002",
                    "type": "calc",
                    "inputs": ["s001"],
                    "params": {"keyword": "HF", "iprog": "orca", "orca_path": str(exe)},
                },
            ],
        )
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        before = _state_bytes(work)
        exe.write_text("#!/bin/sh\n# upgraded\nexit 0\n", encoding="utf-8")
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert _state_bytes(work) == before
        assert bad_handlers.calls == []

    def test_r15_producer_reject_b(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        before = _state_bytes(work)
        state = json.loads(before.decode("utf-8"))
        state["binding"]["provenance"]["producer_version"] = "0.0.1"
        (work / STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        before = _state_bytes(work)
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert _state_bytes(work) == before
        assert bad_handlers.calls == []

    def test_r16_dirty_reject(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        state = json.loads(_state_bytes(work).decode("utf-8"))
        state["binding"]["provenance"]["producer_dirty"] = True
        (work / STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        before = _state_bytes(work)
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert _state_bytes(work) == before
        assert bad_handlers.calls == []

    def test_r17_canonicalization_reject(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        state = json.loads(_state_bytes(work).decode("utf-8"))
        state["binding"]["provenance"]["canonicalization_version"] = "other.v1"
        (work / STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        before = _state_bytes(work)
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert _state_bytes(work) == before
        assert bad_handlers.calls == []

    def test_r18_schema_digest_only_warn_and_allow(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = _setup(tmp_path)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        state = json.loads(_state_bytes(work).decode("utf-8"))
        state["binding"]["provenance"]["workflow_schema_sha256"] = "d" * 64
        (work / STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        before = _state_bytes(work)
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert resume_handlers.calls == ["s002", "s003"]
        assert _state_bytes(work) != before  # state advanced (allowed resume)

    def test_r19_completed_output_missing_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = _setup(tmp_path)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        # s001 completed; delete its artifact and mark s002 completed too so
        # resume reaches s003 requiring s002's output.
        (work / "steps" / "s001" / "search.xyz").unlink()
        store = WorkflowStateV2Store(work)
        state = store.load()
        assert state is not None
        state.update_step("s002", status="completed")
        store.save(state)
        before = _state_bytes(work)
        resume_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="artifact is missing"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert _state_bytes(work) == before
        assert resume_handlers.calls == []

    def test_r20_v1_resume_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """A V2 workflow keeps using workflow_state.v1 end to end."""
        from confflow.workflow.engine import run_workflow

        def fake_confgen(step_dir, current_input, params, input_files, global_config=None):
            output = Path(step_dir) / "search.xyz"
            output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")
            from types import SimpleNamespace

            return SimpleNamespace(
                output_path=str(output), reused_existing=False, copied_multi_frame=False
            )

        monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
        _write_xyz(tmp_path / "input.xyz")
        config = tmp_path / "v2.yaml"
        config.write_text(
            json.dumps(
                {"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}
            ),
            encoding="utf-8",
        )
        work = tmp_path / "v1work"
        run_workflow([str(tmp_path / "input.xyz")], str(config), str(work))
        assert (work / ".workflow_state.json").exists()
        payload = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert payload["content_schema"] == "confflow.workflow_state.v1"


# ---------------------------------------------------------------------------
# RF1–RF8 — rerun-failed by stable ID
# ---------------------------------------------------------------------------
class TestRerunFailed:
    def _failed_run(self, tmp_path: Path, monkeypatch) -> tuple[list[str], str, Path]:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = _setup(tmp_path)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        return inputs, config, work

    def test_rf1_failed_step_rerun_by_id(self, tmp_path: Path, monkeypatch) -> None:
        inputs, config, work = self._failed_run(tmp_path, monkeypatch)
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        assert state.steps["s002"].status == "failed"  # identified by stable ID
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert resume_handlers.calls == ["s002", "s003"]

    def test_rf2_rf3_label_selector_is_not_an_identity(self, tmp_path: Path) -> None:
        # The rerun selector is the durable stable ID: no API exists that
        # accepts a label or legacy dirname, and duplicate labels are inert.

        from confflow.workflow.state import WorkflowStateV2

        assert not hasattr(WorkflowStateV2, "rerun_by_label")
        assert not hasattr(WorkflowStateV2, "rerun_by_dirname")

    def test_rf4_rf5_completed_steps_preserved_pending_rerun(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # s002 fails; everything not completed is re-executed on resume, while
        # completed s001 is reused and its artifact bytes are preserved.
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch, fail_ids={"s002"})
        steps = [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
            {"id": "s004", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
        ]
        inputs, config = _setup(tmp_path, steps)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        s001_artifact = work / "steps" / "s001" / "search.xyz"
        s001_bytes = s001_artifact.read_bytes()
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        assert state.steps["s001"].status == "completed"
        for failed_or_pending in ("s002", "s003", "s004"):
            assert state.steps[failed_or_pending].status != "completed"
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        # completed s001 reused; every incomplete step re-executed
        assert resume_handlers.calls == ["s002", "s003", "s004"]
        assert s001_artifact.read_bytes() == s001_bytes

    def test_rf6_affected_dirs_cleaned_by_id(self, tmp_path: Path, monkeypatch) -> None:
        inputs, config, work = self._failed_run(tmp_path, monkeypatch)
        # the failed step's partial dir exists and is replaced on rerun
        assert (work / "steps" / "s002").exists()
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert (work / "steps" / "s002" / "result.xyz").exists()
        del inputs, config, resume_handlers

    def test_rf7_binding_mismatch_before_cleanup(self, tmp_path: Path, monkeypatch) -> None:
        inputs, config, work = self._failed_run(tmp_path, monkeypatch)
        steps = _linear()
        steps[1]["params"]["keyword"] = "B3LYP"
        mutated = _write_config(tmp_path / "mutated.yaml", steps)
        before = _state_bytes(work)
        artifact = work / "steps" / "s002" / "result.xyz"
        artifact_bytes = artifact.read_bytes() if artifact.exists() else None
        bad_handlers = _FakeHandlers(monkeypatch)
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(input_xyz=inputs, config_file=mutated, work_dir=str(work), resume=True)
        assert _state_bytes(work) == before
        if artifact_bytes is not None:
            assert artifact.read_bytes() == artifact_bytes
        assert bad_handlers.calls == []

    def test_rf8_v1_rerun_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """The V1 rerun-failed path still works through its own config/state."""
        from confflow.config.canonical import require_executable
        from confflow.workflow.rerun_failed import RerunFailedUsageError, run_rerun_failed

        _write_xyz(tmp_path / "input.xyz")
        config = tmp_path / "v2.yaml"
        config.write_text(
            json.dumps(
                {"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}
            ),
            encoding="utf-8",
        )
        # post-flip: V3 passes capability preflight to the rerun-failed handler
        v3_config = _write_config(
            tmp_path / "v3.yaml",
            [{"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}],
        )
        with pytest.raises(RerunFailedUsageError, match="Step directory does not exist"):
            run_rerun_failed(
                step_dir=str(tmp_path / "steps"),
                config_file=str(v3_config),
                step_ref="s001",
            )
        # future schema versions remain blocked by the execution requirement
        with pytest.raises(ConfFlowError, match="execution requires state/binding v2"):
            require_executable("confflow.workflow.v4")
        del run_rerun_failed, RerunFailedUsageError


# ---------------------------------------------------------------------------
# PC1–PC8 — pause/cancel lifecycle
# ---------------------------------------------------------------------------
class TestPauseCancel:
    def _steps(self):
        return _linear()

    def test_pc1_pc2_pc3_pause_resume_with_stable_ids(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path, self._steps())
        pause = work / "PAUSE"

        def _start_pause(step_id: str, step_type: str, step_dir: str) -> None:
            if step_id == "s002":
                pause.write_text("", encoding="utf-8")

        with pytest.raises(StopRequestedError):
            run_v3_workflow(
                input_xyz=inputs,
                config_file=config,
                work_dir=str(work),
                pause_beacon_file=str(pause),
                step_started_callback=_start_pause,
            )
        # s001 completed; s002 was interrupted at the boundary → re-executes
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        assert state.steps["s001"].status == "completed"
        assert set(state.steps) == {"s001", "s002", "s003"}  # stable IDs
        pause.unlink()
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        # s002 completed before the pause boundary and is reused, not re-run
        assert resume_handlers.calls == ["s003"]

    def test_pc4_pc5_cancel_stops_later_steps(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path, self._steps())
        cancel = work / "CANCEL"

        def _start_cancel(step_id: str, step_type: str, step_dir: str) -> None:
            if step_id == "s002":
                cancel.write_text("", encoding="utf-8")

        with pytest.raises(StopRequestedError):
            run_v3_workflow(
                input_xyz=inputs,
                config_file=config,
                work_dir=str(work),
                cancel_beacon_file=str(cancel),
                step_started_callback=_start_cancel,
            )
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        assert state.steps["s001"].status == "completed"
        # the started step finished; the cancel boundary stopped everything after
        assert state.steps["s002"].status == "completed"
        assert state.steps["s003"].status == "pending"
        assert not (work / "steps" / "s003").exists()  # later step never started

    def test_pc6_binding_unchanged_through_pause_and_cancel(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path, self._steps())
        pause = work / "PAUSE"

        def _start_pause(step_id: str, step_type: str, step_dir: str) -> None:
            if step_id == "s002":
                pause.write_text("", encoding="utf-8")

        with pytest.raises(StopRequestedError):
            run_v3_workflow(
                input_xyz=inputs,
                config_file=config,
                work_dir=str(work),
                pause_beacon_file=str(pause),
                step_started_callback=_start_pause,
            )
        binding = json.loads(_state_bytes(work).decode("utf-8"))["binding"]
        pause.unlink()
        cancel = work / "CANCEL"
        cancel.write_text("", encoding="utf-8")  # cancel pending before resume
        with pytest.raises(StopRequestedError):
            run_v3_workflow(
                input_xyz=inputs,
                config_file=config,
                work_dir=str(work),
                resume=True,
                cancel_beacon_file=str(cancel),
            )
        binding_after = json.loads(_state_bytes(work).decode("utf-8"))["binding"]
        assert binding == binding_after

    def test_pc7_duplicate_labels_irrelevant(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
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
        inputs, config = _setup(tmp_path, steps)
        pause = tmp_path / "PAUSE"  # outside the not-yet-created work dir
        pause.write_text("", encoding="utf-8")
        with pytest.raises(StopRequestedError):
            run_v3_workflow(
                input_xyz=inputs,
                config_file=config,
                work_dir=str(work),
                pause_beacon_file=str(pause),
            )
        pause.unlink()
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        assert resume_handlers.calls == ["s001", "s002"]

    def test_pc8_v1_control_tests_unchanged(self) -> None:
        """The V1 lifecycle machinery is untouched — capability permits V3 and fails closed for future versions."""
        assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True
        assert not can_execute("confflow.workflow.v4")


# ---------------------------------------------------------------------------
# SD1–SD7 — state durability during execution
# ---------------------------------------------------------------------------
class TestStateDurability:
    def test_sd1_sd2_progress_atomic_and_binding_immutable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        bindings: list[str] = []
        original_save = WorkflowStateV2Store.save

        def _spy_save(self: WorkflowStateV2Store, state: Any) -> None:
            original_save(self, state)
            payload = json.loads(Path(self.path).read_text("utf-8"))
            bindings.append(json.dumps(payload["binding"], sort_keys=True))

        monkeypatch.setattr(WorkflowStateV2Store, "save", _spy_save)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        assert len(bindings) >= 4
        assert len(set(bindings)) == 1  # every save carried the same binding

    def test_sd3_unknown_step_id_rejected(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        with pytest.raises(WorkflowStateCompatibilityError):
            state.update_step("s999", status="completed")

    def test_sd4_failed_state_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch, fail_ids={"s002"})
        inputs, config = _setup(tmp_path)
        with pytest.raises(RuntimeError, match="calc failed: s002"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        assert state.final_status == "failed"
        assert state.steps["s002"].fail_count == 1

    def test_sd5_submitted_interrupted_is_retried(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        # simulate a crash between submit and complete
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        state.update_step("s003", status="submitted")
        WorkflowStateV2Store(work).save(state)
        resume_handlers = _FakeHandlers(monkeypatch)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)
        # the submitted step was retried, never trusted as completed
        assert resume_handlers.calls == ["s003"]

    def test_sd6_label_snapshot_nonidentity(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=False)
        state = WorkflowStateV2Store(work).load()
        assert state is not None
        state.update_step("s001", label="whatever")
        WorkflowStateV2Store(work).save(state)
        reloaded = WorkflowStateV2Store(work).load()
        assert reloaded is not None
        assert reloaded.steps["s001"].id == "s001"
        assert set(reloaded.steps) == {"s001", "s002", "s003"}

    def test_sd7_v1_state_cross_version_refused(self, tmp_path: Path, monkeypatch) -> None:
        work = tmp_path / "work"
        work.mkdir()
        (work / STATE_FILE).write_text(
            json.dumps(
                {
                    "run_id": "legacy",
                    "work_dir": str(work),
                    "config_file": "wf.yaml",
                    "steps": {"gen": {"name": "gen", "type": "confgen", "status": "completed"}},
                }
            ),
            encoding="utf-8",
        )
        _FakeHandlers(monkeypatch)
        inputs, config = _setup(tmp_path)
        with pytest.raises(ConfFlowError, match="workflow_state.v2"):
            run_v3_workflow(input_xyz=inputs, config_file=config, work_dir=str(work), resume=True)


# ---------------------------------------------------------------------------
# Public-surface boundaries: V3 permitted post-flip, future schemas fail closed
# ---------------------------------------------------------------------------
def test_public_engine_admits_v3_and_blocks_future_schemas(tmp_path: Path, monkeypatch) -> None:
    """The public engine admits V3 post-flip, while future/unknown schemas fail closed."""
    from confflow.config.canonical import require_executable
    from confflow.workflow.engine import run_workflow

    handlers = _FakeHandlers(monkeypatch)
    _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "wf.yaml"
    config.write_text(
        json.dumps({"schema": V3, "steps": _linear()}),
        encoding="utf-8",
    )
    # Post-flip: V3 executes through the public engine
    run_workflow([str(tmp_path / "input.xyz")], str(config), str(tmp_path / "work"))
    assert handlers.calls == ["s001", "s002", "s003"]
    assert (tmp_path / "work" / ".workflow_state.json").exists()

    # Future / unknown schemas fail closed with zero side effects
    work_future = tmp_path / "work_future"
    future_config = tmp_path / "wf_future.yaml"
    future_config.write_text(
        json.dumps({"schema": "confflow.workflow.v4", "steps": _linear()}),
        encoding="utf-8",
    )
    from confflow.config.canonical.issues import ConfigValidationError

    with pytest.raises((ConfFlowError, ConfigValidationError), match="unsupported workflow schema"):
        run_workflow([str(tmp_path / "input.xyz")], str(future_config), str(work_future))
    assert not work_future.exists()

    # The capability gate explicitly fails closed for future schemas
    with pytest.raises(ConfFlowError, match="execution requires state/binding v2"):
        require_executable("confflow.workflow.v4")


def test_capability_table_allows_v3_execution() -> None:
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].parse is True
    assert not can_execute("confflow.workflow.v4")
    assert not can_execute("confflow.workflow.v99")
