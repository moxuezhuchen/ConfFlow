#!/usr/bin/env python3

"""Prepare the immutable inputs and execution graph for a workflow run."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..config.models import load_workflow_model
from ..core.utils import validate_xyz_file
from .dag import build_step_graph, topo_order
from .step_naming import build_step_dir_name_map

__all__ = ["PreparedWorkflow", "prepare_workflow"]


@dataclass(frozen=True)
class PreparedWorkflow:
    """Validated workflow inputs and deterministic execution-plan data."""

    input_files: list[str]
    original_inputs: list[str]
    global_config: dict[str, Any]
    steps: list[dict[str, Any]]
    predecessors: dict[str, list[str]]
    by_step_name: dict[str, dict[str, Any]]
    execution_order: list[str]
    terminal_steps: list[str]
    step_dirnames: list[str]
    step_index_by_name: dict[str, int]
    name_to_dirname: dict[str, str]


def prepare_workflow(
    input_xyz: Sequence[str],
    config_file: str,
    original_input_files: Sequence[str] | None = None,
) -> PreparedWorkflow:
    """Validate workflow inputs and build its deterministic execution plan."""
    input_files = [os.path.abspath(x) for x in input_xyz]
    original_inputs = (
        [os.path.abspath(x) for x in original_input_files] if original_input_files else input_files
    )
    for fp in input_files:
        if not os.path.exists(fp):
            raise FileNotFoundError(f"Input file does not exist: {fp}")
        validate_xyz_file(fp, strict=True)

    cfg = load_workflow_model(config_file).as_legacy_shape()
    global_config = cfg["global"]
    steps = cfg["steps"]

    # Explicit DAG mode is selected by the presence of an ``inputs`` field on
    # any step. In that mode, steps without the field are independent roots;
    # they do not inherit a predecessor from their list position. This keeps
    # mixed configurations deterministic and makes the legacy fallback
    # unambiguous: only a workflow with no ``inputs`` fields is linear.
    raw_predecessors, by_step_name, declared_inputs = build_step_graph(steps)
    explicit_inputs = any("inputs" in step for step in steps)
    if explicit_inputs:
        predecessors = raw_predecessors
    else:
        ordered_names = list(by_step_name)
        predecessors = {
            name: ([ordered_names[index - 1]] if index else [])
            for index, name in enumerate(ordered_names)
        }
    del declared_inputs
    execution_order = [name for wave in topo_order(predecessors) for name in wave]
    if explicit_inputs:
        predecessor_names = {
            predecessor
            for step_predecessors in predecessors.values()
            for predecessor in step_predecessors
        }
        terminal_steps = [name for name in predecessors if name not in predecessor_names]
    else:
        terminal_steps = [execution_order[-1]]

    step_dirnames, _ = build_step_dir_name_map(steps)
    step_index_by_name = {name: index for index, name in enumerate(by_step_name)}
    name_to_dirname = {name: step_dirnames[index] for name, index in step_index_by_name.items()}

    return PreparedWorkflow(
        input_files=input_files,
        original_inputs=original_inputs,
        global_config=global_config,
        steps=steps,
        predecessors=predecessors,
        by_step_name=by_step_name,
        execution_order=execution_order,
        terminal_steps=terminal_steps,
        step_dirnames=step_dirnames,
        step_index_by_name=step_index_by_name,
        name_to_dirname=name_to_dirname,
    )
