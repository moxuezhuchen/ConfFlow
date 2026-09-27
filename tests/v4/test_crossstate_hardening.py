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

Every attack here uses the formal seams (control worker, remote transport,
V4 application) and preserves the historical R1-R7 contracts.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from confflow.application.execution.models import PrepareRequest, RunState
from confflow.application.execution.workflow_adapter import (
    measure_executable,
    open_control_service,
)
from confflow.application.v4_run import (
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.control_worker import run_control_worker
from confflow.domain import FrozenDict
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import store_path
from confflow.persistence.generation import load_run_generation
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.remote.transport import RemoteTransport
from confflow.worker_handoff import HANDOFF_SCHEMA, _canonical_json
from confflow.workflow.v4.assembly import RunInputs
from tests.v4.test_audit_regressions_r2 import (
    REPO_ROOT,
    WATER_XYZ,
    _digest_over,
    _launches,
    _science_native,
    _single_step_doc,
    _stored_environment_digest,
)

RUN_ID = "crossstate-run"

#: Producer credentials that must never become durable provenance.
SECRETS = {
    "GITHUB_TOKEN": "SECRET_GH_CS",
    "OPENAI_API_KEY": "SECRET_OPENAI_CS",
    "AWS_SECRET_ACCESS_KEY": "SECRET_AWS_CS",
    "HTTPS_PROXY": "http://user:password@example.invalid",
}


def _inputs() -> RunInputs:
    return RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)}))


def _native(root: Path, variable: str) -> Path:
    script = _science_native(root)
    script.write_text(script.read_text().replace("SCIENCE_ENV", variable))
    return script


# ---------------------------------------------------------------------------
# CS-1: remote provenance never persists producer secret plaintext
# ---------------------------------------------------------------------------


class TestRemoteSecretProvenance:
    def test_remote_run_persists_digest_not_secret_plaintext(self, tmp_path: Path) -> None:
        os.environ.update(SECRETS)
        os.environ["CF_CS_SCIENCE"] = "-12.3"
        try:
            script = _native(tmp_path, "CF_CS_SCIENCE")
            run_root = tmp_path / "run"
            worker_root = tmp_path / "worker"
            doc = _single_step_doc(script)
            doc["steps"][0]["execution"]["target"] = "cluster-cs"
            with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
                transport = RemoteTransport(
                    run_root=str(run_root),
                    store=store,
                    worker_root=str(worker_root),
                    target_name="cluster-cs",
                )
                report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(
                        workflow_document=doc,
                        run_inputs=_inputs(),
                        run_root=str(run_root),
                        import_sources=FrozenDict({"structures": WATER_XYZ}),
                        transport=transport,
                    )
                )
            assert report.status == "completed"
            assert report.step_results[0].results[0].value == -12.3

            # The scientific identity digest is still recorded and still
            # recomputes from the launched environment.
            received = json.loads((tmp_path / "science-record").read_text())["env"]
            assert _stored_environment_digest(run_root, "ts") == _digest_over(script, received)

            # No producer durable byte may carry a secret plaintext.
            for path in sorted(run_root.rglob("*")):
                if not path.is_file():
                    continue
                data = path.read_bytes()
                for name, secret in SECRETS.items():
                    assert secret.encode() not in data, (
                        f"{name} plaintext persisted in durable run-root file "
                        f"{path.relative_to(run_root)}"
                    )

            # Reuse is unaffected: an identical second invocation relaunches
            # nothing and keeps the same value.
            with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
                transport = RemoteTransport(
                    run_root=str(run_root),
                    store=store,
                    worker_root=str(worker_root),
                    target_name="cluster-cs",
                )
                report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(
                        workflow_document=doc,
                        run_inputs=_inputs(),
                        run_root=str(run_root),
                        import_sources=FrozenDict({"structures": WATER_XYZ}),
                        transport=transport,
                    )
                )
            assert report.status == "completed"
            assert report.step_results[0].results[0].value == -12.3
            assert _launches(tmp_path) == 1, "reuse after redaction must not relaunch"
        finally:
            for name in (*SECRETS, "CF_CS_SCIENCE"):
                os.environ.pop(name, None)


# ---------------------------------------------------------------------------
# CS-2: durable cancel terminalizes the JobDesk-visible generation
# ---------------------------------------------------------------------------


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
        env={**os.environ, "PATH": "/opt/ConfFlow/.venv/bin:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=300,
        start_new_session=True,
    )
    assert crashed.returncode == 17, crashed.stderr[-3000:]


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
            service.cancel(RUN_ID)

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
# ---------------------------------------------------------------------------


