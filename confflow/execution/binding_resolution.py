#!/usr/bin/env python3

"""V4 execution-binding resolution.

Single resolution point for machine settings; scientific definition,
scheduler policy, and execution binding are disjoint and this module never
reads workflow documents, YAML, or application requests: planned per-step
fields always win, request defaults only fill gaps. ``executable`` is planned
wins, else program default map, else adapter default; ``env`` is planned over
defaults as one layer of the effective env (producer adds ambient inheritance;
complete snapshot is digested and launched); recovery params can never inject
or override binding env. ``walltime_seconds``/``sandbox``/
``allowed_executables`` are preserved never dropped, defaults fill only
``None``. The step ``seed`` is the single stochastic authority and travels as
a typed resolved input.
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
    "effective_native_env",
    "resolve_execution_binding",
    "validate_step_seed",
]


@dataclass(frozen=True, slots=True)
class BindingRequestDefaults:
    """Explicit caller-supplied defaults filling planned-binding gaps."""

    executables: Mapping[str, str] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
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


def effective_native_env(
    binding_or_env: Any,
    *,
    inherit: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Return the complete effective native environment.

    This is the SINGLE construction point for the environment a native
    subprocess receives: callers MUST use the returned mapping for the
    subprocess launch, the ``ExecutionEnvironment`` ``relevant_env``
    digest axis, reuse identity, the remote handoff envelope, and worker
    re-measurement. Because there is one mapping, ``launch_env`` and
    ``hashed_env`` are identical by construction — an inherited variable
    that the executable can read can never be absent from the digest.

    ``inherit`` is the explicit inheritance policy:

    - Producer-side callers pass the ambient process environment
      (``os.environ``). Every inherited entry the subprocess can observe
      is then part of the digest, so changing or deleting an inherited
      variable moves execution identity and can never reuse a stale
      scientific result. Explicit binding entries win over inherited
      values (the hashed values are the launched values on collision).
    - Target-side (worker) callers pass no ``inherit``: the handoff
      envelope already carries the producer's complete snapshot, and the
      worker must not merge its own unrelated ambient environment.
    - Probe/reuse callers pass no ``inherit`` for the same reason: the
      snapshot is complete, and the digest must match the worker launch
      exactly.

    The result is fully string-typed and validated; unknown/non-string
    values fail closed.
    """
    if binding_or_env is None:
        declared: dict[str, str] = {}
    elif isinstance(binding_or_env, ExecutionBinding):
        declared = _validate_native_env_mapping(dict(binding_or_env.env))
    elif isinstance(binding_or_env, Mapping):
        declared = _validate_native_env_mapping(binding_or_env)
    else:
        raise DomainError("binding_or_env must be an ExecutionBinding, a mapping, or None")
    if inherit is None:
        return declared
    if not isinstance(inherit, Mapping):
        raise DomainError("inherit must be a mapping or None")
    inherited = _validate_native_env_mapping(inherit)
    effective = dict(inherited)
    effective.update(declared)
    return effective


def _validate_native_env_mapping(raw: Mapping[str, Any]) -> dict[str, str]:
    """Validate one env mapping into a fully string-typed copy (fail closed)."""
    if not isinstance(raw, Mapping):
        raise DomainError("env must be a mapping")
    effective: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise DomainError("effective env keys must be non-empty, trimmed strings")
        if key != key.strip():
            raise DomainError("effective env keys must be trimmed")
        if not isinstance(value, str):
            raise DomainError(
                f"effective env var {key!r} must be a string, got {type(value).__name__}"
            )
        effective[key] = value
    return effective


def resolve_execution_binding(
    *,
    program: str,
    planned: ExecutionBinding | Mapping[str, Any] | None,
    defaults: BindingRequestDefaults | Mapping[str, Any] | None = None,
    adapter_default_executable: str | None = None,
) -> ExecutionBinding:
    """Resolve one executable execution binding from explicit arguments."""
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
            "metadata": dict(planned.metadata),
        }
    elif planned is None:
        planned_map = {}
    elif isinstance(planned, Mapping):
        planned_map = dict(planned)
        if _planned_text(planned_map.get("target")) is not None:
            raise DomainError(
                "planned execution carries retired field 'target': "
                "remote delivery was retired, declare no target"
            )
    else:
        raise DomainError("planned must be an ExecutionBinding, a mapping, or None")
    if defaults is None:
        default_execs: Mapping[str, Any] = {}
        default_env: Mapping[str, Any] = {}
        default_walltime: int | None = None
    elif isinstance(defaults, BindingRequestDefaults):
        default_execs = defaults.executables
        default_env = defaults.env
        default_walltime = defaults.walltime_seconds
    elif isinstance(defaults, Mapping):
        default_execs = defaults.get("executables", {})
        default_env = defaults.get("env", {})
        if _planned_text(defaults.get("target")) is not None:
            raise DomainError(
                "defaults carry retired field 'target': "
                "remote delivery was retired, declare no target"
            )
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
        metadata=FrozenDict({str(k): v for k, v in metadata.items()}),
    )


def validate_step_seed(seed: Any) -> int | None:
    """Validate the typed step seed without touching native mappings."""
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DomainError(f"step seed must be an integer or None, got {seed!r}")
    return seed
