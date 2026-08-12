"""Black-box tests for the packageable synthetic fixture executable."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace

import pytest
import rfc8785

from confflow import fixture_agent
from confflow.application.execution.synthetic_producer import (
    SYNTHETIC_ARTIFACT,
    SYNTHETIC_ARTIFACT_PATH,
    SYNTHETIC_ARTIFACT_SCHEMA,
    SYNTHETIC_ARTIFACT_TERMINAL,
)
from confflow.application.execution.workflow_adapter import measure_executable
from confflow.fixture_agent import main as fixture_main


@pytest.fixture(autouse=True)
def _fixture_test_entrypoint(tmp_path: Path, monkeypatch):
    executable = tmp_path / "confflow-fixture-agent.exe"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(executable)])


def _prepare_payload(
    run_id: str, identity: dict[str, str | None] | None = None
) -> dict[str, object]:
    measured = measure_executable(sys.argv[0])
    expected = identity or {
        "sha256": measured.sha256,
        "realpath": measured.realpath,
        "device_inode": measured.device_inode,
    }
    payload: dict[str, object] = {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": run_id,
        "idempotency_key": run_id,
        "workflow_config": {"path": "workflow.yaml", "sha256": "b" * 64},
        "input_manifest": {"path": "inputs/manifest.json", "sha256": "c" * 64},
        "expected_executable_identity": expected,
    }
    payload["request_digest"] = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
    return payload


def _invoke(capsys, args: list[str]) -> dict[str, object]:
    code = fixture_main(args)
    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.splitlines()
    assert len(lines) == 1, captured.out
    assert captured.err == ""
    return json.loads(lines[0])


def _installed_command(name: str) -> Path:
    scripts_directory = Path(sysconfig.get_path("scripts"))
    candidates = [scripts_directory / name]
    if os.name == "nt" and not name.casefold().endswith(".exe"):
        candidates.insert(0, scripts_directory / f"{name}.exe")
    for command in candidates:
        if command.is_file():
            return command
    pytest.skip(f"installed console script is unavailable: {candidates[0]}")


def _windows_os_proxy() -> SimpleNamespace:
    """Patch the fixture module without mutating pytest's process-wide os.name."""
    return SimpleNamespace(
        name="nt",
        path=os.path,
        fspath=os.fspath,
        realpath=os.path.realpath,
        abspath=os.path.abspath,
    )


def test_fixture_entrypoint_windows_fallback_does_not_use_confflow_exe(
    monkeypatch, tmp_path: Path
):
    scripts_directory = tmp_path / "Scripts"
    scripts_directory.mkdir()
    normal = scripts_directory / "confflow.exe"
    fixture = scripts_directory / "confflow-fixture-agent.exe"
    normal.write_bytes(b"normal")
    fixture.write_bytes(b"fixture")

    monkeypatch.setattr(fixture_agent, "os", _windows_os_proxy())
    monkeypatch.setattr(sys, "argv", [str(normal)])
    monkeypatch.setattr(
        fixture_agent.sysconfig, "get_path", lambda scheme: str(scripts_directory)
    )

    assert fixture_agent._actual_entrypoint() == str(fixture.resolve())


@pytest.mark.parametrize("argv0", ["missing.exe", "confflow-fixture-agent"])
def test_fixture_entrypoint_windows_fallback_handles_invalid_or_extensionless_argv(
    monkeypatch, tmp_path: Path, argv0: str
):
    scripts_directory = tmp_path / "Scripts"
    scripts_directory.mkdir()
    fixture = scripts_directory / "confflow-fixture-agent.exe"
    fixture.write_bytes(b"fixture")

    monkeypatch.setattr(fixture_agent, "os", _windows_os_proxy())
    monkeypatch.setattr(sys, "argv", [str(tmp_path / argv0)])
    monkeypatch.setattr(
        fixture_agent.sysconfig, "get_path", lambda scheme: str(scripts_directory)
    )

    assert fixture_agent._actual_entrypoint() == str(fixture.resolve())


def test_fixture_entrypoint_resolution_fails_closed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(fixture_agent, "os", _windows_os_proxy())
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "confflow.exe")])
    monkeypatch.setattr(
        fixture_agent.sysconfig, "get_path", lambda scheme: str(tmp_path / "Scripts")
    )

    assert fixture_agent._actual_entrypoint() is None


def _run_json(command: Path, args: list[str], *, expected_code: int = 0) -> dict[str, object]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(command), *args],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == expected_code, completed.stderr + completed.stdout
    lines = completed.stdout.splitlines()
    assert len(lines) == 1, completed.stdout
    return json.loads(lines[0])


