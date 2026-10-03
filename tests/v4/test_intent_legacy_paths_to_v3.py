#!/usr/bin/env python3

"""Intent compiles legacy ConfGen paths scopes into typed ``schema_version: 3`` (IS.2).

The mapping is pinned to the IS.1 equivalence golden
(``docs/refactor/paths_equivalence``): every case is mapped by the golden's own
``map_native``, and every hydrogen-bearing ``EQUIVALENT`` case is executed from the
*compiled* block and must reproduce the golden's recorded v3 output.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from confflow.producer.intent import IntentCompilationError, compile_intent

GOLDEN = Path(__file__).resolve().parent.parent.parent / "docs" / "refactor" / "paths_equivalence"
SCOPE_KEYS = {"paths", "angle_step", "bond_scale", "strict_path_bond_check"}


def _golden_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "paths_equivalence_run", GOLDEN / "run_equivalence.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDEN_RUN = _golden_module()
CASES = json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))
RESULT = json.loads((GOLDEN / "result.json").read_text(encoding="utf-8"))
ALL_CASES = [*CASES["cases"], *CASES["hydrogen_cases"]]
IN_SCOPE = [case for case in ALL_CASES if set(case["native"]) <= SCOPE_KEYS]
MAPPABLE = [
    case for case in IN_SCOPE if GOLDEN_RUN.map_native(copy.deepcopy(case["native"]))[1] == []
]
UNKNOWN_PATH_KEYS = [case for case in IN_SCOPE if case not in MAPPABLE]
UNMAPPABLE = [case for case in ALL_CASES if not set(case["native"]) <= SCOPE_KEYS]

GLOBALS = {"charge": 0, "multiplicity": 1}


def _compile(step: dict[str, Any]) -> dict[str, Any]:
    document = compile_intent(
        {
            "schema": "confflow.intent.v1",
            "globals": dict(GLOBALS),
            "steps": [{"card": "confgen@v1", **step}],
        }
    )
    return document["steps"][0]["confgen"]


def _without_seed(block: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in block.items() if key != "seed"}


def test_the_golden_has_the_expected_shape() -> None:
    assert len(CASES["hydrogen_cases"]) == 9
    assert len(MAPPABLE) >= 20
    assert UNMAPPABLE, "some golden cases (chains and friends) have no v3 form"
    assert UNKNOWN_PATH_KEYS, "some golden cases carry path keys the v3 mapping does not know"


@pytest.mark.parametrize("case", MAPPABLE, ids=[case["case_id"] for case in MAPPABLE])
def test_compiled_block_equals_the_golden_mapping(case: dict[str, Any]) -> None:
    expected, unmapped = GOLDEN_RUN.map_native(copy.deepcopy(case["native"]))
    assert unmapped == []
    block = _compile({"native": copy.deepcopy(case["native"])})
    assert block["schema_version"] == 3
    assert _without_seed(block) == expected


@pytest.mark.parametrize(
    "case", UNKNOWN_PATH_KEYS, ids=[case["case_id"] for case in UNKNOWN_PATH_KEYS]
)
def test_unknown_path_keys_are_refused_not_dropped(case: dict[str, Any]) -> None:
    with pytest.raises(IntentCompilationError, match="unsupported keys"):
        _compile({"native": copy.deepcopy(case["native"])})


@pytest.mark.parametrize("case", UNMAPPABLE, ids=[case["case_id"] for case in UNMAPPABLE])
def test_scopes_outside_the_paths_vocabulary_stay_legacy_for_now(case: dict[str, Any]) -> None:
    block = _compile({"native": copy.deepcopy(case["native"])})
    assert block["native"] == case["native"]
    assert "schema_version" not in block


def test_bare_declaration_gets_the_legacy_default_step_120() -> None:
    block = _compile({"native": {"paths": [{"start": 1, "end": 4, "move": "end"}]}})
    assert block["paths"] == [{"start": 1, "end": 4, "move": "end", "step": 120}]


def test_angle_step_becomes_the_step_of_bare_declarations_only() -> None:
    block = _compile(
        {
            "native": {
                "angle_step": 60,
                "paths": [
                    {"start": 1, "end": 4, "move": "end"},
                    {"start": 1, "end": 4, "move": "end", "step": 90},
                    {"start": 1, "end": 4, "move": "end", "angles": [0, 180]},
                ],
            }
        }
    )
    assert [entry.get("step") for entry in block["paths"]] == [60, 90, None]
    assert block["paths"][2]["angles"] == [0, 180]
    assert "angle_step" not in block


def test_bond_scale_and_strict_flag_map_to_the_v3_members() -> None:
    block = _compile(
        {
            "native": {
                "bond_scale": 1.3,
                "strict_path_bond_check": True,
                "paths": [{"start": 1, "end": 4, "move": "end", "step": 120}],
            }
        }
    )
    assert block["tolerances"] == {"bond_scale": 1.3}
    assert block["strict_path_bond_check"] is True


def test_unknown_path_keys_and_non_mapping_declarations_are_refused() -> None:
    with pytest.raises(IntentCompilationError, match="unsupported keys: rotate"):
        _compile({"native": {"paths": [{"start": 1, "end": 4, "rotate": True}]}})
    with pytest.raises(IntentCompilationError, match=r"paths\[0\] must be a mapping"):
        _compile({"native": {"paths": [[1, 4]]}})
    with pytest.raises(IntentCompilationError, match="non-empty list"):
        _compile({"native": {"paths": []}})


def test_a_terminal_endpoint_is_compiled_as_written() -> None:
    block = _compile({"native": {"paths": [{"start": 1, "end": 4, "move": "end", "step": 120}]}})
    assert (block["paths"][0]["start"], block["paths"][0]["end"]) == (1, 4)


def test_seed_and_overrides_ride_along() -> None:
    block = _compile(
        {
            "seed": 7,
            "overrides": {"charge": 1},
            "native": {"paths": [{"start": 1, "end": 4, "move": "end", "step": 120}]},
        }
    )
    assert block["seed"] == 7
    assert block["overrides"] == {"charge": 1}


HYDROGEN_EQUIVALENT = [
    case_id
    for case_id, entry in RESULT["hydrogen_cases"].items()
    if entry["verdict"] == "EQUIVALENT"
]


@pytest.mark.parametrize("case_id", HYDROGEN_EQUIVALENT)
def test_equivalent_golden_cases_reproduce_the_recorded_v3_output(case_id: str) -> None:
    case = next(item for item in CASES["hydrogen_cases"] if item["case_id"] == case_id)
    block = _without_seed(_compile({"native": copy.deepcopy(case["native"])}))
    run = GOLDEN_RUN._execute(case["record"], block)
    recorded = RESULT["hydrogen_cases"][case_id]["v3"]
    assert run["status"] == recorded["status"] == "COMPLETED"
    assert run["diagnostics"] == recorded["diagnostics"]
    assert run["rotors"] == recorded["rotors"]
    assert len(run["structures"]) == len(recorded["structures"])
    for new, old in zip(run["structures"], recorded["structures"]):
        assert new["ordinal"] == old["ordinal"]
        assert new["atoms"] == old["atoms"]
        assert np.allclose(new["coordinates"], old["coordinates"], rtol=0, atol=1e-9)
