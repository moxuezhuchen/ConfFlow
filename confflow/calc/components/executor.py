#!/usr/bin/env python3

"""Task execution and backup.

Responsible for:
- Invoking external programs to run calculations.
- Parsing output.
- Backing up / cleaning up work directories.
- Extracting error details and cleaning up lingering processes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from math import isfinite
from typing import Any

from ...core.exceptions import (
    CalculationExecutionError,
    CalculationInputError,
    CalculationParseError,
    ConfigurationError,
    StopRequestedError,
)
from ...core.path_policy import (
    resolve_sandbox_root,
    validate_cleanup_target,
    validate_managed_path,
)
from ..policies.base import CalculationPolicy
from ..setup import logger
from ..step_contract import (
    compute_calc_config_signature,
    load_calc_config_signature,
    record_calc_step_signature,
)

__all__ = [
    "handle_backups",
    "prepare_task_inputs",
]


def _backup_single_file(src: str, dst: str) -> bool:
    """Move or copy a single artifact into the backup directory."""
    try:
        shutil.move(src, dst)
        return True
    except OSError:
        try:
            shutil.copy2(src, dst)
            return True
        except OSError:
            return False


def _backup_scan_dir(work_dir: str, backup_dir: str) -> tuple[bool, str | None]:
    """Back up the ``scan`` directory when present."""
    scan_src = os.path.join(work_dir, "scan")
    if not os.path.exists(scan_src):
        return True, None

    scan_dst = os.path.join(backup_dir, f"{os.path.basename(work_dir)}_scan")
    try:
        if os.path.exists(scan_dst):
            shutil.rmtree(scan_dst)
        shutil.copytree(scan_src, scan_dst)
        return True, None
    except OSError as e:
        logger.warning(f"Failed to back up scan directory: {e}")
        return False, f"{os.path.basename(work_dir)}/scan"
    except (ValueError, TypeError) as e:
        logger.debug(f"Scan directory backup exception: {e}")
        return False, f"{os.path.basename(work_dir)}/scan"


def handle_backups(
    work_dir: str, config: dict[str, Any], success: bool, cleanup_work_dir: bool = True
):
    """Back up calculation files and clean up the work directory.

    Returns
    -------
    bool
        ``True`` when all requested backup operations succeeded, otherwise ``False``.
        Cleanup is skipped on backup failure to preserve crash artifacts.
    """
    sandbox_root = resolve_sandbox_root(config)
    work_dir = validate_managed_path(work_dir, label="work_dir", sandbox_root=sandbox_root)
    ibkout = int(config.get("ibkout", 1))
    backup_dir = config.get("backup_dir")
    backup_ok = True
    failed_artifacts: list[str] = []

    should_backup = ibkout != 0 and (
        ibkout == 1 or (ibkout == 2 and success) or (ibkout == 3 and (not success))
    )

    if should_backup and backup_dir:
        backup_dir = validate_managed_path(
            str(backup_dir),
            label="backup_dir",
            sandbox_root=sandbox_root,
        )
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except OSError as e:
            logger.warning(f"Failed to create backup directory {backup_dir}: {e}")
            backup_ok = False
            failed_artifacts.append(str(backup_dir))
        else:
            # Also back up the .scan directory if it exists.
            scan_ok, scan_failed = _backup_scan_dir(work_dir, backup_dir)
            if not scan_ok:
                backup_ok = False
                if scan_failed:
                    failed_artifacts.append(scan_failed)

            # Back up rescue diagnostics as well as Gaussian checkpoint artifacts.
            backup_exts = {
                ".inp",
                ".gjf",
                ".out",
                ".log",
                ".xyz",
                ".err",
                ".txt",
                ".chk",
                ".gbw",
            }
            for f in os.listdir(work_dir):
                if os.path.splitext(f)[1].lower() not in backup_exts:
                    continue
                src = os.path.join(work_dir, f)
                dst = os.path.join(backup_dir, f)
                if not _backup_single_file(src, dst):
                    backup_ok = False
                    failed_artifacts.append(f)
                    logger.warning("Failed to back up artifact: %s", src)

    if cleanup_work_dir and os.path.exists(work_dir):
        if should_backup and backup_dir and not backup_ok:
            logger.warning(
                "Skipping cleanup for %s because backup failed for: %s",
                work_dir,
                ", ".join(failed_artifacts) if failed_artifacts else "unknown artifacts",
            )
            return False
        try:
            shutil.rmtree(validate_cleanup_target(work_dir, sandbox_root=sandbox_root))
        except OSError as e:
            logger.warning(f"Failed to remove work directory {work_dir}: {e}")
            try:
                for f in os.listdir(work_dir):
                    fp = os.path.join(work_dir, f)
                    if os.path.isfile(fp) and (
                        f.endswith(".tmp")
                        or f.endswith(".chk")
                        or f.endswith(".rwf")
                        or f.endswith(".gbw")
                        or f.startswith("tmp")
                    ):
                        os.remove(fp)
            except OSError:
                pass
    return backup_ok


def prepare_task_inputs(work_dir: str, job_name: str, config: dict[str, Any]) -> None:
    """Stage cross-step input artifacts back into the current task work_dir.

    Currently supports: Gaussian checkpoint (.chk) exact match by job_name (CID).

    Conventions:
    - ``config['input_chk_dir']`` points to the backups directory of any source
      step (not limited to "the previous step").
    - Files in that directory are named ``{job_name}.chk``.
    - After staging, the file is renamed to ``{job_name}.old.chk`` in the
      current work_dir, and injected into the input file via
      ``config['gaussian_oldchk']``.
    """
    try:
        sandbox_root = resolve_sandbox_root(config)
        work_dir = validate_managed_path(work_dir, label="work_dir", sandbox_root=sandbox_root)
        input_chk_dir = config.get("input_chk_dir")
        if not input_chk_dir or not str(input_chk_dir).strip():
            return

        input_chk_dir = validate_managed_path(
            str(input_chk_dir),
            label="input_chk_dir",
            sandbox_root=sandbox_root,
        )
        src = os.path.join(input_chk_dir, f"{job_name}.chk")
        if not os.path.exists(src):
            return

        os.makedirs(work_dir, exist_ok=True)
        dst_name = f"{job_name}.old.chk"
        dst = os.path.join(work_dir, dst_name)
        try:
            shutil.copy2(src, dst)
        except OSError:
            # Fallback to a plain copy (copy2 can fail preserving metadata)
            shutil.copy(src, dst)

        # Make GaussianPolicy emit %OldChk and also ensure %Chk is written for this step.
        config["gaussian_oldchk"] = dst_name
        config.setdefault("gaussian_write_chk", "true")
    except (OSError, shutil.SameFileError) as e:
        logger.debug(f"prepare_task_inputs failed for {job_name}: {e}")


def _cleanup_lingering_processes(config: dict[str, Any], policy: CalculationPolicy | None = None):
    if policy:
        policy.cleanup_lingering_processes(config)


def _get_error_details(
    work_dir: str,
    job_name: str,
    config: dict[str, Any],
    error: Exception,
    policy: CalculationPolicy | None = None,
) -> str:
    if policy:
        return policy.get_error_details(work_dir, job_name, config)
    return str(error)


def _run_calculation_step(
    work_dir: str,
    job_name: str,
    policy: CalculationPolicy,
    coords,
    config: dict[str, Any],
    is_sp_task: bool = False,
):
    try:
        stop_check_interval = float(config.get("stop_check_interval_seconds", 1))
    except (TypeError, ValueError) as e:
        raise ConfigurationError("stop_check_interval_seconds must be a positive number") from e
    if not isfinite(stop_check_interval) or stop_check_interval <= 0:
        raise ConfigurationError("stop_check_interval_seconds must be a finite positive number")

    max_wall_time_raw = config.get("max_wall_time_seconds")
    max_wall_time_seconds: float | None = None
    if max_wall_time_raw is not None:
        try:
            max_wall_time_seconds = float(max_wall_time_raw)
        except (TypeError, ValueError) as e:
            raise ConfigurationError("max_wall_time_seconds must be a positive number") from e
        if not isfinite(max_wall_time_seconds) or max_wall_time_seconds <= 0:
            raise ConfigurationError("max_wall_time_seconds must be a finite positive number")

    inp = os.path.join(work_dir, f"{job_name}.{policy.input_ext}")
    log = os.path.join(work_dir, f"{job_name}.{policy.log_ext}")

    try:
        policy.generate_input({"job_name": job_name, "coords": coords, "config": config}, inp)
    except (OSError, TypeError, ValueError, RuntimeError) as e:
        raise CalculationInputError(f"Failed to generate input for {job_name}: {e}") from e

    cmd = policy.get_execution_command(config, inp)
    env = policy.get_environment(config, cmd)

    try:
        with open(log, "w") as out, open(os.path.join(work_dir, f"{job_name}.err"), "w") as err:
            proc = subprocess.Popen(cmd, cwd=work_dir, stdout=out, stderr=err, env=env, text=True)
    except OSError as e:
        raise CalculationExecutionError(f"Failed to launch {policy.name}: {e}") from e

    stop_file = config.get("stop_beacon_file")
    start_time = time.monotonic() if max_wall_time_seconds is not None else None
    while proc.poll() is None:
        if stop_file and os.path.exists(stop_file):
            proc.kill()
            raise StopRequestedError("STOP signal received")
        if (
            max_wall_time_seconds is not None
            and start_time is not None
            and time.monotonic() - start_time > max_wall_time_seconds
        ):
            proc.kill()
            proc.wait()
            raise CalculationExecutionError(
                f"{policy.name} exceeded max_wall_time_seconds={max_wall_time_seconds:g}"
            )
        time.sleep(stop_check_interval)

    if proc.returncode != 0:
        raise CalculationExecutionError(f"{policy.name} nonzero exit: {proc.returncode}")
    if not policy.check_termination(log):
        raise CalculationExecutionError("Abnormal termination")

    try:
        return policy.parse_output(log, config, is_sp_task)
    except (OSError, TypeError, ValueError, RuntimeError) as e:
        raise CalculationParseError(
            f"Failed to parse {policy.name} output for {job_name}: {e}"
        ) from e


def _save_config_hash(work_dir: str, config: dict[str, Any]):
    try:
        record_calc_step_signature(work_dir, config)
    except (OSError, ValueError, TypeError) as e:
        logger.debug(f"Config hash save failed: {e}")


def _load_config_hash(work_dir: str) -> str | None:
    return load_calc_config_signature(work_dir)


def _config_hash_matches(work_dir: str, config: dict[str, Any]) -> bool:
    stored = load_calc_config_signature(work_dir)
    if stored is None:
        return False
    return stored == compute_calc_config_signature(config)
