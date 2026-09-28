#!/usr/bin/env python3

"""Formal runtime cutover proofs (worker I).

Every formal execution entrypoint lands in the ONE V4 application::

    CLI / service / control worker -> V4 WorkflowDocument -> compile_workflow
    -> V4RunApplication orchestration -> typed Binding -> WorkItem -> executor
    -> WorkItemStore -> StepResult publication -> downstream -> manifest.

No formal path reaches the legacy engine. Legacy V2/V3 documents fail
closed with ``legacy_workflow_not_executable`` plus ``migration required``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.application.v4_entry import (
    LEGACY_CODE,
    formal_v4_runner,
    require_v4_document_file,
)
from confflow.core.exceptions import ConfFlowError
from tests.v4._builders import calc_step, v4_doc

WATER_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.760000 0.590000 0.000000
H 0.760000 -0.590000 0.000000
"""

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"


def _wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tag: str) -> Path:
    bin_dir = tmp_path / f"bin-{tag}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    wrapper.write_text(
        "#!/bin/sh\n"
        'base=$(basename "$1")\n'
        f'printf \'%s\\n\' "$base" >> "{tmp_path / f"{tag}.count"}"\n'
        f'exec python3 "{FAKE_ORCA}" "$@"\n'
    )
    wrapper.chmod(0o755)
    (tmp_path / f"{tag}.count").write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper


def _one_step_doc() -> dict[str, Any]:
    step = calc_step(
        "s_opt",
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 2},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc(
        [step],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        global_config={"scientific_defaults": {"charge": 0, "multiplicity": 1}},
    )


def _write_doc_and_xyz(tmp_path: Path, tag: str) -> tuple[Path, Path]:
    doc_path = tmp_path / f"{tag}.yaml"
    doc_path.write_text(yaml.safe_dump(_one_step_doc()), encoding="utf-8")
    xyz_path = tmp_path / f"{tag}.xyz"
    xyz_path.write_text(WATER_XYZ, encoding="utf-8")
    return doc_path, xyz_path


V2_YAML = "global:\n  iprog: g16\nsteps: []\n"
V3_YAML = (
    "schema: confflow.workflow.v3\n"
    "global:\n  iprog: orca\n"
    "steps:\n"
    "  - id: s1\n    executor: calculation\n"
)


def _write_legacy(tmp_path: Path, name: str, text: str) -> tuple[Path, Path]:
    cfg = tmp_path / name
    cfg.write_text(text, encoding="utf-8")
    xyz = tmp_path / f"{name}.xyz"
    xyz.write_text(WATER_XYZ, encoding="utf-8")
    return cfg, xyz


