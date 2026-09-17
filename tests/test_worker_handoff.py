"""Boundary and compatibility tests for the external-worker handoff reader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import confflow.control_worker as control_worker
import confflow.worker_handoff as worker_handoff

pytestmark = pytest.mark.skipif(os.name != "posix", reason="worker handoff contract requires POSIX")


def test_control_worker_reexports_handoff_helpers_by_identity() -> None:
    """Legacy private import and patch paths remain stable after extraction."""
    for name in (
        "HANDOFF_SCHEMA",
        "_canonical_json",
        "_file_digest",
        "_load_handoff",
        "_read_json_file",
        "_safe_absolute_path",
        "_sha256_bytes",
        "_validate_attempt_root",
        "_validate_path",
    ):
        assert getattr(control_worker, name) is getattr(worker_handoff, name)


def test_control_worker_handoff_alias_can_still_be_monkeypatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control module remains the patch seam used by older callers."""
    sentinel = object()

    def fake_load(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return sentinel

    monkeypatch.setattr(control_worker, "_load_handoff", fake_load)
    assert control_worker._load_handoff("unused", "unused", sentinel) is sentinel


@pytest.mark.parametrize(
    "value",
    [
        "relative/path.json",
        r"/attempt\handoff.json",
        "/attempt/../outside.json",
    ],
)
def test_safe_absolute_path_rejects_noncanonical_locators(value: str) -> None:
    with pytest.raises(ValueError):
        worker_handoff._safe_absolute_path(value, "handoff")


def test_safe_absolute_path_normalizes_only_canonical_posix_path() -> None:
    assert worker_handoff._safe_absolute_path("/attempt/handoff.json", "handoff") == (
        "/attempt/handoff.json"
    )


def test_read_json_file_uses_owner_owned_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "handoff.json"
    path.write_text('{"ok": true}', encoding="utf-8")
    assert worker_handoff._read_json_file(path) == {"ok": True}
