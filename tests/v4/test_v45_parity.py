#!/usr/bin/env python3

"""V4-5 local/remote parity for multi-output producers.

Local (:class:`LocalTransport` / in-process batch) versus
:class:`RemoteTransport` through ``BatchStepExecutor.execute_step_resumable``
with a real store, for:

(a) multi-output IRC items — genuine remote E2E: the remote worker resolves
    its program adapter through :mod:`confflow.programs.registry`, so the
    test registers the same IRC test seam used locally (blocked production
    seam: ``OrcaAdapter.parse_native_result`` must route IRC jobs to
    :func:`confflow.programs.orca.path.parse_path_endpoints`; the worker
    then inherits the wiring with zero worker changes);
(b) named QST items — real ``named_structures`` assembly plus real
    ``resolve_named_inputs``/``ts_output_lineage``; execution through the
    batch executor is blocked (``WorkItemExecutor`` only drives the single
    ``structure`` port), so the delivery variants are built by the
    documented re-homing helper and the comparison contract is proven
    strict by mutation tests;
(c) ensemble items — real ``parse_goat_members`` parsing plus real
    ``EnsembleProfile.apply``; full transport E2E additionally awaits a
    GOAT-capable fake executable plus the adapter wiring of (a).

Compared axes (must be identical): structure ids, geometry digests, roles,
ordinals, parent ids, lineage roots, group keys, charges/multiplicities,
energy values/units/subjects, artifact semantic roles/checksums/subjects,
non-transport diagnostic codes, statuses, semantic digests.  Enumerated
allowed differences: timestamps, run-relative locators, environment digests,
and transport diagnostics.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import ArtifactLocator, ArtifactRef, ArtifactSet, FrozenDict, StructureSet
from confflow.domain.artifact import LocatorKind
from confflow.domain.completion import WorkItemStatus
from confflow.domain.diagnostics import Diagnostic, DiagnosticSeverity
from confflow.domain.result import ResultSet, ScientificResult
from confflow.domain.structure import StructureRecord
from confflow.domain.units import Unit
from confflow.domain.work_item import RecoveryInfo, Timing, WorkItemResult
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.native import (
    GeometryOutput,
    NativeEnsembleMember,
    NativeResult,
    ParsedGeometry,
    ProducedFile,
    ProgramName,
    ResolvedCalculationInputs,
)
from confflow.execution.output_identity import (
    CONFORMER_ROLE,
    conformer_output_id,
    multi_output_structure_id,
)
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_ensemble import EnsembleProfile
from confflow.execution.profile_path_endpoints import PathEndpointsProfile
from confflow.execution.profiles import ProfileContext
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.orca import ensemble_parse as ensemble_parser
from confflow.programs.orca.path import parse_path_endpoints
from confflow.remote.transport import LocalTransport, RemoteTransport
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    run_inputs,
    structure,
    v4_doc,
)
from tests.v4.fakes.fake_irc import parse_inp_coordinates

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_IRC = FAKES_DIR / "fake_irc.py"
STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

#: Delivery facts that may legitimately differ between transports.  Everything
#: else in the comparison must be identical.
ALLOWED_DIFFERENCES = (
    "timestamps",  # timing.started_at / finished_at / duration_seconds
    "locators",  # run-relative artifact locator paths
    "env digest",  # measured executable environment digest
    "transport diagnostics",  # delivery diagnostics, never scientific checks
)

#: Diagnostic-code prefixes owned by delivery, excluded from check comparison.
TRANSPORT_DIAGNOSTIC_PREFIXES = (
    "transport",
    "remote",
    "staging",
    "worker",
    "handoff",
    "bundle",
    "transfer",
    "lease",
    "reuse_hit",
)

IRC_WRAPPER_SCRIPT = """#!/bin/sh
# Counting orca shim: the remote worker resolves `orca` from PATH.
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


