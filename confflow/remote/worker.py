#!/usr/bin/env python3

"""V4 remote worker: run one handoff through the shared executor (V4-4).

The worker is a delivery shell around the exact local science path.  Given
a validated worker-handoff V2 envelope plus staged input bytes, it rebuilds
the driving structure, bound artifact, and result inputs, resolves the
program adapter, result profile, scientific checks, and recovery policy
named by the compiled execution definition, and calls the same
:class:`WorkItemExecutor` with the same implementation classes local
execution uses.  Produced files are checksummed and packaged into a typed
result bundle for producer-side import.

The worker never touches the producer store, never publishes step results,
and never transitions run state.  Cancellation flows through the shared
executor unchanged: a confirmed stop yields a ``CANCELLED`` result, and an
unconfirmed stop never triggers rescue.

Import discipline: module load is the frozen envelope, the domain layer,
and the standard library only.  Executor-family resolvers (handoff reader,
program registry, profiles, checks, recovery, process boundary, executor,
scientific definition, result packaging) are imported inside functions so
importing this module never pulls a runtime stack.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.resources import ResourceRequest
from ..domain.result import Provenance, ResultSet, ScientificResult
from ..domain.structure import StructureRecord, StructureSet
from ..domain.units import QuantityKind, Unit
from ..domain.work_item import WorkItem, WorkItemInputs
from .envelope import (
    ArtifactBundleEntry,
    ExecutionDefinition,
    ResultEntry,
    StructureBundleEntry,
    WorkerHandoffV2,
    bundle_entry_digest,
)

if TYPE_CHECKING:
    from ..execution.native import ProcessSupervisor

__all__ = [
    "WorkerError",
    "run_worker_envelope",
]

_T = TypeVar("_T")

#: Placeholder checksum the transport writes when an input artifact carries
#: no verified checksum; the rebuilt reference stays unverified, as local.
_UNVERIFIED_CHECKSUM: str = "sha256:" + "0" * 64

#: Constructor fields accepted when rebuilding a structure from a bundle entry.
_STRUCTURE_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "atoms",
        "coordinates",
        "charge",
        "multiplicity",
        "parent_ids",
        "lineage_root_id",
        "source_step_id",
        "source_work_item_id",
        "role",
        "ordinal",
        "group_key",
        "metadata",
    }
)

#: Constructor fields accepted when rebuilding a result from a bundle entry.
_RESULT_FIELDS: frozenset[str] = frozenset(
    {
        "kind",
        "value",
        "unit",
        "quantity",
        "subject_structure_id",
        "source_step_id",
        "source_work_item_id",
        "provenance",
        "metadata",
    }
)

#: Constructor fields accepted when rebuilding result provenance.
_PROVENANCE_FIELDS: frozenset[str] = frozenset(
    {
        "program",
        "program_version",
        "method",
        "basis",
        "adapter",
        "step_id",
        "work_item_id",
        "metadata",
    }
)

#: Exact resource keys carried by the compiled execution definition.
_RESOURCE_FIELDS: frozenset[str] = frozenset({"cores_per_item", "memory_per_item_bytes"})

#: Port receiving rebuilt result inputs; the standard executor never reads
#: result inputs, so this port is a stable carrier, never a semantic claim.
_RESULTS_PORT: str = "results"

#: Mapping-style attributes probed on opaque staged-bundle objects.
_STAGED_MAPPING_ATTRS: tuple[str, ...] = (
    "artifact_files",
    "staged_files",
    "files",
    "mapping",
    "paths",
    "by_id",
    "artifacts",
)

#: Directory-style attributes probed on opaque staged-bundle objects.
_STAGED_DIR_ATTRS: tuple[str, ...] = (
    "work_dir",
    "root",
    "directory",
    "staging_dir",
    "staging_root",
    "base_dir",
    "path",
)


class WorkerError(ValueError):
    """Report a remote-worker failure with stage context."""


def run_worker_envelope(
    *,
    handoff_path: str,
    staged_bundle: Any,
    worker_root: str,
    launch_token: str,
    should_cancel: Callable[[], bool] | None = None,
    supervisor: ProcessSupervisor | None = None,
) -> str:
    """Run one worker-handoff envelope through the shared executor.

    Parameters
    ----------
    handoff_path : str
        Path of the worker-handoff V2 envelope file.
    staged_bundle : Any
        Staged input bundle: a mapping of artifact id to staged file path,
        a directory holding the staged ``bundle_locator`` files, or an
        object exposing one of those shapes.
    worker_root : str
        Worker sandbox root; owns the work, staging, and result directories.
    launch_token : str
        Launch token this attempt must carry; a mismatch fails closed.
    should_cancel : Callable[[], bool] | None
        Cancellation probe forwarded unchanged to the shared executor.
    supervisor : ProcessSupervisor | None
        Process boundary for native execution, or ``None`` for a fresh
        native supervisor.

    Returns
    -------
    str
        Path of the packaged ``result.json`` bundle.

    Raises
    ------
    WorkerError
        Raised with stage context when any worker stage fails.  Scientific
        failures and confirmed cancellations are executor results, packaged
        and returned normally rather than raised.
    """
    if not isinstance(handoff_path, str) or not handoff_path:
        raise WorkerError("remote worker failed at stage 'validate request': handoff_path")
    if not isinstance(worker_root, str) or not worker_root:
        raise WorkerError("remote worker failed at stage 'validate request': worker_root")
    if not isinstance(launch_token, str) or not launch_token:
        raise WorkerError("remote worker failed at stage 'validate request': launch_token")
    worker_root_abs = os.path.abspath(worker_root)

    handoff = _run_stage(
        "read handoff envelope", _load_handoff_envelope, handoff_path, launch_token
    )
    item = _run_stage(
        "rebuild work item inputs",
        _rebuild_work_item,
        handoff,
        staged_bundle,
        worker_root_abs,
    )
    context, execution = _run_stage(
        "resolve execution contracts",
        _resolve_execution_context,
        handoff,
        worker_root_abs,
        launch_token,
        supervisor,
    )
    result = _run_stage(
        "execute work item",
        _execute_work_item,
        item,
        context,
        should_cancel,
    )
    return _run_stage(
        "package result bundle",
        _package_work_item_result,
        result,
        handoff,
        execution,
        context,
        item,
        worker_root_abs,
    )


def _run_stage(name: str, func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Call one worker stage, wrapping unexpected failures with context.

    Parameters
    ----------
    name : str
        Stage name carried into the error message.
    func : Callable[..., Any]
        Stage implementation raising :class:`WorkerError` on known failures.
    args : Any
        Positional stage arguments.
    kwargs : Any
        Keyword stage arguments.

    Returns
    -------
    Any
        The stage value.

    Raises
    ------
    WorkerError
        Raised with stage context when the stage raises anything other
        than an already-contextualized :class:`WorkerError`.
    """
    try:
        return func(*args, **kwargs)
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError(f"remote worker failed at stage {name!r}: {exc}") from exc


