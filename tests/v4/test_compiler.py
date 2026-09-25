#!/usr/bin/env python3

"""V4 compiler invariants: determinism, topology, disabled steps, policies.

Pins the compile behaviour of the greenfield core:

- deterministic compile payloads and stable topological order;
- dependencies derived from bindings only, with cycles rejected;
- compile-time disabled-step passthrough rewiring and capability-loss errors;
- failure/acceptance policies and binding partial-consumption declarations;
- closed capability vocabulary and single-source schema defaults.
"""

from __future__ import annotations

import pytest

from confflow.domain import Cardinality, FrozenDict, Pairing, PortKind
from confflow.domain.canonical import canonical_json_bytes
from confflow.execution import (
    ExecutorCapability,
    ExecutorContract,
    PortSpec,
    build_default_registry,
)
from confflow.workflow.v4 import (
    MaterializedOutputs,
    StepOutputs,
    assemble_work_items,
    build_binding_graph,
    compile_workflow_text,
    parse_workflow_document,
    validate_definition,
)
from tests.v4._builders import (
    analysis_step,
    calc_step,
    codes,
    compile_doc,
    confgen_step,
    passthrough_registry,
    reasons,
    run_inputs,
    structure_set,
    transform_step,
    v4_doc,
)

STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}
CHECKPOINT_INPUTS = {
    "structures": {"kind": "structure", "cardinality": "many"},
    "checkpoints": {"kind": "artifact", "cardinality": "many", "role": "checkpoint"},
}


def _linear_structure_doc(**calc_kwargs: object) -> dict:
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


def _compile_ok(document: dict, registry: object = None):
    result = compile_doc(document, registry=registry)
    assert result.ok, [(d.code, d.details.get("reason"), d.message) for d in result.errors]
    return result


def _error_reasons(result: object) -> list[str]:
    return reasons(result.errors)


