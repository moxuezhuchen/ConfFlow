#!/usr/bin/env python3

"""Phase 0 PathResolver acceptance tests (input simplification).

Covers the Phase 0 contract: simple path resolution, legacy equivalence
(rotors/moving sets/angles/counts plus numerical geometries), branches moving
without extra DOFs, explicit branch paths, cycle refusal, ring endpoints,
shared-rotor dedup, opposite-direction conflict, disconnected/invalid
endpoints, warning/strict mode, pregeometry limit refusal, unchanged atom
ordering, reversed-path/asymmetric-grid checks, sampling conflicts,
mixed chains/paths conflicts, topology-patch ring/connectivity changes,
unrelated disconnected components, deterministic declaration-order policy,
deferred resolution for upstream structures, duplicate source audit, refused
task-spaces never calling geometry, invalid/nonfinite values, 1-based
boundary behavior, typed expansion, producer contract/manifest, and freeze
fail-closed retention.
"""

from __future__ import annotations

import json
import os
from typing import Any

import numpy as np
import pytest

from confflow.domain import FrozenDict, StructureRecord
from confflow.domain.completion import WorkItemStatus
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.science.confgen.torsion.paths import (
    PATH_AMBIGUOUS,
    PATH_CROSSES_RING,
    PATH_DIRECTION_CONFLICT,
    PATH_DISCONNECTED,
    PATH_INVALID_ENDPOINT,
    PATH_SHORT_BOND,
    ROTOR_SAMPLING_CONFLICT,
    PathResolutionError,
    canonicalize_rotors,
    parse_path_declarations,
    resolve_paths,
)
from tests.v4.test_repair_executors import _butane, _ctx, _item, _sci


def _linear(n: int = 4, spacing: float = 1.5) -> list[list[int]]:
    """Return the adjacency of a linear chain (0-based)."""
    adj: list[list[int]] = [[] for _ in range(n)]
    for i in range(n - 1):
        adj[i].append(i + 1)
        adj[i + 1].append(i)
    return adj


def _branched() -> tuple[StructureRecord, list[list[int]]]:
    """Chain 1-2-3-4 with a branch atom 5 on atom 2 (0-based adjacency)."""
    record = StructureRecord(
        id="branched",
        atoms=("C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (3.0, 0.4, 0.0),
            (4.5, 0.4, 0.0),
            (1.5, 1.4, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )
    adj = [[1], [0, 2, 4], [1, 3], [2], [1]]
    return record, adj


def _ring_tail() -> tuple[StructureRecord, list[list[int]]]:
    """Square ring 1-4 plus tail atom 5 on atom 4."""
    record = StructureRecord(
        id="ring-tail",
        atoms=("C", "C", "C", "C", "C"),
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (1.5, 1.5, 0.0),
            (0.0, 1.5, 0.0),
            (0.0, 3.0, 0.0),
        ),
        charge=0,
        multiplicity=1,
    )
    adj = [[1, 3], [0, 2], [1, 3], [2, 0, 4], [3]]
    return record, adj


def _legacy_result(records: list[StructureRecord], native: dict[str, Any], tmp_path) -> Any:
    """Execute the legacy executor (FAILED results returned, never raised)."""
    item = _item("c1:g1", "c1", records)
    sci = _sci(seed=11, native=FrozenDict(dict(native)))
    return ConfgenExecutor().execute(item, _ctx(sci, str(tmp_path)))


def _failed_message(result: Any) -> str:
    """Return the failure diagnostic message of a FAILED result."""
    assert result.status is WorkItemStatus.FAILED
    return " ".join(d.message for d in result.diagnostics)


# -- simple resolution -------------------------------------------------------


def test_simple_path_resolution_order_and_sets():
    """One path declares every consecutive bond with cut-rule moving sets."""
    parsed = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end"}], index_base=1, default_step=120
    )
    resolution = resolve_paths(parsed, _linear())
    assert [r.bond for r in resolution.rotors] == [(0, 1), (1, 2), (2, 3)]
    assert [r.ordered for r in resolution.rotors] == [(0, 1), (1, 2), (2, 3)]
    assert [r.moving for r in resolution.rotors] == [(2, 3), (3,), ()]
    assert resolution.rotors[0].fixed == (0, 1)
    assert resolution.rotors[1].sources == ("paths[0]",)
    assert all(r.angles == (0.0, 120.0, 240.0) for r in resolution.rotors)
    assert resolution.raw_cartesian_size == 27
    assert resolution.topology_digest.startswith("sha256:")
    assert resolution.warnings == ()


