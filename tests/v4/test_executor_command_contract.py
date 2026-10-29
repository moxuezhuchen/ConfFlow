"""Executor command contract: one resolution binds policy, measurement and launch (V2, V3).

Every case goes through the public application entry (``V4RunApplication.run``) and
asserts what is externally visible: the run status, the program that actually started
(a labelled wrapper records every launch), and whether a forbidden program started at all.

Control-process PATH and the step ``execution.env.PATH`` deliberately prefer different
program directories.  The step PATH is the one that must decide what is checked, measured
and launched.

Cases per executor:
  (a) bare name on the allow list          -> completes, the step-PATH program starts
  (b) the resolved absolute path allowed   -> completes, the step-PATH program starts
  (c) another absolute path allowed        -> refused, nothing starts
  (d) control PATH -> A, step PATH -> B    -> measured and launched B; allowing only A refuses

ConfGen xTB reuses the N2 fixtures of ``test_arch_review_1029_confgen_xtb_identity``.
Scientific programs are replaced by labelled fake programs; nothing real is run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.domain.errors import DomainError
from confflow.execution.process import NativeProcessSupervisor
from tests.v4 import test_arch_review_1029_confgen_xtb_identity as cg
from tests.v4 import test_arch_review_launch_identity as launch
from tests.v4._builders import run_inputs, structure_set
from tests.v4._helpers import v43_coverage


@pytest.fixture
def toy_text() -> str:
    """Return the N2 toy structure (the same text the ConfGen identity tests use)."""
    return cg.toy_text.__wrapped__()


@pytest.fixture
def two_xtb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Return the N2 fake xtb pair: control PATH prefers ``a``, binding PATH prefers ``b``."""
    return cg.two_xtb.__wrapped__(tmp_path, monkeypatch)


_PY = sys.executable
_SCRIPT_BODY = (
    "from pathlib import Path\nPath('final.xyz').write_text('1\\nscript\\nHe 0 0 0\\n')\n"
)


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep server config and quota state inside tmp_path."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _labelled_program(
    directory: Path,
    name: str,
    label: str,
    log: Path,
    prefix: Path | None,
    marker: str | None = None,
) -> str:
    """Write ``directory/name``: records *label* in *log*, then runs the real program.

    The real program is the python interpreter, with *prefix* (a fake program script) first
    when given.  With *marker*, only invocations whose arguments contain it are recorded
    (MLIP interpreter probes do not count as launches).  Returns the absolute path.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    head = [_PY] + ([str(prefix)] if prefix is not None else [])
    record = (
        f"with Path({str(log)!r}).open('a', encoding='utf-8') as handle:\n"
        f"    handle.write({label!r} + '\\n')\n"
    )
    if marker is not None:
        record = f"if {marker!r} in sys.argv[1:]:\n" + "".join(
            "    " + line + "\n" for line in record.splitlines()
        )
    target.write_text(
        f"#!{_PY}\n"
        "import os, sys\n"
        "from pathlib import Path\n" + record + f"os.execv({_PY!r}, {head!r} + sys.argv[1:])\n",
        encoding="utf-8",
    )
    target.chmod(0o755)
    return str(target)


def _launches(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _outcome(call: Any) -> str:
    """Return ``completed`` or ``refused``; a refusal is a DomainError or a non-completed run."""
    try:
        report = call()
    except DomainError:
        return "refused"
    return "completed" if report.status == "completed" else "refused"


# -- native -------------------------------------------------------------------------------


@pytest.fixture
def native_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Control PATH resolves ``orca`` to A; the step PATH (set by each test) prefers B."""
    log = tmp_path / "native-launches.log"
    dirs = {label: tmp_path / label.lower() for label in ("A", "B", "C")}
    paths = {
        label: _labelled_program(dirs[label], "orca", label, log, Path(v43_coverage.FAKE_ORCA))
        for label in dirs
    }
    original = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"{dirs['A']}{os.pathsep}{original}")
    return {"paths": paths, "log": log, "ambient": original, "dirs": dirs}


def _native_run(tmp_path: Path, native: dict[str, Any], *, allowed: list[str]) -> Any:
    doc = v43_coverage._document()
    step = doc["steps"][0]
    step["execution"]["executable"] = "orca"
    step["execution"]["allowed_executables"] = allowed
    step["execution"]["env"] = {
        "PATH": f"{native['dirs']['B']}{os.pathsep}{native['ambient']}",
        "FAKE_MODE": "success_opt",
    }
    request = V4RunRequest(
        workflow_document=doc,
        run_inputs=run_inputs(structures={"structures": structure_set("s0")}),
        run_root=str(tmp_path / "native-run"),
        supervisor=NativeProcessSupervisor(),
    )
    return V4RunApplication().run(request)


