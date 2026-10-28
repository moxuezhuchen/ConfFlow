#!/usr/bin/env python3
"""Architecture review R4: script identity fixed at compile time, re-checked at launch.

Compile records the registered script identity on the planned step; batch hands it to
the item context; ScriptExecutor refuses to launch when the live registration or the
script file no longer matches. A refused item never publishes a completed result, so
an old compiled digest cannot label new script content.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain._immutable import freeze_value
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.domain.resources import ResourceRequest
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution import script_executor as script_executor_module
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.script_executor import ScriptExecutor
from confflow.execution.script_registry import load_script_registry, script_identity
from confflow.execution.work_item_executor import ItemExecutionContext
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
from tests.v4._builders import structure

_GIB = 1024**3

_ECHO_SCRIPT = """\
import sys
from pathlib import Path
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 " + sys.argv[1] + "\\n")
"""

_OTHER_SCRIPT = """\
from pathlib import Path
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 9\\n")
"""


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point server config/state at tmp so tests never touch HOME."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _write_config(root: Path, command: list[str]) -> Path:
    path = root / "server.toml"
    path.write_text(
        "total_cores = 96\n"
        "total_memory = '192GB'\n\n"
        "[scripts.demo]\n"
        f"command = {json.dumps(command)}\n",
        encoding="utf-8",
    )
    return path


def _doc() -> dict[str, Any]:
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "s",
                "executor": "script",
                "script": "demo",
                "args": ["1"],
                "outputs": {"structures": "final.xyz"},
                "resources": {"cores_per_item": 1, "memory_per_item": "1GB"},
                "bindings": {"structure": {"source": {"run": "structures"}}},
            }
        ],
    }


def _compiled_step(script: Path) -> Any:
    """Compile the demo workflow against the current registration; return its step."""
    compiled = compile_workflow(_doc())
    assert compiled.ok, [str(item) for item in compiled.errors]
    assert compiled.plan is not None
    step = compiled.plan.steps[0]
    assert step.script_identity is not None
    assert step.script_identity == freeze_value(script_identity(load_script_registry()["demo"]))
    return step


def _item(key: str = "script-item") -> WorkItem:
    record = structure(f"s-{key}", charge=0, multiplicity=1)
    named = WorkItemInputs(structures=FrozenDict({"structure": StructureSet.of(record)}))
    digest = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    return WorkItem(
        id=make_work_item_id(key),
        logical_key=key,
        step_id="s",
        named_inputs=named,
        resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=_GIB),
        semantic_digest=digest,
        ordinal=0,
    )


def _context(run_root: Path, expected: FrozenDict | None) -> ItemExecutionContext:
    return ItemExecutionContext(
        step_id="s",
        scientific=ScientificDefinition(
            script_id="demo",
            script_args=("1",),
            script_outputs=FrozenDict({"structures": "final.xyz"}),
        ),
        scientific_defaults=ScientificDefaults(),
        adapter=None,  # type: ignore[arg-type]  # script items never consult the adapter
        profile=None,  # type: ignore[arg-type]
        supervisor=NativeProcessSupervisor(),
        run_root=str(run_root),
        poll_interval_seconds=0.01,
        expected_script_identity=expected,
    )


@pytest.fixture
def registered(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (script, server.toml, run_root) for a registered echo script."""
    script = tmp_path / "demo.py"
    script.write_text(_ECHO_SCRIPT, encoding="utf-8")
    config = _write_config(tmp_path, [sys.executable, str(script), "1"])
    run_root = tmp_path / "run"
    run_root.mkdir()
    return script, config, run_root


