"""Compatibility tests for the additive canonical configuration namespace."""

from __future__ import annotations

from confflow.config.canonical import CanonicalWorkflowConfig


def test_canonical_config_round_trips_through_one_parser() -> None:
    raw = {
        "global": {"iprog": "gaussian", "freeze": "1, 2"},
        "steps": [{"name": "opt", "type": "task", "params": {"keyword": "B3LYP"}}],
        "editor_metadata": {"selected": "opt"},
    }

    parsed = CanonicalWorkflowConfig.from_mapping(raw)

    assert parsed.global_options.value.iprog == "g16"
    assert parsed.steps[0].type == "calc"
    assert parsed.steps[0].params["keyword"] == "B3LYP"
    assert parsed.as_mapping()["steps"][0]["type"] == "calc"
    assert parsed.extensions["editor_metadata"] == {"selected": "opt"}
