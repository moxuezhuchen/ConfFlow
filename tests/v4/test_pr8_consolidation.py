#!/usr/bin/env python3

"""PR-8 consolidation locks: shared durable-publish and naming authorities.

Pins the extracted ``confflow.persistence.fsatomic`` protocol against the
per-module copies it replaced (same output bytes, same temp naming, same
replace ordering, same failure cleanup, same best-effort directory fsync,
same symlink/umask behavior) and pins the single job-name sanitizer
authority shared by the Gaussian and ORCA renderers.

These are deliberately not happy-path-only: every failure axis that the
former copies handled (replace failure, write/fsync failure, unopenable or
unsyncable directory) is exercised, and a static scan refuses any new
local copies of the consolidated helpers.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from confflow.persistence import fsatomic

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Production scopes whose local copies were consolidated in PR-8.  The
#: ``remote`` package keeps its deliberately self-contained no-follow
#: writers (deferred by audit: different O_EXCL/O_NOFOLLOW/mode/taxonomy)
#: and is intentionally out of this scan.
CONSOLIDATED_SCOPES = (
    "confflow/domain",
    "confflow/execution",
    "confflow/workflow/v4",
    "confflow/persistence",
    "confflow/programs",
    "confflow/producer",
    "confflow/application",
)

#: Helper names that must exist only in the authority module.
DUPLICATE_HELPER_MARKERS = (
    "def _atomic_write_bytes(",
    "def _fsync_directory(",
    "def _fsync_dir(",
    "def _tmp_suffix(",
    "def _next_tmp_suffix(",
)


def _tmp_leftovers(directory: Path) -> list[str]:
    """Return every temp-leftover name in *directory*."""
    return sorted(name for name in os.listdir(directory) if ".tmp." in name)


def _sanitize_via_authority(value: str, *, fallback: str = "job") -> str:
    """Call the job-name authority through its declared public path."""
    from confflow.programs import _naming

    return _naming.sanitize_job_name(value, fallback=fallback)


def _legacy_atomic_write_bytes(target_path: str, payload: bytes) -> None:
    """Exact pre-PR-8 reference protocol (the former per-module copy)."""
    directory = os.path.dirname(target_path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{target_path}.tmp.{os.getpid()}.0"
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


class TestPublishBytesProtocol:
    """The authority preserves the exact durable-replace protocol."""

    def test_replaces_existing_bytes_without_leftovers(self, tmp_path: Path) -> None:
        target = tmp_path / "state.json"
        target.write_bytes(b"old bytes")
        fsatomic.publish_bytes(str(target), b'{"a":1}')
        assert target.read_bytes() == b'{"a":1}'
        assert _tmp_leftovers(tmp_path) == []

    def test_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "b" / "state.json"
        fsatomic.publish_bytes(str(target), b"x")
        assert target.read_bytes() == b"x"
        assert _tmp_leftovers(target.parent) == []

    def test_matches_legacy_protocol_output_bytes(self, tmp_path: Path) -> None:
        payload = b'{"schema": "confflow.run_state.v1", "steps": []}\n'
        legacy = tmp_path / "legacy" / "state.json"
        authority = tmp_path / "authority" / "state.json"
        _legacy_atomic_write_bytes(str(legacy), payload)
        fsatomic.publish_bytes(str(authority), payload)
        assert authority.read_bytes() == payload
        assert legacy.read_bytes() == authority.read_bytes()

    def test_temp_name_and_full_write_are_observable_at_replace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "state.json"
        observed: dict[str, object] = {}
        real_replace = os.replace

        def recording_replace(src: str, dst: str, *args: object, **kwargs: object) -> None:
            observed["src"] = src
            observed["dst"] = dst
            observed["bytes"] = Path(src).read_bytes()
            real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(fsatomic.os, "replace", recording_replace)
        fsatomic.publish_bytes(str(target), b"payload-bytes")
        assert observed["dst"] == str(target)
        assert str(observed["src"]).startswith(f"{target}.tmp.{os.getpid()}.")
        assert observed["bytes"] == b"payload-bytes"

    def test_temp_names_do_not_collide_between_publications(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "state.json"
        sources: list[str] = []
        real_replace = os.replace

        def recording_replace(src: str, dst: str, *args: object, **kwargs: object) -> None:
            sources.append(src)
            real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(fsatomic.os, "replace", recording_replace)
        fsatomic.publish_bytes(str(target), b"one")
        fsatomic.publish_bytes(str(target), b"two")
        assert len(sources) == 2
        assert sources[0] != sources[1]
        assert target.read_bytes() == b"two"
        assert _tmp_leftovers(tmp_path) == []

    def test_file_fsync_precedes_replace_and_directory_fsync_follows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "state.json"
        events: list[str] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def recording_fsync(fd: int) -> None:
            events.append("fsync-dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync-file")
            real_fsync(fd)

        def recording_replace(src: str, dst: str, *args: object, **kwargs: object) -> None:
            events.append("replace")
            real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(fsatomic.os, "fsync", recording_fsync)
        monkeypatch.setattr(fsatomic.os, "replace", recording_replace)
        fsatomic.publish_bytes(str(target), b"payload")
        assert events == ["fsync-file", "replace", "fsync-dir"]

    def test_replace_failure_leaves_target_and_cleans_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "state.json"
        target.write_bytes(b"old")

        def failing_replace(src: str, dst: str, *args: object, **kwargs: object) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(fsatomic.os, "replace", failing_replace)
        with pytest.raises(OSError, match="replace failed"):
            fsatomic.publish_bytes(str(target), b"new")
        assert target.read_bytes() == b"old"
        assert _tmp_leftovers(tmp_path) == []

    def test_file_fsync_failure_propagates_and_cleans_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "state.json"
        real_fsync = os.fsync

        def failing_file_fsync(fd: int) -> None:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("file sync failed")
            real_fsync(fd)

        monkeypatch.setattr(fsatomic.os, "fsync", failing_file_fsync)
        with pytest.raises(OSError, match="file sync failed"):
            fsatomic.publish_bytes(str(target), b"payload")
        assert not target.exists()
        assert _tmp_leftovers(tmp_path) == []

    def test_directory_fsync_is_best_effort_when_open_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "state.json"
        real_open = os.open

        def failing_open(path: str, *args: object, **kwargs: object) -> int:
            if str(path) == str(tmp_path):
                raise OSError("cannot open directory")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(fsatomic.os, "open", failing_open)
        fsatomic.publish_bytes(str(target), b"x")
        assert target.read_bytes() == b"x"

    def test_directory_fsync_is_best_effort_when_sync_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "state.json"
        real_fsync = os.fsync

        def failing_dir_fsync(fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("dir sync failed")
            real_fsync(fd)

        monkeypatch.setattr(fsatomic.os, "fsync", failing_dir_fsync)
        fsatomic.publish_bytes(str(target), b"x")
        assert target.read_bytes() == b"x"

    def test_symlink_target_entry_is_replaced_not_followed(self, tmp_path: Path) -> None:
        """The protocol replaces the directory entry; it never writes through it.

        The consolidated copies never used ``O_NOFOLLOW``/``O_EXCL``; this
        pins that the authority keeps exactly that behavior instead of
        silently hardening (or weakening) it.
        """
        victim = tmp_path / "victim"
        victim.write_bytes(b"victim bytes")
        target = tmp_path / "state.json"
        target.symlink_to(victim)
        fsatomic.publish_bytes(str(target), b"fresh bytes")
        assert not target.is_symlink()
        assert target.read_bytes() == b"fresh bytes"
        assert victim.read_bytes() == b"victim bytes"

    def test_permissions_follow_process_umask(self, tmp_path: Path) -> None:
        target = tmp_path / "state.json"
        previous = os.umask(0o022)
        try:
            fsatomic.publish_bytes(str(target), b"x")
        finally:
            os.umask(previous)
        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_fsync_directory_is_reusable_and_best_effort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        real_fsync = os.fsync

        def recording_fsync(fd: int) -> None:
            calls.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            real_fsync(fd)

        monkeypatch.setattr(fsatomic.os, "fsync", recording_fsync)
        fsatomic.fsync_directory(str(tmp_path))
        assert calls == ["dir"]
        fsatomic.fsync_directory(str(tmp_path / "missing"))


class TestSingleAuthority:
    """Exactly one authority, and every consolidated consumer imports it."""

    def test_no_local_helper_copies_in_consolidated_scopes(self) -> None:
        offenders: list[tuple[str, str]] = []
        for scope in CONSOLIDATED_SCOPES:
            for path in sorted((REPO_ROOT / scope).rglob("*.py")):
                if path.name == "fsatomic.py" and path.parent.name == "persistence":
                    continue
                text = path.read_text(encoding="utf-8")
                for marker in DUPLICATE_HELPER_MARKERS:
                    if marker in text:
                        offenders.append((str(path.relative_to(REPO_ROOT)), marker))
        assert offenders == []

    def test_consumers_import_the_authority_object(self) -> None:
        from confflow.application import v4_run
        from confflow.application.execution import workflow_adapter
        from confflow.persistence import arbitration, generation, imports, publication, run_state
        from confflow.producer import run_result

        for module in (run_state, publication, generation, arbitration, v4_run, run_result):
            assert module.publish_bytes is fsatomic.publish_bytes
        assert imports.fsync_directory is fsatomic.fsync_directory
        assert workflow_adapter.fsync_directory is fsatomic.fsync_directory

    def test_producer_publish_goes_through_the_authority(self) -> None:
        import inspect

        from confflow.producer import run_result

        source = inspect.getsource(run_result.publish_manifest_atomically)
        assert "publish_bytes(" in source
        assert "os.replace" not in source
        assert "jsonschema.validate" in source
        assert "verify_manifest_on_disk" in source


class TestJobNameSanitizerAuthority:
    """One sanitizer authority re-exported by both program renderers."""

    def test_both_renderers_reexport_the_single_authority(self) -> None:
        from confflow.programs import _naming
        from confflow.programs.gaussian import rendering as gaussian
        from confflow.programs.orca import rendering as orca

        assert gaussian.sanitize_job_name is _naming.sanitize_job_name
        assert orca.sanitize_job_name is _naming.sanitize_job_name

    @staticmethod
    def _legacy_sanitize(value: str, fallback: str = "job") -> str:
        """Exact rule the two former renderer copies implemented."""
        import re

        pattern = re.compile(r"[^A-Za-z0-9_.\-]+")
        cleaned = pattern.sub("_", str(value).strip()).strip("._")
        if not cleaned:
            cleaned = pattern.sub("_", str(fallback).strip()).strip("._")
        if not cleaned:
            cleaned = "job"
        return cleaned[:128]

    def test_authority_matches_legacy_rule_on_adversarial_corpus(self) -> None:
        corpus = [chr(code) for code in range(32, 127)] + [
            "s_opt:s0",
            "x" * 200,
            "",
            "...",
            "..hidden..",
            "  spaced  ",
            "a/b\\c",
            "/etc/passwd",
            "\t\n",
            "τσ",
            "job",
        ]
        for value in corpus:
            assert _sanitize_via_authority(value) == self._legacy_sanitize(value), repr(value)
            assert _sanitize_via_authority(value, fallback="f/b") == self._legacy_sanitize(
                value, fallback="f/b"
            ), repr(value)

    @pytest.mark.parametrize(
        ("value", "fallback", "expected"),
        (
            ("s_opt:s0", None, "s_opt_s0"),
            ("...", None, "job"),
            ("", None, "job"),
            ("..hidden..", None, "hidden"),
            ("  a b  ", None, "a_b"),
            ("/etc/passwd", None, "etc_passwd"),
            ("a/b\\c", None, "a_b_c"),
            ("τ", None, "job"),
            ("x" * 200, None, "x" * 128),
            ("...", "a b", "a_b"),
            ("", "...", "job"),
            ("   ", " ", "job"),
        ),
    )
    def test_sanitizer_truth_table(self, value: str, fallback: str | None, expected: str) -> None:
        if fallback is None:
            assert _sanitize_via_authority(value) == expected
        else:
            assert _sanitize_via_authority(value, fallback=fallback) == expected