class TestDeterministicCompile:
    """Compile is pure, deterministic, and order-insensitive."""

    def test_same_document_compiles_to_identical_payload(self) -> None:
        first = _compile_ok(_linear_structure_doc())
        second = _compile_ok(_linear_structure_doc())
        assert first.plan is not None and second.plan is not None
        assert canonical_json_bytes(first.plan.to_payload()) == canonical_json_bytes(
            second.plan.to_payload()
        )
        assert first.plan.definition_digest == second.plan.definition_digest

    def test_document_order_does_not_change_execution_order(self) -> None:
        steps = [analysis_step("s3"), analysis_step("s1"), analysis_step("s2")]
        result = _compile_ok(v4_doc(list(steps)))
        assert result.plan is not None
        assert result.plan.execution_order == ("s1", "s2", "s3")
        reordered = _compile_ok(v4_doc([steps[1], steps[2], steps[0]]))
        assert reordered.plan is not None
        assert reordered.plan.execution_order == ("s1", "s2", "s3")
        assert reordered.plan.definition_digest == result.plan.definition_digest

    def test_numeric_step_ids_sort_numerically(self) -> None:
        result = _compile_ok(
            v4_doc([analysis_step("s10"), analysis_step("s2"), analysis_step("s1")])
        )
        assert result.plan is not None
        assert result.plan.execution_order == ("s1", "s2", "s10")

    def test_dependency_order_follows_bindings(self) -> None:
        doc = v4_doc(
            [
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
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = _compile_ok(doc)
        assert result.plan is not None
        assert result.plan.execution_order == ("s_opt", "s_freq")
        assert result.plan.graph.dependency_ids("s_freq") == ("s_opt",)

    def test_compile_workflow_text_matches_mapping(self) -> None:
        text = (
            "schema: confflow.workflow.v4\n"
            "inputs:\n"
            "  structures:\n"
            "    kind: structure\n"
            "    cardinality: many\n"
            "steps:\n"
            "  - id: s_opt\n"
            "    executor: calculation\n"
            "    bindings:\n"
            "      structure:\n"
            "        source: {run: structures}\n"
            "    calculation:\n"
            "      program: g16\n"
            "      role: opt\n"
            "      native: {keyword: 'B3LYP/6-31G* opt'}\n"
        )
        from_text = compile_workflow_text(text)
        from_mapping = compile_doc(_linear_structure_doc())
        assert from_text.ok and from_mapping.ok
        assert from_text.plan.definition_digest == from_mapping.plan.definition_digest


class TestGraphValidation:
    """Bindings are the only dependency source and fail closed."""

    def test_cycle_rejected(self) -> None:
        doc = v4_doc(
            [
                transform_step(
                    "s_a",
                    bindings={"structure": {"source": {"step": "s_b", "port": "structures"}}},
                ),
                transform_step(
                    "s_b",
                    bindings={"structure": {"source": {"step": "s_a", "port": "structures"}}},
                ),
            ]
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "compile_error" in codes(result.errors)
        assert "dependency_cycle" in _error_reasons(result)

    def test_unknown_source_step(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_missing", "port": "structures"}}},
                )
            ]
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "binding_error" in codes(result.errors)
        assert "unknown_source_step" in _error_reasons(result)

    def test_unknown_port(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "geometry"}}},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_port" in _error_reasons(result)

    def test_kind_mismatch(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "results"}}},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "kind_mismatch" in _error_reasons(result)

    def test_unknown_run_input(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "missing"}}},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_run_input" in _error_reasons(result)

    def test_self_reference_rejected_at_parse(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
                )
            ]
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "binding_error" in codes(result.errors)

    def test_required_input_missing(self) -> None:
        result = compile_doc(v4_doc([calc_step("s_opt")]))
        assert not result.ok
        assert "cardinality_error" in codes(result.errors)
        assert "required_input_missing" in _error_reasons(result)

    def test_adapter_required_port_missing(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_qst",
                    adapter="named_structures",
                    bindings={"reactant": {"source": {"run": "structures"}}},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "required_input_missing" in _error_reasons(result)

    def test_role_selector_requires_advertised_role(self) -> None:
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
                                "select": {"role": "bogus"},
                            },
                            "pairing": "by_subject",
                        },
                    },
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_selector_role" in _error_reasons(result)

    def test_artifact_port_with_multiple_roles_requires_selector(self) -> None:
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
                            "source": {"step": "s_opt", "port": "artifacts"},
                            "pairing": "by_subject",
                        },
                    },
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "role_required" in _error_reasons(result)

    def test_role_selector_not_applicable_to_structure_port(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_freq",
                    bindings={
                        "structure": {
                            "source": {
                                "step": "s_opt",
                                "port": "structures",
                                "select": {"role": "checkpoint"},
                            }
                        }
                    },
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "selector_not_applicable" in _error_reasons(result)

    def test_positional_artifact_pairing_rejected(self) -> None:
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
                            "pairing": "per_structure",
                        },
                    },
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "pairing_not_allowed" in _error_reasons(result)

    def test_binding_cardinality_cannot_weaken_required_port(self) -> None:
        doc = _linear_structure_doc()
        doc["steps"][0]["bindings"]["structure"]["cardinality"] = "many"
        result = compile_doc(doc)
        assert not result.ok
        assert "cardinality_mismatch" in _error_reasons(result)

    def test_duplicate_step_id(self) -> None:
        doc = v4_doc(
            [
                calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "identity_error" in codes(result.errors)


class TestCapabilityVocabulary:
    """The registry is the single capability vocabulary."""

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        [
            ("executor", "magic", "unknown_executor_capability"),
            ("execution_adapter", "magic", "unknown_execution_adapter"),
            ("result_profile", "magic", "unknown_result_profile"),
        ],
    )
    def test_unknown_capability_names(self, field: str, value: str, reason: str) -> None:
        doc = _linear_structure_doc()
        if field == "executor":
            doc["steps"][0]["executor"] = value
        else:
            doc["steps"][0]["calculation"][field] = value
        result = compile_doc(doc)
        assert not result.ok
        assert reason in _error_reasons(result)

    def test_unknown_check(self) -> None:
        doc = _linear_structure_doc(checks=["magic_check"])
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_scientific_check" in _error_reasons(result)

    def test_check_not_supported_by_profile(self) -> None:
        doc = _linear_structure_doc(profile="opaque", checks=["frequencies_required"])
        result = compile_doc(doc)
        assert not result.ok
        assert "check_not_supported" in _error_reasons(result)

    def test_opaque_profile_without_checks_compiles(self) -> None:
        _compile_ok(_linear_structure_doc(profile="opaque"))

    def test_unknown_recovery(self) -> None:
        doc = _linear_structure_doc(recovery="magic_recovery")
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_recovery" in _error_reasons(result)

    def test_unknown_transform_kind(self) -> None:
        doc = v4_doc(
            [
                transform_step(
                    "s_filter",
                    kind="magic",
                    bindings={"structure": {"source": {"run": "structures"}}},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_transform_kind" in _error_reasons(result)

    def test_confgen_requires_explicit_seed(self) -> None:
        doc = v4_doc(
            [
                confgen_step(
                    "s_conf",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    seed=None,
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "seed_required" in _error_reasons(result)

    def test_confgen_with_seed_compiles(self) -> None:
        doc = v4_doc(
            [
                confgen_step(
                    "s_conf",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    seed=7,
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        _compile_ok(doc)

    def test_executor_block_mismatch(self) -> None:
        doc = v4_doc(
            [
                {
                    "id": "s_bad",
                    "executor": "analysis",
                    "calculation": {"program": "g16"},
                    "analysis": {"native": {}},
                }
            ]
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "misplaced_executor_block" in _error_reasons(result)

    def test_artifact_run_input_positional_pairing_rejected(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                )
            ],
            inputs={
                "structures": {"kind": "structure", "cardinality": "many"},
                "checkpoints": {
                    "kind": "artifact",
                    "cardinality": "many",
                    "pairing": "per_structure",
                },
            },
        )
        result = compile_doc(doc)
        assert not result.ok
        assert "schema_error" in codes(result.errors)


class TestFailurePolicyCompilation:
    """Acceptance and partial-consumption are explicit, never guessed."""

    def _partial_producer(self, **completion: object) -> dict:
        return {
            "id": "s_a",
            "executor": "analysis",
            "analysis": {"native": {}},
            "completion": {"mode": "allow_partial", **completion},
        }

    def _consumer(self) -> dict:
        return {
            "id": "s_b",
            "executor": "analysis",
            "analysis": {"native": {}},
            "bindings": {"results": {"source": {"step": "s_a", "port": "results"}}},
        }

    def test_partial_output_denied_blocks_consumers(self) -> None:
        result = compile_doc(v4_doc([self._partial_producer(), self._consumer()]))
        assert not result.ok
        assert "partial_output_denied" in _error_reasons(result)

    def test_partial_consumption_must_be_declared(self) -> None:
        result = compile_doc(
            v4_doc([self._partial_producer(partial_output="allow"), self._consumer()])
        )
        assert not result.ok
        assert "partial_consumption_undefined" in _error_reasons(result)

    def test_partial_consumption_accept_subset_compiles(self) -> None:
        consumer = self._consumer()
        consumer["bindings"]["results"]["partial_consumption"] = "accept_subset"
        result = compile_doc(v4_doc([self._partial_producer(partial_output="allow"), consumer]))
        assert result.ok, [(d.code, d.message) for d in result.errors]

    def test_partial_consumption_require_complete_compiles(self) -> None:
        consumer = self._consumer()
        consumer["bindings"]["results"]["partial_consumption"] = "require_complete"
        result = compile_doc(v4_doc([self._partial_producer(partial_output="allow"), consumer]))
        assert result.ok, [(d.code, d.message) for d in result.errors]

    def test_minimum_success_with_require_all_rejected(self) -> None:
        doc = _linear_structure_doc(completion={"mode": "require_all", "minimum_success": 2})
        result = compile_doc(doc)
        assert not result.ok
        assert "schema_error" in codes(result.errors)


class TestDisabledStepSemantics:
    """Disabled steps are resolved at compile time only."""

    def test_disabled_passthrough_rewrites_consumers(self) -> None:
        doc = v4_doc(
            [
                transform_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                transform_step(
                    "s_b",
                    kind="filter",
                    enabled=False,
                    bindings={"structure": {"source": {"step": "s_a", "port": "structures"}}},
                ),
                transform_step(
                    "s_c",
                    bindings={"structure": {"source": {"step": "s_b", "port": "structures"}}},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc, registry=passthrough_registry())
        assert result.ok, [(d.code, d.details.get("reason"), d.message) for d in result.errors]
        plan = result.plan
        assert plan is not None
        assert "s_b" not in plan.execution_order
        edge = next(edge for edge in plan.graph.edges if edge.target_step_id == "s_c")
        assert edge.source_step_id == "s_a"
        assert edge.via_disabled == ("s_b",)

    def test_disabled_passthrough_assembly_uses_upstream_outputs(self) -> None:
        doc = v4_doc(
            [
                transform_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                transform_step(
                    "s_b",
                    kind="filter",
                    enabled=False,
                    bindings={"structure": {"source": {"step": "s_a", "port": "structures"}}},
                ),
                transform_step(
                    "s_c",
                    bindings={"structure": {"source": {"step": "s_b", "port": "structures"}}},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc, registry=passthrough_registry())
        assert result.plan is not None
        seed = structure_set("s0", "s1")
        materialized = MaterializedOutputs(
            steps=FrozenDict({"s_a": StepOutputs(step_id="s_a", structures=seed)})
        )
        assembly = assemble_work_items(
            result.plan,
            run_inputs(structures={"structures": seed}),
            materialized=materialized,
        )
        assert assembly.ok, [(d.code, d.message) for d in assembly.errors]
        c_items = assembly.for_step("s_c")
        assert len(c_items) == 1
        assert c_items[0].named_inputs.structures["structure"].ids == seed.ids

    def test_disabled_checkpoint_producer_is_capability_loss(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_b",
                    enabled=False,
                    bindings={
                        "structure": {"source": {"step": "s_a", "port": "structures"}},
                        "checkpoint": {
                            "source": {
                                "step": "s_a",
                                "port": "artifacts",
                                "select": {"role": "checkpoint"},
                            },
                            "pairing": "by_subject",
                            "cardinality": "one",
                        },
                    },
                ),
                calc_step(
                    "s_c",
                    bindings={
                        "structure": {"source": {"step": "s_a", "port": "structures"}},
                        "checkpoint": {
                            "source": {
                                "step": "s_b",
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
        result = compile_doc(doc)
        assert not result.ok
        assert "capability_error" in codes(result.errors)
        assert "disabled_capability_lost" in _error_reasons(result)

    def test_disabled_root_consumed_by_enabled_step(self) -> None:
        registry = build_default_registry()
        base = registry.executor(ExecutorCapability.STRUCTURE_TRANSFORM)
        registry.register_executor(
            ExecutorContract(
                capability=ExecutorCapability.STRUCTURE_TRANSFORM,
                contract_version="test.contract.structure_transform.optional_passthrough.v1",
                input_ports=(
                    PortSpec(
                        "structure",
                        PortKind.STRUCTURE,
                        Cardinality.OPTIONAL,
                        Pairing.SINGLE,
                    ),
                ),
                output_ports=base.output_ports,
                passthrough_ports=FrozenDict({"structures": "structure"}),
            )
        )
        doc = v4_doc(
            [
                transform_step("s_b", enabled=False),
                transform_step(
                    "s_c",
                    bindings={"structure": {"source": {"step": "s_b", "port": "structures"}}},
                ),
            ]
        )
        result = compile_doc(doc, registry=registry)
        assert not result.ok
        assert "disabled_root_no_source" in _error_reasons(result)

    def test_unused_disabled_step_warns(self) -> None:
        doc = v4_doc(
            [
                calc_step(
                    "s_opt",
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
                calc_step(
                    "s_unused",
                    enabled=False,
                    bindings={"structure": {"source": {"run": "structures"}}},
                ),
            ],
            inputs=STRUCTURE_INPUTS,
        )
        result = compile_doc(doc)
        assert result.ok
        assert "disabled_step_unused" in reasons(result.warnings)

    def test_named_structures_path_endpoints_profile_compiles(self) -> None:
        """QST2/QST3/NEB shapes are representable without execution."""
        doc = v4_doc(
            [
                calc_step(
                    "s_neb",
                    adapter="named_structures",
                    profile="path_endpoints",
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
        result = compile_doc(doc)
        assert result.ok, [(d.code, d.details.get("reason"), d.message) for d in result.errors]

    def test_disabled_step_toggle_changes_definition_digest(self) -> None:
        enabled_doc = v4_doc(
            [
                transform_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        disabled_doc = v4_doc(
            [
                transform_step(
                    "s_a",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    enabled=False,
                )
            ],
            inputs=STRUCTURE_INPUTS,
        )
        first = _compile_ok(enabled_doc)
        second = _compile_ok(disabled_doc)
        assert first.plan is not None and second.plan is not None
        assert first.plan.definition_digest != second.plan.definition_digest

    def test_no_runtime_bypass_algorithm_exists(self) -> None:
        """Disabled handling lives in the compiler module only."""
        import inspect

        import confflow.workflow.v4.assembly as assembly
        import confflow.workflow.v4.compiler as compiler

        assert "effective_sources" not in inspect.getsource(assembly)
        assert "effective_sources" not in inspect.getsource(compiler)


class TestSchemaStrictness:
    """Unknown semantic members fail closed."""

    def test_auto_clean_is_rejected(self) -> None:
        doc = _linear_structure_doc()
        doc["steps"][0]["calculation"]["auto_clean"] = True
        result = compile_doc(doc)
        assert not result.ok
        assert "schema_error" in codes(result.errors)
        assert "unknown_member" in _error_reasons(result)

    def test_unknown_top_level_member_is_rejected(self) -> None:
        doc = _linear_structure_doc()
        doc["options"] = {"anything": 1}
        result = compile_doc(doc)
        assert not result.ok
        assert "unknown_member" in _error_reasons(result)

    def test_unsupported_schema_version(self) -> None:
        doc = _linear_structure_doc()
        doc["schema"] = "confflow.workflow.v99"
        result = compile_doc(doc)
        assert not result.ok
        assert "version_unsupported" in _error_reasons(result)

    def test_duplicate_yaml_keys_rejected(self) -> None:
        text = (
            "schema: confflow.workflow.v4\n"
            "steps:\n"
            "  - id: s_opt\n"
            "    executor: analysis\n"
            "    executor: calculation\n"
        )
        result = compile_workflow_text(text)
        assert not result.ok
        assert "yaml_syntax" in _error_reasons(result) or "duplicate_key" in _error_reasons(result)

    def test_invalid_run_level_freeze_is_a_diagnostic(self) -> None:
        doc = _linear_structure_doc()
        doc["global"] = {"scientific_defaults": {"freeze": [0]}}
        result = compile_doc(doc)
        assert not result.ok
        assert "schema_error" in codes(result.errors)
        assert "invalid_value" in _error_reasons(result)

    def test_validate_definition_returns_validated_steps(self) -> None:
        parsed = parse_workflow_document(_linear_structure_doc())
        assert parsed.definition is not None
        validation = validate_definition(parsed.definition)
        assert validation.ok
        assert validation.validated is not None
        assert [step.step_id for step in validation.validated.steps] == ["s_opt"]
        assert validation.validated.steps[0].step_semantic_digest.startswith("sha256:")


class TestBindingGraphQueries:
    """The graph exposes deterministic dependency queries."""

    def test_graph_dependencies_and_successors(self) -> None:
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
                calc_step(
                    "s_sp",
                    bindings={
                        "structure": {"source": {"step": "s_freq", "port": "structures"}},
                        "checkpoint": {
                            "source": {
                                "step": "s_freq",
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
        result = _compile_ok(doc)
        graph = result.plan.graph
        assert graph.dependency_ids("s_sp") == ("s_freq",)
        assert graph.successor_ids("s_opt") == ("s_freq",)
        assert graph.successor_ids("s_freq") == ("s_sp",)
        assert graph.dependency_ids("s_opt") == ()
        assert graph.execution_order == ("s_opt", "s_freq", "s_sp")

    def test_build_binding_graph_requires_validated_definition(self) -> None:
        parsed = parse_workflow_document(_linear_structure_doc())
        assert parsed.definition is not None
        validation = validate_definition(parsed.definition)
        assert validation.validated is not None
        graph_result = build_binding_graph(validation.validated)
        assert graph_result.ok
        assert graph_result.graph is not None
        assert graph_result.graph.execution_order == ("s_opt",)
