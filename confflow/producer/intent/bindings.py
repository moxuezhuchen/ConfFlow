#!/usr/bin/env python3

"""Generic intent bindings helpers (L1-C1 mechanical move).

Moved verbatim from :mod:`confflow.producer.intent.compiler`:
:func:`_registry_input_ports` and :func:`_auto_bindings`. Bodies, exception
order, messages, and binding semantics are unchanged. Only the shared light
error :func:`_fail` is imported from :mod:`.common` (no back-import of
``compiler``, so no cycle). Top-level imports stay light
(``copy``/``collections.abc``/``typing`` + ``.common``); registry/solver
knowledge arrives only via the ``registry`` call argument.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .common import _fail


def _registry_input_ports(
    executor: str, adapter_name: str | None, registry: Any
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(required_ports, needs_explicit)`` from the real registry."""
    capability = registry.resolve_executor(executor)
    if capability.requires_adapter:
        if not adapter_name:
            raise _fail(f"execution_adapter is required for executor {executor!r}")
        adapter = registry.resolve_adapter(adapter_name)
        ports = list(adapter.input_ports)
    else:
        ports = list(capability.input_ports)
    required = [
        {
            "name": port.name,
            "kind": port.kind.value if hasattr(port.kind, "value") else str(port.kind),
        }
        for port in ports
        if port.is_required
    ]
    return required, False


def _auto_bindings(
    ordered_ids: list[str],
    by_id: dict[str, dict[str, Any]],
    wire_executors: dict[str, str],
    wire_adapters: dict[str, str | None],
    inputs: Mapping[str, Any],
    registry: Any,
) -> None:
    """Fill missing ``bindings`` with the linear-predecessor rule.

    Head step <- unique default run structure input; step N <- step N-1's
    ``structures`` output.  ``from`` overrides the predecessor.
    Anything else missing fails closed.
    """
    run_names = list(inputs)
    default_run = "structures" if "structures" in inputs else (run_names[0] if run_names else None)
    for position, step_id in enumerate(ordered_ids):
        step = by_id[step_id]
        if step.get("bindings") is not None:
            continue
        executor = wire_executors[step_id]
        adapter_name = wire_adapters.get(step_id)
        required, _ = _registry_input_ports(executor, adapter_name, registry)
        structure_ports = [port for port in required if port["kind"] == "structure"]
        if not structure_ports:
            step["bindings"] = {}
            continue
        predecessor = step.get("_from")
        source: dict[str, Any]
        if predecessor is None:
            if position == 0:
                if default_run is None:
                    raise _fail(
                        f"step {step_id!r} has no run input to bind",
                        step_id=step_id,
                    )
                if len(run_names) != 1:
                    raise _fail(
                        f"step {step_id!r} is ambiguous across run inputs "
                        f"{sorted(run_names)}; declare 'from: run:<input>'",
                        step_id=step_id,
                    )
                source = {"run": default_run}
                bindings = {
                    port["name"]: {"source": copy.deepcopy(source)} for port in structure_ports
                }
                step["bindings"] = bindings
                continue
            predecessor = ordered_ids[position - 1]
        if isinstance(predecessor, str) and predecessor.startswith("run:"):
            run_name = predecessor.split(":", 1)[1]
            if run_name not in inputs:
                raise _fail(
                    f"step {step_id!r} references unknown run input {run_name!r}",
                    step_id=step_id,
                )
            source = {"run": run_name}
        else:
            if predecessor not in by_id:
                raise _fail(
                    f"step {step_id!r} references unknown predecessor {predecessor!r}",
                    step_id=step_id,
                )
            if ordered_ids.index(str(predecessor)) >= position:
                raise _fail(
                    f"step {step_id!r} predecessor {predecessor!r} is not earlier in order",
                    step_id=step_id,
                )
            source = {"step": str(predecessor), "port": "structures"}
        bindings = {port["name"]: {"source": copy.deepcopy(source)} for port in structure_ports}
        step["bindings"] = bindings


# Mechanical compat: old import path was ``confflow.producer.intent.compiler``.
# Same objects are re-exported there (``is`` holds); keep the old module name
# so ``__module__``/pickle/monkeypatch paths observe the pre-move location.
_registry_input_ports.__module__ = "confflow.producer.intent.compiler"
_auto_bindings.__module__ = "confflow.producer.intent.compiler"
