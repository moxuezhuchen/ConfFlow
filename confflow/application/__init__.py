"""Application-level services that are independent of CLI, agent, and storage adapters.

The historical ``from confflow.application import ExecutionService`` surface is
preserved lazily (PEP 562): importing :mod:`confflow.application` no longer
executes the execution-service package eagerly, so importing
``confflow.application.v4_run`` does not drag the durable-service aggregate
(or its dev fixtures) into the V4 runtime closure.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ExecutionService": (".execution", "ExecutionService"),
}

__all__ = [*sorted(_LAZY_EXPORTS)]


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(export[0], package=__name__)
    value = getattr(module, export[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
