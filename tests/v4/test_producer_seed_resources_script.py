"""Seed identity must follow the effective scientific inputs of each step.

Regression coverage for two gaps in :mod:`confflow.producer.seeds`:

* per-item resources inherit from ``global.resources`` when a step omits them
  (the same inheritance the validation authority applies), so the identity
  must not pin them to the schema default;
* script steps carry scientific fields (``script``, ``args``, ``outputs``) that
  change downstream results, so the identity must include them.
"""

from __future__ import annotations

from typing import Any

from confflow.producer.seeds import derive_seed, seed_identity_for_step


def _confgen_step(step_id: str = "c") -> dict[str, Any]:
    return {"id": step_id, "executor": "confgen", "confgen": {"n_conformers": 4}}


def _doc(steps: list[dict[str, Any]], global_block: dict[str, Any] | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {"schema": "confflow.v4", "inputs": {}, "steps": steps}
    if global_block is not None:
        doc["global"] = global_block
    return doc


def test_global_resources_inherit_into_seed_identity() -> None:
    """A step that omits resources inherits ``global.resources`` in the identity."""
    inherited = _doc([_confgen_step()], {"resources": {"cores_per_item": 4}})
    declared = _doc([{**_confgen_step(), "resources": {"cores_per_item": 4}}])
    assert derive_seed("c", inherited) == derive_seed("c", declared)


def test_global_resources_move_seed_when_step_omits_them() -> None:
    """Changing ``global.resources`` changes the effective step science, so the seed moves."""
    four = _doc([_confgen_step()], {"resources": {"cores_per_item": 4}})
    eight = _doc([_confgen_step()], {"resources": {"cores_per_item": 8}})
    assert derive_seed("c", four) != derive_seed("c", eight)


def test_step_resources_override_global_resources() -> None:
    """An explicit step value wins over the run global, per dimension."""
    overridden = _doc(
        [{**_confgen_step(), "resources": {"cores_per_item": 2}}],
        {"resources": {"cores_per_item": 8}},
    )
    declared = _doc([{**_confgen_step(), "resources": {"cores_per_item": 2}}])
    assert derive_seed("c", overridden) == derive_seed("c", declared)


def test_script_step_fields_move_downstream_seed() -> None:
    """Script ``args`` are scientific: an upstream change moves the derived seed."""

    def upstream(args: list[str]) -> list[dict[str, Any]]:
        return [
            {"id": "s", "executor": "script", "script": "prep.py", "args": args},
            _confgen_step(),
        ]

    assert derive_seed("c", _doc(upstream(["--a"]))) != derive_seed("c", _doc(upstream(["--b"])))


def test_script_step_script_name_is_in_identity() -> None:
    steps_a = [{"id": "s", "executor": "script", "script": "a.py"}, _confgen_step()]
    steps_b = [{"id": "s", "executor": "script", "script": "b.py"}, _confgen_step()]
    assert derive_seed("c", _doc(steps_a)) != derive_seed("c", _doc(steps_b))


def test_identity_without_script_fields_is_unchanged() -> None:
    """Documents with no script fields keep their identity (no derived seed moves)."""
    identity = seed_identity_for_step("c", _doc([_confgen_step()]))
    projected = {item["id"]: item for item in identity["steps"]}
    assert "script" not in projected["c"]
    assert "args" not in projected["c"]
    assert "outputs" not in projected["c"]
