#!/usr/bin/env python3

"""ORCA Calculation Policy."""

from __future__ import annotations

import copy
import logging
import os
import re
from typing import Any

from ...core.path_policy import validate_executable_setting
from ...shared.orca_blocks import format_orca_blocks
from ..components.input_helpers import (
    compute_orca_maxcore,
    parse_freeze_indices,
)
from ..constants import BUILTIN_TEMPLATES
from ..geometry import check_termination as _check_termination
from ..geometry import parse_last_geometry
from ..psutil_compat import maybe_import_psutil, psutil_exception_types
from .base import CalculationPolicy

__all__ = [
    "OrcaPolicy",
    "ORCA_POLICY",
]

psutil = maybe_import_psutil()

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

    def generate_input(self, task_info: dict[str, Any], inp_file_path: str) -> None:
        config = task_info["config"]
        template = BUILTIN_TEMPLATES["orca"]

        cores = int(config.get("cores_per_task", 4))
        memory = compute_orca_maxcore(config)

        keyword_line = config.get("keyword", "#p")  # Keep a minimal fallback keyword.
        charge = config.get("charge", 0)
        multiplicity = config.get("multiplicity", 1)

        # Normalize block handling for both string and dict inputs.
        blocks_config = config.get("blocks", "")

        blocks_dict = {}
        # ConfigParser values arrive as strings unless another layer converts them.
        # Prefer string mode, but keep dict mode for compatibility.
        is_dict_mode = isinstance(blocks_config, dict)

        if is_dict_mode:
            blocks_dict = copy.deepcopy(blocks_config)

        # Merge freeze constraints into the generated ORCA blocks.
        freeze = config.get("freeze", "")
        itask_val = config.get("itask", "opt")

        constraint_str = ""
        if freeze and itask_val in ("opt", "opt_freq", "ts", "optts"):
            freeze_atoms = parse_freeze_indices(freeze)
            if freeze_atoms:
                clist = [f"{{ C {int(idx) - 1} C }}" for idx in freeze_atoms]
                if is_dict_mode:
                    # Merge generated constraints into dict-mode blocks.
                    if "geom" not in blocks_dict:
                        blocks_dict["geom"] = {}
                    if "Constraints" not in blocks_dict["geom"]:
                        blocks_dict["geom"]["Constraints"] = clist
                    else:
                        existing = blocks_dict["geom"]["Constraints"]
                        if isinstance(existing, list):
                            for c in clist:
                                if c not in existing:
                                    existing.append(c)
                        elif isinstance(existing, str):
                            blocks_dict["geom"]["Constraints"] = existing.splitlines() + clist
                else:
                    # Render a standalone constraint block for string-mode input.
                    constraint_str = format_orca_blocks({"geom": {"Constraints": clist}})

        if is_dict_mode:
            generated_blocks = format_orca_blocks(blocks_dict)
        else:
            generated_blocks = format_orca_blocks(blocks_config) + constraint_str

        coords_str = "\n".join(task_info["coords"])

        content = template.format(
            cores=cores,
            memory=memory,
            keyword=keyword_line,
            generated_blocks=generated_blocks,
            charge=charge,
            multiplicity=multiplicity,
            coordinates=coords_str,
        )

        with open(inp_file_path, "w") as f:
            f.write(content)

    def parse_output(
        self, log_file: str, config: dict[str, Any], is_sp_task: bool = False
    ) -> dict[str, Any]:
        if not os.path.exists(log_file):
            return {}

        with open(log_file, errors="ignore") as f:
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

    def get_execution_command(self, config: dict[str, Any], inp_file: str) -> list[str]:
        path_key = "orca_path"
        default_exe = "orca"
        prog_path = config.get(path_key) or default_exe
        allowed = config.get("allowed_executables")
        if isinstance(allowed, str):
            allowed = [item.strip() for item in allowed.split(",") if item.strip()]
        executable = validate_executable_setting(
            prog_path,
            label=path_key,
            allowed_executables=allowed,
        )
        return [executable, os.path.basename(inp_file)]

    def check_termination(self, log_file: str) -> bool:
        return _check_termination(log_file, "orca")

    def get_error_details(self, work_dir: str, job_name: str, config: dict[str, Any]) -> str:
        log = os.path.join(work_dir, f"{job_name}.{self.log_ext}")
        details = []
        if os.path.exists(log):
            try:
                with open(log, "rb") as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 2000))
                    tail = f.read().decode("utf-8", errors="ignore")
                    if "ORCA finished by error" in tail:
                        details.append("Abnormal program termination")
                    if "SCF NOT CONVERGED" in tail:
                        details.append("SCF not converged")
            except OSError as e:
                logger.debug(f"Failed to read error log {log}: {e}")
        return " | ".join(details)

    def cleanup_lingering_processes(self, config: dict[str, Any]) -> None:
        del config
        if psutil is None:
            return
        targets = ["orca", "otool_xtb"]
        try:
            current = psutil.Process()
            processes = current.children(recursive=True)
        except psutil_exception_types(psutil) as e:
            logger.debug(f"Failed to enumerate descendant processes: {e}")
            return

        for proc in processes:
            try:
                info = getattr(proc, "info", {}) or {}
                name = info.get("name")
                if not name and hasattr(proc, "name"):
                    name = proc.name()
                if any(t in str(name or "") for t in targets):
                    proc.terminate()
            except psutil_exception_types(psutil) as e:
                pid = (getattr(proc, "info", {}) or {}).get("pid")
                logger.debug(f"Failed to clean up process {pid}: {e}")


#: Module-level singleton (stateless, safe to reuse)
ORCA_POLICY = OrcaPolicy()
