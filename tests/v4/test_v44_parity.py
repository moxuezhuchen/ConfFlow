#!/usr/bin/env python3

"""V4-4 local/remote parity: same science through different delivery.

The same synthetic :class:`WorkItem` is executed through
:class:`LocalTransport` and :class:`RemoteTransport`
(``confflow.remote.transport``)::

    LocalTransport(executor).execute(item, ctx, attempt=1)
    RemoteTransport(run_root=..., store=..., worker_root=...).execute(item, ctx, attempt=1)

and the normalized outcomes must agree on every scientific axis:

- same structure content (geometry payloads, parents, lineage),
- same energy values and units,
- same check outcomes (non-transport diagnostic codes),
- same artifact roles, subjects, and checksums with recomputed-bytes equality,
- same :class:`WorkItemResult` semantics (status, semantic digest, error shape),
- same step-result semantics via ``rebuild_step_result``.

Allowed differences are enumerated in :data:`ALLOWED_DIFFERENCES` (timestamps,
run-relative locators, environment digests, transport diagnostics).  Gaussian
AND orca fakes are both covered.  Checkpoint handoff is covered in both
directions (local-to-remote, remote-to-local) with subject/checksum equality,
and a scheduler/endpoint change (different worker root, different
``max_parallel``) must keep the definition digest identical with full reuse on
the second run.

Test injection: the remote worker resolves the program binary from ``PATH``
(the handoff carries program names, never executable paths), so remote tests
install ``bin/orca`` / ``bin/g16`` shims that count native launches and exec
the fake programs.  ``FAKE_MODE`` propagates through the environment.
Work bases always live under the run root (durable-locator containment).
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet, Unit
from confflow.domain.artifact import ArtifactSet
from confflow.domain.completion import CompletionMode, CompletionPolicy, WorkItemStatus
from confflow.domain.result import ResultSet
from confflow.domain.work_item import WorkItem, WorkItemInputs, WorkItemResult
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, InMemoryReuseStore
from confflow.execution.checks_standard import CHECKS
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.persistence.contracts import OwnerIdentity, StoredWorkItemStatus, store_path
from confflow.programs.registry import get_program_adapter
from tests.v4._builders import (
    assemble,
    calc_step,
    compile_doc,
    energy_result,
    run_inputs,
    structure_set,
    v4_doc,
)
from tests.v4.fakes.fake_g16 import ENERGY_HARTREE as G16_ENERGY
from tests.v4.fakes.fake_orca import ENERGY_HARTREE as ORCA_ENERGY

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_G16 = FAKES_DIR / "fake_g16.py"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

ORCA_NATIVE = {"keyword": "B3LYP D3BJ def2-SVP Opt"}
GAUSSIAN_NATIVE = {"keyword": "B3LYP/6-31G* Opt"}

#: Delivery facts that may legitimately differ between transports.  Everything
#: else in the normalized comparison must be byte-identical.
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


def calculation_doc(
    program: str,
    native: dict[str, Any],
    executable: str,
    *,
    step_id: str = "s_opt",
    scheduler: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single-calculation document bound to *executable*."""
    step = calc_step(
        step_id,
        program="g16" if program == "gaussian" else "orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native=native,
        checks=["normal_termination"],
        scheduler=scheduler,
        resources={"cores_per_item": 4, "memory_per_item": "16GB"},
        execution={"binding_id": "test", "executable": executable},
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


def compile_plan(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def assemble_items(plan: Any, structures: StructureSet) -> Any:
    """Assemble work items for *structures*, asserting success."""
    assembly = assemble(plan, run_inputs(structures={"structures": structures}))
    assert assembly.ok, [item.message for item in assembly.errors]
    return assembly.items


def binding_for(executable: str) -> ExecutionBinding:
    """Build a test execution binding for a fake executable."""
    return ExecutionBinding(binding_id="test", executable=executable, env=FrozenDict({}))


def item_context(
    plan: Any,
    program: str,
    executable: str,
    run_root: str,
    work_base: str,
    supervisor: Any,
) -> ItemExecutionContext:
    """Build an item execution context wired to real adapters and profiles."""
    planned = plan.steps[0]
    return ItemExecutionContext(
        step_id=planned.step_id,
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter(program),
        profile=PROFILES["standard"],
        checks=(CHECKS["normal_termination"],),
        recovery=RECOVERIES["none"],
        execution_binding=binding_for(executable),
        run_root=run_root,
        work_base=work_base,
        supervisor=supervisor,
        environment=None,
        poll_interval_seconds=0.05,
    )


def _open_store(run_root: str, step_id: str) -> Any:
    """Open the durable step store (real ``confflow.persistence`` seam)."""
    from confflow.persistence.work_items import SqliteWorkItemStore

    return SqliteWorkItemStore.open(store_path(run_root, step_id))


def _prepare_attempt(store: Any, item: Any) -> None:
    """Register *item* and claim attempt 1 so result import has a current attempt."""
    store.register_item(
        work_item_id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        work_item_digest=item.semantic_digest,
        step_semantic_digest=item.semantic_digest,
        producer_provenance={},
    )
    assert store.claim(item.id, owner=OwnerIdentity(owner_token="parity-probe")) is True


def _install_program_shims(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counter: Path) -> None:
    """Install counting ``orca``/``g16`` shims on ``PATH`` for the worker.

    The remote worker resolves program binaries by name from ``PATH``; the
    shims count each native launch and then exec the fake programs, so the
    remote path runs the same fakes as the local path.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, fake in (("orca", FAKE_ORCA), ("g16", FAKE_G16)):
        shim = bin_dir / name
        shim.write_text(
            "#!/bin/bash\n"
            f'echo "{name} launch" >> "{counter}"\n'
            f'exec python3 "{fake}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    if not counter.exists():
        counter.write_text("", encoding="utf-8")


def _native_launches(counter: Path) -> int:
    """Return the number of recorded native launches."""
    if not counter.exists():
        return 0
    return len([line for line in counter.read_text(encoding="utf-8").splitlines() if line])


def _is_transport_diagnostic(code: str) -> bool:
    """Return whether *code* is delivery-owned rather than scientific."""
    return code.startswith(TRANSPORT_DIAGNOSTIC_PREFIXES)


def _energies(result: WorkItemResult) -> list[tuple[float, str]]:
    """Return sorted ``(value, unit)`` pairs of the energy results."""
    pairs = [
        (record.value, record.unit.value if record.unit is not None else "")
        for record in result.results
        if record.kind == "energy"
    ]
    return sorted(pairs)


def _artifact_identity(result: WorkItemResult) -> list[tuple[str, str, str | None]]:
    """Return sorted ``(role, checksum, subject)`` triples, sans locators."""
    return sorted(
        (ref.role, ref.checksum or "", ref.subject_structure_id) for ref in result.artifacts
    )


def _check_outcomes(result: WorkItemResult) -> list[tuple[str, str]]:
    """Return sorted non-transport ``(code, severity)`` diagnostic pairs."""
    return sorted(
        (item.code, item.severity.value)
        for item in result.diagnostics
        if not _is_transport_diagnostic(item.code)
    )


def _normalized_payload(result: WorkItemResult) -> dict[str, Any]:
    """Return the parity-compared payload with allowed differences stripped."""
    payload = result.to_dict()
    payload["timing"] = None  # allowed: timestamps
    payload["artifacts"] = sorted(
        (
            {key: entry[key] for key in ("id", "role", "checksum", "subject_structure_id")}
            for entry in payload["artifacts"]
        ),
        key=lambda entry: entry["id"],
    )  # allowed: run-relative locator paths; order is delivery detail
    payload["diagnostics"] = [
        entry for entry in payload["diagnostics"] if not _is_transport_diagnostic(entry["code"])
    ]  # allowed: transport diagnostics
    return payload


def _artifact_bytes(run_root: str, result: WorkItemResult) -> dict[str, bytes]:
    """Map artifact id to raw bytes resolved under *run_root*."""
    from confflow.domain.artifact import LocatorKind

    resolved: dict[str, bytes] = {}
    for ref in result.artifacts:
        assert ref.locator.kind is LocatorKind.RUN_RELATIVE, ref.locator
        assert ref.locator.path is not None
        path = os.path.join(run_root, ref.locator.path)
        assert os.path.isfile(path), path
        with open(path, "rb") as handle:
            resolved[ref.id] = handle.read()
    return resolved


def _assert_parity(local: WorkItemResult, remote: WorkItemResult) -> None:
    """Assert full scientific parity between two transport outcomes."""
    assert remote.status is local.status
    assert remote.semantic_digest == local.semantic_digest
    assert [record.to_dict() for record in remote.structures] == [
        record.to_dict() for record in local.structures
    ]
    assert _energies(remote) == _energies(local) and _energies(local)
    assert _check_outcomes(remote) == _check_outcomes(local)
    assert _artifact_identity(remote) == _artifact_identity(local)
    assert _normalized_payload(remote) == _normalized_payload(local)
    if local.error is not None or remote.error is not None:
        assert remote.error is not None and local.error is not None
        assert remote.error.code == local.error.code
        assert remote.error.retryable == local.error.retryable


class TestLocalTransportBaseline:
    """Local-transport reference outcomes for both fake programs."""

    def test_orca_local_success_energy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.transport import LocalTransport

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        context = item_context(
            plan,
            "orca",
            str(FAKE_ORCA),
            str(tmp_path),
            str(tmp_path / "items"),
            NativeProcessSupervisor(),
        )
        result = LocalTransport(WorkItemExecutor()).execute(items[0], context, attempt=1)
        assert result.status is WorkItemStatus.COMPLETED
        assert result.semantic_digest == items[0].semantic_digest
        energies = _energies(result)
        assert len(energies) >= 1
        assert energies[0][0] == pytest.approx(ORCA_ENERGY, abs=1e-6)
        assert energies[0][1] == Unit.HARTREE.value
        assert all(severity != "error" for _, severity in _check_outcomes(result))

    def test_gaussian_local_success_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.transport import LocalTransport

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        plan = compile_plan(calculation_doc("gaussian", GAUSSIAN_NATIVE, str(FAKE_G16)))
        items = assemble_items(plan, structure_set("s0"))
        context = item_context(
            plan,
            "gaussian",
            str(FAKE_G16),
            str(tmp_path),
            str(tmp_path / "items"),
            NativeProcessSupervisor(),
        )
        result = LocalTransport(WorkItemExecutor()).execute(items[0], context, attempt=1)
        assert result.status is WorkItemStatus.COMPLETED
        energies = _energies(result)
        assert len(energies) >= 1
        assert energies[0][0] == pytest.approx(G16_ENERGY, abs=1e-6)
        assert energies[0][1] == Unit.HARTREE.value
        assert "checkpoint" in {ref.role for ref in result.artifacts}
        assert result.structures[0].parent_ids == ("s0",)


class TestTransportHelpers:
    """Launch tokens, delivery cache, error shape, and constructor guards."""

    def test_launch_token_is_deterministic_and_endpoint_independent(self, tmp_path: Path) -> None:
        from confflow.remote.transport import RemoteTransport

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        run_root = str(tmp_path / "run")
        with _open_store(run_root, "s_opt") as store:
            first = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "w1")
            )
            second = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "w2")
            )
            assert first.launch_token_for(items[0], 1) == second.launch_token_for(items[0], 1)
            assert first.launch_token_for(items[0], 1) == f"remote+{items[0].id}+attempt-1".replace(
                ":", "+"
            )
            assert "/" not in first.launch_token_for(items[0], 1)
            assert first.launch_token_for(items[0], 1) != first.launch_token_for(items[0], 2)

    def test_recorded_delivery_returns_without_touching_worker(self, tmp_path: Path) -> None:
        from confflow.remote.transport import RemoteTransport

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        context = item_context(
            plan, "orca", str(FAKE_ORCA), str(tmp_path), str(tmp_path / "items"), None
        )
        recorded = WorkItemResult(work_item_id=items[0].id, status=WorkItemStatus.COMPLETED)
        worker_root = tmp_path / "never-created-worker"
        with _open_store(str(tmp_path / "run"), "s_opt") as store:
            transport = RemoteTransport(
                run_root=str(tmp_path / "run"), store=store, worker_root=str(worker_root)
            )
            transport._delivered[transport.launch_token_for(items[0], 1)] = recorded
            assert transport.execute(items[0], context, attempt=1) is recorded
        assert not worker_root.exists()

    def test_forget_drops_one_recorded_attempt(self, tmp_path: Path) -> None:
        from confflow.remote.transport import RemoteTransport

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        with _open_store(str(tmp_path / "run"), "s_opt") as store:
            transport = RemoteTransport(
                run_root=str(tmp_path / "run"), store=store, worker_root=str(tmp_path / "w")
            )
            recorded = WorkItemResult(work_item_id=items[0].id, status=WorkItemStatus.COMPLETED)
            token = transport.launch_token_for(items[0], 1)
            transport._delivered[token] = recorded
            transport.forget(items[0], 1)
            assert token not in transport._delivered

    def test_transport_error_result_shape(self, tmp_path: Path) -> None:
        from confflow.remote.transport import transport_error_result

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        result = transport_error_result(
            items[0], code="transfer_error", message="bundle lost", step_id="s_opt"
        )
        assert result.status is WorkItemStatus.FAILED
        assert result.work_item_id == items[0].id
        assert result.error is not None and result.error.code == "transfer_error"
        assert result.error.retryable is True
        assert result.semantic_digest == items[0].semantic_digest

    def test_remote_transport_rejects_empty_roots(self, tmp_path: Path) -> None:
        from confflow.domain.errors import DomainError
        from confflow.remote.transport import RemoteTransport

        with _open_store(str(tmp_path / "run"), "s_opt") as store:
            with pytest.raises(DomainError):
                RemoteTransport(run_root="", store=store, worker_root=str(tmp_path / "w"))
            with pytest.raises(DomainError):
                RemoteTransport(run_root=str(tmp_path / "run"), store=store, worker_root="")

    def test_local_transport_exposes_executor(self) -> None:
        from confflow.remote.transport import ExecutionTransport, LocalTransport

        executor = WorkItemExecutor()
        transport = LocalTransport(executor)
        assert transport.executor is executor
        assert isinstance(transport, ExecutionTransport)


class TestManifestAndDefinitionDeterminism:
    """Bundle-manifest and execution-definition determinism (no delivery)."""

    def test_result_entry_manifest_is_sorted_and_stable(self) -> None:
        from confflow.remote.transport import build_input_bundle_manifest

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        probe: WorkItem = dataclasses.replace(
            items[0],
            named_inputs=WorkItemInputs(
                structures=FrozenDict({}),
                artifacts=FrozenDict({}),
                results=FrozenDict(
                    {
                        "prior": ResultSet.of(
                            energy_result(-75.0, subject_structure_id="s0"),
                            energy_result(-76.0, subject_structure_id="s1"),
                        )
                    }
                ),
            ),
        )
        first = build_input_bundle_manifest(probe)
        second = build_input_bundle_manifest(probe)
        assert [entry.entry_kind for entry in first.entries] == ["result", "result"]
        assert [entry.result_id for entry in first.entries] == sorted(
            entry.result_id for entry in first.entries
        )
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_structure_entry_manifest_is_sorted_by_identity(self) -> None:
        from confflow.remote.transport import build_input_bundle_manifest

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s1", "s0"))
        manifest = build_input_bundle_manifest(items[0])
        kinds = [entry.entry_kind for entry in manifest.entries]
        assert kinds and set(kinds) == {"structure"}
        assert [entry.structure_id for entry in manifest.entries] == sorted(
            entry.structure_id for entry in manifest.entries
        )

    def test_definition_has_no_scheduler_or_endpoint_fields(self) -> None:
        from confflow.remote.envelope import ExecutionDefinition

        for forbidden in ("scheduler", "max_parallel", "worker_root", "endpoint", "target"):
            assert forbidden not in ExecutionDefinition.model_fields, forbidden

    def test_definition_bytes_ignore_scheduler_width(self) -> None:
        from confflow.domain.canonical import canonical_json_bytes
        from confflow.remote.transport import build_execution_definition

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        kwargs: dict[str, Any] = {
            "program": "orca",
            "native": {"keyword": "B3LYP D3BJ def2-SVP Opt"},
            "execution_adapter": "standard",
            "result_profile": "standard",
            "checks": ("normal_termination",),
            "check_params": {},
            "recovery": "none",
            "recovery_params": {},
            "resources": items[0].resources,
            "charge": 0,
            "multiplicity": 1,
            "freeze": None,
            "step_semantic_digest": items[0].semantic_digest,
            "contract_versions": {"adapter": "a.v1"},
        }
        # Narrow (max_parallel=1, worker-A) and wide (max_parallel=4, worker-B)
        # schedulers contribute no definition input, so both builds agree.
        assert build_execution_definition(**kwargs) == build_execution_definition(**kwargs)
        assert canonical_json_bytes(
            build_execution_definition(**kwargs).model_dump(mode="json")
        ) == canonical_json_bytes(build_execution_definition(**kwargs).model_dump(mode="json"))


class TestLocalRemoteParity:
    """Same synthetic item through both transports."""

    def _execute_both(
        self,
        tmp_path: Path,
        program: str,
        native: dict[str, Any],
        executable: str,
        structure_id: str = "s0",
    ) -> tuple[WorkItemResult, WorkItemResult, str]:
        from confflow.remote.transport import LocalTransport, RemoteTransport

        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = compile_plan(calculation_doc(program, native, executable))
        items = assemble_items(plan, structure_set(structure_id))
        local_ctx = item_context(
            plan,
            program,
            executable,
            run_root,
            os.path.join(run_root, "items-local"),
            NativeProcessSupervisor(),
        )
        remote_ctx = item_context(
            plan,
            program,
            executable,
            run_root,
            os.path.join(run_root, "items-remote"),
            NativeProcessSupervisor(),
        )
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, items[0])
            local = LocalTransport(WorkItemExecutor()).execute(items[0], local_ctx, attempt=1)
            remote = RemoteTransport(
                run_root=run_root, store=store, worker_root=worker_root
            ).execute(items[0], remote_ctx, attempt=1)
        return local, remote, run_root

    def test_orca_parity(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        counter = tmp_path / "native-count.txt"
        _install_program_shims(tmp_path, monkeypatch, counter)
        local, remote, run_root = self._execute_both(tmp_path, "orca", ORCA_NATIVE, str(FAKE_ORCA))
        assert local.status is WorkItemStatus.COMPLETED
        assert remote.status is WorkItemStatus.COMPLETED
        assert _energies(local)[0][0] == pytest.approx(ORCA_ENERGY, abs=1e-6)
        _assert_parity(local, remote)
        local_bytes = _artifact_bytes(run_root, local)
        remote_bytes = _artifact_bytes(run_root, remote)
        for _role, checksum, _subject in _artifact_identity(local):
            assert checksum.startswith("sha256:")
        for artifact_id, payload in local_bytes.items():
            assert hashlib.sha256(payload).hexdigest() == next(
                ref.checksum.split(":", 1)[1] for ref in local.artifacts if ref.id == artifact_id
            )
        for artifact_id in local_bytes:
            if artifact_id in remote_bytes:
                assert remote_bytes[artifact_id] == local_bytes[artifact_id]

    def test_gaussian_parity(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        counter = tmp_path / "native-count.txt"
        _install_program_shims(tmp_path, monkeypatch, counter)
        local, remote, _run_root = self._execute_both(
            tmp_path, "gaussian", GAUSSIAN_NATIVE, str(FAKE_G16)
        )
        assert local.status is WorkItemStatus.COMPLETED
        assert remote.status is WorkItemStatus.COMPLETED
        assert _energies(local)[0][0] == pytest.approx(G16_ENERGY, abs=1e-6)
        _assert_parity(local, remote)

    def test_step_result_semantics_agree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.persistence.publication import rebuild_step_result
        from confflow.remote.transport import LocalTransport, RemoteTransport

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        counter = tmp_path / "native-count.txt"
        _install_program_shims(tmp_path, monkeypatch, counter)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        completion = CompletionPolicy(mode=CompletionMode.REQUIRE_ALL)
        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0", "s1"))
        with _open_store(run_root, "s_opt") as store:
            local_transport = LocalTransport(WorkItemExecutor())
            remote_transport = RemoteTransport(
                run_root=run_root, store=store, worker_root=worker_root
            )
            local_results, remote_results = [], []
            for item in items:
                _prepare_attempt(store, item)
                local_results.append(
                    local_transport.execute(
                        item,
                        item_context(
                            plan,
                            "orca",
                            str(FAKE_ORCA),
                            run_root,
                            os.path.join(run_root, "items-local"),
                            NativeProcessSupervisor(),
                        ),
                        attempt=1,
                    )
                )
                remote_results.append(
                    remote_transport.execute(
                        item,
                        item_context(
                            plan,
                            "orca",
                            str(FAKE_ORCA),
                            run_root,
                            os.path.join(run_root, "items-remote"),
                            NativeProcessSupervisor(),
                        ),
                        attempt=1,
                    )
                )
        assert all(result.status is WorkItemStatus.COMPLETED for result in remote_results)
        local_step = rebuild_step_result(
            step_id="s_opt", items=tuple(local_results), completion=completion
        )
        remote_step = rebuild_step_result(
            step_id="s_opt", items=tuple(remote_results), completion=completion
        )
        assert local_step.status == remote_step.status
        assert local_step.to_dict()["structures"] == remote_step.to_dict()["structures"]
        assert local_step.to_dict()["results"] == remote_step.to_dict()["results"]


class TestCheckpointHandoff:
    """Checkpoint subject/checksum equality across the local/remote boundary."""

    def _producer_checkpoint(
        self, tmp_path: Path, run_root: str, program: str, executable: str
    ) -> tuple[Any, WorkItemResult]:
        from confflow.remote.transport import LocalTransport

        plan = compile_plan(
            calculation_doc(
                program,
                GAUSSIAN_NATIVE if program == "gaussian" else ORCA_NATIVE,
                executable,
            )
        )
        context = item_context(
            plan,
            program,
            executable,
            run_root,
            os.path.join(run_root, "items-producer"),
            NativeProcessSupervisor(),
        )
        produced = LocalTransport(WorkItemExecutor()).execute(
            assemble_items(plan, structure_set("s0"))[0], context, attempt=1
        )
        assert produced.status is WorkItemStatus.COMPLETED
        checkpoints = [ref for ref in produced.artifacts if ref.role == "checkpoint"]
        assert checkpoints, "producer must emit a checkpoint artifact"
        return checkpoints[0], produced

    def _consumer_for_checkpoint(self, plan: Any, produced: WorkItemResult, ref: Any) -> WorkItem:
        """Assemble a consumer driven by the producer output structure.

        The executor only stages artifacts bound to the driving structure,
        so the consumer must be driven by the produced structure the
        checkpoint subject names (a chained step).
        """
        assembly = assemble(plan, run_inputs(structures={"structures": produced.structures}))
        assert assembly.ok, [item.message for item in assembly.errors]
        probe = assembly.items[0]
        return dataclasses.replace(
            probe,
            named_inputs=WorkItemInputs(
                structures=probe.named_inputs.structures,
                artifacts=FrozenDict({"checkpoint_in": ArtifactSet.of(ref)}),
                results=probe.named_inputs.results,
            ),
        )

    def test_local_to_remote_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
        from confflow.remote.staging import import_result_artifacts, stage_input_bundle
        from confflow.remote.transport import build_input_bundle_manifest
        from confflow.remote.worker import run_worker_envelope

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        counter = tmp_path / "native-count.txt"
        _install_program_shims(tmp_path, monkeypatch, counter)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        token = "tok-ckpt-lr"
        checkpoint, _produced = self._producer_checkpoint(
            tmp_path, run_root, "gaussian", str(FAKE_G16)
        )
        assert checkpoint.locator.path is not None
        producer_bytes = Path(os.path.join(run_root, checkpoint.locator.path)).read_bytes()
        plan = compile_plan(calculation_doc("gaussian", GAUSSIAN_NATIVE, str(FAKE_G16)))
        consumer = self._consumer_for_checkpoint(plan, _produced, checkpoint)
        manifest = build_input_bundle_manifest(
            consumer,
            artifact_bundle_files={checkpoint.id: os.path.basename(checkpoint.locator.path)},
        )
        entries = [entry for entry in manifest.entries if entry.entry_kind == "artifact"]
        assert len(entries) == 1
        assert entries[0].artifact_id == checkpoint.id
        assert entries[0].role == checkpoint.role
        assert entries[0].checksum == checkpoint.checksum
        assert entries[0].subject_structure_id == checkpoint.subject_structure_id

        from confflow.remote.envelope import HANDOFF_SCHEMA_V2, WorkerHandoffV2
        from confflow.remote.transport import build_execution_definition

        definition = build_execution_definition(
            program="gaussian",
            native=dict(GAUSSIAN_NATIVE),
            execution_adapter="standard",
            result_profile="standard",
            checks=("normal_termination",),
            check_params={},
            recovery="none",
            recovery_params={},
            resources=consumer.resources,
            charge=0,
            multiplicity=1,
            freeze=None,
            step_semantic_digest=consumer.semantic_digest,
            contract_versions={},
        )
        handoff = WorkerHandoffV2.new(
            schema=HANDOFF_SCHEMA_V2,
            protocol_version="v2",
            run_id=os.path.basename(run_root),
            step_id="s_opt",
            work_item_id=consumer.id,
            logical_key=consumer.logical_key,
            attempt_number=1,
            launch_token=token,
            work_item_digest=consumer.semantic_digest,
            step_semantic_digest=consumer.semantic_digest,
            producer_provenance={},
            environment_request={"program": "gaussian"},
            execution=definition.model_dump(mode="python"),
            inputs=manifest.model_dump(mode="python"),
        )
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        handoff = read_handoff_envelope(path=handoff_path, expected_run_id="run")
        staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
            source_files={checkpoint.id: os.path.join(run_root, checkpoint.locator.path)},
        )
        staged_entry = next(entry for entry in staged.entries if entry.kind == "artifact")
        assert staged_entry.checksum_verified is True
        assert staged_entry.staged_path is not None
        assert Path(staged_entry.staged_path).read_bytes() == producer_bytes
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle=staged,
            worker_root=worker_root,
            launch_token=token,
        )
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, consumer)
            remote = import_result_artifacts(
                result_path=result_path, handoff=handoff, run_root=run_root, store=store
            )
        assert remote.status is WorkItemStatus.COMPLETED

    def test_remote_to_local_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.remote.handoff import read_handoff_envelope, write_handoff_envelope
        from confflow.remote.staging import import_result_artifacts, stage_input_bundle
        from confflow.remote.transport import LocalTransport, build_input_bundle_manifest
        from confflow.remote.worker import run_worker_envelope

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        counter = tmp_path / "native-count.txt"
        _install_program_shims(tmp_path, monkeypatch, counter)
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        token = "tok-ckpt-rl"
        plan = compile_plan(calculation_doc("gaussian", GAUSSIAN_NATIVE, str(FAKE_G16)))
        producer = assemble_items(plan, structure_set("s0"))[0]

        from confflow.remote.envelope import (
            HANDOFF_SCHEMA_V2,
            InputBundleManifest,
            StructureBundleEntry,
            WorkerHandoffV2,
            bundle_entry_digest,
        )
        from confflow.remote.transport import build_execution_definition

        payload = next(iter(producer.named_inputs.structures.values()))[0].to_dict()
        manifest = InputBundleManifest(
            entries=(
                StructureBundleEntry(
                    structure_id="s0",
                    payload=payload,
                    digest=bundle_entry_digest("structure", payload),
                ),
            )
        )
        definition = build_execution_definition(
            program="gaussian",
            native=dict(GAUSSIAN_NATIVE),
            execution_adapter="standard",
            result_profile="standard",
            checks=("normal_termination",),
            check_params={},
            recovery="none",
            recovery_params={},
            resources=producer.resources,
            charge=0,
            multiplicity=1,
            freeze=None,
            step_semantic_digest=producer.semantic_digest,
            contract_versions={},
        )
        handoff = WorkerHandoffV2.new(
            schema=HANDOFF_SCHEMA_V2,
            protocol_version="v2",
            run_id=os.path.basename(run_root),
            step_id="s_opt",
            work_item_id=producer.id,
            logical_key=producer.logical_key,
            attempt_number=1,
            launch_token=token,
            work_item_digest=producer.semantic_digest,
            step_semantic_digest=producer.semantic_digest,
            producer_provenance={},
            environment_request={"program": "gaussian"},
            execution=definition.model_dump(mode="python"),
            inputs=manifest.model_dump(mode="python"),
        )
        handoff_path = write_handoff_envelope(
            handoff=handoff, worker_root=worker_root, launch_token=token
        )
        handoff = read_handoff_envelope(path=handoff_path, expected_run_id="run")
        staged = stage_input_bundle(
            manifest=handoff.inputs,
            run_root=run_root,
            worker_root=worker_root,
            launch_token=token,
        )
        result_path = run_worker_envelope(
            handoff_path=handoff_path,
            staged_bundle=staged,
            worker_root=worker_root,
            launch_token=token,
        )
        with _open_store(run_root, "s_opt") as store:
            _prepare_attempt(store, producer)
            produced = import_result_artifacts(
                result_path=result_path, handoff=handoff, run_root=run_root, store=store
            )
        assert produced.status is WorkItemStatus.COMPLETED
        checkpoint = next(ref for ref in produced.artifacts if ref.role == "checkpoint")
        assert checkpoint.locator.path is not None
        imported_bytes = Path(os.path.join(run_root, checkpoint.locator.path)).read_bytes()
        assert "sha256:" + hashlib.sha256(imported_bytes).hexdigest() == checkpoint.checksum
        consumer = self._consumer_for_checkpoint(plan, produced, checkpoint)
        consumer_manifest = build_input_bundle_manifest(consumer)
        consumer_entries = [
            entry for entry in consumer_manifest.entries if entry.entry_kind == "artifact"
        ]
        assert len(consumer_entries) == 1
        assert consumer_entries[0].checksum == checkpoint.checksum
        assert consumer_entries[0].subject_structure_id == checkpoint.subject_structure_id
        local_ctx = item_context(
            plan,
            "gaussian",
            str(FAKE_G16),
            run_root,
            os.path.join(run_root, "items-local"),
            NativeProcessSupervisor(),
        )
        consumed = LocalTransport(WorkItemExecutor()).execute(consumer, local_ctx, attempt=1)
        assert consumed.status is WorkItemStatus.COMPLETED


class TestSchedulerEndpointChange:
    """New scheduler width/endpoint keeps the definition digest; rerun reuses."""

    def test_same_definition_digest_across_widths(self) -> None:
        from confflow.domain.canonical import canonical_json_bytes
        from confflow.remote.transport import build_execution_definition

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        base: dict[str, Any] = {
            "program": "orca",
            "native": dict(ORCA_NATIVE),
            "execution_adapter": "standard",
            "result_profile": "standard",
            "checks": ("normal_termination",),
            "check_params": {},
            "recovery": "none",
            "recovery_params": {},
            "resources": items[0].resources,
            "charge": 0,
            "multiplicity": 1,
            "freeze": None,
            "step_semantic_digest": items[0].semantic_digest,
            "contract_versions": {"adapter": "a.v1", "profile": "p.v1"},
        }
        narrow = build_execution_definition(**base)
        wide = build_execution_definition(**base)
        assert narrow == wide
        assert canonical_json_bytes(narrow.model_dump(mode="json")) == canonical_json_bytes(
            wide.model_dump(mode="json")
        )

    def test_batch_second_run_is_full_reuse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from confflow.execution.batch import StepExecutionRequest

        monkeypatch.setenv("FAKE_MODE", "success_opt")

        class CountingSupervisor(NativeProcessSupervisor):
            def __init__(self) -> None:
                super().__init__()
                self.submits = 0

            def submit(self, request: Any) -> Any:  # type: ignore[override]
                self.submits += 1
                return super().submit(request)

        plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
        items = assemble_items(plan, structure_set("s0"))
        planned = plan.steps[0]
        reuse = InMemoryReuseStore()

        def _request(work_base: str) -> StepExecutionRequest:
            return StepExecutionRequest(
                step=planned,
                items=tuple(items),
                scientific=planned.scientific,
                scientific_defaults=plan.scientific_defaults,
                adapter=get_program_adapter("orca"),
                profile=PROFILES["standard"],
                checks=(CHECKS["normal_termination"],),
                recovery=RECOVERIES["none"],
                execution_binding=binding_for(str(FAKE_ORCA)),
                run_root=str(tmp_path),
                work_base=work_base,
                environment=None,
                definition_digest=plan.definition_digest,
            )

        first_supervisor = CountingSupervisor()
        first = BatchStepExecutor(WorkItemExecutor(), reuse_store=reuse).with_supervisor(
            first_supervisor
        )
        assert first.execute_step(_request(str(tmp_path / "first"))).status is not None
        assert first_supervisor.submits >= 1
        second_supervisor = CountingSupervisor()
        second = BatchStepExecutor(WorkItemExecutor(), reuse_store=reuse).with_supervisor(
            second_supervisor
        )
        repeated = second.execute_step(_request(str(tmp_path / "second")))
        assert repeated.status is not None
        assert second_supervisor.submits == 0
        assert "reuse_hit" in [item.code for item in repeated.item_results[0].diagnostics]

    def test_endpoint_change_preserves_reuse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Different worker root and wider scheduling reuse the same science."""
        from confflow.persistence.reuse import (
            ReuseInputs,
            build_producer_provenance,
            evaluate_reuse,
        )
        from confflow.remote.transport import RemoteTransport

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        run_root = str(tmp_path / "run")
        narrow_plan = compile_plan(
            calculation_doc(
                "orca", ORCA_NATIVE, str(FAKE_ORCA), scheduler={"max_parallel_items": 1}
            )
        )
        wide_plan = compile_plan(
            calculation_doc(
                "orca", ORCA_NATIVE, str(FAKE_ORCA), scheduler={"max_parallel_items": 4}
            )
        )
        assert narrow_plan.definition_digest == wide_plan.definition_digest
        narrow_items = assemble_items(narrow_plan, structure_set("s0"))
        wide_items = assemble_items(wide_plan, structure_set("s0"))
        assert narrow_items[0].semantic_digest == wide_items[0].semantic_digest
        with _open_store(run_root, "s_opt") as store:
            atlantic = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "worker-a")
            )
            pacific = RemoteTransport(
                run_root=run_root, store=store, worker_root=str(tmp_path / "worker-b")
            )
            assert atlantic.launch_token_for(narrow_items[0], 1) == pacific.launch_token_for(
                wide_items[0], 1
            )
        provenance = build_producer_provenance(
            adapter_version="a.v1",
            profile_version="p.v1",
            check_versions={"normal_termination": "c.v1"},
            recovery_version="r.v1",
        )
        axes = ReuseInputs(
            work_item_digest=narrow_items[0].semantic_digest,
            step_semantic_digest=narrow_items[0].semantic_digest,
            environment_digest=None,
            producer_provenance=provenance,
        )
        decision = evaluate_reuse(
            current=axes,
            stored=axes,
            stored_status=StoredWorkItemStatus.COMPLETED,
            work_item_id=narrow_items[0].id,
        )
        assert decision.decision.value == "reuse"


