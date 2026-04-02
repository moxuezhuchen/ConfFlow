#!/usr/bin/env python3

"""Task execution helpers for ``ChemTaskManager``."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any, Callable

from ..core import models
from ..core.console import CalcProgressReporter

logger = logging.getLogger("confflow.calc.manager")

__all__ = [
    "execute_tasks",
]


def execute_tasks(
    todo: list[models.TaskContext],
    config: dict[str, Any],
    results_db: Any,
    run_task_fn: Callable[[models.TaskContext | dict[str, Any]], dict[str, Any]],
    append_result_fn: Callable[[dict[str, Any]], None],
    stop_requested_fn: Callable[[], bool],
    set_stop_requested_fn: Callable[[bool], None],
    progress_reporter_cls: type[CalcProgressReporter] = CalcProgressReporter,
    executor_cls: type[ProcessPoolExecutor] = ProcessPoolExecutor,
    as_completed_fn: Callable[[Any], Any] = as_completed,
) -> None:
    """Dispatch tasks in serial or parallel mode."""
    if not todo:
        return

    report_every = max(1, len(todo) // 10)

    if len(todo) == 1:
        with progress_reporter_cls(total=1, report_every=1) as reporter:
            res = run_task_fn(todo[0].model_dump())
            results_db.insert_result(res)
            append_result_fn(res)
            reporter.report(res.get("status", "failed"))
        return

    max_jobs = int(config.get("max_parallel_jobs", 4))
    with executor_cls(max_workers=max_jobs) as exc:
        futures = {exc.submit(run_task_fn, t.model_dump()): t for t in todo}
        with progress_reporter_cls(total=len(todo), report_every=report_every) as reporter:
            for fut in as_completed_fn(futures):
                if stop_requested_fn() or os.path.exists(config["stop_beacon_file"]):
                    set_stop_requested_fn(True)
                    exc.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 – includes BrokenProcessPool
                    task = futures[fut]
                    logger.warning(
                        "Task %s raised an unexpected exception: %s",
                        task.job_name,
                        e,
                    )
                    failed_result: dict[str, Any] = {
                        "job_name": task.job_name,
                        "status": "failed",
                        "error": str(e),
                        "final_coords": None,
                    }
                    results_db.insert_result(failed_result)
                    reporter.report("failed")
                    continue
                results_db.insert_result(res)
                append_result_fn(res)
                reporter.report(res.get("status", "failed"))
