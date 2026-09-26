#!/usr/bin/env python3

"""V4-5 TS→path-endpoint→SP mini vertical slice (20 TS → 40 endpoints).

Count chain under test, all through real production seams except where noted:

- 20 TS structures (``ts00..ts19``, ``group_key`` ``rxn-00..19``) assemble
  into exactly 20 IRC work items (real ``compile``/``assemble``);
- the IRC step executes locally through :class:`WorkItemExecutor` with the
  real :class:`PathEndpointsProfile` and the real minimal IRC dialect parsed
  by :mod:`confflow.programs.orca.path` (banners
  ``CONFFLOW IRC FORWARD/REVERSE ENDPOINT``);
- each item yields forward+reverse endpoints → exactly 40 structures with
  frozen ids/lineage from :mod:`confflow.execution.output_identity`;
- the endpoint-optimization step assembles exactly 40 work items and produces
  40 optimized structures (real executor, fake ORCA);
- the SP step assembles exactly 40 work items and produces 40 results.

Seam note: the production ORCA program adapter does not yet route IRC output
into ``parse_path_endpoints`` (blocked seam:
``OrcaAdapter.parse_native_result`` must call
:func:`confflow.programs.orca.path.parse_path_endpoints` when the native
request is an IRC job; the remote worker then inherits the wiring).  Until
that lands, these tests drive the executor with the test-local
the real ORCA adapter (retired test shim: fakes now speak real grammar)
adapter except ``parse_native_result`` (real dialect) and the contract
versions.  Parsing, profiling, identity, assembly, completion, and durable
resume are all production code.

The fake executable is ``tests/v4/fakes/fake_irc.py``.  Banner order is
``reverse_first`` for the whole chain run, so every id/role assertion below
also proves direction comes from markers, never from order.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import CompletionMode, CompletionPolicy, StepStatus
from confflow.execution import ExecutionBinding
from confflow.execution.batch import BatchStepExecutor, StepExecutionRequest
from confflow.execution.checks_standard import CHECKS
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.profile_path_endpoints import PathEndpointsProfile
from confflow.execution.profile_standard import PROFILES as STANDARD_PROFILES
from confflow.execution.recovery_standard import RECOVERIES
from confflow.execution.work_item_executor import WorkItemExecutor
from confflow.persistence.contracts import StoredWorkItemStatus, store_path
from confflow.persistence.work_items import SqliteWorkItemStore
from confflow.programs.orca.rendering import sanitize_job_name as orca_job_name
from confflow.programs.registry import get_program_adapter
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
    FORWARD_POINT,
    REVERSE_ENERGY,
    REVERSE_POINT,
)

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_IRC = FAKES_DIR / "fake_irc.py"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"
STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}

FORWARD_ROLE = "path_endpoint_forward"
REVERSE_ROLE = "path_endpoint_reverse"

IRC_WRAPPER_SCRIPT = """#!/bin/sh
# Counting wrapper: logs every native invocation basename, then execs fake_irc.
base=$(basename "$1")
printf '%s\\n' "$base" >> "$IRC_COUNT_FILE"
exec python3 "$IRC_FAKE_REAL" "$@"
"""

ORCA_WRAPPER_SCRIPT = """#!/bin/sh
# Counting wrapper for the endpoint-opt/SP steps (fake_orca behind it).
base=$(basename "$1")
printf '%s\\n' "$base" >> "$ORCA_COUNT_FILE"
exec python3 "$ORCA_FAKE_REAL" "$@"
"""


def _install_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    script: str,
    fake: Path,
    count_name: str,
    prefix: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Install a counting wrapper; return ``(wrapper, count_file)``."""
    bin_dir = tmp_path / f"bin-{name}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / name
    wrapper.write_text(script)
    wrapper.chmod(0o755)
    count_file = tmp_path / count_name
    count_file.write_text("")
    monkeypatch.setenv(f"{prefix}_FAKE_REAL", str(fake))
    monkeypatch.setenv(f"{prefix}_COUNT_FILE", str(count_file))
    for key, value in (extra_env or {}).items():
        monkeypatch.setenv(key, value)
    assert os.access(wrapper, os.X_OK)
    assert not bool(wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))
    return wrapper, count_file


