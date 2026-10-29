"""Public V4 runs with real quota waits: mutable model and actual script interpreter."""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from confflow.execution import mlip_executor
from confflow.execution.quota import ServerQuota
from tests.v4 import test_arch_review_launch_identity as h

OUT = Path(__file__).resolve().parent
ROOT = Path(tempfile.mkdtemp(prefix="post200-assets-probe."))
GIB = 1024**3
results = {"root": str(ROOT)}
# Reuse the quota-observation helper without executing the executable probe.
namespace = {}
source = (
    (OUT / "quota_launch_probe.py").read_text().split("with pytest.MonkeyPatch.context() as mp:")[0]
)
exec(
    compile(source, str(OUT / "quota_launch_probe.py"), "exec"),
    {"__file__": str(__file__)},
    namespace,
)
# Function globals must include the definitions loaded by exec.
namespace["queued_run"].__globals__.update(namespace)
queued_run = namespace["queued_run"]
describe = namespace["describe"]

with pytest.MonkeyPatch.context() as mp:
    config = ROOT / "server.toml"
    config.write_text("total_cores = 1\ntotal_memory = '4GB'\n")
    state = ROOT / "quota"
    mp.setenv("CONFFLOW_SERVER_CONFIG", str(config))
    mp.setenv("CONFFLOW_SERVER_STATE_DIR", str(state))
    manager = ServerQuota(1, 4 * GIB, state)
    model = ROOT / "model.pt"
    model.write_text("-5")
    runner = ROOT / "runner.py"
    model_log = ROOT / "model-launches.log"
    runner.write_text(
        h._MLIP_FAKE_RUNNER.replace("import json", "import json, hashlib").replace(
            '"model_sha256": "fake"',
            '"model_sha256": hashlib.sha256(Path(job["model_path"]).read_bytes()).hexdigest()',
        )
        + f'\nwith open({str(model_log)!r}, "a") as f: f.write(str(energy)+"\\n")\n'
    )
    mp.setattr(mlip_executor, "RUNNER_PATH", runner)
    doc = h._mlip_doc(model, sys.executable)
    doc["steps"][0]["resources"] = {"cores_per_item": 1, "memory_per_item": "1GB"}
    doc["steps"][0]["execution"]["env"]["CONFFLOW_MLIP_THREADS"] = "1"
    first = queued_run(
        lambda: h._run_mlip(ROOT / "model-run", doc), lambda: model.write_text("-10"), manager
    )
    model.write_text("-5")
    resumed = h._run_mlip(ROOT / "model-run", doc)
    fresh = h._run_mlip(ROOT / "model-control", doc)
    results["model"] = {
        "queued": describe(first),
        "resumed": describe(resumed),
        "fresh": describe(fresh),
        "actual_model_sha256": first.step_results[0]
        .item_results[0]
        .diagnostics[0]
        .details.get("model_sha256"),
        "launches": model_log.read_text().splitlines(),
    }

    ambient = ROOT / "ambient-bin"
    ambient.mkdir()
    step = ROOT / "step-bin"
    step.mkdir()
    registered = ambient / "review-python"
    launched = step / "review-python"
    script_log = ROOT / "script.log"

    def wrapper(value):
        return (
            f"#!{sys.executable}\nimport os,sys\n"
            f'os.environ["REVIEW_LAUNCH_X"]={str(value)!r}\n'
            f"os.execv({sys.executable!r},[{sys.executable!r},*sys.argv[1:]])\n"
        )

    original = wrapper(1)
    for p in (registered, launched):
        p.write_text(original)
        p.chmod(0o755)
    script = ROOT / "echo.py"
    script.write_text(h._ECHO_ENV_SCRIPT.format(log=str(script_log)))
    config.write_text(
        "total_cores = 1\ntotal_memory = '4GB'\n[scripts.echo]\ncommand = "
        + json.dumps(["review-python", str(script)])
        + "\n"
    )
    previous_path = os.environ.get("PATH", "")
    mp.setenv("PATH", str(ambient) + os.pathsep + previous_path)
    doc = h._script_doc({"env": {"PATH": str(step) + os.pathsep + previous_path}})

    def replace_interpreter():
        new = step / "review-python.new"
        new.write_text(wrapper(2))
        new.chmod(0o755)
        os.replace(new, launched)

    first = queued_run(
        lambda: h._run_script(ROOT / "script-run", doc), replace_interpreter, manager
    )
    launched.write_text(original)
    launched.chmod(0o755)
    resumed = h._run_script(ROOT / "script-run", doc)
    fresh = h._run_script(ROOT / "script-control", doc)

    def script_description(report):
        return {
            **describe(report),
            "z": (
                h._z(report)
                if report.step_results and len(report.step_results[0].structures)
                else None
            ),
        }

    results["script"] = {
        "queued": script_description(first),
        "resumed": script_description(resumed),
        "fresh": script_description(fresh),
        "launches": script_log.read_text().splitlines(),
    }
    results["remaining_leases"] = json.loads((state / "server-quota.json").read_text())["leases"]
(OUT / "assets-quota-results.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