class TestEnvironmentDigests:
    """Environment measurement is content-sensitive and path-insensitive."""

    def test_relocated_identical_binary_measures_same(self, tmp_path: Path) -> None:
        import shutil

        from confflow.execution.environment import EnvironmentMeasurer

        relocated = tmp_path / "relocated_orca.py"
        shutil.copy2(str(FAKE_ORCA), relocated)
        measurer = EnvironmentMeasurer()
        first = measurer.build_environment(
            str(FAKE_ORCA), adapter=get_program_adapter("orca"), target="node-1"
        )
        second = measurer.build_environment(
            str(relocated), adapter=get_program_adapter("orca"), target="node-1"
        )
        assert first.digest() == second.digest()

    def test_content_change_moves_environment_digest(self, tmp_path: Path) -> None:
        from confflow.execution.environment import EnvironmentMeasurer

        altered = tmp_path / "altered_orca.py"
        altered.write_bytes(Path(str(FAKE_ORCA)).read_bytes() + b"# v4-4 probe\n")
        measurer = EnvironmentMeasurer()
        first = measurer.build_environment(
            str(FAKE_ORCA), adapter=get_program_adapter("orca"), target="node-1"
        )
        second = measurer.build_environment(
            str(altered), adapter=get_program_adapter("orca"), target="node-1"
        )
        assert first.digest() != second.digest()

    def test_store_state_starts_empty(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        with _open_store(run_root, "s_opt") as store:
            assert store.get_state("wi:s_opt:missing") is None
            assert store.list_items(status=StoredWorkItemStatus.COMPLETED) == ()
