#!/usr/bin/env python3

"""V4 whole-workflow application runtime (V4-6, D-owned).

:class:`V4RunApplication` is the single formal path from typed inputs to
published results.  It owns no science: every step — calculation,
confgen, transform, analysis — runs its assembled items through ONE
generic lifecycle (durable :class:`BatchStepExecutor` with the
registry-resolved executor implementation).  There are no per-capability
branches here: capability selection lives in the execution registry
(owner A), binding resolution in the execution layer (owner C), analysis
item framing with wave-2 F, and producer-state gating in assembly
materialization (owner B).  This module only orchestrates: compile,
import-identity reconciliation, RunState lifecycle, assembly-error
propagation, resolution through the frozen registry/binding APIs, and
schema-conformant durable manifest publication.

Pipeline::

    XYZ / typed input
        ↓  import_xyz (opaque entity IDs + arbitrated durable import map)
    V4 WorkflowDocument
        ↓  compile (parser + compiler)
    ExecutionPlan
        ↓  V4RunApplication (topological step order, RunState lifecycle)
    WorkItem assembly → BatchStepExecutor[registry executor]
        → item commit → verified StepResult publication → RunState
    RunResultManifest (schema-conformant, durably written)
    JobDesk

Resume is item-backed: the published step file is never an early return.
Every step re-assembles its current items, propagates assembly errors
scoped to the step, and lets the durable store decide per-item reuse
(definition/input/environment/provenance/artifact mismatch fails
closed).  Completed items are never re-executed.
"""

from __future__ import annotations

import itertools
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..domain._immutable import FrozenDict
from ..domain.canonical import canonical_json_bytes
from ..domain.completion import StepStatus
from ..domain.errors import DomainError
from ..domain.structure import StructureRecord, StructureSet
from ..execution.batch import BatchStepExecutor, StepExecutionRequest
from ..execution.binding_resolution import (
    BindingRequestDefaults,
    resolve_execution_binding,
)
from ..execution.registry import default_registry
from ..persistence.contracts import (
    PersistenceError,
    RunState,
    RunStepStatus,
    store_path,
    validate_run_root,
    wall_now,
)
from ..persistence.imports import resolve_imported_structures
from ..persistence.run_state import (
    detect_published,
    ensure_step,
    load_run_state,
    save_run_state,
    transition_step,
)
from ..persistence.work_items import SqliteWorkItemStore
from ..workflow.v4.assembly import (
    MaterializedOutputs,
    RunInputs,
    StepOutputs,
    assemble_work_items,
)
from ..workflow.v4.compiler import compile_workflow

__all__ = [
    "RUN_RESULT_FILENAME",
    "V4RunApplication",
    "V4RunReport",
    "V4RunRequest",
    "import_xyz",
]

#: Durable run-result manifest filename at the run root. The final name is
#: lead-owned; recorded here so publisher and consumers agree meanwhile.
RUN_RESULT_FILENAME = "run_result.json"

_TMP_COUNTER = itertools.count()