def test_move_start_selects_opposite_component():
    """move=start mirrors the moving sets of move=end."""
    parsed = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "start"}], index_base=1, default_step=120
    )
    resolution = resolve_paths(parsed, _linear())
    assert [r.moving for r in resolution.rotors] == [(), (0,), (0, 1)]
    assert [r.moving_atom for r in resolution.rotors] == [0, 1, 2]


def test_axis_atoms_remain_unmoved():
    """Bond atoms are never in their own rotor's moving set."""
    parsed = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 90.0]}],
        index_base=1,
        default_step=None,
    )
    for rotor in resolve_paths(parsed, _linear()).rotors:
        assert rotor.ordered[0] not in rotor.moving
        assert rotor.ordered[1] not in rotor.moving
        assert set(rotor.moving) | set(rotor.fixed) == {0, 1, 2, 3}


# -- branches -----------------------------------------------------------------


def test_branch_atoms_move_without_extra_dofs():
    """Branch atoms ride rigidly; only path bonds become rotors."""
    record, adj = _branched()
    assert len(record.atoms) == 5
    parsed = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(parsed, adj)
    assert len(resolution.rotors) == 3  # no branch-rotor invention
    first = resolution.rotors[0]
    assert first.bond == (0, 1)
    assert 4 in first.moving  # branch rides with the moving component
    assert first.fixed == (0, 1)


