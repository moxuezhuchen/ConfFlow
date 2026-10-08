#!/usr/bin/env python3
"""External script steps (DIET-2 N2): compile, registry, execution, control."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.errors import DomainError
from confflow.domain.resources import ResourceRequest
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.quota import QuotaError
from confflow.execution.script_executor import ScriptExecutor
from confflow.execution.script_registry import ScriptEntry, load_script_registry
from confflow.execution.work_item_executor import ItemExecutionContext
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition
from tests.v4._builders import structure

_GIB = 1024**3

_SUCCESS_SCRIPT = """\
import json, sys
inp, cores, mem, item = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
text = open(inp).read()
frames = []
for shift in ("0.0", "0.05", "0.10"):
    frames.append(
        "3\\nframe %s\\nO 0 0 %s\\nH 0.757 0.586 0\\nH -0.757 0.586 0" % (shift, shift)
    )
open("final.xyz", "w").write("\\n".join(frames) + "\\n")
open("summary.json", "w").write(
    json.dumps(
        {
            "input": item,
            "status": "ok",
            "cores": cores,
            "mem_gb": mem,
            "input_lines": len(text.splitlines()),
        }
    )
)
open("run.log", "w").write("ran ok\\n")
"""

_FAIL_SCRIPT = """\
import sys
sys.stderr.write("solver exploded\\nlast line\\n")
sys.exit(3)
"""

_SLEEPER_SCRIPT = """\
import subprocess, time
child = subprocess.Popen(["sleep", "30"])
open("child.pid", "w").write(str(child.pid))
child.wait()
"""

_MARKER_SCRIPT = """\
open("calls.marker", "a").write("call\\n")
open("final.xyz", "w").write("1\\nframe\\nHe 0 0 0\\n")
"""


@pytest.fixture(autouse=True)
def _isolated_server_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, pytest.MonkeyPatch]:
    """Point server config/state at tmp so tests never touch HOME."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    return tmp_path, monkeypatch


def _write_script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _write_server_toml(
    tmp_path: Path, scripts: dict[str, dict[str, Any]], *, cores: int = 96
) -> Path:
    lines = [f"total_cores = {cores}", "total_memory = '192GB'", ""]
    for script_id, spec in scripts.items():
        lines.append(f"[scripts.{script_id}]")
        lines.append(f"command = {json.dumps(spec['command'])}")
        if spec.get("description"):
            lines.append(f"description = {json.dumps(spec['description'])}")
        lines.append("")
    path = tmp_path / "server.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _entry(
    script_id: str = "demo",
    command: tuple[str, ...] | None = None,
    sha256: str = "ab" * 32,
) -> ScriptEntry:
    argv = command or (sys.executable, "/tmp/demo.py")
    return ScriptEntry(
        id=script_id,
        command=argv,
        script_path=argv[-1],
        sha256=sha256,
        interpreter=argv[0],
        description="demo script",
    )


def _doc(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": steps,
    }


def _script_step(step_id: str = "s1", **overrides: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": step_id,
        "executor": "script",
        "script": "demo",
        "args": ["{input}", "--nprocs", "{cores}", "--mem", "{mem_gb}", "--id", "{item_id}"],
        "resources": {"cores_per_item": 2, "memory_per_item": "4GB"},
        "outputs": {
            "structures": "final.xyz",
            "summary": "summary.json",
            "artifacts": ["*.log"],
        },
        "bindings": {"structure": {"source": {"run": "structures"}}},
    }
    step.update(overrides)
    return step


def _reasons(result: Any) -> list[str]:
    return [str(item.details.get("reason", "")) for item in result.errors]


def _script_item(
    key: str = "script-item",
    *,
    script_id: str = "demo",
    args: tuple[str, ...] = ("{input}",),
    outputs: dict[str, Any] | None = None,
    cores: int = 1,
) -> WorkItem:
    record = structure(f"s-{key}", charge=0, multiplicity=1)
    named = WorkItemInputs(structures=FrozenDict({"structure": StructureSet.of(record)}))
    digest = "sha256:" + hashlib.sha256(key.encode("utf-8")).hexdigest()
    return WorkItem(
        id=make_work_item_id(key),
        logical_key=key,
        step_id="s1",
        named_inputs=named,
        resources=ResourceRequest(cores_per_item=cores, memory_per_item_bytes=_GIB),
        semantic_digest=digest,
        ordinal=0,
    )


