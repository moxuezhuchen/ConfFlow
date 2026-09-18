#!/usr/bin/env python3

"""Multi-input atom-index provenance: reference overrides are mapped per input."""

from __future__ import annotations

from pathlib import Path

from confflow.blocks.confgen.generator import run_generation
from confflow.blocks.confgen.mapping import get_full_mapping
from confflow.blocks.refine import processor
from confflow.blocks.refine.topology import apply_bond_overrides, build_graph, parse_bond_override
from confflow.core.chem_validation import load_mol_from_xyz
from confflow.core.io import read_xyz_file


def _write_xyz(path: Path, atoms: list[str], coords: list[tuple[float, float, float]]) -> Path:
    lines = [str(len(atoms)), "test"]
    lines += [f"{s} {x} {y} {z}" for s, (x, y, z) in zip(atoms, coords)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _by_cid_prefix(frames: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for frame in frames:
        cid = str(frame["metadata"].get("CID", ""))
        if cid:
            grouped.setdefault(cid[0], []).append(frame)
    return grouped


def test_get_full_mapping_handles_reordered_atoms(tmp_path):
    ref = _write_xyz(tmp_path / "ref.xyz", ["C", "N", "O"], [(0, 0, 0), (1.45, 0, 0), (2.9, 0, 0)])
    tgt = _write_xyz(tmp_path / "tgt.xyz", ["O", "N", "C"], [(2.9, 0, 0), (1.45, 0, 0), (0, 0, 0)])

    ref_mol = load_mol_from_xyz(str(ref), 1.15)
    tgt_mol = load_mol_from_xyz(str(tgt), 1.15)
    mapping = get_full_mapping(ref_mol, tgt_mol)

    # C(0) -> target C index 2, N(1) -> 1, O(2) -> 0
    assert mapping == {0: 2, 1: 1, 2: 0}


def test_confgen_maps_del_bond_to_reordered_input(tmp_path):
    ref = _write_xyz(tmp_path / "ref.xyz", ["C", "N", "O"], [(0, 0, 0), (1.45, 0, 0), (2.9, 0, 0)])
    tgt = _write_xyz(tmp_path / "tgt.xyz", ["O", "N", "C"], [(2.9, 0, 0), (1.45, 0, 0), (0, 0, 0)])

    run_generation(
        [str(ref), str(tgt)],
        chains=["1-2"],
        del_bond=[[2, 3]],
        rotate_side="right",
        output_file=str(tmp_path / "search.xyz"),
        confirm=False,
    )

    frames = read_xyz_file(str(tmp_path / "search.xyz"), parse_metadata=True)
    grouped = _by_cid_prefix(frames)
    assert grouped.get("A") and grouped.get("B")

    # Reference keeps its own indices; the reordered input gets mapped indices.
    assert {f["metadata"].get("DelBond") for f in grouped["A"]} == {"2-3"}
    assert {f["metadata"].get("DelBond") for f in grouped["B"]} == {"2-1"}


def test_confgen_maps_add_bond_to_reordered_input(tmp_path):
    # Distinct elements (C,N,O,F) make the full mapping unique; the added N-O
    # bond closes a ring off the rotated C-F chain.
    ref_coords = [(0.0, 0.0, 0.0), (1.4, 0.0, 0.0), (-0.7, 1.21, 0.0), (-0.7, -1.21, 0.0)]
    tgt_coords = list(reversed(ref_coords))
    ref = _write_xyz(tmp_path / "ref.xyz", ["C", "N", "O", "F"], ref_coords)
    tgt = _write_xyz(tmp_path / "tgt.xyz", ["F", "O", "N", "C"], tgt_coords)

    run_generation(
        [str(ref), str(tgt)],
        chains=["1-4"],
        add_bond=[[2, 3]],
        output_file=str(tmp_path / "search.xyz"),
        confirm=False,
    )

    frames = read_xyz_file(str(tmp_path / "search.xyz"), parse_metadata=True)
    grouped = _by_cid_prefix(frames)
    assert grouped.get("A") and grouped.get("B")
    assert {f["metadata"].get("AddBond") for f in grouped["A"]} == {"2-3"}
    assert {f["metadata"].get("AddBond") for f in grouped["B"]} == {"3-2"}


def test_confgen_single_input_emits_override_metadata(tmp_path):
    ref = _write_xyz(tmp_path / "ref.xyz", ["C", "N", "O"], [(0, 0, 0), (1.45, 0, 0), (2.9, 0, 0)])

    run_generation(
        [str(ref)],
        chains=["1-2"],
        del_bond=[[2, 3]],
        rotate_side="right",
        output_file=str(tmp_path / "search.xyz"),
        confirm=False,
    )

    frames = read_xyz_file(str(tmp_path / "search.xyz"), parse_metadata=True)
    assert frames
    assert {f["metadata"].get("DelBond") for f in frames} == {"2-3"}


def test_parse_and_apply_bond_overrides():
    build = build_graph(["C", "N", "O"], [[0, 0, 0], [1.45, 0, 0], [2.9, 0, 0]])
    assert build.graph is not None
    assert 2 in build.graph.adjacency[1]  # N-O inferred

    deleted = apply_bond_overrides(build, add_bond=[], del_bond=parse_bond_override("2-3"))
    assert deleted.graph is not None
    assert 2 not in deleted.graph.adjacency[1]

    added = apply_bond_overrides(build, add_bond=parse_bond_override("1-3"), del_bond=[])
    assert added.graph is not None
    assert 2 in added.graph.adjacency[0]


def test_refine_reads_override_metadata(tmp_path):
    xyz = tmp_path / "conf.xyz"
    xyz.write_text(
        "3\nConformer 1 | CID=A000001 | DelBond=2-3\nC 0 0 0\nN 1.45 0 0\nO 2.9 0 0\n",
        encoding="utf-8",
    )

    frames = processor.read_xyz_file(str(xyz))

    assert frames[0]["extra_data"]["DelBond"] == "2-3"
