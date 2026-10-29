"""Public-entry probe: a higher-priority PATH entry appears after measurement.

No resolver, measurer, executor, store, or reuse decision is mocked. Controlled
program wrappers materialize the new file at a deterministic point instead of
relying on a timing-sensitive concurrent installation.
"""

import json
import os
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

OUT = Path(__file__).resolve().parent
ROOT = Path(tempfile.mkdtemp(prefix="pr199-resolution-pin."))
result = {"root": str(ROOT)}


def describe(report):
    return {
        "status": report.status,
        "energies": [
            float(r.value) for s in report.step_results for r in s.results if r.kind == "energy"
        ],
        "codes": [
            d.code for s in report.step_results for i in s.item_results for d in i.diagnostics
        ],
    }


def wrapper(path, body):
    path.write_text(f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n" + body)
    path.chmod(0o755)


with pytest.MonkeyPatch.context() as mp:
    mp.setenv("CONFFLOW_SERVER_CONFIG", str(ROOT / "server.toml"))
    mp.setenv("CONFFLOW_SERVER_STATE_DIR", str(ROOT / "quota"))
    a = ROOT / "A"
    b = ROOT / "B"
    a.mkdir()
    b.mkdir()
    search_path = os.pathsep.join((str(b), str(a), os.environ["PATH"]))
    log = ROOT / "mlip-launches.log"
    trigger = ROOT / "install-on-probe"
    trigger.touch()
    target = b / "python3"
    template = ROOT / "interpreter-B.txt"
    template.write_text(
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a') as f: f.write('launch:B\\n')\n"
        "os.environ['REVIEW_RUNTIME_ENERGY'] = '-10'\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    wrapper(
        a / "python3",
        (
            "if '-c' in sys.argv[1:]:\n"
            f"    with Path({str(log)!r}).open('a') as f: f.write('probe:A\\n')\n"
            f"    if Path({str(trigger)!r}).exists():\n"
            f"        Path({str(target)!r}).write_text(Path({str(template)!r}).read_text())\n"
            f"        Path({str(target)!r}).chmod(0o755)\n"
            f"        Path({str(trigger)!r}).unlink()\n"
            "else:\n"
            f"    with Path({str(log)!r}).open('a') as f: f.write('launch:A\\n')\n"
            "os.environ['REVIEW_RUNTIME_ENERGY'] = '-5'\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
        ),
    )
    runner = ROOT / "fake_mlip_runner.py"
    text = h._MLIP_FAKE_RUNNER.replace("import json", "import json, os")
    text = text.replace(
        'energy = float(Path(job["model_path"]).read_text(encoding="utf-8").strip())',
        'energy = float(os.environ["REVIEW_RUNTIME_ENERGY"])',
    )
    runner.write_text(text)
    mp.setattr(mlip_executor, "RUNNER_PATH", runner)
    model = ROOT / "model.pt"
    model.write_text("constant model bytes")
    doc = h._mlip_doc(model, "python3")
    doc["steps"][0]["execution"]["env"]["PATH"] = search_path
    doc["steps"][0]["execution"]["allowed_executables"] = ["python3"]
    first = h._run_mlip(ROOT / "mlip-run", doc)
    first_log = log.read_text().splitlines()
    # Restore the originally measured A without changing A, configuration,
    # model, runner bytes, or the parent environment.
    target.unlink()
    resumed = h._run_mlip(ROOT / "mlip-run", doc)
    resumed_log = log.read_text().splitlines()
    control = h._run_mlip(ROOT / "mlip-control", doc)
    result["mlip"] = {
        "first": describe(first),
        "resumed": describe(resumed),
        "control": describe(control),
        "first_log": first_log,
        "resumed_log": resumed_log,
        "all_log": log.read_text().splitlines(),
    }

    # Native: after item 1 runs A, item 2 must keep the same measured A.
    native_log = ROOT / "native-launches.log"
    target = b / "orca"
    template = ROOT / "orca-B.txt"
    base = (
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        f"with Path({str(native_log)!r}).open('a') as f: f.write('B\\n')\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(v43_coverage.FAKE_ORCA)!r}, *sys.argv[1:]])\n"
    )
    template.write_text(base)
    wrapper(
        a / "orca",
        (
            f"with Path({str(native_log)!r}).open('a') as f: f.write('A\\n')\n"
            f"Path({str(target)!r}).write_text(Path({str(template)!r}).read_text())\n"
            f"Path({str(target)!r}).chmod(0o755)\n"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(v43_coverage.FAKE_ORCA)!r}, *sys.argv[1:]])\n"
        ),
    )
    doc = v43_coverage._document()
    step = doc["steps"][0]
    step["scheduler"]["max_parallel_items"] = 1
    step["execution"] = {
        "binding_id": "test",
        "executable": "orca",
        "allowed_executables": ["orca"],
        "env": {"PATH": search_path, "FAKE_MODE": "success_opt"},
    }
    report = V4RunApplication().run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=run_inputs(structures={"structures": structure_set("s0", "s1")}),
            run_root=str(ROOT / "native-run"),
            supervisor=NativeProcessSupervisor(),
        )
    )
    result["native"] = {"report": describe(report), "launches": native_log.read_text().splitlines()}

(OUT / "resolution-pin-results.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