def test_native_bare_name_allow_launches_step_path_program(
    tmp_path: Path, native_dirs: dict[str, Any]
) -> None:
    outcome = _outcome(lambda: _native_run(tmp_path, native_dirs, allowed=["orca"]))
    assert outcome == "completed"
    assert _launches(native_dirs["log"]) == ["B"]


def test_native_resolved_absolute_allow_launches_step_path_program(
    tmp_path: Path, native_dirs: dict[str, Any]
) -> None:
    allowed = [native_dirs["paths"]["B"]]
    outcome = _outcome(lambda: _native_run(tmp_path, native_dirs, allowed=allowed))
    assert outcome == "completed"
    assert _launches(native_dirs["log"]) == ["B"]


def test_native_other_absolute_allow_refuses_without_launch(
    tmp_path: Path, native_dirs: dict[str, Any]
) -> None:
    allowed = [native_dirs["paths"]["C"]]
    outcome = _outcome(lambda: _native_run(tmp_path, native_dirs, allowed=allowed))
    assert outcome == "refused"
    assert _launches(native_dirs["log"]) == []


def test_native_control_path_allow_refuses_step_path_program(
    tmp_path: Path, native_dirs: dict[str, Any]
) -> None:
    # control PATH points at A and the step PATH at B: measurement and launch are B, so
    # allowing only A must refuse, and nothing starts
    allowed = [native_dirs["paths"]["A"]]
    outcome = _outcome(lambda: _native_run(tmp_path, native_dirs, allowed=allowed))
    assert outcome == "refused"
    assert _launches(native_dirs["log"]) == []


# -- registered script -------------------------------------------------------------------
#
# The registration (server config) fixes the interpreter identity by content, resolved on the
# control PATH (A).  The identity is part of compiled digests and item keys, so it cannot follow
# a step PATH; the launch must therefore be the registered content or be refused (fail closed).


@pytest.fixture
def script_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Control PATH resolves ``python3`` to A (the registration); B and C are other installs."""
    log = tmp_path / "script-launches.log"
    script = tmp_path / "echo_env.py"
    script.write_text(_SCRIPT_BODY, encoding="utf-8")
    (tmp_path / "server.toml").write_text(
        "total_cores = 96\n"
        "total_memory = '192GB'\n\n"
        "[scripts.echo]\n"
        f'command = ["python3", {str(script)!r}]\n',
        encoding="utf-8",
    )
    dirs = {label: tmp_path / label.lower() for label in ("A", "B", "C")}
    paths = {label: _labelled_program(dirs[label], "python3", label, log, None) for label in dirs}
    original = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"{dirs['A']}{os.pathsep}{original}")
    return {"paths": paths, "log": log, "ambient": original, "dirs": dirs}


def _script_run(tmp_path: Path, scripts: dict[str, Any], *, allowed: list[str], step: str) -> Any:
    doc = launch._script_doc(
        {
            "binding_id": "echo",
            "allowed_executables": allowed,
            "env": {"PATH": f"{scripts['dirs'][step]}{os.pathsep}{scripts['ambient']}"},
        }
    )
    return launch._run_script(tmp_path / "script-run", doc)


def test_script_bare_name_allow_with_registered_interpreter_completes(
    tmp_path: Path, script_dirs: dict[str, Any]
) -> None:
    # step PATH resolves to the registered file (A): identity, policy and launch agree
    outcome = _outcome(lambda: _script_run(tmp_path, script_dirs, allowed=["python3"], step="A"))
    assert outcome == "completed"
    assert _launches(script_dirs["log"]) == ["A"]


def test_script_resolved_absolute_allow_with_registered_interpreter_completes(
    tmp_path: Path, script_dirs: dict[str, Any]
) -> None:
    allowed = [script_dirs["paths"]["A"]]
    outcome = _outcome(lambda: _script_run(tmp_path, script_dirs, allowed=allowed, step="A"))
    assert outcome == "completed"
    assert _launches(script_dirs["log"]) == ["A"]


def test_script_other_absolute_allow_refuses_without_launch(
    tmp_path: Path, script_dirs: dict[str, Any]
) -> None:
    allowed = [script_dirs["paths"]["C"]]
    outcome = _outcome(lambda: _script_run(tmp_path, script_dirs, allowed=allowed, step="A"))
    assert outcome == "refused"
    assert _launches(script_dirs["log"]) == []


def test_script_split_bare_allow_refuses_identity_mismatch(
    tmp_path: Path, script_dirs: dict[str, Any]
) -> None:
    # registered identity is A (control PATH); the step PATH launches B: refused, nothing starts
    outcome = _outcome(lambda: _script_run(tmp_path, script_dirs, allowed=["python3"], step="B"))
    assert outcome == "refused"
    assert _launches(script_dirs["log"]) == []


def test_script_split_step_absolute_allow_refuses_identity_mismatch(
    tmp_path: Path, script_dirs: dict[str, Any]
) -> None:
    # the allow list names the step-PATH file B, but the identity is the registered A
    allowed = [script_dirs["paths"]["B"]]
    outcome = _outcome(lambda: _script_run(tmp_path, script_dirs, allowed=allowed, step="B"))
    assert outcome == "refused"
    assert _launches(script_dirs["log"]) == []


def test_script_split_control_path_allow_refuses(
    tmp_path: Path, script_dirs: dict[str, Any]
) -> None:
    allowed = [script_dirs["paths"]["A"]]
    outcome = _outcome(lambda: _script_run(tmp_path, script_dirs, allowed=allowed, step="B"))
    assert outcome == "refused"
    assert _launches(script_dirs["log"]) == []


# -- MLIP (interpreter of the UMA runner; the identity is measured on the launched file) ----


@pytest.fixture
def mlip_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Control PATH resolves ``python3`` to A; the step PATH (set by each test) prefers B."""
    log = tmp_path / "mlip-launches.log"
    dirs = {label: tmp_path / label.lower() for label in ("A", "B", "C")}
    paths = {
        label: _labelled_program(dirs[label], "python3", label, log, None, marker="mlip_runner.py")
        for label in dirs
    }
    model = tmp_path / "model.pt"
    model.write_text("-5\n", encoding="utf-8")
    launch._install_mlip_runner(monkeypatch, tmp_path)
    original = os.environ["PATH"]
    monkeypatch.setenv("PATH", f"{dirs['A']}{os.pathsep}{original}")
    return {"paths": paths, "log": log, "ambient": original, "dirs": dirs, "model": model}


