"""Script retries must not publish output files left by a failed attempt."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from confflow.domain.errors import DomainError
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.script_executor import ScriptExecutor
from tests.test_script_steps import (
    _script_context,
    _script_item,
    _write_script,
    _write_server_toml,
)

_STALE_XYZ = "1\nstale output from failed attempt\nHe 0 0 0\n"

# First attempt writes every declared output and a checkpoint, then fails.
# The second attempt has three modes (last argv entry):
#   broken - exits zero without writing anything;
#   fresh  - requires input.xyz and the checkpoint, then writes fresh outputs;
#   (the first attempt is the one that writes stale files).
_RETRY_SCRIPT = f"""\
from pathlib import Path
import sys
marker = Path('attempt.marker')
if not marker.exists():
    marker.write_text('first attempt')
    Path('checkpoint.chk').write_text('resume state')
    Path('final.xyz').write_text({_STALE_XYZ!r})
    Path('summary.json').write_text('{{"stale": "yes"}}')
    Path('extra.log').write_text('stale log')
    sys.exit(1)
if sys.argv[-1] == 'fresh':
    if not (Path('input.xyz').exists() and Path('checkpoint.chk').exists()):
        sys.exit(2)
    Path('final.xyz').write_text('1\\nfresh\\nH 0 0 0\\n')
    Path('summary.json').write_text('{{"stale": "no"}}')
    Path('run.log').write_text('fresh log')
