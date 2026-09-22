#!/usr/bin/env python3

"""Read-only validation of workflow artifacts reused by strict resume."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..calc.artifacts import (
    CalcArtifactManager,
    CalcManifestCompatibilityError,
    compute_config_digest,
    compute_input_digest,
)
from ..config.canonical import resolve_calc_step
from ..config.models import GlobalOptions
from ..core.utils import validate_xyz_file
from .step_handlers import (
    ConfgenSignatureCompatibilityError,
    _build_confgen_run_kwargs,
    _compute_confgen_step_signature,
    _confgen_multi_frame_mode,
    _load_confgen_step_signature,
    _resolve_chk_input_dir,
)

__all__ = [
    "ResumeArtifactCompatibilityError",
    "validate_reusable_artifact",
]


class ResumeArtifactCompatibilityError(RuntimeError):
    """A strict-resume artifact cannot be safely reused."""


def _artifact_path(path: str, *, root_dir: str, step_dir: str, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(step_dir) / candidate
    resolved = candidate.resolve()
    root = Path(root_dir).resolve()
    step_root = Path(step_dir).resolve()
    try:
        resolved.relative_to(root)
        resolved.relative_to(step_root)
    except ValueError as exc:
        raise ResumeArtifactCompatibilityError(
            f"{label} is outside its workflow step directory: {path}"
        ) from exc
    if not resolved.is_file():
        raise ResumeArtifactCompatibilityError(f"{label} is not a file: {path}")
    return resolved


def _validate_xyz(path: str, *, root_dir: str, step_dir: str, label: str) -> Path:
    resolved = _artifact_path(path, root_dir=root_dir, step_dir=step_dir, label=label)
    try:
        valid, frames = validate_xyz_file(str(resolved), strict=True)
    except Exception as exc:  # noqa: BLE001 - normalize parser failures for resume callers
        raise ResumeArtifactCompatibilityError(
            f"{label} is not a valid XYZ file: {path} ({exc})"
        ) from exc
    if not valid or not frames:
        raise ResumeArtifactCompatibilityError(
            f"{label} is empty or contains no valid XYZ frames: {path}"
        )
    return resolved


def _validate_confgen_signature(
    *,
    step_dir: str,
    output_path: str,
    current_input: str | list[str],
    params: dict[str, Any],
    input_files: list[str],
    global_config: dict[str, Any],
) -> None:
    signature_path = Path(step_dir) / ".confgen_signature"
    if not signature_path.exists():
        # A bound workflow state has no trustworthy way to associate an
        # unmarked search.xyz with its input/configuration.  The normal
        # ConfGen handler also treats a missing signature as stale, so strict
        # resume fails closed here.  Pre-signature checkpoint-only directories
        # are outside the supported bound-state compatibility contract.
        raise ResumeArtifactCompatibilityError(f"confgen signature is missing: {signature_path}")
    if not signature_path.is_file():
        raise ResumeArtifactCompatibilityError(f"confgen signature is not a file: {signature_path}")
    try:
        actual = _load_confgen_step_signature(step_dir)
    except ConfgenSignatureCompatibilityError as exc:
        raise ResumeArtifactCompatibilityError(str(exc)) from exc
    if actual is None:
        raise ResumeArtifactCompatibilityError(f"confgen signature is empty: {signature_path}")
    try:
        expected = _compute_confgen_step_signature(
            current_input=current_input,
            input_files=input_files,
            run_kwargs=_build_confgen_run_kwargs(params, current_input, global_config),
            multi_frame=_confgen_multi_frame_mode(current_input),
        )
    except (OSError, ValueError, TypeError) as exc:
        raise ResumeArtifactCompatibilityError(
            f"cannot compute current confgen signature for {output_path}: {exc}"
        ) from exc
    if actual != expected:
        raise ResumeArtifactCompatibilityError(
            f"confgen signature does not match current inputs or configuration: {signature_path}"
        )


def _validate_calc_manifest(
    *,
    step_dir: str,
    step_name: str,
    output_path: str,
    current_input: str | list[str],
    params: dict[str, Any],
    typed_global: GlobalOptions,
    root_dir: str,
    steps: list[dict[str, Any]],
) -> None:
    manifest_path = Path(step_dir) / "manifest.json"
    if not manifest_path.exists():
        # CalcArtifactManager's resume contract requires a schema-versioned
        # manifest before it can adopt output.  Do not recreate or infer one
        # during validation, since that would turn an unbound historical
        # directory into a falsely verified result.
        raise ResumeArtifactCompatibilityError(f"calc manifest is missing: {manifest_path}")
    if not manifest_path.is_file():
        raise ResumeArtifactCompatibilityError(f"calc manifest is not a file: {manifest_path}")

    if isinstance(current_input, str):
        input_path = current_input
    elif len(current_input) == 1:
        input_path = current_input[0]
    else:
        raise ResumeArtifactCompatibilityError(
            "calc step has no single input for manifest validation"
        )
    try:
        config = resolve_calc_step(
            params,
            typed_global,
            input_chk_dir=_resolve_chk_input_dir(params, root_dir, steps),
        )
        manager = CalcArtifactManager(
            step_dir,
            step_name=step_name,
            config=config,
            input_path=input_path,
        )
        manifest = manager.load()
    except (CalcManifestCompatibilityError, OSError, ValueError, TypeError) as exc:
        raise ResumeArtifactCompatibilityError(
            f"calc manifest cannot be safely interpreted: {manifest_path} ({exc})"
        ) from exc
    if manifest is None:
        raise ResumeArtifactCompatibilityError(f"calc manifest is missing: {manifest_path}")
    if manifest.step_name != step_name:
        raise ResumeArtifactCompatibilityError(
            f"calc manifest was written for step '{manifest.step_name}', not '{step_name}'"
        )
    if manifest.step_type != "calc":
        raise ResumeArtifactCompatibilityError(
            f"calc manifest has unsupported step type {manifest.step_type!r}"
        )
    if manifest.config_digest != compute_config_digest(config):
        raise ResumeArtifactCompatibilityError(
            "calc manifest config digest did not match current configuration"
        )
    if manifest.input_digest != compute_input_digest(input_path):
        raise ResumeArtifactCompatibilityError(
            "calc manifest input digest did not match current input"
        )
    if manifest.status != "completed":
        raise ResumeArtifactCompatibilityError(
            f"calc manifest is not complete (status={manifest.status}); refusing to reuse output"
        )
    if not manifest.output:
        raise ResumeArtifactCompatibilityError("completed calc manifest has no output path")
    manifest_output = _artifact_path(
        manifest.output,
        root_dir=step_dir,
        step_dir=step_dir,
        label="calc manifest output",
    )
    state_output = _artifact_path(
        output_path,
        root_dir=step_dir,
        step_dir=step_dir,
        label="saved calc output",
    )
    if manifest_output != state_output:
        raise ResumeArtifactCompatibilityError(
            "calc manifest output does not match the saved workflow output"
        )


def validate_reusable_artifact(
    *,
    output_path: str,
    root_dir: str,
    step_dir: str,
    step_name: str,
    step_type: str,
    current_input: str | list[str],
    input_files: list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    typed_global: GlobalOptions,
    steps: list[dict[str, Any]],
) -> str:
    """Validate and return an absolute reusable XYZ artifact path.

    The function only reads files.  It intentionally does not call artifact
    preparation or cleanup routines, so a failed strict resume leaves every
    existing artifact unchanged.
    """
    output = _validate_xyz(
        output_path,
        root_dir=root_dir,
        step_dir=step_dir,
        label=f"step '{step_name}' output",
    )
    normalized_type = step_type.lower()
    if normalized_type in {"confgen", "gen"}:
        _validate_confgen_signature(
            step_dir=step_dir,
            output_path=str(output),
            current_input=current_input,
            params=params,
            input_files=input_files,
            global_config=global_config,
        )
    elif normalized_type in {"calc", "task"}:
        _validate_calc_manifest(
            step_dir=step_dir,
            step_name=step_name,
            output_path=str(output),
            current_input=current_input,
            params=params,
            typed_global=typed_global,
            root_dir=root_dir,
            steps=steps,
        )
    return str(output)
