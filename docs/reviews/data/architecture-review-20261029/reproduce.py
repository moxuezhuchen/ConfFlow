"""Read-only architecture review reproductions; only writes under supplied temp root.

Run with snapshot repo on PYTHONPATH and the project's venv Python.
Scientific programs are fake runners from the tracked regression suite.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import pytest

from confflow.domain import FrozenDict

ROOT = Path(tempfile.mkdtemp(prefix="confflow-review-cases-20261029."))


def topology_and_energy(root):
    from confflow.domain.topology import TopologyPatch
    from confflow.producer.run_result import _energies_by_subject
    from confflow.science.topology import resolve_working_adjacency
    from tests.v4 import test_mlip_executor as h

    with pytest.MonkeyPatch.context() as mp:
        h._install_runner(mp, root)
        record = dataclasses.replace(
            h._records()[0],
            topology_patch=TopologyPatch(delete_edges=((1, 3),)),
            working_topology=((1,), (0,), ()),
        )
        result, _ = h._run(h._ctx(root, h._native(), cores=1), [record], cores=1)
        assert result.status.value == "completed", result.diagnostics
        output = result.structures[0]

        def graph(r):
            return resolve_working_adjacency(r, [8, 1, 1], r.coordinates)

        return dict(
            status=result.status.value,
            unchanged_coordinates=output.coordinates == record.coordinates,
            input_graph=graph(record),
            output_graph=graph(output),
            output_patch=output.topology_patch,
            output_topology=output.working_topology,
            energy=result.results[0].value,
            energy_provenance=result.results[0].provenance,
            manifest_energy_projection=[
                {"source_step_id": step_id, "subject_structure_id": subject_id, **energies}
                for (step_id, subject_id), energies in _energies_by_subject(result.results).items()
            ],
        )


def abandoned_environment(root, *, claimed=True):
    from confflow.domain.completion import WorkItemStatus
    from confflow.execution.batch import BatchStepExecutor
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.persistence.contracts import OwnerIdentity, store_path
    from confflow.persistence.work_items import SqliteWorkItemStore
    from tests.v4._helpers import v43_coverage as h

    plan = h._compile(h._document())
    items = h._items(plan, 1)
    item = items[0]
    run_root = str(root / "run")
    req = h._request(plan, items, run_root)
    env_a = dataclasses.replace(req.environment, relevant_env=FrozenDict({"SCIENCE_ENV": "A"}))
    env_b = dataclasses.replace(req.environment, relevant_env=FrozenDict({"SCIENCE_ENV": "B"}))
    req_a = dataclasses.replace(req, environment=env_a, native_env=env_a.relevant_env)
    req_b = dataclasses.replace(req, environment=env_b, native_env=env_b.relevant_env)
    calls = []

    class Executor:
        def execute(self, item, context, **kw):
            used = context.native_env["SCIENCE_ENV"]
            calls.append(used)
            return dataclasses.replace(
                h._result(item, status=WorkItemStatus.COMPLETED),
                metadata=FrozenDict({"actual_environment": used}),
            )

    batch = BatchStepExecutor(Executor()).with_supervisor(NativeProcessSupervisor())
    with SqliteWorkItemStore.open(store_path(run_root, item.step_id)) as store:
        store.register_item(
            work_item_id=item.id,
            logical_key=item.logical_key,
            step_id=item.step_id,
            work_item_digest=item.semantic_digest,
            step_semantic_digest=plan.steps[0].step_semantic_digest,
            environment_digest=env_a.digest(),
            producer_provenance=dict(batch._current_provenance(req_a).thaw()),
        )
        if claimed:
            assert store.claim(item.id, owner=OwnerIdentity(owner_token="crashed-A", pid=2**30))
        first = batch.execute_step_resumable(
            req_b, store=store, run_root=run_root, owner_token="recover-B"
        )
        committed = store.get_registered(item.id)["environment_digest"]
        second = batch.execute_step_resumable(
            req_a, store=store, run_root=run_root, owner_token="resume-A"
        )
        return dict(
            initial_status="running" if claimed else "pending",
            recovery_status=first.status.value,
            return_to_a_status=second.status.value,
            actual_executions=calls,
            registered_as_a=committed == env_a.digest(),
            registered_as_b=committed == env_b.digest(),
            persisted_actual_environment=store.get_result(item.id).metadata["actual_environment"],
            attempts=[a.attempt_number for a in store.get_attempts(item.id)],
        )


def abandoned_model_workflow(root):
    from confflow.persistence.contracts import OwnerIdentity, store_path
    from confflow.persistence.work_items import SqliteWorkItemStore
    from tests.v4 import test_arch_review_launch_identity as h

    with pytest.MonkeyPatch.context() as mp:
        h._install_mlip_runner(mp, root)
        model = root / "model.pt"
        model.write_text("-5\n")
        document = h._mlip_doc(model, sys.executable)
        run_root = root / "run"
        first = h._run_mlip(run_root, document)
        path = store_path(str(run_root), "uma")
        item_id = "wi:uma:all"
        # Seed a valid abandoned claim through the store API. No actual controller
        # is killed; all subsequent runs use the ordinary V4 application path.
        with SqliteWorkItemStore.open(path) as store:
            registered = store.get_registered(item_id)
            original_digest = registered["environment_digest"]
            store.refresh_for_new_environment(
                item_id,
                work_item_digest=registered["work_item_digest"],
                step_semantic_digest=registered["step_semantic_digest"],
                environment_digest=original_digest,
                producer_provenance=registered["producer_provenance"],
                reason="review: seed an abandoned attempt under the original model",
            )
            assert store.claim(item_id, owner=OwnerIdentity(owner_token="ghost", pid=2**30))
        model.write_text("-10\n")
        recovered = h._run_mlip(run_root, document)
        with SqliteWorkItemStore.open(path) as store:
            digest_after_recovery = store.get_registered(item_id)["environment_digest"]
        model.write_text("-5\n")
        restored = h._run_mlip(run_root, document)
        with SqliteWorkItemStore.open(path) as store:
            attempts = [a.attempt_number for a in store.get_attempts(item_id)]
        return dict(
            statuses=[r.status for r in (first, recovered, restored)],
            model_values=[-5, -10, -5],
            returned_energies=[h._mlip_energy(r) for r in (first, recovered, restored)],
            recovered_still_registered_as_original=digest_after_recovery == original_digest,
            attempts=attempts,
        )


def confgen_path(root):
    from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.workflow.v4.assembly import RunInputs
    from tests.v4 import _dg_search_helpers as h
    from tests.v4.test_confgen_search_step import _SEARCH, _scope

    a, b = root / "a", root / "b"
    a.mkdir()
    b.mkdir()
    ax, bx = a / "xtb", b / "xtb"
    for directory, target, label in ((a, ax, "A"), (b, bx, "B")):
        written = h._write_fake(directory)
        written.rename(target)
        target.write_text(target.read_text() + "\n# version " + label + "\n")
    record = root / "xtb-launches.jsonl"
    els, coords = h._toy()
    xyz = f"{len(els)}\ntoy\n" + "".join(f"{e} {x} {y} {z}\n" for e, (x, y, z) in zip(els, coords))
    document = {
        "schema": "confflow.workflow.v4",
        "inputs": {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 0,
                "multiplicity": 1,
            }
        },
        "steps": [
            {
                "id": "cg",
                "executor": "confgen",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "confgen": _scope(search={**_SEARCH, "starts": 1}, seed=11),
                "execution": {
                    "binding_id": "xtb",
                    "executable": "xtb",
                    "allowed_executables": [str(ax)],
                    "env": {
                        "PATH": str(b) + os.pathsep + os.environ["PATH"],
                        "FAKE_XTB_RECORD": str(record),
                    },
                },
            }
        ],
    }
    req = V4RunRequest(
        workflow_document=document,
        run_root=str(root / "run"),
        run_inputs=RunInputs(
            structures=FrozenDict({"structures": import_xyz(xyz, source_name="toy.xyz")})
        ),
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PATH", str(a) + os.pathsep + os.environ["PATH"])
        app = V4RunApplication(supervisor=NativeProcessSupervisor())
        first = app.run(req)
        launches = (
            [json.loads(s) for s in record.read_text().splitlines()] if record.exists() else []
        )
        bx.write_text(bx.read_text().replace("e = -100.0", "e = -200.0"))
        second = app.run(req)
        launches2 = record.read_text().splitlines() if record.exists() else []
        return dict(
            status=first.status,
            second_status=second.status,
            allowed_executable=str(ax),
            actually_launched=sorted({x["argv"][0] for x in launches}),
            launches_before_binary_change=len(launches),
            launches_after_binary_change=len(launches2),
            first_energies=[r.value for r in first.step_results[0].results if r.kind == "energy"],
            second_energies=[r.value for r in second.step_results[0].results if r.kind == "energy"],
        )


def mlip_manifest(root):
    from tests.v4 import test_arch_review_launch_identity as h

    with pytest.MonkeyPatch.context() as mp:
        h._install_mlip_runner(mp, root)
        model = root / "model.pt"
        model.write_text("-5\n")
        report = h._run_mlip(root / "run", h._mlip_doc(model, sys.executable))
        manifest = json.loads((root / "run" / "run_result.json").read_text())
        return dict(
            status=report.status, scientific_energy=h._mlip_energy(report), manifest=manifest
        )


def mlip_topology_workflow(root):
    from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
    from confflow.execution.process import NativeProcessSupervisor
    from confflow.science.topology import resolve_working_adjacency
    from confflow.workflow.v4.assembly import RunInputs
    from tests.v4 import test_arch_review_launch_identity as h

    with pytest.MonkeyPatch.context() as mp:
        h._install_mlip_runner(mp, root)
        model = root / "model.pt"
        model.write_text("-5\n")
        document = h._mlip_doc(model, sys.executable)
        document["inputs"]["structures"]["topology"] = {"delete": [[1, 3]]}
        xyz = "3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n"
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=document,
                run_root=str(root / "run"),
                run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(xyz)})),
            )
        )
        output = report.step_results[0].structures[0]
        return dict(
            status=report.status,
            declared_deleted_bond=[1, 3],
            output_patch=output.topology_patch,
            output_topology=output.working_topology,
            output_graph=resolve_working_adjacency(output, [8, 1, 1], output.coordinates),
        )


def main():
    os.environ["CONFFLOW_SERVER_CONFIG"] = str(ROOT / "server.toml")
    os.environ["CONFFLOW_SERVER_STATE_DIR"] = str(ROOT / "quota-state")
    report = {"artifact_root": str(ROOT), "source": os.environ.get("PYTHONPATH")}
    for name, case in [
        ("abandoned_environment", abandoned_environment),
        ("pending_environment", lambda root: abandoned_environment(root, claimed=False)),
        ("abandoned_model_workflow", abandoned_model_workflow),
        ("mlip_topology_workflow", mlip_topology_workflow),
        ("mlip_topology", topology_and_energy),
        ("mlip_manifest", mlip_manifest),
        ("confgen_path", confgen_path),
    ]:
        folder = ROOT / name
        folder.mkdir()
        try:
            report[name] = case(folder)
        except Exception:
            report[name] = {"error": traceback.format_exc()}
        (ROOT / "results.json").write_text(json.dumps(report, indent=2, default=str))
        print(name, json.dumps(report[name], default=str), flush=True)
    print("REPORT", str(ROOT / "results.json"), flush=True)


if __name__ == "__main__":
    main()
