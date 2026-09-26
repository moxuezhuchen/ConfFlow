#!/usr/bin/env python3

"""V4 execution transports: same science, different delivery (V4-4).

:class:`ExecutionTransport` is the only seam between scheduling and native
execution.  :class:`LocalTransport` calls the in-process
:class:`WorkItemExecutor`; :class:`RemoteTransport` moves a
worker-handoff V2 envelope through staging, a remote worker running the
*same* executor path, and validated result import.  Scientific semantics
(adapter, profile, checks, recovery, result contract, persistence contract)
are identical on both paths; only delivery, environment, and staging
location change.

Import rule: this module resolves sibling remote helpers (handoff,
staging, worker) *inside* methods so importing it never pulls the worker
stack (or the executor stack) at module load.  Top level is envelope,
domain, and stdlib only.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureSet
from ..domain.work_item import RecoveryInfo, ResultError, Timing, WorkItem, WorkItemResult
from .envelope import (
    ArtifactBundleEntry,
    ExecutionDefinition,
    InputBundleManifest,
    ResultEntry,
    StructureBundleEntry,
    WorkerHandoffV2,
    bundle_entry_digest,
)

if TYPE_CHECKING:
    from ..execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
    from ..persistence.work_items import SqliteWorkItemStore

__all__ = [
    "ExecutionTransport",
    "LocalTransport",
    "RemoteTransport",
    "build_execution_definition",
    "build_input_bundle_manifest",
]


@runtime_checkable
class ExecutionTransport(Protocol):
    """Delivery seam between the scheduler and native execution.

    Transport-stage failures must surface as failure *results*, never as
    raised errors (only programming errors propagate), so one item's
    delivery problem cannot kill a whole batch.
    """

    def execute(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        attempt: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one work item and return its normalized result."""
        ...


def build_input_bundle_manifest(
    item: WorkItem,
    *,
    artifact_bundle_files: dict[str, str] | None = None,
    artifact_checksums: dict[str, str] | None = None,
) -> InputBundleManifest:
    """Build the deterministic input bundle manifest for *item*.

    Structures ride as canonical payloads; artifacts ride as identity +
    checksum with deterministic bundle locators; results ride as typed
    payloads.  ``artifact_bundle_files`` maps artifact id to the staged
    source filename (basename only, defaulting to the artifact id);
    ``artifact_checksums`` supplies content-derived checksums for refs
    that lack one (an artifact without content identity cannot be
    transported and raises).  Entries sort by stable identity so the
    manifest digest never depends on insertion order.
    """
    entries: list[Any] = []
    index = 0
    for port in sorted(item.named_inputs.structures):
        for record in item.named_inputs.structures[port]:
            payload = record.to_dict()
            entries.append(
                StructureBundleEntry(
                    structure_id=record.id,
                    payload=payload,
                    digest=bundle_entry_digest("structure", payload),
                )
            )
    staged = artifact_bundle_files or {}
    overrides = artifact_checksums or {}
    for port in sorted(item.named_inputs.artifacts):
        for record in sorted(item.named_inputs.artifacts[port], key=lambda ref: ref.id):
            source_name = staged.get(record.id, record.id)
            safe = (
                "".join(
                    char if char.isalnum() or char in ("_", "-", ".") else "_"
                    for char in source_name
                ).strip("._")
                or f"artifact-{index}"
            )
            index += 1
            checksum = overrides.get(record.id, record.checksum)
            if not checksum:
                raise DomainError(
                    f"artifact {record.id!r} has no checksum; content identity "
                    "is required before remote transport"
                )
            entries.append(
                ArtifactBundleEntry(
                    artifact_id=record.id,
                    role=record.role,
                    checksum=checksum,
                    subject_structure_id=record.subject_structure_id,
                    media_type=record.media_type,
                    bundle_locator=f"files/{index:04d}-{safe}",
                )
            )
    for port in sorted(item.named_inputs.results):
        for record in item.named_inputs.results[port]:
            payload = record.to_dict()
            entries.append(
                ResultEntry(
                    result_id=f"{record.kind}:{record.subject_structure_id or 'unbound'}",
                    payload=payload,
                    digest=bundle_entry_digest("result", payload),
                )
            )

    def _entry_sort_key(entry: Any) -> tuple[str, str]:
        if isinstance(entry, StructureBundleEntry):
            return (entry.entry_kind, entry.structure_id)
        if isinstance(entry, ArtifactBundleEntry):
            return (entry.entry_kind, entry.artifact_id)
        return (entry.entry_kind, entry.result_id)

    entries.sort(key=_entry_sort_key)
    return InputBundleManifest(entries=tuple(entries))