def _load_handoff_envelope(handoff_path: str, launch_token: str) -> WorkerHandoffV2:
    """Read, validate, and token-bind one handoff envelope.

    Parameters
    ----------
    handoff_path : str
        Envelope file path handed to the worker.
    launch_token : str
        Expected launch token of this attempt.

    Returns
    -------
    WorkerHandoffV2
        The validated envelope bound to *launch_token*.

    Raises
    ------
    WorkerError
        Raised when the envelope cannot be read, fails validation, or
        carries a different launch token.
    """
    from .handoff import read_handoff_envelope

    reader_params = inspect.signature(read_handoff_envelope).parameters
    if "path" in reader_params:
        if "expected_run_id" in reader_params:
            if reader_params["expected_run_id"].default is inspect.Parameter.empty:
                raise WorkerError(
                    "remote worker failed at stage 'read handoff envelope': "
                    "reader requires an expected run id the worker cannot know"
                )
            handoff = read_handoff_envelope(path=handoff_path, expected_run_id=None)
        else:
            handoff = read_handoff_envelope(path=handoff_path)
    else:
        handoff = read_handoff_envelope(handoff_path)
    if not isinstance(handoff, WorkerHandoffV2):
        raise WorkerError(
            "remote worker failed at stage 'read handoff envelope': "
            f"reader returned {type(handoff).__name__}, not a V2 envelope"
        )
    if handoff.launch_token != launch_token:
        raise WorkerError(
            "remote worker failed at stage 'read handoff envelope': "
            "launch token mismatch; refusing to run another attempt's work"
        )
    return handoff


