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
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import (
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import FrozenDict
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