def build_execution_definition(
    *,
    program: str,
    native: Any,
    execution_adapter: str | None,
    result_profile: str | None,
    checks: tuple[str, ...],
    check_params: Any,
    recovery: str,
    recovery_params: Any,
    resources: Any,
    charge: int | None,
    multiplicity: int | None,
    freeze: tuple[int, ...] | None,
    step_semantic_digest: str,
    contract_versions: dict[str, str],
) -> ExecutionDefinition:
    """Build the compiled execution definition from resolved values."""
    native_map = dict(native) if isinstance(native, FrozenDict) else dict(native or {})
    params_map = (
        {name: dict(params) for name, params in check_params.items()}
        if isinstance(check_params, FrozenDict)
        else dict(check_params or {})
    )
    recovery_map = (
        dict(recovery_params)
        if isinstance(recovery_params, FrozenDict)
        else dict(recovery_params or {})
    )
    return ExecutionDefinition(
        program=program,
        native=native_map,
        execution_adapter=execution_adapter or "standard",
        result_profile=result_profile or "standard",
        checks=tuple(checks),
        check_params=params_map,
        recovery=recovery or "none",
        recovery_params=recovery_map,
        resources=resources.to_dict() if hasattr(resources, "to_dict") else dict(resources or {}),
        charge=charge,
        multiplicity=multiplicity,
        freeze=tuple(freeze) if freeze is not None else None,
        step_semantic_digest=step_semantic_digest,
        contract_versions=dict(contract_versions),
    )


