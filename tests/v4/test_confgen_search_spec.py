#!/usr/bin/env python3

"""ConfGen v4 search-spec validation branches (search lane).

Exercises the public fail-closed boundary of ``search_spec`` (spec
normalization, coordination and constraint resolution, starts and seed rules)
and the index and topology checks of ``topology`` with valid and rejected
inputs. Every rejection asserts the exception type and a distinctive fragment
of its message; every accepted input asserts the returned value.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.domain import StructureRecord
from confflow.science.confgen.graph import CoordinationSpec, EdgeType
from confflow.science.confgen.search_spec import (
    build_search_context,
    normalize_search_spec,
    resolve_search_coordination,
    resolve_search_starts,
)
from confflow.science.confgen.topology import build_typed_graph, convert_index
from confflow.workflow.v4.confgen_schema import ConfgenModelV3

_BASE: dict[str, Any] = {"schema_version": 4, "index_base": 1, "seed": 1}


def _chain(n: int = 3) -> StructureRecord:
    """Carbon chain at 1.5 A spacing: neighbours bond, next-nearest do not."""
    return StructureRecord(
        id=f"chain{n}",
        atoms=("C",) * n,
        coordinates=tuple((1.5 * i, 0.0, 0.0) for i in range(n)),
        charge=0,
        multiplicity=1,
    )


def _fe_n4() -> StructureRecord:
    """Fe with four N donors on tetrahedral vertices, 2.0 A from the metal."""
    directions = [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]
    coords = [(0.0, 0.0, 0.0)] + [
        (2.0 * x / 1.732, 2.0 * y / 1.732, 2.0 * z / 1.732) for x, y, z in directions
    ]
    return StructureRecord(
        id="fe4",
        atoms=("Fe", "N", "N", "N", "N"),
        coordinates=tuple(coords),
        charge=0,
        multiplicity=1,
    )


def _section(**overrides: Any) -> dict[str, Any]:
    """Return a valid resolved (internal 0-based) tetrahedral coordination section."""
    section: dict[str, Any] = {
        "metal_center": 0,
        "binding_sites": [
            {"id": f"s{i}", "kind": "atom", "atoms": [i + 1], "hapticity": 1} for i in range(4)
        ],
        "shapes": ["tetrahedral"],
    }
    section.update(overrides)
    return section


def _site_section(position: int, **changes: Any) -> dict[str, Any]:
    """Return a valid section with one binding site's keys replaced or removed."""
    sites = [dict(site) for site in _section()["binding_sites"]]
    sites[position].update(changes)
    return _section(binding_sites=sites)


def _constraint(**overrides: Any) -> dict[str, Any]:
    """Return a valid forbidden-transition constraint between sites s0 and s1."""
    entry: dict[str, Any] = {
        "id": "C00",
        "kind": "FORBIDDEN_TRANS",
        "sites": ["s0", "s1"],
        "classification": "REJECTED_BY_POLICY",
        "provenance": "policy",
    }
    entry.update(overrides)
    return entry


def test_normalize_minimal_spec_gets_defaults() -> None:
    spec = normalize_search_spec({"schema_version": 4, "seed": 5})
    assert spec == {
        "schema_version": 4,
        "index_convention": "internal-0-based:normalized",
        "index_base": 0,
        "topology": {},
        "tolerances": {"bond_scale": 1.15},
        "coordination": None,
        "seed": 5,
    }


def test_normalize_converts_one_based_sections_and_drops_search() -> None:
    raw = {
        "schema_version": 4,
        "index_base": 1,
        "index_convention": "internal-0-based:normalized",
        "seed": 7,
        "topology": {
            "bonds": [[1, 2], {"atoms": [2, 3], "kind": "FORMING", "provenance": "rxn"}],
            "atoms": [{"index": 1, "label": "Fe", "role": "metal"}],
        },
        "tolerances": {"bond_scale": 1.3},
        "coordination": {
            "metal_center": 1,
            "shapes": ["tetrahedral"],
            "binding_sites": [{"id": f"s{i}", "atoms": [i + 1]} for i in range(1, 5)],
        },
        "search": {"starts": 4},
    }
    spec = normalize_search_spec(raw)
    assert spec["topology"] == {
        "bonds": [[0, 1], {"atoms": [1, 2], "kind": "FORMING", "provenance": "rxn"}],
        "atoms": [{"index": 0, "label": "Fe", "role": "metal", "stereo": None}],
    }
    assert spec["tolerances"] == {"bond_scale": 1.3}
    assert spec["index_base"] == 0
    assert spec["seed"] == 7
    assert spec["coordination"]["metal_center"] == 0
    assert [site["atoms"] for site in spec["coordination"]["binding_sites"]] == [
        [1],
        [2],
        [3],
        [4],
    ]
    assert "search" not in spec


