#!/usr/bin/env python3

"""ConfGen v4 declaration of coordination shape ``input`` (WP4).

``shapes: ["input"]`` takes the input structure's own metal-donor core as the
template and keeps arrangement only. These tests cover the search-spec
resolution (CN7 and CN8 fixtures), the typed v4 declaration (accept and
reject branches), and the unchanged ideal-shape rules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.domain import StructureRecord
from confflow.science.confgen.graph import CoordinationSpec, load_xyz_frame
from confflow.science.confgen.search_spec import (
    build_search_context,
    resolve_search_coordination,
)
from confflow.workflow.v4.confgen_schema import ConfgenModelV3

_FIXTURES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "coordination" / "input_core"
)


def _structure(name: str) -> tuple[StructureRecord, dict[str, Any]]:
    """Load one input-core fixture as a structure plus its index entry."""
    entry = json.loads((_FIXTURES / "index.json").read_text())[name]
    elements, coordinates = load_xyz_frame(_FIXTURES / f"{name}.xyz")
    structure = StructureRecord(
        id=name,
        atoms=tuple(elements),
        coordinates=tuple(coordinates),
        charge=entry["charge"],
        multiplicity=entry["multiplicity"],
    )
    return structure, entry


def _sites(donors: list[int]) -> list[dict[str, Any]]:
    """Return 0-based binding sites, one per donor atom."""
    return [{"id": f"d{i}", "atoms": [donor]} for i, donor in enumerate(donors)]


def _native(donors: list[int], shapes: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    """Return a native v4 mapping (0-based indices) with the given donor sites."""
    coordination: dict[str, Any] = {
        "metal_center": 0,
        "binding_sites": _sites(donors),
        "shapes": shapes if shapes is not None else ["input"],
    }
    native: dict[str, Any] = {
        "schema_version": 4,
        "index_base": 0,
        "seed": 1,
        "coordination": coordination,
    }
    native.update(extra)
    return native


def _document(donors: list[int], shapes: list[str], **extra: Any) -> dict[str, Any]:
    """Return a v4 declaration document for ``ConfgenModelV3`` (0-based indices)."""
    document: dict[str, Any] = {
        "schema_version": 4,
        "index_base": 0,
        "seed": 1,
        "coordination": {
            "metal_center": 0,
            "binding_sites": _sites(donors),
            "shapes": shapes,
        },
    }
    document.update(extra)
    return document


@pytest.mark.parametrize("name", ["ti7_salan", "zr8_onn"])
def test_input_shape_resolves_donor_core_for_fixture(name: str) -> None:
    structure, entry = _structure(name)
    donors = entry["donors0"]
    context = build_search_context(structure, _native(donors))
    assert isinstance(context.spec, CoordinationSpec)
    assert context.spec.shapes == ("input",)
    assert context.shape == "input"
    assert len(context.spec.binding_sites) == len(donors)
    assert [site.atoms for site in context.spec.binding_sites] == [(d,) for d in donors]


def test_input_shape_cn7_has_seven_sites() -> None:
    structure, entry = _structure("ti7_salan")
    context = build_search_context(structure, _native(entry["donors0"]))
    assert len(context.spec.binding_sites) == 7


def test_input_shape_cn8_has_eight_sites() -> None:
    structure, entry = _structure("zr8_onn")
    context = build_search_context(structure, _native(entry["donors0"]))
    assert len(context.spec.binding_sites) == 8


def test_input_shape_accepts_two_sites_and_no_upper_bound() -> None:
    section = {
        "metal_center": 0,
        "binding_sites": _sites([1, 2]),
        "shapes": ["input"],
    }
    spec = resolve_search_coordination(section)
    assert spec.shapes == ("input",)
    assert len(spec.binding_sites) == 2
    wide = dict(section, binding_sites=_sites(list(range(1, 10))))
    assert len(resolve_search_coordination(wide).binding_sites) == 9


def test_input_shape_rejects_single_site() -> None:
    section = {"metal_center": 0, "binding_sites": _sites([1]), "shapes": ["input"]}
    with pytest.raises(ValueError, match="at least 2 sites"):
        resolve_search_coordination(section)


def test_ideal_shape_keeps_four_to_six_site_range() -> None:
    seven = {
        "metal_center": 0,
        "binding_sites": _sites(list(range(1, 8))),
        "shapes": ["octahedral"],
    }
    with pytest.raises(ValueError, match="4 to 6 sites"):
        resolve_search_coordination(seven)
    three = {"metal_center": 0, "binding_sites": _sites([1, 2, 3]), "shapes": ["tetrahedral"]}
    with pytest.raises(ValueError, match="4 to 6 sites"):
        resolve_search_coordination(three)


def test_auto_shape_stays_rejected() -> None:
    section = {"metal_center": 0, "binding_sites": _sites([1, 2, 3, 4]), "shapes": ["auto"]}
    with pytest.raises(ValueError, match="not 'auto'"):
        resolve_search_coordination(section)


def test_model_accepts_cn7_input_document() -> None:
    model = ConfgenModelV3.model_validate(_document([99, 100, 101, 102, 103, 104, 105], ["input"]))
    assert model.coordination is not None
    assert model.coordination.shapes == ["input"]
    assert len(model.coordination.binding_sites) == 7


def test_model_accepts_input_with_keep_arrangement() -> None:
    document = _document([1, 2, 3, 4], ["input"], search={"arrangement": "keep"})
    model = ConfgenModelV3.model_validate(document)
    assert model.search is not None
    assert model.search.arrangement == "keep"


def test_model_accepts_input_with_search_arrangement() -> None:
    document = _document([1, 2, 3, 4], ["input"], search={"arrangement": "search"})
    model = ConfgenModelV3.model_validate(document)
    assert model.search is not None
    assert model.search.arrangement == "search"


def test_model_rejects_input_with_enumerate() -> None:
    document = _document(
        [99, 100, 101, 102, 103, 104, 105], ["input"], search={"arrangement": "enumerate"}
    )
    with pytest.raises(ValidationError, match="supports arrangement 'keep' or 'search' only"):
        ConfgenModelV3.model_validate(document)


def test_model_rejects_octahedral_with_seven_sites() -> None:
    document = _document([99, 100, 101, 102, 103, 104, 105], ["octahedral"])
    with pytest.raises(ValidationError, match="4 to 6 sites"):
        ConfgenModelV3.model_validate(document)


def test_model_rejects_input_mixed_with_other_shape() -> None:
    document = _document([1, 2, 3, 4, 5, 6], ["input", "octahedral"])
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(document)


def test_model_accepts_cn6_octahedral_document() -> None:
    document = _document([1, 2, 3, 4, 5, 6], ["octahedral"])
    model = ConfgenModelV3.model_validate(document)
    assert model.coordination is not None
    assert model.coordination.shapes == ["octahedral"]


def test_schema_input_constants_match_graph() -> None:
    from confflow.science.confgen import graph
    from confflow.workflow.v4 import confgen_schema

    assert confgen_schema.INPUT_SHAPE == graph.INPUT_SHAPE
    assert confgen_schema.INPUT_CN_MIN == graph.INPUT_CN_MIN
