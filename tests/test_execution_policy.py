"""Pure execution policy extraction and compatibility contracts."""

from __future__ import annotations

import confflow.application.execution.policy as policy
import confflow.application.execution.service as service
from confflow.application.execution import Artifact


def test_service_private_policy_names_remain_compatible():
    """The old service helper names remain aliases of the extracted policy rules."""
    for name in (
        "_ID_CHARS",
        "_terminal_error",
        "_validate_prepare",
        "_is_identifier",
        "_is_digest",
        "_identities_match",
        "_parse_cursor",
        "_validated_artifacts",
        "_canonical_path",
    ):
        assert getattr(service, name) is getattr(policy, name)


def test_policy_validates_and_orders_artifact_metadata_without_service():
    """Manifest validation remains deterministic in the I/O-free policy module."""
    artifacts = (
        Artifact("z_terminal", "z/output.xyz", "f" * 64, 2, "text/plain"),
        Artifact("a_terminal", "a/output.xyz", "e" * 64, 1, "text/plain"),
    )

    assert policy.validated_artifacts(artifacts) == (artifacts[1], artifacts[0])
    assert policy.parse_cursor("r00000000000000000007") == 7
