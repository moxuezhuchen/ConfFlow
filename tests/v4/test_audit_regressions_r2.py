#!/usr/bin/env python3

"""Second-round Astra independent-audit regressions (R1-R7).

These are the authoritative frozen counterexamples from the second
independent review, kept in the suite so the failures they proved can
never regress.  Attack conditions are preserved verbatim:

- R1: ambient/explicit/remote environment identity vs actual launch env.
- R2: execution targets are executable constraints, never annotations.
- R3: live cancellation reaches the running native process.
- R4: analysis result references resolve against the published universe.
- R5: plain XYZ TSPES input carries typed reaction-group identity.
- R6: a new run generation can never leave an old terminal manifest current.
- R7: a durable worker bundle is reconciled before any retry attempt.

Run with the shared V4 conftest (vendor native installs scrubbed from
PATH); every fake native here is a test-owned script executed by an
absolute interpreter path.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import (
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution.environment import ExecutionEnvironment, measure_executable
from confflow.execution.process import NativeProcessSupervisor
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.producer import get_recipe_v4
from confflow.programs.registry import get_program_adapter
from confflow.workflow.v4.assembly import RunInputs

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_ORCA = Path(__file__).resolve().parent / "fakes" / "fake_orca.py"

WATER_XYZ = "3\nwater\nO 0 0 0\nH .76 .59 0\nH .76 -.59 0\n"


def _science_native(root: Path, *, delay: float = 0.0) -> Path:
    """Install the audit fake native: records launch + received env.

    The script appends one line per launch to ``science-count`` and dumps
    the complete environment it actually received plus the value it used
    for the reported energy.  ``SCIENCE_ENV`` (or its absence) drives a
    real, observable scientific difference.
    """
    root.mkdir(parents=True, exist_ok=True)
    script = root / "science_orca"
    script.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, os, sys, time
            from pathlib import Path
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.v4.fakes import fake_orca as f
            count = Path({str(root / "science-count")!r})
            lines = count.read_text().splitlines() if count.exists() else []
            count.write_text("\\n".join(lines + ["launch"]) + "\\n")
            record = Path({str(root / "science-record")!r})
            record.write_text(json.dumps({{
                "value": os.environ.get("SCIENCE_ENV", "ABSENT"),
                "env": dict(os.environ),
            }}))
            time.sleep({delay!r})
            f.ENERGY_HARTREE = float(os.environ.get("SCIENCE_ENV", -30))
            os.environ["FAKE_MODE"] = "ts_candidate"
            sys.exit(f.main(sys.argv))
            """))
    script.chmod(0o755)
    return script


def _launches(root: Path) -> int:
    count = root / "science-count"
    if not count.exists():
        return 0
    return len(count.read_text().splitlines())


def _last_record(root: Path) -> dict[str, Any]:
    return json.loads((root / "science-record").read_text())


