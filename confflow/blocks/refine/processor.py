#!/usr/bin/env python3

"""Refine, filter, and deduplicate conformers from XYZ inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import multiprocessing
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from ...core.constants import HARTREE_TO_KCALMOL
from ...core.contracts import ExitCode, cli_output_to_txt
from ...core.elements import canonicalize_element_symbol
from ...core.io import read_xyz_file as read_xyz_frames_strict
from ._compat import load_console_bindings
from .result import RefineResult

logger = logging.getLogger("confflow.refine")

_console_bindings = load_console_bindings()
console = _console_bindings["console"]
create_progress = _console_bindings["create_progress"]
error = _console_bindings["error"]
heading = _console_bindings["heading"]
info = _console_bindings["info"]
print_table = _console_bindings["print_table"]
success = _console_bindings["success"]
warning = _console_bindings["warning"]


from . import rmsd_engine  # noqa: E402
from .topology import (  # noqa: E402
    DEFAULT_MAPPING_NODE_BUDGET,
    build_graph,
    group_frames_by_topology,
)

__all__ = [
    "RefineOptions",
    "read_xyz_file",
    "RefineResult",
    "process_xyz",
    "main",
]


def fast_rmsd(*args, **kwargs):
    return rmsd_engine.fast_rmsd(*args, **kwargs)


def get_topology_hash_worker(*args, **kwargs):
    return rmsd_engine.get_topology_hash_worker(*args, **kwargs)


def process_topology_group(*args, **kwargs):
    return rmsd_engine.process_topology_group(*args, **kwargs)


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
        max_mapping_nodes=None,
        report=None,
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
        self.max_mapping_nodes = max_mapping_nodes
        self.report = report
        # Cap workers at the local CPU count to avoid over-parallelization.
        cpu_count = multiprocessing.cpu_count()
        self.workers = max(1, min(workers, cpu_count))

        # Normalize the default output path eagerly.
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

    frames = read_xyz_frames_strict(filepath, parse_metadata=True, strict=True)
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
    """Compute count (merged duplicates) for each unique conformer.

    Modifies *final_unique* in-place.  ``rmsd_to_min`` is only meaningful for
    the global-minimum representative (0.0); it is left as ``None`` elsewhere
    because no exact symmetry-aware minimum is computed during dedup.
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
        if entry and str(entry.get("Status", "")).startswith("Kept"):
            counts[curr] += 1

    for position, f in enumerate(final_unique):
        f["count"] = counts.get(f["original_index"], 1)
        f["rmsd_to_min"] = 0.0 if position == 0 else None


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
                atom = canonicalize_element_symbol(a)
                f.write(f"{atom:<4s} {c[0]:12.8f} {c[1]:12.8f} {c[2]:12.8f}\n")


def _write_refine_output_atomic(
    output_path: str,
    final_unique: list[dict],
    global_min: float,
) -> None:
    """Write refine output without clobbering an existing file on failure."""
    output_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(output_path)}.",
        suffix=".tmp",
        dir=output_dir,
    )
    os.close(fd)
    try:
        _write_refine_output(tmp_path, final_unique, global_min)
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _paths_alias(path_a: str | None, path_b: str | None) -> bool:
    """Return True when two paths refer to the same file or location.

    Handles literal paths, relative/absolute spellings, ``..`` segments,
    symlinks and existing hard links.  For not-yet-existing paths the
    canonical real path is compared instead.
    """
    if not path_a or not path_b:
        return False
    if os.path.exists(path_a) and os.path.exists(path_b):
        try:
            if os.path.samefile(path_a, path_b):
                return True
        except OSError:
            pass
    return os.path.realpath(os.path.abspath(path_a)) == os.path.realpath(os.path.abspath(path_b))


