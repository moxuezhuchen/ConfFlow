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
from confflow.domain.completion import StepStatus, WorkItemStatus
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
from confflow.programs.registry import get_program_adapter
from confflow.execution.profile_ensemble import EnsembleProfile
from confflow.execution.profile_path_endpoints import PathEndpointsProfile
from confflow.execution.profile_standard import PROFILES as STANDARD_PROFILES
from confflow.execution.profiles import ProfileContext
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
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
FAKE_G16 = FAKES_DIR / "fake_g16.py"
FAKE_GOAT = FAKES_DIR / "fake_goat.py"
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


def _install_counting_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, name: str, fake: Path, mode: str
) -> tuple[Path, Path]:
    """Install ``bin/<name>`` execing *fake*; return ``(wrapper, count_file)``."""
    bin_dir = tmp_path / f"bin-{name}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / name
    wrapper.write_text(
        "#!/bin/sh\n"
        f'base=$(basename "$1")\n'
        f'printf \'%s\\n\' "$base" >> "{tmp_path / (name + ".count")}"\n'
        f'exec python3 "{fake}" "$@"\n'
    )
    wrapper.chmod(0o755)
    count_file = tmp_path / f"{name}.count"
    count_file.write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_MODE", mode)
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper, count_file


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
        native={"keyword": "IRC B3LYP D3BJ", "irc": {"direction": "both"}},
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
        environment=_measured_env(adapter, executable),
        definition_digest=plan.definition_digest,
        executor_capability=getattr(planned.executor, "value", str(planned.executor)),
    )


def _measured_env(adapter: Any, executable: Any) -> Any:
    """Measure the fake executable for the durable env axis."""
    from confflow.execution.environment import EnvironmentMeasurer

    return EnvironmentMeasurer().build_environment(str(executable), adapter=adapter)


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
    return sorted((ref.role, ref.checksum, ref.subject_structure_id) for ref in result.artifacts)


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


