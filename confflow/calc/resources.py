#!/usr/bin/env python3

"""Resource monitoring module (optional dependency: psutil)."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("confflow.calc.resources")

__all__ = [
    "ResourceMonitor",
]

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:
    psutil = None


def _psutil_exception_types() -> tuple[type[BaseException], ...]:
    """Return supported psutil-related exception types, tolerant of test doubles."""
    base: tuple[type[BaseException], ...] = (AttributeError, OSError, RuntimeError)
    err_type = getattr(psutil, "Error", None)
    if isinstance(err_type, type) and issubclass(err_type, BaseException):
        return (err_type, *base)
    return base


class ResourceMonitor:
    """Dynamic resource monitor.

    Limits whether new tasks can be launched based on CPU/memory usage
    when dynamic resource management is enabled.
    """

    def __init__(self, cpu_threshold: int = 80, mem_threshold: int = 80, check_interval: int = 5):
        self.cpu_threshold = cpu_threshold
        self.mem_threshold = mem_threshold
        self.check_interval = check_interval
        self.enabled = psutil is not None

    def get_current_load(self) -> tuple[float, float]:
        if not self.enabled:
            return 0.0, 0.0
        try:
            assert psutil is not None
            return psutil.cpu_percent(interval=0.5), psutil.virtual_memory().percent
        except _psutil_exception_types() as e:
            logger.debug(f"Failed to get system load: {e}")
            return 0.0, 0.0

    def can_start_new_task(self, current_active_workers: int, max_workers: int) -> bool:
        if not self.enabled:
            return True
        if current_active_workers >= max_workers:
            return False
        cpu, mem = self.get_current_load()
        if cpu > self.cpu_threshold or mem > self.mem_threshold:
            return False
        return True

    def wait_for_resources(self, max_wait_seconds: int = 300) -> bool:
        if not self.enabled:
            return True
        waited = 0
        while waited < max_wait_seconds:
            cpu, mem = self.get_current_load()
            if cpu <= self.cpu_threshold and mem <= self.mem_threshold:
                return True
            time.sleep(self.check_interval)
            waited += self.check_interval
        return False
