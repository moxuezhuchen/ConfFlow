#!/usr/bin/env python3

"""Durable run-state records for ConfFlow Workflow V4 (V4-3).

This module owns publication stage 7 of the frozen eight-stage protocol in
:mod:`confflow.domain.publication`: after a step result is atomically
published, the run state transitions the step to its terminal lifecycle
status.  The record shape itself (:class:`RunState`, :class:`StepLifecycle`,
:func:`RunState.from_dict`) is frozen in :mod:`confflow.persistence.contracts`;
this module only implements load, atomic save, pure transitions, and the
crash-recovery helpers for the "published but state stale" scenario.

Like the rest of the persistence layer, this module imports only
``confflow.domain`` plus the standard library.
"""

from __future__ import annotations

import itertools
import json
import os
from typing import Final

from ..domain.canonical import canonical_json_bytes, typed_digest
from .contracts import (
    RUN_STATE_FILENAME,
    CorruptStateError,
    PersistenceError,
    RunState,
    RunStepStatus,
    StepLifecycle,
    run_state_path,
    validate_run_root,
    wall_now,
)
from .publication import STEP_RESULT_DIGEST_KIND, load_published_step_result

__all__ = [
    "detect_published",
    "ensure_step",
    "load_run_state",
    "repair_after_publish",
    "save_run_state",
    "transition_step",
]

_TMP_COUNTER: Final = itertools.count()


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
    """Return the temp-leftover filename prefix for run states."""
    return RUN_STATE_FILENAME + ".tmp."