def _script_context(
    supervisor: Any,
    run_root: Path,
    *,
    script_id: str = "demo",
    args: tuple[str, ...] = ("{input}",),
    outputs: dict[str, Any] | None = None,
) -> ItemExecutionContext:
    return ItemExecutionContext(
        step_id="s1",
        scientific=ScientificDefinition(
            script_id=script_id,
            script_args=args,
            script_outputs=FrozenDict(dict(outputs or {"structures": "final.xyz"})),
        ),
        scientific_defaults=ScientificDefaults(),
        adapter=None,
        profile=None,
        supervisor=supervisor,
        run_root=str(run_root),
        poll_interval_seconds=0.01,
    )


class TestScriptCompilation:
    def test_valid_script_step_compiles_with_stable_digest(self) -> None:
        table = {"demo": _entry()}
        first = compile_workflow(_doc([_script_step()]), script_registry=table)
        second = compile_workflow(_doc([_script_step()]), script_registry=table)
        assert first.ok, [str(item) for item in first.errors]
        assert first.plan is not None and second.plan is not None
        assert first.plan.steps[0].step_semantic_digest == second.plan.steps[0].step_semantic_digest

    def test_unknown_script_id_fails_compilation(self) -> None:
        result = compile_workflow(_doc([_script_step()]), script_registry={})
        assert not result.ok
        assert "unknown_script" in _reasons(result)

    def test_missing_server_table_fails_compilation(self, tmp_path: Path) -> None:
        assert not (tmp_path / "server.toml").exists()
        result = compile_workflow(_doc([_script_step()]))
        assert not result.ok
        assert "unknown_script" in _reasons(result)

    @pytest.mark.parametrize(
        "args",
        [
            ["{input}", "{cores}x{extra}"],
            ["{mem_gb}", "{item_id"],
            ["a}b{"],
            ["{unknown}"],
            ["{{input}}"],
        ],
    )
    def test_illegal_placeholder_fails_compilation(self, args: list[str]) -> None:
        table = {"demo": _entry()}
        result = compile_workflow(_doc([_script_step(args=args)]), script_registry=table)
        assert not result.ok
        assert "invalid_script_args" in _reasons(result)

    def test_illegal_output_channel_fails_compilation(self) -> None:
        table = {"demo": _entry()}
        step = _script_step()
        step["outputs"] = {"structures": "final.xyz", "bogus": "x.json"}
        result = compile_workflow(_doc([step]), script_registry=table)
        assert not result.ok
        assert result.errors

    def test_missing_registered_command_fails_compilation(self, tmp_path: Path) -> None:
        _write_server_toml(
            tmp_path, {"demo": {"command": [sys.executable, str(tmp_path / "gone.py")]}}
        )
        result = compile_workflow(_doc([_script_step()]))
        assert not result.ok
        assert result.errors

    def test_registered_command_loads_from_server_toml(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(
            tmp_path,
            {"demo": {"command": [sys.executable, str(script)], "description": "d"}},
        )
        result = compile_workflow(_doc([_script_step()]))
        assert result.ok, [str(item) for item in result.errors]

    def test_script_digest_moves_when_script_content_changes(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        first = compile_workflow(_doc([_script_step()]))
        assert first.ok and first.plan is not None
        script.write_text(_SUCCESS_SCRIPT + "# changed\n", encoding="utf-8")
        second = compile_workflow(_doc([_script_step()]))
        assert second.ok and second.plan is not None
        assert first.plan.steps[0].step_semantic_digest != second.plan.steps[0].step_semantic_digest

    def test_script_step_without_script_id_fails(self) -> None:
        table = {"demo": _entry()}
        step = _script_step()
        del step["script"]
        result = compile_workflow(_doc([step]), script_registry=table)
        assert not result.ok

    def test_script_fields_on_calculation_step_rejected(self) -> None:
        table = {"demo": _entry()}
        step = {
            "id": "c1",
            "executor": "calculation",
            "bindings": {"structure": {"source": {"run": "structures"}}},
            "calculation": {
                "program": "g16",
                "native": {"keyword": "B3LYP/6-31G* opt"},
                "recovery": {"profile": "none"},
            },
            "args": ["{input}"],
        }
        result = compile_workflow(_doc([step]), script_registry=table)
        assert not result.ok

    def test_binding_non_structures_port_of_script_fails(self) -> None:
        table = {"demo": _entry()}
        producer = _script_step("s1")
        consumer = {
            "id": "s2",
            "executor": "structure_transform",
            "transform": {"kind": "filter"},
            "bindings": {
                "structure": {"source": {"step": "s1", "port": "structures"}},
                "results": {"source": {"step": "s1", "port": "summary"}},
            },
        }
        result = compile_workflow(_doc([producer, consumer]), script_registry=table)
        assert not result.ok
        assert "script_port_not_bindable" in _reasons(result)

    def test_binding_structures_port_of_script_compiles(self) -> None:
        table = {"demo": _entry()}
        producer = _script_step("s1")
        consumer = {
            "id": "s2",
            "executor": "structure_transform",
            "transform": {"kind": "deduplicate"},
            "bindings": {"structure": {"source": {"step": "s1", "port": "structures"}}},
        }
        result = compile_workflow(_doc([producer, consumer]), script_registry=table)
        assert result.ok, [str(item) for item in result.errors]


class TestScriptRegistry:
    def test_loads_registered_entries(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(
            tmp_path,
            {"demo": {"command": [sys.executable, str(script)], "description": "demo"}},
        )
        table = load_script_registry()
        assert sorted(table) == ["demo"]
        entry = table["demo"]
        assert entry.interpreter == sys.executable
        assert entry.script_path == str(script)
        assert entry.sha256 == hashlib.sha256(script.read_bytes()).hexdigest()
        assert entry.description == "demo"

    def test_absent_config_gives_empty_table(self, tmp_path: Path) -> None:
        assert load_script_registry() == {}

    def test_malformed_command_fails_closed(self, tmp_path: Path) -> None:
        (tmp_path / "server.toml").write_text(
            "total_cores = 96\ntotal_memory = '192GB'\n[scripts.demo]\ncommand = 'nope'\n",
            encoding="utf-8",
        )
        with pytest.raises(QuotaError):
            load_script_registry()


class TestScriptExecution:
    def _run_success(self, tmp_path: Path, **kwargs: Any) -> tuple[Any, Path]:
        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        item = _script_item(**kwargs)
        context = _script_context(
            NativeProcessSupervisor(),
            run_root,
            args=("{input}", "{cores}", "{mem_gb}", "{item_id}"),
            outputs={
                "structures": "final.xyz",
                "summary": "summary.json",
                "artifacts": ["*.log"],
            },
        )
        result = ScriptExecutor().execute(item, context)
        return result, run_root

    def test_success_collects_all_channels(self, tmp_path: Path) -> None:
        result, run_root = self._run_success(tmp_path)
        assert result.status.value == "completed", [
            (item.code, item.message) for item in result.diagnostics
        ]
        assert len(result.structures) == 3
        assert len(result.results) == 1
        summary = result.results[0]
        assert summary.kind == "script_summary"
        assert summary.value["status"] == "ok"
        assert summary.value["cores"] == "1"
        assert summary.value["mem_gb"] == "1"
        assert summary.value["input"] == "wi:script-item"
        assert summary.subject_structure_id == "s-script-item"
        assert len(result.artifacts) >= 1
        assert all(item.checksum for item in result.artifacts)
        provenance = dict(result.diagnostics[0].details)
        assert provenance["script_id"] == "demo"
        assert provenance["exit_code"] == 0
        assert (
            provenance["script_sha256"]
            == hashlib.sha256((tmp_path / "demo.py").read_bytes()).hexdigest()
        )
        assert "--id" not in provenance["argv"]
        assert provenance["argv"][-1] == "wi:script-item"

    def test_exit_nonzero_fails_with_stderr_tail(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _FAIL_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        result = ScriptExecutor().execute(
            _script_item(), _script_context(NativeProcessSupervisor(), run_root)
        )
        assert result.status.value == "failed"
        assert result.error is not None and result.error.code == "script_failed"
        details = dict(result.diagnostics[0].details)
        assert details["exit_code"] == 3
        assert "solver exploded" in details["stderr_tail"]
        assert "last line" in details["stderr_tail"]

    def test_missing_declared_output_fails(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", "open('other.txt', 'w').write('x\\n')\n")
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        result = ScriptExecutor().execute(
            _script_item(), _script_context(NativeProcessSupervisor(), run_root)
        )
        assert result.status.value == "failed"
        assert "missing" in result.diagnostics[0].message

    def test_cancel_ends_the_whole_process_group(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _SLEEPER_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        stop = threading.Event()
        box: dict[str, Any] = {}

        def _run() -> None:
            box["result"] = ScriptExecutor().execute(
                _script_item(key="cancel-me"),
                _script_context(NativeProcessSupervisor(), run_root),
                should_cancel=stop.is_set,
            )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        child_pid: int | None = None
        deadline = time.time() + 15
        while time.time() < deadline:
            candidates = list((run_root / "script" / "s1").glob("*/child.pid"))
            if candidates:
                child_pid = int(candidates[0].read_text().strip())
                break
            time.sleep(0.05)
        assert child_pid is not None, "script never launched its child"
        stop.set()
        thread.join(timeout=20)
        assert not thread.is_alive()
        assert box["result"].status.value == "cancelled"
        for _ in range(100):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("cancelled script left its child process alive")

    def test_interrupted_resume_reuses_item_directory(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _MARKER_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        supervisor = NativeProcessSupervisor()
        item = _script_item(key="resume-me")
        context = _script_context(supervisor, run_root)
        first = ScriptExecutor().execute(item, context)
        second = ScriptExecutor().execute(item, context)
        assert first.status.value == "completed"
        assert second.status.value == "completed"
        key_1 = dict(first.diagnostics[0].details)["item_key"]
        key_2 = dict(second.diagnostics[0].details)["item_key"]
        assert key_1 == key_2
        item_dir = run_root / "script" / "s1" / key_1
        assert (item_dir / "input.xyz").is_file()
        assert (item_dir / "calls.marker").read_text().count("call") == 2

    def test_changed_script_content_starts_a_new_task(self, tmp_path: Path) -> None:
        script = _write_script(tmp_path, "demo.py", _MARKER_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        supervisor = NativeProcessSupervisor()
        item = _script_item(key="changed-me")
        first = ScriptExecutor().execute(item, _script_context(supervisor, run_root))
        script.write_text(_MARKER_SCRIPT + "# v2\n", encoding="utf-8")
        second = ScriptExecutor().execute(item, _script_context(supervisor, run_root))
        key_1 = dict(first.diagnostics[0].details)["item_key"]
        key_2 = dict(second.diagnostics[0].details)["item_key"]
        assert key_1 != key_2

    def test_unknown_script_id_fails_closed(self, tmp_path: Path) -> None:
        _write_server_toml(tmp_path, {})
        run_root = tmp_path / "run"
        run_root.mkdir()
        result = ScriptExecutor().execute(
            _script_item(),
            _script_context(NativeProcessSupervisor(), run_root, script_id="ghost"),
        )
        assert result.status.value == "failed"

    def test_three_frame_output_feeds_deduplicate(self, tmp_path: Path) -> None:
        from confflow.domain.work_item import WorkItemInputs as _Inputs
        from confflow.execution.transform_executor import TransformExecutor

        result, _ = self._run_success(tmp_path)
        assert len(result.structures) == 3
        named = _Inputs(structures=FrozenDict({"structure": result.structures}))
        digest = "sha256:" + hashlib.sha256(b"dedup-after-script").hexdigest()
        item = WorkItem(
            id=make_work_item_id("dedup-after-script"),
            logical_key="dedup-after-script",
            step_id="s2",
            named_inputs=named,
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=_GIB),
            semantic_digest=digest,
            ordinal=0,
        )
        context = ItemExecutionContext(
            step_id="s2",
            scientific=ScientificDefinition(transform="deduplicate"),
            scientific_defaults=ScientificDefaults(),
            adapter=None,
            profile=None,
            run_root=str(tmp_path / "run2"),
        )
        deduped = TransformExecutor().execute(item, context)
        assert deduped.status.value == "completed"
        assert len(deduped.structures) == 3

    def test_oversize_summary_fails(self, tmp_path: Path) -> None:
        script = _write_script(
            tmp_path,
            "demo.py",
            "open('summary.json', 'w').write('{\"k\": \"' + 'x' * 2**20 + '\"}')\n"
            "open('final.xyz', 'w').write('1\\nframe\\nHe 0 0 0\\n')\n",
        )
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        result = ScriptExecutor().execute(
            _script_item(),
            _script_context(
                NativeProcessSupervisor(),
                run_root,
                outputs={"structures": "final.xyz", "summary": "summary.json"},
            ),
        )
        assert result.status.value == "failed"
        assert "summary" in result.diagnostics[0].message

    def test_non_object_summary_fails(self, tmp_path: Path) -> None:
        script = _write_script(
            tmp_path,
            "demo.py",
            "open('summary.json', 'w').write('[1, 2]\\n')\n"
            "open('final.xyz', 'w').write('1\\nframe\\nHe 0 0 0\\n')\n",
        )
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        result = ScriptExecutor().execute(
            _script_item(),
            _script_context(
                NativeProcessSupervisor(),
                run_root,
                outputs={"structures": "final.xyz", "summary": "summary.json"},
            ),
        )
        assert result.status.value == "failed"
        assert "JSON object" in result.diagnostics[0].message


class TestScriptControlCapabilities:
    def test_capabilities_lists_registered_scripts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from confflow.control import main

        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(
            tmp_path,
            {"demo": {"command": [sys.executable, str(script)], "description": "demo"}},
        )
        assert main(["capabilities", "--json"]) == 0
        response = json.loads(capsys.readouterr().out)
        assert response["registered_scripts"] == [{"id": "demo", "description": "demo"}]


class TestScriptRelativePathRegression:
    """A1: the executed argv uses the registry-resolved absolute path."""

    def test_relative_command_runs_from_config_dir(self, tmp_path: Path) -> None:
        from confflow.execution.script_executor import ScriptExecutor

        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, "demo.py"]}})
        table = load_script_registry()
        assert table["demo"].script_path == str(script)
        run_root = tmp_path / "run"
        run_root.mkdir()
        item = _script_item(key="rel-item")
        context = _script_context(
            NativeProcessSupervisor(),
            run_root,
            args=("{input}", "{cores}", "{mem_gb}", "{item_id}"),
            outputs={
                "structures": "final.xyz",
                "summary": "summary.json",
                "artifacts": ["*.log"],
            },
        )
        result = ScriptExecutor().execute(item, context)
        assert result.status.value == "completed", [
            (item.code, item.message) for item in result.diagnostics
        ]
        provenance = dict(result.diagnostics[0].details)
        assert provenance["argv"][1] == str(script)
        assert provenance["script_path"] == str(script)
        second = ScriptExecutor().execute(item, context)
        assert dict(second.diagnostics[0].details)["item_key"] == provenance["item_key"]

    def test_single_element_relative_command_runs(self, tmp_path: Path) -> None:
        import stat

        from confflow.execution.script_executor import ScriptExecutor

        body = "#!" + sys.executable + "\nopen('final.xyz', 'w').write('1\\nframe\\nHe 0 0 0\\n')\n"
        script = _write_script(tmp_path, "solo.py", body)
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        _write_server_toml(tmp_path, {"demo": {"command": ["solo.py"]}})
        table = load_script_registry()
        assert table["demo"].script_path == str(script)
        run_root = tmp_path / "run"
        run_root.mkdir()
        result = ScriptExecutor().execute(
            _script_item(key="solo-item"), _script_context(NativeProcessSupervisor(), run_root)
        )
        assert result.status.value == "completed", [
            (item.code, item.message) for item in result.diagnostics
        ]
        assert dict(result.diagnostics[0].details)["argv"][0] == str(script)


class TestScriptArtifactBoundaryRegression:
    """A5: artifact globs stay inside the item directory."""

    def test_dotdot_pattern_rejected_at_compile(self) -> None:
        table = {"demo": _entry()}
        for patterns in (["../evil/*.log"], ["sub/../evil.log"], [".."]):
            step = _script_step()
            step["outputs"] = {"structures": "final.xyz", "artifacts": patterns}
            result = compile_workflow(_doc([step]), script_registry=table)
            assert not result.ok, patterns

    def test_symlink_escape_rejected_at_execution(self, tmp_path: Path) -> None:
        from confflow.execution.script_executor import ScriptExecutor

        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        item = _script_item(key="escape-item")
        context = _script_context(
            NativeProcessSupervisor(),
            run_root,
            args=("{input}", "{cores}", "{mem_gb}", "{item_id}"),
            outputs={
                "structures": "final.xyz",
                "summary": "summary.json",
                "artifacts": ["*.log"],
            },
        )
        result = ScriptExecutor().execute(item, context)
        assert result.status.value == "completed"
        item_key = dict(result.diagnostics[0].details)["item_key"]
        item_dir = run_root / "script" / "s1" / item_key
        (run_root / "sibling.log").write_text("outside\n", encoding="utf-8")
        (item_dir / "evil.log").symlink_to(run_root / "sibling.log")
        executor = ScriptExecutor()
        with pytest.raises(DomainError, match="escapes the item directory"):
            executor._collect_artifacts(
                {"artifacts": ["*.log"]}, str(item_dir), item, context, item_key
            )

    def test_handle_lost_with_cancel_is_unconfirmed(self, tmp_path: Path) -> None:
        from confflow.execution.script_executor import ScriptExecutor

        script = _write_script(tmp_path, "demo.py", _SUCCESS_SCRIPT)
        _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
        run_root = tmp_path / "run"
        run_root.mkdir()
        executor = ScriptExecutor()
        executor._launcher.launch_and_wait = lambda *a, **k: None  # type: ignore[method-assign]
        result = executor.execute(
            _script_item(key="lost-script"),
            _script_context(NativeProcessSupervisor(), run_root),
            should_cancel=lambda: True,
        )
        assert result.status.value == "failed"
        assert result.error is not None
        assert result.error.code == "cancellation_error"
        assert dict(result.diagnostics[0].details)["confirmed"] is False
