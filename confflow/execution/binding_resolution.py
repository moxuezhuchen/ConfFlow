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
  It travels as a typed resolved input; the native ``RANDOMSEED`` vocabulary
  (verified against the installed ORCA 6.1.1 binary) is rendered exclusively
  by the program adapter.  This module never reads or writes native seed
  keys; it only exposes the effective native mapping.
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
    "is_local_target",
    "require_target_transport",
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


#: Canonical local-target aliases. ``None`` (omitted) and these names all
#: mean in-process local delivery with identical semantics. Every other
#: non-empty target string names a nonlocal endpoint and MUST resolve
#: through a configured transport; otherwise execution fails closed before
#: any native launch (no silent local fallback).
_LOCAL_TARGET_ALIASES: frozenset[str] = frozenset({"local", "localhost"})


def is_local_target(target: str | None) -> bool:
    """Return whether *target* selects in-process local delivery.

    Omitted (``None``) and explicit local aliases (``"local"``,
    ``"localhost"``, case-insensitive, surrounding whitespace ignored)
    are local with identical semantics. Any other non-empty string is a
    nonlocal endpoint identifier requiring a configured transport.
    """
    if target is None:
        return True
    if not isinstance(target, str):
        raise DomainError("target must be a string or None")
    text = target.strip()
    if not text:
        return True
    return text.lower() in _LOCAL_TARGET_ALIASES


def effective_native_env(binding_or_env: Any) -> dict[str, str]:
    """Return the effective native environment for measurement and launch.

    The effective environment is the COMPLETE scientifically relevant set:
    every explicit ``ExecutionBinding.env`` key/value, verbatim. Callers
    MUST use this single helper for (1) the native subprocess overlay,
    (2) ``ExecutionEnvironmentDigest`` ``relevant_env``, (3) reuse identity
    (via that digest), (4) remote ``target_env`` merging, and (5)
    worker-side re-measurement. Ambient ``os.environ`` entries outside
    this set are operational-only (PATH/HOME/TMPDIR and the like) and are
    digest-inert by explicit contract — relevance is declared by the
    binding, never inferred by scanning the process environment. The
    values hashed are always the values launched (binding wins over
    ambient on collision).
    """
    if binding_or_env is None:
        return {}
    if isinstance(binding_or_env, ExecutionBinding):
        raw: Mapping[str, Any] = dict(binding_or_env.env)
    elif isinstance(binding_or_env, Mapping):
        raw = binding_or_env
    else:
        raise DomainError("binding_or_env must be an ExecutionBinding, a mapping, or None")
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


def require_target_transport(target: str | None, transport: Any) -> Any:
    """Resolve the delivery transport for *target*, failing closed.

    Local targets (see :func:`is_local_target`) always return ``None``
    (in-process delivery, never a remote transport). Nonlocal targets
    require a configured *transport*: ``None`` (or a transport whose
    ``supports_target`` hook rejects the target) raises
    :class:`DomainError` BEFORE any native launch, so an explicit
    ``target=nonexistent-cluster`` can never silently execute locally.
    """
    if target is None or is_local_target(target):
        return None
    label = target.strip() if isinstance(target, str) else str(target)
    if transport is None:
        raise DomainError(
            f"step targets {label!r} but no transport is configured; "
            "refusing silent local fallback (0 native launches)"
        )
    supports = getattr(transport, "supports_target", None)
    if callable(supports):
        try:
            ok = supports(label)
        except Exception as exc:
            raise DomainError(f"target {label!r} cannot be resolved to a transport: {exc}") from exc
        if not ok:
            raise DomainError(
                f"step targets {label!r} but no matching transport claims it; "
                "refusing silent local fallback (0 native launches)"
            )
    return transport


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
    typed resolved input (``ResolvedCalculationInputs.seed``).  The native
    ``RANDOMSEED`` seed vocabulary (verified against the installed ORCA
    6.1.1 binary) is rendered exclusively by the program adapter; this
    module never reads or writes ``goat``/``RANDOMSEED`` keys.

    Seed vocabulary (verified): the native ``%goat`` key is ``RANDOMSEED``
    (verified against the installed ORCA 6.1.1 binary); the program adapter
    renders the integer step seed as ``RANDOMSEED`` and rejects any
    user-supplied ``RANDOMSEED`` as a second authority.  The invented
    ``Seed`` key never existed natively.
    """
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DomainError(f"step seed must be an integer or None, got {seed!r}")
    return seed
