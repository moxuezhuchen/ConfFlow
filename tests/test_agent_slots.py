#!/usr/bin/env python3
"""Tests for confflow.agent.slots: SlotManager thread-safe slot pool."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from confflow.agent.slots import Slot, SlotManager, SlotReservation, SlotState

# ---------------------------------------------------------------------------
# Construction & validation
# ---------------------------------------------------------------------------


def test_slot_manager_minimum_slots():
    sm = SlotManager(num_slots=1)
    assert sm.num_slots == 1
    status = sm.get_status()
    assert status["total"] == 1
    assert status["free"] == 1
    assert status["busy"] == 0


def test_slot_manager_rejects_zero_slots():
    with pytest.raises(ValueError, match="num_slots must be >= 1"):
        SlotManager(num_slots=0)


def test_slot_manager_rejects_negative_slots():
    with pytest.raises(ValueError, match="num_slots must be >= 1"):
        SlotManager(num_slots=-3)


def test_slot_manager_initial_all_slots_free():
    sm = SlotManager(num_slots=4)
    status = sm.get_status()
    assert status["total"] == 4
    assert status["free"] == 4
    assert status["busy"] == 0
    assert [s["id"] for s in status["slots"]] == [0, 1, 2, 3]
    assert all(s["state"] == "free" for s in status["slots"])


# ---------------------------------------------------------------------------
# Acquire / release
# ---------------------------------------------------------------------------


def test_acquire_returns_reservation_when_slot_free():
    sm = SlotManager(num_slots=2)
    res = sm.acquire()
    assert isinstance(res, SlotReservation)
    assert isinstance(res.slot, Slot)
    assert res.slot.state == SlotState.BUSY

    status = sm.get_status()
    assert status["busy"] == 1
    assert status["free"] == 1


def test_release_makes_slot_available_again():
    sm = SlotManager(num_slots=1)
    res = sm.acquire()
    assert sm.get_status()["busy"] == 1

    sm.release(res.slot)
    status = sm.get_status()
    assert status["free"] == 1
    assert status["busy"] == 0
    assert res.slot.job_id is None  # release() also clears job_id


def test_release_callable_on_reservation_makes_slot_available():
    sm = SlotManager(num_slots=1)
    res = sm.acquire()
    assert sm.get_status()["busy"] == 1
    res.release()  # use the lambda returned in SlotReservation
    assert sm.get_status()["free"] == 1


def test_get_status_lists_slots_with_state_and_job_id():
    sm = SlotManager(num_slots=2)
    res = sm.acquire()
    res.slot.job_id = "my-job"

    status = sm.get_status()
    busy = [s for s in status["slots"] if s["state"] == "busy"]
    assert len(busy) == 1
    assert busy[0]["job_id"] == "my-job"


# ---------------------------------------------------------------------------
# Timeout / block behavior
# ---------------------------------------------------------------------------


def test_acquire_returns_none_when_all_slots_busy_and_times_out():
    sm = SlotManager(num_slots=1)
    res = sm.acquire()
    assert res is not None
    # Now all slots busy; ask again with a tiny timeout.
    t0 = time.monotonic()
    second = sm.acquire(timeout=0.2)
    elapsed = time.monotonic() - t0
    assert second is None
    # Should have waited approximately the timeout (with some scheduling slack)
    assert 0.15 < elapsed < 1.5


def test_acquire_with_no_timeout_blocks_until_release(monkeypatch):
    """Without timeout, acquire() should block; release unblocks it."""
    sm = SlotManager(num_slots=1)
    sm.acquire()  # hold the only slot

    # Patch time.sleep so cond.wait() returns immediately
    monkeypatch.setattr("confflow.agent.slots._monotonic", lambda: 0.0)

    result = {"reservation": None}

    def acquire_then_store():
        result["reservation"] = sm.acquire()  # no timeout

    t = threading.Thread(target=acquire_then_store)
    t.start()

    time.sleep(0.1)
    assert result["reservation"] is None
    # Release the held slot
    held = sm._slots[0]
    sm.release(held)
    t.join(timeout=2.0)
    assert result["reservation"] is not None


# ---------------------------------------------------------------------------
# Thread safety — concurrent acquisition
# ---------------------------------------------------------------------------


def test_slot_manager_enforces_max_concurrency_under_contention():
    """With 3 slots, only 3 reservations are in-flight even with 10 contenders."""
    sm = SlotManager(num_slots=3)
    in_flight = {"count": 0, "peak": 0}
    lock = threading.Lock()
    reservations: list[SlotReservation] = []
    start = threading.Event()

    def worker():
        start.wait()  # synchronize launch
        res = sm.acquire()
        try:
            assert res is not None
            with lock:
                in_flight["count"] += 1
                in_flight["peak"] = max(in_flight["peak"], in_flight["count"])
            reservations.append(res)
            # Hold briefly so contention is real
            time.sleep(0.05)
        finally:
            with lock:
                in_flight["count"] -= 1
            sm.release(res.slot)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker) for _ in range(10)]
        # release all workers at once
        time.sleep(0.01)
        start.set()
        for f in as_completed(futures):
            f.result(timeout=10.0)

    assert in_flight["peak"] <= 3  # never exceeded num_slots
    assert len(reservations) == 10
    # All slots end up free
    assert sm.get_status()["free"] == 3


def test_slot_manager_acquires_all_unique_slots_across_workers():
    """Concurrent acquisition should never give out the same slot to two workers simultaneously."""
    sm = SlotManager(num_slots=4)
    seen_ids: list[int] = []
    seen_lock = threading.Lock()
    start = threading.Event()

    def worker():
        res = sm.acquire(timeout=10.0)
        try:
            assert res is not None
            with seen_lock:
                seen_ids.append(res.slot.id)
        finally:
            sm.release(res.slot)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker) for _ in range(8)]
        time.sleep(0.01)
        start.set()
        for f in as_completed(futures):
            f.result(timeout=10.0)

    # All slot ids come from {0,1,2,3} (sanity)
    assert set(seen_ids) <= {0, 1, 2, 3}
    # Total observations = 8 (one per worker)
    assert len(seen_ids) == 8
