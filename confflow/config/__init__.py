#!/usr/bin/env python3
"""ConfFlow typed configuration models (lazy compatibility exports).

The concrete V2 typed models live in :mod:`confflow.config.canonical.types`,
exposed through the :mod:`confflow.config.models` compatibility facade.
Importing this package must not eager-load the canonical configuration
runtime: the V4 producer contract only needs the dependency-free identifiers
from :mod:`confflow.config.contract_schemas`.  Every historical
``from confflow.config import X`` name is resolved lazily (PEP 562) and keeps
working unchanged.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "CalcStepParams": (".models", "CalcStepParams"),
    "CleanupOptions": (".models", "CleanupOptions"),
    "ExecutionOptions": (".models", "ExecutionOptions"),
    "GlobalOptions": (".models", "GlobalOptions"),
    "ResourceOptions": (".models", "ResourceOptions"),
    "StepConfig": (".models", "StepConfig"),
    "TSOptions": (".models", "TSOptions"),
    "WorkflowConfig": (".models", "WorkflowConfig"),
    "load_workflow_model": (".models", "load_workflow_model"),
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