def _write_refine_artifacts(
    output_path: str,
    final_unique: list[dict],
    global_min: float,
    *,
    report_path: str | None = None,
    report_payload: dict | None = None,
) -> None:
    """Write the refined XYZ and (optionally) its report without partial success.

    Both files are staged as temporary files.  The report is replaced first and
    the XYZ last, so a failed XYZ write cannot destroy the previous output.  If
    the XYZ replacement fails, the previous report is restored (or the new
    report is removed when none existed).  This is *not* a two-file atomic
    transaction: a crash between replacements can still leave a new report next
    to an old XYZ, but the report stores the input/output digests, so such a
    mismatch is detectable rather than passing as one successful run.
    """
    output_dir = os.path.dirname(os.path.abspath(output_path))
    fd, tmp_output = tempfile.mkstemp(
        prefix=f".{os.path.basename(output_path)}.",
        suffix=".tmp",
        dir=output_dir,
    )
    os.close(fd)
    tmp_report: str | None = None
    backup_report: str | None = None
    report_replaced = False
    try:
        _write_refine_output(tmp_output, final_unique, global_min)
        if report_path is not None and report_payload is not None:
            report_payload["output_sha256"] = _sha256_file(tmp_output)
            report_payload["output_frame_count"] = len(final_unique)
            report_dir = os.path.dirname(os.path.abspath(report_path))
            fd_report, tmp_report = tempfile.mkstemp(
                prefix=f".{os.path.basename(report_path)}.",
                suffix=".tmp",
                dir=report_dir,
            )
            os.close(fd_report)
            with open(tmp_report, "w", encoding="utf-8") as handle:
                json.dump(report_payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            if os.path.exists(report_path):
                fd_backup, backup_report = tempfile.mkstemp(
                    prefix=f".{os.path.basename(report_path)}.backup.",
                    suffix=".tmp",
                    dir=report_dir,
                )
                os.close(fd_backup)
                shutil.copyfile(report_path, backup_report)
            os.replace(tmp_report, report_path)
            tmp_report = None
            report_replaced = True
        try:
            os.replace(tmp_output, output_path)
            tmp_output = None
        except Exception:
            if report_replaced and report_path is not None:
                try:
                    if backup_report is not None:
                        os.replace(backup_report, report_path)
                        backup_report = None
                    else:
                        os.remove(report_path)
                except OSError:
                    pass
            raise
    finally:
        for leftover in (tmp_output, tmp_report, backup_report):
            if leftover and os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass


def _refine_failure_message(result: RefineResult, input_file: str) -> str:
    if result.reason == "missing_input":
        return f"Input file not found: {input_file}"
    if result.reason in {"empty_input", "no_topology"}:
        return f"No input conformers found in {input_file}"
    if result.reason in {"filtered_to_zero", "deduped_to_zero"}:
        return "No conformers remain after filtering."
    if result.reason == "write_failed":
        return "Failed to write refine output; previous output was preserved."
    if result.reason == "report_path_conflict":
        return "Refine report path conflicts with the input or output file."
    return "Refine failed."


# ==============================================================================
# Core entry logic
# ==============================================================================


def _new_frame_record(frame: dict) -> dict:
    extra = frame.get("extra_data", {}) or {}
    return {
        "frame_id": frame.get("original_index"),
        "cid": extra.get("CID"),
        "topology_group": None,
        "topology_status": None,
        "topology_reason": "",
        "status": "pending",
        "reason": "",
        "removed_by": None,
        "representative_frame_id": None,
        "witness_rmsd": None,
        "cutoff": None,
        "mapping": None,
        "mapping_permutation": None,
        "search_complete": None,
        "search_work": None,
        "final_output": False,
    }


def _record_removal(record: dict, removed_by: str, reason: str) -> None:
    if record.get("status") == "removed":
        return
    record["status"] = "removed"
    record["removed_by"] = removed_by
    record["reason"] = reason


def _frame_is_writable(frame: dict) -> bool:
    try:
        atoms = frame.get("atoms", []) or []
        coords = np.asarray(frame.get("coords"), dtype=np.float64)
        canonical = [canonicalize_element_symbol(atom) for atom in atoms]
    except (TypeError, ValueError):
        return False
    natoms = int(frame.get("natoms", len(atoms)))
    if natoms != len(atoms) or not atoms or len(canonical) != natoms:
        return False
    return coords.shape == (natoms, 3) and bool(np.all(np.isfinite(coords)))


def _apply_dedup_reports(
    records: dict[int, dict],
    report_data: list[dict],
) -> None:
    for entry in report_data:
        record = records.get(entry.get("Input_Frame_ID"))
        if record is None:
            continue
        status = str(entry.get("Status", ""))
        if status == "Removed (Duplicate)":
            record.update(
                status="removed",
                removed_by="duplicate",
                reason=str(entry.get("Reason", "duplicate")),
                representative_frame_id=entry.get("Duplicate_Of_Input_ID"),
                witness_rmsd=entry.get("Witness_RMSD"),
                cutoff=entry.get("Cutoff"),
                mapping=entry.get("Mapping"),
                mapping_permutation=entry.get("Mapping_Permutation"),
                search_complete=entry.get("Search_Complete"),
                search_work=entry.get("Search_Work"),
            )
        elif status.startswith("Kept"):
            record.update(
                status="kept",
                reason=str(entry.get("Reason", "")),
                search_complete=entry.get("Search_Complete"),
                search_work=entry.get("Search_Work"),
            )
            if status == "Kept (Unresolved)":
                record["status"] = "kept_unresolved"


def process_xyz(args):
    """Execute the main deduplication and filtering logic.

    Parameters
    ----------
    args : argparse.Namespace or RefineOptions
        Configuration object containing all refine parameters.
    """
    if not os.path.exists(args.input_file):
        error(f"Input file not found: {args.input_file}")
        return RefineResult(False, args.output, 0, "missing_input")

    report_path = getattr(args, "report", None)
    if report_path is None:
        report_path = f"{args.output}.report.json"
    if _paths_alias(report_path, args.input_file) or _paths_alias(report_path, args.output):
        error(f"Refine report path conflicts with the input or output file: {report_path}")
        return RefineResult(False, args.output, 0, "report_path_conflict")

    # Simplified output: single-line refine parameter display
    ewin_str = f"{args.ewin} kcal/mol" if args.ewin is not None else "none"
    console.print(f"RMSD={args.threshold}, E-window={ewin_str}")

    try:
        all_frames = read_xyz_file(args.input_file)
    except (OSError, ValueError) as e:
        error(f"No input conformers found in {args.input_file}: {e}")
        return RefineResult(False, args.output, 0, "empty_input")

    if not all_frames:
        error(f"No input conformers found in {args.input_file}")
        return RefineResult(False, args.output, 0, "empty_input")

    mapping_budget = getattr(args, "max_mapping_nodes", None)
    if mapping_budget is None:
        mapping_budget = DEFAULT_MAPPING_NODE_BUDGET

    # 1. Legacy topology fingerprint (cheap pre-filter / compatibility surface)
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

    # 2. Build one bonding graph per frame, then group with exact matching
    for frame in all_frames:
        build = build_graph(frame["atoms"], frame["coords"])
        frame["graph"] = build.graph
        frame["graph_status"] = build.status
        frame["graph_reason"] = build.reason

    clusters = group_frames_by_topology(all_frames, node_budget=mapping_budget)
    records: dict[int, dict] = {
        int(frame["original_index"]): _new_frame_record(frame) for frame in all_frames
    }
    for cluster in clusters:
        for frame in cluster.frames:
            frame["topology_cluster"] = cluster.cluster_id
            frame["topology_cluster_status"] = cluster.status
            frame["topology_cluster_reason"] = cluster.reason
            record = records.get(int(frame["original_index"]))
            if record is not None:
                record["topology_group"] = cluster.cluster_id
                record["topology_status"] = cluster.status
                record["topology_reason"] = cluster.reason

    # 3. Topology selection (majority) with unresolved/invalid protection
    confirmed_clusters = [cluster for cluster in clusters if cluster.status == "confirmed"]
    problem_clusters = [cluster for cluster in clusters if cluster.status != "confirmed"]
    if args.keep_all_topos:
        selected = list(all_frames)
        majority_filter = "keep_all_topos"
    elif problem_clusters:
        # An unresolved or invalid topology must not be dropped as a minority
        # group: skip majority filtering and report why.
        selected = list(all_frames)
        majority_filter = "skipped_unresolved_or_invalid_topology"
        console.print(
            "  Topology grouping incomplete; majority filter skipped (all frames retained)."
        )
    else:
        main_cluster = max(confirmed_clusters, key=lambda c: (len(c.frames), -c.first_index))
        selected = list(main_cluster.frames)
        selected_ids = {id(frame) for frame in selected}
        for frame in all_frames:
            if id(frame) not in selected_ids:
                record = records.get(int(frame["original_index"]))
                if record is not None:
                    _record_removal(record, "minority_topology", "not in majority topology group")
        majority_filter = f"applied:{main_cluster.cluster_id}"

    # 4. Filtering (energy / imaginary frequencies)
    if args.imag is not None:
        filtered = []
        for frame in selected:
            if frame.get("num_imag_freqs") == args.imag:
                filtered.append(frame)
            else:
                record = records.get(int(frame["original_index"]))
                if record is not None:
                    _record_removal(
                        record,
                        "imag_filter",
                        f"num_imag_freqs={frame.get('num_imag_freqs')} != {args.imag}",
                    )
        selected = filtered

    # Exit early so downstream energy-window and RMSD logic does not assume
    # that at least one conformer survived the metadata-based filters.
    if not selected:
        console.print("  No conformers remain after filtering.")
        return RefineResult(False, args.output, 0, "filtered_to_zero")

    if args.ewin is not None and not args.dedup_only:
        min_e = min(frame["energy"] for frame in selected)
        limit = min_e + args.ewin / HARTREE_TO_KCALMOL
        before_count = len(selected)
        filtered = []
        for frame in selected:
            if frame["energy"] <= limit:
                filtered.append(frame)
            else:
                record = records.get(int(frame["original_index"]))
                if record is not None:
                    _record_removal(
                        record,
                        "energy_window",
                        f"energy {frame['energy']} above window limit {limit}",
                    )
        selected = filtered
        if len(selected) < before_count:
            console.print(f"  E-window filter: {before_count} → {len(selected)}")

    # 5. Per-topology-class RMSD deduplication (never across topologies)
    final_unique: list[dict] = []
    report_data: list[dict] = []
    selected_ids = {id(frame) for frame in selected}
    for cluster in clusters:
        group_frames = [frame for frame in cluster.frames if id(frame) in selected_ids]
        if not group_frames:
            continue
        if cluster.status != "confirmed":
            for frame in group_frames:
                record = records.get(int(frame["original_index"]))
                if not _frame_is_writable(frame):
                    if record is not None:
                        _record_removal(
                            record,
                            "invalid_input",
                            cluster.reason or "frame cannot be written",
                        )
                    continue
                final_unique.append(frame)
                report_data.append(
                    {
                        "Input_Frame_ID": frame["original_index"],
                        "Status": (
                            "Kept (Unresolved)"
                            if cluster.status == "unresolved"
                            else "Kept (Invalid)"
                        ),
                        "Duplicate_Of_Input_ID": "-",
                        "Reason": cluster.reason or cluster.status,
                        "Search_Complete": False,
                    }
                )
            continue
        group_unique, group_report = process_topology_group(
            group_frames,
            args.threshold,
            args.noH,
            args.workers,
            getattr(args, "energy_tolerance", 0.05),
            mapping_budget,
        )
        final_unique.extend(group_unique)
        report_data.extend(group_report)

    if not final_unique:
        console.print("  No conformers remain after filtering.")
        return RefineResult(False, args.output, 0, "deduped_to_zero")

    _apply_dedup_reports(records, report_data)

    # 6. Statistics and output
    final_unique.sort(
        key=lambda frame: (
            frame["energy"] if np.isfinite(frame["energy"]) else float("inf"),
            int(frame.get("original_index", 0)),
        )
    )
    global_min = final_unique[0]["energy"]

    _compute_dedup_counts(final_unique, selected, report_data)

    truncated = []
    if args.max_conformers and len(final_unique) > args.max_conformers:
        truncated = final_unique[args.max_conformers :]
        final_unique = final_unique[: args.max_conformers]
        for frame in truncated:
            record = records.get(int(frame["original_index"]))
            if record is not None:
                _record_removal(record, "max_conformers", "beyond max_conformers limit")

    for frame in final_unique:
        record = records.get(int(frame["original_index"]))
        if record is not None:
            record["final_output"] = True
            if record.get("status") == "pending":
                record["status"] = "kept"

    report_payload = _build_report_payload(
        args,
        all_frames,
        clusters,
        records,
        selected,
        final_unique,
        majority_filter,
        mapping_budget,
    )

    try:
        _write_refine_artifacts(
            args.output,
            final_unique,
            global_min,
            report_path=report_path,
            report_payload=report_payload,
        )
    except OSError as exc:
        error(f"Failed to write refine output: {exc}")
        return RefineResult(False, args.output, 0, "write_failed")

    return RefineResult(True, args.output, len(final_unique), "ok")


def _build_report_payload(
    args,
    all_frames: list[dict],
    clusters: list,
    records: dict[int, dict],
    selected: list[dict],
    final_unique: list[dict],
    majority_filter: str,
    mapping_budget: int,
) -> dict:
    input_sha = _sha256_file(args.input_file)
    params = {
        "threshold": float(args.threshold),
        "ewin": args.ewin,
        "imag": args.imag,
        "noH": bool(args.noH),
        "max_conformers": args.max_conformers,
        "dedup_only": bool(args.dedup_only),
        "keep_all_topos": bool(args.keep_all_topos),
        "energy_tolerance": float(getattr(args, "energy_tolerance", 0.05)),
        "max_mapping_nodes": int(mapping_budget),
    }
    run_id = hashlib.sha256(
        json.dumps({"input_sha256": input_sha, "params": params}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    frame_entries = []
    for frame in all_frames:
        record = records.get(int(frame["original_index"]), _new_frame_record(frame))
        entry = dict(record)
        entry["status"] = record.get("status") if record.get("status") != "pending" else "kept"
        entry["final_output"] = bool(record.get("final_output"))
        frame_entries.append(entry)
    cluster_entries = [
        {
            "cluster_id": cluster.cluster_id,
            "status": cluster.status,
            "frame_ids": [frame["original_index"] for frame in cluster.frames],
            "representative_frame_id": cluster.frames[0]["original_index"],
            "reason": cluster.reason,
        }
        for cluster in clusters
    ]
    return {
        "schema": "confflow.refine.report/v1",
        "run_id": run_id,
        "input_file": str(args.input_file),
        "input_sha256": input_sha,
        "input_frame_count": len(all_frames),
        "output_file": str(args.output),
        "output_sha256": None,
        "output_frame_count": None,
        "params": params,
        "majority_filter": majority_filter,
        "selected_frame_count": len(selected),
        "final_frame_count": len(final_unique),
        "topology_clusters": cluster_entries,
        "frames": frame_entries,
    }


def main():
    """Command-line entry point."""
    if "fork" in multiprocessing.get_all_start_methods():
        try:
            multiprocessing.set_start_method("fork")
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to set the multiprocessing start method: {e}")

    parser = argparse.ArgumentParser(
        description="Refine and deduplicate conformers from an XYZ file"
    )
    parser.add_argument("input_file", help="Path to the input XYZ file")
    parser.add_argument("-o", "--output", help="Path to the output XYZ file")
    parser.add_argument(
        "-t", "--threshold", type=float, default=0.25, help="RMSD threshold (default: 0.25)"
    )
    parser.add_argument("--ewin", type=float, help="Energy window in kcal/mol")
    parser.add_argument("--imag", type=int, help="Number of imaginary frequencies to keep")
    parser.add_argument("--noH", action="store_true", help="Ignore hydrogen atoms in RMSD")
    parser.add_argument(
        "-n", "--max-conformers", type=int, help="Maximum number of conformers to write"
    )
    parser.add_argument("--dedup-only", action="store_true", help="Only deduplicate conformers")
    parser.add_argument(
        "--keep-all-topos", action="store_true", help="Keep all detected topologies"
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=max(1, multiprocessing.cpu_count() - 2),
        help="Number of worker processes to use",
    )
    parser.add_argument(
        "--energy-tolerance",
        type=float,
        default=0.05,
        help="Energy tolerance in kcal/mol for RMSD-threshold relaxation (default: 0.05)",
    )
    parser.add_argument(
        "--max-mapping-nodes",
        type=int,
        default=None,
        help=(
            "Deterministic node budget for exact graph-mapping search "
            f"(default: {DEFAULT_MAPPING_NODE_BUDGET})"
        ),
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path to the machine-readable JSON report (default: <output>.report.json)",
    )

    args = parser.parse_args()

    # If no output file specified, auto-generate one
    if args.output is None:
        base, _ = os.path.splitext(args.input_file)
        args.output = f"{base}_cleaned.xyz"

    if not os.path.exists(args.input_file):
        print(f"Input file not found: {args.input_file}", file=sys.stderr)
        return ExitCode.USAGE_ERROR

    try:
        with cli_output_to_txt(args.input_file):
            result = process_xyz(args)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return ExitCode.RUNTIME_ERROR
    if isinstance(result, RefineResult) and not result.produced_output:
        print(_refine_failure_message(result, args.input_file), file=sys.stderr)
        return ExitCode.RUNTIME_ERROR
    return ExitCode.SUCCESS


if __name__ == "__main__":
    main()