def _single_step_doc(executable: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Recipe-derived one-step document (the R1 attack shape)."""
    doc = copy.deepcopy(get_recipe_v4("tspes")["document"])
    doc["steps"] = doc["steps"][:1]
    doc["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    execution: dict[str, Any] = {"executable": str(executable)}
    if env is not None:
        execution["env"] = dict(env)
    doc["steps"][0]["execution"] = execution
    return doc


def _water_inputs() -> RunInputs:
    return RunInputs(structures=FrozenDict({"structures": import_xyz(WATER_XYZ)}))


def _run(
    doc: dict[str, Any],
    run_root: Path,
    *,
    transport: Any = None,
) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=_water_inputs(),
            run_root=str(run_root),
            import_sources=FrozenDict({"structures": WATER_XYZ}),
            transport=transport,
        )
    )


def _energy(report: Any) -> Any:
    results = [
        item.value for step in report.step_results for item in step.results if item.kind == "energy"
    ]
    return results[0] if results else None


def _stored_environment_digest(run_root: Path, step_id: str) -> str:
    """Return the single item's durable environment digest from the store."""
    with SqliteWorkItemStore.open(store_path(str(run_root), step_id)) as store:
        item_ids = store.list_items()
        assert len(item_ids) == 1, item_ids
        registered = store.get_registered(item_ids[0])
    digest = registered["environment_digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    return digest


def _digest_over(executable: Path, env: dict[str, str]) -> str:
    """Recompute the exact environment digest over a launched env mapping."""
    identity = measure_executable(str(executable), adapter=get_program_adapter("orca"))
    return ExecutionEnvironment(
        program="orca",
        program_version=identity.program_version,
        executable_digest=identity.digest,
        relevant_env=FrozenDict(env),
    ).digest()


class TestR1EnvironmentIdentity:
    """R1: the executed environment IS the hashed environment."""

    def test_explicit_env_change_invalidates_and_relaunches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit binding env -5 -> -10 -> removed: real relaunches."""
        script = _science_native(tmp_path)
        monkeypatch.delenv("SCIENCE_ENV", raising=False)
        run_root = tmp_path / "run-explicit"
        report = _run(_single_step_doc(script, env={"SCIENCE_ENV": "-5"}), run_root)
        assert report.status == "completed"
        assert _energy(report) == -5.0
        assert _launches(tmp_path) == 1

        report = _run(_single_step_doc(script, env={"SCIENCE_ENV": "-10"}), run_root)
        assert _energy(report) == -10.0
        assert _launches(tmp_path) == 2

        report = _run(_single_step_doc(script, env={}), run_root)
        assert _energy(report) == -30.0
        assert _launches(tmp_path) == 3

    def test_inherited_env_change_invalidates_and_relaunches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ambient inheritance is digested: -5 -> -10 -> removed relaunch."""
        script = _science_native(tmp_path)
        run_root = tmp_path / "run-ambient"

        monkeypatch.setenv("SCIENCE_ENV", "-5")
        report = _run(_single_step_doc(script), run_root)
        assert _energy(report) == -5.0
        assert _launches(tmp_path) == 1

        monkeypatch.setenv("SCIENCE_ENV", "-10")
        report = _run(_single_step_doc(script), run_root)
        assert _energy(report) == -10.0
        assert _launches(tmp_path) == 2

        monkeypatch.delenv("SCIENCE_ENV")
        report = _run(_single_step_doc(script), run_root)
        assert _energy(report) == -30.0
        assert _launches(tmp_path) == 3

    def test_explicit_env_wins_over_inherited_and_ambient_is_digest_inert_when_shadowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Binding wins; a shadowed ambient change does not invalidate."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-5")
        run_root = tmp_path / "run-shadow"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-40"})
        report = _run(doc, run_root)
        assert _energy(report) == -40.0
        assert _launches(tmp_path) == 1

        monkeypatch.setenv("SCIENCE_ENV", "-50")
        report = _run(doc, run_root)
        assert _energy(report) == -40.0
        assert _launches(tmp_path) == 1

    def test_actual_subprocess_env_equals_hashed_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R1 proof: the launched env mapping recomputes the stored digest."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-77")
        monkeypatch.setenv("AUDIT_R2_SENTINEL", "launch-only")
        run_root = tmp_path / "run-identity"
        doc = _single_step_doc(script, env={"BINDING_ONLY": "explicit"})
        report = _run(doc, run_root)
        assert report.status == "completed"

        received = _last_record(tmp_path)["env"]
        assert received["SCIENCE_ENV"] == "-77"
        assert received["AUDIT_R2_SENTINEL"] == "launch-only"
        assert received["BINDING_ONLY"] == "explicit"
        assert _stored_environment_digest(run_root, "ts") == _digest_over(script, received)

    def test_remote_worker_launch_env_equals_probed_hash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R1 remote: worker launches exactly the probed producer snapshot."""
        from confflow.remote.transport import RemoteTransport

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-88")
        run_root = tmp_path / "run-remote"
        doc = _single_step_doc(script)
        doc["steps"][0]["execution"]["target"] = "cluster-audit"
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            transport = RemoteTransport(
                run_root=str(run_root),
                store=store,
                worker_root=str(tmp_path / "worker"),
                target_name="cluster-audit",
                target_env={"TARGET_ONLY": "declared"},
            )
            report = _run(doc, run_root, transport=transport)
        assert report.status == "completed"

        received = _last_record(tmp_path)["env"]
        assert received["SCIENCE_ENV"] == "-88"
        assert received["TARGET_ONLY"] == "declared"
        assert _stored_environment_digest(run_root, "ts") == _digest_over(script, received)

    def test_label_metadata_change_does_not_invalidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Presentation-only changes must not invalidate scientific reuse."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-21")
        run_root = tmp_path / "run-label"
        doc = _single_step_doc(script)
        doc["steps"][0]["label"] = "first label"
        doc["steps"][0]["annotations"] = {"ui": {"color": "blue"}}
        report = _run(doc, run_root)
        assert _energy(report) == -21.0
        assert _launches(tmp_path) == 1

        doc2 = copy.deepcopy(doc)
        doc2["steps"][0]["label"] = "renamed label"
        doc2["steps"][0]["annotations"] = {"ui": {"color": "red"}}
        report = _run(doc2, run_root)
        assert _energy(report) == -21.0
        assert _launches(tmp_path) == 1


@pytest.fixture(autouse=True)
def _clean_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient audit variables from leaking between tests."""
    monkeypatch.delenv("SCIENCE_ENV", raising=False)
    monkeypatch.delenv("AUDIT_R2_SENTINEL", raising=False)