def test_explicit_branch_path():
    """A path may end inside a branch (branch bonds are bridges)."""
    _, adj = _branched()
    parsed = parse_path_declarations(
        [{"start": 3, "end": 5, "move": "end", "angles": [0.0, 60.0]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(parsed, adj)
    assert [r.bond for r in resolution.rotors] == [(1, 2), (1, 4)]
    # The branch tip is the axis atom of its own bond: it stays fixed by
    # rule (axis atoms never move), so its moving set is empty.
    assert resolution.rotors[1].moving == ()
    assert resolution.rotors[1].moving_atom == 4
    assert resolution.rotors[0].moving == (0, 4)


# -- rings --------------------------------------------------------------------


def test_ring_atoms_may_be_endpoints():
    """A bridge-only route starting on a ring atom resolves normally."""
    _, adj = _ring_tail()
    parsed = parse_path_declarations(
        [{"start": 4, "end": 5, "move": "end", "angles": [0.0, 120.0]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(parsed, adj)
    assert [r.bond for r in resolution.rotors] == [(3, 4)]
    assert resolution.rotors[0].moving == ()


def test_cycle_edges_refused():
    """Every full-graph route across a ring fails with PATH_CROSSES_RING."""
    _, adj = _ring_tail()
    parsed = parse_path_declarations(
        [{"start": 1, "end": 3, "move": "end", "angles": [0.0]}],
        index_base=1,
        default_step=None,
    )
    with pytest.raises(PathResolutionError) as exc:
        resolve_paths(parsed, adj)
    assert exc.value.code == PATH_CROSSES_RING


def test_ambiguous_code_reserved_never_emitted():
    """PATH_AMBIGUOUS exists but no bridge-only fixture can trigger it."""
    assert PATH_AMBIGUOUS == "PATH_AMBIGUOUS"
    # A diamond with a ring diagonal: full-graph alternatives exist, but the
    # bridge-only forest admits exactly one route (or none -> ring refusal).
    adj = [[1, 2], [0, 3], [0, 3], [1, 2]]
    parsed = parse_path_declarations(
        [{"start": 1, "end": 3, "move": "end", "angles": [0.0]}],
        index_base=1,
        default_step=None,
    )
    with pytest.raises(PathResolutionError) as exc:
        resolve_paths(parsed, adj)
    assert exc.value.code == PATH_CROSSES_RING


# -- connectivity --------------------------------------------------------------


def test_disconnected_endpoints():
    """Endpoints in different components fail with PATH_DISCONNECTED."""
    adj = [[1], [0], [3], [2]]
    parsed = parse_path_declarations(
        [{"start": 1, "end": 3, "move": "end", "angles": [0.0]}],
        index_base=1,
        default_step=None,
    )
    with pytest.raises(PathResolutionError) as exc:
        resolve_paths(parsed, adj)
    assert exc.value.code == PATH_DISCONNECTED


def test_invalid_endpoints_range():
    """Out-of-range references fail with PATH_INVALID_ENDPOINT at resolve."""
    parsed = parse_path_declarations(
        [{"start": 1, "end": 9, "move": "end", "angles": [0.0]}],
        index_base=1,
        default_step=None,
    )
    with pytest.raises(PathResolutionError) as exc:
        resolve_paths(parsed, _linear())
    assert exc.value.code == PATH_INVALID_ENDPOINT


def test_unrelated_disconnected_components_stay_unmoved():
    """Atoms outside the endpoint fragment land in fixed, never moving."""
    adj = [[1], [0], [3], [2]]  # two detached pairs
    parsed = parse_path_declarations(
        [{"start": 1, "end": 2, "move": "end", "angles": [0.0, 90.0]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(parsed, adj, n_atoms=4)
    (rotor,) = resolution.rotors
    assert rotor.moving == ()
    assert rotor.fixed == (0, 1, 2, 3)


def test_topology_patch_connectivity_changes():
    """add/del corrections change the working graph before resolution."""
    adj = [[1], [0], [3], [2]]
    parsed = parse_path_declarations(
        [{"start": 1, "end": 3, "move": "end", "angles": [0.0]}],
        index_base=1,
        default_step=None,
    )
    with pytest.raises(PathResolutionError) as exc:
        resolve_paths(parsed, adj)
    assert exc.value.code == PATH_DISCONNECTED
    patched = [[1], [0, 2], [1, 3], [2]]  # add_bond 2-3 analogue
    resolution = resolve_paths(parsed, patched)
    assert [r.bond for r in resolution.rotors] == [(0, 1), (1, 2)]
    # Same route through a larger graph: identical bonds, distinct digest.
    branched = [[1], [0, 2, 4], [1, 3], [2], [1]]
    other = resolve_paths(parsed, branched)
    assert [r.bond for r in other.rotors] == [(0, 1), (1, 2)]
    assert resolution.topology_digest != other.topology_digest


# -- dedup and conflicts --------------------------------------------------------


def test_shared_rotor_dedup_with_source_audit():
    """Identical duplicates merge with every source recorded."""
    one = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        index_base=1,
        default_step=None,
        source_prefix="paths",
    )
    two = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        index_base=1,
        default_step=None,
        source_prefix="extra",
    )
    merged = canonicalize_rotors(
        [*resolve_paths(one, _linear()).rotors, *resolve_paths(two, _linear()).rotors]
    )
    assert len(merged) == 3
    assert merged[0].sources == ("paths[0]", "extra[0]")


def test_opposite_direction_conflict():
    """Opposite moving components fail with PATH_DIRECTION_CONFLICT."""
    fwd = resolve_paths(
        parse_path_declarations(
            [{"start": 1, "end": 4, "move": "end", "angles": [0.0]}],
            index_base=1,
            default_step=None,
        ),
        _linear(),
    ).rotors
    back = resolve_paths(
        parse_path_declarations(
            [{"start": 1, "end": 4, "move": "start", "angles": [0.0]}],
            index_base=1,
            default_step=None,
        ),
        _linear(),
    ).rotors
    with pytest.raises(PathResolutionError) as exc:
        canonicalize_rotors([*fwd, *back])
    assert exc.value.code == PATH_DIRECTION_CONFLICT


def test_sampling_conflict_on_grids():
    """Same oriented rotor with different grids is never unioned."""
    left = resolve_paths(
        parse_path_declarations(
            [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
            index_base=1,
            default_step=None,
        ),
        _linear(),
    ).rotors
    right = resolve_paths(
        parse_path_declarations(
            [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 90.0]}],
            index_base=1,
            default_step=None,
        ),
        _linear(),
    ).rotors
    with pytest.raises(PathResolutionError) as exc:
        canonicalize_rotors([*left, *right])
    assert exc.value.code == ROTOR_SAMPLING_CONFLICT


def test_reversed_traversal_asymmetric_conflict():
    """Reversed traversal flips the signed-angle meaning -> conflict."""
    fwd = resolve_paths(
        parse_path_declarations(
            [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
            index_base=1,
            default_step=None,
            source_prefix="paths",
        ),
        _linear(),
    ).rotors
    rev = resolve_paths(
        parse_path_declarations(
            [{"start": 4, "end": 1, "move": "start", "angles": [0.0, 120.0]}],
            index_base=1,
            default_step=None,
            source_prefix="paths",
        ),
        _linear(),
    ).rotors
    # Same moving sides, opposite traversal: signed meaning differs.
    with pytest.raises(PathResolutionError) as exc:
        canonicalize_rotors([fwd[0], rev[2]])
    assert exc.value.code == ROTOR_SAMPLING_CONFLICT


def test_declaration_order_policy():
    """First declaration wins position: paths first, then chains."""
    path_rotors = resolve_paths(
        parse_path_declarations(
            [{"start": 3, "end": 4, "move": "end", "angles": [0.0]}],
            index_base=1,
            default_step=None,
            source_prefix="paths",
        ),
        _linear(),
    ).rotors
    from confflow.science.confgen.torsion.paths import CanonicalRotor

    chain_rotor = CanonicalRotor(
        bond=(0, 1),
        ordered=(0, 1),
        moving_atom=1,
        moving=(2, 3),
        fixed=(0, 1),
        angles=(0.0,),
        sources=("chains[0]",),
    )
    merged = canonicalize_rotors([*path_rotors, chain_rotor])
    assert [r.bond for r in merged] == [(2, 3), (0, 1)]


# -- declaration validation -------------------------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        {"start": 0, "end": 2, "move": "end"},  # 1-based boundary
        {"start": -1, "end": 2, "move": "end"},
        {"start": True, "end": 2, "move": "end"},  # bool is not int
        {"start": 1.5, "end": 2, "move": "end"},
        {"start": 1, "end": 1, "move": "end"},  # same endpoints
        {"start": 1, "end": 2},  # move REQUIRED
        {"start": 1, "end": 2, "move": "middle"},
        {"start": 1, "end": 2, "move": "end", "bogus": 1},  # unknown keys
        {"start": 1, "end": 2, "move": "end", "angles": []},
        {"start": 1, "end": 2, "move": "end", "angles": [float("nan")]},
        {"start": 1, "end": 2, "move": "end", "angles": [float("inf")]},
        {"start": 1, "end": 2, "move": "end", "angles": [0.0, 360.0]},
        {"start": 1, "end": 2, "move": "end", "angles": [0.0], "step": 90},
        {"start": 1, "end": 2, "move": "end", "step": 0},
        {"start": 1, "end": 2, "move": "end", "step": 361},
        {"start": 1, "end": 2, "move": "end", "step": True},
    ],
)
def test_invalid_declarations_fail_closed(entry):
    """Structural defects fail closed (never guessed or coerced)."""
    with pytest.raises((ValueError, PathResolutionError)):
        parse_path_declarations([entry], index_base=1, default_step=120)


def test_bare_path_needs_default_or_explicit():
    """Typed scopes (no default) reject bare declarations; native allows."""
    with pytest.raises(ValueError, match="no sampling"):
        parse_path_declarations(
            [{"start": 1, "end": 2, "move": "end"}], index_base=1, default_step=None
        )
    parsed = parse_path_declarations(
        [{"start": 1, "end": 2, "move": "end"}], index_base=1, default_step=120
    )
    assert parsed[0].angles == (0.0, 120.0, 240.0)


def test_raw_cartesian_size_exact_before_geometry():
    """Raw size is exact arbitrary-precision math with no allocation."""
    parsed = parse_path_declarations(
        [{"start": 1, "end": 4, "move": "end", "angles": [float(a) for a in range(100)]}],
        index_base=1,
        default_step=None,
    )
    resolution = resolve_paths(parsed, _linear())
    assert resolution.raw_cartesian_size == 100**3 == 1000000


# -- legacy executor --------------------------------------------------------------


def test_legacy_equivalence_rotors_counts_and_geometries(tmp_path):
    """An equivalent nonoverlapping single chain and path agree exactly."""
    grid = "0,120;0,120;0,120"
    chain_native = {"chains": ["1-2-3-4"], "chain_angles": grid, "rotate_side": "right"}
    path_native = {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}]}
    chain_result = _legacy_result([_butane("seed-a")], chain_native, tmp_path)
    path_result = _legacy_result([_butane("seed-a")], path_native, tmp_path)
    assert chain_result.status is WorkItemStatus.COMPLETED
    assert path_result.status is WorkItemStatus.COMPLETED
    assert [m.ordinal for m in chain_result.structures] == [
        m.ordinal for m in path_result.structures
    ]
    assert len(path_result.structures) == 8
    for left, right in zip(chain_result.structures, path_result.structures):
        assert tuple(left.atoms) == tuple(right.atoms)
        assert np.allclose(np.asarray(left.coordinates), np.asarray(right.coordinates), atol=0.0)


def test_pure_legacy_overlapping_chains_preserved(tmp_path):
    """Pure-chains items keep legacy overlap behavior (no strict contract)."""
    native = {
        "chains": ["1-2-3-4", "1-2-3-4"],
        "chain_angles": ["0;0,120;0", "0;0,120;0"],
    }
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 4  # cumulative duplicate application


def test_mixed_paths_chains_dedup(tmp_path):
    """Mixed mode dedups identical declarations with merged provenance."""
    native = {
        "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        "chains": ["1-2-3-4"],
        "chain_angles": "0,120;0,120;0,120",
        "rotate_side": "right",
    }
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 8
    payload = _report_payload(tmp_path)
    sources = payload["path_resolution"]["rotors"][0]["sources"]
    assert "native.paths[0]" in sources and "chains[0]" in sources


def test_mixed_direction_conflict(tmp_path):
    """Mixed opposite moving sides fail with PATH_DIRECTION_CONFLICT."""
    native = {
        "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        "chains": ["1-2-3-4"],
        "chain_angles": "0,120;0,120;0,120",
        "rotate_side": "left",
    }
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    assert PATH_DIRECTION_CONFLICT in _failed_message(result)


def test_mixed_sampling_conflict(tmp_path):
    """Mixed incompatible grids fail with ROTOR_SAMPLING_CONFLICT."""
    native = {
        "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        "chains": ["1-2-3-4"],
        "chain_angles": "0,90;0,90;0,90",
        "rotate_side": "right",
    }
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    assert ROTOR_SAMPLING_CONFLICT in _failed_message(result)


def test_executor_ring_refusal_before_geometry(tmp_path, monkeypatch):
    """A refused task-space never reaches geometry generation."""
    import confflow.execution.confgen_executor as executor_module

    called = {"n": 0}

    def _spy(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("geometry must not run for refused task-spaces")

    monkeypatch.setattr(executor_module, "legacy_oriented_grid_geometries", _spy)
    record, _ = _ring_tail()
    native = {"paths": [{"start": 1, "end": 3, "move": "end", "angles": [0.0, 90.0]}]}
    result = _legacy_result([record], native, tmp_path)
    assert PATH_CROSSES_RING in _failed_message(result)
    assert called["n"] == 0


def test_pregeometry_limit_refusal(tmp_path, monkeypatch):
    """Oversize raw grids refuse before allocation (cap stays post-only)."""
    import confflow.execution.confgen_executor as executor_module

    called = {"n": 0}

    def _spy(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("geometry must not run past the pregeometry guard")

    monkeypatch.setattr(executor_module, "legacy_oriented_grid_geometries", _spy)
    native = {"paths": [{"start": 1, "end": 4, "move": "end", "step": 15}]}
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    message = _failed_message(result)
    assert "pre-geometry limit" in message and "13824" in message
    assert called["n"] == 0


def test_atom_ordering_preserved(tmp_path):
    """Output members keep the driving atom sequence exactly."""
    record, _ = _branched()
    native = {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 180.0]}]}
    result = _legacy_result([record], native, tmp_path)
    assert result.status is WorkItemStatus.COMPLETED
    for member in result.structures:
        assert tuple(member.atoms) == tuple(record.atoms)


def test_short_bond_warning_and_strict_mode(tmp_path):
    """Suspicious short bonds warn; strict mode turns the warning into error."""
    record = StructureRecord(
        id="short",
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (2.0, 0.0, 0.0), (3.5, 0.4, 0.0)),
        charge=0,
        multiplicity=1,
    )
    native = {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 180.0]}]}
    result = _legacy_result([record], native, tmp_path)
    assert result.status is WorkItemStatus.COMPLETED
    payload = _report_payload(tmp_path)
    warnings = payload["path_resolution"]["warnings"]
    assert any("2-3" in note for note in warnings)
    strict_native = dict(native, strict_path_bond_check=True)
    strict_result = _legacy_result([record], strict_native, tmp_path)
    assert PATH_SHORT_BOND in _failed_message(strict_result)