def _install_irc_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Install IRC + ORCA wrappers; return ``(irc, irc_count, orca_count)``."""
    irc_fail = tmp_path / "irc.fail"
    irc_fail.write_text("")
    irc, irc_count = _install_wrapper(
        tmp_path,
        monkeypatch,
        name="ircfake",
        script=IRC_WRAPPER_SCRIPT,
        fake=FAKE_IRC,
        count_name="irc.count",
        prefix="IRC",
        extra_env={
            "FAKE_IRC_MODE": "success",
            "FAKE_IRC_ORDER": "reverse_first",
            "IRC_FAIL_FILE": str(irc_fail),
        },
    )
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    orca, orca_count = _install_wrapper(
        tmp_path,
        monkeypatch,
        name="orcafake",
        script=ORCA_WRAPPER_SCRIPT,
        fake=FAKE_ORCA,
        count_name="orca.count",
        prefix="ORCA",
    )
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    return irc, irc_count, orca_count


def _native_count(count_file: Path) -> int:
    """Return the number of logged native invocations."""
    return len([line for line in count_file.read_text().splitlines() if line.strip()])


def _ts_structures(count: int, *, prefix: str = "ts", groups: str = "rxn") -> StructureSet:
    """Build *count* TS structures with stable ids and group keys."""
    width = len(str(count - 1)) if count > 1 else 2
    width = max(width, 2)
    return StructureSet.of(
        *(
            structure(
                f"{prefix}{index:0{width}d}",
                group_key=f"{groups}-{index:0{width}d}",
                lineage_root_id=f"root-{index:0{width}d}",
                offset=float(index) * 0.013,
            )
            for index in range(count)
        )
    )


def _chain_doc(
    *,
    irc_completion: dict[str, Any] | None = None,
    width: int = 8,
) -> dict[str, Any]:
    """Build the IRC → endpoint-opt → SP chain document."""
    irc = calc_step(
        "s_irc",
        program="orca",
        adapter="standard",
        profile="path_endpoints",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "IRC B3LYP D3BJ", "irc": {"direction": "both"}},
        checks=["normal_termination", "geometry_required"],
        scheduler={"max_parallel_items": width},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    if irc_completion is not None:
        irc["completion"] = dict(irc_completion)
    eopt = calc_step(
        "s_eopt",
        program="orca",
        adapter="standard",
        profile="standard",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP Opt"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": width},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    sp = calc_step(
        "s_sp",
        program="orca",
        adapter="standard",
        profile="standard",
        bindings={"structure": {"source": {"run": "structures"}}},
        native={"keyword": "B3LYP SP"},
        checks=["normal_termination"],
        scheduler={"max_parallel_items": width},
        resources={"cores_per_item": 1, "memory_per_item": "1GB"},
        execution={"binding_id": "test", "executable": "orca"},
    )
    return v4_doc([irc, eopt, sp], inputs=STRUCTURE_INPUTS)


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
    plan: Any, items: tuple[Any, ...], run_root: str, executable: Path
) -> StepExecutionRequest:
    """Build a durable IRC step request wired to the test adapter."""
    planned = next(step for step in plan.steps if step.step_id == "s_irc")
    irc_adapter = get_program_adapter("orca")  # real adapter: fakes speak real grammar
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=irc_adapter,
        profile=PathEndpointsProfile(),
        checks=(CHECKS["normal_termination"], CHECKS["geometry_required"]),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(executable), env=FrozenDict({})
        ),
        run_root=run_root,
        environment=_measured_env(irc_adapter, executable),
        definition_digest=plan.definition_digest,
        executor_capability=getattr(planned.executor, "value", str(planned.executor)),
    )


def _std_request(
    plan: Any,
    step_id: str,
    items: tuple[Any, ...],
    run_root: str,
    executable: Path,
    work_base: str,
) -> StepExecutionRequest:
    """Build an in-memory standard-profile request for opt/SP steps."""
    planned = next(step for step in plan.steps if step.step_id == step_id)
    std_adapter = get_program_adapter("orca")
    return StepExecutionRequest(
        step=planned,
        items=tuple(items),
        scientific=planned.scientific,
        scientific_defaults=plan.scientific_defaults,
        adapter=std_adapter,
        profile=STANDARD_PROFILES["standard"],
        checks=(CHECKS["normal_termination"],),
        recovery=RECOVERIES["none"],
        execution_binding=ExecutionBinding(
            binding_id="test", executable=str(executable), env=FrozenDict({})
        ),
        run_root=run_root,
        work_base=work_base,
        environment=_measured_env(std_adapter, executable),
        definition_digest=plan.definition_digest,
        executor_capability=getattr(planned.executor, "value", str(planned.executor)),
    )


def _measured_env(adapter: Any, executable: Any) -> Any:
    """Measure the fake executable for the durable env axis."""
    from confflow.execution.environment import EnvironmentMeasurer

    return EnvironmentMeasurer().build_environment(str(executable), adapter=adapter)


def _regroup_by_group_role(structures: Any) -> dict[tuple[str, str], Any]:
    """Index structures by ``(group_key, role)`` without list-index access."""
    table: dict[tuple[str, str], Any] = {}
    for record in structures:
        assert record.group_key is not None, record.id
        assert record.role is not None, record.id
        key = (record.group_key, record.role)
        assert key not in table, f"duplicate endpoint slot {key}"
        table[key] = record
    return table


class TestIrcAssembly:
    """20 TS structures assemble into exactly 20 IRC work items."""

    def test_20_ts_assemble_20_items(self) -> None:
        plan = _compile(_chain_doc())
        structures = _ts_structures(20)
        assembly = assemble(plan, run_inputs(structures={"structures": structures}))
        assert assembly.ok, [item.message for item in assembly.errors]
        items = assembly.for_step("s_irc")
        assert len(items) == 20
        assert {item.logical_key for item in items} == {f"s_irc:ts{i:02d}" for i in range(20)}
        driving = {item.named_inputs.structures["structure"][0].id for item in items}
        assert driving == {f"ts{i:02d}" for i in range(20)}
        assert {item.named_inputs.structures["structure"][0].group_key for item in items} == {
            f"rxn-{i:02d}" for i in range(20)
        }

    def test_step_count_invariance_1_20_100(self) -> None:
        """Input count moves items/structures, never the plan step count."""
        plans = [_compile(_chain_doc()) for _ in range(3)]
        assert [plan.definition_digest for plan in plans][0] == [
            plan.definition_digest for plan in plans
        ][1]
        for plan, count in zip(plans, (1, 20, 100)):
            assert len(plan.steps) == 3
            assert [step.step_id for step in plan.steps] == ["s_eopt", "s_irc", "s_sp"]
            assembly = assemble(plan, run_inputs(structures={"structures": _ts_structures(count)}))
            assert assembly.ok
            assert len(assembly.for_step("s_irc")) == count
            assert len(assembly.for_step("s_eopt")) == count
            assert len(assembly.for_step("s_sp")) == count


class TestIrcExecutionChain:
    """The full 20 → 20 → 40 → 40 → 40 count chain, executed locally."""

    def test_full_count_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        irc, irc_count, orca_count = _install_irc_stack(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_chain_doc())
        structures = _ts_structures(20)
        items = assemble(plan, run_inputs(structures={"structures": structures})).for_step("s_irc")
        assert len(items) == 20
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            irc_result = _batch().execute_step_resumable(
                _irc_request(plan, tuple(items), run_root, irc),
                store=store,
                run_root=run_root,
                owner_token="ctl-irc",
            )
        assert irc_result.summary["completed"] == 20
        assert irc_result.status is StepStatus.COMPLETED
        assert _native_count(irc_count) == 20

        by_item = {result.work_item_id: result for result in irc_result.item_results}
        assert len(by_item) == 20
        for item in items:
            result = by_item[item.id]
            assert result.is_completed
            roles = {record.role for record in result.structures}
            assert roles == {FORWARD_ROLE, REVERSE_ROLE}
            assert len(tuple(result.structures)) == 2
            driving = item.named_inputs.structures["structure"][0]
            for record in result.structures:
                assert record.parent_ids == (driving.id,)
                assert record.lineage_root_id == driving.lineage_root_id
                assert record.group_key == driving.group_key
                expected = f"{item.logical_key}:structure:{record.role}:0"
                assert record.id == expected
            points = {record.metadata.get("point_ordinal") for record in result.structures}
            assert points == {FORWARD_POINT, REVERSE_POINT}
            forward = next(record for record in result.structures if record.role == FORWARD_ROLE)
            reverse = next(record for record in result.structures if record.role == REVERSE_ROLE)
            assert forward.metadata.get("point_ordinal") == FORWARD_POINT
            assert reverse.metadata.get("point_ordinal") == REVERSE_POINT
            energies = {record.subject_structure_id: record.value for record in result.results}
            assert energies[forward.id] == pytest.approx(FORWARD_ENERGY, abs=1e-9)
            assert energies[reverse.id] == pytest.approx(REVERSE_ENERGY, abs=1e-9)

        assert len(tuple(irc_result.structures)) == 40
        endpoints = StructureSet.of(*tuple(irc_result.structures))

        eopt_items = assemble(plan, run_inputs(structures={"structures": endpoints})).for_step(
            "s_eopt"
        )
        assert len(eopt_items) == 40
        assert {item.named_inputs.structures["structure"][0].id for item in eopt_items} == {
            record.id for record in endpoints
        }
        eopt_result = _batch().execute_step(
            _std_request(
                plan,
                "s_eopt",
                tuple(eopt_items),
                str(tmp_path / "run"),
                tmp_path / "bin-orcafake" / "orcafake",
                str(tmp_path / "run" / "work-eopt"),
            )
        )
        assert eopt_result.summary["completed"] == 40
        assert len(tuple(eopt_result.structures)) == 40

        optimized = StructureSet.of(*tuple(eopt_result.structures))
        sp_items = assemble(plan, run_inputs(structures={"structures": optimized})).for_step("s_sp")
        assert len(sp_items) == 40
        sp_result = _batch().execute_step(
            _std_request(
                plan,
                "s_sp",
                tuple(sp_items),
                str(tmp_path / "run"),
                tmp_path / "bin-orcafake" / "orcafake",
                str(tmp_path / "run" / "work-sp"),
            )
        )
        assert sp_result.summary["completed"] == 40
        assert len(tuple(sp_result.results)) >= 40

        table = _regroup_by_group_role(endpoints)
        assert len(table) == 40
        for index in range(20):
            group = f"rxn-{index:02d}"
            forward = table[(group, FORWARD_ROLE)]
            reverse = table[(group, REVERSE_ROLE)]
            assert forward.group_key == reverse.group_key == group
            assert forward.id.endswith(f":structure:{FORWARD_ROLE}:0")
            assert reverse.id.endswith(f":structure:{REVERSE_ROLE}:0")
            assert forward.parent_ids == (f"ts{index:02d}",)
            assert reverse.parent_ids == (f"ts{index:02d}",)

    def test_banner_order_never_moves_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forward-first vs reverse-first banners give identical endpoint ids."""
        irc, irc_count, _ = _install_irc_stack(tmp_path, monkeypatch)
        plan = _compile(_chain_doc())
        (item,) = assemble(plan, run_inputs(structures={"structures": _ts_structures(1)})).for_step(
            "s_irc"
        )

        def _run(order: str, tag: str) -> Any:
            monkeypatch.setenv("FAKE_IRC_ORDER", order)
            with SqliteWorkItemStore.open(store_path(str(tmp_path / tag), "s_irc")) as store:
                return _batch().execute_step_resumable(
                    _irc_request(plan, (item,), str(tmp_path / tag), irc),
                    store=store,
                    run_root=str(tmp_path / tag),
                    owner_token=f"ctl-{tag}",
                )

        first = _run("forward_first", "run-a")
        second = _run("reverse_first", "run-b")
        assert first.summary["completed"] == second.summary["completed"] == 1
        assert [record.id for record in first.structures] == [
            record.id for record in second.structures
        ]
        assert [record.geometry_digest for record in first.structures] == [
            record.geometry_digest for record in second.structures
        ]
        assert _native_count(irc_count) == 2

    def test_19_20_failure_retry_reruns_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One item missing reverse → FAILED under require_all; retry runs 1."""
        irc, irc_count, _ = _install_irc_stack(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(_chain_doc())
        items = assemble(plan, run_inputs(structures={"structures": _ts_structures(20)})).for_step(
            "s_irc"
        )
        victim = next(item for item in items if item.logical_key == "s_irc:ts07")
        victim_base = orca_job_name(victim.logical_key, fallback=victim.id) + ".inp"
        (tmp_path / "irc.fail").write_text(victim_base + "\n")

        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            failed = _batch().execute_step_resumable(
                _irc_request(plan, tuple(items), run_root, irc),
                store=store,
                run_root=run_root,
                owner_token="ctl-1",
            )
        assert failed.summary["completed"] == 19
        assert failed.summary["failed"] == 1
        assert failed.status is StepStatus.FAILED
        assert _native_count(irc_count) == 20
        failed_ids = {
            result.work_item_id for result in failed.item_results if not result.is_completed
        }
        assert failed_ids == {victim.id}
        victim_result = next(
            result for result in failed.item_results if result.work_item_id == victim.id
        )
        assert victim_result.error is not None
        # The victim fake omits its reverse section: the profile fails
        # closed on the incomplete pair (never a parse error — the log
        # that exists is grammatical).
        assert victim_result.error.code == "incomplete_path"

        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            assert len(store.list_items(StoredWorkItemStatus.COMPLETED)) == 19
            assert len(store.list_items(StoredWorkItemStatus.FAILED)) == 1
            durable_endpoints = 0
            for item in items:
                if item.id == victim.id:
                    continue
                stored = store.get_result(item.id)
                assert stored is not None and stored.is_completed
                assert len(tuple(stored.structures)) == 2
                durable_endpoints += 2
            assert durable_endpoints == 38

            (tmp_path / "irc.fail").write_text("")
            retried = _batch().execute_step_resumable(
                _irc_request(plan, tuple(items), run_root, irc),
                store=store,
                run_root=run_root,
                owner_token="ctl-2",
            )
        assert retried.summary["completed"] == 20
        assert retried.status is StepStatus.COMPLETED
        reused = sum(
            1
            for result in retried.item_results
            if any(d.code == "reuse_hit" for d in result.diagnostics)
        )
        assert reused == 19
        assert _native_count(irc_count) == 21
        assert len(tuple(retried.structures)) == 40

    def test_allow_partial_publishes_19_group_subset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Allow-partial publishes the 19 good groups; the failed one is absent."""
        irc, _, _ = _install_irc_stack(tmp_path, monkeypatch)
        run_root = str(tmp_path / "run")
        plan = _compile(
            _chain_doc(
                irc_completion={
                    "mode": "allow_partial",
                    "minimum_success": 1,
                    "partial_output": "allow",
                }
            )
        )
        items = assemble(plan, run_inputs(structures={"structures": _ts_structures(20)})).for_step(
            "s_irc"
        )
        victim = next(item for item in items if item.logical_key == "s_irc:ts07")
        (tmp_path / "irc.fail").write_text(
            orca_job_name(victim.logical_key, fallback=victim.id) + ".inp\n"
        )
        with SqliteWorkItemStore.open(store_path(run_root, "s_irc")) as store:
            partial = _batch().execute_step_resumable(
                _irc_request(plan, tuple(items), run_root, irc),
                store=store,
                run_root=run_root,
                owner_token="ctl-partial",
            )
        assert partial.status is StepStatus.PARTIAL
        assert partial.summary["completed"] == 19
        assert partial.summary["failed"] == 1
        assert len(tuple(partial.structures)) == 38
        table = _regroup_by_group_role(partial.structures)
        assert len(table) == 38
        assert ("rxn-07", FORWARD_ROLE) not in table
        assert ("rxn-07", REVERSE_ROLE) not in table
        for index in list(range(7)) + list(range(8, 20)):
            group = f"rxn-{index:02d}"
            assert table[(group, FORWARD_ROLE)].parent_ids == (f"ts{index:02d}",)
            assert table[(group, REVERSE_ROLE)].parent_ids == (f"ts{index:02d}",)
        assert CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL).accepts_partial


