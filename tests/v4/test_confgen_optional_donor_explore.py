"""Optional donors in the explorer: measured states, arrangement and stretch over required donors.

A Pt square-planar input with four donors; donor 4 is optional. Two declarations are compared:
bound-optional (donor 4 in ``binding_sites`` with ``optional=True``) and detached (donor 4 in
``detached_donors`` only). The engine is the fake calculator from the arrangement tests.
"""

from __future__ import annotations

import numpy as np
import pytest

from confflow.science.confgen import explore, search
from confflow.science.confgen.graph import (
    INPUT_SHAPE,
    AtomRef,
    BindingSite,
    CoordinationSpec,
    EdgeType,
    TypedEdge,
    TypedGraph,
)
from tests.v4.test_confgen_arrangement_search_core import CIS, _square
from tests.v4.test_confgen_input_core_explore import _FakeCalc, _settings

REF_R = 2.3


def _graph() -> TypedGraph:
    atoms = (AtomRef(0, "Pt"), AtomRef(1, "Cl"), AtomRef(2, "Cl"), AtomRef(3, "Br"),
             AtomRef(4, "Br"))  # fmt: skip
    edges = tuple(TypedEdge(a=0, b=i, type=EdgeType.COORDINATION) for i in range(1, 5))
    return TypedGraph(atoms, edges, 0, (), "pt_optional")


def _spec(optional_bound: bool) -> CoordinationSpec:
    sites = [BindingSite(id=f"D{i}", atoms=(i,)) for i in (1, 2, 3)]
    if optional_bound:
        sites.append(BindingSite(id="D4", atoms=(4,), optional=True))
        return CoordinationSpec(metal_center=0, binding_sites=tuple(sites), shapes=(INPUT_SHAPE,))
    return CoordinationSpec(
        metal_center=0, binding_sites=tuple(sites), shapes=(INPUT_SHAPE,), detached_donors=(4,)
    )


def _explorer(
    optional_bound: bool = True, arrangement: str = "search", **kw: object
) -> explore._Explorer:
    st = _settings(arrangement=arrangement, **kw)
    return explore._Explorer(_graph(), _spec(optional_bound), _square(CIS, REF_R), INPUT_SHAPE,
                             st, _FakeCalc(), map, None)  # fmt: skip


def _moved(atom: int, dist: float) -> np.ndarray:
    xyz = _square(CIS, REF_R)
    direction = xyz[atom] / np.linalg.norm(xyz[atom])
    xyz[atom] = direction * dist
    return xyz


def test_optional_bound_donor_is_optional_and_not_a_required_donor() -> None:
    ex = _explorer(optional_bound=True)
    assert ex.optional_donors == (4,)
    assert ex.required_donors == [1, 2, 3]
    assert ex.arr_donors == [1, 2, 3]
    assert abs(ex.optional_refs[4] - REF_R) < 1e-9


def test_detached_donor_has_reference_distance_and_detached_restraint() -> None:
    ex = _explorer(optional_bound=False)
    assert ex.donors == [1, 2, 3]
    assert ex.optional_donors == (4,)
    assert abs(ex.optional_refs[4] - REF_R) < 1e-9
    # restrained stage holds the detached donor at 1.5 x its input distance (equality)
    detached = [r for r in ex.restraints if 4 in (r.i, r.j)]
    assert len(detached) == 1
    assert (detached[0].i, detached[0].j) == (0, 4)
    assert detached[0].target == pytest.approx(search.DETACHED_DISTANCE_FACTOR * REF_R)
    # the release relaxes reaction pairs only: the detached donor is free there
    assert all(r.j != 4 and r.i != 4 for r in ex.free_restraints)


def test_bound_optional_donor_is_held_at_its_input_distance() -> None:
    ex = _explorer(optional_bound=True)
    held = [r for r in ex.restraints if 4 in (r.i, r.j)]
    assert len(held) == 1 and held[0].target == pytest.approx(REF_R, abs=1e-6)


