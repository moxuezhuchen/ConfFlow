#!/usr/bin/env python3

"""Durable single-file publication primitive for the V4 persistence layer.

One authority for the frozen write protocol every durable publication in the
V4 core used to copy locally:

1. create the destination directory when missing;
2. write the exact payload to a unique temp file in the destination
   directory (``<target>.tmp.<pid>.<counter>``), so the final
   ``os.replace`` always stays inside one filesystem;
3. flush and fsync the file *before* the replace, so a truncated payload can
   never become visible under the final name;
4. ``os.replace`` the temp file onto the target (atomic replace semantics
   for readers: they see the old bytes or the new bytes, never a mix);
5. drop the temp file on every failure path, best effort;
6. fsync the destination directory, best effort, so the new name survives a
   crash after the replace committed.

Like the rest of the persistence layer this module imports the standard
library only.
"""

from __future__ import annotations

import itertools
import os
from typing import Final

__all__ = ["fsync_directory", "publish_bytes"]

#: Process-local uniqueness source for temp file names.
_TMP_COUNTER: Final = itertools.count()


def fsync_directory(directory: str) -> None:
    """Fsync *directory* so a new publication survives a crash.

    Best effort by contract: a directory that cannot be opened or synced is
    skipped, because durable-name preservation must never turn an already
    committed publication into a caller-visible error.
    """
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
    """Durably publish *payload* at *target_path* via temp file + rename.

    Parameters
    ----------
    target_path : str
        Final destination path; the temp file lives in the same directory.
    payload : bytes
        Exact bytes to durably persist.
    """
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
