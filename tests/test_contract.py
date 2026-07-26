#!/usr/bin/env python3

"""Pin the producer-side contract owned by ``confflow.contract``.

These tests act as the guard for the JobDesk<->ConfFlow handshake on the
ConfFlow side: any rename or removal of the names listed in
``confflow.contract.__all__`` is a wire-protocol break and must be
coordinated with the JobDesk consumer.
"""

from __future__ import annotations

import inspect

import confflow.cli as cli_module
import confflow.workflow.export as export_module
import confflow.workflow.presenter as presenter_module
import confflow.workflow.state as state_module
from confflow import contract


def test_contract_public_api_is_exactly_what_we_expect():
    """The public contract surface must be exactly these four names."""
    assert contract.__all__ == [
        "CAPABILITY_SCHEMA_VERSION",
        "RUN_SUMMARY_FILE",
        "WORKFLOW_STATS_FILE",
        "WORKFLOW_STATE_FILE",
    ]


def test_capability_schema_version_is_v2():
    """Producer is locked to schema_version=2; JobDesk rejects any other value."""
    assert contract.CAPABILITY_SCHEMA_VERSION == 2
    assert isinstance(contract.CAPABILITY_SCHEMA_VERSION, int)


def test_artifact_filenames_have_expected_values():
    """Producer-side artifact names are the contract; JobDesk matches these."""
    assert contract.RUN_SUMMARY_FILE == "run_summary.json"
    assert contract.WORKFLOW_STATS_FILE == "workflow_stats.json"
    assert contract.WORKFLOW_STATE_FILE == ".workflow_state.json"


def test_contract_is_not_re_exported_from_package_root():
    """Producer-side contract must NOT be importable from the package root.

    JobDesk imports the protocol *only* through CLI JSON. Re-exporting
    the names from ``confflow`` would tempt consumers to bypass the
    handshake and bind to internal identifiers.
    """
    import confflow

    for name in contract.__all__:
        assert not hasattr(confflow, name), (
            f"confflow.{name} must not be re-exported from the package root"
        )


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
    }
    assert payload["capabilities"] == {
        "workflow_state": True,
        "resume": True,
        "dag": True,
    }


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