def test_pure_legacy_has_no_path_warnings(tmp_path):
    """Pure legacy behavior is unaltered (no new warnings for chains)."""
    native = {"chains": ["1-2-3-4"], "chain_angles": "0;0,120;0"}
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    assert result.status is WorkItemStatus.COMPLETED
    payload = _report_payload(tmp_path)
    assert "path_resolution" not in payload


def test_deferred_resolution_for_upstream_structures(tmp_path):
    """The same declarations resolve per driving geometry (upstream-aware)."""
    near, _ = _branched()
    far = StructureRecord(
        id="upstream-product",
        atoms=near.atoms,
        coordinates=(
            (0.0, 0.0, 0.0),
            (1.5, 0.0, 0.0),
            (10.0, 0.0, 0.0),  # stretched: bond 2-3 perception breaks
            (11.5, 0.0, 0.0),
            (1.5, 1.4, 0.0),
        ),
        charge=0,
        multiplicity=1,
        parent_ids=("seed-a",),
    )
    native = {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 180.0]}]}
    ok_result = _legacy_result([near], native, tmp_path)
    assert ok_result.status is WorkItemStatus.COMPLETED
    moved = _legacy_result([far], native, tmp_path)
    assert moved.status is WorkItemStatus.FAILED
    assert PATH_DISCONNECTED in _failed_message(moved)


