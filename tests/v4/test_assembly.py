#!/usr/bin/env python3

"""V4 synthetic work-item assembly.

Pins the four required scenarios plus identity/digest behaviour:

- linear ``N -> N`` structure fan-out;
- checkpoint matching by ``subject_structure_id`` (never order or filename);
- missing required checkpoint diagnostics naming step, logical key, and port;
- named multi-structure pairing only with explicit group keys;
- reuse identity: content-stable digests, locator-insensitive artifacts,
  scheduler-inert and execution-inert digests.
"""

from __future__ import annotations

from confflow.domain import (
    ArtifactRef,
    ArtifactSet,
    Cardinality,
    FrozenDict,
    Pairing,
    PortKind,
    ResultSet,
    StructureSet,
)
from confflow.domain.artifact import ArtifactLocator
from confflow.execution import (
    ExecutorCapability,
    PortSpec,
    build_default_registry,
)
from confflow.workflow.v4 import (
    MaterializedOutputs,
    StepOutputs,
    assemble_work_items,
)
from tests.v4._builders import (
    calc_step,
    checkpoint,
    checkpoint_set,
    compile_doc,
    energy_result,
    run_inputs,
    structure,
    structure_set,
    v4_doc,
)

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}
CHECKPOINT_INPUTS = {
    "structures": {"kind": "structure", "cardinality": "many"},
    "checkpoints": {"kind": "artifact", "cardinality": "many", "role": "checkpoint"},
}


def _calc_doc(**kwargs: object) -> dict:
    return v4_doc(
        [
            calc_step(
                "s_opt",
                bindings={"structure": {"source": {"run": "structures"}}},
                **kwargs,
            )
        ],
        inputs=STRUCTURE_INPUTS,
    )


def _compile(document: dict, registry: object = None):
    result = compile_doc(document, registry=registry)
    assert result.ok, [(d.code, d.details.get("reason"), d.message) for d in result.errors]
    return result.plan


def _seed_set() -> object:
    return structure_set("s0", "s1", "s2")


class TestLinearFanOut:
    """Scenario A: run.structures [A, B, C] -> 3 deterministic items."""

    def test_three_structures_produce_three_items(self) -> None:
        plan = _compile(_calc_doc())
        assembly = assemble_work_items(plan, run_inputs(structures={"structures": _seed_set()}))
        assert assembly.ok
        assert [item.logical_key for item in assembly.items] == [
            "s_opt:s0",
            "s_opt:s1",
            "s_opt:s2",
        ]
        assert [item.id for item in assembly.items] == [
            "wi:s_opt:s0",
            "wi:s_opt:s1",
            "wi:s_opt:s2",
        ]
        assert [item.ordinal for item in assembly.items] == [0, 1, 2]
        for item in assembly.items:
            assert item.named_inputs.structures["structure"].ids == (
                item.logical_key.split(":")[1],
            )
            assert item.resources.cores_per_item is not None
            assert item.resources.memory_per_item_bytes is not None

    def test_assembly_is_deterministic(self) -> None:
        plan = _compile(_calc_doc())
        first = assemble_work_items(plan, run_inputs(structures={"structures": _seed_set()}))
        second = assemble_work_items(plan, run_inputs(structures={"structures": _seed_set()}))
        assert [item.to_dict() for item in first.items] == [item.to_dict() for item in second.items]

    def test_missing_required_run_input(self) -> None:
        plan = _compile(_calc_doc())
        assembly = assemble_work_items(plan, run_inputs())
        assert not assembly.ok
        assert "run_input_missing" in assembly_reasons(assembly)

    def test_unexpected_run_input_warns(self) -> None:
        plan = _compile(_calc_doc())
        assembly = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": _seed_set()},
                artifacts={"extra": checkpoint_set("s0")},
            ),
        )
        assert assembly.ok
        assert "run_input_unexpected" in [str(d.details.get("reason")) for d in assembly.warnings]


def assembly_reasons(assembly: object) -> list[str]:
    """Return assembly diagnostic reasons."""
    return [str(d.details.get("reason")) for d in assembly.diagnostics]


