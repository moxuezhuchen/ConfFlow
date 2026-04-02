#!/usr/bin/env python3

"""Generate plain-text conformer reports and workflow summaries."""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime

from ...core.constants import HARTREE_TO_KCALMOL
from ...core.io import read_xyz_file_safe

# Constants
KB_KCALMOL = 0.001987204  # Boltzmann constant in kcal/(mol·K)

__all__ = [
    "KB_KCALMOL",
    "parse_xyz_file",
    "calculate_boltzmann_weights",
    "format_duration",
    "get_lowest_energy_conformer",
    "generate_text_report",
]

logger = logging.getLogger("confflow.viz")


def parse_xyz_file(filepath: str) -> list[dict]:
    """Parse an XYZ file and extract conformer metadata."""
    if not os.path.exists(filepath):
        logger.debug(f"XYZ file does not exist: {filepath}")
        return []
    return read_xyz_file_safe(filepath, parse_metadata=True)


def calculate_boltzmann_weights(energies: list[float], temperature: float = 298.15) -> list[float]:
    """Calculate Boltzmann population weights.

    Parameters
    ----------
    energies : list[float]
        Absolute energies in Hartree.
    temperature : float
        Temperature in Kelvin (default 298.15).

    Returns
    -------
    list[float]
        Boltzmann weights as percentages.
    """
    if not energies:
        return []

    # Filter invalid energies
    valid_energies = [e for e in energies if e is not None and e != float("inf")]
    if not valid_energies:
        return [0] * len(energies)

    min_energy = min(valid_energies)
    rel_energies = []

    for e in energies:
        if e is None or e == float("inf"):
            rel_energies.append(9999.9)
        else:
            rel_energies.append((e - min_energy) * HARTREE_TO_KCALMOL)

    # Compute Boltzmann factors
    beta = 1.0 / (KB_KCALMOL * temperature)
    boltzmann_factors = []
    for de in rel_energies:
        if de < 50:  # Contribution negligible at high energies
            boltzmann_factors.append(math.exp(-beta * de))
        else:
            boltzmann_factors.append(0.0)

    # Normalize to percentages
    total = sum(boltzmann_factors)
    if total > 0:
        return [bf / total * 100 for bf in boltzmann_factors]
    return [0] * len(energies)


