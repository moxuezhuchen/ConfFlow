#!/usr/bin/env python3

"""Task execution helpers for ``ChemTaskManager``."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable

from ..core import models
from ..core.console import CalcProgressReporter
from .config_types import CalcTaskConfig, ensure_calc_task_config

logger = logging.getLogger("confflow.calc.manager")

__all__ = [
    "execute_tasks",
]


def _classify_future_exception(exc: Exception) -> str:
    if isinstance(exc, BrokenProcessPool):
        return "broken_process_pool"
    msg = str(exc).lower()
    if any(token in msg for token in ("pickle", "serialize", "serializ", "deserializ")):
        return "serialization_error"
    return "worker_exception"


def _future_done(fut: Any) -> bool:
    done_fn = getattr(fut, "done", None)
    if callable(done_fn):
        try:
            return bool(done_fn())
        except Exception:
            return False
    return False


def _future_cancelled(fut: Any) -> bool:
    cancelled_fn = getattr(fut, "cancelled", None)
    if callable(cancelled_fn):
        try:
            return bool(cancelled_fn())
        except Exception:
            return False
    return False


def execute_tasks(
    todo: list[models.TaskContext],
    config: CalcTaskConfig | dict[str, Any],
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

    calc_config = ensure_calc_task_config(config)

    report_every = max(1, len(todo) // 10)
    stop_file = calc_config.get("stop_beacon_file")

    def _task_payload(task: models.TaskContext) -> dict[str, Any]:
        payload = task.model_dump()
        payload["config"] = calc_config
        return payload

    if len(todo) == 1:
        if stop_requested_fn() or (stop_file and os.path.exists(stop_file)):
            set_stop_requested_fn(True)
            results_db.insert_result(
                {
                    "job_name": todo[0].job_name,
                    "status": "canceled",
                    "error": "STOP requested before task start",
                    "error_kind": "stop_requested",
                    "final_coords": None,
                }
            )
            return
        with progress_reporter_cls(total=1, report_every=1) as reporter:
            res = run_task_fn(_task_payload(todo[0]))
            results_db.insert_result(res)
            append_result_fn(res)
            reporter.report(res.get("status", "failed"))
        return

    max_jobs = int(calc_config.get("max_parallel_jobs", 4))
    with executor_cls(max_workers=max_jobs) as exc:
        futures = {exc.submit(run_task_fn, _task_payload(t)): t for t in todo}
        recorded: set[Any] = set()
        with progress_reporter_cls(total=len(todo), report_every=report_every) as reporter:
            for fut in as_completed_fn(futures):
                if stop_requested_fn() or (stop_file and os.path.exists(stop_file)):
                    set_stop_requested_fn(True)
                    exc.shutdown(wait=False, cancel_futures=True)
                    for other_fut, task in futures.items():
                        if other_fut in recorded:
                            continue
                        status = "pending" if _future_done(other_fut) else "canceled"
                        if _future_cancelled(other_fut):
                            status = "canceled"
                        results_db.insert_result(
                            {
                                "job_name": task.job_name,
                                "status": status,
                                "error": (
                                    "Task stopped before result collection"
                                    if status == "pending"
                                    else "STOP requested"
                                ),
                                "error_kind": "stop_requested",
                                "final_coords": None,
                            }
                        )
                        recorded.add(other_fut)
                        reporter.report(status)
                    break
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 – includes BrokenProcessPool
                    task = futures[fut]
                    error_kind = _classify_future_exception(e)
                    logger.warning(
                        "Task %s raised an unexpected exception: %s",
                        task.job_name,
                        e,
                    )
                    failed_result: dict[str, Any] = {
                        "job_name": task.job_name,
                        "status": "failed",
                        "error": str(e),
                        "error_kind": error_kind,
                        "final_coords": None,
                    }
                    results_db.insert_result(failed_result)
                    recorded.add(fut)
                    reporter.report("failed")
                    continue
                results_db.insert_result(res)
                recorded.add(fut)
                append_result_fn(res)
                reporter.report(res.get("status", "failed"))
