#!/usr/bin/env python3

"""Crash-consistent step-result publication for ConfFlow Workflow V4 (V4-3).

This module owns publication stages 5-6 of the frozen eight-stage protocol in
:mod:`confflow.domain.publication` (accepted results are assembled, then the
step result is atomically published; stage 7, the run-state transition, lives
in :mod:`confflow.persistence.run_state`).  It never invents its own commit
order and never imports the executor side: persistence depends only on
``confflow.domain`` plus the standard library.

Authority hierarchy (frozen)::

    WorkItemStore  = per-item execution truth (owned elsewhere)
    StepResult     = published semantic output truth (this module)
    RunState       = workflow / step lifecycle truth (``run_state.py``)

Durability contract
-------------------
Publishing serializes ``StepResult.to_dict()`` through canonical JSON bytes,
writes a temp file ``step_result.json.tmp.<pid>.<counter>`` in the same
directory, fsyncs the file and (where practical) the directory, then
``os.replace`` renames it over ``step_result.json``.  Loading is strict: any
unreadable or misshapen payload raises :class:`CorruptStateError` and never
yields a partial object.  ``*.tmp.*`` leftovers are listed and explicitly
skipped, never loaded.  Result-file exports (XYZ/CSV/JSON reports) are
projections and are never written here.
"""

from __future__ import annotations

import itertools
import json
import os
from collections.abc import Iterable
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet, LocatorKind
from ..domain.canonical import canonical_json_bytes, typed_digest
from ..domain.completion import (
    CompletionPolicy,
    StepStatus,
    WorkItemStatus,
    evaluate_step_status,
)
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.publication import verify_step_publication as _domain_verify_step_publication
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.retention import RetentionClass
from ..domain.step_result import StepProvenance, StepResult
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import QuantityKind, Unit
from ..domain.work_item import (
    RecoveryInfo,
    ResultError,
    Timing,
    WorkItemResult,
)
from .contracts import (
    STEP_RESULT_FILENAME,
    CorruptStateError,
    PersistenceError,
    step_result_path,
    validate_run_root,
)

__all__ = [
    "STEP_RESULT_DIGEST_KIND",
    "load_published_step_result",
    "publish_step_result",
    "rebuild_step_result",
    "verify_for_publication",
]

#: Digest domain marker for published step-result identity.
STEP_RESULT_DIGEST_KIND: Final[str] = "confflow.step_result.v1"

_TMP_COUNTER = itertools.count()


def _tmp_suffix() -> str:
    """Return a unique temp-file suffix for this process."""
    return f".tmp.{os.getpid()}.{next(_TMP_COUNTER)}"


