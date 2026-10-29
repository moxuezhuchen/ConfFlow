"""Run MLIP under a selected source tree without changing the process environment."""

# ruff: noqa: E402
import json
import sys
from pathlib import Path

tree, storage_root, mode = sys.argv[1:]
sys.path.insert(0, tree)

import pytest

import confflow
from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.execution.process import NativeProcessSupervisor
from confflow.workflow.v4.assembly import RunInputs
from tests.v4 import test_arch_review_launch_identity as h

root = Path(storage_root)
root.mkdir(exist_ok=True)
with pytest.MonkeyPatch.context() as mp:
    mp.setenv("CONFFLOW_SERVER_CONFIG", str(root / "absent.toml"))
    mp.setenv("CONFFLOW_SERVER_STATE_DIR", str(root / "quota-state"))
    h._install_mlip_runner(mp, root)
    model = root / "model.pt"
    if not model.exists():
        model.write_text("-5\n")
    document = h._mlip_doc(model, sys.executable)
    document["inputs"]["structures"]["topology"] = {"delete": [[1, 3]]}
    xyz = "3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n"
    folder = root / ("fresh" if mode == "fresh" else "run")
    report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_root=str(folder),
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(xyz)})),
            import_sources=FrozenDict({"structures": xyz}),
        )
    )
    s = report.step_results[0].structures[0]
    e = report.step_results[0].results[0]
    result = dict(
        mode=mode,
        package_file=confflow.__file__,
        status=report.status,
        working_topology=s.working_topology,
        topology_patch=s.topology_patch,
        provenance=e.provenance,
        diagnostics=[d.code for d in report.step_results[0].diagnostics],
    )
    (root / f"{mode}.json").write_text(json.dumps(result, indent=2, default=str))
    print(json.dumps(result, default=str))