def format_duration(seconds: float) -> str:
    """Format a duration in seconds for display."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.1f}min"
    else:
        return f"{seconds / 3600:.1f}h"


def _extract_energies(conformers: list[dict]) -> list[float | None]:
    """Extract energies from conformer metadata (Gibbs preferred)."""
    energies: list[float | None] = []

    for c in conformers:
        meta = c.get("metadata") or {}
        g = meta.get("G")
        e = meta.get("E", meta.get("Energy"))
        e_sp = meta.get("E_sp")
        g_corr = meta.get("G_corr")
        includes = meta.get("E_includes_gcorr")

        val: float | None = None
        try:
            # New convention: if G exists, use it directly (no G_corr stacking).
            if g is not None:
                val = float(g)
                energies.append(val)
                continue

            e_f = float(e) if e is not None else None
            e_sp_f = float(e_sp) if e_sp is not None else None
            g_corr_f = float(g_corr) if g_corr is not None else None
            includes_flag = bool(includes) if includes is not None else False

            # Backward compatibility: many calc/refine outputs carry both E/Energy and G_corr,
            # where E/Energy is already Gibbs (E_sp + G_corr). These files may lack E_includes_gcorr.
            # Heuristic rules:
            # - calc output typically uses Energy=... (metadata retains the Energy key)
            # - refine output typically includes DE=... / Rank=...
            # In both cases, if E_sp is absent, assume G_corr is already included.
            if not includes_flag and e_sp_f is None and g_corr_f is not None:
                if ("Energy" in meta) or ("DE" in meta) or ("Rank" in meta):
                    includes_flag = True

            if e_sp_f is not None and g_corr_f is not None:
                val = e_sp_f + g_corr_f
            elif e_f is not None:
                val = e_f
                if g_corr_f is not None and not includes_flag:
                    # Backward compatibility: treat E as uncorrected energy by default
                    val += g_corr_f
        except (TypeError, ValueError):
            val = None
        energies.append(val)

    return energies


def get_lowest_energy_conformer(
    conformers: list[dict],
) -> tuple[dict | None, float | None, int | None]:
    """Get the lowest-energy conformer along with its energy and index."""
    if not conformers:
        return None, None, None

    energies = _extract_energies(conformers)
    valid = [(i, e) for i, e in enumerate(energies) if e is not None and e != float("inf")]
    if not valid:
        return None, None, None

    idx, e_min = min(valid, key=lambda x: x[1])
    return conformers[idx], float(e_min), idx


def generate_text_report(
    conformers: list[dict],
    temperature: float = 298.15,
    stats: dict | None = None,
) -> str:
    """Generate a plain-text report (formatted output)."""
    from ...core.console import DOUBLE_LINE, LINE_WIDTH, SINGLE_LINE, wrap_text

    lines: list[str] = []

    # === Final report header ===
    lines.append("")
    lines.append(DOUBLE_LINE)
    lines.append(f"{'WORKFLOW SUMMARY':^{LINE_WIDTH}}")
    finished_str = "Finished: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"{finished_str:^{LINE_WIDTH}}")

    if stats:
        total_duration = stats.get("total_duration_seconds", 0)
        time_str = "Total Time: " + format_duration(total_duration)
        lines.append(f"{time_str:^{LINE_WIDTH}}")
    lines.append(DOUBLE_LINE)

    if not conformers:
        lines.append("No conformers found.")
        return "\n".join(lines)

    energies = _extract_energies(conformers)
    valid_energies = [e for e in energies if e is not None and e != float("inf")]
    min_energy = min(valid_energies) if valid_energies else 0.0

    rel_energies = []
    for e in energies:
        if e is None or e == float("inf"):
            rel_energies.append(999.9)
        else:
            rel_energies.append((e - min_energy) * HARTREE_TO_KCALMOL)

    boltzmann_weights = calculate_boltzmann_weights(energies, temperature)

    def _sort_key(i: int) -> float:
        e = energies[i] if i < len(energies) else None
        if e is None or e == float("inf"):
            return float("inf")
        try:
            return float(e)
        except (TypeError, ValueError):
            return float("inf")

    order = sorted(range(len(conformers)), key=_sort_key)

    # === WORKFLOW SUMMARY ===
    if stats:
        steps = stats.get("steps", [])
        total_duration = stats.get("total_duration_seconds", 0)
        initial_confs = stats.get("initial_conformers", 0)
        final_confs = stats.get("final_conformers", 0)
        if final_confs == 0 and steps:
            final_confs = steps[-1].get("output_conformers", 0)

        lines.append("")
        lines.append("WORKFLOW SUMMARY")
        lines.append(SINGLE_LINE)

        # Step table
        header = f"  {'Step':>4}   {'Name':<10}  {'Type':<8}  {'Status':<10}  {'In':>5}  {'Out':>5}  {'Failed':>6}  {'Time':>10}"
        lines.append(header)

        for step in steps:
            idx = step.get("index", 0)
            name = str(step.get("name", "Unknown"))[:10]
            stype = str(step.get("type", ""))[:8]
            status = str(step.get("status", "unknown"))[:10]
            inp = step.get("input_conformers", 0)
            out = step.get("output_conformers", 0)
            failed = step.get("failed_conformers", None)
            dur = step.get("duration_seconds", 0)

            failed_str = "-" if failed is None else str(int(failed))
            dur_str = format_duration(dur)

            line = f"  {idx:>4}   {name:<10}  {stype:<8}  {status:<10}  {inp:>5}  {out:>5}  {failed_str:>6}  {dur_str:>10}"
            lines.append(line)

        lines.append(SINGLE_LINE)
        lines.append(f"  Total: {initial_confs} → {final_confs} conformers")

    # === CONFORMER ANALYSIS ===
    lines.append("")
    lines.append("CONFORMER ANALYSIS")
    lines.append(SINGLE_LINE)
    summary_line = (
        f"  Conformers: {len(conformers)}    Range: {max(rel_energies) if rel_energies else 0:.2f} kcal/mol"
        f"    T: {temperature} K"
    )
    lines.extend(wrap_text(summary_line, width=LINE_WIDTH))
    lines.append(f"  Lowest Energy: {min_energy:.6f} Ha")
    lines.append("")

    # Conformer table
    header = f"  {'Rank':>4}  {'Energy (Ha)':>14}  {'ΔG (kcal)':>11}  {'Pop (%)':>9}  {'Imag':>6}  {'TSBond':>10}  {'CID':>10}"
    lines.append(header)

    for display_rank, idx in enumerate(order, start=1):
        conf = conformers[idx]
        meta = conf.get("metadata", {})
        energy = energies[idx] if idx < len(energies) else None
        de = rel_energies[idx] if idx < len(rel_energies) else 999.9
        imag = meta.get("Imag", meta.get("num_imag_freqs", "-"))
        tsbond = meta.get("TSBond", meta.get("ts_bond_length", "-"))
        cid = meta.get("CID", "-")
        boltz = boltzmann_weights[idx] if idx < len(boltzmann_weights) else 0.0

        e_str = f"{float(energy):.7f}" if energy is not None and energy != float("inf") else "N/A"
        if tsbond == "-" or tsbond is None:
            tsbond_str = "-"
        else:
            try:
                tsbond_str = f"{float(tsbond):.4f}"
            except (ValueError, TypeError):
                tsbond_str = str(tsbond)

        line = f"  {display_rank:>4}  {e_str:>14}  {de:>11.2f}  {boltz:>9.1f}  {str(imag):>6}  {tsbond_str:>10}  {str(cid):>10}"
        lines.append(line)

    lines.append(DOUBLE_LINE)
    return "\n".join(lines)
