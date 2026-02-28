#!/usr/bin/env python3

"""Task manager (migrated from the legacy confflow/calc.py ChemTaskManager).

Compatibility goals:
- Call interface remains unchanged: ``ChemTaskManager(settings_file).run(input_xyz_file)``
- Results DB path / backup-restore / result.xyz output / auto_clean behaviour preserved.
"""

from __future__ import annotations

import configparser
import logging
import multiprocessing
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from ..core import io as io_xyz
from ..core import models
from ..core.cli_base import require_existing_path
from ..core.console import CalcProgressReporter, console, error
from ..core.contracts import ExitCode, cli_output_to_txt
from .analysis import _bond_length_from_xyz_lines, _parse_ts_bond_atoms
from .components.executor import _cleanup_lingering_processes
from .components.task_runner import TaskRunner
from .db.database import ResultsDB
from .policies import get_policy
from .resources import ResourceMonitor
from .setup import get_itask, parse_iprog, setup_logging

__all__ = [
    "ChemTaskManager",
    "main",
]

logger = logging.getLogger("confflow.calc.manager")


def _run_task(task_info: models.TaskContext | dict[str, Any]) -> dict[str, Any]:
    result = TaskRunner().run(task_info)
    return result if isinstance(result, dict) else {}


class ChemTaskManager:
    def __init__(
        self,
        settings_file: Any | None = None,
        resume_dir: str | None = None,
        settings: dict[str, Any] | None = None,
    ):
        if settings is not None:
            self.config = dict(settings)
        elif isinstance(settings_file, dict):
            self.config = dict(settings_file)
        elif settings_file and os.path.exists(settings_file):
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.optionxform = str
            cfg.read(settings_file)
            self.config = {
                k: v.strip('"') for sec in cfg.sections() for k, v in cfg.items(sec) if v
            }
            self.config.update({k: v.strip('"') for k, v in cfg.defaults().items() if v})
        else:
            self.config = {}

        self._default_work_dir = os.path.join(
            os.getcwd(), f"chem_tasks_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self.work_dir = resume_dir if resume_dir else self._default_work_dir
        self._work_dir_initialized = False

        self.backup_dir: str | None = None
        self.results_db: ResultsDB | None = None
        self._result_xyz_path: str | None = None
        self._job_meta_map: dict[str, dict] = {}
        self.monitor = (
            ResourceMonitor()
            if self.config.get("enable_dynamic_resources", "false").lower() == "true"
            else None
        )
        self.stop_requested = False

    def _ensure_work_dir(self):
        if self._work_dir_initialized:
            return
        os.makedirs(self.work_dir, exist_ok=True)
        setup_logging(self.work_dir)

        backup_dir_cfg = self.config.get("backup_dir")
        if backup_dir_cfg and str(backup_dir_cfg).strip():
            self.backup_dir = str(backup_dir_cfg).strip()
        else:
            self.backup_dir = os.path.join(self.work_dir, "backups")
            self.config["backup_dir"] = self.backup_dir
        os.makedirs(self.backup_dir, exist_ok=True)

        self.config["stop_beacon_file"] = os.path.join(self.work_dir, "STOP")
        self.results_db = ResultsDB(os.path.join(self.work_dir, "results.db"))
        self._work_dir_initialized = True

    def _read_single_frame_xyz_coords(self, xyz_path: str) -> list[str] | None:
        """Read the first frame coordinate list (with atom symbols) from an XYZ file."""
        confs = io_xyz.read_xyz_file_safe(xyz_path, parse_metadata=False)
        if not confs:
            return None
        conf = confs[0]
        return [f"{a} {x} {y} {z}" for a, (x, y, z) in zip(conf["atoms"], conf["coords"])]

    def _recover_result_from_backups(
        self, task: models.TaskContext | dict[str, Any]
    ) -> dict[str, Any] | None:
        """Attempt to recover a completed task result from the backup directory."""
        try:
            if not self.backup_dir or not os.path.isdir(self.backup_dir):
                return None

            if isinstance(task, models.TaskContext):
                job_name = task.job_name
                cfg = task.config or self.config
                task_dict = task.model_dump()
            else:
                job_name = task["job_name"]
                cfg = task.get("config", self.config)
                task_dict = task

            iprog = parse_iprog(cfg)
            try:
                policy = get_policy(iprog)
            except ValueError:
                return None

            log_path = os.path.join(self.backup_dir, f"{job_name}.{policy.log_ext}")
            xyz_path = os.path.join(self.backup_dir, f"{job_name}.xyz")

            parsed: dict[str, Any] = {}
            final_coords = None

            if os.path.exists(log_path) and policy.check_termination(log_path):
                is_sp_task = get_itask(cfg) == 1
                parsed = policy.parse_output(log_path, cfg, is_sp_task=is_sp_task) or {}
                final_coords = parsed.get("final_coords")

            if not final_coords and os.path.exists(xyz_path):
                final_coords = self._read_single_frame_xyz_coords(xyz_path)

            if not final_coords:
                return None

            itask = get_itask(cfg)
            e = parsed.get("e_low")
            g = parsed.get("g_low")
            eh = parsed.get("e_high")
            gc = parsed.get("g_corr")
            if itask in [2, 3, 4] and gc is None and e is not None and g is not None:
                gc = g - e

            final_val = g if g is not None else (eh if eh is not None else e)
            key = "final_gibbs_energy" if g is not None else "energy"
            result: dict[str, Any] = {
                **task_dict,
                "status": "success",
                key: final_val,
                "final_sp_energy": eh,
                "final_coords": final_coords,
                "num_imag_freqs": parsed.get("num_imag_freqs"),
                "lowest_freq": parsed.get("lowest_freq"),
                "g_corr": gc,
            }

            if itask == 4:
                ts_bond_atoms = cfg.get("ts_bond_atoms")
                pair = _parse_ts_bond_atoms(ts_bond_atoms)
                if pair:
                    result["ts_bond_atoms"] = f"{pair[0]},{pair[1]}"
                    bl = _bond_length_from_xyz_lines(final_coords, pair[0], pair[1])
                    if bl is not None:
                        result["ts_bond_length"] = bl

            return result
        except (OSError, ValueError, KeyError, AttributeError) as e:
            logger.debug(f"Recovery failed: {e}")
            return None

    def _read_xyz(self, f: str):
        """Read an input trajectory (XYZ), supporting multi-frame files with metadata."""
        conformers = io_xyz.read_xyz_file_safe(f, parse_metadata=True)
        return [
            {
                "title": conf["comment"],
                "coords": [
                    f"{a} {x} {y} {z}" for a, (x, y, z) in zip(conf["atoms"], conf["coords"])
                ],
                "metadata": conf.get("metadata", {}),
            }
            for conf in conformers
        ]

    # ------------------------------------------------------------------
    # Sub-methods split from run()
    # ------------------------------------------------------------------

    @staticmethod
    def _job_name_for_geom(i: int, g: dict[str, Any]) -> str:
        """Generate a job name from the index and the CID in metadata."""
        meta = g.get("metadata") or {}
        cid = meta.get("CID") if isinstance(meta, dict) else None
        if cid is None or str(cid).strip() == "":
            return f"A{i + 1:06d}"

        cid_raw = str(cid).strip()
        try:
            cid_int = int(cid_raw)
            if cid_int > 0:
                return f"A{cid_int:06d}"
        except (ValueError, TypeError):
            pass

        token = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_\-]+", "_", cid_raw)).strip("_")
        if not token:
            return f"A{i + 1:06d}"
        return token[:48] if len(token) > 48 else token

    def _build_task_list(self, geoms: list[dict[str, Any]]) -> list[models.TaskContext]:
        """Build a deduplicated task list from the conformer list."""
        tasks: list[models.TaskContext] = []
        used_names: dict[str, int] = {}
        for i, g in enumerate(geoms):
            job_name = self._job_name_for_geom(i, g)
            if job_name in used_names:
                used_names[job_name] += 1
                job_name = f"{job_name}_dup{used_names[job_name]}"
            else:
                used_names[job_name] = 0

            tasks.append(
                models.TaskContext(
                    job_name=job_name,
                    work_dir=os.path.join(self.work_dir, job_name),
                    coords=g.get("coords", []),
                    metadata=g.get("metadata", {}),
                    config=self.config,
                )
            )
        return tasks

    def _filter_pending(self, tasks: list[models.TaskContext]) -> list[models.TaskContext]:
        """Filter completed/recoverable tasks and return the list of pending ones."""
        assert self.results_db is not None
        todo: list[models.TaskContext] = []
        for t in tasks:
            res = self.results_db.get_result_by_job_name(t.job_name)
            if res and res.get("status") == "success":
                continue
            if str(self.config.get("resume_from_backups", "true")).lower() == "true":
                recovered = self._recover_result_from_backups(t)
                if recovered and recovered.get("status") == "success":
                    self.results_db.insert_result(recovered)
                    continue
            todo.append(t)
        return todo

    def _execute_tasks(self, todo: list[models.TaskContext]) -> None:
        """Dispatch tasks in serial or parallel mode."""
        assert self.results_db is not None
        if not todo:
            return

        report_every = max(1, len(todo) // 10)

        if len(todo) == 1:
            with CalcProgressReporter(total=1, report_every=1) as reporter:
                res = _run_task(todo[0].model_dump())
                self.results_db.insert_result(res)
                self._append_result(res)
                reporter.report(res.get("status", "failed"))
            return

        max_jobs = int(self.config.get("max_parallel_jobs", 4))
        with ProcessPoolExecutor(max_workers=max_jobs) as exc:
            futures = {exc.submit(_run_task, t.model_dump()): t for t in todo}
            with CalcProgressReporter(total=len(todo), report_every=report_every) as reporter:
                for fut in as_completed(futures):
                    if os.path.exists(self.config["stop_beacon_file"]):
                        self.stop_requested = True
                        break
                    res = fut.result()
                    self.results_db.insert_result(res)
                    self._append_result(res)
                    reporter.report(res.get("status", "failed"))

    def _handle_stop(self) -> bool:
        """Return True and clean up lingering processes if a stop signal was received."""
        if not self.stop_requested:
            return False
        iprog = parse_iprog(self.config)
        try:
            policy = get_policy(iprog)
        except ValueError:
            policy = None
        if policy:
            _cleanup_lingering_processes(self.config, policy)
        return True

    def _write_failed_xyz(
        self,
        failed: list[dict[str, Any]],
        tasks: list[models.TaskContext],
    ) -> None:
        """Write failed conformers to failed.xyz (using original input structures)."""
        if not failed:
            return
        job_meta_map = {tc.job_name: tc.metadata for tc in tasks}
        job_coords_map = {tc.job_name: tc.coords for tc in tasks}

        failed_file = os.path.join(self.work_dir, "failed.xyz")
        with open(failed_file, "w") as f:
            for res in failed:
                job_name = res.get("job_name")
                coords = job_coords_map.get(job_name) or []
                if not coords:
                    continue
                orig_meta = job_meta_map.get(job_name, {})
                cid = orig_meta.get("CID")
                err = (res.get("error") or "").strip()
                if len(err) > 200:
                    err = err[:200] + "..."
                info = f"Failed=1 Job={job_name}"
                if cid is not None and str(cid).strip() != "":
                    info += f" CID={cid}"
                if err:
                    info += f" Error={err}"
                f.write(f"{len(coords)}\n{info}\n" + "\n".join(coords) + "\n")

    @staticmethod
    def _format_result_comment(res: dict[str, Any], orig_meta: dict[str, Any]) -> str:
        """Build the XYZ comment line for a single successful result."""
        e_gibbs = res.get("final_gibbs_energy")
        e_sp = res.get("final_sp_energy")
        g_corr_res = res.get("g_corr")
        combined_to_g = (e_gibbs is not None) and (e_sp is not None) and (g_corr_res is not None)

        if combined_to_g:
            info = f"G={e_gibbs}"
        else:
            e_any = e_gibbs if e_gibbs is not None else res.get("energy")
            info = f"Energy={e_any}"

        cid = orig_meta.get("CID")
        if cid is not None and str(cid).strip() != "":
            info += f" CID={cid}"

        if not combined_to_g:
            g_corr = g_corr_res
            if g_corr is None:
                g_corr = orig_meta.get("G_corr")
            if g_corr is not None:
                info += f" G_corr={g_corr}"

        imag = res.get("num_imag_freqs")
        if imag is None:
            imag = orig_meta.get("Imag") or orig_meta.get("num_imag_freqs")
        if imag is not None:
            info += f" Imag={imag}"

        if res.get("lowest_freq") is not None:
            info += f" LowestFreq={res['lowest_freq']:.1f}"
        if res.get("ts_bond_atoms") is not None:
            info += f" TSAtoms={res['ts_bond_atoms']}"
        if res.get("ts_bond_length") is not None:
            info += f" TSBond={float(res['ts_bond_length']):.6f}"
        return info

    def _append_result(self, res: dict[str, Any]) -> None:
        """Append a single successful result to result.xyz immediately."""
        if res.get("status") not in ("success", "skipped"):
            return
        if not self._result_xyz_path:
            return
        coord_lines = res.get("final_coords")
        if not coord_lines:
            return
        orig_meta = self._job_meta_map.get(res.get("job_name", ""), {})
        comment = self._format_result_comment(res, orig_meta)
        io_xyz.append_xyz_conformer(self._result_xyz_path, coord_lines, comment)

    def _run_auto_clean(self, out_file: str) -> None:
        """Invoke external post-processing callback on result.xyz.

        The actual auto_clean logic has been migrated to the workflow layer
        (step_handlers).  ChemTaskManager only calls this method in standalone
        CLI mode, using a lazy import to keep the layers decoupled.
        """
        if self.config.get("auto_clean", "false").lower() != "true":
            return
        console.print("  Refine: ", end="")
        try:
            from ..blocks import refine  # Lazy import, only triggered when called directly via CLI

            opts_str = self.config.get("clean_opts", "-t 0.25")
            thresh, ewin, etol = self._parse_clean_opts(opts_str)
            task_cores = int(self.config.get("cores_per_task", 1))
            clean_args = refine.RefineOptions(
                input_file=out_file,
                output=os.path.join(os.path.dirname(out_file), "output.xyz"),
                threshold=thresh,
                ewin=ewin,
                energy_tolerance=etol,
                workers=task_cores,
            )
            refine.process_xyz(clean_args)
        except Exception as e:
            error(f"Refine auto-clean failed: {e}")

    @staticmethod
    def _parse_clean_opts(opts_str: str) -> tuple[float, float | None, float]:
        """Parse clean_opts string into (threshold, ewin, energy_tolerance).

        Uses shlex-based tokenization for robust flag parsing instead of
        fragile str.split() substring matching.
        """
        import shlex

        thresh = 0.25
        ewin: float | None = None
        etol = 0.05

        try:
            tokens = shlex.split(opts_str)
        except ValueError:
            tokens = opts_str.split()

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "-t" and i + 1 < len(tokens):
                try:
                    thresh = float(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok == "-ewin" and i + 1 < len(tokens):
                try:
                    ewin = float(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok == "--energy-tolerance" and i + 1 < len(tokens):
                try:
                    etol = float(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok.startswith("-t="):
                try:
                    thresh = float(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            elif tok.startswith("-ewin="):
                try:
                    ewin = float(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            elif tok.startswith("--energy-tolerance="):
                try:
                    etol = float(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            else:
                i += 1

        return thresh, ewin, etol

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, input_xyz_file: str) -> None:
        self._ensure_work_dir()
        assert self.results_db is not None

        try:
            geoms = self._read_xyz(input_xyz_file)
            tasks = self._build_task_list(geoms)

            # Build metadata map used by _append_result throughout this run
            self._job_meta_map = {tc.job_name: tc.metadata for tc in tasks}

            # Clear any stale result.xyz so the file reflects only THIS run's results
            self._result_xyz_path = os.path.join(self.work_dir, "result.xyz")
            try:
                os.remove(self._result_xyz_path)
            except FileNotFoundError:
                pass

            todo = self._filter_pending(tasks)

            # Immediately flush already-completed conformers (from DB / backup recovery)
            todo_names = {t.job_name for t in todo}
            for tc in tasks:
                if tc.job_name in todo_names:
                    continue
                done_res = self.results_db.get_result_by_job_name(tc.job_name)
                if done_res:
                    self._append_result(done_res)

            self._execute_tasks(todo)

            if self._handle_stop():
                return

            all_res = self.results_db.get_all_results()
            success = [r for r in all_res if r["status"] in ["success", "skipped"]]
            failed = [r for r in all_res if r.get("status") == "failed"]

            self._write_failed_xyz(failed, tasks)

            if not success:
                return

            out_file = self._result_xyz_path
            self._run_auto_clean(out_file)
        finally:
            try:
                if self.results_db:
                    self.results_db.close()
            except (OSError, AttributeError):
                pass


def main():
    multiprocessing.freeze_support()
    import argparse

    parser = argparse.ArgumentParser(
        description="ConfFlow Calc (v1.0) - quantum chemistry task executor",
        epilog="Example: confcalc search.xyz -s settings.ini",
    )
    parser.add_argument("input_xyz", help="Input XYZ trajectory")
    parser.add_argument("-s", "--settings", required=True, help="Path to INI settings file")
    args = parser.parse_args()

    try:
        require_existing_path(args.input_xyz, "Input file")
        require_existing_path(args.settings, "Settings file")
    except SystemExit as e:
        print(f"ERROR: {e}")
        raise SystemExit(ExitCode.USAGE_ERROR) from e

    with cli_output_to_txt(args.input_xyz):
        ChemTaskManager(args.settings).run(args.input_xyz)
    return ExitCode.SUCCESS


if __name__ == "__main__":
    main()
