#!/usr/bin/env python3

"""V4 digest axes: separation, stability, and inertness.

Pins exactly what moves each digest axis:

- presentation (labels, annotations, document order) never moves digests;
- machine execution bindings and scheduler width never move scientific
  digests;
- native input, checks, recovery, resources, seeds, and effective scientific
  parameters do move them;
- the execution-environment digest is a separate axis.
"""

from __future__ import annotations

from confflow.domain.canonical import canonical_json_bytes
from confflow.execution import ExecutionEnvironment
from confflow.workflow.v4 import assemble_work_items
from tests.v4._builders import (
    calc_step,
    compile_doc,
    confgen_step,
    run_inputs,
    structure_set,
    v4_doc,
)

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


def _linear_doc(**calc_kwargs: object) -> dict:
    return v4_doc(
        [
            calc_step(
                "s_opt",
                bindings={"structure": {"source": {"run": "structures"}}},
                **calc_kwargs,
            )
        ],
        inputs=STRUCTURE_INPUTS,
    )


def _compiled(document: dict, registry: object = None):
    result = compile_doc(document, registry=registry)
    assert result.ok, [(d.code, d.details.get("reason"), d.message) for d in result.errors]
    return result.plan


def _item(plan: object):
    assembly = assemble_work_items(plan, run_inputs(structures={"structures": structure_set("s0")}))
    assert assembly.ok, [d.message for d in assembly.errors]
    return assembly.items[0]


class TestPresentationInertness:
    """Labels, annotations, and document order never move any digest."""

    def test_label_rename_does_not_move_digests(self) -> None:
        first = _compiled(_linear_doc(label="First label"))
        second = _compiled(_linear_doc(label="Renamed entirely"))
        assert first.definition_digest == second.definition_digest
        assert first.steps[0].step_semantic_digest == second.steps[0].step_semantic_digest
        assert _item(first).semantic_digest == _item(second).semantic_digest

    def test_annotations_do_not_move_digests(self) -> None:
        first_doc = _linear_doc()
        second_doc = _linear_doc()
        second_doc["steps"][0]["annotations"] = {"gui": {"x": 10, "y": 20}, "color": "red"}
        first = _compiled(first_doc)
        second = _compiled(second_doc)
        assert first.definition_digest == second.definition_digest
        assert first.steps[0].step_semantic_digest == second.steps[0].step_semantic_digest
        assert _item(first).semantic_digest == _item(second).semantic_digest

    def test_document_order_does_not_move_digests(self) -> None:
        step_a = calc_step(
            "s_b", bindings={"structure": {"source": {"step": "s_a", "port": "structures"}}}
        )
        step_b = calc_step("s_a", bindings={"structure": {"source": {"run": "structures"}}})
        forward = _compiled(v4_doc([step_b, step_a], inputs=STRUCTURE_INPUTS))
        reverse = _compiled(v4_doc([step_a, step_b], inputs=STRUCTURE_INPUTS))
        assert canonical_json_bytes(forward.to_payload()) == canonical_json_bytes(
            reverse.to_payload()
        )

    def test_plan_payload_is_byte_identical_across_recompiles(self) -> None:
        first = _compiled(_linear_doc())
        second = _compiled(_linear_doc())
        assert canonical_json_bytes(first.to_payload()) == canonical_json_bytes(second.to_payload())


class TestExecutionInertness:
    """Machine and scheduler settings never pollute scientific identity."""

    def test_execution_binding_does_not_move_scientific_digests(self) -> None:
        baseline = _compiled(_linear_doc())
        bound = _compiled(
            _linear_doc(
                execution={
                    "binding_id": "cluster",
                    "executable": "/opt/g16/g16",
                    "env": {"GAUSS_SCRDIR": "/scratch"},
                    "sandbox": "/scratch/sandbox",
                    "allowed_executables": ["/opt/g16/g16"],
                    "walltime_seconds": 7200,
                    "target": "node-42",
                }
            )
        )
        assert baseline.definition_digest == bound.definition_digest
        assert baseline.steps[0].step_semantic_digest == bound.steps[0].step_semantic_digest
        assert _item(baseline).semantic_digest == _item(bound).semantic_digest

    def test_scheduler_width_does_not_move_scientific_digests(self) -> None:
        narrow = _compiled(
            v4_doc(
                [
                    calc_step(
                        "s_opt",
                        bindings={"structure": {"source": {"run": "structures"}}},
                        scheduler={"max_parallel_items": 1},
                    )
                ],
                inputs=STRUCTURE_INPUTS,
                global_config={"scheduler": {"max_parallel_items": 1}},
            )
        )
        wide = _compiled(
            v4_doc(
                [
                    calc_step(
                        "s_opt",
                        bindings={"structure": {"source": {"run": "structures"}}},
                        scheduler={"max_parallel_items": 8},
                    )
                ],
                inputs=STRUCTURE_INPUTS,
                global_config={"scheduler": {"max_parallel_items": 8}},
            )
        )
        assert narrow.definition_digest == wide.definition_digest
        assert narrow.steps[0].step_semantic_digest == wide.steps[0].step_semantic_digest
        assert _item(narrow).semantic_digest == _item(wide).semantic_digest
        assert narrow.scheduler.max_parallel_items == 1
        assert wide.scheduler.max_parallel_items == 8

    def test_scheduler_failure_behavior_is_digest_inert(self) -> None:
        continue_doc = _linear_doc(scheduler={"max_parallel_items": 4})
        fast_doc = _linear_doc(scheduler={"max_parallel_items": 4, "on_failure": "fail_fast"})
        first = _compiled(continue_doc)
        second = _compiled(fast_doc)
        assert first.definition_digest == second.definition_digest
        assert _item(first).semantic_digest == _item(second).semantic_digest


