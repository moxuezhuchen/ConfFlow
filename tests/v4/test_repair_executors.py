#!/usr/bin/env python3

"""Wave-1 stream C scoped tests: executors / native boundary / binding.

C-owned seam only; cross-module integration (A registry resolution, B
ResultRef identity, D durable dispatch, G native seed verification) is
recorded as explicit dependencies in
``docs/internal/V4_REPAIR_C_REPORT.md``.

NOTE: these tests deliberately avoid ``confflow.workflow.v4`` imports so
they stay runnable while sibling wave-1 workers own that package.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.completion import WorkItemStatus
from confflow.domain.structure import StructureRecord
from confflow.domain.work_item import WorkItem, WorkItemInputs
from confflow.execution.binding_resolution import (
    BindingRequestDefaults,
    resolve_execution_binding,
    validate_step_seed,
)
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.execution.contracts import ExecutionBinding
from confflow.execution.native import GeometryOutput, NativeResult, ProgramName
from confflow.execution.profile_standard import StandardResultProfile
from confflow.execution.profiles import ProfileContext
from confflow.execution.transform_executor import TransformExecutor
from confflow.execution.work_item_executor import (
    ItemExecutionContext,
    WorkItemExecutor,
    hashed_item_slug,
)
from tests.v4._helpers.repair import _butane, _ctx, _digest, _item, _resources, _sci


def _water(struct_id: str, offset: float = 0.0, **overrides: Any) -> StructureRecord:
    atoms = ("O", "H", "H")
    coords = (
        (0.0 + offset, 0.0 + offset, 0.0 + offset),
        (0.76 + offset, 0.59 + offset, 0.0 + offset),
        (0.76 + offset, -0.59 + offset, 0.0 + offset),
    )
    params: dict[str, Any] = {"charge": 0, "multiplicity": 1}
    params.update(overrides)
    return StructureRecord(id=struct_id, atoms=atoms, coordinates=coords, **params)


CONFGEN_NATIVE = {
    "chains": ["1-2-3-4"],
    "chain_angles": "0;0,120,240;0",
}


def test_confgen_seed_missing_and_bad_native_fail_closed(tmp_path):
    item = _item("c1:g1", "c1", [_butane("seed-a")])
    out = ConfgenExecutor().execute(item, _ctx(_sci(seed=None), str(tmp_path)))
    assert out.status is WorkItemStatus.FAILED
    out2 = ConfgenExecutor().execute(
        item,
        _ctx(
            _sci(seed=1, native=FrozenDict({"chains": ["1-2-3-4"], "bogus_key": 1})), str(tmp_path)
        ),
    )
    assert out2.status is WorkItemStatus.FAILED
    out3 = ConfgenExecutor().execute(item, _ctx(_sci(seed=1, native=FrozenDict({})), str(tmp_path)))
    assert out3.status is WorkItemStatus.FAILED  # chains required
    out4 = ConfgenExecutor().execute(
        item,
        _ctx(
            _sci(seed=1, native=FrozenDict({"chains": ["1-2-3-4"], "optimize": True})),
            str(tmp_path),
        ),
    )
    assert out4.status is WorkItemStatus.FAILED  # MMFF legacy refused


def test_confgen_ring_bond_and_no_rotate(tmp_path):
    square = StructureRecord(
        id="ring",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (1.5, 1.5, 0.0), (0.0, 1.5, 0.0)),
        charge=0,
        multiplicity=1,
    )
    item = _item("c1:ring", "c1", [square])
    out = ConfgenExecutor().execute(
        item,
        _ctx(
            _sci(seed=3, native=FrozenDict({"chains": ["1-2-3-4"], "chain_angles": "0;0,120;0"})),
            str(tmp_path),
        ),
    )
    assert out.status is WorkItemStatus.FAILED  # ring bond refused
    out2 = ConfgenExecutor().execute(
        item,
        _ctx(
            _sci(
                seed=3,
                native=FrozenDict(
                    {
                        "chains": ["1-2-3-4"],
                        "chain_angles": "0;0,120;0",
                        "no_rotate": ["2-3", "1-2", "3-4", "4-1", "1-4", "2-1"],
                    }
                ),
            ),
            str(tmp_path),
        ),
    )
    # Every chain bond excluded -> no rotatable bonds (ring bond 2-3 excluded first).
    assert out2.status is WorkItemStatus.FAILED


def test_transform_deduplicate_scientific_groups(tmp_path):
    same = [_water("x"), _water("x-dup")]
    assert same[0].geometry_digest == same[1].geometry_digest
    item = _item("t1:g", "t1", same)
    out = TransformExecutor().execute(item, _ctx(_sci(transform="deduplicate"), str(tmp_path)))
    assert out.status is WorkItemStatus.COMPLETED
    assert len(out.structures) == 1
    assert out.structures[0].id == "x"
    # Reordered input selects the same representative (reorder invariance).
    item_rev = _item("t1:g", "t1", [_water("x-dup"), _water("x")])
    out_rev = TransformExecutor().execute(
        item_rev, _ctx(_sci(transform="deduplicate"), str(tmp_path))
    )
    assert [r.id for r in out_rev.structures] == ["x"]
    # Distinct charge is never collapsed by equal geometry.
    mixed = [_water("c0", charge=0), _water("c1", charge=1)]
    assert mixed[0].geometry_digest == mixed[1].geometry_digest
    out_mixed = TransformExecutor().execute(
        _item("t1:m", "t1", mixed), _ctx(_sci(transform="deduplicate"), str(tmp_path))
    )
    assert sorted(r.id for r in out_mixed.structures) == ["c0", "c1"]
    # Distinct group_key / role are never collapsed either.
    grouped = [_water("g0", group_key="A"), _water("g1", group_key="B")]
    out_grouped = TransformExecutor().execute(
        _item("t1:gg", "t1", grouped), _ctx(_sci(transform="deduplicate"), str(tmp_path))
    )
    assert len(out_grouped.structures) == 2


def test_transform_refine_rmsd_topology(tmp_path):
    near = [_water("r0"), _water("r1", offset=0.001)]
    out = TransformExecutor().execute(
        _item("t1:r", "t1", near), _ctx(_sci(transform="refine"), str(tmp_path))
    )
    assert out.status is WorkItemStatus.COMPLETED
    assert [r.id for r in out.structures] == ["r0"]  # RMSD witness collapse
    # A true torsional rotamer (C3 swung 120 deg about the C1-C2 bond):
    # bond lengths and adjacency are identical but the Kabsch RMSD
    # (0.71 A) exceeds the 0.25 A threshold, so both are kept.
    base_chain = ((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (2.0, 1.4, 0.0), (3.4, 1.0, 0.5))
    rotamer = ((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (2.0, 1.4, 0.0), (1.7553, 1.5874, -1.5083))

    def _tetra(struct_id: str, coords: Any) -> StructureRecord:
        return StructureRecord(
            id=struct_id, atoms=("C", "C", "C", "C"), coordinates=coords, charge=0, multiplicity=1
        )

    out_far = TransformExecutor().execute(
        _item("t1:f", "t1", [_tetra("f0", base_chain), _tetra("f1", rotamer)]),
        _ctx(_sci(transform="refine"), str(tmp_path)),
    )
    assert sorted(r.id for r in out_far.structures) == ["f0", "f1"]
    # Broken topology (stretched O-H beyond bond perception) is distinct.
    stretched = StructureRecord(
        id="s1",
        atoms=("O", "H", "H"),
        coordinates=((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (0.76, -0.59, 0.0)),
        charge=0,
        multiplicity=1,
    )
    out_topo = TransformExecutor().execute(
        _item("t1:t", "t1", [_water("w0"), stretched]),
        _ctx(_sci(transform="refine"), str(tmp_path)),
    )
    assert len(out_topo.structures) == 2
    # Unknown native keys fail closed.
    out_bad = TransformExecutor().execute(
        _item("t1:b", "t1", near),
        _ctx(_sci(transform="refine", native=FrozenDict({"ewin": 5.0})), str(tmp_path)),
    )
    assert out_bad.status is WorkItemStatus.FAILED


def test_transform_filter_explicit(tmp_path):
    trio = [_water("m1"), _water("m2", 0.5), _water("m3", 1.0)]
    out = TransformExecutor().execute(
        _item("t1:h", "t1", trio),
        _ctx(_sci(transform="filter", native=FrozenDict({"max_structures": 1})), str(tmp_path)),
    )
    assert [r.id for r in out.structures] == ["m1"]
    out_bad = TransformExecutor().execute(
        _item("t1:b", "t1", trio),
        _ctx(_sci(transform="filter", native=FrozenDict({"unknown_key": 1})), str(tmp_path)),
    )
    assert out_bad.status is WorkItemStatus.FAILED
    out_unknown_kind = TransformExecutor().execute(
        _item("t1:k", "t1", trio), _ctx(_sci(transform="nope"), str(tmp_path))
    )
    assert out_unknown_kind.status is WorkItemStatus.FAILED


def test_uniform_executor_seam_signatures():
    import inspect

    from confflow.execution.work_item_executor import WorkItemExecutor as W

    for cls in (ConfgenExecutor, TransformExecutor, W):
        sig = inspect.signature(cls.execute)
        assert list(sig.parameters)[1:3] == ["work_item", "context"]
        assert "should_cancel" in sig.parameters


def test_standard_profile_binds_output_subject_and_result_id():
    from confflow.execution.native import ParsedGeometry, ResolvedCalculationInputs

    profile = StandardResultProfile()
    seed = _water("seed-1")
    produced = NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=True,
        geometry_output=GeometryOutput.PRODUCED,
        final_geometry=ParsedGeometry(
            atoms=("O", "H", "H"),
            coordinates=((0.0, 0.0, 0.1), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0)),
        ),
        energies_hartree=FrozenDict({"electronic": -76.0}),
        frequencies_cm=(),
        native_metadata=FrozenDict({}),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="job.log",
    )
    resolved = ResolvedCalculationInputs(
        structure=seed,
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=_resources(),
        native=FrozenDict({"keyword": "B3LYP Opt"}),
    )
    digest_a = _digest("producer-a")
    ctx = ProfileContext(
        work_item_id="wi:s1:g",
        step_id="s1",
        logical_key="s1:g",
        profile_name="standard",
        profile_version=profile.contract_version,
        native_result=produced,
        inputs=resolved,
        producer_digest=digest_a,
    )
    output = profile.apply(ctx)
    assert len(output.structures) == 1
    assert output.results[0].subject_structure_id == output.structures[0].id
    assert output.structures[0].id != seed.id
    assert output.results[0].result_id is not None
    # Same producer digest -> stable refs; changed science -> new refs.
    output2 = profile.apply(ctx)
    assert output2.results[0].result_id == output.results[0].result_id
    ctx_b = dataclasses.replace(ctx, producer_digest=_digest("producer-b"))
    output_b = profile.apply(ctx_b)
    assert output_b.results[0].result_id != output.results[0].result_id


def test_standard_profile_measurement_retains_input_identity():
    from confflow.execution.native import ParsedGeometry, ResolvedCalculationInputs

    profile = StandardResultProfile()
    seed = _water("seed-sp")
    # Parser reports the input geometry back verbatim: a measurement, not
    # a transformation.
    measured = NativeResult(
        program=ProgramName.GAUSSIAN,
        terminated_normally=True,
        geometry_output=GeometryOutput.PRODUCED,
        final_geometry=ParsedGeometry(atoms=tuple(seed.atoms), coordinates=tuple(seed.coordinates)),
        energies_hartree=FrozenDict({"electronic": -76.0}),
        frequencies_cm=(),
        native_metadata=FrozenDict({}),
        produced_files=(),
        parser_diagnostics=(),
        log_file_name="job.log",
    )
    resolved = ResolvedCalculationInputs(
        structure=seed,
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=_resources(),
        native=FrozenDict({"keyword": "B3LYP SP"}),
    )
    output = profile.apply(
        ProfileContext(
            work_item_id="wi:s1:g",
            step_id="s1",
            logical_key="s1:g",
            profile_name="standard",
            profile_version=profile.contract_version,
            native_result=measured,
            inputs=resolved,
            producer_digest=_digest("p"),
        )
    )
    assert len(output.structures) == 1
    assert output.structures[0].id == seed.id
    assert output.results[0].subject_structure_id == seed.id


def test_recovery_syntax_owned_by_adapter():
    from confflow.programs.gaussian.adapter import GaussianProgramAdapter

    adapter = GaussianProgramAdapter()
    assert "modredundant" in adapter.rescue_scan_keyword("opt=(ts,calcfc,tight) b3lyp freq")
    assert adapter.rescue_freeze_directive(1, 2) == "B 1 2 F"
    from confflow.execution.recovery_standard import (
        ensure_gaussian_modredundant_keyword,
        scan_keyword_for_rescue,
    )

    assert scan_keyword_for_rescue(
        "opt=(ts,calcfc,tight) b3lyp freq"
    ) == adapter.rescue_scan_keyword("opt=(ts,calcfc,tight) b3lyp freq")
    assert ensure_gaussian_modredundant_keyword("opt b3lyp") == adapter.ensure_modredundant_keyword(
        "opt b3lyp"
    )


def test_binding_resolution_planned_wins():
    planned = ExecutionBinding(
        binding_id="planned",
        executable="/opt/g16/g16",
        env=FrozenDict({"OMP": "4"}),
        walltime_seconds=3600,
    )
    defaults = BindingRequestDefaults(
        executables={"orca": "/usr/bin/orca", "g16": "/other/g16"},
        env={"OMP": "1", "BASE": "yes"},
        walltime_seconds=60,
    )
    resolved = resolve_execution_binding(
        program="g16",
        planned=planned,
        defaults=defaults,
        adapter_default_executable="g16",
    )
    assert resolved.executable == "/opt/g16/g16"
    assert resolved.env["OMP"] == "4"
    assert resolved.env["BASE"] == "yes"
    assert resolved.walltime_seconds == 3600


def test_binding_resolution_rejects_retired_target_fields() -> None:
    """R2.2: mappings carrying the retired target fail closed explicitly."""
    from confflow.domain.errors import DomainError

    with pytest.raises(DomainError, match="retired field 'target'"):
        resolve_execution_binding(
            program="g16",
            planned={"binding_id": "p", "target": "cluster"},
            adapter_default_executable="g16",
        )
    with pytest.raises(DomainError, match="retired field 'target'"):
        resolve_execution_binding(
            program="g16",
            planned=None,
            defaults={"executables": {}, "env": {}, "target": "cluster"},
            adapter_default_executable="g16",
        )


def test_validate_step_seed_typed():
    from confflow.domain.errors import DomainError

    assert validate_step_seed(None) is None
    assert validate_step_seed(7) == 7
    with pytest.raises(DomainError):
        validate_step_seed(True)
    with pytest.raises(DomainError):
        validate_step_seed("7")


def test_strict_staging_rejects_weak_artifacts(tmp_path):
    from confflow.programs.registry import get_program_adapter

    item = _item("c1:g1", "c1", [_water("w")])
    driving_id = item.named_inputs.structures["structure"][0].id

    def _item_with(artifact: ArtifactRef) -> WorkItem:
        named = WorkItemInputs(
            structures=item.named_inputs.structures,
            artifacts=FrozenDict({"checkpoint": ArtifactSet((artifact,))}),
            results=item.named_inputs.results,
        )
        return dataclasses.replace(item, named_inputs=named)

    executor = WorkItemExecutor()
    run_root = str(tmp_path)
    ctx = ItemExecutionContext(
        step_id="c1",
        scientific=_sci(),
        scientific_defaults=SimpleNamespace(),
        adapter=get_program_adapter("orca"),
        profile=None,
        checks=(),  # type: ignore[arg-type]
        recovery=None,
        run_root=run_root,
    )
    weak = ArtifactRef(
        id="weak",
        role="checkpoint",
        locator=ArtifactLocator.run_relative("steps/p/w.chk"),
        subject_structure_id=driving_id,
    )
    with pytest.raises(Exception, match="checksum"):
        executor._stage_checkpoints(_item_with(weak), ctx, os.path.join(run_root, "d0"))
    nosub = ArtifactRef(
        id="nosub",
        role="checkpoint",
        locator=ArtifactLocator.run_relative("steps/p/w.chk"),
        checksum="sha256:" + "0" * 64,
    )
    with pytest.raises(Exception, match="subject"):
        executor._stage_checkpoints(_item_with(nosub), ctx, os.path.join(run_root, "d1"))
    from confflow.domain.artifact import LocatorKind

    ext = ArtifactRef(
        id="ext",
        role="checkpoint",
        locator=ArtifactLocator(kind=LocatorKind.EXTERNAL_URI, uri="https://x/y.chk"),
        checksum="sha256:" + "0" * 64,
        subject_structure_id=driving_id,
    )
    with pytest.raises(Exception, match="locator"):
        executor._stage_checkpoints(_item_with(ext), ctx, os.path.join(run_root, "d2"))


def test_orca_rejects_checkpoints_explicitly():
    from confflow.execution.native import ResolvedCalculationInputs, StagedArtifact
    from confflow.programs.orca.adapter import OrcaProgramAdapter

    seed = _water("s-orca")
    resolved = ResolvedCalculationInputs(
        structure=seed,
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=_resources(),
        native=FrozenDict({"keyword": "B3LYP Opt"}),
        checkpoints=(
            StagedArtifact(
                local_name="staged/input-checkpoint-0.chk",
                role="checkpoint",
                subject_structure_id=seed.id,
                checksum="sha256:" + "a" * 64,
            ),
        ),
    )
    with pytest.raises(ValueError, match="artifact_unsupported"):
        OrcaProgramAdapter().materialize_native_input(resolved)


def test_gaussian_qst_rejects_checkpoints():
    from confflow.execution.native import ResolvedCalculationInputs, StagedArtifact
    from confflow.programs.gaussian.adapter import GaussianProgramAdapter

    reactant = _water("r")
    product = _water("p", 0.5)
    resolved = ResolvedCalculationInputs(
        structure=reactant,
        charge=0,
        multiplicity=1,
        freeze=None,
        resources=_resources(),
        native=FrozenDict({"keyword": "opt=(qst2) b3lyp"}),
        checkpoints=(
            StagedArtifact(
                local_name="staged/input-checkpoint-0.chk",
                role="checkpoint",
                subject_structure_id=reactant.id,
                checksum="sha256:" + "a" * 64,
            ),
        ),
        extra_structures=FrozenDict(
            {
                "reactant": StructureSet.of(reactant),
                "product": StructureSet.of(product),
            }
        ),
    )
    with pytest.raises(ValueError, match="artifact_unsupported"):
        GaussianProgramAdapter().materialize_native_input(resolved)


def test_attempt_dirs_collision_resistant_and_isolated(tmp_path):
    ctx0 = ItemExecutionContext(
        step_id="s",
        scientific=None,
        scientific_defaults=None,  # type: ignore[arg-type]
        adapter=None,
        profile=None,
        checks=(),
        recovery=None,  # type: ignore[arg-type]
        run_root=str(tmp_path),
        attempt=0,
    )
    ctx1 = dataclasses.replace(ctx0, attempt=1)

    class _Item:
        def __init__(self, logical_key: str):
            self.logical_key = logical_key
            self.id = f"wi:{logical_key}"

    a = _Item("s:A:B")
    b = _Item("s:A_B")
    assert ctx0.attempt_dir(a) != ctx0.attempt_dir(b)
    assert ctx0.attempt_dir(a) != ctx1.attempt_dir(a)
    assert len(os.path.basename(os.path.dirname(ctx0.attempt_dir(a)))) <= 64
    assert hashed_item_slug("wi:x") != hashed_item_slug("wi:y")


def test_science_package_has_no_legacy_transitives():
    import subprocess
    import sys

    probe = (
        "import confflow.science as s, confflow.execution.confgen_executor, "
        "confflow.execution.transform_executor;"
        " mods=[m for m in __import__('sys').modules if m.startswith(("
        "'confflow.blocks', 'confflow.workflow', 'confflow.application', "
        "'confflow.remote', 'confflow.analysis'))];"
        " print('LEGACY:' + ','.join(sorted(mods)) if mods else 'CLEAN')"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert out.stdout.strip() == "CLEAN", out.stdout.strip() + out.stderr[-500:]
