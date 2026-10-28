#!/usr/bin/env python3

"""Architecture follow-up F1 and F3: MLIP interpreter policy before probes, probe fidelity.

F1  The MLIP interpreter passes the full execution policy (allow list and sandbox, real
    path) before any version probe or computation starts.  A forbidden interpreter leaves
    no trace: its marker file never exists.
F3  The MLIP package-version probe starts the interpreter the way the runner does (no
    ``-I``, the frozen native environment, an empty temporary cwd), and nothing is cached
    across measurements, so an in-place metadata change under the same interpreter moves
    the identity and a ``PYTHONPATH`` override is what the launch actually sees.

Only package metadata directories are created; no numpy, torch or MLIP model is installed
or executed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution import environment, mlip_executor
from confflow.execution.mlip_executor import MODEL_ENV, REFS_ENV
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.run_state import load_run_state
from confflow.workflow.v4.assembly import RunInputs

_TEXT = "1\nlaunch\nHe 0 0 0\n"

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


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep server config and quota state inside tmp_path."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _marker_interpreter(tmp_path: Path, marker: Path) -> str:
    """Write a fake interpreter that records that it ran, then prints an empty version map."""
    path = tmp_path / "bin" / "fake-mlip-python"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        f"touch {marker}\n"
        'echo \'{"fairchem-core": "9.9", "torch": "9.9", "ase": "9.9", "numpy": "9.9"}\'\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return str(path)


def _mlip_doc(model: Path, execution: dict[str, Any]) -> dict[str, Any]:
    execution = {"binding_id": "uma", "env": {MODEL_ENV: str(model)}, **execution}
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
                "execution": execution,
            }
        ],
    }


def _run_mlip(run_root: Path, document: dict[str, Any]) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(_TEXT)})),
            import_sources=FrozenDict({"structures": _TEXT}),
            run_root=str(run_root),
            executables=FrozenDict(),
        )
    )


def _install_mlip_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = tmp_path / "fake_mlip_runner.py"
    runner.write_text(_MLIP_FAKE_RUNNER, encoding="utf-8")
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", runner)


def _model(tmp_path: Path) -> Path:
    model = tmp_path / "uma.pt"
    model.write_text("-76.0\n", encoding="utf-8")
    return model


def _write_numpy_metadata(site: Path, version: str) -> None:
    """Create a minimal ``numpy`` distribution in *site* (metadata only, no package)."""
    dist = site / f"numpy-{version}.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: numpy\nVersion: {version}\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# F1: the policy runs before any probe, and a forbidden interpreter never starts
# ---------------------------------------------------------------------------


class TestMlipInterpreterPolicyBeforeProbe:
    def test_allow_list_refusal_never_starts_the_interpreter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mlip_runner(monkeypatch, tmp_path)
        marker = tmp_path / "fake-interpreter-ran"
        fake = _marker_interpreter(tmp_path, marker)
        run_root = tmp_path / "run"
        document = _mlip_doc(
            _model(tmp_path),
            {"executable": fake, "allowed_executables": [sys.executable]},
        )

        with pytest.raises(DomainError, match="not allowed by allowed_executables"):
            _run_mlip(run_root, document)

        assert not marker.exists()
        state = load_run_state(str(run_root))
        assert state is not None
        assert state.step("uma").status.value == "failed"

    def test_sandbox_refusal_never_starts_the_interpreter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mlip_runner(monkeypatch, tmp_path)
        marker = tmp_path / "fake-interpreter-ran"
        fake = _marker_interpreter(tmp_path, marker)
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        document = _mlip_doc(_model(tmp_path), {"executable": fake, "sandbox": str(sandbox)})

        with pytest.raises(DomainError, match="escapes sandbox"):
            _run_mlip(tmp_path / "outside-run", document)

        assert not marker.exists()

    def test_allowed_real_interpreter_passes_policy_and_completes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_mlip_runner(monkeypatch, tmp_path)
        document = _mlip_doc(
            _model(tmp_path),
            {"executable": sys.executable, "allowed_executables": [sys.executable]},
        )

        report = _run_mlip(tmp_path / "run", document)

        assert report.status == "completed"
        assert float(report.step_results[0].results[0].value) == pytest.approx(-76.0)


# ---------------------------------------------------------------------------
# F3: the probe mirrors the launch and is never cached
# ---------------------------------------------------------------------------


class TestMlipVersionProbeFidelity:
    @staticmethod
    def _identity(runner: Path, env: dict[str, str]) -> str | None:
        return environment.mlip_runtime_identity(
            interpreter=sys.executable,
            runner_path=str(runner),
            env=env,
            model_env=MODEL_ENV,
            refs_env=REFS_ENV,
        )

    def test_same_process_metadata_upgrade_changes_identity(self, tmp_path: Path) -> None:
        site = tmp_path / "site"
        _write_numpy_metadata(site, "1.0")
        runner = tmp_path / "runner.py"
        runner.write_text("print('runner')\n", encoding="utf-8")
        env = {MODEL_ENV: str(_model(tmp_path)), "PYTHONPATH": str(site)}

        before = self._identity(runner, env)
        assert before is not None

        # upgrade the package metadata in place; the interpreter binary is unchanged
        shutil.rmtree(site / "numpy-1.0.dist-info")
        _write_numpy_metadata(site, "2.0")

        after = self._identity(runner, env)
        assert after is not None
        assert after != before

    def test_pythonpath_override_is_what_the_probe_and_the_launch_both_see(
        self, tmp_path: Path
    ) -> None:
        runner = tmp_path / "runner.py"
        runner.write_text("print('runner')\n", encoding="utf-8")
        model = str(_model(tmp_path))
        site = tmp_path / "site"
        _write_numpy_metadata(site, "9.0")
        override = {MODEL_ENV: model, "PYTHONPATH": str(site)}
        plain = {MODEL_ENV: model}

        # the override must reach the probe: its identity differs from the plain environment
        assert self._identity(runner, override) != self._identity(runner, plain)

        # and the probed numpy version is the one the launch with that environment sees
        launched = subprocess.run(
            [sys.executable, "-c", "import importlib.metadata as m; print(m.version('numpy'))"],
            capture_output=True,
            text=True,
            check=True,
            env=dict(override),
            cwd=tmp_path,
        ).stdout.strip()
        assert launched == "9.0"
        probed = environment._interpreter_package_versions(sys.executable, override)
        assert probed["numpy"] == launched