def test_detached_restraint_is_not_symmetry_averaged_with_bound_donors() -> None:
    ex = _explorer(optional_bound=False)
    targets = {r.j: r.target for r in ex.restraints if r.i == 0}
    assert targets[1] == pytest.approx(REF_R, abs=1e-6)
    assert targets[4] == pytest.approx(search.DETACHED_DISTANCE_FACTOR * REF_R)


def test_optional_state_is_measured_against_the_input_distance() -> None:
    ex = _explorer(optional_bound=False)
    assert ex._optional_bound(_square(CIS, REF_R)) == {4: True}
    assert ex._optional_bound(_moved(4, 1.2 * REF_R)) == {4: True}  # within 1.3 x reference
    assert ex._optional_bound(_moved(4, 1.5 * REF_R)) == {4: False}


def test_no_optional_declaration_leaves_payload_and_arrangement_unchanged() -> None:
    graph = _graph()
    plain = CoordinationSpec(
        metal_center=0,
        binding_sites=tuple(BindingSite(id=f"D{i}", atoms=(i,)) for i in (1, 2, 3, 4)),
        shapes=(INPUT_SHAPE,),
    )
    ex = explore._Explorer(graph, plain, _square(CIS, REF_R), INPUT_SHAPE, _settings(),
                           _FakeCalc(), map, None)  # fmt: skip
    assert ex.optional_donors == () and ex.optional_refs == {}
    assert ex.arr_donors == ex.donors == [1, 2, 3, 4]
    assert ex._optional_bound(_square(CIS, REF_R)) is None


def test_arrangement_is_over_required_donors_only() -> None:
    ex = _explorer(optional_bound=True)
    assert ex.arr_ref is not None and ex.arr_ref.shape == (3, 3)
    assert all(len(pm) == 3 for pm in ex.arr_perms)
    # the optional donor moving far away does not change the arrangement of the required ones
    assert ex._arrangement_of(_moved(4, 3.0)) == "a0"


def test_donor_stretch_excludes_optional_donors() -> None:
    ex = _explorer(optional_bound=True)
    stretch, atom = ex._donor_stretch(_moved(4, 3.0 * REF_R))
    assert stretch == 1.0 and atom in (1, 2, 3)
    stretch, atom = ex._donor_stretch(_moved(2, 1.4 * REF_R))
    assert abs(stretch - 1.4) < 1e-9 and atom == 2


def test_optional_donor_stretch_is_not_a_failure_by_itself() -> None:
    ex = _explorer(optional_bound=True, max_donor_stretch=1.2)
    _, bad = ex._audit(_moved(4, 1.6 * REF_R), True, True)
    assert "donor_stretch" not in bad
    _, bad = ex._audit(_moved(2, 1.6 * REF_R), True, True)
    assert "donor_stretch" in bad


def test_explore_records_carry_measured_optional_state() -> None:
    run = explore.explore(_graph(), _spec(True), _square(CIS, REF_R), INPUT_SHAPE,
                          _settings(budget=4, starts=1, batch=4), _FakeCalc(), map)  # fmt: skip
    assert run.records
    for rec in run.records:
        assert rec.optional_bound is not None
        assert set(rec.optional_bound) == {4}
        assert rec.optional_bound[4] == search.optional_bound_state(rec.coords, 0, {4: REF_R})[4]


def test_explore_without_optional_donors_has_no_optional_state() -> None:
    plain = CoordinationSpec(
        metal_center=0,
        binding_sites=tuple(BindingSite(id=f"D{i}", atoms=(i,)) for i in (1, 2, 3, 4)),
        shapes=(INPUT_SHAPE,),
    )
    run = explore.explore(_graph(), plain, _square(CIS, REF_R), INPUT_SHAPE,
                          _settings(budget=4, starts=1, batch=4), _FakeCalc(), map)  # fmt: skip
    assert run.records
    assert all(rec.optional_bound is None for rec in run.records)
