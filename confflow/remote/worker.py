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
        "topology_patch",
        "working_topology",
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
        "result_id",
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
    target_default_executable: str | None = None,
    target_env: Mapping[str, str] | None = None,
) -> str:
    """Run one worker-handoff envelope through the shared executor.

    Parameters
    ----------
    handoff_path : str
        Path of the worker-handoff V3 envelope file.
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
    target_default_executable : str | None
        Target-host default binary for the handoff program; the program
        adapter default applies when absent.  The handoff's explicit
        executable request is never replaced silently: it is carried
        verbatim and fails closed when unmeasurable on the target.
    target_env : Mapping[str, str] | None
        Target-host environment entries under the handoff env.

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
        target_default_executable=target_default_executable,
        target_env=target_env,
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

    handoff = read_handoff_envelope(path=handoff_path, expected_run_id=None)
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

    structures_by_port: dict[str, list[StructureRecord]] = {}
    artifact_entries: list[ArtifactBundleEntry] = []
    result_entries: list[ResultEntry] = []
    for entry in handoff.inputs.entries:
        if isinstance(entry, StructureBundleEntry):
            structures_by_port.setdefault(entry.port, []).append(_structure_from_entry(entry))
        elif isinstance(entry, ArtifactBundleEntry):
            artifact_entries.append(entry)
        elif isinstance(entry, ResultEntry):
            result_entries.append(entry)
        else:  # pragma: no cover - pydantic union parsing guards the type
            raise WorkerError(
                "remote worker failed at stage 'rebuild work item inputs': "
                f"unknown bundle entry {type(entry).__name__}"
            )
    driving = structures_by_port.get(DRIVING_STRUCTURE_PORT, [])
    if len(driving) == 1 and len(structures_by_port) == 1:
        structures = driving
        structure_inputs = FrozenDict({DRIVING_STRUCTURE_PORT: StructureSet.of(structures[0])})
    elif DRIVING_STRUCTURE_PORT not in structures_by_port and structures_by_port:
        # Named-structure items carry slots (reactant/product/guess) and no
        # driving port; slots rebuild by explicit port name, never by order.
        structure_inputs = FrozenDict(
            {
                port: StructureSet.of(*records)
                for port, records in sorted(structures_by_port.items())
            }
        )
    else:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            "expected exactly one driving structure entry or named slots, "
            f"found ports {sorted(structures_by_port)}"
        )
    artifacts = _artifacts_from_entries(
        artifact_entries, staged_bundle=staged_bundle, worker_root=worker_root
    )
    results_by_port: dict[str, list[ScientificResult]] = {}
    for entry in sorted(result_entries, key=lambda item: (item.port, item.result_id)):
        results_by_port.setdefault(entry.port, []).append(_result_from_entry(entry))
    results = FrozenDict(
        {port: ResultSet(tuple(records)) for port, records in sorted(results_by_port.items())}
    )
    resources = _resources_from_definition(handoff.execution)
    artifact_inputs = FrozenDict(
        {port: ArtifactSet(tuple(references)) for port, references in sorted(artifacts.items())}
    )
    result_inputs = results
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
    payload_id = payload.get("result_id")
    if payload_id is not None and entry.result_id != payload_id:
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"result entry {entry.result_id!r} mismatches payload result_id "
            f"{payload_id!r}; bundle identity must be the producer-scoped id"
        )
    if payload_id is None and not entry.result_id.startswith("legacy:"):
        raise WorkerError(
            "remote worker failed at stage 'rebuild work item inputs': "
            f"result entry {entry.result_id!r} carries no result_id but is not "
            "marked legacy; producer outputs must travel under their true id"
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
    """Rebuild artifact references grouped by port from staged bytes.

    Port structure is preserved exactly: the rebuilt item carries the same
    named artifact ports the producer assembled.  Grouping is by the
    bundle ``port`` (never by role or order).

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
        Artifact references keyed by port.

    Raises
    ------
    WorkerError
        Raised when staged bytes are missing, escape the worker root, or a
        reference is not rebuildable.
    """
    grouped: dict[str, list[ArtifactRef]] = {}
    for entry in sorted(entries, key=lambda item: (item.port, item.artifact_id)):
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
        grouped.setdefault(entry.port, []).append(reference)
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


def _resolve_program(program: str | None, versions: dict[str, str]) -> Any:
    """Resolve the handoff program through the program registry.

    Calculation handoffs name a real program; anything else fails closed
    here, never at native launch.
    """
    from confflow.programs.registry import get_program_adapter

    if not isinstance(program, str) or not program.strip():
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            "calculation handoff names no program"
        )
    try:
        adapter = get_program_adapter(program)
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown program {program!r}: {exc}"
        ) from exc
    _require_contract_version(versions, "adapter", adapter.adapter_version, "program adapter")
    if not versions.get("parser"):
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            "program parser contract version is missing from the handoff; "
            "refusing a handoff that does not pin parser semantics"
        )
    _require_contract_version(versions, "parser", adapter.parser_version, "program parser")
    return adapter


def _measure_worker_environment(
    *, capability: str, adapter: Any, binding: Any, relevant_env: Any = None
) -> Any:
    """Measure the worker-side native execution environment.

    The measured ``relevant_env`` is the complete target-side effective env
    (``target_env`` under the producer handoff snapshot, handoff wins — see
    ``resolve_remote_target_binding``).  The caller passes the same
    immutable snapshot the worker launches with; the worker never merges
    its own ambient environment. Unknown/unmeasurable never stands in for
    verified equivalence: a binary that cannot be measured fails the
    handoff closed before any native launch.
    """
    from confflow.execution.environment import EnvironmentMeasurer

    candidate = binding.executable
    if not isinstance(candidate, str) or not candidate.strip():
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            "target execution binding carries no executable"
        )
    try:
        from confflow.execution.binding_resolution import effective_native_env

        relevant = dict(relevant_env) if relevant_env is not None else effective_native_env(binding)
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"target execution binding carries an invalid env: {exc}"
        ) from exc
    try:
        return EnvironmentMeasurer().build_environment(
            candidate, adapter=adapter, target=binding.target, relevant_env=relevant
        )
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"cannot measure worker execution environment for {candidate!r}: {exc}"
        ) from exc


def _measure_pure_worker_environment(*, capability: str) -> Any:
    """Build the worker-side pure-implementation environment identity."""
    from confflow.execution.environment import build_pure_environment
    from confflow.execution.registry import default_registry

    try:
        contract = default_registry().resolve_executor(capability)
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown executor capability {capability!r}: {exc}"
        ) from exc
    return build_pure_environment(
        implementation=f"confflow.{capability}",
        implementation_version=contract.contract_version,
        metadata={"worker": "remote"},
    )


def _build_calculation_scientific(execution: ExecutionDefinition) -> tuple[Any, Any]:
    """Map the execution definition onto calculation scientific values."""
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
            seed=execution.seed,
            overrides=FrozenDict(overrides),
            transform=None,
        )
        return scientific, ScientificDefaults()
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"scientific definition is not constructible: {exc}"
        ) from exc


def _build_pure_scientific(execution: ExecutionDefinition) -> tuple[Any, Any]:
    """Map the execution definition onto pure-executor scientific values.

    The worker rebuilds the same typed scientific state the producer
    compiled: native vocabulary plus the single-authority seed for
    stochastic capabilities, the transform kind for structure transforms,
    and no program-backed fields.
    """
    from confflow.workflow.v4.document import ScientificDefaults, ScientificDefinition

    capability = execution.executor
    try:
        if capability == "confgen":
            scientific = ScientificDefinition(
                result_profile="ensemble",
                native=FrozenDict(dict(execution.native)),
                seed=execution.seed,
                overrides=FrozenDict({}),
            )
        elif capability == "structure_transform":
            if not isinstance(execution.transform, str) or not execution.transform.strip():
                raise ValueError("structure_transform handoff names no transform kind")
            scientific = ScientificDefinition(
                result_profile="standard",
                native=FrozenDict(dict(execution.native)),
                transform=execution.transform.strip(),
            )
        elif capability == "analysis":
            scientific = ScientificDefinition(
                result_profile="standard",
                native=FrozenDict(dict(execution.native)),
                checks=tuple(execution.checks),
                check_params=FrozenDict(
                    {name: dict(params) for name, params in execution.check_params.items()}
                ),
            )
        else:
            raise ValueError(f"unsupported pure executor {capability!r}")
        return scientific, ScientificDefaults()
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"pure scientific definition is not constructible: {exc}"
        ) from exc


def _resolve_execution_context(
    handoff: WorkerHandoffV2,
    worker_root: str,
    launch_token: str,
    supervisor: ProcessSupervisor | None,
    *,
    target_default_executable: str | None = None,
    target_env: Mapping[str, str] | None = None,
) -> Any:
    """Resolve the executor context from the compiled execution definition.

    The worker dispatches through the execution registry on the handoff
    executor capability (never a hardcoded calculation executor), resolves
    the target-side binding through the shared binding authority, measures
    its own execution environment, and threads the handoff attempt number
    into the context so attempt-isolated directories align with the
    producer's durable attempt.

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
    target_default_executable : str | None
        Target-host default binary for the handoff program; falls back to
        the program adapter default when absent.
    target_env : Mapping[str, str] | None
        Target-host environment entries under the handoff env.

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
    from confflow.execution.binding_resolution import resolve_remote_target_binding
    from confflow.execution.work_item_executor import ItemExecutionContext

    execution = handoff.execution
    versions = dict(execution.contract_versions)
    capability = execution.executor
    from confflow.execution.registry import RegistryLookupError, default_registry

    try:
        registry = default_registry()
        executor_contract = registry.resolve_executor(capability)
    except RegistryLookupError as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown executor capability {capability!r}: {exc}"
        ) from exc
    _require_contract_version(versions, "executor", executor_contract.contract_version, "executor")
    if capability == "calculation":
        adapter = _resolve_program(execution.program, versions)
        profile = _resolve_profile(execution.result_profile, versions)
        checks = _resolve_checks(execution.checks, versions)
        recovery = _resolve_recovery(execution.recovery, versions, adapter)
        default_executable = target_default_executable or adapter.default_executable
        try:
            binding = resolve_remote_target_binding(
                program=adapter.program_name.value,
                handoff_execution={
                    "executable": execution.handoff_executable,
                    "env": dict(execution.handoff_env),
                    "walltime_seconds": execution.handoff_walltime_seconds,
                    "target": handoff.environment_request.target,
                },
                target_default_executable=default_executable,
                target_env=target_env,
            )
        except Exception as exc:
            raise WorkerError(
                "remote worker failed at stage 'resolve execution contracts': "
                f"target execution binding is not resolvable: {exc}"
            ) from exc
        # ONE immutable target-side snapshot: target defaults with the
        # producer's complete handoff snapshot overlaid (handoff wins).
        # The worker MUST NOT merge its own unrelated ambient environment.
        # The same mapping is measured and launched.
        from confflow.execution.binding_resolution import effective_native_env

        native_env = FrozenDict(effective_native_env(binding))
        environment = _measure_worker_environment(
            capability=capability,
            adapter=adapter,
            binding=binding,
            relevant_env=native_env,
        )
        scientific, defaults = _build_calculation_scientific(execution)
    elif capability in ("confgen", "structure_transform", "analysis"):
        adapter = None
        profile = None
        checks = ()
        recovery = None
        binding = None
        native_env = None
        environment = _measure_pure_worker_environment(capability=capability)
        scientific, defaults = _build_pure_scientific(execution)
    else:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown executor capability {capability!r}"
        )
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
            environment=environment,
            native_env=native_env,
            attempt=int(handoff.attempt_number),
            executor_capability=capability,
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

    Single-authority resolution goes through the execution registry:
    the descriptor and the runtime implementation are one entry, so a
    published-but-unimplemented profile fails closed here instead of
    compiling and crashing at runtime.

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
    from confflow.execution.registry import RegistryLookupError, default_registry

    try:
        profile = default_registry().profile_implementation(result_profile)
    except RegistryLookupError as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown result profile {result_profile!r}"
        ) from exc
    _require_contract_version(versions, "profile", profile.contract_version, "result profile")
    return profile


