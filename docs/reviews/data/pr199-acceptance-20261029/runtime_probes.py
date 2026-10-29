"""Public-application probes for execution and MLIP terminal semantics."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.execution import mlip_executor
from confflow.execution.process import NativeProcessSupervisor
from tests.v4 import test_arch_review_launch_identity as h
from tests.v4._builders import run_inputs, structure_set
from tests.v4._helpers import v43_coverage

ROOT = Path(tempfile.mkdtemp(prefix="post198-runtime-probes."))
OUT = Path("/tmp/confflow-pr199-acceptance")
results = {"root": str(ROOT)}


def describe(report):
    return {
        "status": report.status,
        "structures": [len(s.structures) for s in report.step_results],
        "results": [len(s.results) for s in report.step_results],
        "diagnostics": [
            (
                d.to_dict()
                if hasattr(d, "to_dict")
                else {"code": d.code, "message": d.message, "details": dict(d.details)}
            )
            for s in report.step_results
            for i in s.item_results
            for d in i.diagnostics
        ],
        "errors": [i.error.message for s in report.step_results for i in s.item_results if i.error],
    }


with pytest.MonkeyPatch.context() as mp:
    mp.setenv("CONFFLOW_SERVER_CONFIG", str(ROOT / "server.toml"))
    mp.setenv("CONFFLOW_SERVER_STATE_DIR", str(ROOT / "quota"))

    # Uses the real runner's load/compute/error-row protocol with a controlled
    # backend factory. A transient backend outage does not change model/env.
    flag = ROOT / "backend-unavailable"
    flag.write_text("transient backend outage")
    log = ROOT / "backend-launches.log"
    runner = ROOT / "backend_runner.py"
    runner.write_text("""import json, os, sys
from pathlib import Path
from confflow.execution.mlip_runner import run_job
FLAG = Path(%r)
LOG = Path(%r)
class Backend:
    def energy(self, symbols, coords):
        return -27.211386245988, 1
def factory(job):
    if FLAG.exists():
        raise RuntimeError("temporary model/backend initialization failure")
    return Backend()
with LOG.open('a') as stream:
    stream.write('runner\\n')
raise SystemExit(run_job(json.loads(Path(sys.argv[1]).read_text()), os.getcwd(), factory))
""" % (str(flag), str(log)))  # noqa: UP031
    mp.setattr(mlip_executor, "RUNNER_PATH", runner)
    model = ROOT / "model.pt"
    model.write_text("constant model bytes")
    doc = h._mlip_doc(model, sys.executable)
    doc["steps"][0]["resources"] = {"cores_per_item": 1, "memory_per_item": "1GB"}
    doc["steps"][0]["execution"]["env"]["CONFFLOW_MLIP_THREADS"] = "1"
    first = h._run_mlip(ROOT / "outage-run", doc)
    flag.unlink()
    second = h._run_mlip(ROOT / "outage-run", doc)
    fresh = h._run_mlip(ROOT / "healthy-fresh", doc)
    results["backend_outage"] = {
        "first": describe(first),
        "resumed": describe(second),
        "fresh": describe(fresh),
        "runner_launches": log.read_text().splitlines(),
    }

    # Name allowlist contract: configured name should remain available to policy.
    h._install_mlip_runner(mp, ROOT)
    model.write_text("-5\n")
    interpreter = shutil.which("python3")
    assert interpreter
    for rule in ("python3", interpreter):
        doc = h._mlip_doc(model, "python3")
        doc["steps"][0]["execution"]["allowed_executables"] = [rule]
        report = h._run_mlip(ROOT / ("mlip-name" if rule == "python3" else "mlip-absolute"), doc)
        results["mlip_allow_" + ("name" if rule == "python3" else "absolute")] = describe(report)

    script_log = ROOT / "script-launches.log"
    h._register_script(ROOT, script_log)
    config = ROOT / "server.toml"
    config.write_text(config.read_text().replace(json.dumps(sys.executable), json.dumps("python3")))
    for rule in ("python3", interpreter):
        doc = h._script_doc({"binding_id": "echo", "allowed_executables": [rule]})
        report = h._run_script(
            ROOT / ("script-name" if rule == "python3" else "script-absolute"), doc
        )
        results["script_allow_" + ("name" if rule == "python3" else "absolute")] = describe(report)
    results["script_launches"] = script_log.read_text().splitlines() if script_log.exists() else []

    # Native executable selection when the binding PATH differs from the controller.
    original_path = os.environ["PATH"]
    native_log = ROOT / "native-launches.log"
    for label in ("A", "B"):
        directory = ROOT / label
        directory.mkdir()
        wrapper = directory / "orca"
        wrapper.write_text(
            "#!"
            + sys.executable
            + "\n"
            + "import os, sys\nfrom pathlib import Path\n"
            + "with Path("
            + repr(str(native_log))
            + ').open("a") as f: f.write('
            + repr(label + "\n")
            + ")\n"
            + "os.execv("
            + repr(sys.executable)
            + ", ["
            + repr(sys.executable)
            + ", "
            + repr(str(v43_coverage.FAKE_ORCA))
            + ", *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
    mp.setenv("PATH", str(ROOT / "A") + os.pathsep + original_path)
    doc = v43_coverage._document()
    doc["steps"][0]["execution"]["executable"] = "orca"
    doc["steps"][0]["execution"]["env"] = {
        "PATH": str(ROOT / "B") + os.pathsep + original_path,
        "FAKE_MODE": "success_opt",
    }
    request = V4RunRequest(
        workflow_document=doc,
        run_inputs=run_inputs(structures={"structures": structure_set("s0")}),
        run_root=str(ROOT / "native-path"),
        supervisor=NativeProcessSupervisor(),
    )
    report = V4RunApplication().run(request)
    results["native_binding_path"] = {
        "report": describe(report),
        "launches": native_log.read_text().splitlines(),
        "expected": shutil.which("orca", path=doc["steps"][0]["execution"]["env"]["PATH"]),
    }

(OUT / "runtime-results.json").write_text(json.dumps(results, indent=2, default=str))
print(OUT / "runtime-results.json")
for key, value in results.items():
    if key not in ("root", "backend_outage", "native_binding_path") and isinstance(value, dict):
        print(key, value["status"], value["errors"])
print(
    "backend:", {k: results["backend_outage"][k]["status"] for k in ("first", "resumed", "fresh")}
)
print("native chosen:", results["native_binding_path"]["launches"])