def _install_irc_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a counting ``orca`` shim speaking fake IRC; return count file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "orca"
    shim.write_text(IRC_WRAPPER_SCRIPT)
    shim.chmod(0o755)
    count_file = tmp_path / "irc.count"
    count_file.write_text("")
    fail_file = tmp_path / "irc.fail"
    fail_file.write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("IRC_FAKE_REAL", str(FAKE_IRC))
    monkeypatch.setenv("IRC_COUNT_FILE", str(count_file))
    monkeypatch.setenv("FAKE_IRC_MODE", "success")
    monkeypatch.setenv("FAKE_IRC_ORDER", "reverse_first")
    monkeypatch.setenv("IRC_FAIL_FILE", str(fail_file))
    assert os.access(shim, os.X_OK)
    assert not bool(shim.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return count_file


def _native_count(count_file: Path) -> int:
    """Return the number of logged native invocations."""
    return len([line for line in count_file.read_text().splitlines() if line.strip()])


def _irc_doc() -> dict[str, Any]:
    """Build the single-IRC-step document."""
    step = calc_step(
        "s_irc",
        program="orca",
        adapter="standard",
        profile="path_endpoints",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "IRC B3LYP D3BJ"},
        checks=["normal_termination", "geometry_required"],
        scheduler={"max_parallel_items": 4},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
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


def _irc_request(
    plan: Any, items: tuple[Any, ...], run_root: str, adapter: Any, executable: str
) -> StepExecutionRequest:
    """Build a durable IRC step request."""
    planned = next(step for step in plan.steps if step.step_id == "s_irc")
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=adapter,
        profile=PathEndpointsProfile(),
        checks=(CHECKS["normal_termination"], CHECKS["geometry_required"]),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=executable, env=FrozenDict({})
        ),
        run_root=run_root,
        environment=None,
        definition_digest=plan.definition_digest,
    )


def _is_transport_diagnostic(code: str) -> bool:
    """Return whether *code* is delivery-owned rather than scientific."""
    return code.startswith(TRANSPORT_DIAGNOSTIC_PREFIXES)


def _structure_axes(result: WorkItemResult) -> list[tuple[Any, ...]]:
    """Return sorted structure comparison axes (no locators, no timing)."""
    return sorted(
        (
            record.id,
            record.geometry_digest,
            record.role,
            record.ordinal,
            record.parent_ids,
            record.lineage_root_id,
            record.group_key,
            record.charge,
            record.multiplicity,
        )
        for record in result.structures
    )


def _energy_axes(result: WorkItemResult) -> list[tuple[Any, ...]]:
    """Return sorted ``(kind, value, unit, subject)`` energy axes."""
    return sorted(
        (
            record.kind,
            record.value,
            record.unit.value if record.unit is not None else None,
            record.subject_structure_id,
        )
        for record in result.results
        if record.kind == "energy"
    )


def _artifact_axes(result: WorkItemResult) -> list[tuple[Any, ...]]:
    """Return sorted ``(role, checksum, subject)`` axes, sans locators."""
    for ref in result.artifacts:
        assert ref.checksum is not None and ref.checksum.startswith("sha256:"), ref.id
    return sorted(
        (ref.role, ref.checksum, ref.subject_structure_id) for ref in result.artifacts
    )


def _check_axes(result: WorkItemResult) -> list[tuple[str, str]]:
    """Return sorted non-transport ``(code, severity)`` diagnostic pairs."""
    return sorted(
        (item.code, item.severity.value)
        for item in result.diagnostics
        if not _is_transport_diagnostic(item.code)
    )


def _assert_science_parity(local: WorkItemResult, remote: WorkItemResult) -> None:
    """Assert full scientific parity; allowed differences are stripped."""
    assert remote.status is local.status
    assert remote.semantic_digest == local.semantic_digest
    assert _structure_axes(remote) == _structure_axes(local)
    assert _structure_axes(local), "parity needs at least one structure"
    assert _energy_axes(remote) == _energy_axes(local)
    assert _energy_axes(local), "parity needs at least one energy result"
    assert _artifact_axes(remote) == _artifact_axes(local)
    assert _check_axes(remote) == _check_axes(local)
    if local.error is not None or remote.error is not None:
        assert remote.error is not None and local.error is not None
        assert remote.error.code == local.error.code


def _artifact_bytes(run_root: str, result: WorkItemResult) -> dict[str, bytes]:
    """Map artifact id to raw bytes resolved under *run_root*."""
    resolved: dict[str, bytes] = {}
    for ref in result.artifacts:
        assert ref.locator.kind is LocatorKind.RUN_RELATIVE, ref.locator
        assert ref.locator.path is not None
        path = os.path.join(run_root, ref.locator.path)
        assert os.path.isfile(path), path
        with open(path, "rb") as handle:
            resolved[ref.id] = handle.read()
    return resolved


