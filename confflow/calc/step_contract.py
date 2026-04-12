#!/usr/bin/env python3

"""Public contract for calc step artifacts and resume compatibility."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypedDict

__all__ = [
    "CalcStepState",
    "PreparedCalcStep",
    "CompatConfig",
    "ExecutionConfig",
    "compute_calc_config_signature",
    "compute_calc_input_signature",
    "load_calc_config_signature",
    "record_calc_step_signature",
    "resolve_calc_step_output",
    "inspect_calc_step_state",
    "prepare_calc_step_dir",
    "resolve_effective_auto_clean",
]


# ==============================================================================
# Config boundary types
# ==============================================================================


class CompatConfig(TypedDict, total=False):
    """Compat/signature baseline config (legacy flat dict).

    This represents the minimal config structure required for signature
    computation and auto-clean resolution. All keys are optional to support
    partial configs and runtime updates.

    Used by:
    - compute_calc_config_signature()
    - resolve_effective_auto_clean()
    - record_calc_step_signature()
    - inspect_calc_step_state()
    - prepare_calc_step_dir()
    """

    # Auto-clean baseline
    auto_clean: bool | str
    clean_opts: str
    clean_params: str | Mapping[str, Any]

    # Signature-excluded runtime paths (not part of config hash)
    backup_dir: str
    stop_beacon_file: str
    gaussian_oldchk: bool | str
    gaussian_oldchk_file: str
    input_chk_dir: str


class ExecutionConfig(TypedDict, total=False):
    """Execution config overlay (structured or flat).

    This represents the optional execution-time config that can override
    compat baseline for cleanup semantics. Can be either:
    - CalcTaskConfig instance (structured, with .cleanup attribute)
    - Flat dict with auto_clean/clean_opts keys

    Used by:
    - compute_calc_config_signature() (execution_config parameter)
    - resolve_effective_auto_clean() (execution_config parameter)
    - record_calc_step_signature() (execution_config parameter)
    - inspect_calc_step_state() (execution_config parameter)
    - prepare_calc_step_dir() (execution_config parameter)
    """

    # Flat fallback keys (when not CalcTaskConfig)
    auto_clean: bool | str
    clean_opts: str
    clean_params: str | Mapping[str, Any]


_CONFIG_HASH_EXCLUDE_KEYS = {
    "backup_dir",
    "stop_beacon_file",
    "gaussian_oldchk",
    "gaussian_oldchk_file",
    "input_chk_dir",
    "gaussian_write_chk",
    "enable_dynamic_resources",
    "resume_from_backups",
}

_CALC_RESUME_ARTIFACTS = (
    "output.xyz",
    "result.xyz",
    "failed.xyz",
    "results.db",
    "backups",
    "scan",
    "STOP",
    ".config_hash",
)


def _normalize_config_for_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _normalize_config_for_signature(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in _CONFIG_HASH_EXCLUDE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_config_for_signature(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_config_for_signature(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _coerce_clean_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _format_clean_opts_from_mapping(value: Mapping[str, Any]) -> str:
    opts: list[str] = []
    if _coerce_clean_flag(value.get("dedup_only")):
        opts.append("--dedup-only")
    if _coerce_clean_flag(value.get("keep_all_topos")):
        opts.append("--keep-all-topos")
    if _coerce_clean_flag(value.get("noH", value.get("no_h"))):
        opts.append("--noH")

    threshold = value.get("threshold", value.get("rmsd_threshold"))
    if threshold is not None and str(threshold).strip():
        opts.append(f"-t {threshold}")

    energy_window = value.get("energy_window", value.get("ewin"))
    if energy_window is not None and str(energy_window).strip():
        opts.append(f"-ewin {energy_window}")

    energy_tolerance = value.get("energy_tolerance", value.get("etol"))
    if energy_tolerance is not None and str(energy_tolerance).strip():
        opts.append(f"--energy-tolerance {energy_tolerance}")

    return " ".join(opts)


def _resolve_clean_opts_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        clean_opts = _format_clean_opts_from_mapping(value)
        return clean_opts or None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                return stripped
            if isinstance(parsed, Mapping):
                clean_opts = _format_clean_opts_from_mapping(parsed)
                return clean_opts or None
        return stripped
    stripped = str(value).strip()
    return stripped or None


def resolve_effective_auto_clean(
    config: CompatConfig,
    execution_config: ExecutionConfig | Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    """Resolve effective auto-clean enable flag and clean_opts.

    This function implements the dual-lane handoff contract for cleanup semantics:
    - ``config`` provides the compat/signature baseline
    - ``execution_config`` can override with structured cleanup options
    - Both signature computation and runtime auto-clean use the same priority rules

    See ``docs/internal/COMPAT_EXECUTION_BOUNDARY.md`` for the complete parameter
    classification table and boundary contract.

    Parameters
    ----------
    config : CompatConfig
        Compat config (legacy flat dict). Provides baseline for ``auto_clean``
        and ``clean_opts`` / ``clean_params``.
    execution_config : ExecutionConfig | Mapping[str, Any] | None
        Execution config (structured or flat). Can override cleanup semantics
        via ``cleanup.enabled`` / ``cleanup.to_legacy_clean_opts()`` (if
        CalcTaskConfig) or ``auto_clean`` / ``clean_opts`` / ``clean_params``
        (if flat dict).

    Returns
    -------
    tuple[bool, str]
        (enabled, clean_opts):
        - enabled: whether auto-clean is actually enabled
        - clean_opts: effective clean_opts string (only meaningful if enabled=True)

    Priority for auto_clean flag:
        1. config["auto_clean"] (compat baseline)
        2. execution_config.cleanup.enabled (structured override)
        3. execution_config["auto_clean"] (flat fallback)
        4. default False

    Priority for clean_opts (only when enabled=True):
        1. config["clean_opts"] (compat baseline)
        2. config["clean_params"] (compat baseline legacy alias)
        3. execution_config.cleanup.to_legacy_clean_opts() (structured override)
        4. execution_config["clean_opts"] (flat fallback)
        5. execution_config["clean_params"] (flat fallback legacy alias)
        6. default "-t 0.25"

    Notes
    -----
    Runtime updates to ``config`` (e.g., ``manager.config.update({"clean_opts": ...})``)
    must be visible to both signature computation and auto-clean execution.
    This is guaranteed by passing the same mutable ``config`` object to both paths.
    """
    from .config_types import CalcTaskConfig

    def _is_enabled(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return str(value).strip().lower() == "true"

    # Resolve auto_clean flag
    auto_clean_raw = config.get("auto_clean")
    if auto_clean_raw is None:
        if execution_config is None:
            auto_clean_enabled = False
        elif isinstance(execution_config, CalcTaskConfig):
            auto_clean_enabled = execution_config.cleanup.enabled or _is_enabled(
                execution_config.get("auto_clean", "false")
            )
        else:
            auto_clean_enabled = _is_enabled(execution_config.get("auto_clean", "false"))
    else:
        auto_clean_enabled = _is_enabled(auto_clean_raw)

    if not auto_clean_enabled:
        return False, ""

    # Resolve clean_opts (only when enabled)
    clean_opts_raw = _resolve_clean_opts_value(config.get("clean_opts"))
    if clean_opts_raw is None:
        clean_opts_raw = _resolve_clean_opts_value(config.get("clean_params"))
    if clean_opts_raw is not None:
        return True, clean_opts_raw

    if execution_config is None:
        return True, "-t 0.25"

    if isinstance(execution_config, CalcTaskConfig) and execution_config.cleanup.enabled:
        clean_opts = execution_config.cleanup.to_legacy_clean_opts()
        if clean_opts.strip():
            return True, clean_opts

    clean_opts_fallback = _resolve_clean_opts_value(execution_config.get("clean_opts"))
    if clean_opts_fallback is None:
        clean_opts_fallback = _resolve_clean_opts_value(execution_config.get("clean_params"))
    if clean_opts_fallback is not None:
        return True, clean_opts_fallback

    return True, "-t 0.25"


def compute_calc_config_signature(
    config: CompatConfig,
    *,
    execution_config: ExecutionConfig | Mapping[str, Any] | None = None,
) -> str:
    """Compute calc step signature with effective cleanup overlay.

    This function implements the dual-lane handoff contract for signature computation:
    - ``config`` provides the compat/signature baseline (legacy flat dict)
    - ``execution_config`` provides structured cleanup that must be reflected in signature
    - Effective cleanup is overlaid onto signature ONLY if auto-clean is enabled

    See ``docs/internal/COMPAT_EXECUTION_BOUNDARY.md`` for the complete parameter
    classification table and boundary contract.

    Parameters
    ----------
    config : CompatConfig
        Compat config (legacy flat dict). Provides the signature baseline.
        All parameters in this dict (except those in ``_CONFIG_HASH_EXCLUDE_KEYS``)
        contribute to the signature.
    execution_config : ExecutionConfig | Mapping[str, Any] | None
        Execution config (structured or flat). If provided, effective cleanup
        semantics are resolved via ``resolve_effective_auto_clean()`` and
        overlaid onto the signature view.

    Returns
    -------
    str
        12-character MD5 hex digest of the normalized config JSON.

    Notes
    -----
    - If ``auto_clean=False``, cleanup parameters do NOT affect signature.
    - If ``auto_clean=True``, effective cleanup (resolved from both config and
      execution_config) MUST be reflected in signature to ensure stale detection.
    - Runtime updates to ``config`` (e.g., ``manager.config.update({"clean_opts": ...})``)
      must produce a different signature if they change effective cleanup semantics.
    - Signature stability is critical: changing this algorithm breaks断点续传 for
      all existing step artifacts.
    """
    # Create shallow copy for signature overlay
    signature_view = dict(config)

    # Overlay effective cleanup ONLY if auto-clean is actually enabled
    auto_clean_enabled, effective_clean_opts = resolve_effective_auto_clean(
        config, execution_config
    )
    if auto_clean_enabled:
        signature_view["clean_opts"] = effective_clean_opts
        signature_view.pop("clean_params", None)
        signature_view["auto_clean"] = "true"
    else:
        # Normalize auto_clean to false and remove all cleanup params from signature
        signature_view.pop("clean_opts", None)
        signature_view.pop("clean_params", None)
        signature_view["auto_clean"] = "false"

    payload = json.dumps(
        _normalize_config_for_signature(signature_view),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


def _file_content_digest(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def compute_calc_input_signature(input_source: str | list[str] | tuple[str, ...] | None) -> str:
    """Build a stable signature for the calc step input files/content."""
    if input_source is None:
        return "no-input"

    paths = [input_source] if isinstance(input_source, str) else list(input_source)
    normalized: list[dict[str, str | int]] = []
    for path in paths:
        real = os.path.realpath(os.path.abspath(str(path)))
        try:
            stat = os.stat(real)
        except OSError:
            normalized.append(
                {
                    "path": real,
                    "missing": "true",
                }
            )
            continue
        normalized.append(
            {
                "path": real,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "content_md5": _file_content_digest(real),
            }
        )
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


def _signature_path(step_dir: str) -> str:
    return os.path.join(step_dir, ".config_hash")


def load_calc_config_signature(step_dir: str) -> str | None:
    path = _signature_path(step_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def record_calc_step_signature(
    step_dir: str,
    config: CompatConfig,
    *,
    input_signature: str | None = None,
    execution_config: ExecutionConfig | Mapping[str, Any] | None = None,
) -> None:
    """Record calc step signature to ``.config_hash`` file.

    This function implements the dual-lane handoff contract for signature recording:
    - ``config`` provides the compat/signature baseline
    - ``execution_config`` provides structured cleanup that must be reflected in signature
    - Signature is computed via ``compute_calc_config_signature()`` with the same
      dual-lane parameters

    See ``docs/internal/COMPAT_EXECUTION_BOUNDARY.md`` for the complete parameter
    classification table and boundary contract.

    Parameters
    ----------
    step_dir : str
        Calc step directory path. The ``.config_hash`` file will be written here.
    config : CompatConfig
        Compat config (legacy flat dict). Provides the signature baseline.
    input_signature : str | None
        Optional input file signature (from ``compute_calc_input_signature()``).
        If provided, the final signature is ``<config_sig>:<input_sig>``.
    execution_config : ExecutionConfig | Mapping[str, Any] | None
        Execution config (structured or flat). If provided, effective cleanup
        semantics are resolved and overlaid onto the signature.

    Notes
    -----
    - Must be called AFTER calc step completes successfully.
    - Subsequent runs will read this signature via ``load_calc_config_signature()``
      to determine if old artifacts are reusable or stale.
    - Runtime updates to ``config`` before this call will be reflected in the
      recorded signature (e.g., ``manager.config.update({"clean_opts": ...})``).
    """
    os.makedirs(step_dir, exist_ok=True)
    with open(_signature_path(step_dir), "w", encoding="utf-8") as handle:
        signature = compute_calc_config_signature(config, execution_config=execution_config)
        if input_signature is not None:
            signature = f"{signature}:{input_signature}"
        handle.write(signature)


def resolve_calc_step_output(step_dir: str) -> str | None:
    for name in ("output.xyz", "result.xyz"):
        candidate = os.path.join(step_dir, name)
        if os.path.exists(candidate):
            return candidate
    return None


def _list_resume_artifacts(step_dir: str) -> list[str]:
    if not os.path.isdir(step_dir):
        return []
    entries = []
    for name in os.listdir(step_dir):
        if name in _CALC_RESUME_ARTIFACTS or not name.startswith("."):
            entries.append(name)
    return sorted(entries)


def _clear_step_dir_contents(step_dir: str) -> None:
    for entry in os.listdir(step_dir):
        path = os.path.join(step_dir, entry)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)


@dataclass(frozen=True)
class CalcStepState:
    step_dir: str
    output_path: str | None
    failed_path: str | None
    stored_signature: str | None
    current_signature: str
    artifact_names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_resume_state(self) -> bool:
        return bool(self.artifact_names)

    @property
    def has_results_db(self) -> bool:
        return "results.db" in self.artifact_names

    @property
    def has_backups(self) -> bool:
        return "backups" in self.artifact_names

    @property
    def signature_matches(self) -> bool:
        return self.stored_signature is not None and self.stored_signature == self.current_signature

    @property
    def can_resume_without_output(self) -> bool:
        return self.signature_matches and (self.has_results_db or self.has_backups)

    @property
    def is_reusable(self) -> bool:
        return self.output_path is not None and self.signature_matches


@dataclass(frozen=True)
class PreparedCalcStep:
    state: CalcStepState
    cleaned_stale_artifacts: bool = False

    @property
    def reusable_output(self) -> str | None:
        return self.state.output_path if self.state.is_reusable else None


def inspect_calc_step_state(
    step_dir: str,
    task_config: CompatConfig,
    *,
    input_signature: str | None = None,
    execution_config: ExecutionConfig | Mapping[str, Any] | None = None,
) -> CalcStepState:
    current_signature = compute_calc_config_signature(
        task_config, execution_config=execution_config
    )
    if input_signature is not None:
        current_signature = f"{current_signature}:{input_signature}"
    return CalcStepState(
        step_dir=step_dir,
        output_path=resolve_calc_step_output(step_dir),
        failed_path=(
            os.path.join(step_dir, "failed.xyz")
            if os.path.exists(os.path.join(step_dir, "failed.xyz"))
            else None
        ),
        stored_signature=load_calc_config_signature(step_dir),
        current_signature=current_signature,
        artifact_names=tuple(_list_resume_artifacts(step_dir)),
    )


def prepare_calc_step_dir(
    step_dir: str,
    task_config: CompatConfig,
    *,
    input_signature: str | None = None,
    execution_config: ExecutionConfig | Mapping[str, Any] | None = None,
) -> PreparedCalcStep:
    """Prepare calc step directory for execution (clean stale artifacts if needed).

    This function implements the dual-lane handoff contract for stale detection:
    - ``task_config`` provides the compat/signature baseline
    - ``execution_config`` provides structured cleanup that must be reflected in signature
    - Stale artifacts are cleaned ONLY if stored signature != current signature

    See ``docs/internal/COMPAT_EXECUTION_BOUNDARY.md`` for the complete parameter
    classification table and boundary contract.

    Parameters
    ----------
    step_dir : str
        Calc step directory path. Will be created if it doesn't exist.
    task_config : CompatConfig
        Compat config (legacy flat dict). Provides the signature baseline.
    input_signature : str | None
        Optional input file signature (from ``compute_calc_input_signature()``).
        If provided, the final signature is ``<config_sig>:<input_sig>``.
    execution_config : ExecutionConfig | Mapping[str, Any] | None
        Execution config (structured or flat). If provided, effective cleanup
        semantics are resolved and overlaid onto the signature.

    Returns
    -------
    PreparedCalcStep
        Contains ``state`` (CalcStepState) and ``cleaned_stale_artifacts`` (bool).
        - If ``state.is_reusable``: old output.xyz/result.xyz can be reused
        - If ``state.can_resume_without_output``: can resume from results.db/backups
        - If ``cleaned_stale_artifacts=True``: old artifacts were stale and removed

    Notes
    -----
    - Must be called BEFORE starting calc step execution.
    - If signature mismatch is detected, ALL old artifacts are removed to prevent
      mixing results from different configurations.
    - Runtime updates to ``task_config`` before this call will affect stale detection
      (e.g., ``manager.config.update({"clean_opts": ...})`` will trigger cleanup
      if the new cleanup semantics differ from stored signature).
    """
    state = inspect_calc_step_state(
        step_dir, task_config, input_signature=input_signature, execution_config=execution_config
    )
    if state.is_reusable or state.can_resume_without_output:
        return PreparedCalcStep(state=state, cleaned_stale_artifacts=False)

    should_clean = state.has_resume_state and not (
        state.is_reusable or state.can_resume_without_output
    )
    if should_clean:
        _clear_step_dir_contents(step_dir)
        state = inspect_calc_step_state(
            step_dir,
            task_config,
            input_signature=input_signature,
            execution_config=execution_config,
        )
    return PreparedCalcStep(state=state, cleaned_stale_artifacts=should_clean)
