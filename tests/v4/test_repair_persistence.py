#!/usr/bin/env python3

"""Wave-1 D scoped tests: importer identity, import maps, strict publication.

Covers D-owned behavior only: opaque XYZ entity IDs, durable import-map
arbitration (including real competing processes), strict item-backed
publication refusal, and the generic application lifecycle (RunState,
schema-conformant durable manifest, item-backed resume) through a
test-registered executor.  No production code outside D ownership is
exercised beyond its frozen APIs.
"""

from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from confflow.application.v4_run import (
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import WorkItemStatus
from confflow.domain.errors import DomainError
from confflow.domain.work_item import Timing, WorkItemResult
from confflow.execution import ExecutorCapability, build_default_registry
from confflow.persistence import (
    CorruptStateError,
    PersistenceError,
    load_import_map,
    load_run_state,
    resolve_imported_structures,
    save_import_map,
    source_content_digest,
)
from confflow.persistence.contracts import RunStepStatus, store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.producer.contract import run_result_json_schema
from confflow.workflow.v4.assembly import RunInputs
from tests.v4._builders import calc_step, v4_doc

WATER_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.760000 0.590000 0.000000
H 0.760000 -0.590000 0.000000
"""

TWO_WATER_XYZ = WATER_XYZ + WATER_XYZ


def _two_step_doc() -> dict[str, Any]:
    opt = calc_step(
        "s_opt",
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 2},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
    )
    sp = calc_step(
        "s_sp",
        program="orca",
        bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
        native={"keyword": "B3LYP SP"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": 2},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
    )
    return v4_doc(
        [opt, sp],
        inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        global_config={"scientific_defaults": {"charge": 0, "multiplicity": 1}},
    )


class EchoExecutor:
    """Test-only executor CLASS: echo input structures as completed outputs.

    Registered as a class (per the atomic registry: the registry stores
    the class, application instantiates it).  Calls are logged on the
    class so tests can observe executions across instances.  Emitted
    results carry producer-scoped ids stamped with the producing item's
    semantic digest as ``producer_digest``.
    """

    calls: list[str] = []
    attempts: list[Any] = []

    def execute(self, work_item: Any, context: Any, *, should_cancel: Any = None) -> Any:
        from confflow.domain.result import ResultSet, ScientificResult, make_result_id
        from confflow.domain.units import Unit

        type(self).calls.append(work_item.id)
        type(self).attempts.append(context.attempt)
        merged = StructureSet()
        for value in work_item.named_inputs.structures.values():
            merged = merged + value
        subject = merged[0].id if len(merged) else None
        stamped = (
            ResultSet.of(
                ScientificResult(
                    kind="echo",
                    value=1.0,
                    unit=Unit.HARTREE,
                    subject_structure_id=subject,
                    source_step_id=work_item.step_id,
                    source_work_item_id=work_item.id,
                    result_id=make_result_id(
                        step_id=work_item.step_id,
                        work_item_id=work_item.id,
                        kind="echo",
                        subject_structure_id=subject,
                        producer_digest=work_item.semantic_digest,
                    ),
                )
            )
            if len(merged)
            else ResultSet()
        )
        return WorkItemResult(
            work_item_id=work_item.id,
            status=WorkItemStatus.COMPLETED,
            structures=merged,
            results=stamped,
            timing=Timing(duration_seconds=0.0),
            semantic_digest=work_item.semantic_digest,
        )


def _competing_import(payload: tuple[str, str, str]) -> list[str]:
    """Import and resolve in a separate process; return adopted entity IDs."""
    run_root, input_name, text = payload
    fresh = import_xyz(text, source_name="race.xyz")
    resolved = resolve_imported_structures(
        run_root=run_root,
        input_name=input_name,
        source_name="race.xyz",
        source_text=text,
        fresh=fresh,
    )
    return [record.id for record in resolved]


class TestOpaqueImporter:
    def test_fresh_imports_mint_independent_ids(self) -> None:
        first = import_xyz(WATER_XYZ, source_name="a.xyz")
        second = import_xyz(WATER_XYZ, source_name="a.xyz")
        assert [r.id for r in first] != [r.id for r in second]
        assert not any(r.id.startswith("xyz:") for r in first)

    def test_duplicate_geometries_stay_distinct(self) -> None:
        structures = import_xyz(TWO_WATER_XYZ, source_name="two.xyz")
        ids = [record.id for record in structures]
        assert len(ids) == 2 and ids[0] != ids[1]

    def test_explicit_ids_bind_positionally(self) -> None:
        structures = import_xyz(TWO_WATER_XYZ, entity_ids=("ent-a", "ent-b"))
        assert [record.id for record in structures] == ["ent-a", "ent-b"]

    def test_explicit_ids_count_mismatch_fails(self) -> None:
        with pytest.raises(DomainError):
            import_xyz(TWO_WATER_XYZ, entity_ids=("only-one",))

    def test_explicit_ids_duplicates_fail(self) -> None:
        with pytest.raises(DomainError):
            import_xyz(TWO_WATER_XYZ, entity_ids=("dup", "dup"))

    def test_malformed_still_fails(self) -> None:
        with pytest.raises(DomainError):
            import_xyz("", source_name="empty.xyz")


class TestImportMaps:
    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        structures = import_xyz(TWO_WATER_XYZ, source_name="in.xyz")
        saved = save_import_map(
            run_root=run_root,
            input_name="structures",
            source_name="in.xyz",
            source_text=TWO_WATER_XYZ,
            structures=structures,
        )
        assert saved["source_content_digest"] == source_content_digest(TWO_WATER_XYZ)
        loaded = load_import_map(run_root=run_root, input_name="structures")
        assert loaded == saved

    def test_same_digest_republish_keeps_winner_ids(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        first = import_xyz(TWO_WATER_XYZ, source_name="in.xyz")
        save_import_map(
            run_root=run_root,
            input_name="structures",
            source_name="in.xyz",
            source_text=TWO_WATER_XYZ,
            structures=first,
        )
        second = import_xyz(TWO_WATER_XYZ, source_name="in.xyz")
        assert [r.id for r in second] != [r.id for r in first]
        winner = save_import_map(
            run_root=run_root,
            input_name="structures",
            source_name="in.xyz",
            source_text=TWO_WATER_XYZ,
            structures=second,
        )
        assert winner["entity_ids"] == [r.id for r in first]

    def test_changed_bytes_fail_closed(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        save_import_map(
            run_root=run_root,
            input_name="structures",
            source_name="in.xyz",
            source_text=TWO_WATER_XYZ,
            structures=import_xyz(TWO_WATER_XYZ),
        )
        with pytest.raises(PersistenceError):
            save_import_map(
                run_root=run_root,
                input_name="structures",
                source_name="in.xyz",
                source_text=WATER_XYZ,
                structures=import_xyz(WATER_XYZ),
            )

    def test_corrupt_map_fails_closed(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        target = tmp_path / "run" / "imports" / "structures.json"
        target.parent.mkdir(parents=True)
        target.write_text("{not json")
        with pytest.raises(CorruptStateError):
            load_import_map(run_root=run_root, input_name="structures")

    def test_resolve_reloads_winner_ids(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        original = resolve_imported_structures(
            run_root=run_root,
            input_name="structures",
            source_name="in.xyz",
            source_text=TWO_WATER_XYZ,
            fresh=import_xyz(TWO_WATER_XYZ),
        )
        again = resolve_imported_structures(
            run_root=run_root,
            input_name="structures",
            source_name="in.xyz",
            source_text=TWO_WATER_XYZ,
            fresh=import_xyz(TWO_WATER_XYZ),
        )
        assert [r.id for r in again] == [r.id for r in original]

    def test_resolve_changed_bytes_fails(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        resolve_imported_structures(
            run_root=run_root,
            input_name="structures",
            source_name="in.xyz",
            source_text=TWO_WATER_XYZ,
            fresh=import_xyz(TWO_WATER_XYZ),
        )
        with pytest.raises(PersistenceError):
            resolve_imported_structures(
                run_root=run_root,
                input_name="structures",
                source_name="in.xyz",
                source_text=WATER_XYZ,
                fresh=import_xyz(WATER_XYZ),
            )

    def test_competing_processes_converge(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        payload = (run_root, "structures", TWO_WATER_XYZ)
        with ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_competing_import, [payload] * 4))
        first = results[0]
        assert len(first) == 2 and first[0] != first[1]
        assert all(entry == first for entry in results)
        assert load_import_map(run_root=run_root, input_name="structures") is not None
        assert load_import_map(run_root=run_root, input_name="structures")["entity_ids"] == first


class TestStrictPublication:
    def test_invalidated_item_refuses_to_publish(self, tmp_path: Path) -> None:
        from confflow.domain.completion import WorkItemStatus as _S
        from confflow.domain.work_item import WorkItemResult as _R
        from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
        from confflow.execution.contracts import ExecutionEnvironment
        from confflow.execution.work_item_executor import WorkItemExecutor
        from confflow.persistence import OwnerIdentity
        from confflow.persistence.reuse import build_producer_provenance
        from confflow.workflow.v4.assembly import assemble_work_items
        from confflow.workflow.v4.compiler import compile_workflow

        registry = build_default_registry()
        run_root = str(tmp_path / "run")
        compiled = compile_workflow(_two_step_doc())
        assert compiled.ok and compiled.plan is not None
        plan = compiled.plan
        structures = import_xyz(WATER_XYZ, source_name="in.xyz")
        assembly = assemble_work_items(
            plan, RunInputs(structures=FrozenDict({"structures": structures}))
        )
        assert assembly.ok
        (item,) = assembly.for_step("s_opt")
        adapter = registry.resolve_program("orca")
        environment = ExecutionEnvironment(
            program="orca",
            program_version="test",
            executable_digest="sha256:" + "0" * 64,
            target=None,
        )
        provenance = build_producer_provenance(
            adapter_version=adapter.adapter_version,
            profile_version=registry.profile_implementation("standard").contract_version,
            check_versions={
                "normal_termination": registry.check_implementation(
                    "normal_termination"
                ).contract_version
            },
            recovery_version=registry.recovery_implementation("none").contract_version,
        )
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            store.register_item(
                work_item_id=item.id,
                logical_key=item.logical_key,
                step_id=item.step_id,
                work_item_digest="sha256:" + "f" * 64,
                step_semantic_digest=plan.steps[0].step_semantic_digest,
                environment_digest=environment.digest(),
                producer_provenance=dict(provenance.thaw()),
            )
            store.claim(item.id, owner=OwnerIdentity(owner_token="old"))
            store.complete(
                item.id,
                result=_R(
                    work_item_id=item.id,
                    status=_S.COMPLETED,
                    semantic_digest=item.semantic_digest,
                ),
            )
            batch = BatchStepExecutor(WorkItemExecutor()).with_supervisor(object())
            step_request = StepExecutionRequest(
                step=plan.steps[0],
                items=(item,),
                scientific=plan.steps[0].scientific,
                scientific_defaults=plan.scientific_defaults,
                adapter=adapter,
                profile=registry.profile_implementation("standard"),
                checks=(registry.check_implementation("normal_termination"),),
                recovery=registry.recovery_implementation("none"),
                run_root=run_root,
                environment=environment,
            )
            with pytest.raises(PersistenceError, match="refusing to publish"):
                batch.execute_step_resumable(
                    step_request, store=store, run_root=run_root, owner_token="test"
                )
            assert not os.path.exists(os.path.join(run_root, "steps", "s_opt", "step_result.json"))

    def test_missing_environment_is_prohibited(self, tmp_path: Path) -> None:
        from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
        from confflow.execution.work_item_executor import WorkItemExecutor
        from confflow.workflow.v4.compiler import compile_workflow

        run_root = str(tmp_path / "run")
        compiled = compile_workflow(_two_step_doc())
        assert compiled.ok and compiled.plan is not None
        plan = compiled.plan
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            batch = BatchStepExecutor(WorkItemExecutor()).with_supervisor(object())
            step_request = StepExecutionRequest(
                step=plan.steps[0],
                items=(),
                scientific=plan.steps[0].scientific,
                scientific_defaults=plan.scientific_defaults,
                run_root=run_root,
            )
            with pytest.raises(PersistenceError, match="measured environment"):
                batch.execute_step_resumable(
                    step_request, store=store, run_root=run_root, owner_token="test"
                )
            assert not os.path.exists(os.path.join(run_root, "steps", "s_opt", "step_result.json"))


class TestGenericApplicationLifecycle:
    def _registry(self) -> Any:
        registry = build_default_registry()
        EchoExecutor.calls = []
        EchoExecutor.attempts = []
        registry.register_executor(registry.executor(ExecutorCapability.CALCULATION), EchoExecutor)
        return registry

    def _request(self, run_root: str, structures: Any) -> V4RunRequest:
        true_exe = shutil.which("true") or "/bin/true"
        return V4RunRequest(
            workflow_document=_two_step_doc(),
            run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
            run_root=run_root,
            executables=FrozenDict({"orca": true_exe}),
            supervisor=object(),
            import_sources=FrozenDict({"structures": TWO_WATER_XYZ}),
        )

    def test_run_publishes_manifest_runstate_and_reuses(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        registry = self._registry()
        first = V4RunApplication(supervisor=object(), registry=registry).run(
            self._request(run_root, import_xyz(TWO_WATER_XYZ))
        )
        assert first.status == "completed"
        assert [r.step_id for r in first.step_results] == ["s_opt", "s_sp"]
        assert len(EchoExecutor.calls) == 4
        assert EchoExecutor.attempts and all(a == 1 for a in EchoExecutor.attempts)
        for result in first.step_results:
            for record in result.results:
                assert record.result_id is not None
        manifest_path = tmp_path / "run" / RUN_RESULT_FILENAME
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        jsonschema.validate(instance=manifest, schema=run_result_json_schema())
        assert manifest["status"] == "completed"
        assert [s["id"] for s in manifest["steps"]] == ["s_opt", "s_sp"]
        assert all(s["digest"].startswith("sha256:") for s in manifest["steps"])
        state = load_run_state(run_root)
        assert state is not None
        assert {s.step_id: s.status for s in state.steps} == {
            "s_opt": RunStepStatus.COMPLETED,
            "s_sp": RunStepStatus.COMPLETED,
        }
        assert all(s.published_step_result_digest for s in state.steps)

        before = list(EchoExecutor.calls)
        second = V4RunApplication(supervisor=object(), registry=registry).run(
            self._request(run_root, import_xyz(TWO_WATER_XYZ))
        )
        assert second.status == "completed"
        assert list(EchoExecutor.calls) == before

    def test_close_reopen_preserves_result_identity(self, tmp_path: Path) -> None:
        from confflow.persistence import load_published_step_result

        run_root = str(tmp_path / "run")
        registry = self._registry()
        V4RunApplication(supervisor=object(), registry=registry).run(
            self._request(run_root, import_xyz(TWO_WATER_XYZ))
        )
        expected: dict[str, list[str]] = {}
        for step_id in ("s_opt", "s_sp"):
            with SqliteWorkItemStore.open(store_path(run_root, step_id)) as store:
                rows = []
                for item_id in store.list_items():
                    stored = store.get_result(item_id)
                    assert stored is not None
                    rows.extend(record.result_id or "" for record in stored.results)
                assert rows and all(rows)
                expected[step_id] = sorted(rows)
        # Actual close/reopen across distinct connections: identity survives.
        for step_id in ("s_opt", "s_sp"):
            with SqliteWorkItemStore.open(store_path(run_root, step_id)) as store:
                reopened = []
                for item_id in store.list_items():
                    stored = store.get_result(item_id)
                    assert stored is not None
                    reopened.extend(record.result_id or "" for record in stored.results)
                assert sorted(reopened) == expected[step_id]
            loaded = load_published_step_result(run_root=run_root, step_id=step_id)
            assert loaded is not None
            assert sorted(r.result_id or "" for r in loaded.results) == expected[step_id]

    def test_definition_change_over_same_root_fails_closed(self, tmp_path: Path) -> None:
        run_root = str(tmp_path / "run")
        registry = self._registry()
        V4RunApplication(supervisor=object(), registry=registry).run(
            self._request(run_root, import_xyz(TWO_WATER_XYZ))
        )
        doc = _two_step_doc()
        doc["steps"] = doc["steps"][:1]
        bad = V4RunRequest(
            workflow_document=doc,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(TWO_WATER_XYZ)})),
            run_root=run_root,
            executables=FrozenDict({"orca": shutil.which("true") or "/bin/true"}),
            supervisor=object(),
            import_sources=FrozenDict({"structures": TWO_WATER_XYZ}),
        )
        with pytest.raises(DomainError):
            V4RunApplication(supervisor=object(), registry=registry).run(bad)

    def test_unregistered_executor_fails_closed(self, tmp_path: Path) -> None:
        from confflow.execution.registry import ExecutionRegistry, RegistryLookupError

        bare = ExecutionRegistry()
        bare.register_executor(build_default_registry().executor(ExecutorCapability.CALCULATION))
        with pytest.raises(RegistryLookupError, match="no runtime executor"):
            V4RunApplication(supervisor=object(), registry=bare).run(
                self._request(str(tmp_path / "run"), import_xyz(TWO_WATER_XYZ))
            )


class TestResultIdentityGates:
    def _stamped(self, result_id: str | None) -> Any:
        from confflow.domain.result import ResultSet, ScientificResult
        from confflow.domain.units import Unit

        return ResultSet.of(
            ScientificResult(
                kind="energy",
                value=-76.0,
                unit=Unit.HARTREE,
                subject_structure_id="s",
                result_id=result_id,
            )
        )

    def _step_result(self, results: Any) -> Any:
        from confflow.domain.completion import CompletionPolicy, StepStatus, WorkItemStatus
        from confflow.domain.step_result import StepProvenance, StepResult
        from confflow.domain.work_item import WorkItemResult

        item = WorkItemResult(
            work_item_id="wi:s:all",
            status=WorkItemStatus.COMPLETED,
            results=results,
            semantic_digest="sha256:" + "0" * 64,
        )
        return StepResult(
            step_id="s",
            status=StepStatus.COMPLETED,
            results=results,
            item_results=(item,),
            provenance=StepProvenance(),
            summary=FrozenDict(
                {
                    "total": 1,
                    "completed": 1,
                    "failed": 0,
                    "cancelled": 0,
                    "completion_mode": CompletionPolicy().mode.value,
                    "status": "completed",
                }
            ),
        )

    def test_publish_rejects_missing_ids(self, tmp_path: Path) -> None:
        from confflow.persistence import publish_step_result

        result = self._step_result(self._stamped(None))
        with pytest.raises(PersistenceError, match="identity-less"):
            publish_step_result(run_root=str(tmp_path / "run"), step_id="s", step_result=result)

    def test_publish_rejects_duplicate_ids(self, tmp_path: Path) -> None:
        from confflow.domain.result import ResultSet
        from confflow.persistence import publish_step_result

        one = list(self._stamped("sha256:" + "a" * 64))[0]
        two = list(self._stamped("sha256:" + "a" * 64))[0]
        result = self._step_result(ResultSet.of(one, two))
        with pytest.raises(PersistenceError, match="duplicate result ids"):
            publish_step_result(run_root=str(tmp_path / "run"), step_id="s", step_result=result)

    def test_load_rejects_identity_less_publication(self, tmp_path: Path) -> None:
        import json as _json

        from confflow.persistence import load_published_step_result

        result = self._step_result(self._stamped(None))
        payload = result.to_dict()
        step_dir = tmp_path / "run" / "steps" / "s"
        step_dir.mkdir(parents=True)
        (step_dir / "step_result.json").write_text(_json.dumps(payload))
        with pytest.raises(CorruptStateError):
            load_published_step_result(run_root=str(tmp_path / "run"), step_id="s")

    def test_store_rebuild_rejects_corrupted_id(self, tmp_path: Path) -> None:
        from confflow.persistence.work_items import _build_scientific_result

        with pytest.raises(CorruptStateError):
            _build_scientific_result(
                {
                    "kind": "energy",
                    "value": 1.0,
                    "unit": "hartree",
                    "result_id": 42,
                }
            )

    def test_store_commit_rejects_missing_ids(self, tmp_path: Path) -> None:
        from confflow.domain.completion import WorkItemStatus as _S
        from confflow.domain.work_item import WorkItemResult as _R
        from confflow.persistence import OwnerIdentity

        with SqliteWorkItemStore.open(store_path(str(tmp_path / "run"), "s")) as store:
            store.register_item(
                work_item_id="wi:s:all",
                logical_key="s:all",
                step_id="s",
                work_item_digest="sha256:" + "0" * 64,
                step_semantic_digest="sha256:" + "1" * 64,
            )
            store.claim("wi:s:all", owner=OwnerIdentity(owner_token="t"))
            with pytest.raises(PersistenceError, match="identity-less"):
                store.complete(
                    "wi:s:all",
                    result=_R(
                        work_item_id="wi:s:all",
                        status=_S.COMPLETED,
                        results=self._stamped(None),
                        semantic_digest="sha256:" + "0" * 64,
                    ),
                )
