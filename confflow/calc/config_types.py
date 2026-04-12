#!/usr/bin/env python3

"""Structured internal calc configuration models and legacy adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..core.models import _coerce_freeze_indices, _coerce_two_atom_indices
from ..shared.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_ENABLE_DYNAMIC_RESOURCES,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_RESUME_FROM_BACKUPS,
    DEFAULT_TOTAL_MEMORY,
    DEFAULT_TS_RESCUE_SCAN,
)

__all__ = [
    "Program",
    "TaskKind",
    "CleanupOptions",
    "TSOptions",
    "ExecutionOptions",
    "CalcTaskConfig",
    "ensure_calc_task_config",
]


class Program(str, Enum):
    """Supported calculation backends."""

    GAUSSIAN = "g16"
    ORCA = "orca"


class TaskKind(str, Enum):
    """Supported calc task kinds."""

    OPT = "opt"
    SP = "sp"
    FREQ = "freq"
    OPT_FREQ = "opt_freq"
    TS = "ts"


@dataclass(frozen=True)
class CleanupOptions:
    """Structured representation of refine/auto-clean options."""

    enabled: bool
    dedup_only: bool = False
    keep_all_topos: bool = False
    no_h: bool = False
    rmsd_threshold: float | None = None
    energy_window: float | None = None
    energy_tolerance: float | None = None

    def to_legacy_clean_opts(self) -> str:
        if not self.enabled:
            return ""

        opts: list[str] = []
        if self.dedup_only:
            opts.append("--dedup-only")
        if self.keep_all_topos:
            opts.append("--keep-all-topos")
        if self.no_h:
            opts.append("--noH")
        if self.rmsd_threshold is not None:
            opts.append(f"-t {self.rmsd_threshold}")
        if self.energy_window is not None:
            opts.append(f"-ewin {self.energy_window}")
        if self.energy_tolerance is not None:
            opts.append(f"--energy-tolerance {self.energy_tolerance}")
        return " ".join(opts)


@dataclass(frozen=True)
class TSOptions:
    """Structured TS/rescue-related options."""

    bond_atoms: tuple[int, int] | None = None
    rescue_scan: bool = False
    bond_drift_threshold: float | None = None
    rmsd_threshold: float | None = None
    scan_coarse_step: float | None = None
    scan_fine_step: float | None = None
    scan_uphill_limit: int | None = None
    scan_max_steps: int | None = None
    scan_fine_half_window: float | None = None
    keep_scan_dirs: bool | None = None
    scan_backup: bool | None = None


@dataclass(frozen=True)
class ExecutionOptions:
    """Structured execution/path policy options."""

    enable_dynamic_resources: bool = DEFAULT_ENABLE_DYNAMIC_RESOURCES
    resume_from_backups: bool = DEFAULT_RESUME_FROM_BACKUPS
    auto_clean: bool = False
    delete_work_dir: bool = False
    sandbox_root: str | None = None
    input_chk_dir: str | None = None
    allowed_executables: tuple[str, ...] = ()
    gaussian_write_chk: bool | None = None


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


def _normalize_program(value: Any) -> Program | str:
    s = str(value).strip().lower()
    if s in {"1", "g16", "gaussian", "gau", "g09", "g03"}:
        return Program.GAUSSIAN
    if s in {"2", "orca"}:
        return Program.ORCA
    return str(value).strip()


def _normalize_task(value: Any) -> TaskKind | str:
    s = str(value).strip().lower()
    mapping = {
        "0": TaskKind.OPT,
        "1": TaskKind.SP,
        "2": TaskKind.FREQ,
        "3": TaskKind.OPT_FREQ,
        "4": TaskKind.TS,
        "opt": TaskKind.OPT,
        "sp": TaskKind.SP,
        "freq": TaskKind.FREQ,
        "opt_freq": TaskKind.OPT_FREQ,
        "optfreq": TaskKind.OPT_FREQ,
        "ts": TaskKind.TS,
    }
    return mapping.get(s, str(value).strip())


def _normalize_allowed_executables(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _normalize_pair(value: Any) -> tuple[int, int] | None:
    pair = _coerce_two_atom_indices(value)
    if pair is None:
        return None
    a, b = pair
    if a <= 0 or b <= 0 or a == b:
        return None
    return (a, b)


def _parse_clean_opts(opts_str: str) -> dict[str, Any]:
    import shlex

    parsed: dict[str, Any] = {
        "dedup_only": False,
        "keep_all_topos": False,
        "noH": False,
        "rmsd_threshold": None,
        "energy_window": None,
        "energy_tolerance": None,
    }

    try:
        tokens = shlex.split(opts_str)
    except ValueError:
        tokens = opts_str.split()

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--dedup-only":
            parsed["dedup_only"] = True
            i += 1
        elif tok == "--keep-all-topos":
            parsed["keep_all_topos"] = True
            i += 1
        elif tok == "--noH":
            parsed["noH"] = True
            i += 1
        elif tok == "-t" and i + 1 < len(tokens):
            try:
                parsed["rmsd_threshold"] = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif tok == "-ewin" and i + 1 < len(tokens):
            try:
                parsed["energy_window"] = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif tok == "--energy-tolerance" and i + 1 < len(tokens):
            try:
                parsed["energy_tolerance"] = float(tokens[i + 1])
            except (TypeError, ValueError):
                pass
            i += 2
        elif tok.startswith("-t="):
            try:
                parsed["rmsd_threshold"] = float(tok.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        elif tok.startswith("-ewin="):
            try:
                parsed["energy_window"] = float(tok.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        elif tok.startswith("--energy-tolerance="):
            try:
                parsed["energy_tolerance"] = float(tok.split("=", 1)[1])
            except (IndexError, TypeError, ValueError):
                pass
            i += 1
        else:
            i += 1

    return parsed


class CalcTaskConfig(dict[str, Any]):
    """Structured calc config that remains dict-compatible for legacy call sites."""

    def __init__(
        self,
        *,
        program: Program | str,
        task: TaskKind | str,
        keyword: str,
        gaussian_path: str = "g16",
        orca_path: str = "orca",
        cores_per_task: int = DEFAULT_CORES_PER_TASK,
        total_memory: str = DEFAULT_TOTAL_MEMORY,
        max_parallel_jobs: int = DEFAULT_MAX_PARALLEL_JOBS,
        charge: int = DEFAULT_CHARGE,
        multiplicity: int = DEFAULT_MULTIPLICITY,
        freeze: tuple[int, ...] = (),
        cleanup: CleanupOptions | None = None,
        ts: TSOptions | None = None,
        execution: ExecutionOptions | None = None,
        blocks: str | dict[str, Any] | None = None,
        orca_maxcore: int | str | None = None,
        gaussian_modredundant: str | list[str] | tuple[str, ...] | None = None,
        gaussian_link0: str | list[str] | tuple[str, ...] | None = None,
        ibkout: int | str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.program = program
        self.task = task
        self.keyword = keyword
        self.freeze = tuple(int(x) for x in freeze)
        self.cleanup = cleanup or CleanupOptions(enabled=False)
        self.ts = ts or TSOptions()
        self.execution = execution or ExecutionOptions()

        payload: dict[str, Any] = {
            "iprog": program.value if isinstance(program, Program) else str(program),
            "itask": task.value if isinstance(task, TaskKind) else str(task),
            "keyword": keyword,
            "gaussian_path": gaussian_path,
            "orca_path": orca_path,
            "cores_per_task": int(cores_per_task),
            "total_memory": str(total_memory),
            "max_parallel_jobs": int(max_parallel_jobs),
            "charge": int(charge),
            "multiplicity": int(multiplicity),
            "freeze": list(self.freeze),
            "enable_dynamic_resources": self.execution.enable_dynamic_resources,
            "resume_from_backups": self.execution.resume_from_backups,
            "auto_clean": self.execution.auto_clean,
            "delete_work_dir": self.execution.delete_work_dir,
        }

        if self.execution.sandbox_root is not None:
            payload["sandbox_root"] = self.execution.sandbox_root
        if self.execution.input_chk_dir is not None:
            payload["input_chk_dir"] = self.execution.input_chk_dir
        if self.execution.allowed_executables:
            payload["allowed_executables"] = list(self.execution.allowed_executables)
        if self.execution.gaussian_write_chk is not None:
            payload["gaussian_write_chk"] = self.execution.gaussian_write_chk

        if blocks is not None:
            payload["blocks"] = blocks
        if orca_maxcore is not None:
            payload["orca_maxcore"] = orca_maxcore
        if gaussian_modredundant is not None:
            payload["gaussian_modredundant"] = gaussian_modredundant
        if gaussian_link0 is not None:
            payload["gaussian_link0"] = gaussian_link0
        if ibkout is not None:
            payload["ibkout"] = ibkout

        if self.ts.bond_atoms is not None:
            payload["ts_bond_atoms"] = [self.ts.bond_atoms[0], self.ts.bond_atoms[1]]
        payload["ts_rescue_scan"] = self.ts.rescue_scan
        if self.ts.bond_drift_threshold is not None:
            payload["ts_bond_drift_threshold"] = self.ts.bond_drift_threshold
        if self.ts.rmsd_threshold is not None:
            payload["ts_rmsd_threshold"] = self.ts.rmsd_threshold
        if self.ts.scan_coarse_step is not None:
            payload["scan_coarse_step"] = self.ts.scan_coarse_step
        if self.ts.scan_fine_step is not None:
            payload["scan_fine_step"] = self.ts.scan_fine_step
        if self.ts.scan_uphill_limit is not None:
            payload["scan_uphill_limit"] = self.ts.scan_uphill_limit
        if self.ts.scan_max_steps is not None:
            payload["scan_max_steps"] = self.ts.scan_max_steps
        if self.ts.scan_fine_half_window is not None:
            payload["scan_fine_half_window"] = self.ts.scan_fine_half_window
        if self.ts.keep_scan_dirs is not None:
            payload["ts_rescue_keep_scan_dirs"] = self.ts.keep_scan_dirs
        if self.ts.scan_backup is not None:
            payload["ts_rescue_scan_backup"] = self.ts.scan_backup

        if extra:
            payload.update(dict(extra))

        super().__init__(payload)

    def copy(self) -> CalcTaskConfig:
        return ensure_calc_task_config(self)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> CalcTaskConfig:
        if isinstance(mapping, CalcTaskConfig):
            return mapping

        raw = dict(mapping)

        cleanup_enabled = _coerce_bool_flag(raw.get("auto_clean", False))
        cleanup_raw = _parse_clean_opts(str(raw.get("clean_opts", ""))) if raw.get("clean_opts") else {}
        cleanup = CleanupOptions(
            enabled=cleanup_enabled,
            dedup_only=_coerce_bool_flag(raw.get("dedup_only", cleanup_raw.get("dedup_only", False))),
            keep_all_topos=_coerce_bool_flag(
                raw.get("keep_all_topos", cleanup_raw.get("keep_all_topos", False))
            ),
            no_h=_coerce_bool_flag(raw.get("noH", cleanup_raw.get("noH", False))),
            rmsd_threshold=(
                None
                if raw.get("rmsd_threshold", cleanup_raw.get("rmsd_threshold")) is None
                else float(raw.get("rmsd_threshold", cleanup_raw.get("rmsd_threshold")))
            ),
            energy_window=(
                None
                if raw.get("energy_window", cleanup_raw.get("energy_window")) is None
                else float(raw.get("energy_window", cleanup_raw.get("energy_window")))
            ),
            energy_tolerance=float(
                raw.get("energy_tolerance", cleanup_raw.get("energy_tolerance"))
            )
            if raw.get("energy_tolerance", cleanup_raw.get("energy_tolerance")) is not None
            else None,
        )

        freeze = tuple(_coerce_freeze_indices(raw.get("freeze")))
        ts_pair = _normalize_pair(raw.get("ts_bond_atoms"))
        ts = TSOptions(
            bond_atoms=ts_pair,
            rescue_scan=_coerce_bool_flag(raw.get("ts_rescue_scan", DEFAULT_TS_RESCUE_SCAN)),
            bond_drift_threshold=(
                None
                if raw.get("ts_bond_drift_threshold") is None
                else float(raw.get("ts_bond_drift_threshold"))
            ),
            rmsd_threshold=(
                None if raw.get("ts_rmsd_threshold") is None else float(raw.get("ts_rmsd_threshold"))
            ),
            scan_coarse_step=(
                None if raw.get("scan_coarse_step") is None else float(raw.get("scan_coarse_step"))
            ),
            scan_fine_step=(
                None if raw.get("scan_fine_step") is None else float(raw.get("scan_fine_step"))
            ),
            scan_uphill_limit=(
                None if raw.get("scan_uphill_limit") is None else int(raw.get("scan_uphill_limit"))
            ),
            scan_max_steps=(
                None if raw.get("scan_max_steps") is None else int(raw.get("scan_max_steps"))
            ),
            scan_fine_half_window=(
                None
                if raw.get("scan_fine_half_window") is None
                else float(raw.get("scan_fine_half_window"))
            ),
            keep_scan_dirs=(
                None
                if raw.get("ts_rescue_keep_scan_dirs") is None
                else _coerce_bool_flag(raw.get("ts_rescue_keep_scan_dirs"))
            ),
            scan_backup=(
                None
                if raw.get("ts_rescue_scan_backup") is None
                else _coerce_bool_flag(raw.get("ts_rescue_scan_backup"))
            ),
        )

        execution = ExecutionOptions(
            enable_dynamic_resources=_coerce_bool_flag(
                raw.get("enable_dynamic_resources", DEFAULT_ENABLE_DYNAMIC_RESOURCES)
            ),
            resume_from_backups=_coerce_bool_flag(
                raw.get("resume_from_backups", DEFAULT_RESUME_FROM_BACKUPS)
            ),
            auto_clean=cleanup_enabled,
            delete_work_dir=_coerce_bool_flag(raw.get("delete_work_dir", False)),
            sandbox_root=(
                str(raw.get("sandbox_root")).strip() if raw.get("sandbox_root") is not None else None
            ),
            input_chk_dir=(
                str(raw.get("input_chk_dir")).strip() if raw.get("input_chk_dir") is not None else None
            ),
            allowed_executables=_normalize_allowed_executables(raw.get("allowed_executables")),
            gaussian_write_chk=(
                None
                if raw.get("gaussian_write_chk") is None
                else _coerce_bool_flag(raw.get("gaussian_write_chk"))
            ),
        )

        known_keys = {
            "iprog",
            "itask",
            "keyword",
            "gaussian_path",
            "orca_path",
            "cores_per_task",
            "total_memory",
            "max_parallel_jobs",
            "charge",
            "multiplicity",
            "freeze",
            "clean_opts",
            "dedup_only",
            "keep_all_topos",
            "noH",
            "rmsd_threshold",
            "energy_window",
            "energy_tolerance",
            "enable_dynamic_resources",
            "resume_from_backups",
            "auto_clean",
            "delete_work_dir",
            "sandbox_root",
            "input_chk_dir",
            "allowed_executables",
            "gaussian_write_chk",
            "ts_bond_atoms",
            "ts_rescue_scan",
            "ts_bond_drift_threshold",
            "ts_rmsd_threshold",
            "scan_coarse_step",
            "scan_fine_step",
            "scan_uphill_limit",
            "scan_max_steps",
            "scan_fine_half_window",
            "ts_rescue_keep_scan_dirs",
            "ts_rescue_scan_backup",
            "blocks",
            "orca_maxcore",
            "gaussian_modredundant",
            "gaussian_link0",
            "ibkout",
        }

        return cls(
            program=_normalize_program(raw.get("iprog", Program.ORCA.value)),
            task=_normalize_task(raw.get("itask", TaskKind.OPT_FREQ.value)),
            keyword=str(raw.get("keyword", "")),
            gaussian_path=str(raw.get("gaussian_path", "g16")),
            orca_path=str(raw.get("orca_path", "orca")),
            cores_per_task=int(raw.get("cores_per_task", DEFAULT_CORES_PER_TASK)),
            total_memory=str(raw.get("total_memory", DEFAULT_TOTAL_MEMORY)),
            max_parallel_jobs=int(raw.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS)),
            charge=int(raw.get("charge", DEFAULT_CHARGE)),
            multiplicity=int(raw.get("multiplicity", DEFAULT_MULTIPLICITY)),
            freeze=freeze,
            cleanup=cleanup,
            ts=ts,
            execution=execution,
            blocks=raw.get("blocks"),
            orca_maxcore=raw.get("orca_maxcore"),
            gaussian_modredundant=raw.get("gaussian_modredundant"),
            gaussian_link0=raw.get("gaussian_link0"),
            ibkout=raw.get("ibkout"),
            extra={key: value for key, value in raw.items() if key not in known_keys},
        )

    def to_legacy_dict(self) -> dict[str, str]:
        data: dict[str, str] = {
            "iprog": self["iprog"],
            "itask": self["itask"],
            "keyword": str(self["keyword"]),
            "gaussian_path": str(self.get("gaussian_path", "g16")),
            "orca_path": str(self.get("orca_path", "orca")),
            "cores_per_task": str(self.get("cores_per_task", DEFAULT_CORES_PER_TASK)),
            "total_memory": str(self.get("total_memory", DEFAULT_TOTAL_MEMORY)),
            "max_parallel_jobs": str(self.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS)),
            "charge": str(self.get("charge", DEFAULT_CHARGE)),
            "multiplicity": str(self.get("multiplicity", DEFAULT_MULTIPLICITY)),
            "enable_dynamic_resources": str(self.execution.enable_dynamic_resources).lower(),
            "resume_from_backups": str(self.execution.resume_from_backups).lower(),
            "auto_clean": str(self.execution.auto_clean).lower(),
            "delete_work_dir": str(self.execution.delete_work_dir).lower(),
        }

        freeze_vals = self.get("freeze", [])
        if isinstance(freeze_vals, (list, tuple)):
            data["freeze"] = ",".join(str(x) for x in freeze_vals) if freeze_vals else "0"
        else:
            data["freeze"] = str(freeze_vals)

        if self.execution.sandbox_root:
            data["sandbox_root"] = self.execution.sandbox_root
        if self.execution.input_chk_dir:
            data["input_chk_dir"] = self.execution.input_chk_dir
        if self.execution.allowed_executables:
            data["allowed_executables"] = ",".join(self.execution.allowed_executables)
        if self.execution.gaussian_write_chk is not None:
            data["gaussian_write_chk"] = str(self.execution.gaussian_write_chk).lower()

        ts_pair = self.ts.bond_atoms
        if ts_pair is not None:
            data["ts_bond_atoms"] = f"{ts_pair[0]},{ts_pair[1]}"
        data["ts_rescue_scan"] = str(self.ts.rescue_scan).lower()

        for key in [
            "ts_bond_drift_threshold",
            "ts_rmsd_threshold",
            "scan_coarse_step",
            "scan_fine_step",
            "scan_uphill_limit",
            "scan_max_steps",
            "scan_fine_half_window",
            "ts_rescue_keep_scan_dirs",
            "ts_rescue_scan_backup",
            "blocks",
            "orca_maxcore",
            "gaussian_modredundant",
            "gaussian_link0",
            "ibkout",
            "backup_dir",
            "stop_beacon_file",
            "gaussian_oldchk",
            "gaussian_oldchk_file",
        ]:
            if key in self and self.get(key) is not None:
                data[key] = str(self[key])

        clean_opts = self.cleanup.to_legacy_clean_opts()
        if clean_opts:
            data["clean_opts"] = clean_opts

        passthrough = {
            key: value
            for key, value in self.items()
            if key not in data
            and key
            not in {
                "freeze",
                "ts_bond_atoms",
                "allowed_executables",
            }
            and value is not None
        }
        for key, value in passthrough.items():
            if isinstance(value, bool):
                data[key] = str(value).lower()
            elif isinstance(value, (list, tuple)):
                data[key] = ",".join(str(item) for item in value)
            else:
                data[key] = str(value)

        return {key: value for key, value in data.items() if value != ""}


def ensure_calc_task_config(config: Mapping[str, Any] | CalcTaskConfig) -> CalcTaskConfig:
    """Normalize legacy mappings or structured configs to CalcTaskConfig."""
    return CalcTaskConfig.from_mapping(config)