def _atomic_write_bytes(target_path: str, payload: bytes) -> None:
    """Write *payload* to *target_path* atomically via temp file + rename.

    Parameters
    ----------
    target_path : str
        Final destination path; the temp file lives in the same directory.
    payload : bytes
        Exact bytes to durably persist.
    """
    directory = os.path.dirname(target_path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = target_path + _tmp_suffix()
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


def _tmp_prefix() -> str:
    """Return the temp-leftover filename prefix for step results."""
    return STEP_RESULT_FILENAME + ".tmp."


def publish_step_result(*, run_root: str, step_id: str, step_result: StepResult) -> str:
    """Atomically publish *step_result* for *step_id* under *run_root*.

    Parameters
    ----------
    run_root : str
        Managed persistence root owning the step directory.
    step_id : str
        Step this result belongs to; must equal ``step_result.step_id``.
    step_result : StepResult
        Assembled step result to publish.

    Returns
    -------
    str
        Typed digest ``sha256:<hex>`` of the published ``to_dict`` payload.

    Raises
    ------
    PersistenceError
        Raised when *step_result* is not a ``StepResult`` or its step id
        does not match *step_id*.
    """
    root = validate_run_root(run_root)
    if not isinstance(step_result, StepResult):
        raise PersistenceError("step_result must be a StepResult")
    if step_result.step_id != step_id:
        raise PersistenceError(f"step result belongs to {step_result.step_id!r}, not {step_id!r}")
    target = step_result_path(root, step_id)
    payload = step_result.to_dict()
    _atomic_write_bytes(target, canonical_json_bytes(payload))
    return typed_digest(STEP_RESULT_DIGEST_KIND, payload)


def _mapping(payload: Any, what: str) -> dict[str, Any]:
    """Return *payload* as a mapping or raise :class:`CorruptStateError`."""
    if not isinstance(payload, dict):
        raise CorruptStateError(f"{what} must be a mapping")
    return payload


def _mapping_or_empty(payload: Any, what: str) -> dict[str, Any]:
    """Return *payload* as a mapping, treating absence as empty."""
    if payload is None:
        return {}
    return _mapping(payload, what)


def _sequence(payload: Any, what: str) -> list[Any]:
    """Return *payload* as a list or raise :class:`CorruptStateError`."""
    if not isinstance(payload, list):
        raise CorruptStateError(f"{what} must be a list")
    return payload


def _structure_record(entry: Any) -> StructureRecord:
    """Rebuild one structure record, failing closed on any defect."""
    data = _mapping(entry, "structure record")
    try:
        return StructureRecord(
            id=data["id"],
            atoms=data["atoms"],
            coordinates=data["coordinates"],
            charge=data.get("charge"),
            multiplicity=data.get("multiplicity"),
            parent_ids=tuple(_sequence(data.get("parent_ids", []), "structure parent_ids")),
            lineage_root_id=data.get("lineage_root_id"),
            source_step_id=data.get("source_step_id"),
            source_work_item_id=data.get("source_work_item_id"),
            role=data.get("role"),
            ordinal=data.get("ordinal"),
            group_key=data.get("group_key"),
            metadata=_mapping_or_empty(data.get("metadata"), "structure metadata"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"structure record is invalid: {exc}") from exc


def _result_provenance(entry: Any) -> Provenance | None:
    """Rebuild result provenance, failing closed on any defect."""
    if entry is None:
        return None
    data = _mapping(entry, "result provenance")
    try:
        return Provenance(
            program=data.get("program"),
            program_version=data.get("program_version"),
            method=data.get("method"),
            basis=data.get("basis"),
            adapter=data.get("adapter"),
            step_id=data.get("step_id"),
            work_item_id=data.get("work_item_id"),
            metadata=_mapping_or_empty(data.get("metadata"), "result provenance metadata"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"result provenance is invalid: {exc}") from exc


def _scientific_result(entry: Any) -> ScientificResult:
    """Rebuild one scientific result, failing closed on any defect."""
    data = _mapping(entry, "scientific result")
    try:
        unit_value = data.get("unit")
        quantity_value = data.get("quantity")
        return ScientificResult(
            kind=data["kind"],
            value=data["value"],
            unit=Unit(unit_value) if unit_value is not None else None,
            quantity=QuantityKind(quantity_value) if quantity_value is not None else None,
            subject_structure_id=data.get("subject_structure_id"),
            source_step_id=data.get("source_step_id"),
            source_work_item_id=data.get("source_work_item_id"),
            provenance=_result_provenance(data.get("provenance")),
            metadata=_mapping_or_empty(data.get("metadata"), "scientific result metadata"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"scientific result is invalid: {exc}") from exc


def _artifact_locator(entry: Any) -> ArtifactLocator:
    """Rebuild one artifact locator, failing closed on any defect."""
    data = _mapping(entry, "artifact locator")
    try:
        return ArtifactLocator(
            kind=LocatorKind(data["kind"]),
            path=data.get("path"),
            uri=data.get("uri"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"artifact locator is invalid: {exc}") from exc


def _artifact_ref(entry: Any) -> ArtifactRef:
    """Rebuild one artifact reference, failing closed on any defect."""
    data = _mapping(entry, "artifact reference")
    try:
        return ArtifactRef(
            id=data["id"],
            role=data["role"],
            locator=_artifact_locator(data["locator"]),
            checksum=data.get("checksum"),
            media_type=data.get("media_type"),
            program=data.get("program"),
            producer_step_id=data.get("producer_step_id"),
            producer_work_item_id=data.get("producer_work_item_id"),
            subject_structure_id=data.get("subject_structure_id"),
            retention=RetentionClass(data["retention"]),
            metadata=_mapping_or_empty(data.get("metadata"), "artifact metadata"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"artifact reference is invalid: {exc}") from exc


def _diagnostic(entry: Any) -> Diagnostic:
    """Rebuild one diagnostic, failing closed on any defect."""
    data = _mapping(entry, "diagnostic")
    try:
        return Diagnostic(
            code=data["code"],
            message=data["message"],
            severity=DiagnosticSeverity(data["severity"]),
            step_id=data.get("step_id"),
            work_item_id=data.get("work_item_id"),
            logical_key=data.get("logical_key"),
            field_path=data.get("field_path"),
            details=_mapping_or_empty(data.get("details"), "diagnostic details"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"diagnostic is invalid: {exc}") from exc


def _timing(entry: Any) -> Timing | None:
    """Rebuild timing info, failing closed on any defect."""
    if entry is None:
        return None
    data = _mapping(entry, "timing")
    try:
        return Timing(
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            duration_seconds=data.get("duration_seconds"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"timing is invalid: {exc}") from exc


def _result_error(entry: Any) -> ResultError | None:
    """Rebuild failure info, failing closed on any defect."""
    if entry is None:
        return None
    data = _mapping(entry, "result error")
    try:
        return ResultError(
            code=data["code"],
            message=data["message"],
            retryable=data["retryable"],
            details=_mapping_or_empty(data.get("details"), "result error details"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"result error is invalid: {exc}") from exc


def _recovery_info(entry: Any) -> RecoveryInfo | None:
    """Rebuild recovery metadata, failing closed on any defect."""
    if entry is None:
        return None
    data = _mapping(entry, "recovery info")
    try:
        return RecoveryInfo(
            profile=data["profile"],
            attempted=data["attempted"],
            succeeded=data.get("succeeded"),
            details=_mapping_or_empty(data.get("details"), "recovery details"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"recovery info is invalid: {exc}") from exc


def _structure_set(entries: Any, what: str) -> StructureSet:
    """Rebuild a structure set, failing closed on any defect."""
    return StructureSet(tuple(_structure_record(entry) for entry in _sequence(entries, what)))


def _result_set(entries: Any, what: str) -> ResultSet:
    """Rebuild a result set, failing closed on any defect."""
    return ResultSet(tuple(_scientific_result(entry) for entry in _sequence(entries, what)))


def _artifact_set(entries: Any, what: str) -> ArtifactSet:
    """Rebuild an artifact set, failing closed on any defect."""
    return ArtifactSet(tuple(_artifact_ref(entry) for entry in _sequence(entries, what)))


def _work_item_result(entry: Any) -> WorkItemResult:
    """Rebuild one work-item result, failing closed on any defect."""
    data = _mapping(entry, "work item result")
    try:
        status = WorkItemStatus(data["status"])
        structures = _structure_set(data["structures"], "work item structures")
        results = _result_set(data["results"], "work item results")
        artifacts = _artifact_set(data["artifacts"], "work item artifacts")
        diagnostics = tuple(
            _diagnostic(item) for item in _sequence(data["diagnostics"], "work item diagnostics")
        )
        timing = _timing(data.get("timing"))
        error = _result_error(data.get("error"))
        recovery = _recovery_info(data.get("recovery"))
        return WorkItemResult(
            work_item_id=data["work_item_id"],
            status=status,
            structures=structures,
            results=results,
            artifacts=artifacts,
            diagnostics=diagnostics,
            timing=timing,
            error=error,
            recovery=recovery,
            semantic_digest=data.get("semantic_digest"),
            metadata=_mapping_or_empty(data.get("metadata"), "work item metadata"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"work item result is invalid: {exc}") from exc


def _step_provenance(entry: Any) -> StepProvenance | None:
    """Rebuild step provenance, failing closed on any defect."""
    if entry is None:
        return None
    data = _mapping(entry, "step provenance")
    try:
        return StepProvenance(
            workflow_definition_digest=data.get("workflow_definition_digest"),
            step_semantic_digest=data.get("step_semantic_digest"),
            compiler_version=data.get("compiler_version"),
            metadata=_mapping_or_empty(data.get("metadata"), "step provenance metadata"),
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"step provenance is invalid: {exc}") from exc


def load_published_step_result(*, run_root: str, step_id: str) -> StepResult | None:
    """Load the published step result for *step_id*, or ``None`` when absent.

    ``*.tmp.*`` leftovers in the step directory are listed and explicitly
    skipped: only the exact ``step_result.json`` path is ever loaded.

    Parameters
    ----------
    run_root : str
        Managed persistence root owning the step directory.
    step_id : str
        Step whose published result should be loaded.

    Returns
    -------
    StepResult | None
        The rebuilt step result, or ``None`` when no publication exists.

    Raises
    ------
    CorruptStateError
        Raised when the file exists but is unreadable, is not valid JSON,
        has the wrong shape, carries a mismatched step id, or violates any
        domain invariant.  Corrupt data is never returned partially.
    """
    target = step_result_path(validate_run_root(run_root), step_id)
    directory = os.path.dirname(target)
    try:
        for entry in os.listdir(directory):
            if entry.startswith(_tmp_prefix()):
                continue  # crash leftover: listed explicitly, never a load candidate
    except OSError:
        pass
    try:
        with open(target, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CorruptStateError(f"cannot read published step result: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CorruptStateError(f"published step result is not valid JSON: {exc}") from exc
    data = _mapping(payload, "published step result")
    if data.get("step_id") != step_id:
        raise CorruptStateError(
            f"published step result belongs to {data.get('step_id')!r}, not {step_id!r}"
        )
    try:
        status = StepStatus(data["status"])
        structures = _structure_set(data["structures"], "step structures")
        results = _result_set(data["results"], "step results")
        artifacts = _artifact_set(data["artifacts"], "step artifacts")
        item_results = tuple(
            _work_item_result(entry)
            for entry in _sequence(data["item_results"], "step item results")
        )
        diagnostics = tuple(
            _diagnostic(entry) for entry in _sequence(data["diagnostics"], "step diagnostics")
        )
        summary = FrozenDict(_mapping_or_empty(data.get("summary"), "step summary"))
        provenance = _step_provenance(data.get("provenance"))
        return StepResult(
            step_id=data["step_id"],
            status=status,
            structures=structures,
            results=results,
            artifacts=artifacts,
            item_results=item_results,
            diagnostics=diagnostics,
            summary=summary,
            provenance=provenance,
        )
    except CorruptStateError:
        raise
    except (KeyError, TypeError, ValueError, DomainError) as exc:
        raise CorruptStateError(f"published step result is invalid: {exc}") from exc


def rebuild_step_result(
    *,
    step_id: str,
    items: tuple[WorkItemResult, ...],
    completion: CompletionPolicy,
    definition_digest: str | None = None,
    step_semantic_digest: str | None = None,
) -> StepResult:
    """Assemble a publishable step result from accepted work-item results.

    Mirrors the execution batch assembly exactly: the contributing items are
    visited in deterministic ``work_item_id`` order (never insertion or
    filesystem order), only the completed subset feeds the aggregated
    structures/results/artifacts, the summary carries total/completed/failed/
    cancelled counts plus the completion mode and status string, and the
    status comes from :func:`evaluate_step_status`.  All supplied items are
    retained in ``item_results`` (a failed item is never dropped), which is
    what keeps ``PARTIAL``/``FAILED`` results valid.  Rebuilding never
    verifies durability; use :func:`verify_for_publication` for that gate.

    Parameters
    ----------
    step_id : str
        Step this result belongs to.
    items : tuple[WorkItemResult, ...]
        Observed work-item results, in any order.
    completion : CompletionPolicy
        Acceptance policy deciding the step status.
    definition_digest : str | None
        Workflow definition digest recorded as provenance.
    step_semantic_digest : str | None
        Step semantic digest recorded as provenance.

    Returns
    -------
    StepResult
        Assembled result ready for :func:`publish_step_result`.

    Raises
    ------
    PersistenceError
        Raised when *items* members are not ``WorkItemResult`` or
        *completion* is not a ``CompletionPolicy``.
    """
    collected = tuple(items)
    for item in collected:
        if not isinstance(item, WorkItemResult):
            raise PersistenceError("items members must be WorkItemResult")
    if not isinstance(completion, CompletionPolicy):
        raise PersistenceError("completion must be a CompletionPolicy")
    ordered = tuple(sorted(collected, key=lambda item: item.work_item_id))
    status = evaluate_step_status(completion, tuple(item.status for item in ordered))
    accepted = tuple(item for item in ordered if item.is_completed)
    structures = StructureSet()
    results = ResultSet()
    artifacts = ArtifactSet()
    for item in accepted:
        structures = structures + item.structures
        results = results + item.results
        artifacts = artifacts + item.artifacts
    summary = FrozenDict(
        {
            "total": len(ordered),
            "completed": sum(1 for item in ordered if item.is_completed),
            "failed": sum(1 for item in ordered if item.status is WorkItemStatus.FAILED),
            "cancelled": sum(1 for item in ordered if item.status is WorkItemStatus.CANCELLED),
            "completion_mode": completion.mode.value,
            "status": status.value,
        }
    )
    return StepResult(
        step_id=step_id,
        status=status,
        structures=structures,
        results=results,
        artifacts=artifacts,
        item_results=ordered,
        diagnostics=(),
        summary=summary,
        provenance=StepProvenance(
            workflow_definition_digest=definition_digest,
            step_semantic_digest=step_semantic_digest,
        ),
    )


def verify_for_publication(
    step_result: StepResult,
    *,
    durable_item_ids: Iterable[str],
    verified_artifact_checksums: Iterable[str],
) -> None:
    """Verify *step_result* may be published durably.

    Thin wiring-layer wrapper around the domain durability gate
    (:func:`confflow.domain.publication.verify_step_publication`): every
    backing work-item result must be durable and every published artifact must
    carry a verified checksum.

    Parameters
    ----------
    step_result : StepResult
        Result about to be published.
    durable_item_ids : Iterable[str]
        Work-item ids whose results are durably persisted.
    verified_artifact_checksums : Iterable[str]
        Checksums verified against written bytes.

    Raises
    ------
    PublicationError
        Raised when any durability requirement is unmet.
    """
    _domain_verify_step_publication(
        step_result,
        durable_item_ids=durable_item_ids,
        verified_artifact_checksums=verified_artifact_checksums,
    )