class TestExecutorIdentityCheck:
    def test_unchanged_script_runs_with_compiled_identity(
        self, registered: tuple[Path, Path, Path]
    ) -> None:
        script, _, run_root = registered
        step = _compiled_step(script)
        result = ScriptExecutor().execute(_item(), _context(run_root, step.script_identity))
        assert result.status is WorkItemStatus.COMPLETED, [d.message for d in result.diagnostics]
        assert result.structures[0].coordinates[0][2] == pytest.approx(1.0)

    def test_script_edited_after_compile_is_refused(
        self, registered: tuple[Path, Path, Path]
    ) -> None:
        script, _, run_root = registered
        step = _compiled_step(script)
        script.write_text(_OTHER_SCRIPT, encoding="utf-8")
        result = ScriptExecutor().execute(_item(), _context(run_root, step.script_identity))
        assert result.status is not WorkItemStatus.COMPLETED
        assert result.error is not None and result.error.code == "script_failed"
        assert "changed after the workflow was compiled" in result.diagnostics[0].message
        assert not result.structures
        assert not result.results

    def test_registration_changed_after_compile_is_refused(
        self, registered: tuple[Path, Path, Path]
    ) -> None:
        script, config, run_root = registered
        step = _compiled_step(script)
        config.write_text(config.read_text().replace('"1"]', '"2"]'), encoding="utf-8")
        result = ScriptExecutor().execute(_item(), _context(run_root, step.script_identity))
        assert result.status is not WorkItemStatus.COMPLETED
        assert "changed after the workflow was compiled" in result.diagnostics[0].message
        assert not result.structures

    def test_no_expected_identity_keeps_previous_behaviour(
        self, registered: tuple[Path, Path, Path]
    ) -> None:
        script, _, run_root = registered
        _compiled_step(script)
        script.write_text(_OTHER_SCRIPT, encoding="utf-8")
        result = ScriptExecutor().execute(_item(), _context(run_root, None))
        assert result.status is WorkItemStatus.COMPLETED, [d.message for d in result.diagnostics]
        assert result.structures[0].coordinates[0][2] == pytest.approx(9.0)

    def test_file_changed_between_registry_load_and_launch_is_refused(
        self, registered: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script, _, run_root = registered
        real_load = script_executor_module.load_script_registry

        def load_then_edit() -> dict[str, Any]:
            registry = real_load()
            script.write_text(_OTHER_SCRIPT, encoding="utf-8")
            return registry

        monkeypatch.setattr(script_executor_module, "load_script_registry", load_then_edit)
        result = ScriptExecutor().execute(_item(), _context(run_root, None))
        assert result.status is not WorkItemStatus.COMPLETED
        assert result.error is not None and result.error.code == "script_failed"
        assert "changed between registry load and launch" in result.diagnostics[0].message
        assert not result.structures
        assert not list(run_root.rglob("final.xyz"))

    def test_file_removed_between_registry_load_and_launch_is_refused(
        self, registered: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script, _, run_root = registered
        real_load = script_executor_module.load_script_registry

        def load_then_remove() -> dict[str, Any]:
            registry = real_load()
            script.unlink()
            return registry

        monkeypatch.setattr(script_executor_module, "load_script_registry", load_then_remove)
        result = ScriptExecutor().execute(_item(), _context(run_root, None))
        assert result.status is not WorkItemStatus.COMPLETED
        assert "unreadable before launch" in result.diagnostics[0].message


class TestBatchCarriesCompiledIdentity:
    def _request(self, step: Any, run_root: Path) -> StepExecutionRequest:
        return StepExecutionRequest(
            step=step,
            items=(_item(),),
            scientific=step.scientific,
            scientific_defaults=ScientificDefaults(),
            adapter=None,
            profile=None,
            checks=(),
            recovery=None,
            execution_binding=None,
            run_root=str(run_root),
            environment=None,
            definition_digest=None,
            producer_provenance=FrozenDict({"executor": "script"}),
        )

    def _executor(self) -> BatchStepExecutor:
        return BatchStepExecutor(ScriptExecutor()).with_supervisor(NativeProcessSupervisor())

    def test_unchanged_registration_completes_the_step(
        self, registered: tuple[Path, Path, Path]
    ) -> None:
        script, _, run_root = registered
        step = _compiled_step(script)
        result = self._executor().execute_step(self._request(step, run_root))
        assert result.status is StepStatus.COMPLETED, [
            d.message for d in result.item_results[0].diagnostics
        ]

    def test_script_edited_after_compile_never_completes_the_step(
        self, registered: tuple[Path, Path, Path]
    ) -> None:
        script, _, run_root = registered
        step = _compiled_step(script)
        script.write_text(_OTHER_SCRIPT, encoding="utf-8")
        result = self._executor().execute_step(self._request(step, run_root))
        assert result.status is not StepStatus.COMPLETED
        item = result.item_results[0]
        assert item.status is not WorkItemStatus.COMPLETED
        assert not item.structures
        assert "changed after the workflow was compiled" in item.diagnostics[0].message

    def test_plan_payload_digest_does_not_include_script_identity(
        self, registered: tuple[Path, Path, Path]
    ) -> None:
        compiled = compile_workflow(_doc())
        assert compiled.plan is not None
        payload = compiled.plan.steps[0].to_payload()
        assert "script_identity" not in payload
        assert payload["step_semantic_digest"] == compiled.plan.steps[0].step_semantic_digest
