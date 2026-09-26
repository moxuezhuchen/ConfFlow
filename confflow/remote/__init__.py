#!/usr/bin/env python3

"""V4 remote execution boundary: typed handoff, staging, and transport.

The remote worker consumes compiled V4 semantics (never YAML, graphs, or
task-name dispatch) and runs the same :class:`WorkItemExecutor` path as
local execution.  This package imports ``confflow.domain`` plus the
standard library at module load; executor, program, workflow, and
persistence resolvers run inside functions so importing this package never
pulls a runtime stack eagerly.
"""

from __future__ import annotations

from .envelope import (
    BUNDLE_DIGEST_KIND,
    HANDOFF_SCHEMA_V2,
    MAX_HANDOFF_BYTES,
    RESULT_SCHEMA_V2,
    ArtifactBundleEntry,
    ExecutionDefinition,
    InputBundleManifest,
    ResultBundle,
    ResultEntry,
    ResultProducedArtifact,
    StructureBundleEntry,
    WorkerHandoffV2,
    bundle_entry_digest,
    canonical_envelope_bytes,
    compute_bundle_digest,
    compute_handoff_digest,
    compute_result_digest,
)

__all__ = [
    "BUNDLE_DIGEST_KIND",
    "HANDOFF_SCHEMA_V2",
    "MAX_HANDOFF_BYTES",
    "RESULT_SCHEMA_V2",
    "ArtifactBundleEntry",
    "ExecutionDefinition",
    "InputBundleManifest",
    "ResultBundle",
    "ResultEntry",
    "ResultProducedArtifact",
    "StructureBundleEntry",
    "WorkerHandoffV2",
    "bundle_entry_digest",
    "canonical_envelope_bytes",
    "compute_bundle_digest",
    "compute_handoff_digest",
    "compute_result_digest",
]