def _crash_after_bundle(tmp_path: Path, variable: str, env_value: str) -> None:
    runner = tmp_path / "crash_after_bundle.py"
    runner.write_text(textwrap.dedent(f"""\
            import copy, os, sys
            from pathlib import Path
            sys.path.insert(0, {str(REPO_ROOT)!r})
            import confflow.remote.staging as staging
            from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
            from confflow.domain import FrozenDict
            from confflow.execution.process import NativeProcessSupervisor
            from confflow.persistence.contracts import store_path
            from confflow.persistence.work_items import SqliteWorkItemStore
            from confflow.producer import get_recipe_v4
            from confflow.remote.transport import RemoteTransport
            from confflow.workflow.v4.assembly import RunInputs

            tmp = Path({str(tmp_path)!r})
            run_root = str(tmp / "run")
            doc = copy.deepcopy(get_recipe_v4("tspes")["document"])
            doc["steps"] = doc["steps"][:1]
            doc["global"] = {{"scientific_defaults": {{"charge": 0, "multiplicity": 1}}}}
            doc["steps"][0]["execution"] = {{
                "executable": str(tmp / "science_orca"),
                "target": "cluster-cs",
            }}
            inputs = RunInputs(structures=FrozenDict({{
                "structures": import_xyz({WATER_XYZ!r}),
            }}))
            staging.import_result_artifacts = lambda **kwargs: os._exit(17)
            with SqliteWorkItemStore.open(store_path(run_root, "ts")) as store:
                transport = RemoteTransport(
                    run_root=run_root,
                    store=store,
                    worker_root=str(tmp / "worker"),
                    target_name="cluster-cs",
                )
                V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                    V4RunRequest(
                        workflow_document=doc,
                        run_inputs=inputs,
                        run_root=run_root,
                        import_sources=FrozenDict({{"structures": {WATER_XYZ!r}}}),
                        transport=transport,
                    )
                )
            """))
    os.environ[variable] = env_value
    try:
        crashed = subprocess.run(
            [sys.executable, str(runner)],
            env={**os.environ, "PATH": "/opt/ConfFlow/.venv/bin:/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=300,
            start_new_session=True,
        )
        assert crashed.returncode == 17, crashed.stderr[-3000:]
    finally:
        os.environ.pop(variable, None)


def _run_remote(tmp_path: Path, script: Path) -> Any:
    run_root = tmp_path / "run"
    doc = _single_step_doc(script)
    doc["steps"][0]["execution"]["target"] = "cluster-cs"
    with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
        transport = RemoteTransport(
            run_root=str(run_root),
            store=store,
            worker_root=str(tmp_path / "worker"),
            target_name="cluster-cs",
        )
        return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=_inputs(),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": WATER_XYZ}),
                transport=transport,
            )
        )


class TestEnvironmentGenerationVsAbandonedBundle:
    def test_changed_environment_does_not_adopt_abandoned_bundle(self, tmp_path: Path) -> None:
        script = _native(tmp_path, "CF_CS_SCIENCE")
        _crash_after_bundle(tmp_path, "CF_CS_SCIENCE", "-10")
        assert _launches(tmp_path) == 1

        os.environ["CF_CS_SCIENCE"] = "-20"
        try:
            report = _run_remote(tmp_path, script)
            assert report.status == "completed"
            assert report.step_results[0].results[0].value == -20.0, (
                "a changed execution environment must not publish the abandoned "
                "generation's stale result"
            )
            assert _launches(tmp_path) == 2, "the new environment must execute"
            run_root = tmp_path / "run"
            with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
                (item_id,) = store.list_items()
                assert [a.status.value for a in store.get_attempts(item_id)] == [
                    "interrupted",
                    "completed",
                ]
        finally:
            os.environ.pop("CF_CS_SCIENCE", None)

    def test_same_environment_still_reconciles_without_relaunch(self, tmp_path: Path) -> None:
        script = _native(tmp_path, "CF_CS_SCIENCE")
        _crash_after_bundle(tmp_path, "CF_CS_SCIENCE", "-10")
        assert _launches(tmp_path) == 1

        os.environ["CF_CS_SCIENCE"] = "-10"
        try:
            report = _run_remote(tmp_path, script)
            assert report.status == "completed"
            assert report.step_results[0].results[0].value == -10.0
            assert _launches(tmp_path) == 1, "R7 reconciliation must stay intact"
        finally:
            os.environ.pop("CF_CS_SCIENCE", None)
