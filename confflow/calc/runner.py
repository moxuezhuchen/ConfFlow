#!/usr/bin/env python3

"""Calc step runner for the non-legacy workflow path."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from ..blocks.refine.result import RefineResult
from ..config.models import CalcStepParams, load_workflow_model
from ..core import io as io_xyz
from ..core import models
from ..core.console import CalcProgressReporter
from ..core.exceptions import ConfFlowError
from ..core.path_policy import resolve_sandbox_root, validate_managed_path
from .artifacts import CalcArtifactManager
from .components.task_runner import TaskRunner
from .db.database import ResultsDB
from .postprocess import run_refine_postprocess
from .result_writer import append_result
from .run_services import ResultAssemblyService, TaskRecoveryService, TaskSourceBuilder
from .setup import setup_logging
from .task_execution import execute_tasks


@dataclass(frozen=True)
class CalcStepRequest:
    step_name: str
    step_dir: str
    input_xyz: str
    config: CalcStepParams
    resume: bool = False


@dataclass(frozen=True)
class CalcStepResult:
    output_path: str
    failed_path: str | None
    total_tasks: int
    succeeded: int
    failed: int
    reused: bool = False
    cleaned_stale_artifacts: bool = False


class CalcStepRunner:
    """Run one calc step using typed config and manifest artifacts."""

    def __init__(self) -> None:
        self.stop_requested = False

    @staticmethod
    def _job_name_for_geom(i: int, geom: dict) -> str:
        meta = geom.get("metadata") or {}
        cid = meta.get("CID") if isinstance(meta, dict) else None
        if isinstance(cid, str) and cid.strip() and not cid.strip().replace(".", "").isdigit():
            import re

            token = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_\\-]+", "_", cid.strip())).strip("_")
            if token:
                return token[:48]
        return f"A{i + 1:06d}"

    @staticmethod
    def _iter_input_geometries(filepath: str):
        for conf in io_xyz.iter_xyz_frames(filepath, parse_metadata=True, strict=False):
            yield {
                "title": conf["comment"],
                "coords": [
                    f"{atom} {x} {y} {z}" for atom, (x, y, z) in zip(conf["atoms"], conf["coords"])
                ],
                "metadata": conf.get("metadata", {}),
            }

    @staticmethod
    def _run_task(task_info: models.TaskContext | dict) -> dict:
        result = TaskRunner().run(task_info)
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _count_failed(rows: list[dict]) -> int:
        return sum(1 for row in rows if row.get("status") in {"failed", "canceled", "pending"})

    @staticmethod
    def _recover_disabled(task: models.TaskContext | dict) -> dict | None:
        del task
        return None

    def run(self, request: CalcStepRequest) -> CalcStepResult:
        step_dir = Path(request.step_dir)
        artifact_manager = CalcArtifactManager(
            step_dir,
            step_name=request.step_name,
            config=request.config,
            input_path=request.input_xyz,
        )
        prepared = artifact_manager.prepare(resume=request.resume)
        if prepared.reusable_output is not None:
            failed = step_dir / "failed.xyz"
            return CalcStepResult(
                output_path=str(prepared.reusable_output),
                failed_path=str(failed) if failed.exists() else None,
                total_tasks=0,
                succeeded=0,
                failed=0,
                reused=True,
                cleaned_stale_artifacts=prepared.cleaned_stale_artifacts,
            )

        runtime_config = request.config.to_runtime_dict()
        sandbox_root = resolve_sandbox_root(runtime_config)
        validated_step_dir = validate_managed_path(
            str(step_dir),
            label="work_dir",
            sandbox_root=sandbox_root,
        )
        step_dir = Path(validated_step_dir)
        step_dir.mkdir(parents=True, exist_ok=True)
        setup_logging(str(step_dir))

        backup_dir = step_dir / "backups"
        runtime_config["backup_dir"] = str(
            validate_managed_path(str(backup_dir), label="backup_dir", sandbox_root=sandbox_root)
        )
        runtime_config["stop_beacon_file"] = str(step_dir / "STOP")
        backup_dir.mkdir(parents=True, exist_ok=True)

        db = ResultsDB(str(step_dir / "results.db"))
        result_xyz_path: str | None = None
        try:
            artifact_manager.mark_running()
            builder = TaskSourceBuilder(
                work_dir=str(step_dir),
                config=runtime_config,
                iter_geometries_fn=self._iter_input_geometries,
                job_name_fn=self._job_name_for_geom,
            )
            tasks, job_meta_map = builder.build_from_input(request.input_xyz)
            result_xyz_path = str(step_dir / "result.xyz")

            def append(res: dict) -> None:
                append_result(result_xyz_path, job_meta_map, res)

            assembly = ResultAssemblyService(
                work_dir=str(step_dir),
                results_db=db,
                job_meta_map=job_meta_map,
                append_result_fn=append,
            )
            result_xyz_path = assembly.reset_result_xyz()
            recovery = TaskRecoveryService(
                results_db=db,
                config=runtime_config,
                recover_result_fn=self._recover_disabled,
            )
            todo = recovery.filter_pending(tasks)
            assembly.flush_completed_results(tasks, todo)
            execute_tasks(
                todo=todo,
                config=runtime_config,
                results_db=db,
                run_task_fn=self._run_task,
                append_result_fn=append,
                stop_requested_fn=lambda: self.stop_requested,
                set_stop_requested_fn=lambda value: setattr(self, "stop_requested", value),
                progress_reporter_cls=CalcProgressReporter,
                executor_cls=ProcessPoolExecutor,
                as_completed_fn=as_completed,
            )
            succeeded, failed_rows = assembly.collect_outcomes()
            assembly.write_failed_xyz(failed_rows, tasks)
            failed_path = step_dir / "failed.xyz"
            failed_count = self._count_failed(failed_rows)

            output_path = Path(result_xyz_path)
            if succeeded > 0 and request.config.cleanup.enabled:
                clean_output = step_dir / "output.xyz"
                clean_result = run_refine_postprocess(
                    input_file=str(output_path),
                    output_file=str(clean_output),
                    **request.config.cleanup.to_clean_kwargs(
                        workers=request.config.resources.cores_per_task
                    ),
                )
                if isinstance(clean_result, RefineResult) and clean_result.kept_count > 0:
                    output_path = clean_output
                elif clean_output.exists():
                    output_path = clean_output

            if not output_path.exists():
                raise ConfFlowError("Calculation step did not produce an output XYZ file")

            artifact_manager.mark_completed(
                output_path=str(output_path),
                failed_path=str(failed_path) if failed_path.exists() else None,
                total_tasks=len(tasks),
                succeeded=succeeded,
                failed_count=failed_count,
            )
            return CalcStepResult(
                output_path=str(output_path),
                failed_path=str(failed_path) if failed_path.exists() else None,
                total_tasks=len(tasks),
                succeeded=succeeded,
                failed=failed_count,
                cleaned_stale_artifacts=prepared.cleaned_stale_artifacts,
            )
        except Exception as exc:
            artifact_manager.mark_failed(str(exc))
            raise
        finally:
            db.close()


def main() -> int:
    """Standalone calc step CLI using workflow YAML calc step definitions."""
    import argparse

    parser = argparse.ArgumentParser(description="Run one ConfFlow calc step")
    parser.add_argument("input_xyz", help="Path to the input XYZ trajectory")
    parser.add_argument("-c", "--config", required=True, help="Workflow YAML configuration")
    parser.add_argument(
        "--step",
        help="Calc step name or 1-based index among calc steps (default: first calc step)",
    )
    parser.add_argument("-w", "--work-dir", help="Output step directory")
    args = parser.parse_args()

    workflow = load_workflow_model(args.config)
    calc_steps = [step for step in workflow.steps if step.type == "calc"]
    if not calc_steps:
        raise SystemExit("No calc step found in workflow config")

    selected = calc_steps[0]
    if args.step:
        raw = str(args.step).strip()
        if raw.isdigit():
            idx = int(raw)
            if idx < 1 or idx > len(calc_steps):
                raise SystemExit(f"Calc step index out of range: {idx}")
            selected = calc_steps[idx - 1]
        else:
            matches = [step for step in calc_steps if step.name == raw]
            if not matches:
                raise SystemExit(f"Calc step not found: {raw}")
            selected = matches[0]

    step_dir = args.work_dir or f"{Path(args.input_xyz).stem}_{selected.name}"
    config = CalcStepParams.from_params(selected.params, workflow.global_options)
    result = CalcStepRunner().run(
        CalcStepRequest(
            step_name=selected.name,
            step_dir=step_dir,
            input_xyz=args.input_xyz,
            config=config,
        )
    )
    print(f"Output: {result.output_path}")
    if result.failed_path:
        print(f"Failed: {result.failed_path}")
    print(
        "Summary: "
        f"total={result.total_tasks}, succeeded={result.succeeded}, failed={result.failed}"
    )
    return 0
