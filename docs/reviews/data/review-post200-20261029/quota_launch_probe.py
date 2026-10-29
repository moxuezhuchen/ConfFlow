"""Use the real quota to hold a public V4 run, replace its executable, then release.

Only scientific programs and the MLIP runner are fixtures. A logging handler observes
the real quota's waiting message; it does not intercept or alter acquire/submit.
"""

import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.execution import mlip_executor
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.quota import ServerQuota
from tests.v4 import test_arch_review_launch_identity as h
from tests.v4._builders import run_inputs, structure_set
from tests.v4._helpers import v43_coverage

OUT = Path(__file__).resolve().parent
ROOT = Path(tempfile.mkdtemp(prefix="post200-quota-probe."))
GIB = 1024**3
results = {"root": str(ROOT)}


class ObserveWaiting(logging.Handler):
    def __init__(self):
        super().__init__()
        self.waiting = threading.Event()

    def emit(self, record):
        if record.getMessage().startswith("quota waiting:"):
            self.waiting.set()


def describe(report):
    return {
        "status": report.status,
        "energies": [
            float(r.value) for s in report.step_results for r in s.results if r.kind == "energy"
        ],
        "codes": [
            d.code for s in report.step_results for i in s.item_results for d in i.diagnostics
        ],
        "errors": [i.error.message for s in report.step_results for i in s.item_results if i.error],
    }


def queued_run(run, swap, manager):
    lease = manager.acquire(1, GIB, "review-holder", "holder")
    observation = ObserveWaiting()
    logger = logging.getLogger("confflow.execution.quota")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(observation)
    box = {}

    def task():
        try:
            box["report"] = run()
        except BaseException as error:
            box["exception"] = repr(error)

    thread = threading.Thread(target=task, daemon=True)
    try:
        thread.start()
        if not observation.waiting.wait(40):
            raise AssertionError(f"no real quota wait: {box}")
        swap()
    finally:
        manager.release(lease)
        thread.join(40)
        logger.removeHandler(observation)
        logger.setLevel(previous)
    assert not thread.is_alive()
    if "exception" in box:
        raise AssertionError(box)
    return box["report"]


with pytest.MonkeyPatch.context() as mp:
    config = ROOT / "server.toml"
    config.write_text("total_cores = 1\ntotal_memory = '4GB'\n")
    state = ROOT / "quota"
    mp.setenv("CONFFLOW_SERVER_CONFIG", str(config))
    mp.setenv("CONFFLOW_SERVER_STATE_DIR", str(state))
    manager = ServerQuota(1, 4 * GIB, state)
    log = ROOT / "mlip-launches.log"
    exe = ROOT / "python-engine"

    def interpreter(label, energy):
        return (
            f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
            "if '-c' not in sys.argv[1:]:\n"
            f"    with Path({str(log)!r}).open('a') as f: f.write({label!r}+'\\n')\n"
            f"os.environ['REVIEW_ENGINE_ENERGY']={energy!r}\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
        )

    original = interpreter("A", "-5")
    exe.write_text(original)
    exe.chmod(0o755)
    replacement = ROOT / "python-engine.new"
    replacement.write_text(interpreter("B", "-10"))
    replacement.chmod(0o755)
    model = ROOT / "model.pt"
    model.write_text("constant model bytes")
    runner = ROOT / "runner.py"
    text = h._MLIP_FAKE_RUNNER.replace("import json", "import json, os").replace(
        'energy = float(Path(job["model_path"]).read_text(encoding="utf-8").strip())',
        'energy = float(os.environ["REVIEW_ENGINE_ENERGY"])',
    )
    runner.write_text(text)
    mp.setattr(mlip_executor, "RUNNER_PATH", runner)
    doc = h._mlip_doc(model, str(exe))
    doc["steps"][0]["resources"] = {"cores_per_item": 1, "memory_per_item": "1GB"}
    doc["steps"][0]["execution"]["env"]["CONFFLOW_MLIP_THREADS"] = "1"
    first = queued_run(
        lambda: h._run_mlip(ROOT / "mlip-run", doc), lambda: os.replace(replacement, exe), manager
    )
    exe.write_text(original)
    exe.chmod(0o755)
    resumed = h._run_mlip(ROOT / "mlip-run", doc)
    control = h._run_mlip(ROOT / "mlip-control", doc)
    results["mlip"] = {
        "queued": describe(first),
        "resumed": describe(resumed),
        "fresh": describe(control),
        "launches": log.read_text().splitlines(),
    }

    native_log = ROOT / "native-launches.log"
    native = ROOT / "orca"

    def native_body(label):
        return (
            f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
            f"with Path({str(native_log)!r}).open('a') as f: f.write({label!r}+'\\n')\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(v43_coverage.FAKE_ORCA)!r}, *sys.argv[1:]])\n"
        )

    native.write_text(native_body("A"))
    native.chmod(0o755)
    replacement = ROOT / "orca.new"
    replacement.write_text(native_body("B"))
    replacement.chmod(0o755)
    doc = v43_coverage._document()
    doc["steps"][0]["execution"]["executable"] = str(native)
    doc["steps"][0]["execution"]["env"] = {"FAKE_MODE": "success_opt"}

    def run_native():
        return V4RunApplication().run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=run_inputs(structures={"structures": structure_set("s0")}),
                run_root=str(ROOT / "native-run"),
                supervisor=NativeProcessSupervisor(),
            )
        )

    report = queued_run(run_native, lambda: os.replace(replacement, native), manager)
    results["native"] = {
        "queued": describe(report),
        "launches": native_log.read_text().splitlines(),
    }
    results["remaining_leases"] = json.loads((state / "server-quota.json").read_text())["leases"]

(OUT / "quota-launch-results.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
