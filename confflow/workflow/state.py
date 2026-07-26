#!/usr/bin/env python3

"""Persistent, atomically-written workflow state."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..contract import WORKFLOW_STATE_FILE

__all__ = ["StepRecord", "WorkflowState", "WorkflowStateStore"]


@dataclass
class StepRecord:
    """Persisted state for one configured workflow step."""

    name: str
    type: str
    status: str = "pending"
    submitted_at: float | None = None
    completed_at: float | None = None
    output_xyz: str | None = None
    error: str | None = None
    executor_handle_data: dict[str, Any] | None = None
    fail_count: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StepRecord:
        """Deserialize a record, tolerating state files from older revisions."""
        return cls(
            name=str(raw["name"]),
            type=str(raw["type"]),
            status=str(raw.get("status", "pending")),
            submitted_at=_optional_float(raw.get("submitted_at")),
            completed_at=_optional_float(raw.get("completed_at")),
            output_xyz=_optional_str(raw.get("output_xyz")),
            error=_optional_str(raw.get("error")),
            executor_handle_data=_optional_dict(raw.get("executor_handle_data")),
            fail_count=int(raw.get("fail_count", 0)),
        )


@dataclass
class WorkflowState:
    """All state required to resume a workflow from its working directory."""

    run_id: str
    work_dir: str
    input_files: list[str]
    original_inputs: list[str]
    config_file: str
    steps: dict[str, StepRecord] = field(default_factory=dict)
    wavefront_index: int = 0
    started_at: float = field(default_factory=time.time)
    last_updated_at: float = field(default_factory=time.time)
    final_status: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WorkflowState:
        """Deserialize a state file and validate its minimum shape."""
        steps_raw = raw.get("steps", {})
        if not isinstance(steps_raw, dict):
            raise ValueError("workflow state 'steps' must be an object")
        return cls(
            run_id=str(raw["run_id"]),
            work_dir=str(raw["work_dir"]),
            input_files=_string_list(raw.get("input_files")),
            original_inputs=_string_list(raw.get("original_inputs")),
            config_file=str(raw["config_file"]),
            steps={
                str(key): StepRecord.from_dict(value)
                for key, value in steps_raw.items()
                if isinstance(value, dict)
            },
            wavefront_index=int(raw.get("wavefront_index", 0)),
            started_at=float(raw.get("started_at", time.time())),
            last_updated_at=float(raw.get("last_updated_at", time.time())),
            final_status=str(raw.get("final_status", "")),
        )


class WorkflowStateStore:
    """Atomically read and write ``<work_dir>/{WORKFLOW_STATE_FILE}``.

    The filename is sourced from :mod:`confflow.contract` so the on-disk
    layout and the cross-repository capability payload can never drift.
    """

    def __init__(self, work_dir: str):
        self.path = os.path.join(work_dir, WORKFLOW_STATE_FILE)

    def load(self) -> WorkflowState | None:
        """Return saved state, or ``None`` when no workflow state exists yet."""
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid workflow state file {self.path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid workflow state file {self.path}: expected an object")
        try:
            return WorkflowState.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid workflow state file {self.path}: {exc}") from exc

    def save(self, state: WorkflowState) -> None:
        """Persist state by replacing the destination only after JSON is complete."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        state.last_updated_at = time.time()
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, self.path)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("expected a list of paths")
    return [str(item) for item in value]