class TestIrcTransportParity:
    """Same multi-output IRC items through local and remote delivery."""

    def test_local_remote_endpoint_parity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No registry monkeypatching: the worker resolves the real
        # program adapter, profiles, checks, and recovery through the
        # single execution registry, exactly like local execution.
        count_file = _install_irc_shim(tmp_path, monkeypatch)
        adapter = get_program_adapter("orca")  # real adapter: fakes speak real grammar
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
            transport = RemoteTransport(run_root=remote_root, store=store, worker_root=worker_root)
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
    """Named QST2 items through local and remote delivery (genuine).

    Gaussian QST2 (the only program with QST semantics) runs the same
    named reactant/product slots on both transports: slot lineage,
    mapped reordering, and TS-candidate identity must agree exactly.
    No result is ever re-homed; both sides execute natively.
    """

    def _qst_doc(self, mapping: Any = None) -> dict[str, Any]:
        """Build the Gaussian QST2 document, optionally with a mapping."""
        native: dict[str, Any] = {"keyword": "B3LYP QST2 Opt"}
        if mapping is not None:
            native["atom_mapping"] = mapping
        step = calc_step(
            "s_qst",
            program="gaussian",
            adapter="named_structures",
            profile="standard",
            bindings={
                "reactant": {"source": {"run": "reactants"}, "pairing": "by_group_key"},
                "product": {"source": {"run": "products"}, "pairing": "by_group_key"},
            },
            native=native,
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 4},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "g16"},
        )
        return v4_doc(
            [step],
            inputs={
                "reactants": {"kind": "structure", "cardinality": "many"},
                "products": {"kind": "structure", "cardinality": "many"},
            },
        )

    def _run_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapping: Any = None
    ) -> tuple[Any, Any]:
        """Run one QST2 step locally and remotely; return step results."""
        import dataclasses as _dc

        from confflow.remote.transport import LocalTransport, RemoteTransport

        wrapper, _count = _install_counting_shim(
            tmp_path, monkeypatch, name="g16", fake=FAKE_G16, mode="ts_candidate"
        )
        plan = _compile(self._qst_doc(mapping))
        reactants = StructureSet.of(
            *(
                structure(f"R{i}", kind="water", group_key=f"g{i}", offset=0.01 * i)
                for i in range(2)
            )
        )
        products = StructureSet.of(
            *(
                structure(f"P{i}", kind="water", group_key=f"g{i}", offset=0.05 + 0.01 * i)
                for i in range(2)
            )
        )
        if mapping is not None:
            # Permute every product slot end to end: the uniform mapping
            # must then hold for all pairs (assembly validates each
            # pair against it and the adapter renders reference order).
            import dataclasses as _dc

            remapped = []
            for record in products:
                remapped.append(
                    _dc.replace(
                        record,
                        atoms=("H", "H", "O"),
                        coordinates=tuple(reversed(record.coordinates)),
                    )
                )
            products = StructureSet.of(*remapped)
        run_inputs_map = {"reactants": reactants, "products": products}
        assembly = assemble(plan, run_inputs(structures=run_inputs_map))
        assert assembly.ok, [item.message for item in assembly.errors]
        items = assembly.for_step("s_qst")
        assert len(items) == 2

        def _request_for(items: Any, run_root: str) -> Any:
            planned = plan.steps[0]
            adapter = get_program_adapter("gaussian")
            return StepExecutionRequest(
                step=planned,
                items=tuple(items),
                scientific=planned.scientific,
                scientific_defaults=plan.scientific_defaults,
                adapter=adapter,
                profile=STANDARD_PROFILES["standard"],
                checks=(CHECKS["normal_termination"],),
                recovery=RECOVERIES["none"],
                execution_binding=ExecutionBinding(
                    binding_id="test", executable=str(wrapper), env=FrozenDict({})
                ),
                run_root=run_root,
                environment=_measured_env(adapter, str(wrapper)),
                definition_digest=plan.definition_digest,
                executor_capability="calculation",
            )

        local_root = str(tmp_path / "run-local")
        with SqliteWorkItemStore.open(store_path(local_root, "s_qst")) as store:
            local_result = _batch().execute_step_resumable(
                _request_for(tuple(items), local_root),
                store=store,
                run_root=local_root,
                owner_token="ctl-local",
                transport=LocalTransport(WorkItemExecutor()),
            )
        assert local_result.status is StepStatus.COMPLETED

        remote_root = str(tmp_path / "run-remote")
        worker_root = str(tmp_path / "worker")
        with SqliteWorkItemStore.open(store_path(remote_root, "s_qst")) as store:
            transport = RemoteTransport(
                run_root=remote_root,
                store=store,
                worker_root=worker_root,
                target_default_executable=str(wrapper),
            )
            remote_result = _batch().execute_step_resumable(
                _request_for(tuple(items), remote_root),
                store=store,
                run_root=remote_root,
                owner_token="ctl-remote",
                transport=transport,
            )
        assert remote_result.status is StepStatus.COMPLETED
        return local_result, remote_result

    def test_named_qst_delivery_parity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unmapped QST2 pairs agree across transports, slots intact."""
        local_result, remote_result = self._run_both(tmp_path, monkeypatch)
        assert local_result.summary["completed"] == 2
        assert remote_result.summary["completed"] == 2
        local_by_id = {result.work_item_id: result for result in local_result.item_results}
        remote_by_id = {result.work_item_id: result for result in remote_result.item_results}
        assert set(local_by_id) == set(remote_by_id)
        for work_item_id, local in local_by_id.items():
            remote = remote_by_id[work_item_id]
            _assert_science_parity(local, remote)
            record = next(iter(local.structures))
            assert record.parent_ids[0].startswith("R")
            assert record.parent_ids[1].startswith("P")
            assert record.group_key in ("g0", "g1")

    def test_mapped_qst_delivery_parity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A permuted product slot maps end to end on both transports."""
        mapping = {"kind": "explicit_permutation", "permutation": [2, 0, 1]}
        local_result, remote_result = self._run_both(tmp_path, monkeypatch, mapping)
        assert local_result.summary["completed"] == 2
        assert remote_result.summary["completed"] == 2
        local_by_id = {result.work_item_id: result for result in local_result.item_results}
        remote_by_id = {result.work_item_id: result for result in remote_result.item_results}
        for work_item_id, local in local_by_id.items():
            _assert_science_parity(local, remote_by_id[work_item_id])

    def test_named_qst_slots_never_swap(self, tmp_path: Path) -> None:
        """Reactant/product slot order is semantic, never positional."""
        plan = _compile(self._qst_doc())
        reactant = structure("R0", kind="water", group_key="g0", offset=0.0)
        product = structure("P0", kind="water", group_key="g0", offset=0.05)
        assembly = assemble(
            plan,
            run_inputs(
                structures={
                    "reactants": StructureSet.of(reactant),
                    "products": StructureSet.of(product),
                }
            ),
        )
        assert assembly.ok
        (item,) = assembly.for_step("s_qst")
        resolved = item.named_inputs.structures
        assert tuple(resolved["reactant"])[0].id == "R0"
        assert tuple(resolved["product"])[0].id == "P0"



