"""Application-facing shared-filesystem approval verification.

The verification rules live in ``confflow.persistence.storage_policy``; this
module keeps the execution-layer names and reports failures as repository
errors with the stable ``REPOSITORY_UNAVAILABLE`` code.
"""

from __future__ import annotations

import os  # noqa: F401  # module attribute kept: tests patch shared_fs_approval.os.fstat
from pathlib import Path

from ...persistence.storage_policy import ApprovalVerifier as _PersistenceApprovalVerifier
from ...persistence.storage_policy import SharedFilesystemApproval, StorageUnsupportedError
from .errors import ErrorCode, ExecutionServiceError

__all__ = ["ApprovalVerifier", "SharedFilesystemApproval"]


class ApprovalVerifier(_PersistenceApprovalVerifier):
    """Verify root-owned approval evidence and raise execution-layer errors."""

    def verify(
        self, path: str | os.PathLike[str], *, root: Path, filesystem_type: str
    ) -> SharedFilesystemApproval:
        """Return the verified approval or raise ``ExecutionServiceError``."""
        try:
            return super().verify(path, root=root, filesystem_type=filesystem_type)
        except StorageUnsupportedError as error:
            raise ExecutionServiceError(ErrorCode.REPOSITORY_UNAVAILABLE, str(error)) from error
