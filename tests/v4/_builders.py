#!/usr/bin/env python3

"""Shared builders for ConfFlow V4 tests.

Pure helpers only: document dicts, domain records, diagnostics accessors, and
one test-only registry with a pure-passthrough structure transform contract.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from confflow.domain import (
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    Diagnostic,
    FrozenDict,
    ResultSet,
    ScientificResult,
    StructureRecord,
    StructureSet,
)
from confflow.execution import (
    ExecutorCapability,
    ExecutorContract,
    build_default_registry,
)
from confflow.workflow.v4 import (
    AssemblyResult,
    CompileResult,
    RunInputs,
    assemble_work_items,
    compile_workflow,
)

V4_SCHEMA = "confflow.workflow.v4"

ElementSpec = tuple[str, tuple[tuple[float, float, float], ...]]

_WATER: ElementSpec = (
    ("O", "H", "H"),
    ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0)),
)
_METHANE: ElementSpec = (
    ("C", "H", "H", "H", "H"),
    (
        (0.0, 0.0, 0.0),
        (0.63, 0.63, 0.63),
        (-0.63, -0.63, 0.63),
        (-0.63, 0.63, -0.63),
        (0.63, -0.63, -0.63),
    ),
)


def structure(
    structure_id: str,
    *,
    kind: str = "water",
    charge: int | None = 0,
    multiplicity: int | None = 1,
    group_key: str | None = None,
    offset: float = 0.0,
    role: str | None = None,
    lineage_root_id: str | None = None,
    parent_ids: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> StructureRecord:
    """Build a deterministic test structure record."""
    spec = _WATER if kind == "water" else _METHANE
    atoms, coordinates = spec
    shifted = tuple((x + offset, y + offset, z + offset) for x, y, z in coordinates)
    return StructureRecord(
        id=structure_id,
        atoms=atoms,
        coordinates=shifted,
        charge=charge,
        multiplicity=multiplicity,
        group_key=group_key,
        role=role,
        lineage_root_id=lineage_root_id,
        parent_ids=parent_ids,
        metadata=FrozenDict(metadata or {}),
    )


def structure_set(*structure_ids: str, **kwargs: Any) -> StructureSet:
    """Build a structure set from generated ids."""
    return StructureSet.of(*(structure(structure_id, **kwargs) for structure_id in structure_ids))


def checkpoint(
    subject_structure_id: str,
    *,
    artifact_id: str | None = None,
    role: str = "checkpoint",
    checksum_seed: str | None = None,
    producer_step_id: str | None = None,
) -> ArtifactRef:
    """Build a checkpoint artifact bound to a subject structure."""
    seed = checksum_seed if checksum_seed is not None else subject_structure_id
    checksum = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return ArtifactRef(
        id=artifact_id or f"chk_{subject_structure_id}",
        role=role,
        locator=ArtifactLocator.run_relative(f"steps/{producer_step_id or 'producer'}/{seed}.chk"),
        checksum="sha256:" + checksum,
        subject_structure_id=subject_structure_id,
        producer_step_id=producer_step_id,
    )


def checkpoint_set(*subject_structure_ids: str, **kwargs: Any) -> ArtifactSet:
    """Build a checkpoint set for the given subject ids."""
    return ArtifactSet.of(
        *(checkpoint(subject_id, **kwargs) for subject_id in subject_structure_ids)
    )


def energy_result(
    value: float,
    *,
    subject_structure_id: str | None = None,
    source_step_id: str | None = None,
) -> ScientificResult:
    """Build an energy result in canonical Hartree."""
    from confflow.domain import Unit

    return ScientificResult(
        kind="energy",
        value=value,
        unit=Unit.HARTREE,
        subject_structure_id=subject_structure_id,
        source_step_id=source_step_id,
    )


def run_inputs(
    *,
    structures: Mapping[str, StructureSet] | None = None,
    artifacts: Mapping[str, ArtifactSet] | None = None,
    results: Mapping[str, ResultSet] | None = None,
) -> RunInputs:
    """Build run inputs from plain mappings."""
    return RunInputs(
        structures=FrozenDict(structures or {}),
        artifacts=FrozenDict(artifacts or {}),
        results=FrozenDict(results or {}),
    )


def v4_doc(
    steps: list[dict[str, Any]],
    *,
    inputs: Mapping[str, Any] | None = None,
    global_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a raw V4 document mapping."""
    document: dict[str, Any] = {"schema": V4_SCHEMA, "steps": steps}
    if inputs is not None:
        document["inputs"] = dict(inputs)
    if global_config is not None:
        document["global"] = dict(global_config)
    return document


