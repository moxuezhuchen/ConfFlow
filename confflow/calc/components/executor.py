#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""任务执行与备份。

负责：
- 调用外部程序运行计算
- 解析输出
- 备份/清理工作目录
- 错误详情提取与残留进程清理
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

from ..core import logger
from ..policies.base import CalculationPolicy

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


def handle_backups(
    work_dir: str, config: Dict[str, Any], success: bool, cleanup_work_dir: bool = True
):
    """备份计算文件并清理工作目录。"""
    ibkout = int(config.get("ibkout", 1))
    backup_dir = config.get("backup_dir")

    should_backup = ibkout != 0 and (
        ibkout == 1 or (ibkout == 2 and success) or (ibkout == 3 and (not success))
    )

    if should_backup and backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        # 增加 .scan 目录的备份支持（如果存在）
        if os.path.exists(os.path.join(work_dir, "scan")):
            scan_src = os.path.join(work_dir, "scan")
            scan_dst = os.path.join(backup_dir, f"{os.path.basename(work_dir)}_scan")
            try:
                if os.path.exists(scan_dst):
                    shutil.rmtree(scan_dst)
                shutil.copytree(scan_src, scan_dst)
            except (IOError, OSError) as e:
                logger.warning(f"备份 scan 目录失败: {e}")
            except Exception as e:
                logger.debug(f"备份 scan 目录异常: {e}")

        # 兼容：rescue 过程会写入 ts_failures.txt（以及可能的诊断 .txt），将其一并备份。
        # 对于 Gaussian(g16)，checkpoint(.chk) 往往是关键中间产物，也需要纳入备份。
        backup_exts = {".inp", ".gjf", ".out", ".log", ".xyz", ".err", ".txt", ".chk", ".gbw"}
        for f in os.listdir(work_dir):
            if os.path.splitext(f)[1].lower() in backup_exts:
                src = os.path.join(work_dir, f)
                dst = os.path.join(backup_dir, f)
                try:
                    shutil.move(src, dst)
                except (IOError, OSError):
                    try:
                        shutil.copy2(src, dst)
                    except (IOError, OSError):
                        pass

    if cleanup_work_dir and os.path.exists(work_dir):
        try:
            shutil.rmtree(work_dir)
        except (IOError, OSError) as e:
            logger.warning(f"删除工作目录失败 {work_dir}: {e}")
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
            except (IOError, OSError):
                pass


def prepare_task_inputs(work_dir: str, job_name: str, config: Dict[str, Any]) -> None:
    """将跨步骤依赖的输入工件回填到当前任务 work_dir。

    当前支持：Gaussian checkpoint (.chk) 的按 job_name(CID) 精确对应。

    约定：
    - config['input_chk_dir'] 指向任意来源步骤的 backups 目录（不限定“上一步”）
    - 该目录下文件名为 {job_name}.chk
    - 回填到当前 work_dir 后命名为 {job_name}.old.chk，并通过 config['gaussian_oldchk'] 注入到输入文件
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
        except Exception:
            # Fallback to a plain copy
            shutil.copy(src, dst)

        # Make GaussianPolicy emit %OldChk and also ensure %Chk is written for this step.
        config["gaussian_oldchk"] = dst_name
        config.setdefault("gaussian_write_chk", "true")
    except Exception as e:
        logger.debug(f"prepare_task_inputs failed for {job_name}: {e}")


def _cleanup_lingering_processes(
    config: Dict[str, Any], policy: Optional[CalculationPolicy] = None
):
    if policy:
        policy.cleanup_lingering_processes(config)


def _get_error_details(
    work_dir: str,
    job_name: str,
    config: Dict[str, Any],
    error: Exception,
    policy: Optional[CalculationPolicy] = None,
) -> str:
    if policy:
        return policy.get_error_details(work_dir, job_name, config)
    return str(error)


def _run_calculation_step(
    work_dir: str,
    job_name: str,
    policy: CalculationPolicy,
    coords,
    config: Dict[str, Any],
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


def _save_config_hash(work_dir: str, config: Dict[str, Any]):
    try:
        # 兼容旧逻辑：hash 仅用于标识同类任务，不做安全用途
        h = hashlib.md5(f"{config.get('itask')}_{config.get('iprog')}".encode()).hexdigest()[:8]
        with open(os.path.join(work_dir, ".config_hash"), "w") as f:
            f.write(h)
    except Exception as e:
        logger.debug(f"config hash 保存失败: {e}")
