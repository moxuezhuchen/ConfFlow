"""Resolved V3 execution context and the Execution Fingerprint C.

This module owns the *execution-site* half of the V3 identity model
(RFC §16.C, runtime plan §2):

- **external input identity** — the ordered list of external-input content
  SHA-256 digests plus the cardinality fact; paths and basenames are
  provenance only and never enter the fingerprint (frozen PD-2);
- **executable identity** — per calc step, ``(program, execution-site
  resolved realpath, entrypoint SHA-256)``; resolution reads the filesystem
  only (no subprocess) and fails closed when the entrypoint is missing or
  unreadable (frozen PD-5);
- **ResolvedExecutionContextV3** — the immutable data the fingerprint is
  computed over: the R4.3/R4.5 handoff object;
- **Execution Fingerprint C** — the canonical hash of the frozen Definition
  Fingerprint A payload *plus* the execution context. C embeds the A payload
  itself (never a re-implementation of the A rules), so any semantic change
  moves C, while execution-only changes move C without touching A.

C is finalized where execution happens: the same functions run unchanged on a
worker host and depend on no controller state, cwd or caches.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config.canonical import (
    EXECUTION_CLASS_GLOBAL_MEMBERS,
    EXECUTION_CLASS_STEP_PARAMS,
    ValidationProfile,
    resolve_step_semantic_params,
)
from ..config.canonical.fingerprint import build_workflow_definition_payload_v3
from ..config.canonical.serialization import canonical_sha256
from ..core.exceptions import ConfFlowError
from ..core.path_policy import validate_executable_setting
from .plan import WorkflowV3Plan

__all__ = [
    "ExecutableIdentity",
    "ResolvedExecutionContextV3",
    "build_execution_fingerprint_payload_v3",
    "resolve_execution_context_v3",
    "resolve_executable_identity",
    "resolve_external_input_identity",
    "workflow_execution_fingerprint_v3",
]

_SHA256_PREFIX = "sha256:"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return _SHA256_PREFIX + digest.hexdigest()


# ---------------------------------------------------------------------------
# External input identity (PD-2)
# ---------------------------------------------------------------------------
def resolve_external_input_identity(input_files: list[str]) -> tuple[str, ...]:
    """Return the ordered content digests of the external inputs.

    Order is significant (multi-input workflows consume inputs positionally);
    paths and basenames are not part of the identity. A missing, unreadable or
    non-regular input fails closed — the fingerprint cannot be finalized from
    incomplete run context.
    """
    digests: list[str] = []
    for raw in input_files:
        path = Path(raw)
        if not path.is_file():
            raise ConfFlowError(f"External input is missing or not a regular file: {raw}")
        try:
            digests.append(_file_sha256(path))
        except OSError as exc:
            raise ConfFlowError(
                f"External input cannot be read; execution identity cannot be "
                f"finalized: {raw} ({exc})"
            ) from exc
    return tuple(digests)


# ---------------------------------------------------------------------------
# Executable identity (PD-5)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecutableIdentity:
    """Execution-site identity of one chemistry program entrypoint.

    ``resolved_path`` is the canonical realpath on the host that will execute
    the program; ``entrypoint_sha256`` is the SHA-256 of that entrypoint file
    (binary or wrapper script — the wrapper is the minimum identity; install
    trees are deliberately not hashed). Two spellings that resolve to the same
    realpath with the same bytes are the same identity.
    """

    program: str
    resolved_path: str
    entrypoint_sha256: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "program": self.program,
            "resolved_path": self.resolved_path,
            "sha256": self.entrypoint_sha256,
        }


def resolve_executable_identity(
    program: str, configured_executable: str, *, allowed_executables: tuple[str, ...] | None = None
) -> ExecutableIdentity:
    """Resolve one configured executable on the current execution site.

    Uses the existing single-executable path policy (free-form command
    prefixes are already rejected there), then ``PATH`` lookup for bare names,
    canonical realpath resolution, and an entrypoint SHA-256. Read-only: no
    subprocess is ever launched. Fail closed when the entrypoint is missing,
    not a regular file, or unreadable.
    """
    executable = validate_executable_setting(
        configured_executable, label=f"{program}_path", allowed_executables=allowed_executables
    )
    candidate = Path(executable)
    if not candidate.is_absolute():
        found = shutil.which(executable)
        if found is None:
            raise ConfFlowError(
                f"{program} executable {executable!r} not found on PATH; "
                "execution identity cannot be finalized"
            )
        candidate = Path(found)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ConfFlowError(
            f"{program} executable {executable!r} cannot be resolved: {exc}"
        ) from exc
    if not resolved.is_file():
        raise ConfFlowError(
            f"{program} executable {resolved} is not a regular file; "
            "execution identity cannot be finalized"
        )
    try:
        digest = _file_sha256(resolved)
    except OSError as exc:
        raise ConfFlowError(
            f"{program} executable {resolved} cannot be read; "
            "execution identity cannot be finalized"
        ) from exc
    return ExecutableIdentity(
        program=program, resolved_path=str(resolved), entrypoint_sha256=digest
    )


# ---------------------------------------------------------------------------
# Resolved execution context
# ---------------------------------------------------------------------------
def _step_execution_params(
    plan: WorkflowV3Plan,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Execution-class resolved params of the *enabled* steps, by stable ID.

    Frozen policy (RFC §12: a bypassed step never executes, so runnable
    preconditions apply only to enabled steps): C describes the actual
    execution context of this run, not the dormant configuration bag. A
    disabled step's semantic config is already bound by A — including its
    ``enabled`` flag — so flipping it later is a resume-rejecting A change
    regardless of C. The filter is driven by ``WorkflowV3Plan.step.enabled``
    (stable-ID keyed), never by raw YAML, state status, dirname or label.
    """
    definition = plan.definition
    pairs: list[tuple[str, dict[str, Any]]] = []
    for step in definition.steps:
        if step.id is None:
            raise ConfFlowError("V3 execution context requires persisted step ids")
        if not step.enabled:
            continue
        resolved = resolve_step_semantic_params(
            step, definition, profile=ValidationProfile.RUNNABLE
        )
        pairs.append(
            (
                step.id,
                {
                    key: value
                    for key, value in resolved.items()
                    if key in EXECUTION_CLASS_STEP_PARAMS
                },
            )
        )
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


