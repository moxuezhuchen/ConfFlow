#!/usr/bin/env python3

"""V4-5 durable resume for multi-output producers.

All resume decisions run through the real production machinery
(``BatchStepExecutor.execute_step_resumable`` + ``SqliteWorkItemStore`` +
:func:`evaluate_reuse`); native invocations are counted at the process
boundary via a logging wrapper, so duplicates fail loudly:

- IRC complete → resume runs 0 native;
- GOAT complete → resume runs 0 native;
- 19/20 (one failed) → resume runs exactly 1 native;
- remote IRC complete + producer restart (fresh transport, new worker root)
  → full reuse, no duplicate launch;
- QST completed + atom mapping unchanged → reuse;
- QST mapping changed (products swapped across groups) → invalidation, the
  work-item digest moves, nothing executes;
- endpoint ids are bit-identical across resume (order + ids).

Seeded completed results are synthetic-but-rule-bound (frozen
:mod:`confflow.execution.output_identity` ids/lineage, real
``resolve_named_inputs``/``ts_output_lineage`` for QST); the register →
decide → reuse-or-launch path they traverse is production code.  The IRC
executor seam is the same test-local adapter as the TSPES file (blocked
production seam: ``OrcaAdapter.parse_native_result`` IRC routing).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.artifact import ArtifactSet
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.domain.diagnostics import Diagnostic, DiagnosticSeverity
from confflow.domain.result import ResultSet
from confflow.domain.structure import StructureRecord
from confflow.domain.units import Unit
from confflow.domain.work_item import RecoveryInfo, ResultError, Timing, WorkItemResult
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.output_identity import (
    CONFORMER_ROLE,
    conformer_output_id,
    endpoint_lineage,
    multi_output_structure_id,
)
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_ensemble import EnsembleProfile
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.persistence import OwnerIdentity
from confflow.persistence.contracts import store_path
from confflow.persistence.reuse import build_producer_provenance
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.registry import get_program_adapter
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure,
    v4_doc,
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_IRC = FAKES_DIR / "fake_irc.py"
STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

IRC_WRAPPER_SCRIPT = """#!/bin/sh
# Counting wrapper: logs every native invocation basename, then execs fake_irc.
base=$(basename "$1")
printf '%s\\n' "$base" >> "$IRC_COUNT_FILE"
exec python3 "$IRC_FAKE_REAL" "$@"
"""


def _install_irc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Install the counting IRC wrapper; return ``(wrapper, count_file)``."""
    bin_dir = tmp_path / "bin-irc"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "ircfake"
    wrapper.write_text(IRC_WRAPPER_SCRIPT)
    wrapper.chmod(0o755)
    count_file = tmp_path / "irc.count"
    count_file.write_text("")
    fail_file = tmp_path / "irc.fail"
    fail_file.write_text("")
    monkeypatch.setenv("IRC_FAKE_REAL", str(FAKE_IRC))
    monkeypatch.setenv("IRC_COUNT_FILE", str(count_file))
    monkeypatch.setenv("FAKE_IRC_MODE", "success")
    monkeypatch.setenv("FAKE_IRC_ORDER", "reverse_first")
    monkeypatch.setenv("IRC_FAIL_FILE", str(fail_file))
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper, count_file


def _native_count(count_file: Path) -> int:
    """Return the number of logged native invocations."""
    return len([line for line in count_file.read_text().splitlines() if line.strip()])


