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
    _is_v3_config,
    _load_artifacts,
    _load_artifacts_v3_required,
    _load_completed_stats,
    _load_stats,
    _load_stats_v3_required,
    build_workflow_service,
    run_workflow_through_service,
)
from confflow.config.canonical import CAPABILITIES, VersionCapability
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION_V3
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.v3_runtime import run_v3_workflow

pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="durable service contract requires POSIX"),
    # Hermetic CI: fake orca/g16 entrypoints on PATH (real files, real identity).
    # Fail-closed tests use explicit spellings and never consult PATH.
    pytest.mark.usefixtures("fake_qc_executables_on_path"),
]

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


# ---------------------------------------------------------------------------
# PF1-PF8 — preflight fail-closed ordering (Agent D)
#
# Once the config is identified as V3 (schema == confflow.workflow.v3),
# build_workflow_plan failures (schema/params/graph/checkpoint) and
# effective-DF findings propagate BEFORE StateRoot/SQLite/run-paths
# creation. Only unidentifiable-as-V3 specs fall through to the legacy
# path. Each invalid case proves zero durable effects: no SQLite, no
# StateRoot files, no work dirs, no handler, no subprocess.
# ---------------------------------------------------------------------------
def _pf_schema_steps() -> list[dict[str, Any]]:
    """Structural schema violation: unsupported step type."""
    return [{"id": "s001", "type": "bogus", "inputs": [], "params": {}}]


def _pf_params_steps() -> list[dict[str, Any]]:
    """Semantic params violation: calc without its required keyword."""
    return [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {}},
    ]


def _pf_graph_steps() -> list[dict[str, Any]]:
    """Graph violation: unknown predecessor."""
    return [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["nope"], "params": {"keyword": "HF"}},
    ]


def _pf_checkpoint_steps() -> list[dict[str, Any]]:
    """Checkpoint violation: calc reuses a confgen checkpoint (build-level)."""
    return [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {
            "id": "s002",
            "type": "calc",
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
            "checkpoint": {"from_step": "s001"},
        },
    ]


