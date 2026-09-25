#!/usr/bin/env python3

"""Producer-side tests for the opaque V3 step-id allocator and recipe instantiate.

Requirement map (recipe requirements RCP3-RCP9; RCP1/RCP2/RCP10-RCP12 live in
``tests/test_configuration_contract_v3.py`` alongside the contract shape):

* RCP3: allocated ids match ``^s_[a-z2-7]{8}$`` exactly (base32-lowercase,
  ``s_`` + 8 symbols).
* RCP4: the allocator never emits the sequential family (never ``s001``),
  never derives from ``max()+1``.
* RCP5: a collision against the existing document ids causes a redraw.
* RCP6: exhausting the bounded retries raises ``StepIdExhaustionError``.
* RCP7: disjoint instantiations -- two copies into one document share no ids.
* RCP8: the recipe DAG is rebased (template-local inputs rewritten, topology
  preserved).
* RCP9: ``checkpoint.from_step`` is rebased through the same old-to-new map.
* Attachment: roots attach only when ``attach_roots_to`` is given (``None``
  leaves roots as roots; ``[]`` is starter semantics; a list attaches every
  root to the same set); non-roots keep their rebased internal edges.
* Determinism: a seeded RNG reproduces the instantiation; results are isolated
  copies.
"""

from __future__ import annotations

import random
import re
import string
from typing import Any

import pytest

from confflow.config.canonical.recipes import (
    RECIPE_STEP_ID_PATTERN,
    STEP_ID_ALPHABET,
    STEP_ID_MAX_ATTEMPTS,
    STEP_ID_SUFFIX_LENGTH,
    StepIdExhaustionError,
    allocate_step_id,
    build_recipe_catalog_v3,
    instantiate_recipe_v3,
)


class _StubRng:
    """A ``choice(seq)`` source replaying a fixed character script."""

    def __init__(self, script: str) -> None:
        self._script = list(script)
        self._index = 0

    def choice(self, seq: Any) -> str:
        char = self._script[self._index % len(self._script)]
        self._index += 1
        assert char in seq, char
        return char


def _two_step_template() -> dict[str, Any]:
    """Return a future-style multi-step template with local ids and a checkpoint."""
    return {
        "schema": "confflow.workflow.v3",
        "global": {},
        "steps": [
            {
                "id": "r_opt",
                "label": "Opt",
                "type": "calc",
                "inputs": [],
                "params": {"itask": "opt"},
            },
            {
                "id": "r_freq",
                "label": "Freq",
                "type": "calc",
                "inputs": ["r_opt"],
                "params": {"itask": "freq"},
                "checkpoint": {"from_step": "r_opt"},
            },
        ],
    }


class TestOpaqueShape:
    """RCP3: the generated shape is exactly ``s_`` + 8 base32-lowercase symbols."""

    def test_generated_ids_match_the_opaque_pattern(self) -> None:
        rng = random.Random(20260925)
        for _ in range(200):
            candidate = allocate_step_id(rng=rng)
            assert re.fullmatch(RECIPE_STEP_ID_PATTERN, candidate), candidate

    def test_alphabet_is_rfc4648_base32_lowercase(self) -> None:
        assert STEP_ID_ALPHABET == string.ascii_lowercase + "234567"
        assert len(STEP_ID_ALPHABET) == 32
        assert STEP_ID_SUFFIX_LENGTH == 8

    def test_near_misses_do_not_match(self) -> None:
        for bad in (
            "s001",
            "S_ABCDEFGH",
            "s_ABCDEFGH",
            "s_abcdefg",
            "s_abcdefghi",
            "s_abcdefg0",
            "s_abcdefg1",
            "s_abcdefg8",
            "s_abcdefg9",
            "s_abcdef h",
        ):
            assert re.fullmatch(RECIPE_STEP_ID_PATTERN, bad) is None, bad
        assert re.fullmatch(RECIPE_STEP_ID_PATTERN, "s_k7m2qvdx")


class TestNeverSequential:
    """RCP4: the allocator never emits the sequential family or derives max()+1."""

    def test_seed_sweep_never_produces_a_sequential_id(self) -> None:
        for seed in range(50):
            rng = random.Random(seed)
            for _ in range(20):
                candidate = allocate_step_id(rng=rng)
                assert not re.fullmatch(r"^s[0-9]+$", candidate), candidate
                assert candidate != "s001"

    def test_a_deleted_sequential_id_is_not_reissued(self) -> None:
        existing = {"s001", "s002"}
        rng = random.Random(11)
        for _ in range(20):
            assert allocate_step_id(existing, rng=rng) not in existing


class TestCollisionRedraw:
    """RCP5: a taken candidate is discarded and redrawn."""

    def test_collision_redraws_until_fresh(self) -> None:
        taken = {"s_" + "a" * 8}
        rng = _StubRng("a" * 8 + "b" * 8)
        assert allocate_step_id(taken, rng=rng) == "s_" + "b" * 8  # type: ignore[arg-type]

    def test_fresh_on_first_try_needs_no_redraw(self) -> None:
        rng = _StubRng("c" * 8)
        assert allocate_step_id(set(), rng=rng) == "s_" + "c" * 8  # type: ignore[arg-type]


class TestExhaustion:
    """RCP6: bounded retries end in an explicit error, never a duplicate."""

    def test_always_colliding_raises(self) -> None:
        taken = {"s_" + "c" * 8}
        with pytest.raises(StepIdExhaustionError, match="attempts"):
            allocate_step_id(taken, rng=_StubRng("c" * 8))  # type: ignore[arg-type]

    def test_the_bound_is_the_published_constant(self) -> None:
        assert STEP_ID_MAX_ATTEMPTS == 32


