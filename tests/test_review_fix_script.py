#!/usr/bin/env python3
"""Review fixes #3 and #5: script reuse identity, execution policy, env and walltime.

#3: the registered command (fixed args) and the interpreter identity are part of the
script step digest and of the per-item key, so changing ``[scripts.X].command`` recomputes.
#5: the script process gets the step's declared env and walltime, and the execution
policy is enforced before any input file is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict, StructureSet
from confflow.domain.errors import DomainError
from confflow.domain.resources import ResourceRequest
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.contracts import ExecutionBinding
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.script_executor import ScriptExecutor, script_item_key
from confflow.execution.script_registry import load_script_registry, script_identity
from confflow.execution.work_item_executor import ItemExecutionContext
from confflow.workflow.v4.assembly import RunInputs
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
from tests.v4._builders import structure

_GIB = 1024**3

# Writes the z coordinate taken from the first registered argument, so the
# coordinates of the output show which registration produced the result.
_COMMAND_ECHO_SCRIPT = """\
import sys
from pathlib import Path
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 " + sys.argv[1] + "\\n")
"""

_ENV_SCRIPT = """\
import json, os
from pathlib import Path
Path("summary.json").write_text(json.dumps({
    "declared": os.environ.get("REVIEW_DECLARED_ENV"),
    "ambient": os.environ.get("REVIEW_AMBIENT_MARKER"),
}))
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 0\\n")
"""

_SLEEP_SCRIPT = """\
import time
from pathlib import Path
time.sleep(2)
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 0\\n")
"""


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point server config/state at tmp so tests never touch HOME."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _write_script(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


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


def _doc(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [step],
    }


def _script_step(**overrides: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "s",
        "executor": "script",
        "script": "demo",
        "args": [],
        "outputs": {"structures": "final.xyz"},
        "resources": {"cores_per_item": 1, "memory_per_item": "1GB"},
        "bindings": {"structure": {"source": {"run": "structures"}}},
    }
    step.update(overrides)
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


def _context(
    run_root: Path,
    *,
    binding: ExecutionBinding | None = None,
    native_env: FrozenDict | None = None,
    args: tuple[str, ...] = (),
    outputs: dict[str, Any] | None = None,
) -> ItemExecutionContext:
    return ItemExecutionContext(
        step_id="s",
        scientific=ScientificDefinition(
            script_id="demo",
            script_args=args,
            script_outputs=FrozenDict(dict(outputs or {"structures": "final.xyz"})),
        ),
        scientific_defaults=ScientificDefaults(),
        adapter=None,  # type: ignore[arg-type]  # script items never consult the adapter
        profile=None,  # type: ignore[arg-type]
        supervisor=NativeProcessSupervisor(),
        run_root=str(run_root),
        poll_interval_seconds=0.01,
        execution_binding=binding,
        native_env=native_env,
    )


def _digest() -> str:
    compiled = compile_workflow(_doc(_script_step()))
    assert compiled.ok, [str(item) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan.steps[0].step_semantic_digest


class TestCommandIdentity:
    def test_registered_argument_change_moves_step_digest(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _COMMAND_ECHO_SCRIPT)
        _write_config(tmp_path, [sys.executable, str(script), "1"])
        first = _digest()
        _write_config(tmp_path, [sys.executable, str(script), "2"])
        second = _digest()
        assert first != second

    def test_interpreter_content_change_moves_step_digest(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _COMMAND_ECHO_SCRIPT)
        wrapper = _write_script(tmp_path, "wrapper.sh", f'#!/bin/sh\nexec {sys.executable} "$@"\n')
        wrapper.chmod(0o700)
        _write_config(tmp_path, [str(wrapper), str(script), "1"])
        first = _digest()
        wrapper.write_text(f'#!/bin/sh\n# revised\nexec {sys.executable} "$@"\n', encoding="utf-8")
        second = _digest()
        assert first != second

    def test_identity_replaces_script_element_and_keeps_fixed_args(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _COMMAND_ECHO_SCRIPT)
        _write_config(tmp_path, [sys.executable, str(script), "7"])
        entry = load_script_registry()["demo"]
        identity = script_identity(entry)
        assert identity["command"] == [sys.executable, entry.sha256, "7"]
        assert "interpreter" not in identity  # path is machine detail, content is identity
        assert (
            identity["interpreter_sha256"]
            == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        )

    def test_item_key_moves_when_registered_argument_changes(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _COMMAND_ECHO_SCRIPT)
        config = _write_config(tmp_path, [sys.executable, str(script), "1"])
        run_root = tmp_path / "run"
        run_root.mkdir()
        first = ScriptExecutor().execute(
            _item(), _context(run_root, outputs={"structures": "final.xyz"})
        )
        assert first.status.value == "completed", [d.message for d in first.diagnostics]
        config.write_text(config.read_text().replace('"1"]', '"2"]'), encoding="utf-8")
        second = ScriptExecutor().execute(
            _item(), _context(run_root, outputs={"structures": "final.xyz"})
        )
        assert second.status.value == "completed", [d.message for d in second.diagnostics]
        first_key = dict(first.diagnostics[0].details)["item_key"]
        second_key = dict(second.diagnostics[0].details)["item_key"]
        assert first_key != second_key
        assert first.structures[0].coordinates[0][2] == pytest.approx(1.0)
        assert second.structures[0].coordinates[0][2] == pytest.approx(2.0)

    def test_item_key_is_stable_for_unchanged_registration(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _COMMAND_ECHO_SCRIPT)
        _write_config(tmp_path, [sys.executable, str(script), "1"])
        entry = load_script_registry()["demo"]
        record = structure("s-a", charge=0, multiplicity=1)
        first = script_item_key(identity=script_identity(entry), expanded_args=(), structure=record)
        second = script_item_key(
            identity=script_identity(entry), expanded_args=(), structure=record
        )
        assert first == second

    def test_end_to_end_command_change_is_not_silently_reused(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _COMMAND_ECHO_SCRIPT)
        config = _write_config(tmp_path, [sys.executable, str(script), "1"])
        text = "1\nreview\nHe 0 0 0\n"
        document = _doc(_script_step())

        def run_once(run_root: Path) -> Any:
            return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                V4RunRequest(
                    workflow_document=document,
                    run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(text)})),
                    import_sources=FrozenDict({"structures": text}),
                    run_root=str(run_root),
                    executables=FrozenDict(),
                )
            )

        run_root = tmp_path / "run"
        first = run_once(run_root)
        assert first.step_results[0].structures[0].coordinates[0][2] == pytest.approx(1.0)
        config.write_text(config.read_text().replace('"1"]', '"2"]'), encoding="utf-8")
        # The step digest now differs, so the same run root refuses the changed definition
        # instead of reusing the "1" result.
        with pytest.raises(DomainError, match="different workflow definition"):
            run_once(run_root)
        # A fresh run root recomputes with the new registered argument.
        second = run_once(tmp_path / "fresh")
        assert second.step_results[0].structures[0].coordinates[0][2] == pytest.approx(2.0)


class TestPolicyEnvironmentWalltime:
    def _registered(self, tmp_path: Path, body: str) -> tuple[Path, Path]:
        script = _write_script(tmp_path, "demo.py", body)
        _write_config(tmp_path, [sys.executable, str(script)])
        run_root = tmp_path / "run"
        run_root.mkdir()
        return script, run_root

    def test_forbidden_interpreter_fails_before_any_write(self, tmp_path: Path) -> None:
        _, run_root = self._registered(tmp_path, _COMMAND_ECHO_SCRIPT)
        other = _write_script(tmp_path, "other-python", "#!/bin/sh\nexit 0\n")
        binding = ExecutionBinding(binding_id="restricted", allowed_executables=(str(other),))
        result = ScriptExecutor().execute(
            _item(), _context(run_root, binding=binding, args=("{input}",))
        )
        assert result.status.value == "failed"
        assert result.error is not None and result.error.code == "script_failed"
        assert list(run_root.rglob("input.xyz")) == []
        assert not (run_root / "script").exists()

    def test_allowed_interpreter_inside_sandbox_completes(self, tmp_path: Path) -> None:
        _, run_root = self._registered(tmp_path, _COMMAND_ECHO_SCRIPT)
        binding = ExecutionBinding(
            binding_id="ok",
            allowed_executables=(sys.executable,),
            sandbox=str(tmp_path),
        )
        result = ScriptExecutor().execute(_item(), _context(run_root, binding=binding, args=("1",)))
        assert result.status.value == "completed", [d.message for d in result.diagnostics]

    def test_run_root_outside_sandbox_fails_before_any_write(self, tmp_path: Path) -> None:
        _, run_root = self._registered(tmp_path, _COMMAND_ECHO_SCRIPT)
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        binding = ExecutionBinding(binding_id="sandboxed", sandbox=str(sandbox))
        result = ScriptExecutor().execute(_item(), _context(run_root, binding=binding, args=("1",)))
        assert result.status.value == "failed"
        assert list(run_root.rglob("input.xyz")) == []
        assert list(sandbox.iterdir()) == []

    def test_declared_env_is_passed_to_script(self, tmp_path: Path) -> None:
        _, run_root = self._registered(tmp_path, _ENV_SCRIPT)
        env = FrozenDict({"PATH": os.environ.get("PATH", ""), "REVIEW_DECLARED_ENV": "expected"})
        result = ScriptExecutor().execute(
            _item(),
            _context(
                run_root,
                native_env=env,
                outputs={"structures": "final.xyz", "summary": "summary.json"},
            ),
        )
        assert result.status.value == "completed", [d.message for d in result.diagnostics]
        assert result.results[0].value["declared"] == "expected"
        assert result.results[0].value["ambient"] is None

    def test_missing_snapshot_falls_back_to_ambient_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, run_root = self._registered(tmp_path, _ENV_SCRIPT)
        monkeypatch.setenv("REVIEW_AMBIENT_MARKER", "ambient-value")
        result = ScriptExecutor().execute(
            _item(),
            _context(run_root, outputs={"structures": "final.xyz", "summary": "summary.json"}),
        )
        assert result.status.value == "completed", [d.message for d in result.diagnostics]
        assert result.results[0].value["ambient"] == "ambient-value"

    def test_walltime_overrun_fails_with_timeout_diagnostic(self, tmp_path: Path) -> None:
        _, run_root = self._registered(tmp_path, _SLEEP_SCRIPT)
        binding = ExecutionBinding(binding_id="short", walltime_seconds=1)
        result = ScriptExecutor().execute(_item(), _context(run_root, binding=binding))
        assert result.status.value == "failed"
        assert result.error is not None and result.error.code == "script_failed"
        assert "exceeded walltime of 1 seconds" in result.diagnostics[0].message
        details = dict(result.diagnostics[0].details)
        assert details["timed_out"] is True
        assert details["walltime_seconds"] == 1.0

    def test_no_walltime_lets_a_short_script_complete(self, tmp_path: Path) -> None:
        _, run_root = self._registered(tmp_path, _SLEEP_SCRIPT)
        result = ScriptExecutor().execute(_item(), _context(run_root))
        assert result.status.value == "completed", [d.message for d in result.diagnostics]