def _rebuild_work_item(handoff: WorkerHandoffV2, staged_bundle: Any, worker_root: str) -> WorkItem:
    """Rebuild the driving work item from handoff entries plus staged bytes.

    Parameters
    ----------
    handoff : WorkerHandoffV2
        Validated envelope whose input manifest drives the rebuild.
    staged_bundle : Any
        Staged input bundle locating artifact bytes under *worker_root*.
    worker_root : str
        Absolute worker sandbox root owning the staged bytes.

    Returns
    -------
    WorkItem
        The work item the shared executor must run.

    Raises
    ------
    WorkerError
        Raised when entries fail digest checks, payloads are not strictly
        rebuildable, staged bytes are missing, or the item is incoherent.
    """
    from ..execution.work_item_executor import DRIVING_STRUCTURE_PORT

    structures: list[StructureRecord] = []
    artifact_entries: list[ArtifactBundleEntry] = []
    result_entries: list[ResultEntry] = []
    for entry in handoff.inputs.entries:
        if isinstance(entry, StructureBundleEntry):
            structures.append(_structure_from_entry(entry))
        elif isinstance(entry, ArtifactBundleEntry):
            artifact_entries.append(entry)
        elif isinstance(entry, ResultEntry):
            result_entries.append(entry)
        else:  # pragma: no cover - pydantic union parsing guards the type
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"unknown bundle entry {type(entry).__name__}"
            )
    if len(structures) != 1:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"expected exactly one driving structure entry, found {len(structures)}"
        )
    artifacts = _artifacts_from_entries(
        artifact_entries, staged_bundle=staged_bundle, worker_root=worker_root
    )
    results = ResultSet(tuple(_result_from_entry(entry) for entry in result_entries))
    resources = _resources_from_definition(handoff.execution)
    structure_inputs = FrozenDict({DRIVING_STRUCTURE_PORT: StructureSet.of(structures[0])})
    artifact_inputs = FrozenDict(
        {role: ArtifactSet(tuple(references)) for role, references in sorted(artifacts.items())}
    )
    result_inputs = FrozenDict({_RESULTS_PORT: results}) if len(results) else FrozenDict()
    try:
        return WorkItem(
            id=handoff.work_item_id,
            logical_key=handoff.logical_key,
            step_id=handoff.step_id,
            named_inputs=WorkItemInputs(
                structures=structure_inputs,
                artifacts=artifact_inputs,
                results=result_inputs,
            ),
            resources=resources,
            semantic_digest=handoff.work_item_digest,
        )
    except Exception as exc:
        raise WorkerError(
            f"remote worker failed at stage 'rebuild work item inputs': {exc}"
        ) from exc


def _structure_from_entry(entry: StructureBundleEntry) -> StructureRecord:
    """Rebuild one structure record from its authenticated payload.

    Parameters
    ----------
    entry : StructureBundleEntry
        Structure bundle entry carrying the canonical record payload.

    Returns
    -------
    StructureRecord
        The rebuilt driving structure.

    Raises
    ------
    WorkerError
        Raised when the entry digest does not recompute or the payload is
        not a strict structure record.
    """
    payload = dict(entry.payload)
    if bundle_entry_digest("structure", payload) != entry.digest:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"structure entry {entry.structure_id!r} fails its content digest"
        )
    payload.pop("geometry_digest", None)
    unknown = sorted(set(payload) - _STRUCTURE_FIELDS)
    if unknown:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"structure entry {entry.structure_id!r} carries unknown fields {unknown}"
        )
    try:
        return StructureRecord(**payload)
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"structure entry {entry.structure_id!r} is not rebuildable: {exc}"
        ) from exc


