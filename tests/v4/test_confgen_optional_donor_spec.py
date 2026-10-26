#!/usr/bin/env python3

"""ConfGen optional (semi-labile) binding sites: schema, state enumeration, resolution.

Covers the workflow declaration (``BindingSiteModel.optional`` and the
``CoordinationGenerationSpec`` rules), the science-level ``donor_states`` enumeration
(0, 1 and 2 optional sites under both index bases), the internal ``detached_sites`` key
resolving into ``CoordinationSpec.detached_donors``, and the byte-identity of declarations
without optional sites.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.science.confgen.graph import BindingSite, CoordinationSpec, UnsupportedTopologyError
from confflow.science.confgen.search_spec import (
    DonorState,
    donor_states,
    normalize_search_spec,
    resolve_search_coordination,
)
from confflow.workflow.v4.confgen_schema import BindingSiteModel, ConfgenModelV3


def _site(site_id: str, atom: int, optional: bool | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": site_id, "kind": "atom", "atoms": [atom], "hapticity": 1}
    if optional is not None:
        entry["optional"] = optional
    return entry


def _native(sites: list[dict[str, Any]], *, shapes: list[str] | None = None, base: int = 1):
    return {
        "schema_version": 4,
        "index_base": base,
        "seed": 7,
        "coordination": {
            "metal_center": 1 if base == 1 else 0,
            "binding_sites": sites,
            "shapes": shapes or ["input"],
        },
    }


def _doc(sites: list[dict[str, Any]], shapes: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "index_base": 1,
        "seed": 7,
        "coordination": {
            "metal_center": 1,
            "binding_sites": sites,
            "shapes": shapes or ["input"],
        },
    }


# ---------------------------------------------------------------- workflow schema


def test_binding_site_optional_defaults_false_and_is_strict_bool() -> None:
    assert BindingSiteModel(id="O39", atoms=[39]).optional is False
    assert BindingSiteModel(id="O82", atoms=[82], optional=True).optional is True
    with pytest.raises(ValidationError):
        BindingSiteModel(id="O82", atoms=[82], optional="yes")


def test_schema_accepts_one_and_two_optional_sites_under_input() -> None:
    one = [_site("O39", 39), _site("N40", 40), _site("O82", 82, optional=True)]
    model = ConfgenModelV3.model_validate(_doc(one))
    assert [site.optional for site in model.coordination.binding_sites] == [False, False, True]

    two = [
        _site("O39", 39),
        _site("N40", 40),
        _site("O82", 82, optional=True),
        _site("O90", 90, optional=True),
    ]
    assert ConfgenModelV3.model_validate(_doc(two)) is not None


def test_schema_rejects_optional_with_ideal_shape() -> None:
    sites = [
        _site("O1", 2),
        _site("O2", 3),
        _site("O3", 4),
        _site("O4", 5),
        _site("O5", 6, optional=True),
        _site("O6", 7, optional=True),
    ]
    with pytest.raises(ValidationError, match="require shapes"):
        ConfgenModelV3.model_validate(_doc(sites, shapes=["octahedral"]))


def test_schema_rejects_more_than_two_optional_sites() -> None:
    sites = [
        _site("O39", 39),
        _site("N40", 40),
        _site("O82", 82, optional=True),
        _site("O90", 90, optional=True),
        _site("O91", 91, optional=True),
    ]
    with pytest.raises(ValidationError, match="at most 2 optional"):
        ConfgenModelV3.model_validate(_doc(sites))


def test_schema_rejects_fewer_than_two_required_sites() -> None:
    sites = [_site("O39", 39), _site("O82", 82, optional=True)]
    with pytest.raises(ValidationError, match="at least 2 non-optional"):
        ConfgenModelV3.model_validate(_doc(sites))


def test_schema_rejects_internal_detached_sites_key() -> None:
    doc = _doc([_site("O39", 39), _site("N40", 40), _site("O82", 82, optional=True)])
    doc["coordination"]["detached_sites"] = [82]
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(doc)


# ---------------------------------------------------------------- donor_states


def test_donor_states_without_optional_sites_is_identity() -> None:
    native = _native([_site("O39", 39), _site("N40", 40), _site("O41", 41)])
    before = copy.deepcopy(native)
    states = donor_states(native)
    assert len(states) == 1
    state = states[0]
    assert isinstance(state, DonorState)
    assert state.label == ""
    assert state.bound == {}
    assert state.native == before
    assert native == before


def test_donor_states_one_optional_site_index_base_one() -> None:
    native = _native(
        [_site("O39", 40), _site("N40", 41), _site("O82", 83, optional=True)],
    )
    before = copy.deepcopy(native)
    states = donor_states(native)
    assert [s.label for s in states] == ["O82+", "O82-"]
    assert [s.bound for s in states] == [{83: True}, {83: False}]

    bound, detached = states[0].native, states[1].native
    assert bound["coordination"]["binding_sites"] == [
        _site("O39", 40),
        _site("N40", 41),
        _site("O82", 83, optional=True),
    ]
    assert "detached_sites" not in bound["coordination"]
    assert bound["index_base"] == 1
    assert bound["seed"] == 7

    assert detached["coordination"]["binding_sites"] == [_site("O39", 40), _site("N40", 41)]
    assert detached["coordination"]["detached_sites"] == [83]
    assert native == before


def test_donor_states_two_optional_sites_order_and_index_base_zero() -> None:
    native = _native(
        [
            _site("O10", 10),
            _site("A", 11, optional=True),
            _site("N12", 12),
            _site("B", 13, optional=True),
            _site("O14", 14),
        ],
        base=0,
    )
    states = donor_states(native)
    assert [s.label for s in states] == ["A+,B+", "A+,B-", "A-,B+", "A-,B-"]
    assert [s.bound for s in states] == [
        {11: True, 13: True},
        {11: True, 13: False},
        {11: False, 13: True},
        {11: False, 13: False},
    ]
    all_bound = states[0].native["coordination"]
    assert [site["id"] for site in all_bound["binding_sites"]] == ["O10", "A", "N12", "B", "O14"]
    flagged = [site["id"] for site in all_bound["binding_sites"] if site.get("optional")]
    assert flagged == ["A", "B"]
    assert all(
        "optional" not in site
        for site in all_bound["binding_sites"]
        if site["id"] in ("O10", "N12", "O14")
    )
    assert "detached_sites" not in all_bound
    assert states[0].native["index_base"] == 0

    only_a = states[1].native["coordination"]
    assert [site["id"] for site in only_a["binding_sites"]] == ["O10", "A", "N12", "O14"]
    assert only_a["detached_sites"] == [13]
    kept_a = next(site for site in only_a["binding_sites"] if site["id"] == "A")
    assert kept_a["optional"] is True

    none_bound = states[3].native["coordination"]
    assert [site["id"] for site in none_bound["binding_sites"]] == ["O10", "N12", "O14"]
    assert none_bound["detached_sites"] == [11, 13]
    assert states[3].native["index_base"] == 0


def test_donor_states_rejects_more_than_two_optional_sites() -> None:
    native = _native(
        [
            _site("O1", 1, optional=True),
            _site("O2", 2, optional=True),
            _site("O3", 3, optional=True),
            _site("O4", 4),
            _site("O5", 5),
        ]
    )
    with pytest.raises(ValueError, match="at most 2 optional"):
        donor_states(native)


# ---------------------------------------------------------------- resolution


def test_resolve_fills_detached_donors_for_detached_state() -> None:
    native = _native([_site("O39", 40), _site("N40", 41), _site("O82", 83, optional=True)])
    bound_state, detached_state = donor_states(native)

    bound = resolve_search_coordination(normalize_search_spec(bound_state.native)["coordination"])
    assert bound.donor_indices == (39, 40, 82)
    assert bound.detached_donors == ()
    assert [site.optional for site in bound.binding_sites] == [False, False, True]

    detached = resolve_search_coordination(
        normalize_search_spec(detached_state.native)["coordination"]
    )
    assert detached.donor_indices == (39, 40)
    assert detached.detached_donors == (82,)
    assert detached.metal_center == 0


def test_resolve_accepts_optional_flag_on_site() -> None:
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": "O39", "kind": "atom", "atoms": [38]},
            {"id": "N40", "kind": "atom", "atoms": [39]},
            {"id": "O82", "kind": "atom", "atoms": [81], "optional": True},
        ],
        "shapes": ["input"],
    }
    spec = resolve_search_coordination(section)
    assert [site.optional for site in spec.binding_sites] == [False, False, True]
    assert spec.detached_donors == ()


def test_resolve_rejects_optional_with_non_input_shape() -> None:
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": f"S{i}", "kind": "atom", "atoms": [i + 1], "optional": i == 4} for i in range(6)
        ],
        "shapes": ["octahedral"],
    }
    with pytest.raises(ValueError, match="requires shapes"):
        resolve_search_coordination(section)


def test_resolve_rejects_detached_atom_that_is_a_bound_donor() -> None:
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": "O39", "kind": "atom", "atoms": [38]},
            {"id": "N40", "kind": "atom", "atoms": [39]},
        ],
        "shapes": ["input"],
        "detached_sites": [38],
    }
    with pytest.raises(ValueError):
        resolve_search_coordination(section)


def test_resolve_rejects_malformed_detached_sites() -> None:
    section = {
        "metal_center": 0,
        "binding_sites": [
            {"id": "O39", "kind": "atom", "atoms": [38]},
            {"id": "N40", "kind": "atom", "atoms": [39]},
        ],
        "shapes": ["input"],
        "detached_sites": "81",
    }
    with pytest.raises(ValueError, match="detached_sites"):
        resolve_search_coordination(section)


# ---------------------------------------------------------------- graph layer


def test_graph_binding_site_optional_default_and_type() -> None:
    assert BindingSite(id="O82", atoms=(81,)).optional is False
    assert BindingSite(id="O82", atoms=(81,), optional=True).optional is True
    with pytest.raises(UnsupportedTopologyError):
        BindingSite(id="O82", atoms=(81,), optional=1)  # type: ignore[arg-type]


def test_graph_coordination_spec_detached_donors_validation() -> None:
    sites = (BindingSite(id="O39", atoms=(38,)), BindingSite(id="N40", atoms=(39,)))
    spec = CoordinationSpec(metal_center=0, binding_sites=sites, shapes=("input",))
    assert spec.detached_donors == ()
    detached = CoordinationSpec(
        metal_center=0, binding_sites=sites, shapes=("input",), detached_donors=(81,)
    )
    assert detached.detached_donors == (81,)
    assert detached.donor_indices == (38, 39)
    with pytest.raises(UnsupportedTopologyError):
        CoordinationSpec(
            metal_center=0, binding_sites=sites, shapes=("input",), detached_donors=(38,)
        )
    with pytest.raises(UnsupportedTopologyError):
        CoordinationSpec(
            metal_center=0, binding_sites=sites, shapes=("input",), detached_donors=(0,)
        )
    with pytest.raises(UnsupportedTopologyError):
        CoordinationSpec(
            metal_center=0, binding_sites=sites, shapes=("input",), detached_donors=(81, 81)
        )
    with pytest.raises(UnsupportedTopologyError):
        CoordinationSpec(
            metal_center=0, binding_sites=sites, shapes=("input",), detached_donors=(-1,)
        )


def test_scientific_native_omits_optional_key_when_no_optional_sites() -> None:
    doc = _doc([_site("O39", 39), _site("N40", 40), _site("O41", 41)])
    native = ConfgenModelV3.model_validate(doc).scientific_native()
    assert native["coordination"]["binding_sites"] == [
        {"id": "O39", "kind": "atom", "atoms": [39], "hapticity": 1},
        {"id": "N40", "kind": "atom", "atoms": [40], "hapticity": 1},
        {"id": "O41", "kind": "atom", "atoms": [41], "hapticity": 1},
    ]
    assert all("optional" not in site for site in native["coordination"]["binding_sites"])


def test_scientific_native_emits_optional_true_only() -> None:
    doc = _doc([_site("O39", 39, optional=False), _site("N40", 40), _site("O82", 82, True)])
    sites = ConfgenModelV3.model_validate(doc).scientific_native()["coordination"]["binding_sites"]
    assert "optional" not in sites[0]
    assert "optional" not in sites[1]
    assert sites[2]["optional"] is True
