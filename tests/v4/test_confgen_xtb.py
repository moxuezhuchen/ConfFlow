"""Unit tests for the GFN2-xTB calculator (``confflow.execution.confgen_xtb``).

Covers the pure helpers (restraint block, xyz round trip, executable resolution), the engine
identity, cancellation, and ``relax_many`` driven by the fake xtb executable shared with the
search-worker tests.  No real xtb binary is needed.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from confflow.execution import confgen_xtb
from confflow.execution.confgen_xtb import (
    Cancelled,
    XtbCalculator,
    _constrain_block,
    _fixed_block,
    _input_blocks,
    read_first_frame,
    resolve_xtb,
    write_xyz_frames,
)
from confflow.science.confgen.calculator import (
    CAP_FIXED_ATOMS,
    CAP_HESSIAN,
    CAP_RESTRAINED_RELAX,
    DistanceRestraint,
    FixedAtom,
    RelaxRequest,
)
from tests.v4._dg_search_helpers import _write_fake

_ELS = ["H", "H"]
_COORDS = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]])
_FAKE_ENV = (
    "FAKE_XTB_MODE",
    "FAKE_XTB_COUNT",
    "FAKE_XTB_RECORD",
    "FAKE_XTB_PAD",
    "FAKE_XTB_PIDS",
    "FAKE_XTB_SLOW_AFTER",
    "FAKE_XTB_STATE_DIR",
)


@pytest.fixture
def fake_xtb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return the fake xtb executable with every FAKE_XTB_* switch cleared."""
    for name in _FAKE_ENV:
        monkeypatch.delenv(name, raising=False)
    return _write_fake(tmp_path)


def _calc(exe: str | Path, tmp_path: Path, cores: int = 2) -> XtbCalculator:
    return XtbCalculator(
        str(exe), tmp_path / "work", _ELS, charge=0, uhf=0, max_cycles=5, cores=cores
    )


def test_constrain_block_empty_is_none() -> None:
    assert _constrain_block(()) is None


def test_constrain_block_renders_one_based_indices() -> None:
    block = _constrain_block((DistanceRestraint(0, 1, 1.5), DistanceRestraint(2, 3, 2.25)))
    assert block == (
        "$constrain\n  force constant=1.0\n  distance: 1, 2, 1.50000\n"
        "  distance: 3, 4, 2.25000\n$end\n"
    )


def test_xyz_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "frames.xyz"
    write_xyz_frames(path, _ELS, [(_COORDS, "frame")])
    back = read_first_frame(path, 2)
    assert back is not None
    np.testing.assert_allclose(back, _COORDS, atol=1e-6)


def test_read_first_frame_rejects_wrong_count(tmp_path: Path) -> None:
    path = tmp_path / "frames.xyz"
    write_xyz_frames(path, _ELS, [(_COORDS, "frame")])
    assert read_first_frame(path, 3) is None


def test_read_first_frame_missing_file(tmp_path: Path) -> None:
    assert read_first_frame(tmp_path / "absent.xyz", 2) is None


def test_resolve_xtb_paths(tmp_path: Path) -> None:
    assert resolve_xtb(str(tmp_path / "no-such-xtb")) is None
    exe = tmp_path / "xtb"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    assert resolve_xtb(str(exe)) == str(exe)


def test_info_identity_and_capabilities(tmp_path: Path) -> None:
    info = _calc("/bin/xtb", tmp_path).info
    assert info.program == "xtb"
    assert info.method == "GFN2-xTB"
    assert info.supports(CAP_RESTRAINED_RELAX)
    assert info.supports(CAP_HESSIAN)
    assert info.supports(CAP_FIXED_ATOMS)


def test_fixed_atoms_render_separately_from_distance_restraints() -> None:
    fixed = (FixedAtom(0, (0.0, 0.0, 0.0)), FixedAtom(3, (1.0, 2.0, 3.0)))
    assert _fixed_block(fixed) == "$fix\n  atoms: 1, 4\n$end\n"
    combined = _input_blocks((DistanceRestraint(0, 1, 1.5),), fixed)
    assert combined is not None
    assert "$constrain" in combined and "$fix" in combined
    assert "distance: 1, 2, 1.50000" in combined


