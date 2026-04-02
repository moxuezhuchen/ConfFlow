#!/usr/bin/env python3

"""ConfFlow type definitions.

Provides unified type aliases and TypedDict definitions for static type
checking and documenting dictionary structures.  For data containers requiring
runtime validation, use the Pydantic models in ``core.models``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from typing_extensions import TypedDict

# ==============================================================================
# Basic type aliases
# ==============================================================================

CoordLine = str
CoordLines = list[CoordLine]

# Cartesian coordinate array shape: ``[[x, y, z], ...]``.
Coords3D = list[list[float]]

# Atom symbol sequence such as ``["C", "H", "H", ...]``.
AtomList = list[str]


# ==============================================================================
# Status constants and enumerations
# ==============================================================================


class TaskStatus(str, Enum):
    """Task and step status constants."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    SKIPPED_MULTI = "skipped_multi_frame"
    COMPLETED = "completed"
    RUNNING = "running"
    PENDING = "pending"
    CANCELED = "canceled"


# ==============================================================================
# Configuration-related TypedDicts
# ==============================================================================


class GlobalConfig(TypedDict, total=False):
    """Global configuration parameter type definition."""

    # Program paths
    gaussian_path: str
    orca_path: str

    # Resource configuration
    cores_per_task: int
    total_memory: str
    max_parallel_jobs: int

    # Molecular properties
    charge: int
    multiplicity: int

    # Calculation parameters
    rmsd_threshold: float
    energy_window: float
    freeze: str | list[int]

    # TS-related
    ts_bond_atoms: str | list[int]
    ts_rescue_scan: bool
    scan_coarse_step: float
    scan_fine_step: float
    scan_uphill_limit: float
    ts_bond_drift_threshold: float
    ts_rmsd_threshold: float

    # Resource management
    enable_dynamic_resources: bool
    resume_from_backups: bool
    stop_check_interval_seconds: int

    # ORCA-specific
    orca_maxcore: int


class StepParams(TypedDict, total=False):
    """Workflow step parameter type definition."""

    # General
    name: str
    type: str
    enabled: bool

    # Calculation parameters (may override global config)
    iprog: str | int
    itask: str | int
    keyword: str

    # Resource configuration (may override global config)
    cores_per_task: int
    total_memory: str
    max_parallel_jobs: int

    # Molecular properties (may override global config)
    charge: int
    multiplicity: int

    # Deduplication parameters
    rmsd_threshold: float
    energy_window: float
    noH: bool
    dedup_only: bool
    keep_all_topos: bool

    # Constraint parameters
    freeze: str | list[int]
    ts_bond_atoms: str | list[int]

    # Conformer generation parameters
    angle_step: int
    bond_multiplier: float
    add_bond: list[list[int]]
    del_bond: list[list[int]]
    no_rotate: list[list[int]]
    force_rotate: list[list[int]]
    optimize: bool
    chains: str | list[str]
    chain_steps: str | list[str]
    chain_angles: str | list[str]
    rotate_side: str

    # chk file related
    chk_from_step: str | int
    input_chk_dir: str
    gaussian_write_chk: bool


class ConformerData(TypedDict, total=False):
    """Conformer data type definition."""

    natoms: int
    comment: str
    atoms: AtomList
    coords: Coords3D
    frame_index: int
    metadata: dict[str, Any]


class TaskResult(TypedDict, total=False):
    """Calculation task result type definition."""

    job_name: str
    status: str  # "success", "failed", "skipped", "canceled", "pending"
    error: str
    error_kind: str
    error_details: str

    # Energy
    energy: float
    final_gibbs_energy: float
    final_sp_energy: float
    g_corr: float

    # Geometry
    final_coords: CoordLines

    # Frequency information
    num_imag_freqs: int
    lowest_freq: float

    # TS-specific
    ts_bond_atoms: str
    ts_bond_length: float


class WorkflowStats(TypedDict, total=False):
    """Workflow statistics type definition."""

    start_time: str
    end_time: str
    input_files: list[str]
    initial_conformers: int
    final_conformers: int
    is_multi_frame_input: bool
    total_duration_seconds: float
    final_output: str
    steps: list[dict[str, Any]]


class StepStats(TypedDict, total=False):
    """Step statistics type definition."""

    name: str
    type: str
    index: int
    status: str
    input_conformers: int
    output_conformers: int
    failed_conformers: int
    duration_seconds: float
    output_xyz: str
    error: str
    end_time: str


# ==============================================================================
# Parsed output types
# ==============================================================================


class ParsedOutput(TypedDict, total=False):
    """Calculation output parsed result type definition."""

    e_low: float  # Low-level energy
    e_high: float  # High-level energy (ONIOM)
    g_low: float  # Gibbs free energy
    g_corr: float  # Gibbs correction
    final_coords: CoordLines
    num_imag_freqs: int
    lowest_freq: float
    frequencies: list[float]


# ==============================================================================
# Validation-related types
# ==============================================================================


class ValidationResult(TypedDict):
    """Validation result type definition."""

    valid: bool
    errors: list[str]
    warnings: list[str]


__all__ = [
    # Basic types
    "CoordLine",
    "CoordLines",
    "Coords3D",
    "AtomList",
    # Configuration types
    "GlobalConfig",
    "StepParams",
    # Data types
    "ConformerData",
    "TaskResult",
    "WorkflowStats",
    "StepStats",
    "ParsedOutput",
    "ValidationResult",
]
