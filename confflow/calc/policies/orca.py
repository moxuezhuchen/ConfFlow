#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ORCA Calculation Policy."""

from __future__ import annotations

import logging
import os
import re
import shlex
from typing import Any, Dict, List, Optional

from .base import CalculationPolicy
from ..constants import BUILTIN_TEMPLATES
from ..core import get_itask
from ..geometry import check_termination as _check_termination
from ..geometry import parse_last_geometry
from ..components.input_helpers import (
    compute_orca_maxcore,
    normalize_blocks,
    orca_constraint_block,
    parse_freeze_indices,
)

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None

logger = logging.getLogger("confflow.calc.policies.orca")


class OrcaPolicy(CalculationPolicy):
    @property
    def name(self) -> str:
        return "orca"

    @property
    def input_ext(self) -> str:
        return "inp"

    @property
    def log_ext(self) -> str:
        return "out"

    def generate_input(self, task_info: Dict[str, Any], inp_file_path: str) -> None:
        config = task_info["config"]
        template = BUILTIN_TEMPLATES["orca"]

        cores = int(config.get("cores_per_task", 4))
        memory = compute_orca_maxcore(config)

        keyword_line = config.get("keyword", "#p")  # Default fallback
        charge = config.get("charge", 0)
        multiplicity = config.get("multiplicity", 1)

        solvent_block, custom_block = normalize_blocks(
            config.get("solvent_block", ""), config.get("custom_block", "")
        )

        constraint_block = ""
        freeze = config.get("freeze", "")
        itask_val = config.get("itask", "opt")

        # Only apply freeze constraint for opt/opt_freq tasks in ORCA
        if freeze and itask_val in ("opt", "opt_freq"):
            freeze_atoms = parse_freeze_indices(freeze)
            constraint_block = orca_constraint_block(freeze_atoms)

        coords_str = "\n".join(task_info["coords"])

        content = template.format(
            cores=cores,
            memory=memory,
            keyword=keyword_line,
            solvent_block=solvent_block,
            custom_block=custom_block,
            constraint_block=constraint_block,
            charge=charge,
            multiplicity=multiplicity,
            coordinates=coords_str,
        )

        with open(inp_file_path, "w") as f:
            f.write(content)

    def parse_output(
        self, log_file: str, config: Dict[str, Any], is_sp_task: bool = False
    ) -> Dict[str, Any]:
        if not os.path.exists(log_file):
            return {}

        with open(log_file, "r", errors="ignore") as f:
            content = f.read()

        e_low = None
        g_low = None
        num_imag_freqs = None
        g_corr = None
        e_high = None
        lowest_freq = None

        if is_sp_task:
            if m := re.search(r"FINAL SINGLE POINT ENERGY\s+([\d.-]+)", content):
                e_high = float(m.group(1))
        else:
            if m := re.search(r"G-E\(el\)\s+\.\.\.\s+([\d.-]+)\s+Eh", content):
                g_corr = float(m.group(1))
            if m := re.search(r"Final Gibbs free energy\s+\.\.\.\s+([\d.-]+)\s+Eh", content):
                g_low = float(m.group(1))
            if g_low is None and (
                m := re.search(r"FINAL SINGLE POINT ENERGY\s+([\d.-]+)", content)
            ):
                e_low = float(m.group(1))
            if "VIBRATIONAL FREQUENCIES" in content:
                freq_section = content.split("VIBRATIONAL FREQUENCIES")[-1]
                all_freqs = [float(f) for f in re.findall(r"\d+:\s+([-\d.]+)\s+cm", freq_section)]
                num_imag_freqs = sum(1 for f in all_freqs if f < 0)
                real_freqs = [f for f in all_freqs[6:] if abs(f) > 0.1]
                if real_freqs:
                    lowest_freq = min(real_freqs)

        final_coords = parse_last_geometry(log_file, 2)

        return {
            "e_low": e_low,
            "g_low": g_low,
            "g_corr": g_corr,
            "e_high": e_high,
            "num_imag_freqs": num_imag_freqs,
            "lowest_freq": lowest_freq,
            "final_coords": final_coords,
        }

    def get_execution_command(self, config: Dict[str, Any], inp_file: str) -> List[str]:
        path_key = "orca_path"
        default_exe = "orca"
        prog_path = config.get(path_key) or default_exe
        cmd = shlex.split(str(prog_path)) + [os.path.basename(inp_file)]
        return cmd

    def check_termination(self, log_file: str) -> bool:
        return _check_termination(log_file, "orca")

    def get_error_details(self, work_dir: str, job_name: str, config: Dict[str, Any]) -> str:
        log = os.path.join(work_dir, f"{job_name}.{self.log_ext}")
        details = []
        if os.path.exists(log):
            try:
                with open(log, "rb") as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 2000))
                    tail = f.read().decode("utf-8", errors="ignore")
                    if "ORCA finished by error" in tail:
                        details.append("程序异常终止")
                    if "SCF NOT CONVERGED" in tail:
                        details.append("SCF不收敛")
            except Exception as e:
                logger.debug(f"读取错误日志失败 {log}: {e}")
        return " | ".join(details)

    def cleanup_lingering_processes(self, config: Dict[str, Any]) -> None:
        if psutil is None:
            return
        targets = ["orca", "otool_xtb"]
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if any(t in (proc.info.get("name") or "") for t in targets):
                    proc.terminate()
            except Exception as e:
                logger.debug(f"清理进程失败 {proc.info.get('pid')}: {e}")