def _analysis_doc(target: str) -> tuple[dict[str, Any], RunInputs]:
    """Build the frozen R2 pure-executor attack document.

    One formal reaction-profile analysis step bound to run-input
    structures/results, carrying a nonlocal execution target.  The target
    must be an executable constraint even though the executor is pure.
    """
    from confflow.domain import ResultSet, ScientificResult, StructureSet
    from confflow.domain.units import Unit
    from tests.v4._builders import structure

    step = copy.deepcopy(get_recipe_v4("tspes")["document"]["steps"][-1])
    step["bindings"] = {
        "structures": {"source": {"run": "structures"}},
        "results": {"source": {"run": "results"}},
    }
    native = step["analysis"]["native"]
    native.pop("electronic_source_steps", None)
    native.pop("correction_source_steps", None)
    step["execution"] = {"target": target}
    doc = {
        "schema": "confflow.workflow.v4",
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "inputs": {
            "structures": {"kind": "structure", "cardinality": "many"},
            "results": {"kind": "result", "cardinality": "many"},
        },
        "steps": [step],
    }
    t = structure("T", group_key="g")
    f = structure("F", parent_ids=("T",), role="path_endpoint_forward", group_key="g")
    r = structure("R", parent_ids=("T",), role="path_endpoint_reverse", group_key="g")
    results = [
        ScientificResult(
            kind=kind,
            value=value,
            unit=Unit.HARTREE,
            subject_structure_id=subject,
            result_id=f"{subject}{kind}",
        )
        for subject, energy in (("T", -5.0), ("F", -10.0), ("R", -20.0))
        for kind, value in (("energy", energy), ("gibbs_correction", 0.1))
    ]
    inputs = RunInputs(
        structures=FrozenDict({"structures": StructureSet((t, f, r))}),
        results=FrozenDict({"results": ResultSet(tuple(results))}),
    )
    return doc, inputs


class TestR2TargetSemantics:
    """R2: target is an executable constraint, never an annotation."""

    def test_unknown_target_without_transport_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-11")
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-11"})
        doc["steps"][0]["execution"]["target"] = "nonexistent-cluster"
        with pytest.raises(DomainError):
            _run(doc, tmp_path / "run")
        assert _launches(tmp_path) == 0

    def test_local_transport_rejects_nonlocal_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.execution.work_item_executor import WorkItemExecutor
        from confflow.remote.transport import LocalTransport

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-11")
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-11"})
        doc["steps"][0]["execution"]["target"] = "nonexistent-cluster"
        with pytest.raises(DomainError):
            _run(
                doc,
                tmp_path / "run",
                transport=LocalTransport(WorkItemExecutor()),
            )
        assert _launches(tmp_path) == 0

    def test_unnamed_remote_transport_rejects_nonlocal_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.transport import RemoteTransport

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-11")
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-11"})
        doc["steps"][0]["execution"]["target"] = "nonexistent-cluster"
        run_root = tmp_path / "run"
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            transport = RemoteTransport(
                run_root=str(run_root),
                store=store,
                worker_root=str(tmp_path / "worker"),
                target_name=None,
            )
            with pytest.raises(DomainError):
                _run(doc, run_root, transport=transport)
        assert _launches(tmp_path) == 0

    def test_named_remote_transport_rejects_target_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.transport import RemoteTransport

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-11")
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-11"})
        doc["steps"][0]["execution"]["target"] = "cluster-typo"
        run_root = tmp_path / "run"
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            transport = RemoteTransport(
                run_root=str(run_root),
                store=store,
                worker_root=str(tmp_path / "worker"),
                target_name="cluster-real",
            )
            with pytest.raises(DomainError):
                _run(doc, run_root, transport=transport)
        assert _launches(tmp_path) == 0

    def test_pure_executor_nonlocal_target_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc, inputs = _analysis_doc("gpu01")
        with pytest.raises(DomainError):
            V4RunApplication(supervisor=NativeProcessSupervisor()).run(
                V4RunRequest(workflow_document=doc, run_inputs=inputs, run_root=str(tmp_path))
            )

    def test_local_aliases_still_run_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-12")
        for index, alias in enumerate(("local", "localhost", "LOCAL")):
            run_root = tmp_path / f"run-alias-{index}"
            doc = _single_step_doc(script, env={"SCIENCE_ENV": "-12"})
            doc["steps"][0]["execution"]["target"] = alias
            report = _run(doc, run_root)
            assert report.status == "completed"
        assert _launches(tmp_path) == 3