class TestDisjointInstantiation:
    """RCP7: two copies into one document share no ids and avoid existing ones."""

    def test_two_instantiations_are_disjoint(self) -> None:
        recipe = next(
            item for item in build_recipe_catalog_v3()["recipes"] if item["id"] == "optimize"
        )
        first = instantiate_recipe_v3(recipe, rng=random.Random(1))
        second = instantiate_recipe_v3(
            recipe,
            existing_ids=[step["id"] for step in first],
            rng=random.Random(2),
        )
        first_ids = {step["id"] for step in first}
        second_ids = {step["id"] for step in second}
        assert first_ids.isdisjoint(second_ids)
        for step in second:
            assert re.fullmatch(RECIPE_STEP_ID_PATTERN, step["id"]), step

    def test_ids_within_one_call_are_unique(self) -> None:
        steps = instantiate_recipe_v3(_two_step_template(), rng=random.Random(3))
        assert len({step["id"] for step in steps}) == 2


class TestDagRebase:
    """RCP8: template-local inputs are rewritten; topology is preserved."""

    def test_internal_edges_are_rebased_through_the_old_to_new_map(self) -> None:
        steps = instantiate_recipe_v3(_two_step_template(), rng=random.Random(5))
        assert len(steps) == 2
        assert steps[0]["inputs"] == []
        assert steps[1]["inputs"] == [steps[0]["id"]]
        assert steps[0]["id"] != "r_opt"
        assert steps[1]["id"] != "r_freq"
        assert steps[0]["params"] == {"itask": "opt"}
        assert steps[1]["params"] == {"itask": "freq"}

    def test_single_step_recipes_gain_an_opaque_id_and_keep_params(self) -> None:
        recipe = next(
            item for item in build_recipe_catalog_v3()["recipes"] if item["id"] == "opt_freq"
        )
        (step,) = instantiate_recipe_v3(recipe, rng=random.Random(9))
        assert re.fullmatch(RECIPE_STEP_ID_PATTERN, step["id"])
        assert step["inputs"] == []
        assert step["params"] == {"itask": "opt_freq"}
        assert step["label"] == "Optimize + Frequency"


class TestCheckpointRebase:
    """RCP9: ``checkpoint.from_step`` follows the same old-to-new map."""

    def test_internal_checkpoint_reference_is_rebased(self) -> None:
        steps = instantiate_recipe_v3(_two_step_template(), rng=random.Random(13))
        assert steps[1]["checkpoint"] == {"from_step": steps[0]["id"]}

    def test_steps_without_a_checkpoint_gain_none(self) -> None:
        steps = instantiate_recipe_v3(_two_step_template(), rng=random.Random(17))
        assert "checkpoint" not in steps[0]


class TestAttachment:
    """Roots attach only when ``attach_roots_to`` is given."""

    def test_none_leaves_roots_as_roots(self) -> None:
        steps = instantiate_recipe_v3(
            _two_step_template(), attach_roots_to=None, rng=random.Random(21)
        )
        assert steps[0]["inputs"] == []
        assert steps[1]["inputs"] == [steps[0]["id"]]

    def test_empty_list_is_starter_semantics(self) -> None:
        steps = instantiate_recipe_v3(
            _two_step_template(), attach_roots_to=[], rng=random.Random(23)
        )
        assert steps[0]["inputs"] == []

    def test_a_list_attaches_every_root_to_the_same_set(self) -> None:
        template = {
            "schema": "confflow.workflow.v3",
            "global": {},
            "steps": [
                {"id": "r_a", "type": "calc", "inputs": [], "params": {}},
                {"id": "r_b", "type": "calc", "inputs": [], "params": {}},
                {
                    "id": "r_c",
                    "type": "confgen",
                    "inputs": ["r_a", "r_b"],
                    "params": {},
                },
            ],
        }
        steps = instantiate_recipe_v3(template, attach_roots_to=["s_ext"], rng=random.Random(29))
        by_old = {old: new for old, new in zip(("r_a", "r_b", "r_c"), [s["id"] for s in steps])}
        assert steps[0]["inputs"] == ["s_ext"]
        assert steps[1]["inputs"] == ["s_ext"]
        assert sorted(steps[2]["inputs"]) == sorted([by_old["r_a"], by_old["r_b"]])

    def test_a_single_string_attaches_the_root(self) -> None:
        steps = instantiate_recipe_v3(
            _two_step_template(), attach_roots_to="s_ext", rng=random.Random(31)
        )
        assert steps[0]["inputs"] == ["s_ext"]


class TestPurity:
    """instantiate is pure and deterministic given the allocator output."""

    def test_same_seed_reproduces_the_instantiation(self) -> None:
        first = instantiate_recipe_v3(_two_step_template(), rng=random.Random(99))
        second = instantiate_recipe_v3(_two_step_template(), rng=random.Random(99))
        assert first == second

    def test_the_template_and_catalog_are_not_mutated(self) -> None:
        template = _two_step_template()
        before = build_recipe_catalog_v3()
        steps = instantiate_recipe_v3(template, rng=random.Random(4))
        steps[0]["params"]["itask"] = "tampered"
        steps.append({"id": "evil"})
        assert template["steps"][0]["id"] == "r_opt"
        assert template["steps"][0]["params"] == {"itask": "opt"}
        assert build_recipe_catalog_v3() == before

    def test_a_malformed_template_fails_loudly(self) -> None:
        with pytest.raises(ValueError, match="steps"):
            instantiate_recipe_v3({"schema": "confflow.workflow.v3"}, rng=random.Random(0))