def test_hessian_without_output_file_is_none(
    tmp_path: Path, fake_xtb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fake xtb never writes the ``hessian`` file, so the parse fails and the result is None.
    monkeypatch.setenv("FAKE_XTB_MODE", "ok")
    assert _calc(fake_xtb, tmp_path).hessian(RelaxRequest("h", _COORDS)) is None


class _StuckProcess:
    """A child that ignores terminate and only exits once it has been killed."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self.reaped = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int | None:
        if not self.killed:
            raise subprocess.TimeoutExpired("xtb", timeout or 0)
        self.reaped = True
        self.returncode = -9
        return self.returncode


def test_launch_reaps_process_killed_after_terminate_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _StuckProcess()
    monkeypatch.setattr(confgen_xtb.subprocess, "Popen", lambda *a, **k: proc)
    calc = _calc("/bin/xtb", tmp_path)
    calc._stop.set()
    with pytest.raises(Cancelled):
        calc._launch(["xtb"], tmp_path, dict(os.environ))
    assert proc.killed
    assert proc.reaped


def test_hessian_after_cancel_raises(tmp_path: Path, fake_xtb: Path) -> None:
    calc = _calc(fake_xtb, tmp_path)
    calc.cancel()
    with pytest.raises(Cancelled):
        calc.hessian(RelaxRequest("h", _COORDS))


def test_cancelled_batch_raises(tmp_path: Path) -> None:
    calc = _calc("/bin/xtb", tmp_path)
    calc.cancel()
    assert calc.cancelled
    with pytest.raises(Cancelled):
        calc.relax_many([RelaxRequest("a", _COORDS)])
    with pytest.raises(Cancelled):
        calc.relax_many([])


def test_relax_converged_returns_energy_and_coords(tmp_path: Path, fake_xtb: Path) -> None:
    (outcome,) = _calc(fake_xtb, tmp_path).relax_many([RelaxRequest("a", _COORDS)])
    assert outcome.converged is True
    assert outcome.energy_eh is not None
    assert outcome.coords is not None and outcome.coords.shape == (2, 3)
    assert outcome.wall_s >= 0.0


def test_relax_reuses_done_json_without_relaunch(
    tmp_path: Path, fake_xtb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    count = tmp_path / "count.txt"
    monkeypatch.setenv("FAKE_XTB_COUNT", str(count))
    calc = _calc(fake_xtb, tmp_path)
    first = calc.relax_many([RelaxRequest("a", _COORDS)])
    assert (tmp_path / "work" / "a" / "done.json").is_file()
    assert count.read_text(encoding="utf-8").count("x") == 1
    second = calc.relax_many([RelaxRequest("a", _COORDS)])
    assert count.read_text(encoding="utf-8").count("x") == 1
    assert second[0].energy_eh == first[0].energy_eh
    assert second[0].converged is True
    np.testing.assert_allclose(second[0].coords, first[0].coords)


def test_noconverge_gives_no_coords(
    tmp_path: Path, fake_xtb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_XTB_MODE", "noconverge")
    (outcome,) = _calc(fake_xtb, tmp_path).relax_many([RelaxRequest("a", _COORDS)])
    assert outcome.converged is False
    assert outcome.coords is None


def test_restraints_pass_input_file_and_hold_distance(
    tmp_path: Path, fake_xtb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_XTB_RECORD", str(record))
    req = RelaxRequest("r", _COORDS, (DistanceRestraint(0, 1, 1.5),))
    (outcome,) = _calc(fake_xtb, tmp_path).relax_many([req])
    rows = [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["argv"] == [
        str(fake_xtb),
        "in.xyz",
        "--gfn",
        "2",
        "--opt",
        "--input",
        "c.inp",
        "--chrg",
        "0",
        "--uhf",
        "0",
        "--cycles",
        "5",
    ]
    assert (tmp_path / "work" / "r" / "c.inp").read_text(encoding="utf-8").startswith("$constrain")
    assert outcome.coords is not None
    assert np.linalg.norm(outcome.coords[1] - outcome.coords[0]) == pytest.approx(1.5, abs=1e-4)


def test_unrestrained_run_has_no_input_flag(
    tmp_path: Path, fake_xtb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_XTB_RECORD", str(record))
    _calc(fake_xtb, tmp_path).relax_many([RelaxRequest("u", _COORDS)])
    row = json.loads(record.read_text(encoding="utf-8").splitlines()[-1])
    assert "--input" not in row["argv"]


def test_cancelled_run_is_not_cached(
    tmp_path: Path, fake_xtb: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    pids, count = tmp_path / "pids.txt", tmp_path / "count.txt"
    monkeypatch.setenv("FAKE_XTB_MODE", "sleep")
    monkeypatch.setenv("FAKE_XTB_PIDS", str(pids))
    calc, raised = _calc(fake_xtb, tmp_path), []

    def _run() -> None:
        try:
            calc.relax_many([RelaxRequest("a", _COORDS)])
        except Cancelled:
            raised.append(True)

    worker = threading.Thread(target=_run)
    worker.start()
    deadline = time.monotonic() + 20
    while not (pids.is_file() and pids.read_text(encoding="utf-8").strip()):
        assert time.monotonic() < deadline, "fake xtb never launched"
        time.sleep(0.02)
    calc.cancel()
    worker.join(timeout=40)
    assert raised == [True]
    assert not (tmp_path / "work" / "a" / "done.json").exists()
    monkeypatch.setenv("FAKE_XTB_MODE", "ok")
    monkeypatch.setenv("FAKE_XTB_COUNT", str(count))
    (outcome,) = _calc(fake_xtb, tmp_path).relax_many([RelaxRequest("a", _COORDS)])
    assert outcome.converged is True
    assert count.read_text(encoding="utf-8").count("x") == 1