class LocalTransport:
    """In-process transport: direct delegation to a :class:`WorkItemExecutor`."""

    def __init__(self, executor: WorkItemExecutor) -> None:
        self._executor = executor

    @property
    def executor(self) -> WorkItemExecutor:
        """Return the wrapped executor."""
        return self._executor

    def execute(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        attempt: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute *item* locally; *attempt* is recorded for parity only."""
        del attempt
        return self._executor.execute(item, context, should_cancel=should_cancel)


class RemoteTransport:
    """File-based remote transport through worker-handoff V2.

    One transport owns one producer-side run binding plus one worker
    sandbox root.  Each :meth:`execute` builds a handoff envelope, stages
    the input bundle, runs the worker entry point, and imports the
    validated result bundle.  Repeated delivery of the same launch token
    returns the recorded result without relaunching native execution.
    """

    def __init__(
        self,
        *,
        run_root: str,
        store: SqliteWorkItemStore,
        worker_root: str,
        launch_token_prefix: str = "remote",
    ) -> None:
        if not run_root or not worker_root:
            raise DomainError("run_root and worker_root must be non-empty strings")
        self._run_root = os.path.abspath(run_root)
        self._worker_root = os.path.abspath(worker_root)
        self._store = store
        self._prefix = launch_token_prefix
        self._lock = threading.Lock()
        self._token_locks: dict[str, threading.Lock] = {}
        self._delivered: dict[str, WorkItemResult] = {}

    @property
    def run_root(self) -> str:
        """Return the producer-side run root."""
        return self._run_root

    @property
    def worker_root(self) -> str:
        """Return the worker sandbox root."""
        return self._worker_root

    def launch_token_for(self, item: WorkItem, attempt: int) -> str:
        """Return the deterministic launch token for one attempt.

        Colons (present in work-item ids) become ``+`` so the token is a
        safe single path segment everywhere, matching the lease marker
        convention; the mapping stays injective and stable.
        """
        raw = f"{self._prefix}:{item.id}:attempt-{int(attempt)}"
        return raw.replace(":", "+")

    def _token_lock(self, token: str) -> threading.Lock:
        """Return the per-token delivery lock (in-process duplicate gate)."""
        with self._lock:
            lock = self._token_locks.get(token)
            if lock is None:
                lock = threading.Lock()
                self._token_locks[token] = lock
            return lock

    @staticmethod
    def _result_path_for(worker_root: str, token: str) -> str:
        """Return the deterministic prior-result path for one token.

        Mirrors the worker packaging layout (``results/<token>/result.json``)
        so a producer that crashed after transfer but before commit can
        recover the bundle without relaunching native execution.
        """
        component = token.replace("/", "_").replace("\x00", "_").replace(os.sep, "_")
        return os.path.join(worker_root, "results", component or "attempt", "result.json")

    def execute(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        attempt: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WorkItemResult:
        """Execute one attempt through the remote worker and import it.

        Transport-stage failures (handoff, staging, worker, import) return
        typed failure results without native duplication; only programming
        errors propagate.  Repeated delivery of the same launch token
        returns the recorded result.
        """
        from ..persistence.contracts import CorruptStateError, PersistenceError
        from .handoff import HandoffError
        from .result_bundle import ResultBundleError
        from .staging import StagingError
        from .worker import WorkerError

        token = self.launch_token_for(item, attempt)
        with self._token_lock(token):
            with self._lock:
                recorded = self._delivered.get(token)
            if recorded is not None:
                return recorded
            try:
                recovered = self._recover_prior_result(item, token)
            except (StagingError, PersistenceError, CorruptStateError, DomainError) as exc:
                return self._stage_failure(item, context, token, exc)
            if recovered is not None:
                with self._lock:
                    self._delivered[token] = recovered
                return recovered
            try:
                return self._run_remote(
                    item, context, attempt=attempt, token=token, should_cancel=should_cancel
                )
            except (HandoffError, StagingError, WorkerError, ResultBundleError) as exc:
                return self._stage_failure(item, context, token, exc)

    def _recover_prior_result(self, item: WorkItem, token: str) -> WorkItemResult | None:
        """Import a prior result bundle for *token* without relaunching.

        Returns the imported result when a complete, identity-matching
        bundle survived a previous delivery (e.g. the producer crashed
        after transfer but before commit), else ``None``.  Unreadable or
        corrupt priors fall through to fresh delivery — packaging is
        atomic, so only disk faults land here.  Identity or checksum
        mismatches fail closed with :class:`StagingError` (never relaunch
        over a bundle that may belong to a live worker).
        """
        from .handoff import HandoffError, read_handoff_envelope
        from .result_bundle import ResultBundleError
        from .staging import StagingError, import_result_artifacts

        prior = self._result_path_for(self._worker_root, token)
        if not os.path.isfile(prior):
            return None
        try:
            handoff = read_handoff_envelope(
                path=os.path.join(self._worker_root, "inbox", token, "handoff.json"),
                expected_run_id=None,
            )
        except (OSError, HandoffError):
            return None
        if handoff.launch_token != token or handoff.work_item_id != item.id:
            return None
        try:
            imported = import_result_artifacts(
                result_path=prior,
                handoff=handoff,
                run_root=self._run_root,
                store=self._store,
            )
        except (OSError, ResultBundleError):
            return None
        if not isinstance(imported, WorkItemResult):
            raise StagingError("prior result import must return a WorkItemResult")
        return imported

    def _run_remote(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        attempt: int,
        token: str,
        should_cancel: Callable[[], bool] | None,
    ) -> WorkItemResult:
        """Run the handoff → stage → worker → import pipeline once."""
        from .handoff import read_handoff_envelope, write_handoff_envelope
        from .staging import import_result_artifacts, stage_input_bundle
        from .worker import run_worker_envelope

        source_files, checksums = self._artifact_sources(item)
        handoff = self._build_handoff(
            item, context, attempt=attempt, token=token, artifact_checksums=checksums
        )
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=self._worker_root, launch_token=token
        )
        # Re-read through the secure reader so producer and worker agree
        # byte-for-byte on the validated envelope.
        handoff = read_handoff_envelope(path=handoff_path, expected_run_id=handoff.run_id)
        source_files, _checksums = self._artifact_sources(item)
        staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=self._run_root,
            worker_root=self._worker_root,
            launch_token=token,
            source_files=source_files,
        )
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle=staged,
            worker_root=self._worker_root,
            launch_token=token,
            should_cancel=should_cancel,
        )
        imported = import_result_artifacts(
            result_path=result_path,
            handoff=handoff,
            run_root=self._run_root,
            store=self._store,
        )
        if not isinstance(imported, WorkItemResult):
            raise DomainError("result import must return a WorkItemResult")
        with self._lock:
            self._delivered[token] = imported
        return imported

    def _stage_failure(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        token: str,
        error: Exception,
    ) -> WorkItemResult:
        """Record a transport-stage failure without native duplication."""
        from .handoff import HandoffError
        from .result_bundle import ResultBundleError
        from .staging import StagingError
        from .worker import WorkerError

        stage = "transport"
        if isinstance(error, HandoffError):
            stage = "handoff"
        elif isinstance(error, StagingError):
            stage = "staging"
        elif isinstance(error, WorkerError):
            stage = "worker"
        elif isinstance(error, ResultBundleError):
            stage = "result_bundle"
        failed = transport_error_result(
            item,
            code=f"remote_{stage}_error",
            message=f"remote {stage} failed for launch {token}: {error}",
            step_id=context.step_id,
            details={"launch_token": token, "stage": stage, "error": str(error)},
        )
        with self._lock:
            self._delivered[token] = failed
        return failed

    def _artifact_sources(self, item: WorkItem) -> tuple[dict[str, str], dict[str, str]]:
        """Resolve producer artifact paths and content-derived checksums.

        Returns ``(source_files, checksums)`` mapping artifact id to the
        absolute producer path and to its checksum.  Refs without a
        checksum are hashed from producer bytes (fail closed when missing
        or unreadable); every path is containment-checked before staging
        re-validates it securely.
        """
        import hashlib

        from ..domain.artifact import LocatorKind

        sources: dict[str, str] = {}
        checksums: dict[str, str] = {}
        for port in sorted(item.named_inputs.artifacts):
            for ref in item.named_inputs.artifacts[port]:
                locator = ref.locator
                if locator.kind is not LocatorKind.RUN_RELATIVE or not locator.path:
                    raise DomainError(f"artifact {ref.id!r} has no transportable locator")
                candidate = os.path.realpath(os.path.join(self._run_root, locator.path))
                if os.path.commonpath([self._run_root, candidate]) != self._run_root:
                    raise DomainError(
                        f"artifact {ref.id!r} escapes the run root; refusing transport"
                    )
                if not os.path.isfile(candidate):
                    raise DomainError(
                        f"artifact {ref.id!r} is not materialized at {locator.path!r}"
                    )
                sources[ref.id] = candidate
                if ref.checksum:
                    checksums[ref.id] = ref.checksum
                else:
                    with open(candidate, "rb") as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                    checksums[ref.id] = f"sha256:{digest}"
        return sources, checksums

    def _build_handoff(
        self,
        item: WorkItem,
        context: ItemExecutionContext,
        *,
        attempt: int,
        token: str,
        artifact_checksums: dict[str, str] | None = None,
    ) -> WorkerHandoffV2:
        """Build the V2 envelope from resolved execution inputs."""
        from ..execution.work_item_executor import select_driving_structure
        from ..workflow.v4.scientific import resolve_scientific_parameters

        scientific = context.scientific
        defaults = context.scientific_defaults
        driving = select_driving_structure(item)
        effective, _ = resolve_scientific_parameters(
            structure=driving, overrides=scientific.overrides, defaults=defaults
        )
        manifest = build_input_bundle_manifest(item, artifact_checksums=artifact_checksums)
        environment_request = {"program": context.adapter.program_name.value}
        binding = context.execution_binding
        target = getattr(binding, "target", None)
        if target:
            environment_request["target"] = target
        check_versions = {check.name: check.contract_version for check in context.checks}
        recovery = context.recovery
        definition = build_execution_definition(
            program=context.adapter.program_name.value,
            native=scientific.native,
            execution_adapter=scientific.execution_adapter,
            result_profile=scientific.result_profile,
            checks=tuple(check.name for check in context.checks),
            check_params=scientific.check_params,
            recovery=getattr(recovery, "name", "none") or "none",
            recovery_params=scientific.recovery_params,
            resources=item.resources,
            charge=effective.charge,
            multiplicity=effective.multiplicity,
            freeze=effective.freeze,
            step_semantic_digest=item.semantic_digest,
            contract_versions={
                "adapter": context.adapter.adapter_version,
                "profile": context.profile.contract_version,
                **{f"check:{name}": version for name, version in check_versions.items()},
                "recovery": getattr(recovery, "contract_version", "none") or "none",
            },
        )
        return WorkerHandoffV2.new(
            run_id=os.path.basename(self._run_root.rstrip(os.sep)) or "run",
            step_id=context.step_id,
            work_item_id=item.id,
            logical_key=item.logical_key,
            attempt_number=int(attempt),
            launch_token=token,
            work_item_digest=item.semantic_digest,
            step_semantic_digest=item.semantic_digest,
            producer_provenance={},
            environment_request=environment_request,
            execution=definition,
            inputs=manifest,
        )

    def forget(self, item: WorkItem, attempt: int) -> None:
        """Drop the recorded delivery for one attempt (test support)."""
        with self._lock:
            self._delivered.pop(self.launch_token_for(item, attempt), None)


def transport_error_result(
    item: WorkItem,
    *,
    code: str,
    message: str,
    step_id: str,
    recovery_profile: str = "none",
    details: dict[str, Any] | None = None,
) -> WorkItemResult:
    """Build a failed transport-level result without native execution."""
    return WorkItemResult(
        work_item_id=item.id,
        status=WorkItemStatus.FAILED,
        structures=StructureSet(),
        results=ResultSet(),
        artifacts=ArtifactSet(),
        diagnostics=(
            Diagnostic(
                code=code,
                message=message,
                severity=DiagnosticSeverity.ERROR,
                step_id=step_id,
                work_item_id=item.id,
                logical_key=item.logical_key,
                details=FrozenDict(details or {}),
            ),
        ),
        timing=Timing(finished_at=time.time(), duration_seconds=0.0),
        error=ResultError(
            code=code,
            message=message,
            retryable=True,
            details=FrozenDict(details or {}),
        ),
        recovery=RecoveryInfo(profile=recovery_profile, attempted=False),
        semantic_digest=item.semantic_digest,
    )