def _result_from_entry(entry: ResultEntry) -> ScientificResult:
    """Rebuild one scientific result from its authenticated payload.

    Parameters
    ----------
    entry : ResultEntry
        Result bundle entry carrying the typed result payload.

    Returns
    -------
    ScientificResult
        The rebuilt scientific result.

    Raises
    ------
    WorkerError
        Raised when the entry digest does not recompute or the payload is
        not a strict scientific result.
    """
    payload = dict(entry.payload)
    if bundle_entry_digest("result", payload) != entry.digest:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"result entry {entry.result_id!r} fails its content digest"
        )
    payload.pop("value_digest", None)
    unknown = sorted(set(payload) - _RESULT_FIELDS)
    if unknown:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"result entry {entry.result_id!r} carries unknown fields {unknown}"
        )
    unit = payload.get("unit")
    try:
        payload["unit"] = Unit(unit) if unit is not None else None
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"result entry {entry.result_id!r} carries an unknown unit: {exc}"
        ) from exc
    quantity = payload.get("quantity")
    try:
        payload["quantity"] = QuantityKind(quantity) if quantity is not None else None
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"result entry {entry.result_id!r} carries an unknown quantity: {exc}"
        ) from exc
    provenance = payload.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, Mapping):
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"result entry {entry.result_id!r} carries malformed provenance"
            )
        provenance_map = dict(provenance)
        unknown_provenance = sorted(set(provenance_map) - _PROVENANCE_FIELDS)
        if unknown_provenance:
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"result entry {entry.result_id!r} carries unknown provenance "
                f"fields {unknown_provenance}"
            )
        try:
            payload["provenance"] = Provenance(**provenance_map)
        except Exception as exc:
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"result entry {entry.result_id!r} carries invalid provenance: {exc}"
            ) from exc
    try:
        return ScientificResult(**payload)
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"result entry {entry.result_id!r} is not rebuildable: {exc}"
        ) from exc


def _artifacts_from_entries(
    entries: list[ArtifactBundleEntry], *, staged_bundle: Any, worker_root: str
) -> dict[str, list[ArtifactRef]]:
    """Rebuild artifact references grouped by role from staged bytes.

    Parameters
    ----------
    entries : list[ArtifactBundleEntry]
        Artifact bundle entries of the input manifest.
    staged_bundle : Any
        Staged input bundle locating artifact bytes under *worker_root*.
    worker_root : str
        Absolute worker sandbox root owning the staged bytes.

    Returns
    -------
    dict[str, list[ArtifactRef]]
        Artifact references keyed by role.

    Raises
    ------
    WorkerError
        Raised when staged bytes are missing, escape the worker root, or a
        reference is not rebuildable.
    """
    grouped: dict[str, list[ArtifactRef]] = {}
    for entry in sorted(entries, key=lambda item: item.artifact_id):
        staged_path = _resolve_staged_file(
            staged_bundle=staged_bundle,
            artifact_id=entry.artifact_id,
            bundle_locator=entry.bundle_locator,
            worker_root=worker_root,
        )
        real_root = os.path.realpath(worker_root)
        real_path = os.path.realpath(staged_path)
        relative = os.path.relpath(real_path, real_root)
        if relative == ".." or relative.startswith(f"..{os.sep}") or os.path.isabs(relative):
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"staged file for artifact {entry.artifact_id!r} escapes the worker root"
            )
        checksum = entry.checksum
        if checksum == _UNVERIFIED_CHECKSUM:
            checksum = None
        try:
            reference = ArtifactRef(
                id=entry.artifact_id,
                role=entry.role,
                locator=ArtifactLocator.run_relative(relative.replace(os.sep, "/")),
                checksum=checksum,
                media_type=entry.media_type,
                subject_structure_id=entry.subject_structure_id,
            )
        except Exception as exc:
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"artifact entry {entry.artifact_id!r} is not rebuildable: {exc}"
            ) from exc
        grouped.setdefault(entry.role, []).append(reference)
    return grouped