def _pf_fragment_steps() -> list[dict[str, Any]]:
    """Unrunnable fragment.

    Parseable as a fragment (id optional) but not runnable as a document
    (id required).
    """
    return [{"type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}]


def _assert_zero_preflight_effects(tmp_path: Path, state_root: Path, work_dirs: list[Path]) -> None:
    """Zero durable effects: no SQLite, no StateRoot files, no work files."""
    assert not (state_root / "v1" / "repository.sqlite3").exists()
    assert not list(state_root.rglob("repository.sqlite3"))
    assert not list(tmp_path.rglob("repository.sqlite3"))
    assert not list(tmp_path.rglob(".workflow_state.json"))
    for work in work_dirs:
        _assert_no_workflow_files(work)
        assert not (work / ".confflow-work.lock").exists()


class _NeverRunner:
    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("runner must never run for invalid preflight")


class TestPFPreflightFailClosed:
    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf1_invalid_df_zero_effects(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _invalid_df_steps())
        assert _is_v3_config(str(config))
        spec = WorkflowRunSpec(
            run_id="pf1-direct",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(tmp_path / "work-direct"),
        )
        with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
            build_workflow_service(
                spec, state_root=tmp_path / "state", workflow_runner=_NeverRunner()
            )
        with pytest.raises(ConfFlowError, match="effective dataflow validation failed"):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="pf1-run",
            )
        assert not (tmp_path / "state").exists()
        _assert_zero_preflight_effects(
            tmp_path, tmp_path / "state", [tmp_path / "work", tmp_path / "work-direct"]
        )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf2_invalid_schema_zero_effects(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _pf_schema_steps())
        assert _is_v3_config(str(config))
        spec = WorkflowRunSpec(
            run_id="pf2-direct",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(tmp_path / "work-direct"),
        )
        with pytest.raises(ConfFlowError):
            build_workflow_service(
                spec, state_root=tmp_path / "state", workflow_runner=_NeverRunner()
            )
        with pytest.raises(ConfFlowError):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="pf2-run",
            )
        assert not (tmp_path / "state").exists()
        _assert_zero_preflight_effects(
            tmp_path, tmp_path / "state", [tmp_path / "work", tmp_path / "work-direct"]
        )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf3_invalid_params_zero_effects(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _pf_params_steps())
        assert _is_v3_config(str(config))
        spec = WorkflowRunSpec(
            run_id="pf3-direct",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(tmp_path / "work-direct"),
        )
        with pytest.raises(ConfFlowError):
            build_workflow_service(
                spec, state_root=tmp_path / "state", workflow_runner=_NeverRunner()
            )
        with pytest.raises(ConfFlowError):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="pf3-run",
            )
        assert not (tmp_path / "state").exists()
        _assert_zero_preflight_effects(
            tmp_path, tmp_path / "state", [tmp_path / "work", tmp_path / "work-direct"]
        )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf4_invalid_graph_zero_effects(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _pf_graph_steps())
        assert _is_v3_config(str(config))
        spec = WorkflowRunSpec(
            run_id="pf4-direct",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(tmp_path / "work-direct"),
        )
        with pytest.raises(ConfFlowError):
            build_workflow_service(
                spec, state_root=tmp_path / "state", workflow_runner=_NeverRunner()
            )
        with pytest.raises(ConfFlowError):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="pf4-run",
            )
        assert not (tmp_path / "state").exists()
        _assert_zero_preflight_effects(
            tmp_path, tmp_path / "state", [tmp_path / "work", tmp_path / "work-direct"]
        )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf5_invalid_checkpoint_zero_effects(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _pf_checkpoint_steps())
        assert _is_v3_config(str(config))
        spec = WorkflowRunSpec(
            run_id="pf5-direct",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(tmp_path / "work-direct"),
        )
        with pytest.raises(ConfFlowError):
            build_workflow_service(
                spec, state_root=tmp_path / "state", workflow_runner=_NeverRunner()
            )
        with pytest.raises(ConfFlowError):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="pf5-run",
            )
        assert not (tmp_path / "state").exists()
        _assert_zero_preflight_effects(
            tmp_path, tmp_path / "state", [tmp_path / "work", tmp_path / "work-direct"]
        )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf6_unrunnable_fragment_zero_effects(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _pf_fragment_steps())
        assert _is_v3_config(str(config))
        spec = WorkflowRunSpec(
            run_id="pf6-direct",
            input_xyz=(str(input_xyz),),
            config_file=str(config),
            work_dir=str(tmp_path / "work-direct"),
        )
        with pytest.raises(ConfFlowError):
            build_workflow_service(
                spec, state_root=tmp_path / "state", workflow_runner=_NeverRunner()
            )
        with pytest.raises(ConfFlowError):
            run_workflow_through_service(
                input_xyz=[str(input_xyz)],
                config_file=str(config),
                work_dir=str(tmp_path / "work"),
                state_root=tmp_path / "state",
                run_id="pf6-run",
            )
        assert not (tmp_path / "state").exists()
        _assert_zero_preflight_effects(
            tmp_path, tmp_path / "state", [tmp_path / "work", tmp_path / "work-direct"]
        )
        assert handlers.calc_calls == []
        assert handlers.confgen_calls == []

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf7_valid_executes(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = _write_config(tmp_path / "wf.yaml", _valid_steps())
        result = run_workflow_through_service(
            input_xyz=[str(input_xyz)],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
            state_root=tmp_path / "state",
            run_id="pf7-run",
        )
        assert result is not None
        assert [call["step_name"] for call in handlers.calc_calls] == ["s002"]
        assert handlers.confgen_calls
        manifest = json.loads((tmp_path / "work" / "output_manifest.json").read_text())
        assert manifest["content_schema"] == "confflow.output_manifest.v2"
        stats = json.loads((tmp_path / "work" / "workflow_stats.json").read_text())
        assert stats["content_schema"] == "confflow.workflow_stats.v2"

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_pf8_v2_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        from confflow.contract import OUTPUT_MANIFEST_SCHEMA, WORKFLOW_STATE_SCHEMA

        def fake_confgen(step_dir: Any, *args: Any, **kwargs: Any) -> Any:
            output = Path(step_dir) / "search.xyz"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")

            class _Result:
                output_path = str(output)
                reused_existing = False
                copied_multi_frame = False

            return _Result()

        monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
        assert not _is_v3_config(str(tmp_path / "v2.yaml")) or True
        input_xyz = _write_xyz(tmp_path / "input.xyz")
        config = tmp_path / "v2.yaml"
        config.write_text(
            yaml.safe_dump(
                {"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}
            ),
            encoding="utf-8",
        )
        assert not _is_v3_config(str(config))
        result = run_workflow_through_service(
            input_xyz=[str(input_xyz)],
            config_file=str(config),
            work_dir=str(tmp_path / "work"),
            state_root=tmp_path / "state",
            run_id="pf8-run",
        )
        assert result is not None
        state = json.loads((tmp_path / "work" / ".workflow_state.json").read_text())
        assert state["content_schema"] == WORKFLOW_STATE_SCHEMA
        manifest = json.loads((tmp_path / "work" / "output_manifest.json").read_text())
        assert manifest["content_schema"] == OUTPUT_MANIFEST_SCHEMA


# ---------------------------------------------------------------------------
# S1-S3,S6-S7,S10 - V3 artifact integrity (Agent D)
# ---------------------------------------------------------------------------
def _v3_service_completed(
    tmp_path: Path, monkeypatch: Any, *, run_id: str = "s-run"
) -> tuple[Any, WorkflowRunSpec, _FakeHandlers, Path, Path, Path, Path]:
    """Run one valid V3 attempt through the service; returns service + parts."""
    from confflow.application.execution.models import RunState
    from confflow.application.execution.workflow_adapter import (
        _prepare_request,
        executor_identity,
    )
    from confflow.workflow.engine import run_workflow as _runner

    handlers = _FakeHandlers(monkeypatch)
    input_xyz = _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", _valid_steps())
    work = tmp_path / "work"
    state_root = tmp_path / "state"
    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=(str(input_xyz),),
        config_file=str(config),
        work_dir=str(work),
    )
    service, executor = build_workflow_service(spec, state_root=state_root, workflow_runner=_runner)
    service.prepare(_prepare_request(spec, executor_identity(service)))
    service.execute(run_id)
    executor.wait()
    assert service.status(run_id).state is RunState.COMPLETED
    return service, spec, handlers, input_xyz, config, work, state_root


def _v1_service_completed(
    tmp_path: Path, monkeypatch: Any, *, run_id: str = "v1-s-run"
) -> tuple[Any, WorkflowRunSpec, Path, Path, Path, Path]:
    """Run one valid V1/V2 attempt through the service (legacy tolerant)."""

    def fake_confgen(step_dir: Any, *args: Any, **kwargs: Any) -> Any:
        output = Path(step_dir) / "search.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            reused_existing = False
            copied_multi_frame = False

        return _Result()

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
    from confflow.application.execution.models import RunState
    from confflow.application.execution.workflow_adapter import (
        _prepare_request,
        executor_identity,
    )
    from confflow.workflow.engine import run_workflow as _runner

    input_xyz = _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "v2.yaml"
    config.write_text(
        yaml.safe_dump(
            {"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}
        ),
        encoding="utf-8",
    )
    work = tmp_path / "work"
    state_root = tmp_path / "state"
    spec = WorkflowRunSpec(
        run_id=run_id,
        input_xyz=(str(input_xyz),),
        config_file=str(config),
        work_dir=str(work),
    )
    service, executor = build_workflow_service(spec, state_root=state_root, workflow_runner=_runner)
    service.prepare(_prepare_request(spec, executor_identity(service)))
    service.execute(run_id)
    executor.wait()
    assert service.status(run_id).state is RunState.COMPLETED
    return service, spec, input_xyz, config, work, state_root


class TestV3ArtifactIntegrity:
    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s1_missing_manifest_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        from confflow.application.execution.models import RunState

        handlers = _FakeHandlers(monkeypatch)
        _input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        (work / "output_manifest.json").unlink()
        assert _load_artifacts(work_dir) == ()
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_artifacts_v3_required(work_dir)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        work2 = tmp_path / "work2"

        def _empty_runner(**kwargs: Any) -> dict[str, Any]:
            Path(kwargs["work_dir"]).mkdir(parents=True, exist_ok=True)
            return {"ok": True}

        input_xyz = _write_xyz(tmp_path / "input2.xyz")
        config2 = _write_config(tmp_path / "wf2.yaml", _valid_steps())
        spec2 = WorkflowRunSpec(
            run_id="s1-run",
            input_xyz=(str(input_xyz),),
            config_file=str(config2),
            work_dir=str(work2),
        )
        service2, executor2 = build_workflow_service(
            spec2, state_root=tmp_path / "state2", workflow_runner=_empty_runner
        )
        from confflow.application.execution.workflow_adapter import (
            _prepare_request,
            executor_identity,
        )

        service2.prepare(_prepare_request(spec2, executor_identity(service2)))
        service2.execute("s1-run")
        with pytest.raises(ExecutionServiceError) as excinfo2:
            executor2.wait()
        assert excinfo2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        assert service2.status("s1-run").state is RunState.FAILED
        assert service2.artifacts("s1-run").artifacts == ()

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s2_corrupt_manifest_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        _input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        (work / "output_manifest.json").write_text("{not valid json", encoding="utf-8")
        assert _load_artifacts(work_dir) == ()
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_artifacts_v3_required(work_dir)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s3_wrong_schema_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        from confflow.contract import OUTPUT_MANIFEST_SCHEMA

        handlers = _FakeHandlers(monkeypatch)
        _input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        valid = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
        assert valid["content_schema"] == "confflow.output_manifest.v2"
        v1_payload = {
            "content_schema": OUTPUT_MANIFEST_SCHEMA,
            "terminals": {
                t["id"]: [a.split("/")[-1] for a in t["artifacts"]] for t in valid["terminals"]
            },
        }
        (work / "output_manifest.json").write_text(json.dumps(v1_payload), encoding="utf-8")
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_artifacts_v3_required(work_dir)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        (work / "output_manifest.json").write_text(
            json.dumps({"content_schema": "confflow.output_manifest.v9", "terminals": []}),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as excinfo2:
            _load_artifacts_v3_required(work_dir)
        assert excinfo2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        (work / "output_manifest.json").write_text(json.dumps({"terminals": []}), encoding="utf-8")
        with pytest.raises(ExecutionServiceError) as excinfo3:
            _load_artifacts_v3_required(work_dir)
        assert excinfo3.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s6_unsafe_or_missing_artifact_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _FakeHandlers(monkeypatch)
        _input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
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
            _load_artifacts_v3_required(work_dir)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        (work / "output_manifest.json").write_text(
            json.dumps(
                {
                    "content_schema": "confflow.output_manifest.v2",
                    "terminals": [{"id": "s002", "label": None, "artifacts": ["../escape.xyz"]}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as excinfo2:
            _load_artifacts_v3_required(work_dir)
        assert excinfo2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        (work / "output_manifest.json").write_text(
            json.dumps(
                {
                    "content_schema": "confflow.output_manifest.v2",
                    "terminals": [{"id": "s002", "label": None, "artifacts": ["gone.xyz"]}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as excinfo3:
            _load_artifacts_v3_required(work_dir)
        assert excinfo3.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s7_stats_strict_v2_only(self, tmp_path: Path, monkeypatch) -> None:
        from confflow.contract import WORKFLOW_STATS_SCHEMA

        handlers = _FakeHandlers(monkeypatch)
        _input_xyz, config, work_dir = _completed_run(tmp_path, handlers)
        work = Path(work_dir)
        stats = _load_stats_v3_required(work_dir)
        assert stats["content_schema"] == "confflow.workflow_stats.v2"
        (work / "workflow_stats.json").unlink()
        assert _load_stats(work_dir) is None
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_stats_v3_required(work_dir)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        (work / "workflow_stats.json").write_text(
            json.dumps({"final_output": "x"}), encoding="utf-8"
        )
        assert _load_stats(work_dir) is not None
        with pytest.raises(ExecutionServiceError) as excinfo2:
            _load_stats_v3_required(work_dir)
        assert excinfo2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        (work / "workflow_stats.json").write_text(
            json.dumps({"content_schema": WORKFLOW_STATS_SCHEMA, "final_output": "x"}),
            encoding="utf-8",
        )
        assert _load_stats(work_dir) is not None
        with pytest.raises(ExecutionServiceError) as excinfo3:
            _load_stats_v3_required(work_dir)
        assert excinfo3.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s10_valid_v3_passes_strict(self, tmp_path: Path, monkeypatch) -> None:
        service, spec, handlers, _input_xyz, _config, work, _state_root = _v3_service_completed(
            tmp_path, monkeypatch, run_id="s10-run"
        )
        loaded = _load_artifacts_v3_required(str(work))
        assert loaded
        assert all(a.content_schema == "confflow.output_manifest.v2" for a in loaded)
        stats = _load_stats_v3_required(str(work))
        assert stats["content_schema"] == "confflow.workflow_stats.v2"
        assert service.artifacts("s10-run").artifacts


# ---------------------------------------------------------------------------
# S4,S5,S8,S9 - V3 completed attach (Agent D)
# ---------------------------------------------------------------------------
class TestV3CompletedAttach:
    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s4_v3_attach_missing_state_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        service, spec, _handlers, _input_xyz, _config, work, _state_root = _v3_service_completed(
            tmp_path, monkeypatch, run_id="s4-run"
        )
        (work / ".workflow_state.json").unlink()
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_completed_stats(service, "s4-run", str(work), spec)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        v1_base = tmp_path / "v1"
        v1_base.mkdir()
        service1, spec1, _in1, _cfg1, work1, _root1 = _v1_service_completed(
            v1_base, monkeypatch, run_id="s4-v1-run"
        )
        (work1 / ".workflow_state.json").unlink()
        assert _load_completed_stats(service1, "s4-v1-run", str(work1), spec1) is not None

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s5_v3_attach_missing_or_v1_stats_fails_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from confflow.contract import WORKFLOW_STATS_SCHEMA

        service, spec, _handlers, _input_xyz, _config, work, _state_root = _v3_service_completed(
            tmp_path, monkeypatch, run_id="s5-run"
        )
        (work / "workflow_stats.json").unlink()
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_completed_stats(service, "s5-run", str(work), spec)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        second = tmp_path / "second"
        second.mkdir()
        service2, spec2, _h2, _in2, _cfg2, work2, _root2 = _v3_service_completed(
            second, monkeypatch, run_id="s5b-run"
        )
        work2 = Path(work2)
        (work2 / "workflow_stats.json").write_text(
            json.dumps({"content_schema": WORKFLOW_STATS_SCHEMA, "final_output": "x"}),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as excinfo2:
            _load_completed_stats(service2, "s5b-run", str(work2), spec2)
        assert excinfo2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s8_v3_attach_manifest_and_hash_fails_closed(self, tmp_path: Path, monkeypatch) -> None:
        service, spec, _handlers, _input_xyz, _config, work, _state_root = _v3_service_completed(
            tmp_path, monkeypatch, run_id="s8-run"
        )
        work = Path(work)
        manifest_bytes = (work / "output_manifest.json").read_bytes()
        (work / "output_manifest.json").unlink()
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_completed_stats(service, "s8-run", str(work), spec)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        (work / "output_manifest.json").write_bytes(manifest_bytes)
        manifest = service.artifacts("s8-run")
        assert manifest.artifacts
        victim = work / manifest.artifacts[0].path
        victim.write_bytes(victim.read_bytes() + b"\ncorrupt\n")
        with pytest.raises(ExecutionServiceError) as excinfo2:
            _load_completed_stats(service, "s8-run", str(work), spec)
        assert excinfo2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    @pytest.mark.usefixtures("fake_qc_executables_on_path")
    def test_s9_identity_marker_policy(self, tmp_path: Path, monkeypatch) -> None:
        service, spec, _handlers, _input_xyz, _config, work, _state_root = _v3_service_completed(
            tmp_path, monkeypatch, run_id="s9-run"
        )
        work = Path(work)
        identity_path = work / ".confflow_execution_identity.json"
        assert identity_path.exists()
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        assert set(identity) == {"run_id", "request_digest"}
        saved = identity_path.read_bytes()
        identity_path.unlink()
        with pytest.raises(ExecutionServiceError) as excinfo:
            _load_completed_stats(service, "s9-run", str(work), spec)
        assert excinfo.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        identity_path.write_bytes(saved)
        tampered = dict(identity)
        tampered["request_digest"] = "0" * 64
        identity_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ExecutionServiceError) as excinfo2:
            _load_completed_stats(service, "s9-run", str(work), spec)
        assert excinfo2.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED
        identity_path.write_bytes(saved)
        assert _load_completed_stats(service, "s9-run", str(work), spec) is not None

        v1_base = tmp_path / "v1s9"
        v1_base.mkdir()
        service1, spec1, _in1, _cfg1, work1, _root1 = _v1_service_completed(
            v1_base, monkeypatch, run_id="s9-v1-run"
        )
        (Path(work1) / ".confflow_execution_identity.json").unlink(missing_ok=True)
        assert _load_completed_stats(service1, "s9-v1-run", str(work1), spec1) is not None
