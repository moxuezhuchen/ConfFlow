#!/usr/bin/env python3
"""Tests for the workflow DAG introspection and conditional execution module."""

from __future__ import annotations

import pytest
import sys, pathlib

# Always reload from source
_source_path = pathlib.Path("/opt/ConfFlow/confflow/workflow/dag/__init__.py")
assert _source_path.exists(), f"Source not found at {_source_path}"
_spec = __import__("importlib").util.spec_from_file_location("dag_src", _source_path)
assert _spec is not None
_dag_mod = __import__("importlib").util.module_from_spec(_spec)
_spec.loader.exec_module(_dag_mod)

DAGGraph = _dag_mod.DAGGraph
DAGStep = _dag_mod.DAGStep
WorkflowDAG = _dag_mod.WorkflowDAG


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