def _resolve_staged_file(
    *, staged_bundle: Any, artifact_id: str, bundle_locator: str, worker_root: str
) -> str:
    """Resolve the worker-local staged file for one artifact entry.

    Parameters
    ----------
    staged_bundle : Any
        Mapping of artifact id to staged path, a directory holding the
        staged ``bundle_locator`` files, or an object exposing either shape.
    artifact_id : str
        Bundle identity of the wanted artifact.
    bundle_locator : str
        Deterministic locator of the artifact inside a staged directory.
    worker_root : str
        Absolute worker sandbox root used for relative staged paths.

    Returns
    -------
    str
        Absolute path of an existing staged file.

    Raises
    ------
    WorkerError
        Raised when the bundle shape is unsupported or the staged file is
        missing.
    """
    if staged_bundle is None:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"no staged bundle for artifact {artifact_id!r}"
        )
    if isinstance(staged_bundle, Mapping):
        candidate = _lookup_mapping(staged_bundle, artifact_id, bundle_locator)
    elif isinstance(staged_bundle, (str, os.PathLike)):
        candidate = _lookup_directory(staged_bundle, artifact_id, bundle_locator)
    else:
        candidate = _lookup_opaque(staged_bundle, artifact_id, bundle_locator)
    if not isinstance(candidate, (str, os.PathLike)):
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"staged path for artifact {artifact_id!r} is not a path"
        )
    text = os.fspath(candidate)
    absolute = text if os.path.isabs(text) else os.path.join(worker_root, text)
    if not os.path.isfile(absolute):
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"staged file for artifact {artifact_id!r} is missing"
        )
    return absolute


def _lookup_mapping(staged_bundle: Mapping[str, Any], artifact_id: str, bundle_locator: str) -> Any:
    """Look up one artifact in a mapping-style staged bundle.

    Parameters
    ----------
    staged_bundle : Mapping[str, Any]
        Artifact id (or bundle locator) to staged path mapping.
    artifact_id : str
        Bundle identity of the wanted artifact.
    bundle_locator : str
        Fallback locator key inside a staged directory layout.

    Returns
    -------
    Any
        The staged path value.

    Raises
    ------
    WorkerError
        Raised when neither key is present in the mapping.
    """
    if artifact_id in staged_bundle:
        return staged_bundle[artifact_id]
    if bundle_locator in staged_bundle:
        return staged_bundle[bundle_locator]
    raise WorkerError(
        "remote worker failed at stage 'rebuild work item inputs': "
        f"staged bundle has no file for artifact {artifact_id!r}"
    )


def _lookup_directory(
    staged_bundle: str | os.PathLike[str], artifact_id: str, bundle_locator: str
) -> Any:
    """Resolve one artifact inside a directory-style staged bundle.

    Parameters
    ----------
    staged_bundle : str | os.PathLike[str]
        Directory holding the staged ``bundle_locator`` files.
    artifact_id : str
        Bundle identity of the wanted artifact (error context only).
    bundle_locator : str
        Deterministic locator of the artifact inside the directory.

    Returns
    -------
    Any
        The staged file path.

    Raises
    ------
    WorkerError
        Raised when the locator escapes the directory or is missing.
    """
    root = os.fspath(staged_bundle)
    if not bundle_locator or bundle_locator.startswith("/") or ".." in bundle_locator.split("/"):
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"artifact {artifact_id!r} carries an unsafe bundle locator"
        )
    candidate = os.path.join(root, bundle_locator)
    if not os.path.isfile(candidate):
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"staged file for artifact {artifact_id!r} is missing"
        )
    return candidate


