#!/usr/bin/env python3

"""Workflow statistics, checkpointing, and result analysis module."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from typing import Any

from ..core import io as io_xyz

__all__ = [
    "count_task_statuses_in_results_db",
    "CheckpointManager",
    "WorkflowStatsTracker",
    "TaskStatsCollector",
    "FailureTracker",
    "Tracer",
]

logger = logging.getLogger("confflow.workflow.stats")


def count_task_statuses_in_results_db(db_path: str) -> dict[str, int] | None:
    """Count task statuses from the calc results database (compatibility interface)."""
    return TaskStatsCollector.count_statuses(db_path)


class CheckpointManager:
    def __init__(self, root_dir: str):
        self.checkpoint_file = os.path.join(root_dir, ".checkpoint")

    def load(self) -> int:
        """Load last completed step index.

        Returns
        -------
            int: last completed step index, or -1 if checkpoint missing/unreadable.
        """
        if not os.path.exists(self.checkpoint_file):
            return -1
        try:
            with open(self.checkpoint_file, encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("last_completed_step", -1))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.debug(f"Failed to load checkpoint: {e}")
            return -1

    def save(self, step_index: int, workflow_stats: dict[str, Any]) -> None:
        data = {
            "last_completed_step": step_index,
            "timestamp": datetime.now().isoformat(),
            "stats": workflow_stats,
        }
        with open(self.checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


class WorkflowStatsTracker:
    def __init__(self, input_files: list[str], original_inputs: list[str]):
        self.start_time = datetime.now()
        self.stats = {
            "start_time": self.start_time.isoformat(),
            "input_files": input_files,
            "original_input_files": original_inputs,
            "steps": [],
            "_start_ts": time.time(),
        }

    def add_step(self, step_stats: dict[str, Any]) -> None:
        steps = self.stats["steps"]
        if isinstance(steps, list):
            steps.append(step_stats)

    def finalize(self, final_output: Any) -> dict[str, Any]:
        self.stats["end_time"] = datetime.now().isoformat()
        self.stats["final_output"] = final_output if isinstance(final_output, str) else None
        start_ts_raw = self.stats.pop("_start_ts", time.time())
        start_ts = float(start_ts_raw) if isinstance(start_ts_raw, (int, float)) else time.time()
        self.stats["total_duration_seconds"] = round(time.time() - start_ts, 2)
        return self.stats

    def get_stats(self) -> dict[str, Any]:
        return self.stats


class TaskStatsCollector:
    @staticmethod
    def count_statuses(db_path: str) -> dict[str, int] | None:
        try:
            if not db_path or (not os.path.exists(db_path)):
                return None
            con = sqlite3.connect(db_path)
            try:
                cur = con.cursor()
                cur.execute("select status, count(*) from task_results group by status")
                rows = cur.fetchall() or []
                counts: dict[str, int] = {}
                for st, n in rows:
                    if st is None:
                        continue
                    counts[str(st)] = int(n)
                return {
                    "success": counts.get("success", 0),
                    "failed": counts.get("failed", 0),
                    "skipped": counts.get("skipped", 0),
                    "total": sum(int(n) for st, n in rows if st),
                }
            finally:
                con.close()
        except (sqlite3.Error, OSError) as e:
            logger.debug(f"Failed to count task statuses in {db_path}: {e}")
            return None

    @staticmethod
    def count_failed(db_path: str) -> int | None:
        if not os.path.exists(db_path):
            return None
        counts = TaskStatsCollector.count_statuses(db_path)
        if counts is None:
            return None
        return int(counts.get("failed", 0))


class FailureTracker:
    def __init__(self, failed_dir: str):
        self.failed_dir = failed_dir
        self.combined_failed = os.path.join(failed_dir, "failed.xyz")
        self.summary_path = os.path.join(failed_dir, "failed_summary.txt")

    def clear_previous(self) -> None:
        import contextlib

        for fp in (self.combined_failed, self.summary_path):
            with contextlib.suppress(FileNotFoundError):
                os.remove(fp)

    def append(self, src_failed: str, step_name: str) -> None:
        try:
            with open(src_failed, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return

        out_lines = []
        i = 0
        while i < len(lines):
            try:
                natoms = int(lines[i].strip())
            except (ValueError, IndexError):
                break
            try:
                comment = lines[i + 1].rstrip("\n")
                if "Step=" not in comment:
                    comment = f"{comment} Step={step_name}"
                out_lines.append(f"{natoms}\n")
                out_lines.append(comment + "\n")
                out_lines.extend(lines[i + 2 : i + 2 + natoms])
                i += 2 + natoms
            except IndexError:
                break

        if out_lines:
            with open(self.combined_failed, "a", encoding="utf-8") as f:
                f.writelines(out_lines)
            self._update_summary()

    def _update_summary(self) -> None:
        if not os.path.exists(self.combined_failed):
            return
        rows = ["name\terror\trescue\n"]
        with open(self.combined_failed, encoding="utf-8") as f:
            lines = f.readlines()
        i = 0
        while i < len(lines):
            try:
                natoms = int(lines[i].strip())
                comment = lines[i + 1].strip()
                job = "unknown"
                err = "unknown"
                for p in comment.split():
                    if p.startswith("Job="):
                        job = p.split("=")[1]
                    if p.startswith("Error="):
                        err = p.split("=")[1]
                rows.append(f"{job}\t{err}\tCheck logs\n")
                i += 2 + natoms
            except (ValueError, IndexError) as e:  # P2-4: typed exception + debug log
                logger.debug("_update_summary: failed to parse failed.xyz at line %d: %s", i, e)
                break
        with open(self.summary_path, "w", encoding="utf-8") as f:
            f.writelines(rows)


class Tracer:
    @staticmethod
    def trace_low_energy(workflow_stats: dict[str, Any], k: int = 10) -> None:
        """Trace low-energy conformers through each workflow stage.

        Parameters
        ----------
        workflow_stats : dict
            The accumulated workflow statistics dictionary.
        k : int
            Number of top low-energy conformers to trace.
        """
        final_xyz = workflow_stats.get("final_output")
        if not final_xyz or not os.path.exists(final_xyz):
            return

        def _extract_energy(meta: dict[str, Any]) -> float | None:
            val = meta.get("G", meta.get("E", meta.get("Energy")))
            try:
                return float(val) if val is not None else None
            except (TypeError, ValueError):
                return None

        def _build_idx(xyz):
            confs = io_xyz.read_xyz_file(xyz, parse_metadata=True)
            io_xyz.ensure_conformer_cids(confs, prefix="trace")
            cid_map = {}
            e_rows = []
            for idx, c in enumerate(confs):
                cid = c.get("metadata", {}).get("CID")
                if not cid:
                    continue
                e = _extract_energy(c.get("metadata", {}))
                cid_map[str(cid)] = {"frame_index": idx, "energy": e}
                if e is not None:
                    e_rows.append((e, str(cid)))
            e_rows.sort()
            ranks = {cid: r for r, (e, cid) in enumerate(e_rows, 1)}
            return cid_map, ranks

        final_confs = io_xyz.read_xyz_file(final_xyz, parse_metadata=True)
        io_xyz.ensure_conformer_cids(final_confs, prefix="final")

        candidates = []
        for c in final_confs:
            e = _extract_energy(c.get("metadata", {}))
            cid = c.get("metadata", {}).get("CID")
            if e is not None and cid:
                candidates.append((e, str(cid)))

        candidates.sort()
        top_k_candidates = candidates[:k]

        step_outputs = [s for s in workflow_stats.get("steps", []) if s.get("output_xyz")]
        step_indexes = []
        for s in step_outputs:
            if os.path.exists(s.get("output_xyz", "")):
                cm, rk = _build_idx(s["output_xyz"])
                step_indexes.append({"step": s, "cid_map": cm, "ranks": rk})

        results = []
        for e_final, cid in top_k_candidates:
            trace = []
            for idx_info in step_indexes:
                info = idx_info["cid_map"].get(cid)
                if not info:
                    trace.append(
                        {"step_index": idx_info["step"].get("index", 0), "status": "missing"}
                    )
                else:
                    trace.append(
                        {
                            "step_index": idx_info["step"].get("index", 0),
                            "status": "found",
                            "energy": info["energy"],
                            "rank_by_energy": idx_info["ranks"].get(cid),
                        }
                    )
            results.append({"cid": cid, "final_energy": e_final, "trace": trace})

        workflow_stats["low_energy_trace"] = {
            "source_xyz": final_xyz,
            "top_k": len(results),
            "conformers": results,
        }
        return workflow_stats["low_energy_trace"]  # type: ignore[no-any-return]
