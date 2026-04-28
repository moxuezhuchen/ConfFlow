#!/usr/bin/env python3

"""Execute a single calculation task through the shared executor flow."""

from __future__ import annotations

import os
from typing import Any

from ...core import models
from ...core.exceptions import (
    CalculationExecutionError,
    CalculationInputError,
    CalculationParseError,
    StopRequestedError,
)
from ...shared.defaults import DEFAULT_TS_BOND_DRIFT_THRESHOLD
from ..analysis import (
    _bond_length_from_xyz_lines,
    _keyword_requests_freq,
    _parse_ts_bond_atoms,
    is_rescue_enabled,
    validate_ts_bond_drift,
)
from ..config_types import ensure_calc_task_config
from ..policies import get_policy_for_config
from ..rescue import _ts_rescue_scan
from ..setup import get_itask
from . import executor

__all__ = [
    "TaskRunner",
]


class TaskRunner:
    def _get_policy(self, config: dict[str, Any]):
        return get_policy_for_config(config)

    def _try_rescue(self, cfg: dict, task_dict: dict, err_msg: str) -> dict | None:
        """Attempt TS rescue scan if enabled. Returns rescued result or None."""
        if is_rescue_enabled(cfg):
            rescued = _ts_rescue_scan(task_dict, err_msg)
            if rescued is not None:
                return rescued
        return None

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        """Map runtime exceptions to stable failure categories."""
        if isinstance(exc, StopRequestedError):
            return "stop_requested"
        if isinstance(exc, CalculationInputError):
            return "input_error"
        if isinstance(exc, CalculationParseError):
            return "parse_error"
        if isinstance(exc, CalculationExecutionError):
            msg = str(exc)
            if "Abnormal termination" in msg or "nonzero exit" in msg:
                return "abnormal_termination"
            return "exec_error"
        return "worker_exception"

    @staticmethod
    def _failed_result(
        task_dict: dict[str, Any],
        error: str,
        error_kind: str,
        error_details: str | None = None,
    ) -> dict[str, Any]:
        """Build a normalized failed/canceled result payload."""
        result: dict[str, Any] = {
            **task_dict,
            "status": "canceled" if error_kind == "stop_requested" else "failed",
            "error": error,
            "error_kind": error_kind,
        }
        if error_details is not None:
            result["error_details"] = error_details
        return result

    @staticmethod
    def _cleanup_work_dir_enabled(
        cfg: dict[str, Any],
        raw_config: Any,
    ) -> bool:
        if isinstance(raw_config, dict) and "delete_work_dir" not in raw_config:
            return True
        raw = cfg.get("delete_work_dir", True)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"0", "false", "no", "off", ""}:
                return False
            if normalized in {"1", "true", "yes", "on"}:
                return True
        return bool(raw)

    def run(self, task_info: models.TaskContext | dict[str, Any]):
        task_dict = (
            task_info.model_dump() if isinstance(task_info, models.TaskContext) else task_info
        )
        raw_config = task_dict["config"]
        cfg = ensure_calc_task_config(raw_config)
        task_dict = {**task_dict, "config": cfg}
        job, wd = task_dict["job_name"], task_dict["work_dir"]
        coords = task_dict["coords"]

        os.makedirs(wd, exist_ok=True)
        success = False

        policy = self._get_policy(cfg)

        # Stage cross-step artifacts (e.g., Gaussian .chk) into this job work_dir.
        try:
            executor.prepare_task_inputs(wd, job, cfg)
        except OSError:
            pass

        try:
            try:
                res = executor._run_calculation_step(wd, job, policy, coords, cfg)
            except StopRequestedError as e:
                return self._failed_result(task_dict, str(e), "stop_requested")
            except (CalculationInputError, CalculationExecutionError, CalculationParseError) as e:
                error_kind = self._classify_error(e)
                if get_itask(cfg) == 4:
                    rescued = self._try_rescue(cfg, task_dict, str(e))
                    if rescued is not None:
                        return rescued
                    if is_rescue_enabled(cfg):
                        error_kind = "rescue_failed"
                return self._failed_result(
                    task_dict,
                    str(e),
                    error_kind,
                    error_details=executor._get_error_details(wd, job, cfg, e, policy),
                )
            except Exception as e:
                error_kind = "worker_exception"
                if get_itask(cfg) == 4:
                    rescued = self._try_rescue(cfg, task_dict, str(e))
                    if rescued is not None:
                        return rescued
                    if is_rescue_enabled(cfg):
                        error_kind = "rescue_failed"
                return self._failed_result(
                    task_dict,
                    str(e),
                    error_kind,
                    error_details=executor._get_error_details(wd, job, cfg, e, policy),
                )

            final_coords = res.get("final_coords")
            itask = get_itask(cfg)
            if not final_coords:
                if itask == 1:
                    final_coords = task_dict["coords"]
                else:
                    return self._failed_result(task_dict, "No coords", "parse_error")

            num_imag_raw = res.get("num_imag_freqs")
            num_imag = 0 if num_imag_raw is None else int(num_imag_raw)
            lowest_freq = res.get("lowest_freq")

            if itask == 4 and _keyword_requests_freq(cfg):
                if num_imag_raw is None:
                    err_msg = (
                        "TS task keyword contains freq but no frequency info was parsed from output"
                    )
                    rescued = self._try_rescue(cfg, task_dict, err_msg)
                    if rescued is not None:
                        return rescued
                    error_kind = "rescue_failed" if is_rescue_enabled(cfg) else "parse_error"
                    return self._failed_result(task_dict, err_msg, error_kind)
                if num_imag != 1:
                    err_msg = f"TS task requires exactly 1 imaginary frequency, got {num_imag}"
                    if lowest_freq is not None:
                        err_msg += f" (lowest freq: {lowest_freq:.1f} cm⁻¹)"
                    rescued = self._try_rescue(cfg, task_dict, err_msg)
                    if rescued is not None:
                        return rescued
                    error_kind = "rescue_failed" if is_rescue_enabled(cfg) else "parse_error"
                    return self._failed_result(task_dict, err_msg, error_kind)

            if itask == 3 and num_imag > 0:
                err_msg = f"opt+freq task has {num_imag} imaginary frequencies"
                if lowest_freq is not None:
                    err_msg += f" (lowest freq: {lowest_freq:.1f} cm⁻¹)"
                return self._failed_result(task_dict, err_msg, "parse_error")

            ts_bond_atoms = cfg.get("ts_bond_atoms")
            ts_bond_length = None
            ts_pair = _parse_ts_bond_atoms(ts_bond_atoms)
            if ts_pair and final_coords:
                ts_bond_length = _bond_length_from_xyz_lines(final_coords, ts_pair[0], ts_pair[1])
                ts_bond_atoms = f"{ts_pair[0]},{ts_pair[1]}"

            if itask == 4 and not _keyword_requests_freq(cfg):
                bond_drift_threshold = float(
                    cfg.get("ts_bond_drift_threshold", DEFAULT_TS_BOND_DRIFT_THRESHOLD)
                )
                if ts_pair is not None:
                    err_msg = validate_ts_bond_drift(
                        task_dict["coords"],
                        final_coords,
                        ts_pair[0],
                        ts_pair[1],
                        bond_drift_threshold,
                    )
                    if err_msg is not None:
                        rescued = self._try_rescue(cfg, task_dict, err_msg)
                        if rescued is not None:
                            return rescued
                        error_kind = "rescue_failed" if is_rescue_enabled(cfg) else "parse_error"
                        return self._failed_result(task_dict, err_msg, error_kind)

            inherited_gc = None
            try:
                meta = task_dict.get("metadata") or {}
                # Once a step has produced G=... (Gibbs), stop propagating G_corr.
                # Only G_corr from freq/opt_freq steps is carried forward until it
                # is combined with a downstream SP energy to form Gibbs energy.
                if "G" in meta:
                    inherited_gc = None
                elif "G_corr" in meta:
                    inherited_gc = float(meta.get("G_corr"))
                elif "g_corr" in meta:
                    inherited_gc = float(meta.get("g_corr"))
            except (ValueError, TypeError):
                inherited_gc = None

            e, g, gc = res.get("e_low"), res.get("g_low"), res.get("g_corr")
            if itask in [2, 3, 4] and gc is None and e is not None and g is not None:
                gc = g - e
            if gc is None and inherited_gc is not None:
                gc = inherited_gc

            final_sp_energy = None
            if itask == 1:
                if e is not None and gc is not None:
                    final_sp_energy = e
                    final_val = e + gc
                    key = "final_gibbs_energy"
                else:
                    final_val = e
                    key = "energy"
            else:
                if g is not None:
                    final_val = g
                    key = "final_gibbs_energy"
                elif e is not None and gc is not None:
                    final_val = e + gc
                    key = "final_gibbs_energy"
                else:
                    final_val = e
                    key = "energy"

            if final_val is None:
                err_msg = "No energy parsed from calculation output"
                if itask == 4:
                    rescued = self._try_rescue(cfg, task_dict, err_msg)
                    if rescued is not None:
                        return rescued
                    error_kind = "rescue_failed" if is_rescue_enabled(cfg) else "parse_error"
                    return self._failed_result(task_dict, err_msg, error_kind)
                return self._failed_result(
                    task_dict,
                    err_msg,
                    "parse_error",
                )

            success = True
            result = {
                **task_dict,
                "status": "success",
                key: final_val,
                "final_sp_energy": final_sp_energy,
                "final_coords": final_coords,
                "num_imag_freqs": res.get("num_imag_freqs"),
                "lowest_freq": res.get("lowest_freq"),
                "g_corr": gc,
            }
            if ts_bond_atoms is not None:
                result["ts_bond_atoms"] = str(ts_bond_atoms)
            if ts_bond_length is not None:
                result["ts_bond_length"] = ts_bond_length
            return result
        finally:
            executor.handle_backups(
                wd,
                cfg,
                success,
                cleanup_work_dir=self._cleanup_work_dir_enabled(cfg, raw_config),
            )