class TestFormalChainAnalysisPes:
    """Full formal chain with every value from a real preceding step.

    ``TS -> Freq -> high-level SP -> IRC -> endpoint Opt -> endpoint
    Freq -> endpoint high-level SP -> Analysis -> PES``: no hand-forged
    endpoint Gibbs values or subjects.  Electronic ``E_high`` legs are
    pinned to the real single-point step results, Gibbs-correction legs
    to the real frequency step results (explicit ``ResultRef``
    traceability via ``result_id``/``source_step_id``); optimized /
    frequency / SP descendants resolve to their reaction node through
    parent-id lineage.  The PES derives strictly from emitted
    ``reaction_profile`` analysis results.
    """

    def _chain_doc(self) -> dict[str, Any]:
        ts_freq = calc_step(
            "s_ts_freq",
            program="orca",
            adapter="standard",
            profile="standard",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "B3LYP Freq"},
            checks=["normal_termination", "frequencies_required"],
            scheduler={"max_parallel_items": 2},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        ts_sp = calc_step(
            "s_ts_sp",
            program="orca",
            adapter="standard",
            profile="standard",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "B3LYP SP"},
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 2},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        irc = calc_step(
            "s_irc",
            program="orca",
            adapter="standard",
            profile="path_endpoints",
            bindings={"structure": {"source": {"run": "structures"}}},
            native={"keyword": "IRC B3LYP D3BJ", "irc": {"direction": "both"}},
            checks=["normal_termination", "geometry_required"],
            scheduler={"max_parallel_items": 2},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        eopt = calc_step(
            "s_eopt",
            program="orca",
            adapter="standard",
            profile="standard",
            bindings={"structure": {"source": {"step": "s_irc", "port": "structures"}}},
            native={"keyword": "B3LYP Opt"},
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 4},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        efreq = calc_step(
            "s_efreq",
            program="orca",
            adapter="standard",
            profile="standard",
            bindings={"structure": {"source": {"step": "s_eopt", "port": "structures"}}},
            native={"keyword": "B3LYP Freq"},
            checks=["normal_termination", "frequencies_required"],
            scheduler={"max_parallel_items": 4},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        esp = calc_step(
            "s_esp",
            program="orca",
            adapter="standard",
            profile="standard",
            bindings={"structure": {"source": {"step": "s_efreq", "port": "structures"}}},
            native={"keyword": "B3LYP SP"},
            checks=["normal_termination"],
            scheduler={"max_parallel_items": 4},
            resources={"cores_per_item": 1, "memory_per_item": "1GB"},
            execution={"binding_id": "test", "executable": "orca"},
        )
        return v4_doc([ts_freq, ts_sp, irc, eopt, efreq, esp], inputs=STRUCTURE_INPUTS)

    def _run_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        irc_exe, _, _ = _install_irc_stack(tmp_path, monkeypatch)
        orca_exe = tmp_path / "bin-orcafake" / "orcafake"
        run_root = str(tmp_path / "run")
        plan = _compile(self._chain_doc())
        structures = _ts_structures(1)
        (ts_record,) = tuple(structures)
        group_key = ts_record.group_key
        assert group_key is not None

        from confflow.workflow.v4.assembly import MaterializedOutputs, StepOutputs

        materialized = MaterializedOutputs(steps=FrozenDict({}))
        collected_structures: list[Any] = [ts_record]
        collected_results: list[Any] = []

        def _exec_calc(
            step_id: str,
            fake_mode: str | None,
            executable: Path,
            profile_name: str = "standard",
            tag: str = "",
        ) -> Any:
            if fake_mode is not None:
                monkeypatch.setenv("FAKE_MODE", fake_mode)
            assembly = assemble(
                plan,
                run_inputs(structures={"structures": _ts_structures(1)}),
                materialized=materialized,
            )
            assert assembly.ok, [item.message for item in assembly.errors]
            items = assembly.for_step(step_id)
            assert len(items) >= 1, (step_id, tag)
            request = _std_request(
                plan,
                step_id,
                tuple(items),
                run_root,
                executable,
                f"{run_root}/work-{step_id}",
            )
            if profile_name == "path_endpoints":
                request = _irc_request(plan, tuple(items), run_root, executable)
            result = _batch().execute_step(request)
            assert result.summary.get("completed", 0) >= 1, (step_id, result.summary)
            return result

        ts_freq_result = _exec_calc("s_ts_freq", "success_freq", orca_exe)
        materialized = MaterializedOutputs(
            steps=FrozenDict(
                {
                    **dict(materialized.steps),
                    "s_ts_freq": StepOutputs(
                        step_id="s_ts_freq",
                        structures=ts_freq_result.structures,
                        artifacts=ts_freq_result.artifacts,
                        status=StepStatus.COMPLETED,
                    ),
                }
            )
        )
        collected_structures.extend(tuple(ts_freq_result.structures))
        collected_results.extend(tuple(ts_freq_result.results))

        ts_sp_result = _exec_calc("s_ts_sp", "success_sp", orca_exe)
        collected_results.extend(tuple(ts_sp_result.results))
        collected_structures.extend(tuple(ts_sp_result.structures))

        monkeypatch.setenv("FAKE_IRC_ORDER", "reverse_first")
        irc_assembly = assemble(
            plan,
            run_inputs(structures={"structures": _ts_structures(1)}),
            materialized=materialized,
        )
        assert irc_assembly.ok
        irc_items = irc_assembly.for_step("s_irc")
        assert len(irc_items) == 1
        from confflow.persistence.work_items import SqliteWorkItemStore as _Store

        with _Store.open(store_path(run_root, "s_irc")) as store:
            irc_result = _batch().execute_step_resumable(
                _irc_request(plan, tuple(irc_items), run_root, irc_exe),
                store=store,
                run_root=run_root,
                owner_token="ctl-formal",
            )
        assert irc_result.status is StepStatus.COMPLETED
        assert len(tuple(irc_result.structures)) == 2
        materialized = MaterializedOutputs(
            steps=FrozenDict(
                {
                    **dict(materialized.steps),
                    "s_irc": StepOutputs(
                        step_id="s_irc",
                        structures=irc_result.structures,
                        artifacts=irc_result.artifacts,
                        status=StepStatus.COMPLETED,
                    ),
                }
            )
        )
        collected_structures.extend(tuple(irc_result.structures))
        collected_results.extend(tuple(irc_result.results))

        def _exec_downstream(step_id: str, fake_mode: str, upstream: str) -> Any:
            monkeypatch.setenv("FAKE_MODE", fake_mode)
            assembly = assemble(
                plan,
                run_inputs(structures={"structures": _ts_structures(1)}),
                materialized=materialized,
            )
            assert assembly.ok, [item.message for item in assembly.errors]
            items = assembly.for_step(step_id)
            assert len(items) >= 1, step_id
            request = _std_request(
                plan,
                step_id,
                tuple(items),
                run_root,
                orca_exe,
                f"{run_root}/work-{step_id}",
            )
            result = _batch().execute_step(request)
            assert result.summary.get("completed", 0) >= 1, (step_id, result.summary)
            return result, items

        eopt_result, _ = _exec_downstream("s_eopt", "success_opt", "s_irc")
        materialized = MaterializedOutputs(
            steps=FrozenDict(
                {
                    **dict(materialized.steps),
                    "s_eopt": StepOutputs(
                        step_id="s_eopt",
                        structures=eopt_result.structures,
                        artifacts=eopt_result.artifacts,
                        status=StepStatus.COMPLETED,
                    ),
                }
            )
        )
        collected_structures.extend(tuple(eopt_result.structures))
        collected_results.extend(tuple(eopt_result.results))

        efreq_result, _ = _exec_downstream("s_efreq", "success_freq", "s_eopt")
        materialized = MaterializedOutputs(
            steps=FrozenDict(
                {
                    **dict(materialized.steps),
                    "s_efreq": StepOutputs(
                        step_id="s_efreq",
                        structures=efreq_result.structures,
                        artifacts=efreq_result.artifacts,
                        status=StepStatus.COMPLETED,
                    ),
                }
            )
        )
        collected_structures.extend(tuple(efreq_result.structures))
        collected_results.extend(tuple(efreq_result.results))

        esp_result, _ = _exec_downstream("s_esp", "success_sp", "s_efreq")
        collected_structures.extend(tuple(esp_result.structures))
        collected_results.extend(tuple(esp_result.results))

        return {
            "group_key": group_key,
            "ts_id": ts_record.id,
            "structures": collected_structures,
            "results": collected_results,
        }

    def _pinned_analysis(self, bundle: dict[str, Any], endpoint_assignment: dict[str, str]) -> Any:
        from confflow.analysis.compute import ReactionEnergyModel, policy_from_native
        from confflow.analysis.executor import AnalysisExecutor
        from confflow.analysis.models import AnalysisDefinition, AnalysisInputs
        from confflow.domain import ResultSet as _ResultSet
        from confflow.domain import StructureSet as _StructureSet

        structures = _StructureSet.of(*bundle["structures"])
        pinned: list[Any] = []
        for record in bundle["results"]:
            step = record.source_step_id
            kind = record.kind
            if kind == "energy" and step in ("s_ts_sp", "s_esp"):
                pinned.append(record)
            elif kind == "gibbs_correction" and step in ("s_ts_freq", "s_efreq"):
                pinned.append(record)
        assert pinned, "no pinned real results"
        for record in pinned:
            assert record.result_id is not None
            assert record.source_step_id in ("s_ts_sp", "s_esp", "s_ts_freq", "s_efreq")
        policy = policy_from_native(
            {
                "energy_mode": "composite",
                "electronic_result_kind": "energy",
                "correction_result_kind": "gibbs_correction",
                "energy_fallback": "none",
            }
        )
        definition = AnalysisDefinition(
            kind="reaction_profile",
            energy_model=ReactionEnergyModel(policy),
            params=FrozenDict({}),
            endpoint_assignment=FrozenDict(endpoint_assignment),
        )
        inputs = AnalysisInputs(
            structures=FrozenDict({"structures": structures}),
            results=FrozenDict({"results": _ResultSet(tuple(pinned))}),
            definition=definition,
        )
        return AnalysisExecutor().execute(inputs, analysis_step_id="s_analysis")

    def test_formal_chain_unassigned(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bundle = self._run_chain(tmp_path, monkeypatch)
        step = self._pinned_analysis(bundle, {"forward": "unassigned", "reverse": "unassigned"})
        assert step.ok, [(item.code, item.message) for item in step.diagnostics]
        by_kind = {result.kind: result for result in step.results}
        assert set(by_kind) == {
            "barrier_forward_endpoint",
            "barrier_reverse_endpoint",
            "endpoint_gibbs_delta",
            "endpoint_energy_delta",
            "reaction_profile",
        }
        for result in step.results:
            assert result.source_step_id == "s_analysis"
            assert result.subject_structure_id == bundle["ts_id"]
        profile = dict(by_kind["reaction_profile"].value)
        assert profile["group_key"] == bundle["group_key"]
        assert set(profile["nodes"]) == {"ts", "forward", "reverse"}
        assert profile["assignment"] == "unassigned"
        assert profile["endpoint_assignment"] == {
            "forward": "unassigned",
            "reverse": "unassigned",
        }
        assert by_kind["barrier_forward_endpoint"].value == pytest.approx(0.0, abs=1e-9)
        assert by_kind["barrier_reverse_endpoint"].value == pytest.approx(0.0, abs=1e-9)
        sources = list(profile["source_result_ids"])
        assert sources == sorted(sources) and len(sources) == 6
        from confflow.analysis.pes import pes_from_analysis_results

        combined = pes_from_analysis_results(step.results)
        assert combined["group_keys"] == [bundle["group_key"]]
        assert combined["count"] == 1

    def test_formal_chain_explicit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bundle = self._run_chain(tmp_path, monkeypatch)
        step = self._pinned_analysis(bundle, {"forward": "reactant", "reverse": "product"})
        assert step.ok, [(item.code, item.message) for item in step.diagnostics]
        by_kind = {result.kind: result for result in step.results}
        profile = dict(by_kind["reaction_profile"].value)
        assert profile["assignment"] == "explicit"
        assert profile["endpoint_assignment"] == {"forward": "reactant", "reverse": "product"}
        for result in step.results:
            assert result.provenance is not None
            assert dict(result.provenance.metadata)["endpoint_assignment"] == {
                "forward": "reactant",
                "reverse": "product",
            }