class TestCheckpointMatching:
    """Scenario B/C: subject matching, order independence, fail closed."""

    def _doc(self, *, cardinality: str | None = "one") -> dict:
        checkpoint_binding: dict = {
            "source": {"run": "checkpoints"},
            "pairing": "by_subject",
        }
        if cardinality is not None:
            checkpoint_binding["cardinality"] = cardinality
        return v4_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={
                        "structure": {"source": {"run": "structures"}},
                        "checkpoint": checkpoint_binding,
                    },
                )
            ],
            inputs=CHECKPOINT_INPUTS,
        )

    def test_checkpoint_binds_by_subject_not_order(self) -> None:
        plan = _compile(self._doc())
        structures = structure_set("s0", "s1", "s2")
        shuffled = checkpoint_set("s2", "s0", "s1")
        assembly = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": structures},
                artifacts={"checkpoints": shuffled},
            ),
        )
        assert assembly.ok
        for item in assembly.items:
            subject_id = item.logical_key.split(":")[1]
            bound = item.named_inputs.artifacts["checkpoint"]
            assert len(bound) == 1
            assert bound[0].subject_structure_id == subject_id
            assert bound[0].id == f"chk_{subject_id}"

    def test_missing_required_checkpoint_is_a_diagnostic(self) -> None:
        plan = _compile(self._doc())
        structures = structure_set("s0", "s1", "s2")
        incomplete = checkpoint_set("s0", "s2")
        assembly = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": structures},
                artifacts={"checkpoints": incomplete},
            ),
        )
        assert not assembly.ok
        assert assembly.items == ()
        errors = assembly.errors
        assert len(errors) == 1
        diagnostic = errors[0]
        assert diagnostic.code == "cardinality_error"
        assert diagnostic.details.get("reason") == "artifact_subject_missing"
        assert diagnostic.step_id == "s_freq"
        assert diagnostic.logical_key == "s_freq:s1"
        assert diagnostic.details.get("port") == "checkpoint"
        assert diagnostic.details.get("subject_structure_id") == "s1"

    def test_optional_checkpoint_absence_is_allowed(self) -> None:
        plan = _compile(self._doc(cardinality=None))
        structures = structure_set("s0", "s1")
        assembly = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": structures},
                artifacts={"checkpoints": checkpoint_set("s0")},
            ),
        )
        assert assembly.ok
        matching = {item.logical_key: item for item in assembly.items}
        assert len(matching["s_freq:s0"].named_inputs.artifacts["checkpoint"]) == 1
        assert len(matching["s_freq:s1"].named_inputs.artifacts["checkpoint"]) == 0

    def test_duplicate_checkpoints_for_subject_rejected_by_cardinality(self) -> None:
        plan = _compile(self._doc())
        structures = structure_set("s0")
        duplicates = checkpoint_set("s0") + checkpoint_set("s0", artifact_id="chk_s0_b")
        assembly = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": structures},
                artifacts={"checkpoints": duplicates},
            ),
        )
        assert not assembly.ok
        assert "cardinality_mismatch" in assembly_reasons(assembly)


