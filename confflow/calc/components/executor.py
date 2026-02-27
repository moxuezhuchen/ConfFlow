#!/usr/bin/env python3

"""Task execution and backup.

Responsible for:
- Invoking external programs to run calculations.
- Parsing output.
- Backing up / cleaning up work directories.
- Extracting error details and cleaning up lingering processes.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from typing import Any

from ..policies.base import CalculationPolicy
from ..setup import logger

__all__ = [
    "handle_backups",
    "prepare_task_inputs",
]

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


def handle_backups(
    work_dir: str, config: dict[str, Any], success: bool, cleanup_work_dir: bool = True
):
    """Back up calculation files and clean up the work directory."""
    ibkout = int(config.get("ibkout", 1))
    backup_dir = config.get("backup_dir")

    should_backup = ibkout != 0 and (
        ibkout == 1 or (ibkout == 2 and success) or (ibkout == 3 and (not success))
    )

    if should_backup and backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        # Also back up the .scan directory if it exists
        if os.path.exists(os.path.join(work_dir, "scan")):
            scan_src = os.path.join(work_dir, "scan")
            scan_dst = os.path.join(backup_dir, f"{os.path.basename(work_dir)}_scan")
            try:
                if os.path.exists(scan_dst):
                    shutil.rmtree(scan_dst)
                shutil.copytree(scan_src, scan_dst)
            except OSError as e:
                logger.warning(f"Failed to back up scan directory: {e}")
            except Exception as e:
                logger.debug(f"Scan directory backup exception: {e}")

        # Compat: rescue writes ts_failures.txt (and possibly diagnostic .txt);
        # back them up too.  For Gaussian (g16), checkpoint (.chk) files are
        # key intermediate products that also need backing up.
        backup_exts = {".inp", ".gjf", ".out", ".log", ".xyz", ".err", ".txt", ".chk", ".gbw"}
        for f in os.listdir(work_dir):
            if os.path.splitext(f)[1].lower() in backup_exts:
                src = os.path.join(work_dir, f)
                dst = os.path.join(backup_dir, f)
                try:
                    shutil.move(src, dst)
                except OSError:
                    try:
                        shutil.copy2(src, dst)
                    except OSError:
                        pass

    if cleanup_work_dir and os.path.exists(work_dir):
        try:
            shutil.rmtree(work_dir)
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
        input_chk_dir = config.get("input_chk_dir")
        if not input_chk_dir or not str(input_chk_dir).strip():
            return

        src = os.path.join(str(input_chk_dir), f"{job_name}.chk")
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
    except Exception as e:
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
    inp = os.path.join(work_dir, f"{job_name}.{policy.input_ext}")
    log = os.path.join(work_dir, f"{job_name}.{policy.log_ext}")

    policy.generate_input({"job_name": job_name, "coords": coords, "config": config}, inp)

    cmd = policy.get_execution_command(config, inp)
    env = policy.get_environment(config, cmd)

    with open(log, "w") as out, open(os.path.join(work_dir, f"{job_name}.err"), "w") as err:
        proc = subprocess.Popen(cmd, cwd=work_dir, stdout=out, stderr=err, env=env, text=True)

    stop_file = config.get("stop_beacon_file")
    while proc.poll() is None:
        if stop_file and os.path.exists(stop_file):
            proc.kill()
            raise RuntimeError("STOP signal received")
        time.sleep(int(config.get("stop_check_interval_seconds", 1)))

    if proc.returncode != 0:
        raise RuntimeError(f"{policy.name} nonzero exit: {proc.returncode}")
    if not policy.check_termination(log):
        raise RuntimeError("Abnormal termination")

    return policy.parse_output(log, config, is_sp_task)


def _save_config_hash(work_dir: str, config: dict[str, Any]):
    try:
        # Compat: hash is only used to identify similar tasks, not for security
        h = hashlib.md5(f"{config.get('itask')}_{config.get('iprog')}".encode()).hexdigest()[:8]
        with open(os.path.join(work_dir, ".config_hash"), "w") as f:
            f.write(h)
    except Exception as e:
        logger.debug(f"Config hash save failed: {e}")
