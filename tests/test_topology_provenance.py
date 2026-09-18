#!/usr/bin/env python3

"""Multi-input atom-index provenance: reference overrides are mapped per input."""

from __future__ import annotations

from pathlib import Path

from confflow.blocks.confgen.generator import run_generation
from confflow.blocks.confgen.mapping import get_full_mapping
from confflow.blocks.refine import processor
from confflow.blocks.refine.topology import apply_bond_overrides, build_graph, parse_bond_override
from confflow.calc.result_writer import append_result, write_failed_xyz
from confflow.calc.run_services import TaskSourceBuilder
from confflow.calc.runner import CalcStepRunner
from confflow.core.chem_validation import load_mol_from_xyz
from confflow.core.io import read_xyz_file
from confflow.core.models import TaskContext


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


# ---------------------------------------------------------------------------
# ConfGen -> Calc -> Refine override survival
# ---------------------------------------------------------------------------


def _calc_leg(tmp_path: Path, search_xyz: Path) -> Path:
    """Run the real calc-side task sourcing and result writing."""
    builder = TaskSourceBuilder(
        work_dir=str(tmp_path / "calc"),
        config={},
        iter_geometries_fn=CalcStepRunner._iter_input_geometries,
        job_name_fn=lambda i, geom: f"A{i + 1:06d}",
    )
    tasks, job_meta_map = builder.build_from_input(str(search_xyz))

    result_path = tmp_path / "result.xyz"
    append_result(
        str(result_path),
        job_meta_map,
        {
            "status": "success",
            "job_name": tasks[0].job_name,
            "energy": -1.0,
            "final_coords": tasks[0].coords,
        },
    )
    return result_path


def test_del_bond_survives_calc_and_refine(tmp_path):
    ref = _write_xyz(tmp_path / "ref.xyz", ["C", "N", "O"], [(0, 0, 0), (1.45, 0, 0), (2.9, 0, 0)])

    run_generation(
        [str(ref)],
        chains=["1-2"],
        del_bond=[[2, 3]],
        rotate_side="right",
        output_file=str(tmp_path / "search.xyz"),
        confirm=False,
    )

    result_path = _calc_leg(tmp_path, tmp_path / "search.xyz")

    # Refine leg: the override must still be attached after the calc step.
    frames = processor.read_xyz_file(str(result_path))
    assert frames[0]["extra_data"].get("DelBond") == "2-3"

    # Plain distance inference would re-create N-O; the override must prevent it.
    build = build_graph(frames[0]["atoms"], frames[0]["coords"])
    assert build.graph is not None
    assert 2 in build.graph.adjacency[1]

    refined = apply_bond_overrides(
        build, add_bond=[], del_bond=parse_bond_override(frames[0]["extra_data"]["DelBond"])
    )
    assert refined.graph is not None
    assert 2 not in refined.graph.adjacency[1]


def test_add_bond_survives_calc_and_refine(tmp_path):
    # C-F is 4.0 A apart, so distance inference would never create it; only
    # the explicit AddBond override may.
    ref = _write_xyz(
        tmp_path / "ref.xyz",
        ["C", "N", "O", "F"],
        [(0, 0, 0), (1.4, 0, 0), (2.8, 0, 0), (0, 4.0, 0)],
    )

    run_generation(
        [str(ref)],
        chains=["1-2"],
        add_bond=[[1, 4]],
        rotate_side="right",
        output_file=str(tmp_path / "search.xyz"),
        confirm=False,
    )

    result_path = _calc_leg(tmp_path, tmp_path / "search.xyz")

    frames = processor.read_xyz_file(str(result_path))
    assert frames[0]["extra_data"].get("AddBond") == "1-4"

    build = build_graph(frames[0]["atoms"], frames[0]["coords"])
    assert build.graph is not None
    assert 3 not in build.graph.adjacency[0]  # C-F not distance-inferable

    refined = apply_bond_overrides(
        build, add_bond=parse_bond_override(frames[0]["extra_data"]["AddBond"]), del_bond=[]
    )
    assert refined.graph is not None
    assert 3 in refined.graph.adjacency[0]


def test_failed_xyz_preserves_bond_overrides(tmp_path):
    """Failed frames may be re-submitted as new calc inputs.

    Their topology overrides must survive the failed.xyz path too.
    """
    tasks = [
        TaskContext(
            job_name="A000001",
            work_dir=str(tmp_path / "A000001"),
            coords=["C 0.0 0.0 0.0", "N 1.45 0.0 0.0", "O 2.9 0.0 0.0"],
            metadata={"CID": "A000001", "DelBond": "2-3"},
            config={},
        )
    ]

    write_failed_xyz(
        str(tmp_path),
        [{"job_name": "A000001", "status": "failed", "error": "SCF failed"}],
        tasks,
    )

    frames = processor.read_xyz_file(str(tmp_path / "failed.xyz"))

    assert frames[0]["extra_data"].get("DelBond") == "2-3"
    build = build_graph(frames[0]["atoms"], frames[0]["coords"])
    refined = apply_bond_overrides(
        build, add_bond=[], del_bond=parse_bond_override(frames[0]["extra_data"]["DelBond"])
    )
    assert refined.graph is not None
    assert 2 not in refined.graph.adjacency[1]
