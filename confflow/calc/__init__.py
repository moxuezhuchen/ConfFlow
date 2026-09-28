#!/usr/bin/env python3

"""ConfFlow calc sub-package.

Legacy calculation tooling: the ``confts`` CLI and the typed
``CalcStepRunner``/``TaskRunner`` execution path.  It is deliberately outside
the formal V4 runtime closure — no V4 production root may import it.

The public names below are resolved lazily (PEP 562), so importing a leaf
module (for example :mod:`confflow.calc.result`, which the refine tooling
uses) no longer drags in the whole execution runtime (executor, task runner,
policies, database).  Accessing a public name still imports the concrete
module exactly as before: ``from confflow.calc import CalcStepRunner`` and
``confflow.calc.TaskRunner`` keep working.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ResultsDB": (".db.database", "ResultsDB"),
    "ResourceMonitor": (".resources", "ResourceMonitor"),
    "parse_output": (".components.parser", "parse_output"),
    "handle_backups": (".components.executor", "handle_backups"),
    "CalcStepRequest": (".runner", "CalcStepRequest"),
    "CalcStepResult": (".runner", "CalcStepResult"),
    "CalcStepRunner": (".runner", "CalcStepRunner"),
    "TaskRunner": (".components.task_runner", "TaskRunner"),
    "get_itask": (".setup", "get_itask"),
    "parse_iprog": (".setup", "parse_iprog"),
    "setup_logging": (".setup", "setup_logging"),
    "get_policy": (".policies", "get_policy"),
}

#: Submodules that stay reachable as attributes after ``import confflow.calc``
#: (``confflow.calc.runner`` etc.), matching the historical eager behavior.
_SUBMODULES = frozenset(
    {
        "analysis",
        "artifacts",
        "components",
        "constants",
        "db",
        "executor",
        "geometry",
        "policies",
        "psutil_compat",
        "rescue",
        "resources",
        "result",
        "result_writer",
        "run_services",
        "runner",
        "scan_ops",
        "setup",
        "task_execution",
    }
)

__all__ = [
    "ResultsDB",
    "ResourceMonitor",
    "parse_output",
    "handle_backups",
    "CalcStepRequest",
    "CalcStepResult",
    "CalcStepRunner",
    "TaskRunner",
    "get_itask",
    "parse_iprog",
    "setup_logging",
    "get_policy",
]


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is not None:
        module = importlib.import_module(export[0], package=__name__)
        value = getattr(module, export[1])
        globals()[name] = value
        return value
    if name in _SUBMODULES:
        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_SUBMODULES))
