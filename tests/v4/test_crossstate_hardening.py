#!/usr/bin/env python3

"""Cross-state hardening regressions (internal third-round red-team).

Three counterexamples proved during the internal cross-state red-team, all
of the "single fix correct, crossed state machines wrong" kind:

CS-1 (security): a remote import attached the COMPLETE worker-measured
execution environment (including producer credentials) to the work-item
result metadata, which the durable store and the published
``step_result.json`` persisted as plaintext producer provenance.

CS-2 (lifecycle): a worker crash before the run manifest, followed by a
durable cancel and a control-worker restart, committed the service
aggregate CANCELLED while the JobDesk-visible ``run_generation.json``
stayed ``running`` forever.  The completion linearization point is the
durable same-generation manifest: when it exists, completion wins and the
service must commit that status; when it does not, the cancel wins and the
generation record must be terminalized.

CS-3 (science): an abandoned attempt's durable bundle was reconciled even
when the current invocation asked for a DIFFERENT execution environment,
publishing the stale-environment result as the new generation's result.
Reconciliation is only legal for the same registered generation axes.

Every attack here uses the formal seams (control worker, V4 application)
and preserves the historical R1-R7 contracts (R7 remote reconciliation
retired in R1.2).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from confflow.application.execution.errors import ErrorCode, ExecutionServiceError
from confflow.application.execution.models import RunState
from confflow.application.execution.workflow_adapter import (
    open_control_service,
)
from confflow.application.v4_run import (
    RUN_RESULT_FILENAME,
)
from confflow.control_worker import run_control_worker
from confflow.persistence.generation import load_run_generation
from tests.v4._helpers.arbitration_state import (
    RUN_ID,
    _control_setup,
    _crash_worker,
)

# ---------------------------------------------------------------------------
# CS-2: durable cancel terminalizes the JobDesk-visible generation
# ---------------------------------------------------------------------------


CRASH_BEFORE_MANIFEST = (
    "import confflow.application.v4_run as v4run\n"
    "v4run.V4RunApplication._publish_manifest = lambda self, **kw: os._exit(17)"
)
CRASH_AFTER_MANIFEST = (
    "import confflow.persistence.arbitration as arbitration\n"
    "_orig_write = arbitration._write_public_generation_locked\n"
    "def _write(root, record):\n"
    "    if record.status != 'running':\n"
    "        os._exit(17)\n"
    "    return _orig_write(root, record)\n"
    "arbitration._write_public_generation_locked = _write\n"
)


class TestCancellationTerminalTruth:
    def test_crash_before_manifest_then_cancel_publishes_cancelled_generation(
        self, tmp_path: Path
    ) -> None:
        os.environ["CF_CS_SCIENCE"] = "-41"
        try:
            fixture = _control_setup(tmp_path, "CF_CS_SCIENCE")
            _crash_worker(tmp_path, fixture, crash=CRASH_BEFORE_MANIFEST)
            work_dir = fixture["work_dir"]
            state_root = fixture["state_root"]
            generation = load_run_generation(str(work_dir))
            assert generation is not None and generation.status == "running"
            service = open_control_service(state_root, identity_executable=sys.executable)
            assert service.status(RUN_ID).state is RunState.RUNNING
            service.cancel(RUN_ID)

            state = run_control_worker(
                state_root=state_root,
                run_id=RUN_ID,
                handoff_path=fixture["handoff_path"],
            )
            assert state is RunState.CANCELLED
            generation_after = load_run_generation(str(work_dir))
            assert generation_after is not None
            assert generation_after.status == "cancelled"
            assert generation_after.manifest_generation_id is None
            assert not (work_dir / RUN_RESULT_FILENAME).is_file()
            # The cancellation is a real terminal no-op afterwards.
            service_after = open_control_service(state_root, identity_executable=sys.executable)
            assert service_after.status(RUN_ID).state is RunState.CANCELLED
        finally:
            os.environ.pop("CF_CS_SCIENCE", None)

    def test_crash_after_manifest_then_cancel_keeps_completed_generation(
        self, tmp_path: Path
    ) -> None:
        os.environ["CF_CS_SCIENCE"] = "-42"
        try:
            fixture = _control_setup(tmp_path, "CF_CS_SCIENCE")
            _crash_worker(tmp_path, fixture, crash=CRASH_AFTER_MANIFEST)
            work_dir = fixture["work_dir"]
            state_root = fixture["state_root"]
            # Manifest linearized completion; the generation record never
            # became terminal because the process died in between.
            manifest = json.loads((work_dir / RUN_RESULT_FILENAME).read_text())
            assert manifest["status"] == "completed"
            generation = load_run_generation(str(work_dir))
            assert generation is not None and generation.status == "running"
            service = open_control_service(state_root, identity_executable=sys.executable)
            # Completion linearized before the cancel: the cancel is refused
            # explicitly (no misleading cancel_requested event) and the
            # crashed worker's recovery projects the manifest winner.
            with pytest.raises(ExecutionServiceError) as rejected:
                service.cancel(RUN_ID)
            assert rejected.value.code is ErrorCode.INVALID_STATE_TRANSITION
            events = [event.type for event in service._repository.read(RUN_ID).events]
            assert "cancel_requested" not in events

            state = run_control_worker(
                state_root=state_root,
                run_id=RUN_ID,
                handoff_path=fixture["handoff_path"],
            )
            assert state is RunState.COMPLETED, "completion linearized before the cancel"
            generation_after = load_run_generation(str(work_dir))
            assert generation_after is not None
            assert generation_after.status == "completed"
            assert generation_after.manifest_generation_id == generation.generation_id
            manifest_after = json.loads((work_dir / RUN_RESULT_FILENAME).read_text())
            assert manifest_after["status"] == "completed"
            service_after = open_control_service(state_root, identity_executable=sys.executable)
            assert service_after.status(RUN_ID).state is RunState.COMPLETED
        finally:
            os.environ.pop("CF_CS_SCIENCE", None)


# ---------------------------------------------------------------------------
# CS-3: environment generation beats abandoned-bundle reconciliation
