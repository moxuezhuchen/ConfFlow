#!/usr/bin/env python3

"""Workflow step handler functions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from ..blocks import confgen
from ..calc.runner import CalcStepRequest, CalcStepRunner
from ..config.models import CalcStepParams, GlobalOptions
from ..core.exceptions import ConfFlowError
from ..core.pairs import normalize_pair_list
from ..core.utils import get_logger
from ..shared.defaults import DEFAULT_MAX_PARALLEL_JOBS
from .helpers import as_list, is_multi_frame_any, pushd
from .step_naming import build_step_dir_name_map
from .stats import FailureTracker

__all__ = [
    "StepContext",
    "StepExecutionResult",
    "run_confgen_step",
    "run_calc_step",
]

logger = get_logger()
_CONFGEN_SIGNATURE_FILE = ".confgen_signature"
_CONFGEN_SIGNATURE_PREFIX = "sha256:"


@dataclass
class StepContext:
    """Encapsulates common parameters shared between step handler functions.

    Reduces the parameter count of ``run_calc_step`` from 8 positional
    arguments to a single context object, improving readability and
    making it easier to add new context fields in the future.
    """

    step_dir: str
    current_input: str | list[str]
    params: dict[str, Any]
    global_config: dict[str, Any] = field(default_factory=dict)
    root_dir: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    failure_tracker: FailureTracker | None = None
    step_name: str = ""


@dataclass(frozen=True)
class StepExecutionResult:
    """Explicit step result passed from handlers back to the workflow engine."""

    output_path: str
    failed_path: str | None = None
    reused_existing: bool = False
    copied_multi_frame: bool = False
    cleaned_stale_artifacts: bool = False


def _normalize_confgen_signature_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_confgen_signature_value(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_confgen_signature_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_confgen_signature_value(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _compute_input_signature(input_source: str | list[str]) -> str:
    paths = [input_source] if isinstance(input_source, str) else list(input_source)
    payload = []
    for path in paths:
        abspath = os.path.abspath(str(path))
        try:
            payload.append({"path": os.path.basename(abspath), "sha256": _file_sha256(abspath)})
        except OSError:
            payload.append({"path": os.path.basename(abspath), "missing": True})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _confgen_signature_path(step_dir: str) -> str:
    return os.path.join(step_dir, _CONFGEN_SIGNATURE_FILE)


def _resolve_confgen_workers(
    params: dict[str, Any],
    global_config: dict[str, Any] | None,
) -> int:
    global_config = global_config or {}
    raw_workers = params.get("workers")
    if raw_workers is None:
        raw_workers = params.get("max_workers")
    if raw_workers is None:
        raw_workers = params.get("max_parallel_jobs")
    if raw_workers is None:
        raw_workers = global_config.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS)
    try:
        workers = int(raw_workers)
    except (TypeError, ValueError) as exc:
        raise ConfFlowError(
            f"confgen workers must be an integer >= 1, got {raw_workers!r}"
        ) from exc
    if workers < 1:
        raise ConfFlowError(f"confgen workers must be an integer >= 1, got {raw_workers!r}")
    return workers


def _build_confgen_run_kwargs(
    params: dict[str, Any],
    current_input: str | list[str],
    global_config: dict[str, Any] | None = None,
) -> dict:
    return {
        "input_files": current_input,
        "angle_step": params.get("angle_step", 120),
        "bond_threshold": params.get("bond_multiplier", 1.15),
        "clash_threshold": 0.65,
        "add_bond": normalize_pair_list(params.get("add_bond")),
        "del_bond": normalize_pair_list(params.get("del_bond")),
        "no_rotate": normalize_pair_list(params.get("no_rotate")),
        "force_rotate": normalize_pair_list(params.get("force_rotate")),
        "optimize": params.get("optimize", False),
        "confirm": False,
        "chains": as_list(params.get("chains", params.get("chain"))),
        "chain_steps": as_list(params.get("chain_steps", params.get("steps"))),
        "chain_angles": as_list(params.get("chain_angles", params.get("angles"))),
        "rotate_side": params.get("rotate_side", "left"),
        "collect_results": False,
        "workers": _resolve_confgen_workers(params, global_config),
    }


def _compute_confgen_step_signature(
    *,
    current_input: str | list[str],
    input_files: list[str],
    run_kwargs: dict[str, Any],
    multi_frame: bool,
) -> str:
    payload = {
        "input_signature": _compute_input_signature(current_input),
        "input_files_signature": _compute_input_signature(input_files),
        "multi_frame": multi_frame,
        "run_kwargs": _normalize_confgen_signature_value(run_kwargs),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{_CONFGEN_SIGNATURE_PREFIX}{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _load_confgen_step_signature(step_dir: str) -> str | None:
    try:
        with open(_confgen_signature_path(step_dir), encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def _record_confgen_step_signature(step_dir: str, signature: str) -> None:
    path = _confgen_signature_path(step_dir)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(signature)
        handle.write("\n")
    os.replace(tmp_path, path)


def _discard_confgen_artifacts(step_dir: str, expected_output: str) -> bool:
    removed = False
    for path in (expected_output, _confgen_signature_path(step_dir)):
        try:
            os.remove(path)
            removed = True
        except FileNotFoundError:
            continue
    return removed


def run_confgen_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    input_files: list[str],
    global_config: dict[str, Any] | None = None,
) -> StepExecutionResult:
    """Execute a conformer generation step (execution adapter layer)."""
    expected_output = os.path.join(step_dir, "search.xyz")
    multi_frame = len(input_files) == 1 and is_multi_frame_any(current_input)
    run_kwargs = _build_confgen_run_kwargs(params, current_input, global_config)
    signature = _compute_confgen_step_signature(
        current_input=current_input,
        input_files=input_files,
        run_kwargs=run_kwargs,
        multi_frame=multi_frame,
    )
    cleaned_stale_artifacts = False

    if os.path.exists(expected_output):
        if _load_confgen_step_signature(step_dir) == signature:
            return StepExecutionResult(
                output_path=expected_output,
                reused_existing=True,
                copied_multi_frame=multi_frame,
            )
        cleaned_stale_artifacts = _discard_confgen_artifacts(step_dir, expected_output)

    if multi_frame and isinstance(current_input, str):
        shutil.copy2(current_input, expected_output)
        _record_confgen_step_signature(step_dir, signature)
        return StepExecutionResult(
            output_path=expected_output,
            copied_multi_frame=True,
            cleaned_stale_artifacts=cleaned_stale_artifacts,
        )
    else:
        with pushd(step_dir):
            confgen.run_generation(**run_kwargs)
        if not os.path.exists(expected_output):
            raise ConfFlowError("confgen did not produce search.xyz")
        _record_confgen_step_signature(step_dir, signature)
    return StepExecutionResult(
        output_path=expected_output,
        cleaned_stale_artifacts=cleaned_stale_artifacts,
    )


def _resolve_chk_input_dir(
    params: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
) -> str | None:
    chk_from = params.get("chk_from_step")
    if not chk_from:
        return None
    step_dirs, by_name = build_step_dir_name_map(steps)
    raw = str(chk_from).strip()
    from_dir = None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(step_dirs):
            from_dir = step_dirs[idx - 1]
    else:
        from_dir = by_name.get(raw)
    if from_dir is None:
        return None
    return os.path.join(root_dir, from_dir, "backups")


def run_calc_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str,
) -> StepExecutionResult:
    """Execute a calculation step via the typed calc runner."""
    if isinstance(current_input, list):
        if len(current_input) != 1:
            raise ConfFlowError(
                "Calc step requires exactly one input file; add a confgen step to merge "
                "multiple inputs before calc."
            )
        current_input = current_input[0]

    typed_global = GlobalOptions.from_mapping(global_config)
    calc_config = CalcStepParams.from_params(
        params,
        typed_global,
        input_chk_dir=_resolve_chk_input_dir(params, root_dir, steps),
    )

    try:
        result = CalcStepRunner().run(
            CalcStepRequest(
                step_name=step_name,
                step_dir=step_dir,
                input_xyz=current_input,
                config=calc_config,
                resume=False,
            )
        )
    except (RuntimeError, ValueError) as exc:
        if "did not produce an output XYZ file" in str(exc):
            raise ConfFlowError(str(exc)) from exc
        raise

    if result.cleaned_stale_artifacts:
        logger.warning(
            "Discarding stale calc artifacts in '%s' because the step state is incomplete or outdated.",
            step_dir,
        )
    if isinstance(current_input, list) and len(current_input) > 1:
        logger.warning(
            "Calc step received %d input files; using only '%s'. "
            "Add a confgen step to merge multiple inputs before calc.",
            len(current_input),
            current_input[0],
        )

    if result.failed_path is not None and failure_tracker is not None:
        failure_tracker.append(result.failed_path, step_name)

    if not os.path.exists(result.output_path):
        raise ConfFlowError("Calculation step did not produce an output XYZ file")

    return StepExecutionResult(
        output_path=result.output_path,
        failed_path=result.failed_path,
        reused_existing=result.reused,
        cleaned_stale_artifacts=result.cleaned_stale_artifacts,
    )
