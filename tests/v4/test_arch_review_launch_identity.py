#!/usr/bin/env python3

"""Architecture review R1, R2 and R5: launch environment, MLIP identity, registry binding.

R1  Script/MLIP launches digest the environment they receive, with or without an
    ``execution`` block; session-only variables never move identity.
R2  MLIP reuse identity covers the interpreter, runner, model and refs content and the
    installed MLIP package versions, measured before any reuse decision.
R5  The application compiles with the registry it executes with, and script registration
    is snapshotted once, so the reported definition digest is the offline digest.

Scientific programs are replaced by fake interpreters/runners; no model is loaded.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution import mlip_executor
from confflow.execution.contracts import ExecutorCapability
from confflow.execution.mlip_executor import MODEL_ENV, REFS_ENV, MlipExecutor
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.registry import build_default_registry
from confflow.workflow.v4.assembly import RunInputs
from confflow.workflow.v4.compiler import compile_workflow

_ECHO_ENV_SCRIPT = """\
import os
from pathlib import Path
with open({log!r}, "a", encoding="utf-8") as handle:
    handle.write("run\\n")
value = os.environ.get("REVIEW_LAUNCH_X", "0")
Path("final.xyz").write_text("1\\nscript\\nHe 0 0 " + value + "\\n", encoding="utf-8")
"""

_MLIP_FAKE_RUNNER = """\
import json
from pathlib import Path
job = json.loads(Path("job.json").read_text(encoding="utf-8"))
energy = float(Path(job["model_path"]).read_text(encoding="utf-8").strip())
rows = [
    {"key": item["key"], "stage": "sp", "energy_eh": energy, "n_evals": 1, "wall_s": 0.1}
    for item in job["structures"]
]
Path("mlip_results.jsonl").write_text(
    "".join(json.dumps(row) + "\\n" for row in rows), encoding="utf-8"
)
Path("mlip_done.json").write_text(json.dumps({"model_sha256": "fake", "wall_s": 0.1}))
"""

_TEXT = "1\nlaunch\nHe 0 0 0\n"


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep server config and quota state inside tmp_path."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _register_script(tmp_path: Path, log: Path) -> None:
    script = tmp_path / "echo_env.py"
    script.write_text(_ECHO_ENV_SCRIPT.format(log=str(log)), encoding="utf-8")
    (tmp_path / "server.toml").write_text(
        "total_cores = 96\n"
        "total_memory = '192GB'\n\n"
        "[scripts.echo]\n"
        f"command = {json.dumps([sys.executable, str(script)])}\n",
        encoding="utf-8",
    )


def _script_doc(execution: dict[str, Any] | None = None) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "s",
        "executor": "script",
        "script": "echo",
        "args": [],
        "outputs": {"structures": "final.xyz"},
        "resources": {"cores_per_item": 1, "memory_per_item": "1GB"},
        "bindings": {"structure": {"source": {"run": "structures"}}},
    }
    if execution is not None:
        step["execution"] = execution
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [step],
    }


def _run_script(run_root: Path, document: dict[str, Any]) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(_TEXT)})),
            import_sources=FrozenDict({"structures": _TEXT}),
            run_root=str(run_root),
            executables=FrozenDict(),
        )
    )


def _z(report: Any) -> float:
    return float(report.step_results[0].structures[0].coordinates[0][2])


def _mlip_doc(model: Path, executable: str) -> dict[str, Any]:
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "uma",
                "executor": "mlip",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "mlip": {"model": "uma-s-1p2p1", "mode": "sp"},
                "execution": {
                    "binding_id": "uma",
                    "executable": executable,
                    "env": {MODEL_ENV: str(model)},
                },
            }
        ],
    }


def _install_mlip_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = tmp_path / "fake_mlip_runner.py"
    runner.write_text(_MLIP_FAKE_RUNNER, encoding="utf-8")
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", runner)


def _mlip_energy(report: Any) -> float:
    return float(report.step_results[0].results[0].value)


def _run_mlip(run_root: Path, document: dict[str, Any], registry: Any = None) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor(), registry=registry).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(_TEXT)})),
            import_sources=FrozenDict({"structures": _TEXT}),
            run_root=str(run_root),
            executables=FrozenDict(),
        )
    )


# ---------------------------------------------------------------------------
# R1: launch environment equals digested environment
# ---------------------------------------------------------------------------


class TestScriptLaunchEnvironment:
    def test_env_change_without_execution_block_reruns_with_new_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "runs.log"
        _register_script(tmp_path, log)
        run_root = tmp_path / "run"
        monkeypatch.setenv("REVIEW_LAUNCH_X", "1")
        first = _run_script(run_root, _script_doc())
        assert first.status == "completed"
        assert _z(first) == pytest.approx(1.0)

        monkeypatch.setenv("REVIEW_LAUNCH_X", "2")
        second = _run_script(run_root, _script_doc())
        assert second.status == "completed"
        assert _z(second) == pytest.approx(2.0)
        assert log.read_text(encoding="utf-8").count("run") == 2

    def test_session_only_term_change_reuses_with_explicit_binding(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "runs.log"
        _register_script(tmp_path, log)
        run_root = tmp_path / "run"
        document = _script_doc({"binding_id": "echo", "executable": sys.executable})
        monkeypatch.setenv("TERM", "xterm-256color")
        first = _run_script(run_root, document)
        assert first.status == "completed"
        assert log.read_text(encoding="utf-8").count("run") == 1

        monkeypatch.setenv("TERM", "screen")
        monkeypatch.setenv("COLORTERM", "truecolor")
        second = _run_script(run_root, document)
        assert second.status == "completed"
        assert _z(second) == pytest.approx(_z(first))
        assert log.read_text(encoding="utf-8").count("run") == 1

    def test_declared_non_session_change_with_explicit_binding_reruns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log = tmp_path / "runs.log"
        _register_script(tmp_path, log)
        run_root = tmp_path / "run"
        monkeypatch.delenv("REVIEW_LAUNCH_X", raising=False)
        first = _run_script(
            run_root,
            _script_doc({"binding_id": "echo", "env": {"REVIEW_LAUNCH_X": "3"}}),
        )
        assert _z(first) == pytest.approx(3.0)
        second = _run_script(
            run_root,
            _script_doc({"binding_id": "echo", "env": {"REVIEW_LAUNCH_X": "4"}}),
        )
        assert _z(second) == pytest.approx(4.0)
        assert log.read_text(encoding="utf-8").count("run") == 2

    def test_strip_session_env_drops_only_session_variables(self) -> None:
        from confflow.execution.environment import strip_session_env

        env = {
            "PATH": "/bin",
            "TERM": "xterm",
            "TERM_PROGRAM": "vscode",
            "SSH_CONNECTION": "a b c d",
            "XDG_SESSION_TYPE": "tty",
            "TMUX": "/tmp/tmux",
            "DISPLAY": ":0",
            "_": "/usr/bin/python",
            "PWD": "/work",
            "OMP_NUM_THREADS": "4",
            "REVIEW_LAUNCH_X": "1",
        }
        assert strip_session_env(env) == {
            "PATH": "/bin",
            "OMP_NUM_THREADS": "4",
            "REVIEW_LAUNCH_X": "1",
        }


# ---------------------------------------------------------------------------
# R2: MLIP pre-run identity
# ---------------------------------------------------------------------------


class TestMlipRuntimeIdentity:
    def test_model_file_replaced_at_same_path_is_not_reused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mlip_runner(monkeypatch, tmp_path)
        model = tmp_path / "uma.pt"
        model.write_text("-76.0\n", encoding="utf-8")
        run_root = tmp_path / "run"
        document = _mlip_doc(model, sys.executable)
        first = _run_mlip(run_root, document)
        assert first.status == "completed"
        assert _mlip_energy(first) == pytest.approx(-76.0)

        model.write_text("-80.00\n", encoding="utf-8")
        second = _run_mlip(run_root, document)
        assert second.status == "completed"
        assert _mlip_energy(second) == pytest.approx(-80.0)

    def test_interpreter_replaced_by_missing_path_is_not_reported_completed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mlip_runner(monkeypatch, tmp_path)
        model = tmp_path / "uma.pt"
        model.write_text("-76.0\n", encoding="utf-8")
        run_root = tmp_path / "run"
        first = _run_mlip(run_root, _mlip_doc(model, sys.executable))
        assert first.status == "completed"

        missing = str(tmp_path / "no-such-python")
        try:
            second = _run_mlip(run_root, _mlip_doc(model, missing))
        except DomainError:
            return
        assert second.status != "completed"
        assert second.step_results[0].status.value != "completed"


# ---------------------------------------------------------------------------
# R5: one registry authority and one script snapshot per generation
# ---------------------------------------------------------------------------


class TestCompileUsesApplicationRegistry:
    def test_custom_mlip_contract_version_is_the_reported_definition_digest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mlip_runner(monkeypatch, tmp_path)
        model = tmp_path / "uma.pt"
        model.write_text("-76.0\n", encoding="utf-8")
        document = _mlip_doc(model, sys.executable)

        registry = build_default_registry()
        contract = registry.resolve_executor(ExecutorCapability.MLIP)
        registry.register_executor(
            dataclasses.replace(contract, contract_version="review.mlip.v999"),
            MlipExecutor,
        )
        offline = compile_workflow(document, registry=registry)
        default_offline = compile_workflow(document)
        assert offline.ok and default_offline.ok
        assert offline.plan is not None and default_offline.plan is not None
        assert offline.plan.definition_digest != default_offline.plan.definition_digest

        report = _run_mlip(tmp_path / "run", document, registry=registry)
        assert report.definition_digest == offline.plan.definition_digest

    def test_script_registry_is_snapshotted_for_script_workflows(self, tmp_path: Path) -> None:
        from confflow.application.v4_run import _script_registry_snapshot
        from confflow.workflow.v4.parser import parse_workflow_document

        log = tmp_path / "runs.log"
        _register_script(tmp_path, log)
        with_script = parse_workflow_document(_script_doc())
        snapshot = _script_registry_snapshot(with_script)
        assert snapshot is not None and "echo" in snapshot
        without_script = parse_workflow_document(
            {
                "schema": "confflow.workflow.v4",
                "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
                "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
                "steps": [],
            }
        )
        assert _script_registry_snapshot(without_script) is None

    def test_unloadable_script_registry_is_left_to_the_compiler(self, tmp_path: Path) -> None:
        from confflow.application.v4_run import _script_registry_snapshot
        from confflow.workflow.v4.parser import parse_workflow_document

        (tmp_path / "server.toml").write_text("[scripts]\necho = 3\n", encoding="utf-8")
        assert _script_registry_snapshot(parse_workflow_document(_script_doc())) is None


# ---------------------------------------------------------------------------
# Environment measurement helpers (fail-closed and cache branches)
# ---------------------------------------------------------------------------


def _fake_interpreter(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


class TestMlipRuntimeIdentityHelper:
    @staticmethod
    def _helper() -> Any:
        from confflow.execution import environment

        return environment.mlip_runtime_identity

    def _inputs(self, tmp_path: Path) -> tuple[str, str, dict[str, str]]:
        runner = tmp_path / "runner.py"
        runner.write_text("print('runner')\n", encoding="utf-8")
        model = tmp_path / "model.pt"
        model.write_text("1.0", encoding="utf-8")
        env = {MODEL_ENV: str(model)}
        return sys.executable, str(runner), env

    def test_identity_is_stable_and_well_formed(self, tmp_path: Path) -> None:
        interpreter, runner, env = self._inputs(tmp_path)
        first = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        second = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert first is not None and first.startswith("sha256:")
        assert first == second

    def test_refs_content_and_model_content_move_identity(self, tmp_path: Path) -> None:
        interpreter, runner, env = self._inputs(tmp_path)
        refs = tmp_path / "refs.json"
        refs.write_text("{}", encoding="utf-8")
        base = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        with_refs = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env={**env, REFS_ENV: str(refs)},
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        refs.write_text('{"changed": true}', encoding="utf-8")
        changed_refs = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env={**env, REFS_ENV: str(refs)},
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert base != with_refs != changed_refs

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda env, tmp: {},  # model variable missing
            lambda env, tmp: {MODEL_ENV: "relative/model.pt"},  # relative model path
            lambda env, tmp: {MODEL_ENV: str(tmp / "absent.pt")},  # model file missing
            lambda env, tmp: {MODEL_ENV: str(tmp), REFS_ENV: "rel/refs.json"},  # bad refs
        ],
    )
    def test_unresolvable_inputs_yield_none(self, tmp_path: Path, mutate: Any) -> None:
        interpreter, runner, env = self._inputs(tmp_path)
        identity = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env=mutate(env, tmp_path),
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert identity is None

    def test_missing_interpreter_and_runner_yield_none(self, tmp_path: Path) -> None:
        _, runner, env = self._inputs(tmp_path)
        missing = str(tmp_path / "missing-python")
        assert (
            self._helper()(
                interpreter=missing,
                runner_path=runner,
                env=env,
                model_env=MODEL_ENV,
                refs_env=REFS_ENV,
            )
            is None
        )
        assert (
            self._helper()(
                interpreter="/no/such/dir/python",
                runner_path=runner,
                env=env,
                model_env=MODEL_ENV,
                refs_env=REFS_ENV,
            )
            is None
        )
        assert (
            self._helper()(
                interpreter=sys.executable,
                runner_path=str(tmp_path / "gone.py"),
                env=env,
                model_env=MODEL_ENV,
                refs_env=REFS_ENV,
            )
            is None
        )

    def test_shell_metacharacter_interpreter_yields_none(self, tmp_path: Path) -> None:
        _, runner, env = self._inputs(tmp_path)
        assert (
            self._helper()(
                interpreter="python3;rm",
                runner_path=runner,
                env=env,
                model_env=MODEL_ENV,
                refs_env=REFS_ENV,
            )
            is None
        )

    def test_directory_model_yields_none(self, tmp_path: Path) -> None:
        interpreter, runner, _ = self._inputs(tmp_path)
        directory = tmp_path / "model-dir"
        directory.mkdir()
        identity = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env={MODEL_ENV: str(directory)},
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert identity is None

    @pytest.mark.parametrize(
        "body",
        [
            "exit 3\n",
            "echo not-json\n",
            "echo '[]'\n",
            'echo \'{"fairchem-core": 1, "torch": null, "ase": null, "numpy": null}\'\n',
            "echo '{\"fairchem-core\": null}'\n",
        ],
    )
    def test_broken_version_probe_yields_none(self, tmp_path: Path, body: str) -> None:
        interpreter = _fake_interpreter(tmp_path, "probe-python", body)
        _, runner, env = self._inputs(tmp_path)
        identity = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert identity is None

    def test_non_executable_interpreter_yields_none(self, tmp_path: Path) -> None:
        # A regular file without the execute bit: the version probe cannot start it.
        plain = tmp_path / "not-executable-python"
        plain.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        plain.chmod(0o644)
        _, runner, env = self._inputs(tmp_path)
        identity = self._helper()(
            interpreter=str(plain),
            runner_path=runner,
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert identity is None

    def test_bare_interpreter_name_not_on_path_yields_none(self, tmp_path: Path) -> None:
        _, runner, env = self._inputs(tmp_path)
        identity = self._helper()(
            interpreter="confflow-no-such-interpreter-xyz",
            runner_path=runner,
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert identity is None

    def test_unreadable_model_during_hash_yields_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.execution import environment

        interpreter, runner, env = self._inputs(tmp_path)
        real_open = open
        model_path = env[MODEL_ENV]

        def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
            if str(file) == model_path:
                raise OSError("simulated read failure")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(environment, "open", failing_open, raising=False)
        identity = self._helper()(
            interpreter=interpreter,
            runner_path=runner,
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )
        assert identity is None

    def test_unresolved_identity_is_unique_and_never_a_sha256_digest(self) -> None:
        from confflow.execution.environment import unresolved_runtime_identity

        first = unresolved_runtime_identity()
        second = unresolved_runtime_identity()
        assert first != second
        assert first.startswith("unresolved:")
