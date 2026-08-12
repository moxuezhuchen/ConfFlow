"""Boundary tests for control-worker lease ownership and recovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow import worker_lease
from confflow.worker_lease import TokenLeaseManager


class _FakeLease:
    def __init__(self, owner: dict[str, object] | None) -> None:
        self.path = Path("/fake/claim")
        self.previous_owner = owner
        self.acquired = False
        self.released = False

    def acquire(self) -> bool:
        self.acquired = True
        return True

    def release(self) -> None:
        self.released = True


def _manager_with_owner(
    monkeypatch: pytest.MonkeyPatch, owner: dict[str, object] | None
) -> tuple[TokenLeaseManager, _FakeLease]:
    fake = _FakeLease(owner)
    monkeypatch.setattr(worker_lease, "TokenLaunchLease", lambda *args: fake)
    return TokenLeaseManager("/runs", "run-1", "token-1"), fake


def test_manager_preserves_owner_marker_and_delegates_lease(monkeypatch: pytest.MonkeyPatch):
    owner = {"pid": 11, "pgid": 12, "isolated_session": True}
    manager, fake = _manager_with_owner(monkeypatch, owner)

    assert manager.previous_owner == owner
    assert manager.acquire() is True
    manager.release()
    assert fake.acquired is True
    assert fake.released is True


@pytest.mark.parametrize("owner", [None, {}, {"pid": 11}, {"pid": 11, "pgid": 12}])
def test_unknown_or_incomplete_owner_rejects_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owner: dict[str, object] | None
) -> None:
    manager, _ = _manager_with_owner(monkeypatch, owner)
    monkeypatch.setattr(worker_lease, "_has_live_work_process", lambda *args, **kwargs: False)
    assert manager.can_recover(tmp_path / "work") is False


def test_live_previous_owner_rejects_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = {"pid": 11, "pgid": 12, "isolated_session": True}
    manager, _ = _manager_with_owner(monkeypatch, owner)
    monkeypatch.setattr(worker_lease, "_has_live_work_process", lambda *args, **kwargs: True)

    assert manager.can_recover(tmp_path / "work") is False
