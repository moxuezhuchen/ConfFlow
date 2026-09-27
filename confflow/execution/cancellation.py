#!/usr/bin/env python3

"""Execution-layer cancellation signal.

One signal type carries cancellation intent across every layer: service /
control cancel and the CLI ``CANCEL`` beacon become a probe, the probe
becomes ``V4RunRequest.should_cancel``, the application threads it into
every batch step request, the batch checks it between item launches and
forwards it to the executor, and the executor terminates the running
native process boundary through :class:`NativeProcessSupervisor`.  A
requested signal is one-way and thread-safe: it never resets, so a cancel
racing a launch cannot be lost, and every layer observes the same truth.

Beacon probes fail closed: an unreadable beacon path reports "stop"
rather than silently continuing to launch scientific work.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

__all__ = [
    "CancellationSignal",
    "beacon_probe",
    "combine_probes",
]


class CancellationSignal:
    """Thread-safe, one-way cancellation signal shared across layers."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        """Request cancellation; idempotent and never reset."""
        self._event.set()

    @property
    def requested(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def __call__(self) -> bool:
        """Return whether cancellation has been requested (probe form)."""
        return self._event.is_set()


def beacon_probe(
    beacon_path: str | None,
    *,
    signal: CancellationSignal | None = None,
) -> Callable[[], bool]:
    """Return a cancellation probe over a beacon file and/or signal.

    The probe returns True when the in-process *signal* is requested or
    the beacon file exists.  A configured-but-unreadable beacon reports
    True (fail closed): a cancellation channel that cannot be inspected
    must stop scientific work rather than risk an unstoppable run.
    """
    if beacon_path is not None and (not isinstance(beacon_path, str) or not beacon_path.strip()):
        raise ValueError("beacon_path must be a non-empty string or None")

    def probe() -> bool:
        if signal is not None and signal.requested:
            return True
        if beacon_path:
            try:
                return os.path.exists(beacon_path)
            except OSError:
                return True
        return False

    return probe


def combine_probes(*probes: Callable[[], bool] | None) -> Callable[[], bool]:
    """Return a probe that is True when any supplied probe is True."""
    active = tuple(probe for probe in probes if probe is not None)

    def probe() -> bool:
        return any(bool(item()) for item in active)

    return probe
