#!/usr/bin/env python3

"""Generic intent resources helpers (L1-C1 mechanical move).

Moved verbatim from :mod:`confflow.producer.intent.compiler`:
:func:`_wire_resources`, :func:`_wire_scheduler`,
:func:`_apply_machine_profile`, and :func:`_apply_checkpoints` (generic
orchestration only; Gaussian science details stay single-owner in G1 and are
not mixed here). Bodies, exception order/messages, resource/scheduler/
checkpoint compile results are unchanged. Shared light errors come from
:mod:`.common` (no back-import of ``compiler``). Heavy lanes
(``producer.machine``, ``producer.checkpoints``, ``workflow.v4.schema``)
stay function-local lazy imports, preserved verbatim.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .common import IntentCompilationError, _fail


def _wire_resources(step: Mapping[str, Any], step_id: str) -> dict[str, Any] | None:
    resources = step.get("resources")
    if resources is None:
        return None
    if not isinstance(resources, Mapping):
        raise _fail(f"step {step_id!r} resources must be a mapping", step_id=step_id)
    allowed = {"cores_per_item", "memory_per_item"}
    unknown = sorted(set(resources) - allowed)
    if unknown:
        raise _fail(
            f"step {step_id!r} resources carry unknown members: {', '.join(unknown)}",
            step_id=step_id,
        )
    out = {key: copy.deepcopy(resources[key]) for key in allowed if key in resources}
    return out or None


def _wire_scheduler(step: Mapping[str, Any], step_id: str) -> dict[str, Any] | None:
    scheduler = step.get("scheduler")
    if scheduler is None:
        return None
    if not isinstance(scheduler, Mapping):
        raise _fail(f"step {step_id!r} scheduler must be a mapping", step_id=step_id)
    allowed = {"max_parallel_items", "on_failure"}
    unknown = sorted(set(scheduler) - allowed)
    if unknown:
        raise _fail(
            f"step {step_id!r} scheduler carries unknown members: {', '.join(unknown)}",
            step_id=step_id,
        )
    out = {key: copy.deepcopy(scheduler[key]) for key in allowed if key in scheduler}
    return out or None


def _apply_machine_profile(
    wire_steps: list[dict[str, Any]],
    machine_profile: Mapping[str, Any],
    *,
    intent_registry: Any = None,
) -> dict[str, dict[str, Any]]:
    """Resolve operational resources via the resources lane (late import).

    The resolver's canonical ``provenance["operational"]`` record is the
    authority for every execution field: per-program ``executable``
    selection, merged ``env``, canonical ``target``, ``binding_id``,
    ``sandbox``, ``allowed_executables``, and ``walltime_seconds``.  Only
    allowed ``ExecutionModel`` members are projected; explicit step
    ``execution`` entries win over profile values per key.

    Program location comes from the same assembly channel
    (``wire_block_key_for_executor``) with the compile-time
    ``intent_registry`` instance. The two-positional call stays valid
    (``intent_registry`` defaults to ``None`` → builtin defaults); compile
    passes its unique registry explicitly. Assembly import/query failures
    fail closed with step context and never continue with a guessed
    default that would silently drop ``executable``.
    """
    try:
        from ..machine import resolve_machine_resources
    except ImportError as exc:
        raise _fail(
            "machine_profile was provided but producer.machine is not available yet; "
            "coordinate with the resources lane instead of guessing"
        ) from exc
    if not isinstance(machine_profile, Mapping):
        raise _fail("machine_profile must be a mapping")
    profile_name = machine_profile.get("name", "machine")
    try:
        from ...workflow.v4.schema import (
            DEFAULT_CORES_PER_ITEM,
            DEFAULT_MEMORY_PER_ITEM,
        )
    except ImportError:
        _fallback_cores: Any = 1
        _fallback_memory: Any = "1GiB"
    else:
        _fallback_cores = DEFAULT_CORES_PER_ITEM
        _fallback_memory = DEFAULT_MEMORY_PER_ITEM
    provenance: dict[str, dict[str, Any]] = {}
    for step in wire_steps:
        step_id = str(step.get("id"))
        request = step.get("resources")
        if request is None:
            request = {
                "cores_per_item": _fallback_cores,
                "memory_per_item": _fallback_memory,
            }
        elif isinstance(request, Mapping):
            # A partial per-item request means the same as the no-profile
            # wire: absent dimensions inherit the strict schema defaults.
            # Fill them from the single authority before the helper so a
            # partial request resolves to scientifically equivalent
            # resources (and seeds) with or without a profile.
            filled = dict(request)
            if filled.get("cores_per_item") is None:
                filled["cores_per_item"] = copy.deepcopy(_fallback_cores)
            if filled.get("memory_per_item") is None:
                filled["memory_per_item"] = copy.deepcopy(_fallback_memory)
            request = filled
        try:
            resolved = resolve_machine_resources(
                machine_profile,
                request,
                step.get("scheduler"),
            )
        except Exception as exc:
            raise _fail(
                f"step {step_id!r}: machine resolution failed: {exc}",
                step_id=step_id,
            ) from exc
        if not isinstance(resolved, Mapping):
            raise _fail(f"step {step_id!r}: machine resolver returned a non-mapping")
        if resolved.get("resources") is not None:
            step["resources"] = copy.deepcopy(dict(resolved["resources"]))
        if resolved.get("scheduler") is not None:
            step["scheduler"] = copy.deepcopy(dict(resolved["scheduler"]))
        record = resolved.get("provenance")
        operational = (
            copy.deepcopy(dict(record.get("operational", {})))
            if isinstance(record, Mapping)
            else {}
        )
        # L1-A2a v2: program extraction via the same assembly channel.
        # The strict V4 builtin wire keys own the block location; query by
        # step executor with the compile-time registry. Import/query
        # failures fail closed (no except-to-None-and-continue); unknown
        # executors legitimately yield None and keep program None.
        try:
            from .capabilities.registry import (
                wire_block_key_for_executor as _assembly_block_key,
            )
        except Exception as exc:
            raise _fail(
                f"step {step_id!r}: machine program binding unavailable: {exc}",
                step_id=step_id,
            ) from exc
        try:
            _block_key = _assembly_block_key(intent_registry, str(step.get("executor")))
        except IntentCompilationError:
            raise
        except Exception as exc:
            raise _fail(
                f"step {step_id!r}: machine program binding broken: {exc}",
                step_id=step_id,
            ) from exc
        program: str | None = None
        if isinstance(_block_key, str) and _block_key:
            _owner_block = step.get(_block_key)
            if isinstance(_owner_block, Mapping):
                raw_program = _owner_block.get("program")
                program = raw_program if isinstance(raw_program, str) else None
        explicit = step.get("execution")
        explicit_map = dict(explicit) if isinstance(explicit, Mapping) else {}
        if "target" in explicit_map:
            raise _fail(
                f"step {step_id!r}: execution 'target' is retired with remote "
                "delivery (R2.2); declare no target"
            )
        execution: dict[str, Any] = {}
        execution["binding_id"] = (
            explicit_map.get("binding_id")
            or operational.get("binding_id")
            or f"machine-{profile_name}"
        )
        raw_executable = operational.get("executable")
        selected: Any = None
        if isinstance(raw_executable, Mapping) and program is not None:
            selected = raw_executable.get(program, raw_executable.get("default"))
        elif isinstance(raw_executable, str):
            selected = raw_executable
        execution["executable"] = explicit_map.get("executable", selected)
        raw_profile_env = operational.get("env")
        profile_env: dict[str, Any] = (
            dict(raw_profile_env) if isinstance(raw_profile_env, Mapping) else {}
        )
        raw_explicit_env = explicit_map.get("env")
        explicit_env: dict[str, Any] = (
            dict(raw_explicit_env) if isinstance(raw_explicit_env, Mapping) else {}
        )
        merged_env = {**profile_env, **explicit_env}
        execution["env"] = (
            explicit_map["env"] if "env" in explicit_map and not merged_env else merged_env
        )
        if not execution["env"]:
            execution.pop("env", None)
        for key in ("sandbox", "allowed_executables", "walltime_seconds"):
            if key in explicit_map:
                execution[key] = copy.deepcopy(explicit_map[key])
            elif operational.get(key) is not None:
                execution[key] = copy.deepcopy(operational[key])
        for key in ("executable", "sandbox", "allowed_executables", "walltime_seconds"):
            if execution.get(key) is None:
                execution.pop(key, None)
        step["execution"] = execution
        provenance[step_id] = copy.deepcopy(dict(record)) if isinstance(record, Mapping) else {}
    return provenance


def _apply_checkpoints(
    wire_document: dict[str, Any], intents: Mapping[str, dict[str, Any]]
) -> None:
    """Wire semantic checkpoint intents through the checkpoints lane."""
    try:
        from ..checkpoints import wire_checkpoint_reuse
    except ImportError as exc:
        raise _fail(
            "reuse_checkpoint was declared but producer.checkpoints is not available yet"
        ) from exc
    edges = [
        (target_id, dict(spec)) for target_id, spec in intents.items() if isinstance(spec, Mapping)
    ]
    for step in wire_document.get("steps", []):
        if isinstance(step, dict):
            for key in [key for key in step if key.startswith("_")]:
                step.pop(key, None)
    for target_id, spec in edges:
        try:
            updated = wire_checkpoint_reuse(
                wire_document,
                str(target_id),
                str(spec["step"]),
                mode=str(spec["mode"]),
                allow_method_change=bool(spec.get("allow_method_change", False)),
            )
        except IntentCompilationError:
            raise
        except Exception as exc:
            raise _fail(
                f"step {target_id!r}: checkpoint wiring failed: {exc}",
                step_id=str(target_id),
            ) from exc
        if not isinstance(updated, Mapping):
            raise _fail(
                f"step {target_id!r}: checkpoint helper returned a non-mapping",
                step_id=str(target_id),
            )
        wire_document.clear()
        wire_document.update(copy.deepcopy(dict(updated)))


# Mechanical compat: old import path was ``confflow.producer.intent.compiler``.
# Same objects are re-exported there (``is`` holds); keep the old module name
# so ``__module__``/pickle/monkeypatch paths observe the pre-move location.
_wire_resources.__module__ = "confflow.producer.intent.compiler"
_wire_scheduler.__module__ = "confflow.producer.intent.compiler"
_apply_machine_profile.__module__ = "confflow.producer.intent.compiler"
_apply_checkpoints.__module__ = "confflow.producer.intent.compiler"