def load_run_state(run_root: str) -> RunState | None:
    """Load the durable run state, or ``None`` when no state file exists.

    Parameters
    ----------
    run_root : str
        Managed persistence root owning ``run_state.json``.

    Returns
    -------
    RunState | None
        The rebuilt run state, or ``None`` when the file is absent.

    Raises
    ------
    CorruptStateError
        Raised when the file exists but is unreadable, is not valid JSON,
        or fails shape/version validation (via ``RunState.from_dict``,
        which already fails closed).  ``*.tmp.*`` leftovers are listed and
        explicitly skipped, never loaded.
    """
    target = run_state_path(validate_run_root(run_root))
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
        raise CorruptStateError(f"cannot read run state: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CorruptStateError(f"run state is not valid JSON: {exc}") from exc
    return RunState.from_dict(payload)


def save_run_state(run_root: str, state: RunState) -> None:
    """Atomically save *state* as the durable run state of *run_root*.

    Parameters
    ----------
    run_root : str
        Managed persistence root owning ``run_state.json``.
    state : RunState
        Run state to persist.

    Raises
    ------
    PersistenceError
        Raised when *state* is not a ``RunState``.
    """
    if not isinstance(state, RunState):
        raise PersistenceError("state must be a RunState")
    target = run_state_path(validate_run_root(run_root))
    _atomic_write_bytes(target, canonical_json_bytes(state.to_dict()))


def ensure_step(state: RunState, step_id: str) -> RunState:
    """Return *state* with *step_id* present as a ``PENDING`` lifecycle record.

    Pure and idempotent: when the step already exists the original object is
    returned unchanged; otherwise a new :class:`RunState` gains one
    ``PENDING`` record.

    Parameters
    ----------
    state : RunState
        Current run state; never mutated.
    step_id : str
        Step to ensure.

    Returns
    -------
    RunState
        The original state or a new state with the step added.

    Raises
    ------
    PersistenceError
        Raised when *state* is not a ``RunState`` or *step_id* is not a
        single portable path segment.
    """
    if not isinstance(state, RunState):
        raise PersistenceError("state must be a RunState")
    if state.step(step_id) is not None:
        return state
    record = StepLifecycle(step_id=step_id)
    return RunState(
        run_id=state.run_id,
        schema_version=state.schema_version,
        definition_digest=state.definition_digest,
        steps=state.steps + (record,),
        created_wall=state.created_wall,
        updated_wall=wall_now(),
    )


def transition_step(
    state: RunState,
    step_id: str,
    status: RunStepStatus,
    *,
    published_step_result_digest: str | None = None,
) -> RunState:
    """Return a new run state with *step_id* moved to *status*.

    Pure: the input state is never mutated.  The new state carries a fresh
    ``updated_wall`` timestamp and the step record carries exactly the given
    *published_step_result_digest* (``None`` clears any previous digest).

    Parameters
    ----------
    state : RunState
        Current run state; never mutated.
    step_id : str
        Step to transition; must already exist in *state*.
    status : RunStepStatus
        New lifecycle status of the step.
    published_step_result_digest : str | None
        Digest returned by publication, when the step now has one.

    Returns
    -------
    RunState
        New run state with the updated step record.

    Raises
    ------
    PersistenceError
        Raised when *state*/*status* have the wrong types, *step_id* is
        unknown, or the digest is neither a string nor ``None``.
    """
    if not isinstance(state, RunState):
        raise PersistenceError("state must be a RunState")
    if not isinstance(status, RunStepStatus):
        raise PersistenceError("status must be a RunStepStatus")
    if published_step_result_digest is not None and not isinstance(
        published_step_result_digest, str
    ):
        raise PersistenceError("published_step_result_digest must be a string or None")
    if state.step(step_id) is None:
        raise PersistenceError(f"unknown step: {step_id!r}")
    record = StepLifecycle(
        step_id=step_id,
        status=status,
        published_step_result_digest=published_step_result_digest,
        updated_wall=wall_now(),
    )
    steps = tuple(record if entry.step_id == step_id else entry for entry in state.steps)
    return RunState(
        run_id=state.run_id,
        schema_version=state.schema_version,
        definition_digest=state.definition_digest,
        steps=steps,
        created_wall=state.created_wall,
        updated_wall=wall_now(),
    )


def repair_after_publish(
    state: RunState, step_id: str, status: RunStepStatus, digest: str
) -> RunState:
    """Return a new run state recording an already-published step result.

    Pure helper for the crash-E scenario (the step result was published but
    the run state was never updated): ensures the step exists, then
    transitions it to the caller-supplied *status* with *digest* attached.
    The caller supplies the status honestly from the published result; this
    helper never infers ``COMPLETED`` versus ``PARTIAL`` itself.

    Parameters
    ----------
    state : RunState
        Current run state; never mutated.
    step_id : str
        Published step to record.
    status : RunStepStatus
        Lifecycle status matching the published result.
    digest : str
        Digest returned when the step result was published.

    Returns
    -------
    RunState
        New run state with the step ensured and transitioned.

    Raises
    ------
    PersistenceError
        Raised on unknown/invalid inputs, following :func:`ensure_step` and
        :func:`transition_step`.
    """
    if not isinstance(digest, str) or not digest.strip():
        raise PersistenceError("digest must be a non-empty string")
    return transition_step(
        ensure_step(state, step_id),
        step_id,
        status,
        published_step_result_digest=digest,
    )


def detect_published(run_root: str, step_id: str) -> str | None:
    """Return the published digest for *step_id*, or ``None`` when absent.

    When :func:`load_published_step_result` succeeds, its ``to_dict``
    payload is re-digested with the step-result kind marker, which equals
    the digest returned by :func:`publish_step_result` because publication
    digests are deterministic over the canonical payload.

    Parameters
    ----------
    run_root : str
        Managed persistence root owning the step directory.
    step_id : str
        Step to inspect.

    Returns
    -------
    str | None
        The published digest, or ``None`` when no publication exists.

    Raises
    ------
    CorruptStateError
        Propagates from the loader when a publication exists but is
        corrupt: corruption is never reported as absence.
    """
    result = load_published_step_result(run_root=run_root, step_id=step_id)
    if result is None:
        return None
    return typed_digest(STEP_RESULT_DIGEST_KIND, result.to_dict())
