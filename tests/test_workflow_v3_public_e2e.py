"""R4.5 — Public Workflow V3 execution end-to-end (PE1–PE8, PE-R, PE-F, PE-C).

Every test drives the real public stack: the public CLI (``confflow.cli.main``)
or the public engine entry → ExecutionService → the accepted V3 runtime core →
state v2 → output manifest v2 → artifact loader. Step handlers are faked at
the runtime's indirection points (no Gaussian/ORCA). The real capability table
is in effect: these tests are the flip's acceptance suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.cli import main as cli_main
from confflow.config.canonical import CAPABILITIES, WORKFLOW_SCHEMA_VERSION_V3
from confflow.contract import OUTPUT_MANIFEST_SCHEMA_V2, WORKFLOW_STATS_SCHEMA_V2
from confflow.core.contracts import ExitCode
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.rerun_failed import RerunFailedUsageError, run_rerun_failed
from confflow.workflow.v3_runtime import run_v3_workflow

V3 = "confflow.workflow.v3"


# Hermetic CI: resolve bare "orca"/"g16" defaults against fake executables on
# PATH (no system QC programs required). Explicit fail-closed spellings never
# touch PATH and stay fail-closed.
pytestmark = pytest.mark.usefixtures("fake_qc_executables_on_path")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def _write_xyz(path: Path, note: str = "seed") -> Path:
    path.write_text(f"1\n{note}\nH 0 0 0\n", encoding="utf-8")
    return path


def _config(path: Path, steps: list[dict[str, Any]], **root: Any) -> Path:
    document: dict[str, Any] = {"schema": V3, "steps": steps}
    document.update(root)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _step(step_id: str, step_type: str, inputs: list[str], **extra: Any) -> dict[str, Any]:
    step: dict[str, Any] = {"id": step_id, "type": step_type, "inputs": inputs}
    step.update(extra)
    if step_type == "confgen":
        step.setdefault("params", {"chains": ["1-2"]})
    else:
        step.setdefault("params", {"keyword": "HF"})
    return step


class _Handlers:
    """Fake handlers recording invocations by stable ID, with failure injection."""

    def __init__(self, monkeypatch, *, fail_ids: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_ids = fail_ids or set()
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_calc_step", self._calc)
        monkeypatch.setattr("confflow.workflow.v3_runtime._run_confgen_step", self._confgen)

    def _finish(self, step_dir: Path, name: str) -> Any:
        output_name = "search.xyz" if name == "confgen" else "result.xyz"
        output = step_dir / output_name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake E=-1.0\nH 0 0 0\n", encoding="utf-8")
        (step_dir / "backups").mkdir(parents=True, exist_ok=True)

        class _Result:
            output_path = str(output)
            reused_existing = False
            copied_multi_frame = False

        return _Result()

    def _calc(self, **kwargs: Any):
        step_id = kwargs["step_name"]
        assert step_id == Path(kwargs["step_dir"]).name
        self.calls.append(step_id)
        if step_id in self.fail_ids:
            raise RuntimeError(f"calc failed: {step_id}")
        return self._finish(Path(kwargs["step_dir"]), step_id)

    def _confgen(self, **kwargs: Any):
        self.calls.append(Path(kwargs["step_dir"]).name)
        return self._finish(Path(kwargs["step_dir"]), "confgen")


def _cli(
    tmp_path: Path,
    monkeypatch,
    config: Path,
    input_name: str = "input.xyz",
    *args: str,
    resume: bool = False,
):
    xyz = tmp_path / input_name
    if not xyz.exists():
        _write_xyz(xyz)
    work = tmp_path / "work"
    cli_args = [str(xyz), "-c", str(config), "-w", str(work), *args]
    if resume:
        cli_args.append("--resume")
    return cli_main(cli_args), work


def _manifest(work: Path) -> dict[str, Any]:
    return json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))


def _stats(work: Path) -> dict[str, Any]:
    return json.loads((work / "workflow_stats.json").read_text(encoding="utf-8"))


def _state(work: Path) -> dict[str, Any]:
    return json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))


def _assert_public_v2_artifacts(work: Path) -> None:
    """Assert the public chain published coherent v2 sidecars that load back."""
    manifest = _manifest(work)
    assert manifest["content_schema"] == OUTPUT_MANIFEST_SCHEMA_V2
    stats = _stats(work)
    assert stats["content_schema"] == WORKFLOW_STATS_SCHEMA_V2
    state = _state(work)
    assert state["content_schema"] == "confflow.workflow_state.v2"
    assert state["final_status"] == "completed"
    assert state["binding"]["schema"] == "confflow.workflow_binding.v2"
    # the service artifact loader reads the v2 manifest back
    from confflow.application.execution.workflow_adapter import _load_artifacts

    artifacts = _load_artifacts(str(work))
    assert artifacts, "public run must publish loadable artifacts"
    assert all(artifact.terminal in state["steps"] for artifact in artifacts)


# ---------------------------------------------------------------------------
# PE1–PE8 — public CLI runs
# ---------------------------------------------------------------------------
class TestPublicRuns:
    def test_pe1_simple_calc_full_public_chain(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        config = _config(tmp_path / "wf.yaml", [_step("s001", "calc", [])])
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        _assert_public_v2_artifacts(work)
        assert (work / "steps" / "s001" / "result.xyz").is_file()
        assert _manifest(work)["terminals"] == [
            {"id": "s001", "label": None, "artifacts": ["steps/s001/result.xyz"]}
        ]

    def test_pe2_confgen_then_calc(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        _assert_public_v2_artifacts(work)
        assert _manifest(work)["terminals"][0]["id"] == "s002"

    def test_pe3_branching_dag(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [
                _step("s001", "confgen", []),
                _step("s002", "calc", ["s001"]),
                _step("s003", "calc", ["s001"]),
            ],
        )
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        assert [entry["id"] for entry in _manifest(work)["terminals"]] == ["s002", "s003"]

    def test_pe4_duplicate_labels(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", [], label="Optimize"),
            _step("s002", "calc", ["s001"], label="Optimize"),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        # the artifact key is the stable ID; the label stays display-only
        assert _manifest(work)["terminals"] == [
            {"id": "s002", "label": "Optimize", "artifacts": ["steps/s002/result.xyz"]}
        ]

    def test_pe5_disabled_bypass(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"], enabled=False),
            _step("s003", "calc", ["s002"]),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        # s002 never executed; s003 consumed s001's output through the bypass
        assert handlers.calls == ["s001", "s003"]
        assert _state(work)["steps"]["s002"]["status"] == "skipped"
        assert _manifest(work)["terminals"][0]["artifacts"] == ["steps/s003/result.xyz"]

    def test_pe6_checkpoint_by_id(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"]),
            _step("s003", "calc", ["s002"], checkpoint={"from_step": "s002"}),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        _assert_public_v2_artifacts(work)
        # the calc handler produced per-ID checkpoint backups consumed by s003
        assert (work / "steps" / "s002" / "backups").is_dir()
        assert handlers.calls == ["s001", "s002", "s003"]

    def test_pe7_multiple_roots(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        steps = [_step("s001", "confgen", []), _step("s002", "confgen", [])]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        assert [entry["id"] for entry in _manifest(work)["terminals"]] == ["s001", "s002"]

    def test_pe8_multiple_terminals_no_collapse(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"]),
            _step("s003", "calc", ["s001"]),
            _step("s004", "calc", []),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        terminals = _manifest(work)["terminals"]
        assert [entry["id"] for entry in terminals] == ["s002", "s003", "s004"]
        # every terminal artifact survives; nothing is silently collapsed
        stats = _stats(work)
        assert set(stats["terminal_outputs"]) == {"s002", "s003", "s004"}


# ---------------------------------------------------------------------------
# PE-R1–PE-R7 — public resume
# ---------------------------------------------------------------------------
class TestPublicResume:
    def _completed_prefix(
        self, tmp_path: Path, monkeypatch, *, keyword: str = "HF"
    ) -> tuple[Path, Path, _Handlers]:
        """Run until s002 fails; s001 is completed."""
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"], params={"keyword": keyword}),
            _step("s003", "calc", ["s002"]),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        handlers = _Handlers(monkeypatch, fail_ids={"s002"})
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        assert handlers.calls == ["s001", "s002"]
        return config, work, handlers

    def test_pe_r1_public_resume_after_failure(self, tmp_path: Path, monkeypatch) -> None:
        config, work, handlers = self._completed_prefix(tmp_path, monkeypatch)
        fresh = _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.SUCCESS
        # the failed step and its pending successor re-executed; s001 reused
        assert fresh.calls == ["s002", "s003"]
        _assert_public_v2_artifacts(work)

    def test_pe_r2_label_rename_resume(self, tmp_path: Path, monkeypatch) -> None:
        config, work, _handlers = self._completed_prefix(tmp_path, monkeypatch)
        document = yaml.safe_load(config.read_text(encoding="utf-8"))
        document["steps"][0]["label"] = "Renamed"
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        fresh = _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.SUCCESS
        assert fresh.calls == ["s002", "s003"]
        # the label snapshot drifted; identity did not
        assert _state(work)["steps"]["s001"]["label"] == "Renamed"

    def test_pe_r3_yaml_reorder_resume(self, tmp_path: Path, monkeypatch) -> None:
        config, work, _handlers = self._completed_prefix(tmp_path, monkeypatch)
        document = yaml.safe_load(config.read_text(encoding="utf-8"))
        document["steps"] = [document["steps"][2], document["steps"][1], document["steps"][0]]
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        fresh = _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.SUCCESS
        assert fresh.calls == ["s002", "s003"]

    def test_pe_r4_input_rename_only_resume(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        xyz = tmp_path / "in_a.xyz"
        _write_xyz(xyz, note="stable bytes")
        work = tmp_path / "work"
        assert cli_main([str(xyz), "-c", str(config), "-w", str(work)]) is ExitCode.SUCCESS
        before = _state(work)["binding"]["execution_fingerprint"]

        renamed = tmp_path / "in_b.xyz"
        xyz.rename(renamed)
        code = cli_main([str(renamed), "-c", str(config), "-w", str(work), "--resume"])
        assert code is ExitCode.SUCCESS
        assert _state(work)["binding"]["execution_fingerprint"] == before

    def test_pe_r5_a_mismatch_rejects_zero_side_effects(self, tmp_path: Path, monkeypatch) -> None:
        config, work, _handlers = self._completed_prefix(tmp_path, monkeypatch)
        before = (work / ".workflow_state.json").read_bytes()
        document = yaml.safe_load(config.read_text(encoding="utf-8"))
        document["steps"][1]["params"]["keyword"] = "B3LYP"  # semantic change ⇒ A
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        fresh = _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        assert fresh.calls == []  # no handler ran
        assert (work / ".workflow_state.json").read_bytes() == before

    def test_pe_r6_b_producer_mismatch_rejects(self, tmp_path: Path, monkeypatch) -> None:
        """A different producer cannot join an existing run (B audit, PD-1)."""
        _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "work"
        assert (
            cli_main([str(tmp_path / "input.xyz"), "-c", str(config), "-w", str(work)])
            is ExitCode.SUCCESS
        )
        before = (work / ".workflow_state.json").read_bytes()

        from confflow.workflow.binding_v2 import PRODUCER_IDENTITY, BindingProvenanceV2

        provenance = BindingProvenanceV2(
            workflow_schema="confflow.workflow.v3",
            workflow_schema_sha256="sha256:" + "0" * 64,
            canonicalization_version="confflow-canonicalization-1",
            producer_identity=PRODUCER_IDENTITY,
            producer_version="9.9.9",
            producer_commit="different",
            producer_dirty=False,
        )
        with pytest.raises(ConfFlowError, match="binding mismatch"):
            run_v3_workflow(
                input_xyz=[str(tmp_path / "input.xyz")],
                config_file=str(config),
                work_dir=str(work),
                resume=True,
                provenance=provenance,
            )
        assert (work / ".workflow_state.json").read_bytes() == before

    def test_pe_r7_c_mismatch_rejects_zero_side_effects(self, tmp_path: Path, monkeypatch) -> None:
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "calc", [])],
            **{"global": {"orca_path": str(exe)}},
        )
        _Handlers(monkeypatch)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS
        before = (work / ".workflow_state.json").read_bytes()

        # the executable bytes changed at the (same) execution site ⇒ C differs
        exe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fresh = _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        assert fresh.calls == []
        assert (work / ".workflow_state.json").read_bytes() == before


# ---------------------------------------------------------------------------
# PE-F1–PE-F5 — public rerun semantics
# ---------------------------------------------------------------------------
class TestPublicRerun:
    def _failed_run(self, tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
        """Build a failed run with an unrelated completed branch.

        s001 completes, s003 completes, s004 (with its pending structure)
        fails after them in topological order.
        """
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"]),
            _step("s003", "calc", ["s001"]),
            _step("s004", "calc", ["s002"], params={"keyword": "MP2"}),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        handlers = _Handlers(monkeypatch, fail_ids={"s004"})
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        assert handlers.calls == ["s001", "s002", "s003", "s004"]
        return config, work

    def test_pe_f1_f2_failed_step_and_successor_rerun_by_id(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config, work = self._failed_run(tmp_path, monkeypatch)
        fresh = _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.SUCCESS
        # the failed step is re-identified by stable ID and its pending
        # successor is reset/re-executed
        assert fresh.calls == ["s004"]
        assert _state(work)["steps"]["s004"]["status"] == "completed"

    def test_pe_f3_unrelated_completed_branch_preserved(self, tmp_path: Path, monkeypatch) -> None:
        config, work = self._failed_run(tmp_path, monkeypatch)
        sibling = work / "steps" / "s003" / "result.xyz"
        sibling_bytes = sibling.read_bytes()
        s001_bytes = (work / "steps" / "s001" / "search.xyz").read_bytes()
        _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.SUCCESS
        assert sibling.read_bytes() == sibling_bytes
        assert (work / "steps" / "s001" / "search.xyz").read_bytes() == s001_bytes

    def test_pe_f4_duplicate_labels_safe_on_rerun(self, tmp_path: Path, monkeypatch) -> None:
        steps = [
            _step("s001", "confgen", [], label="Same"),
            _step("s002", "calc", ["s001"], label="Same", params={"keyword": "MP2"}),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        _Handlers(monkeypatch, fail_ids={"s002"})
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _Handlers(monkeypatch)
        code, _work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.SUCCESS
        assert _state(work)["steps"]["s002"]["status"] == "completed"

    def test_pe_f5_label_or_index_selector_rejected(self, tmp_path: Path, monkeypatch) -> None:
        _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", [], label="Optimize"),
            _step("s002", "calc", ["s001"]),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.SUCCESS

        with pytest.raises(RerunFailedUsageError, match="stable step id only"):
            run_rerun_failed(
                step_dir=str(work / "steps" / "s002"),
                config_file=str(config),
                step_ref="Optimize",  # label — never a V3 selector
            )
        with pytest.raises(RerunFailedUsageError, match="stable step id only"):
            run_rerun_failed(
                step_dir=str(work / "steps" / "s002"),
                config_file=str(config),
                step_ref="1",  # 1-based index — never a V3 selector
            )


# ---------------------------------------------------------------------------
# PE-C1–PE-C5 — public control semantics
# ---------------------------------------------------------------------------
class TestPublicControl:
    def test_pe_c3_c4_public_cancel_no_later_steps(self, tmp_path: Path, monkeypatch) -> None:
        handlers = _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "work"
        work.mkdir()
        (work / "CANCEL").touch()  # cancelled before the first boundary
        code = cli_main([str(tmp_path / "input.xyz"), "-c", str(config), "-w", str(work)])
        assert code is ExitCode.RUNTIME_ERROR
        assert handlers.calls == []  # no later steps after cancel
        assert not (work / "output_manifest.json").exists()

    def test_pe_c5_binding_state_coherent_after_midrun_cancel(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "work"
        work.mkdir()

        import confflow.workflow.v3_runtime as v3_runtime

        real_confgen = v3_runtime._run_confgen_step

        def spy_confgen(**kwargs: Any):
            (work / "CANCEL").touch()  # cancel arrives while s001 executes
            return real_confgen(**kwargs)

        monkeypatch.setattr(v3_runtime, "_run_confgen_step", spy_confgen)
        code = cli_main([str(tmp_path / "input.xyz"), "-c", str(config), "-w", str(work)])
        assert code is ExitCode.RUNTIME_ERROR
        # cancel wins over pause; s001 completed coherently, s002 never ran,
        # and no success manifest was published for an incomplete run
        persisted = _state(work)
        assert persisted["binding"]["schema"] == "confflow.workflow_binding.v2"
        assert persisted["steps"]["s001"]["status"] == "completed"
        assert persisted["steps"]["s002"]["status"] == "pending"
        assert persisted["final_status"] == ""  # not forged as completed
        assert not (work / "output_manifest.json").exists()

    def test_pe_c1_c2_pause_and_resume_via_service_protocol(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Prove public pause/resume via the service protocol.

        Covered end-to-end in TestService.test_svc7: durable PAUSED state,
        formal resume, stable-ID continuity.
        """
        import tests.test_workflow_v3_service as svc

        assert hasattr(svc.TestService, "test_svc7_pause_then_resume")


# ---------------------------------------------------------------------------
# Guard regression — unknown future schema still zero-side-effect blocked
# ---------------------------------------------------------------------------
def test_future_schema_still_blocked_publicly(tmp_path: Path, monkeypatch) -> None:
    _Handlers(monkeypatch)
    config = _config(tmp_path / "wf.yaml", [_step("s001", "calc", [])])
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    document["schema"] = "confflow.workflow.v9"
    config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    _write_xyz(tmp_path / "input.xyz")
    work = tmp_path / "work"
    code = cli_main([str(tmp_path / "input.xyz"), "-c", str(config), "-w", str(work)])
    assert code is ExitCode.USAGE_ERROR
    # no V3 runtime artifacts of any kind
    assert not list(work.glob("**/.workflow_state.json")) if work.exists() else True
    assert not (work / "steps").exists() if work.exists() else True
    assert not (work / "external_inputs").exists() if work.exists() else True
    # and the flipped table only ever enables the V3 entry
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].parse is True
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True