def _lookup_opaque(staged_bundle: Any, artifact_id: str, bundle_locator: str) -> Any:
    """Resolve one artifact from an opaque staged-bundle object.

    Parameters
    ----------
    staged_bundle : Any
        Object exposing a mapping-style attribute, a directory-style
        attribute, or a ``get`` accessor.
    artifact_id : str
        Bundle identity of the wanted artifact.
    bundle_locator : str
        Deterministic locator of the artifact inside a staged directory.

    Returns
    -------
    Any
        The staged file path.

    Raises
    ------
    WorkerError
        Raised when the object exposes no supported staging shape.
    """
    for attribute in _STAGED_MAPPING_ATTRS:
        value = getattr(staged_bundle, attribute, None)
        if isinstance(value, Mapping):
            return _lookup_mapping(value, artifact_id, bundle_locator)
        if isinstance(value, (str, os.PathLike)):
            return _lookup_directory(value, artifact_id, bundle_locator)
    for attribute in _STAGED_DIR_ATTRS:
        value = getattr(staged_bundle, attribute, None)
        if isinstance(value, Mapping):
            return _lookup_mapping(value, artifact_id, bundle_locator)
        if isinstance(value, (str, os.PathLike)):
            return _lookup_directory(value, artifact_id, bundle_locator)
    accessor = getattr(staged_bundle, "get", None)
    if callable(accessor):
        try:
            candidate = accessor(artifact_id)
        except Exception as exc:
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"staged bundle accessor failed for artifact {artifact_id!r}: {exc}"
            ) from exc
        if candidate is None:
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"staged bundle has no file for artifact {artifact_id!r}"
            )
        return candidate
    raise WorkerError(
        "remote worker failed at stage 'rebuild work item inputs': "
        "staged bundle exposes no artifact mapping or staging directory"
    )


def _resources_from_definition(execution: ExecutionDefinition) -> ResourceRequest:
    """Build the resolved resource request from the execution definition.

    Parameters
    ----------
    execution : ExecutionDefinition
        Compiled execution semantics carrying the resolved resources.

    Returns
    -------
    ResourceRequest
        The per-item resources, already resolved by the producer.

    Raises
    ------
    WorkerError
        Raised when the resources are missing, drifted, or unresolved.
    """
    resources = execution.resources
    if not isinstance(resources, Mapping):
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            "execution resources must be a mapping"
        )
    unknown = sorted(set(resources) - _RESOURCE_FIELDS)
    if unknown:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"execution resources carry unknown fields {unknown}"
        )
    try:
        return ResourceRequest(
            cores_per_item=resources.get("cores_per_item"),
            memory_per_item_bytes=resources.get("memory_per_item_bytes"),
        )
    except Exception as exc:
        raise WorkerError(
            f"remote worker failed at stage 'rebuild work item inputs': {exc}"
        ) from exc


def _resolve_execution_context(
    handoff: WorkerHandoffV2,
    worker_root: str,
    launch_token: str,
    supervisor: ProcessSupervisor | None,
) -> Any:
    """Resolve the executor context from the compiled execution definition.

    Parameters
    ----------
    handoff : WorkerHandoffV2
        Validated envelope carrying the compiled execution semantics.
    worker_root : str
        Absolute worker sandbox root owning work directories.
    launch_token : str
        Launch token scoping the worker-local work directory.
    supervisor : ProcessSupervisor | None
        Caller-provided process boundary, or ``None`` for a fresh native
        supervisor.

    Returns
    -------
    Any
        The ``(ItemExecutionContext, ExecutionDefinition)`` pair the shared
        executor must run.

    Raises
    ------
    WorkerError
        Raised when any named implementation is unknown, contract versions
        drift, or the context is not constructible.
    """
    from confflow.execution.contracts import ExecutionBinding
    from confflow.execution.work_item_executor import ItemExecutionContext
    from confflow.programs.registry import get_program_adapter

    execution = handoff.execution
    versions = dict(execution.contract_versions)
    try:
        adapter = get_program_adapter(execution.program)
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown program {execution.program!r}: {exc}"
        ) from exc
    _require_contract_version(versions, "adapter", adapter.adapter_version, "program adapter")
    profile = _resolve_profile(execution.result_profile, versions)
    checks = _resolve_checks(execution.checks, versions)
    recovery = _resolve_recovery(execution.recovery, versions, adapter)
    scientific, defaults = _build_scientific(execution)
    walltime = handoff.environment_request.walltime_seconds
    try:
        binding = ExecutionBinding(
            binding_id=launch_token,
            executable=None,
            env=FrozenDict({}),
            walltime_seconds=walltime,
            target=handoff.environment_request.target,
        )
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"execution binding is not constructible: {exc}"
        ) from exc
    if supervisor is None:
        from confflow.execution.process import NativeProcessSupervisor

        supervisor = NativeProcessSupervisor()
    try:
        context = ItemExecutionContext(
            step_id=handoff.step_id,
            scientific=scientific,
            scientific_defaults=defaults,
            adapter=adapter,
            profile=profile,
            checks=checks,
            recovery=recovery,
            execution_binding=binding,
            run_root=worker_root,
            work_base=os.path.join(worker_root, "work", _safe_token_component(launch_token)),
            supervisor=supervisor,
            environment=None,
        )
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"execution context is not constructible: {exc}"
        ) from exc
    return context, execution


