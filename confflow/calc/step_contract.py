#!/usr/bin/env python3

"""Public contract for calc step artifacts and resume compatibility."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CalcStepState",
    "PreparedCalcStep",
    "compute_calc_config_signature",
    "compute_calc_input_signature",
    "load_calc_config_signature",
    "record_calc_step_signature",
    "resolve_calc_step_output",
    "inspect_calc_step_state",
    "prepare_calc_step_dir",
]

_CONFIG_HASH_EXCLUDE_KEYS = {
    "backup_dir",
    "stop_beacon_file",
    "gaussian_oldchk",
    "gaussian_oldchk_file",
    "input_chk_dir",
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


def compute_calc_config_signature(config: dict[str, Any]) -> str:
    payload = json.dumps(
        _normalize_config_for_signature(config),
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
    config: dict[str, Any],
    *,
    input_signature: str | None = None,
) -> None:
    os.makedirs(step_dir, exist_ok=True)
    with open(_signature_path(step_dir), "w", encoding="utf-8") as handle:
        signature = compute_calc_config_signature(config)
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
    task_config: dict[str, Any],
    *,
    input_signature: str | None = None,
) -> CalcStepState:
    current_signature = compute_calc_config_signature(task_config)
    if input_signature is not None:
        current_signature = f"{current_signature}:{input_signature}"
    return CalcStepState(
        step_dir=step_dir,
        output_path=resolve_calc_step_output(step_dir),
        failed_path=(
            os.path.join(step_dir, "failed.xyz") if os.path.exists(os.path.join(step_dir, "failed.xyz")) else None
        ),
        stored_signature=load_calc_config_signature(step_dir),
        current_signature=current_signature,
        artifact_names=tuple(_list_resume_artifacts(step_dir)),
    )


def prepare_calc_step_dir(
    step_dir: str,
    task_config: dict[str, Any],
    *,
    input_signature: str | None = None,
) -> PreparedCalcStep:
    state = inspect_calc_step_state(step_dir, task_config, input_signature=input_signature)
    if state.is_reusable or state.can_resume_without_output:
        return PreparedCalcStep(state=state, cleaned_stale_artifacts=False)

    should_clean = state.has_resume_state and not (
        state.is_reusable or state.can_resume_without_output
    )
    if should_clean:
        _clear_step_dir_contents(step_dir)
        state = inspect_calc_step_state(step_dir, task_config, input_signature=input_signature)
    return PreparedCalcStep(state=state, cleaned_stale_artifacts=should_clean)
