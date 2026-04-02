#!/usr/bin/env python3

"""Result XYZ / failed XYZ writing helpers for ``ChemTaskManager``."""

from __future__ import annotations

import os
from typing import Any

from ..core import io as io_xyz
from ..core import models

__all__ = [
    "append_result",
    "format_result_comment",
    "write_failed_xyz",
]


def write_failed_xyz(
    work_dir: str,
    failed: list[dict[str, Any]],
    tasks: list[models.TaskContext],
) -> None:
    """Write failed conformers to ``failed.xyz`` using original input structures."""
    if not failed:
        return
    job_meta_map = {tc.job_name: tc.metadata for tc in tasks}
    job_coords_map = {tc.job_name: tc.coords for tc in tasks}

    failed_file = os.path.join(work_dir, "failed.xyz")
    with open(failed_file, "w", encoding="utf-8") as f:
        for res in failed:
            job_name = res.get("job_name")
            coords = job_coords_map.get(job_name) or []
            if not coords:
                continue
            orig_meta = job_meta_map.get(job_name, {})
            cid = orig_meta.get("CID")
            err = (res.get("error") or "").strip()
            if len(err) > 200:
                err = err[:200] + "..."
            info = f"Failed=1 Job={job_name}"
            if cid is not None and str(cid).strip() != "":
                info += f" CID={cid}"
            if err:
                info += f" Error={err}"
            f.write(f"{len(coords)}\n{info}\n" + "\n".join(coords) + "\n")


def format_result_comment(res: dict[str, Any], orig_meta: dict[str, Any]) -> str:
    """Build the XYZ comment line for a single successful result."""
    e_gibbs = res.get("final_gibbs_energy")
    e_sp = res.get("final_sp_energy")
    g_corr_res = res.get("g_corr")
    combined_to_g = (e_gibbs is not None) and (e_sp is not None) and (g_corr_res is not None)

    if combined_to_g:
        info = f"G={e_gibbs}"
    else:
        e_any = e_gibbs if e_gibbs is not None else res.get("energy")
        info = f"Energy={e_any}"

    cid = orig_meta.get("CID")
    if cid is not None and str(cid).strip() != "":
        info += f" CID={cid}"

    if not combined_to_g:
        g_corr = g_corr_res
        if g_corr is None:
            g_corr = orig_meta.get("G_corr")
        if g_corr is not None:
            info += f" G_corr={g_corr}"

    imag = res.get("num_imag_freqs")
    if imag is None:
        imag = orig_meta.get("Imag") or orig_meta.get("num_imag_freqs")
    if imag is not None:
        info += f" Imag={imag}"

    if res.get("lowest_freq") is not None:
        info += f" LowestFreq={res['lowest_freq']:.1f}"
    if res.get("ts_bond_atoms") is not None:
        info += f" TSAtoms={res['ts_bond_atoms']}"
    if res.get("ts_bond_length") is not None:
        info += f" TSBond={float(res['ts_bond_length']):.6f}"
    return info


def append_result(
    result_xyz_path: str | None,
    job_meta_map: dict[str, dict[str, Any]],
    res: dict[str, Any],
) -> None:
    """Append a single successful result to ``result.xyz`` immediately."""
    if res.get("status") not in ("success", "skipped"):
        return
    if not result_xyz_path:
        return
    coord_lines = res.get("final_coords")
    if not coord_lines:
        return
    orig_meta = job_meta_map.get(res.get("job_name", ""), {})
    comment = format_result_comment(res, orig_meta)
    io_xyz.append_xyz_conformer(result_xyz_path, coord_lines, comment)
