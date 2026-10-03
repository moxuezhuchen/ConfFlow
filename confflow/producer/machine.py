#!/usr/bin/env python3

"""Producer machine-capacity resolver (Phase 5 input simplification).

:func:`resolve_machine_resources` turns a per-item scientific request plus a
machine capacity profile into strict V4 ``resources``/``scheduler`` wire
mappings.  It is a pure authoring helper: every rule it applies comes from an
existing authority, and it creates no second runtime.

Authorities consulted (imported, never copied):

- :func:`confflow.domain.resources.parse_memory_bytes` -- the single memory
  text authority for capacities and requests;
- :class:`confflow.domain.resources.ResourceRequest` /
  :class:`confflow.domain.resources.SchedulerPolicy` -- range rules
  (cores ``>= 1``, memory ``> 0``, width ``>= 1``) and the
  scientific-vs-operational split;
- :class:`confflow.domain.resources.OnFailure` -- the closed scheduler
  failure vocabulary;
- :mod:`confflow.workflow.v4.schema` -- the strict V4 wire shapes the
  returned mappings must satisfy (``cores_per_item``/``memory_per_item``,
  ``max_parallel_items``/``on_failure``).

Digest contract (from :mod:`confflow.workflow.v4.fingerprint`):

- the returned ``resources`` are scientific: they move step and definition
  digests;
- the returned ``scheduler`` width, ``on_failure``, and every operational
  profile field (executable, env, remote/target locators) are digest-inert
  provenance: they travel in ``provenance`` only.

The request is never silently defaulted: both resource dimensions are
required, an oversized request fails instead of clamping, and an explicit
scheduler override must fit the capacity it claims.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..domain.errors import InvalidResourceError
from ..domain.resources import OnFailure, parse_memory_bytes

__all__ = [
    "MACHINE_RESOLUTION_VERSION",
    "resolve_machine_resources",
]

#: Version stamp recorded on every machine-resolution provenance record.
MACHINE_RESOLUTION_VERSION = "confflow.producer.machine.v1"

#: Operational machine capacity used to size scheduling.
_CAPACITY_KEYS = frozenset({"name", "total_cores", "total_memory"})

#: Profile keys that are operational only: recorded in provenance, never
#: resolved into scientific wire.  ``remote`` is the human-facing alias for
#: the endpoint locator the V4 execution binding calls ``target``; both are
#: accepted and both stay provenance-only.
_OPERATIONAL_KEYS = frozenset(
    {
        "executable",
        "binding_id",
        "env",
        "remote",
        "remote_target",
        "target",
        "sandbox",
        "allowed_executables",
        "walltime_seconds",
    }
)

_PROFILE_KEYS = _CAPACITY_KEYS | _OPERATIONAL_KEYS

#: Allowed keys of the per-item scientific request (the V4 resources wire).
_REQUEST_KEYS = frozenset({"cores_per_item", "memory_per_item"})

#: Allowed keys of the scheduler override (the V4 scheduler wire).
_SCHEDULER_KEYS = frozenset({"max_parallel_items", "on_failure"})


def _reject_bool(value: Any, field_name: str) -> None:
    """Raise when *value* is a boolean masquerading as a number."""
    if isinstance(value, bool):
        raise InvalidResourceError(f"{field_name} must not be a boolean, got {value!r}")


def _require_int(value: Any, field_name: str, *, minimum: int) -> int:
    """Return *value* as a validated integer ``>= minimum``."""
    _reject_bool(value, field_name)
    if not isinstance(value, int):
        raise InvalidResourceError(f"{field_name} must be an integer >= {minimum}, got {value!r}")
    if value < minimum:
        raise InvalidResourceError(f"{field_name} must be >= {minimum}, got {value!r}")
    return value


def _check_unknown(mapping: Mapping[str, Any], allowed: frozenset[str], *, what: str) -> None:
    """Raise when *mapping* carries a key outside *allowed*."""
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise InvalidResourceError(
            f"{what} carries unknown field(s) {unknown}; allowed fields are {sorted(allowed)}"
        )


def _memory_bytes(value: Any, field_name: str) -> int:
    """Parse *value* through the memory authority into positive bytes."""
    _reject_bool(value, field_name)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidResourceError(f"{field_name} must be finite, got {value!r}")
        raise InvalidResourceError(
            f"{field_name} must be an integer byte count or a suffixed string "
            f"such as '16GB', got {value!r}"
        )
    try:
        amount = parse_memory_bytes(value)
    except InvalidResourceError as exc:
        raise InvalidResourceError(f"{field_name} is invalid: {exc}") from exc
    if amount <= 0:
        raise InvalidResourceError(f"{field_name} must be > 0 bytes, got {value!r}")
    return amount


def _coerce_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    """Return *value* as a mapping, or raise."""
    if not isinstance(value, Mapping):
        raise InvalidResourceError(f"{field_name} must be a mapping, got {type(value).__name__}")
    return value


def _profile_name(raw: Any) -> str:
    """Return the validated machine profile name."""
    if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
        raise InvalidResourceError("profile 'name' must be a non-empty, trimmed string")
    return raw


def _operational_record(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate operational profile fields into a provenance-only record."""
    record: dict[str, Any] = {}
    if "executable" in profile:
        executable = profile["executable"]
        if isinstance(executable, Mapping):
            if not executable or any(
                not isinstance(program, str)
                or not program.strip()
                or not isinstance(path, str)
                or not path.strip()
                for program, path in executable.items()
            ):
                raise InvalidResourceError(
                    "profile 'executable' mapping must map non-empty program names to paths"
                )
            record["executable"] = dict(executable)
        elif executable is not None and (not isinstance(executable, str) or not executable.strip()):
            raise InvalidResourceError("profile 'executable' must be a non-empty string or None")
        else:
            record["executable"] = executable
    if "env" in profile:
        env = profile["env"]
        if env is not None and not isinstance(env, Mapping):
            raise InvalidResourceError("profile 'env' must be a mapping of strings or None")
        if isinstance(env, Mapping):
            for key, item in env.items():
                if not isinstance(key, str) or not isinstance(item, str):
                    raise InvalidResourceError("profile 'env' must map strings to strings")
            record["env"] = dict(env)
        else:
            record["env"] = None
    for key in ("binding_id", "remote", "remote_target", "target", "sandbox"):
        if key in profile:
            locator = profile[key]
            if locator is not None and (not isinstance(locator, str) or not locator.strip()):
                raise InvalidResourceError(f"profile {key!r} must be a non-empty string or None")
            record[key] = locator
    targets = {record[key] for key in ("target", "remote", "remote_target") if record.get(key)}
    if len(targets) > 1:
        raise InvalidResourceError("profile target/remote/remote_target aliases conflict")
    if targets:
        record["target"] = next(iter(targets))
    record.pop("remote", None)
    record.pop("remote_target", None)
    if "allowed_executables" in profile:
        allowed = profile["allowed_executables"]
        if not isinstance(allowed, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in allowed
        ):
            raise InvalidResourceError(
                "profile 'allowed_executables' must be a list of non-empty strings"
            )
        record["allowed_executables"] = list(allowed)
    if "walltime_seconds" in profile:
        record["walltime_seconds"] = _require_int(
            profile["walltime_seconds"], "profile 'walltime_seconds'", minimum=1
        )
    return record


