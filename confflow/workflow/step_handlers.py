#!/usr/bin/env python3

"""Workflow step handler functions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any

from ..blocks import confgen
from ..calc.executor import CalcExecutor
from ..calc.runner import CalcStepRequest, CalcStepRunner
from ..config.canonical import resolve_calc_step, resolve_global_options
from ..config.models import GlobalOptions
from ..core.exceptions import ConfFlowError
from ..core.io import iter_xyz_frames
from ..core.utils import get_logger, index_to_letter_prefix
from ..core.xyz_metadata import parse_comment_metadata, upsert_comment_kv
from ..shared.confgen_params import resolve_confgen_params
from ..shared.defaults import DEFAULT_MAX_PARALLEL_JOBS
from .composition import configure_default_refine
from .helpers import pushd
from .stats import FailureTracker
from .step_naming import build_step_dir_name_map

__all__ = [
    "StepContext",
    "StepExecutionResult",
    "run_confgen_step",
    "run_calc_step",
]

logger = get_logger()
_CONFGEN_SIGNATURE_FILE = ".confgen_signature"
_CONFGEN_SIGNATURE_PREFIX = "sha256:"


class ConfgenSignatureCompatibilityError(ConfFlowError):
    """An existing confgen signature cannot be safely interpreted for cleanup."""


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
    payload: list[dict[str, Any]] = []
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


def _build_confgen_run_kwargs(
    params: dict[str, Any],
    current_input: str | list[str],
    global_config: dict[str, Any] | None = None,
) -> dict:
    global_config = global_config or {}
    canonical = resolve_confgen_params(
        params,
        default_workers=global_config.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS),
    )
    return {
        "input_files": current_input,
        **canonical,
        "confirm": False,
        "collect_results": False,
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
    if not value:
        return None
    digest = value.removeprefix(_CONFGEN_SIGNATURE_PREFIX)
    if not value.startswith(_CONFGEN_SIGNATURE_PREFIX) or len(digest) != 64:
        raise ConfgenSignatureCompatibilityError(
            f"Unsupported confgen signature generation; refusing cleanup: {step_dir}"
        )
    if any(char not in "0123456789abcdef" for char in digest.lower()):
        raise ConfgenSignatureCompatibilityError(
            f"Malformed confgen signature; refusing cleanup: {step_dir}"
        )
    return value


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


def _confgen_input_paths(current_input: str | list[str]) -> list[str]:
    """Return the concrete XYZ paths represented by a step input."""
    paths = [current_input] if isinstance(current_input, str) else list(current_input)
    if not paths:
        raise ConfFlowError("confgen requires at least one XYZ input file")
    return [str(path) for path in paths]


def _validate_confgen_inputs(current_input: str | list[str]) -> list[int]:
    """Strictly validate every input and return its frame count.

    ConfGen's chemistry loader intentionally reads one structure because it is
    used for seed generation.  Workflow fan-in needs a different boundary: all
    frames in every source must be valid before a merged output can be adopted.
    Streaming the parser keeps this preflight bounded by one frame of memory.
    """
    frame_counts: list[int] = []
    for path in _confgen_input_paths(current_input):
        count = 0
        try:
            for _frame in iter_xyz_frames(path, parse_metadata=False, strict=True):
                count += 1
        except (OSError, ValueError) as exc:
            raise ConfFlowError(f"invalid confgen XYZ input {path}: {exc}") from exc
        if count == 0:
            raise ConfFlowError(f"confgen XYZ input contains no frames: {path}")
        frame_counts.append(count)
    return frame_counts


def _validate_confgen_output(path: str) -> bool:
    """Return whether a cached ConfGen output is a non-empty XYZ stream."""
    count = 0
    try:
        for _frame in iter_xyz_frames(path, parse_metadata=False, strict=True):
            count += 1
    except (OSError, ValueError):
        return False
    return count > 0


def _has_confgen_cid_conflict(path: str) -> bool:
    """Return whether one source repeats a conformer CID."""
    seen: set[str] = set()
    try:
        for frame in iter_xyz_frames(path, parse_metadata=True, strict=True):
            raw_cid = parse_comment_metadata(str(frame.get("comment", ""))).get("CID")
            if not isinstance(raw_cid, str) or not raw_cid.strip():
                continue
            cid = raw_cid.strip()
            if cid in seen:
                return True
            seen.add(cid)
    except (OSError, ValueError):
        # The strict preflight reports the useful input error.  Keep this
        # predicate conservative if a source changes between the two reads.
        return True
    return False


def _confgen_multi_frame_mode(current_input: str | list[str]) -> bool:
    """Return whether the current input contains one or more extra frames.

    This predicate deliberately operates on the current step input rather than
    the workflow's original input list.  A DAG merge can therefore enter the
    same pass-through mode as a single multi-frame source.
    """
    return any(count >= 2 for count in _validate_confgen_inputs(current_input))


def _write_xyz_frame(handle: Any, frame: dict[str, Any], comment: str) -> None:
    """Write one parsed XYZ frame without loading the complete trajectory."""
    atoms = frame.get("atoms", [])
    coords = frame.get("coords", [])
    if len(atoms) != len(coords):
        raise ConfFlowError(
            "confgen input frame has mismatched atom and coordinate counts "
            f"({len(atoms)} != {len(coords)})"
        )
    handle.write(f"{len(atoms)}\n{comment}\n")
    for atom, coord in zip(atoms, coords):
        try:
            x, y, z = (float(value) for value in coord)
        except (TypeError, ValueError) as exc:
            raise ConfFlowError("confgen input frame contains invalid coordinates") from exc
        # 17 significant digits round-trip an IEEE float while preserving the
        # source coordinate values more faithfully than the generator's display
        # formatter.  Element order is retained exactly as parsed.
        handle.write(f"{atom:<2s} {x:.17g} {y:.17g} {z:.17g}\n")


_LEGACY_NUMERIC_CID = re.compile(r"^\d+(?:\.0+)?$")


def _merged_frame_cid(
    comment: str,
    *,
    source_index: int,
    frame_index: int,
    used_cids: set[str],
) -> str:
    """Return a deterministic, unique CID while retaining existing IDs."""
    raw_cid = parse_comment_metadata(comment).get("CID")
    cid = str(raw_cid).strip() if isinstance(raw_cid, str) else ""
    if not cid or _LEGACY_NUMERIC_CID.fullmatch(cid) or cid in used_cids:
        prefix = index_to_letter_prefix(source_index)
        candidate = f"{prefix}{frame_index + 1:06d}"
        suffix = frame_index + 1
        while candidate in used_cids:
            suffix += 1
            candidate = f"{prefix}{suffix:06d}"
        cid = candidate
    used_cids.add(cid)
    return cid


def _publish_validated_xyz_inputs(
    current_input: str | list[str],
    expected_output: str,
    frame_counts: list[int],
) -> None:
    """Publish a validated single source or a streamed multi-source merge."""
    paths = _confgen_input_paths(current_input)
    temp_path = f"{expected_output}.tmp"
    try:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass

        rewrite_single = len(paths) == 1 and _has_confgen_cid_conflict(paths[0])
        if len(paths) == 1 and not rewrite_single:
            # Preserve a single source byte-for-byte, including comments and
            # coordinate precision.  It was fully validated before this copy.
            shutil.copy2(paths[0], temp_path)
        else:
            used_cids: set[str] = set()
            written = 0
            with open(temp_path, "w", encoding="utf-8") as handle:
                for source_index, path in enumerate(paths):
                    frame_index = 0
                    for frame in iter_xyz_frames(path, parse_metadata=True, strict=True):
                        comment = str(frame.get("comment", ""))
                        original_cid = parse_comment_metadata(comment).get("CID")
                        original_cid = (
                            str(original_cid).strip()
                            if isinstance(original_cid, str) and original_cid.strip()
                            else None
                        )
                        cid = _merged_frame_cid(
                            comment,
                            source_index=source_index,
                            frame_index=frame_index,
                            used_cids=used_cids,
                        )
                        if original_cid != cid:
                            comment = upsert_comment_kv(comment, "CID", cid)
                            # A collision or a missing/legacy CID is remapped
                            # to a source-scoped ID. Preserve the original
                            # identifier and source location in comment
                            # metadata so downstream records remain traceable.
                            if original_cid is not None:
                                comment = upsert_comment_kv(comment, "SourceCID", original_cid)
                            comment = upsert_comment_kv(
                                comment, "SourceFile", os.path.basename(path)
                            )
                            comment = upsert_comment_kv(comment, "SourceFrame", frame_index + 1)
                        _write_xyz_frame(handle, frame, comment)
                        frame_index += 1
                        written += 1
                    if frame_index != frame_counts[source_index]:
                        raise ConfFlowError(f"confgen input changed while merging: {path}")
            if written == 0:
                raise ConfFlowError("confgen merge produced no XYZ frames")

        os.replace(temp_path, expected_output)
    except Exception:
        try:
            os.remove(temp_path)
        except FileNotFoundError:
            pass
        raise


def run_confgen_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    input_files: list[str],
    global_config: dict[str, Any] | None = None,
) -> StepExecutionResult:
    """Execute a conformer generation step (execution adapter layer)."""
    expected_output = os.path.join(step_dir, "search.xyz")
    # Validate the complete current input before touching an existing output.
    # The old predicate looked at the original workflow input count, so a DAG
    # fan-in list was sent to the chemistry loader, which only reads frame one
    # from each file.
    frame_counts = _validate_confgen_inputs(current_input)
    multi_frame = any(count >= 2 for count in frame_counts)
    run_kwargs = _build_confgen_run_kwargs(params, current_input, global_config)
    signature = _compute_confgen_step_signature(
        current_input=current_input,
        input_files=input_files,
        run_kwargs=run_kwargs,
        multi_frame=multi_frame,
    )
    cleaned_stale_artifacts = False

    had_existing_artifact = os.path.exists(expected_output) or os.path.exists(
        _confgen_signature_path(step_dir)
    )
    if os.path.exists(expected_output):
        if _load_confgen_step_signature(step_dir) == signature:
            if _validate_confgen_output(expected_output):
                return StepExecutionResult(
                    output_path=expected_output,
                    reused_existing=True,
                    copied_multi_frame=multi_frame,
                )

    if multi_frame:
        _publish_validated_xyz_inputs(current_input, expected_output, frame_counts)
        _record_confgen_step_signature(step_dir, signature)
        return StepExecutionResult(
            output_path=expected_output,
            copied_multi_frame=True,
            cleaned_stale_artifacts=had_existing_artifact,
        )
    else:
        # Generation owns its own streaming temporary output.  Remove an
        # incompatible old artifact only after all input validation above has
        # succeeded, preserving it when preflight rejects a later frame.
        if had_existing_artifact:
            cleaned_stale_artifacts = _discard_confgen_artifacts(step_dir, expected_output)
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
    *,
    typed_global: GlobalOptions | None = None,
    calc_executor: CalcExecutor | None = None,
    cancel_beacon_file: str | None = None,
) -> StepExecutionResult:
    """Execute a calculation step via the typed calc runner."""
    if isinstance(current_input, list):
        if len(current_input) != 1:
            raise ConfFlowError(
                "Calc step requires exactly one input file; add a confgen step to merge "
                "multiple inputs before calc."
            )
        current_input = current_input[0]

    if typed_global is None:
        typed_global = resolve_global_options(global_config)
    calc_config = resolve_calc_step(
        params,
        typed_global,
        input_chk_dir=_resolve_chk_input_dir(params, root_dir, steps),
    )

    configure_default_refine()
    try:
        runner = (
            CalcStepRunner(calc_executor=calc_executor)
            if calc_executor is not None
            else CalcStepRunner()
        )
        result = runner.run(
            CalcStepRequest(
                step_name=step_name,
                step_dir=step_dir,
                input_xyz=current_input,
                config=calc_config,
                resume=False,
                cancel_beacon_file=cancel_beacon_file,
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
