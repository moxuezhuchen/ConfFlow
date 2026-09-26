#!/usr/bin/env python3

"""V4 whole-workflow application runtime (V4-6, main-owned).

:class:`V4RunApplication` is the single formal path from typed inputs to
published results.  It owns no science: per-step work goes through the
existing :class:`BatchStepExecutor` (calculation/confgen) or the pure
:class:`AnalysisExecutor` (analysis); persistence, resume, artifact
matching, and result profiling all stay where V4-1..V4-5 put them.

Pipeline::

    XYZ / typed input
        ↓  import_xyz
    V4 WorkflowDocument
        ↓  compile (parser + compiler)
    ExecutionPlan
        ↓  V4RunApplication (topological step order)
    WorkItem assembly → Calculation | Analysis
        ↓  StepResult → persistence → publication
    RunResultManifest

Resume: published complete steps load from disk; incomplete calculation
steps resume per work item; completed analysis reuses; downstream steps
that never started continue.  A resume never restarts every step.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.artifact import ArtifactSet
from ..domain.binding import PartialConsumption
from ..domain.completion import WorkItemStatus, evaluate_step_status
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.step_result import StepProvenance, StepResult
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import Timing, WorkItem, WorkItemResult
from ..execution.batch import BatchStepExecutor, StepExecutionRequest
from ..execution.contracts import ExecutionBinding, ExecutorCapability
from ..execution.work_item_executor import WorkItemExecutor
from ..persistence.contracts import store_path
from ..persistence.publication import load_published_step_result
from ..persistence.work_items import SqliteWorkItemStore
from ..workflow.v4.assembly import (
    MaterializedOutputs,
    RunInputs,
    StepOutputs,
    assemble_work_items,
)
from ..workflow.v4.compiler import compile_workflow

__all__ = [
    "V4RunApplication",
    "V4RunReport",
    "V4RunRequest",
    "import_xyz",
]


def import_xyz(text: str, *, source_name: str = "<input>") -> StructureSet:
    """Parse XYZ text into a :class:`StructureSet` with stable identities.

    Parameters
    ----------
    text : str
        XYZ content: first line the atom count, second a comment, then
        one ``<symbol> <x> <y> <z>`` row per atom in Angstrom.
    source_name : str
        Import provenance locator (a path or handle name).  The path is
        provenance only — never runtime identity.  Records are named
        ``xyz:<index>`` with ``parent_ids=()``.

    Returns
    -------
    StructureSet
        One record per molecule block found in *text*.
    """
    if not isinstance(text, str) or not text.strip():
        raise DomainError(f"XYZ input from {source_name!r} must be non-empty text")
    blocks = _split_xyz_blocks(text)
    if not blocks:
        raise DomainError(f"XYZ input from {source_name!r} carries no molecule blocks")
    records = []
    for index, (count, rows) in enumerate(blocks):
        atoms: list[str] = []
        coords: list[tuple[float, float, float]] = []
        for row in rows:
            parts = row.split()
            if len(parts) < 4:
                raise DomainError(
                    f"XYZ input from {source_name!r} block {index} has a malformed row"
                )
            try:
                point = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError as exc:
                raise DomainError(
                    f"XYZ input from {source_name!r} block {index} has " f"non-numeric coordinates"
                ) from exc
            atoms.append(parts[0])
            coords.append(point)
        if len(atoms) != count:
            raise DomainError(
                f"XYZ input from {source_name!r} block {index} declares "
                f"{count} atoms but carries {len(atoms)}"
            )
        records.append(
            StructureRecord(
                id=f"xyz:{index}",
                atoms=tuple(atoms),
                coordinates=tuple(coords),
                metadata=FrozenDict({"import_source": source_name}),
            )
        )
    return StructureSet.of(*records)


def _split_xyz_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Split XYZ text into ``(count, rows)`` molecule blocks."""
    lines = [line.strip() for line in text.splitlines()]
    blocks: list[tuple[int, list[str]]] = []
    cursor = 0
    while cursor < len(lines):
        while cursor < len(lines) and not lines[cursor]:
            cursor += 1
        if cursor >= len(lines):
            break
        try:
            count = int(lines[cursor].split()[0])
        except (ValueError, IndexError) as exc:
            raise DomainError(f"XYZ block at line {cursor + 1} needs an atom count") from exc
        if count <= 0:
            raise DomainError(f"XYZ block at line {cursor + 1} has no atoms")
        cursor += 2
        rows = [line for line in lines[cursor : cursor + count] if line]
        if len(rows) != count:
            raise DomainError(f"XYZ block declares {count} atoms but carries {len(rows)} rows")
        blocks.append((count, rows))
        cursor += count
    return blocks