def test_stable_chemistry_and_reaction_metadata_roundtrip() -> None:
    model = ConfgenModelV3.model_validate(
        {
            "schema_version": 4,
            "index_base": 1,
            "seed": 9,
            "topology": {
                "bonds": [{"atoms": [1, 2], "kind": "COVALENT", "bond_order": 1.0}],
                "reaction_pairs": [[2, 3]],
                "atoms": [
                    {"index": 1, "formal_charge": 0},
                    {"index": 2, "formal_charge": -1},
                    {"index": 3, "formal_charge": 0},
                ],
            },
        }
    )
    anionic_chain = StructureRecord(
        id="anionic-chain",
        atoms=("C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (3.0, 0.0, 0.0)),
        charge=-1,
        multiplicity=1,
    )
    context = build_search_context(anionic_chain, model.scientific_native())
    assert [atom.formal_charge for atom in context.graph.atoms] == [0, -1, 0]
    assert context.graph.reaction_pairs == ((1, 2),)
    assert context.graph.pairs_of_kind(EdgeType.COVALENT) == ((0, 1),)
    assert context.graph.edges[0].bond_order == 1.0
    assert context.graph.total_charge == -1
    assert context.graph.multiplicity == 1
    encoded = context.graph.to_mapping(convention="internal0")
    restored = type(context.graph).from_mapping(encoded, ("C", "C", "C"), convention="internal0")
    assert restored == context.graph


@pytest.mark.parametrize("tolerances", [None, {}])
def test_normalize_missing_tolerances_use_default(tolerances: Any) -> None:
    assert normalize_search_spec({**_BASE, "tolerances": tolerances})["tolerances"] == {
        "bond_scale": 1.15
    }


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ([], "spec must be a mapping"),
        ({**_BASE, "rings": []}, "spec holds unknown keys"),
        ({"schema_version": 2, "seed": 1}, "schema_version must be 4, got 2"),
        ({"schema_version": 3, "seed": 1}, "no longer supported"),
        ({**_BASE, "index_base": 2}, "index_base must be 0 or 1"),
        ({"schema_version": 4, "seed": 1, "topology": {"bonds": [[0, 1]]}}, "no index_base"),
        ({"schema_version": 4, "seed": 1, "coordination": {"metal_center": 1}}, "no index_base"),
        (
            {"schema_version": 4, "seed": 1, "coordination": {"binding_sites": [{"atoms": [1]}]}},
            "no index_base",
        ),
        ({**_BASE, "index_convention": "internal-1-based"}, "is not recognized"),
        ({**_BASE, "topology": []}, "topology must be a mapping"),
        ({**_BASE, "topology": {"rings": []}}, "topology holds unknown keys"),
        ({**_BASE, "topology": {"bonds": "01"}}, "topology.bonds must be a list"),
        ({**_BASE, "topology": {"del_bond": 5}}, "topology.del_bond must be a list"),
        ({**_BASE, "topology": {"add_bond": "ab"}}, "topology.add_bond must be a list"),
        ({**_BASE, "topology": {"atoms": "ab"}}, "topology.atoms must be a list"),
        (
            {**_BASE, "topology": {"bonds": [[1, 2]], "add_bond": [[2, 3]]}},
            "explicit topology.bonds wins outright",
        ),
        ({**_BASE, "topology": {"bonds": [[0, 1]]}}, "below declared index_base 1"),
        ({**_BASE, "tolerances": []}, "tolerances must be a mapping"),
        ({**_BASE, "tolerances": {"clash": 0.5}}, "holds unknown keys"),
        ({**_BASE, "coordination": "Fe"}, "coordination must be a mapping or null"),
        (
            {**_BASE, "coordination": {"metal_center": 1, "treatment": "enumerate"}},
            "coordination holds unknown keys",
        ),
        ({**_BASE, "coordination": {"binding_sites": "abc"}}, "binding_sites must be a list"),
        (
            {**_BASE, "coordination": {"binding_sites": [7]}},
            r"binding_sites\[0\] must be a mapping",
        ),
        (
            {**_BASE, "coordination": {"binding_sites": [{"id": "s", "atoms": [0]}]}},
            "below declared index_base 1",
        ),
        ({**_BASE, "seed": "3"}, "must be an integer or null"),
        ({**_BASE, "seed": True}, "must be an integer or null"),
    ],
)
def test_normalize_rejects_malformed_spec(raw: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        normalize_search_spec(raw)


@pytest.mark.parametrize("bond_scale", ["1.2", True, None, 0, -1.5, float("nan"), float("inf")])
def test_normalize_rejects_non_positive_bond_scale(bond_scale: Any) -> None:
    with pytest.raises(ValueError, match="bond_scale must be a positive number"):
        normalize_search_spec({**_BASE, "tolerances": {"bond_scale": bond_scale}})


def test_normalize_rejects_removed_coordination_key_by_name() -> None:
    raw = {**_BASE, "coordination": {"metal_center": 1, "backend": "rigid"}}
    with pytest.raises(ValueError, match=r"unknown keys \['backend'\]"):
        normalize_search_spec(raw)


@pytest.mark.parametrize(
    ("value", "base", "match"),
    [
        (True, 1, "p must be an integer atom index"),
        ("2", 0, "p must be an integer atom index"),
        (0, 1, "p index 0 below declared index_base 1"),
    ],
)
def test_convert_index_rejects_bad_value(value: Any, base: int, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        convert_index(value, base=base, path="p")


def test_convert_index_rebases_to_zero() -> None:
    assert convert_index(3, base=1, path="p") == 2
    assert convert_index(0, base=0, path="p") == 0


def test_resolve_coordination_accepts_valid_section() -> None:
    spec = resolve_search_coordination(_section())
    assert isinstance(spec, CoordinationSpec)
    assert spec.metal_center == 0
    assert spec.coordination_number == 4
    assert spec.shapes == ("tetrahedral",)
    assert [site.atoms for site in spec.binding_sites] == [(1,), (2,), (3,), (4,)]
    assert spec.constraints == ()


def test_resolve_coordination_accepts_constraints_with_default_ids() -> None:
    entries = [_constraint(), {k: v for k, v in _constraint().items() if k != "id"}]
    spec = resolve_search_coordination(_section(constraints=entries))
    assert [c.id for c in spec.constraints] == ["C00", "C01"]
    first = spec.constraints[0]
    assert first.sites == ("s0", "s1")
    assert first.classification == "REJECTED_BY_POLICY"
    assert first.provenance == "policy"


@pytest.mark.parametrize(
    ("section", "match"),
    [
        ("not-a-mapping", "coordination section must be a mapping"),
        ({**_section(), "budgets": {}}, "holds unknown keys"),
        (_section(metal_center=-1), "metal_center must be a non-negative int"),
        (_section(metal_center=True), "metal_center must be a non-negative int"),
        (_section(metal_center=None), "metal_center must be a non-negative int"),
        (_section(binding_sites=_section()["binding_sites"][:3]), "must hold 4 to 6 sites"),
        (_section(binding_sites="abcd"), "must hold 4 to 6 sites"),
        (_section(binding_sites=[5, *_section()["binding_sites"][1:]]), "must be a mapping"),
        (_site_section(0, treatment="enumerate"), "holds unknown keys"),
        (_site_section(0, id=""), r"binding_sites\[0\]\.id must be a non-empty string"),
        (_site_section(0, id=7), r"binding_sites\[0\]\.id must be a non-empty string"),
        (_site_section(1, id="s0"), "duplicate coordination binding site id 's0'"),
        (_site_section(0, kind="fragment"), "kind must be 'atom'"),
        (_site_section(0, atoms=None), "must hold exactly one atom index"),
        (_site_section(0, atoms=[1, 2]), "must hold exactly one atom index"),
        (_site_section(0, atoms=[-1]), "must hold a non-negative int"),
        (_site_section(0, atoms=[True]), "must hold a non-negative int"),
        (_site_section(0, hapticity=2), "hapticity must be 1"),
        (_section(shapes=[]), "must name exactly one shape"),
        (_section(shapes=["tetrahedral", "square_planar"]), "must name exactly one shape"),
        (_section(shapes=["auto"]), "not 'auto'"),
        (_section(shapes=["octahedral"]), "is not admissible at CN=4"),
        (_section(shapes=["bogus"]), "unknown coordination shape 'bogus'"),
        (_section(metal_center=1), "metal center must not be a donor atom"),
        (_site_section(1, atoms=[1]), "duplicate donor atoms"),
        (_section(constraints=["C00"]), r"constraints\[0\] must be a mapping"),
        (_section(constraints=[_constraint(kind="CONTACT")]), "kind must be 'FORBIDDEN_TRANS'"),
        (_section(constraints=[_constraint(proof={})]), "holds unknown keys"),
        (_section(constraints=[_constraint(sites=["s0"])]), "must hold exactly two site ids"),
        (_section(constraints=[_constraint(sites=["s0", "s0"])]), "two distinct sites"),
        (_section(constraints=[_constraint(sites=["s0", "zz"])]), "unknown binding site 'zz'"),
        (
            _section(constraints=[_constraint(classification="ACCEPTED")]),
            "classification must be 'REJECTED_BY_POLICY'",
        ),
        (_section(constraints=[_constraint(provenance=None)]), "provenance must be a non-empty"),
    ],
)
def test_resolve_coordination_rejects_malformed_section(section: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        resolve_search_coordination(section)


@pytest.mark.parametrize(
    ("search", "has_coordination", "expected"),
    [
        (None, True, None),
        (None, False, 60),
        ({"starts": None}, True, None),
        ({"starts": 3}, True, 3),
    ],
)
def test_resolve_starts_defaults_and_declared(
    search: dict[str, Any] | None, has_coordination: bool, expected: int | None
) -> None:
    assert resolve_search_starts(search, has_coordination=has_coordination) == expected


@pytest.mark.parametrize("starts", [0, -2, True, "4", 2.0])
def test_resolve_starts_rejects_bad_count(starts: Any) -> None:
    with pytest.raises(ValueError, match="search.starts must be an integer >= 1 or null"):
        resolve_search_starts({"starts": starts}, has_coordination=False)


def test_build_search_context_accepts_coordination_and_ignores_search() -> None:
    raw = {
        "schema_version": 4,
        "index_base": 1,
        "seed": 3,
        "topology": {"bonds": [{"atoms": [1, i], "kind": "COORDINATION"} for i in (2, 3, 4, 5)]},
        "coordination": {
            "metal_center": 1,
            "shapes": ["tetrahedral"],
            "binding_sites": [{"id": f"s{i}", "atoms": [i]} for i in range(2, 6)],
        },
        "search": {"starts": 2},
    }
    context = build_search_context(_fe_n4(), raw)
    assert context.seed == 3
    assert context.shape == "tetrahedral"
    assert context.spec is not None
    assert context.spec.metal_center == 0
    kinds = {(edge.a, edge.b): edge.type for edge in context.graph.edges}
    assert kinds[(0, 1)] is EdgeType.COORDINATION
    assert kinds[(0, 4)] is EdgeType.COORDINATION


def test_build_search_context_without_coordination_has_no_shape() -> None:
    context = build_search_context(_chain(), {**_BASE, "seed": 9})
    assert context.seed == 9
    assert context.coordination_section is None
    assert context.spec is None
    assert context.shape is None
    assert context.adjacency == ((1,), (0, 2), (1,))


@pytest.mark.parametrize(
    ("native", "match"),
    [
        ([], "spec must be a mapping"),
        ({"schema_version": 4, "index_base": 1}, "requires an explicit top-level seed"),
        ({**_BASE, "seed": "3"}, "seed must be an integer, got '3'"),
        ({**_BASE, "seed": True}, "seed must be an integer"),
    ],
)
def test_build_search_context_rejects_bad_scope(native: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        build_search_context(_chain(), native)


def test_build_typed_graph_explicit_bonds_win() -> None:
    resolved = normalize_search_spec({**_BASE, "topology": {"bonds": [[1, 2], [2, 3]]}})
    adjacency, graph = build_typed_graph(_chain(), resolved["topology"], resolved)
    assert adjacency == [[1], [0, 2], [1]]
    assert sorted((e.a, e.b) for e in graph.edges) == [(0, 1), (1, 2)]


def test_build_typed_graph_deleting_absent_pair_is_silent() -> None:
    resolved = normalize_search_spec({**_BASE, "topology": {"del_bond": [[1, 3]]}})
    adjacency, _graph = build_typed_graph(_chain(), resolved["topology"], resolved)
    assert adjacency == [[1], [0, 2], [1]]


@pytest.mark.parametrize(
    ("topology", "match"),
    [
        ({"bonds": "01"}, "topology.bonds must be a list of entries"),
        ({"bonds": [[0, 9]]}, r"topology.bonds\[0\] index 9 out of range for 3 atoms"),
        ({"add_bond": "01"}, "topology.add_bond must be a list of entries"),
        ({"add_bond": [[0, -1]]}, r"topology.add_bond\[0\] index -1 out of range"),
        ({"del_bond": [[0, 7]]}, r"topology.del_bond\[0\] index 7 out of range"),
        (
            {"add_bond": [{"atoms": [0, 1], "kind": "COORDINATION"}, [1, 0]]},
            "already given a typed non-covalent role",
        ),
        (
            {"add_bond": [[0, 1], {"atoms": [1, 0], "kind": "COORDINATION"}]},
            "already declared COVALENT",
        ),
        ({"del_bond": [{"atoms": [0, 1], "kind": "FORMING"}]}, "must name a covalent pair"),
        ({"atoms": [{"index": 9}]}, r"topology.atoms declaration index 9 out of range"),
    ],
)
def test_build_typed_graph_rejects_bad_topology(topology: dict[str, Any], match: str) -> None:
    resolved = normalize_search_spec(dict(_BASE))
    with pytest.raises(ValueError, match=match):
        build_typed_graph(_chain(), topology, resolved)


def test_build_typed_graph_rejects_non_mapping_topology() -> None:
    resolved = normalize_search_spec(dict(_BASE))
    with pytest.raises(ValueError, match="topology must be a mapping"):
        build_typed_graph(_chain(), ["bonds"], resolved)