def import_xyz(
    text: str,
    *,
    source_name: str = "<input>",
    entity_ids: tuple[str, ...] | list[str] | None = None,
) -> StructureSet:
    """Parse XYZ text into a :class:`StructureSet` with opaque identities.

    Fresh imports mint independent opaque entity IDs (UUID hex): two
    imports of equal geometry never share identity, and list position is
    never identity.  Pass ``entity_ids`` to bind user-supplied entities
    explicitly; the count must match the block count exactly and every id
    must be unique.  ``source_name`` is provenance only.

    Durable identity across resumes comes from the run-root import map
    (see :mod:`confflow.persistence.imports`), reconciled by the
    application, never from these minted IDs alone.
    """
    if not isinstance(text, str) or not text.strip():
        raise DomainError(f"XYZ input from {source_name!r} must be non-empty text")
    blocks = _split_xyz_blocks(text)
    if not blocks:
        raise DomainError(f"XYZ input from {source_name!r} carries no molecule blocks")
    if entity_ids is not None:
        supplied = tuple(entity_ids)
        if len(supplied) != len(blocks):
            raise DomainError(
                f"XYZ input from {source_name!r} carries {len(blocks)} blocks "
                f"but {len(supplied)} explicit entity ids were supplied"
            )
        seen: set[str] = set()
        for position, candidate in enumerate(supplied):
            if not isinstance(candidate, str) or not candidate or candidate != candidate.strip():
                raise DomainError(
                    f"XYZ input from {source_name!r} entity_ids[{position}] "
                    "must be a non-empty string"
                )
            if candidate in seen:
                raise DomainError(
                    f"XYZ input from {source_name!r} carries a duplicate entity id {candidate!r}"
                )
            seen.add(candidate)
        resolved_ids = supplied
    else:
        resolved_ids = tuple(uuid.uuid4().hex for _ in blocks)
    records = []
    for index, ((count, rows), entity_id) in enumerate(zip(blocks, resolved_ids)):
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
                    f"XYZ input from {source_name!r} block {index} has non-numeric coordinates"
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
                id=entity_id,
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
    import_sources: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_document, dict):
            raise DomainError("workflow_document must be a mapping")
        if not isinstance(self.run_inputs, RunInputs):
            raise DomainError("run_inputs must be RunInputs")
        if not self.run_root or not isinstance(self.run_root, str):
            raise DomainError("run_root must be a non-empty string")
        if not isinstance(self.executables, FrozenDict):
            object.__setattr__(self, "executables", FrozenDict(self.executables))
        if not isinstance(self.import_sources, FrozenDict):
            object.__setattr__(self, "import_sources", FrozenDict(self.import_sources))
        for name, text in self.import_sources.items():
            if not isinstance(text, str) or not text.strip():
                raise DomainError(f"import source {name!r} must be non-empty text")


@dataclass(frozen=True, slots=True)
class V4RunReport:
    """Outcome of one whole-workflow V4 run."""

    run_id: str
    status: str
    definition_digest: str
    step_results: tuple[Any, ...] = ()
    manifest: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_results", tuple(self.step_results))
        if not isinstance(self.manifest, FrozenDict):
            object.__setattr__(self, "manifest", FrozenDict(self.manifest))


def _map_step_status(status: StepStatus) -> RunStepStatus:
    """Map a semantic step status onto its lifecycle record."""
    return {
        StepStatus.COMPLETED: RunStepStatus.COMPLETED,
        StepStatus.PARTIAL: RunStepStatus.PARTIAL,
        StepStatus.FAILED: RunStepStatus.FAILED,
        StepStatus.CANCELLED: RunStepStatus.CANCELLED,
    }[status]