"""


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))
    return tmp_path


def _default_outputs() -> dict[str, Any]:
    return {
        "structures": "final.xyz",
        "summary": "summary.json",
        "artifacts": ["*.log"],
    }


def _setup(
    tmp_path: Path, retry_arg: str, outputs: dict[str, Any] | None = None
) -> tuple[Path, Any]:
    script = _write_script(tmp_path, "demo.py", _RETRY_SCRIPT)
    _write_server_toml(tmp_path, {"demo": {"command": [sys.executable, str(script)]}})
    run_root = tmp_path / "run"
    run_root.mkdir()
    context = _script_context(
        NativeProcessSupervisor(),
        run_root,
        args=("{input}", retry_arg),
        outputs=outputs or _default_outputs(),
    )
    return run_root, context


def _age(path: Path) -> None:
    """Backdate *path* to look like a file left by an attempt long ago."""
    old = time.time() - 3600
    os.utime(path, (old, old))


def test_retry_that_writes_no_output_does_not_publish_stale_files(tmp_path: Path) -> None:
    run_root, context = _setup(tmp_path, "broken")
    item = _script_item(key="stale-retry")
    first = ScriptExecutor().execute(item, context)
    assert first.status.value == "failed"
    assert first.error is not None and first.error.code == "script_failed"

    second = ScriptExecutor().execute(item, context)
    key = dict(second.diagnostics[0].details)["item_key"]
    item_dir = run_root / "script" / "s1" / key

    assert second.status.value == "failed", [(d.code, d.message) for d in second.diagnostics]
    assert "missing" in second.diagnostics[0].message
    assert len(second.structures) == 0
    assert len(second.results) == 0
    # Declared channel files are removed before the launch; the artifact-matched
    # log is not a channel file and stays on disk.
    assert not (item_dir / "final.xyz").exists()
    assert not (item_dir / "summary.json").exists()
    assert (item_dir / "extra.log").exists()


def test_retry_publishes_only_fresh_artifacts_and_keeps_checkpoints(tmp_path: Path) -> None:
    run_root, context = _setup(tmp_path, "fresh")
    item = _script_item(key="fresh-retry")
    first = ScriptExecutor().execute(item, context)
    assert first.status.value == "failed"
    item_dir = run_root / "script" / "s1" / dict(first.diagnostics[0].details)["item_key"]
    # Stale log from the failed attempt, older than the attempt window.
    _age(item_dir / "extra.log")

    second = ScriptExecutor().execute(item, context)
    assert second.status.value == "completed", [(d.code, d.message) for d in second.diagnostics]
    assert [s.atoms for s in second.structures] == [("H",)]
    assert second.results[0].value["stale"] == "no"
    assert (item_dir / "checkpoint.chk").read_text() == "resume state"
    assert (item_dir / "extra.log").read_text() == "stale log"
    published = {ref.checksum for ref in second.artifacts}
    # stdout.log/stderr.log from this attempt also match *.log; only the stale
    # extra.log must be absent.
    assert "sha256:" + hashlib.sha256(b"fresh log").hexdigest() in published
    assert "sha256:" + hashlib.sha256(b"stale log").hexdigest() not in published


def test_immediate_retry_does_not_publish_untouched_artifact(tmp_path: Path) -> None:
    """A retry right after a failure must not publish the failed attempt's log.

    The stale file is seconds old at most, so a time window cannot tell it apart; the
    pre-launch file snapshot does.
    """
    run_root, context = _setup(tmp_path, "fresh")
    item = _script_item(key="fast-retry")
    first = ScriptExecutor().execute(item, context)
    assert first.status.value == "failed"
    item_dir = run_root / "script" / "s1" / dict(first.diagnostics[0].details)["item_key"]
    stale = item_dir / "extra.log"
    stale_mtime = stale.stat().st_mtime_ns

    second = ScriptExecutor().execute(item, context)

    assert second.status.value == "completed", [(d.code, d.message) for d in second.diagnostics]
    assert stale.stat().st_mtime_ns == stale_mtime
    published = {ref.locator.path.rsplit("/", 1)[-1] for ref in second.artifacts}
    assert "extra.log" not in published
    assert "run.log" in published


def test_rewritten_artifact_from_earlier_attempt_is_published(tmp_path: Path) -> None:
    item_dir = tmp_path / "item"
    item_dir.mkdir()
    log = item_dir / "run.log"
    log.write_text("old")
    untouched = item_dir / "keep.log"
    untouched.write_text("old")
    before = ScriptExecutor._artifact_file_states({"artifacts": ["*.log", ""]}, str(item_dir))
    log.write_text("new content")

    run_root = tmp_path
    context = _script_context(NativeProcessSupervisor(), run_root)
    artifacts = ScriptExecutor()._collect_artifacts(
        {"artifacts": ["*.log"]},
        str(item_dir),
        _script_item(key="rw"),
        context,
        "rw",
        before=before,
    )

    assert [ref.locator.path for ref in artifacts] == ["item/run.log"]
    assert ScriptExecutor._artifact_file_states({"artifacts": "*.log"}, str(item_dir)) == {}


def test_artifact_glob_star_does_not_delete_input_or_checkpoint(tmp_path: Path) -> None:
    outputs = {"structures": "final.xyz", "summary": "summary.json", "artifacts": ["*"]}
    run_root, context = _setup(tmp_path, "fresh", outputs=outputs)
    item = _script_item(key="star-glob")
    first = ScriptExecutor().execute(item, context)
    assert first.status.value == "failed"
    item_dir = run_root / "script" / "s1" / dict(first.diagnostics[0].details)["item_key"]

    second = ScriptExecutor().execute(item, context)
    assert second.status.value == "completed", [(d.code, d.message) for d in second.diagnostics]
    assert (item_dir / "input.xyz").is_file()
    assert (item_dir / "checkpoint.chk").read_text() == "resume state"
    assert [s.atoms for s in second.structures] == [("H",)]


def test_clear_skips_invalid_declared_paths_and_deletes_nothing_outside(tmp_path: Path) -> None:
    item_dir = tmp_path / "item"
    item_dir.mkdir()
    outside = tmp_path / "x.xyz"
    outside.write_text("keep me\n")
    (item_dir / "final.xyz").write_text("stale\n")
    # Absolute path and parent-escaping name are both invalid declarations.
    outputs = {"structures": str(outside), "summary": "../x.xyz"}
    with pytest.raises(DomainError):
        ScriptExecutor._channel_path(str(item_dir), "../x.xyz", "summary")

    ScriptExecutor()._clear_declared_outputs(
        str(item_dir), outputs, protected=str(item_dir / "input.xyz")
    )

    assert outside.read_text() == "keep me\n"
    # An invalid channel does not abort the loop: a valid sibling is still cleared.
    ScriptExecutor()._clear_declared_outputs(
        str(item_dir), {"structures": "../x.xyz", "summary": "final.xyz"}, protected="/nope"
    )
    assert not (item_dir / "final.xyz").exists()
    assert outside.read_text() == "keep me\n"


def test_clear_keeps_protected_input_and_removes_declared_summary(tmp_path: Path) -> None:
    item_dir = tmp_path / "item"
    item_dir.mkdir()
    (item_dir / "input.xyz").write_text("1\ninput\nH 0 0 0\n")
    (item_dir / "summary.json").write_text("{}")
    outputs = {"structures": "input.xyz", "summary": "summary.json"}

    ScriptExecutor()._clear_declared_outputs(
        str(item_dir), outputs, protected=str(item_dir / "input.xyz")
    )

    assert (item_dir / "input.xyz").read_text() == "1\ninput\nH 0 0 0\n"
    assert not (item_dir / "summary.json").exists()


def test_overlapping_artifact_patterns_publish_each_file_once(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    context = _script_context(NativeProcessSupervisor(), run_root)
    item = _script_item(key="overlap")
    item_dir = run_root / "script" / "s1" / "overlap"
    item_dir.mkdir(parents=True)
    (item_dir / "run.log").write_text("fresh log")

    artifacts = ScriptExecutor()._collect_artifacts(
        {"artifacts": ["*.log", "run.*"]}, str(item_dir), item, context, "overlap"
    )

    assert len(artifacts) == 1
    (ref,) = list(artifacts)
    assert ref.checksum == "sha256:" + hashlib.sha256(b"fresh log").hexdigest()
    assert ref.locator.path == "script/s1/overlap/run.log"


def test_directory_matching_artifact_pattern_is_not_published(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    context = _script_context(NativeProcessSupervisor(), run_root)
    item = _script_item(key="dirmatch")
    item_dir = run_root / "script" / "s1" / "dirmatch"
    item_dir.mkdir(parents=True)
    (item_dir / "out.d").mkdir()
    (item_dir / "out.txt").write_text("result")

    artifacts = ScriptExecutor()._collect_artifacts(
        {"artifacts": ["out*"]}, str(item_dir), item, context, "dirmatch"
    )

    assert len(artifacts) == 1
    (ref,) = list(artifacts)
    assert ref.checksum == "sha256:" + hashlib.sha256(b"result").hexdigest()
