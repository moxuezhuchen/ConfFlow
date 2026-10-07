#!/usr/bin/env python3

"""Producer authoring boundary coverage (public seams only)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from confflow.producer import boundary
from confflow.producer.authoring import (
    binding_candidates,
    describe_step,
    dispatch_request,
    instantiate_card,
    validate_document,
)
from confflow.producer.boundary import (
    canonicalize_text,
    jcs_vectors,
)
from confflow.producer.cards import CARD_TYPES, CARD_VERSION, get_card, parse_card_ref
from confflow.producer.intent import (
    INTENT_SCHEMA,
    IntentCompilationError,
    compile_intent,
    intent_catalog,
)
from confflow.producer.presets import PRESET_TYPES, PRESET_VERSION, get_preset, parse_preset_ref
from confflow.producer.seeds import (
    assign_seeds,
    derive_seed,
    needs_seed,
    seed_identity_for_step,
)
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


def _opt_chain(n: int = 1) -> dict[str, Any]:
    return _intent(
        [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)} for _ in range(n)]
    )


def _capped_confgen(**step_extra: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "card": "confgen@v1",
        "native": {
            "schema_version": 3,
            "torsions": copy.deepcopy(TORSIONS),
            "sampling": {"cap": 50},
        },
    }
    step.update(step_extra)
    return _intent([step])


# R2.2: the tspes assignment helpers are retired with the recipe.


def _two_cards() -> dict[str, Any]:
    return {
        "low": {
            "card": "opt@v1",
            "program": "orca",
            "native_by_role": {
                "ts": {"keyword": "r2SCAN-3c OptTS"},
                "ts_freq": {"keyword": "r2SCAN-3c Freq"},
                "freq": {"keyword": "r2SCAN-3c Freq"},
                "opt": {"keyword": "r2SCAN-3c Opt"},
                "irc": {"keyword": "r2SCAN-3c IRC", "irc": {"direction": "both"}},
            },
        },
        "high": {
            "card": "sp@v1",
            "program": "orca",
            "native": {"keyword": "wB97X-D4 def2-TZVP SP"},
        },
    }


# ----------------------------------------------------------------------
# Card references
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [None, 123, [], {}, "", "opt", "opt@", "opt@v2", "banana@v1"],
    ids=["None", "123", "ref4", "ref5", "", "opt", "opt@", "opt@v2", "banana@v1"],
)
def test_parse_card_ref_malformed_fails_closed(ref: Any) -> None:
    before = copy.deepcopy(ref)
    with pytest.raises(ValueError):
        parse_card_ref(ref)
    assert ref == before


@pytest.mark.parametrize(
    "ref",
    [
        {"type": "opt"},
        {"type": "", "version": "v1"},
        {"type": "opt", "version": ""},
        {"type": 1, "version": "v1"},
        {"type": "opt", "version": "v1", "bogus": 1},
        {"type": "banana", "version": "v1"},
        {"type": "opt", "version": "v2"},
    ],
    ids=["ref0", "ref2", "ref3", "ref4", "ref5", "ref7", "ref8"],
)
def test_parse_card_ref_mapping_malformed_fails_closed(ref: Any) -> None:
    before = copy.deepcopy(ref)
    with pytest.raises(ValueError):
        parse_card_ref(ref)
    assert ref == before


@pytest.mark.parametrize("card_type", sorted(CARD_TYPES))
def test_parse_card_ref_roundtrip_all_types(card_type: str) -> None:
    assert parse_card_ref(f"{card_type}@{CARD_VERSION}") == (card_type, CARD_VERSION)
    assert parse_card_ref({"type": card_type, "version": CARD_VERSION}) == (
        card_type,
        CARD_VERSION,
    )


def test_get_card_unknown_and_bad_version_fail_closed() -> None:
    with pytest.raises(ValueError):
        get_card("banana")
    with pytest.raises(ValueError):
        get_card("opt", "v2")
    with pytest.raises(ValueError):
        parse_card_ref("opt@v2")


def test_get_card_returns_isolated_copy() -> None:
    first = get_card("opt")
    first["checks"].append("mutated")
    assert get_card("opt")["checks"] == ["normal_termination", "geometry_required"]


def test_ts_freq_card_purpose_shape() -> None:
    card = get_card("ts_freq")
    assert card["default_role"] == "freq"
    assert card["check_params"] == {"imaginary_frequency_count": {"expected": 1}}


# R2.2 (G18): the named-structure cards (qst2/qst3/neb) are retired.


@pytest.mark.parametrize("card_type", ["opt", "sp", "freq", "confgen", "refine", "deduplicate"])
def test_plain_cards_do_not_require_explicit_bindings(card_type: str) -> None:
    assert get_card(card_type)["requires_explicit_bindings"] is False


# ----------------------------------------------------------------------
# Preset references
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        None,
        7,
        [],
        "",
        "refine_default",
        "refine_default@",
        "@v1",
        "nope@v1",
        "refine_default@v2",
    ],
    ids=[
        "None",
        "7",
        "ref3",
        "",
        "refine_default",
        "refine_default@",
        "@v1",
        "nope@v1",
        "refine_default@v2",
    ],
)
def test_parse_preset_ref_malformed_fails_closed(ref: Any) -> None:
    before = copy.deepcopy(ref)
    with pytest.raises(ValueError):
        parse_preset_ref(ref)
    assert ref == before


@pytest.mark.parametrize(
    "ref",
    [
        {"name": "refine_default"},
        {"name": "", "version": "v1"},
        {"name": "refine_default", "version": ""},
        {"name": "refine_default", "version": "v1", "bogus": 1},
        {"type": "refine_default", "version": "v2"},
    ],
    ids=["ref0", "ref1", "ref2", "ref3", "ref5"],
)
def test_parse_preset_ref_mapping_malformed_fails_closed(ref: Any) -> None:
    before = copy.deepcopy(ref)
    with pytest.raises(ValueError):
        parse_preset_ref(ref)
    assert ref == before


@pytest.mark.parametrize("name", sorted(PRESET_TYPES))
def test_parse_preset_ref_roundtrip_all_names(name: str) -> None:
    assert parse_preset_ref(f"{name}@{PRESET_VERSION}") == (name, PRESET_VERSION)


def test_get_preset_unknown_version_and_isolation() -> None:
    with pytest.raises(ValueError):
        get_preset("nope")
    with pytest.raises(ValueError):
        get_preset("refine_default", "v2")
    first = get_preset("refine_default")
    first["native"]["rmsd_threshold_angstrom"] = 99.0
    assert get_preset("refine_default")["native"]["rmsd_threshold_angstrom"] == 0.25


def test_preset_table_card_linkage() -> None:
    assert get_preset("refine_default")["card"] == "refine"
    assert get_preset("refine_strict")["card"] == "refine"
    assert get_preset("dedup_default")["card"] == "deduplicate"
    assert get_preset("dedup_default")["native"] == {}


# ----------------------------------------------------------------------
# Catalog
# ----------------------------------------------------------------------


def test_intent_catalog_shape_and_purity() -> None:
    catalog = intent_catalog()
    assert catalog["intent_schema"] == INTENT_SCHEMA
    assert catalog["card_version"] == CARD_VERSION
    assert catalog["preset_version"] == PRESET_VERSION
    assert {c["type"] for c in catalog["cards"]} == set(CARD_TYPES)
    assert {p["name"] for p in catalog["presets"]} == set(PRESET_TYPES)
    # R2.2 声明：目录剩 7 项，退役项不再出现。
    # N4 声明新增：ensemble_refine appended last，目录 7→8。
    assert catalog["supported_recipes"] == [
        "optimize",
        "single_point",
        "frequency",
        "opt_freq",
        "transition_state",
        "confgen_torsion",
        "monomer_conformers",
        "ensemble_refine",
    ]
    assert "tspes" not in catalog["supported_recipes"]
    assert catalog["schema_keys"] == sorted(catalog["schema_keys"])
    assert catalog["step_keys"] == sorted(catalog["step_keys"])
    before = copy.deepcopy(catalog)
    catalog["cards"].append({"type": "mutated"})
    assert intent_catalog()["cards"] != catalog["cards"]
    assert intent_catalog() == before


# ----------------------------------------------------------------------
# Intent top-level refusals
# ----------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, 123, "x", []])
def test_compile_intent_non_mapping_fails_closed(bad: Any) -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(bad)  # type: ignore[arg-type]
    assert isinstance(IntentCompilationError("m"), ValueError)


def test_compile_intent_bad_schema_and_unknown_top() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent({"schema": "bogus"})
    before = _opt_chain()
    bad = copy.deepcopy(before)
    bad["bogus_top"] = 1
    with pytest.raises(IntentCompilationError, match="unknown members"):
        compile_intent(bad)
    assert bad["bogus_top"] == 1


@pytest.mark.parametrize("inputs", [[], {}, "x", 123, {"structures": "bad"}])
def test_compile_intent_bad_inputs_fails_closed(inputs: Any) -> None:
    before = copy.deepcopy(inputs)
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}], inputs=inputs
            )
        )
    assert inputs == before


def test_compile_intent_globals_bad_members() -> None:
    with pytest.raises(IntentCompilationError, match="unknown members"):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}],
                globals={"charge": 0, "bogus": 1},
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}], globals="x"
            )
        )


@pytest.mark.parametrize(
    "globals_map",
    [
        {"charge": True, "multiplicity": 1},
        {"charge": "0", "multiplicity": 1},
        {"charge": 0.5, "multiplicity": 1},
        {"charge": 0, "multiplicity": True},
        {"charge": 0, "multiplicity": -1},
        {"charge": 0, "multiplicity": "1"},
        {"charge": 0, "multiplicity": 1, "freeze": "x"},
        {"charge": 0, "multiplicity": 1, "freeze": [True]},
        {"charge": 0, "multiplicity": 1, "freeze": [0]},
        {"charge": 0, "multiplicity": 1, "freeze": ["1"]},
    ],
    ids=[
        "globals_map0",
        "globals_map1",
        "globals_map2",
        "globals_map3",
        "globals_map5",
        "globals_map6",
        "globals_map7",
        "globals_map8",
        "globals_map9",
        "globals_map11",
    ],
)
def test_compile_intent_globals_malformed_values_fail_closed(globals_map: Any) -> None:
    before = copy.deepcopy(globals_map)
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}],
                globals=globals_map,
            )
        )
    assert globals_map == before


def test_compile_intent_empty_steps_and_non_mapping_entries() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([]))
    with pytest.raises(IntentCompilationError):
        compile_intent({"schema": INTENT_SCHEMA, "globals": dict(GLOBALS), "steps": [42]})
    with pytest.raises(IntentCompilationError):
        compile_intent({"schema": INTENT_SCHEMA, "globals": dict(GLOBALS), "steps": "x"})


def test_compile_intent_step_id_shape_and_duplicates() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"id": "9bad", "card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"id": "dup", "card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"id": "dup", "card": "sp@v1", "program": "orca", "native": dict(SP_NATIVE)},
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent([{"id": 7, "card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}])
        )


def test_compile_intent_unknown_step_keys_and_missing_card() -> None:
    with pytest.raises(IntentCompilationError, match="unknown members"):
        compile_intent(
            _intent([{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE), "bogus": 1}])
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"program": "orca", "native": dict(OPT_NATIVE)}]))
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": None, "program": "orca", "native": dict(OPT_NATIVE)}]))


# ----------------------------------------------------------------------
# Scientific fail-closed: program / native / role / checks
# ----------------------------------------------------------------------


def test_compile_intent_unknown_program_and_missing_program() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "opt@v1", "native": dict(OPT_NATIVE)}]))
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "opt@v1", "program": "nope", "native": dict(OPT_NATIVE)}]))
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "opt@v1", "program": "", "native": dict(OPT_NATIVE)}]))


@pytest.mark.parametrize("native", [None, {}, "x", []])
def test_compile_intent_calc_native_missing_or_empty(native: Any) -> None:
    before = copy.deepcopy(native)
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "opt@v1", "program": "orca", "native": native}]))
    assert native == before


def test_compile_intent_bogus_native_key_rejected() -> None:
    # R2.2: the RANDOMSEED second-authority guard is retired with GOAT;
    # the bogus-key refusal is unchanged.
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": {"keyword": "X", "bogus_key": 1}}]
            )
        )


@pytest.mark.parametrize("role", [123, "", "  "])
def test_compile_intent_bad_role_values(role: Any) -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE), "role": role}]
            )
        )


def test_compile_intent_bad_check_and_recovery_params() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "check_params": [1],
                    }
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "recovery_params": [1],
                    }
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "seed": True,
                    }
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "overrides": {"bogus": 1},
                    }
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "overrides": [1],
                    }
                ]
            )
        )


def test_compile_intent_confgen_seed_inside_native_refused() -> None:
    with pytest.raises(IntentCompilationError, match="step level"):
        compile_intent(
            _intent(
                [
                    {
                        "card": "confgen@v1",
                        "native": {
                            "schema_version": 3,
                            "torsions": copy.deepcopy(TORSIONS),
                            "seed": 5,
                        },
                    }
                ]
            )
        )


@pytest.mark.parametrize(
    "resources",
    ["x", [], {"bogus": 1}, {"cores_per_item": 1, "extra": 2}],
    ids=["x", "resources2", "resources3", "resources4"],
)
def test_compile_intent_bad_resources_refused(resources: Any) -> None:
    before = copy.deepcopy(resources)
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "resources": resources,
                    }
                ]
            )
        )
    assert resources == before


@pytest.mark.parametrize(
    "scheduler",
    ["x", [], {"bogus": 1}, {"max_parallel_items": 1, "extra": 2}],
    ids=["x", "scheduler2", "scheduler3", "scheduler4"],
)
def test_compile_intent_bad_scheduler_refused(scheduler: Any) -> None:
    before = copy.deepcopy(scheduler)
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "scheduler": scheduler,
                    }
                ]
            )
        )
    assert scheduler == before


# ----------------------------------------------------------------------
# Advanced misplaced-field refusals
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "step",
    [
        {
            "card": "confgen@v1",
            "program": "orca",
            "native": {"schema_version": 3, "torsions": copy.deepcopy(TORSIONS)},
        },
        {
            "card": "confgen@v1",
            "preset": "refine_default@v1",
            "native": {"schema_version": 3, "torsions": copy.deepcopy(TORSIONS)},
        },
        {
            "card": "confgen@v1",
            "checks": ["normal_termination"],
            "native": {"schema_version": 3, "torsions": copy.deepcopy(TORSIONS)},
        },
    ],
)
def test_compile_intent_confgen_cannot_consume_calc_fields(step: dict[str, Any]) -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([step]))


def test_compile_intent_transform_cannot_consume_program_or_seed() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "refine@v1", "seed": 3}]))
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "refine@v1", "program": "orca"}]))
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "deduplicate@v1", "program": "orca"}]))


def test_compile_intent_calc_cannot_consume_preset() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "preset": "refine_default@v1",
                    }
                ]
            )
        )


def test_compile_intent_preset_mismatch_and_unknown_keys() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": "deduplicate@v1", "preset": "refine_strict@v1"}]))
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "preset": "nope@v1"},
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "native": {"energy_window": 5.0}},
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "native": {"bogus_param": 1.0}},
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "deduplicate@v1", "native": {"max_structures": 5}},
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "native": "x"},
                ]
            )
        )


def test_compile_intent_bad_bindings_from_execution_shapes() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE), "bindings": [1]}]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent([{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE), "from": 42}])
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE), "from": "   "}]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "execution": [1],
                    }
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE), "label": 42}]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "resources": "x",
                    }
                ]
            )
        )


def test_compile_intent_branching_errors_fail_closed() -> None:
    with pytest.raises(IntentCompilationError, match="unknown predecessor"):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {
                        "card": "sp@v1",
                        "program": "orca",
                        "native": dict(SP_NATIVE),
                        "from": "missing",
                    },
                ]
            )
        )
    with pytest.raises(IntentCompilationError, match="unknown run input"):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {
                        "card": "sp@v1",
                        "program": "orca",
                        "native": dict(SP_NATIVE),
                        "from": "run:missing",
                    },
                ]
            )
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "inputs": {
                    "a": {"kind": "structure", "cardinality": "many"},
                    "b": {"kind": "structure", "cardinality": "many"},
                },
                "steps": [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}],
            }
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "id": "second",
                        "card": "sp@v1",
                        "program": "orca",
                        "native": dict(SP_NATIVE),
                        "from": "first",
                    },
                    {
                        "id": "first",
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                    },
                ]
            )
        )


def test_compile_intent_named_qst_without_bindings_refused() -> None:
    # R2.2: qst2/neb are retired; the references now fail closed as unknown
    # cards (retired vocabulary is never compiled).
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
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {
                        "card": "neb@v1",
                        "program": "orca",
                        "native": {
                            "keyword": "B3LYP NEB",
                            "neb": {"n_images": 7},
                            "atom_mapping": {"kind": "identity"},
                        },
                    }
                ]
            )
        )


# ----------------------------------------------------------------------
# Recipe / card conflict and role-family purpose
# ----------------------------------------------------------------------


def test_compile_intent_unknown_recipe_and_missing_recipe_steps_shape() -> None:
    with pytest.raises(IntentCompilationError, match="unknown intent recipe"):
        compile_intent(
            {"schema": INTENT_SCHEMA, "globals": dict(GLOBALS), "recipe": "nope", "steps": []}
        )
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # malformed-steps shape refusals are unchanged.
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {"schema": INTENT_SCHEMA, "globals": dict(GLOBALS), "recipe": "optimize", "steps": "x"}
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "steps": [42],
            }
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {"schema": INTENT_SCHEMA, "globals": dict(GLOBALS), "recipe": "", "steps": []}
        )


def test_compile_intent_recipe_requires_explicit_science_per_step() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired).
    with pytest.raises(IntentCompilationError, match="missing"):
        compile_intent(
            {"schema": INTENT_SCHEMA, "globals": dict(GLOBALS), "recipe": "optimize", "steps": []}
        )
    partial = [
        {"id": "other", "card": "ts@v1", "program": "orca", "native": {"keyword": "X OptTS"}}
    ]
    with pytest.raises(IntentCompilationError, match="missing"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "steps": partial,
            }
        )


def test_compile_intent_recipe_assignment_needs_program_and_native() -> None:
    for bad in (
        {"id": "optimize", "card": "opt@v1", "native": {"keyword": "X"}},
        {"id": "optimize", "card": "opt@v1", "program": "orca"},
    ):
        with pytest.raises(IntentCompilationError, match="explicit program and native"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "recipe": "optimize",
                    "steps": [bad],
                }
            )


def test_compile_intent_recipe_cards_need_recipe_and_known_cards() -> None:
    cards = _two_cards()
    with pytest.raises(IntentCompilationError, match="need a 'recipe'"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": cards,
                "role_cards": {"opt": "low"},
                "steps": [],
            }
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "cards": cards,
                "role_cards": {"opt": "missing"},
                "steps": [],
            }
        )
    # R2.2: the recipe_cards normal mode is retired: any well-formed
    # declaration fails closed with the retired message, regardless of shape.
    for recipe_cards in (
        {"low_level": "low", "single_point": "missing"},
        {"low_level": "low"},
    ):
        with pytest.raises(IntentCompilationError, match="is retired"):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "recipe": "optimize",
                    "cards": cards,
                    "recipe_cards": recipe_cards,
                    "steps": [],
                }
            )
    with pytest.raises(IntentCompilationError, match="unknown members"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "cards": cards,
                "recipe_cards": {"bogus": "low", "single_point": "high"},
                "steps": [],
            }
        )


def test_compile_intent_recipe_cards_normal_mode_retired() -> None:
    # R2.2: the recipe_cards normal mode is retired with tspes: every
    # recipe, including tspes itself, fails closed with the retired message.
    from confflow.producer.intent import _recipe_cards_to_role_cards

    for recipe_id in ("optimize", "tspes"):
        with pytest.raises(IntentCompilationError, match="is retired"):
            _recipe_cards_to_role_cards(
                {"low_level": "low", "single_point": "high"}, recipe_id=recipe_id
            )


def test_compile_intent_role_cards_conflict_explicit_wins_and_purpose_checked() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # explicit-wins and purpose-checked refusals are unchanged in shape.
    cards = {
        "low": {
            "card": "opt@v1",
            "program": "orca",
            "native": {"keyword": "L Opt"},
        },
        "high": {"card": "sp@v1", "program": "orca", "native": {"keyword": "H SP"}},
    }
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "optimize",
            "cards": cards,
            "role_cards": {"opt": "low"},
            "steps": [
                {
                    "id": "optimize",
                    "card": "opt@v1",
                    "program": "orca",
                    "native": {"keyword": "Explicit Opt"},
                }
            ],
        }
    )
    by_id = {s["id"]: s for s in document["steps"]}
    assert by_id["optimize"]["calculation"]["native"] == {"keyword": "Explicit Opt"}
    assert compile_workflow(document).ok
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "cards": cards,
                "role_cards": {"opt": "high"},
                "steps": [],
            }
        )


def test_compile_intent_family_variant_step_id_wins_and_missing_variant() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # step-id-wins and missing-variant refusals are unchanged in shape.
    cards = {
        "fam": {
            "card": "opt@v1",
            "program": "orca",
            "native_by_role": {"optimize": {"keyword": "F Step"}},
        },
        "opt": {"card": "opt@v1", "program": "orca", "native": {"keyword": "F Opt"}},
    }
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "optimize",
            "cards": cards,
            "role_cards": {"opt": "fam"},
            "steps": [],
        }
    )
    by_id = {s["id"]: s for s in document["steps"]}
    assert by_id["optimize"]["calculation"]["native"] == {"keyword": "F Step"}
    with pytest.raises(IntentCompilationError, match="opt"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "cards": {
                    "fam": {
                        "card": "opt@v1",
                        "program": "orca",
                        "native_by_role": {"other": {"keyword": "O"}},
                    },
                    "opt": {"card": "opt@v1", "program": "orca", "native": {"keyword": "F Opt"}},
                },
                "role_cards": {"opt": "fam"},
                "steps": [],
            }
        )


def test_compile_intent_named_cards_conflicts() -> None:
    with pytest.raises(IntentCompilationError, match="both 'native' and 'native_by_role'"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {
                    "c": {
                        "card": "opt@v1",
                        "native": {"keyword": "X"},
                        "native_by_role": {"opt": {"keyword": "Y"}},
                    }
                },
                "steps": [{"card": "c", "program": "orca", "native": {"keyword": "X"}}],
            }
        )
    with pytest.raises(IntentCompilationError, match="unknown members"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {"c": {"card": "opt@v1", "bogus": 1}},
                "steps": [{"card": "c"}],
            }
        )
    with pytest.raises(IntentCompilationError, match="unknown card"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {"a": {"card": "missing", "program": "orca"}},
                "steps": [{"card": "a", "native": {"keyword": "X"}}],
            }
        )
    with pytest.raises(IntentCompilationError, match="cycle"):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {"a": {"card": "b"}, "b": {"card": "a"}},
                "steps": [{"card": "a", "native": {"keyword": "X"}}],
            }
        )
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {"bad@x": {"card": "opt@v1"}},
                "steps": [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}],
            }
        )


def test_compile_intent_role_cards_shape_refusals() -> None:
    cards = _two_cards()
    for bad_roles in ("x", [], {}, {"": "low"}, {"opt": "has@version"}, {"opt": ""}, {"opt": 42}):
        with pytest.raises(IntentCompilationError):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "recipe": "optimize",
                    "cards": cards,
                    "role_cards": bad_roles,
                    "steps": [],
                }
            )
    for bad_recipe_cards in (
        "x",
        [],
        {"low_level": "has@version"},
        {"low_level": "low", "single_point": 42},
    ):
        with pytest.raises(IntentCompilationError):
            compile_intent(
                {
                    "schema": INTENT_SCHEMA,
                    "globals": dict(GLOBALS),
                    "recipe": "optimize",
                    "cards": cards,
                    "recipe_cards": bad_recipe_cards,
                    "steps": [],
                }
            )


# R2.2 (G18): test_compile_intent_full_tspes_role_and_recipe_cards_agree is
# retired with the tspes recipe and the recipe_cards normal mode.


# ----------------------------------------------------------------------
# Seeds: public API + determinism vs science
# ----------------------------------------------------------------------


def test_needs_seed_matrix() -> None:
    # R2.2: a goat native mapping no longer selects stochastic handling.
    assert (
        needs_seed({"executor": "calculation", "calculation": {"native": {"goat": {"MaxIter": 1}}}})
        is False
    )
    assert (
        needs_seed({"executor": "calculation", "calculation": {"native": {"keyword": "X"}}})
        is False
    )
    assert needs_seed({"executor": "calculation"}) is False
    capped = {"executor": "confgen", "confgen": {"schema_version": 3, "sampling": {"cap": 5}}}
    assert needs_seed(capped) is True
    full = {"executor": "confgen", "confgen": {"schema_version": 3}}
    assert needs_seed(full) is False
    legacy = {"executor": "confgen", "confgen": {"native": {"keyword": "X"}}}
    assert needs_seed(legacy) is True
    assert needs_seed({"executor": "confgen"}) is False
    assert needs_seed({"executor": "analysis"}) is False
    assert needs_seed({"executor": "structure_transform"}) is False


def test_derive_seed_stable_positive_and_explicit_preserved() -> None:
    first = compile_intent(_capped_confgen())
    second = compile_intent(_capped_confgen())
    assert first["steps"][0]["confgen"]["seed"] == second["steps"][0]["confgen"]["seed"]
    assert isinstance(first["steps"][0]["confgen"]["seed"], int)
    assert first["steps"][0]["confgen"]["seed"] >= 1
    explicit = compile_intent(_capped_confgen(seed=77))
    assert explicit["steps"][0]["confgen"]["seed"] == 77
    wire = compile_intent(_capped_confgen())
    assert derive_seed("confgen_1", wire) == derive_seed("confgen_1", wire)


def test_seed_operational_invariance_full_workflow() -> None:
    base = compile_intent(_capped_confgen())
    sched = compile_intent(_capped_confgen(scheduler={"max_parallel_items": 4}))
    assert base["steps"][0]["confgen"]["seed"] == sched["steps"][0]["confgen"]["seed"]
    profile = {"name": "box", "total_cores": 64, "total_memory": "256GiB"}
    machined = compile_intent(_capped_confgen(), machine_profile=profile)
    assert base["steps"][0]["confgen"]["seed"] == machined["steps"][0]["confgen"]["seed"]
    assert (
        compile_workflow(base).plan.definition_digest
        == compile_workflow(machined).plan.definition_digest
    )
    described = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "inputs": {
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "grouping": "each_entity",
                    "description": "display",
                }
            },
            "steps": _capped_confgen()["steps"],
        }
    )
    assert base["steps"][0]["confgen"]["seed"] == described["steps"][0]["confgen"]["seed"]


def test_seed_scientific_changes_move_seed() -> None:
    base_seed = compile_intent(_capped_confgen())["steps"][0]["confgen"]["seed"]
    changed_native = compile_intent(
        _intent(
            [
                {
                    "card": "confgen@v1",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 51},
                    },
                }
            ]
        )
    )["steps"][0]["confgen"]["seed"]
    assert changed_native != base_seed
    changed_res = compile_intent(_capped_confgen(resources={"cores_per_item": 4}))["steps"][0][
        "confgen"
    ]["seed"]
    assert changed_res != base_seed
    changed_enabled = copy.deepcopy(compile_intent(_capped_confgen()))
    changed_enabled["steps"][0]["enabled"] = False
    assert derive_seed("confgen_1", compile_intent(_capped_confgen())) != derive_seed(
        "confgen_1", changed_enabled
    )
    changed_completion = copy.deepcopy(compile_intent(_capped_confgen()))
    changed_completion["steps"][0]["completion"] = {"mode": "allow_partial"}
    assert derive_seed("confgen_1", compile_intent(_capped_confgen())) != derive_seed(
        "confgen_1", changed_completion
    )


def test_seed_memory_spelling_canonical_and_topology_canonical() -> None:
    def _seed_for(memory: str) -> int:
        return compile_intent(
            _capped_confgen(resources={"cores_per_item": 1, "memory_per_item": memory})
        )["steps"][0]["confgen"]["seed"]

    assert _seed_for("1GiB") == _seed_for("1024MiB")

    def _seed_for_topo(topo: Any) -> int:
        return compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "inputs": {
                    "structures": {
                        "kind": "structure",
                        "cardinality": "many",
                        "grouping": "each_entity",
                        "topology": topo,
                    }
                },
                "steps": _capped_confgen()["steps"],
            }
        )["steps"][0]["confgen"]["seed"]

    assert _seed_for_topo({"add": [[1, 2]], "delete": [], "provenance": "a"}) == _seed_for_topo(
        {"add": [[2, 1]], "delete": [], "provenance": "b"}
    )
    assert _seed_for_topo({"add": [[1, 2]], "delete": []}) != _seed_for_topo(
        {"add": [[1, 3]], "delete": []}
    )


def test_seed_identity_topology_invalid_deterministic_and_empty_collapses() -> None:
    wire = {"inputs": {}, "global": {}, "steps": []}
    assert seed_identity_for_step("s", wire)["seed_version"] == "v1"
    doc_a = {"inputs": {"x": {"topology": {"add": "bad", "provenance": "p"}}}, "steps": []}
    doc_b = {"inputs": {"x": {"topology": {"add": "bad", "provenance": "other"}}}, "steps": []}
    assert seed_identity_for_step("s", doc_a) == seed_identity_for_step("s", doc_b)
    assert derive_seed("s", doc_a) == derive_seed("s", doc_b)
    doc_empty = {
        "inputs": {"x": {"topology": {"add": [], "delete": [], "provenance": "p"}}},
        "steps": [],
    }
    doc_absent = {"inputs": {"x": {}}, "steps": []}
    assert seed_identity_for_step("s", doc_empty) == seed_identity_for_step("s", doc_absent)
    assert seed_identity_for_step("s", {"inputs": "bad", "steps": []})["inputs"] == {}


def test_assign_seeds_provenance_and_explicit_audit() -> None:
    document, provenance = assign_seeds(
        {
            "steps": [
                {
                    "id": "a",
                    "executor": "confgen",
                    "confgen": {"schema_version": 3, "sampling": {"cap": 3}},
                },
                {"id": "b", "executor": "calculation", "calculation": {"native": {"keyword": "X"}}},
                "not-a-step",
            ]
        }
    )
    assert provenance["a"]["source"] == "derived"
    assert provenance["b"]["source"] == "none"
    assert provenance["b"]["seed"] is None
    assert isinstance(document["steps"][0]["confgen"]["seed"], int)
    explicit_doc, explicit_prov = assign_seeds(
        {"steps": [{"id": "a", "executor": "confgen", "confgen": {"schema_version": 3, "seed": 9}}]}
    )
    assert explicit_prov["a"] == {"seed": 9, "source": "explicit", "seed_version": "v1"}
    assert explicit_doc["steps"][0]["confgen"]["seed"] == 9


def test_seed_step_order_permutation_invariant() -> None:
    two = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "steps": [
                {
                    "id": "aaa",
                    "card": "confgen@v1",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 50},
                    },
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                },
                {
                    "id": "zzz",
                    "card": "opt@v1",
                    "program": "orca",
                    "native": dict(OPT_NATIVE),
                    "bindings": {"structure": {"source": {"step": "aaa", "port": "structures"}}},
                },
            ],
        }
    )
    swapped = copy.deepcopy(two)
    swapped["steps"] = [swapped["steps"][1], swapped["steps"][0]]
    assert derive_seed("aaa", two) == derive_seed("aaa", swapped)
    assert derive_seed("zzz", two) == derive_seed("zzz", swapped)


# ----------------------------------------------------------------------
# Checkpoint intents through public compile
# ----------------------------------------------------------------------


def _checkpoint_intent(extra: dict[str, Any] | None = None) -> dict[str, Any]:
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
                "native": {"keyword": "B3LYP/6-31G(d) Opt"},
                "reuse_checkpoint": reuse,
            },
        ],
    }


def test_checkpoint_intent_happy_path_and_flag_required() -> None:
    document = compile_intent(_checkpoint_intent())
    assert document["steps"][1]["bindings"]["checkpoint"]["cardinality"] == "one"
    assert compile_workflow(document).ok
    other = {
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
                "reuse_checkpoint": {"step": "opt", "mode": "readfc"},
            },
        ],
    }
    with pytest.raises(IntentCompilationError):
        compile_intent(other)
    allowed = copy.deepcopy(other)
    allowed["steps"][1]["reuse_checkpoint"]["allow_method_change"] = True
    assert compile_workflow(compile_intent(allowed)).ok


@pytest.mark.parametrize(
    "reuse",
    [
        "x",
        [],
        {"mode": "checkpoint"},
        {"step": "opt"},
        {"step": "", "mode": "checkpoint"},
        {"step": "opt", "mode": "bogus"},
        {"step": "opt", "mode": "checkpoint", "allow_method_change": "yes"},
    ],
    ids=["x", "reuse2", "reuse3", "reuse4", "reuse5", "reuse6", "reuse7"],
)
def test_checkpoint_intent_malformed_refused(reuse: Any) -> None:
    intent = {
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
                "native": {"keyword": "B3LYP/6-31G(d) Opt"},
                "reuse_checkpoint": reuse,
            },
        ],
    }
    with pytest.raises(IntentCompilationError):
        compile_intent(intent)


def test_checkpoint_intent_unknown_source_step_refused() -> None:
    intent = _checkpoint_intent()
    intent["steps"][1]["reuse_checkpoint"] = {"step": "missing", "mode": "checkpoint"}
    with pytest.raises(IntentCompilationError):
        compile_intent(intent)


# ----------------------------------------------------------------------
# Authoring: describe_step / binding_candidates / instantiate / validate
# ----------------------------------------------------------------------


def _compiled_opt_doc() -> dict[str, Any]:
    return compile_intent(_opt_chain())


def test_describe_step_projection_and_unknown_step() -> None:
    document = _compiled_opt_doc()
    before = copy.deepcopy(document)
    answer = describe_step(document, "opt_1")
    assert answer["ok"] is True
    assert answer["operation"] == "describe_step"
    assert answer["result"]["step_id"] == "opt_1"
    assert answer["result"]["capability"] == "calculation"
    assert document == before
    refused = describe_step(document, "missing")
    assert refused["ok"] is False
    assert refused["result"] is None
    non_string = describe_step(document, 42)
    assert non_string["ok"] is False
    assert non_string["result"] is None


@pytest.mark.parametrize("document", [b"\xff\xfe", "{bad yaml: [", 42, None, []])
def test_describe_step_unparseable_fails_closed(document: Any) -> None:
    answer = describe_step(document, "opt_1")
    assert answer["ok"] is False
    assert answer["result"] is None
    assert answer["diagnostics"]


def test_describe_step_accepts_text_and_bytes_documents() -> None:
    document = _compiled_opt_doc()
    text = json.dumps(document)
    assert describe_step(text, "opt_1")["ok"] is True
    assert describe_step(text.encode("utf-8"), "opt_1")["ok"] is True


def test_binding_candidates_lists_legal_sources_and_stale_warns() -> None:
    document = _compiled_opt_doc()
    answer = binding_candidates(document, "opt_1", "structure")
    assert answer["ok"] is True
    assert isinstance(answer["result"], list)
    assert len(answer["result"]) >= 1
    first = answer["result"][0]
    assert first["target_step_id"] == "opt_1"
    assert first["compatibility"] in ("compatible", "needs_revalidation", "unsupported")
    assert first["auto_wire"] is False
    assert "user_action_not_explicit" in first["auto_wire_reasons"]
    stale = binding_candidates(
        document, "opt_1", "structure", expected_document_digest="sha256:" + "0" * 64
    )
    assert stale["ok"] is True
    assert any(d.get("message", "").find("does not match") >= 0 for d in stale["diagnostics"])
    fresh_digest = stale["request_document_digest"]
    fresh = binding_candidates(
        document, "opt_1", "structure", expected_document_digest=fresh_digest, action="add"
    )
    assert fresh["ok"] is True


def test_binding_candidates_unknown_step_and_port() -> None:
    document = _compiled_opt_doc()
    assert binding_candidates(document, "missing", "structure")["ok"] is False
    assert binding_candidates(document, 42, "structure")["ok"] is False
    refused = binding_candidates(document, "opt_1", "missing_port")
    assert refused["ok"] is False
    assert refused["result"] is None
    assert binding_candidates(document, "opt_1", 42)["ok"] is False


@pytest.mark.parametrize("document", [b"\xff", "{bad: [", 42])
def test_binding_candidates_unparseable_fails_closed(document: Any) -> None:
    assert binding_candidates(document, "opt_1", "structure")["ok"] is False


def test_instantiate_card_snapshot_variants() -> None:
    assert instantiate_card(None)["ok"] is False
    assert instantiate_card({})["ok"] is False
    assert instantiate_card("")["ok"] is False
    assert instantiate_card({"nonsense": 1})["ok"] is False
    assert instantiate_card({"document": {"steps": []}})["ok"] is False
    bad_context = instantiate_card({"executor": "calculation"}, None, "not-mapping-at-all??? [")
    assert bad_context["ok"] is False
    assert bad_context["result"] is None
    assert bad_context["diagnostics"]
    assert any(
        "mapping" in str(d.get("message", "")).lower()
        or "unparseable" in str(d.get("reason", "")).lower()
        for d in bad_context["diagnostics"]
    )
    bad_choice = instantiate_card({"executor": "calculation"}, None, None, {"structure": 42})
    assert bad_choice["ok"] is False


def test_instantiate_card_allocates_ids_and_validates() -> None:
    document = _compiled_opt_doc()
    snapshot = {"id": "opt_1", "executor": "calculation"}
    answer = instantiate_card(snapshot, "opt_1", document, None)
    assert answer["ok"] is False
    assert answer["diagnostics"]
    assert answer["result"]["allocated"] is True
    assert answer["result"]["step_id"] != "opt_1"
    fresh = instantiate_card(snapshot, "brand_new", document, None)
    assert fresh["result"]["step_id"] == "brand_new"
    assert fresh["result"]["allocated"] is False
    illegal = instantiate_card(snapshot, "9bad!!!", document, None)
    assert illegal["result"]["allocated"] is True


def test_validate_document_ok_and_refusals() -> None:
    document = _compiled_opt_doc()
    before = copy.deepcopy(document)
    good = validate_document(document)
    assert good["ok"] is True
    assert document == before
    assert validate_document(json.dumps(document))["ok"] is True
    assert validate_document(json.dumps(document).encode())["ok"] is True
    bad = validate_document({"schema": "confflow.workflow.v4", "steps": []})
    assert bad["ok"] is False
    assert bad["result"] is not None
    assert validate_document(42)["ok"] is False
    assert validate_document(b"\xff\xfe")["ok"] is False


# ----------------------------------------------------------------------
# Authoring dispatch boundaries
# ----------------------------------------------------------------------


def _request(operation: str, **fields: Any) -> str:
    payload: dict[str, Any] = {"content_schema": "confflow.authoring.v4", "operation": operation}
    payload.update(fields)
    return json.dumps(payload)


def test_dispatch_malformed_requests_fail_closed() -> None:
    assert dispatch_request("not json")["ok"] is False
    assert dispatch_request(b"\xff\xfe")["ok"] is False
    assert dispatch_request(json.dumps([1, 2]))["ok"] is False
    unknown = dispatch_request(_request("nope"))
    assert unknown["ok"] is False
    assert unknown["operation"] is None
    missing_schema = dispatch_request(json.dumps({"operation": "describe_step"}))
    assert missing_schema["ok"] is False


def test_dispatch_schema_violations_fail_closed() -> None:
    bad = json.dumps({"content_schema": "wrong", "operation": "describe_step"})
    answer = dispatch_request(bad)
    assert answer["ok"] is False
    assert answer["operation"] == "describe_step"


def test_dispatch_describe_and_validate_roundtrip() -> None:
    document = _compiled_opt_doc()
    answer = dispatch_request(
        _request("describe_step", document=document, parameters={"step_id": "opt_1"})
    )
    assert answer["ok"] is True
    assert answer["result"]["step_id"] == "opt_1"
    validated = dispatch_request(_request("validate_document", document=document))
    assert validated["ok"] is True


def test_dispatch_binding_candidates_and_instantiate() -> None:
    document = _compiled_opt_doc()
    answer = dispatch_request(
        _request(
            "binding_candidates",
            document=document,
            parameters={"target_step_id": "opt_1", "target_port": "structure"},
        )
    )
    assert answer["ok"] is True
    assert isinstance(answer["result"], list)
    instantiated = dispatch_request(
        _request(
            "instantiate_card",
            document=document,
            parameters={
                "snapshot": {"id": "opt_1", "executor": "calculation"},
                "requested_step_id": "fresh_one",
            },
        )
    )
    assert instantiated["operation"] == "instantiate_card"
    assert instantiated["result"]["step_id"] == "fresh_one"


def test_dispatch_compile_intent_success_and_scientific_failure() -> None:
    good = dispatch_request(_request("compile_intent", parameters={"intent": _opt_chain()}))
    assert good["ok"] is True
    assert good["result"]["document"]["schema"] == "confflow.workflow.v4"
    bad = dispatch_request(_request("compile_intent", parameters={"intent": {"schema": "bogus"}}))
    assert bad["ok"] is False
    assert bad["result"] is None
    non_object = dispatch_request(_request("compile_intent", parameters={"intent": [1]}))
    assert non_object["ok"] is False
    with_profile = dispatch_request(
        _request(
            "compile_intent",
            parameters={
                "intent": _opt_chain(),
                "machine_profile": {"name": "b", "total_cores": 4, "total_memory": "8GiB"},
            },
        )
    )
    assert with_profile["ok"] is True


def test_dispatch_preview_paths_and_unimplemented_operation() -> None:
    pending = dispatch_request(_request("check_compatibility"))
    assert pending["ok"] is False
    assert "not implemented" in pending["diagnostics"][0]["message"]
    preview_bad = dispatch_request(_request("preview_paths", parameters={}))
    assert preview_bad["ok"] is False
    assert preview_bad["result"] is None


# ----------------------------------------------------------------------
# Boundary: canonical topology/memory + JCS + compatibility
# ----------------------------------------------------------------------


def test_canonicalize_text_vectors_and_rejections() -> None:
    vectors = jcs_vectors()
    assert len(vectors) >= 10
    by_id = {v["id"]: v for v in vectors}
    assert by_id["scalar_float_one"]["expect"] == "ok"
    assert by_id["duplicate_keys_rejected"]["expect"] == "rejected"
    assert by_id["duplicate_keys_rejected"]["reason_code"] == "duplicate_mapping_key"
    assert canonicalize_text('{"a":1,"a":2}')["reason_code"] == "duplicate_mapping_key"
    assert canonicalize_text('{"x":NaN}')["reason_code"] == "non_finite_number"
    assert canonicalize_text('{"x":[1,-Infinity]}')["reason_code"] == "non_finite_number"
    assert canonicalize_text(b"\xff\xfe")["reason_code"] == "invalid_json"
    assert canonicalize_text("not json")["reason_code"] == "invalid_json"
    beyond = canonicalize_text("9007199254740993")
    assert beyond["ok"] is False
    assert beyond["reason_code"] == "integer_out_of_domain"
    ok = canonicalize_text('{"b":2,"a":1}')
    assert ok["ok"] is True
    assert ok["canonical_json"] == '{"a":1,"b":2}'


def test_boundary_module_identity_constants() -> None:
    assert boundary.BOUNDARY_PROTOCOL_ID == "confflow.boundary.v4"
    assert boundary.AUTHORING_PROTOCOL_SCHEMA == "confflow.authoring.v4"
    assert set(boundary.COMPATIBILITY_STATUSES) == {
        "compatible",
        "needs_revalidation",
        "unsupported",
    }
    doc = boundary.canonicalization_document()
    assert doc["algorithm"].startswith("RFC 8785")
    assert len(doc["vectors"]) == len(jcs_vectors())


# ----------------------------------------------------------------------
# Machine profile + legacy passthrough boundaries
# ----------------------------------------------------------------------


def test_machine_profile_bad_shapes_fail_closed() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_opt_chain(), machine_profile="x")  # type: ignore[arg-type]
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _opt_chain(), machine_profile={"name": "b", "total_cores": -1, "total_memory": "8GiB"}
        )


def test_machine_profile_operational_only_seed_stable() -> None:
    profile = {"name": "box", "total_cores": 64, "total_memory": "256GiB"}
    plain = compile_intent(_opt_chain())
    machined = compile_intent(_opt_chain(), machine_profile=profile)
    assert machined["steps"][0]["execution"]["binding_id"] == "machine-box"
    assert (
        compile_workflow(plain).plan.definition_digest
        == compile_workflow(machined).plan.definition_digest
    )


def test_legacy_passthrough_verbatim_and_stochastic_refused() -> None:
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
    before = copy.deepcopy(legacy)
    assert compile_intent(legacy) == legacy
    assert legacy == before
    stochastic = copy.deepcopy(legacy)
    stochastic["steps"][0]["calculation"]["native"] = {"keyword": "X", "goat": {"MaxIter": 5}}
    with pytest.raises(IntentCompilationError):
        compile_intent(stochastic)
    broken = copy.deepcopy(legacy)
    broken["steps"][0]["calculation"] = {"program": "orca"}
    with pytest.raises(IntentCompilationError):
        compile_intent(broken)


def test_error_carries_value_error_and_input_unchanged_on_failure() -> None:
    bad = _intent([{"card": "banana@v1"}])
    before = copy.deepcopy(bad)
    with pytest.raises(ValueError):
        compile_intent(bad)
    assert bad == before
    with pytest.raises(IntentCompilationError) as excinfo:
        compile_intent(
            _intent(
                [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE), "bindings": [1]}]
            )
        )
    assert isinstance(excinfo.value, ValueError)


def test_full_workflow_seed_invariance_end_to_end() -> None:
    def _build(**step_extra: Any) -> dict[str, Any]:
        step: dict[str, Any] = {
            "card": "confgen@v1",
            "native": {
                "schema_version": 3,
                "torsions": copy.deepcopy(TORSIONS),
                "sampling": {"cap": 50},
            },
        }
        step.update(step_extra)
        return _intent(
            [
                step,
                {
                    "card": "opt@v1",
                    "program": "orca",
                    "native": dict(OPT_NATIVE),
                    "from": "confgen_1",
                },
            ]
        )

    base = compile_intent(_build())
    sched_variant = compile_intent(_build(scheduler={"max_parallel_items": 2}))
    assert base["steps"][0]["confgen"]["seed"] == sched_variant["steps"][0]["confgen"]["seed"]
    moved = compile_intent(_build(resources={"cores_per_item": 8}))
    assert moved["steps"][0]["confgen"]["seed"] != base["steps"][0]["confgen"]["seed"]
    assert compile_workflow(base).ok
    assert compile_workflow(sched_variant).ok


# ----------------------------------------------------------------------
# Round 2: remaining CI-uncovered intent lanes (public compile_intent only)
# ----------------------------------------------------------------------


def test_r2_chained_named_cards_replace_wholesale() -> None:
    before = {
        "schema": INTENT_SCHEMA,
        "globals": dict(GLOBALS),
        "cards": {
            "base": {"card": "opt@v1", "program": "orca", "native": {"keyword": "B Opt"}},
            "mid": {"card": "base", "resources": {"cores_per_item": 2}},
        },
        "steps": [{"card": "mid", "native": {"keyword": "S Opt"}}],
    }
    snapshot = copy.deepcopy(before)
    document = compile_intent(before)
    assert before == snapshot
    calc = document["steps"][0]["calculation"]
    assert calc["native"] == {"keyword": "S Opt"}
    assert calc["program"] == "orca"
    assert document["steps"][0]["resources"] == {"cores_per_item": 2}
    assert compile_workflow(document).ok


def test_r2_chained_three_level_resolution() -> None:
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "cards": {
                "a": {"card": "opt@v1", "program": "orca", "native": {"keyword": "A Opt"}},
                "b": {"card": "a", "scheduler": {"max_parallel_items": 2}},
                "c": {"card": "b", "native": {"keyword": "C Opt"}},
            },
            "steps": [{"card": "c"}],
        }
    )
    assert document["steps"][0]["calculation"]["native"] == {"keyword": "C Opt"}
    assert document["steps"][0]["scheduler"] == {"max_parallel_items": 2}
    assert compile_workflow(document).ok


@pytest.mark.parametrize(
    "cards",
    [
        "x",
        {"bad@x": {"card": "opt@v1"}},
        {"c": "x"},
        {"c": {"card": "opt@v1", "native": "x"}},
        {"c": {"card": "opt@v1", "native_by_role": {}}},
        {"c": {"card": "opt@v1", "native_by_role": {"": {"keyword": "X"}}}},
        {"c": {"card": "opt@v1", "native_by_role": {"opt": {}}}},
        {"c": {"card": 42}},
    ],
)
def test_r2_named_card_definition_refusals(cards: Any) -> None:
    before = copy.deepcopy(cards)
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": cards,
                "steps": [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}],
            }
        )
    assert cards == before


def test_r2_step_mapping_card_ref_passes_through() -> None:
    before = _intent(
        [{"card": {"type": "opt", "version": "v1"}, "program": "orca", "native": dict(OPT_NATIVE)}]
    )
    snapshot = copy.deepcopy(before)
    document = compile_intent(before)
    assert before == snapshot
    assert document["steps"][0]["calculation"]["program"] == "orca"
    assert compile_workflow(document).ok


@pytest.mark.parametrize("ref", [42, "   "])
def test_r2_step_card_ref_shapes_refused(ref: Any) -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([{"card": ref, "program": "orca", "native": dict(OPT_NATIVE)}]))


def test_r2_step_unknown_named_card_refused() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {"c": {"card": "opt@v1"}},
                "steps": [{"card": "missing", "program": "orca", "native": dict(OPT_NATIVE)}],
            }
        )


def test_r2_named_family_role_hint_positive() -> None:
    before = {
        "schema": INTENT_SCHEMA,
        "globals": dict(GLOBALS),
        "cards": {
            "fam": {
                "card": "opt@v1",
                "program": "orca",
                "native_by_role": {"opt": {"keyword": "F Opt"}},
            }
        },
        "steps": [{"card": "fam", "role": "opt"}],
    }
    snapshot = copy.deepcopy(before)
    document = compile_intent(before)
    assert before == snapshot
    assert document["steps"][0]["calculation"]["native"] == {"keyword": "F Opt"}
    assert compile_workflow(document).ok


@pytest.mark.parametrize(
    "native_by_role",
    [
        {"sp": {"keyword": "F SP"}},
        {"opt": "bad"},
        "bad",
    ],
)
def test_r2_named_family_variant_refusals(native_by_role: Any) -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "cards": {
                    "fam": {"card": "opt@v1", "program": "orca", "native_by_role": native_by_role}
                },
                "steps": [{"card": "fam", "role": "opt"}],
            }
        )


def test_r2_recipe_patch_via_named_card_preserves_annotation() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired).
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "optimize",
            "cards": {"fam": {"card": "opt@v1", "program": "orca", "native": {"keyword": "F Opt"}}},
            "steps": [
                {
                    "id": "optimize",
                    "card": "fam",
                }
            ],
        }
    )
    by_id = {s["id"]: s for s in document["steps"]}
    assert by_id["optimize"]["calculation"]["native"] == {"keyword": "F Opt"}
    resolution = by_id["optimize"]["annotations"]["producer_resolution"]
    assert resolution["named_card"] == "fam"
    assert resolution["recipe_assignment"] is True
    assert compile_workflow(document).ok


def test_r2_role_cards_full_operational_preservation() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # input-immutability and operational-preservation assertions hold.
    cards = {
        "low": {
            "card": "opt@v1",
            "program": "orca",
            "seed": 5,
            "overrides": {"charge": 1},
            "resources": {"cores_per_item": 2},
            "scheduler": {"max_parallel_items": 3},
            "native": {"keyword": "L Opt"},
        },
    }
    before = {
        "schema": INTENT_SCHEMA,
        "globals": dict(GLOBALS),
        "recipe": "optimize",
        "cards": cards,
        "role_cards": {"opt": "low"},
        "steps": [],
    }
    snapshot = copy.deepcopy(before)
    document = compile_intent(before)
    assert before == snapshot
    by_id = {s["id"]: s for s in document["steps"]}
    assert by_id["optimize"]["calculation"]["seed"] == 5
    assert by_id["optimize"]["calculation"]["overrides"] == {"charge": 1}
    assert by_id["optimize"]["resources"] == {"cores_per_item": 2}
    assert by_id["optimize"]["scheduler"] == {"max_parallel_items": 3}
    assert by_id["optimize"]["annotations"]["producer_resolution"]["role_card"] == "low"
    assert compile_workflow(document).ok


def test_r2_role_cards_scientific_defaults_preservation() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired).
    cards = {
        "rich": {
            "card": "opt@v1",
            "program": "orca",
            "adapter": "standard",
            "profile": "standard",
            "checks": ["normal_termination"],
            "check_params": {},
            "recovery": "none",
            "native": {"keyword": "R Opt"},
        },
    }
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "optimize",
            "cards": cards,
            "role_cards": {"opt": "rich"},
            "steps": [],
        }
    )
    calc = {s["id"]: s for s in document["steps"]}["optimize"]["calculation"]
    assert calc["execution_adapter"] == "standard"
    assert calc["result_profile"] == "standard"
    assert calc["checks"] == ["normal_termination"]
    assert calc["native"] == {"keyword": "R Opt"}
    assert compile_workflow(document).ok


def test_r2_role_cards_skip_ids_explicit_wins() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired).
    cards = _two_cards()
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "optimize",
            "cards": cards,
            "role_cards": {"opt": "low"},
            "steps": [
                {
                    "id": "optimize",
                    "card": "opt@v1",
                    "program": "orca",
                    "native": {"keyword": "Explicit Opt"},
                }
            ],
        }
    )
    by_id = {s["id"]: s for s in document["steps"]}
    assert by_id["optimize"]["calculation"]["native"] == {"keyword": "Explicit Opt"}
    assert compile_workflow(document).ok


@pytest.mark.parametrize(
    "cards",
    [
        {
            "low": {"card": "opt@v1", "program": "orca", "native_by_role": {"ts": {"k": "v"}}},
        },
        {
            "low": {"card": "opt@v1", "native": {"keyword": "L Opt"}},
        },
    ],
)
def test_r2_role_cards_family_and_science_refusals(cards: Any) -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # family-variant and missing-program refusals are unchanged in shape.
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "cards": cards,
                "role_cards": {"opt": "low"},
                "steps": [],
            }
        )


def test_r2_role_cards_step_id_wins_over_role_purpose() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # step-id-wins and role-purpose refusals are unchanged in shape.
    cards = {
        "optcard": {"card": "opt@v1", "program": "orca", "native": {"keyword": "Q Opt"}},
        "spcard": {"card": "sp@v1", "program": "orca", "native": {"keyword": "Q SP"}},
    }
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "cards": cards,
                "role_cards": {"opt": "spcard"},
                "steps": [],
            }
        )
    document = compile_intent(
        {
            "schema": INTENT_SCHEMA,
            "globals": dict(GLOBALS),
            "recipe": "optimize",
            "cards": cards,
            "role_cards": {"optimize": "spcard"},
            "steps": [],
        }
    )
    assert len(document["steps"]) == 1
    assert compile_workflow(document).ok


def test_r2_calc_scientific_fields_preserved() -> None:
    before = _intent(
        [
            {
                "card": "opt@v1",
                "program": "gaussian",
                "native": {"keyword": "B3LYP/6-31G(d) Opt"},
                "role": "opt",
                "check_params": {"normal_termination": {}},
                "recovery_params": {"max_attempts": 2},
                "seed": 11,
                "overrides": {"charge": 0, "multiplicity": 1},
            }
        ]
    )
    snapshot = copy.deepcopy(before)
    document = compile_intent(before)
    assert before == snapshot
    calc = document["steps"][0]["calculation"]
    assert calc["role"] == "opt"
    assert calc["check_params"] == {"normal_termination": {}}
    assert calc["recovery"]["params"] == {"max_attempts": 2}
    assert calc["seed"] == 11
    assert calc["overrides"] == {"charge": 0, "multiplicity": 1}
    assert compile_workflow(document).ok


def test_r2_calc_default_role_applied() -> None:
    document = compile_intent(_opt_chain())
    assert document["steps"][0]["calculation"]["role"] == "opt"
    assert document["steps"][0]["calculation"]["checks"] == [
        "normal_termination",
        "geometry_required",
    ]
    assert compile_workflow(document).ok


def test_r2_confgen_v3_seed_overrides_preserved() -> None:
    document = compile_intent(
        _intent(
            [
                {
                    "card": "confgen@v1",
                    "native": {
                        "schema_version": 3,
                        "torsions": copy.deepcopy(TORSIONS),
                        "sampling": {"cap": 50},
                    },
                    "seed": 7,
                    "overrides": {"charge": 1},
                }
            ]
        )
    )
    block = document["steps"][0]["confgen"]
    assert block["seed"] == 7
    assert block["overrides"] == {"charge": 1}
    assert block["schema_version"] == 3
    assert compile_workflow(document).ok


@pytest.mark.parametrize(
    "native",
    [
        {"max_structures": 5},
        "x",
    ],
)
def test_r2_dedup_native_refused(native: Any) -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "deduplicate@v1", "native": native},
                ]
            )
        )


@pytest.mark.parametrize(
    "native",
    [
        {"energy_window": 5.0},
        {"bogus_param": 1.0},
        "x",
    ],
)
def test_r2_refine_native_refused(native: Any) -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "native": native},
                ]
            )
        )


def test_r2_refine_preset_mismatch_refused() -> None:
    with pytest.raises(IntentCompilationError):
        compile_intent(
            _intent(
                [
                    {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                    {"card": "refine@v1", "preset": "dedup_default@v1"},
                ]
            )
        )


def test_r2_refine_dedup_positive() -> None:
    document = compile_intent(
        _intent(
            [
                {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
                {"card": "refine@v1", "native": {"rmsd_threshold_angstrom": 0.5}},
                {"card": "deduplicate@v1"},
            ]
        )
    )
    assert len(document["steps"]) == 3
    assert compile_workflow(document).ok


@pytest.mark.parametrize(
    "extra",
    [
        {"label": 42},
        {"execution": "x"},
    ],
)
def test_r2_step_label_execution_refusals(extra: Any) -> None:
    step: dict[str, Any] = {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}
    step.update(extra)
    with pytest.raises(IntentCompilationError):
        compile_intent(_intent([step]))


def test_r2_bogus_bindings_fail_strict_compilation() -> None:
    before = _intent(
        [
            {
                "card": "opt@v1",
                "program": "orca",
                "native": dict(OPT_NATIVE),
                "bindings": {"structure": {"source": {"step": "nope", "port": "structures"}}},
            }
        ]
    )
    snapshot = copy.deepcopy(before)
    with pytest.raises(IntentCompilationError, match="strict compilation"):
        compile_intent(before)
    assert before == snapshot


def test_r2_recipe_patch_rich_science_preserved() -> None:
    # R2.2: vehicle is the retained optimize recipe (tspes retired); the
    # rich-assignment patch assertions are unchanged in shape.
    rich = {
        "id": "optimize",
        "card": "opt@v1",
        "program": "orca",
        "native": {"keyword": "E o"},
        "role": "opt",
        "adapter": "standard",
        "profile": "standard",
        "checks": ["normal_termination"],
        "check_params": {},
        "recovery": "none",
        "seed": 3,
        "overrides": {"charge": 0},
        "resources": {"cores_per_item": 1},
        "scheduler": {"max_parallel_items": 2},
    }
    before = {
        "schema": INTENT_SCHEMA,
        "globals": dict(GLOBALS),
        "recipe": "optimize",
        "steps": [rich],
    }
    snapshot = copy.deepcopy(before)
    document = compile_intent(before)
    assert before == snapshot
    calc = {s["id"]: s for s in document["steps"]}["optimize"]["calculation"]
    assert calc["role"] == "opt"
    assert calc["execution_adapter"] == "standard"
    assert calc["checks"] == ["normal_termination"]
    assert calc["seed"] == 3
    assert calc["overrides"] == {"charge": 0}
    assert compile_workflow(document).ok
    assert [s["id"] for s in document["steps"]] == ["optimize"]


def test_r2_recipe_assignment_native_not_mapping() -> None:
    steps = [{"id": "optimize", "card": "opt@v1", "program": "orca", "native": "x"}]
    with pytest.raises(IntentCompilationError):
        compile_intent(
            {
                "schema": INTENT_SCHEMA,
                "globals": dict(GLOBALS),
                "recipe": "optimize",
                "steps": steps,
            }
        )


# R2.2 (G18): test_r2_goat_seed_scope_workflow_identity is retired with GOAT.


def test_r2_freeze_positive_and_empty_clear() -> None:
    ordered = compile_intent(
        _intent(
            [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}],
            globals={"charge": 0, "multiplicity": 1, "freeze": [2, 1, 2]},
        )
    )
    assert ordered["global"]["scientific_defaults"]["freeze"] == [1, 2]
    assert compile_workflow(ordered).ok
    cleared = compile_intent(
        _intent(
            [{"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)}],
            globals={"charge": 0, "multiplicity": 1, "freeze": []},
        )
    )
    assert cleared["global"]["scientific_defaults"]["freeze"] == []
    assert compile_workflow(cleared).ok


def test_r2_explicit_id_collision_allocates_next() -> None:
    document = compile_intent(
        _intent(
            [
                {
                    "id": "opt_1",
                    "card": "opt@v1",
                    "program": "orca",
                    "native": dict(OPT_NATIVE),
                },
                {"card": "opt@v1", "program": "orca", "native": dict(OPT_NATIVE)},
            ]
        )
    )
    assert [s["id"] for s in document["steps"]] == ["opt_1", "opt_2"]
    assert compile_workflow(document).ok


def test_r2_coerce_source_forms() -> None:
    document = compile_intent(intent=_opt_chain())
    assert compile_workflow(document).ok
    with pytest.raises(IntentCompilationError):
        compile_intent()


def test_r2_confgen_seed_scope_native_sampling() -> None:
    document = compile_intent(
        _intent(
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
    )
    resolution = document["steps"][0]["annotations"]["producer_resolution"]
    assert resolution["seed_scope"] == "native_sampling"
    assert compile_workflow(document).ok


def test_r2_machine_profile_operational_and_provenance() -> None:
    before = _opt_chain()
    snapshot = copy.deepcopy(before)
    document = compile_intent(
        before, machine_profile={"name": "box", "total_cores": 64, "total_memory": "256GiB"}
    )
    assert before == snapshot
    resolution = document["steps"][0]["annotations"]["producer_resolution"]
    assert resolution["machine_profile"] == "box"
    assert resolution["machine_provenance"]
    assert compile_workflow(document).ok


def test_r2_machine_explicit_execution_wins() -> None:
    document = compile_intent(
        _intent(
            [
                {
                    "card": "opt@v1",
                    "program": "orca",
                    "native": dict(OPT_NATIVE),
                    "execution": {"env": {"A": "1"}},
                }
            ]
        ),
        machine_profile={"name": "box", "total_cores": 64, "total_memory": "256GiB"},
    )
    assert document["steps"][0]["execution"]["env"] == {"A": "1"}
    assert compile_workflow(document).ok


def test_r2_intent_execution_target_retired() -> None:
    # R2.2: an explicit execution target in an intent step fails closed
    # (the wire schema would also reject it, but the intent lane reports
    # the retirement directly).
    with pytest.raises(IntentCompilationError, match="retired"):
        compile_intent(
            _intent(
                [
                    {
                        "card": "opt@v1",
                        "program": "orca",
                        "native": dict(OPT_NATIVE),
                        "execution": {"target": "node-9"},
                    }
                ]
            ),
            machine_profile={"name": "box", "total_cores": 64, "total_memory": "256GiB"},
        )
