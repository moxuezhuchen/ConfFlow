"""Minimal COMPLETED V4 manifest publisher for service/control test doubles.

L2-CF-delete alternative coverage: the service/control success paths no
longer tolerate a missing manifest as an empty artifact set, so doubles
that report completion must publish the same ``run_result.json`` the
single V4 application would. No generation record or arbitration ledger
is written, so only the manifest's own integrity is asserted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

__all__ = ["publish_completed_v4_manifest"]


def publish_completed_v4_manifest(work_dir: Path, *, run_id: str = "test-v4-run") -> Path:
    """Write a minimal valid COMPLETED ``run_result.json`` plus its artifact."""
    payload_bytes = b"1\nV4 artifact\nH 0 0 0\n"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "result.xyz").write_text(payload_bytes.decode("utf-8"), encoding="utf-8")
    manifest = {
        "content_schema": "confflow.run_result_manifest.v1",
        "run_id": run_id,
        "status": "completed",
        "definition_digest": "sha256:" + "ab" * 32,
        "provenance": {},
        "steps": [],
        "analyses": [],
        "artifacts": [
            {
                "role": "result",
                "checksum": "sha256:" + hashlib.sha256(payload_bytes).hexdigest(),
                "locator": "result.xyz",
            }
        ],
    }
    manifest_path = work_dir / "run_result.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path
