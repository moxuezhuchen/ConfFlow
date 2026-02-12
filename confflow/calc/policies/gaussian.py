#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Gaussian Calculation Policy."""

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
    compute_gaussian_mem,
    gaussian_apply_freeze,
    normalize_blocks,
    normalize_gaussian_keyword,
    parse_freeze_indices,
)

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None

logger = logging.getLogger("confflow.calc.policies.gaussian")


class GaussianPolicy(CalculationPolicy):
    @property
    def name(self) -> str:
        return "gaussian"

    @property
    def input_ext(self) -> str:
        return "gjf"

    @property
    def log_ext(self) -> str:
        return "log"

    def generate_input(self, task_info: Dict[str, Any], inp_file_path: str) -> None:
        config = task_info["config"]
        template = BUILTIN_TEMPLATES["gaussian"]

        cores = int(config.get("cores_per_task", 4))
        memory = compute_gaussian_mem(config)
        keyword_line = normalize_gaussian_keyword(config.get("keyword", "#p"))
        charge = config.get("charge", 0)
        multiplicity = config.get("multiplicity", 1)

        solvent_block, custom_block = normalize_blocks(
            config.get("solvent_block", ""), config.get("custom_block", "")
        )
        blocks_str = config.get("blocks", "")
        if isinstance(blocks_str, dict): # 极少见，但为了健壮性
            from ..components.input_helpers import format_orca_blocks
            blocks_str = format_orca_blocks(blocks_str)
        
        extra_section = solvent_block + custom_block
        if blocks_str:
            if extra_section and not extra_section.endswith("\n"):
                extra_section += "\n"
            extra_section += str(blocks_str).strip() + "\n"

        # Handle modredundant input
        mr = config.get("gaussian_modredundant")
        if mr is not None and str(mr).strip():
            mr_str = ""
            if isinstance(mr, (list, tuple)):
                mr_str = "\n".join(str(x).rstrip() for x in mr if str(x).strip())
            else:
                mr_str = str(mr).strip()

            if mr_str:
                if extra_section and not extra_section.endswith("\n"):
                    extra_section += "\n"
                extra_section += mr_str + "\n"

        coords_lines = task_info["coords"]

        freeze_indices = parse_freeze_indices(config.get("freeze", "0"))
        coords_str = gaussian_apply_freeze(coords_lines, freeze_indices)

        # Link0 directives (e.g., %Chk / %OldChk). Keep optional for backward compatibility.
        link0_lines: List[str] = []

        # Optional: inherit checkpoint from previous step
        oldchk = config.get("gaussian_oldchk") or config.get("gaussian_oldchk_file")
        if oldchk is not None and str(oldchk).strip():
            link0_lines.append(f"%OldChk={str(oldchk).strip()}")

        # Optional: write checkpoint for downstream reuse (default: enabled)
        write_chk_raw = config.get("gaussian_write_chk", "true")
        write_chk = str(write_chk_raw).strip().lower() not in {"0", "false", "no", "off"}
        chk_name = config.get("gaussian_chk") or config.get("gaussian_chk_file")
        if write_chk:
            chk = str(chk_name).strip() if chk_name is not None and str(chk_name).strip() else None
            if chk is None:
                chk = f"{task_info['job_name']}.chk"
            
            # Ensure %Chk is in link0_lines
            chk_line = f"%Chk={chk}"
            if chk_line not in link0_lines:
                link0_lines.insert(0, chk_line)

        # User-provided link0 lines
        user_link0 = config.get("gaussian_link0")
        if user_link0 is not None and str(user_link0).strip():
            if isinstance(user_link0, (list, tuple)):
                for ln in user_link0:
                    s = str(ln).strip()
                    if s:
                        link0_lines.append(s)
            else:
                for ln in str(user_link0).splitlines():
                    s = ln.strip()
                    if s:
                        link0_lines.append(s)

        link0 = "".join(ln.rstrip() + "\n" for ln in link0_lines)

        content = template.format(
            link0=link0,
            cores=cores,
            memory=memory,
            keyword_line=keyword_line,
            job_name=task_info["job_name"],
            charge=charge,
            multiplicity=multiplicity,
            coordinates=coords_str,
            extra_section=extra_section,
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

        itask = get_itask(config)

        # NOTE:
        # - Gaussian Archive section (e.g., \HF=...) is convenient but can be misleading in large
        #   concatenated logs or in rare wrapped/truncated cases.
        # - Archive numeric fields can be wrapped across lines (e.g., "\HF=-3\n 576.57...");
        #   remove all whitespace so wrapped numbers are reconstructed.
        # - For robustness we prefer the final "SCF Done" electronic energy; only fall back to
        #   Archive values if SCF Done is unavailable.
        compact = re.sub(r"\s+", "", content.replace("D", "E"))
        energy = None
        if sl := re.findall(r"SCF Done:.*", content):
            try:
                energy = float(sl[-1].replace("D", "E").split()[4])
            except Exception:
                energy = None
        if energy is None:
            # Use the last occurrence, in case Archive appears multiple times.
            hfs = re.findall(r"\\HF=([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)", compact)
            if hfs:
                energy = float(hfs[-1])

        if is_sp_task:
            e_high = energy
        else:
            e_low = energy

        cs = content.replace("D", "E")
        if m := re.search(
            r"Sum\s+of\s+electronic\s+and\s+thermal\s+Free\s+Energies=\s*(\S+)",
            cs,
        ):
            g_low = float(m.group(1))
        # Archive Gibbs as a fallback only (avoid overriding the explicit thermochemistry line).
        if g_low is None:
            gibbs_vals = re.findall(r"\\Gibbs=([-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?)", compact)
            if gibbs_vals:
                g_low = float(gibbs_vals[-1])

        if m := re.search(r"Thermal\s+correction\s+to\s+Gibbs\s+Free\s+Energy=\s*(\S+)", cs):
            g_corr = float(m.group(1))

        if fm := re.findall(r"Frequencies --\s+([-\d\.\s]+)", content):
            all_freqs = [float(f) for f in " ".join(fm).split()]
            num_imag_freqs = sum(1 for f in all_freqs if f < 0)
            if all_freqs:
                lowest_freq = min(all_freqs)

        final_coords = parse_last_geometry(log_file, 1)

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
        path_key = "gaussian_path"
        default_exe = "g16"
        prog_path = config.get(path_key) or default_exe
        cmd = shlex.split(str(prog_path)) + [os.path.basename(inp_file)]
        return cmd

    def get_environment(self, config: Dict[str, Any], cmd: List[str]) -> Dict[str, str]:
        env = os.environ.copy()
        if len(cmd) > 0 and os.path.isabs(cmd[0]):
            env["GAUSS_EXEDIR"] = os.path.dirname(cmd[0])
        return env

    def check_termination(self, log_file: str) -> bool:
        return _check_termination(log_file, "gaussian")

    def get_error_details(self, work_dir: str, job_name: str, config: Dict[str, Any]) -> str:
        log = os.path.join(work_dir, f"{job_name}.{self.log_ext}")
        details = []
        if os.path.exists(log):
            try:
                with open(log, "rb") as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 2000))
                    tail = f.read().decode("utf-8", errors="ignore")
                    if "Error termination" in tail:
                        details.append("程序异常终止")
                    if "Convergence failure" in tail or "SCF NOT CONVERGED" in tail:
                        details.append("SCF不收敛")
                    if "memory" in tail.lower():
                        details.append("内存不足")
            except Exception as e:
                logger.debug(f"读取错误日志失败 {log}: {e}")
        return " | ".join(details)

    def cleanup_lingering_processes(self, config: Dict[str, Any]) -> None:
        if psutil is None:
            return
        targets = ["g16", "l9999.exe"]
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if any(t in (proc.info.get("name") or "") for t in targets):
                    proc.terminate()
            except Exception as e:
                logger.debug(f"清理进程失败 {proc.info.get('pid')}: {e}")
