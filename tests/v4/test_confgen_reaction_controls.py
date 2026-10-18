"""Regressions for the frozen-TS, local-embedding and generation-cost cause controls."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from confflow.science.confgen import dg_seed, explore, search
from confflow.science.confgen.graph import BindingSite, CoordinationSpec, TypedGraph
from tests.v4.test_confgen_explore import _butane, _FakeCalc, _settings


def _case(name):
    path = Path(__file__).parents[1] / "fixtures/confgen/reaction_controls/inputs.json"
    d = json.loads(path.read_text())[name]
    graph = TypedGraph.from_mapping(d["graph"], d["elements"], convention="internal0")
    return d, graph, np.asarray(d["reference"])


@pytest.mark.parametrize("seed", [12, 29])
def test_fully_frozen_nonstandard_reaction_center_embeds_without_disabling_stereo(seed):
    d, graph, reference = _case("epoxidation_chem")
    molecule = dg_seed.stable_chemistry_molecule(graph, reference)[0]
    Chem.AssignStereochemistryFrom3D(molecule)
    embedding, relaxed = dg_seed.embedding_molecule(graph, molecule, reference, d["frozen"])
    assert relaxed == (0,)
    assert embedding.GetAtomWithIdx(0).GetChiralTag() == Chem.ChiralType.CHI_UNSPECIFIED
    assert molecule.GetAtomWithIdx(0).GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED
    # Center 1 is fully frozen but geometrically ordinary; centers 4/6/9 are not fully frozen.
    for center in (1, 4, 6, 9):
        assert (
            embedding.GetAtomWithIdx(center).GetChiralTag()
            == molecule.GetAtomWithIdx(center).GetChiralTag()
        )
    assert not dg_seed.embedding_molecule(graph, molecule, reference, [0])[1]
    out = dg_seed.generate_dg_seeds(
        graph,
        None,
        reference,
        (),
        None,
        dg_seed.DGSeedSettings(
            count=3, random_seed=seed, timeout_seconds=5, freeze_core=tuple(d["frozen"])
        ),
    )
    assert len(out.coords) == 3
    assert out.embedding_relaxed_centers == (0,)
    assert 0 in out.stereo_centers  # the independent audit still protects this center
    for coords in out.coords:
        np.testing.assert_array_equal(coords[d["frozen"]], reference[d["frozen"]])
        assert not search.audit_structure(
            coords, graph, None, reference, None, stereo_centers=out.stereo_centers
        ).failed_checks


def test_three_ti_local_controls_share_global_embedding_relations_and_preserve_pinned_core():
    d, graph, reference = _case("Ti_elimination_chem")
    control = d["local_control"]
    parent = np.asarray(control["parent"])
    mol = dg_seed.stable_chemistry_molecule(graph, reference)[0]
    Chem.AssignStereochemistryFrom3D(mol)
    centers = tuple(a.GetIdx() for a in mol.GetAtoms() if "TETRAHEDRAL" in str(a.GetChiralTag()))
    spec = CoordinationSpec(
        d["metal"], tuple(BindingSite(str(i), atoms=(i,)) for i in d["donors"]), ("input",)
    )
    for k, mask in enumerate(control["masks"]):
        fixed = sorted(set(range(graph.natoms)) - set(mask))
        coords, error = explore._reembed_task(
            (graph, parent, frozenset(mask), control["restraints"], 12 + k, (), 5)
        )
        assert error is None and coords is not None
        np.testing.assert_array_equal(coords[fixed], parent[fixed])
        assert not search.audit_structure(
            coords, graph, spec, reference, "input", stereo_centers=centers
        ).failed_checks


def _failed_generator(monkeypatch):
    graph, reference = _butane()
    ex = explore._Explorer(graph, None, reference, None, _settings(), _FakeCalc(), map, None)
    ex.pool.add(-1.0, reference, "t00", "input")
    monkeypatch.setattr(ex, "_take", lambda *args, **kwargs: ex.pool.minima)
    monkeypatch.setattr(ex, "_free_sets", lambda n: [{0}] * n)
    calls = []

    def timed_fail(fn, tasks):
        calls.extend(tasks)
        return [((None, "embed"), 5.0, 4.5) for _ in tasks]

    ex.pmap = timed_fail
    return ex, calls


def test_failed_generation_cost_is_reported_and_reembed_is_backed_off(monkeypatch):
    ex, calls = _failed_generator(monkeypatch)
    assert not ex._reembed(4)
    initial = ex.generation["reembed"]
    assert initial["attempts"] == initial["failures"] == 4
    assert initial["generated"] == initial["optimized"] == 0
    assert initial["cpu_s"] == 18.0 and initial["worker_wall_s"] == 20.0
    assert ex.spent["reembed"] == 0
    assert ex._reembed_share() == 0.0
    for round_index in range(1, 60):
        ex.round_index = round_index
        ex._reembed(10)
    # Three single-task probes, rather than retrying the fixed allocation indefinitely.
    assert len(calls) == 7
    assert ex.reembed_disabled
    stats = ex._stats()
    assert stats["generation"]["reembed"]["cpu_s"] == 31.5
    assert stats["reembed_scheduler"]["disabled"]


def test_generation_cpu_cost_reduces_allocation_and_success_recovers_from_backoff(monkeypatch):
    ex, _ = _failed_generator(monkeypatch)
    ex._reembed(4)
    ex.round_index = ex.reembed_next_round
    ex.pmap = lambda fn, tasks: [((ex.ref.copy(), None), 0.01, 0.01) for _ in tasks]
    assert len(ex._reembed(10)) == 1
    assert ex.reembed_backoff_level == ex.reembed_failure_streak == 0
    ex.generation["dg"].update(attempts=10, generated=10, cpu_s=1.0)
    ex.generation["reembed"].update(attempts=10, generated=10, cpu_s=100.0)
    assert 0 < ex._reembed_share() < ex.st.share_reembed
