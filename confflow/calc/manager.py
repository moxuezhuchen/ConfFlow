#!/usr/bin/env python3

"""Manage calculation tasks for a calc step or direct ``confcalc`` run."""

from __future__ import annotations

import configparser
import logging
import multiprocessing
import os
import re
import sys
from collections.abc import Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..core import io as io_xyz
from ..core import models
from ..core.cli_base import require_existing_path
from ..core.console import CalcProgressReporter, console, error
from ..core.contracts import ExitCode, cli_output_to_txt
from ..core.exceptions import ConfFlowError
from .analysis import _bond_length_from_xyz_lines, _parse_ts_bond_atoms
from .components.executor import _cleanup_lingering_processes
from .components.task_runner import TaskRunner
from .config_types import ensure_calc_task_config
from .db.database import ResultsDB
from .policies import get_policy
from .postprocess import run_refine_postprocess
from .resources import ResourceMonitor
from .result_writer import append_result, format_result_comment, write_failed_xyz
from .run_services import (
    ResultAssemblyService,
    TaskRecoveryService,
    TaskSourceBuilder,
    WorkDirService,
)
from .setup import get_itask, parse_iprog, setup_logging
from .step_contract import (
    canonicalize_calc_step_config,
    compute_calc_input_signature,
    prepare_calc_step_dir,
    record_calc_step_signature,
)
from .task_execution import execute_tasks

__all__ = [
    "CalcRunSummary",
    "ChemTaskManager",
    "format_all_failed_message",
    "main",
]

logger = logging.getLogger("confflow.calc.manager")
_LEGACY_NUMERIC_CID_RE = re.compile(r"^\d+(?:\.0+)?$")


@dataclass(frozen=True)
class CalcRunSummary:
    total_tasks: int
    success_count: int
    failed: list[dict[str, Any]]

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def all_tasks_failed(self) -> bool:
        return self.total_tasks > 0 and self.success_count == 0


def _first_failure_reason(failed: list[dict[str, Any]]) -> str:
    for result in failed:
        error_msg = str(result.get("error") or "").strip()
        if error_msg:
            return error_msg
    return "unknown error"


def format_all_failed_message(summary: CalcRunSummary) -> str:
    reason = _first_failure_reason(summary.failed)
    return (
        f"All calculation tasks failed ({summary.failed_count}/{summary.total_tasks}). "
        f"First failure: {reason}"
    )


