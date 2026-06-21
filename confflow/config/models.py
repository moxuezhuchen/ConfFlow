#!/usr/bin/env python3

"""Typed configuration models for the non-legacy ConfFlow runtime."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..core.exceptions import ConfigurationError
from ..core.models import _coerce_freeze_indices, _coerce_two_atom_indices
from ..shared.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_DELETE_WORK_DIR,
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
    DEFAULT_WORKFLOW_AUTO_CLEAN,
)
from ..shared.orca_blocks import format_orca_blocks

ProgramName = Literal["g16", "orca"]
TaskName = Literal["opt", "sp", "freq", "opt_freq", "ts"]
StepType = Literal["confgen", "calc"]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return bool(value)


def _normalize_iprog_label(value: Any) -> str:
    raw = str(value).strip().lower()
    if raw in {"1", "g16", "gaussian", "gau", "g09", "g03"}:
        return "g16"
    if raw in {"2", "orca"}:
        return "orca"
    return str(value).strip()


def _itask_label(value: Any) -> str:
    raw = str(value).strip().lower()
    mapping = {
        "0": "opt",
        "1": "sp",
        "2": "freq",
        "3": "opt_freq",
        "4": "ts",
        "opt": "opt",
        "sp": "sp",
        "freq": "freq",
        "opt_freq": "opt_freq",
        "optfreq": "opt_freq",
        "ts": "ts",
    }
    return mapping.get(raw, str(value).strip())


def _parse_clean_opts_like_string(opts_str: str) -> tuple[float | None, float | None, float | None]:
    threshold: float | None = None
    energy_window: float | None = None
    energy_tolerance: float | None = None
    try:
        tokens = shlex.split(opts_str)
    except ValueError:
        tokens = opts_str.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "-t" and i + 1 < len(tokens):
            try:
                threshold = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif token == "-ewin" and i + 1 < len(tokens):
            try:
                energy_window = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif token == "--energy-tolerance" and i + 1 < len(tokens):
            try:
                energy_tolerance = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif token.startswith("-t="):
            try:
                threshold = float(token.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        elif token.startswith("-ewin="):
            try:
                energy_window = float(token.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        elif token.startswith("--energy-tolerance="):
            try:
                energy_tolerance = float(token.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        else:
            i += 1
    return threshold, energy_window, energy_tolerance


def _merge(global_options: GlobalOptions, params: dict[str, Any], key: str, default: Any = None):
    if key in params and params[key] is not None:
        return params[key]
    return getattr(global_options, key, default)


def _parse_pair(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        pair = _coerce_two_atom_indices(value)
    except (TypeError, ValueError):
        pair = None
    if pair is None:
        return None
    a, b = int(pair[0]), int(pair[1])
    if a <= 0 or b <= 0 or a == b:
        return None
    return (a, b)


def _validate_memory(value: str) -> str:
    raw = str(value).strip()
    if not re.match(r"^\d+(?:\.\d+)?\s*(?:GB|MB|KB|B)$", raw.upper()):
        raise ValueError(f"total_memory format error: {value!r}, expected '4GB' or '500MB'")
    return raw


@dataclass(frozen=True)
class ResourceOptions:
    cores_per_task: int = DEFAULT_CORES_PER_TASK
    total_memory: str = DEFAULT_TOTAL_MEMORY
    max_parallel_jobs: int = DEFAULT_MAX_PARALLEL_JOBS

    def __post_init__(self) -> None:
        if self.cores_per_task < 1:
            raise ValueError("cores_per_task must be >= 1")
        if self.max_parallel_jobs < 1:
            raise ValueError("max_parallel_jobs must be >= 1")
        object.__setattr__(self, "total_memory", _validate_memory(self.total_memory))


@dataclass(frozen=True)
class CleanupOptions:
    enabled: bool = DEFAULT_WORKFLOW_AUTO_CLEAN
    dedup_only: bool = False
    keep_all_topos: bool = False
    no_h: bool = False
    rmsd_threshold: float | None = DEFAULT_RMSD_THRESHOLD
    energy_window: float | None = None
    energy_tolerance: float | None = 0.05
    imag: int | None = None
    max_conformers: int | None = None

    @classmethod
    def from_params(cls, params: dict[str, Any], global_options: GlobalOptions) -> CleanupOptions:
        clean_params = params.get("clean_params")
        if clean_params is None:
            clean_params = params.get("clean_opts")
        enabled = _coerce_bool_flag(
            _merge(global_options, params, "auto_clean", DEFAULT_WORKFLOW_AUTO_CLEAN)
        )
        dedup_only = _coerce_bool_flag(params.get("dedup_only", False))
        keep_all_topos = _coerce_bool_flag(params.get("keep_all_topos", False))
        no_h = _coerce_bool_flag(params.get("noH", global_options.noH))
        rmsd = params.get("rmsd_threshold", global_options.rmsd_threshold)
        ewin = params.get("energy_window", global_options.energy_window)
        etol = params.get("energy_tolerance", global_options.energy_tolerance)

        if isinstance(clean_params, dict):
            enabled = True
            dedup_only = _coerce_bool_flag(clean_params.get("dedup_only", dedup_only))
            keep_all_topos = _coerce_bool_flag(clean_params.get("keep_all_topos", keep_all_topos))
            no_h = _coerce_bool_flag(clean_params.get("noH", clean_params.get("no_h", no_h)))
            rmsd = clean_params.get("threshold", clean_params.get("rmsd_threshold", rmsd))
            ewin = clean_params.get("energy_window", clean_params.get("ewin", ewin))
            etol = clean_params.get("energy_tolerance", clean_params.get("etol", etol))
        elif clean_params:
            enabled = True
            text = str(clean_params)
            parsed_rmsd, parsed_ewin, parsed_etol = _parse_clean_opts_like_string(text)
            dedup_only = dedup_only or "--dedup-only" in text
            keep_all_topos = keep_all_topos or "--keep-all-topos" in text
            no_h = no_h or "--noH" in text
            rmsd = parsed_rmsd if parsed_rmsd is not None else rmsd
            ewin = parsed_ewin if parsed_ewin is not None else ewin
            etol = parsed_etol if parsed_etol is not None else etol

        return cls(
            enabled=enabled,
            dedup_only=dedup_only,
            keep_all_topos=keep_all_topos,
            no_h=no_h,
            rmsd_threshold=None if rmsd is None else float(rmsd),
            energy_window=None if ewin is None else float(ewin),
            energy_tolerance=None if etol is None else float(etol),
            imag=None if params.get("imag") is None else int(params["imag"]),
            max_conformers=(
                None if params.get("max_conformers") is None else int(params["max_conformers"])
            ),
        )

    def to_clean_kwargs(self, *, workers: int | None = None) -> dict[str, Any]:
        kwargs = {
            "threshold": self.rmsd_threshold if self.rmsd_threshold is not None else 0.25,
            "ewin": self.energy_window,
            "energy_tolerance": (
                self.energy_tolerance if self.energy_tolerance is not None else 0.05
            ),
            "noH": self.no_h,
            "dedup_only": self.dedup_only,
            "keep_all_topos": self.keep_all_topos,
            "imag": self.imag,
            "max_conformers": self.max_conformers,
        }
        if workers is not None:
            kwargs["workers"] = workers
        return kwargs


@dataclass(frozen=True)
class TSOptions:
    bond_atoms: tuple[int, int] | None = None
    rescue_scan: bool = DEFAULT_TS_RESCUE_SCAN
    bond_drift_threshold: float = DEFAULT_TS_BOND_DRIFT_THRESHOLD
    rmsd_threshold: float = DEFAULT_TS_RMSD_THRESHOLD
    scan_coarse_step: float = DEFAULT_SCAN_COARSE_STEP
    scan_fine_step: float = DEFAULT_SCAN_FINE_STEP
    scan_uphill_limit: int = DEFAULT_SCAN_UPHILL_LIMIT
    scan_max_steps: int | None = None
    scan_fine_half_window: float | None = None
    keep_scan_dirs: bool | None = None
    scan_backup: bool | None = None


@dataclass(frozen=True)
class ExecutionOptions:
    enable_dynamic_resources: bool = DEFAULT_ENABLE_DYNAMIC_RESOURCES
    resume_from_backups: bool = DEFAULT_RESUME_FROM_BACKUPS
    max_wall_time_seconds: float | None = None
    delete_work_dir: bool = DEFAULT_DELETE_WORK_DIR
    sandbox_root: str | None = None
    input_chk_dir: str | None = None
    allowed_executables: tuple[str, ...] = ()
    gaussian_write_chk: bool | None = None
    stop_check_interval_seconds: float = DEFAULT_STOP_CHECK_INTERVAL_SECONDS


@dataclass(frozen=True)
class GlobalOptions:
    gaussian_path: str = "g16"
    orca_path: str = "orca"
    cores_per_task: int = DEFAULT_CORES_PER_TASK
    total_memory: str = DEFAULT_TOTAL_MEMORY
    max_parallel_jobs: int = DEFAULT_MAX_PARALLEL_JOBS
    charge: int = DEFAULT_CHARGE
    multiplicity: int = DEFAULT_MULTIPLICITY
    rmsd_threshold: float = DEFAULT_RMSD_THRESHOLD
    energy_window: float | None = None
    energy_tolerance: float = 0.05
    noH: bool = False
    freeze: tuple[int, ...] = ()
    ts_bond_atoms: tuple[int, int] | None = None
    ts_rescue_scan: bool = DEFAULT_TS_RESCUE_SCAN
    scan_coarse_step: float = DEFAULT_SCAN_COARSE_STEP
    scan_fine_step: float = DEFAULT_SCAN_FINE_STEP
    scan_uphill_limit: int = DEFAULT_SCAN_UPHILL_LIMIT
    ts_bond_drift_threshold: float = DEFAULT_TS_BOND_DRIFT_THRESHOLD
    ts_rmsd_threshold: float = DEFAULT_TS_RMSD_THRESHOLD
    enable_dynamic_resources: bool = DEFAULT_ENABLE_DYNAMIC_RESOURCES
    resume_from_backups: bool = DEFAULT_RESUME_FROM_BACKUPS
    auto_clean: bool = DEFAULT_WORKFLOW_AUTO_CLEAN
    delete_work_dir: bool = DEFAULT_DELETE_WORK_DIR
    stop_check_interval_seconds: float = DEFAULT_STOP_CHECK_INTERVAL_SECONDS
    force_consistency: bool = DEFAULT_FORCE_CONSISTENCY
    sandbox_root: str | None = None
    input_chk_dir: str | None = None
    allowed_executables: tuple[str, ...] = ()
    gaussian_write_chk: bool | None = None
    max_wall_time_seconds: float | None = None
    keyword: str | None = None
    iprog: str = "orca"
    itask: str = "opt_freq"
    blocks: str | dict[str, Any] | None = None
    orca_maxcore: int | str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> GlobalOptions:
        raw = _as_dict(raw)
        allowed = raw.get("allowed_executables")
        if isinstance(allowed, str):
            allowed_tuple = tuple(item.strip() for item in allowed.split(",") if item.strip())
        elif isinstance(allowed, (list, tuple, set)):
            allowed_tuple = tuple(str(item).strip() for item in allowed if str(item).strip())
        else:
            allowed_tuple = ()

        return cls(
            gaussian_path=str(raw.get("gaussian_path", "g16")),
            orca_path=str(raw.get("orca_path", "orca")),
            cores_per_task=int(raw.get("cores_per_task", DEFAULT_CORES_PER_TASK)),
            total_memory=_validate_memory(str(raw.get("total_memory", DEFAULT_TOTAL_MEMORY))),
            max_parallel_jobs=int(raw.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS)),
            charge=int(raw.get("charge", DEFAULT_CHARGE)),
            multiplicity=int(raw.get("multiplicity", DEFAULT_MULTIPLICITY)),
            rmsd_threshold=float(raw.get("rmsd_threshold", DEFAULT_RMSD_THRESHOLD)),
            energy_window=(
                None if raw.get("energy_window") is None else float(raw.get("energy_window"))
            ),
            energy_tolerance=float(raw.get("energy_tolerance", 0.05)),
            noH=_coerce_bool_flag(raw.get("noH", False)),
            freeze=tuple(_coerce_freeze_indices(raw.get("freeze"))),
            ts_bond_atoms=_parse_pair(raw.get("ts_bond_atoms")),
            ts_rescue_scan=_coerce_bool_flag(raw.get("ts_rescue_scan", DEFAULT_TS_RESCUE_SCAN)),
            scan_coarse_step=float(raw.get("scan_coarse_step", DEFAULT_SCAN_COARSE_STEP)),
            scan_fine_step=float(raw.get("scan_fine_step", DEFAULT_SCAN_FINE_STEP)),
            scan_uphill_limit=int(raw.get("scan_uphill_limit", DEFAULT_SCAN_UPHILL_LIMIT)),
            ts_bond_drift_threshold=float(
                raw.get("ts_bond_drift_threshold", DEFAULT_TS_BOND_DRIFT_THRESHOLD)
            ),
            ts_rmsd_threshold=float(raw.get("ts_rmsd_threshold", DEFAULT_TS_RMSD_THRESHOLD)),
            enable_dynamic_resources=_coerce_bool_flag(
                raw.get("enable_dynamic_resources", DEFAULT_ENABLE_DYNAMIC_RESOURCES)
            ),
            resume_from_backups=_coerce_bool_flag(
                raw.get("resume_from_backups", DEFAULT_RESUME_FROM_BACKUPS)
            ),
            auto_clean=_coerce_bool_flag(raw.get("auto_clean", DEFAULT_WORKFLOW_AUTO_CLEAN)),
            delete_work_dir=_coerce_bool_flag(raw.get("delete_work_dir", DEFAULT_DELETE_WORK_DIR)),
            stop_check_interval_seconds=float(
                raw.get("stop_check_interval_seconds", DEFAULT_STOP_CHECK_INTERVAL_SECONDS)
            ),
            force_consistency=_coerce_bool_flag(
                raw.get("force_consistency", DEFAULT_FORCE_CONSISTENCY)
            ),
            sandbox_root=(
                None if raw.get("sandbox_root") in {None, ""} else str(raw.get("sandbox_root"))
            ),
            input_chk_dir=(
                None if raw.get("input_chk_dir") in {None, ""} else str(raw.get("input_chk_dir"))
            ),
            allowed_executables=allowed_tuple,
            gaussian_write_chk=(
                None
                if raw.get("gaussian_write_chk") is None
                else _coerce_bool_flag(raw.get("gaussian_write_chk"))
            ),
            max_wall_time_seconds=(
                None
                if raw.get("max_wall_time_seconds") is None
                else float(raw.get("max_wall_time_seconds"))
            ),
            keyword=None if raw.get("keyword") is None else str(raw.get("keyword")),
            iprog=_normalize_iprog_label(raw.get("iprog", "orca")),
            itask=_itask_label(raw.get("itask", "opt_freq")),
            blocks=raw.get("blocks"),
            orca_maxcore=raw.get("orca_maxcore", raw.get("maxcore")),
        )


@dataclass(frozen=True)
class CalcStepParams:
    program: ProgramName
    task: TaskName
    keyword: str
    gaussian_path: str
    orca_path: str
    resources: ResourceOptions
    charge: int
    multiplicity: int
    freeze: tuple[int, ...]
    cleanup: CleanupOptions
    ts: TSOptions
    execution: ExecutionOptions
    blocks: str | dict[str, Any] | None = None
    orca_maxcore: int | str | None = None
    gaussian_modredundant: str | list[str] | None = None
    gaussian_link0: str | list[str] | None = None
    ibkout: int | None = None

    @classmethod
    def from_params(
        cls,
        params: dict[str, Any],
        global_options: GlobalOptions,
        *,
        input_chk_dir: str | None = None,
    ) -> CalcStepParams:
        params = _as_dict(params)
        program = _normalize_iprog_label(params.get("iprog", global_options.iprog))
        if program not in {"g16", "orca"}:
            raise ValueError(f"Unsupported calc program: {program}")
        task = _itask_label(params.get("itask", global_options.itask))
        if task not in {"opt", "sp", "freq", "opt_freq", "ts"}:
            raise ValueError(f"Unsupported calc task: {task}")
        keyword = params.get("keyword", global_options.keyword)
        if keyword is None or not str(keyword).strip():
            raise ValueError("calc step requires a non-empty keyword")

        freeze = ()
        if task in {"opt", "opt_freq", "ts"}:
            freeze = tuple(_coerce_freeze_indices(params.get("freeze", global_options.freeze)))

        ts_pair = _parse_pair(params.get("ts_bond_atoms")) or global_options.ts_bond_atoms
        if ts_pair is None and len(freeze) >= 2:
            ts_pair = (freeze[0], freeze[1])

        allowed = params.get("allowed_executables", global_options.allowed_executables)
        if isinstance(allowed, str):
            allowed_tuple = tuple(item.strip() for item in allowed.split(",") if item.strip())
        elif isinstance(allowed, (list, tuple, set)):
            allowed_tuple = tuple(str(item).strip() for item in allowed if str(item).strip())
        else:
            allowed_tuple = ()

        resources = ResourceOptions(
            cores_per_task=int(_merge(global_options, params, "cores_per_task")),
            total_memory=str(_merge(global_options, params, "total_memory")),
            max_parallel_jobs=int(_merge(global_options, params, "max_parallel_jobs")),
        )
        cleanup = CleanupOptions.from_params(params, global_options)
        execution = ExecutionOptions(
            enable_dynamic_resources=_coerce_bool_flag(
                _merge(global_options, params, "enable_dynamic_resources")
            ),
            resume_from_backups=_coerce_bool_flag(
                _merge(global_options, params, "resume_from_backups")
            ),
            max_wall_time_seconds=(
                None
                if _merge(global_options, params, "max_wall_time_seconds") is None
                else float(_merge(global_options, params, "max_wall_time_seconds"))
            ),
            delete_work_dir=_coerce_bool_flag(_merge(global_options, params, "delete_work_dir")),
            sandbox_root=_merge(global_options, params, "sandbox_root"),
            input_chk_dir=input_chk_dir or _merge(global_options, params, "input_chk_dir"),
            allowed_executables=allowed_tuple,
            gaussian_write_chk=(
                None
                if _merge(global_options, params, "gaussian_write_chk") is None
                else _coerce_bool_flag(_merge(global_options, params, "gaussian_write_chk"))
            ),
            stop_check_interval_seconds=float(
                _merge(global_options, params, "stop_check_interval_seconds")
            ),
        )
        ts = TSOptions(
            bond_atoms=ts_pair,
            rescue_scan=(
                _coerce_bool_flag(params.get("ts_rescue_scan", global_options.ts_rescue_scan))
                if task == "ts"
                else False
            ),
            bond_drift_threshold=float(
                params.get("ts_bond_drift_threshold", global_options.ts_bond_drift_threshold)
            ),
            rmsd_threshold=float(params.get("ts_rmsd_threshold", global_options.ts_rmsd_threshold)),
            scan_coarse_step=float(params.get("scan_coarse_step", global_options.scan_coarse_step)),
            scan_fine_step=float(params.get("scan_fine_step", global_options.scan_fine_step)),
            scan_uphill_limit=int(
                params.get("scan_uphill_limit", global_options.scan_uphill_limit)
            ),
            scan_max_steps=(
                None if params.get("scan_max_steps") is None else int(params["scan_max_steps"])
            ),
            scan_fine_half_window=(
                None
                if params.get("scan_fine_half_window") is None
                else float(params["scan_fine_half_window"])
            ),
            keep_scan_dirs=(
                None
                if params.get("ts_rescue_keep_scan_dirs") is None
                else _coerce_bool_flag(params["ts_rescue_keep_scan_dirs"])
            ),
            scan_backup=(
                None
                if params.get("ts_rescue_scan_backup") is None
                else _coerce_bool_flag(params["ts_rescue_scan_backup"])
            ),
        )
        blocks = params.get("blocks", global_options.blocks)
        if program == "g16" and isinstance(blocks, dict):
            raise ValueError("Gaussian calc steps do not support dict 'blocks'")
        return cls(
            program=program,  # type: ignore[arg-type]
            task=task,  # type: ignore[arg-type]
            keyword=str(keyword),
            gaussian_path=str(params.get("gaussian_path", global_options.gaussian_path)),
            orca_path=str(params.get("orca_path", global_options.orca_path)),
            resources=resources,
            charge=int(params.get("charge", global_options.charge)),
            multiplicity=int(params.get("multiplicity", global_options.multiplicity)),
            freeze=freeze,
            cleanup=cleanup,
            ts=ts,
            execution=execution,
            blocks=blocks,
            orca_maxcore=params.get("orca_maxcore", global_options.orca_maxcore),
            gaussian_modredundant=params.get("gaussian_modredundant"),
            gaussian_link0=params.get("gaussian_link0"),
            ibkout=None if params.get("ibkout") is None else int(params["ibkout"]),
        )

    def canonical_dict(self) -> dict[str, Any]:
        data = json.loads(
            json.dumps(self.to_runtime_dict(include_runtime_paths=False), sort_keys=True)
        )
        return data if isinstance(data, dict) else {}

    def to_runtime_dict(self, *, include_runtime_paths: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "iprog": self.program,
            "itask": self.task,
            "keyword": self.keyword,
            "gaussian_path": self.gaussian_path,
            "orca_path": self.orca_path,
            "cores_per_task": self.resources.cores_per_task,
            "total_memory": self.resources.total_memory,
            "max_parallel_jobs": self.resources.max_parallel_jobs,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "freeze": ",".join(str(x) for x in self.freeze) if self.freeze else "0",
            "auto_clean": self.cleanup.enabled,
            "dedup_only": self.cleanup.dedup_only,
            "keep_all_topos": self.cleanup.keep_all_topos,
            "noH": self.cleanup.no_h,
            "rmsd_threshold": self.cleanup.rmsd_threshold,
            "energy_window": self.cleanup.energy_window,
            "energy_tolerance": self.cleanup.energy_tolerance,
            "enable_dynamic_resources": self.execution.enable_dynamic_resources,
            "resume_from_backups": self.execution.resume_from_backups,
            "delete_work_dir": self.execution.delete_work_dir,
            "stop_check_interval_seconds": self.execution.stop_check_interval_seconds,
        }
        if self.cleanup.imag is not None:
            data["imag"] = self.cleanup.imag
        if self.cleanup.max_conformers is not None:
            data["max_conformers"] = self.cleanup.max_conformers
        if self.blocks is not None:
            data["blocks"] = (
                format_orca_blocks(self.blocks) if isinstance(self.blocks, dict) else self.blocks
            )
        if self.orca_maxcore is not None:
            data["orca_maxcore"] = self.orca_maxcore
        if self.gaussian_modredundant is not None:
            data["gaussian_modredundant"] = self.gaussian_modredundant
        if self.gaussian_link0 is not None:
            data["gaussian_link0"] = self.gaussian_link0
        if self.ibkout is not None:
            data["ibkout"] = self.ibkout
        if self.ts.bond_atoms is not None:
            data["ts_bond_atoms"] = f"{self.ts.bond_atoms[0]},{self.ts.bond_atoms[1]}"
        if self.task == "ts":
            data.update(
                {
                    "ts_rescue_scan": self.ts.rescue_scan,
                    "ts_bond_drift_threshold": self.ts.bond_drift_threshold,
                    "ts_rmsd_threshold": self.ts.rmsd_threshold,
                    "scan_coarse_step": self.ts.scan_coarse_step,
                    "scan_fine_step": self.ts.scan_fine_step,
                    "scan_uphill_limit": self.ts.scan_uphill_limit,
                }
            )
            if self.ts.scan_max_steps is not None:
                data["scan_max_steps"] = self.ts.scan_max_steps
            if self.ts.scan_fine_half_window is not None:
                data["scan_fine_half_window"] = self.ts.scan_fine_half_window
            if self.ts.keep_scan_dirs is not None:
                data["ts_rescue_keep_scan_dirs"] = self.ts.keep_scan_dirs
            if self.ts.scan_backup is not None:
                data["ts_rescue_scan_backup"] = self.ts.scan_backup
        if self.execution.input_chk_dir:
            data["input_chk_dir"] = self.execution.input_chk_dir
        if self.execution.gaussian_write_chk is not None:
            data["gaussian_write_chk"] = self.execution.gaussian_write_chk
        if include_runtime_paths:
            if self.execution.sandbox_root:
                data["sandbox_root"] = self.execution.sandbox_root
            if self.execution.allowed_executables:
                data["allowed_executables"] = list(self.execution.allowed_executables)
            if self.execution.max_wall_time_seconds is not None:
                data["max_wall_time_seconds"] = self.execution.max_wall_time_seconds
        return data


@dataclass(frozen=True)
class StepConfig:
    name: str
    type: StepType
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowConfig:
    global_options: GlobalOptions
    steps: tuple[StepConfig, ...]
    raw: dict[str, Any]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> WorkflowConfig:
        if not isinstance(raw, dict):
            raise ValueError("workflow config root must be a mapping")
        global_options = GlobalOptions.from_mapping(raw.get("global"))
        raw_steps = raw.get("steps") or []
        if not isinstance(raw_steps, list):
            raise ValueError("workflow config 'steps' must be a list")
        steps: list[StepConfig] = []
        for index, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"step {index} must be a mapping")
            step_type = str(step.get("type", "")).strip().lower()
            if step_type == "gen":
                step_type = "confgen"
            if step_type == "task":
                step_type = "calc"
            if step_type not in {"confgen", "calc"}:
                raise ValueError(f"step {index} has unsupported type: {step_type!r}")
            name = str(step.get("name") or f"{step_type}_{index}")
            enabled = _coerce_bool_flag(step.get("enabled", True))
            params = _as_dict(step.get("params"))
            steps.append(
                StepConfig(name=name, type=step_type, enabled=enabled, params=dict(params))
            )  # type: ignore[arg-type]
        return cls(global_options=global_options, steps=tuple(steps), raw=raw)

    def as_legacy_shape(self) -> dict[str, Any]:
        return {
            "global": self.global_options.__dict__,
            "steps": [
                {
                    "name": step.name,
                    "type": step.type,
                    "enabled": step.enabled,
                    "params": dict(step.params),
                }
                for step in self.steps
            ],
            "raw": self.raw,
        }


def load_workflow_model(config_file: str | Path) -> WorkflowConfig:
    import yaml

    path = Path(config_file)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    if not path.is_file():
        raise ConfigurationError(f"Configuration path is not a file: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML configuration: {exc}") from exc
    try:
        return WorkflowConfig.from_mapping(raw)
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