def _rehomed_as_remote(
    result: WorkItemResult, *, locator_prefix: str, worker_tag: str
) -> WorkItemResult:
    """Return *result* with delivery facts re-homed to a remote worker.

    Only allowed differences move: artifact locators are rewritten under
    *locator_prefix*, timing is replaced, and one ``remote_``-prefixed
    delivery diagnostic is added.  Science (ids, geometries, roles,
    lineage, results, checksums) is untouched.
    """
    relocated = ArtifactSet.of(
        *(
            ArtifactRef(
                id=ref.id,
                role=ref.role,
                locator=ArtifactLocator.run_relative(
                    f"{locator_prefix}/{Path(ref.locator.path or ref.id).name}"
                ),
                checksum=ref.checksum,
                media_type=ref.media_type,
                program=ref.program,
                producer_step_id=ref.producer_step_id,
                producer_work_item_id=ref.producer_work_item_id,
                subject_structure_id=ref.subject_structure_id,
                retention=ref.retention,
                metadata=ref.metadata,
            )
            for ref in result.artifacts
        )
    )
    delivery = Diagnostic(
        code="remote_handoff_ok",
        message=f"delivered via worker {worker_tag}",
        severity=DiagnosticSeverity.INFO,
        step_id=result.diagnostics[0].step_id if result.diagnostics else None,
        work_item_id=result.work_item_id,
        details=FrozenDict({"worker": worker_tag}),
    )
    return WorkItemResult(
        work_item_id=result.work_item_id,
        status=result.status,
        structures=result.structures,
        results=result.results,
        artifacts=relocated,
        diagnostics=tuple(result.diagnostics) + (delivery,),
        timing=Timing(started_at=1720000000.0, finished_at=1720000001.0, duration_seconds=1.0),
        error=result.error,
        recovery=result.recovery,
        semantic_digest=result.semantic_digest,
        metadata=result.metadata,
    )


class TestIrcTransportParity:
    """Same multi-output IRC items through local and remote delivery."""

    def test_local_remote_endpoint_parity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import confflow.execution.profile_standard as standard_profiles
        import confflow.programs.registry as program_registry
        from confflow.execution.profile_path_endpoints import (
            PROFILES as PATH_PROFILES,
        )

        count_file = _install_irc_shim(tmp_path, monkeypatch)
        adapter = _IrcTestAdapter()
        # The remote worker resolves its adapter by program name; register
        # the same IRC seam so both sides parse the identical dialect.
        monkeypatch.setitem(program_registry._PROGRAM_ADAPTERS, "orca", adapter)
        # Blocked production seam: the worker resolves result profiles from
        # the standard-only registry; until it consults the multi-profile
        # registry, register the production path-endpoints profile here.
        for name, profile in PATH_PROFILES.items():
            monkeypatch.setitem(standard_profiles.PROFILES, name, profile)
        plan = _compile(_irc_doc())
        structures = StructureSet.of(
            *(
                structure(
                    f"ts{i:02d}",
                    group_key=f"rxn-{i:02d}",
                    lineage_root_id=f"root-{i:02d}",
                    offset=float(i) * 0.017,
                )
                for i in range(2)
            )
        )
        assembly = assemble(plan, run_inputs(structures={"structures": structures}))
        assert assembly.ok
        items = assembly.for_step("s_irc")
        assert len(items) == 2

        local_root = str(tmp_path / "run-local")
        with SqliteWorkItemStore.open(store_path(local_root, "s_irc")) as store:
            local_result = _batch().execute_step_resumable(
                _irc_request(plan, tuple(items), local_root, adapter, "orca"),
                store=store,
                run_root=local_root,
                owner_token="ctl-local",
                transport=LocalTransport(WorkItemExecutor()),
            )
        assert local_result.summary["completed"] == 2

        remote_root = str(tmp_path / "run-remote")
        worker_root = str(tmp_path / "worker")
        with SqliteWorkItemStore.open(store_path(remote_root, "s_irc")) as store:
            transport = RemoteTransport(
                run_root=remote_root, store=store, worker_root=worker_root
            )
            remote_result = _batch().execute_step_resumable(
                _irc_request(plan, tuple(items), remote_root, adapter, "orca"),
                store=store,
                run_root=remote_root,
                owner_token="ctl-remote",
                transport=transport,
            )
        assert remote_result.summary["completed"] == 2
        # Exactly one native launch per item per side: no duplicates.
        assert _native_count(count_file) == 4

        assert len(tuple(local_result.structures)) == 4
        assert len(tuple(remote_result.structures)) == 4
        local_by_id = {result.work_item_id: result for result in local_result.item_results}
        remote_by_id = {result.work_item_id: result for result in remote_result.item_results}
        assert set(local_by_id) == set(remote_by_id)
        for work_item_id, local in local_by_id.items():
            remote = remote_by_id[work_item_id]
            _assert_science_parity(local, remote)
            for ref in local.artifacts:
                assert ref.locator.kind is LocatorKind.RUN_RELATIVE
            for ref in remote.artifacts:
                assert ref.locator.kind is LocatorKind.RUN_RELATIVE
        local_bytes = {
            artifact_id: payload
            for result in local_by_id.values()
            for artifact_id, payload in _artifact_bytes(local_root, result).items()
        }
        remote_bytes = {
            artifact_id: payload
            for result in remote_by_id.values()
            for artifact_id, payload in _artifact_bytes(remote_root, result).items()
        }
        assert set(local_bytes) == set(remote_bytes)
        for artifact_id, payload in local_bytes.items():
            assert remote_bytes[artifact_id] == payload
            digest = "sha256:" + hashlib.sha256(payload).hexdigest()
            owners = [
                ref.checksum
                for result in list(local_by_id.values()) + list(remote_by_id.values())
                for ref in result.artifacts
                if ref.id == artifact_id
            ]
            assert owners and all(checksum == digest for checksum in owners)


