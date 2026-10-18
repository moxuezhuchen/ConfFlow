"""Chemistry-context regression for broad metal near-contact screens."""

import numpy as np
import pytest

from confflow.science.confgen import search
from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph


def _contacts(metal, elements, bonds, reference, candidate):
    graph = TypedGraph(
        tuple(AtomRef(i, e) for i, e in enumerate([metal, *elements])),
        tuple(TypedEdge(a, b, EdgeType.COVALENT, order) for a, b, order in bonds),
        0,
        (),
        "contact-test",
    )
    return search._coordination_contacts(
        np.array(candidate, float), np.array(reference, float), graph, ()
    )


def test_rb_saturated_carbon_and_ch_hydrogen_are_explained_proximity():
    # Ordinary tetrahedral methyl context: the generous Rb covalent-radius screen
    # captures both C and H. Neither alone establishes a new donor.
    xyz = np.array(
        [[0, 0, 0], [3.5, 0, 0], [2.4, 0, 0], [3.9, 1, 0], [3.9, -0.5, 0.87], [3.9, -0.5, -0.87]]
    )
    ref = xyz.copy()
    ref[1:] += [2, 0, 0]
    contacts = _contacts(
        "Rb", ["C", "H", "H", "H", "H"], [(1, j, 1) for j in range(2, 6)], ref, xyz
    )
    assert contacts
    assert not any(c.changed for c in contacts)
    assert {c.mode for c in contacts} == {
        "saturated_carbon_proximity",
        "carbon_bound_hydrogen_proximity",
    }
    assert contacts[0].neighbors == (2, 3, 4, 5)
    assert contacts[0].reference_distance > contacts[0].distance


@pytest.mark.parametrize("element", ["O", "N", "C", "H"])
def test_potential_donors_including_organometallic_carbon_and_hydride_still_flag(element):
    contacts = _contacts("Ti", [element], [], [[0, 0, 0], [5, 0, 0]], [[0, 0, 0], [2, 0, 0]])
    assert len(contacts) == 1
    assert contacts[0].changed


def test_transition_metal_bent_short_ch_contact_retains_agostic_warning():
    contacts = _contacts(
        "Ti",
        ["H", "C"],
        [(1, 2, 1)],
        [[0, 0, 0], [5, 0, 0], [5.3, 1, 0]],
        [[0, 0, 0], [1.8, 0, 0], [2.1, 1, 0]],
    )
    hydrogen = next(c for c in contacts if c.atom == 1)
    assert hydrogen.changed
    assert hydrogen.mode == "possible_agostic_hydrogen"


def test_extremely_short_ionic_ch_contact_remains_rejected():
    contacts = _contacts(
        "Rb",
        ["H", "C"],
        [(1, 2, 1)],
        [[0, 0, 0], [5, 0, 0], [6.1, 0, 0]],
        [[0, 0, 0], [1.5, 0, 0], [2.6, 0, 0]],
    )
    assert next(c for c in contacts if c.atom == 1).changed


def test_evidence_is_serializable_in_search_record():
    contact = _contacts("Ti", ["O"], [], [[0, 0, 0], [5, 0, 0]], [[0, 0, 0], [2, 0, 0]])[0]
    record = search.SearchRecord("t00", 0, False, None, False, coordination_contacts=(contact,))
    assert record.to_dict()["coordination_contacts"][0]["atom"] == 1


def test_non_carbon_bound_hydrogen_remains_conservative():
    contacts = _contacts(
        "Rb",
        ["H", "O"],
        [(1, 2, 1)],
        [[0, 0, 0], [5, 0, 0], [6, 0, 0]],
        [[0, 0, 0], [2.4, 0, 0], [3.4, 0, 0]],
    )
    hydrogen = next(c for c in contacts if c.atom == 1)
    assert hydrogen.changed
    assert hydrogen.mode == "possible_hydride_or_other_hydrogen"


def test_archived_jigpuk_contact_witnesses_have_explicit_proximity_evidence():
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures/confgen/coordination/jigpuk_contact_witnesses.json"
    )
    for case in json.loads(path.read_text()):
        graph = TypedGraph(
            tuple(AtomRef(i, e) for i, e in enumerate(case["elements"])),
            tuple(TypedEdge(a, b, EdgeType.COVALENT, order) for a, b, order in case["bonds"]),
            0,
            (),
            case["source"],
        )
        contacts = search._coordination_contacts(
            np.array(case["candidate"]), np.array(case["reference"]), graph, case["donors"]
        )
        witnesses = [c for c in contacts if c.atom in case["hits"]]
        assert len(witnesses) == len(case["hits"])
        assert not any(c.changed for c in witnesses)
        assert all(
            c.mode in {"saturated_carbon_proximity", "carbon_bound_hydrogen_proximity"}
            for c in witnesses
        )
