"""Slot pool management for concurrent job execution."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


class SlotState(str, Enum):
    FREE = "free"
    BUSY = "busy"


@dataclass
class Slot:
    id: int
    state: SlotState = SlotState.FREE
    job_id: str | None = None


@dataclass
class SlotReservation:
    slot: Slot
    release: Callable[[], None]


class SlotManager:
    """Manages a pool of execution slots.

    Each slot can run one job at a time.  Slots are acquired with
    ``acquire()`` and released with ``release()`` (or the context manager).
    """

    def __init__(self, num_slots: int = 2):
        if num_slots < 1:
            raise ValueError("num_slots must be >= 1")
        self.num_slots = num_slots
        self._slots = [Slot(id=i) for i in range(num_slots)]
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def acquire(self, timeout: float | None = None) -> SlotReservation | None:
        """Acquire a free slot, blocking up to ``timeout`` seconds.

        Returns a ``SlotReservation`` if successful, ``None`` if timeout.
        """
        with self._cond:
            deadline: float | None = None
            if timeout is not None:
                deadline = _monotonic() + timeout

            while True:
                for slot in self._slots:
                    if slot.state == SlotState.FREE:
                        slot.state = SlotState.BUSY
                        logger.debug("Slot %d acquired", slot.id)
                        return SlotReservation(slot=slot, release=lambda: self._release(slot))

                # No free slot — wait
                remaining: float | None = None
                if deadline is not None:
                    remaining = deadline - _monotonic()
                    if remaining <= 0:
                        logger.warning("Slot acquisition timed out after %.1fs", timeout)
                        return None

                logger.debug("All %d slots busy, waiting for one to free", self.num_slots)
                notified = self._cond.wait(timeout=remaining)
                if not notified and deadline is not None and _monotonic() >= deadline:
                    logger.warning("Slot acquisition timed out after %.1fs", timeout)
                    return None

    def _release(self, slot: Slot) -> None:
        with self._lock:
            if slot.state == SlotState.BUSY:
                slot.state = SlotState.FREE
                slot.job_id = None
                logger.debug("Slot %d released", slot.id)
                self._cond.notify()

    def release(self, slot: Slot) -> None:
        self._release(slot)

    def get_status(self) -> dict:
        """Return the status of all slots."""
        with self._lock:
            return {
                "total": self.num_slots,
                "free": sum(1 for s in self._slots if s.state == SlotState.FREE),
                "busy": sum(1 for s in self._slots if s.state == SlotState.BUSY),
                "slots": [
                    {"id": s.id, "state": s.state.value, "job_id": s.job_id}
                    for s in self._slots
                ],
            }


def _monotonic() -> float:
    import time
    return time.monotonic()
