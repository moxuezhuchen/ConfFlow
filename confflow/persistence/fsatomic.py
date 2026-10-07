#!/usr/bin/env python3

"""Durable single-file publication primitive for the V4 persistence layer.

Single authority for frozen write protocol: create destination directory if missing;
write payload to unique temp ``<target>.tmp.<pid>.<counter>`` in destination directory
(same filesystem for ``os.replace``); flush + fsync file before replace so truncated
payload never becomes visible; ``os.replace`` temp onto target (readers see old or new,
never mix); drop temp on every failure path (best effort); fsync destination directory
(best effort) so new name survives crash after commit. Standard library only.
"""

from __future__ import annotations

import itertools
import os
from typing import Final

__all__ = ["fsync_directory", "publish_bytes"]

#: Process-local uniqueness source for temp file names.
_TMP_COUNTER: Final = itertools.count()


def fsync_directory(directory: str) -> None:
    """Fsync *directory* so a new publication survives a crash."""
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


def publish_bytes(target_path: str, payload: bytes) -> None:
    """Durably publish *payload* at *target_path* via temp file + rename."""
    directory = os.path.dirname(target_path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{target_path}.tmp.{os.getpid()}.{next(_TMP_COUNTER)}"
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
    fsync_directory(directory)
