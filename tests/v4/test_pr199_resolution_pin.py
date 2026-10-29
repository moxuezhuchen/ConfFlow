"""A2 regression: the one resolved command is what policy, measurement and launch use.

A program measured as A must be the program launched, even when a higher-priority PATH
directory gains a same-named file B after measurement.  A result is never stored under
A's identity while B produced it.  Every case enters through ``V4RunApplication.run`` or the
work-item executor, with labelled fake programs and no mocked resolution, measurement,
batch or persistence.  The only substitutions are the MLIP runner source (a fake backend)
and a call counter that passes every lookup through unchanged.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.domain.completion import WorkItemStatus
from confflow.execution import command_resolution, mlip_executor, work_item_executor
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import WorkItemExecutor
from tests.v4 import test_arch_review_launch_identity as h
from tests.v4._builders import run_inputs, structure_set
from tests.v4._helpers import v43_coverage
from tests.v4._helpers.v42_executors import (
    ORCA_NATIVE,
    assemble_items,
    calculation_doc,
    compile_plan,
    item_context,
)


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep server config and quota state inside tmp_path."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _write_wrapper(path: Path, body: str) -> None:
    """Write an executable python wrapper whose body runs before the real program."""
    path.write_text(f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n" + body)
    path.chmod(0o755)


def _lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _energies(report: Any) -> list[float]:
    return [
        float(result.value)
        for step in report.step_results
        for result in step.results
        if result.kind == "energy"
    ]


def _codes(report: Any) -> list[str]:
    return [
        d.code
        for step in report.step_results
        for item in step.item_results
        for d in item.diagnostics
    ]


# -- (i) MLIP: a program installed ahead of the measured one after measurement ----------


@pytest.fixture
def mlip_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Measured interpreter A; B appears in the higher-priority directory at the probe."""
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    log = tmp_path / "mlip-launches.log"
    trigger = tmp_path / "install-on-probe"
    trigger.touch()
    target = b / "python3"
    template = tmp_path / "interpreter-B.txt"
    template.write_text(
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a') as f: f.write('launch:B\\n')\n"
        "os.environ['REVIEW_RUNTIME_ENERGY'] = '-10'\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )
    _write_wrapper(
        a / "python3",
        "if '-c' in sys.argv[1:]:\n"
        f"    with Path({str(log)!r}).open('a') as f: f.write('probe:A\\n')\n"
        f"    if Path({str(trigger)!r}).exists():\n"
        f"        Path({str(target)!r}).write_text(Path({str(template)!r}).read_text())\n"
        f"        Path({str(target)!r}).chmod(0o755)\n"
        f"        Path({str(trigger)!r}).unlink()\n"
        "else:\n"
        f"    with Path({str(log)!r}).open('a') as f: f.write('launch:A\\n')\n"
        "os.environ['REVIEW_RUNTIME_ENERGY'] = '-5'\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
    )
    runner = tmp_path / "fake_mlip_runner.py"
    text = h._MLIP_FAKE_RUNNER.replace("import json", "import json, os")
    text = text.replace(
        'energy = float(Path(job["model_path"]).read_text(encoding="utf-8").strip())',
        'energy = float(os.environ["REVIEW_RUNTIME_ENERGY"])',
    )
    runner.write_text(text, encoding="utf-8")
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", runner)
    model = tmp_path / "model.pt"
    model.write_text("constant model bytes", encoding="utf-8")
    doc = h._mlip_doc(model, "python3")
    doc["steps"][0]["execution"]["env"]["PATH"] = os.pathsep.join(
        (str(b), str(a), os.environ["PATH"])
    )
    doc["steps"][0]["execution"]["allowed_executables"] = ["python3"]
    return {"doc": doc, "log": log, "target": target, "root": tmp_path}


def test_mlip_program_installed_after_measurement_is_not_launched(
    mlip_pin: dict[str, Any],
) -> None:
    doc, log, target, root = mlip_pin["doc"], mlip_pin["log"], mlip_pin["target"], mlip_pin["root"]
    first = h._run_mlip(root / "mlip-run", doc)
    # The measured A runs; B exists but is never launched and never names the stored result.
    assert first.status == "completed"
    assert [line for line in _lines(log) if line.startswith("launch:")] == ["launch:A"]
    assert _energies(first) == [-5.0]

    # Restore the originally measured A (B removed); the same run root resumes from its record.
    target.unlink()
    resumed = h._run_mlip(root / "mlip-run", doc)
    assert resumed.status == "completed"
    assert _energies(resumed) == [-5.0]
    assert "reuse_hit" in _codes(resumed)
    assert [line for line in _lines(log) if line.startswith("launch:")] == ["launch:A"]

    control = h._run_mlip(root / "mlip-control", doc)
    assert _energies(control) == [-5.0]


# -- (ii) native: a program installed ahead of the measured one between two items -----


def test_native_second_item_keeps_the_measured_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    log = tmp_path / "native-launches.log"
    target = b / "orca"
    template = tmp_path / "orca-B.txt"
    template.write_text(
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a') as f: f.write('B\\n')\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(v43_coverage.FAKE_ORCA)!r}, *sys.argv[1:]])\n"
    )
    # Every launch of A installs B ahead of it, so the second item would start B on a fresh search.
    _write_wrapper(
        a / "orca",
        f"with Path({str(log)!r}).open('a') as f: f.write('A\\n')\n"
        f"Path({str(target)!r}).write_text(Path({str(template)!r}).read_text())\n"
        f"Path({str(target)!r}).chmod(0o755)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, {str(v43_coverage.FAKE_ORCA)!r}, *sys.argv[1:]])\n",
    )
    doc = v43_coverage._document()
    step = doc["steps"][0]
    step["scheduler"]["max_parallel_items"] = 1
    step["execution"] = {
        "binding_id": "test",
        "executable": "orca",
        "allowed_executables": ["orca"],
        "env": {
            "PATH": os.pathsep.join((str(b), str(a), os.environ["PATH"])),
            "FAKE_MODE": "success_opt",
        },
    }
    report = V4RunApplication().run(
        V4RunRequest(
            workflow_document=doc,
            run_inputs=run_inputs(structures={"structures": structure_set("s0", "s1")}),
            run_root=str(tmp_path / "native-run"),
            supervisor=NativeProcessSupervisor(),
        )
    )
    assert report.status == "completed"
    assert _lines(log) == ["A", "A"]


# -- (iii) direct executor call without a resolved command resolves once per entry ----


def test_direct_item_without_resolved_command_resolves_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    original = v43_coverage.FAKE_ORCA.read_text(encoding="utf-8")
    fake = bin_dir / "orca"
    fake.write_text("#!" + sys.executable + "\n" + original.split("\n", 1)[1], encoding="utf-8")
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, "orca"))
    items = assemble_items(plan, structure_set("s0"))
    run_root = tmp_path / "run"
    context = item_context(
        plan,
        "orca",
        "orca",
        str(run_root),
        str(run_root / "items"),
        NativeProcessSupervisor(),
        ["normal_termination"],
    )

    looked_up: list[str] = []
    real_locate = command_resolution.locate_command

    def counting(configured: str, env: Any) -> Any:
        looked_up.append(configured)
        return real_locate(configured, env)

    monkeypatch.setattr(command_resolution, "locate_command", counting)
    monkeypatch.setattr(work_item_executor, "locate_command", counting, raising=False)
    result = WorkItemExecutor().execute(items[0], context)
    assert result.status is WorkItemStatus.COMPLETED
    assert looked_up == ["orca"]