def _goat_doc() -> dict[str, Any]:
    """Build a single-step ensemble-shaped (GOAT) document."""
    step = calc_step(
        "s_goat",
        program="orca",
        adapter="standard",
        profile="ensemble",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "GOAT B3LYP", "goat": {"MaxIter": 50}},
        checks=["normal_termination", "geometry_required"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
        seed=7,
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def _compile(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def _batch() -> BatchStepExecutor:
    """Build a batch executor with a fresh supervisor."""
    return BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())


def _request(
    plan: Any,
    step_id: str,
    items: tuple[Any, ...],
    run_root: str,
    executable: Path,
    *,
    adapter: Any,
    profile: Any,
    checks: tuple[Any, ...],
) -> StepExecutionRequest:
    """Build a durable step request under the complete-environment rule.

    The measured environment and the threaded ``native_env`` snapshot are
    the same mapping (ambient inheritance + declared binding env), exactly
    as the formal application builds them, so seeded rows and resume
    requests agree on execution identity.
    """
    planned = next(step for step in plan.steps if step.step_id == step_id)
    from confflow.execution.binding_resolution import effective_native_env

    binding = ExecutionBinding(binding_id="test", executable=str(executable), env=FrozenDict({}))
    native_env = FrozenDict(effective_native_env(binding, inherit=os.environ))
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=profile,
        checks=checks,
        recovery=RECOVERIES["none"],
        execution_binding=binding,
        run_root=run_root,
        environment=_measured_env(adapter, executable, relevant_env=native_env),
        native_env=native_env,
        definition_digest=plan.definition_digest,
        executor_capability=getattr(planned.executor, "value", str(planned.executor)),
    )


def _measured_env(adapter: Any, executable: Any, *, relevant_env: Any = None) -> Any:
    """Measure the fake executable for the durable env axis."""
    from confflow.execution.environment import EnvironmentMeasurer

    return EnvironmentMeasurer().build_environment(
        str(executable), adapter=adapter, relevant_env=relevant_env
    )


def _provenance_for(request: StepExecutionRequest) -> FrozenDict:
    """Build the producer provenance the batch executor will compute."""
    assert request.adapter is not None and request.profile is not None
    recovery_version = getattr(request.recovery, "contract_version", None) or "none"
    return build_producer_provenance(
        adapter_version=request.adapter.adapter_version,
        profile_version=request.profile.contract_version,
        check_versions={check.name: check.contract_version for check in request.checks},
        recovery_version=recovery_version,
        parser_version=request.adapter.parser_version,
    )


def _seed_completed(
    store: SqliteWorkItemStore,
    item: Any,
    step_semantic_digest: str,
    provenance: FrozenDict,
    result: WorkItemResult,
    environment_digest: str,
) -> None:
    """Seed one COMPLETED result: register → claim → record.

    The registration environment must equal the resume request's
    measured digest: seeded rows stand in for previously committed
    production results, which always record their execution truth.
    """
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=step_semantic_digest,
        environment_digest=environment_digest,
        producer_provenance=dict(provenance.thaw()),
    )
    assert store.claim(item.id, owner=OwnerIdentity(owner_token="seed")) is True
    store.record_finished(result)


def _seed_failed(
    store: SqliteWorkItemStore,
    item: Any,
    step_semantic_digest: str,
    provenance: FrozenDict,
    result: WorkItemResult,
    environment_digest: str,
) -> None:
    """Seed one FAILED result: register → claim → record."""
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=step_semantic_digest,
        environment_digest=environment_digest,
        producer_provenance=dict(provenance.thaw()),
    )
    assert store.claim(item.id, owner=OwnerIdentity(owner_token="seed")) is True
    store.record_finished(result)


def _shifted(
    coordinates: tuple[tuple[float, float, float], ...], delta: float
) -> tuple[tuple[float, float, float], ...]:
    """Shift coordinates deterministically along x."""
    return tuple((x + delta, y, z) for x, y, z in coordinates)


class _FakeSubject:
    """Minimal subject carrier for stamping seeded singleton results."""

    def __init__(self, subject_id: str) -> None:
        self.id = subject_id