class TestQstNamedParity:
    """Named QST items: real assembly/resolution/lineage, re-homed delivery."""

    def _qst_doc(self) -> dict[str, Any]:
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
            checks=["normal_termination", "geometry_required"],
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

    def _candidate_result(self, item: Any, *, step_id: str = "s_qst") -> WorkItemResult:
        """Build the synthetic TS-candidate result for *item*.

        No production QST TS result profile exists yet, so the candidate
        shape below is synthetic-but-rule-bound: parents in semantic slot
        order and lineage from :func:`ts_output_lineage`, the id from the
        frozen :func:`multi_output_structure_id`.  Local and remote
        variants share this exact science.
        """
        from confflow.execution.named_structures import (
            resolve_named_inputs,
            ts_output_lineage,
            validate_named_compatibility,
        )

        resolved = resolve_named_inputs(item, require_guess=False)
        charge, multiplicity = validate_named_compatibility(resolved)
        parent_ids, lineage_root, group_key = ts_output_lineage(resolved)
        assert group_key == resolved.group_key
        midpoint = tuple(
            (ra + pa) / 2.0
            for ra, pa in zip(resolved.reactant.coordinates[0], resolved.product.coordinates[0])
        )
        _ = midpoint
        atoms = tuple(resolved.reactant.atoms)
        coordinates = tuple(
            tuple((ra + pa) / 2.0 for ra, pa in zip(r_point, p_point))
            for r_point, p_point in zip(
                resolved.reactant.coordinates, resolved.product.coordinates
            )
        )
        candidate_id = multi_output_structure_id(item.logical_key, "ts_candidate", 0)
        record = StructureRecord(
            id=candidate_id,
            atoms=atoms,
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
        payload = b"qst-candidate:" + item.id.encode("utf-8")
        checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
        artifact = ArtifactRef(
            id=f"{item.id}:native_output",
            role="native_output",
            locator=ArtifactLocator.run_relative(f"steps/{step_id}/{item.id}/job.out"),
            checksum=checksum,
            subject_structure_id=candidate_id,
            producer_step_id=step_id,
            producer_work_item_id=item.id,
        )
        energy = ScientificResult(
            kind="energy",
            value=-76.400001,
            unit=Unit.HARTREE,
            subject_structure_id=candidate_id,
            source_step_id=step_id,
            source_work_item_id=item.id,
        )
        return WorkItemResult(
            work_item_id=item.id,
            status=WorkItemStatus.COMPLETED,
            structures=StructureSet.of(record),
            results=ResultSet.of(energy),
            artifacts=ArtifactSet.of(artifact),
            diagnostics=(),
            timing=Timing(started_at=1720000000.0, finished_at=1720000002.0, duration_seconds=2.0),
            error=None,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest=item.semantic_digest,
        )

    def test_named_qst_delivery_parity(self) -> None:
        from confflow.execution.named_structures import qst_logical_key

        plan = _compile(self._qst_doc())
        reactants = StructureSet.of(
            *(
                structure(f"R{i}", kind="methane", group_key=f"g{i}", offset=0.0)
                for i in range(2)
            )
        )
        products = StructureSet.of(
            *(
                structure(f"P{i}", kind="methane", group_key=f"g{i}", offset=0.05)
                for i in range(2)
            )
        )
        assembly = assemble(
            plan, run_inputs(structures={"reactants": reactants, "products": products})
        )
        assert assembly.ok, [item.message for item in assembly.errors]
        items = assembly.for_step("s_qst")
        assert len(items) == 2
        assert {item.logical_key for item in items} == {
            qst_logical_key("s_qst", "g0"),
            qst_logical_key("s_qst", "g1"),
        }
        for item in items:
            local = self._candidate_result(item)
            remote = _rehomed_as_remote(
                local, locator_prefix="steps/s_qst/remote", worker_tag="node-b"
            )
            _assert_science_parity(local, remote)
            record = next(iter(local.structures))
            assert record.parent_ids[0].startswith("R")
            assert record.parent_ids[1].startswith("P")
            assert record.group_key in ("g0", "g1")

    def test_named_qst_slots_never_swap(self) -> None:
        """Reactant/product slot order is semantic, never positional."""
        plan = _compile(self._qst_doc())
        reactant = structure("R0", kind="methane", group_key="g0", offset=0.0)
        product = structure("P0", kind="methane", group_key="g0", offset=0.05)
        first = assemble(
            plan,
            run_inputs(
                structures={
                    "reactants": StructureSet.of(reactant),
                    "products": StructureSet.of(product),
                }
            ),
        )
        assert first.ok
        (item,) = first.for_step("s_qst")
        local = self._candidate_result(item)
        (record,) = tuple(local.structures)
        assert record.parent_ids == ("R0", "P0")


class TestEnsembleParity:
    """Ensemble items: real GOAT parsing plus real profile application."""

    def _goat_log(self, *, duplicate_geometry: bool = False) -> str:
        """Build a crafted-dialect GOAT log with three conformer members."""
        atoms = ("O", "H", "H")
        geoms = [
            ((0.0, 0.0, 0.0), (0.757, 0.586, 0.0), (-0.757, 0.586, 0.0)),
            ((0.0, 0.0, 0.1), (0.800, 0.500, 0.0), (-0.800, 0.500, 0.0)),
            ((0.0, 0.0, 0.1), (0.800, 0.500, 0.0), (-0.800, 0.500, 0.0))
            if duplicate_geometry
            else ((0.1, 0.0, 0.0), (0.757, 0.586, 0.1), (-0.757, 0.586, 0.1)),
        ]
        blocks = []
        for index, points in enumerate(geoms):
            lines = [f"GOAT CONFORMER {index}"]
            for symbol, (x, y, z) in zip(atoms, points):
                lines.append(f"{symbol} {x:.6f} {y:.6f} {z:.6f}")
            lines.append(f"Conformer energy: {-76.0 - index:.6f} Hartree")
            blocks.append("\n".join(lines))
        return "\n".join(blocks) + "\n"

    def _ensemble_result(
        self, *, logical_key: str, work_item_id: str, step_id: str, seed: Any
    ) -> WorkItemResult:
        """Apply the real ensemble profile to really-parsed GOAT members."""
        from confflow.domain.resources import ResourceRequest

        members = ensemble_parser.parse_goat_members(self._goat_log(), atoms=seed.atoms)
        assert len(members) == 3
        native_result = NativeResult(
            program=ProgramName.ORCA,
            terminated_normally=True,
            geometry_output=GeometryOutput.NONE,
            final_geometry=None,
            energies_hartree=FrozenDict({}),
            frequencies_cm=(),
            native_metadata=FrozenDict({"goat_dialect": "confflow-goat-v1"}),
            produced_files=(),
            parser_diagnostics=(),
            log_file_name="job.out",
            path_endpoints=(),
            ensemble_members=members,
        )
        resources = ResourceRequest(cores_per_item=1, memory_per_item_bytes=1024**3)
        assert resources.is_resolved
        output = EnsembleProfile().apply(
            ProfileContext(
                work_item_id=work_item_id,
                step_id=step_id,
                logical_key=logical_key,
                profile_name="ensemble",
                profile_version="ensemble",
                native_result=native_result,
                inputs=ResolvedCalculationInputs(
                    structure=seed,
                    charge=seed.charge,
                    multiplicity=seed.multiplicity,
                    freeze=None,
                    resources=resources,
                    native=FrozenDict({"keyword": "GOAT"}),
                    checkpoints=(),
                    extra_structures=FrozenDict({}),
                    step_id=step_id,
                    work_item_id=work_item_id,
                    logical_key=logical_key,
                ),
                discovered_artifacts=ArtifactSet(),
            )
        )
        assert len(tuple(output.structures)) == 3
        payload = b"ensemble-report:" + work_item_id.encode("utf-8")
        report = ArtifactRef(
            id=f"{work_item_id}:ensemble_report",
            role="ensemble_report",
            locator=ArtifactLocator.run_relative(f"steps/{step_id}/{work_item_id}/job.out"),
            checksum="sha256:" + hashlib.sha256(payload).hexdigest(),
            subject_structure_id=None,
            producer_step_id=step_id,
            producer_work_item_id=work_item_id,
        )
        return WorkItemResult(
            work_item_id=work_item_id,
            status=WorkItemStatus.COMPLETED,
            structures=output.structures,
            results=output.results,
            artifacts=ArtifactSet.of(report),
            diagnostics=tuple(output.diagnostics),
            timing=Timing(started_at=1720000000.0, finished_at=1720000003.0, duration_seconds=3.0),
            error=None,
            recovery=RecoveryInfo(profile="none", attempted=False),
            semantic_digest="sha256:" + "a" * 64,
        )

    def test_ensemble_delivery_parity(self) -> None:
        seed = structure("seed-0", group_key="ens-0", lineage_root_id="root-ens-0")
        logical_key = "s_goat:ens-0"
        work_item_id = f"wi:{logical_key}"
        local = self._ensemble_result(
            logical_key=logical_key, work_item_id=work_item_id, step_id="s_goat", seed=seed
        )
        remote = _rehomed_as_remote(
            local, locator_prefix="steps/s_goat/remote", worker_tag="node-c"
        )
        _assert_science_parity(local, remote)
        by_id = {record.id: record for record in local.structures}
        assert set(by_id) == {conformer_output_id(logical_key, index) for index in range(3)}
        ordinals = sorted(record.ordinal for record in local.structures)
        assert ordinals == [0, 1, 2]
        assert all(record.role == CONFORMER_ROLE for record in local.structures)
        assert all(record.parent_ids == ("seed-0",) for record in local.structures)
        assert all(record.group_key == "ens-0" for record in local.structures)
        subjects = {record.subject_structure_id for record in local.results}
        assert subjects == set(by_id)

    def test_identical_geometries_never_deduped(self) -> None:
        """Shared content under distinct member indexes stays distinct."""
        seed = structure("seed-0", group_key="ens-0", lineage_root_id="root-ens-0")
        members = ensemble_parser.parse_goat_members(
            self._goat_log(duplicate_geometry=True), atoms=seed.atoms
        )
        assert members[1].geometry.coordinates == members[2].geometry.coordinates
        assert members[1].member_index != members[2].member_index


class TestParityComparator:
    """The comparator is strict: any science drift fails loudly."""

    def _baseline(self) -> tuple[WorkItemResult, WorkItemResult]:
        seed = structure("seed-0", group_key="ens-0", lineage_root_id="root-ens-0")
        logical_key = "s_goat:ens-0"
        work_item_id = f"wi:{logical_key}"
        local = TestEnsembleParity()._ensemble_result(
            logical_key=logical_key, work_item_id=work_item_id, step_id="s_goat", seed=seed
        )
        remote = _rehomed_as_remote(
            local, locator_prefix="steps/s_goat/remote", worker_tag="node-c"
        )
        return local, remote

    def test_allowed_differences_are_enumerated(self) -> None:
        assert ALLOWED_DIFFERENCES == (
            "timestamps",
            "locators",
            "env digest",
            "transport diagnostics",
        )
        local, remote = self._baseline()
        assert local.timing != remote.timing
        assert [ref.locator.path for ref in local.artifacts] != [
            ref.locator.path for ref in remote.artifacts
        ]
        assert any(_is_transport_diagnostic(item.code) for item in remote.diagnostics)
        _assert_science_parity(local, remote)

    def test_geometry_drift_rejected(self) -> None:
        import dataclasses as _dc

        local, remote = self._baseline()
        records = tuple(remote.structures)
        moved = _dc.replace(
            records[0],
            coordinates=tuple((x + 0.5, y, z) for x, y, z in records[0].coordinates),
        )
        tampered = WorkItemResult(
            work_item_id=remote.work_item_id,
            status=remote.status,
            structures=StructureSet.of(moved, *records[1:]),
            results=remote.results,
            artifacts=remote.artifacts,
            diagnostics=remote.diagnostics,
            timing=remote.timing,
            error=remote.error,
            recovery=remote.recovery,
            semantic_digest=remote.semantic_digest,
        )
        with pytest.raises(AssertionError):
            _assert_science_parity(local, tampered)

    def test_role_swap_rejected(self) -> None:
        import dataclasses as _dc

        local, remote = self._baseline()
        records = tuple(remote.structures)
        swapped = _dc.replace(records[0], role="path_endpoint_forward")
        tampered = WorkItemResult(
            work_item_id=remote.work_item_id,
            status=remote.status,
            structures=StructureSet.of(swapped, *records[1:]),
            results=remote.results,
            artifacts=remote.artifacts,
            diagnostics=remote.diagnostics,
            timing=remote.timing,
            error=remote.error,
            recovery=remote.recovery,
            semantic_digest=remote.semantic_digest,
        )
        with pytest.raises(AssertionError):
            _assert_science_parity(local, tampered)

    def test_energy_drift_rejected(self) -> None:
        local, remote = self._baseline()
        results = tuple(remote.results)
        tampered_results = ResultSet.of(
            ScientificResult(
                kind=results[0].kind,
                value=float(results[0].value) + 0.01,
                unit=results[0].unit,
                subject_structure_id=results[0].subject_structure_id,
                source_step_id=results[0].source_step_id,
                source_work_item_id=results[0].source_work_item_id,
            ),
            *results[1:],
        )
        tampered = WorkItemResult(
            work_item_id=remote.work_item_id,
            status=remote.status,
            structures=remote.structures,
            results=tampered_results,
            artifacts=remote.artifacts,
            diagnostics=remote.diagnostics,
            timing=remote.timing,
            error=remote.error,
            recovery=remote.recovery,
            semantic_digest=remote.semantic_digest,
        )
        with pytest.raises(AssertionError):
            _assert_science_parity(local, tampered)

    def test_parent_mispairing_rejected(self) -> None:
        import dataclasses as _dc

        local, remote = self._baseline()
        records = tuple(remote.structures)
        reparented = _dc.replace(records[0], parent_ids=("some-other-parent",))
        tampered = WorkItemResult(
            work_item_id=remote.work_item_id,
            status=remote.status,
            structures=StructureSet.of(reparented, *records[1:]),
            results=remote.results,
            artifacts=remote.artifacts,
            diagnostics=remote.diagnostics,
            timing=remote.timing,
            error=remote.error,
            recovery=remote.recovery,
            semantic_digest=remote.semantic_digest,
        )
        with pytest.raises(AssertionError):
            _assert_science_parity(local, tampered)

    def test_local_transport_baseline(self) -> None:
        """LocalTransport delegates to the wrapped executor (constructor guard)."""
        executor = WorkItemExecutor()
        transport = LocalTransport(executor)
        assert transport.executor is executor

    def test_scientific_result_member_shapes(self) -> None:
        """Ensemble members carry native identity, not parser order."""
        members = ensemble_parser.parse_goat_members(
            TestEnsembleParity()._goat_log(), atoms=("O", "H", "H")
        )
        assert [member.member_index for member in members] == [0, 1, 2]
        assert all(isinstance(member.geometry, ParsedGeometry) for member in members)
        assert all(isinstance(member, NativeEnsembleMember) for member in members)
