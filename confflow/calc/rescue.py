#!/usr/bin/env python3

"""TS failure rescue via constrained scan.

Besides performing the rescue, also outputs scan diagnostic information:
- Prints a bond-length vs. energy table to the terminal (marks the MAX point).
- Writes ``<work_dir>/scan/scan_table.txt``, also saved alongside the backup directory.

Coordinate utilities, scan parameters, and the Scanner class have been split
into the ``scan_ops`` module.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..core.console import SINGLE_LINE, console, print_kv
from ..core.keyword_rewrite import make_scan_keyword_from_ts_keyword
from .analysis import (
    _bond_length_from_xyz_lines,
    _keyword_requests_freq,
    _parse_ts_bond_atoms,
    validate_ts_bond_drift,
)
from .components import executor
from .policies import get_policy_for_config as _get_policy
from .scan_ops import (
    _ConstrainedScanner,
    _emit_and_write_scan_table,
    _find_failed_ts_input_coords,
    _find_local_max,
    _ScanParams,
    _write_ts_failure_report,
)
from .setup import get_itask, parse_iprog

try:
    from ..config.defaults import DEFAULT_TS_BOND_DRIFT_THRESHOLD
except ImportError:  # pragma: no cover
    DEFAULT_TS_BOND_DRIFT_THRESHOLD = 0.4

logger = logging.getLogger("confflow.calc.rescue")

__all__: list[str] = []


def _prepare_rescue_context(
    task_info: dict[str, Any],
    fail_reason: str,
) -> dict[str, Any] | None:
    """Validate preconditions and prepare the context needed for rescue.

    Returns
    -------
    dict or None
        Context dict on success, None on failure.
    """
    cfg = task_info.get("config", {})
    job = task_info.get("job_name", "job")
    wd = task_info.get("work_dir", ".")

    prog_id = parse_iprog(cfg)
    if prog_id != 1:
        _write_ts_failure_report(
            wd,
            job,
            "rescue",
            f"not enabled: currently only Gaussian scan is supported (iprog={cfg.get('iprog')})",
        )
        return None

    pair = _parse_ts_bond_atoms(cfg.get("ts_bond_atoms"))
    if not pair:
        _write_ts_failure_report(
            wd, job, "rescue", "not enabled: missing ts_bond_atoms (cannot define scan bond)"
        )
        return None

    scan_kw = make_scan_keyword_from_ts_keyword(str(cfg.get("keyword", "") or ""))
    if not scan_kw:
        _write_ts_failure_report(wd, job, "rescue", "not enabled: scan keyword is empty")
        return None

    base_coords = _find_failed_ts_input_coords(wd, job, cfg)
    if base_coords:
        console.print("  Scan origin: TS input structure (from backup)")
        logger.info("Using the failed TS input structure as the scan origin")
    if not base_coords:
        base_coords = task_info.get("coords")
    if not base_coords:
        _write_ts_failure_report(
            wd,
            job,
            "rescue",
            "not enabled: missing TS input structure coordinates (neither job.gjf/job.com found nor task_info['coords'] provided)",
        )
        return None

    a1, a2 = pair
    r0 = _bond_length_from_xyz_lines(base_coords, a1, a2)
    if r0 is None:
        _write_ts_failure_report(
            wd, job, "rescue", "not enabled: unable to compute bond length from input structure"
        )
        return None

    console.print()
    console.print("TS RESCUE")
    console.print(SINGLE_LINE)
    print_kv("Job", str(job))
    print_kv("Bond", f"{a1}-{a2}")
    print_kv("r0", f"{r0:.3f} Å")
    print_kv("Reason", str(fail_reason))
    logger.info(f"Started TS rescue for {job} | bond {a1}-{a2} | r₀={r0:.3f} Å")

    return {
        "cfg": cfg,
        "job": job,
        "wd": wd,
        "a1": a1,
        "a2": a2,
        "r0": r0,
        "base_coords": base_coords,
    }


def _coarse_extend(
    scanner: _ConstrainedScanner,
    params: _ScanParams,
    r0: float,
    direction: int,
    start_coords: list[str],
    first_e: float | None,
    base_e: float | None,
    points: list[tuple[float, float, list[str]]],
) -> bool:
    """Extend coarse scan in the given direction. Return True if energy strictly rises."""
    best_e = None
    uphill = 0
    last_coords = start_coords
    all_increasing = True
    prev_e = first_e
    consecutive_down = 0
    k_max = min(params.max_steps, params.coarse_k_max)

    if prev_e is None:
        all_increasing = False
    elif base_e is not None and prev_e < base_e:
        consecutive_down = 1

    for k in range(2, k_max + 1):
        r = r0 + direction * params.coarse_step * k
        e, c, _ = scanner.run(last_coords, r)
        if e is None or c is None:
            uphill += 1
            all_increasing = False
            if uphill >= params.uphill_limit:
                break
            continue
        points.append((r, e, c))
        last_coords = c
        if prev_e is not None and e < prev_e:
            consecutive_down += 1
            all_increasing = False
            if consecutive_down >= 2:
                break
        else:
            consecutive_down = 0
        prev_e = e
        if best_e is None or e < best_e:
            best_e = e
            uphill = 0
        else:
            uphill += 1
            if uphill >= params.uphill_limit:
                break

    return bool(all_increasing and prev_e is not None and k_max >= 2)


def _run_coarse_and_fine_scan(
    scanner: _ConstrainedScanner,
    r0: float,
    base_coords: list[str],
    params: _ScanParams,
    wd: str,
    job: str,
    a1: int,
    a2: int,
    fail_reason: str,
) -> (
    tuple[
        float, list[str], list[tuple[float, float, list[str]]], list[tuple[float, float, list[str]]]
    ]
    | None
):
    """Perform coarse + fine scan. Return (r_best, coords_best, coarse_points, fine_points) or None."""
    points: list[tuple[float, float, list[str]]] = []
    initial_coords = base_coords

    # Constrained optimization at the initial point
    e0, c0, err0 = scanner.run(initial_coords, r0)
    if e0 is None or c0 is None:
        msg = (
            f"initial point r0={r0:.3f} Å constrained optimization failed, cannot continue rescue; "
            f"err={err0 or 'unknown'}; TS failure reason={fail_reason}"
        )
        _write_ts_failure_report(wd, job, "scan", msg)
        console.print(
            f"  ✗ initial point optimization failed | r₀={r0:.3f} Å | {err0 or 'unknown'}"
        )
        logger.warning(f"TS scan initial-point optimization failed for {job}")
        return None

    points.append((r0, e0, c0))
    initial_coords = c0

    # Probe one step in each ± direction
    e_m, c_m, _ = scanner.run(initial_coords, r0 - params.coarse_step)
    if e_m is not None and c_m is not None:
        points.append((r0 - params.coarse_step, e_m, c_m))

    e_p, c_p, _ = scanner.run(initial_coords, r0 + params.coarse_step)
    if e_p is not None and c_p is not None:
        points.append((r0 + params.coarse_step, e_p, c_p))

    direct_fine = e0 is not None and e_m is not None and e_p is not None and e_m < e0 and e_p < e0
    scan_pos = e_p is not None
    scan_neg = e_m is not None

    # Coarse scan extension
    if not direct_fine:
        rising_pos = (
            _coarse_extend(scanner, params, r0, +1, c_p or initial_coords, e_p, e0, points)
            if scan_pos
            else False
        )
        rising_neg = (
            _coarse_extend(scanner, params, r0, -1, c_m or initial_coords, e_m, e0, points)
            if scan_neg
            else False
        )

        if not scan_pos and not scan_neg:
            direct_fine = True

        if rising_pos or rising_neg:
            _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=None, selected_r=None)
            _write_ts_failure_report(
                wd,
                job,
                "scan",
                f"coarse scan energy strictly increasing within <={params.coarse_k_max} steps (~{params.coarse_step * params.coarse_k_max:.2f} Å), "
                f"rescue aborted; TS failure reason={fail_reason}",
            )
            return None

    # Find coarse scan maximum
    coarse_peak = _find_local_max(points)
    if coarse_peak is None:
        if direct_fine and e0 is not None and c0 is not None:
            coarse_peak = (r0, e0, c0)
        else:
            _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=None, selected_r=None)
            _write_ts_failure_report(
                wd,
                job,
                "scan",
                f"coarse scan found no local maximum; TS failure reason={fail_reason}",
            )
            return None

    r_peak = coarse_peak[0]
    center = r0 if direct_fine else r_peak
    r_left = center - params.fine_half_window
    r_right = center + params.fine_half_window

    # Fine scan
    fine_points: list[tuple[float, float, list[str]]] = []
    last_coords = initial_coords
    n_steps = max(2, int(round((r_right - r_left) / params.fine_step)))

    for i in range(n_steps + 1):
        r = r_left + params.fine_step * i
        e, c, _ = scanner.run(last_coords, r)
        if e is None or c is None:
            continue
        fine_points.append((r, e, c))
        last_coords = c

    fine_peak = _find_local_max(fine_points)
    if fine_peak is None:
        if fine_points:
            fine_peak = max(fine_points, key=lambda x: x[1])
        else:
            _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=None, selected_r=None)
            _write_ts_failure_report(
                wd, job, "scan", "fine scan has no valid points, rescue failed"
            )
            return None

    r_best, _, coords_best = fine_peak
    _emit_and_write_scan_table(wd, job, a1, a2, points, fine_points=fine_points, selected_r=r_best)
    return r_best, coords_best, points, fine_points


def _run_ts_reoptimization(
    cfg: dict[str, Any],
    task_info: dict[str, Any],
    wd: str,
    job: str,
    a1: int,
    a2: int,
    r_best: float,
    coords_best: list[str],
    base_coords: list[str],
    points: list[tuple[float, float, list[str]]],
    fine_points: list[tuple[float, float, list[str]]],
) -> dict[str, Any] | None:
    """Re-optimize TS from scan peak structure. Return successful result or None."""
    ts_wd = os.path.join(wd, "ts_rescue")
    os.makedirs(ts_wd, exist_ok=True)
    ts_job = f"{job}_rescue"

    ok = False
    try:
        ts_cfg = dict(cfg)
        ts_cfg["keyword"] = cfg.get("keyword", "")

        res = executor._run_calculation_step(
            ts_wd,
            ts_job,
            _get_policy(cfg),
            coords_best,
            ts_cfg,
            is_sp_task=False,
        )
        ok = True
        final_coords = res.get("final_coords")
        if not final_coords:
            raise RuntimeError("TS rescue produced no final structure")

        # Drift check
        if not _keyword_requests_freq(cfg):
            threshold = float(cfg.get("ts_bond_drift_threshold", DEFAULT_TS_BOND_DRIFT_THRESHOLD))
            drift_err = validate_ts_bond_drift(
                base_coords,
                final_coords,
                a1,
                a2,
                threshold,
                context="TS rescue",
            )
            if drift_err:
                raise RuntimeError(drift_err)

        # Imaginary frequency check
        num_imag_raw = res.get("num_imag_freqs")
        num_imag = 0 if num_imag_raw is None else int(num_imag_raw)
        lowest_freq = res.get("lowest_freq")
        if _keyword_requests_freq(cfg):
            if num_imag_raw is None:
                raise RuntimeError(
                    "TS rescue keyword contains freq but no frequency info was parsed from output"
                )
            if num_imag != 1:
                msg = f"TS rescue requires exactly 1 imaginary frequency, got {num_imag}"
                if lowest_freq is not None:
                    msg += f" (lowest freq: {lowest_freq:.1f} cm⁻¹)"
                raise RuntimeError(msg)

        # Assemble result
        e = res.get("e_low")
        g = res.get("g_low")
        gc = res.get("g_corr")
        itask = get_itask(cfg)
        if itask in [2, 3, 4] and gc is None and e is not None and g is not None:
            gc = g - e

        final_val = g if g is not None else e
        key = "final_gibbs_energy" if g is not None else "energy"

        out: dict[str, Any] = {
            **task_info,
            "status": "success",
            key: final_val,
            "final_coords": final_coords,
            "num_imag_freqs": res.get("num_imag_freqs"),
            "lowest_freq": res.get("lowest_freq"),
            "g_corr": gc,
            "rescued_by_scan": True,
            "scan_peak_bond": float(r_best),
        }

        ts_bond_length = _bond_length_from_xyz_lines(final_coords, a1, a2)
        out["ts_bond_atoms"] = f"{a1},{a2}"
        if ts_bond_length is not None:
            out["ts_bond_length"] = ts_bond_length

        console.print()
        console.print(
            f"  ✓ TS rescue succeeded | r_peak={r_best:.3f} Å | r_final={ts_bond_length:.3f} Å"
            if ts_bond_length
            else f"  ✓ TS rescue succeeded | r_peak={r_best:.3f} Å"
        )
        logger.info(f"TS rescue succeeded for {job} | r_peak={r_best:.3f} Å")
        return out
    except Exception as e:
        _write_ts_failure_report(wd, job, "ts_rescue", str(e))
        console.print()
        console.print(f"  ✗ TS rescue failed | {str(e)[:60]}")
        logger.warning(f"TS rescue failed for {job}: {e}")
        return None
    finally:
        try:
            keep = str(cfg.get("ts_rescue_keep_scan_dirs", "false")).lower() == "true"
            executor.handle_backups(ts_wd, cfg, success=ok, cleanup_work_dir=(not keep))
        except (OSError, RuntimeError) as _cleanup_err:
            logger.debug(f"TS rescue cleanup failed (non-fatal): {_cleanup_err}")


def _ts_rescue_scan(task_info: dict[str, Any], fail_reason: str) -> dict[str, Any] | None:
    """TS failure rescue via constrained scan (supports Gaussian and ORCA, determined by iprog in cfg)."""
    ctx = _prepare_rescue_context(task_info, fail_reason)
    if ctx is None:
        return None

    cfg = ctx["cfg"]
    job, wd = ctx["job"], ctx["wd"]
    a1, a2, r0 = ctx["a1"], ctx["a2"], ctx["r0"]
    base_coords = ctx["base_coords"]

    params = _ScanParams(cfg)
    scanner = _ConstrainedScanner(cfg, wd, a1, a2)

    peak = _run_coarse_and_fine_scan(
        scanner,
        r0,
        base_coords,
        params,
        wd,
        job,
        a1,
        a2,
        fail_reason,
    )
    if peak is None:
        return None

    r_best, coords_best, points, fine_points = peak
    return _run_ts_reoptimization(
        cfg,
        task_info,
        wd,
        job,
        a1,
        a2,
        r_best,
        coords_best,
        base_coords,
        points,
        fine_points,
    )