def _stamped_results(
    records: Any, item: Any, step_id: str, energies: Any, *, discriminator: Any = None
) -> Any:
    """Stamp seeded fake results with producer-scoped ids (test-only).

    Seeded store rows stand in for previously committed production
    results, which always carry ``result_id``; the stamp binds the
    producing item's semantic digest as the producer digest.
    """
    from confflow.domain.result import ResultSet as _ResultSet
    from confflow.domain.result import ScientificResult as _ScientificResult
    from confflow.domain.result import make_result_id as _make_result_id

    stamped = []
    for record, energy in zip(records, energies):
        base_kwargs: dict[str, Any] = {
            "kind": "energy",
            "value": energy,
            "unit": Unit.HARTREE,
            "subject_structure_id": record.id,
            "source_step_id": step_id,
            "source_work_item_id": item.id,
        }
        disc = discriminator(record) if callable(discriminator) else discriminator
        base_kwargs["result_id"] = _make_result_id(
            step_id=step_id,
            work_item_id=item.id,
            kind="energy",
            subject_structure_id=record.id,
            **({"discriminator": disc} if disc else {}),
            producer_digest=item.semantic_digest,
        )
        stamped.append(_ScientificResult(**base_kwargs))
    return _ResultSet.of(*stamped)


def _goat_result(item: Any, *, step_id: str = "s_goat", members: int = 3) -> WorkItemResult:
    """Build the rule-bound conformer-ensemble result for a GOAT *item*."""
    driving = item.named_inputs.structures["structure"][0]
    parent_ids, lineage_root, group_key = endpoint_lineage(driving)
    records = [
        StructureRecord(
            id=conformer_output_id(item.logical_key, index),
            atoms=tuple(driving.atoms),
            coordinates=_shifted(tuple(driving.coordinates), 0.01 * (index + 1)),
            charge=driving.charge,
            multiplicity=driving.multiplicity,
            parent_ids=parent_ids,
            lineage_root_id=lineage_root,
            source_step_id=step_id,
            source_work_item_id=item.id,
            role=CONFORMER_ROLE,
            ordinal=index,
            group_key=group_key,
            metadata=FrozenDict({"member_index": index}),
        )
        for index in range(members)
    ]
    results = _stamped_results(
        records,
        item,
        step_id,
        [-76.0 - index for index in range(len(records))],
        discriminator=lambda record: f"member:{record.ordinal}",
    )
    return WorkItemResult(
        work_item_id=item.id,
        status=WorkItemStatus.COMPLETED,
        structures=StructureSet.of(*records),
        results=results,
        artifacts=ArtifactSet(),
        diagnostics=(),
        timing=Timing(started_at=1720000000.0, finished_at=1720000001.0, duration_seconds=1.0),
        error=None,
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=item.semantic_digest,
    )


def _failed_result(item: Any) -> WorkItemResult:
    """Build a retryable FAILED result for *item*."""
    diagnostic = Diagnostic(
        code="native_execution_error",
        message="seeded failure for resume",
        severity=DiagnosticSeverity.ERROR,
        step_id=item.step_id,
        work_item_id=item.id,
        logical_key=item.logical_key,
    )
    return WorkItemResult(
        work_item_id=item.id,
        status=WorkItemStatus.FAILED,
        structures=StructureSet(),
        results=ResultSet(),
        artifacts=ArtifactSet(),
        diagnostics=(diagnostic,),
        timing=Timing(finished_at=1720000000.0, duration_seconds=0.0),
        error=ResultError(
            code="native_execution_error",
            message="seeded failure for resume",
            retryable=True,
        ),
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=item.semantic_digest,
    )


