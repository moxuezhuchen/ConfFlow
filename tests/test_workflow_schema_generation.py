"""Schema artifact and generator parity tests."""

from __future__ import annotations

from pathlib import Path

from confflow.config.schema import SCHEMA_GENERATOR_VERSION, schema_bytes


def test_packaged_schema_is_generated_from_canonical_metadata() -> None:
    path = Path(__file__).parents[1] / "confflow" / "config" / "workflow_config_v1.schema.json"
    assert path.read_bytes() == schema_bytes()
    assert SCHEMA_GENERATOR_VERSION in path.read_text(encoding="utf-8")