class TestR7RemoteReconciliation:
    """R7: reconcile a durable worker bundle before advancing the attempt."""

    @staticmethod
    def _crash_runner(tmp_path: Path, *, mode: str) -> Path:
        """Write the controller that dies at the chosen crash point.

        ``import``: worker bundle is durable, controller dies before import
        (the frozen R7 counterexample).  ``package``: worker dies mid-native
        before any durable bundle exists (no recoverable result).
        """
        if mode == "import":
            crash = "staging.import_result_artifacts = lambda **kwargs: os._exit(17)"
        else:
            crash = (
                "import confflow.remote.result_bundle as result_bundle\n"
                "                result_bundle.package_result_bundle = "
                "lambda **kwargs: os._exit(17)"
            )
        runner = tmp_path / f"crash_controller_{mode}.py"
        runner.write_text(textwrap.dedent(f"""\
                import copy, os, sys
                from pathlib import Path
                sys.path.insert(0, {str(REPO_ROOT)!r})
                import confflow.remote.staging as staging
                from confflow.application.v4_run import (
                    V4RunApplication, V4RunRequest, import_xyz,
                )
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
                    "target": "cluster",
                }}
                inputs = RunInputs(structures=FrozenDict({{
                    "structures": import_xyz({WATER_XYZ!r}),
                }}))
                {crash}
                with SqliteWorkItemStore.open(store_path(run_root, "ts")) as store:
                    transport = RemoteTransport(
                        run_root=run_root,
                        store=store,
                        worker_root=str(tmp / "worker"),
                        target_name="cluster",
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
        return runner

    @staticmethod
    def _resume_doc(script: Path, env_value: str) -> dict[str, Any]:
        doc = _single_step_doc(script, env={"SCIENCE_ENV": env_value})
        doc["steps"][0]["execution"]["target"] = "cluster"
        return doc

    def test_restart_imports_bundle_without_duplicate_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess

        from confflow.remote.transport import RemoteTransport

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-55")
        runner = self._crash_runner(tmp_path, mode="import")
        crashed = subprocess.run(
            [sys.executable, str(runner)],
            env={**os.environ, "PATH": "/opt/ConfFlow/.venv/bin:/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=300,
            start_new_session=True,
        )
        assert crashed.returncode == 17, crashed.stderr[-2000:]
        assert _launches(tmp_path) == 1

        run_root = tmp_path / "run"
        doc = self._resume_doc(script, "-55")
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            transport = RemoteTransport(
                run_root=str(run_root),
                store=store,
                worker_root=str(tmp_path / "worker"),
                target_name="cluster",
            )
            report = _run(doc, run_root, transport=transport)
            assert report.status == "completed"
            assert _launches(tmp_path) == 1
            # Idempotent: repeated resume reuses the committed row.
            report = _run(doc, run_root, transport=transport)
            assert report.status == "completed"
            assert _launches(tmp_path) == 1
            (item_id,) = store.list_items()
            attempts = store.get_attempts(item_id)
            assert [entry.status.value for entry in attempts] == ["completed"]

    def test_worker_crash_mid_native_still_retries_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No durable bundle means the retry protocol still advances."""
        import subprocess

        from confflow.remote.transport import RemoteTransport

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-56")
        runner = self._crash_runner(tmp_path, mode="package")
        crashed = subprocess.run(
            [sys.executable, str(runner)],
            env={**os.environ, "PATH": "/opt/ConfFlow/.venv/bin:/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=300,
            start_new_session=True,
        )
        assert crashed.returncode == 17, crashed.stderr[-2000:]
        # The worker died before packaging anything durable: the retry is
        # allowed to relaunch (the "no recoverable result" branch).
        assert _launches(tmp_path) == 1
        run_root = tmp_path / "run"
        doc = self._resume_doc(script, "-56")
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            transport = RemoteTransport(
                run_root=str(run_root),
                store=store,
                worker_root=str(tmp_path / "worker"),
                target_name="cluster",
            )
            report = _run(doc, run_root, transport=transport)
            assert report.status == "completed"
            (item_id,) = store.list_items()
            attempts = store.get_attempts(item_id)
            assert [entry.status.value for entry in attempts] == [
                "interrupted",
                "completed",
            ]
        assert _launches(tmp_path) == 2


def _sleeping_native(root: Path, *, sleep_seconds: float) -> Path:
    """Fake native that proves live cancellation: started/slept/finished."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "sleep_orca"
    script.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os, sys, time
            from pathlib import Path
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from tests.v4.fakes import fake_orca as f
            (Path({str(root / "started")!r})).write_text("started")
            time.sleep({sleep_seconds!r})
            (Path({str(root / "finished")!r})).write_text("finished")
            os.environ["FAKE_MODE"] = "ts_candidate"
            sys.exit(f.main(sys.argv))
            """))
    script.chmod(0o755)
    return script


class TestR4ResultReferenceIntegrity:
    """R4: analysis citations resolve against the published universe."""

    @staticmethod
    def _run_analysis(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
        doc, inputs = _analysis_doc("local")
        run_root = tmp_path / "run"
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(workflow_document=doc, run_inputs=inputs, run_root=str(run_root))
        )
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        return report, manifest

    def test_formal_manifest_publishes_and_resolves_every_citation(self, tmp_path: Path) -> None:
        report, manifest = self._run_analysis(tmp_path)
        assert report.status == "completed"
        published = {entry["result_id"]: entry for entry in manifest["results"]}
        run_input_ids = {
            entry["result_id"]
            for entry in manifest["results"]
            if entry.get("origin") == "run_input"
        }
        assert run_input_ids == {
            "Tenergy",
            "Tgibbs_correction",
            "Fenergy",
            "Fgibbs_correction",
            "Renergy",
            "Rgibbs_correction",
        }
        groups = [entry for entry in manifest["analyses"] if "group_key" in entry]
        assert groups, "the formal run must project its reaction group"
        for group in groups:
            for cited in group["source_result_ids"]:
                assert cited in published, cited
        assert set(groups[0]["source_result_ids"]) <= run_input_ids

    def test_projector_rejects_dangling_duplicate_typed_and_stale(self, tmp_path: Path) -> None:
        from dataclasses import replace

        from confflow.domain import ResultSet
        from confflow.producer.run_result import project_analysis_groups

        report, _manifest = self._run_analysis(tmp_path)
        (step,) = report.step_results
        profile = next(item for item in step.results if item.kind == "reaction_profile")
        for sources, expected in (
            (["NONEXISTENT"], "unresolved"),
            (["NONEXISTENT", "NONEXISTENT"], "duplicate"),
            ([123], "non-empty strings"),
            (["old-attempt-id"], "unresolved"),
        ):
            value = dict(profile.value)
            value["source_result_ids"] = sources
            changed = replace(profile, value=FrozenDict(value))
            changed_step = replace(step, results=ResultSet((changed,)))
            with pytest.raises(ValueError) as error:
                project_analysis_groups((changed_step,))
            assert expected in str(error.value), (sources, error.value)

    def test_projector_rejects_wrong_subject_citation(self, tmp_path: Path) -> None:
        from dataclasses import replace

        from confflow.domain import ResultSet, ScientificResult
        from confflow.domain.units import Unit
        from confflow.producer.run_result import project_analysis_groups

        report, _manifest = self._run_analysis(tmp_path)
        (step,) = report.step_results
        profile = next(item for item in step.results if item.kind == "reaction_profile")
        foreign = ScientificResult(
            kind="energy",
            value=-99.0,
            unit=Unit.HARTREE,
            subject_structure_id="not-in-group",
            result_id="foreign-energy",
        )
        value = dict(profile.value)
        value["source_result_ids"] = ["foreign-energy"]
        changed = replace(profile, value=FrozenDict(value))
        changed_step = replace(step, results=ResultSet((changed, foreign)))
        with pytest.raises(ValueError) as error:
            project_analysis_groups((changed_step,))
        assert "wrong-subject" in str(error.value)

    def test_reference_index_rejects_duplicate_produced_ids(self) -> None:
        from dataclasses import replace

        from confflow.domain import ResultSet, ScientificResult
        from confflow.domain.units import Unit
        from confflow.producer.run_result import build_result_reference_index

        first = ScientificResult(kind="energy", value=-1.0, unit=Unit.HARTREE, result_id="dup")
        second = ScientificResult(kind="energy", value=-2.0, unit=Unit.HARTREE, result_id="dup")
        step = type(
            "StepStub",
            (),
            {"step_id": "s", "results": ResultSet((first, replace(second)))},
        )()
        with pytest.raises(ValueError) as error:
            build_result_reference_index((step,))
        assert "duplicate" in str(error.value)


class TestR5TypedGrouping:
    """R5: plain XYZ carries typed reaction-group identity end to end."""

    @staticmethod
    def _plain_inputs(text: str) -> RunInputs:
        return RunInputs(structures=FrozenDict({"structures": import_xyz(text)}))

    @staticmethod
    def _run_plain(doc: dict[str, Any], text: str, run_root: Path) -> Any:
        return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=TestR5TypedGrouping._plain_inputs(text),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": text}),
            )
        )

    def test_plain_xyz_derives_entity_group_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = TestR6GenerationLifecycle._science_chain_native(tmp_path)
        doc = TestR6GenerationLifecycle._tspes_doc(script, sp="-70", freq="-60")
        run_root = tmp_path / "run"
        report = self._run_plain(doc, WATER_XYZ, run_root)
        assert report.status == "completed"
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        groups = [entry for entry in manifest["analyses"] if "group_key" in entry]
        assert len(groups) == 1
        group = groups[0]
        # The group key is the opaque imported entity id, not a position or
        # filename; derived TS structures carry that root in their lineage.
        assert len(group["group_key"]) == 32
        assert all(char in "0123456789abcdef" for char in group["group_key"])
        assert group["group_key"] in group["ts_structure_id"]
        assert group["forward_endpoint_id"]
        assert group["reverse_endpoint_id"]

    def test_twenty_plain_ts_blocks_yield_twenty_groups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = TestR6GenerationLifecycle._science_chain_native(tmp_path)
        doc = TestR6GenerationLifecycle._tspes_doc(script, sp="-70", freq="-60")
        blocks = []
        for index in range(20):
            shift = index * 0.013
            blocks.append(
                f"3\nts{index:02d}\n"
                f"O {shift:.6f} 0.000000 0.000000\n"
                f"H {0.76 + shift:.6f} 0.590000 0.000000\n"
                f"H {0.76 + shift:.6f} -0.590000 0.000000\n"
            )
        text = "\n".join(blocks)
        run_root = tmp_path / "run"
        report = self._run_plain(doc, text, run_root)
        assert report.status == "completed"
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        groups = [entry for entry in manifest["analyses"] if "group_key" in entry]
        assert len(groups) == 20
        keys = {group["group_key"] for group in groups}
        assert len(keys) == 20
        assert all(group["group_key"] in group["ts_structure_id"] for group in groups)

    def test_grouping_requirement_fails_closed_before_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = TestR6GenerationLifecycle._science_chain_native(tmp_path)
        doc = TestR6GenerationLifecycle._tspes_doc(script, sp="-70", freq="-60")
        # Remove the typed grouping contract: the workflow still needs
        # reaction grouping, but the input cannot establish identity.
        del doc["inputs"]["structures"]["grouping"]
        run_root = tmp_path / "run"
        with pytest.raises(DomainError) as error:
            self._run_plain(doc, WATER_XYZ, run_root)
        assert "reaction grouping" in str(error.value)
        assert not (tmp_path / "chain-count").exists(), "native work must not start"


