#!/usr/bin/env python3
"""Tests for the workflow DAG introspection and conditional execution module.

The implementation lives under :mod:`confflow.workflow.dag.legacy`. The
public ``confflow.workflow.dag`` package only re-exports the explicit DAG
API (v1.4.3+) and warns when the legacy classes are accessed; importing
them straight from ``legacy`` avoids the deprecation warning while the
classes are still part of the v1.4.x compatibility window.
"""

from __future__ import annotations

# Import legacy classes directly from their canonical home. These names are
# still re-exported via ``confflow.workflow.dag`` for backward compatibility,
# but accessing them through the package surfaces a DeprecationWarning.
from confflow.workflow.dag.legacy import DAGGraph, DAGStep


class TestDAGGraphDebug:
    def test_topological_sort_fan_in(self):
        """Debug fan_in: check adjacency and in_degree."""
        graph = DAGGraph()
        graph.add_step(DAGStep(name="conf1", step_type="confgen"))
        graph.add_step(DAGStep(name="conf2", step_type="confgen"))
        graph.add_step(DAGStep(name="calc", step_type="calc", depends_on=["conf1", "conf2"]))
        graph.add_edge("conf1", "calc")
        graph.add_edge("conf2", "calc")
        # Check adjacency
        adj = {s.name: [] for s in graph.steps}
        indeg = {s.name: 0 for s in graph.steps}
        for src, dst in graph.edges:
            adj[src].append(dst)
            indeg[dst] += 1
        print(f"edges: {graph.edges}")
        print(f"adj: {adj}")
        print(f"indeg: {indeg}")
        sorted_names = graph.topological_sort()
        print(f"sorted: {sorted_names}")
        assert sorted_names[-1] == "calc", f"FAIL: expected calc last, got {sorted_names}"

    def test_topological_sort_fan_out(self):
        """Debug fan_out."""
        graph = DAGGraph()
        graph.add_step(DAGStep(name="gen", step_type="confgen"))
        graph.add_step(DAGStep(name="opt1", step_type="calc", depends_on=["gen"]))
        graph.add_step(DAGStep(name="opt2", step_type="calc", depends_on=["gen"]))
        graph.add_step(DAGStep(name="merge", step_type="calc", depends_on=["opt1", "opt2"]))
        graph.add_edge("gen", "opt1")
        graph.add_edge("gen", "opt2")
        graph.add_edge("opt1", "merge")
        graph.add_edge("opt2", "merge")
        sorted_names = graph.topological_sort()
        print(f"sorted: {sorted_names}")
        assert sorted_names[-1] == "merge", f"FAIL: expected merge last, got {sorted_names}"
