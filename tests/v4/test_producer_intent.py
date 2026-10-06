#!/usr/bin/env python3

"""Producer intent vertical slice (Phases 2-4 + 7).

Every test compiles through the REAL strict V4 parser and compiler and
asserts on real output fields and digest behavior.  No second runtime.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from confflow.producer.cards import CARD_TYPES, CARD_VERSION, get_card
from confflow.producer.intent import (
    INTENT_SCHEMA,
    IntentCompilationError,
    compile_intent,
    intent_catalog,
)
from confflow.producer.presets import PRESET_TYPES, PRESET_VERSION
from confflow.producer.seeds import SEED_VERSION
from confflow.workflow.v4.compiler import compile_workflow

GLOBALS = {"charge": 0, "multiplicity": 1}

OPT_NATIVE = {"keyword": "B3LYP D3BJ Opt"}
FREQ_NATIVE = {"keyword": "B3LYP D3BJ Freq"}
SP_NATIVE = {"keyword": "B3LYP D3BJ SP"}

TORSIONS = [
    {
        "id": "t1",
        "bond": [1, 2],
        "model": "relative_rotation_grid",
        "angles": [0, 120, 240],
        "treatment": "enumerate",
    }
]


def _intent(steps: list[dict[str, Any]], **top: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "globals": dict(GLOBALS),
        "steps": steps,
    }
    document.update(top)
    return document


def _opt_freq_sp() -> dict[str, Any]:
    return _intent(
        [
            {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
            {"card": "freq@v1", "program": "orca", "native": dict(FREQ_NATIVE)},
            {"card": "sp@v1", "program": "orca", "native": dict(SP_NATIVE)},
        ]
    )


def test_opt_freq_sp_chain_compiles_with_linear_bindings() -> None:
    document = compile_intent(_opt_freq_sp())
    assert document["schema"] == "confflow.workflow.v4"
    assert [step["id"] for step in document["steps"]] == ["opt_1", "freq_1", "sp_1"]
    assert document["steps"][0]["calculation"]["role"] == "opt"
    assert document["steps"][1]["calculation"]["role"] == "freq"
    assert document["steps"][2]["calculation"]["role"] == "sp"
    assert document["steps"][0]["bindings"] == {"structure": {"source": {"run": "structures"}}}
    assert document["steps"][1]["bindings"] == {
        "structure": {"source": {"step": "opt_1", "port": "structures"}}
    }
    assert document["steps"][2]["bindings"] == {
        "structure": {"source": {"step": "freq_1", "port": "structures"}}
    }
    compiled = compile_workflow(document)
    assert compiled.ok and compiled.plan is not None
    assert [step.step_id for step in compiled.plan.steps] == ["opt_1", "freq_1", "sp_1"]


def test_explicit_id_and_role_overrides_preserved() -> None:
    document = compile_intent(
        _intent(
            [
                {
                    "id": "myopt",
                    "card": "opt@v1",
                    "role": "custom",
                    "program": "orca",
                    "native": dict(OPT_NATIVE),
                }
            ]
        )
    )
    assert document["steps"][0]["id"] == "myopt"
    assert document["steps"][0]["calculation"]["role"] == "custom"
    assert compile_workflow(document).ok


def test_confgen_full_enumeration_gets_no_seed() -> None:
    document = compile_intent(
        _intent(
            [
                {
                    "card": "confgen@v1",
                    "native": {"schema_version": 3, "torsions": copy.deepcopy(TORSIONS)},
                }
            ]
        )
    )
    assert document["steps"][0]["confgen"].get("seed") is None
    assert compile_workflow(document).ok


def test_confgen_capped_sampling_derives_stable_seed() -> None:
    def build() -> dict[str, Any]:
        return _intent(
            [
                {
                    "card": "confgen@v1",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 50},
                    },
                }
            ]
        )

    first = compile_intent(build())["steps"][0]["confgen"]["seed"]
    second = compile_intent(build())["steps"][0]["confgen"]["seed"]
    assert isinstance(first, int) and first == second
    assert compile_workflow(compile_intent(build())).ok


def test_explicit_seed_override_preserved() -> None:
    # R2.2: vehicle is the retained sp card (goat retired); the rule is
    # unchanged: an explicit seed is kept verbatim and never rendered
    # into a native seed key.
    document = compile_intent(
        _intent(
            [
                {
                    "card": "sp@v1",
                    "program": "orca",
                    "native": {"keyword": "B3LYP D3BJ SP"},
                    "seed": 7,
                }
            ]
        )
    )
    assert document["steps"][0]["calculation"]["seed"] == 7
    assert "Seed" not in document["steps"][0]["calculation"]["native"]
    assert document["steps"][0]["annotations"]["producer_resolution"]["seed_scope"] is None
    assert compile_workflow(document).ok


def test_invalid_card_type_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "banana@v1"}]))


def test_stochastic_change_moves_seed_operational_change_does_not() -> None:
    def build(extra: dict[str, Any]) -> dict[str, Any]:
        step: dict[str, Any] = {
            "card": "confgen@v1",
            "native": {
                "schema_version": 3,
                "torsions": copy.deepcopy(TORSIONS),
                "sampling": {"cap": 50},
            },
        }
        step.update(extra)
        return _intent([step])

    base = compile_intent(build({}))["steps"][0]["confgen"]["seed"]
    sched = compile_intent(build({"scheduler": {"max_parallel_items": 4}}))["steps"][0]["confgen"][
        "seed"
    ]
    assert base == sched
    moved = compile_intent(build({"resources": {"cores_per_item": 4}}))["steps"][0]["confgen"][
        "seed"
    ]
    assert moved != base


def test_operational_change_keeps_definition_digest() -> None:
    plain = compile_intent(_opt_freq_sp())
    wider_steps = copy.deepcopy(_opt_freq_sp()["steps"])
    wider_steps[0]["scheduler"] = {"max_parallel_items": 8}
    wider = compile_intent(_intent(wider_steps))
    assert compile_workflow(plain).plan is not None
    assert compile_workflow(wider).plan is not None
    assert (
        compile_workflow(plain).plan.definition_digest
        == compile_workflow(wider).plan.definition_digest
    )
    other_steps = copy.deepcopy(_opt_freq_sp()["steps"])
    other_steps[0]["native"] = {"keyword": "B3LYP D3BJ Opt Tight"}
    other = compile_intent(_intent(other_steps))
    assert (
        compile_workflow(plain).plan.definition_digest
        != compile_workflow(other).plan.definition_digest
    )


def test_dedup_preset_compiles() -> None:
    document = compile_intent(
        _intent(
            [
                {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                {"card": "deduplicate@v1"},
            ]
        )
    )
    transform = document["steps"][1]["transform"]
    assert transform["kind"] == "deduplicate"
    assert compile_workflow(document).ok


def test_refine_strict_preset_applies_threshold() -> None:
    document = compile_intent(
        _intent(
            [
                {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                {"card": "refine@v1", "preset": "refine_strict@v1"},
            ]
        )
    )
    assert document["steps"][1]["transform"]["native"]["rmsd_threshold_angstrom"] == 0.1
    assert compile_workflow(document).ok


def test_named_card_without_bindings_refuses() -> None:
    # R2.2: qst2 is retired; the reference now fails closed as an unknown
    # card (retired vocabulary is never compiled).
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "qst2@v1",
                        "program": "gaussian",
                        "native": {
                            "keyword": "B3LYP/6-31G(d) QST2 Opt",
                            "atom_mapping": {"kind": "identity"},
                        },
                    }
                ]
            )
        )


def test_legacy_v4_document_returned_unchanged() -> None:
    legacy = {
        "schema": "confflow.workflow.v4",
        "inputs": {
            "structures": {"kind": "structure", "cardinality": "many", "grouping": "each_entity"}
        },
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "opt",
                "executor": "calculation",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "calculation": {
                    "program": "orca",
                    "role": "opt",
                    "execution_adapter": "standard",
                    "result_profile": "standard",
                    "native": {"keyword": "B3LYP D3BJ Opt"},
                    "checks": ["normal_termination", "geometry_required"],
                    "recovery": {"profile": "none"},
                },
            }
        ],
    }
    assert compile_intent(legacy) == legacy
    assert compile_intent(copy.deepcopy(legacy)) == legacy


def test_branching_from_reference() -> None:
    document = compile_intent(
        _intent(
            [
                {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                {"card": "freq@v1", "program": "orca", "native": dict(FREQ_NATIVE)},
                {
                    "card": "sp@v1",
                    "from": "opt_1",
                    "program": "orca",
                    "native": dict(SP_NATIVE),
                },
            ]
        )
    )
    assert document["steps"][2]["bindings"] == {
        "structure": {"source": {"step": "opt_1", "port": "structures"}}
    }
    assert compile_workflow(document).ok


def test_recipe_without_assignments_rejected() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # missing-assignment gate is unchanged.
    with pytest.raises(IntentCompilationError):
        compile_intent({"schema": INTENT_SCHEMA, "globals": dict(GLOBALS), "recipe": "optimize"})
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "steps": [
                    {
                        "id": "other",
                        "card": "ts@v1",
                        "program": "orca",
                        "native": {"keyword": "wB97X-D3 OptTS"},
                    }
                ],
            }
        )


def test_recipe_without_explicit_state_rejected() -> None:
    assignments = [
        {
            "id": "optimize",
            "card": "sp@v1",
            "program": "orca",
            "native": {"keyword": "wB97X-D3 SP"},
        }
    ]
    with pytest.raises(IntentCompilationError, match="explicit 'globals'"):
        compile_intent({"schema": INTENT_SCHEMA, "recipe": "optimize", "steps": assignments})


def test_provenance_annotations_record_card_and_seed() -> None:
    document = compile_intent(_opt_freq_sp())
    resolution = document["steps"][0]["annotations"]["producer_resolution"]
    assert resolution["intent_schema"] == INTENT_SCHEMA
    assert resolution["card"] == "opt"
    assert resolution["card_version"] == CARD_VERSION
    assert resolution["seed_version"] == SEED_VERSION


def test_intent_catalog_is_pure_constants() -> None:
    catalog = intent_catalog()
    assert catalog["card_version"] == CARD_VERSION
    assert catalog["preset_version"] == PRESET_VERSION
    assert {card["type"] for card in catalog["cards"]} == set(CARD_TYPES)
    assert {preset["name"] for preset in catalog["presets"]} == set(PRESET_TYPES)
    assert get_card("opt")["checks"] == ["normal_termination", "geometry_required"]


def test_error_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        compile_intent({"schema": INTENT_SCHEMA, "steps": []})


def test_missing_charge_and_multiplicity_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "steps": [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                ],
            }
        )


def test_invalid_native_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": {"keyword": "B3LYP D3BJ Opt", "bogus_key": 1},
                    }
                ]
            )
        )


def test_legacy_stochastic_without_seed_rejected() -> None:
    legacy = {
        "schema": "confflow.workflow.v4",
        "inputs": {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "grouping": "each_entity",
            }
        },
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "goat",
                "executor": "calculation",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "calculation": {
                    "program": "orca",
                    "role": "goat",
                    "execution_adapter": "standard",
                    "result_profile": "ensemble",
                    "native": {"keyword": "B3LYP D3BJ GOAT", "goat": {"MaxIter": 50}},
                    "checks": ["normal_termination"],
                    "recovery": {"profile": "none"},
                },
            }
        ],
    }
    with pytest.raises(IntentCompilationError):
        compile_intent(legacy)


def test_refine_preset_mismatch_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "deduplicate@v1", "preset": "refine_strict@v1"},
                ]
            )
        )


def test_unknown_refine_native_key_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "native": {"energy_window": 5.0}},
                ]
            )
        )


def test_dedup_native_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "deduplicate@v1", "native": {"max_structures": 5}},
                ]
            )
        )


def test_refine_default_preset_pins_versioned_fields() -> None:
    document = compile_intent(
        _intent(
            [
                {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                {"card": "refine@v1"},
            ]
        )
    )
    assert document["steps"][1]["transform"]["native"] == {
        "rmsd_threshold_angstrom": 0.25,
        "bond_scale": 1.2,
        "heavy_only": False,
    }
    assert compile_workflow(document).ok


def test_ts_card_has_no_frequency_checks() -> None:
    document = compile_intent(
        _intent([{"card": "ts@v1", "program": "orca", "native": dict(OPT_NATIVE)}])
    )
    assert document["steps"][0]["calculation"]["checks"] == [
        "normal_termination",
        "geometry_required",
    ]


def test_ts_freq_card_expects_one_imaginary_mode() -> None:
    document = compile_intent(
        _intent([{"card": "ts_freq@v1", "program": "orca", "native": dict(FREQ_NATIVE)}])
    )
    calculation = document["steps"][0]["calculation"]
    assert "imaginary_frequency_count" in calculation["checks"]
    assert calculation["check_params"] == {"imaginary_frequency_count": {"expected": 1}}


def test_malformed_explicit_bindings_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "bindings": [1, 2],
                    }
                ]
            )
        )


def test_seed_on_transform_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "seed": 3},
                ]
            )
        )


def test_program_on_confgen_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "confgen@v1",
                        "program": "orca",
                        "native": {
                            "schema_version": 3,
                            "torsions": copy.deepcopy(TORSIONS),
                        },
                    }
                ]
            )
        )


def test_calc_without_native_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "opt@v1", "program": "orca"}]))


def test_card_mapping_unknown_keys_rejected() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent([{"card": {"type": "opt", "version": "v1", "bogus": 1}, "program": "orca"}])
        )


def test_memory_spelling_equality_in_seed() -> None:
    def build(memory: str) -> dict[str, Any]:
        return _intent(
            [
                {
                    "card": "confgen@v1",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 50},
                    },
                    "resources": {"cores_per_item": 1, "memory_per_item": memory},
                }
            ]
        )

    assert (
        compile_intent(build("1GiB"))["steps"][0]["confgen"]["seed"]
        == compile_intent(build("1024MiB"))["steps"][0]["confgen"]["seed"]
    )


def test_input_description_does_not_move_seed() -> None:
    def build(description: Any) -> dict[str, Any]:
        inputs: dict[str, Any] = {
            "structures": {"kind": "structure", "cardinality": "many", "grouping": "each_entity"}
        }
        if description is not None:
            inputs["structures"]["description"] = description
        return {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "inputs": inputs,
            "steps": [
                {
                    "card": "confgen@v1",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 50},
                    },
                }
            ],
        }

    assert (
        compile_intent(build(None))["steps"][0]["confgen"]["seed"]
        == compile_intent(build("display text"))["steps"][0]["confgen"]["seed"]
    )


def test_machine_profile_leaves_seed_and_digest_unchanged() -> None:
    profile = {"name": "test-box", "total_cores": 64, "total_memory": "256GiB"}
    plain = compile_intent(_opt_freq_sp())
    machined = compile_intent(_opt_freq_sp(), machine_profile=profile)
    assert machined["steps"][0]["execution"]["binding_id"] == "machine-test-box"
    assert (
        compile_workflow(plain).plan.definition_digest
        == compile_workflow(machined).plan.definition_digest
    )

    def capped() -> dict[str, Any]:
        return _intent(
            [
                {
                    "card": "confgen@v1",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 50},
                    },
                }
            ]
        )

    assert (
        compile_intent(capped())["steps"][0]["confgen"]["seed"]
        == compile_intent(capped(), machine_profile=profile)["steps"][0]["confgen"]["seed"]
    )


def test_checkpoint_method_change_needs_explicit_flag() -> None:
    def build(extra: Any) -> dict[str, Any]:
        reuse: dict[str, Any] = {"step": "opt", "mode": "readfc"}
        if extra:
            reuse.update(extra)
        return {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "steps": [
                {
                    "id": "opt",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G(d) Opt"},
                },
                {
                    "id": "opt2",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "PBE/6-31G(d) Opt"},
                    "reuse_checkpoint": reuse,
                },
            ],
        }

    with pytest.raises(IntentCompilationError):
        compile_intent(build(None))
    document = compile_intent(build({"allow_method_change": True}))
    checkpoint = document["steps"][1]["bindings"]["checkpoint"]
    assert checkpoint["cardinality"] == "one"
    assert checkpoint["source"]["select"] == {"role": "checkpoint"}
    assert compile_workflow(document).ok


def test_same_method_opt_to_sp_checkpoint_succeeds() -> None:
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "steps": [
                {
                    "id": "opt",
                    "card": "opt@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G(d) Opt"},
                },
                {
                    "id": "sp",
                    "card": "sp@v1",
                    "program": "gaussian",
                    "native": {"keyword": "B3LYP/6-31G(d) SP"},
                    "reuse_checkpoint": {"step": "opt", "mode": "checkpoint"},
                },
            ],
        }
    )
    checkpoint = document["steps"][1]["bindings"]["checkpoint"]
    assert checkpoint["cardinality"] == "one"
    assert checkpoint["source"]["select"] == {"role": "checkpoint"}
    assert compile_workflow(document).ok
