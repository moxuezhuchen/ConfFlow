#!/usr/bin/env python3

"""ConfGen v3 workflow/executor/producer integration tests (coordinator lane).

Covers the typed v3 boundary end to end: schema validators source science
authorities, the registry publishes the v3 contract, validation enforces
conditional seed rules and freeze rejection, the executor runs the engine
(leaf-only publication, confgen_state results, deterministic gzip JSONL
artifacts, upstream chaining), legacy numeric regressions stay bit-for-bit,
and the producer advertises real typed options plus a compiling recipe.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.artifact import ArtifactSet
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.domain.result import ResultSet, ScientificResult
from confflow.execution.confgen_executor import (
    CONFGEN_REPORT_ROLE,
    CONFGEN_STATE_KIND,
    CONFGEN_TARGETS_ROLE,
    ConfgenExecutor,
)
from confflow.execution.contracts import ExecutorCapability
from confflow.execution.registry import default_registry
from confflow.workflow.v4.assembly import (
    MaterializedOutputs,
    RunInputs,
    StepOutputs,
    assemble_work_items,
)
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from confflow.workflow.v4.parser import parse_workflow_document
from tests.v4._helpers.repair import _butane, _ctx, _item, _sci


def _typed_native(**overrides: Any) -> dict[str, Any]:
    declaration: dict[str, Any] = {
        "schema_version": 3,
        "torsions": [
            {
                "id": "central",
                "bond": [2, 3],
                "model": "relative_rotation_grid",
                "angles": [0.0, 120.0, 240.0],
                "treatment": "enumerate",
            }
        ],
    }
    declaration.update(overrides)
    return ConfgenModelV3.model_validate(declaration).scientific_native()


def _v3_doc(confgen: dict[str, Any], **extra: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": "cg",
        "executor": "confgen",
        "bindings": {"structure": {"source": {"run": "structures"}}},
        "confgen": confgen,
    }
    step.update(extra)
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [step],
    }


# ----------------------------------------------------------------------
# Typed schema validators
# ----------------------------------------------------------------------


class TestTypedValidators:
    def test_chemical_requires_four_atom_frame(self) -> None:
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [
                        {
                            "id": "rot",
                            "bond": [2, 3],
                            "model": "chemical",
                            "states": {"gauche": 60.0, "trans": 180.0},
                        }
                    ],
                }
            )
        valid = ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "torsions": [
                    {
                        "id": "rot",
                        "atoms": [1, 2, 3, 4],
                        "model": "chemical",
                        "states": {"gauche": 60.0, "trans": 180.0},
                    }
                ],
            }
        )
        assert valid.torsions[0].states == {"gauche": 60.0, "trans": 180.0}

    def test_chemical_rejects_angles_and_empty_states(self) -> None:
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [
                        {
                            "id": "rot",
                            "atoms": [1, 2, 3, 4],
                            "model": "chemical",
                            "angles": [60.0],
                            "states": {"gauche": 60.0},
                        }
                    ],
                }
            )
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [{"id": "rot", "atoms": [1, 2, 3, 4], "model": "chemical"}],
                }
            )

    def test_preserve_input_needs_no_grid_but_enumerate_does(self) -> None:
        held = ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "torsions": [
                    {
                        "id": "held",
                        "bond": [2, 3],
                        "model": "relative_rotation_grid",
                        "treatment": "preserve_input",
                    }
                ],
            }
        )
        assert held.torsions[0].angles is None
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [
                        {
                            "id": "held",
                            "bond": [2, 3],
                            "model": "relative_rotation_grid",
                        }
                    ],
                }
            )

    def test_unknown_shapes_and_templates_rejected(self) -> None:
        with pytest.raises(ValidationError, match="shapes"):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "coordination": {
                        "metal_center": 1,
                        "binding_sites": [{"id": f"s{i}", "atoms": [i + 2]} for i in range(4)],
                        "shapes": ["pentagonal_pyramid"],
                    },
                }
            )
        with pytest.raises(ValidationError, match="template"):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "rings": [{"id": "r1", "atoms": [1, 2, 3, 4], "templates": ["chair_A_6"]}],
                }
            )
        with pytest.raises(ValidationError, match="not registered"):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "rings": [{"id": "r1", "atoms": [1, 2, 3, 4], "templates": ["nope"]}],
                }
            )

    def test_sampling_cap_requires_seed(self) -> None:
        with pytest.raises(ValidationError, match="seed"):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [
                        {
                            "id": "t",
                            "bond": [1, 2],
                            "model": "relative_rotation_grid",
                            "angles": [0.0, 90.0],
                        }
                    ],
                    "sampling": {"cap": 1},
                }
            )
        valid = ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "torsions": [
                    {
                        "id": "t",
                        "bond": [1, 2],
                        "model": "relative_rotation_grid",
                        "angles": [0.0, 90.0],
                    }
                ],
                "seed": 5,
                "sampling": {"cap": 1},
            }
        )
        assert valid.sampling is not None and valid.sampling.cap == 1

    def test_periodic_duplicate_angles_rejected(self) -> None:
        for angles in ([0.0, 360.0], [120.0, 480.0], [60.0, 60.0]):
            with pytest.raises(ValidationError):
                ConfgenModelV3.model_validate(
                    {
                        "schema_version": 3,
                        "torsions": [
                            {
                                "id": "t",
                                "bond": [1, 2],
                                "model": "relative_rotation_grid",
                                "angles": list(angles),
                            }
                        ],
                    }
                )
        for angles in ([0.0, 90.0, 180.0, 270.0], [0.0, 0.5]):
            valid = ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [
                        {
                            "id": "t",
                            "bond": [1, 2],
                            "model": "relative_rotation_grid",
                            "angles": list(angles),
                        }
                    ],
                }
            )
            assert len(valid.torsions[0].angles or []) == len(angles)

    def test_duplicate_axis_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate"):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "torsions": [
                        {
                            "id": "t",
                            "bond": [1, 2],
                            "model": "relative_rotation_grid",
                            "angles": [0.0],
                        },
                        {
                            "id": "t",
                            "bond": [3, 4],
                            "model": "relative_rotation_grid",
                            "angles": [0.0],
                        },
                    ],
                }
            )

    def test_exclusion_proof_requires_id(self) -> None:
        with pytest.raises(ValidationError, match="proof_id"):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "exclusions": [
                        {"axis": "torsions", "match": {"t": 0.0}, "reason": "x", "proof": {}}
                    ],
                }
            )

    def test_defaults_track_science_authorities(self) -> None:
        from confflow.science.confgen.coordination.stage import (
            DEFAULT_SECTION_TOLERANCES,
        )
        from confflow.science.confgen.tolerances import ConfgenTolerances
        from confflow.workflow.v4.confgen_schema import (
            ConfgenToleranceModel,
            CoordinationTolerancesModel,
        )

        assert CoordinationTolerancesModel().model_dump() == dict(DEFAULT_SECTION_TOLERANCES)
        science = ConfgenTolerances()
        model = ConfgenToleranceModel()
        for name in model.model_fields:
            assert float(getattr(model, name)) == float(getattr(science, name)), name


# ----------------------------------------------------------------------
# Registry contract v3
# ----------------------------------------------------------------------


class TestRegistryContractV3:
    def test_confgen_contract_is_deterministic_v3(self) -> None:
        contract = default_registry().resolve_executor(ExecutorCapability.CONFGEN)
        assert contract.contract_version == "confflow.contract.executor.confgen.v3"
        assert contract.stochastic is False

    def test_results_many_by_subject_and_report_roles(self) -> None:
        contract = default_registry().resolve_executor(ExecutorCapability.CONFGEN)
        results = contract.output_port("results")
        assert results is not None
        assert results.cardinality.value == "many"
        assert results.pairing.value == "by_subject"
        artifacts = contract.output_port("artifacts")
        assert artifacts is not None
        assert CONFGEN_REPORT_ROLE in artifacts.roles
        assert CONFGEN_TARGETS_ROLE in artifacts.roles

    def test_confgen_state_input_port_optional_by_subject(self) -> None:
        contract = default_registry().resolve_executor(ExecutorCapability.CONFGEN)
        port = contract.input_port("confgen_state")
        assert port is not None
        assert port.kind.value == "result"
        assert port.cardinality.value == "optional"
        assert port.pairing.value == "by_subject"


# ----------------------------------------------------------------------
# Validation: conditional seed + freeze rejection
# ----------------------------------------------------------------------


class TestValidationRules:
    def _reasons(self, doc: dict[str, Any]) -> tuple[list[str], bool]:
        compiled = compile_workflow(doc)
        reasons = [str(item.details.get("reason", item.code)) for item in compiled.diagnostics] + [
            item.code for item in compiled.diagnostics
        ]
        return reasons, compiled.ok

    def test_typed_deterministic_compiles_without_seed(self) -> None:
        reasons, ok = self._reasons(_v3_doc({"schema_version": 3, **_typed_native()}))
        assert ok, reasons

    def test_typed_cap_without_seed_rejected(self) -> None:
        from confflow.workflow.v4.validation import _confgen_seed_requirement

        assert _confgen_seed_requirement({"chains": ["1-2-3"]}) is not None
        assert _confgen_seed_requirement({"schema_version": 3}) is None
        assert _confgen_seed_requirement({"schema_version": 3, "sampling": {"cap": 2}}) is not None

    def test_typed_cap_with_seed_compiles(self) -> None:
        reasons, ok = self._reasons(_v3_doc(_typed_native(seed=7, sampling={"cap": 2})))
        assert ok, reasons

    def test_confgen_freeze_override_rejected(self) -> None:
        doc = _v3_doc({"native": {"chains": ["1-2-3"]}, "seed": 1, "overrides": {"freeze": [1]}})
        reasons, ok = self._reasons(doc)
        assert not ok, reasons


# ----------------------------------------------------------------------
# Executor: v3 engine path
# ----------------------------------------------------------------------


def _run_v3(native: dict[str, Any], seed: int | None, tmp: str, **sci_kwargs: Any):
    item = _item("c1:g1", "c1", [_butane("seed-a")])
    sci = _sci(seed=seed, native=FrozenDict(native), **sci_kwargs)
    return ConfgenExecutor().execute(item, _ctx(sci, tmp)), item


def _targets_rows(out: Any, tmp: str) -> list[dict[str, Any]]:
    import gzip as _gzip

    ref = {r.role: r for r in out.artifacts}[CONFGEN_TARGETS_ROLE]
    with _gzip.open(os.path.join(tmp, ref.locator.path), "rb") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _abs_native(**overrides: Any) -> dict[str, Any]:
    declaration: dict[str, Any] = {
        "schema_version": 3,
        "torsions": [
            {
                "id": "T1",
                "atoms": [1, 2, 3, 4],
                "model": "absolute_dihedral_grid",
                "angles": [60.0, 180.0],
                "treatment": "enumerate",
            }
        ],
    }
    declaration.update(overrides)
    return ConfgenModelV3.model_validate(declaration).scientific_native()


def _abs_preserve_native() -> dict[str, Any]:
    # NOTE: angles are repeated explicitly because the CORE planner currently
    # requires explicit grids even for preserve_input (requested in the
    # interface note); preserve axes never rotate from them.
    return ConfgenModelV3.model_validate(
        {
            "schema_version": 3,
            "torsions": [
                {
                    "id": "T1",
                    "atoms": [1, 2, 3, 4],
                    "model": "absolute_dihedral_grid",
                    "angles": [60.0, 180.0],
                    "treatment": "preserve_input",
                }
            ],
        }
    ).scientific_native()


def _chain_item(logical_key: str, step_id: str, driving: Any, result: Any):
    from confflow.domain.work_item import WorkItemInputs

    item = _item(logical_key, step_id, [driving])
    return item.__class__(
        id=item.id,
        logical_key=item.logical_key,
        step_id=item.step_id,
        named_inputs=WorkItemInputs(
            structures=item.named_inputs.structures,
            results=FrozenDict({"confgen_state": ResultSet((result,))}),
        ),
        resources=item.resources,
        semantic_digest=item.semantic_digest,
    )


def _resubject(result: Any, subject: str) -> Any:
    """Return *result* with an updated subject (test helper)."""
    import dataclasses

    return dataclasses.replace(result, subject_structure_id=subject)


class TestExecutorV3:
    def test_deterministic_full_grid_no_seed(self, tmp_path) -> None:
        out, _ = _run_v3(_typed_native(), None, str(tmp_path))
        assert out.status is WorkItemStatus.COMPLETED, out.diagnostics
        assert len(out.structures) == 3
        assert [r.ordinal for r in out.structures] == [0, 1, 2]
        assert [r.id for r in out.structures] == [
            f"c1:g1:structure:conformer:{i}" for i in (0, 1, 2)
        ]
        assert all(r.role == "conformer" for r in out.structures)
        assert all(r.parent_ids == ("seed-a",) for r in out.structures)
        assert all(r.charge == 0 and r.multiplicity == 1 for r in out.structures)

    def test_repeat_runs_are_identical(self, tmp_path) -> None:
        out1, _ = _run_v3(_typed_native(), None, str(tmp_path))
        out2, _ = _run_v3(_typed_native(), None, str(tmp_path))
        assert [r.id for r in out1.structures] == [r.id for r in out2.structures]
        assert [r.coordinates for r in out1.structures] == [r.coordinates for r in out2.structures]
        assert [r.value for r in out1.results] == [r.value for r in out2.results]

    def test_state_results_leaf_only_no_energy(self, tmp_path) -> None:
        out, _ = _run_v3(_typed_native(), None, str(tmp_path))
        assert len(out.results) == len(out.structures) == 3
        assert {r.kind for r in out.results} == {CONFGEN_STATE_KIND}
        subjects = {r.subject_structure_id for r in out.results}
        assert subjects == {r.id for r in out.structures}
        assert all(r.result_id is not None for r in out.results)
        for result in out.results:
            value = dict(result.value)
            assert value["schema_version"] == 3
            assert set(value["torsions"]) == {"central"}

    def test_report_and_targets_artifacts(self, tmp_path) -> None:
        out, item = _run_v3(_typed_native(), None, str(tmp_path))
        assert out.status is WorkItemStatus.COMPLETED
        roles = sorted(ref.role for ref in out.artifacts)
        assert roles == sorted([CONFGEN_REPORT_ROLE, CONFGEN_TARGETS_ROLE])
        by_role = {ref.role: ref for ref in out.artifacts}
        for ref in out.artifacts:
            assert ref.checksum.startswith("sha256:")
            assert ref.subject_structure_id == "seed-a"
            assert ref.locator.path is not None and ref.locator.path.endswith(
                "ensemble_report.json"
                if ref.role == CONFGEN_REPORT_ROLE
                else "confgen_states.jsonl.gz"
            )
            assert os.path.isfile(os.path.join(str(tmp_path), ref.locator.path))
        report = json.loads(
            open(os.path.join(str(tmp_path), by_role[CONFGEN_REPORT_ROLE].locator.path)).read()
        )
        assert report["schema_version"] == 3
        assert report["certificate"]["equations_ok"] is True
        assert report["certificate"]["digest"].startswith("sha256:")
        assert report["counts"]["published"] == 3
        assert len(report["members"]) == 3
        raw = open(
            os.path.join(str(tmp_path), by_role[CONFGEN_TARGETS_ROLE].locator.path), "rb"
        ).read()
        assert raw[:2] == b"\x1f\x8b"
        with gzip.open(
            os.path.join(str(tmp_path), by_role[CONFGEN_TARGETS_ROLE].locator.path), "rb"
        ) as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        assert len(rows) == 3
        assert [row["ordinal"] for row in rows] == [0, 1, 2]
        assert all(row["status"] == "published_leaf" for row in rows)

    def test_gzip_bytes_deterministic(self, tmp_path) -> None:
        out1, _ = _run_v3(_typed_native(), None, str(tmp_path))
        first = {ref.role: ref for ref in out1.artifacts}[CONFGEN_TARGETS_ROLE]
        with open(os.path.join(str(tmp_path), first.locator.path), "rb") as handle:
            blob1 = handle.read()
        assert blob1[4:8] == b"\x00\x00\x00\x00"  # gzip header mtime == 0
        out2, _ = _run_v3(_typed_native(), None, str(tmp_path))
        second = {ref.role: ref for ref in out2.artifacts}[CONFGEN_TARGETS_ROLE]
        with open(os.path.join(str(tmp_path), second.locator.path), "rb") as handle:
            blob2 = handle.read()
        assert blob1 == blob2
        assert hashlib.sha256(blob1).hexdigest() == first.checksum.split(":")[1]

    def test_sampling_cap_requires_seed_at_runtime(self, tmp_path) -> None:
        native = _typed_native(seed=7, sampling={"cap": 1})
        native.pop("seed", None)
        out, _ = _run_v3(native, None, str(tmp_path))
        assert out.status is WorkItemStatus.FAILED

    def test_sampled_subset_sorted_and_stable(self, tmp_path) -> None:
        native = _typed_native(seed=7, sampling={"cap": 2})
        out1, _ = _run_v3(native, 7, str(tmp_path))
        out2, _ = _run_v3(native, 7, str(tmp_path))
        assert out1.status is WorkItemStatus.COMPLETED
        assert len(out1.structures) == 2
        assert [r.ordinal for r in out1.structures] == sorted(r.ordinal for r in out1.structures)
        assert [r.id for r in out1.structures] == [r.id for r in out2.structures]
        rows = _targets_rows(out1, str(tmp_path))
        assert {row["unit"] for row in rows} <= {"final-leaf", "deferred-range"}
        assert "deferred-range" in {row["unit"] for row in rows}

    def test_nested_run_rows_carry_units(self, tmp_path) -> None:
        import math as _math

        import numpy as _np

        from confflow.domain.structure import StructureRecord
        from confflow.science.confgen.ring.puckering import canonical_forms, cp_to_coords

        # R4 rewrite (root-allowed exception): the old planar hexagon input
        # (z=0) legally publishes 0/16 under R3/CP audits. This units test
        # proves executor nested-rows metadata, not planar solving, so use
        # a fixed credible CP chair (frozen R1 inverse, same atom
        # order/count/elements). The old planar input is preserved verbatim
        # as a negative case in test_confgen_r4_cp_forms.py.
        _forms = {f"{f.family}_{f.index}": f for f in canonical_forms(6)}
        _chair = _np.asarray(cp_to_coords(_forms["C_0"].cp_target))
        _cent = _chair.mean(axis=0)
        _out = (_chair[0] - _cent) / float(_np.linalg.norm(_chair[0] - _cent))
        _methyl_pos = _chair[0] + 1.54 * _out
        _h_off = _np.array([1.09 * _math.cos(1.2), 1.09 * _math.sin(1.2), 0.35])
        coords = [tuple(float(v) for v in row) for row in _chair]
        coords.append(tuple(float(v) for v in _methyl_pos))
        coords.append(tuple(float(v) for v in (_methyl_pos + _h_off)))
        chain = StructureRecord(
            id="mch",
            atoms=("C",) * 7 + ("H",),
            coordinates=tuple(coords),
            charge=0,
            multiplicity=1,
        )
        native = ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "rings": [{"id": "r1", "atoms": [1, 2, 3, 4, 5, 6]}],
                "torsions": [
                    {
                        "id": "t1",
                        "atoms": [2, 1, 7, 8],
                        "model": "absolute_dihedral_grid",
                        "angles": [60.0, 180.0],
                    },
                ],
            }
        ).scientific_native()
        item = _item("c1:g1", "c1", [chain])
        out = ConfgenExecutor().execute(
            item, _ctx(_sci(seed=None, native=FrozenDict(native)), str(tmp_path))
        )
        assert out.status is WorkItemStatus.COMPLETED, out.diagnostics
        rows = _targets_rows(out, str(tmp_path))
        units = {row["unit"] for row in rows}
        assert units <= {"final-leaf", "internal-attempt", "deferred-range"}, units
        assert "final-leaf" in units and "internal-attempt" in units

    def test_report_points_at_unit_sections(self, tmp_path) -> None:
        import json as _json

        out, _ = _run_v3(_typed_native(seed=7, sampling={"cap": 2}), 7, str(tmp_path))
        assert out.status is WorkItemStatus.COMPLETED
        ref = {r.role: r for r in out.artifacts}[CONFGEN_REPORT_ROLE]
        with open(os.path.join(str(tmp_path), ref.locator.path)) as handle:
            payload = _json.load(handle)
        assert payload["count_units"]["leaf_certificate"].startswith("final-leaf")
        assert payload["leaf_certificate"] is not None
        assert payload["attempt_ledger"] is not None

    def test_upstream_state_chaining(self, tmp_path) -> None:
        from confflow.domain.work_item import WorkItemInputs

        first, item = _run_v3(_typed_native(), None, str(tmp_path))
        assert first.status is WorkItemStatus.COMPLETED
        # Chain onto the produced leaf itself with its real result object
        # (provenance with inherited scope intact — hand-crafted results
        # without scope fail closed by design).
        leaf = first.structures[0]
        upstream_result = first.results[0]
        assert upstream_result.subject_structure_id == leaf.id
        chained = _item("c2:g1", "c2", [leaf])
        chained = chained.__class__(
            id=chained.id,
            logical_key=chained.logical_key,
            step_id=chained.step_id,
            named_inputs=WorkItemInputs(
                structures=chained.named_inputs.structures,
                results=FrozenDict({"confgen_state": ResultSet((upstream_result,))}),
            ),
            resources=chained.resources,
            semantic_digest=chained.semantic_digest,
        )
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(_typed_native())), str(tmp_path))
        )
        assert out.status is WorkItemStatus.COMPLETED, out.diagnostics
        assert all(
            r.metadata.get("input_confgen_state") == upstream_result.result_id
            for r in out.structures
        )

    def test_ambiguous_chained_state_rejected(self, tmp_path) -> None:
        from confflow.domain.work_item import WorkItemInputs

        driving = _butane("seed-a")
        first, item = _run_v3(_typed_native(), None, str(tmp_path))
        dup = ResultSet((first.results[0], first.results[1]))
        chained = _item("c1:g1", "c1", [driving])
        chained = chained.__class__(
            id=chained.id,
            logical_key=chained.logical_key,
            step_id=chained.step_id,
            named_inputs=WorkItemInputs(
                structures=chained.named_inputs.structures,
                results=FrozenDict({"confgen_state": dup}),
            ),
            resources=chained.resources,
            semantic_digest=chained.semantic_digest,
        )
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(_typed_native())), str(tmp_path))
        )
        assert out.status is WorkItemStatus.FAILED

    def test_freeze_override_fails_closed(self, tmp_path) -> None:
        out, _ = _run_v3(
            _typed_native(),
            None,
            str(tmp_path),
            overrides=FrozenDict({"freeze": [1]}),
        )
        assert out.status is WorkItemStatus.FAILED

    def test_seed_conflict_rejected(self, tmp_path) -> None:
        out, _ = _run_v3(_typed_native(seed=7), 8, str(tmp_path))
        assert out.status is WorkItemStatus.FAILED

    def test_result_provenance_carries_certificate(self, tmp_path) -> None:
        out, _ = _run_v3(_typed_native(), None, str(tmp_path))
        assert out.status is WorkItemStatus.COMPLETED
        report = {ref.role: ref for ref in out.artifacts}[CONFGEN_REPORT_ROLE]
        with open(os.path.join(str(tmp_path), report.locator.path)) as handle:
            payload = json.load(handle)
        for result in out.results:
            assert result.provenance is not None
            assert (
                result.provenance.metadata.get("certificate_digest")
                == payload["certificate"]["digest"]
            )

    def test_subject_none_chained_rejected(self, tmp_path) -> None:
        from confflow.domain.work_item import WorkItemInputs

        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        assert first.status is WorkItemStatus.COMPLETED
        orphan = ScientificResult(
            kind=CONFGEN_STATE_KIND,
            value=dict(first.results[0].value),
            subject_structure_id=None,
            source_step_id="cg",
            source_work_item_id="wi:c1:g1",
            result_id=first.results[0].result_id,
        )
        chained = _item("c1:g1", "c1", [_butane("seed-a")])
        chained = chained.__class__(
            id=chained.id,
            logical_key=chained.logical_key,
            step_id=chained.step_id,
            named_inputs=WorkItemInputs(
                structures=chained.named_inputs.structures,
                results=FrozenDict({"confgen_state": ResultSet((orphan,))}),
            ),
            resources=chained.resources,
            semantic_digest=chained.semantic_digest,
        )
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(_typed_native())), str(tmp_path))
        )
        assert out.status is WorkItemStatus.FAILED


# ----------------------------------------------------------------------
# Chained inherited locks (critical review)
# ----------------------------------------------------------------------


class TestChainedLocks:
    def test_scope_persisted_in_provenance_not_key_or_metadata(self, tmp_path) -> None:
        out, _ = _run_v3(_abs_native(), None, str(tmp_path))
        assert out.status is WorkItemStatus.COMPLETED
        result = out.results[0]
        assert result.provenance is not None
        scope = result.provenance.metadata.get("inherited_scope")
        assert isinstance(scope, Mapping)
        assert list(scope["torsions"]["T1"]["frame"]) == [0, 1, 2, 3]
        assert scope["torsions"]["T1"]["model"] == "absolute_dihedral_grid"
        assert scope["torsions"]["T1"]["label"] == 60.0
        assert set(result.value["torsions"]) == {"T1"}  # labels only in the key
        for record in out.structures:
            assert set(record.metadata) <= {"member_index", "seed", "input_confgen_state"}

    def test_preserve_unchanged_passes_with_inherited_label(self, tmp_path) -> None:
        first, _ = _run_v3(_abs_native(), None, str(tmp_path))
        assert first.status is WorkItemStatus.COMPLETED
        leaf = first.structures[0]
        chained = _chain_item("c2:g1", "c2", leaf, first.results[0])
        out = ConfgenExecutor().execute(
            chained,
            _ctx(_sci(seed=None, native=FrozenDict(_abs_preserve_native())), str(tmp_path)),
        )
        assert out.status is WorkItemStatus.COMPLETED, out.diagnostics
        assert len(out.structures) == 1
        assert dict(out.results[0].value["torsions"]) == {"T1": 60.0}

    def test_adversarial_rotation_cannot_publish_stale(self, tmp_path) -> None:
        import dataclasses

        import numpy as np

        from confflow.science.torsion import rotate_atoms_around_bond

        first, _ = _run_v3(_abs_native(), None, str(tmp_path))
        leaf = first.structures[0]
        coords = np.asarray(leaf.coordinates, dtype=float)
        rotate_atoms_around_bond(coords, 2, 1, [0], 60.0)
        perturbed = dataclasses.replace(
            leaf,
            id="adv-0",
            coordinates=tuple((float(x), float(y), float(z)) for x, y, z in coords.tolist()),
            parent_ids=(leaf.id,),
        )
        smuggled = _resubject(first.results[0], "adv-0")
        chained = _chain_item("c2:g1", "c2", perturbed, smuggled)
        out = ConfgenExecutor().execute(
            chained,
            _ctx(_sci(seed=None, native=FrozenDict(_abs_preserve_native())), str(tmp_path)),
        )
        assert out.status is WorkItemStatus.FAILED
        assert "INHERITED_STATE_SCOPE_MISSING" in out.diagnostics[0].message
        assert not out.structures and not out.results

    def test_relative_omitted_fails(self, tmp_path) -> None:
        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        leaf = first.structures[0]
        other = ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "torsions": [
                    {
                        "id": "other",
                        "bond": [1, 2],
                        "model": "relative_rotation_grid",
                        "angles": [0.0],
                    }
                ],
            }
        ).scientific_native()
        chained = _chain_item("c2:g1", "c2", leaf, _resubject(first.results[0], leaf.id))
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(other)), str(tmp_path))
        )
        assert out.status is WorkItemStatus.FAILED
        assert "INHERITED_STATE_SCOPE_MISSING" in out.diagnostics[0].message

    @staticmethod
    def _relative_preserve_native() -> dict[str, Any]:
        # NOTE: angles repeated explicitly (CORE planner workaround, see
        # interface note); preserve axes never rotate from them.
        return ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "torsions": [
                    {
                        "id": "central",
                        "bond": [2, 3],
                        "model": "relative_rotation_grid",
                        "angles": [0.0, 120.0, 240.0],
                        "treatment": "preserve_input",
                    }
                ],
            }
        ).scientific_native()

    def test_relative_preserve_consistent_passes(self, tmp_path) -> None:
        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        leaf = first.structures[0]
        chained = _chain_item("c2:g1", "c2", leaf, first.results[0])
        out = ConfgenExecutor().execute(
            chained,
            _ctx(
                _sci(seed=None, native=FrozenDict(self._relative_preserve_native())),
                str(tmp_path),
            ),
        )
        assert out.status is WorkItemStatus.COMPLETED, out.diagnostics
        # Original relative label preserved verbatim (lock verified it).
        assert dict(out.results[0].value["torsions"]) == dict(first.results[0].value["torsions"])

    def test_relative_preserve_drifted_blocked(self, tmp_path) -> None:
        import dataclasses

        import numpy as np

        from confflow.science.torsion import rotate_atoms_around_bond

        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        leaf = first.structures[0]
        coords = np.asarray(leaf.coordinates, dtype=float)
        rotate_atoms_around_bond(coords, 2, 1, [0], 60.0)
        perturbed = dataclasses.replace(
            leaf,
            id="adv-rel-0",
            coordinates=tuple((float(x), float(y), float(z)) for x, y, z in coords.tolist()),
            parent_ids=(leaf.id,),
        )
        chained = _chain_item("c2:g1", "c2", perturbed, _resubject(first.results[0], "adv-rel-0"))
        out = ConfgenExecutor().execute(
            chained,
            _ctx(
                _sci(seed=None, native=FrozenDict(self._relative_preserve_native())),
                str(tmp_path),
            ),
        )
        assert out.status is WorkItemStatus.FAILED
        assert "INHERITED_STATE_SCOPE_MISSING" in out.diagnostics[0].message
        assert not out.structures and not out.results

    def test_relative_reenumerated_passes_fresh(self, tmp_path) -> None:
        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        leaf = first.structures[0]
        chained = _chain_item("c2:g1", "c2", leaf, first.results[0])
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(_typed_native())), str(tmp_path))
        )
        assert out.status is WorkItemStatus.COMPLETED, out.diagnostics
        assert set(out.results[0].value["torsions"]) == {"central"}
        assert float(out.results[0].value["torsions"]["central"]) in (0.0, 120.0, 240.0)

    def test_missing_scope_provenance_fails_closed(self, tmp_path) -> None:
        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        leaf = first.structures[0]
        bare = ScientificResult(
            kind=CONFGEN_STATE_KIND,
            value=dict(first.results[0].value),
            subject_structure_id=leaf.id,
            source_step_id="c1",
            source_work_item_id="wi:c1:g1",
            result_id=None,
        )
        chained = _chain_item("c2:g1", "c2", leaf, bare)
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(_typed_native())), str(tmp_path))
        )
        assert out.status is WorkItemStatus.FAILED
        assert "INHERITED_STATE_SCOPE_MISSING" in out.diagnostics[0].message

    def test_zero_axes_with_incoming_key_fails_closed(self, tmp_path) -> None:
        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        leaf = first.structures[0]
        empty = ConfgenModelV3.model_validate({"schema_version": 3}).scientific_native()
        chained = _chain_item("c2:g1", "c2", leaf, first.results[0])
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(empty)), str(tmp_path))
        )
        assert out.status is WorkItemStatus.FAILED
        assert "INHERITED_STATE_SCOPE_MISSING" in out.diagnostics[0].message

    def test_crafted_ring_and_coord_entries_audited(self, tmp_path) -> None:
        import dataclasses

        from confflow.science.confgen.model import ConfgenStateKey

        first, _ = _run_v3(_typed_native(), None, str(tmp_path))
        leaf = first.structures[0]
        base = first.results[0]
        case_key = ConfgenStateKey(
            coordination={
                "center": 46,
                "shape": "octahedral",
                "placement": [0, 1, 2, 3, 4, 5],
                "sites": {},
            },
            rings={"r1": {"template": "chair_A_6"}},
            torsions=dict(base.value["torsions"]),
        )
        meta = dict(base.provenance.metadata) if base.provenance else {}
        scope = dict(meta.get("inherited_scope", {}))
        scope["rings"] = {"r1": {"atoms": [0, 1, 2, 3], "label": {"template": "chair_A_6"}}}
        scope["coordination"] = {
            "metal_center": 46,
            "donor_atoms": [1, 2, 3, 4, 5, 6],
            "shapes": ["octahedral"],
            "label": dict(case_key.coordination),
        }
        smuggled = dataclasses.replace(
            base,
            value=case_key.to_dict(),
            subject_structure_id=leaf.id,
            provenance=dataclasses.replace(
                base.provenance, metadata=FrozenDict({**meta, "inherited_scope": scope})
            ),
        )
        # Downstream spec declares only torsions: ring + coordination locks dropped.
        chained = _chain_item("c2:g1", "c2", leaf, smuggled)
        out = ConfgenExecutor().execute(
            chained, _ctx(_sci(seed=None, native=FrozenDict(_typed_native())), str(tmp_path))
        )
        assert out.status is WorkItemStatus.FAILED
        assert "INHERITED_STATE_SCOPE_MISSING" in out.diagnostics[0].message
        # Ring-only crafted entry, coordination untouched: the dropped ring
        # lock alone fails the chain.
        ring_key = ConfgenStateKey(
            rings={"r1": {"template": "chair_A_6"}},
            torsions=dict(base.value["torsions"]),
        )
        ring_smuggled = dataclasses.replace(
            base,
            value=ring_key.to_dict(),
            subject_structure_id=leaf.id,
            provenance=dataclasses.replace(
                base.provenance, metadata=FrozenDict({**meta, "inherited_scope": scope})
            ),
        )
        out_ring = ConfgenExecutor().execute(
            _chain_item("c4:g1", "c4", leaf, ring_smuggled),
            _ctx(_sci(seed=None, native=FrozenDict(_typed_native())), str(tmp_path)),
        )
        assert out_ring.status is WorkItemStatus.FAILED
        assert "rings.r1" in out_ring.diagnostics[0].message
        # Preserve-attempt on the ring still fails: lane-owned labels need engine locks.
        ring_preserve = ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "torsions": [
                    {
                        "id": "central",
                        "bond": [2, 3],
                        "model": "relative_rotation_grid",
                        "angles": [0.0, 120.0, 240.0],
                    }
                ],
                "rings": [{"id": "r1", "atoms": [1, 2, 3, 4], "treatment": "preserve_input"}],
            }
        ).scientific_native()
        out2 = ConfgenExecutor().execute(
            _chain_item("c3:g1", "c3", leaf, smuggled),
            _ctx(_sci(seed=None, native=FrozenDict(ring_preserve)), str(tmp_path)),
        )
        assert out2.status is WorkItemStatus.FAILED
        assert "INHERITED_STATE_SCOPE_MISSING" in out2.diagnostics[0].message


# ----------------------------------------------------------------------
# Legacy regressions stay versioned
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Coordination boundary: backend/budgets/witnessed site_group + TS1 sigma
# ----------------------------------------------------------------------


class TestCoordinationBoundary:
    FIXTURE = Path("tests/fixtures/confgen/coordination/ts1")

    def test_backend_and_budget_authority_tracking(self) -> None:
        import typing

        from confflow.science.confgen.coordination.stage import BACKEND_CHOICES, CoordinationStage
        from confflow.workflow.v4.confgen_schema import (
            CoordinationBudgetModel,
            CoordinationGenerationSpec,
        )

        literal = set(
            typing.get_args(CoordinationGenerationSpec.model_fields["backend"].annotation)
        )
        assert literal == set(BACKEND_CHOICES)
        section = {
            "metal_center": 0,
            "binding_sites": [
                {"id": f"s{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1} for i in range(4)
            ],
        }
        built = CoordinationStage(section)
        assert built._backend == CoordinationGenerationSpec.model_fields["backend"].default
        assert built._max_nfev == CoordinationBudgetModel.model_fields["max_nfev"].default
        assert built._maxiter == CoordinationBudgetModel.model_fields["maxiter"].default

    def test_site_group_and_donor_forms(self) -> None:
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "coordination": {
                        "metal_center": 1,
                        "binding_sites": [{"id": f"s{i}", "atoms": [i + 2]} for i in range(4)],
                        "backend": "quantum",
                    },
                }
            )
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "coordination": {
                        "metal_center": 1,
                        "binding_sites": [{"id": f"s{i}", "atoms": [i + 2]} for i in range(4)],
                        "site_group": {"generators": [[0, 0, 1, 2]]},
                    },
                }
            )
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(
                {
                    "schema_version": 3,
                    "coordination": {
                        "metal_center": 1,
                        "binding_sites": [{"id": f"s{i}", "atoms": [i + 2]} for i in range(4)],
                        "site_group": {"generators": [[1, 0, 3, 2]], "molecular_symmetry": True},
                    },
                }
            )
        valid = ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "coordination": {
                    "metal_center": 1,
                    "binding_sites": [{"id": f"s{i}", "atoms": [i + 2]} for i in range(4)],
                    "donor_configuration": ["s0", "s1", "s2", "s3"],
                    "site_group": {
                        "generators": [[1, 0, 3, 2]],
                        "provenance": "fixture-sigma",
                    },
                },
            }
        )
        assert valid.coordination is not None
        assert valid.coordination.site_group is not None
        assert valid.coordination.site_group.scope == "declared_topological_subgroup"

    def test_ts1_sigma_workflow_boundary(self) -> None:
        from collections.abc import Iterator

        from confflow.domain.structure import StructureRecord
        from confflow.science.confgen import model as core_model
        from confflow.science.confgen.coordination.stage import CoordinationStage
        from confflow.science.confgen.graph import load_typed_topology, load_xyz_frame

        fixture = self.FIXTURE
        graph, spec0, raw = load_typed_topology(fixture / "topology" / "typed_topology.json")
        elements, xyz = load_xyz_frame(fixture / "structures" / "ts1_original.xyz")
        structure = StructureRecord(
            id="ts1",
            atoms=tuple(elements),
            coordinates=tuple(tuple(point) for point in xyz),
            charge=0,
            multiplicity=1,
        )
        seen: set[tuple[int, int, str]] = set()
        topo_bonds: list[dict[str, Any]] = []
        for entry in raw["edges"]:
            key = (entry["a_1based"], entry["b_1based"], entry["type"])
            if key in seen:
                continue
            seen.add(key)
            topo_bonds.append(
                {"atoms": [entry["a_1based"], entry["b_1based"]], "kind": entry["type"]}
            )
        for rel in raw["reaction_relations"]:
            key = (rel["a_1based"], rel["b_1based"], "FORMING")
            if key in seen:
                continue
            seen.add(key)
            topo_bonds.append({"atoms": [rel["a_1based"], rel["b_1based"]], "kind": "FORMING"})
        assert len(topo_bonds) == 131
        witness = json.loads((fixture / "benchmark" / "expected_sigma_witness.json").read_text())
        benchmark = json.loads(
            (fixture / "benchmark" / "expected_coordination_benchmark.json").read_text()
        )
        assert witness["verified_properties"]["involutive"] is True
        assert witness["verified_properties"]["typed_edge_preserving"] is True
        # The supplied witness is full-atom (122 mappings) and explicitly NOT
        # a suppression or stereo authority; the test honors exactly that.
        assert sorted(witness["mapping"], key=int) == [str(i) for i in range(1, 123)]
        assert "suppress" in witness["usage"] and "must not" in witness["usage"]
        sigma_perm = benchmark["sigma_site_permutation_0based"]
        assert sorted(sigma_perm) == [0, 1, 2, 3, 4, 5]
        constraints = json.loads(
            (fixture / "benchmark" / "coordination_constraints.json").read_text()
        )["constraints"]
        confgen_block: dict[str, Any] = {
            "schema_version": 3,
            "index_base": 1,
            "topology": {"bonds": topo_bonds},
            "coordination": {
                "metal_center": 47,
                "binding_sites": [
                    {
                        "id": site.id,
                        "kind": "atom",
                        "atoms": [a + 1 for a in site.atoms],
                        "hapticity": 1,
                    }
                    for site in spec0.binding_sites
                ],
                "shapes": ["octahedral"],
                "treatment": "enumerate",
                "constraints": [
                    {
                        "id": entry["id"],
                        "kind": "FORBIDDEN_TRANS",
                        "sites": entry["sites"],
                        "classification": entry["classification"],
                        "provenance": entry["source"],
                    }
                    for entry in constraints
                ],
                "donor_configuration": list(spec0.site_ids),
                "backend": "rigid",
                "budgets": {"max_nfev": 120, "maxiter": 400},
                "site_group": {
                    "generators": [sigma_perm],
                    "provenance": str(fixture / "benchmark" / "expected_sigma_witness.json"),
                },
            },
        }
        doc = _v3_doc(confgen_block)
        parsed = parse_workflow_document(doc)
        assert parsed.definition is not None, [str(d) for d in parsed.diagnostics]
        compiled = compile_workflow(parsed)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        native = ConfgenModelV3.model_validate(doc["steps"][0]["confgen"]).scientific_native()
        context = core_model.build_context(structure, native)
        assert context.graph.edge_set() == graph.edge_set()
        stage = CoordinationStage(dict(context.resolved_spec))
        parent = core_model.WorkingRealization(
            structure=structure, state_key=core_model.ConfgenStateKey()
        )
        estimate = stage.estimate(parent, context)
        assert estimate.exact is True
        # 30 shape classes -> 12 after the 4 declared policy exclusions.
        assert estimate.declared_count == 12
        assert estimate.upper_bound == 720
        certificate = estimate.details["certificate"]
        assert certificate["excluded_summary"]
        pipes = certificate["site_group"]
        orbits = [orbit for pipe in pipes for orbit in pipe["molecular_orbits"]]
        assert len(orbits) == 6
        expected = sorted(sorted(orbit["members"]) for orbit in benchmark["sigma_orbits"])
        assert sorted(sorted(orbit["members"]) for orbit in orbits) == expected
        enumerated = stage.enumerate_targets(parent, context)
        assert isinstance(enumerated, Iterator) and not isinstance(enumerated, (tuple, list))
        targets = list(enumerated)
        assert [t.ordinal for t in targets] == list(range(12))
        for target in targets:
            key = dict(target.state_value)
            assert set(key) == {"center", "shape", "placement", "sites"}
            assert key["center"] == 46
            assert key["shape"] == "octahedral"


# ----------------------------------------------------------------------
# Producer + workflow wiring
# ----------------------------------------------------------------------


class TestProducerWiring:
    def test_contract_confgen_section_tracks_registries(self) -> None:
        from confflow.producer.contract import build_configuration_contract_v4
        from confflow.science.confgen.coordination.stage import BACKEND_CHOICES

        envelope = build_configuration_contract_v4(producer_version="0.0-test")
        section = envelope["confgen"]
        assert section["schema_version"] == 3
        assert "octahedral" in section["coordination_shapes"]
        assert section["coordination_shapes_by_cn"]["6"] == [
            "octahedral",
            "trigonal_prismatic",
        ]
        assert "chair_A_6" in section["ring_templates_by_size"]["6"]
        assert "chemical" in section["torsion_models"]
        assert section["coordination_backends"] == list(BACKEND_CHOICES)
        assert section["coordination_budgets"] == {"max_nfev": 120, "maxiter": 400}
        assert section["coordination_site_group_scope"] == "declared_topological_subgroup"
        assert section["result_provenance"] == ["certificate_digest", "inherited_scope"]
        assert section["report_provenance"] == [
            "/certificate/digest",
            "/enumeration/digest",
            "/input_state_digest",
            "/input_certificate_digest",
        ]

    def test_manifest_v3_fields_resolve(self) -> None:
        from confflow.producer.manifest import build_editor_manifest_v4

        manifest = build_editor_manifest_v4()
        ids = {field["field_id"] for field in manifest["fields"]}
        assert "confgen.v3.torsions" in ids
        assert "confgen.native" not in ids  # the legacy native path is retired

    def test_confgen_recipe_compiles(self) -> None:
        from confflow.producer.recipes import get_recipe_v4

        recipe = get_recipe_v4("confgen_torsion")
        assert recipe["required_fields"] == ["confgen.v3.torsions"]
        compiled = compile_workflow(recipe["document"])
        assert compiled.ok, [str(d) for d in compiled.diagnostics]

    def test_recipe_order_root_decision(self) -> None:
        from confflow.producer.recipes import RECIPE_IDS_V4, build_recipe_catalog_v4

        catalog = {recipe["id"]: recipe for recipe in build_recipe_catalog_v4()["recipes"]}
        # R2.2: tspes retired; the frozen orders of the rest hold, new card sorts last.
        # N4: ensemble_refine appended after monomer_conformers (order 140).
        assert catalog["confgen_torsion"]["order"] == 120
        assert catalog["monomer_conformers"]["order"] == 130
        assert catalog["ensemble_refine"]["order"] == 140
        assert list(RECIPE_IDS_V4).index("ensemble_refine") == len(RECIPE_IDS_V4) - 1
        assert list(RECIPE_IDS_V4).index("monomer_conformers") == len(RECIPE_IDS_V4) - 2
        assert list(RECIPE_IDS_V4).index("confgen_torsion") == len(RECIPE_IDS_V4) - 3
        assert catalog["ensemble_refine"]["order"] > max(
            order
            for recipe_id, order in ((r["id"], r["order"]) for r in catalog.values())
            if recipe_id != "ensemble_refine"
        )

    def test_workflow_chaining_compiles_and_assembles(self) -> None:
        doc = {
            "schema": "confflow.workflow.v4",
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
            "steps": [
                {
                    "id": "cg1",
                    "executor": "confgen",
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                    "confgen": {"schema_version": 3, **_typed_native()},
                },
                {
                    "id": "cg2",
                    "executor": "confgen",
                    "bindings": {
                        "structure": {"source": {"step": "cg1", "port": "structures"}},
                        "confgen_state": {
                            "source": {"step": "cg1", "port": "results"},
                            "pairing": "by_subject",
                            "cardinality": "optional",
                        },
                    },
                    "confgen": {"schema_version": 3, **_typed_native()},
                },
            ],
        }
        parsed = parse_workflow_document(doc)
        assert parsed.definition is not None, [str(d) for d in parsed.diagnostics]
        compiled = compile_workflow(parsed)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        assert compiled.plan is not None
        seed = _butane("seed-a")
        first_out, _ = _run_v3(_typed_native(), None, "/tmp")
        assert first_out.status is WorkItemStatus.COMPLETED
        assembly = assemble_work_items(
            compiled.plan,
            RunInputs(
                structures=FrozenDict({"structures": StructureSet((seed,))}),
            ),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        "cg1": StepOutputs(
                            step_id="cg1",
                            structures=first_out.structures,
                            results=first_out.results,
                            artifacts=ArtifactSet(),
                            status=StepStatus.COMPLETED,
                        )
                    }
                )
            ),
        )
        assert assembly.ok, [str(d) for d in assembly.diagnostics]
        chained = assembly.for_step("cg2")
        assert chained, "chained confgen step assembled no items"
        for item in chained:
            assert "confgen_state" in item.named_inputs.results
