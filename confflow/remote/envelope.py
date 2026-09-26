#!/usr/bin/env python3

"""V4 remote execution envelope contracts (V4-4, frozen shared authority).

This module is owned by the main agent and is **frozen**: subagents read it
but never edit it.  It defines the worker-handoff V2 and result-bundle V2
typed models so the handoff, staging, worker, lease, test, and transport
workstreams proceed in parallel without interface drift.

Design rules (frozen):

- Pydantic v2 strict models (``strict=True, extra="forbid", frozen=True``)
  are the single source of truth; JSON Schema is generated from them, never
  hand-written twice.
- Canonical digest (JCS via :mod:`confflow.domain.canonical`) covers
  identity, content, and contract versions only — never worker directories,
  staging names, timestamps, hostnames, or scheduler width.
- ``bundle_locator`` names inside a bundle directory are derived
  deterministically (``files/<index>-<safe-name>``); worker-local
  materialized paths never enter any model or digest.
- The remote worker receives compiled execution semantics
  (:class:`ExecutionDefinition`); it never sees a ``WorkflowDocument``,
  YAML, graph, or task-name dispatch.

Dependency rule: this module imports ``confflow.domain`` (canonical only)
plus stdlib and pydantic.  Never ``confflow.execution``,
``confflow.workflow``, ``confflow.calc``, or legacy runtimes at module
load; resolvers run inside functions on the worker side.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..domain.canonical import canonical_json_bytes, typed_digest

__all__ = [
    "BUNDLE_DIGEST_KIND",
    "HANDOFF_SCHEMA_V2",
    "MAX_HANDOFF_BYTES",
    "RESULT_SCHEMA_V2",
    "ArtifactBundleEntry",
    "ExecutionDefinition",
    "InputBundleManifest",
    "ResultBundle",
    "ResultProducedArtifact",
    "ResultEntry",
    "StructureBundleEntry",
    "WorkerHandoffV2",
    "compute_bundle_digest",
    "compute_handoff_digest",
    "compute_result_digest",
]

#: Protocol identity of the worker-handoff V2 envelope.  Never V1.
HANDOFF_SCHEMA_V2: Final[str] = "confflow.control.worker-handoff.v2"

#: Protocol identity of the worker result bundle.  Never V1.
RESULT_SCHEMA_V2: Final[str] = "confflow.control.worker-result.v2"

#: Digest domain marker for bundle/handoff/result identity.
BUNDLE_DIGEST_KIND: Final[str] = "confflow.remote.bundle.v1"

#: Upper bound for a handoff envelope file; larger payloads fail closed.
MAX_HANDOFF_BYTES: Final[int] = 64 * 1024 * 1024

_STRICT_FROZEN = ConfigDict(strict=True, extra="forbid", frozen=True)

_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


def _sequence_to_tuple(value: Any) -> Any:
    """Coerce JSON-native sequences to tuples before strict validation.

    Pydantic strict mode accepts tuples but not the JSON arrays every
    envelope necessarily round-trips through.  This pre-validator keeps
    the frozen tuple types while accepting their JSON form; anything else
    still fails strict validation downstream.
    """
    if isinstance(value, list):
        return tuple(_sequence_to_tuple(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_sequence_to_tuple(item) for item in value)
    return value


def _jsonable(value: Any) -> Any:
    """Convert nested envelope models to JSON-mode dumps for digesting."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


class StructureBundleEntry(BaseModel):
    """One structure input transported as a typed canonical payload."""

    model_config = _STRICT_FROZEN

    entry_kind: Literal["structure"] = "structure"
    structure_id: str = Field(min_length=1)
    payload: dict[str, Any]
    digest: str = Field(pattern=_DIGEST_PATTERN)
    port: str = Field(default="structure", min_length=1)
    """Named input slot this structure fills (``structure`` for the
    standard driving port; ``reactant``/``product``/``guess`` for named
    inputs).  The worker rebuilds slots from this field, never from
    entry order.  Defaulted so V4-4 envelopes without the field still
    validate (backward-compatible V2 extension for V4-5 named inputs)."""


class ArtifactBundleEntry(BaseModel):
    """One artifact input: identity plus content checksum, never a raw path."""

    model_config = _STRICT_FROZEN

    entry_kind: Literal["artifact"] = "artifact"
    artifact_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    checksum: str = Field(pattern=_DIGEST_PATTERN)
    subject_structure_id: str | None = None
    media_type: str | None = None
    bundle_locator: str = Field(min_length=1)
    size_bytes: int | None = Field(default=None, ge=0)


