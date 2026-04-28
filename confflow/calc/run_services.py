#!/usr/bin/env python3

"""Internal services used by ``ChemTaskManager``."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from typing import Any

from ..core import models
from ..core.path_policy import resolve_sandbox_root, validate_managed_path
from .config_types import CalcTaskConfig, ensure_calc_task_config
from .result_writer import write_failed_xyz
from .setup import setup_logging

__all__ = [
    "WorkDirService",
    "TaskSourceBuilder",
    "TaskRecoveryService",
    "ResultAssemblyService",
]


class WorkDirService:
    """Prepare and validate the calc working directory."""

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def ensure_ready(self) -> None:
        if self.manager._work_dir_initialized:
            return
        sandbox_root = resolve_sandbox_root(self.manager.config)
        self.manager.work_dir = validate_managed_path(
            self.manager.work_dir,
            label="work_dir",
            sandbox_root=sandbox_root,
        )
        os.makedirs(self.manager.work_dir, exist_ok=True)
        setup_logging(self.manager.work_dir)

        backup_dir_cfg = self.manager.config.get("backup_dir")
        if backup_dir_cfg and str(backup_dir_cfg).strip():
            self.manager.backup_dir = validate_managed_path(
                str(backup_dir_cfg).strip(),
                label="backup_dir",
                sandbox_root=sandbox_root,
            )
        else:
            self.manager.backup_dir = os.path.join(self.manager.work_dir, "backups")
            self.manager.config["backup_dir"] = self.manager.backup_dir
        os.makedirs(self.manager.backup_dir, exist_ok=True)

        self.manager.config["stop_beacon_file"] = os.path.join(self.manager.work_dir, "STOP")
        self.manager.results_db = self.manager._results_db_factory(
            os.path.join(self.manager.work_dir, "results.db")
        )
        self.manager._work_dir_initialized = True


class TaskSourceBuilder:
    """Create task contexts from input XYZ geometries."""

    def __init__(
        self,
        *,
        work_dir: str,
        config: CalcTaskConfig | dict[str, Any],
        iter_geometries_fn: Callable[[str], Iterable[dict[str, Any]]],
        job_name_fn: Callable[[int, dict[str, Any]], str],
    ) -> None:
        self.work_dir = work_dir
        self.config = ensure_calc_task_config(config)
        self.iter_geometries_fn = iter_geometries_fn
        self.job_name_fn = job_name_fn

    def build_from_input(
        self,
        input_xyz_file: str,
    ) -> tuple[list[models.TaskContext], dict[str, dict[str, Any]]]:
        tasks: list[models.TaskContext] = []
        job_meta_map: dict[str, dict[str, Any]] = {}
        used_names: dict[str, int] = {}

        for i, geom in enumerate(self.iter_geometries_fn(input_xyz_file)):
            job_name = self.job_name_fn(i, geom)
            if job_name in used_names:
                used_names[job_name] += 1
                job_name = f"{job_name}_dup{used_names[job_name]}"
            else:
                used_names[job_name] = 0
            task = models.TaskContext(
                job_name=job_name,
                work_dir=os.path.join(self.work_dir, job_name),
                coords=geom.get("coords", []),
                metadata=geom.get("metadata", {}),
                config=self.config,
            )
            tasks.append(task)
            job_meta_map[job_name] = task.metadata

        if not tasks:
            raise ValueError(f"no readable XYZ frames found in input: {input_xyz_file}")
        return tasks, job_meta_map


class TaskRecoveryService:
    """Resolve which tasks still need execution."""

    def __init__(
        self,
        *,
        results_db: Any,
        config: CalcTaskConfig | dict[str, Any],
        recover_result_fn: Callable[[models.TaskContext | dict[str, Any]], dict[str, Any] | None],
    ) -> None:
        self.results_db = results_db
        self.config = ensure_calc_task_config(config)
        self.recover_result_fn = recover_result_fn

    def filter_pending(self, tasks: list[models.TaskContext]) -> list[models.TaskContext]:
        todo: list[models.TaskContext] = []
        resume_from_backups = str(self.config.get("resume_from_backups", "true")).lower() == "true"
        for task in tasks:
            res = self.results_db.get_result_by_job_name(task.job_name)
            if res and res.get("status") == "success":
                continue
            if resume_from_backups:
                recovered = self.recover_result_fn(task)
                if recovered and recovered.get("status") == "success":
                    self.results_db.insert_result(recovered)
                    continue
            todo.append(task)
        return todo


class ResultAssemblyService:
    """Assemble result/failed XYZ files from DB and in-memory task metadata."""

    def __init__(
        self,
        *,
        work_dir: str,
        results_db: Any,
        job_meta_map: dict[str, dict[str, Any]],
        append_result_fn: Callable[[dict[str, Any]], None],
    ) -> None:
        self.work_dir = work_dir
        self.results_db = results_db
        self.job_meta_map = job_meta_map
        self.append_result_fn = append_result_fn
        self.result_xyz_path = os.path.join(work_dir, "result.xyz")

    def reset_result_xyz(self) -> str:
        try:
            os.remove(self.result_xyz_path)
        except FileNotFoundError:
            pass
        return self.result_xyz_path

    def flush_completed_results(
        self,
        tasks: list[models.TaskContext],
        todo: list[models.TaskContext],
    ) -> None:
        todo_names = {task.job_name for task in todo}
        for task in tasks:
            if task.job_name in todo_names:
                continue
            done_res = self.results_db.get_result_by_job_name(task.job_name)
            if done_res:
                self.append_result_fn(done_res)

    def collect_outcomes(self) -> tuple[int, list[dict[str, Any]]]:
        success_count = 0
        failed: list[dict[str, Any]] = []
        result_iter = (
            self.results_db.iter_all_results()
            if hasattr(self.results_db, "iter_all_results")
            else iter(self.results_db.get_all_results())
        )
        for result in result_iter:
            if result["status"] in ["success", "skipped"]:
                success_count += 1
            elif result.get("status") == "failed":
                failed.append(result)
        return success_count, failed

    def write_failed_xyz(
        self, failed: list[dict[str, Any]], tasks: list[models.TaskContext]
    ) -> None:
        write_failed_xyz(self.work_dir, failed, tasks)
