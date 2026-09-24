#!/usr/bin/env python3
"""Legacy DAG migration tests for ConfFlow 1.4.4.

Pins the deprecation path introduced by the M2 D6 follow-up:
* ``workflow/dag/legacy.py`` owns the legacy ``DAGStep`` / ``DAGGraph`` /
  ``WorkflowDAG`` implementations.
* ``workflow/dag/__init__.py`` must trigger ``DeprecationWarning`` once
  per name when the legacy classes are accessed through it (direct,
  ``from ... import`` and star import).
* The explicit API ``build_step_graph`` / ``topo_order`` must continue
  to be importable from ``confflow.workflow.dag`` without any warning.
* The engine must continue to rely solely on the explicit DAG API.
"""

from __future__ import annotations

import sys
import warnings
from types import ModuleType

import pytest

import confflow.workflow.dag as dag_pkg
import confflow.workflow.dag.explicit as explicit_module
import confflow.workflow.dag.legacy as legacy_module
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.dag import build_step_graph, topo_order


def test_legacy_module_owns_public_classes():
    """The legacy classes live in workflow.dag.legacy."""
    assert hasattr(legacy_module, "DAGStep")
    assert hasattr(legacy_module, "DAGGraph")
    assert hasattr(legacy_module, "WorkflowDAG")


def test_explicit_module_exports_canonical_api():
    """The explicit DAG API lives in workflow.dag.explicit."""
    assert hasattr(explicit_module, "build_step_graph")
    assert hasattr(explicit_module, "topo_order")


def test_explicit_api_is_re_exported_without_warning():
    """``build_step_graph`` and ``topo_order`` are exposed without deprecation."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert dag_pkg.build_step_graph is build_step_graph
        assert dag_pkg.topo_order is topo_order
    assert all(
        not issubclass(w.category, DeprecationWarning) for w in caught
    ), "Re-exporting the explicit API must not emit DeprecationWarning"


def test_direct_legacy_import_triggers_deprecation_warning():
    """Importing a legacy class through the dag package emits DeprecationWarning."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        legacy_graph_cls = dag_pkg.DAGGraph  # noqa: F841

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, "expected a DeprecationWarning"
    msg = str(deprecations[0].message)
    assert "DAGGraph" in msg
    assert "confflow.workflow.dag.explicit" in msg


def test_star_import_does_not_inject_legacy_silently():
    """``from confflow.workflow.dag import *`` keeps the explicit API and does not silently inject the legacy classes."""
    star_module_name = "_stage1_test_dag_star"
    star_module = ModuleType(star_module_name)
    star_module.__dict__["__name__"] = star_module_name
    sys.modules[star_module_name] = star_module
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            exec("from confflow.workflow.dag import *", star_module.__dict__)  # noqa: S102
        assert "build_step_graph" in star_module.__dict__
        assert "topo_order" in star_module.__dict__
        assert "DAGStep" not in star_module.__dict__
        assert "DAGGraph" not in star_module.__dict__
        assert "WorkflowDAG" not in star_module.__dict__
        assert not any(issubclass(w.category, DeprecationWarning) for w in caught)
    finally:
        sys.modules.pop(star_module_name, None)


def test_unknown_attribute_raises_attribute_error():
    """Asking for an unknown attribute surfaces AttributeError, not the warning path."""
    with pytest.raises(AttributeError):
        _ = dag_pkg.NonExistentName  # type: ignore[attr-defined]


def test_explicit_planning_path_does_not_use_legacy():
    """The typed planning path builds the graph via the explicit DAG API.

    R1 moved the graph construction out of ``workflow.plan`` into the canonical
    V2 adapter (:mod:`confflow.config.canonical.v2_adapter`), which now owns
    ``build_step_graph`` / ``topo_order`` for the planning path. ``workflow.plan``
    delegates to that adapter. The invariant is unchanged: neither module may
    reach for the legacy ``DAGGraph`` / ``DAGStep`` / ``WorkflowDAG`` classes.
    """
    import inspect

    from confflow.config.canonical import v2_adapter as adapter_module
    from confflow.workflow import plan as plan_module

    adapter_source = inspect.getsource(adapter_module)
    plan_source = inspect.getsource(plan_module)

    assert "build_step_graph" in adapter_source
    assert "topo_order" in adapter_source
    assert "to_canonical_workflow" in plan_source

    for source in (plan_source, adapter_source):
        for name in ("DAGGraph", "DAGStep", "WorkflowDAG"):
            assert (
                f"import {name}" not in source
            ), f"typed planner must not import legacy {name!r}; rely on the explicit API"


def test_legacy_classes_have_correct_behavior_regression():
    """The migrated legacy classes still behave as before the move."""
    graph = legacy_module.DAGGraph()
    graph.add_step(legacy_module.DAGStep(name="a", step_type="confgen"))
    graph.add_step(legacy_module.DAGStep(name="b", step_type="calc", depends_on=["a"]))
    graph.add_edge("a", "b")
    assert graph.topological_sort() == ["a", "b"]

    dag = legacy_module.WorkflowDAG(
        steps=[
            {"name": "gen", "type": "confgen"},
            {
                "name": "opt",
                "type": "calc",
                "depends_on": ["gen"],
                "when": "prev.failed_count == 0",
            },
        ]
    )
    assert dag.validate() == []
    assert dag.topological_sort() == ["gen", "opt"]
    decisions = dag.evaluate_conditions({"opt": {"failed_count": 0}})
    assert decisions["opt"] is True


def test_build_step_graph_rejects_boolean_step_name():
    with pytest.raises(ConfFlowError, match="parsed as a boolean"):
        build_step_graph([{"name": False, "type": "confgen", "params": {}}])


def test_build_step_graph_rejects_boolean_inputs_entry():
    with pytest.raises(ConfFlowError, match="parsed as a boolean"):
        build_step_graph(
            [
                {"name": "gen", "type": "confgen", "params": {}},
                {"name": "calc", "type": "calc", "inputs": [False], "params": {}},
            ]
        )


def test_build_step_graph_accepts_quoted_off_name():
    predecessors, by_name, _ = build_step_graph(
        [
            {"name": "off", "type": "confgen", "params": {}},
            {"name": "downstream", "type": "calc", "inputs": ["off"], "params": {}},
        ]
    )
    assert set(by_name) == {"off", "downstream"}
    assert predecessors["downstream"] == ["off"]