class TestR6GenerationLifecycle:
    """R6: a new generation can never leave an old manifest current."""

    @staticmethod
    def _science_chain_native(root: Path) -> Path:
        """Fake native for the full TSPES chain (freq failure switchable)."""
        root.mkdir(parents=True, exist_ok=True)
        script = root / "chain_orca"
        script.write_text(textwrap.dedent(f"""\
                #!{sys.executable}
                import os, sys
                from pathlib import Path
                sys.path.insert(0, {str(REPO_ROOT)!r})
                from tests.v4.fakes import fake_orca as f
                counter = Path({str(root / "chain-count")!r})
                lines = counter.read_text().splitlines() if counter.exists() else []
                counter.write_text("\\n".join(lines + ["launch"]) + "\\n")
                text = open(sys.argv[1]).read()
                cwd = os.getcwd()
                if "IRC" in text:
                    os.execv(sys.executable, [sys.executable, {str(REPO_ROOT / "tests/v4/fakes/fake_irc.py")!r}, *sys.argv[1:]])
                if "Freq" in text:
                    os.environ["FAKE_MODE"] = "success_freq_noshift"
                    ts = "/ts_freq/" in cwd
                    f.ENERGY_HARTREE = float(os.environ["TS_FREQ_E"]) if ts else -65.0
                    f.GIBBS_CORRECTION = 0.10 if ts else 0.20
                elif " SP" in text:
                    os.environ["FAKE_MODE"] = "success_sp"
                    f.ENERGY_HARTREE = float(os.environ["TS_SP_E"]) if "/ts_sp/" in cwd else -80.0
                elif "OptTS" in text:
                    os.environ["FAKE_MODE"] = "ts_candidate"
                else:
                    os.environ["FAKE_MODE"] = "success_opt"
                sys.exit(f.main(sys.argv))
                """))
        script.chmod(0o755)
        return script

    @staticmethod
    def _tspes_doc(script: Path, *, sp: str, freq: str) -> dict[str, Any]:

        doc = copy.deepcopy(get_recipe_v4("tspes")["document"])
        doc["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
        for step in doc["steps"]:
            if step["executor"] == "calculation":
                step["execution"] = {
                    "executable": str(script),
                    "env": {"TS_SP_E": sp, "TS_FREQ_E": freq},
                }
        return doc

    @staticmethod
    def _tspes_inputs() -> RunInputs:
        from dataclasses import replace as _replace

        from confflow.domain import StructureSet

        (record,) = tuple(import_xyz(WATER_XYZ))
        # Stable group identity for this pre-R5 helper: a constant group key
        # survives import-map reconciliation; lineage re-roots to the
        # persisted entity id on both the fresh and resumed paths.
        record = _replace(record, group_key="rxn")
        return RunInputs(structures=FrozenDict({"structures": StructureSet.of(record)}))

    def _run_tspes(self, doc: dict[str, Any], run_root: Path) -> Any:
        return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=self._tspes_inputs(),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": WATER_XYZ}),
            )
        )

    def test_old_completed_generation_never_remains_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case A: gen1 completed, gen2 fails downstream -> gen2 truth wins."""
        from confflow.persistence.generation import load_run_generation

        script = self._science_chain_native(tmp_path)
        run_root = tmp_path / "run"
        first = self._run_tspes(self._tspes_doc(script, sp="-70", freq="-60"), run_root)
        assert first.status == "completed"
        first_manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        first_generation = load_run_generation(str(run_root))
        assert first_generation is not None
        assert first_generation.status == "completed"
        assert first_manifest["generation_id"] == first_generation.generation_id

        failing = self._tspes_doc(script, sp="-70", freq="invalid-number")
        with pytest.raises(DomainError):
            self._run_tspes(failing, run_root)

        second_manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        second_generation = load_run_generation(str(run_root))
        assert second_generation is not None
        assert second_generation.status == "failed"
        assert second_generation.generation_id != first_generation.generation_id
        assert second_manifest["status"] == "failed"
        assert second_manifest["generation_id"] == second_generation.generation_id
        assert second_generation.manifest_generation_id == second_generation.generation_id
        assert second_generation.failure["step_id"] == "reaction_profile"
        assert second_generation.failure["blocked_downstream"] is True
        step_statuses = {step["id"]: step["status"] for step in second_manifest["steps"]}
        assert step_statuses["ts_freq"] == "failed"
        assert "reaction_profile" not in step_statuses

    def test_fresh_upstream_failure_publishes_terminal_failed_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case B: upstream failed -> blocked downstream -> explicit terminal."""
        from confflow.persistence.generation import load_run_generation

        script = self._science_chain_native(tmp_path)
        run_root = tmp_path / "run"
        doc = self._tspes_doc(script, sp="-70", freq="invalid-number")
        with pytest.raises(DomainError):
            self._run_tspes(doc, run_root)
        generation = load_run_generation(str(run_root))
        assert generation is not None and generation.status == "failed"
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "failed"
        assert manifest["generation_id"] == generation.generation_id
        assert manifest["steps"], "the failed upstream step must be durable in the manifest"

    def test_successful_resume_is_a_new_completed_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.persistence.generation import load_run_generation

        script = self._science_chain_native(tmp_path)
        run_root = tmp_path / "run"
        doc = self._tspes_doc(script, sp="-70", freq="-60")
        first = self._run_tspes(doc, run_root)
        assert first.status == "completed"
        first_generation = load_run_generation(str(run_root))
        second = self._run_tspes(doc, run_root)
        assert second.status == "completed"
        second_generation = load_run_generation(str(run_root))
        assert second_generation is not None and second_generation.status == "completed"
        assert second_generation.generation_id != first_generation.generation_id
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["generation_id"] == second_generation.generation_id


