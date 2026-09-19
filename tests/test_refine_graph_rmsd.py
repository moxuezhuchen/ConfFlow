#!/usr/bin/env python3

"""Regression tests for graph-constrained RMSD dedup and correct proper Kabsch.

These tests encode the target contract:

* ``fast_rmsd`` must be a proper (reflection-free) Kabsch RMSD.
* Topology grouping must use exact element/edge-preserving graph mappings.
* Geometry search must only use legal graph mappings, with an explicit
  ``unresolved`` outcome when the search budget is exhausted.
* Every removed conformer must carry a direct representative.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from confflow.blocks.refine.processor import (
    RefineOptions,
    _compute_dedup_counts,
    process_xyz,
    read_xyz_file,
)
from confflow.blocks.refine.rmsd_engine import (
    compare_frames,
    fast_rmsd,
    get_topology_hash_worker,
    process_topology_group,
)

SEED = 20260913


def _reference_kabsch_rmsd(coords1, coords2) -> float:
    """Independent proper-Kabsch reference (plan section 3.3)."""
    x = np.asarray(coords1, dtype=np.float64)
    y = np.asarray(coords2, dtype=np.float64)
    xc = x - x.mean(axis=0)
    yc = y - y.mean(axis=0)
    h = yc.T @ x
    u, _s, vt = np.linalg.svd(h)
    d = np.eye(3)
    d[-1, -1] = 1.0 if np.linalg.det(u @ vt) >= 0.0 else -1.0
    r = u @ d @ vt
    aligned = yc @ r
    return float(np.sqrt(np.mean(np.sum((xc - aligned) ** 2, axis=1))))


def _rigid_copy(coords, rng):
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(q) < 0:
        q[:, -1] *= -1.0
    return coords @ q + rng.normal(size=3) * 5.0


# ---------------------------------------------------------------------------
# Phase 1: proper Kabsch
# ---------------------------------------------------------------------------


def test_fast_rmsd_rotation_translation_roundtrip():
    x = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 3.0]])
    t = 0.7
    q = np.array(
        [
            [np.cos(t), -np.sin(t), 0.0],
            [np.sin(t), np.cos(t), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    y = x @ q + np.array([5.0, 6.0, 7.0])
    assert fast_rmsd(x, y) == pytest.approx(0.0, abs=1e-10)


def test_fast_rmsd_matches_independent_reference_on_rotations():
    rng = np.random.default_rng(SEED)
    base = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.1, -0.2],
            [0.3, 2.0, 0.4],
            [-0.6, 0.2, 3.0],
            [1.2, -1.4, 0.7],
        ]
    )
    for _ in range(20):
        y = _rigid_copy(base, rng)
        assert fast_rmsd(base, y) == pytest.approx(0.0, abs=1e-9)
        assert fast_rmsd(base, y) == pytest.approx(_reference_kabsch_rmsd(base, y), abs=1e-9)


def test_fast_rmsd_refuses_reflection():
    chiral = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.3, 0.2, 0.1],
        ]
    )
    mirrored = chiral.copy()
    mirrored[:, 2] *= -1.0
    value = fast_rmsd(chiral, mirrored)
    assert value == pytest.approx(_reference_kabsch_rmsd(chiral, mirrored), abs=1e-9)
    assert value > 0.3

    # Non-chiral control: a planar set mirror-folds onto itself.
    planar = np.array([[0.0, 0.0, 0.0], [1.0, 0.2, 0.0], [0.5, 1.0, 0.0]])
    planar_mirror = planar.copy()
    planar_mirror[:, 2] *= -1.0
    assert fast_rmsd(planar, planar_mirror) == pytest.approx(0.0, abs=1e-10)


def test_fast_rmsd_degenerate_inputs():
    assert fast_rmsd(np.empty((0, 3)), np.empty((0, 3))) == 999.9
    assert fast_rmsd(np.zeros((3, 3)), np.zeros((4, 3))) == 999.9

    single = np.array([[1.0, 2.0, 3.0]])
    assert fast_rmsd(single, single + 4.0) == pytest.approx(0.0, abs=1e-12)

    line = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    line_mirror = line.copy()
    line_mirror[:, 1:] *= -1.0
    assert math.isfinite(fast_rmsd(line, line_mirror))
    assert fast_rmsd(line, line_mirror) == pytest.approx(
        _reference_kabsch_rmsd(line, line_mirror), abs=1e-10
    )

    flat = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]])
    flat_mirror = flat.copy()
    flat_mirror[:, 2] *= -1.0
    assert fast_rmsd(flat, flat_mirror) == pytest.approx(0.0, abs=1e-10)

    repeated = np.zeros((4, 3))
    assert fast_rmsd(repeated, repeated + np.array([0.0, 0.0, 1.0])) == pytest.approx(
        0.0, abs=1e-10
    )


# ---------------------------------------------------------------------------
# Phase 1: direct dedup / report contract
# ---------------------------------------------------------------------------


def _triangle(scale: float) -> np.ndarray:
    radius = (1.0 / math.sqrt(3.0)) * scale
    angles = np.deg2rad([90.0, 210.0, 330.0])
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(3)])


def _c_frames(scales_energies):
    return [
        {
            "original_index": idx,
            "energy": energy,
            "atoms": ["C", "C", "C"],
            "coords": _triangle(scale),
        }
        for idx, (scale, energy) in enumerate(scales_energies)
    ]


def test_process_topology_group_reports_direct_representative_for_batch_duplicate():
    # A(1.0) - B(1.2) and B(1.2) - C(1.3) are below cutoff, A - C is not:
    # C must be reported as a duplicate of B directly (no transitive A).
    frames = _c_frames([(1.0, -3.0), (1.2, -2.0), (1.3, -1.0)])
    unique, report = process_topology_group([dict(f) for f in frames], 0.1, False, 1, 0.05)

    assert [frame["original_index"] for frame in unique] == [0, 1]
    by_id = {entry["Input_Frame_ID"]: entry for entry in report}
    assert by_id[0]["Status"] == "Kept"
    assert by_id[1]["Status"] == "Kept"
    assert by_id[2]["Status"] == "Removed (Duplicate)"
    assert by_id[2]["Duplicate_Of_Input_ID"] == 1
    assert by_id[2]["Witness_RMSD"] < 0.1

    _compute_dedup_counts(unique, frames, report)
    counts = {frame["original_index"]: frame["count"] for frame in unique}
    assert counts == {0: 1, 1: 2}


def test_process_topology_group_identity_trap_searches_other_legal_mappings():
    path = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [2.0, 1.4, 0.0],
            [1.0, 2.5, 0.2],
            [1.0, 3.0, 1.6],
        ]
    )
    reversed_path = path[::-1].copy()
    frames = [
        {
            "original_index": 0,
            "energy": -2.0,
            "atoms": ["C"] * 5,
            "coords": path,
        },
        {
            "original_index": 1,
            "energy": -1.0,
            "atoms": ["C"] * 5,
            "coords": reversed_path,
        },
    ]
    # identity mapping is legal but gives ~0.45 A; the reversed automorphism is exact.
    unique, report = process_topology_group(frames, 0.25, False, 1, 0.05)
    assert [f["original_index"] for f in unique] == [0]
    removed = report[1]
    assert removed["Status"] == "Removed (Duplicate)"
    assert removed["Duplicate_Of_Input_ID"] == 0
    assert removed["Witness_RMSD"] < 0.25
    assert removed["Mapping"] != "identity"


def test_process_topology_group_does_not_mutate_input_list():
    frames = _c_frames([(1.0, -3.0), (1.2, -2.0), (1.3, -1.0)])
    snapshot = [frame["original_index"] for frame in frames]
    process_topology_group(frames, 0.1, False, 1, 0.05)
    assert [frame["original_index"] for frame in frames] == snapshot


# ---------------------------------------------------------------------------
# Phase 2/3: exact graph layer
# ---------------------------------------------------------------------------


def _prism_graph():
    from confflow.blocks.refine.topology import graph_from_adjacency

    adjacency = [
        (1, 2, 3),
        (0, 2, 4),
        (0, 1, 5),
        (0, 4, 5),
        (1, 3, 5),
        (2, 3, 4),
    ]
    return graph_from_adjacency(["C"] * 6, adjacency)


def _k33_graph():
    from confflow.blocks.refine.topology import graph_from_adjacency

    adjacency = [
        (3, 4, 5),
        (3, 4, 5),
        (3, 4, 5),
        (0, 1, 2),
        (0, 1, 2),
        (0, 1, 2),
    ]
    return graph_from_adjacency(["C"] * 6, adjacency)


def test_prism_and_k33_share_cheap_invariants_but_are_not_isomorphic():
    from confflow.blocks.refine.topology import find_isomorphism, graphs_may_be_isomorphic

    prism = _prism_graph()
    k33 = _k33_graph()
    assert sorted(prism.degree_sequence) == sorted(k33.degree_sequence)
    assert graphs_may_be_isomorphic(prism, k33)  # 3-regular WL cannot separate them

    result = find_isomorphism(prism, k33, node_budget=100_000)
    assert result.status == "non_isomorphic"
    assert result.mapping is None
    assert result.stats.complete is True

    same = find_isomorphism(prism, k33, node_budget=100_000)  # deterministic repeat
    assert same.status == result.status


def test_group_frames_by_topology_separates_prism_and_k33():
    from confflow.blocks.refine.topology import group_frames_by_topology

    prism = _prism_graph()
    k33 = _k33_graph()
    frames = []
    for idx, graph in enumerate([prism, prism, k33, k33]):
        frames.append(
            {
                "original_index": idx,
                "energy": -1.0 - idx * 0.01,
                "atoms": ["C"] * 6,
                "coords": np.zeros((6, 3)),
                "graph": graph,
            }
        )
    clusters = group_frames_by_topology(frames, node_budget=100_000)
    sizes = sorted(len(cluster.frames) for cluster in clusters if cluster.status == "confirmed")
    assert sizes == [2, 2]
    assigned = {
        frame["original_index"]: cluster.cluster_id
        for cluster in clusters
        for frame in cluster.frames
    }
    assert assigned[0] == assigned[1]
    assert assigned[2] == assigned[3]
    assert assigned[0] != assigned[2]


def test_compare_frames_never_matches_across_topologies():
    from confflow.blocks.refine.rmsd_engine import compare_frames

    prism = _prism_graph()
    k33 = _k33_graph()
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 0.0, 1.0],
        ]
    )
    candidate = {
        "original_index": 1,
        "energy": -1.0,
        "atoms": ["C"] * 6,
        "coords": coords,
        "graph": k33,
    }
    representative = {
        "original_index": 0,
        "energy": -1.0,
        "atoms": ["C"] * 6,
        "coords": coords,
        "graph": prism,
    }
    verdict = compare_frames(candidate, representative, threshold=10.0, heavy_only=False)
    assert verdict.status == "different_topology"
    assert verdict.witness_rmsd is None


def test_compare_frames_budget_exhaustion_is_unresolved_not_duplicate_or_distinct():
    from confflow.blocks.refine.rmsd_engine import compare_frames

    path = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [2.0, 1.4, 0.0],
            [1.0, 2.5, 0.2],
            [1.0, 3.0, 1.6],
        ]
    )
    candidate = {
        "original_index": 1,
        "energy": -1.0,
        "atoms": ["C"] * 5,
        "coords": path[::-1].copy(),
    }
    representative = {
        "original_index": 0,
        "energy": -1.5,
        "atoms": ["C"] * 5,
        "coords": path,
    }
    verdict = compare_frames(
        candidate,
        representative,
        threshold=0.25,
        heavy_only=False,
        node_budget=3,
    )
    assert verdict.status == "unresolved"
    assert verdict.search_complete is False
    assert verdict.witness_rmsd is None


def test_compare_frames_threshold_is_strictly_less_than():
    from confflow.blocks.refine.rmsd_engine import compare_frames, kabsch_rmsd

    left = {
        "original_index": 0,
        "energy": -2.0,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.0),
    }
    right = {
        "original_index": 1,
        "energy": -1.0,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.2),
    }
    exact = kabsch_rmsd(_triangle(1.0), _triangle(1.2))
    assert exact > 0.0
    equal_cutoff = compare_frames(left, right, threshold=exact, heavy_only=False)
    assert equal_cutoff.status == "distinct"
    above_cutoff = compare_frames(left, right, threshold=exact * (1.0 + 1e-9), heavy_only=False)
    assert above_cutoff.status == "duplicate"
    assert above_cutoff.witness_rmsd < above_cutoff.cutoff


# ---------------------------------------------------------------------------
# Phase 4: pipeline integration (topology collision, reports, atomic output)
# ---------------------------------------------------------------------------


def _xyz_text(frames, atom_symbols):
    blocks = []
    for comment, coords in frames:
        lines = [str(len(coords)), comment]
        for symbol, coord in zip(atom_symbols, coords):
            lines.append(f"{symbol:<4s} {coord[0]:12.8f} {coord[1]:12.8f} {coord[2]:12.8f}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + "\n"


def _planar_hexagon(radius: float = 1.5) -> np.ndarray:
    angles = np.deg2rad(np.arange(6) * 60.0)
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(6)])


def _chair_hexagon(radius: float = 1.5, z: float = 0.4) -> np.ndarray:
    angles = np.deg2rad(np.arange(6) * 60.0)
    zs = np.array([z, -z, z, -z, z, -z])
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles), zs])


def _two_triangles(side: float = 1.5, offset: float = 8.0) -> np.ndarray:
    radius = side / math.sqrt(3.0)
    angles = np.deg2rad([90.0, 210.0, 330.0])
    first = np.column_stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(3)])
    second = first + np.array([offset, 0.0, 0.0])
    return np.vstack([first, second])


def _collision_case_input(tmp_path: Path) -> Path:
    coordinates = [_planar_hexagon(), _chair_hexagon(), _two_triangles()]
    comments = ["E=-2.0 | CID=N1", "E=-1.9 | CID=N2", "E=-3.0 | CID=ANOM"]
    text = _xyz_text(list(zip(comments, coordinates)), ["C"] * 6)
    path = tmp_path / "collision.xyz"
    path.write_text(text, encoding="utf-8")
    return path


def test_topology_collision_hash_cannot_merge_non_isomorphic_frames(tmp_path):
    path = _collision_case_input(tmp_path)
    frames = read_xyz_file(str(path))
    hashes = {get_topology_hash_worker((f["atoms"], f["coords"])) for f in frames}
    assert len(hashes) == 1  # legacy one-layer fingerprint collides

    out = tmp_path / "out.xyz"
    result = process_xyz(RefineOptions(input_file=str(path), output=str(out), threshold=0.1))
    assert result.produced_output is True
    written = read_xyz_file(str(out))
    cids = {frame["extra_data"].get("CID") for frame in written}
    assert cids == {"N1", "N2"}  # anomaly topology is the minority and is dropped
    for frame in written:
        assert frame["extra_data"].get("CID") != "ANOM"


def test_keep_all_topos_isolates_confirmed_topology_groups(tmp_path):
    path = _collision_case_input(tmp_path)
    out = tmp_path / "out_all.xyz"
    report_path = tmp_path / "out_all.xyz.report.json"
    result = process_xyz(
        RefineOptions(
            input_file=str(path),
            output=str(out),
            threshold=0.1,
            keep_all_topos=True,
        )
    )
    assert result.produced_output is True
    written = read_xyz_file(str(out))
    assert {frame["extra_data"].get("CID") for frame in written} == {"N1", "N2", "ANOM"}
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    groups = {entry["topology_group"] for entry in payload["frames"]}
    assert len(groups) == 2
    anom_groups = {
        entry["topology_group"] for entry in payload["frames"] if entry.get("cid") == "ANOM"
    }
    normal_groups = {
        entry["topology_group"] for entry in payload["frames"] if entry.get("cid") in {"N1", "N2"}
    }
    assert anom_groups.isdisjoint(normal_groups)


def test_refine_report_links_input_and_output_digests(tmp_path):
    path = _collision_case_input(tmp_path)
    out = tmp_path / "out.xyz"
    report_path = tmp_path / "out.xyz.report.json"
    process_xyz(RefineOptions(input_file=str(path), output=str(out), threshold=0.1))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["input_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert payload["output_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert payload["input_frame_count"] == 3
    assert payload["output_frame_count"] == 2
    for entry in payload["frames"]:
        for key in ("frame_id", "status", "reason", "topology_group"):
            assert key in entry
    removed = [
        entry for entry in payload["frames"] if entry.get("removed_by") == "minority_topology"
    ]
    assert [entry.get("cid") for entry in removed] == ["ANOM"]


def test_process_xyz_write_failure_preserves_old_output(tmp_path, monkeypatch):
    import confflow.blocks.refine.processor as processor

    path = _collision_case_input(tmp_path)
    out = tmp_path / "out.xyz"
    out.write_text("old output\n", encoding="utf-8")

    def exploding_write(*args, **kwargs):
        raise OSError("disk full simulation")

    monkeypatch.setattr(processor, "_write_refine_output", exploding_write)
    result = process_xyz(RefineOptions(input_file=str(path), output=str(out), threshold=0.1))
    assert result.produced_output is False
    assert out.read_text(encoding="utf-8") == "old output\n"
    assert not (tmp_path / "out.xyz.report.json").exists()


def _chiral_pair():
    backbone = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0]])
    hydrogens = np.array([[0.9, 0.0, 0.3], [-0.4, 0.8, 0.3]])
    frame_a = {
        "original_index": 0,
        "energy": -2.0,
        "atoms": ["C", "C", "H", "H"],
        "coords": np.vstack([backbone, hydrogens]),
    }
    mirrored = np.vstack([backbone, hydrogens * np.array([1.0, 1.0, -1.0])])
    frame_b = {
        "original_index": 1,
        "energy": -1.9,
        "atoms": ["C", "C", "H", "H"],
        "coords": mirrored,
    }
    return frame_a, frame_b


def test_noH_ignores_h_geometry_but_keeps_h_topology_constraints():
    frame_a, frame_b = _chiral_pair()
    # Heavy atoms are identical; H geometry is ignored, so this is a duplicate.
    unique, report = process_topology_group([frame_a, frame_b], 0.2, True, 1, 0.0)
    assert [frame["original_index"] for frame in unique] == [0]
    assert report[1]["Status"] == "Removed (Duplicate)"

    # With hydrogens in the comparison domain the mirrored pair is distinct.
    frame_a, frame_b = _chiral_pair()
    unique_all, report_all = process_topology_group([frame_a, frame_b], 0.2, False, 1, 0.0)
    assert [frame["original_index"] for frame in unique_all] == [0, 1]
    assert report_all[1]["Status"] == "Kept"


def test_energy_relaxation_reports_effective_cutoff():
    # right has the lower energy, so it becomes the representative; the energy
    # difference is inside the tolerance, relaxing 0.1 to the effective 0.15.
    right = {
        "original_index": 1,
        "energy": -2.00005,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.2),
    }
    left = {
        "original_index": 0,
        "energy": -2.0,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.0),
    }
    unique, report = process_topology_group([left, right], 0.1, False, 1, 0.05)
    assert [frame["original_index"] for frame in unique] == [1]
    removed = next(entry for entry in report if entry["Input_Frame_ID"] == 0)
    assert removed["Status"] == "Removed (Duplicate)"
    assert removed["Duplicate_Of_Input_ID"] == 1
    assert removed["Cutoff"] == pytest.approx(0.15)
    assert removed["Witness_RMSD"] < 0.15

    # Without relaxation the same pair is distinct.
    unique_strict, _ = process_topology_group([left, right], 0.1, False, 1, 0.0)
    assert sorted(frame["original_index"] for frame in unique_strict) == [0, 1]


def test_energy_tolerance_zero_disables_relaxation():
    # Identical energies with tolerance 0 must use the nominal cutoff, even
    # though the old <= comparison would have relaxed them.
    right = {
        "original_index": 1,
        "energy": -2.0,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.2),
    }
    left = {
        "original_index": 0,
        "energy": -2.0,
        "atoms": ["C", "C", "C"],
        "coords": _triangle(1.0),
    }
    unique, report = process_topology_group([left, right], 0.1, False, 1, 0.0)

    assert sorted(frame["original_index"] for frame in unique) == [0, 1]
    kept = report[1]
    assert kept["Status"] == "Kept"
    assert kept["Cutoff"] == pytest.approx(0.1)


def test_unresolved_topology_skips_majority_filter_and_retains_all(tmp_path):
    # Triangle with a pendant, then the same graph under a non-automorphic
    # relabeling: isomorphic, but identity numbering is not legal.
    geometry = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [0.75, 1.299, 0.0],
            [3.0, 0.0, 0.0],
        ]
    )
    relebeled = geometry[[3, 1, 2, 0]]
    text = _xyz_text(
        [
            ("E=-2.0 | CID=P0", geometry),
            ("E=-1.0 | CID=P1", relebeled),
        ],
        ["C"] * 4,
    )
    source = tmp_path / "renumbered.xyz"
    source.write_text(text, encoding="utf-8")
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            threshold=0.25,
            max_mapping_nodes=1,
        )
    )
    assert result.produced_output is True
    written = read_xyz_file(str(out))
    assert sorted(frame["extra_data"].get("CID") for frame in written) == ["P0", "P1"]
    payload = json.loads((tmp_path / "out.xyz.report.json").read_text(encoding="utf-8"))
    assert payload["majority_filter"] == "skipped_unresolved_or_invalid_topology"


def test_max_conformers_reports_truncated_representative(tmp_path):
    text = _xyz_text(
        [
            ("E=-3.0 | CID=F0", np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0]])),
            ("E=-2.0 | CID=F1", np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0]])),
            ("E=-1.0 | CID=F2", np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [0.0, 1.5, 0.0]])),
        ],
        ["C", "C", "C"],
    )
    source = tmp_path / "frames.xyz"
    source.write_text(text, encoding="utf-8")
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            threshold=0.25,
            keep_all_topos=True,
            max_conformers=2,
        )
    )
    assert result.produced_output is True
    assert result.kept_count == 2
    payload = json.loads((tmp_path / "out.xyz.report.json").read_text(encoding="utf-8"))
    by_cid = {entry["cid"]: entry for entry in payload["frames"]}
    assert by_cid["F0"]["final_output"] is True
    assert by_cid["F1"]["final_output"] is True
    assert by_cid["F2"]["final_output"] is False
    assert by_cid["F2"]["removed_by"] == "max_conformers"


def test_non_finite_frame_is_isolated_reported_and_not_written(tmp_path):
    source = tmp_path / "nan.xyz"
    source.write_text(
        "2\nE=-1.0 | CID=GOOD\nC 0 0 0\nH 0 0 1.0\n" "2\nE=-2.0 | CID=NAN\nC nan 0 0\nH 0 0 1.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            threshold=0.25,
            keep_all_topos=True,
        )
    )
    assert result.produced_output is True
    assert [frame["extra_data"].get("CID") for frame in read_xyz_file(str(out))] == ["GOOD"]
    payload = json.loads((tmp_path / "out.xyz.report.json").read_text(encoding="utf-8"))
    by_cid = {entry["cid"]: entry for entry in payload["frames"]}
    assert by_cid["NAN"]["topology_status"] == "invalid"
    assert by_cid["NAN"]["status"] == "removed"
    assert by_cid["NAN"]["removed_by"] == "invalid_input"
    assert by_cid["GOOD"]["status"] == "kept"


def test_all_hydrogen_noH_keeps_frames_and_reports_empty_domain():
    frames = [
        {
            "original_index": 0,
            "energy": -1.0,
            "atoms": ["H", "H"],
            "coords": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),
        },
        {
            "original_index": 1,
            "energy": -0.5,
            "atoms": ["H", "H"],
            "coords": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]),
        },
    ]
    unique, report = process_topology_group(frames, 0.25, True, 1, 0.05)
    assert [frame["original_index"] for frame in unique] == [0, 1]
    assert all(entry["Status"].startswith("Kept") for entry in report)
    assert any("comparison_domain" in str(entry.get("Reason", "")) for entry in report)


# ---------------------------------------------------------------------------
# F1: noH with atom renumbering must use the candidate comparison domain
# ---------------------------------------------------------------------------


def _renumbered_co_frames(o_distance: float):
    """Build reference C-O-H order versus renumbered H-C-O candidate."""
    reference = {
        "original_index": 0,
        "energy": -1.0,
        "atoms": ["C", "O", "H"],
        "coords": np.array([[0.0, 0.0, 0.0], [1.4, 0.0, 0.0], [2.3, 0.0, 0.0]]),
    }
    candidate = {
        "original_index": 1,
        "energy": 0.0,
        "atoms": ["H", "C", "O"],
        "coords": np.array([[2.3, 0.0, 0.0], [0.0, 0.0, 0.0], [o_distance, 0.0, 0.0]]),
    }
    return reference, candidate


def _write_renumbered_co_pair(path: Path, o_distance: float) -> None:
    path.write_text(
        "3\nE=-1.0 | CID=R\nC 0 0 0\nO 1.4 0 0\nH 2.3 0 0\n"
        f"3\nE=0.0 | CID=C\nH 2.3 0 0\nC 0 0 0\nO {o_distance} 0 0\n",
        encoding="utf-8",
    )


def test_f1_noH_renumbered_distinct_heavy_geometry_is_not_deleted():
    reference, candidate = _renumbered_co_frames(1.6)
    # Correct heavy-atom distance is 0.1 A, above the 0.05 cutoff.
    correct = _reference_kabsch_rmsd(candidate["coords"][[1, 2]], reference["coords"][[0, 1]])
    assert correct == pytest.approx(0.1, abs=1e-9)

    for first, second in ((candidate, reference), (reference, candidate)):
        verdict = compare_frames(
            first, second, threshold=0.05, heavy_only=True, energy_tolerance=0.0
        )
        assert verdict.status == "distinct", verdict
        assert verdict.witness_rmsd is None


def test_f1_noH_renumbered_true_duplicate_is_deleted_both_directions():
    reference, candidate = _renumbered_co_frames(1.4)
    verdict_forward = compare_frames(
        candidate, reference, threshold=0.05, heavy_only=True, energy_tolerance=0.0
    )
    verdict_backward = compare_frames(
        reference, candidate, threshold=0.05, heavy_only=True, energy_tolerance=0.0
    )
    for verdict in (verdict_forward, verdict_backward):
        assert verdict.status == "duplicate", verdict
        assert verdict.witness_rmsd == pytest.approx(0.0, abs=1e-9)
        assert verdict.mapping is not None

    assert verdict_forward.mapping == (2, 0, 1)
    assert verdict_backward.mapping == (1, 2, 0)


def test_f1_process_xyz_noH_renumbering_does_not_delete_distinct_frame(tmp_path):
    source = tmp_path / "renumbered.xyz"
    _write_renumbered_co_pair(source, 1.6)
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            threshold=0.05,
            noH=True,
            energy_tolerance=0.0,
            keep_all_topos=True,
            workers=1,
        )
    )
    assert result.produced_output is True
    assert result.kept_count == 2
    assert sorted(frame["extra_data"].get("CID") for frame in read_xyz_file(str(out))) == [
        "C",
        "R",
    ]
    payload = json.loads((tmp_path / "out.xyz.report.json").read_text(encoding="utf-8"))
    assert all(entry["removed_by"] != "duplicate" for entry in payload["frames"])


def test_f1_process_xyz_noH_renumbering_deletes_true_duplicate_with_replayable_mapping(
    tmp_path,
):
    source = tmp_path / "renumbered.xyz"
    _write_renumbered_co_pair(source, 1.4)
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            threshold=0.05,
            noH=True,
            energy_tolerance=0.0,
            keep_all_topos=True,
            workers=1,
        )
    )
    assert result.produced_output is True
    assert [frame["extra_data"].get("CID") for frame in read_xyz_file(str(out))] == ["R"]
    payload = json.loads((tmp_path / "out.xyz.report.json").read_text(encoding="utf-8"))
    by_cid = {entry["cid"]: entry for entry in payload["frames"]}
    removed = by_cid["C"]
    assert removed["removed_by"] == "duplicate"
    assert removed["representative_frame_id"] == by_cid["R"]["frame_id"]
    mapping = removed["mapping_permutation"]
    assert mapping == [2, 0, 1]

    # The stored mapping must let an independent reviewer recompute the witness.
    frames = read_xyz_file(str(source))
    candidate = next(frame for frame in frames if frame["extra_data"].get("CID") == "C")
    representative = next(frame for frame in frames if frame["extra_data"].get("CID") == "R")
    heavy_candidate = [i for i, atom in enumerate(candidate["atoms"]) if atom != "H"]
    recomputed = _reference_kabsch_rmsd(
        candidate["coords"][heavy_candidate],
        representative["coords"][[mapping[i] for i in heavy_candidate]],
    )
    assert recomputed == pytest.approx(removed["witness_rmsd"], abs=1e-12)


def test_f1_noH_random_renumbering_duplicate_and_non_duplicate():
    base_atoms = ["C", "C", "O", "H"]
    base = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [2.7, 0.0, 0.0], [2.7, 0.97, 0.0]])
    rng = np.random.default_rng(SEED)
    representative = {
        "original_index": 0,
        "energy": -1.0,
        "atoms": base_atoms,
        "coords": base,
    }
    for _ in range(6):
        permutation = rng.permutation(4)
        atoms = [base_atoms[j] for j in permutation]
        coords = base[permutation].copy()
        duplicate = {
            "original_index": 1,
            "energy": 0.0,
            "atoms": atoms,
            "coords": coords,
        }
        shifted = coords.copy()
        shifted[atoms.index("O")] += np.array([0.0, 0.0, 0.25])
        non_duplicate = {
            "original_index": 2,
            "energy": 0.0,
            "atoms": atoms,
            "coords": shifted,
        }

        duplicate_verdict = compare_frames(
            duplicate, representative, threshold=0.05, heavy_only=True, energy_tolerance=0.0
        )
        assert duplicate_verdict.status == "duplicate", (permutation, duplicate_verdict)
        assert duplicate_verdict.witness_rmsd == pytest.approx(0.0, abs=1e-9)

        non_duplicate_verdict = compare_frames(
            non_duplicate, representative, threshold=0.05, heavy_only=True, energy_tolerance=0.0
        )
        assert non_duplicate_verdict.status != "duplicate", (permutation, non_duplicate_verdict)


# ---------------------------------------------------------------------------
# F2: report path must not alias the input or output file
# ---------------------------------------------------------------------------


def _single_frame_source(tmp_path: Path, name: str = "in.xyz") -> Path:
    source = tmp_path / name
    source.write_text("1\nE=-1.0 | CID=ONLY\nH 0 0 0\n", encoding="utf-8")
    return source


def test_f2_report_path_colliding_with_input_is_rejected(tmp_path):
    source = _single_frame_source(tmp_path)
    original = source.read_bytes()
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            report=str(source),
            workers=1,
        )
    )
    assert result.produced_output is False
    assert result.reason == "report_path_conflict"
    assert source.read_bytes() == original
    assert not out.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_f2_report_path_colliding_with_output_is_rejected(tmp_path):
    source = _single_frame_source(tmp_path)
    original = source.read_bytes()
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            report=str(out),
            workers=1,
        )
    )
    assert result.produced_output is False
    assert result.reason == "report_path_conflict"
    assert source.read_bytes() == original
    assert not out.exists()


def test_f2_relative_report_path_resolves_to_absolute_input(tmp_path, monkeypatch):
    source = _single_frame_source(tmp_path)
    original = source.read_bytes()
    monkeypatch.chdir(tmp_path)
    result = process_xyz(
        RefineOptions(
            input_file="in.xyz",
            output="out.xyz",
            report=str(source),
            workers=1,
        )
    )
    assert result.produced_output is False
    assert result.reason == "report_path_conflict"
    assert source.read_bytes() == original


def test_f2_symlink_report_alias_is_rejected(tmp_path):
    source = _single_frame_source(tmp_path)
    original = source.read_bytes()
    alias = tmp_path / "alias.xyz"
    alias.symlink_to(source)
    out = tmp_path / "out.xyz"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            report=str(alias),
            workers=1,
        )
    )
    assert result.produced_output is False
    assert result.reason == "report_path_conflict"
    assert source.read_bytes() == original
    assert alias.is_symlink()
    assert not out.exists()


def test_f2_distinct_report_path_still_written(tmp_path):
    source = _collision_case_input(tmp_path)
    out = tmp_path / "out.xyz"
    report = tmp_path / "custom.report.json"
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            report=str(report),
            threshold=0.1,
            workers=1,
        )
    )
    assert result.produced_output is True
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["input_file"] == str(source)


def test_f2_in_place_refine_input_equals_output_still_supported(tmp_path):
    source = _single_frame_source(tmp_path)
    result = process_xyz(RefineOptions(input_file=str(source), output=str(source), workers=1))
    assert result.produced_output is True
    frames = read_xyz_file(str(source))
    assert len(frames) == 1
    assert frames[0]["extra_data"].get("CID") == "ONLY"


def test_f2_final_output_replace_failure_restores_old_output_and_report(tmp_path, monkeypatch):
    import confflow.blocks.refine.processor as processor

    source = _collision_case_input(tmp_path)
    out = tmp_path / "out.xyz"
    out.write_text("old output\n", encoding="utf-8")
    report = tmp_path / "out.xyz.report.json"
    report.write_text('{"old_report": true}\n', encoding="utf-8")

    real_replace = os.replace

    def flaky_replace(src, dst, *args, **kwargs):
        if os.path.abspath(str(dst)) == os.path.abspath(str(out)):
            raise OSError("simulated final replace failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(processor.os, "replace", flaky_replace)
    result = process_xyz(
        RefineOptions(
            input_file=str(source),
            output=str(out),
            threshold=0.1,
            workers=1,
        )
    )
    assert result.produced_output is False
    assert result.reason == "write_failed"
    assert out.read_text(encoding="utf-8") == "old output\n"
    assert report.read_text(encoding="utf-8") == '{"old_report": true}\n'
    assert list(tmp_path.glob("*.tmp")) == []


def test_build_candidate_priority_matches_reference_ordering():
    """Pin candidate-priority ordering against the per-pair sorted reference."""
    from confflow.blocks.refine.rmsd_engine import (
        _build_candidate_priority,
        _element_distance_fingerprint,
    )
    from confflow.blocks.refine.topology import build_graph

    here = Path(__file__).parent
    frame = read_xyz_file(str(here / "pentane.xyz"))[0]
    atoms = list(frame["atoms"])
    coords = np.asarray(frame["coords"], dtype=np.float64)

    reverse = list(range(len(atoms)))[::-1]
    cand_atoms = [atoms[i] for i in reverse]
    cand_coords = coords[reverse]

    graph_candidate = build_graph(cand_atoms, cand_coords).graph
    graph_representative = build_graph(atoms, coords).graph

    got = _build_candidate_priority(graph_candidate, graph_representative, cand_coords, coords)

    # independent reference: the straightforward per-pair sorted order
    elements = sorted(
        set(graph_candidate.atomic_numbers) | set(graph_representative.atomic_numbers)
    )
    fp_candidate = _element_distance_fingerprint(
        graph_candidate.atomic_numbers, cand_coords, elements
    )
    fp_representative = _element_distance_fingerprint(
        graph_representative.atomic_numbers, coords, elements
    )
    positions_b: dict[int, list[int]] = {}
    for index in range(graph_representative.n):
        positions_b.setdefault(graph_representative.atomic_numbers[index], []).append(index)

    expected = {}
    for index in range(graph_candidate.n):
        candidates = positions_b[graph_candidate.atomic_numbers[index]]
        scored = sorted(
            (
                float(np.sum((fp_candidate[index] - fp_representative[other]) ** 2)),
                other,
            )
            for other in candidates
        )
        expected[index] = tuple(other for _, other in scored)

    assert got == expected
    assert got is not None