def test_fixture_entrypoint_capabilities_bind_to_actual_executable(
    monkeypatch, capsys, tmp_path: Path
):
    executable = tmp_path / "confflow-fixture-agent.exe"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(executable)])

    assert fixture_main(["--capabilities", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["executable"]["path"] == str(executable.resolve())
    assert payload["executable"]["realpath"] == str(executable.resolve())
    metadata = executable.stat()
    assert payload["executable"]["device_inode"] == f"{metadata.st_dev}:{metadata.st_ino}"
    assert payload["executable"]["sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert payload["executable"]["python"] == os.path.abspath(sys.executable)


def test_fixture_console_script_is_declared_as_a_package_entrypoint():
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'confflow-fixture-agent = "confflow.fixture_agent:main"' in pyproject


@pytest.mark.skipif(os.name == "nt", reason="control state roots are POSIX-only")
def test_fixture_cli_runs_one_json_control_chain_to_fixed_manifest(capsys, tmp_path: Path):
    root = tmp_path / "state"
    run_id = "run-fixture-cli"
    request_path = tmp_path / "prepare.json"
    request_path.write_text(json.dumps(_prepare_payload(run_id)), encoding="utf-8")

    prepared = _invoke(
        capsys,
        [
            "control",
            "prepare",
            "--state-root",
            str(root),
            "--request",
            str(request_path),
            "--json",
        ],
    )
    assert prepared["state"] == "prepared"

    executed = _invoke(
        capsys,
        ["control", "execute", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert executed["state"] == "completed"
    assert executed["revision"] == 5

    status = _invoke(
        capsys,
        ["control", "status", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert status["state"] == "completed"

    events = _invoke(
        capsys,
        ["control", "events", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert [event["type"] for event in events["events"]] == [
        "prepared",
        "queued",
        "running",
        "checkpointed",
        "completed",
    ]
    assert events["next_cursor"] == "r00000000000000000005"

    artifacts = _invoke(
        capsys,
        ["control", "artifacts", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert artifacts["artifacts"] == [
        {
            "terminal": SYNTHETIC_ARTIFACT_TERMINAL,
            "path": SYNTHETIC_ARTIFACT_PATH,
            "sha256": SYNTHETIC_ARTIFACT.sha256,
            "size": SYNTHETIC_ARTIFACT.size,
            "content_schema": SYNTHETIC_ARTIFACT_SCHEMA,
        }
    ]


@pytest.mark.skipif(os.name == "nt", reason="control state roots are POSIX-only")
def test_fixture_cli_cancel_and_resume_use_standard_control_semantics(capsys, tmp_path: Path):
    root = tmp_path / "state"
    run_id = "run-fixture-cancel"
    request_path = tmp_path / "prepare.json"
    request_path.write_text(json.dumps(_prepare_payload(run_id)), encoding="utf-8")

    _invoke(
        capsys,
        [
            "control",
            "prepare",
            "--state-root",
            str(root),
            "--request",
            str(request_path),
            "--json",
        ],
    )
    cancelled = _invoke(
        capsys,
        ["control", "cancel", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert cancelled["state"] == "cancelled"

    code = fixture_main(
        ["control", "resume", "--state-root", str(root), "--run-id", run_id, "--json"]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out)["error"]["code"] == "terminal_run"


def test_fixture_cli_reuses_typed_invalid_request_response(capsys, tmp_path: Path):
    code = fixture_main(
        [
            "control",
            "execute",
            "--state-root",
            str(tmp_path / "state"),
            "--run-id",
            "run-invalid",
            "--unexpected",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out)["error"]["code"] == "invalid_request"


@pytest.mark.skipif(os.name == "nt", reason="control state roots are POSIX-only")
def test_fixture_cli_reuses_typed_error_response_for_unknown_run(capsys, tmp_path: Path):
    code = fixture_main(
        [
            "control",
            "status",
            "--state-root",
            str(tmp_path / "state"),
            "--run-id",
            "missing-run",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert len(captured.out.splitlines()) == 1
    response = json.loads(captured.out)
    assert response["ok"] is False
    assert response["error"]["code"] == "unknown_run"


@pytest.mark.skipif(os.name == "nt", reason="control state roots are POSIX-only")
def test_installed_fixture_drop_in_cli_uses_capability_identity_and_completes(tmp_path: Path):
    fixture = _installed_command("confflow-fixture-agent")
    normal = _installed_command("confflow")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    capability_process = subprocess.run(
        [str(fixture), "--capabilities", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert capability_process.returncode == 0
    capabilities = json.loads(capability_process.stdout)
    executable = capabilities["executable"]
    assert executable["path"] == str(fixture.resolve())
    assert executable["realpath"] == str(fixture.resolve())
    assert executable["device_inode"] == measure_executable(str(fixture)).device_inode

    root = tmp_path / "fixture-state"
    run_id = "run-installed-fixture"
    request_path = tmp_path / "fixture-prepare.json"
    request_path.write_text(
        json.dumps(
            _prepare_payload(
                run_id,
                {
                    "sha256": executable["sha256"],
                    "realpath": executable["realpath"],
                    "device_inode": executable["device_inode"],
                },
            )
        ),
        encoding="utf-8",
    )
    prepared = _run_json(
        fixture,
        [
            "control",
            "prepare",
            "--state-root",
            str(root),
            "--request",
            str(request_path),
            "--json",
        ],
    )
    assert prepared["state"] == "prepared"
    assert (
        _run_json(
            fixture,
            ["control", "execute", "--state-root", str(root), "--run-id", run_id, "--json"],
        )["state"]
        == "completed"
    )
    assert (
        _run_json(
            fixture,
            ["control", "status", "--state-root", str(root), "--run-id", run_id, "--json"],
        )["state"]
        == "completed"
    )
    events = _run_json(
        fixture,
        ["control", "events", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert events["revision"] == 5
    artifacts = _run_json(
        fixture,
        ["control", "artifacts", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert len(artifacts["artifacts"]) == 1

    normal_root = tmp_path / "normal-state"
    normal_run_id = "run-installed-normal"
    normal_identity = measure_executable(sys.executable)
    normal_request = tmp_path / "normal-prepare.json"
    normal_request.write_text(
        json.dumps(
            _prepare_payload(
                normal_run_id,
                {
                    "sha256": normal_identity.sha256,
                    "realpath": normal_identity.realpath,
                    "device_inode": normal_identity.device_inode,
                },
            )
        ),
        encoding="utf-8",
    )
    _run_json(
        normal,
        [
            "control",
            "prepare",
            "--state-root",
            str(normal_root),
            "--request",
            str(normal_request),
            "--json",
        ],
    )
    assert (
        _run_json(
            normal,
            [
                "control",
                "execute",
                "--state-root",
                str(normal_root),
                "--run-id",
                normal_run_id,
                "--json",
            ],
        )["state"]
        == "queued"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows console-script regression only")
def test_installed_fixture_windows_capabilities_bind_to_fixture_script():
    fixture = _installed_command("confflow-fixture-agent")
    normal = _installed_command("confflow")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [str(fixture), "--capabilities", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    executable = json.loads(completed.stdout)["executable"]
    assert executable["path"] == str(fixture.resolve())
    assert executable["realpath"] == str(fixture.resolve())
    assert executable["path"] != str(normal.resolve())
    assert executable["sha256"] == hashlib.sha256(fixture.read_bytes()).hexdigest()


@pytest.mark.skipif(os.name == "nt", reason="control state roots are POSIX-only")
@pytest.mark.parametrize("mismatch", ["python", "other_console", "tampered", "missing"])
def test_installed_fixture_rejects_non_fixture_identity_without_worker(
    tmp_path: Path, mismatch: str
):
    fixture = _installed_command("confflow-fixture-agent")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    capability_process = subprocess.run(
        [str(fixture), "--capabilities", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    capabilities = json.loads(capability_process.stdout)
    executable = capabilities["executable"]
    if mismatch == "python":
        measured = measure_executable(sys.executable)
        identity = {
            "sha256": measured.sha256,
            "realpath": measured.realpath,
            "device_inode": measured.device_inode,
        }
    elif mismatch == "other_console":
        measured = measure_executable(str(_installed_command("confflow")))
        identity = {
            "sha256": measured.sha256,
            "realpath": measured.realpath,
            "device_inode": measured.device_inode,
        }
    elif mismatch == "tampered":
        identity = {
            "sha256": "0" * 64,
            "realpath": executable["realpath"],
            "device_inode": executable["device_inode"],
        }
    else:
        identity = {"sha256": executable["sha256"]}
    root = tmp_path / f"state-{mismatch}"
    run_id = f"run-{mismatch}-identity"
    request_path = tmp_path / f"{mismatch}.json"
    request_path.write_text(json.dumps(_prepare_payload(run_id, identity)), encoding="utf-8")
    response = _run_json(
        fixture,
        [
            "control",
            "prepare",
            "--state-root",
            str(root),
            "--request",
            str(request_path),
            "--json",
        ],
        expected_code=2,
    )
    assert response["error"]["code"] == "executable_identity_mismatch"
    assert not (root.parent / f"run_{run_id}").exists()
    execute = _run_json(
        fixture,
        ["control", "execute", "--state-root", str(root), "--run-id", run_id, "--json"],
        expected_code=2,
    )
    assert execute["error"]["code"] == "unknown_run"
