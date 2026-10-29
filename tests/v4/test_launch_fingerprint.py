"""Launch fingerprint: a resolved program replaced after resolution is refused, never launched.

The application resolves and measures the program once; the launch re-checks a cheap file
fingerprint (stat fields and realpath) taken at resolution.  Unit cases exercise the
fingerprint directly.  Integration cases go through ``V4RunApplication.run`` for the native
and MLIP executors: a labelled wrapper replaces its own file after the first launch (or during
the MLIP probe), and the second launch must be refused with the replacement never starting.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.domain.errors import DomainError
from confflow.execution.command_resolution import (
    LaunchFingerprint,
    ResolvedCommand,
    locate_command,
    take_fingerprint,
    verify_launch_unchanged,
)
from confflow.execution.mlip_executor import MODEL_ENV
from confflow.execution.process import NativeProcessSupervisor
from tests.v4 import test_arch_review_launch_identity as h
from tests.v4._builders import run_inputs, structure_set
from tests.v4._helpers import v43_coverage

_PY = sys.executable


@pytest.fixture(autouse=True)
def _isolated_server_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep server config and quota state inside tmp_path."""
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(tmp_path / "server.toml"))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(tmp_path / "quota-state"))


def _exe(path: Path, body: str = "pass\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{_PY}\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def _lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _outcome(call: Any) -> str:
    """Return ``completed`` or ``refused``; a refusal is a DomainError or a non-completed run."""
    try:
        report = call()
    except DomainError:
        return "refused"
    return "completed" if report.status == "completed" else "refused"


# -- unit: the fingerprint of a resolution ------------------------------------------------


def test_locate_command_records_a_fingerprint(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "bin" / "prog")
    resolved = locate_command(str(exe), {})
    assert resolved is not None
    assert resolved.fingerprint == take_fingerprint(str(exe))
    assert resolved.fingerprint is not None
    assert resolved.fingerprint.real_path == os.path.realpath(exe)


def test_bare_name_resolution_records_a_fingerprint(tmp_path: Path) -> None:
    _exe(tmp_path / "bin" / "prog")
    resolved = locate_command("prog", {"PATH": str(tmp_path / "bin")})
    assert resolved is not None
    assert resolved.fingerprint is not None
    assert resolved.absolute == str(tmp_path / "bin" / "prog")


def test_fingerprint_is_not_part_of_equality(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "prog")
    located = locate_command(str(exe), {})
    assert located is not None
    bare = ResolvedCommand(configured=str(exe), absolute=str(exe))
    assert located == bare
    assert bare.fingerprint is None


def test_verify_passes_when_unchanged(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "prog")
    resolved = locate_command(str(exe), {})
    assert resolved is not None
    verify_launch_unchanged(resolved)


def test_verify_refuses_in_place_rewrite_with_different_length(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "prog")
    resolved = locate_command(str(exe), {})
    assert resolved is not None
    exe.write_text(f"#!{_PY}\nprint('a much longer replacement body')\n", encoding="utf-8")
    with pytest.raises(DomainError, match="changed after it was resolved"):
        verify_launch_unchanged(resolved)


def test_verify_refuses_in_place_rewrite_with_new_mtime(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "prog")
    resolved = locate_command(str(exe), {})
    assert resolved is not None
    os.utime(exe, ns=(1_000_000_000, 1_000_000_000))
    with pytest.raises(DomainError, match="changed after it was resolved"):
        verify_launch_unchanged(resolved)


def test_verify_refuses_after_os_replace_with_new_file(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "prog")
    resolved = locate_command(str(exe), {})
    assert resolved is not None
    staged = _exe(tmp_path / "prog.new", "print('replacement')\n")
    os.replace(staged, exe)
    with pytest.raises(DomainError, match="changed after it was resolved"):
        verify_launch_unchanged(resolved)


def test_verify_refuses_after_symlink_is_retargeted(tmp_path: Path) -> None:
    first = _exe(tmp_path / "first" / "prog")
    second = _exe(tmp_path / "second" / "prog", "print('second')\n")
    link = tmp_path / "link"
    link.symlink_to(first)
    resolved = locate_command(str(link), {})
    assert resolved is not None
    assert resolved.fingerprint is not None
    assert resolved.fingerprint.real_path == os.path.realpath(first)
    link.unlink()
    link.symlink_to(second)
    with pytest.raises(DomainError, match="changed after it was resolved"):
        verify_launch_unchanged(resolved)


def test_verify_refuses_when_file_is_deleted(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "prog")
    resolved = locate_command(str(exe), {})
    assert resolved is not None
    exe.unlink()
    with pytest.raises(DomainError, match="changed after it was resolved"):
        verify_launch_unchanged(resolved)


def test_verify_is_a_no_op_without_a_fingerprint(tmp_path: Path) -> None:
    missing = str(tmp_path / "never-created")
    verify_launch_unchanged(ResolvedCommand(configured=missing, absolute=missing))


def test_fingerprint_fields_match_stat(tmp_path: Path) -> None:
    exe = _exe(tmp_path / "prog")
    fingerprint = take_fingerprint(str(exe))
    st = os.stat(exe)
    assert isinstance(fingerprint, LaunchFingerprint)
    assert (fingerprint.device, fingerprint.inode) == (st.st_dev, st.st_ino)
    assert (fingerprint.size, fingerprint.mtime_ns, fingerprint.ctime_ns) == (
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


# -- integration: native ------------------------------------------------------------------

_HEADER = "import os, sys\nfrom pathlib import Path\n"


def _record(log: Path, label: str) -> str:
    return (
        f"with Path({str(log)!r}).open('a', encoding='utf-8') as handle:\n"
        f"    handle.write({label + chr(10)!r})\n"
    )


def _native_orca(tmp_path: Path, log: Path, *, replace: bool) -> Path:
    """Return the ``a/orca`` wrapper; with *replace* it swaps itself for a new file on each launch.

    The swap writes a sibling file and ``os.replace``s it over the wrapper, so the wrapper's
    inode changes after the item was resolved.  The replacement records ``R`` when it starts.
    """
    bin_dir = tmp_path / "a"
    bin_dir.mkdir(exist_ok=True)
    fake = str(v43_coverage.FAKE_ORCA)
    launch = f"os.execv({_PY!r}, [{_PY!r}, {fake!r}, *sys.argv[1:]])\n"
    replacement = _exe(bin_dir / "orca.new", _HEADER + _record(log, "R") + launch)
    swap = f"os.replace({str(replacement)!r}, {str(bin_dir / 'orca')!r})\n" if replace else ""
    body = _HEADER + _record(log, "A") + swap + launch
    return _exe(bin_dir / "orca", body)


def _native_run(tmp_path: Path, *, replace: bool) -> tuple[Any, Path]:
    """Run two orca items on one worker: the first launch may replace the program."""
    log = tmp_path / "native-launches.log"
    _native_orca(tmp_path, log, replace=replace)
    doc = v43_coverage._document()
    step = doc["steps"][0]
    step["scheduler"]["max_parallel_items"] = 1
    step["execution"] = {
        "binding_id": "test",
        "executable": "orca",
        "allowed_executables": ["orca"],
        "env": {
            "PATH": os.pathsep.join((str(tmp_path / "a"), os.environ["PATH"])),
            "FAKE_MODE": "success_opt",
        },
    }
    request = V4RunRequest(
        workflow_document=doc,
        run_inputs=run_inputs(structures={"structures": structure_set("s0", "s1")}),
        run_root=str(tmp_path / "native-run"),
        supervisor=NativeProcessSupervisor(),
    )
    return V4RunApplication().run(request), log


def test_native_unchanged_program_launches_every_item(tmp_path: Path) -> None:
    report, log = _native_run(tmp_path, replace=False)
    assert report.status == "completed"
    assert _lines(log) == ["A", "A"]


def test_native_replaced_program_is_refused_before_launch(tmp_path: Path) -> None:
    report, log = _native_run(tmp_path, replace=True)
    # the first item launches the measured program; the replacement must never start
    assert report.status != "completed"
    assert "R" not in _lines(log)
    assert _lines(log) == ["A"]


# -- integration: MLIP --------------------------------------------------------------------


def _mlip_interpreter(tmp_path: Path, log: Path, *, replace: bool) -> Path:
    """Return ``a/python3``; its interpreter probe may swap the file for a recording one."""
    bin_dir = tmp_path / "a"
    bin_dir.mkdir(exist_ok=True)
    target = bin_dir / "python3"
    staged = bin_dir / "python3.new"
    replacement = (
        f"#!{_PY}\nimport os, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a', encoding='utf-8') as f: f.write('launch:R\\n')\n"
        f"os.execv({_PY!r}, [{_PY!r}, *sys.argv[1:]])\n"
    )
    swap = (
        f"    Path({str(staged)!r}).write_text({replacement!r})\n"
        f"    Path({str(staged)!r}).chmod(0o755)\n"
        f"    os.replace({str(staged)!r}, {str(target)!r})\n"
    )
    body = (
        "import os, sys\nfrom pathlib import Path\n"
        "if '-c' in sys.argv[1:]:\n"
        f"    with Path({str(log)!r}).open('a', encoding='utf-8') as f: f.write('probe\\n')\n"
        + (swap if replace else "    pass\n")
        + "else:\n"
        f"    with Path({str(log)!r}).open('a', encoding='utf-8') as f: f.write('launch:A\\n')\n"
        f"os.execv({_PY!r}, [{_PY!r}, *sys.argv[1:]])\n"
    )
    return _exe(target, body)


def _mlip_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, replace: bool
) -> tuple[Any, Path]:
    log = tmp_path / "mlip-launches.log"
    _mlip_interpreter(tmp_path, log, replace=replace)
    h._install_mlip_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-3.0", encoding="utf-8")
    doc = h._mlip_doc(model, "python3")
    execution = doc["steps"][0]["execution"]
    execution["env"]["PATH"] = os.pathsep.join((str(tmp_path / "a"), os.environ["PATH"]))
    execution["env"][MODEL_ENV] = str(model)
    doc["steps"][0]["execution"]["allowed_executables"] = ["python3"]
    return h._run_mlip(tmp_path / "mlip-run", doc), log


def test_mlip_unchanged_interpreter_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, log = _mlip_run(tmp_path, monkeypatch, replace=False)
    assert report.status == "completed"
    assert "launch:A" in _lines(log)
    assert "launch:R" not in _lines(log)


def test_mlip_replaced_interpreter_is_refused_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report, log = _mlip_run(tmp_path, monkeypatch, replace=True)
    assert report.status != "completed"
    assert "launch:R" not in _lines(log)
    assert "launch:A" not in _lines(log)