def _qst_result(item: Any, *, step_id: str = "s_qst") -> WorkItemResult:
    """Build the rule-bound TS-candidate result for a QST *item*.

    No production QST TS result profile exists yet, so the candidate shape
    is synthetic-but-rule-bound: parents in semantic slot order and lineage
    from :func:`ts_output_lineage`, the id from the frozen
    :func:`multi_output_structure_id`.
    """
    from confflow.execution.named_structures import (
        resolve_named_inputs,
        ts_output_lineage,
        validate_named_compatibility,
    )

    resolved = resolve_named_inputs(item, require_guess=False)
    charge, multiplicity = validate_named_compatibility(resolved)
    parent_ids, lineage_root, group_key = ts_output_lineage(resolved)
    candidate_id = multi_output_structure_id(item.logical_key, "ts_candidate", 0)
    coordinates = tuple(
        tuple((ra + pa) / 2.0 for ra, pa in zip(r_point, p_point))
        for r_point, p_point in zip(resolved.reactant.coordinates, resolved.product.coordinates)
    )
    record = StructureRecord(
        id=candidate_id,
        atoms=tuple(resolved.reactant.atoms),
        coordinates=coordinates,
        charge=charge,
        multiplicity=multiplicity,
        parent_ids=parent_ids,
        lineage_root_id=lineage_root,
        source_step_id=step_id,
        source_work_item_id=item.id,
        role="ts_candidate",
        ordinal=0,
        group_key=group_key,
        metadata=FrozenDict({"slots": ("reactant", "product")}),
    )
    return WorkItemResult(
        work_item_id=item.id,
        status=WorkItemStatus.COMPLETED,
        structures=StructureSet.of(record),
        results=_stamped_results(
            [_FakeSubject(candidate_id)],
            item,
            step_id,
            [-76.400001],
            discriminator="ts_candidate",
        ),
        artifacts=ArtifactSet(),
        diagnostics=(),
        timing=Timing(started_at=1720000000.0, finished_at=1720000001.0, duration_seconds=1.0),
        error=None,
        recovery=RecoveryInfo(profile="none", attempted=False),
        semantic_digest=item.semantic_digest,
    )


def _reuse_hits(result: Any) -> int:
    """Count item results carrying a reuse diagnostic."""
    return sum(
        1 for item in result.item_results if any(d.code == "reuse_hit" for d in item.diagnostics)
    )


class TestGoatResume:
    """GOAT step resume: complete → 0 native, ids stable."""

    def test_goat_complete_resumes_zero_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file = _install_irc(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_goat_doc())
        structures = StructureSet.of(
            *(
                structure(f"seed-{i}", group_key=f"ens-{i}", lineage_root_id=f"root-{i}")
                for i in range(4)
            )
        )
        items = assemble(plan, run_inputs(structures={"structures": structures})).for_step("s_goat")
        assert len(items) == 4
        adapter = get_program_adapter("orca")  # real adapter: fakes speak real grammar
        profile = EnsembleProfile()
        checks = (CHECKS["normal_termination"], CHECKS["geometry_required"])
        request = _request(
            plan,
            "s_goat",
            tuple(items),
            run_root,
            wrapper,
            adapter=adapter,
            profile=profile,
            checks=checks,
        )
        planned = next(step for step in plan.steps if step.step_id == "s_goat")
        provenance = _provenance_for(request)
        with SqliteWorkItemStore.open(store_path(run_root, "s_goat")) as store:
            for item in items:
                _seed_completed(
                    store,
                    item,
                    planned.step_semantic_digest,
                    provenance,
                    _goat_result(item),
                    request.environment.digest(),
                )
            resumed = _batch().execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl-resume"
            )
        assert resumed.summary["completed"] == 4
        assert resumed.status is StepStatus.COMPLETED
        assert _reuse_hits(resumed) == 4
        assert _native_count(count_file) == 0
        assert len(tuple(resumed.structures)) == 12
        by_item = {result.work_item_id: result for result in resumed.item_results}
        for item in items:
            result = by_item[item.id]
            assert {record.role for record in result.structures} == {CONFORMER_ROLE}
            assert sorted(record.ordinal for record in result.structures) == [0, 1, 2]


# R2.2 (G18): TestIrcResume, TestQstMappingResume and TestEndpointIdStability
# are retired with the path_endpoints profile and the named_structures
# adapter. TestGoatResume stays: the ensemble profile is retained as the
# ConfGen result profile.