@dataclass(frozen=True, slots=True)
class V4RunRequest:
    """Everything one whole-workflow V4 run may read."""

    workflow_document: dict[str, Any]
    run_inputs: RunInputs
    run_root: str
    owner_token: str = "v4-run"
    executables: FrozenDict = field(default_factory=FrozenDict)
    supervisor: Any = None
    transport: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_document, dict):
            raise DomainError("workflow_document must be a mapping")
        if not isinstance(self.run_inputs, RunInputs):
            raise DomainError("run_inputs must be RunInputs")
        if not self.run_root or not isinstance(self.run_root, str):
            raise DomainError("run_root must be a non-empty string")
        if not isinstance(self.executables, FrozenDict):
            object.__setattr__(self, "executables", FrozenDict(self.executables))


@dataclass(frozen=True, slots=True)
class V4RunReport:
    """Outcome of one whole-workflow V4 run."""

    run_id: str
    status: str
    definition_digest: str
    step_results: tuple[StepResult, ...] = ()
    manifest: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_results", tuple(self.step_results))
        if not isinstance(self.manifest, FrozenDict):
            object.__setattr__(self, "manifest", FrozenDict(self.manifest))


class V4RunApplication:
    """Run a compiled V4 workflow step by step with durable resume."""

    def __init__(self, *, supervisor: Any = None) -> None:
        self._supervisor = supervisor

    def run(self, request: V4RunRequest) -> V4RunReport:
        """Compile, execute, publish, and summarize one V4 workflow."""
        from ..programs.registry import get_program_adapter

        compiled = compile_workflow(request.workflow_document)
        if not compiled.ok:
            raise DomainError(
                "V4 workflow does not compile: "
                + "; ".join(item.message for item in compiled.errors)
            )
        assert compiled.plan is not None
        plan = compiled.plan
        run_id = os.path.basename(request.run_root.rstrip(os.sep)) or "run"
        os.makedirs(request.run_root, exist_ok=True)
        materialized = MaterializedOutputs.empty()
        step_results: list[StepResult] = []
        supervisor = request.supervisor if request.supervisor is not None else self._supervisor
        for planned in plan.steps:
            published = load_published_step_result(
                run_root=request.run_root, step_id=planned.step_id
            )
            if published is not None:
                step_results.append(published)
                materialized = _extend_materialized(materialized, published)
                continue
            assembly = assemble_work_items(plan, request.run_inputs, materialized=materialized)
            items = assembly.for_step(planned.step_id)
            if planned.executor is ExecutorCapability.ANALYSIS:
                result = self._run_analysis_step(plan, planned, tuple(items))
            else:
                result = self._run_calculation_step(
                    plan,
                    planned,
                    tuple(items),
                    request,
                    supervisor,
                    get_program_adapter,
                )
            step_results.append(result)
            materialized = _extend_materialized(materialized, result)
        status = (
            "completed" if all(r.status.value == "completed" for r in step_results) else "failed"
        )
        manifest = self._publish_manifest(
            run_id=run_id,
            status=status,
            definition_digest=plan.definition_digest,
            step_results=tuple(step_results),
        )
        return V4RunReport(
            run_id=run_id,
            status=status,
            definition_digest=plan.definition_digest,
            step_results=tuple(step_results),
            manifest=manifest,
        )

    def _run_calculation_step(
        self,
        plan: Any,
        planned: Any,
        items: tuple[WorkItem, ...],
        request: V4RunRequest,
        supervisor: Any,
        get_adapter: Any,
    ) -> StepResult:
        """Execute one calculation/confgen step through the batch executor."""
        from ..execution.checks_standard import CHECKS
        from ..execution.profile_standard import PROFILES
        from ..execution.recovery_standard import RECOVERIES

        program = planned.scientific.program or "orca"
        executable = request.executables.get(program)
        if not executable:
            raise DomainError(
                f"no executable configured for program {program!r}; "
                "executables map program names to native paths"
            )
        batch = BatchStepExecutor(WorkItemExecutor())
        if supervisor is not None:
            batch = batch.with_supervisor(supervisor)
        step_request = StepExecutionRequest(
            step=planned,
            items=items,
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=get_adapter(program),
            profile=PROFILES[planned.profile_name],
            checks=tuple(CHECKS[name] for name in planned.scientific.checks),
            recovery=RECOVERIES[planned.scientific.recovery or "none"],
            execution_binding=ExecutionBinding(
                binding_id="v4-run", executable=str(executable), env=FrozenDict({})
            ),
            run_root=request.run_root,
            environment=None,
            definition_digest=plan.definition_digest,
        )
        with SqliteWorkItemStore.open(store_path(request.run_root, planned.step_id)) as store:
            return batch.execute_step_resumable(
                step_request,
                store=store,
                run_root=request.run_root,
                owner_token=request.owner_token,
                transport=request.transport,
            )

    def _run_analysis_step(
        self, plan: Any, planned: Any, items: tuple[WorkItem, ...]
    ) -> StepResult:
        """Execute one analysis step through the pure analysis executor."""
        from ..analysis.compute import policy_from_native
        from ..analysis.executor import AnalysisExecutor
        from ..analysis.models import AnalysisInputs

        definition = _analysis_definition(planned, policy_from_native)
        merged_structures: dict[str, StructureSet] = {}
        merged_results: dict[str, ResultSet] = {}
        merged_artifacts: dict[str, ArtifactSet] = {}
        for work_item in items:
            for port, value in work_item.named_inputs.structures.items():
                merged_structures[port] = merged_structures.get(port, StructureSet()) + value
            for port, value in work_item.named_inputs.results.items():
                merged_results[port] = merged_results.get(port, ResultSet()) + value
            for port, value in work_item.named_inputs.artifacts.items():
                merged_artifacts[port] = merged_artifacts.get(port, ArtifactSet()) + value
        output = AnalysisExecutor().execute(
            AnalysisInputs(
                structures=FrozenDict(merged_structures),
                results=FrozenDict(merged_results),
                definition=definition,
            )
        )
        collected: list[WorkItemResult] = []
        for source in items:
            if output.diagnostics and any(d.severity.value == "error" for d in output.diagnostics):
                collected.append(
                    WorkItemResult(
                        work_item_id=source.id,
                        status=WorkItemStatus.FAILED,
                        diagnostics=tuple(output.diagnostics),
                        timing=Timing(duration_seconds=0.0),
                        error=None,
                        semantic_digest=source.semantic_digest,
                    )
                )
            else:
                collected.append(
                    WorkItemResult(
                        work_item_id=source.id,
                        status=WorkItemStatus.COMPLETED,
                        structures=output.structures,
                        results=output.results,
                        artifacts=output.artifacts,
                        diagnostics=tuple(output.diagnostics),
                        timing=Timing(duration_seconds=0.0),
                        semantic_digest=source.semantic_digest,
                    )
                )
        statuses = tuple(entry.status for entry in collected)
        status = evaluate_step_status(planned.completion, statuses)
        accepted = tuple(entry for entry in collected if entry.is_completed)
        structures = StructureSet()
        results = ResultSet()
        artifacts = ArtifactSet()
        for entry in accepted:
            structures = structures + entry.structures
            results = results + entry.results
            artifacts = artifacts + entry.artifacts
        return StepResult(
            step_id=planned.step_id,
            status=status,
            structures=structures,
            results=results,
            artifacts=artifacts,
            item_results=tuple(collected),
            diagnostics=tuple(output.diagnostics),
            summary=FrozenDict(
                {
                    "total": len(collected),
                    "completed": sum(1 for entry in collected if entry.is_completed),
                    "failed": sum(
                        1 for entry in collected if entry.status is WorkItemStatus.FAILED
                    ),
                    "cancelled": 0,
                    "completion_mode": planned.completion.mode.value,
                    "status": status.value,
                }
            ),
            provenance=StepProvenance(
                workflow_definition_digest=plan.definition_digest,
                step_semantic_digest=planned.step_semantic_digest,
            ),
        )

    def _publish_manifest(
        self,
        *,
        run_id: str,
        status: str,
        definition_digest: str,
        step_results: tuple[StepResult, ...],
    ) -> FrozenDict:
        """Publish the producer-facing run-result manifest."""
        import confflow

        from ..producer.contract import build_run_result_manifest

        steps = [
            {
                "id": result.step_id,
                "status": result.status.value,
                "step_result_digest": result.summary.thaw().get("status", ""),
                "counts": dict(result.summary.thaw()),
                "diagnostics_summary": [
                    {"code": item.code, "severity": item.severity.value}
                    for item in result.diagnostics
                ],
            }
            for result in step_results
        ]
        manifest = build_run_result_manifest(
            run_id=run_id,
            status=status,
            definition_digest=definition_digest,
            producer_version=getattr(confflow, "__version__", "unknown"),
            steps=steps,
            analyses=[
                {
                    "group_key": payload.get("group_key", ""),
                    "ts_structure_id": payload.get("nodes", {}).get("ts", ""),
                    "forward_endpoint_id": payload.get("nodes", {}).get("forward", ""),
                    "reverse_endpoint_id": payload.get("nodes", {}).get("reverse", ""),
                    "gibbs_energy": payload.get("gibbs_energy", {}),
                    "barriers": payload.get("barriers", {}),
                    "assignment": payload.get("assignment"),
                    "source_result_ids": payload.get("source_result_ids", []),
                }
                for result in step_results
                for record in result.results
                if record.kind == "reaction_profile"
                for payload in [record.value if isinstance(record.value, dict) else {}]
            ],
        )
        return FrozenDict(manifest)


