#!/usr/bin/env python3

"""V4-5 reaction chain through production adapters (main-owned).

Every test here runs the REAL program adapter (Gaussian/ORCA), the REAL
result profile (path_endpoints/ensemble/standard), and the REAL executor —
no adapter shims.  Native programs are fakes speaking the strict
production dialects.  This file proves the milestone's core claim: one
WorkItem in, N structures out, with deterministic identity, lineage, and
grouping intact across a 20 → 40 → 40 → 40 chain.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_standard import PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.persistence.contracts import store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.registry import get_program_adapter
from confflow.remote.transport import RemoteTransport
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
FAKE_ORCA = FAKES_DIR / "fake_orca.py"
FAKE_G16 = FAKES_DIR / "fake_g16.py"
FAKE_GOAT = FAKES_DIR / "fake_goat.py"
FAKE_NEB = FAKES_DIR / "fake_neb.py"
STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

COUNT_WRAPPER = """#!/bin/sh
base=$(basename "$1")
printf '%s\\n' "$base" >> "$COUNT_FILE_TAGGED"
exec python3 "$FAKE_REAL_TAGGED" "$@"
"""


def _install_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: Path, tag: str
) -> tuple[Path, Path]:
    """Install *fake* as a counting executable, returning (wrapper, count)."""
    safe = "".join(char if char.isalnum() else "_" for char in tag).upper()
    count_var = f"COUNT_FILE_{safe}"
    real_var = f"FAKE_REAL_{safe}"
    bin_dir = tmp_path / f"bin-{tag}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / tag
    wrapper.write_text(
        COUNT_WRAPPER.replace("$COUNT_FILE_TAGGED", f"${count_var}").replace(
            "$FAKE_REAL_TAGGED", f"${real_var}"
        )
    )
    wrapper.chmod(0o755)
    count_file = tmp_path / f"{tag}.count"
    count_file.write_text("")
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv(real_var, str(fake))
    monkeypatch.setenv(count_var, str(count_file))
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper, count_file


def _native_count(count_file: Path) -> int:
    """Return the number of logged native invocations."""
    return len([line for line in count_file.read_text().splitlines() if line.strip()])


def _compile(document: dict[str, Any]) -> Any:
    """Compile *document*, asserting a clean compile."""
    compiled = compile_doc(document)
    assert compiled.ok, [(item.code, item.message) for item in compiled.errors]
    assert compiled.plan is not None
    return compiled.plan


def _request(
    plan: Any,
    step_id: str,
    items: tuple[Any, ...],
    run_root: str,
    executable: str,
    *,
    profile_name: str = "standard",
    program: str = "orca",
    checks: tuple[str, ...] = ("normal_termination",),
) -> StepExecutionRequest:
    """Build a durable step request wired to *executable*."""
    planned = next(step for step in plan.steps if step.step_id == step_id)
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=get_program_adapter(program),
        profile=PROFILES[profile_name],
        checks=tuple(CHECKS[name] for name in checks),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=executable, env=FrozenDict({})
        ),
        run_root=run_root,
        environment=None,
        definition_digest=plan.definition_digest,
    )


def _batch() -> BatchStepExecutor:
    """Build a batch executor with a fresh supervisor."""
    return BatchStepExecutor(WorkItemExecutor()).with_supervisor(NativeProcessSupervisor())


def _assemble(plan: Any, step_id: str, structures: StructureSet, **kwargs: Any) -> Any:
    """Assemble items for *step_id*."""
    assembly = assemble(plan, run_inputs(structures={"structures": structures}), **kwargs)
    assert assembly.ok, [item.message for item in assembly.errors]
    return assembly.for_step(step_id)


def _ts_structures(prefix: str, count: int) -> StructureSet:
    """Build *count* TS structures with stable ids and group keys."""
    return StructureSet.of(
        *(structure(f"{prefix}{index:02d}", group_key=f"rxn-{index:02d}") for index in range(count))
    )


def _irc_doc(width: int = 4) -> dict[str, Any]:
    """Build the single-step IRC document."""
    step = calc_step(
        "s_irc",
        program="orca",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP Opt", "irc": {"direction": "both"}},
        profile="path_endpoints",
        checks=["normal_termination"],
        scheduler={"max_parallel_items": width},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc([step], inputs=STRUCTURE_INPUTS)


class TestRealIrcTwentyToForty:
    """20 TS → 20 IRC WorkItems → 40 endpoints through the real adapter."""

    def test_chain_counts_and_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        monkeypatch.setenv("FAKE_IRC_ORDER", "reverse_first")
        wrapper, count_file = _install_fake(tmp_path, monkeypatch, FAKE_IRC, "orca-irc")
        run_root = str(tmp_path / "run")
        plan = _compile(_irc_doc())
        structures = _ts_structures("ts", 20)
        items = _assemble(plan, "s_irc", structures)
        assert len(items) == 20
        assert [item.logical_key for item in items] == [f"s_irc:ts{i:02d}" for i in range(20)]
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            result = _batch().execute_step_resumable(
                _request(
                    plan,
                    "s_irc",
                    tuple(items),
                    run_root,
                    str(wrapper),
                    profile_name="path_endpoints",
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl",
            )
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 20
        assert _native_count(count_file) == 20
        assert len(result.structures) == 40
        by_role = {record.role for record in result.structures}
        assert by_role == {"path_endpoint_forward", "path_endpoint_reverse"}
        # Deterministic ids, lineage, grouping — no list-index access.
        regrouped: dict[tuple[str, str], str] = {}
        for record in result.structures:
            assert record.id == (f"s_irc:ts{record.group_key[-2:]}" f":structure:{record.role}:0")
            assert record.parent_ids == (f"ts{record.group_key[-2:]}",)
            assert record.lineage_root_id == f"ts{record.group_key[-2:]}"
            regrouped[(record.group_key or "", record.role or "")] = record.id
        assert len(regrouped) == 40
        for index in range(20):
            key = f"rxn-{index:02d}"
            assert (key, "path_endpoint_forward") in regrouped
            assert (key, "path_endpoint_reverse") in regrouped

    def test_step_count_invariant(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        _install_fake(tmp_path, monkeypatch, FAKE_IRC, "orca-irc")
        counts = []
        for size in (1, 20, 100):
            plan = _compile(_irc_doc())
            items = _assemble(plan, "s_irc", _ts_structures("q", size))
            counts.append((len(plan.steps), len(items)))
        assert [step_count for step_count, _ in counts] == [1, 1, 1]
        assert [item_count for _, item_count in counts] == [1, 20, 100]

    def test_full_chain_to_sp(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        monkeypatch.setenv("FAKE_IRC_ORDER", "reverse_first")
        irc_wrapper, irc_count = _install_fake(tmp_path, monkeypatch, FAKE_IRC, "orca-irc")
        opt_wrapper, opt_count = _install_fake(tmp_path, monkeypatch, FAKE_ORCA, "orca-opt")
        run_root = str(tmp_path / "run")

        def _chain_doc() -> dict[str, Any]:
            irc = calc_step(
                "s_irc",
                program="orca",
                bindings={"structure": {"source": {"run": "structures"}}},
                native={"keyword": "B3LYP Opt", "irc": {"direction": "both"}},
                profile="path_endpoints",
                checks=["normal_termination"],
                scheduler={"max_parallel_items": 8},
                resources={"cores_per_item": 1, "memory_per_item": "1GB"},
                execution={"binding_id": "test", "executable": "orca"},
            )
            opt = calc_step(
                "s_opt",
                program="orca",
                bindings={"structure": {"source": {"step": "s_irc", "port": "structures"}}},
                native={"keyword": "B3LYP Opt"},
                checks=["normal_termination"],
                scheduler={"max_parallel_items": 8},
                resources={"cores_per_item": 1, "memory_per_item": "1GB"},
                execution={"binding_id": "test", "executable": "orca"},
            )
            sp = calc_step(
                "s_sp",
                program="orca",
                bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                native={"keyword": "B3LYP SP"},
                checks=["normal_termination"],
                scheduler={"max_parallel_items": 8},
                resources={"cores_per_item": 1, "memory_per_item": "1GB"},
                execution={"binding_id": "test", "executable": "orca"},
            )
            return v4_doc([irc, opt, sp], inputs=STRUCTURE_INPUTS)

        from confflow.workflow.v4.assembly import MaterializedOutputs, StepOutputs

        plan = _compile(_chain_doc())
        structures = _ts_structures("ts", 20)
        irc_items = _assemble(plan, "s_irc", structures)
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            irc_result = _batch().execute_step_resumable(
                _request(
                    plan,
                    "s_irc",
                    tuple(irc_items),
                    run_root,
                    str(irc_wrapper),
                    profile_name="path_endpoints",
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl-irc",
            )
        assert irc_result.status is StepStatus.COMPLETED
        assert len(irc_result.structures) == 40

        materialized = MaterializedOutputs(
            steps=FrozenDict(
                {
                    "s_irc": StepOutputs(
                        step_id="s_irc",
                        structures=irc_result.structures,
                        artifacts=irc_result.artifacts,
                    )
                }
            )
        )
        opt_items = _assemble(plan, "s_opt", structures, materialized=materialized)
        assert len(opt_items) == 40
        with SqliteWorkItemStore.open(store_path(run_root, "s_opt")) as store:
            opt_result = _batch().execute_step_resumable(
                _request(plan, "s_opt", tuple(opt_items), run_root, str(opt_wrapper)),
                store=store,
                run_root=run_root,
                owner_token="ctl-opt",
            )
        assert opt_result.status is StepStatus.COMPLETED
        assert len(opt_result.structures) == 40

        materialized_opt = MaterializedOutputs(
            steps=FrozenDict(
                {
                    "s_opt": StepOutputs(
                        step_id="s_opt",
                        structures=opt_result.structures,
                        artifacts=opt_result.artifacts,
                    )
                }
            )
        )
        sp_items = _assemble(plan, "s_sp", structures, materialized=materialized_opt)
        assert len(sp_items) == 40
        with SqliteWorkItemStore.open(store_path(run_root, "s_sp")) as store:
            sp_result = _batch().execute_step_resumable(
                _request(plan, "s_sp", tuple(sp_items), run_root, str(opt_wrapper)),
                store=store,
                run_root=run_root,
                owner_token="ctl-sp",
            )
        assert sp_result.status is StepStatus.COMPLETED
        assert sp_result.summary["completed"] == 40
        # Regroup per reaction without list indices: every group keeps
        # exactly two optimized descendants, one per endpoint parent.
        endpoint_ids = {record.id for record in irc_result.structures}
        answers: dict[str, list[str]] = {}
        for record in opt_result.structures:
            assert len(record.parent_ids) == 1
            assert record.parent_ids[0] in endpoint_ids
            answers.setdefault(record.group_key or "", []).append(record.parent_ids[0])
        assert len(answers) == 20
        for index in range(20):
            key = f"rxn-{index:02d}"
            assert len(answers[key]) == 2
            roles = {
                next(s.role for s in irc_result.structures if s.id == parent)
                for parent in answers[key]
            }
            assert roles == {"path_endpoint_forward", "path_endpoint_reverse"}
        assert _native_count(irc_count) == 20
        assert _native_count(opt_count) == 80


class TestRealQst2:
    """QST2 through the real Gaussian adapter + named execution path."""

    def _qst_doc(self) -> dict[str, Any]:
        """Build the reactant/product → QST2 document."""
        step = calc_step(
            "s_qst",
            program="gaussian",
            bindings={
                "reactant": {
                    "source": {"run": "reactants"},
                    "pairing": "by_group_key",
                    "cardinality": "one",
                },
                "product": {
                    "source": {"run": "products"},
                    "pairing": "by_group_key",
                    "cardinality": "one",
                },
            },
            native={"keyword": "QST2 B3LYP/6-31G", "atom_mapping": {"kind": "identity"}},
            checks=["normal_termination", "imaginary_frequency_count"],
            scheduler={"max_parallel_items": 4},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "g16"},
            adapter="named_structures",
        )
        step["calculation"]["check_params"] = {"imaginary_frequency_count": {"expected": 1}}
        return v4_doc(
            [step],
            inputs={
                "reactants": {"kind": "structure", "cardinality": "many"},
                "products": {"kind": "structure", "cardinality": "many"},
            },
        )

    def test_qst2_ts_candidates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "ts_candidate")
        wrapper, count_file = _install_fake(tmp_path, monkeypatch, FAKE_G16, "g16")
        run_root = str(tmp_path / "run")
        plan = _compile(self._qst_doc())
        reactants = StructureSet.of(
            *(structure(f"r{i:02d}", group_key=f"rxn-{i:02d}") for i in range(3))
        )
        products = StructureSet.of(
            *(structure(f"p{i:02d}", group_key=f"rxn-{i:02d}", offset=0.05) for i in range(3))
        )
        assembly = assemble(
            plan,
            run_inputs(structures={"reactants": reactants, "products": products}),
        )
        assert assembly.ok, [item.message for item in assembly.errors]
        items = assembly.for_step("s_qst")
        assert len(items) == 3
        assert [item.logical_key for item in items] == [f"s_qst:rxn-{i:02d}" for i in range(3)]
        with SqliteWorkItemStore.open(store_path(run_root, "s_qst")) as store:
            result = _batch().execute_step_resumable(
                _request(
                    plan,
                    "s_qst",
                    tuple(items),
                    run_root,
                    str(wrapper),
                    program="gaussian",
                    checks=("normal_termination", "imaginary_frequency_count"),
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl",
            )
        assert result.status is StepStatus.COMPLETED
        assert result.summary["completed"] == 3
        assert _native_count(count_file) == 3
        for record in result.structures:
            assert set(record.parent_ids) >= {
                f"r{record.group_key[-2:]}",
                f"p{record.group_key[-2:]}",
            }
            assert record.group_key is not None and record.group_key.startswith("rxn-")


class TestRealGoat:
    """GOAT ensemble through the real ORCA adapter."""

    def test_three_conformers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        wrapper, count_file = _install_fake(tmp_path, monkeypatch, FAKE_GOAT, "orca-goat")
        run_root = str(tmp_path / "run")
        step = calc_step(
            "s_goat",
            program="orca",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "B3LYP D3BJ Opt", "goat": {"MaxIter": 50}},
            profile="ensemble",
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 2},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        plan = _compile(v4_doc([step], inputs=STRUCTURE_INPUTS))
        structures = StructureSet.of(
            structure("c0", group_key="conf"), structure("c1", group_key="conf")
        )
        items = _assemble(plan, "s_goat", structures)
        assert len(items) == 2
        with SqliteWorkItemStore.open(store_path(run_root, "s_goat")) as store:
            result = _batch().execute_step_resumable(
                _request(
                    plan, "s_goat", tuple(items), run_root, str(wrapper), profile_name="ensemble"
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl",
            )
        assert result.status is StepStatus.COMPLETED
        assert len(result.structures) == 6
        roles = {record.role for record in result.structures}
        assert roles == {"conformer"}
        ids = sorted(record.id for record in result.structures)
        assert len(set(ids)) == 6
        by_subject = {
            record.subject_structure_id: record.value
            for record in result.results
            if record.kind == "energy"
        }
        assert len(by_subject) == 6
        for record in result.structures:
            assert record.id in by_subject
        assert _native_count(count_file) == 2


class TestRealNeb:
    """NEB path images through the real ORCA adapter."""

    def test_five_images_plus_ts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        monkeypatch.setenv("FAKE_NEB_TS", "1")
        wrapper, count_file = _install_fake(tmp_path, monkeypatch, FAKE_NEB, "orca-neb")
        run_root = str(tmp_path / "run")
        step = calc_step(
            "s_neb",
            program="orca",
            bindings={
                "reactant": {
                    "source": {"run": "reactants"},
                    "pairing": "by_group_key",
                    "cardinality": "one",
                },
                "product": {
                    "source": {"run": "products"},
                    "pairing": "by_group_key",
                    "cardinality": "one",
                },
            },
            native={
                "keyword": "B3LYP D3BJ Opt",
                "neb": {"n_images": 5, "neb_ts": True},
                "atom_mapping": {"kind": "identity"},
            },
            profile="ensemble",
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 2},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
            adapter="named_structures",
        )
        plan = _compile(
            v4_doc(
                [step],
                inputs={
                    "reactants": {"kind": "structure", "cardinality": "many"},
                    "products": {"kind": "structure", "cardinality": "many"},
                },
            )
        )
        reactants = StructureSet.of(structure("r0", group_key="rxn-00"))
        products = StructureSet.of(structure("p0", group_key="rxn-00", offset=0.05))
        assembly = assemble(
            plan, run_inputs(structures={"reactants": reactants, "products": products})
        )
        assert assembly.ok, [item.message for item in assembly.errors]
        items = assembly.for_step("s_neb")
        assert len(items) == 1
        with SqliteWorkItemStore.open(store_path(run_root, "s_neb")) as store:
            result = _batch().execute_step_resumable(
                _request(
                    plan, "s_neb", tuple(items), run_root, str(wrapper), profile_name="ensemble"
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl",
            )
        assert result.status is StepStatus.COMPLETED
        roles = sorted(record.role for record in result.structures)
        assert roles == ["neb_image"] * 5 + ["neb_ts_candidate"]
        assert _native_count(count_file) == 1


class TestRealIrcRemoteParity:
    """Local vs remote IRC through the real adapter and profile."""

    def test_local_remote_identical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        # The worker resolves the executable by program name on PATH
        # (handoffs never carry producer absolute paths), so the fake
        # must be visible as `orca`.
        wrapper, _count = _install_fake(tmp_path, monkeypatch, FAKE_IRC, "orca")
        run_root = str(tmp_path / "run")
        worker_root = str(tmp_path / "worker")
        plan = _compile(_irc_doc())
        structures = _ts_structures("ts", 2)
        items = tuple(_assemble(plan, "s_irc", structures))
        planned = plan.steps[0]

        from confflow.execution.work_item_executor import ItemExecutionContext

        context = ItemExecutionContext(
            step_id="s_irc",
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=get_program_adapter("orca"),
            profile=PROFILES["path_endpoints"],
            checks=(CHECKS["normal_termination"],),
            recovery=RECOVERIES["none"],
            execution_binding=ExecutionBinding(
                binding_id="test", executable=str(wrapper), env=FrozenDict({})
            ),
            run_root=run_root,
            work_base=os.path.join(run_root, "work"),
            supervisor=NativeProcessSupervisor(),
            environment=None,
            poll_interval_seconds=0.05,
        )
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            from confflow.persistence import OwnerIdentity

            for item in items:
                store.register_item(
                    work_item_id=item.id,
                    logical_key=item.logical_key,
                    step_id=item.step_id,
                    work_item_digest=item.semantic_digest,
                    step_semantic_digest=planned.step_semantic_digest,
                )
                assert store.claim(item.id, owner=OwnerIdentity(owner_token="ctl"))
            transport = RemoteTransport(run_root=run_root, store=store, worker_root=worker_root)
            remote_results = [transport.execute(item, context, attempt=1) for item in items]
        assert all(r.status is WorkItemStatus.COMPLETED for r in remote_results)
        for item, remote in zip(items, remote_results):
            assert remote.work_item_id == item.id
            assert len(remote.structures) == 2
            assert {s.role for s in remote.structures} == {
                "path_endpoint_forward",
                "path_endpoint_reverse",
            }
            assert remote.semantic_digest == item.semantic_digest


class TestRealIrcIncomplete:
    """Missing reverse direction fails closed with one native execution."""

    def test_missing_reverse_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FAKE_MODE", "success_opt")
        monkeypatch.setenv("FAKE_IRC_MODE", "missing_reverse")
        wrapper, count_file = _install_fake(tmp_path, monkeypatch, FAKE_IRC, "orca-irc")
        run_root = str(tmp_path / "run")
        plan = _compile(_irc_doc())
        items = tuple(_assemble(plan, "s_irc", _ts_structures("ts", 2)))
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            result = _batch().execute_step_resumable(
                _request(
                    plan, "s_irc", items, run_root, str(wrapper), profile_name="path_endpoints"
                ),
                store=store,
                run_root=run_root,
                owner_token="ctl",
            )
        assert result.status is StepStatus.FAILED
        assert result.summary["failed"] == 2
        # The strict dialect rejects the missing banner at parse time:
        # one native execution per item, no faked endpoint, no structures.
        assert _native_count(count_file) == 2
        for item_result in result.item_results:
            assert item_result.error is not None
            assert item_result.error.code == "native_parse_error"
            assert len(item_result.structures) == 0

    def test_partial_profile_output_fails_incomplete_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A parser yielding one endpoint fails the item as incomplete_path."""
        from confflow.execution.native import (
            GeometryOutput,
            NativeResult,
            ProgramName,
        )

        monkeypatch.setenv("FAKE_MODE", "success_opt")
        wrapper, count_file = _install_fake(tmp_path, monkeypatch, FAKE_IRC, "orca-irc")
        run_root = str(tmp_path / "run")
        plan = _compile(_irc_doc())
        (item,) = tuple(_assemble(plan, "s_irc", _ts_structures("ts", 1)))

        real_adapter = get_program_adapter("orca")

        class _PartialAdapter:
            """Real adapter with a one-endpoint native result."""

            def __init__(self) -> None:
                self._real = real_adapter

            def __getattr__(self, name: str) -> Any:
                return getattr(self._real, name)

            def parse_native_result(
                self, *, work_dir: str, log_file_name: str, materialized: Any
            ) -> NativeResult:
                native = self._real.parse_native_result(
                    work_dir=work_dir,
                    log_file_name=log_file_name,
                    materialized=materialized,
                )
                (endpoint,) = native.path_endpoints[:1]
                return NativeResult(
                    program=ProgramName.ORCA,
                    terminated_normally=True,
                    geometry_output=GeometryOutput.NONE,
                    energies_hartree=native.energies_hartree,
                    native_metadata=native.native_metadata,
                    produced_files=native.produced_files,
                    log_file_name=log_file_name,
                    path_endpoints=(endpoint,),
                )

        planned = plan.steps[0]
        request = StepExecutionRequest(
            step=planned,
            items=(item,),
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=_PartialAdapter(),  # type: ignore[arg-type]
            profile=PROFILES["path_endpoints"],
            checks=(CHECKS["normal_termination"],),
            recovery=RECOVERIES["none"],
            execution_binding=ExecutionBinding(
                binding_id="test", executable=str(wrapper), env=FrozenDict({})
            ),
            run_root=run_root,
            environment=None,
            definition_digest=plan.definition_digest,
        )
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            result = _batch().execute_step_resumable(
                request, store=store, run_root=run_root, owner_token="ctl"
            )
        assert result.summary["failed"] == 1
        assert _native_count(count_file) == 1
        (item_result,) = result.item_results
        assert item_result.error is not None
        assert item_result.error.code == "incomplete_path"
        assert len(item_result.structures) == 0
