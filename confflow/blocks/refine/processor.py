#!/usr/bin/env python3

"""
ConfFlow Refine - Conformer Post-processing Tool (v1.0).

RMSD deduplication, energy filtering, and topology analysis.
Modular design (Library & Script).

Core computations (PMI, RMSD, topology hash, batch dedup) are in rmsd_engine.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from ...core.constants import HARTREE_TO_KCALMOL
from ...core.contracts import ExitCode, cli_output_to_txt

logger = logging.getLogger("confflow.refine")

try:
    from ...core.console import (
        console,
        create_progress,
        error,
        heading,
        info,
        print_table,
        success,
        warning,
    )
except (ImportError, ModuleNotFoundError):
    console = type("Mock", (), {"print": print})

    def create_progress():
        return type(
            "Mock",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda *a: None,
                "add_task": lambda *a: 0,
                "update": lambda *a: None,
            },
        )()

    def info(m):
        print(f"INFO: {m}")

    def success(m):
        print(f"SUCCESS: {m}")

    def warning(m):
        print(f"WARNING: {m}")

    def error(m):
        print(f"ERROR: {m}")

    def heading(m):
        print(f"=== {m} ===")

    def print_table(*a, **k):
        pass


from .rmsd_engine import (  # noqa: E402
    fast_rmsd,
    get_topology_hash_worker,
    process_topology_group,
)

__all__ = [
    "RefineOptions",
    "read_xyz_file",
    "process_xyz",
    "main",
]

# ==============================================================================
# Parameter container (API)
# ==============================================================================


class RefineOptions:
    """Container for passing parameters across modules (mimics argparse.Namespace)."""

    def __init__(
        self,
        input_file,
        output=None,
        threshold=0.25,
        ewin=None,
        imag=None,
        noH=False,
        max_conformers=None,
        dedup_only=False,
        keep_all_topos=False,
        workers=1,
        energy_tolerance=0.05,
    ):
        self.input_file = input_file
        self.output = output
        self.threshold = threshold
        self.ewin = ewin
        self.imag = imag
        self.noH = noH
        self.max_conformers = max_conformers
        self.dedup_only = dedup_only
        self.keep_all_topos = keep_all_topos
        self.energy_tolerance = energy_tolerance
        # Improvement: cap workers at CPU count to avoid over-parallelization
        cpu_count = multiprocessing.cpu_count()
        self.workers = max(1, min(workers, cpu_count))

        # Validation logic
        if self.output is None:
            base, _ = os.path.splitext(self.input_file)
            self.output = f"{base}_cleaned.xyz"


# ==============================================================================
# IO functions
# ==============================================================================


def read_xyz_file(filepath):
    """Read an XYZ file and return frame structures for internal refine use."""
    if not os.path.exists(filepath):
        return []

    from ...core.io import read_xyz_file_safe as io_read_xyz_file_safe

    frames = io_read_xyz_file_safe(filepath, parse_metadata=True)
    out = []
    for frame_idx, fr in enumerate(frames):
        meta = fr.get("metadata", {}) or {}

        # Energy: prefer G (Gibbs), then E/Energy; default to inf if missing
        energy_key = (
            "G"
            if "G" in meta
            else ("E" if "E" in meta else ("Energy" if "Energy" in meta else None))
        )
        energy_val = meta.get("G", meta.get("E", meta.get("Energy")))
        try:
            energy = float(energy_val)
        except (TypeError, ValueError):
            energy = float("inf")

        # Imaginary frequency count: compatible with Imag=1 / num_imag_freqs=1
        imag_val = meta.get("num_imag_freqs", meta.get("Imag"))
        try:
            num_imag = int(imag_val) if imag_val is not None else None
        except (TypeError, ValueError):
            num_imag = None

        # Extra metadata: filter out common primary fields, keep the rest as-is
        skip = {"e", "g", "energy", "imag", "num_imag_freqs", "rank", "count", "de", "rmsd", "topo"}
        extra_data = {k: v for k, v in meta.items() if str(k).lower() not in skip}

        atoms = fr.get("atoms", []) or []
        coords = np.array(fr.get("coords", []) or [], dtype=np.float64)

        out.append(
            {
                "natoms": fr.get("natoms", len(atoms)),
                "comment": fr.get("comment", ""),
                "energy": energy,
                "energy_key": energy_key,
                "num_imag_freqs": num_imag,
                "extra_data": extra_data,
                "atoms": atoms,
                "original_atoms": atoms,
                "coords": coords,
                "original_index": fr.get("original_index", frame_idx),
            }
        )

    return out


# ==============================================================================
# process_xyz sub-steps
# ==============================================================================


def _compute_dedup_counts(
    final_unique: list[dict],
    frames_to_process: list[dict],
    report_data: list[dict],
) -> None:
    """Compute count (merged duplicates) and rmsd_to_min for each unique conformer.

    Modifies *final_unique* in-place.

    Parameters
    ----------
    final_unique : list[dict]
        List of unique conformer dicts.
    frames_to_process : list[dict]
        All frames considered during dedup.
    report_data : list[dict]
        Dedup report entries.
    """
    report_map = {r["Input_Frame_ID"]: r for r in report_data}
    counts: dict[int, int] = defaultdict(int)
    for f in frames_to_process:
        curr = f["original_index"]
        path = {curr}
        entry = report_map.get(curr)
        while entry and entry.get("Status") == "Removed (Duplicate)":
            dup_id = entry.get("Duplicate_Of_Input_ID")
            if dup_id in path:
                break
            path.add(dup_id)
            curr = dup_id
            entry = report_map.get(curr)
        if entry and entry.get("Status") == "Kept":
            counts[curr] += 1

    ref_heavy = final_unique[0]["heavy_coords"] if final_unique else None
    for f in final_unique:
        f["count"] = counts.get(f["original_index"], 1)
        f["rmsd_to_min"] = fast_rmsd(f["heavy_coords"], ref_heavy) if ref_heavy is not None else 0


def _write_refine_output(output_path: str, final_unique: list[dict], global_min: float) -> None:
    """Write deduplicated conformers to the output XYZ file."""
    with open(output_path, "w") as f:
        for i, frame in enumerate(final_unique, 1):
            de = (frame["energy"] - global_min) * HARTREE_TO_KCALMOL
            imag_val = frame.get("num_imag_freqs")
            extra_items = []
            emit_g = str(frame.get("energy_key") or "").upper() == "G"
            for k, v in frame.get("extra_data", {}).items():
                if str(k).lower() == "tsatoms":
                    continue
                if emit_g and str(k) in {"G_corr", "E_sp", "E_includes_gcorr"}:
                    continue
                extra_items.append(f"{k}={v}")
            extra = " | ".join(extra_items)

            label = "G" if emit_g else "E"
            line = f"Rank={i} | {label}={frame['energy']:.8f} | DE={de:.2f} kcal/mol"
            if imag_val is not None:
                line += f" | Imag={imag_val}"
            if extra:
                line += " | " + extra

            f.write(f"{frame['natoms']}\n{line}\n")
            for a, c in zip(frame["original_atoms"], frame["coords"]):
                f.write(f"{a:<4s} {c[0]:12.8f} {c[1]:12.8f} {c[2]:12.8f}\n")


# ==============================================================================
# Core entry logic
# ==============================================================================


def process_xyz(args):
    """Execute the main deduplication and filtering logic.

    Parameters
    ----------
    args : argparse.Namespace or RefineOptions
        Configuration object containing all refine parameters.
    """
    if not os.path.exists(args.input_file):
        error(f"Input file not found: {args.input_file}")
        return

    # Simplified output: single-line refine parameter display
    ewin_str = f"{args.ewin} kcal/mol" if args.ewin is not None else "none"
    console.print(f"RMSD={args.threshold}, E-window={ewin_str}")

    all_frames = read_xyz_file(args.input_file)
    if not all_frames:
        return

    # 1. Topology analysis
    topologies = defaultdict(list)
    atom_coord_pairs = [(f["atoms"], f["coords"]) for f in all_frames]

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        chunk = max(1, len(all_frames) // (args.workers * 4) + 1)

        topo_hashes = []
        with create_progress() as progress:
            task_id = progress.add_task("Topology hash", total=len(all_frames))
            for res in executor.map(get_topology_hash_worker, atom_coord_pairs, chunksize=chunk):
                topo_hashes.append(res)
                progress.advance(task_id)

    for i, h in enumerate(topo_hashes):
        all_frames[i]["topology_hash"] = h
        topologies[h].append(all_frames[i])

    if not topologies:
        return

    # 2. Determine main topology
    main_topo_hash = max(topologies, key=lambda k: len(topologies[k]))
    frames_to_process = all_frames if args.keep_all_topos else topologies[main_topo_hash]

    # 3. Filtering (energy / imaginary frequencies)
    if args.imag is not None:
        frames_to_process = [f for f in frames_to_process if f.get("num_imag_freqs") == args.imag]

    if args.ewin is not None and not args.dedup_only:
        min_e = min(f["energy"] for f in frames_to_process)
        limit = min_e + args.ewin / HARTREE_TO_KCALMOL
        before_count = len(frames_to_process)
        frames_to_process = [f for f in frames_to_process if f["energy"] <= limit]
        if len(frames_to_process) < before_count:
            console.print(f"  E-window filter: {before_count} → {len(frames_to_process)}")

    # 4. RMSD deduplication
    if frames_to_process:
        final_unique, report_data = process_topology_group(
            frames_to_process, args.threshold, args.noH, args.workers,
            getattr(args, 'energy_tolerance', 0.05)
        )
    else:
        final_unique, report_data = [], []

    if not final_unique:
        console.print("  No conformers remain after filtering.")
        return

    # 5. Statistics and output
    final_unique.sort(key=lambda x: x["energy"])
    global_min = final_unique[0]["energy"]

    _compute_dedup_counts(final_unique, frames_to_process, report_data)

    if args.max_conformers and len(final_unique) > args.max_conformers:
        final_unique = final_unique[: args.max_conformers]

    _write_refine_output(args.output, final_unique, global_min)


# ==============================================================================
# Command-line entry
# ==============================================================================


def main():
    """Command-line entry point."""
    try:
        multiprocessing.set_start_method("fork")
    except RuntimeError as e:
        logger.debug(f"failed to set multiprocessing start method: {e}")

    parser = argparse.ArgumentParser(
        description="ConfFlow Refine (v1.0) - conformer post-processing"
    )
    parser.add_argument("input_file", help="Input XYZ file")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument(
        "-t", "--threshold", type=float, default=0.25, help="RMSD threshold (default 0.25)"
    )
    parser.add_argument("--ewin", type=float, help="Energy window (kcal/mol)")
    parser.add_argument("--imag", type=int, help="Number of imaginary frequencies to keep")
    parser.add_argument("--noH", action="store_true", help="Ignore H atoms in RMSD")
    parser.add_argument(
        "-n", "--max-conformers", type=int, help="Maximum number of output conformers"
    )
    parser.add_argument("--dedup-only", action="store_true", help="Deduplicate only")
    parser.add_argument("--keep-all-topos", action="store_true", help="Keep all topologies")
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=max(1, multiprocessing.cpu_count() - 2),
        help="Parallel worker count",
    )
    parser.add_argument(
        "--energy-tolerance",
        type=float,
        default=0.05,
        help="Energy tolerance (kcal/mol) for RMSD threshold relaxation (default 0.05)",
    )

    args = parser.parse_args()

    # If no output file specified, auto-generate one
    if args.output is None:
        base, _ = os.path.splitext(args.input_file)
        args.output = f"{base}_cleaned.xyz"

    with cli_output_to_txt(args.input_file):
        process_xyz(args)
    return ExitCode.SUCCESS


if __name__ == "__main__":
    main()