def _report_payload(tmp_path) -> dict[str, Any]:
    """Load the legacy confgen report artifact from the attempt directory."""
    for root, _, files in os.walk(str(tmp_path)):
        if "confgen_report.json" in files:
            with open(os.path.join(root, "confgen_report.json"), encoding="utf-8") as fh:
                return json.load(fh)
    raise AssertionError("confgen_report.json not written")


def test_report_records_resolution_identity(tmp_path):
    """Report/provenance audits input identity, graph, rotors, and counts."""
    record, _ = _branched()
    native = {"paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 180.0]}]}
    result = _legacy_result([record], native, tmp_path)
    assert result.status is WorkItemStatus.COMPLETED
    payload = _report_payload(tmp_path)
    assert payload["driving_id"] == "branched"
    resolved = payload["path_resolution"]
    assert resolved["topology_digest"].startswith("sha256:")
    assert resolved["raw_cartesian_size"] == 8
    assert len(resolved["rotors"]) == 3
    first = resolved["rotors"][0]
    assert first["bond"] == [1, 2] and first["moving_atom"] == 2
    assert 5 in first["moving"] and first["sources"] == ["native.paths[0]"]


# -- typed v3 -------------------------------------------------------------------


def test_typed_paths_expand_to_torsion_axes():
    """build_context expands paths on the working topology (deferred)."""
    from confflow.science.confgen.model import build_context

    context = build_context(
        _butane("typed-seed"),
        {
            "schema_version": 3,
            "index_base": 1,
            "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
        },
    )
    torsions = context.resolved_spec["torsions"]
    assert [tuple(t["bond"]) for t in torsions] == [(0, 1), (1, 2), (2, 3)]
    assert [t["rotate_side"] for t in torsions] == ["right", "right", "right"]
    resolved = context.resolved_spec["paths_resolved"]
    assert len(resolved["rotors"]) == 3
    assert resolved["topology_digest"].startswith("sha256:")


def test_typed_paths_collision_with_explicit_torsion():
    """A path bond colliding with an explicit torsion fails closed."""
    from confflow.science.confgen.model import build_context

    with pytest.raises(ValueError, match=ROTOR_SAMPLING_CONFLICT):
        build_context(
            _butane("typed-seed"),
            {
                "schema_version": 3,
                "index_base": 1,
                "torsions": [
                    {
                        "id": "central",
                        "bond": [2, 3],
                        "model": "relative_rotation_grid",
                        "angles": [0.0, 120.0],
                    }
                ],
                "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
            },
        )


def test_typed_bare_path_rejected_at_normalization():
    """Typed bare paths fail closed (explicit sampling required)."""
    from confflow.science.confgen.planner import normalize_spec

    with pytest.raises(ValueError, match="no sampling"):
        normalize_spec(
            {
                "schema_version": 3,
                "index_base": 1,
                "paths": [{"start": 1, "end": 2, "move": "end"}],
            }
        )


def _hexane(struct_id: str) -> StructureRecord:
    """Linear C6 chain: interior bonds have measurable typed frames."""
    return StructureRecord(
        id=struct_id,
        atoms=("C",) * 6,
        coordinates=tuple((float(i) * 1.5, 0.4 * (i % 2), 0.0) for i in range(6)),
        charge=0,
        multiplicity=1,
    )


def test_typed_paths_executor_publishes(tmp_path):
    """The v3 executor runs path-expanded grids and audits the report."""
    item = _item("c1:g1", "c1", [_hexane("seed-a")])
    native = {
        "schema_version": 3,
        "index_base": 1,
        "paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0, 120.0]}],
    }
    sci = _sci(seed=None, native=FrozenDict(native))
    result = ConfgenExecutor().execute(item, _ctx(sci, str(tmp_path)))
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 8
    found = None
    for root, _, files in os.walk(str(tmp_path)):
        if "ensemble_report.json" in files:
            with open(os.path.join(root, "ensemble_report.json"), encoding="utf-8") as fh:
                found = json.load(fh)
    assert found is not None
    assert len(found["path_resolution"]["rotors"]) == 3


def test_typed_paths_ring_refusal(tmp_path):
    """Typed path ring crossings fail closed before geometry."""
    record, _ = _ring_tail()
    item = _item("c1:g1", "c1", [record])
    native = {
        "schema_version": 3,
        "index_base": 1,
        "paths": [{"start": 1, "end": 3, "move": "end", "angles": [0.0]}],
    }
    sci = _sci(seed=None, native=FrozenDict(native))
    result = ConfgenExecutor().execute(item, _ctx(sci, str(tmp_path)))
    assert result.status is WorkItemStatus.FAILED
    assert PATH_CROSSES_RING in " ".join(d.message for d in result.diagnostics)


def test_typed_terminal_path_frame_refused_not_invented(tmp_path):
    """Typed paths inherit measurability: terminal bonds fail, never fake."""
    item = _item("c1:g1", "c1", [_butane("seed-a")])
    native = {
        "schema_version": 3,
        "index_base": 1,
        "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0, 120.0]}],
    }
    sci = _sci(seed=None, native=FrozenDict(native))
    result = ConfgenExecutor().execute(item, _ctx(sci, str(tmp_path)))
    assert result.status is WorkItemStatus.FAILED
    assert "measurable dihedral frame" in " ".join(d.message for d in result.diagnostics)


# -- producer and validation ------------------------------------------------------


def test_workflow_with_paths_compiles_but_defers_resolution():
    """Submission-time compile is structural/advisory; geometry defers."""
    from confflow.workflow.v4.compiler import compile_workflow

    def _doc(move: str) -> dict[str, Any]:
        return {
            "schema": "confflow.workflow.v4",
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
            "steps": [
                {
                    "id": "s_gen",
                    "executor": "confgen",
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                    "confgen": {
                        "schema_version": 3,
                        "index_base": 1,
                        "paths": [{"start": 1, "end": 4, "move": move, "angles": [0.0, 90.0]}],
                    },
                }
            ],
        }

    left = compile_workflow(_doc("start"))
    right = compile_workflow(_doc("end"))
    assert left.ok and right.ok
    assert left.plan is not None and right.plan is not None
    # Resolution identity is semantic: distinct declarations, distinct digests.
    assert left.plan.definition_digest != right.plan.definition_digest


def test_confgen_schema_rejects_bad_paths():
    """Typed shape validation fails closed on malformed path declarations."""
    from pydantic import ValidationError

    from confflow.workflow.v4.confgen_schema import ConfgenModelV3

    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate({"schema_version": 3, "paths": [{"start": 1, "end": 2}]})
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(
            {
                "schema_version": 3,
                "paths": [{"start": True, "end": 2, "move": "end", "step": 90}],
            }
        )
    model = ConfgenModelV3.model_validate(
        {
            "schema_version": 3,
            "paths": [{"start": 1, "end": 2, "move": "end", "step": 90}],
            "strict_path_bond_check": True,
        }
    )
    assert model.paths[0].step == 90
    assert model.strict_path_bond_check is True


def test_freeze_still_rejected_for_paths():
    """Freeze stays fail-closed with paths declared (no new semantics)."""
    from confflow.workflow.v4.compiler import compile_workflow

    doc = {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "s_gen",
                "executor": "confgen",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "confgen": {
                    "schema_version": 3,
                    "index_base": 1,
                    "paths": [{"start": 1, "end": 4, "move": "end", "step": 120}],
                    "overrides": {"freeze": [1]},
                },
            }
        ],
    }
    compiled = compile_workflow(doc)
    assert not compiled.ok
    assert any("freeze" in d.message for d in compiled.diagnostics)


