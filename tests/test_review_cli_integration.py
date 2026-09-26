"""Regression checks through the real CLI and its durable execution adapter.

Formal V4 ports: every test drives the real CLI in a subprocess against a
V4 document and real-format fake QC programs. Legacy V2/V3 configs are
sealed by the V4 guard (covered in tests/test_workflow_v3_*_guard files);
here the CLI-level durable behaviors (fresh-run recompute, definition
change, failed-run resume, corrupt-durable refusal, locked workdir,
multi-frame fan-in) are proven on the single V4 application.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from confflow.application.execution.workflow_adapter import acquire_work_directory_lease

pytestmark = pytest.mark.skipif(os.name != "posix", reason="durable CLI uses POSIX state roots")

FAKE_ORCA = Path(__file__).resolve().parent / "v4" / "fakes" / "fake_orca.py"


def _write_seed(path: Path, coordinate: int = 0) -> None:
    path.write_text(
        f"1\nCID=A1 | E=-1\nH {coordinate} 0 0\n" f"1\nCID=A2 | E=0\nH {coordinate + 1} 0 0\n",
        encoding="utf-8",
    )


def _write_h2(path: Path, coordinate: int = 0) -> None:
    """Write one H2 seed block (even electrons: singlet multiplicity is valid)."""
    path.write_text(
        f"2\nCID=H2-{coordinate}\nH {coordinate} 0 0\nH {coordinate + 0.74:.2f} 0 0\n",
        encoding="utf-8",
    )


def _v4_doc(path: Path, executable: str) -> Path:
    """Write a single-SP V4 document wired to *executable*."""
    path.write_text(
        "schema: confflow.workflow.v4\n"
        "inputs:\n"
        "  structures: {kind: structure, cardinality: many}\n"
        "global:\n"
        "  scientific_defaults: {charge: 0, multiplicity: 1}\n"
        "steps:\n"
        "  - id: s_sp\n"
        "    executor: calculation\n"
        "    bindings:\n"
        "      structure: {source: {run: structures}}\n"
        "    calculation:\n"
        "      program: orca\n"
        "      role: sp\n"
        "      execution_adapter: standard\n"
        "      result_profile: standard\n"
        "      native: {keyword: B3LYP SP}\n"
        "      checks: [normal_termination]\n"
        "      recovery: {profile: none}\n"
        "    execution:\n"
        f"      binding_id: review\n      executable: {json.dumps(executable)}\n",
        encoding="utf-8",
    )
    return path


def _mode_wrapper(path: Path, mode_file: Path) -> Path:
    """Write an executable selecting fake_orca mode from *mode_file*.

    Missing mode file means the ABNORMAL (failing) leg; writing
    ``success_sp`` into it flips subsequent invocations to success. The
    fake speaks the strict production ORCA dialect either way.
    """
    path.write_text(
        "#!/bin/sh\n"
        f'if [ -f "{mode_file}" ]; then mode=$(cat "{mode_file}"); '
        "else mode=abnormal; fi\n"
        f'exec env FAKE_MODE="$mode" "{sys.executable}" "{FAKE_ORCA}" "$@"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


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
        timeout=120,
    )


def _assert_success(result: subprocess.CompletedProcess, seed: Path) -> None:
    report = seed.with_suffix(".txt")
    assert result.returncode == 0, (
        result.stdout,
        result.stderr,
        report.read_text(encoding="utf-8") if report.exists() else "no report",
    )


def _manifest(work: Path) -> dict:
    return json.loads((work / "run_result.json").read_text(encoding="utf-8"))


def test_cli_fresh_run_recomputes_after_same_path_input_change(tmp_path: Path) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    program = _mode_wrapper(tmp_path / "orca-sp", tmp_path / "mode")
    (tmp_path / "mode").write_text("success_sp", encoding="utf-8")
    _write_h2(seed)
    _v4_doc(config, str(program))

    run1 = tmp_path / "run1"
    _assert_success(_invoke(seed, config, run1), seed)
    first = _manifest(run1)
    assert first["status"] == "completed"
    assert first["steps"][0]["counts"] == {"completed": 1, "failed": 0, "cancelled": 0}

    # Same run root, changed inputs: fail closed (no stale attach), and the
    # published manifest is untouched.
    _write_h2(seed, 5)
    changed = _invoke(seed, config, run1)
    assert changed.returncode != 0
    assert _manifest(run1) == first

    # Fresh run root recomputes: same definition digest, new publication.
    run2 = tmp_path / "run2"
    _assert_success(_invoke(seed, config, run2), seed)
    second = _manifest(run2)
    assert second["status"] == "completed"
    assert second["definition_digest"] == first["definition_digest"]
    assert second["steps"][0]["digest"] != first["steps"][0]["digest"]


def test_cli_config_change_with_disabled_terminal_preserves_generated_output(
    tmp_path: Path,
) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    program = _mode_wrapper(tmp_path / "orca-sp", tmp_path / "mode")
    (tmp_path / "mode").write_text("success_sp", encoding="utf-8")
    _write_h2(seed)
    _v4_doc(config, str(program))
    _assert_success(_invoke(seed, config, work), seed)
    before = (work / "run_result.json").read_bytes()

    # A changed definition on the same run root is refused; history is never
    # reinterpreted under the new digest, and the published output survives.
    config.write_text(
        config.read_text(encoding="utf-8").replace("B3LYP SP", "B3LYP D3BJ SP"),
        encoding="utf-8",
    )
    result = _invoke(seed, config, work)
    assert result.returncode != 0
    assert (work / "run_result.json").read_bytes() == before


def test_cli_resumes_failed_calculation_after_external_cause_is_fixed(tmp_path: Path) -> None:
    """Keep configuration fixed while a controlled external program recovers."""
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    program = _mode_wrapper(tmp_path / "orca-sp", tmp_path / "mode")
    _write_h2(seed)
    _v4_doc(config, str(program))

    result = _invoke(seed, config, work)
    assert result.returncode != 0
    failed = _manifest(work)
    assert failed["status"] == "failed"
    assert failed["steps"][0]["counts"]["failed"] == 1

    (tmp_path / "mode").write_text("success_sp", encoding="utf-8")
    _assert_success(_invoke(seed, config, work, "--resume"), seed)

    after = _manifest(work)
    assert after["status"] == "completed"
    assert after["steps"][0]["counts"] == {"completed": 1, "failed": 0, "cancelled": 0}
    assert after["definition_digest"] == failed["definition_digest"]


def test_cli_resume_rejects_corrupted_completed_output(tmp_path: Path) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    program = _mode_wrapper(tmp_path / "orca-sp", tmp_path / "mode")
    (tmp_path / "mode").write_text("success_sp", encoding="utf-8")
    _write_h2(seed)
    _v4_doc(config, str(program))
    _assert_success(_invoke(seed, config, work), seed)
    output = work / "steps" / "s_sp" / "step_result.json"
    output.write_bytes(b"")

    result = _invoke(seed, config, work, "--resume")

    assert result.returncode != 0
    assert output.read_bytes() == b""


def test_cli_changed_request_cannot_mutate_locked_work_directory(tmp_path: Path) -> None:
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    program = _mode_wrapper(tmp_path / "orca-sp", tmp_path / "mode")
    (tmp_path / "mode").write_text("success_sp", encoding="utf-8")
    _write_h2(seed)
    _v4_doc(config, str(program))
    _assert_success(_invoke(seed, config, work), seed)
    before = {
        path.relative_to(work): path.read_bytes() for path in work.rglob("*") if path.is_file()
    }
    report = seed.with_suffix(".txt")
    report_before = report.read_bytes() if report.exists() else None
    _write_h2(seed, 5)
    lease = acquire_work_directory_lease(str(work))
    try:
        result = _invoke(seed, config, work)
        assert result.returncode != 0
        assert "already running" in result.stdout + result.stderr
        if report_before is not None:
            assert report.read_bytes() == report_before
        after = {
            path.relative_to(work): path.read_bytes() for path in work.rglob("*") if path.is_file()
        }
        assert after == before
    finally:
        lease.release()


def test_cli_dag_merge_passes_every_frame_to_calculation(tmp_path: Path) -> None:
    """Exercise multi-frame fan-in through the real CLI layers."""
    seed = tmp_path / "seed.xyz"
    config = tmp_path / "workflow.yaml"
    work = tmp_path / "work"
    program = _mode_wrapper(tmp_path / "orca-sp", tmp_path / "mode")
    (tmp_path / "mode").write_text("success_sp", encoding="utf-8")
    seed.write_text(
        "2\nCID=A1\nH 0 0 0\nH 0.74 0 0\n" "2\nCID=A2\nH 1 0 0\nH 1.74 0 0\n",
        encoding="utf-8",
    )
    _v4_doc(config, str(program))

    _assert_success(_invoke(seed, config, work), seed)

    manifest = _manifest(work)
    assert manifest["status"] == "completed"
    assert manifest["steps"][0]["counts"] == {"completed": 2, "failed": 0, "cancelled": 0}
    assert len(manifest["results"]) == 2
    subjects = {entry["subject_structure_id"] for entry in manifest["results"]}
    assert len(subjects) == 2

    manifest_before = _manifest(work)
    _assert_success(_invoke(seed, config, work, "--resume"), seed)
    manifest_after = _manifest(work)
    # Resume is an explicit strict revalidation (fresh controlled record that
    # re-executes through durable reuse): scientific outputs must be
    # identical while execution-trace reuse provenance is recorded. Byte
    # identity cannot hold because each reused frame appends one reuse_hit
    # diagnostic (plus its derived step digest); that provenance is intended
    # (asserted load-bearing across tests/v4 resume suites).
    assert manifest_after["status"] == "completed"
    assert manifest_after["steps"][0]["counts"] == {"completed": 2, "failed": 0, "cancelled": 0}
    assert manifest_after["results"] == manifest_before["results"]
    assert manifest_after["definition_digest"] == manifest_before["definition_digest"]
    codes = [item["code"] for item in manifest_after["steps"][0]["diagnostics"]]
    assert codes.count("reuse_hit") == 2
    assert codes.count("native_termination") == 2