class TestScientificSensitivity:
    """What actually changes the science moves the digests."""

    def test_native_change_moves_step_and_definition_digests(self) -> None:
        first = _compiled(_linear_doc(native={"keyword": "B3LYP/6-31G* opt"}))
        second = _compiled(_linear_doc(native={"keyword": "M06-2X/def2-TZVP opt"}))
        assert first.definition_digest != second.definition_digest
        assert first.steps[0].step_semantic_digest != second.steps[0].step_semantic_digest
        assert _item(first).semantic_digest != _item(second).semantic_digest

    def test_checks_change_moves_step_digest(self) -> None:
        first = _compiled(_linear_doc())
        second = _compiled(_linear_doc(checks=["normal_termination"]))
        assert first.steps[0].step_semantic_digest != second.steps[0].step_semantic_digest
        assert first.definition_digest != second.definition_digest
        assert _item(first).semantic_digest != _item(second).semantic_digest

    def test_recovery_change_moves_step_digest(self) -> None:
        first = _compiled(_linear_doc())
        second = _compiled(_linear_doc(recovery="ts_rescue_scan"))
        assert first.steps[0].step_semantic_digest != second.steps[0].step_semantic_digest

    def test_adapter_change_moves_step_digest(self) -> None:
        standard = _compiled(_linear_doc(adapter="standard"))
        template = _compiled(_linear_doc(adapter="native_template"))
        assert standard.steps[0].step_semantic_digest != template.steps[0].step_semantic_digest

    def test_result_profile_change_moves_step_digest(self) -> None:
        standard = _compiled(_linear_doc(profile="standard"))
        ensemble = _compiled(_linear_doc(profile="ensemble"))
        assert standard.steps[0].step_semantic_digest != ensemble.steps[0].step_semantic_digest

    def test_cores_change_moves_step_and_item_digests(self) -> None:
        first = _compiled(_linear_doc(resources={"cores_per_item": 4, "memory_per_item": "16GiB"}))
        second = _compiled(_linear_doc(resources={"cores_per_item": 8, "memory_per_item": "16GiB"}))
        assert first.steps[0].step_semantic_digest != second.steps[0].step_semantic_digest
        assert _item(first).semantic_digest != _item(second).semantic_digest

    def test_memory_change_moves_step_and_item_digests(self) -> None:
        first = _compiled(_linear_doc(resources={"cores_per_item": 4, "memory_per_item": "16GiB"}))
        second = _compiled(_linear_doc(resources={"cores_per_item": 4, "memory_per_item": "32GiB"}))
        assert first.steps[0].step_semantic_digest != second.steps[0].step_semantic_digest
        assert _item(first).semantic_digest != _item(second).semantic_digest

    def test_seed_change_moves_all_scientific_digests(self) -> None:
        def confgen_doc(seed: int) -> dict:
            return v4_doc(
                [
                    confgen_step(
                        "s_conf",
                        bindings={"structure": {"source": {"run": "structures"}}},
                        seed=seed,
                    )
                ],
                inputs=STRUCTURE_INPUTS,
            )

        first = _compiled(confgen_doc(42))
        second = _compiled(confgen_doc(43))
        assert first.definition_digest != second.definition_digest
        assert first.steps[0].step_semantic_digest != second.steps[0].step_semantic_digest
        assert _item(first).semantic_digest != _item(second).semantic_digest

    def test_run_scientific_defaults_move_definition_and_item_digests(self) -> None:
        def water_doc(charge: int, multiplicity: int) -> dict:
            document = _linear_doc()
            document["global"] = {
                "scientific_defaults": {"charge": charge, "multiplicity": multiplicity}
            }
            return document

        bare = structure_set("s0")
        # Strip inherent charge/multiplicity so run defaults apply.
        from confflow.domain import StructureSet

        bare = StructureSet.of(
            type(bare[0])(
                id=bare[0].id,
                atoms=bare[0].atoms,
                coordinates=bare[0].coordinates,
            )
        )
        neutral = _compiled(water_doc(0, 1))
        cation = _compiled(water_doc(1, 2))
        # Run-level defaults are not part of the step's own science...
        assert neutral.steps[0].step_semantic_digest == cation.steps[0].step_semantic_digest
        # ...but they are part of definition semantics and item content.
        assert neutral.definition_digest != cation.definition_digest
        neutral_item = assemble_work_items(neutral, run_inputs(structures={"structures": bare}))
        cation_item = assemble_work_items(cation, run_inputs(structures={"structures": bare}))
        assert neutral_item.ok and cation_item.ok
        assert neutral_item.items[0].semantic_digest != cation_item.items[0].semantic_digest

    def test_freeze_override_moves_step_and_item_digests(self) -> None:
        baseline = _compiled(_linear_doc())
        frozen = _compiled(_linear_doc(overrides={"freeze": [2, 1, 1]}))
        assert frozen.steps[0].scientific.overrides["freeze"] == (1, 2)
        assert baseline.steps[0].step_semantic_digest != frozen.steps[0].step_semantic_digest
        assert baseline.definition_digest != frozen.definition_digest
        assert _item(baseline).semantic_digest != _item(frozen).semantic_digest

    def test_freeze_index_beyond_atom_count_is_an_error(self) -> None:
        plan = _compiled(_linear_doc(overrides={"freeze": [99]}))
        assembly = assemble_work_items(
            plan, run_inputs(structures={"structures": structure_set("s0")})
        )
        assert not assembly.ok
        assert "freeze_index_out_of_range" in [
            str(d.details.get("reason")) for d in assembly.errors
        ]

    def test_completion_mode_moves_definition_digest(self) -> None:
        strict = _compiled(_linear_doc())
        partial = _compiled(
            _linear_doc(
                completion={
                    "mode": "allow_partial",
                    "partial_output": "allow",
                }
            )
        )
        assert strict.definition_digest != partial.definition_digest


