"""Validate the design-only ConfFlow control protocol schema bundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from referencing import Registry, Resource

jsonschema = pytest.importorskip("jsonschema")

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _ROOT / "docs" / "control_protocol" / "v1"
_FIXTURE_DIR = _ROOT / "tests" / "fixtures" / "control_protocol" / "v1"
_MANIFEST_PATH = _FIXTURE_DIR / "manifest.json"


def _load_json(path: Path) -> dict[str, Any]:
    """Load one protocol JSON document."""
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"Protocol JSON object required: {path}")
    return loaded


def _validator(schema_name: str):
    """Create a Draft 2020-12 validator with the complete local schema store."""
    schemas = [_load_json(path) for path in _SCHEMA_DIR.glob("*.schema.json")]
    selected = next(schema for schema in schemas if str(schema["$id"]).endswith(schema_name))
    registry = Registry().with_resources(
        (str(schema["$id"]), Resource.from_contents(schema)) for schema in schemas
    )
    return jsonschema.Draft202012Validator(selected, registry=registry)


def _manifest_rows(kind: str) -> list[dict[str, str]]:
    """Return one fixture kind after validating the manifest's file inventory."""
    manifest = _load_json(_MANIFEST_PATH)
    if manifest.get("schema") != "confflow.control.fixtures.v1":
        raise AssertionError("Fixture manifest schema must be confflow.control.fixtures.v1")
    rows = manifest.get(kind)
    if not isinstance(rows, list):
        raise AssertionError(f"Fixture manifest {kind!r} must be a list")
    expected: set[Path] = set()
    parsed: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "validator"}:
            raise AssertionError(f"Fixture manifest {kind!r} row is invalid: {row!r}")
        path = row["path"]
        validator = row["validator"]
        if not isinstance(path, str) or not isinstance(validator, str):
            raise AssertionError(f"Fixture manifest {kind!r} row has non-string values")
        target = _FIXTURE_DIR / path
        if target in expected or not target.is_file():
            raise AssertionError(f"Fixture manifest {kind!r} has missing or duplicate path: {path}")
        expected.add(target)
        parsed.append({"path": path, "validator": validator})
    actual = set((_FIXTURE_DIR / kind).glob("*.json"))
    if actual != expected:
        raise AssertionError(f"Fixture manifest {kind!r} does not exactly match files")
    return parsed


def _write_fixture_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    """Write the minimum valid fixture manifest for inventory tests."""
    path.write_text(
        json.dumps({"schema": "confflow.control.fixtures.v1", "golden": rows, "negative": []}),
        encoding="utf-8",
    )


def test_fixture_manifest_rejects_unlisted_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fixture inventory must reject a file that has no manifest row."""
    fixture_dir = tmp_path / "fixtures"
    golden_dir = fixture_dir / "golden"
    golden_dir.mkdir(parents=True)
    (golden_dir / "unlisted.json").write_text("{}", encoding="utf-8")
    manifest_path = fixture_dir / "manifest.json"
    _write_fixture_manifest(manifest_path, [])
    monkeypatch.setattr("tests.test_control_protocol_schema._FIXTURE_DIR", fixture_dir)
    monkeypatch.setattr("tests.test_control_protocol_schema._MANIFEST_PATH", manifest_path)
    with pytest.raises(AssertionError, match="does not exactly match files"):
        _manifest_rows("golden")


def test_fixture_manifest_rejects_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fixture inventory must reject a manifest row whose file is absent."""
    fixture_dir = tmp_path / "fixtures"
    (fixture_dir / "golden").mkdir(parents=True)
    manifest_path = fixture_dir / "manifest.json"
    _write_fixture_manifest(manifest_path, [{"path": "golden/missing.json", "validator": "x"}])
    monkeypatch.setattr("tests.test_control_protocol_schema._FIXTURE_DIR", fixture_dir)
    monkeypatch.setattr("tests.test_control_protocol_schema._MANIFEST_PATH", manifest_path)
    with pytest.raises(AssertionError, match="missing or duplicate path"):
        _manifest_rows("golden")


@pytest.mark.parametrize("row", _manifest_rows("golden"))
def test_golden_protocol_examples_validate(row: dict[str, str]):
    """Every RFC golden example must validate against its declared schema."""
    fixture = _load_json(_FIXTURE_DIR / row["path"])
    assert fixture.pop("_schema", None) == row["validator"]
    _validator(row["validator"]).validate(fixture)


@pytest.mark.parametrize("row", _manifest_rows("negative"))
def test_negative_protocol_examples_are_rejected(row: dict[str, str]):
    """Every negative fixture must fail against its declared schema."""
    fixture = _load_json(_FIXTURE_DIR / row["path"])
    assert fixture.pop("_schema", None) == row["validator"]
    with pytest.raises(jsonschema.ValidationError):
        _validator(row["validator"]).validate(fixture)