class TestNamedInputPairing:
    """Scenario D: named structures require explicit pairing keys."""

    def _doc(self) -> dict:
        return v4_doc(
            [
                calc_step(
                    "s_ts",
                    adapter="named_structures",
                    bindings={
                        "reactant": {
                            "source": {"run": "reactants"},
                            "pairing": "by_group_key",
                        },
                        "product": {
                            "source": {"run": "products"},
                            "pairing": "by_group_key",
                        },
                    },
                )
            ],
            inputs={
                "reactants": {"kind": "structure", "cardinality": "many"},
                "products": {"kind": "structure", "cardinality": "many"},
            },
        )

    def test_without_group_keys_pairing_is_rejected(self) -> None:
        plan = _compile(self._doc())
        reactants = _structure_set_with_keys(["R1", "R2"], kind="methane")
        products = _structure_set_with_keys(["P1", "P2"], kind="methane", offset=0.05)
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"reactants": reactants, "products": products}),
        )
        assert not assembly.ok
        assert assembly.items == ()
        assert "pairing_undefined" in assembly_reasons(assembly)

    def test_with_group_keys_materializes_pairs(self) -> None:
        plan = _compile(self._doc())
        reactants = _structure_set_with_keys(
            ["R1", "R2"], kind="methane", group_keys={"R1": "g1", "R2": "g2"}
        )
        products = _structure_set_with_keys(
            ["P1", "P2"],
            kind="methane",
            offset=0.05,
            group_keys={"P1": "g1", "P2": "g2"},
        )
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"reactants": reactants, "products": products}),
        )
        assert assembly.ok, assembly_reasons(assembly)
        assert [item.logical_key for item in assembly.items] == ["s_ts:g1", "s_ts:g2"]
        first = assembly.items[0]
        assert first.named_inputs.structures["reactant"].ids == ("R1",)
        assert first.named_inputs.structures["product"].ids == ("P1",)
        assert first.named_inputs.structures["reactant"].ids != (
            first.named_inputs.structures["product"].ids
        )

    def test_atom_count_mismatch_rejected(self) -> None:
        plan = _compile(self._doc())
        reactants = _structure_set_with_keys(["R1"], kind="methane", group_keys={"R1": "g1"})
        products = _structure_set_with_keys(["P1"], kind="water", group_keys={"P1": "g1"})
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"reactants": reactants, "products": products}),
        )
        assert not assembly.ok
        assert "atom_count_mismatch" in assembly_reasons(assembly)

    def test_element_order_mismatch_rejected(self) -> None:
        plan = _compile(self._doc())
        reactant = structure("R1", kind="methane", group_key="g1")
        reordered = type(reactant)(
            id="P1",
            atoms=tuple(reversed(reactant.atoms)),
            coordinates=tuple(reversed(reactant.coordinates)),
            group_key="g1",
        )
        assembly = assemble_work_items(
            plan,
            run_inputs(
                structures={
                    "reactants": StructureSet.of(reactant),
                    "products": StructureSet.of(reordered),
                }
            ),
        )
        assert not assembly.ok
        assert "element_mismatch" in assembly_reasons(assembly)

    def test_two_per_structure_drivers_rejected(self) -> None:
        registry = build_default_registry()
        base = registry.adapter("named_structures")
        registry.register_adapter(
            type(base)(
                name="two_drivers",
                contract_version="test.contract.adapter.two_drivers.v1",
                capability=ExecutorCapability.CALCULATION,
                input_ports=(
                    PortSpec(
                        "reactant",
                        PortKind.STRUCTURE,
                        Cardinality.ONE,
                        Pairing.PER_STRUCTURE,
                    ),
                    PortSpec(
                        "product",
                        PortKind.STRUCTURE,
                        Cardinality.ONE,
                        Pairing.PER_STRUCTURE,
                    ),
                ),
            )
        )
        doc = self._doc()
        doc["steps"][0]["calculation"]["execution_adapter"] = "two_drivers"
        plan = _compile(doc, registry=registry)
        assembly = assemble_work_items(
            plan,
            run_inputs(
                structures={
                    "reactants": structure_set("R1"),
                    "products": structure_set("P1"),
                }
            ),
        )
        assert not assembly.ok
        assert "pairing_undefined" in assembly_reasons(assembly)


def _structure_set_with_keys(
    structure_ids: list[str],
    *,
    kind: str = "water",
    offset: float = 0.0,
    group_keys: dict[str, str] | None = None,
) -> StructureSet:
    """Build a structure set with optional per-id group keys."""
    keys = group_keys or {}
    return StructureSet.of(
        *(
            structure(
                structure_id,
                kind=kind,
                offset=offset,
                group_key=keys.get(structure_id),
            )
            for structure_id in structure_ids
        )
    )