class TestEnsembleParity:
    """Ensemble items through local and remote delivery (genuine).

    GOAT conformer generation runs the same ensemble profile on both
    transports: member ids, ordinals, roles, lineage, subjects, and
    energies must agree exactly.  No result is ever re-homed; both
    sides execute natively through the fake GOAT binary (real output
    grammar).
    """

    def _goat_doc(self) -> dict[str, Any]:
        """Build the single-GOAT-step document."""
        step = calc_step(
            "s_goat",
            program="orca",
            adapter="standard",
            profile="ensemble",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "B3LYP D3BJ GOAT", "goat": {"MaxIter": 50}},
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 4},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
            seed=7,
        )
        return v4_doc([step], inputs=STRUCTURE_INPUTS)

    def _run_both(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
        """Run one GOAT step locally and remotely; return step results."""
        from confflow.remote.transport import LocalTransport, RemoteTransport

        wrapper, _count = _install_counting_shim(
            tmp_path, monkeypatch, name="goat", fake=FAKE_GOAT, mode="success"
        )
        plan = _compile(self._goat_doc())
        seed = structure("seed-0", group_key="ens-0", lineage_root_id="root-ens-0")
        assembly = assemble(plan, run_inputs(structures={"structures": StructureSet.of(seed)}))
        assert assembly.ok, [item.message for item in assembly.errors]
        items = assembly.for_step("s_goat")
        assert len(items) == 1

        def _request_for(items: Any, run_root: str) -> Any:
            planned = plan.steps[0]
            adapter = get_program_adapter("orca")
            return StepExecutionRequest(
                step=planned,
                items=tuple(items),
                scientific=planned.scientific,
                scientific_defaults=plan.scientific_defaults,
                adapter=adapter,
                profile=STANDARD_PROFILES["ensemble"],
                checks=(CHECKS["normal_termination"],),
                recovery=RECOVERIES["none"],
                execution_binding=ExecutionBinding(
                    binding_id="test", executable=str(wrapper), env=FrozenDict({})
                ),
                run_root=run_root,
                environment=_measured_env(adapter, str(wrapper)),
                definition_digest=plan.definition_digest,
                executor_capability="calculation",
            )

        local_root = str(tmp_path / "run-local")
        with SqliteWorkItemStore.open(store_path(local_root, "s_goat")) as store:
            local_result = _batch().execute_step_resumable(
                _request_for(tuple(items), local_root),
                store=store,
                run_root=local_root,
                owner_token="ctl-local",
                transport=LocalTransport(WorkItemExecutor()),
            )
        assert local_result.status is StepStatus.COMPLETED

        remote_root = str(tmp_path / "run-remote")
        worker_root = str(tmp_path / "worker")
        with SqliteWorkItemStore.open(store_path(remote_root, "s_goat")) as store:
            transport = RemoteTransport(
                run_root=remote_root,
                store=store,
                worker_root=worker_root,
                target_default_executable=str(wrapper),
            )
            remote_result = _batch().execute_step_resumable(
                _request_for(tuple(items), remote_root),
                store=store,
                run_root=remote_root,
                owner_token="ctl-remote",
                transport=transport,
            )
        assert remote_result.status is StepStatus.COMPLETED
        return local_result, remote_result

    def test_ensemble_delivery_parity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three conformer members agree across transports, lineage intact."""
        local_result, remote_result = self._run_both(tmp_path, monkeypatch)
        assert local_result.summary["completed"] == 1
        assert remote_result.summary["completed"] == 1
        local = local_result.item_results[0]
        remote = remote_result.item_results[0]
        _assert_science_parity(local, remote)
        by_id = {record.id: record for record in local.structures}
        assert set(by_id) == {conformer_output_id("s_goat:seed-0", index) for index in range(3)}
        assert sorted(record.ordinal for record in local.structures) == [0, 1, 2]
        assert all(record.role == CONFORMER_ROLE for record in local.structures)
        assert all(record.parent_ids == ("seed-0",) for record in local.structures)
        assert all(record.group_key == "ens-0" for record in local.structures)
        subjects = {record.subject_structure_id for record in local.results}
        assert subjects == set(by_id)

    def test_identical_geometries_never_deduped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Shared content under distinct member indexes stays distinct."""
        local_result, _ = self._run_both(tmp_path, monkeypatch)
        local = local_result.item_results[0]
        by_index = {record.ordinal: record for record in local.structures}
        assert set(by_index) == {0, 1, 2}
        assert len({record.id for record in local.structures}) == 3



class TestParityComparator:
    """The comparator is strict: any science drift fails loudly.

    Baselines are genuine local/remote GOAT executions (never re-homed):
    tamper variants then prove each drift class is rejected.
    """

    def _baseline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[WorkItemResult, WorkItemResult]:
        local_result, remote_result = TestEnsembleParity()._run_both(tmp_path, monkeypatch)
        return local_result.item_results[0], remote_result.item_results[0]

    def test_allowed_differences_are_enumerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert ALLOWED_DIFFERENCES == (
            "timestamps",
            "locators",
            "env digest",
            "transport diagnostics",
        )
        local, remote = self._baseline(tmp_path, monkeypatch)
        assert local.timing != remote.timing
        assert [ref.locator.path for ref in local.artifacts] != [
            ref.locator.path for ref in remote.artifacts
        ]
        # Delivery provenance rides in metadata (worker-measured
        # environment), not in synthesized diagnostics: a clean remote
        # delivery adds no diagnostic codes of its own.
        assert "remote_execution_environment" in dict(remote.metadata.thaw())
        _assert_science_parity(local, remote)

    def test_geometry_drift_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import dataclasses as _dc

        local, remote = self._baseline(tmp_path, monkeypatch)
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

    def test_role_swap_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import dataclasses as _dc

        local, remote = self._baseline(tmp_path, monkeypatch)
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

    def test_energy_drift_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        local, remote = self._baseline(tmp_path, monkeypatch)
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

    def test_parent_mispairing_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import dataclasses as _dc

        local, remote = self._baseline(tmp_path, monkeypatch)
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

    def test_scientific_result_member_shapes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ensemble members carry native identity, not parser order."""
        local, _ = self._baseline(tmp_path, monkeypatch)
        assert [record.ordinal for record in local.structures] == [0, 1, 2]
        assert all(record.role == CONFORMER_ROLE for record in local.structures)