class TestR3LiveCancellation:
    """R3: cancellation reaches and terminates the running native process."""

    def test_service_cancel_terminates_running_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        from confflow.application.execution.models import RunState
        from confflow.application.execution.workflow_adapter import (
            WorkflowRunSpec,
            _prepare_request,
            build_workflow_service,
            executor_identity,
        )

        monkeypatch.delenv("SCIENCE_ENV", raising=False)
        script = _sleeping_native(tmp_path, sleep_seconds=5.0)
        work = tmp_path / "work"
        doc = _single_step_doc(script, env={"SLEEP_SENTINEL": "1"})
        config = tmp_path / "config.json"
        config.write_text(json.dumps(doc))
        xyz = tmp_path / "a.xyz"
        xyz.write_text(WATER_XYZ)
        spec = WorkflowRunSpec(
            run_id="cancel-live",
            input_xyz=(str(xyz),),
            config_file=str(config),
            work_dir=str(work),
        )
        service, executor = build_workflow_service(spec, state_root=tmp_path / "state")
        service.prepare(_prepare_request(spec, executor_identity(service)))
        service.execute(spec.run_id)
        started = tmp_path / "started"
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists()

        cancel_started = time.monotonic()
        service.cancel(spec.run_id)
        executor.wait(30)
        elapsed = time.monotonic() - cancel_started

        assert service.status(spec.run_id).state is RunState.CANCELLED
        assert not (tmp_path / "finished").exists()
        assert elapsed < 4.0, f"cancel waited for the native sleep ({elapsed:.2f}s)"
        manifest = json.loads((work / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"

    def test_cli_beacon_terminates_running_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import subprocess
        import time

        monkeypatch.delenv("SCIENCE_ENV", raising=False)
        script = _sleeping_native(tmp_path, sleep_seconds=5.0)
        work = tmp_path / "work"
        doc = _single_step_doc(script)
        config = tmp_path / "config.json"
        config.write_text(json.dumps(doc))
        xyz = tmp_path / "a.xyz"
        xyz.write_text(WATER_XYZ)
        executable = Path(sys.executable).parent / "confflow"
        if not executable.exists():  # pragma: no cover - environment guard
            pytest.skip("confflow console script is not installed")
        process = subprocess.Popen(
            [str(executable), str(xyz), "-c", str(config), "-w", str(work)],
            env={**os.environ, "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        started = tmp_path / "started"
        deadline = time.monotonic() + 30
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists()
        cancel_started = time.monotonic()
        (work / "CANCEL").touch()
        exit_code = process.wait(timeout=30)
        elapsed = time.monotonic() - cancel_started

        assert exit_code != 0
        assert not (tmp_path / "finished").exists()
        assert elapsed < 4.0, f"CLI cancel waited for the native sleep ({elapsed:.2f}s)"
        manifest = json.loads((work / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"

    def test_cancel_before_first_launch_never_launches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-31")
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-31"})
        run_root = tmp_path / "run"
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=_water_inputs(),
                run_root=str(run_root),
                should_cancel=lambda: True,
            )
        )
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 0
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            assert store.list_items() == ()

    def test_cancel_between_items_keeps_durable_completed_item(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancel after item 1 commits: item 1 stays durable, item 2 cancelled."""
        from confflow.domain.completion import WorkItemStatus

        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-32")
        two_blocks = WATER_XYZ + WATER_XYZ
        run_root = tmp_path / "run"
        stop_file = tmp_path / "stop-now"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-32"})
        # The native writes the stop marker after its first launch; the probe
        # then cancels the second item before it launches.
        script.write_text(
            script.read_text().replace(
                'count.write_text("\\n".join(lines + ["launch"]) + "\\n")',
                'count.write_text("\\n".join(lines + ["launch"]) + "\\n")\n'
                f'(Path({str(stop_file)!r})).write_text("stop")',
            )
        )
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(two_blocks)})),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": two_blocks}),
                should_cancel=lambda: stop_file.exists(),
            )
        )
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 1
        (step_result,) = report.step_results
        statuses = {item.status for item in step_result.item_results}
        assert WorkItemStatus.COMPLETED in statuses
        assert WorkItemStatus.CANCELLED in statuses
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            completed = [
                item_id
                for item_id in store.list_items()
                if store.get_state(item_id).value == "completed"
            ]
        assert len(completed) == 1

    def test_cancel_after_step_result_before_manifest_is_cancelled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A durable step result is never rolled back; the run is cancelled."""
        script = _science_native(tmp_path)
        monkeypatch.setenv("SCIENCE_ENV", "-33")
        run_root = tmp_path / "run"
        doc = _single_step_doc(script, env={"SCIENCE_ENV": "-33"})
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=_water_inputs(),
                run_root=str(run_root),
                import_sources=FrozenDict({"structures": WATER_XYZ}),
                should_cancel=lambda: (run_root / "steps" / "ts" / "step_result.json").exists(),
            )
        )
        assert report.status == "cancelled"
        assert _launches(tmp_path) == 1
        assert report.step_results[0].status.value == "completed"
        manifest = json.loads((run_root / RUN_RESULT_FILENAME).read_text())
        assert manifest["status"] == "cancelled"
        with SqliteWorkItemStore.open(store_path(str(run_root), "ts")) as store:
            (item_id,) = store.list_items()
            assert store.get_state(item_id).value == "completed"