def _mlip_run(tmp_path: Path, mlip: dict[str, Any], *, allowed: list[str], step: str) -> Any:
    doc = launch._mlip_doc(mlip["model"], "python3")
    doc["steps"][0]["resources"] = {"cores_per_item": 1, "memory_per_item": "1GB"}
    execution = doc["steps"][0]["execution"]
    execution["allowed_executables"] = allowed
    execution["env"]["PATH"] = f"{mlip['dirs'][step]}{os.pathsep}{mlip['ambient']}"
    execution["env"]["CONFFLOW_MLIP_THREADS"] = "1"
    return launch._run_mlip(tmp_path / "mlip-run", doc)


def test_mlip_bare_name_allow_launches_step_path_interpreter(
    tmp_path: Path, mlip_dirs: dict[str, Any]
) -> None:
    outcome = _outcome(lambda: _mlip_run(tmp_path, mlip_dirs, allowed=["python3"], step="B"))
    assert outcome == "completed"
    assert _launches(mlip_dirs["log"]) == ["B"]


def test_mlip_resolved_absolute_allow_launches_step_path_interpreter(
    tmp_path: Path, mlip_dirs: dict[str, Any]
) -> None:
    allowed = [mlip_dirs["paths"]["B"]]
    outcome = _outcome(lambda: _mlip_run(tmp_path, mlip_dirs, allowed=allowed, step="B"))
    assert outcome == "completed"
    assert _launches(mlip_dirs["log"]) == ["B"]


def test_mlip_other_absolute_allow_refuses_without_launch(
    tmp_path: Path, mlip_dirs: dict[str, Any]
) -> None:
    allowed = [mlip_dirs["paths"]["C"]]
    outcome = _outcome(lambda: _mlip_run(tmp_path, mlip_dirs, allowed=allowed, step="B"))
    assert outcome == "refused"
    assert _launches(mlip_dirs["log"]) == []


def test_mlip_control_path_allow_refuses_step_path_interpreter(
    tmp_path: Path, mlip_dirs: dict[str, Any]
) -> None:
    allowed = [mlip_dirs["paths"]["A"]]
    outcome = _outcome(lambda: _mlip_run(tmp_path, mlip_dirs, allowed=allowed, step="B"))
    assert outcome == "refused"
    assert _launches(mlip_dirs["log"]) == []


# -- ConfGen xTB (N2 fixtures: control PATH -> a, binding PATH -> b) -----------------------


def test_confgen_bare_name_allow_launches_binding_path_program(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    report = cg._run(cg._document(two_xtb, allowed=Path("xtb")), tmp_path / "run", toy_text)
    assert report.status == "completed"
    assert set(cg._launches(two_xtb["record"])) == {os.path.realpath(two_xtb["b"])}


def test_confgen_resolved_absolute_allow_launches_binding_path_program(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    report = cg._run(cg._document(two_xtb, allowed=two_xtb["b"]), tmp_path / "run", toy_text)
    assert report.status == "completed"
    assert set(cg._launches(two_xtb["record"])) == {os.path.realpath(two_xtb["b"])}


def test_confgen_other_absolute_allow_refuses_without_launch(
    tmp_path: Path, toy_text: str, two_xtb: dict[str, Any]
) -> None:
    outcome = _outcome(
        lambda: cg._run(cg._document(two_xtb, allowed=two_xtb["a"]), tmp_path / "run", toy_text)
    )
    assert outcome == "refused"
    assert cg._launches(two_xtb["record"]) == []
