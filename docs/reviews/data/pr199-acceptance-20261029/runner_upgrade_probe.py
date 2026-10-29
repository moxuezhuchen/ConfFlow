"""Show how the real runner-source upgrade affects otherwise healthy MLIP reuse."""

import json
import sys
import tempfile
from pathlib import Path

import pytest

from confflow.execution import mlip_executor
from tests.v4 import test_arch_review_launch_identity as h

out = Path(__file__).resolve().parent
root = Path(tempfile.mkdtemp(prefix="pr199-runner-upgrade."))
log = root / "launches.log"
result = {"root": str(root)}
injected = """\nclass _ReviewBackend:
    def __init__(self, job): pass
    def energy(self, symbols, coords): return -27.211386245988, 1
_FairchemBackend = _ReviewBackend
with open(%r, 'a') as f: f.write('run\\n')
\n""" % str(log)  # noqa: UP031
paths = {}
for version, source in [
    ("old", "/opt/ConfFlow/confflow/execution/mlip_runner.py"),
    ("new", "/tmp/confflow-pr199-source/confflow/execution/mlip_runner.py"),
]:
    text = Path(source).read_text()
    text = text.replace('if __name__ == "__main__":', injected + 'if __name__ == "__main__":')
    paths[version] = root / f"{version}_runner.py"
    paths[version].write_text(text)
with pytest.MonkeyPatch.context() as mp:
    mp.setenv("CONFFLOW_SERVER_CONFIG", str(root / "server.toml"))
    mp.setenv("CONFFLOW_SERVER_STATE_DIR", str(root / "quota"))
    model = root / "model.pt"
    model.write_text("constant model bytes")
    doc = h._mlip_doc(model, sys.executable)
    doc["steps"][0]["resources"] = {"cores_per_item": 1, "memory_per_item": "1GB"}
    doc["steps"][0]["execution"]["env"]["CONFFLOW_MLIP_THREADS"] = "1"
    for label, version in [
        ("initial", "old"),
        ("unchanged_resume", "old"),
        ("upgraded_resume", "new"),
    ]:
        mp.setattr(mlip_executor, "RUNNER_PATH", paths[version])
        r = h._run_mlip(root / "run", doc)
        result[label] = {
            "status": r.status,
            "energy": [float(v.value) for s in r.step_results for v in s.results],
            "codes": [
                d.code for s in r.step_results for i in s.item_results for d in i.diagnostics
            ],
            "launches": len(log.read_text().splitlines()),
        }
(out / "runner-upgrade-results.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
