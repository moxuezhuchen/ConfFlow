"""R4.5 — Public Workflow V3 execution end-to-end, SEALED inventory (PE1–PE8, PE-R, PE-F, PE-C).

The V4-only runtime cutover retired the V3 execution path: the formal CLI
entry (``confflow.cli.main``) refuses every V2/V3 config with
``legacy_workflow_not_executable`` before the public stack runs. Each test
below preserves its scenario's config construction and proves the refusal
with zero side effects — no manifests, no state files, no handler calls.
Direct-engine follow-up legs were dropped (formal-entry scope).
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


def _assert_sealed_cli(work: Path, capsys, handlers: _Handlers | None = None) -> None:
    """Assert a sealed CLI refusal: guard code, machine error, zero traces."""
    assert "legacy_workflow_not_executable" in capsys.readouterr().err
    if handlers is not None:
        assert handlers.calls == []
    assert not (work / "output_manifest.json").exists()
    assert not (work / ".workflow_state.json").exists()
    assert not (work / "steps").exists()


# ---------------------------------------------------------------------------
# PE1–PE8 — public CLI runs
# ---------------------------------------------------------------------------
class TestPublicRuns:
    def test_pe1_simple_calc_full_public_chain_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: single calc, full chain)."""
        handlers = _Handlers(monkeypatch)
        config = _config(tmp_path / "wf.yaml", [_step("s001", "calc", [])])
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe2_confgen_then_calc_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: confgen then calc)."""
        handlers = _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe3_branching_dag_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: branching DAG)."""
        handlers = _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [
                _step("s001", "confgen", []),
                _step("s002", "calc", ["s001"]),
                _step("s003", "calc", ["s001"]),
            ],
        )
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe4_duplicate_labels_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: duplicate labels)."""
        handlers = _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", [], label="Optimize"),
            _step("s002", "calc", ["s001"], label="Optimize"),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe5_disabled_bypass_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: disabled-step bypass)."""
        handlers = _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"], enabled=False),
            _step("s003", "calc", ["s002"]),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe6_checkpoint_by_id_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: checkpoint by ID)."""
        handlers = _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"]),
            _step("s003", "calc", ["s002"], checkpoint={"from_step": "s002"}),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe7_multiple_roots_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: multiple roots)."""
        handlers = _Handlers(monkeypatch)
        steps = [_step("s001", "confgen", []), _step("s002", "confgen", [])]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe8_multiple_terminals_no_collapse_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: multiple terminals)."""
        handlers = _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"]),
            _step("s003", "calc", ["s001"]),
            _step("s004", "calc", []),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)


# ---------------------------------------------------------------------------
# PE-R1–PE-R7 — public resume
# ---------------------------------------------------------------------------
class TestPublicResume:
    def _prefix_steps(self, *, keyword: str = "HF") -> list[dict[str, Any]]:
        """Scenario construction shared by the resume tests: s002 fails."""
        return [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"], params={"keyword": keyword}),
            _step("s003", "calc", ["s002"]),
        ]

    def test_pe_r1_public_resume_after_failure_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the resume (scenario: resume after failure)."""
        handlers = _Handlers(monkeypatch, fail_ids={"s002"})
        config = _config(tmp_path / "wf.yaml", self._prefix_steps())
        # the prefix run is refused, so the resume leg is the only leg
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_r2_label_rename_resume_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the resume (scenario: label rename)."""
        handlers = _Handlers(monkeypatch, fail_ids={"s002"})
        config = _config(tmp_path / "wf.yaml", self._prefix_steps())
        document = yaml.safe_load(config.read_text(encoding="utf-8"))
        document["steps"][0]["label"] = "Renamed"
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_r3_yaml_reorder_resume_sealed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """V3 sealed: the V4 guard refuses the resume (scenario: YAML reorder)."""
        handlers = _Handlers(monkeypatch, fail_ids={"s002"})
        config = _config(tmp_path / "wf.yaml", self._prefix_steps())
        document = yaml.safe_load(config.read_text(encoding="utf-8"))
        document["steps"] = [document["steps"][2], document["steps"][1], document["steps"][0]]
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_r4_input_rename_only_resume_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the resume (scenario: input rename only)."""
        handlers = _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        xyz = tmp_path / "in_a.xyz"
        _write_xyz(xyz, note="stable bytes")
        work = tmp_path / "work"
        renamed = tmp_path / "in_b.xyz"
        xyz.rename(renamed)
        code = cli_main([str(renamed), "-c", str(config), "-w", str(work), "--resume"])
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_r5_a_mismatch_rejects_zero_side_effects_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the resume (scenario: A param mismatch)."""
        handlers = _Handlers(monkeypatch, fail_ids={"s002"})
        config = _config(tmp_path / "wf.yaml", self._prefix_steps())
        document = yaml.safe_load(config.read_text(encoding="utf-8"))
        document["steps"][1]["params"]["keyword"] = "B3LYP"  # semantic change ⇒ A
        config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_r6_b_producer_mismatch_rejects_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: producer mismatch).

        The direct-engine ``run_v3_workflow`` provenance leg is dropped:
        it is not a formal entry, so it is out of the sealed scope.
        """
        handlers = _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "work"
        code = cli_main([str(tmp_path / "input.xyz"), "-c", str(config), "-w", str(work)])
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_r7_c_mismatch_rejects_zero_side_effects_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the resume (scenario: C exe mismatch)."""
        exe = tmp_path / "fake_orca"
        exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "calc", [])],
            **{"global": {"orca_path": str(exe)}},
        )
        handlers = _Handlers(monkeypatch)
        # the executable bytes changed at the (same) execution site ⇒ C differs,
        # but the resume is refused before any binding comparison runs
        exe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)


# ---------------------------------------------------------------------------
# PE-F1–PE-F5 — public rerun semantics
# ---------------------------------------------------------------------------
class TestPublicRerun:
    def _failed_steps(self) -> list[dict[str, Any]]:
        """Scenario construction: s004 fails after an unrelated completed branch."""
        return [
            _step("s001", "confgen", []),
            _step("s002", "calc", ["s001"]),
            _step("s003", "calc", ["s001"]),
            _step("s004", "calc", ["s002"], params={"keyword": "MP2"}),
        ]

    def test_pe_f1_f2_failed_step_and_successor_rerun_by_id_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the rerun (scenario: failed step rerun)."""
        handlers = _Handlers(monkeypatch, fail_ids={"s004"})
        config = _config(tmp_path / "wf.yaml", self._failed_steps())
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_f3_unrelated_completed_branch_preserved_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the rerun (scenario: completed branch)."""
        handlers = _Handlers(monkeypatch, fail_ids={"s004"})
        config = _config(tmp_path / "wf.yaml", self._failed_steps())
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_f4_duplicate_labels_safe_on_rerun_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the rerun (scenario: duplicate labels)."""
        handlers = _Handlers(monkeypatch, fail_ids={"s002"})
        steps = [
            _step("s001", "confgen", [], label="Same"),
            _step("s002", "calc", ["s001"], label="Same", params={"keyword": "MP2"}),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config, resume=True)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

    def test_pe_f5_label_or_index_selector_rejected_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: label/index selectors).

        The ``run_rerun_failed`` selector legs are dropped: no work layout
        ever exists for a selector to address.
        """
        handlers = _Handlers(monkeypatch)
        steps = [
            _step("s001", "confgen", [], label="Optimize"),
            _step("s002", "calc", ["s001"]),
        ]
        config = _config(tmp_path / "wf.yaml", steps)
        code, work = _cli(tmp_path, monkeypatch, config)
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)


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

    def test_pe_c5_binding_state_coherent_after_midrun_cancel_sealed(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """V3 sealed: the V4 guard refuses the run (scenario: cancel arriving mid-run)."""
        handlers = _Handlers(monkeypatch)
        config = _config(
            tmp_path / "wf.yaml",
            [_step("s001", "confgen", []), _step("s002", "calc", ["s001"])],
        )
        _write_xyz(tmp_path / "input.xyz")
        work = tmp_path / "work"
        work.mkdir()
        (work / "CANCEL").touch()  # cancel arrives before any step executes
        # the mid-run cancel spy and binding/state coherence legs can never
        # exist: the guard refuses before the runtime starts.
        code = cli_main([str(tmp_path / "input.xyz"), "-c", str(config), "-w", str(work)])
        assert code is ExitCode.RUNTIME_ERROR
        _assert_sealed_cli(work, capsys, handlers)

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
def test_future_schema_still_blocked_publicly_sealed(tmp_path: Path, monkeypatch, capsys) -> None:
    """V3 sealed: unknown future schemas stay blocked (scenario: v9 config, public CLI)."""
    handlers = _Handlers(monkeypatch)
    config = _config(tmp_path / "wf.yaml", [_step("s001", "calc", [])])
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    document["schema"] = "confflow.workflow.v9"
    config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    _write_xyz(tmp_path / "input.xyz")
    work = tmp_path / "work"
    code = cli_main([str(tmp_path / "input.xyz"), "-c", str(config), "-w", str(work)])
    assert code is ExitCode.RUNTIME_ERROR
    _assert_sealed_cli(work, capsys, handlers)
    assert not (work / "external_inputs").exists()
    # and the flipped table only ever enables the V3 entry
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].parse is True
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True
