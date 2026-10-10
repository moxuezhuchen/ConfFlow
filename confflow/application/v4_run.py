#!/usr/bin/env python3

"""V4 whole-workflow application runtime.

V4RunApplication is the single formal path from typed inputs to published results; owns no science.
All steps share one durable BatchStepExecutor lifecycle with registry-resolved executors; no
per-capability branches (capability selection in execution registry, binding in execution layer,
analysis framing in wave-2 F, producer-state gating in assembly materialization).
Orchestrates compile, import-identity reconciliation, RunState lifecycle, assembly errors,
frozen registry/binding resolution, and schema-conformant manifest publication.
Resume is item-backed: step file never early-returns; each step re-assembles items, propagates
step-scoped assembly errors, reuses per-item only on definition/input/environment/provenance/
artifact match (mismatch fails closed); completed items never re-executed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
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
from ..execution.contracts import ExecutorCapability
from ..execution.registry import default_registry
from ..execution.xyz_import import import_xyz
from ..persistence import arbitration
from ..persistence.contracts import (
    PersistenceError,
    RunState,
    RunStepStatus,
    store_path,
    validate_run_root,
    wall_now,
)
from ..persistence.fsatomic import publish_bytes
from ..persistence.generation import (
    RunGeneration,
    new_generation_id,
    running_record,
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
from ..workflow.v4.parser import parse_workflow_document

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


@dataclass(frozen=True, slots=True)
class V4RunRequest:
    """Everything one whole-workflow V4 run may read."""

    workflow_document: dict[str, Any]
    run_inputs: RunInputs
    run_root: str
    owner_token: str = "v4-run"
    executables: FrozenDict = field(default_factory=FrozenDict)
    supervisor: Any = None
    import_sources: FrozenDict = field(default_factory=FrozenDict)
    should_cancel: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_document, dict):
            raise DomainError("workflow_document must be a mapping")
        if not isinstance(self.run_inputs, RunInputs):
            raise DomainError("run_inputs must be RunInputs")
        if not self.run_root or not isinstance(self.run_root, str):
            raise DomainError("run_root must be a non-empty string")
        if self.should_cancel is not None and not callable(self.should_cancel):
            raise DomainError("should_cancel must be a callable probe or None")
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
    generation_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_results", tuple(self.step_results))
        if not isinstance(self.manifest, FrozenDict):
            object.__setattr__(self, "manifest", FrozenDict(self.manifest))


@dataclass(slots=True)
class _GenerationContext:
    """Mutable per-generation bookkeeping for failure publication."""

    run_id: str
    run_root: str
    generation_id: str
    record: RunGeneration
    definition_digest: str | None = None
    plan: Any = None
    state: RunState | None = None
    step_results: list[Any] = field(default_factory=list)
    completed_step_ids: tuple[str, ...] = ()
    active_step_id: str | None = None
    terminal_published: bool = False
    terminal_status: str | None = None
    generation_started: bool = False


def _map_step_status(status: StepStatus) -> RunStepStatus:
    """Map a semantic step status onto its lifecycle record."""
    return {
        StepStatus.COMPLETED: RunStepStatus.COMPLETED,
        StepStatus.PARTIAL: RunStepStatus.PARTIAL,
        StepStatus.FAILED: RunStepStatus.FAILED,
        StepStatus.CANCELLED: RunStepStatus.CANCELLED,
    }[status]


def _search_xtb_binding(planned: Any, request: V4RunRequest) -> Any:
    """Return the xTB binding for a confgen search step, else None.

    Only a confgen step whose native scope carries a ``search`` section
    gets a binding (program ``xtb``: planned step execution wins, else the
    request default). Every other pure step keeps today's empty binding.
    Unresolvable xTB also yields None: the executor then fails the work
    item before anything is launched.
    """
    if planned.executor is not ExecutorCapability.CONFGEN:
        return None
    native = planned.scientific.native
    search = native.get("search") if isinstance(native, Mapping) else None
    if search is None:
        return None
    try:
        return resolve_execution_binding(
            program="xtb",
            planned=planned.execution,
            defaults=BindingRequestDefaults(executables=dict(request.executables.thaw())),
        )
    except DomainError:
        return None


def _declared_input_grouping(document: Any) -> dict[str, str]:
    """Return ``{input_name: grouping}`` for declared typed groupings."""
    from collections.abc import Mapping

    if not isinstance(document, Mapping):
        return {}
    raw_inputs = document.get("inputs")
    if not isinstance(raw_inputs, Mapping):
        return {}
    grouping: dict[str, str] = {}
    for name, declaration in raw_inputs.items():
        if isinstance(declaration, Mapping) and declaration.get("grouping") == "each_entity":
            grouping[str(name)] = "each_entity"
    return grouping


def _apply_declared_input_topology(name: str, structures: Any, declaration: Any) -> StructureSet:
    """Attach a declared input topology/charge/spin to every record.

    The declaration is the compiled ``RunInputDeclaration`` for *name*.
    A record-level patch semantically distinct from the declared patch
    fails closed (identical edge sets are accepted — provenance may
    differ); likewise a conflicting record charge/multiplicity.  The
    working graph then resolves exactly once on the import geometry for
    every persist-worthy record (declared or producer-attached patch),
    so descendants never re-perceive moved geometry.  Pure-legacy
    records pass through untouched.
    """
    from dataclasses import replace as _replace

    from ..science.topology import resolve_and_persist_kwargs as _persist_kwargs

    decl_patch = getattr(declaration, "topology", None)
    decl_charge = getattr(declaration, "charge", None)
    decl_mult = getattr(declaration, "multiplicity", None)
    rebuilt: list[StructureRecord] = []
    changed = False
    for record in structures:
        if not isinstance(record, StructureRecord):
            raise DomainError(
                f"run input {name!r} carries a non-structure member; "
                "topology attachment requires typed structure records"
            )
        patch = record.topology_patch
        if decl_patch is not None and not decl_patch.is_empty:
            if patch is None or patch.is_empty:
                if record.working_topology is not None:
                    raise DomainError(
                        f"run input {name!r} record {record.id!r} already carries "
                        "an authoritative working graph with no patch, and the "
                        "input declares a new topology patch; declare one "
                        "authority"
                    )
                patch = decl_patch
            elif not decl_patch.same_semantics(patch):
                raise DomainError(
                    f"run input {name!r} record {record.id!r} carries a topology "
                    "patch distinct from the declared input topology; "
                    "declare one authority"
                )
        charge = record.charge
        if decl_charge is not None:
            if charge is None:
                charge = decl_charge
            elif charge != decl_charge:
                raise DomainError(
                    f"run input {name!r} record {record.id!r} carries charge "
                    f"{charge} conflicting with the declared input charge "
                    f"{decl_charge}; declare it once"
                )
        multiplicity = record.multiplicity
        if decl_mult is not None:
            if multiplicity is None:
                multiplicity = decl_mult
            elif multiplicity != decl_mult:
                raise DomainError(
                    f"run input {name!r} record {record.id!r} carries multiplicity "
                    f"{multiplicity} conflicting with the declared input "
                    f"multiplicity {decl_mult}; declare it once"
                )
        merged = record
        if (
            patch is not record.topology_patch
            or charge != record.charge
            or multiplicity != record.multiplicity
        ):
            merged = _replace(
                record,
                topology_patch=patch,
                charge=charge,
                multiplicity=multiplicity,
            )
            changed = True
        graph_kwargs = _persist_kwargs(merged, merged.coordinates)
        if graph_kwargs:
            merged = _replace(merged, **graph_kwargs)
            changed = True
        rebuilt.append(merged)
    if not changed:
        if isinstance(structures, StructureSet):
            return structures
        return StructureSet(tuple(structures))
    return StructureSet.of(*rebuilt)


def _stamp_each_entity_grouping(name: str, structures: Any) -> StructureSet:
    """Derive group identity for an ``each_entity`` input."""
    from dataclasses import replace as _replace

    stamped: list[StructureRecord] = []
    for record in structures:
        if not isinstance(record, StructureRecord):
            raise DomainError(
                f"run input {name!r} carries a non-structure member; "
                "grouping identity requires typed structure records"
            )
        if record.group_key is None:
            record = _replace(record, group_key=record.id)
        stamped.append(record)
    return StructureSet.of(*stamped)


def _save_run_state_fenced(run_root: str, state: RunState, generation_id: str) -> None:
    """Write the step lifecycle state under the generation publication fence."""
    with arbitration.generation_publication_scope(
        run_root,
        expected_generation_id=generation_id,
        action="run-state publication",
    ):
        save_run_state(run_root, state)


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
        run_root = validate_run_root(request.run_root)
        os.makedirs(run_root, exist_ok=True)
        run_id = os.path.basename(request.run_root.rstrip(os.sep)) or "run"
        generation_id = new_generation_id()
        record = running_record(run_id=run_id, generation_id=generation_id)
        context = _GenerationContext(
            run_id=run_id,
            run_root=run_root,
            generation_id=generation_id,
            record=record,
        )
        try:
            return self._run_generation(request, context)
        except BaseException as error:
            # Current-generation terminal truth must exist before the
            # exception reaches the caller: status, generation, failure
            # location, and the completed partial steps.  Programmer
            # corruption is never swallowed — the original exception is
            # re-raised unchanged.
            self._publish_generation_failure(request, context, error)
            raise

    def _run_generation(self, request: V4RunRequest, context: _GenerationContext) -> V4RunReport:
        """Execute one generation; see :meth:`run` for the lifecycle."""
        run_id = context.run_id
        run_root = context.run_root
        generation_id = context.generation_id
        parsed_doc = parse_workflow_document(request.workflow_document)
        compiled = compile_workflow(parsed_doc)
        if not compiled.ok:
            raise DomainError(
                "V4 workflow does not compile: "
                + "; ".join(item.message for item in compiled.errors)
            )
        assert compiled.plan is not None
        plan = compiled.plan
        context.plan = plan
        context.definition_digest = plan.definition_digest
        state = self._load_or_init_run_state(
            run_root=run_root, run_id=run_id, definition_digest=plan.definition_digest
        )
        for planned in plan.steps:
            state = ensure_step(state, planned.step_id)
        # Install this generation as the run root's current owner BEFORE any
        # step state or publication is written.  A superseded writer loses
        # publication authority permanently at this point (CONTRACT 7).
        arbitration.begin_generation(
            run_root,
            generation_id=generation_id,
            run_id=run_id,
            definition_digest=plan.definition_digest,
        )
        _save_run_state_fenced(run_root, state, generation_id)
        context.state = state
        context.generation_started = True
        run_inputs = self._resolve_run_inputs(
            run_root=run_root,
            request=request,
            declarations=(
                {item.name: item for item in parsed_doc.definition.inputs}
                if parsed_doc.definition is not None
                else {}
            ),
        )
        materialized = MaterializedOutputs.empty()
        step_results: list[Any] = []
        cancelled_steps: list[str] = []
        supervisor = request.supervisor if request.supervisor is not None else self._supervisor
        should_cancel = request.should_cancel
        for planned in plan.steps:
            if not arbitration.generation_is_current(run_root, generation_id):
                # CONTRACT 7: once a newer generation owns the run root, this
                # writer must not execute or publish anything else.
                raise arbitration.StaleGenerationError(
                    f"generation {generation_id!r} was superseded before step "
                    f"{planned.step_id!r}; refusing further execution and publication"
                )
            if should_cancel is not None and should_cancel():
                # Live cancellation between steps: the remaining steps are
                # durable lifecycle truth (cancelled), never executed.  A
                # cancelled producer would block its consumer at assembly;
                # stopping at the boundary keeps the run publishable as a
                # cancelled manifest instead of an assembly error.
                cancelled_steps.append(planned.step_id)
                state = transition_step(
                    ensure_step(state, planned.step_id),
                    planned.step_id,
                    RunStepStatus.CANCELLED,
                )
                _save_run_state_fenced(run_root, state, generation_id)
                context.state = state
                continue
            state = transition_step(
                ensure_step(state, planned.step_id), planned.step_id, RunStepStatus.RUNNING
            )
            _save_run_state_fenced(run_root, state, generation_id)
            context.state = state
            context.active_step_id = planned.step_id
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
                generation_id=generation_id,
            )
            if result.step_id != planned.step_id:
                raise DomainError(
                    f"step executor returned {result.step_id!r} for {planned.step_id!r}"
                )
            step_results.append(result)
            context.step_results = step_results
            context.completed_step_ids = tuple(
                item.step_id for item in step_results if item.status.value == "completed"
            )
            materialized = _extend_materialized(plan, planned, materialized, result)
            digest = detect_published(run_root=run_root, step_id=planned.step_id)
            state = transition_step(
                state,
                planned.step_id,
                _map_step_status(result.status),
                published_step_result_digest=digest,
            )
            _save_run_state_fenced(run_root, state, generation_id)
            context.state = state
            context.active_step_id = None
        status = _evaluate_run_status(tuple(result.status for result in step_results))
        if cancelled_steps:
            status = "cancelled"
        # Terminal arbitration is the single winner authority: a durable
        # cancel claim (recorded before this claim) or a live cancellation
        # signal observed inside the lock wins over the requested status.
        # The last ``should_cancel`` probe is part of the arbitration, never
        # a separate pre-publication check.
        manifest, effective_status = self._publish_manifest(
            run_id=run_id,
            status=status,
            definition_digest=plan.definition_digest,
            step_results=tuple(step_results),
            plan=plan,
            run_root=run_root,
            generation_id=generation_id,
            run_inputs=run_inputs,
            cancel_probe=should_cancel,
        )
        context.terminal_published = True
        context.terminal_status = effective_status
        return V4RunReport(
            run_id=run_id,
            status=effective_status,
            definition_digest=plan.definition_digest,
            step_results=tuple(step_results),
            manifest=manifest,
            generation_id=generation_id,
        )

    def _publish_generation_failure(
        self,
        request: V4RunRequest,
        context: _GenerationContext,
        error: BaseException,
    ) -> None:
        """Best-effort current-generation terminal truth for a failed run."""
        failure = FrozenDict(
            {
                "type": type(error).__name__,
                "message": str(error)[:2000],
                "step_id": context.active_step_id,
                "blocked_downstream": "not assemblable" in str(error)
                or "unmaterialized producer" in str(error),
            }
        )
        if not context.generation_started:
            # The invocation never became a generation of this run root
            # (uncompilable document, or a different definition aimed at an
            # occupied run root).  The run root's last valid terminal truth
            # stays current; history is never reinterpreted under a
            # rejected digest.
            return
        if context.terminal_published:
            # The generation's terminal truth is already durable; re-confirm
            # it idempotently so a lifecycle-record write failure can never
            # leave a published terminal manifest without its generation
            # pointer.  A published terminal manifest is never downgraded.
            try:
                arbitration.finalize_generation(
                    context.run_root,
                    generation_id=context.generation_id,
                    requested_status=context.terminal_status or "failed",
                    cancel_probe=request.should_cancel,
                    failure=dict(failure),
                    active_step_id=context.active_step_id,
                )
            except Exception:
                pass
            return
        # The failing/blocked step is durable lifecycle truth, not RUNNING.
        # The write is fenced on generation ownership so a superseded writer
        # can never overwrite the newer generation's step state.
        try:
            if context.state is not None and context.active_step_id is not None:
                current = context.state.step(context.active_step_id)
                if current is not None and current.status is RunStepStatus.RUNNING:
                    failed_state = transition_step(
                        context.state, context.active_step_id, RunStepStatus.FAILED
                    )
                    _save_run_state_fenced(context.run_root, failed_state, context.generation_id)
                    context.state = failed_state
        except Exception:
            pass
        effective_status = "failed"
        if context.plan is not None and context.definition_digest is not None:
            try:
                _manifest, effective_status = self._publish_manifest(
                    run_id=context.run_id,
                    status="failed",
                    definition_digest=context.definition_digest,
                    step_results=tuple(context.step_results),
                    plan=context.plan,
                    run_root=context.run_root,
                    generation_id=context.generation_id,
                    run_inputs=request.run_inputs,
                    cancel_probe=request.should_cancel,
                    terminal_failure=dict(failure),
                    terminal_active_step_id=context.active_step_id,
                )
            except Exception:
                effective_status = "failed"
        try:
            effective_status = arbitration.finalize_generation(
                context.run_root,
                generation_id=context.generation_id,
                requested_status=effective_status,
                cancel_probe=request.should_cancel,
                failure=dict(failure),
                active_step_id=context.active_step_id,
            )
        except Exception:
            pass
        context.terminal_published = True
        context.terminal_status = effective_status

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
    def _resolve_run_inputs(
        *,
        run_root: str,
        request: V4RunRequest,
        declarations: Mapping[str, Any] | None = None,
    ) -> RunInputs:
        """Reconcile typed run inputs against durable import maps.

        Inputs carrying a raw source text in ``request.import_sources``
        reload the persisted entity IDs for identical bytes (concurrent
        first imports arbitrate to one winner) and fail closed on changed
        bytes.  Inputs without a source text are validated against a
        persisted entity snapshot so a silently re-minted import can never
        masquerade as the original entities.  Declared input topology and
        charge/spin (``declarations``, the compiled ``RunInputDeclaration``
        records) then attach to every record of that input: a conflicting
        record-level patch or charge fails closed, an identical semantic
        patch is accepted (provenance may differ), and the working graph
        resolves exactly once on the import geometry before execution.
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
        declared = declarations or {}
        for name, current in list(structures.items()):
            declaration = declared.get(name)
            if declaration is not None:
                structures[name] = _apply_declared_input_topology(name, current, declaration)
        grouping = _declared_input_grouping(request.workflow_document)
        for name, current in list(structures.items()):
            if grouping.get(name) == "each_entity":
                structures[name] = _stamp_each_entity_grouping(name, current)
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
        generation_id: str,
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
            from ..execution.binding_resolution import effective_native_env

            # ONE immutable native-environment snapshot per step.  Ambient
            # inheritance is explicit producer-side policy (os.environ) and
            # is fully digested; binding env wins on collision.  The SAME
            # snapshot feeds measurement and provenance.
            native_env = FrozenDict(effective_native_env(binding, inherit=os.environ))
            environment = self._measure_environment(
                planned=planned,
                binding=binding,
                adapter=adapter,
                relevant_env=native_env,
            )
            provenance = None
        else:
            # Pure executors (confgen/transform): no native program,
            # hence no binding and no binary measurement.  The measured
            # implementation environment uses the shared helper with the
            # real registered executor contract version, so implementation
            # changes invalidate reuse.
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
            binding = _search_xtb_binding(planned, request)
            provenance = FrozenDict(dict(provenance))
        batch = BatchStepExecutor(executor)
        if supervisor is not None:
            batch = batch.with_supervisor(supervisor)

        def _ownership_guard() -> None:
            """Refuse step publication once a newer generation owns the root."""
            if not arbitration.generation_is_current(run_root, generation_id):
                raise arbitration.StaleGenerationError(
                    f"generation {generation_id!r} was superseded before publishing "
                    f"step {planned.step_id!r}; stale step publication refused"
                )

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
            should_cancel=request.should_cancel,
            ownership_guard=_ownership_guard,
            generation_id=generation_id,
        )
        with SqliteWorkItemStore.open(store_path(run_root, planned.step_id)) as store:
            return batch.execute_step_resumable(
                step_request,
                store=store,
                run_root=run_root,
                owner_token=request.owner_token,
            )

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
                candidate, adapter=adapter, relevant_env=relevant_env
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
        generation_id: str,
        run_inputs: Any = None,
        cancel_probe: Any = None,
        terminal_failure: dict[str, Any] | None = None,
        terminal_active_step_id: str | None = None,
    ) -> tuple[FrozenDict, str]:
        """Claim terminal ownership, publish the manifest, confirm the winner."""
        completed_step_ids = tuple(
            item.step_id for item in step_results if item.status.value == "completed"
        )
        with arbitration.terminal_publication(
            run_root,
            generation_id=generation_id,
            requested_status=status,
            cancel_probe=cancel_probe,
        ) as scope:
            effective_status = scope.claim.status
            manifest = self._build_and_write_manifest(
                run_id=run_id,
                status=effective_status,
                definition_digest=definition_digest,
                step_results=step_results,
                plan=plan,
                run_root=run_root,
                generation_id=generation_id,
                run_inputs=run_inputs,
            )
            scope.confirm(
                manifest_generation_id=generation_id,
                completed_step_ids=completed_step_ids,
                active_step_id=terminal_active_step_id,
                failure=terminal_failure,
            )
        return manifest, effective_status

    def _build_and_write_manifest(
        self,
        *,
        run_id: str,
        status: str,
        definition_digest: str,
        step_results: tuple[Any, ...],
        plan: Any,
        run_root: str,
        generation_id: str,
        run_inputs: Any = None,
    ) -> FrozenDict:
        """Build, validate, and atomically write one run-result manifest."""
        import jsonschema

        import confflow

        from ..producer.contract import build_run_result_manifest, run_result_json_schema
        from ..producer.run_result import (
            artifact_entry,
            result_ref_entries,
            step_entry,
        )

        run_input_results = getattr(run_inputs, "results", ()) if run_inputs is not None else ()

        semantic = {
            planned.step_id: planned.step_semantic_digest
            for planned in plan.steps
            if getattr(planned, "step_semantic_digest", None) is not None
        }
        steps: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
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
            for artifact in result.artifacts:
                artifacts.append(artifact_entry(artifact))
        # Authoritative ResultRef universe: the current generation's actually
        # published results plus legitimate run-input references.
        # R2.3a: no analysis citations remain; the index still guards
        # duplicate result ids fail-closed for retained steps.
        try:
            results = result_ref_entries(tuple(step_results), run_input_results=run_input_results)
        except ValueError as exc:
            raise DomainError(
                f"run-result manifest cannot build its reference index: {exc}"
            ) from exc
        analyses: list[dict[str, Any]] = []
        manifest = build_run_result_manifest(
            run_id=run_id,
            status=status,
            definition_digest=definition_digest,
            producer_version=getattr(confflow, "__version__", "unknown"),
            steps=steps,
            analyses=analyses,
            artifacts=artifacts,
            results=results,
            generation_id=generation_id,
        )
        try:
            jsonschema.validate(instance=manifest, schema=run_result_json_schema())
        except Exception as exc:
            raise DomainError(f"run-result manifest fails its schema: {exc}") from exc
        target = os.path.join(validate_run_root(run_root), RUN_RESULT_FILENAME)
        publish_bytes(target, canonical_json_bytes(manifest))
        return FrozenDict(manifest)


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
    """Validate an input without raw source bytes against its snapshot."""
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
    payload = {
        "input_name": input_name,
        "entity_ids": list(entity_ids),
        "geometry_digests": list(geometry_digests),
    }
    publish_bytes(target, canonical_json_bytes(payload))


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
    """Add one step result to the materialized producer outputs."""
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