class V4RunApplication:
    """Run a compiled V4 workflow step by step with durable resume."""

    def __init__(self, *, supervisor: Any = None, registry: Any = None) -> None:
        self._supervisor = supervisor
        self._registry = registry

    @property
    def _active_registry(self) -> Any:
        """Return the execution registry: the single capability authority."""
        return self._registry if self._registry is not None else default_registry()

    def run(self, request: V4RunRequest) -> V4RunReport:
        """Compile, execute, publish, and summarize one V4 workflow."""
        compiled = compile_workflow(request.workflow_document)
        if not compiled.ok:
            raise DomainError(
                "V4 workflow does not compile: "
                + "; ".join(item.message for item in compiled.errors)
            )
        assert compiled.plan is not None
        plan = compiled.plan
        run_id = os.path.basename(request.run_root.rstrip(os.sep)) or "run"
        run_root = validate_run_root(request.run_root)
        os.makedirs(run_root, exist_ok=True)
        state = self._load_or_init_run_state(
            run_root=run_root, run_id=run_id, definition_digest=plan.definition_digest
        )
        for planned in plan.steps:
            state = ensure_step(state, planned.step_id)
        save_run_state(run_root, state)
        run_inputs = self._resolve_run_inputs(run_root=run_root, request=request)
        materialized = MaterializedOutputs.empty()
        step_results: list[Any] = []
        supervisor = request.supervisor if request.supervisor is not None else self._supervisor
        for planned in plan.steps:
            state = transition_step(
                ensure_step(state, planned.step_id), planned.step_id, RunStepStatus.RUNNING
            )
            save_run_state(run_root, state)
            # Producer-state gating lives in assembly materialization
            # (owner B): failed/partial/cancelled producers surface here as
            # errors scoped to this step.  The caller only propagates them —
            # never executes an errored step, never invents a second policy.
            assembly = assemble_work_items(plan, run_inputs, materialized=materialized)
            scoped_errors = tuple(
                item
                for item in assembly.errors
                if getattr(item, "step_id", None) in (None, planned.step_id)
            )
            if scoped_errors:
                codes = "; ".join(f"{item.code}: {item.message}" for item in scoped_errors)
                raise DomainError(
                    f"step {planned.step_id!r} is not assemblable and never executes: {codes}"
                )
            if planned.step_id in tuple(assembly.skipped_step_ids):
                raise DomainError(
                    f"step {planned.step_id!r} waits for unmaterialized producer outputs; "
                    "blocked steps are lifecycle data and never execute or publish"
                )
            result = self._run_step(
                plan,
                planned,
                tuple(assembly.for_step(planned.step_id)),
                request,
                supervisor,
                run_root=run_root,
            )
            if result.step_id != planned.step_id:
                raise DomainError(
                    f"step executor returned {result.step_id!r} for {planned.step_id!r}"
                )
            step_results.append(result)
            materialized = _extend_materialized(plan, planned, materialized, result)
            digest = detect_published(run_root=run_root, step_id=planned.step_id)
            state = transition_step(
                state,
                planned.step_id,
                _map_step_status(result.status),
                published_step_result_digest=digest,
            )
            save_run_state(run_root, state)
        status = _evaluate_run_status(tuple(result.status for result in step_results))
        manifest = self._publish_manifest(
            run_id=run_id,
            status=status,
            definition_digest=plan.definition_digest,
            step_results=tuple(step_results),
            plan=plan,
            run_root=run_root,
        )
        return V4RunReport(
            run_id=run_id,
            status=status,
            definition_digest=plan.definition_digest,
            step_results=tuple(step_results),
            manifest=manifest,
        )

    # ------------------------------------------------------------------
    # Run-state and import persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _load_or_init_run_state(*, run_root: str, run_id: str, definition_digest: str) -> RunState:
        """Load the durable run state or initialize it for this definition.

        Reusing a run root for a different workflow definition fails
        closed: history is never reinterpreted under a new digest.  No
        second lock runtime is invented: per-item ownership claims plus
        this lifecycle record are the existing arbitration mechanism.
        """
        try:
            existing = load_run_state(run_root)
        except Exception as exc:
            raise DomainError(f"run state at {run_root!r} is corrupt: {exc}") from exc
        if existing is None:
            now = wall_now()
            return RunState(
                run_id=run_id,
                definition_digest=definition_digest,
                steps=(),
                created_wall=now,
                updated_wall=now,
            )
        if (
            existing.definition_digest is not None
            and existing.definition_digest != definition_digest
        ):
            raise DomainError(
                "run root already holds a different workflow definition "
                f"(was {existing.definition_digest!r}, now {definition_digest!r}); "
                "use a fresh run root or explicit migration"
            )
        if existing.definition_digest is None:
            return RunState(
                run_id=existing.run_id,
                definition_digest=definition_digest,
                steps=existing.steps,
                created_wall=existing.created_wall,
                updated_wall=wall_now(),
            )
        return existing

    @staticmethod
    def _resolve_run_inputs(*, run_root: str, request: V4RunRequest) -> RunInputs:
        """Reconcile typed run inputs against durable import maps.

        Inputs carrying a raw source text in ``request.import_sources``
        reload the persisted entity IDs for identical bytes (concurrent
        first imports arbitrate to one winner) and fail closed on changed
        bytes.  Inputs without a source text are validated against a
        persisted entity snapshot so a silently re-minted import can never
        masquerade as the original entities.
        """
        structures = dict(request.run_inputs.structures.items())
        for name, current in list(structures.items()):
            source_text = request.import_sources.get(name)
            if source_text is not None:
                structures[name] = resolve_imported_structures(
                    run_root=run_root,
                    input_name=name,
                    source_name=name,
                    source_text=source_text,
                    fresh=current,
                )
            else:
                structures[name] = _validate_unresolved_input(
                    run_root=run_root, input_name=name, current=current
                )
        return RunInputs(
            structures=FrozenDict(structures),
            artifacts=request.run_inputs.artifacts,
            results=request.run_inputs.results,
        )

    # ------------------------------------------------------------------
    # One generic step lifecycle for every executor capability
    # ------------------------------------------------------------------

    def _run_step(
        self,
        plan: Any,
        planned: Any,
        items: tuple[Any, ...],
        request: V4RunRequest,
        supervisor: Any,
        *,
        run_root: str,
    ) -> Any:
        """Execute one step's items through the registry-resolved executor.

        Resolution uses the frozen registry APIs only (descriptors and
        implementations are one authority; unknown or
        published-but-unimplemented capabilities fail closed here, never
        through a default program).  Steps whose executor contract
        requires a native adapter resolve program/profile/checks/recovery
        plus the shared binding authority and measured environment.
        Pure steps record their implementation environment with the real
        registered contract version.  Every step then shares the same
        durable batch lifecycle, so analysis reuse, resume, and
        publication are identical to every other capability.
        Remote delivery always binds the step-specific store: a
        multi-step transport is rebound per step before dispatch.
        """
        registry = self._active_registry
        contract = registry.resolve_executor(planned.executor)
        # The registry returns the executor CLASS; application instantiates
        # it directly with no capability switch and no dynamic fallback.
        # Unregistered capabilities (analysis until wave-2 F) fail closed
        # here with the registry error.
        executor = registry.executor_implementation(planned.executor)()
        program = planned.scientific.program
        if contract.requires_adapter:
            if not program:
                raise DomainError(
                    f"step {planned.step_id!r} requires a native adapter but declares no program"
                )
            adapter = registry.resolve_program(program)
            profile = registry.profile_implementation(planned.profile_name)
            checks = tuple(
                registry.check_implementation(name) for name in planned.scientific.checks
            )
            recovery = registry.recovery_implementation(
                planned.scientific.recovery or "none", adapter=adapter
            )
            binding = resolve_execution_binding(
                program=program,
                planned=planned.execution,
                defaults=BindingRequestDefaults(
                    executables=dict(request.executables.thaw()),
                ),
                adapter_default_executable=adapter.default_executable,
            )
            # Explicit target gate BEFORE measurement/launch: nonlocal
            # targets require a configured transport; otherwise fail closed
            # with 0 native launches (no silent local fallback). Local
            # targets (omitted/"local"/"localhost") always run in-process.
            from ..execution.binding_resolution import (
                effective_native_env,
                require_target_transport,
            )

            require_target_transport(binding.target, request.transport)
            # ONE immutable native-environment snapshot per step.  Ambient
            # inheritance is explicit producer-side policy (os.environ) and
            # is fully digested; binding env wins on collision.  The SAME
            # snapshot feeds measurement, the remote handoff envelope, the
            # worker launch, and provenance — never two constructions.
            native_env = FrozenDict(effective_native_env(binding, inherit=os.environ))
            environment = self._measure_environment(
                planned=planned,
                binding=binding,
                adapter=adapter,
                relevant_env=native_env,
            )
            provenance = None
        else:
            # Pure executors (confgen/transform/analysis): no native program,
            # hence no binding and no binary measurement.  The measured
            # implementation environment uses the shared helper with the
            # real registered executor contract version, so implementation
            # changes invalidate reuse.  Analysis dispatches through its
            # in-package work-item adapter on the same lifecycle.
            from ..execution.environment import build_pure_environment
            from ..persistence.reuse import build_producer_provenance

            implementation = f"confflow.{contract.capability.value}"
            environment = build_pure_environment(
                implementation=implementation,
                implementation_version=contract.contract_version,
                metadata={"step_id": planned.step_id},
            )
            native_env = None
            provenance = build_producer_provenance(
                adapter_version=implementation,
                profile_version=contract.contract_version,
                check_versions={},
                recovery_version="none",
            )
            adapter = None
            profile = None
            checks = ()
            recovery = None
            binding = None
            provenance = FrozenDict(dict(provenance))
        batch = BatchStepExecutor(executor)
        if supervisor is not None:
            batch = batch.with_supervisor(supervisor)
        step_request = StepExecutionRequest(
            step=planned,
            items=items,
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=adapter,
            profile=profile,
            checks=checks,
            recovery=recovery,
            execution_binding=binding,
            run_root=run_root,
            environment=environment,
            native_env=native_env,
            definition_digest=plan.definition_digest,
            producer_provenance=provenance,
            executor_capability=contract.capability.value,
        )
        with SqliteWorkItemStore.open(store_path(run_root, planned.step_id)) as store:
            return batch.execute_step_resumable(
                step_request,
                store=store,
                run_root=run_root,
                owner_token=request.owner_token,
                transport=self._resolve_step_transport(
                    binding, request.transport, store, step_id=planned.step_id
                ),
            )

    @staticmethod
    def _resolve_step_transport(binding: Any, transport: Any, store: Any, *, step_id: str) -> Any:
        """Resolve per-step delivery, failing closed on nonlocal targets.

        Local bindings (``None``/``"local"``/``"localhost"``) always
        return ``None`` (in-process delivery) so a configured remote
        transport never hijacks local steps. Nonlocal bindings require
        the configured *transport* (rebound to *store* via ``with_store``
        when available); with no configured or no matching transport this
        raises BEFORE any native launch.
        """
        from ..execution.binding_resolution import (
            is_local_target,
            require_target_transport,
        )

        target = getattr(binding, "target", None) if binding is not None else None
        if target is None or is_local_target(target):
            return None
        resolved = require_target_transport(target, transport)
        # ``require`` returned the configured transport (or raised); rebind
        # it to this step's store so imports commit into the right DB.
        rebind = getattr(resolved, "with_store", None)
        if callable(rebind):
            try:
                return rebind(store)
            except Exception as exc:
                raise DomainError(
                    f"step {step_id!r} targets {target!r}: cannot bind transport: {exc}"
                ) from exc
        return resolved

    @staticmethod
    def _step_transport(transport: Any, store: Any) -> Any:
        """Bind *transport* to the current step's store.

        A remote transport that spans steps must import each step's
        bundles into that step's store; siblings share delivery records
        so duplicate delivery still converges without relaunch.
        """
        rebind = getattr(transport, "with_store", None)
        if callable(rebind):
            return rebind(store)
        return transport

    @staticmethod
    def _measure_environment(
        *, planned: Any, binding: Any, adapter: Any, relevant_env: Any = None
    ) -> Any:
        """Measure the real execution environment for the reuse axis.

        The measured ``relevant_env`` is the COMPLETE effective native
        environment the executor will launch with (``relevant_env``, the
        single immutable snapshot built by ``_run_step`` from the ambient
        inheritance policy plus the explicit ``ExecutionBinding.env``).
        The environment digest therefore covers every variable the native
        subprocess can read: launch env and hashed env are the same
        mapping by construction, so an inherited-variable change can never
        reuse a stale scientific result. Unknown never stands in for
        verified equivalence: when the executable cannot be measured the
        run fails closed instead of recording ``None``.
        """
        from ..execution.binding_resolution import effective_native_env
        from ..execution.environment import EnvironmentMeasurer

        candidate = binding.executable or adapter.default_executable
        if relevant_env is None:
            relevant_env = effective_native_env(binding, inherit=os.environ)
        try:
            return EnvironmentMeasurer().build_environment(
                candidate, adapter=adapter, target=binding.target, relevant_env=relevant_env
            )
        except DomainError as exc:
            raise DomainError(
                f"step {planned.step_id!r}: cannot measure execution environment "
                f"for {candidate!r}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Manifest: real digests, schema shape, durable publication
    # ------------------------------------------------------------------

    def _publish_manifest(
        self,
        *,
        run_id: str,
        status: str,
        definition_digest: str,
        step_results: tuple[Any, ...],
        plan: Any,
        run_root: str,
    ) -> FrozenDict:
        """Publish the producer-facing run-result manifest.

        Every step entry carries the real published digest re-discovered
        from durable storage (never a status string) plus the planned-step
        semantic digest from the validated plan fingerprint, schema-exact
        counts and diagnostics, and only artifacts with verified sha256
        checksums and portable run-relative locators.  Top-level ``results``
        carries one ResultRef per real emitted scientific result.  Analysis
        steps keep their minimal ``{capability, step_id}`` ref AND project
        rich reaction-group entries from the actual analysis
        ``StepResult``/``ScientificResult`` objects (group_key, TS,
        forward/reverse endpoints, E/G entries, barriers, verbatim
        assignment, source ``ResultRef`` ids) via the producer-owned
        projector, so the durable manifest is readable as reaction groups
        by the JobDesk consumer.  The manifest is validated against the
        actual producer schema and atomically written to
        ``run_root/run_result.json`` (directory fsynced); the in-memory
        report mirrors the durable bytes.

        Projection delegates to the producer-owned helpers
        (:mod:`confflow.producer.run_result`) so there is exactly one
        manifest shape.  The planned-step semantic map is read from the
        validated plan only (no second capability table); per-step
        provenance already carries the same fingerprint value as fallback.
        """
        import jsonschema

        import confflow

        from ..producer.contract import build_run_result_manifest, run_result_json_schema
        from ..producer.run_result import (
            artifact_entry,
            project_analysis_groups,
            result_ref_entry,
            step_entry,
        )

        semantic = {
            planned.step_id: planned.step_semantic_digest
            for planned in plan.steps
            if getattr(planned, "step_semantic_digest", None) is not None
        }
        steps: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for result in step_results:
            digest = detect_published(run_root=run_root, step_id=result.step_id)
            if digest is None:
                raise PersistenceError(
                    f"step {result.step_id!r} has no durable publication; "
                    "manifest publication requires every step result to be published"
                )
            steps.append(
                step_entry(
                    result,
                    published_digest=digest,
                    semantic_digest=semantic.get(result.step_id),
                )
            )
            for record in result.results:
                results.append(result_ref_entry(record))
            for artifact in result.artifacts:
                artifacts.append(artifact_entry(artifact))
        analyses: list[dict[str, Any]] = []
        for planned in plan.steps:
            if planned.executor.value == "analysis":
                analyses.append({"capability": planned.executor.value, "step_id": planned.step_id})
        try:
            analyses.extend(project_analysis_groups(tuple(step_results)))
        except ValueError as exc:
            raise DomainError(f"run-result manifest cannot project analysis groups: {exc}") from exc
        manifest = build_run_result_manifest(
            run_id=run_id,
            status=status,
            definition_digest=definition_digest,
            producer_version=getattr(confflow, "__version__", "unknown"),
            steps=steps,
            analyses=analyses,
            artifacts=artifacts,
            results=results,
        )
        try:
            jsonschema.validate(instance=manifest, schema=run_result_json_schema())
        except Exception as exc:
            raise DomainError(f"run-result manifest fails its schema: {exc}") from exc
        target = os.path.join(validate_run_root(run_root), RUN_RESULT_FILENAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp_path = f"{target}.tmp.{os.getpid()}.{next(_TMP_COUNTER)}"
        try:
            with open(tmp_path, "wb") as handle:
                handle.write(canonical_json_bytes(manifest))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
        _fsync_directory(os.path.dirname(target))
        return FrozenDict(manifest)


def _fsync_directory(directory: str) -> None:
    """Fsync *directory* so a new publication survives a crash."""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _evaluate_run_status(statuses: tuple[StepStatus, ...]) -> str:
    """Evaluate the run status without collapsing partial/cancelled to failed."""
    if not statuses:
        return "completed"
    if any(status is StepStatus.CANCELLED for status in statuses):
        return "cancelled"
    if any(status is StepStatus.FAILED for status in statuses):
        return "failed"
    if any(status is StepStatus.PARTIAL for status in statuses):
        return "partial"
    return "completed"


def _validate_unresolved_input(*, run_root: str, input_name: str, current: Any) -> Any:
    """Validate an input without raw source bytes against its snapshot.

    The first run persists the ordered entity IDs plus geometry digests;
    later runs must present the same entities in the same order.  Freshly
    minted UUIDs never match: callers must either reuse their typed
    records or supply the raw source text so identity reloads durably.
    """
    from ..domain.structure import StructureSet

    if not isinstance(current, StructureSet):
        raise DomainError(f"run input {input_name!r} must hold a StructureSet")
    ordered = list(current)
    snapshot = _load_input_snapshot(run_root=run_root, input_name=input_name)
    if snapshot is None:
        _save_input_snapshot(
            run_root=run_root,
            input_name=input_name,
            entity_ids=[record.id for record in ordered],
            geometry_digests=[record.geometry_digest for record in ordered],
        )
        return current
    if [record.id for record in ordered] != list(snapshot["entity_ids"]):
        raise DomainError(
            f"run input {input_name!r} entity IDs do not match the persisted import; "
            "re-minted XYZ imports never masquerade as persisted entities: reuse the "
            "original typed records or supply the raw source text via import_sources"
        )
    if [record.geometry_digest for record in ordered] != list(snapshot["geometry_digests"]):
        raise DomainError(
            f"run input {input_name!r} geometry changed under persisted entity IDs; "
            "the same id never silently points at changed geometry"
        )
    return current


def _snapshot_path(run_root: str, input_name: str) -> str:
    if not isinstance(input_name, str) or not input_name or input_name != input_name.strip():
        raise DomainError(f"input_name must be a non-empty string: {input_name!r}")
    if input_name in (".", "..") or "/" in input_name or "\\" in input_name:
        raise DomainError(f"input_name must be a single path segment: {input_name!r}")
    return os.path.join(validate_run_root(run_root), "imports", f"{input_name}.snapshot.json")


def _save_input_snapshot(
    *, run_root: str, input_name: str, entity_ids: list[str], geometry_digests: list[str]
) -> None:
    target = _snapshot_path(run_root, input_name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp_path = f"{target}.tmp.{os.getpid()}.{next(_TMP_COUNTER)}"
    payload = {
        "input_name": input_name,
        "entity_ids": list(entity_ids),
        "geometry_digests": list(geometry_digests),
    }
    try:
        with open(tmp_path, "wb") as handle:
            handle.write(canonical_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
    _fsync_directory(os.path.dirname(target))


def _load_input_snapshot(*, run_root: str, input_name: str) -> dict[str, Any] | None:
    import json as _json

    target = _snapshot_path(run_root, input_name)
    try:
        with open(target, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DomainError(f"cannot read input snapshot for {input_name!r}: {exc}") from exc
    try:
        payload = _json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DomainError(f"input snapshot for {input_name!r} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DomainError(f"input snapshot for {input_name!r} must be a mapping")
    return payload


def _extend_materialized(
    plan: Any, planned: Any, materialized: MaterializedOutputs, result: Any
) -> MaterializedOutputs:
    """Add one step result to the materialized producer outputs.

    The real step status plus completion provenance travels on the
    ``StepOutputs`` record (owner B's materialization), so downstream
    assembly gates on the actual producer outcome.
    """
    steps = dict(materialized.steps)
    steps[result.step_id] = StepOutputs(
        step_id=result.step_id,
        structures=result.structures,
        artifacts=result.artifacts,
        results=result.results,
        status=result.status,
        producer_step_digest=planned.step_semantic_digest,
        item_statuses=tuple(item.status for item in result.item_results),
    )
    return MaterializedOutputs(steps=FrozenDict(steps))
