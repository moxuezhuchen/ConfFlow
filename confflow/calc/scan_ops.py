#!/usr/bin/env python3

"""TS rescue scan operations -- coordinate utilities, diagnostic tables, Scanner class.

Split from rescue.py to reduce single-file complexity.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table

from ..core import io as io_xyz
from ..core.console import SINGLE_LINE, console, print_kv
from ..core.constants import HARTREE_TO_KCALMOL
from ..core.keyword_rewrite import make_scan_keyword_from_ts_keyword
from .components import executor
from .policies import get_policy_for_config as _get_policy

logger = logging.getLogger("confflow.calc.rescue")

__all__: list[str] = []


# ---------------------------------------------------------------------------
# Coordinate utilities
# ---------------------------------------------------------------------------


def _coords_lines_to_xyz(coords_lines: list[str]):
    """Parse coordinate lines into (symbol, x, y, z) tuples.

    Handles optional prefix integers (e.g. Gaussian freeze codes) by
    treating the three rightmost columns as x, y, z.
    """
    try:
        out = []
        for ln in coords_lines:
            parts = ln.split()
            if len(parts) < 4:
                return None
            sym = parts[0]
            x_val, y_val, z_val = float(parts[-3]), float(parts[-2]), float(parts[-1])
            out.append((sym, x_val, y_val, z_val))
        return out
    except (ValueError, IndexError) as e:
        logger.debug(f"Failed to convert coordinate lines to XYZ: {e}")
        return None


def _xyz_to_coords_lines(xyz) -> list[str]:
    return [f"{sym:<2s} {x: >12.6f} {y: >12.6f} {z: >12.6f}" for sym, x, y, z in xyz]


def _set_bond_length_on_coords(
    coords_lines: list[str], a1: int, a2: int, target: float
) -> list[str] | None:
    """Adjust coordinates so that the a1-a2 bond length equals target (only moves a2)."""
    xyz = _coords_lines_to_xyz(coords_lines)
    if xyz is None:
        return None
    if a1 < 1 or a2 < 1 or a1 > len(xyz) or a2 > len(xyz) or a1 == a2:
        return None
    _, x1, y1, z1 = xyz[a1 - 1]
    sym2, x2, y2, z2 = xyz[a2 - 1]
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    r = (dx * dx + dy * dy + dz * dz) ** 0.5
    if r <= 1e-10:
        return None
    ux, uy, uz = dx / r, dy / r, dz / r
    new_x2, new_y2, new_z2 = (
        x1 + ux * float(target),
        y1 + uy * float(target),
        z1 + uz * float(target),
    )
    xyz[a2 - 1] = (sym2, new_x2, new_y2, new_z2)
    return _xyz_to_coords_lines(xyz)


def _read_gaussian_input_coords(path: str) -> list[str] | None:
    """Parse the coordinate block from a Gaussian input file (.gjf/.com)."""
    try:
        if not path or not os.path.exists(path):
            return None
        res = io_xyz.parse_gaussian_input(path)
        return res.get("raw_coords_lines")
    except (OSError, ValueError, IndexError, KeyError) as e:
        logger.debug(f"Failed to parse Gaussian input coordinates ({path}): {e}")
        return None


def _find_failed_ts_input_coords(wd: str, job: str, cfg: dict[str, Any]) -> list[str] | None:
    """Find the input structure coordinates for a failed TS task."""
    try:
        cand_paths: list[str] = [
            os.path.join(wd, f"{job}.gjf"),
            os.path.join(wd, f"{job}.com"),
        ]
        backup_dir = cfg.get("backup_dir")
        if backup_dir:
            cand_paths.extend(
                [
                    os.path.join(str(backup_dir), f"{job}.gjf"),
                    os.path.join(str(backup_dir), f"{job}.com"),
                ]
            )
        for p in cand_paths:
            coords = _read_gaussian_input_coords(p)
            if coords:
                return coords
        return None
    except (OSError, ValueError, IndexError) as e:
        logger.debug(f"Failed to find input coordinates for failed TS: {e}")
        return None


# ---------------------------------------------------------------------------
# Diagnostic reports
# ---------------------------------------------------------------------------


def _write_ts_failure_report(work_dir: str, job_name: str, stage: str, message: str) -> None:
    """Record TS task failure information to a report file."""
    try:
        os.makedirs(work_dir, exist_ok=True)
        path = os.path.join(work_dir, "ts_failures.txt")
        ts = datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {job_name} | {stage} | {message}\n")
    except OSError as e:
        logger.warning(f"Failed to write TS failure report (I/O error): {e}")
    except (UnicodeError, TypeError) as e:
        logger.warning(f"Failed to write TS failure report (exception): {e}")


def _write_scan_marker(scan_dir: str, job_name: str, message: str) -> None:
    """Write a diagnostic file in the scan directory."""
    try:
        if not scan_dir:
            return
        os.makedirs(scan_dir, exist_ok=True)
        path = os.path.join(scan_dir, f"{job_name}.scan_error.txt")
        ts = datetime.now().isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"[{ts}] {job_name}: {message}\n")
    except (OSError, UnicodeError) as e:
        logger.debug(f"Failed to write scan marker for {job_name}: {e}")
        return


# ---------------------------------------------------------------------------
# Rich Table rendering
# ---------------------------------------------------------------------------


def _render_scan_table_rich(
    job: str,
    a1: int,
    a2: int,
    rows: list[tuple[float, float, str]],
    selected_r: float | None = None,
) -> Table:
    """Build a Rich Table object for the scan data."""
    rows_sorted = sorted(rows, key=lambda x: x[0])
    if not rows_sorted:
        return Table(title="Empty Scan Data", box=box.SIMPLE)

    energies = [e for _, e, _ in rows_sorted]
    e_min = min(energies)
    e_max = max(energies)

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))

    table.add_column("Idx", justify="right", no_wrap=True)
    table.add_column("r (Å)", justify="right")
    table.add_column("E (Eh)", justify="right")
    table.add_column("dE (kcal)", justify="right")
    table.add_column("Stage", justify="center", style="dim")
    table.add_column("Note", justify="left")

    for idx, (r, e, stage) in enumerate(rows_sorted, start=1):
        de = (e - e_min) * HARTREE_TO_KCALMOL

        notes = []
        if selected_r is not None and abs(r - selected_r) < 1e-4:
            notes.append("[bold green]PEAK[/]")

        if e == e_max:
            if not any("PEAK" in str(n) for n in notes):
                notes.append("MAX")
            else:
                notes.append("(GMAX)")

        if e == e_min:
            notes.append("MIN")

        note_str = " ".join(notes)

        s_idx = str(idx)
        s_r = f"{r:.3f}"
        s_e = f"{e:.8f}"
        s_de = f"{de:.2f}"

        table.add_row(s_idx, s_r, s_e, s_de, stage, note_str)

    return table


def _emit_and_write_scan_table(
    wd: str,
    job: str,
    a1: int,
    a2: int,
    points: list[tuple[float, float, list[str]]],
    fine_points: list[tuple[float, float, list[str]]] | None = None,
    selected_r: float | None = None,
) -> None:
    """Print to terminal and write scan_table.txt in the scan directory."""
    try:
        rows: list[tuple[float, float, str]] = [(r, e, "coarse") for r, e, _ in points]
        if fine_points:
            rows.extend((r, e, "fine") for r, e, _ in fine_points)

        merged: dict[float, tuple[float, float, str]] = {}
        for r, e, stage in rows:
            key = round(float(r), 6)
            if key not in merged:
                merged[key] = (float(r), float(e), stage)
            else:
                if merged[key][2] != "fine" and stage == "fine":
                    merged[key] = (float(r), float(e), stage)

        table = _render_scan_table_rich(job, a1, a2, list(merged.values()), selected_r=selected_r)

        rows_sorted = sorted(merged.values(), key=lambda x: x[0])
        energies = [e for _, e, _ in rows_sorted]
        e_min = min(energies)
        e_max = max(energies)
        r_at_max = max(rows_sorted, key=lambda x: x[1])[0]
        r_at_min = min(rows_sorted, key=lambda x: x[1])[0]
        de_total = (e_max - e_min) * HARTREE_TO_KCALMOL

        console.print()
        console.print(f"TS RESCUE SCAN: {job}")
        console.print(SINGLE_LINE)
        print_kv("Bond", f"{a1}-{a2}")
        print_kv("Points", f"{len(rows_sorted)}")
        print_kv("ΔE", f"{de_total:.2f} kcal/mol")

        info_parts = []
        if selected_r is not None:
            info_parts.append(f"r@Peak: {selected_r:.3f} Å (Selected)")

        info_parts.append(f"r@GlobalMax: {r_at_max:.3f} Å")
        info_parts.append(f"r@Min: {r_at_min:.3f} Å")
        print_kv("Range", " | ".join(info_parts))

        console.print()
        console.print(table)

        scan_dir = os.path.join(wd, "scan")
        os.makedirs(scan_dir, exist_ok=True)
        out_path = os.path.join(scan_dir, "scan_table.txt")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"TS RESCUE SCAN: {job}\n")
            f.write(f"Bond {a1}-{a2} | Points: {len(rows_sorted)} | ΔE: {de_total:.2f} kcal/mol\n")

            f.write(" | ".join(info_parts) + "\n\n")

            file_console = Console(
                file=f, force_terminal=False, color_system=None, width=console.width
            )
            file_console.print(table)

        logger.info(f"Wrote the scan table to: {out_path}")

    except (OSError, UnicodeError, TypeError, ValueError) as e:
        logger.warning(f"Failed to write scan table: {e}")
        return


# ---------------------------------------------------------------------------
# Peak detection
# ---------------------------------------------------------------------------


def _ensure_has_opt(keyword: str) -> str:
    """Ensure the keyword contains 'opt'."""
    kw = (keyword or "").strip()
    if not kw:
        return ""
    if re.search(r"(?i)\bopt\b", kw):
        return kw
    return f"opt {kw}".strip()


def _find_local_max(
    points: list[tuple[float, float, list[str]]],
) -> tuple[float, float, list[str]] | None:
    """Find the local maximum (highest energy) among the scan points."""
    if len(points) < 3:
        return None
    pts = sorted(points, key=lambda x: x[0])
    maxima: list[tuple[float, float, list[str]]] = []
    for i in range(1, len(pts) - 1):
        _, e_prev, _ = pts[i - 1]
        r_mid, e_mid, c_mid = pts[i]
        _, e_next, _ = pts[i + 1]
        if e_prev < e_mid and e_mid > e_next:
            maxima.append((r_mid, e_mid, c_mid))
    if not maxima:
        return None
    return max(maxima, key=lambda x: x[1])


# ---------------------------------------------------------------------------
# Scan parameters & Scanner
# ---------------------------------------------------------------------------


class _ScanParams:
    """Parameter set for the scan process."""

    __slots__ = (
        "coarse_step",
        "fine_step",
        "uphill_limit",
        "max_steps",
        "fine_half_window",
        "coarse_k_max",
    )

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.coarse_step = float(cfg.get("scan_coarse_step", 0.1))
        self.fine_step = float(cfg.get("scan_fine_step", 0.02))
        self.uphill_limit = int(cfg.get("scan_uphill_limit", 10))
        self.max_steps = int(cfg.get("scan_max_steps", 60))
        self.fine_half_window = float(cfg.get("scan_fine_half_window", 0.1))

        try:
            k = int(round(1.0 / self.coarse_step))
            self.coarse_k_max = max(1, min(k, 10))
        except (ValueError, ZeroDivisionError, OverflowError):
            self.coarse_k_max = 10


class _ConstrainedScanner:
    """Encapsulate constrained optimization logic to avoid closure coupling with outer variables."""

    def __init__(self, cfg: dict[str, Any], wd: str, a1: int, a2: int) -> None:
        self._cfg = cfg
        self._wd = wd
        self._a1 = a1
        self._a2 = a2

    def run(
        self,
        start_coords: list[str],
        target_r: float,
    ) -> tuple[float | None, list[str] | None, str | None]:
        """Run a single constrained optimization. Return (energy, final_coords, error_msg)."""
        cfg, a1, a2 = self._cfg, self._a1, self._a2
        scan_cfg = dict(cfg)
        scan_cfg.pop("gaussian_oldchk", None)
        scan_cfg.pop("gaussian_oldchk_file", None)
        scan_cfg.pop("input_chk_dir", None)
        scan_cfg["itask"] = "opt"

        base_kw = make_scan_keyword_from_ts_keyword(str(cfg.get("keyword", "") or ""))
        base_kw = re.sub(r"(?i)\bmodredundant\b", " ", base_kw)
        scan_kw_local = _ensure_has_opt(base_kw)
        scan_kw_local = re.sub(
            r"(?i)(^|\s)freq\b(\s*=\s*\([^)]*\)|\s*\([^)]*\)|\s*=\s*[^\s]+)?", " ", scan_kw_local
        )
        scan_kw_local = re.sub(r"\s+", " ", scan_kw_local).strip()

        scan_cfg["keyword"] = scan_kw_local
        scan_cfg["freeze"] = f"{a1},{a2}"
        scan_cfg["ibkout"] = 0

        adjusted = _set_bond_length_on_coords(start_coords, a1, a2, target_r)
        if adjusted is None:
            return None, None, "unable to adjust coordinates to target bond length"

        scan_dir = os.path.join(self._wd, "scan")
        os.makedirs(scan_dir, exist_ok=True)
        job_name = f"{target_r:.3f}"

        try:
            res = executor._run_calculation_step(
                scan_dir,
                job_name,
                _get_policy(cfg),
                adjusted,
                scan_cfg,
                is_sp_task=False,
            )
            e = res.get("g_low")
            if e is None:
                e = res.get("e_low")
            return (
                (float(e) if e is not None else None),
                (res.get("final_coords") or adjusted),
                None,
            )
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
            msg = str(exc)
            _write_scan_marker(scan_dir, job_name, msg)
            return None, None, msg
