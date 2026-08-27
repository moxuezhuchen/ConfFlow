#!/usr/bin/env python3

"""Pin the producer-side contract owned by ``confflow.contract``.

These tests act as the guard for the JobDesk<->ConfFlow handshake on the
ConfFlow side: any rename or removal of the names listed in
``confflow.contract.__all__`` is a wire-protocol break and must be
coordinated with the JobDesk consumer.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import confflow.cli as cli_module
import confflow.workflow.export as export_module
import confflow.workflow.presenter as presenter_module
import confflow.workflow.state as state_module
from confflow import contract


def test_contract_public_api_is_exactly_what_we_expect():
    """The public contract surface must include all versioned artifact schemas."""
    assert contract.__all__ == [
        "OUTPUT_MANIFEST_SCHEMA",
        "OUTPUT_MANIFEST_FILE",
        "CAPABILITY_SCHEMA_VERSION",
        "RUN_SUMMARY_SCHEMA",
        "WORKFLOW_STATS_SCHEMA",
        "WORKFLOW_STATE_SCHEMA",
        "RUN_SUMMARY_FILE",
        "WORKFLOW_STATS_FILE",
        "WORKFLOW_STATE_FILE",
        "RUN_REPORT_FILE",
        "RUN_MIN_XYZ_TEMPLATE",
        "REQUIRED_COMMANDS",
    ]


def test_capability_schema_version_is_v4():
    """Producer is locked to schema_version=4; JobDesk rejects any other value."""
    assert contract.CAPABILITY_SCHEMA_VERSION == 4
    assert isinstance(contract.CAPABILITY_SCHEMA_VERSION, int)


def test_artifact_filenames_have_expected_values():
    """Producer-side artifact names are the contract; JobDesk matches these."""
    assert contract.RUN_SUMMARY_FILE == "run_summary.json"
    assert contract.WORKFLOW_STATS_FILE == "workflow_stats.json"
    assert contract.WORKFLOW_STATE_FILE == ".workflow_state.json"
    assert contract.RUN_REPORT_FILE == "{basename}.txt"
    assert contract.RUN_MIN_XYZ_TEMPLATE == "{basename}min.xyz"
    assert contract.REQUIRED_COMMANDS == (
        "bash",
        "nohup",
        "setsid",
        "xargs",
        "sha256sum",
        "mktemp",
        "base64",
    )
    assert contract.RUN_SUMMARY_SCHEMA == "confflow.run_summary.v1"
    assert contract.WORKFLOW_STATS_SCHEMA == "confflow.workflow_stats.v1"
    assert contract.WORKFLOW_STATE_SCHEMA == "confflow.workflow_state.v1"


def test_contract_is_not_re_exported_from_package_root():
    """Producer-side contract must NOT be importable from the package root.

    JobDesk imports the protocol *only* through CLI JSON. Re-exporting
    the names from ``confflow`` would tempt consumers to bypass the
    handshake and bind to internal identifiers.
    """
    import confflow

    for name in contract.__all__:
        assert not hasattr(
            confflow, name
        ), f"confflow.{name} must not be re-exported from the package root"


def test_cli_capability_payload_uses_contract_constants():
    """The CLI handshake payload must source schema and artifact constants.

    This keeps the producer contract fields from drifting apart.
    """
    payload = cli_module._CAPABILITY_PAYLOAD
    assert payload["schema_version"] == contract.CAPABILITY_SCHEMA_VERSION
    assert payload["artifacts"] == {
        "run_summary": contract.RUN_SUMMARY_FILE,
        "workflow_stats": contract.WORKFLOW_STATS_FILE,
        "workflow_state": contract.WORKFLOW_STATE_FILE,
        "run_report": contract.RUN_REPORT_FILE,
        "min_xyz": contract.RUN_MIN_XYZ_TEMPLATE,
        "output_manifest": contract.OUTPUT_MANIFEST_FILE,
    }
    assert set(payload["commands"]) == set(contract.REQUIRED_COMMANDS)
    assert all(isinstance(value, bool) for value in payload["commands"].values())
    assert payload["build"] == {"commit": None, "dirty": None}
    assert payload["capabilities"] == {
        "workflow_state": True,
        "resume": True,
        "dag": True,
        "control_worker": (
            os.name == "posix" and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
        ),
    }
    assert payload["producer"] == {
        "package": "confflow",
        "version": payload["version"],
        "build": payload["build"],
        "wheel": {"filename": None, "sha256": None},
        "install_provenance": {"status": "missing", "reason_code": "missing_file"},
    }
    assert set(payload["executable"]) == {
        "path",
        "realpath",
        "device_inode",
        "sha256",
        "python",
    }
    assert "unbound" not in json.dumps(
        payload
    ), 'Producer must not emit the literal "unbound" placeholder'


def test_capability_executable_identity_binds_to_invoked_venv(tmp_path, monkeypatch):
    import confflow.cli as cli_module

    venv = tmp_path / "confflow-1.4.5-candidate"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    python = bin_dir / "python"
    executable = bin_dir / "confflow"
    python.write_text("python", encoding="utf-8")
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(cli_module.sys, "executable", str(python))
    monkeypatch.setattr(cli_module.sys, "argv", [str(executable), "--capabilities", "--json"])
    monkeypatch.setattr(cli_module.sys, "prefix", str(venv))

    payload = cli_module._build_capability_payload()
    assert payload["executable"]["path"] == str(executable.resolve())
    assert payload["executable"]["realpath"] == str(executable.resolve())
    metadata = executable.stat()
    assert payload["executable"]["device_inode"] == f"{metadata.st_dev}:{metadata.st_ino}"
    assert payload["executable"]["python"] == str(python)
    assert Path(payload["executable"]["path"]).is_relative_to(venv)
    assert Path(payload["executable"]["python"]).is_relative_to(venv)


def test_resolved_executable_prefers_reported_windows_exe_over_path(tmp_path, monkeypatch):
    """A launcher-reported path must win over another same-named PATH entry."""
    reported = tmp_path / "confflow"
    invoked = reported.with_name("confflow.exe")
    reported.write_text("launcher metadata", encoding="utf-8")
    invoked.write_bytes(b"MZ invoked launcher")
    path_copy = tmp_path / "other" / "confflow.exe"
    path_copy.parent.mkdir()
    path_copy.write_bytes(b"MZ PATH copy")
    python = tmp_path / "python.exe"
    python.write_bytes(b"python")

    host_os_name = os.name
    monkeypatch.setattr(
        cli_module,
        "os",
        SimpleNamespace(name="nt", fspath=os.fspath, path=os.path),
    )
    monkeypatch.setattr(cli_module.sys, "argv", [str(reported), "--capabilities", "--json"])
    monkeypatch.setattr(cli_module.sys, "executable", str(python))
    monkeypatch.setattr(cli_module.shutil, "which", lambda name: str(path_copy))

    assert cli_module._resolved_confflow_executable() == str(invoked.resolve())
    assert os.name == host_os_name
    assert Path(os.fspath(tmp_path)).is_dir()


def test_executable_resolution_keeps_posix_reported_path_before_exe_sibling(tmp_path, monkeypatch):
    """POSIX launchers must never reinterpret a real no-suffix path as .exe."""
    reported = tmp_path / "confflow"
    sibling = reported.with_name("confflow.exe")
    reported.write_text("POSIX launcher", encoding="utf-8")
    sibling.write_bytes(b"not the POSIX launcher")
    host_os_name = os.name
    monkeypatch.setattr(
        cli_module,
        "os",
        SimpleNamespace(name="posix", fspath=os.fspath, path=os.path),
    )

    assert cli_module._resolve_existing_executable(reported) == str(reported.resolve())
    assert os.name == host_os_name
    assert Path(os.fspath(tmp_path)).is_dir()


def test_presenter_uses_contract_filenames():
    """The presenter writes the filenames declared in the contract.

    The actual literal bytes are not allowed in module source — the
    contract must be referenced symbolically — so this test pins that
    behaviour by importing the presenter and pointing it at a temp dir,
    then asserting the on-disk filenames are exactly the contract.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        presenter_module.write_final_statistics(tmp, {"steps": [], "input_files": []})
        from pathlib import Path

        assert (Path(tmp) / contract.RUN_SUMMARY_FILE).exists()
        assert (Path(tmp) / contract.WORKFLOW_STATS_FILE).exists()

    # The presenter module must import the contract names (no string literals).
    src = inspect.getsource(presenter_module)
    assert "from ..contract import" in src
    assert "RUN_SUMMARY_FILE" in src
    assert "WORKFLOW_STATS_FILE" in src


def test_state_uses_contract_filename():
    """The workflow state file path is declared in the contract."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = state_module.WorkflowStateStore(tmp)
        assert store.path.endswith(contract.WORKFLOW_STATE_FILE), (
            f"WorkflowStateStore.path must end with {contract.WORKFLOW_STATE_FILE}, "
            f"got {store.path}"
        )

    src = inspect.getsource(state_module)
    assert "from ..contract import" in src
    assert "WORKFLOW_STATE_FILE" in src


def test_export_uses_contract_filenames():
    """The export step meta-loader must consult the contract filenames."""
    import json
    import tempfile
    from pathlib import Path

    # Exercise the real loader against a temp dir using the contract names.
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / contract.WORKFLOW_STATS_FILE).write_text(
            json.dumps({"steps": [{"name": "s1", "index": 1}]})
        )
        meta = export_module._load_step_meta(tmp)
        assert meta.names == {"s1": "s1"}
        assert meta.order == {"s1": 1}

    src = inspect.getsource(export_module)
    assert "from confflow.contract import" in src
    assert contract.RUN_SUMMARY_FILE not in src, "literal filename must not appear in source"
    assert contract.WORKFLOW_STATS_FILE not in src, "literal filename must not appear in source"