def resolve_machine_resources(
    profile: Mapping[str, Any],
    resources: Mapping[str, Any],
    scheduler: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a per-item request against a machine capacity profile.

    Parameters
    ----------
    profile :
        Machine capacity plus operational identity.  Required capacity keys
        are ``name``, ``total_cores`` (integer ``>= 1``), and the
        ``"total_memory"`` key in any form the memory authority accepts
        (integer bytes or a suffixed string such as ``"16GB"``).  The key
        is a plain string: it is never bound to a Python variable, per the
        architecture vocabulary gate.  Operational keys (``executable`` as
        a plain path string or as a per-program mapping of program names
        to paths, ``binding_id``, ``env``, ``remote``/``remote_target``/
        ``target`` endpoint aliases, ``sandbox``,
        ``allowed_executables``, ``walltime_seconds``) are validated and
        carried into ``provenance`` only; the target aliases canonicalize
        to ``target`` and conflict with each other when they disagree.
        Any other key fails closed.
    resources :
        Per-item scientific request in the existing V4 wire shape:
        ``cores_per_item`` (integer ``>= 1``) and ``memory_per_item``
        (integer bytes or suffixed string, ``> 0``).  Both dimensions are
        required: an absent dimension is never filled from scientific
        defaults.  Any other key fails closed.
    scheduler :
        Optional scheduler override in the existing V4 wire shape:
        ``max_parallel_items`` (explicit width, ``>= 1`` and within the
        capacity-derived limit) and ``on_failure`` (``"continue"`` or
        ``"fail_fast"``, preserved verbatim).  ``None`` means no override.

    Returns
    -------
    dict
        ``{"resources", "scheduler", "provenance"}`` where ``resources``
        and ``scheduler`` satisfy the strict V4 wire shapes (drop-in for a
        step or the run global) and ``provenance`` records the canonical
        machine resolution (capacities, request, slot limits, override
        flag, operational record, version).

    Raises
    ------
    InvalidResourceError
        On unknown fields, booleans, non-finite numbers, non-positive
        capacities, an unresolved request, an oversized request (zero
        slots), or an out-of-capacity explicit override.
    """
    profile_mapping = _coerce_mapping(profile, "profile")
    _check_unknown(profile_mapping, _PROFILE_KEYS, what="profile")
    request_mapping = _coerce_mapping(resources, "resources")
    _check_unknown(request_mapping, _REQUEST_KEYS, what="resources")
    override_mapping = _coerce_mapping(scheduler, "scheduler") if scheduler is not None else {}
    _check_unknown(override_mapping, _SCHEDULER_KEYS, what="scheduler")

    machine_name = _profile_name(profile_mapping.get("name"))
    if "total_cores" not in profile_mapping:
        raise InvalidResourceError("profile must declare 'total_cores'")
    if "total_memory" not in profile_mapping:
        raise InvalidResourceError("profile must declare the 'total_memory' key")
    capacity_cores = _require_int(
        profile_mapping["total_cores"], "profile 'total_cores'", minimum=1
    )
    capacity_bytes = _memory_bytes(profile_mapping["total_memory"], "profile 'total_memory'")

    if "cores_per_item" not in request_mapping:
        raise InvalidResourceError(
            "resources must declare 'cores_per_item'; the request is never defaulted"
        )
    if "memory_per_item" not in request_mapping:
        raise InvalidResourceError(
            "resources must declare 'memory_per_item'; the request is never defaulted"
        )
    request_cores = _require_int(request_mapping["cores_per_item"], "'cores_per_item'", minimum=1)
    request_bytes = _memory_bytes(request_mapping["memory_per_item"], "'memory_per_item'")

    cpu_slots = capacity_cores // request_cores
    mem_slots = capacity_bytes // request_bytes
    slot_limit = min(cpu_slots, mem_slots)
    if slot_limit < 1:
        raise InvalidResourceError(
            f"request ({request_cores} cores, {request_bytes} bytes per item) exceeds "
            f"machine capacity ({capacity_cores} cores, {capacity_bytes} bytes); "
            "oversized requests fail instead of clamping"
        )

    explicit_override = False
    if "max_parallel_items" in override_mapping:
        explicit_override = True
        width = _require_int(
            override_mapping["max_parallel_items"], "'max_parallel_items'", minimum=1
        )
        if width > slot_limit:
            raise InvalidResourceError(
                f"explicit 'max_parallel_items' {width} exceeds the capacity limit "
                f"{slot_limit} (cpu slots {cpu_slots}, memory slots {mem_slots})"
            )
    else:
        width = slot_limit

    on_failure: str | None = None
    if "on_failure" in override_mapping:
        raw_failure = override_mapping["on_failure"]
        if not isinstance(raw_failure, str) or raw_failure not in (
            OnFailure.CONTINUE.value,
            OnFailure.FAIL_FAST.value,
        ):
            raise InvalidResourceError(
                f"'on_failure' must be one of "
                f"{[item.value for item in OnFailure]}, got {raw_failure!r}"
            )
        on_failure = raw_failure

    declared_memory = request_mapping["memory_per_item"]
    if isinstance(declared_memory, str):
        wire_memory: str | int = declared_memory.strip()
    else:
        wire_memory = request_bytes

    wire_resources: dict[str, Any] = {
        "cores_per_item": request_cores,
        "memory_per_item": wire_memory,
    }
    wire_scheduler: dict[str, Any] = {"max_parallel_items": width}
    if on_failure is not None:
        wire_scheduler["on_failure"] = on_failure

    provenance: dict[str, Any] = {
        "profile": machine_name,
        "capacity_cores": capacity_cores,
        "capacity_bytes": capacity_bytes,
        "request_cores": request_cores,
        "request_bytes": request_bytes,
        "cpu_slots": cpu_slots,
        "mem_slots": mem_slots,
        "max_parallel_items": width,
        "explicit_override": explicit_override,
        "on_failure": on_failure,
        "operational": _operational_record(profile_mapping),
        "version": MACHINE_RESOLUTION_VERSION,
    }
    return {"resources": wire_resources, "scheduler": wire_scheduler, "provenance": provenance}