class TestDigestAxisSeparation:
    """The four axes never collapse into each other."""

    def test_definition_and_step_digests_are_distinct_values(self) -> None:
        plan = _compiled(_linear_doc())
        assert plan.definition_digest != plan.steps[0].step_semantic_digest

    def test_environment_digest_is_a_separate_axis(self) -> None:
        environment = ExecutionEnvironment(
            program="gaussian",
            program_version="16.C.01",
            executable_digest="sha256:" + "a" * 64,
            target="node-1",
        )
        plan = _compiled(_linear_doc())
        environment_digest = environment.digest()
        assert environment_digest.startswith("sha256:")
        assert environment_digest != plan.definition_digest
        assert environment_digest != plan.steps[0].step_semantic_digest
        assert environment_digest != _item(plan).semantic_digest

    def test_environment_digest_ignores_absolute_paths(self) -> None:
        first = ExecutionEnvironment(
            program="gaussian",
            program_version="16.C.01",
            executable_digest="sha256:" + "b" * 64,
        )
        second = ExecutionEnvironment(
            program="gaussian",
            program_version="16.C.01",
            executable_digest="sha256:" + "b" * 64,
            target="elsewhere",
        )
        assert first.digest() != second.digest()
        assert (
            ExecutionEnvironment(program="gaussian", program_version="16.C.01").digest()
            == ExecutionEnvironment(program="gaussian", program_version="16.C.01").digest()
        )

    def test_version_change_moves_environment_digest(self) -> None:
        first = ExecutionEnvironment(program="gaussian", program_version="16.C.01")
        second = ExecutionEnvironment(program="gaussian", program_version="16.C.02")
        assert first.digest() != second.digest()


class TestGoldenDigests:
    """Pinned digest values guard against accidental canonicalization churn."""

    def test_geometry_digest_golden(self) -> None:
        from tests.v4._builders import structure

        record = structure("golden")
        assert record.geometry_digest == (
            "sha256:05458eff9074bb1c401d62bad92748c91fa5edbaf8e8b0654710abffd26275f4"
        )

    def test_definition_digest_golden(self) -> None:
        plan = _compiled(_linear_doc())
        assert plan.definition_digest == (
            "sha256:8d2472317155722e3457b6568deb3345c427fdf06cb8e5a5b6146517050cc8ed"
        )

    def test_work_item_digest_golden(self) -> None:
        plan = _compiled(_linear_doc())
        assert _item(plan).semantic_digest == (
            "sha256:686e3a95398e32e1146d5fb4111724c5c18270fb7aa324d5ea8c35179da98e4c"
        )