def _is_enabled_flag(value: Any) -> bool:
    """Interpret common bool-like config values without assuming string input."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return str(value).strip().lower() == "true"


def _run_task(task_info: models.TaskContext | dict[str, Any]) -> dict[str, Any]:
    result = TaskRunner().run(task_info)
    return result if isinstance(result, dict) else {}


class ChemTaskManager:
    def __init__(
        self,
        settings_file: Any | None = None,
        resume_dir: str | None = None,
        settings: Mapping[str, Any] | None = None,
        execution_config: Mapping[str, Any] | None = None,
    ):
        raw_config: Mapping[str, Any]
        if settings is not None:
            raw_config = settings
        elif isinstance(settings_file, dict):
            raw_config = dict(settings_file)
        elif settings_file and os.path.exists(settings_file):
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.optionxform = str
            cfg.read(settings_file)
            raw_config = {k: v.strip('"') for sec in cfg.sections() for k, v in cfg.items(sec) if v}
            raw_config = dict(raw_config)
            raw_config.update({k: v.strip('"') for k, v in cfg.defaults().items() if v})
        else:
            raw_config = {}

        self.config = dict(raw_config)
        self.compat_config = self.config
        self.execution_config = (
            ensure_calc_task_config(execution_config) if execution_config is not None else None
        )

        self._default_work_dir = os.path.join(
            os.getcwd(), f"chem_tasks_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self.work_dir = resume_dir if resume_dir else self._default_work_dir
        self._work_dir_initialized = False

        self.backup_dir: str | None = None
        self.results_db: ResultsDB | None = None
        self._results_db_factory = lambda db_path: ResultsDB(db_path)
        self._result_xyz_path: str | None = None
        self._input_signature_override: str | None = None
        self._job_meta_map: dict[str, dict] = {}

        if "enable_dynamic_resources" in self.config:
            enable_dynamic = _is_enabled_flag(self.config["enable_dynamic_resources"])
        elif self.execution_config is not None:
            enable_dynamic = _is_enabled_flag(
                self.execution_config.get("enable_dynamic_resources", False)
            )
        else:
            enable_dynamic = False
        self.monitor = ResourceMonitor() if enable_dynamic else None
        self.stop_requested = False
        self._work_dir_service = WorkDirService(self)

    def _ensure_work_dir(self):
        self._work_dir_service.ensure_ready()

    def _require_results_db(self) -> ResultsDB:
        if self.results_db is None:
            raise ConfFlowError(
                "Results database is not initialized; call _ensure_work_dir() before using it."
            )
        return self.results_db

    def _compat_signature_config(self) -> dict[str, Any]:
        """Compatibility wrapper around the step-contract canonical boundary."""
        return canonicalize_calc_step_config(self.config, execution_config=self.execution_config)

    def _read_single_frame_xyz_coords(self, xyz_path: str) -> list[str] | None:
        """Read the first frame coordinate list (with atom symbols) from an XYZ file."""
        try:
            confs = io_xyz.read_xyz_file(xyz_path, parse_metadata=False, strict=True)
        except (OSError, ValueError):
            return None
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
                cfg = ensure_calc_task_config(task.config or self.config)
                task_dict = task.model_dump()
            else:
                job_name = task["job_name"]
                cfg = ensure_calc_task_config(task.get("config", self.config))
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

    def _read_xyz(self, f: str) -> list[dict[str, Any]]:
        """Read an input trajectory (XYZ), supporting multi-frame files with metadata."""
        try:
            conformers = io_xyz.read_xyz_file(f, parse_metadata=True, strict=False)
        except (OSError, ValueError):
            return []
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

    def _load_input_geometries(self, filepath: str) -> list[dict[str, Any]]:
        geoms = self._read_xyz(filepath)
        if not geoms:
            raise ValueError(f"no readable XYZ frames found in input: {filepath}")
        return geoms

    def _iter_input_geometries(self, filepath: str) -> Iterable[dict[str, Any]]:
        try:
            streamed = io_xyz.iter_xyz_frames(filepath, parse_metadata=True, strict=False)
            for conf in streamed:
                yield {
                    "title": conf["comment"],
                    "coords": [
                        f"{a} {x} {y} {z}" for a, (x, y, z) in zip(conf["atoms"], conf["coords"])
                    ],
                    "metadata": conf.get("metadata", {}),
                }
            return
        except (OSError, ValueError):
            pass

        yield from self._read_xyz(filepath)

    # ------------------------------------------------------------------
    # Sub-methods split from run()
    # ------------------------------------------------------------------

    @staticmethod
    def _job_name_for_geom(i: int, g: dict[str, Any]) -> str:
        """Generate a job name from the index and the CID in metadata."""
        meta = g.get("metadata") or {}
        cid = meta.get("CID") if isinstance(meta, dict) else None
        if not isinstance(cid, str):
            return f"A{i + 1:06d}"

        cid_raw = cid.strip()
        if not cid_raw or _LEGACY_NUMERIC_CID_RE.fullmatch(cid_raw):
            return f"A{i + 1:06d}"

        token = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_\-]+", "_", cid_raw)).strip("_")
        if not token:
            return f"A{i + 1:06d}"
        return token[:48] if len(token) > 48 else token

    def _build_task_list(self, geoms: list[dict[str, Any]]) -> list[models.TaskContext]:
        """Build a deduplicated task list from the conformer list."""
        builder = TaskSourceBuilder(
            work_dir=self.work_dir,
            config=self.config,
            iter_geometries_fn=lambda _path: geoms,
            job_name_fn=self._job_name_for_geom,
        )
        tasks, _ = builder.build_from_input("<in-memory>")
        return tasks

    def _filter_pending(self, tasks: list[models.TaskContext]) -> list[models.TaskContext]:
        """Filter completed/recoverable tasks and return the list of pending ones."""
        results_db = self._require_results_db()
        return TaskRecoveryService(
            results_db=results_db,
            config=self.config,
            recover_result_fn=self._recover_result_from_backups,
        ).filter_pending(tasks)

    def _execute_tasks(self, todo: list[models.TaskContext]) -> None:
        """Dispatch tasks in serial or parallel mode."""
        results_db = self._require_results_db()
        execute_tasks(
            todo=todo,
            config=self.config,
            results_db=results_db,
            run_task_fn=_run_task,
            append_result_fn=self._append_result,
            stop_requested_fn=lambda: self.stop_requested,
            set_stop_requested_fn=lambda value: setattr(self, "stop_requested", value),
            progress_reporter_cls=CalcProgressReporter,
            executor_cls=ProcessPoolExecutor,
            as_completed_fn=as_completed,
        )

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
        write_failed_xyz(self.work_dir, failed, tasks)

    @staticmethod
    def _format_result_comment(res: dict[str, Any], orig_meta: dict[str, Any]) -> str:
        """Build the XYZ comment line for a single successful result."""
        return format_result_comment(res, orig_meta)

    def _append_result(self, res: dict[str, Any]) -> None:
        """Append a single successful result to result.xyz immediately."""
        append_result(self._result_xyz_path, self._job_meta_map, res)

    def _resolve_effective_clean_opts(self) -> tuple[bool, str]:
        """Resolve effective auto-clean flag and opts (delegates to shared logic)."""
        from .step_contract import resolve_effective_auto_clean

        return resolve_effective_auto_clean(self.config, self.execution_config)

    def _run_auto_clean(self, out_file: str) -> None:
        """Invoke external post-processing callback on result.xyz.

        The actual auto_clean logic has been migrated to the workflow layer
        (step_handlers).  ChemTaskManager only calls this method in standalone
        CLI mode, using a lazy import to keep the layers decoupled.
        """
        enabled, opts_str = self._resolve_effective_clean_opts()
        if not enabled:
            return
        console.print("  Refine: ", end="")
        try:
            task_cores = int(self.config.get("cores_per_task", 1))
            clean_kwargs = self._parse_clean_opts(opts_str)
            clean_kwargs.setdefault("workers", task_cores)
            run_refine_postprocess(
                input_file=out_file,
                output_file=os.path.join(os.path.dirname(out_file), "output.xyz"),
                **clean_kwargs,
            )
        except (ImportError, OSError, TypeError, ValueError, RuntimeError) as e:
            error(f"Refine auto-clean failed: {e}")

    @staticmethod
    def _parse_clean_opts(opts_str: str) -> dict[str, Any]:
        """Parse clean_opts string into ``run_refine_postprocess`` keyword args.

        Uses shlex-based tokenization for robust flag parsing instead of
        fragile str.split() substring matching.
        """
        import shlex

        parsed: dict[str, Any] = {
            "threshold": 0.25,
            "ewin": None,
            "energy_tolerance": 0.05,
            "noH": False,
            "dedup_only": False,
            "keep_all_topos": False,
            "imag": None,
            "max_conformers": None,
        }

        try:
            tokens = shlex.split(opts_str)
        except ValueError:
            tokens = opts_str.split()

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == "-t" and i + 1 < len(tokens):
                try:
                    parsed["threshold"] = float(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok == "-ewin" and i + 1 < len(tokens):
                try:
                    parsed["ewin"] = float(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok == "--energy-tolerance" and i + 1 < len(tokens):
                try:
                    parsed["energy_tolerance"] = float(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok == "--imag" and i + 1 < len(tokens):
                try:
                    parsed["imag"] = int(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok in {"-n", "--max-conformers"} and i + 1 < len(tokens):
                try:
                    parsed["max_conformers"] = int(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok in {"-w", "--workers"} and i + 1 < len(tokens):
                try:
                    parsed["workers"] = int(tokens[i + 1])
                except (ValueError, TypeError):
                    pass
                i += 2
            elif tok == "--noH":
                parsed["noH"] = True
                i += 1
            elif tok == "--dedup-only":
                parsed["dedup_only"] = True
                i += 1
            elif tok == "--keep-all-topos":
                parsed["keep_all_topos"] = True
                i += 1
            elif tok.startswith("-t="):
                try:
                    parsed["threshold"] = float(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            elif tok.startswith("-ewin="):
                try:
                    parsed["ewin"] = float(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            elif tok.startswith("--energy-tolerance="):
                try:
                    parsed["energy_tolerance"] = float(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            elif tok.startswith("--imag="):
                try:
                    parsed["imag"] = int(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            elif tok.startswith("--max-conformers="):
                try:
                    parsed["max_conformers"] = int(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            elif tok.startswith("--workers="):
                try:
                    parsed["workers"] = int(tok.split("=", 1)[1])
                except (ValueError, IndexError):
                    pass
                i += 1
            else:
                i += 1

        return parsed

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, input_xyz_file: str) -> CalcRunSummary:
        self._ensure_work_dir()
        stop_path = os.path.join(self.work_dir, "STOP")
        try:
            input_signature = self._input_signature_override or compute_calc_input_signature(
                input_xyz_file
            )
            if not os.path.exists(stop_path):
                prepared = prepare_calc_step_dir(
                    self.work_dir,
                    self.config,
                    input_signature=input_signature,
                    execution_config=self.execution_config,
                )
                if prepared.cleaned_stale_artifacts:
                    logger.warning(
                        "Discarded stale calc artifacts in %s before starting the run.",
                        self.work_dir,
                    )
                    if self.results_db is not None:
                        self.results_db.close()
                    self.results_db = self._results_db_factory(
                        os.path.join(self.work_dir, "results.db")
                    )
                    setup_logging(self.work_dir)
            record_calc_step_signature(
                self.work_dir,
                self.config,
                input_signature=input_signature,
                execution_config=self.execution_config,
            )
            results_db = self._require_results_db()
            task_builder = TaskSourceBuilder(
                work_dir=self.work_dir,
                config=self.config,
                iter_geometries_fn=self._iter_input_geometries,
                job_name_fn=self._job_name_for_geom,
            )
            tasks, self._job_meta_map = task_builder.build_from_input(input_xyz_file)
            total_tasks = len(tasks)

            assembly = ResultAssemblyService(
                work_dir=self.work_dir,
                results_db=results_db,
                job_meta_map=self._job_meta_map,
                append_result_fn=self._append_result,
            )
            self._result_xyz_path = assembly.reset_result_xyz()

            todo = self._filter_pending(tasks)
            assembly.flush_completed_results(tasks, todo)

            self._execute_tasks(todo)

            if self._handle_stop():
                return CalcRunSummary(total_tasks, 0, [])

            success_count, failed = assembly.collect_outcomes()
            assembly.write_failed_xyz(failed, tasks)
            summary = CalcRunSummary(total_tasks, success_count, failed)

            if success_count == 0:
                return summary

            out_file = self._result_xyz_path
            self._run_auto_clean(out_file)
            return summary
        finally:
            self._input_signature_override = None
            try:
                if self.results_db:
                    self.results_db.close()
            except (OSError, AttributeError):
                pass


def main():
    multiprocessing.freeze_support()
    import argparse

    parser = argparse.ArgumentParser(
        description="Run quantum-chemistry calculation tasks for an XYZ trajectory",
        epilog="Example: confcalc search.xyz -s settings.ini",
    )
    parser.add_argument("input_xyz", help="Path to the input XYZ trajectory")
    parser.add_argument("-s", "--settings", required=True, help="Path to the INI settings file")
    args = parser.parse_args()

    try:
        require_existing_path(args.input_xyz, "Input file")
        require_existing_path(args.settings, "Settings file")
    except SystemExit as e:
        print(f"Error: {e}")
        raise SystemExit(ExitCode.USAGE_ERROR) from e

    try:
        with cli_output_to_txt(args.input_xyz):
            summary = ChemTaskManager(args.settings).run(args.input_xyz)
    except (configparser.Error, ConfFlowError, OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return ExitCode.RUNTIME_ERROR
    if isinstance(summary, CalcRunSummary) and summary.all_tasks_failed:
        print(format_all_failed_message(summary), file=sys.stderr)
        return ExitCode.RUNTIME_ERROR
    return ExitCode.SUCCESS


if __name__ == "__main__":
    main()
