#!/usr/bin/env python3

"""Compatibility re-export of the canonical workflow graph primitives.

The explicit-DAG helpers now live in :mod:`confflow.config.canonical.workflow`,
which owns workflow graph semantics. This module keeps the historical
``confflow.workflow.dag.explicit`` import path working unchanged, including its
``build_step_graph`` / ``topo_order`` names, so existing callers and tests do not
have to move.
"""

from __future__ import annotations

from ...config.canonical.workflow import build_step_graph, topo_order

__all__ = [
    "build_step_graph",
    "topo_order",
]