def _execution_globals(plan: WorkflowV3Plan) -> dict[str, Any]:
    global_options = plan.definition.global_options
    return {
        member: getattr(global_options, member) for member in sorted(EXECUTION_CLASS_GLOBAL_MEMBERS)
    }


def _step_executables(
    plan: WorkflowV3Plan,
) -> tuple[tuple[str, ExecutableIdentity], ...]:
    """Execution-site identities for the *enabled* calc steps.

    Same frozen policy: a disabled calc never runs, so its dormant program
    must not be required to exist on this machine for C to finalize (and its
    executable bytes can change without moving C — the enabled flag is bound
    by A). Confgen steps have no external executable.
    """
    definition = plan.definition
    pairs: list[tuple[str, ExecutableIdentity]] = []
    for step in definition.steps:
        if step.id is None or step.type != "calc" or not step.enabled:
            continue
        resolved = resolve_step_semantic_params(
            step, definition, profile=ValidationProfile.RUNNABLE
        )
        program = str(resolved.get("iprog"))
        path_setting = resolved.get("gaussian_path" if program == "g16" else "orca_path")
        if path_setting is None:
            raise ConfFlowError(
                f"calc step {step.id!r} has no resolved executable path for {program!r}"
            )
        pairs.append((step.id, resolve_executable_identity(program, str(path_setting))))
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


@dataclass(frozen=True)
class ResolvedExecutionContextV3:
    """Immutable execution-site context the Execution Fingerprint C hashes.

    Built where execution happens; carries only fingerprint inputs — no state,
    directories, artifacts or step statuses. Everything is stored in
    normalized, sorted, immutable forms so the fingerprint can never drift
    from the context it was computed over.
    """

    input_digests: tuple[str, ...]
    execution_globals: tuple[tuple[str, Any], ...]
    step_execution_params: tuple[tuple[str, dict[str, Any]], ...]
    step_executables: tuple[tuple[str, ExecutableIdentity], ...]

    @property
    def input_count(self) -> int:
        return len(self.input_digests)


def resolve_execution_context_v3(
    plan: WorkflowV3Plan, *, input_files: list[str]
) -> ResolvedExecutionContextV3:
    """Resolve the full execution context on the current execution site.

    Read-only filesystem inspection: external inputs are hashed, calc
    executables are resolved and hashed. No directories are created and no
    process is started.
    """
    return ResolvedExecutionContextV3(
        input_digests=resolve_external_input_identity(input_files),
        execution_globals=tuple(sorted(_execution_globals(plan).items())),
        step_execution_params=_step_execution_params(plan),
        step_executables=_step_executables(plan),
    )


# ---------------------------------------------------------------------------
# Execution Fingerprint C
# ---------------------------------------------------------------------------
def build_execution_fingerprint_payload_v3(
    plan: WorkflowV3Plan, context: ResolvedExecutionContextV3
) -> dict[str, Any]:
    """Build the canonical C payload: the frozen A payload plus run context.

    The A payload is embedded verbatim (single Definition Fingerprint
    authority — no second set of semantic resolution rules), followed by the
    ordered external-input identity, the execution-class globals, the
    per-step execution-class params (stable-ID keyed) and the execution-site
    executable identities.
    """
    definition_payload = build_workflow_definition_payload_v3(plan.definition, registry=None)
    return {
        "definition": definition_payload,
        "inputs": {"count": context.input_count, "digests": list(context.input_digests)},
        "execution_globals": dict(context.execution_globals),
        "step_execution": {step_id: params for step_id, params in context.step_execution_params},
        "executables": {
            step_id: identity.to_payload() for step_id, identity in context.step_executables
        },
    }


def workflow_execution_fingerprint_v3(
    plan: WorkflowV3Plan, context: ResolvedExecutionContextV3
) -> str:
    """Return the Execution Fingerprint C (``sha256:…``) for the context.

    Pure function over the immutable context: same workflow + same inputs +
    same resources + same executable identities ⇒ same digest, independent of
    run id, work directory, config filename, timestamps or step statuses.
    """
    return _SHA256_PREFIX + canonical_sha256(build_execution_fingerprint_payload_v3(plan, context))