def test_producer_contract_and_manifest_cover_paths():
    """Contract confgen section and editor manifest publish path fields."""
    import confflow
    from confflow.producer.contract import build_configuration_contract_v4
    from confflow.producer.manifest import build_editor_manifest_v4

    envelope = build_configuration_contract_v4(producer_version=confflow.__version__)
    assert "path" in envelope["confgen"]["description"].lower()
    manifest = build_editor_manifest_v4()
    ids = {field["field_id"] for field in manifest["fields"]}
    assert "confgen.v3.paths" in ids
    assert "confgen.v3.strict_path_bond_check" in ids


def test_strict_native_flag_type_rejected(tmp_path):
    """strict_path_bond_check=1 (int) is rejected: no bool/int coercion."""
    native = {
        "paths": [{"start": 1, "end": 4, "move": "end", "angles": [0.0]}],
        "strict_path_bond_check": 1,
    }
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    assert "boolean" in _failed_message(result)


def test_unknown_native_path_key_rejected(tmp_path):
    """Unknown keys inside path entries fail closed at the executor."""
    native = {"paths": [{"start": 1, "end": 4, "move": "end", "waypoint": 2}]}
    result = _legacy_result([_butane("seed-a")], native, tmp_path)
    assert "unknown keys" in _failed_message(result)
