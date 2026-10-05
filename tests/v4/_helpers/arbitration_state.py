"""Shared cross-state arbitration setup helpers (moved verbatim from test_crossstate_hardening)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from confflow.application.execution.models import PrepareRequest, RunState
from confflow.application.execution.workflow_adapter import (
    measure_executable,
    open_control_service,
)
from confflow.worker_handoff import HANDOFF_SCHEMA, _canonical_json

from .audit_native import REPO_ROOT, WATER_XYZ, _science_native

RUN_ID = "crossstate-run"


def _native(root: Path, variable: str) -> Path:
    script = _science_native(root)
    script.write_text(script.read_text().replace("SCIENCE_ENV", variable))
    return script


def _control_setup(tmp_path: Path, variable: str) -> dict[str, Any]:
    import copy

    from confflow.producer import get_recipe_v4

    script = _native(tmp_path, variable)
    config = tmp_path / "workflow.json"
    doc = copy.deepcopy(get_recipe_v4("tspes")["document"])
    doc["steps"] = doc["steps"][:1]
    doc["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    doc["steps"][0]["execution"] = {"executable": str(script)}
    config.write_text(json.dumps(doc), encoding="utf-8")
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text(WATER_XYZ, encoding="utf-8")
    work_dir = tmp_path / "results" / "run_work"
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": RUN_ID,
        "workflow_config": {
            "path": str(config),
            "sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        },
        "tasks": [
            {
                "task_id": "water",
                "input_xyz": str(input_xyz),
                "work_dir": str(work_dir),
                "sha256": hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_bytes(_canonical_json(handoff))
    state_root = tmp_path / "state"
    state_root.mkdir()
    os.chmod(state_root, 0o700)
    service = open_control_service(state_root, identity_executable=sys.executable)
    service.prepare(
        PrepareRequest(
            run_id=RUN_ID,
            idempotency_key=RUN_ID,
            request_digest="a" * 64,
            workflow_config_digest=handoff["workflow_config"]["sha256"],
            input_manifest_digest=hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
            expected_executable_identity=measure_executable(sys.executable),
        )
    )
    assert service.execute(RUN_ID).state is RunState.QUEUED
    return {
        "script": script,
        "work_dir": work_dir,
        "handoff_path": handoff_path,
        "state_root": state_root,
    }


def _crash_worker(tmp_path: Path, fixture: dict[str, Any], *, crash: str) -> None:
    runner = tmp_path / f"crash_worker_{abs(hash(crash)) % 10000}.py"
    runner.write_text(
        "import os, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        + crash
        + "\nfrom confflow.control_worker import run_control_worker\n"
        + "run_control_worker(\n"
        + f"    state_root={str(fixture['state_root'])!r},\n"
        + f"    run_id={RUN_ID!r},\n"
        + f"    handoff_path={str(fixture['handoff_path'])!r},\n"
        + ")\n"
    )
    crashed = subprocess.run(
        [sys.executable, str(runner)],
        env=dict(os.environ),  # identical env for the crashed attempt and the retry
        capture_output=True,
        text=True,
        timeout=300,
        start_new_session=True,
    )
    assert crashed.returncode == 17, crashed.stderr[-3000:]
