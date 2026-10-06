#!/usr/bin/env python3

"""Pin the producer-side contract owned by ``confflow.contract``.

These tests act as the guard for the JobDesk<->ConfFlow handshake on the
ConfFlow side: any rename or removal of the names listed in
``confflow.contract.__all__`` is a wire-protocol break and must be
coordinated with the JobDesk consumer.

L2-CF-contract retired the legacy schema/filename constants (their only
production consumer was the ``--capabilities`` announcement itself). The
guard below pins the retired names as absent and the V4 filenames as the
only advertised artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import confflow.cli as cli_module
from confflow import contract


def test_contract_public_api_is_exactly_what_we_expect():
    """The public contract surface is the V4 handshake only."""
    assert contract.__all__ == [
        "CAPABILITY_SCHEMA_VERSION",
        "REQUIRED_COMMANDS",
        "RUN_GENERATION_FILE",
        "RUN_RESULT_FILE",
    ]


def test_legacy_contract_symbols_stay_retired():
    """The L2-retired schema/filename constants must not come back."""
    for retired in (
        "OUTPUT_MANIFEST_SCHEMA",
        "OUTPUT_MANIFEST_SCHEMA_V2",
        "OUTPUT_MANIFEST_FILE",
        "RUN_SUMMARY_SCHEMA",
        "WORKFLOW_STATS_SCHEMA",
        "WORKFLOW_STATS_SCHEMA_V2",
        "WORKFLOW_STATE_SCHEMA",
        "WORKFLOW_STATE_SCHEMA_V2",
        "RUN_SUMMARY_FILE",
        "WORKFLOW_STATS_FILE",
        "WORKFLOW_STATE_FILE",
        "RUN_REPORT_FILE",
        "RUN_MIN_XYZ_TEMPLATE",
    ):
        assert retired not in contract.__all__, f"{retired} must stay retired"
        assert not hasattr(contract, retired), f"contract.{retired} must stay deleted"


def test_capability_schema_version_is_v4():
    """Producer stays at schema_version=4; no strict JD consumer needs a bump."""
    assert contract.CAPABILITY_SCHEMA_VERSION == 4
    assert isinstance(contract.CAPABILITY_SCHEMA_VERSION, int)


def test_artifact_filenames_have_expected_values():
    """Producer-side artifact names are the contract; JobDesk matches these."""
    assert contract.RUN_RESULT_FILE == "run_result.json"
    assert contract.RUN_GENERATION_FILE == "run_generation.json"
    assert contract.REQUIRED_COMMANDS == (
        "bash",
        "nohup",
        "setsid",
        "xargs",
        "sha256sum",
        "mktemp",
        "base64",
    )


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
        "run_result": contract.RUN_RESULT_FILE,
        "run_generation": contract.RUN_GENERATION_FILE,
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