def _resolve_checks(checks: tuple[str, ...], versions: dict[str, str]) -> tuple[Any, ...]:
    """Resolve the scientific checks named by the execution definition.

    Single-authority resolution goes through the execution registry.

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
    from confflow.execution.registry import RegistryLookupError, default_registry

    registry = default_registry()
    resolved: list[Any] = []
    for name in checks:
        try:
            check = registry.check_implementation(name)
        except RegistryLookupError as exc:
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

    Single-authority resolution goes through the execution registry with
    the step's program adapter, so rescue work renders through the same
    file-format authority as primary work.

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
    from confflow.execution.registry import RegistryLookupError, default_registry

    try:
        policy = default_registry().recovery_implementation(recovery, adapter=adapter)
    except RegistryLookupError as exc:
        raise WorkerError(
            "remote worker failed at stage 'resolve execution contracts': "
            f"unknown recovery profile {recovery!r}"
        ) from exc
    _require_contract_version(versions, "recovery", policy.contract_version, "recovery policy")
    return policy


def _execute_work_item(item: WorkItem, context: Any, should_cancel: Any) -> Any:
    """Run one rebuilt work item through the registry-dispatched executor.

    The executor is resolved from the handoff capability carried on the
    context (never a hardcoded calculation executor): the worker runs the
    same executor family the producer dispatched.

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
        Raised when no executor is registered for the capability or the
        executor itself cannot run.  Executor *results* (including FAILED
        and CANCELLED) are data and are returned, never raised.
    """
    from confflow.execution.registry import RegistryLookupError, default_registry

    capability = getattr(context, "executor_capability", None)
    if not isinstance(capability, str) or not capability.strip():
        raise WorkerError(
            "remote worker failed at stage 'execute work item': "
            "execution context carries no executor capability"
        )
    try:
        executor = default_registry().executor_implementation(capability)()
    except RegistryLookupError as exc:
        raise WorkerError(
            "remote worker failed at stage 'execute work item': "
            f"no executor registered for capability {capability!r}: {exc}"
        ) from exc
    try:
        return executor.execute(item, context, should_cancel=should_cancel)
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

    environment = context.environment
    if environment is None or not hasattr(environment, "to_dict"):
        raise WorkerError(
            "remote worker failed at stage 'package result bundle': "
            "execution context carries no measured environment"
        )
    try:
        environment_map = dict(environment.to_dict())
    except Exception as exc:
        raise WorkerError(
            "remote worker failed at stage 'package result bundle': "
            f"measured environment is not serializable: {exc}"
        ) from exc
    result_dir = os.path.join(worker_root, "results", _safe_token_component(handoff.launch_token))
    try:
        transfer_files_from = context.attempt_dir(item)
    except Exception as exc:
        raise WorkerError(f"remote worker failed at stage 'package result bundle': {exc}") from exc
    try:
        return package_result_bundle(
            work_item_result=result,
            handoff=handoff,
            result_dir=result_dir,
            environment=environment_map,
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