class TestMaterializedChains:
    """Downstream steps assemble from materialized producer outputs."""

    def test_downstream_subject_matching_from_materialized_outputs(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={
                        "structure": {"source": {"step": "s_opt", "port": "structures"}},
                        "checkpoint": {
                            "source": {
                                "step": "s_opt",
                                "port": "artifacts",
                                "select": {"role": "checkpoint"},
                            },
                            "pairing": "by_subject",
                            "cardinality": "one",
                        },
                    },
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        plan = _compile(doc)
        structures = structure_set("s0", "s1")
        checkpoints = checkpoint_set("s0", "s1", producer_step_id="s_opt")
        assembly = assemble_work_items(
            plan,
            run_inputs(structures={"structures": structures}),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        "s_opt": StepOutputs(
                            step_id="s_opt",
                            structures=structures,
                            artifacts=checkpoints,
                        )
                    }
                )
            ),
        )
        assert assembly.ok, assembly_reasons(assembly)
        freq_items = assembly.for_step("s_freq")
        assert [item.logical_key for item in freq_items] == ["s_freq:s0", "s_freq:s1"]
        for item in freq_items:
            subject_id = item.logical_key.split(":")[1]
            assert item.named_inputs.artifacts["checkpoint"][0].subject_structure_id == (subject_id)

    def test_unmaterialized_producer_is_skipped_not_guessed(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        plan = _compile(doc)
        assembly = assemble_work_items(
            plan, run_inputs(structures={"structures": structure_set("s0")})
        )
        assert assembly.ok
        assert assembly.skipped_step_ids == ("s_freq",)
        assert [item.step_id for item in assembly.items] == ["s_opt"]
        assert "not_assemblable" in assembly_reasons(assembly)

    def test_ids_selector_selects_with_canonical_item_order(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={
                        "structure": {
                            "source": {
                                "run": "structures",
                                "select": {"ids": ["s2", "s0"]},
                            }
                        }
                    },
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        plan = _compile(doc)
        assembly = assemble_work_items(plan, run_inputs(structures={"structures": _seed_set()}))
        assert assembly.ok, assembly_reasons(assembly)
        # Selector order chooses membership; item order stays canonical.
        assert [item.logical_key for item in assembly.items] == ["s_opt:s0", "s_opt:s2"]

    def test_ids_selector_missing_id_is_a_diagnostic(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={
                        "structure": {
                            "source": {
                                "run": "structures",
                                "select": {"ids": ["s9"]},
                            }
                        }
                    },
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        plan = _compile(doc)
        assembly = assemble_work_items(plan, run_inputs(structures={"structures": _seed_set()}))
        assert not assembly.ok
        assert "cardinality_mismatch" in assembly_reasons(assembly)


class TestScientificResolution:
    """Effective scientific parameters flow into item identity."""

    def test_charge_override_changes_item_digest_and_warns(self) -> None:
        base_plan = _compile(_calc_doc())
        override_plan = _compile(_calc_doc(overrides={"charge": 1, "multiplicity": 2}))
        inputs = run_inputs(structures={"structures": structure_set("s0")})
        base = assemble_work_items(base_plan, inputs)
        overridden = assemble_work_items(override_plan, inputs)
        assert base.ok and overridden.ok
        assert base.items[0].semantic_digest != overridden.items[0].semantic_digest
        assert "structure_value_overridden" in [
            str(d.details.get("reason")) for d in overridden.warnings
        ]

    def test_electron_parity_error_blocks_assembly(self) -> None:
        plan = _compile(_calc_doc())
        odd = StructureSet.of(structure("s0", charge=0, multiplicity=2))
        assembly = assemble_work_items(plan, run_inputs(structures={"structures": odd}))
        assert not assembly.ok
        assert "electron_parity_mismatch" in assembly_reasons(assembly)

    def test_identical_content_keeps_reuse_digest_across_entity_ids(self) -> None:
        plan = _compile(_calc_doc())
        first = assemble_work_items(
            plan, run_inputs(structures={"structures": structure_set("a0")})
        )
        second = assemble_work_items(
            plan, run_inputs(structures={"structures": structure_set("b0")})
        )
        assert first.ok and second.ok
        assert first.items[0].logical_key != second.items[0].logical_key
        assert first.items[0].semantic_digest == second.items[0].semantic_digest


class TestDigestInputs:
    """What does and does not move a work-item digest."""

    def _item(self, **calc_kwargs: object):
        plan = _compile(_calc_doc(**calc_kwargs))
        assembly = assemble_work_items(
            plan, run_inputs(structures={"structures": structure_set("s0")})
        )
        assert assembly.ok, assembly_reasons(assembly)
        return assembly.items[0]

    def test_memory_and_cores_move_digest(self) -> None:
        baseline = self._item()
        more_memory = self._item(resources={"cores_per_item": 1, "memory_per_item": "32GiB"})
        more_cores = self._item(resources={"cores_per_item": 4, "memory_per_item": "1GiB"})
        assert baseline.semantic_digest != more_memory.semantic_digest
        assert baseline.semantic_digest != more_cores.semantic_digest

    def test_scheduler_width_does_not_move_digest(self) -> None:
        baseline = self._item()
        wide = self._item(scheduler={"max_parallel_items": 8})
        assert baseline.semantic_digest == wide.semantic_digest

    def test_execution_binding_does_not_move_digest(self) -> None:
        baseline = self._item()
        bound = self._item(
            execution={
                "binding_id": "local",
                "executable": "/opt/g16/g16",
                "target": "node-a",
                "walltime_seconds": 3600,
            }
        )
        assert baseline.semantic_digest == bound.semantic_digest
        assert bound.execution_binding_id == "local"

    def test_artifact_locator_move_does_not_move_digest(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_freq",
                    bindings={
                        "structure": {"source": {"run": "structures"}},
                        "checkpoint": {
                            "source": {"run": "checkpoints"},
                            "pairing": "by_subject",
                        },
                    },
                )
            ],
            inputs=CHECKPOINT_INPUTS,
        )
        plan = _compile(doc)
        structures = structure_set("s0")
        original = checkpoint("s0", checksum_seed="a")
        moved = ArtifactRef(
            id=original.id,
            role=original.role,
            locator=ArtifactLocator.run_relative("moved/elsewhere.chk"),
            checksum=original.checksum,
            subject_structure_id=original.subject_structure_id,
        )
        first = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": structures},
                artifacts={"checkpoints": ArtifactSet.of(original)},
            ),
        )
        second = assemble_work_items(
            plan,
            run_inputs(
                structures={"structures": structures},
                artifacts={"checkpoints": ArtifactSet.of(moved)},
            ),
        )
        assert first.ok and second.ok
        assert first.items[0].semantic_digest == second.items[0].semantic_digest

    def test_native_change_moves_digest(self) -> None:
        baseline = self._item()
        changed = self._item(native={"keyword": "M06-2X/6-31G* opt"})
        assert baseline.semantic_digest != changed.semantic_digest

    def test_result_content_feeds_downstream_digest(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_sp",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                ),
                {
                    "id": "s_report",
                    "executor": "analysis",
                    "bindings": {
                        "results": {
                            "source": {"step": "s_opt", "port": "results"},
                            "pairing": "single",
                        }
                    },
                    "analysis": {"native": {}},
                },
            ],
            inputs=STRUCTURE_INPUTS,
        )
        plan = _compile(doc)
        structures = structure_set("s0")
        first_results = ResultSet.of(
            energy_result(-76.4, subject_structure_id="s0", source_step_id="s_opt")
        )
        second_results = ResultSet.of(
            energy_result(-76.5, subject_structure_id="s0", source_step_id="s_opt")
        )
        first = assemble_work_items(
            plan,
            run_inputs(structures={"structures": structures}),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        "s_opt": StepOutputs(
                            step_id="s_opt",
                            structures=structures,
                            results=first_results,
                        )
                    }
                )
            ),
        )
        second = assemble_work_items(
            plan,
            run_inputs(structures={"structures": structures}),
            materialized=MaterializedOutputs(
                steps=FrozenDict(
                    {
                        "s_opt": StepOutputs(
                            step_id="s_opt",
                            structures=structures,
                            results=second_results,
                        )
                    }
                )
            ),
        )
        assert first.ok and second.ok
        first_report = first.for_step("s_report")[0]
        second_report = second.for_step("s_report")[0]
        assert first_report.semantic_digest != second_report.semantic_digest
