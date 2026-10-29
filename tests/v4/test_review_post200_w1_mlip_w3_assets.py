#!/usr/bin/env python3

"""Post-#200 review W1 (MLIP) and W3 (model and refs files): measured inputs hold until launch.

W1  The MLIP interpreter and the copied runner are checked again right before submit, after the
    real server quota is held; a replacement during the wait is refused and never launched.
W3  The runner verifies the model and refs bytes the executor measured, before any load, pins
    their stat fingerprints, and refuses a file that changed before or during each backend load.
    Multi-worker jobs are refused the same way.  A refusal is a FAILED item; resuming after the
    file is restored recomputes the energy and never reuses a result from the replaced file.

Scientific programs are replaced by fixtures: the interpreter is a wrapper, and the runner is the
real ``mlip_runner.py`` source with only its UMA backend swapped for a backend that reads the
energy (in Hartree) from the model file, so the real pin, check and launch code all run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.errors import DomainError
from confflow.execution import environment, mlip_executor
from confflow.execution import mlip_runner as mr
from confflow.execution.mlip_executor import MODEL_ENV, REFS_ENV
from confflow.execution.quota import ServerQuota
from tests.v4 import test_arch_review_launch_identity as h

GIB = 1024**3
_REAL_RUNNER = Path(mr.__file__)
_BACKEND_CALL = "    return _FairchemBackend(job)"
_FIXTURE_BACKEND = '''
class _ModelFileBackend:
    """Fixture backend: the model file holds the energy in Hartree, read once at load."""

    def __init__(self, job):
        with open(job["model_path"], encoding="utf-8") as handle:
            self.value = float(handle.read().strip())

    def energy(self, symbols, coords):
        return self.value * EV_PER_EH, 1

'''


def _fixture_runner_source() -> str:
    """Return the real runner with its UMA backend replaced by the model-file fixture backend."""
    source = _REAL_RUNNER.read_text(encoding="utf-8")
    assert source.count(_BACKEND_CALL) == 1
    source = source.replace(_BACKEND_CALL, "    return _ModelFileBackend(job)")
    marker = 'if __name__ == "__main__":'
    assert source.count(marker) == 1
    return source.replace(marker, _FIXTURE_BACKEND + marker)


@pytest.fixture
def quota_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[int], tuple[ServerQuota, Path]]:
    """Return a function that configures a real server quota with its own state and config."""

    def configure(total_cores: int) -> tuple[ServerQuota, Path]:
        config = tmp_path / "server.toml"
        config.write_text(f"total_cores = {total_cores}\ntotal_memory = '4GB'\n")
        state = tmp_path / "quota-state"
        monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(config))
        monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(state))
        return ServerQuota(total_cores, 4 * GIB, state), state

    return configure


class _ObserveWaiting(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.waiting = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("quota waiting:"):
            self.waiting.set()


def _held_run(run: Any, swap: Any, manager: ServerQuota, cores: int = 1) -> Any:
    """Hold the quota, run *run* until it is observed waiting, apply *swap*, then release.

    The wait is the real quota wait: the holder lease occupies the cores the item needs, and the
    "quota waiting:" log line is the observation point.  Nothing in the acquire/submit path is
    replaced.
    """
    lease = manager.acquire(cores, GIB, "review-holder", "holder")
    observation = _ObserveWaiting()
    logger = logging.getLogger("confflow.execution.quota")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(observation)
    box: dict[str, Any] = {}

    def task() -> None:
        try:
            box["report"] = run()
        except BaseException as error:  # surfaced below, never swallowed
            box["exception"] = error

    thread = threading.Thread(target=task, daemon=True)
    try:
        thread.start()
        assert observation.waiting.wait(60), "the item never waited on the real quota"
        swap()
    finally:
        manager.release(lease)
        thread.join(120)
        logger.removeHandler(observation)
        logger.setLevel(previous)
    assert not thread.is_alive()
    if "exception" in box:
        raise box["exception"]
    return box["report"]


def _assert_no_leases(state: Path) -> None:
    """Assert that no quota lease is left in the state file, so the refused launch released it."""
    path = state / "server-quota.json"
    leases = json.loads(path.read_text(encoding="utf-8")).get("leases") if path.exists() else None
    assert not leases, leases


def _outcome(report: Any) -> dict[str, Any]:
    items = [item for step in report.step_results for item in step.item_results]
    return {
        "status": report.status,
        "energies": [
            float(result.value)
            for step in report.step_results
            for result in step.results
            if result.kind == "energy"
        ],
        "errors": [item.error.message for item in items if item.error is not None],
        "codes": [d.code for item in items for d in item.diagnostics],
        "messages": [d.message for item in items for d in item.diagnostics],
    }


def _assert_refused(outcome: dict[str, Any], reason: str) -> None:
    assert outcome["status"] != "completed", outcome
    assert outcome["energies"] == [], outcome
    texts = [*outcome["errors"], *outcome["messages"]]
    assert any(reason in text for text in texts), outcome


def _mlip_document(
    model: Path, executable: str, *, workers: int = 1, refs: Path | None = None
) -> dict[str, Any]:
    document = h._mlip_doc(model, executable)
    step = document["steps"][0]
    step["resources"] = {"cores_per_item": workers, "memory_per_item": "1GB"}
    step["execution"]["env"]["CONFFLOW_MLIP_THREADS"] = "1"
    if refs is not None:
        step["execution"]["env"][REFS_ENV] = str(refs)
    return document


def _install_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    runner = tmp_path / "fixture_mlip_runner.py"
    runner.write_text(_fixture_runner_source(), encoding="utf-8")
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", runner)
    return runner


def _interpreter_wrapper(log: Path, label: str) -> str:
    """Return a launcher script that records its label in *log* and runs the real interpreter."""
    import sys

    return (
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a') as handle: handle.write({label!r} + '\\n')\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n"
    )


# ---------------------------------------------------------------- W3 model and refs (real quota)


def test_model_replaced_during_quota_wait_is_refused_and_resume_recomputes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quota_env: Any
) -> None:
    import sys

    manager, state = quota_env(1)
    _install_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5", encoding="utf-8")
    document = _mlip_document(model, sys.executable)
    run_root = tmp_path / "run"

    first = _held_run(
        lambda: h._run_mlip(run_root, document),
        lambda: model.write_text("-10", encoding="utf-8"),
        manager,
    )
    _assert_refused(_outcome(first), "model file changed after it was measured")

    model.write_text("-5", encoding="utf-8")
    resumed = h._run_mlip(run_root, document)
    fresh = h._run_mlip(tmp_path / "control", document)
    assert _outcome(resumed)["energies"] == pytest.approx([-5.0]), _outcome(resumed)
    assert _outcome(fresh)["energies"] == pytest.approx([-5.0])
    assert all(abs(value + 10.0) > 1e-6 for value in _outcome(resumed)["energies"])
    _assert_no_leases(state)


def test_refs_replaced_during_quota_wait_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quota_env: Any
) -> None:
    import sys

    manager, state = quota_env(1)
    _install_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5", encoding="utf-8")
    refs = tmp_path / "refs.yaml"
    refs.write_text("refs-a\n", encoding="utf-8")
    document = _mlip_document(model, sys.executable, refs=refs)
    run_root = tmp_path / "run"

    first = _held_run(
        lambda: h._run_mlip(run_root, document),
        lambda: refs.write_text("refs-b\n", encoding="utf-8"),
        manager,
    )
    _assert_refused(_outcome(first), "refs file changed after it was measured")

    refs.write_text("refs-a\n", encoding="utf-8")
    resumed = h._run_mlip(run_root, document)
    assert _outcome(resumed)["energies"] == pytest.approx([-5.0]), _outcome(resumed)
    _assert_no_leases(state)


def test_multi_worker_model_replaced_during_quota_wait_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quota_env: Any
) -> None:
    import sys

    manager, _state = quota_env(2)
    _install_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5", encoding="utf-8")
    document = _mlip_document(model, sys.executable, workers=2)
    run_root = tmp_path / "run"

    first = _held_run(
        lambda: h._run_mlip(run_root, document),
        lambda: model.write_text("-10", encoding="utf-8"),
        manager,
        cores=1,
    )
    _assert_refused(_outcome(first), "model file changed after it was measured")
    for path in run_root.rglob("mlip_results.jsonl"):
        assert path.read_text(encoding="utf-8").strip() == "", path


# ---------------------------------------------------------------- W1 MLIP interpreter and runner copy


def test_interpreter_replaced_during_quota_wait_is_refused_and_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quota_env: Any
) -> None:
    manager, _state = quota_env(1)
    _install_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5", encoding="utf-8")
    launches = tmp_path / "launches.log"
    executable = tmp_path / "python-engine"
    executable.write_text(_interpreter_wrapper(launches, "A"), encoding="utf-8")
    executable.chmod(0o755)
    replacement = tmp_path / "python-engine.new"
    replacement.write_text(_interpreter_wrapper(launches, "B"), encoding="utf-8")
    replacement.chmod(0o755)
    document = _mlip_document(model, str(executable))
    run_root = tmp_path / "run"

    first = _held_run(
        lambda: h._run_mlip(run_root, document),
        lambda: replacement.replace(executable),
        manager,
    )
    _assert_refused(_outcome(first), "changed after it was resolved and measured")
    assert not launches.exists() or "B" not in launches.read_text(encoding="utf-8").split()

    executable.write_text(_interpreter_wrapper(launches, "A"), encoding="utf-8")
    executable.chmod(0o755)
    resumed = h._run_mlip(run_root, document)
    assert _outcome(resumed)["energies"] == pytest.approx([-5.0]), _outcome(resumed)
    assert "B" not in launches.read_text(encoding="utf-8").split()


def test_runner_copy_tampered_before_submit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quota_env: Any
) -> None:
    import sys

    manager, _state = quota_env(1)
    _install_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5", encoding="utf-8")
    document = _mlip_document(model, sys.executable)
    run_root = tmp_path / "run"

    def tamper() -> None:
        copies = list(run_root.rglob("mlip_runner.py"))
        assert len(copies) == 1, copies
        copies[0].write_text("raise SystemExit(0)\n", encoding="utf-8")

    first = _held_run(lambda: h._run_mlip(run_root, document), tamper, manager)
    _assert_refused(_outcome(first), "changed after it was measured")
    for results in run_root.rglob("mlip_results.jsonl"):
        assert results.read_text(encoding="utf-8").strip() == ""

    resumed = h._run_mlip(run_root, document)
    assert _outcome(resumed)["energies"] == pytest.approx([-5.0]), _outcome(resumed)


# ---------------------------------------------------------------- digest pin (identity unchanged)


def _legacy_identity(
    *,
    interpreter: Path,
    runner: Path,
    model: Path,
    refs: Path | None,
    packages: dict[str, str | None],
) -> str:
    """Return the identity formula exactly as it was before the components split (pinned reference)."""

    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    payload = {
        "kind": "confflow.mlip.runtime.v1",
        "interpreter_sha256": sha(interpreter),
        "runner_sha256": sha(runner),
        "model_sha256": sha(model),
        "refs_sha256": sha(refs) if refs is not None else None,
        "packages": packages,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


#: Identity of the ``_identity_fixture`` files, produced by the pre-change implementation
#: (``git show HEAD:confflow/execution/environment.py``).  Changing it re-measures every stored
#: MLIP result, so it must not change.
_PINNED_FIXTURE_IDENTITY = "sha256:7293be5b7d0b49d74985b6f8ac1f72196b005dab391c6c7fdede0fa7c54d7954"


def _identity_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    interpreter = tmp_path / "python-engine"
    interpreter.write_bytes(b"#!/bin/sh\necho interpreter\n")
    interpreter.chmod(0o755)
    runner = tmp_path / "runner.py"
    runner.write_bytes(b"print('runner')\n")
    model = tmp_path / "model.pt"
    model.write_bytes(b"-5")
    refs = tmp_path / "refs.yaml"
    refs.write_bytes(b"refs")
    packages: dict[str, str | None] = {
        "fairchem-core": "1.2.3",
        "torch": None,
        "ase": "3.4.0",
        "numpy": "2.0.1",
    }
    monkeypatch.setattr(
        environment, "_interpreter_package_versions", lambda real, env: dict(packages)
    )
    return {
        "interpreter": interpreter,
        "runner": runner,
        "model": model,
        "refs": refs,
        "packages": packages,
    }


def test_identity_digest_is_byte_identical_to_the_pre_change_formula(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _identity_fixture(tmp_path, monkeypatch)
    env = {MODEL_ENV: str(fx["model"]), REFS_ENV: str(fx["refs"])}
    legacy = _legacy_identity(
        interpreter=fx["interpreter"],
        runner=fx["runner"],
        model=fx["model"],
        refs=fx["refs"],
        packages=fx["packages"],
    )
    digest = environment.mlip_runtime_identity(
        interpreter=str(fx["interpreter"]),
        runner_path=str(fx["runner"]),
        env=env,
        model_env=MODEL_ENV,
        refs_env=REFS_ENV,
    )
    assert digest == legacy
    assert digest == _PINNED_FIXTURE_IDENTITY


def test_identity_components_digest_the_same_payload_and_keep_paths_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _identity_fixture(tmp_path, monkeypatch)
    env = {MODEL_ENV: str(fx["model"])}
    components = environment.mlip_runtime_components(
        interpreter=str(fx["interpreter"]),
        runner_path=str(fx["runner"]),
        env=env,
        model_env=MODEL_ENV,
        refs_env=REFS_ENV,
    )
    assert components is not None
    payload = components["payload"]
    assert set(payload) == {
        "kind",
        "interpreter_sha256",
        "runner_sha256",
        "model_sha256",
        "refs_sha256",
        "packages",
    }
    assert components["paths"] == {"model_path": str(fx["model"]), "refs_path": None}
    assert str(fx["model"]) not in json.dumps(payload)
    assert environment.mlip_identity_of_payload(payload) == environment.mlip_runtime_identity(
        interpreter=str(fx["interpreter"]),
        runner_path=str(fx["runner"]),
        env=env,
        model_env=MODEL_ENV,
        refs_env=REFS_ENV,
    )


def test_identity_does_not_change_when_the_model_file_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = _identity_fixture(tmp_path, monkeypatch)
    moved = tmp_path / "elsewhere"
    moved.mkdir()
    copy = moved / "model.pt"
    copy.write_bytes(fx["model"].read_bytes())
    first = environment.mlip_runtime_identity(
        interpreter=str(fx["interpreter"]),
        runner_path=str(fx["runner"]),
        env={MODEL_ENV: str(fx["model"])},
        model_env=MODEL_ENV,
        refs_env=REFS_ENV,
    )
    second = environment.mlip_runtime_identity(
        interpreter=str(fx["interpreter"]),
        runner_path=str(fx["runner"]),
        env={MODEL_ENV: str(copy)},
        model_env=MODEL_ENV,
        refs_env=REFS_ENV,
    )
    assert first == second is not None


# ---------------------------------------------------------------- runner unit checks


def _job(tmp_path: Path, *, expected: bool = True, refs: bool = False) -> dict[str, Any]:
    model = tmp_path / "model.pt"
    if not model.exists():
        model.write_text("-5", encoding="utf-8")
    job: dict[str, Any] = {
        "model_path": str(model),
        "refs_path": None,
        "task": "omol",
        "device": "cpu",
        "charge": 0,
        "multiplicity": 1,
        "mode": "sp",
        "optimizer": "lbfgs",
        "fmax": 0.02,
        "max_steps": 10,
        "fix_pairs": [],
        "pick": None,
        "workers": 1,
        "threads": 1,
        "structures": [
            {"key": "s00001", "label": None, "symbols": ["He"], "coords": [[0.0, 0.0, 0.0]]}
        ],
    }
    if refs:
        refs_file = tmp_path / "refs.yaml"
        refs_file.write_text("refs\n", encoding="utf-8")
        job["refs_path"] = str(refs_file)
    if expected:
        job["expected_model_sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
        if refs:
            job["expected_refs_sha256"] = hashlib.sha256(
                Path(job["refs_path"]).read_bytes()
            ).hexdigest()
    return job


class _Backend:
    def __init__(self) -> None:
        self.loads = 0


def test_runner_pins_a_matching_model_and_loads_it(tmp_path: Path) -> None:
    job = _job(tmp_path)
    mr.pin_measured_assets(job)
    assert "model_path" in job["pinned_fingerprints"]
    backend = mr._load_backend(lambda _job: _Backend(), job)
    assert isinstance(backend, _Backend)


def test_runner_refuses_a_model_whose_bytes_differ_from_the_measured_sha(tmp_path: Path) -> None:
    job = _job(tmp_path)
    Path(job["model_path"]).write_text("-10", encoding="utf-8")
    with pytest.raises(mr.BackendLoadError, match="model file changed after it was measured"):
        mr.pin_measured_assets(job)


def test_runner_refuses_a_refs_file_whose_bytes_differ_from_the_measured_sha(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path, refs=True)
    Path(job["refs_path"]).write_text("other\n", encoding="utf-8")
    with pytest.raises(mr.BackendLoadError, match="refs file changed after it was measured"):
        mr.pin_measured_assets(job)


def test_runner_refuses_a_model_changed_while_it_was_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _job(tmp_path)
    real_sha = mr._sha256
    model = Path(job["model_path"])

    def hash_then_touch(path: str) -> str | None:
        digest = real_sha(path)
        import os

        stat = os.stat(path)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        return digest

    monkeypatch.setattr(mr, "_sha256", hash_then_touch)
    with pytest.raises(mr.BackendLoadError, match="model file changed after it was measured"):
        mr.pin_measured_assets(job)
    assert model.is_file()


def test_runner_refuses_a_model_replaced_between_pin_and_load(tmp_path: Path) -> None:
    job = _job(tmp_path)
    mr.pin_measured_assets(job)
    calls: list[str] = []
    model = Path(job["model_path"])
    model.write_text("-10", encoding="utf-8")  # same path, new bytes and stat fingerprint

    def factory(_job: dict[str, Any]) -> _Backend:
        calls.append("factory")
        return _Backend()

    with pytest.raises(mr.BackendLoadError, match="model file changed after it was measured"):
        mr._load_backend(factory, job)
    assert calls == []


def test_runner_refuses_a_model_replaced_while_the_backend_loads(tmp_path: Path) -> None:
    job = _job(tmp_path)
    mr.pin_measured_assets(job)
    model = Path(job["model_path"])

    def factory(_job: dict[str, Any]) -> _Backend:
        model.write_text("-10", encoding="utf-8")  # changed under the loader, after the pre-check
        return _Backend()

    with pytest.raises(mr.BackendLoadError, match="model file changed after it was measured"):
        mr._load_backend(factory, job)


def test_runner_refuses_refs_replaced_before_load(tmp_path: Path) -> None:
    job = _job(tmp_path, refs=True)
    mr.pin_measured_assets(job)
    Path(job["refs_path"]).write_text("other\n", encoding="utf-8")
    with pytest.raises(mr.BackendLoadError, match="refs file changed after it was measured"):
        mr._load_backend(lambda _job: _Backend(), job)


def test_worker_initializer_records_the_refusal_instead_of_loading(
    tmp_path: Path,
) -> None:
    job = _job(tmp_path)
    mr.pin_measured_assets(job)
    Path(job["model_path"]).write_text("-10", encoding="utf-8")
    try:
        mr._init_worker(lambda _job: _Backend(), job)
        assert mr._BACKEND is None
        assert "model file changed after it was measured" in str(mr._BACKEND_ERROR)
    finally:
        mr._BACKEND, mr._BACKEND_ERROR = None, None


def test_job_without_expected_sha_is_not_pinned_and_loads_as_before(tmp_path: Path) -> None:
    job = _job(tmp_path, expected=False)
    Path(job["model_path"]).write_text("-10", encoding="utf-8")
    mr.pin_measured_assets(job)
    assert "pinned_fingerprints" not in job
    assert isinstance(mr._load_backend(lambda _job: _Backend(), job), _Backend)


def test_runner_rejects_an_expected_refs_sha_without_a_refs_path(tmp_path: Path) -> None:
    job = _job(tmp_path)
    job["expected_refs_sha256"] = "00"
    with pytest.raises(ValueError, match="expected_refs_sha256"):
        mr.validate_job(job)


# ---------------------------------------------------------------- executor pre-queue and launch checks


def test_measured_job_fields_pin_the_measured_paths_and_refuse_a_moved_path() -> None:
    expected = FrozenDict(
        {
            "model_path": "/m/model.pt",
            "refs_path": None,
            "model_sha256": "aa",
            "refs_sha256": None,
            "runner_sha256": "rr",
        }
    )
    assert mlip_executor._measured_job_fields(None, "/m/model.pt", None) == {}
    assert mlip_executor._measured_job_fields(expected, "/m/model.pt", None) == {
        "expected_model_sha256": "aa"
    }
    with pytest.raises(DomainError, match="path differs from the one measured"):
        mlip_executor._measured_job_fields(expected, "/other/model.pt", None)
    with pytest.raises(DomainError, match="path differs from the one measured"):
        mlip_executor._measured_job_fields(expected, "/m/model.pt", "/m/refs.yaml")


def test_runner_copy_check_refuses_a_different_copy(tmp_path: Path) -> None:
    copy = tmp_path / "mlip_runner.py"
    copy.write_text("print(1)\n", encoding="utf-8")
    measured = hashlib.sha256(copy.read_bytes()).hexdigest()
    mlip_executor._check_runner_copy(str(copy), measured)
    copy.write_text("print(2)\n", encoding="utf-8")
    with pytest.raises(DomainError, match="changed after it was measured"):
        mlip_executor._check_runner_copy(str(copy), measured)