def _require_contract_version(versions: dict[str, str], key: str, actual: str, what: str) -> None:
    """Fail closed when a resolved contract version drifts from the handoff.

    Parameters
    ----------
    versions : dict[str, str]
        Contract versions recorded by the producer.
    key : str
        Version key of the resolved implementation.
    actual : str
        Contract version the worker resolved.
    what : str
        Human-readable implementation kind for error messages.

    Raises
    ------
    WorkerError
        Raised when the recorded version is present and differs.
    """
    expected = versions.get(key)
    if expected is not None and expected != actual:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"{what} contract version drift: handoff pins {expected!r}, "
            f"worker provides {actual!r}"
        )


def _resolve_profile(result_profile: str, versions: dict[str, str]) -> Any:
    """Resolve the result profile named by the execution definition.

    Parameters
    ----------
    result_profile : str
        Profile name recorded by the producer.
    versions : dict[str, str]
        Contract versions recorded by the producer.

    Returns
    -------
    Any
        The result profile implementation.

    Raises
    ------
    WorkerError
        Raised when the profile is unknown or its contract drifts.
    """
    from confflow.execution.profile_standard import PROFILES

    try:
        profile = PROFILES[result_profile]
    except KeyError as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown result profile {result_profile!r}"
        ) from exc
    _require_contract_version(versions, "profile", profile.contract_version, "result profile")
    return profile


def _resolve_checks(checks: tuple[str, ...], versions: dict[str, str]) -> tuple[Any, ...]:
    """Resolve the scientific checks named by the execution definition.

    Parameters
    ----------
    checks : tuple[str, ...]
        Check names recorded by the producer, in declared order.
    versions : dict[str, str]
        Contract versions recorded by the producer.

    Returns
    -------
    tuple[Any, ...]
        The check implementations in declared order.

    Raises
    ------
    WorkerError
        Raised when a check is unknown or its contract drifts.
    """
    from confflow.execution.checks_standard import CHECKS

    resolved: list[Any] = []
    for name in checks:
        try:
            check = CHECKS[name]
        except KeyError as exc:
            raise WorkerError(
                "remote worker failed at stage 'resolve execution contracts': "
                f"unknown scientific check {name!r}"
            ) from exc
        _require_contract_version(
            versions, f"check:{name}", check.contract_version, f"check {name!r}"
        )
        resolved.append(check)
    return tuple(resolved)


def _resolve_recovery(recovery: str, versions: dict[str, str], adapter: Any) -> Any:
    """Resolve the recovery policy, binding rescue scans like batch does.

    Parameters
    ----------
    recovery : str
        Recovery profile name recorded by the producer.
    versions : dict[str, str]
        Contract versions recorded by the producer.
    adapter : Any
        The resolved program adapter binding rescue rendering.

    Returns
    -------
    Any
        The recovery policy implementation.

    Raises
    ------
    WorkerError
        Raised when the profile is unknown or its contract drifts.
    """
    from confflow.execution.recovery_standard import RECOVERIES, TsRescueScanPolicy

    try:
        policy = RECOVERIES[recovery]
    except KeyError as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown recovery profile {recovery!r}"
        ) from exc
    _require_contract_version(versions, "recovery", policy.contract_version, "recovery policy")
    if (
        getattr(policy, "name", "") == "ts_rescue_scan"
        and getattr(policy, "_adapter", None) is None
    ):
        policy = TsRescueScanPolicy(adapter=adapter)
    return policy


