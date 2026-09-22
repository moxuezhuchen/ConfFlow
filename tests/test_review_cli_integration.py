"""Regression checks through the real CLI and its durable execution adapter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from confflow.application.execution.workflow_adapter import acquire_work_directory_lease
from confflow.core.io import read_xyz_file

pytestmark = pytest.mark.skipif(os.name != "posix", reason="durable CLI uses POSIX state roots")


def _write_seed(path: Path, coordinate: int = 0) -> None:
    path.write_text(
        f"1\nCID=A1 | E=-1\nH {coordinate} 0 0\n" f"1\nCID=A2 | E=0\nH {coordinate + 1} 0 0\n",
        encoding="utf-8",
    )


def _invoke(seed: Path, config: Path, work: Path, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from confflow.main import main; raise SystemExit(main())",
            str(seed),
            "-c",
            str(config),
            "-w",
            str(work),
            *extra,
        ],
        env=env,
        cwd=seed.parent,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _assert_success(result: subprocess.CompletedProcess, seed: Path) -> None:
    report = seed.with_suffix(".txt")
    assert result.returncode == 0, (
        result.stdout,
        result.stderr,
        report.read_text(encoding="utf-8") if report.exists() else "no report",
    )


def test_cli_fresh_run_recomputes_after_same_path_input_change(tmp_path: Path) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    _write_seed(seed)
    config.write_text("global: {}\nsteps:\n  - name: gen\n    type: confgen\n")
    _assert_success(_invoke(seed, config, work), seed)
    assert read_xyz_file(str(work / "gen" / "search.xyz"))[0]["coords"][0][0] == 0

    _write_seed(seed, 5)
    _assert_success(_invoke(seed, config, work), seed)

    frames = read_xyz_file(str(work / "gen" / "search.xyz"))
    assert len(frames) == 2
    assert frames[0]["coords"][0][0] == 5

    # Returning to an older request must not attach its historical execution
    # record while this shared work directory still contains the newer output.
    _write_seed(seed)
    _assert_success(_invoke(seed, config, work), seed)
    assert read_xyz_file(str(work / "gen" / "search.xyz"))[0]["coords"][0][0] == 0


def test_cli_config_change_with_disabled_terminal_preserves_generated_output(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    _write_seed(seed)
    config.write_text("global: {}\nsteps:\n  - name: original\n    type: confgen\n")
    _assert_success(_invoke(seed, config, work), seed)

    config.write_text(
        "global: {}\nsteps:\n"
        "  - name: replacement\n    type: confgen\n"
        "  - name: disabled\n    type: confgen\n    enabled: false\n"
    )
    _assert_success(_invoke(seed, config, work), seed)

    stats = json.loads((work / "workflow_stats.json").read_text())
    assert stats["final_output"] == str(work / "replacement" / "search.xyz")
    manifest = json.loads((work / "output_manifest.json").read_text())
    artifacts = [path for paths in manifest["terminals"].values() for path in paths]
    assert artifacts == ["replacement/search.xyz"]
    assert len(read_xyz_file(stats["final_output"])) == 2


def test_cli_resumes_failed_calculation_after_external_cause_is_fixed(tmp_path: Path) -> None:
    """Keep configuration fixed while a controlled external program recovers."""
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    ready = tmp_path / "program-ready"
    program = tmp_path / "fake-orca"
    program.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        f"if not Path({str(ready)!r}).exists(): raise SystemExit(1)\n"
        "print('FINAL SINGLE POINT ENERGY      -123.456789')\n"
        "print('****ORCA TERMINATED NORMALLY****')\n"
    )
    program.chmod(0o700)
    seed.write_text("1\nCID=A1\nH 0 0 0\n")
    config.write_text(
        "global: {}\nsteps:\n"
        "  - name: calc\n    type: calc\n    params:\n"
        "      iprog: orca\n      itask: sp\n      keyword: HF\n"
        f"      orca_path: {json.dumps(str(program))}\n"
        "      auto_clean: false\n      stop_check_interval_seconds: 0.01\n"
    )
    result = _invoke(seed, config, work)
    assert result.returncode != 0
    before = json.loads((work / ".workflow_state.json").read_text())
    assert before["steps"]["calc"]["status"] == "failed"

    ready.touch()
    _assert_success(_invoke(seed, config, work, "--resume"), seed)

    after = json.loads((work / ".workflow_state.json").read_text())
    assert after["final_status"] == "completed"
    assert after["steps"]["calc"]["status"] == "completed"
    frames = read_xyz_file(after["steps"]["calc"]["output_xyz"])
    assert len(frames) == 1
    assert frames[0]["metadata"]["E"] == pytest.approx(-123.456789)


def test_cli_resume_rejects_corrupted_completed_output(tmp_path: Path) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    _write_seed(seed)
    config.write_text("global: {}\nsteps:\n  - name: gen\n    type: confgen\n")
    _assert_success(_invoke(seed, config, work), seed)
    output = work / "gen" / "search.xyz"
    output.write_bytes(b"")
    signature = work / "gen" / ".confgen_signature"
    previous_signature = signature.read_bytes()

    result = _invoke(seed, config, work, "--resume")

    assert result.returncode != 0
    assert output.read_bytes() == b""
    assert signature.read_bytes() == previous_signature


def test_cli_changed_request_cannot_mutate_locked_work_directory(tmp_path: Path) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    _write_seed(seed)
    config.write_text("global: {}\nsteps:\n  - name: gen\n    type: confgen\n")
    _assert_success(_invoke(seed, config, work), seed)
    before = {
        path.relative_to(work): path.read_bytes() for path in work.rglob("*") if path.is_file()
    }
    report = seed.with_suffix(".txt")
    report_before = report.read_bytes()
    _write_seed(seed, 5)
    lease = acquire_work_directory_lease(str(work))
    try:
        result = _invoke(seed, config, work)
        assert result.returncode != 0
        assert "already running" in result.stdout + result.stderr
        assert report.read_bytes() == report_before
        after = {
            path.relative_to(work): path.read_bytes() for path in work.rglob("*") if path.is_file()
        }
        assert after == before
    finally:
        lease.release()


def test_cli_dag_merge_passes_every_frame_to_calculation(tmp_path: Path) -> None:
    """Exercise multi-frame fan-in and task allocation across real CLI layers."""
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    program = tmp_path / "fake-orca"
    _write_seed(seed)
    program.write_text(
        f"#!{sys.executable}\n"
        "print('FINAL SINGLE POINT ENERGY      -123.456789')\n"
        "print('****ORCA TERMINATED NORMALLY****')\n"
    )
    program.chmod(0o700)
    config.write_text(
        "global: {}\nsteps:\n"
        "  - name: left\n    type: confgen\n    inputs: []\n"
        "  - name: right\n    type: confgen\n    inputs: []\n"
        "  - name: merge\n    type: confgen\n    inputs: [left, right]\n"
        "  - name: calc\n    type: calc\n    inputs: [merge]\n    params:\n"
        "      iprog: orca\n      itask: sp\n      keyword: HF\n"
        f"      orca_path: {json.dumps(str(program))}\n"
        "      auto_clean: false\n      stop_check_interval_seconds: 0.01\n"
    )

    _assert_success(_invoke(seed, config, work), seed)

    merged = read_xyz_file(str(work / "merge" / "search.xyz"))
    calculated = read_xyz_file(str(work / "calc" / "result.xyz"))
    manifest = json.loads((work / "calc" / "manifest.json").read_text())
    assert len(merged) == len(calculated) == 4
    assert manifest["total_tasks"] == manifest["succeeded"] == 4
    assert sorted(frame["coords"][0][0] for frame in calculated) == [0, 0, 1, 1]

    output_before = (work / "calc" / "result.xyz").read_bytes()
    _assert_success(_invoke(seed, config, work, "--resume"), seed)
    assert (work / "calc" / "result.xyz").read_bytes() == output_before
