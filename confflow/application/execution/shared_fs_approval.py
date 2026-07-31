"""Root-owned approval evidence required before a shared filesystem is trusted."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, ExecutionServiceError


@dataclass(frozen=True)
class SharedFilesystemApproval:
    """Verified administrator declaration bound to exactly one state-root identity."""

    approval_id: str
    root_realpath: str
    device: int
    inode: int
    filesystem_type: str


class ApprovalVerifier:
    """Verify external root-owned approval JSON without trusting caller-supplied objects."""

    def verify(self, path: str | Path, *, root: Path, filesystem_type: str) -> SharedFilesystemApproval:
        """Read a regular root-owned immutable-to-user approval and bind every identity field."""
        candidate = Path(path)
        descriptor: int | None = None
        try:
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            if not nofollow:
                raise _deny("Shared-FS approval verification requires O_NOFOLLOW")
            descriptor = os.open(candidate, os.O_RDONLY | nofollow)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise _deny("Shared-FS approval must be a regular non-symlink file")
            if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & (
                stat.S_IWGRP | stat.S_IWOTH
            ):
                raise _deny("Shared-FS approval must be root-owned and non-writable by group/other")
            with os.fdopen(descriptor, encoding="utf-8", closefd=False) as stream:
                payload = json.load(stream)
            guarantees = payload["guarantees"]
            root_stat = root.stat()
            if (
                not isinstance(payload, dict)
                or set(payload)
                != {
                    "approval_id",
                    "root_realpath",
                    "device",
                    "inode",
                    "filesystem_type",
                    "guarantees",
                }
                or not isinstance(guarantees, dict)
                or set(guarantees) != {"locking", "atomic_rename", "fsync"}
                or any(type(guarantees[key]) is not bool for key in guarantees)
                or not all(guarantees.values())
                or not isinstance(payload["approval_id"], str)
                or not payload["approval_id"]
                or not isinstance(payload["root_realpath"], str)
                or payload["root_realpath"] != str(root.resolve())
                or type(payload["device"]) is not int
                or payload["device"] < 0
                or payload["device"] != root_stat.st_dev
                or type(payload["inode"]) is not int
                or payload["inode"] < 0
                or payload["inode"] != root_stat.st_ino
                or not isinstance(payload["filesystem_type"], str)
                or not payload["filesystem_type"]
                or payload["filesystem_type"] != filesystem_type
            ):
                raise ValueError("approval does not bind current root/filesystem guarantees")
            return SharedFilesystemApproval(
                payload["approval_id"], payload["root_realpath"], payload["device"], payload["inode"], payload["filesystem_type"]
            )
        except ExecutionServiceError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as error:
            raise _deny(f"Invalid shared-FS approval: {error}") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _deny(message: str) -> ExecutionServiceError:
    return ExecutionServiceError(ErrorCode.REPOSITORY_UNAVAILABLE, message)
