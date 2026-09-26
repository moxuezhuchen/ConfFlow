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
from confflow.domain.result import ResultSet, ScientificResult
from confflow.domain.structure import StructureRecord
from confflow.domain.units import Unit
from confflow.domain.work_item import RecoveryInfo, ResultError, Timing, WorkItemResult
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.native import (
    GeometryOutput,
    NativeResult,
    ProducedFile,
    ProgramName,
)
from confflow.execution.output_identity import (
    CONFORMER_ROLE,
    PATH_ENDPOINT_FORWARD_ROLE,
    PATH_ENDPOINT_REVERSE_ROLE,
    conformer_output_id,
    endpoint_lineage,
    endpoint_output_id,
    multi_output_structure_id,
)
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_ensemble import EnsembleProfile
from confflow.execution.profile_path_endpoints import PathEndpointsProfile
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.persistence import OwnerIdentity
from confflow.persistence.contracts import store_path
from confflow.persistence.reuse import build_producer_provenance
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.orca.path import parse_path_endpoints
from confflow.remote.transport import RemoteTransport
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure,
    v4_doc,
)
from tests.v4.fakes.fake_irc import (
    FORWARD_ENERGY,
    REVERSE_ENERGY,
    parse_inp_coordinates,
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


class _IrcTestAdapter:
    """Test seam: real ORCA adapter with IRC-dialect parsing (see tspes file)."""

    def __init__(self) -> None:
        from confflow.programs.registry import get_program_adapter

        self._real = get_program_adapter("orca")

    @property
    def program_name(self) -> ProgramName:
        return ProgramName.ORCA

    @property
    def adapter_version(self) -> str:
        return "test.adapter.irc.v1"

    @property
    def parser_version(self) -> str:
        return "test.parser.irc.v1"

    @property
    def input_extension(self) -> str:
        return self._real.input_extension

    @property
    def log_extension(self) -> str:
        return self._real.log_extension

    @property
    def default_executable(self) -> str:
        return self._real.default_executable

    def materialize_native_input(self, inputs: Any) -> Any:
        return self._real.materialize_native_input(inputs)

    def build_execution_request(
        self,
        materialized: Any,
        *,
        executable: str,
        work_dir: str,
        env: dict[str, str],
        walltime_seconds: float | None,
    ) -> Any:
        return self._real.build_execution_request(
            materialized,
            executable=executable,
            work_dir=work_dir,
            env=env,
            walltime_seconds=walltime_seconds,
        )

    def parse_native_result(
        self, *, work_dir: str, log_file_name: str, materialized: Any
    ) -> NativeResult:
        log_path = os.path.join(work_dir, log_file_name)
        try:
            with open(log_path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise ValueError(f"native_parse_error: missing IRC log: {exc}") from exc
        atoms, _ = parse_inp_coordinates(os.path.join(work_dir, materialized.main_input_name))
        endpoints = parse_path_endpoints(text, atoms=atoms)
        produced: list[ProducedFile] = []
        stem, _ = os.path.splitext(log_file_name)
        for suffix, role in (
            ("out", "native_output"),
            ("inp", "native_input"),
            ("xyz", "native_geometry"),
            ("gbw", "checkpoint_wavefunction"),
            ("err", "stderr"),
        ):
            name = log_file_name if suffix == "out" else f"{stem}.{suffix}"
            candidate = os.path.join(work_dir, name)
            if not os.path.isfile(candidate):
                continue
            try:
                size = os.path.getsize(candidate)
            except OSError:
                continue
            produced.append(ProducedFile(name=name, role=role, size_bytes=int(size)))
        return NativeResult(
            program=ProgramName.ORCA,
            terminated_normally=True,
            geometry_output=GeometryOutput.NONE,
            final_geometry=None,
            energies_hartree=FrozenDict({}),
            frequencies_cm=(),
            native_metadata=FrozenDict({"irc_dialect": "confflow-irc-v1"}),
            produced_files=tuple(produced),
            parser_diagnostics=(),
            log_file_name=log_file_name,
            path_endpoints=endpoints,
            ensemble_members=(),
        )

    def discover_artifacts(self, **kwargs: Any) -> Any:
        return self._real.discover_artifacts(**kwargs)

    def environment_probe(self, executable: str) -> dict[str, Any]:
        return self._real.environment_probe(executable)


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


def _irc_doc(*, step_id: str = "s_irc", profile: str = "path_endpoints") -> dict[str, Any]:
    """Build a single-step IRC-shaped document."""
    step = calc_step(
        step_id,
        program="orca",
        adapter="standard",
        profile=profile,
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "IRC B3LYP D3BJ"},
        checks=["normal_termination", "geometry_required"],
        scheduler={"max_parallel_items": 8},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def _goat_doc() -> dict[str, Any]:
    """Build a single-step ensemble-shaped (GOAT) document."""
    step = calc_step(
        "s_goat",
        program="orca",
        adapter="standard",
        profile="ensemble",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "GOAT B3LYP"},
        checks=["normal_termination", "geometry_required"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def _qst_doc() -> dict[str, Any]:
    """Build the QST2 document (reactant/product paired by group key)."""
    step = calc_step(
        "s_qst",
        program="orca",
        adapter="named_structures",
        profile="path_endpoints",
        bindings={
            "reactant": {"source": {"run": "reactants"}, "pairing": "by_group_key"},
            "product": {"source": {"run": "products"}, "pairing": "by_group_key"},
        },
        native={"keyword": "QST2 B3LYP"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc(
        [step],
        inputs={
            "reactants": {"kind": "structure", "cardinality": "many"},
            "products": {"kind": "structure", "cardinality": "many"},
        },
    )


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
    """Build a durable step request."""
    planned = next(step for step in plan.steps if step.step_id == step_id)
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=profile,
        checks=checks,
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(executable), env=FrozenDict({})
        ),
        run_root=run_root,
        environment=None,
        definition_digest=plan.definition_digest,
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
    )


def _seed_completed(
    store: SqliteWorkItemStore,
    item: Any,
    step_semantic_digest: str,
    provenance: FrozenDict,
    result: WorkItemResult,
) -> None:
    """Seed one COMPLETED result: register → claim → record."""
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=step_semantic_digest,
        environment_digest=None,
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
) -> None:
    """Seed one FAILED result: register → claim → record."""
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=step_semantic_digest,
        environment_digest=None,
        producer_provenance=dict(provenance.thaw()),
    )
    assert store.claim(item.id, owner=OwnerIdentity(owner_token="seed")) is True
    store.record_finished(result)


def _shifted(
    coordinates: tuple[tuple[float, float, float], ...], delta: float
) -> tuple[tuple[float, float, float], ...]:
    """Shift coordinates deterministically along x."""
    return tuple((x + delta, y, z) for x, y, z in coordinates)


def _irc_result(item: Any, *, step_id: str = "s_irc") -> WorkItemResult:
    """Build the rule-bound two-endpoint result for an IRC *item*."""
    driving = item.named_inputs.structures["structure"][0]
    parent_ids, lineage_root, group_key = endpoint_lineage(driving)
    records = []
    for direction, role, delta, _energy in (
        ("forward", PATH_ENDPOINT_FORWARD_ROLE, 0.02, FORWARD_ENERGY),
        ("reverse", PATH_ENDPOINT_REVERSE_ROLE, -0.02, REVERSE_ENERGY),
    ):
        record_id = endpoint_output_id(item.logical_key, direction)
        records.append(
            StructureRecord(
                id=record_id,
                atoms=tuple(driving.atoms),
                coordinates=_shifted(tuple(driving.coordinates), delta),
                charge=driving.charge,
                multiplicity=driving.multiplicity,
                parent_ids=parent_ids,
                lineage_root_id=lineage_root,
                source_step_id=step_id,
                source_work_item_id=item.id,
                role=role,
                ordinal=0,
                group_key=group_key,
                metadata=FrozenDict({"direction": direction}),
            )
        )
    results = ResultSet.of(
        *(
            ScientificResult(
                kind="energy",
                value=energy,
                unit=Unit.HARTREE,
                subject_structure_id=record.id,
                source_step_id=step_id,
                source_work_item_id=item.id,
            )
            for record, energy in zip(records, (FORWARD_ENERGY, REVERSE_ENERGY))
        )
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
    results = ResultSet.of(
        *(
            ScientificResult(
                kind="energy",
                value=-76.0 - index,
                unit=Unit.HARTREE,
                subject_structure_id=record.id,
                source_step_id=step_id,
                source_work_item_id=item.id,
            )
            for index, record in enumerate(records)
        )
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
        for r_point, p_point in zip(
            resolved.reactant.coordinates, resolved.product.coordinates
        )
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
        results=ResultSet.of(
            ScientificResult(
                kind="energy",
                value=-76.400001,
                unit=Unit.HARTREE,
                subject_structure_id=candidate_id,
                source_step_id=step_id,
                source_work_item_id=item.id,
            )
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
        1
        for item in result.item_results
        if any(d.code == "reuse_hit" for d in item.diagnostics)
    )


class TestIrcResume:
    """IRC step resume: complete → 0 native, 19/20 → 1 native."""

    def test_irc_complete_resumes_zero_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file = _install_irc(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_irc_doc())
        structures = StructureSet.of(
            *(
                structure(
                    f"ts{i:02d}",
                    group_key=f"rxn-{i:02d}",
                    lineage_root_id=f"root-{i:02d}",
                    offset=float(i) * 0.013,
                )
                for i in range(20)
            )
        )
        items = assemble(plan, run_inputs(structures={"structures": structures})).for_step(
            "s_irc"
        )
        assert len(items) == 20
        adapter = _IrcTestAdapter()
        profile = PathEndpointsProfile()
        checks = (CHECKS["normal_termination"], CHECKS["geometry_required"])
        request = _request(plan, "s_irc", tuple(items), run_root, wrapper,
                           adapter=adapter, profile=profile, checks=checks)
        planned = next(step for step in plan.steps if step.step_id == "s_irc")
        provenance = _provenance_for(request)
        seeded_ids: list[str] = []
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            for item in items:
                result = _irc_result(item)
                seeded_ids.extend(record.id for record in result.structures)
                _seed_completed(store, item, planned.step_semantic_digest, provenance, result)
            resumed = _batch().execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl-resume"
            )
        assert resumed.summary["completed"] == 20
        assert resumed.status is StepStatus.COMPLETED
        assert _reuse_hits(resumed) == 20
        assert _native_count(count_file) == 0
        assert [record.id for record in resumed.structures] == seeded_ids

    def test_19_of_20_resumes_single_native(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file = _install_irc(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_irc_doc())
        structures = StructureSet.of(
            *(structure(f"ts{i:02d}", group_key=f"rxn-{i:02d}") for i in range(20))
        )
        items = assemble(plan, run_inputs(structures={"structures": structures})).for_step(
            "s_irc"
        )
        adapter = _IrcTestAdapter()
        profile = PathEndpointsProfile()
        checks = (CHECKS["normal_termination"], CHECKS["geometry_required"])
        request = _request(plan, "s_irc", tuple(items), run_root, wrapper,
                           adapter=adapter, profile=profile, checks=checks)
        planned = next(step for step in plan.steps if step.step_id == "s_irc")
        provenance = _provenance_for(request)
        failed_item = next(item for item in items if item.logical_key == "s_irc:ts13")
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            for item in items:
                if item.id == failed_item.id:
                    _seed_failed(
                        store, item, planned.step_semantic_digest, provenance,
                        _failed_result(item),
                    )
                else:
                    _seed_completed(
                        store, item, planned.step_semantic_digest, provenance,
                        _irc_result(item),
                    )
            resumed = _batch().execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl-resume"
            )
        assert resumed.summary["completed"] == 20
        assert resumed.status is StepStatus.COMPLETED
        assert _reuse_hits(resumed) == 19
        assert _native_count(count_file) == 1
        assert len(tuple(resumed.structures)) == 40
        retried = next(
            result for result in resumed.item_results if result.work_item_id == failed_item.id
        )
        assert retried.is_completed
        assert not any(d.code == "reuse_hit" for d in retried.diagnostics)
        assert {record.role for record in retried.structures} == {
            PATH_ENDPOINT_FORWARD_ROLE,
            PATH_ENDPOINT_REVERSE_ROLE,
        }

    def test_remote_restart_reuses_without_duplicate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file = _install_irc(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_irc_doc())
        structures = StructureSet.of(
            *(structure(f"ts{i:02d}", group_key=f"rxn-{i:02d}") for i in range(4))
        )
        items = assemble(plan, run_inputs(structures={"structures": structures})).for_step(
            "s_irc"
        )
        adapter = _IrcTestAdapter()
        profile = PathEndpointsProfile()
        checks = (CHECKS["normal_termination"], CHECKS["geometry_required"])
        planned = next(step for step in plan.steps if step.step_id == "s_irc")
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            first_request = _request(
                plan, "s_irc", tuple(items), run_root, wrapper,
                adapter=adapter, profile=profile, checks=checks,
            )
            provenance = _provenance_for(first_request)
            for item in items:
                _seed_completed(
                    store, item, planned.step_semantic_digest, provenance,
                    _irc_result(item),
                )
            first_transport = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "worker-a")
            )
            first = _batch().execute_step_resumable(
                first_request,
                store=store,
                run_root=run_root,
                owner_token="ctl-a",
                transport=first_transport,
            )
            assert _reuse_hits(first) == 4
            assert _native_count(count_file) == 0
            # A restarted producer with a fresh transport and worker root
            # recovers the prior results instead of relaunching.
            second_transport = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "worker-b")
            )
            second = _batch().execute_step_resumable(
                _request(
                    plan, "s_irc", tuple(items), run_root, wrapper,
                    adapter=adapter, profile=profile, checks=checks,
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl-b",
                transport=second_transport,
            )
            assert _reuse_hits(second) == 4
            assert _native_count(count_file) == 0
            assert second.summary["completed"] == 4
            assert [record.id for record in first.structures] == [
                record.id for record in second.structures
            ]


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
        items = assemble(plan, run_inputs(structures={"structures": structures})).for_step(
            "s_goat"
        )
        assert len(items) == 4
        adapter = _IrcTestAdapter()
        profile = EnsembleProfile()
        checks = (CHECKS["normal_termination"], CHECKS["geometry_required"])
        request = _request(plan, "s_goat", tuple(items), run_root, wrapper,
                           adapter=adapter, profile=profile, checks=checks)
        planned = next(step for step in plan.steps if step.step_id == "s_goat")
        provenance = _provenance_for(request)
        with SqliteWorkItemStore.open(store_path(run_root, "s_goat")) as store:
            for item in items:
                _seed_completed(
                    store, item, planned.step_semantic_digest, provenance,
                    _goat_result(item),
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


class TestQstMappingResume:
    """QST mapping stability controls reuse: same mapping reuses, moved digest invalidates."""

    def _mapping(
        self, plan: Any, *, swapped: bool
    ) -> tuple[Any, StructureSet, StructureSet]:
        """Assemble QST items; *swapped* exchanges products across groups."""
        reactants = StructureSet.of(
            *(
                structure(f"R{i}", kind="methane", group_key=f"g{i}", offset=0.1 * i)
                for i in range(2)
            )
        )
        products = StructureSet.of(
            *(
                structure(
                    f"P{i}",
                    kind="methane",
                    group_key=f"g{1 - i}" if swapped else f"g{i}",
                    offset=0.05 + 0.1 * i,
                )
                for i in range(2)
            )
        )
        assembly = assemble(
            plan, run_inputs(structures={"reactants": reactants, "products": products})
        )
        assert assembly.ok, [item.message for item in assembly.errors]
        return assembly.for_step("s_qst"), reactants, products

    def test_mapping_unchanged_reuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file = _install_irc(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_qst_doc())
        items, _, _ = self._mapping(plan, swapped=False)
        assert len(items) == 2
        adapter = _IrcTestAdapter()
        profile = PathEndpointsProfile()
        checks = (CHECKS["normal_termination"],)
        request = _request(plan, "s_qst", tuple(items), run_root, wrapper,
                           adapter=adapter, profile=profile, checks=checks)
        planned = next(step for step in plan.steps if step.step_id == "s_qst")
        provenance = _provenance_for(request)
        with SqliteWorkItemStore.open(store_path(run_root, "s_qst")) as store:
            for item in items:
                _seed_completed(
                    store, item, planned.step_semantic_digest, provenance,
                    _qst_result(item),
                )
            resumed = _batch().execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl-resume"
            )
        assert resumed.summary["completed"] == 2
        assert _reuse_hits(resumed) == 2
        assert _native_count(count_file) == 0
        for result in resumed.item_results:
            (record,) = tuple(result.structures)
            assert record.group_key in ("g0", "g1")

    def test_mapping_changed_invalidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file = _install_irc(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_qst_doc())
        original, _, _ = self._mapping(plan, swapped=False)
        moved, _, _ = self._mapping(plan, swapped=True)
        assert {item.logical_key for item in moved} == {
            item.logical_key for item in original
        }
        original_digests = {item.logical_key: item.semantic_digest for item in original}
        moved_digests = {item.logical_key: item.semantic_digest for item in moved}
        assert original_digests != moved_digests, "swapped mapping must move the digest"
        adapter = _IrcTestAdapter()
        profile = PathEndpointsProfile()
        checks = (CHECKS["normal_termination"],)
        planned = next(step for step in plan.steps if step.step_id == "s_qst")
        with SqliteWorkItemStore.open(store_path(run_root, "s_qst")) as store:
            seed_request = _request(
                plan, "s_qst", tuple(original), run_root, wrapper,
                adapter=adapter, profile=profile, checks=checks,
            )
            provenance = _provenance_for(seed_request)
            for item in original:
                _seed_completed(
                    store, item, planned.step_semantic_digest, provenance,
                    _qst_result(item),
                )
            resumed = _batch().execute_step_resumable(
                _request(
                    plan, "s_qst", tuple(moved), run_root, wrapper,
                    adapter=adapter, profile=profile, checks=checks,
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl-resume",
            )
        assert resumed.summary["completed"] == 0
        assert resumed.summary["failed"] == 2
        assert _native_count(count_file) == 0
        codes = {
            result.error.code for result in resumed.item_results if result.error is not None
        }
        assert codes == {"invalidate_input"}


class TestEndpointIdStability:
    """Endpoint ids are bit-identical across resume, order included."""

    def test_ids_stable_across_resume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrapper, count_file = _install_irc(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_irc_doc())
        structures = StructureSet.of(
            *(structure(f"ts{i:02d}", group_key=f"rxn-{i:02d}") for i in range(3))
        )
        items = assemble(plan, run_inputs(structures={"structures": structures})).for_step(
            "s_irc"
        )
        adapter = _IrcTestAdapter()
        profile = PathEndpointsProfile()
        checks = (CHECKS["normal_termination"], CHECKS["geometry_required"])
        request = _request(plan, "s_irc", tuple(items), run_root, wrapper,
                           adapter=adapter, profile=profile, checks=checks)
        planned = next(step for step in plan.steps if step.step_id == "s_irc")
        provenance = _provenance_for(request)
        expected = [
            endpoint_output_id(item.logical_key, direction)
            for item in sorted(items, key=lambda entry: entry.logical_key)
            for direction in ("forward", "reverse")
        ]
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            for item in items:
                _seed_completed(
                    store, item, planned.step_semantic_digest, provenance,
                    _irc_result(item),
                )
            first = _batch().execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl-1"
            )
            second = _batch().execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl-2"
            )
        assert [record.id for record in first.structures] == expected
        assert [record.id for record in second.structures] == expected
        assert _native_count(count_file) == 0