def _build_scientific(execution: ExecutionDefinition) -> tuple[Any, Any]:
    """Map the execution definition onto scientific values for the executor.

    The effective charge, multiplicity, and freeze arrive already resolved
    by the producer, so they ride as step overrides: overrides outrank
    structure properties, which reproduces the resolved values without ever
    re-deriving or defaulting them.

    Parameters
    ----------
    execution : ExecutionDefinition
        Compiled execution semantics of the work item.

    Returns
    -------
    tuple[Any, Any]
        The scientific definition and the (empty) scientific defaults.

    Raises
    ------
    WorkerError
        Raised when the scientific values are not constructible.
    """
    from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition

    overrides: dict[str, Any] = {}
    if execution.charge is not None:
        overrides["charge"] = execution.charge
    if execution.multiplicity is not None:
        overrides["multiplicity"] = execution.multiplicity
    if execution.freeze is not None:
        overrides["freeze"] = tuple(execution.freeze)
    try:
        scientific = ScientificDefinition(
            program=execution.program,
            role=None,
            execution_adapter=execution.execution_adapter,
            result_profile=execution.result_profile,
            native=FrozenDict(dict(execution.native)),
            checks=tuple(execution.checks),
            check_params=FrozenDict(
                {name: dict(params) for name, params in execution.check_params.items()}
            ),
            recovery=execution.recovery,
            recovery_params=FrozenDict(dict(execution.recovery_params)),
            seed=None,
            overrides=FrozenDict(overrides),
            transform=None,
        )
        return scientific, ScientificDefaults()
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"scientific definition is not constructible: {exc}"
        ) from exc


def _execute_work_item(item: WorkItem, context: Any, should_cancel: Any) -> Any:
    """Run one rebuilt work item through the shared executor.

    Parameters
    ----------
    item : WorkItem
        The rebuilt work item.
    context : Any
        The resolved item execution context.
    should_cancel : Any
        Cancellation probe forwarded unchanged to the executor.

    Returns
    -------
    Any
        The normalized work-item result, whatever its status.

    Raises
    ------
    WorkerError
        Raised when the executor itself cannot run.
    """
    from confflow.execution.work_item_executor import WorkItemExecutor

    try:
        return WorkItemExecutor().execute(item, context, should_cancel=should_cancel)
    except Exception as exc:
        raise WorkerError(f"remote worker failed at stage 'execute work item': {exc}") from exc


def _package_work_item_result(
    result: Any,
    handoff: WorkerHandoffV2,
    execution: ExecutionDefinition,
    context: Any,
    item: WorkItem,
    worker_root: str,
) -> str:
    """Package one executed result into a result-bundle directory.

    Parameters
    ----------
    result : Any
        The normalized work-item result from the shared executor.
    handoff : WorkerHandoffV2
        Validated envelope this work item was launched from.
    execution : ExecutionDefinition
        Compiled execution semantics (program identity for the bundle).
    context : Any
        The resolved item execution context locating the work directory.
    item : WorkItem
        The rebuilt work item locating its own item directory.
    worker_root : str
        Absolute worker sandbox root owning the result directory.

    Returns
    -------
    str
        Path of the packaged ``result.json`` bundle.

    Raises
    ------
    WorkerError
        Raised when produced files cannot be staged or packaged.
    """
    from .result_bundle import package_result_bundle

    environment: dict[str, Any] = {"program": execution.program}
    target = handoff.environment_request.target
    if target is not None:
        environment["target"] = target
    result_dir = os.path.join(worker_root, "results", _safe_token_component(handoff.launch_token))
    try:
        transfer_files_from = context.item_directory(item.logical_key)
    except Exception as exc:
        raise WorkerError(f"remote worker failed at stage 'package result bundle': {exc}") from exc
    try:
        return package_result_bundle(
            work_item_result=result,
            handoff=handoff,
            result_dir=result_dir,
            environment=environment,
            transfer_files_from=transfer_files_from,
        )
    except Exception as exc:
        raise WorkerError(f"remote worker failed at stage 'package result bundle': {exc}") from exc


def _safe_token_component(launch_token: str) -> str:
    """Return a filesystem-safe directory component for a launch token.

    Parameters
    ----------
    launch_token : str
        Launch token scoping one attempt.

    Returns
    -------
    str
        Token with path separators replaced, never empty.
    """
    cleaned = launch_token.replace("/", "_").replace("\x00", "_")
    cleaned = cleaned.replace(os.sep, "_")
    return cleaned or "attempt"
