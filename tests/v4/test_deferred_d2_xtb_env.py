"""D2 regression: xtb launch environments come from the explicit ``base_env`` snapshot.

The calculator must not read ``os.environ``; it adds only the thread and stack overrides to the
snapshot it froze at construction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from confflow.execution import confgen_xtb
from confflow.execution.confgen_xtb import XtbCalculator
from confflow.science.confgen.calculator import RelaxRequest

_ELS = ["H", "H"]
_COORDS = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]])
# OMP_* in the snapshot must be overridden by the launch overrides, not kept.
_BASE = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "OMP_NUM_THREADS": "99",
    "OMP_STACKSIZE": "1G",
}
_HESSIAN_EXPECTED = dict(
    _BASE, OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", OMP_STACKSIZE="2G"
)
_RELAX_EXPECTED = dict(
    _BASE, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", OMP_STACKSIZE="2G"
)


class _Done:
    """A child that has already exited with status 0."""

    returncode = 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _Recorder:
    """Stand-in for ``subprocess.Popen`` that records the ``env`` of every launch."""

    def __init__(self) -> None:
        self.envs: list[dict[str, str]] = []

    def __call__(self, argv: Any, **kwargs: Any) -> _Done:
        self.envs.append(dict(kwargs["env"]))
        return _Done()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(confgen_xtb.subprocess, "Popen", rec)
    return rec


def _calc(tmp_path: Path, base_env: dict[str, str], cores: int = 2) -> XtbCalculator:
    return XtbCalculator("xtb", tmp_path / "work", _ELS, 0, 0, 5, cores, base_env=base_env)


def test_base_env_is_required(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        XtbCalculator("xtb", tmp_path, _ELS, 0, 0, 5, 1)  # type: ignore[call-arg]


def test_base_env_is_keyword_only(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        XtbCalculator("xtb", tmp_path, _ELS, 0, 0, 5, 1, "", 1.0, _BASE)  # type: ignore[misc]


def test_hessian_launch_env_is_base_env_plus_thread_overrides(
    tmp_path: Path, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("D2_PROCESS_ONLY", "leaked")
    calc = _calc(tmp_path, _BASE, cores=2)
    calc.hessian(RelaxRequest("h", _COORDS))
    assert recorder.envs == [_HESSIAN_EXPECTED]
    assert "D2_PROCESS_ONLY" not in recorder.envs[0]


def test_relax_launch_env_is_base_env_plus_thread_overrides(
    tmp_path: Path, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("D2_PROCESS_ONLY", "leaked")
    calc = _calc(tmp_path, _BASE)
    calc.relax_many([RelaxRequest("a", _COORDS)])
    assert recorder.envs == [_RELAX_EXPECTED]


def test_base_env_is_frozen_at_construction(
    tmp_path: Path, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = dict(_BASE)
    calc = _calc(tmp_path, source)
    source["PATH"] = "/changed"
    source["NEW_KEY"] = "later"
    monkeypatch.setenv("PATH", "/process/changed")
    monkeypatch.setenv("D2_PROCESS_ONLY", "later")
    calc.relax_many([RelaxRequest("a", _COORDS)])
    assert recorder.envs == [_RELAX_EXPECTED]


def test_calculator_does_not_read_os_environ(
    tmp_path: Path, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    calc = _calc(tmp_path, _BASE)
    monkeypatch.setattr(os, "environ", {})
    calc.hessian(RelaxRequest("h", _COORDS))
    assert recorder.envs == [_HESSIAN_EXPECTED]