def _extend_materialized(
    materialized: MaterializedOutputs, result: StepResult
) -> MaterializedOutputs:
    """Add one step result to the materialized producer outputs."""
    steps = dict(materialized.steps)
    steps[result.step_id] = StepOutputs(
        step_id=result.step_id,
        structures=result.structures,
        artifacts=result.artifacts,
        results=result.results,
    )
    return MaterializedOutputs(steps=FrozenDict(steps))


def _analysis_definition(planned: Any, policy_from_native: Any) -> Any:
    """Build the analysis definition from a planned analysis step."""
    from ..analysis.compute import ReactionEnergyModel
    from ..analysis.models import AnalysisDefinition

    native = planned.scientific.native
    params = {
        key: (value.thaw() if hasattr(value, "thaw") else value)
        for key, value in dict(native).items()
        if key not in ("energy_model", "endpoint_assignment", "partial_policy")
    }
    assignment = native.get("endpoint_assignment")
    policy_name = native.get("partial_policy", "require_complete")
    try:
        partial_policy = PartialConsumption(str(policy_name))
    except ValueError as exc:
        raise DomainError(f"unknown analysis partial_policy {policy_name!r}") from exc
    return AnalysisDefinition(
        kind=str(native.get("analysis_kind", "reaction_profile")),
        energy_model=ReactionEnergyModel(policy_from_native(native)),
        params=FrozenDict(params),
        endpoint_assignment=(
            FrozenDict({key: assignment.get(key, "unassigned") for key in ("forward", "reverse")})
            if assignment is not None
            else FrozenDict({"forward": "unassigned", "reverse": "unassigned"})
        ),
        partial_policy=partial_policy,
    )
