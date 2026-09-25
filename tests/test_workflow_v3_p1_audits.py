"""P1 audits — V3 invalid-dataflow side effects (P1-A) and finalization (P1-B).

Filesystem evidence, not just code reading:

P1-A verdict (verified with throwaway scripts before the fix): an
invalid-effective-dataflow V3 config reached ``run_v3_workflow``'s preflight
only AFTER ``build_workflow_service`` had created durable files —
``state/v1/repository.sqlite3`` (eagerly via the repository constructor's
migration), ``state/v1/runs/<id>/{staging,work}`` — and the through-service
attempt additionally left a FAILED aggregate plus ``work/.confflow-work.lock``
(the work lease mkdirs its parent). The workflow ``work_dir`` itself never
gained ``.workflow_state.json``/``steps``/``external_inputs``, and no handler
was invoked. The minimal fix (``_preflight_v3_effective_dataflow`` in
``workflow_adapter.build_workflow_service``, inherited by
``run_workflow_through_service``) rejects the same error before any of that.

P1-B policy (verified against real behavior, not redesigned): the runtime
resume path deterministically RE-FINALIZES when valid completed artifacts
exist (missing/corrupt stats or manifest are republished with zero handler
calls and an unchanged binding) and FAILS CLOSED otherwise (missing terminal
artifact, missing state). The service attach path (``_load_completed_stats``)
fails closed on missing stats/outputs. No test asserts
state-says-completed + missing-manifest == success: every success assertion
follows a re-finalize that restored the missing file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.application.execution.errors import ErrorCode, ExecutionServiceError
from confflow.application.execution.workflow_adapter import (
    WorkflowRunSpec,
    _load_artifacts,
    _load_completed_stats,
    _load_stats,
    build_workflow_service,
    run_workflow_through_service,
)
from confflow.config.canonical import CAPABILITIES, VersionCapability
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION_V3
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.v3_runtime import run_v3_workflow

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="durable service contract requires POSIX"
)

V3 = "confflow.workflow.v3"


@pytest.fixture(autouse=True)
def _v3_execution_enabled(monkeypatch):
    monkeypatch.setitem(
        CAPABILITIES, WORKFLOW_SCHEMA_VERSION_V3, VersionCapability(parse=True, execute=True)
    )


# ---------------------------------------------------------------------------
# Harness (mirrors tests/test_workflow_v3_runtime.py helpers)
# ---------------------------------------------------------------------------
def _write_xyz(path: Path, note: str = "seed") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n{note}\nH 0 0 0\n", encoding="utf-8")
    return path


def _write_config(path: Path, steps: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"schema": V3, "steps": steps}), encoding="utf-8")
    return path


def _invalid_df_steps() -> list[dict[str, Any]]:
    """Disabled calc fan-in: s004 sees 2 effective inputs after bypass (R6)."""
    return [
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


def _valid_steps() -> list[dict[str, Any]]:
    return [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
    ]


class _FakeHandlers:
    """Fake calc/confgen handlers with call counting (runtime seam pattern)."""

    def __init__(self, monkeypatch) -> None:
        self.calc_calls: list[dict[str, Any]] = []
        self.confgen_calls: list[dict[str, Any]] = []
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_calc_step", self._calc)
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_confgen_step", self._confgen)

    def _calc(self, **kwargs: Any):
        self.calc_calls.append(kwargs)
        step_dir = Path(kwargs["step_dir"])
        output = step_dir / "result.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake calc\nH 0 0 0\n", encoding="utf-8")

        class _Result:
            output_path = str(output)

        return _Result()

    def _confgen(self, **kwargs: Any):
        self.confgen_calls.append(kwargs)
        output = Path(kwargs["step_dir"]) / "search.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("2\nfake confgen\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

        class _Result:
            output_path = str(output)

        return _Result()


_WORKFLOW_FILES = (
    ".workflow_state.json",
    "steps",
    "external_inputs",
    "output_manifest.json",
    "workflow_stats.json",
)


def _assert_no_workflow_files(work: Path) -> None:
    for name in _WORKFLOW_FILES:
        assert not (work / name).exists(), f"unexpected workflow file: {work / name}"


def _completed_run(tmp_path: Path, handlers: _FakeHandlers) -> tuple[str, str, str]:
    """Build a real completed V3 work_dir; returns (input, config, work)."""
    tag = tmp_path.name
    base = tmp_path / f"run-{tag}"
    input_xyz = _write_xyz(base / "input.xyz")
    config = _write_config(base / "wf.yaml", _valid_steps())
    work = base / "work"
    run_v3_workflow(input_xyz=[str(input_xyz)], config_file=str(config), work_dir=str(work))
    state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
    assert state["final_status"] == "completed"
    handlers.calc_calls.clear()
    handlers.confgen_calls.clear()
    return str(input_xyz), str(config), str(work)


# ---------------------------------------------------------------------------
# P1-A — invalid effective dataflow through the public path
# ---------------------------------------------------------------------------
class TestP1AInvalidDataflowSideEffects:
    def test_p1a1_public_invalid_df_no_state_file(self, tmp_path: Path, monkeypatch) -> None:
        """The public path rejects invalid DF with no workflow state file."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _invalid_df_steps())
        with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="p1a1-run",
            )
        _assert_no_workflow_files(tmp_path / "work")
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    def test_p1a2_no_sqlite_or_stateroot_durable_files(self, tmp_path: Path, monkeypatch) -> None:
        """Neither the builder nor the through-service path leaves durable files.

        Layer verdict: ``build_workflow_service`` is the lowest shared boundary
        (``_ensure_state_root`` mkdir/chmod, ``ensure_run_paths``, and the
        eager ``SQLiteExecutionRepository`` migration all live below it), so the
        DF preflight there covers every caller. The workflow ``work_dir`` is
        asserted file-free either way.
        """
        _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _invalid_df_steps())

        # Direct builder call: refused before the state root exists.
        spec = WorkflowRunSpec(
            run_id="p1a2-direct",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(tmp_path / "work-direct"),
        )

        class _NeverRunner:
            def __call__(self, **kwargs: Any) -> dict[str, Any]:
                raise AssertionError("runner must never run for invalid dataflow")

        with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
            build_workflow_service(
                spec, state_root=tmp_path / "state", workflow_runner=_NeverRunner()
            )
        assert not (tmp_path / "state").exists()
        assert not (tmp_path / "work-direct").exists()

        # Full through-service call: no SQLite, no run layout, no work files.
        with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="p1a2-run",
            )
        assert not (tmp_path / "state").exists()
        assert not list(tmp_path.rglob("repository.sqlite3"))
        assert not list(tmp_path.rglob(".workflow_state.json"))
        _assert_no_workflow_files(tmp_path / "work")

    def test_p1a3_no_handler_invoked(self, tmp_path: Path, monkeypatch) -> None:
        """Step handlers (and therefore subprocesses) never run for invalid DF."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _invalid_df_steps())
        with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="p1a3-run",
            )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    def test_p1a4_valid_workflow_still_executes(self, tmp_path: Path, monkeypatch) -> None:
        """The preflight admits valid V3: the public path still executes."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _valid_steps())
        result = run_workflow_through_service(
            input_xyz=[str(input_xyz)],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
            state_root=tmp_path / "state",
            run_id="p1a4-run",
        )
        assert result is not None
        assert [call["step_name"] for call in handlers.calc_calls] == ["s002"]
        assert handlers.confgen_calls
        manifest = json.loads((tmp_path / "work" / "output_manifest.json").read_text())
        assert manifest["content_schema"] == "confflow.output_manifest.v2"
        stats = json.loads((tmp_path / "work" / "workflow_stats.json").read_text())
        assert stats["content_schema"] == "confflow.workflow_stats.v2"

    def test_p1a5_v2_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """The V2 config path is untouched by the V3 DF preflight."""
        from confflow.contract import OUTPUT_MANIFEST_SCHEMA, WORKFLOW_STATE_SCHEMA

        def fake_confgen(step_dir, *args: Any, **kwargs: Any):
            output = Path(step_dir) / "search.xyz"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")

            class _Result:
                output_path = str(output)
                reused_existing = False
                copied_multi_frame = False

            return _Result()

        monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = tmp_path / "v2.yaml"
        config.write_text(
            yaml.safe_dump(
                {"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}
            ),
            encoding="utf-8",
        )
        result = run_workflow_through_service(
            input_xyz=[str(input_xyz)],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
            state_root=tmp_path / "state",
            run_id="p1a5-run",
        )
        assert result is not None
        state = json.loads((tmp_path / "work" / ".workflow_state.json").read_text())
        assert state["content_schema"] == WORKFLOW_STATE_SCHEMA
        manifest = json.loads((tmp_path / "work" / "output_manifest.json").read_text())
        assert manifest["content_schema"] == OUTPUT_MANIFEST_SCHEMA


# ---------------------------------------------------------------------------
# P1-B — finalization policy around completed state
# ---------------------------------------------------------------------------
class TestP1BFinalizationPolicy:
    def test_p1b1_completed_state_manifest_missing_refinalizes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Crash window stats-written/manifest-missing: resume republishes."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        (work / "output_manifest.json").unlink()
        state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["final_status"] == "completed"  # the crash window, not success
        assert not (work / "output_manifest.json").exists()

        run_v3_workflow(input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True)
        # Recovery, not silent success: the manifest is deterministically
        # republished with zero chemistry, and the state stays completed.
        manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert manifest["content_schema"] == "confflow.output_manifest.v2"
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []
        state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["final_status"] == "completed"

    def test_p1b2_stats_missing_refinalizes(self, tmp_path: Path, monkeypatch) -> None:
        """Missing stats are republished by resume with zero handler calls."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        (work / "workflow_stats.json").unlink()

        run_v3_workflow(input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True)
        stats = json.loads((work / "workflow_stats.json").read_text(encoding="utf-8"))
        assert stats["content_schema"] == "confflow.workflow_stats.v2"
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    def test_p1b3_malformed_manifest(self, tmp_path: Path, monkeypatch) -> None:
        """A corrupt manifest file is republished; the strict loader fails closed."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        (work / "output_manifest.json").write_text("{not valid json", encoding="utf-8")
        # Unparseable files project to empty (attach finds nothing to verify)...
        assert _load_artifacts(work_dir) == ()
        # ...while schema/path violations raise instead of dropping outputs.
        (work / "output_manifest.json").write_text(
            json.dumps(
                {
                    "content_schema": "confflow.output_manifest.v2",
                    "terminals": [{"id": "s002", "label": None, "artifacts": ["/etc/passwd"]}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_artifacts(work_dir)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        # Resume republishes a valid manifest with zero chemistry.
        (work / "output_manifest.json").write_text("{not valid json", encoding="utf-8")
        run_v3_workflow(input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True)
        manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert manifest["content_schema"] == "confflow.output_manifest.v2"
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    def test_p1b4_terminal_artifact_missing_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        """A completed record without its artifact is refused, never recomputed."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        for artifact in (work / "steps" / "s002").glob("*.xyz"):
            artifact.unlink()
        with pytest.raises(ConfFlowError, match="output artifact is missing"):
            run_v3_workflow(
                input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True
            )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []
        # The completed state is left intact for the operator to inspect.
        state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
        assert state["final_status"] == "completed"

    def test_p1b5_stats_before_manifest_crash_window(self, tmp_path: Path, monkeypatch) -> None:
        """Finalize order (state -> stats -> manifest, atomic writes) analysis.

        The only crash window that order admits is {stats present, manifest
        absent}: manifest-without-stats is unproducible by a crash, and a fresh
        completed run always carries both files. Both absences recover via
        resume with zero handler calls.
        """
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        assert (work / "workflow_stats.json").exists()
        assert (work / "output_manifest.json").exists()

        # Crash after stats, before manifest.
        (work / "output_manifest.json").unlink()
        run_v3_workflow(input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True)
        assert (work / "output_manifest.json").exists()
        assert (work / "workflow_stats.json").exists()
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

        # Crash before any sidecar write.
        (work / "output_manifest.json").unlink()
        (work / "workflow_stats.json").unlink()
        run_v3_workflow(input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True)
        assert (work / "output_manifest.json").exists()
        assert (work / "workflow_stats.json").exists()
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    def test_p1b6_no_unnecessary_chemistry_rerun(self, tmp_path: Path, monkeypatch) -> None:
        """Resume/re-finalize of a completed run never re-invokes handlers."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        assert handlers.calc_calls == [] and handlers.confgen_calls == []
        run_v3_workflow(input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True)
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    def test_p1b7_binding_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """Resume preserves the immutable binding (fingerprints identical)."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        before = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))["binding"]
        result = run_v3_workflow(
            input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True
        )
        after = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))["binding"]
        assert after == before
        assert result["execution_fingerprint"] == after["execution_fingerprint"]
        assert result["definition_fingerprint"] == after["definition_fingerprint"]

    def test_p1b8_completed_attach_requires_stats(self, tmp_path: Path, monkeypatch) -> None:
        """The service attach path fails closed when stats are missing."""

        class _StubService:
            def artifacts(self, run_id: str):
                raise AssertionError("unreachable: stats check fires first")

        handlers = _FakeHandlers(monkeypatch)
        _input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        assert _load_stats(work_dir) is not None
        (work / "workflow_stats.json").unlink()
        spec = WorkflowRunSpec(
            run_id="p1b8-run",
            input_xyz=(str(work.parent / "input.xyz"),),
            config_file=config,
            work_dir=work_dir,
        )
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_completed_stats(_StubService(), "p1b8-run", work_dir, spec)  # type: ignore[arg-type]
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    def test_p1b9_missing_state_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        """Resume without any state file is refused, never rebuilt from outputs."""
        handlers = _FakeHandlers(monkeypatch)
        input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        (Path(work_dir) / ".workflow_state.json").unlink()
        with pytest.raises(ConfFlowError, match="no workflow state found"):
            run_v3_workflow(
                input_xyz=[input_xyz], config_file=config, work_dir=work_dir, resume=True
            )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []
