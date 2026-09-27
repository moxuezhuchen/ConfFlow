#!/usr/bin/env python3

"""Single formal V4 runtime authority (worker I).

Every formal execution entrypoint — plain ``confflow`` CLI, the application
service (``run_workflow_through_service`` / ``build_workflow_service``), and
the control worker — enters the ONE V4 application through this module::

    formal entry -> V4 WorkflowDocument -> compile_workflow
        -> V4RunApplication orchestration -> typed Binding -> WorkItem
        -> executor -> WorkItemStore -> StepResult publication
        -> downstream -> manifest.

No formal path reaches the legacy engine (the historical workflow engine,
its calculation runners and result stores, legacy workflow-state execution,
legacy rerun glue, or program/task dispatch).  Legacy V2/V3 documents fail
closed with ``legacy_workflow_not_executable`` plus ``migration required``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.exceptions import ConfFlowError

V4_SCHEMA_ID = "confflow.workflow.v4"
LEGACY_CODE = "legacy_workflow_not_executable"

__all__ = [
    "LEGACY_CODE",
    "V4_SCHEMA_ID",
    "formal_v4_runner",
    "is_v4_document",
    "load_v4_document_file",
    "require_v4_document",
    "require_v4_document_file",
    "run_v4_document",
    "terminalize_generation_for_durable_cancel",
    "v4_status_payload",
]

#: Terminal run-result statuses the durable-cancel terminalizer understands.
_TERMINAL_RUN_STATUSES: tuple[str, ...] = ("completed", "partial", "failed", "cancelled")


def _legacy_error(detail: str) -> ConfFlowError:
    return ConfFlowError(f"{LEGACY_CODE}: {detail}; migration required")


def is_v4_document(document: Any) -> bool:
    """Return True only for a V4 workflow document mapping."""
    return isinstance(document, dict) and document.get("schema", "") == V4_SCHEMA_ID


def require_v4_document(document: Any) -> dict[str, Any]:
    """Return *document* when it is a V4 document, else fail closed."""
    if not isinstance(document, dict):
        raise _legacy_error("workflow document must be a mapping")
    if document.get("schema", "") != V4_SCHEMA_ID:
        raise _legacy_error("not a V4 workflow document")
    return document


def load_v4_document_file(path: str | Path) -> dict[str, Any]:
    """Load one workflow document file and require the V4 schema."""
    import yaml

    try:
        with open(path, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise _legacy_error(f"workflow file is not readable: {path}") from exc
    except OSError as exc:
        raise _legacy_error(f"workflow file is not readable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise _legacy_error(f"workflow file is not valid YAML: {exc}") from exc
    return require_v4_document(document)


def require_v4_document_file(path: str | Path) -> dict[str, Any]:
    """Preflight one config file for formal execution (side-effect free)."""
    return load_v4_document_file(path)


def run_v4_document(
    document: dict[str, Any],
    *,
    run_inputs: Any,
    run_root: str,
    owner_token: str = "formal",
    executables: dict[str, str] | None = None,
    supervisor: Any = None,
    transport: Any = None,
    import_sources: Any = None,
    should_cancel: Any = None,
) -> Any:
    """Run one V4 document through the single V4 application object."""
    from ..domain._immutable import FrozenDict
    from ..execution.process import NativeProcessSupervisor
    from .v4_run import V4RunApplication, V4RunRequest

    require_v4_document(document)
    resolved_supervisor = supervisor if supervisor is not None else NativeProcessSupervisor()
    request = V4RunRequest(
        workflow_document=document,
        run_inputs=run_inputs,
        run_root=run_root,
        owner_token=owner_token,
        executables=FrozenDict(dict(executables or {})),
        supervisor=resolved_supervisor,
        transport=transport,
        import_sources=FrozenDict(dict(import_sources or {})),
        should_cancel=should_cancel,
    )
    # One V4 application authority: compile_workflow lives inside run().
    return V4RunApplication(supervisor=resolved_supervisor).run(request)


def terminalize_generation_for_durable_cancel(*, run_root: str, reason: str) -> str:
    """Publish the run root's terminal generation truth for a confirmed cancel.

    A restarting controller that proved the prior producer process is gone
    and holds a durable cancellation intent must bring the JobDesk-visible
    generation record to its terminal truth instead of leaving a crashed
    attempt's ``running`` record current forever.

    The completion linearization point is the durable ``run_result.json``
    of the SAME generation:

    - a terminal manifest for the current generation already exists ->
      completion (or the scientific failure) linearized before the cancel;
      the generation record is terminalized with that manifest's status and
      is never rewritten as cancelled;
    - otherwise the durable cancel wins: the running generation record is
      terminalized as ``cancelled`` with no manifest pointer and an explicit
      failure note, so consumers report the cancel instead of a phantom
      running invocation.

    A missing generation record is never invented (the invocation never
    became a generation), and an already-terminal record is never rewritten
    — both return their existing effective status.  Returns the effective
    terminal status (``completed``/``partial``/``failed``/``cancelled``).
    """
    from ..persistence.generation import (
        load_run_generation,
        save_run_generation,
        terminal_record,
    )

    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string")
    record = load_run_generation(run_root)
    if record is None:
        return "cancelled"
    if record.is_terminal:
        return record.status
    manifest_status = _same_generation_manifest_status(run_root, record.generation_id)
    if manifest_status is not None:
        save_run_generation(
            run_root,
            terminal_record(
                record,
                status=manifest_status,
                manifest_generation_id=record.generation_id,
            ),
        )
        return manifest_status
    save_run_generation(
        run_root,
        terminal_record(
            record,
            status="cancelled",
            manifest_generation_id=None,
            active_step_id=record.active_step_id,
            failure={
                "type": "Cancelled",
                "message": reason,
                "step_id": record.active_step_id,
                "blocked_downstream": False,
            },
        ),
    )
    return "cancelled"


def _same_generation_manifest_status(run_root: str, generation_id: str) -> str | None:
    """Return the terminal status of the same-generation manifest, if any."""
    import json
    import os

    from .v4_run import RUN_RESULT_FILENAME

    path = os.path.join(run_root, RUN_RESULT_FILENAME)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("generation_id") != generation_id:
        return None
    status = payload.get("status")
    if status in _TERMINAL_RUN_STATUSES:
        return str(status)
    return None


def _declared_input_names(document: dict[str, Any]) -> list[str]:
    raw_inputs = document.get("inputs", {})
    if isinstance(raw_inputs, dict):
        return [name for name in raw_inputs if isinstance(name, str)]
    return []


def _build_run_inputs(
    document: dict[str, Any], xyz_texts: dict[str, str], *, source_names: dict[str, str]
) -> tuple[Any, Any]:
    """Build typed V4 run inputs plus raw import sources from XYZ texts."""
    from ..domain._immutable import FrozenDict
    from ..workflow.v4.assembly import RunInputs
    from .v4_run import import_xyz

    names = _declared_input_names(document)
    if not names:
        names = ["structures"]
    structures: dict[str, Any] = {}
    sources: dict[str, str] = {}
    if len(names) == 1:
        combined = "\n".join(text for text in xyz_texts.values() if text.strip())
        if not combined.strip():
            raise _legacy_error("no XYZ input content")
        first = next(iter(xyz_texts))
        structures[names[0]] = import_xyz(combined, source_name=source_names.get(first, first))
        sources[names[0]] = combined
    else:
        ordered_paths = list(xyz_texts)
        if len(ordered_paths) != len(names):
            raise _legacy_error(
                f"V4 workflow declares {len(names)} inputs but got {len(ordered_paths)} XYZ files"
            )
        for name, path in zip(names, ordered_paths):
            text = xyz_texts[path]
            structures[name] = import_xyz(text, source_name=source_names.get(path, path))
            sources[name] = text
    return (
        RunInputs(structures=FrozenDict(structures)),
        FrozenDict(sources),
    )


def formal_v4_runner(**kwargs: Any) -> dict[str, Any] | None:
    """Drop-in formal runner with the legacy engine call signature.

    Accepts the historical ``run_workflow`` keyword surface (``input_xyz``,
    ``config_file``, ``work_dir``, ``original_input_files``, ``resume``,
    ``verbose``, ``pause_beacon_file``, ``cancel_beacon_file``,
    ``step_started_callback``, ``on_step_status_change``, plus V4
    ``executables``/``supervisor``/``transport``) and runs the single V4
    application.  Legacy-only callbacks are accepted and ignored: progress
    publication flows through the durable V4 store, not callbacks.
    """
    from ..domain._immutable import FrozenDict
    from ..workflow.v4.assembly import RunInputs
    from .v4_run import V4RunApplication, V4RunRequest, import_xyz

    input_xyz = kwargs.get("input_xyz") or []
    config_file = kwargs.get("config_file")
    work_dir = kwargs.get("work_dir")
    executables = kwargs.get("executables") or {}
    supervisor = kwargs.get("supervisor")
    transport = kwargs.get("transport")
    owner_token = kwargs.get("owner_token") or "formal"
    if not config_file:
        raise _legacy_error("a V4 workflow document is required")
    if not work_dir:
        raise _legacy_error("a managed run root (work_dir) is required")
    if not input_xyz:
        raise _legacy_error("at least one XYZ input is required")
    document = load_v4_document_file(config_file)
    # Beacon pre-check: a pre-existing CANCEL/PAUSE beacon is a formal
    # lifecycle boundary. The V4 store is the durable truth; surfacing the
    # stop before launching keeps Ctrl-C / worker interruption faithful
    # without a second persistence.
    import os as _os

    from ..core.exceptions import StopRequestedError as _Stop
    from ..execution.cancellation import beacon_probe

    cancel_beacon = kwargs.get("cancel_beacon_file")
    for _beacon in (cancel_beacon, kwargs.get("pause_beacon_file")):
        if _beacon and _os.path.exists(_beacon):
            raise _Stop(f"workflow stopped by beacon: {_beacon}")
    # Live cancellation: the SAME beacon the service/control cancel paths
    # touch is polled by every layer for the whole run, so a cancel during
    # a native execution terminates the process boundary instead of
    # waiting for the run to complete.
    should_cancel = beacon_probe(cancel_beacon) if cancel_beacon else None
    xyz_texts: dict[str, str] = {}
    for path in list(input_xyz):
        try:
            with open(path, encoding="utf-8") as handle:
                xyz_texts[str(path)] = handle.read()
        except OSError as exc:
            raise _legacy_error(f"XYZ input is not readable: {path}") from exc
    names = _declared_input_names(document)
    if not names:
        names = ["structures"]
    structures: dict[str, Any] = {}
    sources: dict[str, str] = {}
    if len(names) == 1:
        combined = "\n".join(text for text in xyz_texts.values() if text.strip())
        if not combined.strip():
            raise _legacy_error("no XYZ input content")
        structures[names[0]] = import_xyz(combined, source_name=str(input_xyz[0]))
        sources[names[0]] = combined
    else:
        ordered = list(xyz_texts)
        if len(ordered) != len(names):
            raise _legacy_error(
                f"V4 workflow declares {len(names)} inputs but got {len(ordered)} XYZ files"
            )
        for name, path in zip(names, ordered):
            structures[name] = import_xyz(xyz_texts[path], source_name=path)
            sources[name] = xyz_texts[path]
    run_inputs = RunInputs(structures=FrozenDict(structures))
    from ..execution.process import NativeProcessSupervisor

    resolved_supervisor = supervisor if supervisor is not None else NativeProcessSupervisor()
    request = V4RunRequest(
        workflow_document=document,
        run_inputs=run_inputs,
        run_root=str(work_dir),
        owner_token=str(owner_token),
        executables=FrozenDict(dict(executables)),
        supervisor=resolved_supervisor,
        transport=transport,
        import_sources=FrozenDict(sources),
        should_cancel=should_cancel,
    )
    # One V4 application authority: compile_workflow lives inside run().
    report = V4RunApplication(supervisor=resolved_supervisor).run(request)
    return {
        "ok": True,
        "run_id": report.run_id,
        "status": report.status,
        "definition_digest": report.definition_digest,
    }


def v4_status_payload(report: Any) -> dict[str, Any]:
    """Return the JSON-faithful status projection of a V4 run report."""
    return {
        "run_id": report.run_id,
        "status": report.status,
        "definition_digest": report.definition_digest,
        "steps": [
            {
                "id": result.step_id,
                "status": result.status.value,
                "summary": dict(result.summary.thaw()),
                "diagnostics": [
                    {
                        "code": item.code,
                        "severity": item.severity.value,
                        "message": item.message,
                        "step_id": item.step_id,
                        "field_path": item.field_path,
                    }
                    for item in result.diagnostics
                ],
            }
            for result in report.step_results
        ],
        "manifest": report.manifest.thaw(),
    }