class ResultEntry(BaseModel):
    """One result input as a typed scientific payload."""

    model_config = _STRICT_FROZEN

    entry_kind: Literal["result"] = "result"
    result_id: str = Field(min_length=1)
    payload: dict[str, Any]
    digest: str = Field(pattern=_DIGEST_PATTERN)


class InputBundleManifest(BaseModel):
    """Typed input bundle: every entry has stable identity and a digest."""

    model_config = _STRICT_FROZEN

    entries: tuple[StructureBundleEntry | ArtifactBundleEntry | ResultEntry, ...] = ()

    @field_validator("entries", mode="before")
    @classmethod
    def _coerce_entries(cls, value: Any) -> Any:
        """Accept the JSON array form of the frozen tuple."""
        return _sequence_to_tuple(value)


class ExecutionDefinition(BaseModel):
    """Compiled, resolved execution semantics for one work item.

    Everything the worker needs to construct the *same*
    :class:`WorkItemExecutor` request the producer would build locally:
    program vocabulary, native definition, adapter/profile/check/recovery
    contracts with versions, resources, and resolved scientific parameters.
    No graph, no YAML, no bindings, no scheduler width.
    """

    model_config = _STRICT_FROZEN

    program: str = Field(min_length=1)
    native: dict[str, Any] = Field(default_factory=dict)
    execution_adapter: str = "standard"
    result_profile: str = "standard"
    checks: tuple[str, ...] = ()
    check_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    recovery: str = "none"
    recovery_params: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    charge: int | None = None
    multiplicity: int | None = None
    freeze: tuple[int, ...] | None = None
    step_semantic_digest: str = Field(pattern=_DIGEST_PATTERN)
    contract_versions: dict[str, str] = Field(default_factory=dict)

    @field_validator("checks", "freeze", mode="before")
    @classmethod
    def _coerce_sequences(cls, value: Any) -> Any:
        """Accept the JSON array form of the frozen tuples."""
        return _sequence_to_tuple(value)


class EnvironmentRequest(BaseModel):
    """Transport hints for where the work should run.  Never digested."""

    model_config = _STRICT_FROZEN

    program: str = "unspecified"
    target: str | None = None
    walltime_seconds: int | None = Field(default=None, gt=0)


class WorkerHandoffV2(BaseModel):
    """Typed V2 handoff envelope: one work-item attempt, fully specified."""

    model_config = _STRICT_FROZEN

    schema: Literal["confflow.control.worker-handoff.v2"] = "confflow.control.worker-handoff.v2"
    protocol_version: str = "v2"
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    work_item_id: str = Field(min_length=1)
    logical_key: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    launch_token: str = Field(min_length=1)
    work_item_digest: str = Field(pattern=_DIGEST_PATTERN)
    step_semantic_digest: str = Field(pattern=_DIGEST_PATTERN)
    producer_provenance: dict[str, Any] = Field(default_factory=dict)
    environment_request: EnvironmentRequest = Field(default_factory=EnvironmentRequest)
    execution: ExecutionDefinition
    inputs: InputBundleManifest = Field(default_factory=InputBundleManifest)
    manifest_digest: str = Field(pattern=_DIGEST_PATTERN)

    @model_validator(mode="after")
    def _check_manifest_digest(self) -> WorkerHandoffV2:
        """Fail closed when the manifest digest does not recompute."""
        expected = compute_handoff_digest(self._digest_payload())
        if expected != self.manifest_digest:
            raise ValueError("handoff manifest digest mismatch; envelope is not trusted")
        return self

    def _digest_payload(self) -> dict[str, Any]:
        """Return the digest-covered payload (no transport facts)."""
        dumped: dict[str, Any] = self.model_dump(mode="json")
        dumped.pop("manifest_digest", None)
        dumped.pop("environment_request", None)
        return dumped

    @classmethod
    def new(cls, **fields: Any) -> WorkerHandoffV2:
        """Build an envelope, computing its manifest digest."""
        environment_request = fields.pop("environment_request", None)
        payload = {key: _jsonable(value) for key, value in fields.items()}
        if environment_request is not None:
            payload["environment_request"] = _jsonable(environment_request)
        return cls(**payload, manifest_digest=_handoff_digest_of(cls, payload))