def calc_step(
    step_id: str,
    *,
    bindings: Mapping[str, Any] | None = None,
    program: str = "g16",
    role: str = "opt",
    adapter: str = "standard",
    profile: str = "standard",
    native: Mapping[str, Any] | None = None,
    checks: Iterable[str] = (),
    recovery: str = "none",
    seed: int | None = None,
    overrides: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
    label: str | None = None,
    completion: Mapping[str, Any] | None = None,
    resources: Mapping[str, Any] | None = None,
    scheduler: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a calculation step mapping."""
    step: dict[str, Any] = {
        "id": step_id,
        "executor": "calculation",
        "bindings": dict(bindings or {}),
        "calculation": {
            "program": program,
            "role": role,
            "execution_adapter": adapter,
            "result_profile": profile,
            "native": dict(native or {"keyword": "B3LYP/6-31G* opt"}),
            "checks": list(checks),
            "recovery": {"profile": recovery},
        },
    }
    calculation = step["calculation"]
    if seed is not None:
        calculation["seed"] = seed
    if overrides is not None:
        calculation["overrides"] = dict(overrides)
    if enabled is not None:
        step["enabled"] = enabled
    if label is not None:
        step["label"] = label
    if completion is not None:
        step["completion"] = dict(completion)
    if resources is not None:
        step["resources"] = dict(resources)
    if scheduler is not None:
        step["scheduler"] = dict(scheduler)
    if execution is not None:
        step["execution"] = dict(execution)
    return step


def confgen_step(
    step_id: str,
    *,
    bindings: Mapping[str, Any] | None = None,
    native: Mapping[str, Any] | None = None,
    seed: int | None = 42,
    overrides: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Build a confgen step mapping."""
    confgen: dict[str, Any] = {"native": dict(native or {"chains": ["1-2-3"]})}
    if seed is not None:
        confgen["seed"] = seed
    if overrides is not None:
        confgen["overrides"] = dict(overrides)
    step: dict[str, Any] = {
        "id": step_id,
        "executor": "confgen",
        "bindings": dict(bindings or {}),
        "confgen": confgen,
    }
    if enabled is not None:
        step["enabled"] = enabled
    return step


def transform_step(
    step_id: str,
    *,
    bindings: Mapping[str, Any] | None = None,
    kind: str = "refine",
    native: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Build an explicit structure-transform step mapping."""
    step: dict[str, Any] = {
        "id": step_id,
        "executor": "structure_transform",
        "bindings": dict(bindings or {}),
        "transform": {"kind": kind, "native": dict(native or {})},
    }
    if enabled is not None:
        step["enabled"] = enabled
    return step


def analysis_step(
    step_id: str,
    *,
    bindings: Mapping[str, Any] | None = None,
    checks: Iterable[str] = (),
    native: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Build an analysis step mapping."""
    step: dict[str, Any] = {
        "id": step_id,
        "executor": "analysis",
        "bindings": dict(bindings or {}),
        "analysis": {"checks": list(checks), "native": dict(native or {})},
    }
    if enabled is not None:
        step["enabled"] = enabled
    return step


def compile_doc(
    document: Mapping[str, Any],
    *,
    registry: Any = None,
) -> CompileResult:
    """Compile a raw document mapping."""
    return compile_workflow(document, registry=registry)


def assemble(
    plan: Any,
    inputs: RunInputs | None = None,
    *,
    materialized: Any = None,
) -> AssemblyResult:
    """Assemble work items for a compiled plan."""
    return assemble_work_items(plan, inputs, materialized=materialized)


def errors(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Return error diagnostics."""
    return [item for item in diagnostics if item.is_error]


def warnings(diagnostics: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Return non-error diagnostics."""
    return [item for item in diagnostics if not item.is_error]


def codes(diagnostics: Iterable[Diagnostic]) -> list[str]:
    """Return diagnostic codes in order."""
    return [item.code for item in diagnostics]


def reasons(diagnostics: Iterable[Diagnostic]) -> list[str]:
    """Return machine-readable diagnostic reasons in order."""
    return [str(item.details.get("reason")) for item in diagnostics]


def assert_no_errors(diagnostics: Iterable[Diagnostic]) -> None:
    """Assert that no diagnostic is an error."""
    offenders = errors(diagnostics)
    assert offenders == [], [
        (item.code, item.details.get("reason"), item.message) for item in offenders
    ]


PASSTHROUGH_TRANSFORM_CONTRACT_VERSION = "test.contract.structure_transform.passthrough.v1"


def passthrough_registry() -> Any:
    """Return a registry where the structure transform is a pure passthrough.

    The built-in V4-1 registry intentionally declares no production step as a
    pure passthrough; disabled-step rewriting is a contract-driven mechanism
    exercised with this test-only contract.
    """
    registry = build_default_registry()
    base = registry.executor(ExecutorCapability.STRUCTURE_TRANSFORM)
    registry.register_executor(
        ExecutorContract(
            capability=ExecutorCapability.STRUCTURE_TRANSFORM,
            contract_version=PASSTHROUGH_TRANSFORM_CONTRACT_VERSION,
            input_ports=base.input_ports,
            output_ports=base.output_ports,
            passthrough_ports=FrozenDict({"structures": "structure"}),
        )
    )
    return registry
