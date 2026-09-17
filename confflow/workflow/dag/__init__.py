#!/usr/bin/env python3
"""Workflow DAG introspection and conditional execution - Phase 1b.

Production semantics live in :mod:`confflow.workflow.dag.explicit`; the
classes in :mod:`confflow.workflow.dag.legacy` are kept for one release
cycle of compatibility (v1.4.4 deprecated, earliest removal v1.5.0).
Accessing the legacy classes through this package emits a
``DeprecationWarning``; ``build_step_graph`` and ``topo_order`` are
re-exported without any warning so that ``from confflow.workflow.dag
import build_step_graph`` continues to work for engine callers.
"""

from __future__ import annotations

import warnings

from .explicit import build_step_graph, topo_order

__all__ = ["build_step_graph", "topo_order"]


_LEGACY_EXPORTS: dict[str, str] = {
    "DAGStep": "confflow.workflow.dag.legacy.DAGStep",
    "DAGGraph": "confflow.workflow.dag.legacy.DAGGraph",
    "WorkflowDAG": "confflow.workflow.dag.legacy.WorkflowDAG",
}


def _resolve_legacy(name: str):
    import importlib

    module_name, attr = _LEGACY_EXPORTS[name].rsplit(".", 1)
    return getattr(importlib.import_module(module_name, package=__name__), attr)


def __getattr__(name: str):
    if name in _LEGACY_EXPORTS:
        warnings.warn(
            (
                f"confflow.workflow.dag.{name} is deprecated; "
                "import confflow.workflow.dag.explicit for production semantics, "
                "or confflow.workflow.dag.legacy to silence this warning for the "
                "v1.4.x compatibility window. Removal is scheduled no earlier than v1.5.0."
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        value = _resolve_legacy(name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:  # pragma: no cover - tab completion
    return sorted(set(list(_LEGACY_EXPORTS) + __all__))
