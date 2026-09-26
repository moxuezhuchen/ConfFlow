#!/usr/bin/env python3

"""V4 execution-binding resolution (wave-1 stream C).

Single resolution point for machine-specific execution settings.  Scientific
definition, scheduler policy, and execution binding are three disjoint axes:
this module never reads workflow documents, YAML, or application request
objects.  Callers pass explicit domain/execution values; planned (per-step)
fields always win and request-level defaults only fill gaps.

Rules (frozen):

- ``executable``: planned absolute/PATH name wins; else the caller-supplied
  program default map; else the adapter default.  Resolution never requires a
  local absolute path to exist on a remote target: target-side callers pass
  their own resolved executable and this function only carries the string.
- ``env``: planned entries win over default entries; the merged mapping is
  recorded verbatim for audit.  Scientific recovery params can never inject
  or override binding environment (see ``work_item_executor``).
- ``target`` / ``walltime_seconds`` / ``sandbox`` / ``allowed_executables``:
  planned values are preserved, never dropped; defaults fill only ``None``.
- Seed propagation: the step ``seed`` is the single stochastic authority.
  Helpers here validate/inject the native GOAT ``Seed`` so adapters render
  exactly one seed.  Digest folding stays with fingerprint (B owns); remote
  handoff threading is D/E work — this module only exposes the effective
  native mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.errors import DomainError
from .contracts import ExecutionBinding

__all__ = [
    "BindingRequestDefaults",
    "resolve_execution_binding",
    "resolve_remote_target_binding",
    "validate_step_seed",
]


@dataclass(frozen=True, slots=True)
class BindingRequestDefaults:
    """Explicit caller-supplied defaults filling planned-binding gaps."""

    executables: Mapping[str, str] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    target: str | None = None
    walltime_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.executables, Mapping):
            raise DomainError("executables defaults must be a mapping")
        if not isinstance(self.env, Mapping):
            raise DomainError("env defaults must be a mapping")


def _planned_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_execution_binding(
    *,
    program: str,
    planned: ExecutionBinding | Mapping[str, Any] | None,
    defaults: BindingRequestDefaults | Mapping[str, Any] | None = None,
    adapter_default_executable: str | None = None,
) -> ExecutionBinding:
    """Resolve one executable execution binding from explicit arguments.

    Parameters
    ----------
    program : str
        Scientific program name (for the executables-map lookup).
    planned : ExecutionBinding | Mapping | None
        Per-step planned execution (``StepModel.execution`` equivalent).
        Every set field wins over defaults.
    defaults : BindingRequestDefaults | Mapping | None
        Caller-level defaults (executables map, env, target, walltime).
        A plain mapping may carry ``executables``/``env``/``target``/
        ``walltime_seconds`` keys.
    adapter_default_executable : str | None
        Adapter ``default_executable`` used as the last resort.

    Returns
    -------
    ExecutionBinding
        Resolved binding preserving env/target/walltime/executable
        identity.  No filesystem validation happens here, so remote
        targets never need local absolute paths to resolve.
    """
    if not isinstance(program, str) or not program.strip():
        raise DomainError("program must be a non-empty string")
    if isinstance(planned, ExecutionBinding):
        planned_map: dict[str, Any] = {
            "binding_id": planned.binding_id,
            "executable": planned.executable,
            "env": dict(planned.env),
            "sandbox": planned.sandbox,
            "allowed_executables": list(planned.allowed_executables),
            "walltime_seconds": planned.walltime_seconds,
            "target": planned.target,
            "metadata": dict(planned.metadata),
        }
    elif planned is None:
        planned_map = {}
    elif isinstance(planned, Mapping):
        planned_map = dict(planned)
    else:
        raise DomainError("planned must be an ExecutionBinding, a mapping, or None")
    if defaults is None:
        default_execs: Mapping[str, Any] = {}
        default_env: Mapping[str, Any] = {}
        default_target: str | None = None
        default_walltime: int | None = None
    elif isinstance(defaults, BindingRequestDefaults):
        default_execs = defaults.executables
        default_env = defaults.env
        default_target = defaults.target
        default_walltime = defaults.walltime_seconds
    elif isinstance(defaults, Mapping):
        default_execs = defaults.get("executables", {})
        default_env = defaults.get("env", {})
        default_target = defaults.get("target")
        default_walltime = defaults.get("walltime_seconds")
        if not isinstance(default_execs, Mapping):
            raise DomainError("defaults executables must be a mapping")
        if not isinstance(default_env, Mapping):
            raise DomainError("defaults env must be a mapping")
    else:
        raise DomainError("defaults must be BindingRequestDefaults, a mapping, or None")

    planned_exe = _planned_text(planned_map.get("executable"))
    default_exe = _planned_text(default_execs.get(program.strip()))
    executable = planned_exe or default_exe or _planned_text(adapter_default_executable)
    if executable is None:
        raise DomainError(
            f"no executable resolved for program {program!r}; "
            "planned execution, defaults map, and adapter default are all empty"
        )

    merged_env: dict[str, str] = {str(k): str(v) for k, v in dict(default_env).items()}
    planned_env = planned_map.get("env", {})
    if planned_env is None:
        planned_env = {}
    if not isinstance(planned_env, Mapping):
        raise DomainError("planned env must be a mapping")
    for key, value in planned_env.items():
        merged_env[str(key)] = str(value)

    walltime = planned_map.get("walltime_seconds", None)
    if walltime is None:
        walltime = default_walltime
    target = _planned_text(planned_map.get("target")) or _planned_text(default_target)
    sandbox = _planned_text(planned_map.get("sandbox"))
    binding_id = _planned_text(planned_map.get("binding_id")) or "resolved"
    allowed = tuple(
        str(item) for item in (planned_map.get("allowed_executables") or ()) if str(item).strip()
    )
    metadata_raw = planned_map.get("metadata", {})
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    metadata.setdefault("program", program.strip())
    return ExecutionBinding(
        binding_id=binding_id,
        executable=executable,
        env=FrozenDict(merged_env),
        sandbox=sandbox,
        allowed_executables=allowed,
        walltime_seconds=walltime,
        target=target,
        metadata=FrozenDict({str(k): v for k, v in metadata.items()}),
    )


def resolve_remote_target_binding(
    *,
    program: str,
    handoff_execution: Mapping[str, Any] | None,
    target_default_executable: str,
    target_env: Mapping[str, str] | None = None,
) -> ExecutionBinding:
    """Resolve the target-side binding for a remote handoff.

    The handoff's requested executable is carried verbatim: an explicitly
    requested path stays explicit and fails closed at measurement/launch
    when it does not exist on the target — it is never silently rewritten
    to another binary.  Only an absent request falls back to the target's
    own configured default.  The requested value additionally rides as
    audit provenance in metadata.  No filename parsing ever decides the
    launch path.
    """
    if not isinstance(target_default_executable, str) or not target_default_executable.strip():
        raise DomainError("target_default_executable must be a non-empty string")
    base: dict[str, Any] = dict(handoff_execution) if handoff_execution else {}
    requested = _planned_text(base.get("executable"))
    target_default = target_default_executable.strip()
    executable = requested if requested else target_default
    env: dict[str, str] = {}
    if target_env:
        env.update({str(k): str(v) for k, v in dict(target_env).items()})
    handoff_env = base.get("env", {})
    if isinstance(handoff_env, Mapping):
        env.update({str(k): str(v) for k, v in handoff_env.items()})
    return ExecutionBinding(
        binding_id=str(base.get("binding_id") or "remote-target"),
        executable=executable,
        env=FrozenDict(env),
        sandbox=None,
        allowed_executables=(),
        walltime_seconds=base.get("walltime_seconds"),
        target=_planned_text(base.get("target")),
        metadata=FrozenDict(
            {"program": program, "resolved": "remote-target", "requested_executable": requested}
        ),
    )


def validate_step_seed(seed: Any) -> int | None:
    """Validate the typed step seed without touching native mappings.

    The step seed is the single stochastic authority and travels as a
    typed resolved input (``ResolvedCalculationInputs.seed``).  Native seed
    vocabulary, if any verifies against the installed binary in wave 2, is
    rendered exclusively by the program adapter; this module never reads or
    writes ``goat``/``Seed``/``RANDOMSEED`` keys.

    Wave-1 status: ORCA 6.1 documents no integer GOAT ``Seed`` (older
    manuals document a ``RANDOMSEED`` boolean whose 6.1 semantics are
    unverified), so no adapter renders a seed yet — see the wave-2
    dependency in the C report.  Nothing here claims deterministic native
    sampling.
    """
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DomainError(f"step seed must be an integer or None, got {seed!r}")
    return seed