def _model_digest_payload(
    model_cls: type[BaseModel], payload: dict[str, Any], *, exclude: frozenset[str]
) -> dict[str, Any]:
    """Return the digest-covered payload with defaults materialized."""
    dumped: dict[str, Any] = model_cls.model_construct(**payload).model_dump(mode="json")
    for key in exclude:
        dumped.pop(key, None)
    return dumped


def _handoff_digest_of(model_cls: type[BaseModel], payload: dict[str, Any]) -> str:
    """Return the handoff digest for a writer payload."""
    return compute_handoff_digest(
        _model_digest_payload(
            model_cls, payload, exclude=frozenset({"manifest_digest", "environment_request"})
        )
    )


def _result_digest_of(model_cls: type[BaseModel], payload: dict[str, Any]) -> str:
    """Return the result-bundle digest for a writer payload."""
    return compute_result_digest(
        _model_digest_payload(
            model_cls, payload, exclude=frozenset({"bundle_digest", "transport_metadata"})
        )
    )


def compute_handoff_digest(payload: dict[str, Any]) -> str:
    """Return the deterministic digest of a handoff digest-payload."""
    return typed_digest(BUNDLE_DIGEST_KIND, {"handoff": payload})


def compute_bundle_digest(payload: dict[str, Any]) -> str:
    """Return the deterministic digest of a bundle payload."""
    return typed_digest(BUNDLE_DIGEST_KIND, {"bundle": payload})


class ResultProducedArtifact(BaseModel):
    """One worker-produced artifact announced for producer-side import."""

    model_config = _STRICT_FROZEN

    artifact_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    checksum: str = Field(pattern=_DIGEST_PATTERN)
    size_bytes: int | None = Field(default=None, ge=0)
    subject_structure_id: str | None = None
    media_type: str | None = None
    bundle_locator: str = Field(min_length=1)


class ResultBundle(BaseModel):
    """Typed V2 result bundle returned by the remote worker."""

    model_config = _STRICT_FROZEN

    schema: Literal["confflow.control.worker-result.v2"] = "confflow.control.worker-result.v2"
    protocol_version: str = "v2"
    run_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    work_item_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    launch_token: str = Field(min_length=1)
    work_item_digest: str = Field(pattern=_DIGEST_PATTERN)
    environment: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any]
    produced_artifacts: tuple[ResultProducedArtifact, ...] = ()
    bundle_digest: str = Field(pattern=_DIGEST_PATTERN)
    transport_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("produced_artifacts", mode="before")
    @classmethod
    def _coerce_produced(cls, value: Any) -> Any:
        """Accept the JSON array form of the frozen tuple."""
        return _sequence_to_tuple(value)

    @model_validator(mode="after")
    def _check_bundle_digest(self) -> ResultBundle:
        """Fail closed when the bundle digest does not recompute."""
        expected = compute_result_digest(self._digest_payload())
        if expected != self.bundle_digest:
            raise ValueError("result bundle digest mismatch; bundle is not trusted")
        return self

    def _digest_payload(self) -> dict[str, Any]:
        """Return the digest-covered payload (no transport metadata)."""
        dumped: dict[str, Any] = self.model_dump(mode="json")
        dumped.pop("bundle_digest", None)
        dumped.pop("transport_metadata", None)
        return dumped

    @classmethod
    def new(cls, **fields: Any) -> ResultBundle:
        """Build a result bundle, computing its digest."""
        transport_metadata = fields.pop("transport_metadata", None)
        payload = {key: _jsonable(value) for key, value in fields.items()}
        if transport_metadata is not None:
            payload["transport_metadata"] = _jsonable(transport_metadata)
        return cls(**payload, bundle_digest=_result_digest_of(cls, payload))


def compute_result_digest(payload: dict[str, Any]) -> str:
    """Return the deterministic digest of a result-bundle digest-payload."""
    return typed_digest(BUNDLE_DIGEST_KIND, {"result": payload})


def bundle_entry_digest(entry_type: str, payload: dict[str, Any]) -> str:
    """Return the content digest of one bundle entry payload."""
    return typed_digest(BUNDLE_DIGEST_KIND, {"entry": entry_type, "payload": payload})


def canonical_envelope_bytes(model: BaseModel) -> bytes:
    """Return JCS canonical bytes of a pydantic envelope model."""
    return canonical_json_bytes(model.model_dump(mode="json"))