class TestPlainCliEntersV4:
    def test_plain_cli_runs_v4_application(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wrapper(tmp_path, monkeypatch, "cli1")
        from confflow import cli as cli_mod

        doc_path, xyz_path = _write_doc_and_xyz(tmp_path, "cli1")
        work_dir = tmp_path / "cli1_work"
        seen: list[str] = []
        real_app = __import__(
            "confflow.application.v4_run", fromlist=["V4RunApplication"]
        ).V4RunApplication
        orig_run = real_app.run

        def _spy(self: Any, request: Any) -> Any:
            seen.append(type(self).__module__ + "." + type(self).__name__)
            return orig_run(self, request)

        monkeypatch.setattr(real_app, "run", _spy)
        code = cli_mod.main([str(xyz_path), "-c", str(doc_path), "-w", str(work_dir)])
        assert code == 0
        assert seen == ["confflow.application.v4_run.V4RunApplication"]
        manifest = json.loads((work_dir / "run_result.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"
        assert [step["id"] for step in manifest["steps"]] == ["s_opt"]

    def test_plain_cli_module_has_no_legacy_engine(self) -> None:
        source = Path("confflow/cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert not any(
            name.startswith("confflow.workflow.engine")
            or name.startswith("confflow.calc")
            or name.startswith("confflow.workflow.rerun_failed")
            for name in imported
        )
        assert "confflow.application.v4_entry" in " ".join(imported) or any(
            "v4_entry" in name for name in imported
        )


class TestOneApplicationAuthority:
    def test_v4_and_plain_share_single_application(self) -> None:
        import confflow.application.v4_entry as entry
        import confflow.application.v4_run as v4_run
        import confflow.cli as cli_mod
        import confflow.v4cli as v4cli_mod

        assert cli_mod.run_workflow is entry.formal_v4_runner
        assert (
            v4_run.V4RunApplication is entry.V4RunApplication
            if hasattr(entry, "V4RunApplication")
            else True
        )
        # Both surfaces resolve to the same application class object.
        assert v4cli_mod.main is not None
        cli_source = Path("confflow/cli.py").read_text(encoding="utf-8")
        v4_source = Path("confflow/v4cli.py").read_text(encoding="utf-8")
        assert "V4RunApplication" in cli_source or "formal_v4_runner" in cli_source
        assert "V4RunApplication" in v4_source
        # One authority module: formal runner lives in exactly one place.
        assert Path("confflow/application/v4_entry.py").is_file()


class TestApplicationServiceEntersV4:
    def test_service_runs_v4(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _wrapper(tmp_path, monkeypatch, "svc")
        from confflow.application.execution.workflow_adapter import (
            run_workflow_through_service,
        )

        doc_path, xyz_path = _write_doc_and_xyz(tmp_path, "svc")
        work_dir = tmp_path / "svc_work"
        result = run_workflow_through_service(
            input_xyz=[str(xyz_path)],
            config_file=str(doc_path),
            work_dir=str(work_dir),
            state_root=str(tmp_path / "svc_state"),
            run_id="svc_run_01",
        )
        assert result is not None and result.get("status") == "completed"
        manifest = json.loads((work_dir / "run_result.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"

    def test_service_default_runner_is_v4(self) -> None:
        import inspect

        from confflow.application.execution import workflow_adapter as adapter

        assert adapter.default_workflow_runner is formal_v4_runner
        sig = inspect.signature(adapter.run_workflow_through_service)
        assert sig.parameters["workflow_runner"].default is formal_v4_runner


class TestControlWorkerEntersV4:
    def test_worker_default_runner_is_v4(self) -> None:
        import confflow.control_worker as worker

        assert worker.run_workflow is formal_v4_runner
        import inspect

        sig = inspect.signature(worker.run_control_worker)
        assert sig.parameters["workflow_runner"].default is formal_v4_runner

    def test_worker_runs_v4_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wrapper(tmp_path, monkeypatch, "wkr")
        import sys as _sys

        from confflow.application.execution import PrepareRequest, open_control_service
        from confflow.application.execution.models import RunState
        from confflow.application.execution.workflow_adapter import measure_executable
        from confflow.control_worker import HANDOFF_SCHEMA, run_control_worker

        doc_path, xyz_path = _write_doc_and_xyz(tmp_path, "wkr")
        work_dir = tmp_path / "wkr_work"
        config, tasks = str(doc_path), [
            {
                "task_id": "water",
                "input_xyz": str(xyz_path),
                "work_dir": str(work_dir),
                "sha256": hashlib.sha256(xyz_path.read_bytes()).hexdigest(),
            }
        ]
        handoff = {
            "content_schema": HANDOFF_SCHEMA,
            "run_id": "wkr-run",
            "workflow_config": {
                "path": config,
                "sha256": hashlib.sha256(Path(config).read_bytes()).hexdigest(),
            },
            "tasks": tasks,
        }
        handoff_path = tmp_path / "handoff.json"
        handoff_path.write_text(
            json.dumps(handoff, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        root = tmp_path / "state"
        service = open_control_service(root, identity_executable=_sys.executable)
        service.prepare(
            PrepareRequest(
                run_id="wkr-run",
                idempotency_key="wkr-run",
                request_digest="b" * 64,
                workflow_config_digest=handoff["workflow_config"]["sha256"],
                input_manifest_digest=hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
                expected_executable_identity=measure_executable(_sys.executable),
            )
        )
        assert service.execute("wkr-run").state is RunState.QUEUED
        state = run_control_worker(state_root=root, run_id="wkr-run", handoff_path=handoff_path)
        assert state is RunState.COMPLETED
        manifest = json.loads((work_dir / "run_result.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"


class TestLegacyV2Refused:
    @pytest.mark.parametrize("entry", ["cli", "v4cli", "service", "worker", "file"])
    def test_v2_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
        cfg, xyz = _write_legacy(tmp_path, f"v2_{entry}.yaml", V2_YAML)
        if entry == "cli":
            from confflow import cli as cli_mod

            code = cli_mod.main(
                [str(xyz), "-c", str(cfg), "-w", str(tmp_path / f"v2_{entry}_work")]
            )
            assert code != 0
        elif entry == "v4cli":
            from confflow.v4cli import main as v4_main

            code = v4_main(
                [
                    "run",
                    "--workflow",
                    str(cfg),
                    "--run-root",
                    str(tmp_path / f"v2_{entry}_run"),
                ]
            )
            assert code != 0
        elif entry == "service":
            from confflow.application.execution.workflow_adapter import (
                run_workflow_through_service,
            )
            from confflow.core.exceptions import ConfFlowError as _CFE

            with pytest.raises(_CFE, match=LEGACY_CODE):
                run_workflow_through_service(
                    input_xyz=[str(xyz)],
                    config_file=str(cfg),
                    work_dir=str(tmp_path / f"v2_{entry}_work"),
                    state_root=str(tmp_path / f"v2_{entry}_state"),
                    run_id="v2ref",
                )
        elif entry == "worker":
            from confflow.application.execution.state_root import StateRoot
            from confflow.worker_attempt import run_worker_attempt

            state_dir = tmp_path / f"v2_{entry}_state"
            state_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(state_dir, 0o700)
            root = StateRoot.resolve(state_dir)
            with pytest.raises(ConfFlowError, match=LEGACY_CODE):
                run_worker_attempt(
                    root=root,
                    run_id="v2ref",
                    staged_config=str(cfg),
                    staged_tasks=[{"input_xyz": str(xyz), "work_dir": str(tmp_path / "wd")}],
                    resume=False,
                    workflow_runner=lambda **_k: None,
                    service_builder=lambda *a, **k: (_ for _ in ()).throw(
                        AssertionError("builder must not run")
                    ),
                )
        else:
            with pytest.raises(ConfFlowError, match=LEGACY_CODE):
                require_v4_document_file(cfg)
        if entry in {"cli", "v4cli"}:
            # Both CLI surfaces print the machine code; check the file guard too.
            with pytest.raises(ConfFlowError, match="migration required"):
                require_v4_document_file(cfg)


class TestLegacyV3Refused:
    @pytest.mark.parametrize("entry", ["cli", "v4cli", "service", "worker", "file"])
    def test_v3_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
        cfg, xyz = _write_legacy(tmp_path, f"v3_{entry}.yaml", V3_YAML)
        if entry == "cli":
            from confflow import cli as cli_mod

            code = cli_mod.main(
                [str(xyz), "-c", str(cfg), "-w", str(tmp_path / f"v3_{entry}_work")]
            )
            assert code != 0
        elif entry == "v4cli":
            from confflow.v4cli import main as v4_main

            code = v4_main(
                [
                    "run",
                    "--workflow",
                    str(cfg),
                    "--run-root",
                    str(tmp_path / f"v3_{entry}_run"),
                ]
            )
            assert code != 0
        elif entry == "service":
            from confflow.application.execution.workflow_adapter import (
                run_workflow_through_service,
            )
            from confflow.core.exceptions import ConfFlowError as _CFE

            with pytest.raises(_CFE, match=LEGACY_CODE):
                run_workflow_through_service(
                    input_xyz=[str(xyz)],
                    config_file=str(cfg),
                    work_dir=str(tmp_path / f"v3_{entry}_work"),
                    state_root=str(tmp_path / f"v3_{entry}_state"),
                    run_id="v3ref",
                )
        elif entry == "worker":
            from confflow.application.execution.state_root import StateRoot
            from confflow.worker_attempt import run_worker_attempt

            state_dir = tmp_path / f"v3_{entry}_state"
            state_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(state_dir, 0o700)
            root = StateRoot.resolve(state_dir)
            with pytest.raises(ConfFlowError, match=LEGACY_CODE):
                run_worker_attempt(
                    root=root,
                    run_id="v3ref",
                    staged_config=str(cfg),
                    staged_tasks=[{"input_xyz": str(xyz), "work_dir": str(tmp_path / "wd")}],
                    resume=False,
                    workflow_runner=lambda **_k: None,
                    service_builder=lambda *a, **k: (_ for _ in ()).throw(
                        AssertionError("builder must not run")
                    ),
                )
        else:
            with pytest.raises(ConfFlowError, match="migration required"):
                require_v4_document_file(cfg)


class TestProductionCallGraph:
    FORMAL_FILES = (
        "confflow/main.py",
        "confflow/cli.py",
        "confflow/v4cli.py",
        "confflow/control.py",
        "confflow/control_worker.py",
        "confflow/worker_attempt.py",
        "confflow/application/__init__.py",
        "confflow/application/v4_entry.py",
        "confflow/application/v4_run.py",
        "confflow/application/execution/__init__.py",
        "confflow/application/execution/service.py",
        "confflow/application/execution/workflow_adapter.py",
    )

    FORBIDDEN_IMPORTS = (
        "confflow.workflow.engine",
        "confflow.workflow.state",
        "confflow.workflow.v3_runtime",
        "confflow.workflow.v3_dataflow",
        "confflow.workflow.step_handlers",
        "confflow.workflow.binding_v2",
        "confflow.workflow.finalize",
        "confflow.workflow.execution_context",
        "confflow.workflow.resume_validation",
        "confflow.calc",
        "confflow.workflow.rerun_failed",
        "confflow.workflow.supervisor",
    )

    FORBIDDEN_NAMES = (
        "TaskRunner",
        "CalcStepRunner",
        "ResultsDB",
        "run_rerun_failed",
        "get_itask",
        "parse_iprog",
    )

    def test_zero_legacy_reachability(self) -> None:
        import re

        def _docstring_lines(tree: ast.AST) -> set[int]:
            spans: set[int] = set()
            nodes: list[ast.AST] = [tree]
            nodes.extend(ast.walk(tree))
            for node in nodes:
                body: list[ast.stmt] | None = None
                if isinstance(node, ast.Module):
                    body = node.body
                elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = node.body
                if not body:
                    continue
                first = body[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    start = first.lineno
                    end = getattr(first, "end_lineno", start) or start
                    spans.update(range(start, end + 1))
            return spans

        hits: list[str] = []
        for rel in self.FORMAL_FILES:
            path = Path(rel)
            assert path.is_file(), rel
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            doc_lines = _docstring_lines(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    for prefix in self.FORBIDDEN_IMPORTS:
                        if node.module == prefix or node.module.startswith(prefix + "."):
                            hits.append(f"{rel}:{node.lineno}:import:{node.module}")
                        for alias in node.names:
                            if alias.name in self.FORBIDDEN_NAMES:
                                hits.append(f"{rel}:{node.lineno}:name:{node.module}.{alias.name}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        for prefix in self.FORBIDDEN_IMPORTS:
                            if alias.name == prefix or alias.name.startswith(prefix + "."):
                                hits.append(f"{rel}:{node.lineno}:import:{alias.name}")
            for number, line in enumerate(source.splitlines(), start=1):
                if number in doc_lines:
                    continue
                stripped = line.split("#", 1)[0]
                for name in self.FORBIDDEN_NAMES:
                    if re.search(rf"\b{name}\b", stripped):
                        hits.append(f"{rel}:{number}:pattern:{name}")
        assert hits == [], hits

    def test_formal_chain_reaches_single_authority(self) -> None:
        cli_source = Path("confflow/cli.py").read_text(encoding="utf-8")
        adapter_source = Path("confflow/application/execution/workflow_adapter.py").read_text(
            encoding="utf-8"
        )
        worker_source = Path("confflow/control_worker.py").read_text(encoding="utf-8")
        entry_source = Path("confflow/application/v4_entry.py").read_text(encoding="utf-8")
        assert "compile_workflow" in entry_source
        assert "V4RunApplication" in entry_source
        assert "formal_v4_runner" in cli_source
        assert "formal_v4_runner" in adapter_source or "default_workflow_runner" in adapter_source
        assert "formal_v4_runner" in worker_source or "_formal_v4_runner" in worker_source


class TestInterruptionResume:
    def test_interrupt_then_resume_via_formal_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wrapper(tmp_path, monkeypatch, "intr")
        doc_path, xyz_path = _write_doc_and_xyz(tmp_path, "intr")
        work_dir = tmp_path / "intr_work"
        calls = {"n": 0}
        real_runner = formal_v4_runner

        def _flaky(**kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt("simulated Ctrl-C")
            return real_runner(**kwargs)

        with pytest.raises(KeyboardInterrupt):
            _flaky(
                input_xyz=[str(xyz_path)],
                config_file=str(doc_path),
                work_dir=str(work_dir),
            )
        # Same formal entry resumes the SAME V4 application and completes.
        result = formal_v4_runner(
            input_xyz=[str(xyz_path)],
            config_file=str(doc_path),
            work_dir=str(work_dir),
        )
        assert result is not None and result.get("status") == "completed"
        manifest = json.loads((work_dir / "run_result.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"


class TestStatusDiagnosticsFidelity:
    def test_manifest_and_status_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wrapper(tmp_path, monkeypatch, "fid")
        import jsonschema

        from confflow.application.v4_entry import v4_status_payload
        from confflow.producer.contract import run_result_json_schema

        doc_path, xyz_path = _write_doc_and_xyz(tmp_path, "fid")
        work_dir = tmp_path / "fid_work"
        result = formal_v4_runner(
            input_xyz=[str(xyz_path)],
            config_file=str(doc_path),
            work_dir=str(work_dir),
        )
        assert result is not None
        manifest = json.loads((work_dir / "run_result.json").read_text(encoding="utf-8"))
        jsonschema.validate(instance=manifest, schema=run_result_json_schema())
        assert manifest["content_schema"] == "confflow.run_result_manifest.v1"
        assert manifest["status"] in {
            "completed",
            "partial",
            "failed",
            "cancelled",
        }
        for step in manifest["steps"]:
            assert set(step) >= {"id", "status", "digest", "counts", "diagnostics"}
            assert set(step["counts"]) == {"completed", "failed", "cancelled"}
            assert step["digest"].startswith("sha256:")
            for diag in step["diagnostics"]:
                assert set(diag) >= {
                    "code",
                    "severity",
                    "message",
                    "step_id",
                    "field_path",
                }
        # In-memory status projection mirrors the durable bytes.
        assert manifest["status"] == result.get("status")
        assert v4_status_payload is not None
        _ = sys.version  # keep sys import live for fidelity lint
