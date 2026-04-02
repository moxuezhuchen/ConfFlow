#!/usr/bin/env python3

"""ConfFlow Pydantic data models.

Complementary to the TypedDict definitions in ``core.types``:

- TypedDict: lightweight static type annotations (no runtime overhead).
- Pydantic models: data containers requiring runtime validation and serialisation.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_ENABLE_DYNAMIC_RESOURCES,
    DEFAULT_FORCE_CONSISTENCY,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_RESUME_FROM_BACKUPS,
    DEFAULT_RMSD_THRESHOLD,
    DEFAULT_SCAN_COARSE_STEP,
    DEFAULT_SCAN_FINE_STEP,
    DEFAULT_SCAN_UPHILL_LIMIT,
    DEFAULT_STOP_CHECK_INTERVAL_SECONDS,
    DEFAULT_TOTAL_MEMORY,
    DEFAULT_TS_BOND_DRIFT_THRESHOLD,
    DEFAULT_TS_RESCUE_SCAN,
    DEFAULT_TS_RMSD_THRESHOLD,
)
from .parsers import parse_index_spec

__all__ = [
    "TaskContext",
    "GlobalConfigModel",
    "CalcConfigModel",
]


def _coerce_freeze_indices(v: Any) -> list[int]:
    """Coerce freeze indices from list/range string/None into a list of ints."""
    if v is None:
        return []
    if isinstance(v, str):
        return parse_index_spec(v)
    if isinstance(v, (list, tuple)):
        result: list[int] = []
        for item in v:
            if isinstance(item, str):
                result.extend(parse_index_spec(item))
            else:
                result.append(int(item))
        return result
    return []


def _coerce_two_atom_indices(v: Any) -> list[int] | None:
    """Coerce two-atom index input from list or string into ``[a, b]``."""
    if v is None:
        return None
    if isinstance(v, str):
        parts = v.replace(",", " ").split()
        if len(parts) == 2:
            return [int(parts[0]), int(parts[1])]
        return None
    if isinstance(v, (list, tuple)):
        if len(v) == 2:
            return [int(v[0]), int(v[1])]
        return None
    return None


class TaskContext(BaseModel):
    """Context information for a computation task."""

    model_config = ConfigDict(extra="allow")

    job_name: str
    work_dir: str
    coords: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class GlobalConfigModel(BaseModel):
    """Pydantic model for global configuration validation.

    Provides runtime type coercion and validation for all global parameters.
    Used as a validation layer alongside the existing ConfigSchema.
    """

    model_config = ConfigDict(extra="allow")

    # Program paths
    gaussian_path: str = ""
    orca_path: str = ""

    # Resource configuration
    cores_per_task: int = DEFAULT_CORES_PER_TASK
    total_memory: str = DEFAULT_TOTAL_MEMORY
    max_parallel_jobs: int = DEFAULT_MAX_PARALLEL_JOBS

    # Molecular properties
    charge: int = DEFAULT_CHARGE
    multiplicity: int = DEFAULT_MULTIPLICITY

    # Refine parameters
    rmsd_threshold: float = DEFAULT_RMSD_THRESHOLD
    energy_window: float | None = None
    energy_tolerance: float = 0.05
    noH: bool = False

    # Freeze & TS
    freeze: list[int] = Field(default_factory=list)
    ts_bond_atoms: list[int] | None = None

    # TS rescue
    ts_rescue_scan: bool = DEFAULT_TS_RESCUE_SCAN
    scan_coarse_step: float = DEFAULT_SCAN_COARSE_STEP
    scan_fine_step: float = DEFAULT_SCAN_FINE_STEP
    scan_uphill_limit: int = DEFAULT_SCAN_UPHILL_LIMIT
    ts_bond_drift_threshold: float = DEFAULT_TS_BOND_DRIFT_THRESHOLD
    ts_rmsd_threshold: float = DEFAULT_TS_RMSD_THRESHOLD

    # Workflow control
    enable_dynamic_resources: bool = DEFAULT_ENABLE_DYNAMIC_RESOURCES
    resume_from_backups: bool = DEFAULT_RESUME_FROM_BACKUPS
    stop_check_interval_seconds: int = DEFAULT_STOP_CHECK_INTERVAL_SECONDS
    force_consistency: bool = DEFAULT_FORCE_CONSISTENCY

    @field_validator("cores_per_task")
    @classmethod
    def validate_cores(cls, v: int) -> int:
        """Ensure cores_per_task >= 1."""
        if v < 1:
            raise ValueError(f"cores_per_task must be >= 1, got {v}")
        return v

    @field_validator("max_parallel_jobs")
    @classmethod
    def validate_max_jobs(cls, v: int) -> int:
        """Ensure max_parallel_jobs >= 1."""
        if v < 1:
            raise ValueError(f"max_parallel_jobs must be >= 1, got {v}")
        return v

    @field_validator("multiplicity")
    @classmethod
    def validate_multiplicity(cls, v: int) -> int:
        """Ensure multiplicity >= 1."""
        if v < 1:
            raise ValueError(f"multiplicity must be >= 1, got {v}")
        return v

    @field_validator("total_memory")
    @classmethod
    def validate_memory_format(cls, v: str) -> str:
        """Validate memory format like '4GB' or '500MB'."""
        v_upper = str(v).strip().upper()
        if not re.match(r"^\d+(?:\.\d+)?\s*(?:GB|MB|KB|B)$", v_upper):
            raise ValueError(f"total_memory format error: '{v}', expected '4GB' or '500MB'")
        return v

    @field_validator("freeze", mode="before")
    @classmethod
    def coerce_freeze(cls, v: Any) -> list[int]:
        """Accept list or comma-separated string for freeze indices."""
        return _coerce_freeze_indices(v)

    @field_validator("ts_bond_atoms", mode="before")
    @classmethod
    def coerce_ts_bond_atoms(cls, v: Any) -> list[int] | None:
        """Accept list or comma/space-separated string for ts_bond_atoms."""
        return _coerce_two_atom_indices(v)


class CalcConfigModel(BaseModel):
    """Pydantic model for calc step configuration validation.

    Validates required fields and program/task type constraints.
    """

    model_config = ConfigDict(extra="allow")

    iprog: str | int
    itask: str | int
    keyword: str
    cores_per_task: int | None = None
    total_memory: str | None = None
    max_parallel_jobs: int | None = None
    charge: int | None = None
    multiplicity: int | None = None
    ts_bond_atoms: list[int] | None = None

    @field_validator("keyword")
    @classmethod
    def validate_keyword(cls, v: str) -> str:
        """Ensure keyword is non-empty."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("keyword must be a non-empty string")
        return v

    @field_validator("iprog")
    @classmethod
    def validate_iprog(cls, v: str | int) -> str | int:
        """Validate program identifier."""
        valid = {"gaussian", "g16", "orca", "1", "2", 1, 2}
        if v not in valid:
            raise ValueError(f"invalid iprog: {v}, valid: gaussian, g16, orca, 1, 2")
        return v

    @field_validator("itask")
    @classmethod
    def validate_itask(cls, v: str | int) -> str | int:
        """Validate task type."""
        valid = {"opt", "sp", "freq", "opt_freq", "ts", "0", "1", "2", "3", "4", 0, 1, 2, 3, 4}
        if v not in valid:
            raise ValueError(f"invalid itask: {v}, valid: opt, sp, freq, opt_freq, ts, 0-4")
        return v

    @field_validator("cores_per_task")
    @classmethod
    def validate_cores_per_task(cls, v: int | None) -> int | None:
        """Ensure cores_per_task >= 1 when provided."""
        if v is not None and v < 1:
            raise ValueError(f"cores_per_task must be >= 1, got {v}")
        return v

    @field_validator("max_parallel_jobs")
    @classmethod
    def validate_max_parallel_jobs(cls, v: int | None) -> int | None:
        """Ensure max_parallel_jobs >= 1 when provided."""
        if v is not None and v < 1:
            raise ValueError(f"max_parallel_jobs must be >= 1, got {v}")
        return v

    @field_validator("multiplicity")
    @classmethod
    def validate_calc_multiplicity(cls, v: int | None) -> int | None:
        """Ensure multiplicity >= 1 when provided."""
        if v is not None and v < 1:
            raise ValueError(f"multiplicity must be >= 1, got {v}")
        return v

    @field_validator("total_memory")
    @classmethod
    def validate_calc_memory_format(cls, v: str | None) -> str | None:
        """Validate memory format like '4GB' or '500MB' when provided."""
        if v is None:
            return None
        v_upper = str(v).strip().upper()
        if not re.match(r"^\d+(?:\.\d+)?\s*(?:GB|MB|KB|B)$", v_upper):
            raise ValueError(f"total_memory format error: '{v}', expected '4GB' or '500MB'")
        return v

    @field_validator("ts_bond_atoms", mode="before")
    @classmethod
    def coerce_calc_ts_bond_atoms(cls, v: Any) -> list[int] | None:
        """Accept list or comma/space-separated string for ts_bond_atoms."""
        if v is None:
            return None
        coerced = _coerce_two_atom_indices(v)
        if coerced is not None:
            return coerced
        if isinstance(v, str) or isinstance(v, (list, tuple)):
            raise ValueError("ts_bond_atoms must contain exactly two atom indices")
        raise ValueError("ts_bond_atoms must be a list or comma-separated string")
