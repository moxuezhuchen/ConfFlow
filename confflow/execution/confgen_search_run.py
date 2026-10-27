#!/usr/bin/env python3

"""ConfGen search mode: DG search as the single confgen engine.

Native scope carries ``schema_version: 4`` with an optional ``search``
section. Graph, coordination spec and shape come from
:mod:`confflow.science.confgen.search_spec`; one supervised worker runs
restrained xTB; passing structures import as plain members. A confgen step
that consumes the members of an earlier confgen step treats them as plain
structures: no chained confgen state is read here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any

from confflow.science.confgen.search_spec import (
    DEFAULT_SEARCH_BUDGET,
    DEFAULT_SEARCH_BUDGET_CAP,
    DEFAULT_SEARCH_WINDOW_KCAL,
    build_search_context,
    donor_states,
    resolve_search_starts,
)
from confflow.science.topology import should_persist_working_graph

from ..domain._immutable import FrozenDict, thaw_value
from ..domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from ..domain.completion import WorkItemStatus
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from ..domain.errors import DomainError
from ..domain.result import ResultSet
from ..domain.structure import StructureRecord, StructureSet
from ..domain.work_item import RecoveryInfo, Timing, WorkItem, WorkItemResult
from .binding_resolution import effective_native_env
from .confgen_executor import ConfgenExecutor
from .confgen_search_worker import donor_state_label
from .native import NativeExecutionRequest
from .output_identity import CONFORMER_MEMBER_METADATA_KEY, conformer_output_id, endpoint_lineage
from .quota import QuotaCancelled, QuotaError
from .work_item_executor import ItemExecutionContext, WorkItemExecutor
from .xyz_import import import_xyz

__all__ = ["run_search_item"]

_HARTREE_TO_KCAL = 627.5094740631
_SHAPE_FIT_MARGIN = 0.2  # Å: declared-shape RMSD excess over the best shape that warns
_KEEP_WIDENED_SCOPE = "all_classes_input_unclassified"  # search.arrangement_scope value
_INPUT_ARRANGEMENT = "a0"  # arrangement id of the input donor arrangement
_HIGH_CN_THRESHOLD = 7  # coordination numbers from here on have ill-defined arrangement labels
_DONOR_STRETCH_WARN = 1.25  # max metal-donor distance / input distance before a donor is flagged


class _ImportError(DomainError):
    pass


def _shape_fit_diagnostic(fit: Any, step_id: str, work_item: WorkItem) -> Diagnostic | None:
    """Warn when the input donor geometry fits another admissible shape clearly better."""
    if not isinstance(fit, Mapping) or fit.get("best") == fit.get("declared"):
        return None
    declared, best = fit.get("declared"), fit.get("best")
    try:
        declared_rmsd, best_rmsd = float(fit["declared_rmsd"]), float(fit["best_rmsd"])
    except (KeyError, TypeError, ValueError):
        return None
    if not declared_rmsd - best_rmsd >= _SHAPE_FIT_MARGIN:  # also rejects NaN
        return None
    return Diagnostic(
        "confgen_input_shape_mismatch",
        f"input donor geometry fits {best} (RMSD {best_rmsd:.2f} Å) better than the declared "
        f"{declared} ({declared_rmsd:.2f} Å); the search still uses the declared shape - "
        "correct coordination.shapes if the declaration is wrong",
        DiagnosticSeverity.WARNING,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(dict(fit)),
    )


def _keep_scope_diagnostic(stats: Any, step_id: str, work_item: WorkItem) -> Diagnostic | None:
    """Warn when ``arrangement: keep`` widened the search to every coordination class."""
    if not isinstance(stats, Mapping) or stats.get("arrangement_scope") != _KEEP_WIDENED_SCOPE:
        return None
    template = stats.get("template")
    targets = stats.get("targets_searched")
    target_text = (
        ", ".join(str(target) for target in targets)
        if isinstance(targets, (list, tuple))
        else "unknown"
    )
    return Diagnostic(
        "confgen_keep_scope_widened",
        f"arrangement 'keep' was requested but the input donor geometry fits no class of shape "
        f"{template}, so all admissible coordination classes were searched (targets: {target_text}); "
        "declare coordination.shapes: [input] to keep the input arrangement",
        DiagnosticSeverity.WARNING,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(
            {
                "template": template,
                "arrangement_scope": stats.get("arrangement_scope"),
                "targets_searched": targets,
            }
        ),
    )


def _high_cn_arrangement_diagnostic(
    cn: int | None, arrangement: Any, step_id: str, work_item: WorkItem
) -> Diagnostic | None:
    """Warn when a high-CN search runs with ``arrangement: search``.

    The schema fills the default before this layer, so an explicit ``search`` cannot be told
    apart from the default; the message says how to silence it either way.
    """
    if cn is None or arrangement != "search" or cn < _HIGH_CN_THRESHOLD:
        return None
    return Diagnostic(
        "confgen_high_cn_arrangement_default",
        f"coordination number {cn} >= {_HIGH_CN_THRESHOLD}: donor-arrangement labels are "
        "ill-defined, but search.arrangement is 'search' (the default); set "
        "search.arrangement: keep to search only the input arrangement, or ignore this "
        "warning if searching other arrangements is intended",
        DiagnosticSeverity.WARNING,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict({"coordination_number": cn, "arrangement": "search"}),
    )


def _donor_states_diagnostics(
    rows: list[Any], measured: list[Any], step_id: str, work_item: WorkItem
) -> list[Diagnostic]:
    """Report optional-donor states by measured state, and per sub-search re-bindings.

    ``measured`` counts published structures by the donor state they measure at (not the
    sub-search that produced them). ``rows`` count, per sub-search, every passing structure
    and those that ended in another measured state. A declared state with no published
    structure is a WARNING saying whether its sub-search changed state or produced nothing.
    """
    rows = [row for row in rows if isinstance(row, dict)]
    measured = [row for row in measured if isinstance(row, dict)]
    parts = [
        f"{row.get('label') or '-'}: {row.get('published')} published"
        + (
            ""
            if not row.get("published") or row.get("lowest_rel_kcal") is None
            else f", lowest {float(row['lowest_rel_kcal']):+.2f}"
        )
        for row in measured
    ]
    clauses = [_sub_search_clause(row) for row in rows]
    message = (
        f"optional donor states (measured): {'; '.join(parts)}. "
        f"Sub-searches: {'; '.join(clauses) if clauses else 'none'}."
    )
    info = Diagnostic(
        "confgen_donor_states",
        message,
        DiagnosticSeverity.INFO,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(
            {"states": [dict(row) for row in rows], "measured": [dict(row) for row in measured]}
        ),
    )
    out = [info]
    missing = [row for row in measured if not row.get("published")]
    if missing:
        by_label = {str(row.get("label") or "-"): row for row in rows}
        texts = [
            _missing_state_text(row, by_label.get(str(row.get("label") or "-"))) for row in missing
        ]
        out.append(
            Diagnostic(
                "confgen_donor_state_missing",
                "declared donor state(s) with no published structure: " + "; ".join(texts),
                DiagnosticSeverity.WARNING,
                step_id,
                work_item.id,
                work_item.logical_key,
                None,
                FrozenDict({"missing": [str(row.get("label") or "-") for row in missing]}),
            )
        )
    return out


def _changed_notes(row: Mapping[str, Any]) -> list[str]:
    """``N re-bound <site>`` / ``N detached <site>`` notes of one sub-search row."""
    return [
        f"{count} {verb} {sid}"
        for verb, key in (("re-bound", "rebound"), ("detached", "detached"))
        for sid, count in sorted((row.get(key) or {}).items())
    ]


def _sub_search_clause(row: Mapping[str, Any]) -> str:
    """One sub-search: passing count, what changed state, lowest energy."""
    label = str(row.get("label") or "-")
    passed = int(row.get("passed") or 0)
    noun = "structure" if passed == 1 else "structures"
    notes = _changed_notes(row)
    text = f"{label} search: {passed} passing {noun}"
    text += (", " + ", ".join(notes)) if notes else (", none changed donor state" if passed else "")
    lowest = _finite_number(row.get("lowest_rel_kcal"))
    if lowest is not None and passed:
        text += f" (lowest {lowest:+.2f} kcal)"
    return text


def _missing_state_text(row: Mapping[str, Any], sub: Mapping[str, Any] | None) -> str:
    """Why a declared state has no published structure: its sub-search changed state or not."""
    label = str(row.get("label") or "-")
    passed = int((sub or {}).get("passed") or 0)
    if not passed:
        return f"{label} (its sub-search produced no passing structures)"
    notes = _changed_notes(sub or {})
    noun = "structure" if passed == 1 else "structures"
    if notes:
        return (
            f"{label} (its sub-search produced {passed} passing {noun}, which changed state "
            f"({', '.join(notes)}); none was published)"
        )
    return (
        f"{label} (its sub-search produced {passed} passing {noun}, none changed state; "
        "none was published)"
    )


def _donor_job_fields(native: Mapping[str, Any], driving: Any, base: int) -> dict[str, Any]:
    """Job fields for optional binding sites; empty without any (no new keys then)."""
    try:
        states = donor_states(native)
    except ValueError as exc:
        raise DomainError(f"confgen v4 search rejected: {exc}") from exc
    if len(states) == 1 and not states[0].label:
        return {}
    coordination = native.get("coordination")
    sites = coordination.get("binding_sites") if isinstance(coordination, Mapping) else None
    donor_sites = [
        [str(site.get("id")), int(site["atoms"][0]) - base]
        for site in (sites or [])
        if isinstance(site, Mapping) and site.get("optional", False) is True
    ]
    entries: list[dict[str, Any]] = []
    for state in states:
        built = build_search_context(driving, state.native)
        section = built.coordination_section
        entries.append(
            {
                "label": state.label,
                "coordination": thaw_value(section) if isinstance(section, Mapping) else None,
            }
        )
    return {"donor_states": entries, "donor_sites": donor_sites}


def _optional_bound_of(rec: Mapping[str, Any]) -> dict[int, bool] | None:
    raw = rec.get("optional_bound")
    if raw is None:
        return None
    return {int(k): bool(v) for k, v in raw.items()}


def _donor_metadata(rec: Mapping[str, Any], sites: list[tuple[str, int]]) -> dict[str, Any]:
    """Member keys for the measured donor state (none without optional donors)."""
    bound = _optional_bound_of(rec)
    if bound is None or not sites:
        return {}
    return {
        "search_optional_bound": {str(atom + 1): flag for atom, flag in sorted(bound.items())},
        "search_donor_state": donor_state_label(sites, bound),
    }


def _arrangement_changed_diagnostic(
    stats: Any, rows: Any, step_id: str, work_item: WorkItem
) -> Diagnostic | None:
    """Warn when the lowest published structure lies in a donor arrangement other than the input's."""
    table = stats.get("arrangements") if isinstance(stats, Mapping) else None
    if not isinstance(table, list) or not isinstance(rows, list):
        return None
    lowest = next(
        (r for r in rows if isinstance(r, Mapping) and r.get("passed") is True),
        None,
    )
    arrangement = lowest.get("arrangement") if lowest is not None else None
    if not isinstance(arrangement, str) or arrangement == _INPUT_ARRANGEMENT:
        return None
    input_row = next(
        (r for r in table if isinstance(r, Mapping) and r.get("id") == _INPUT_ARRANGEMENT),
        None,
    )
    gap = input_row.get("rel_kcal") if input_row is not None else None
    if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap):
        gap = None
    if gap is None:
        where = "the input arrangement not found in the arrangement table"
    else:
        where = (
            f"{float(gap):.2f} kcal/mol below the input arrangement's lowest ({_INPUT_ARRANGEMENT})"
        )
    return Diagnostic(
        "confgen_arrangement_changed",
        f"the lowest structure has donor arrangement {arrangement}, {where}; other "
        "arrangements are different coordination isomers - compare them per arrangement "
        "(members carry search_arrangement)",
        DiagnosticSeverity.WARNING,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(
            {
                "arrangement": arrangement,
                "input_rel_kcal": None if gap is None else float(gap),
            }
        ),
    )


def _state_change_diagnostic(
    stats: Any, step_id: str, work_item: WorkItem, base: int
) -> Diagnostic | None:
    """Warn that torsions left their input state and were explored freely (declare to fix)."""
    if not isinstance(stats, Mapping):
        return None
    changes: Any = stats.get("state_changes")
    units_raw: Any = changes.get("units") if isinstance(changes, Mapping) else None
    if not isinstance(units_raw, list) or not units_raw:
        return None
    units: list[dict[str, Any]] = []
    try:
        for unit in units_raw:
            if not isinstance(unit, Mapping):
                continue
            atoms = unit.get("atoms")
            if (
                not isinstance(atoms, (list, tuple))
                or len(atoms) != 4
                or any(isinstance(a, bool) or not isinstance(a, int) for a in atoms)
            ):
                continue
            same = unit.get("lowest_same_kcal")
            units.append(
                {
                    "atoms": [int(a) + base for a in atoms],
                    "kind": str(unit.get("kind")),
                    "structures": int(unit["structures"]),
                    "lowest_changed_kcal": float(unit["lowest_changed_kcal"]),
                    "lowest_same_kcal": None if same is None else float(same),
                }
            )
    except (KeyError, TypeError, ValueError):
        return None
    if not units:
        return None
    lines = [
        f"{unit['atoms']} ({unit['kind']}): {unit['structures']} structures in the other state, "
        f"lowest {unit['lowest_changed_kcal']:.2f} kcal/mol above the lowest scanned structure"
        for unit in units[:5]
    ]
    paste = ", ".join(str(unit["atoms"]) for unit in units)
    more = f" (first 5 of {len(units)} shown)" if len(units) > 5 else ""
    return Diagnostic(
        "confgen_conformational_states",
        f"{len(units)} torsion state(s) changed from the input state during the search and were "
        f"explored freely{more}: " + "; ".join(lines) + ". To keep them fixed, declare: "
        f"search.fixed_states: [{paste}]",
        DiagnosticSeverity.WARNING,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(
            {"aligned": changes.get("aligned"), "units": tuple(FrozenDict(u) for u in units)}
        ),
    )


def _state_window_diagnostic(
    stats: Any, step_id: str, work_item: WorkItem, base: int = 1
) -> Diagnostic | None:
    """Report the states the state window added beyond the energy window (feature on only).

    Only states that contributed structures are listed, lowest released energy first; atoms are
    reported with the step's index base, as the other state diagnostics do.
    """
    if not isinstance(stats, Mapping):
        return None
    window = stats.get("state_window")
    if not isinstance(window, Mapping):
        return None
    window_kcal = _finite_number(window.get("window_kcal")) or 0.0
    rows = [s for s in window.get("states") or () if isinstance(s, Mapping)]
    listed = [s for s in rows if int(s.get("added") or 0) > 0]

    def released_order(row: Mapping[str, Any]) -> float:
        value = _finite_number(row.get("released_lowest_kcal"))
        return float("inf") if value is None else value

    listed.sort(key=released_order)
    states = []
    for s in listed:
        released = _finite_number(s.get("released_lowest_kcal"))
        atoms = [[int(a) + base for a in unit] for unit in s.get("atoms") or ()]
        over = released is not None and released > window_kcal
        states.append(
            {
                "atoms": atoms,
                "added": int(s.get("added") or 0),
                "restrained_lowest_kcal": _finite_number(s.get("lowest_kcal")),
                "released_lowest_kcal": released,
                "selected_on": "restrained",
                "exceeds_window": over,
            }
        )
    lines = []
    for s in states[:5]:
        head = f"{s['atoms']}: {s['added']} added, released lowest {s['released_lowest_kcal']:.2f} kcal/mol"
        if s["exceeds_window"]:
            head += (
                f"; selected on restrained energies; released lowest "
                f"{s['released_lowest_kcal']:.2f} kcal/mol is above the {window_kcal:.2f} window"
            )
        lines.append(head)
    more = f" (first 5 of {len(states)} shown)" if len(states) > 5 else ""
    truncated = int(window.get("truncated") or 0)
    cut = f"; {truncated} further state(s) cut by state_max_states" if truncated else ""
    error = f"; scan error: {window['error']}" if window.get("error") else ""
    return Diagnostic(
        "confgen_state_window",
        f"state window kept {int(window.get('added_structures') or 0)} structure(s) from "
        f"{len(states)} state(s) outside the energy window{more}{cut}{error}"
        + (": " + "; ".join(lines) if lines else ""),
        DiagnosticSeverity.INFO,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(
            {
                "window_kcal": window_kcal,
                "state_max": window.get("state_max"),
                "state_max_states": window.get("state_max_states"),
                "states": tuple(FrozenDict(st) for st in states),
                "states_selected": len(rows),
                "states_already_in_window": int(window.get("states_no_added") or 0),
                "added_states": len(states),
                "added_structures": window.get("added_structures"),
                "truncated": truncated,
            }
        ),
    )


def _finite_number(value: Any) -> float | None:
    """Return *value* as a float when it is a finite real number (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _donor_stretch_diagnostic(
    rows: Any, step_id: str, work_item: WorkItem, base: int
) -> Diagnostic | None:
    """Warn when a published structure has a donor moved far away from the metal."""
    stretched: list[tuple[float, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        ratio = _finite_number(row.get("donor_stretch"))
        if ratio is not None and ratio > _DONOR_STRETCH_WARN:
            stretched.append((ratio, row.get("donor_stretch_atom")))
    if not stretched:
        return None
    worst, worst_atom = max(stretched, key=lambda item: item[0])
    atom = None
    if isinstance(worst_atom, int) and not isinstance(worst_atom, bool):
        atom = worst_atom + base
    atom_text = f"donor atom {atom}" if atom is not None else "donor atom unknown"
    return Diagnostic(
        "confgen_donor_stretched",
        f"{len(stretched)} published structure(s) with a donor stretched beyond "
        f"{_DONOR_STRETCH_WARN:.2f}x the input metal-donor distance (worst {worst:.2f}x, "
        f"{atom_text}): a donor moved away from the metal; check these structures (members "
        "carry search_donor_stretch); set search.max_donor_stretch to reject them",
        DiagnosticSeverity.WARNING,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(
            {
                "count": len(stretched),
                "worst": worst,
                "atom": atom,
                "threshold": _DONOR_STRETCH_WARN,
            }
        ),
    )


def _input_gap_diagnostic(
    input_baseline: Any, step_id: str, work_item: WorkItem
) -> Diagnostic | None:
    """Report how far the input structure lies above (or below) the lowest published structure."""
    if not isinstance(input_baseline, Mapping):
        return None
    delta = _finite_number(input_baseline.get("lowest_final_delta_kcal"))
    if delta is None:
        return None
    gap = -delta  # delta = lowest published energy minus input energy
    relation = "above" if gap >= 0 else "below"
    disposition = input_baseline.get("disposition")
    return Diagnostic(
        "confgen_input_gap",
        f"the input structure is {abs(gap):.2f} kcal/mol {relation} the lowest published "
        f"structure (input disposition: {disposition})",
        DiagnosticSeverity.INFO,
        step_id,
        work_item.id,
        work_item.logical_key,
        None,
        FrozenDict(
            {
                "gap_kcal": gap,
                "disposition": disposition if isinstance(disposition, str) else None,
            }
        ),
    )


def _histogram(payload: Any) -> dict[str, int]:
    """Return the failure-reason counts of a summary (iterative search: ``search.failures``)."""
    counts: dict[str, int] = {}
    section = payload.get("search") if isinstance(payload, Mapping) else None
    failures = section.get("failures") if isinstance(section, Mapping) else None
    if isinstance(failures, Mapping):
        for name, value in failures.items():
            if isinstance(value, int) and not isinstance(value, bool):
                counts[str(name)] = counts.get(str(name), 0) + value
    rows = payload.get("structures") if isinstance(payload, Mapping) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        for check in row.get("failed_checks", []) or []:
            counts[str(check)] = counts.get(str(check), 0) + 1
    return dict(sorted(counts.items()))


def _read_validated(
    driving: StructureRecord, rundir: str | os.PathLike[str]
) -> tuple[dict[str, Any], dict[str, Any], list[Any], StructureSet]:
    try:
        payload = json.loads(open(os.path.join(rundir, "summary.json"), encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        raise _ImportError(f"confgen search summary is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise _ImportError("confgen search summary must hold a JSON object")
    totals, rows = payload.get("totals"), payload.get("structures")
    if not isinstance(totals, dict) or not isinstance(rows, list):
        raise _ImportError("confgen search summary lacks totals/structures")
    passing = [r for r in rows if isinstance(r, dict) and r.get("passed") is True]
    if totals.get("passed") != len(passing):
        raise _ImportError("confgen search summary totals disagree with passing records")
    try:
        xyz_text = open(os.path.join(rundir, "structures.xyz"), encoding="utf-8").read()
    except OSError as exc:
        raise _ImportError(f"confgen search structures are unreadable: {exc}") from exc
    try:
        frames = import_xyz(xyz_text, source_name="confgen-search")
    except DomainError as exc:
        raise _ImportError(f"confgen search structures are invalid: {exc}") from exc
    if len(frames) != len(passing):
        raise _ImportError("confgen search frame count disagrees with passed totals")
    if any(tuple(r.atoms) != tuple(driving.atoms) for r in frames):
        raise _ImportError("confgen search frame breaks the atomic invariant")
    tags = re.findall(r"target=(\S+) start=(\d+) energy_eh=(\S+)", xyz_text)
    if len(tags) != len(frames):
        raise _ImportError("confgen search xyz comments are malformed")
    try:
        framed = [(str(target), int(start), float(energy)) for target, start, energy in tags]
    except (TypeError, ValueError) as exc:
        raise _ImportError("confgen search xyz comments are malformed") from exc
    keyed: dict[tuple[str, int], Any] = {}
    for row in passing:
        target, start = row.get("target"), row.get("start")
        if not isinstance(target, str) or isinstance(start, bool) or not isinstance(start, int):
            raise _ImportError("confgen search summary records are malformed")
        if (target, start) in keyed:
            raise _ImportError("confgen search summary holds duplicate passing records")
        keyed[(target, start)] = row
    if sorted(keyed) != sorted((target, start) for target, start, _ in framed):
        raise _ImportError("confgen search xyz comments disagree with passing records")
    ordered: list[Any] = []
    for target, start, energy in framed:
        record = keyed[(target, start)]
        try:
            want = float(record.get("energy_eh") or 0.0)
        except (TypeError, ValueError) as exc:
            raise _ImportError("confgen search summary energies are malformed") from exc
        if round(energy, 6) != round(want, 6):
            raise _ImportError("confgen search frame energies disagree with passing records")
        ordered.append(record)
    return totals, payload, ordered, frames


def run_search_item(
    executor: ConfgenExecutor,
    work_item: WorkItem,
    context: ItemExecutionContext,
    wall_start: float,
    monotonic_start: float,
    should_cancel: Callable[[], bool] | None = None,
) -> WorkItemResult:
    """Run one confgen work item in DG-search mode (never raises)."""
    try:
        return _run(executor, work_item, context, wall_start, monotonic_start, should_cancel)
    except QuotaCancelled:
        return executor._cancelled(work_item, context, wall_start, monotonic_start)
    except (DomainError, ValueError) as exc:
        return executor._fail(work_item, context, wall_start, monotonic_start, str(exc))
    except Exception as exc:
        msg = f"confgen internal failure: {exc}"
        return executor._fail(work_item, context, wall_start, monotonic_start, msg)


def _run(
    executor: ConfgenExecutor,
    work_item: WorkItem,
    context: ItemExecutionContext,
    wall_start: float,
    monotonic_start: float,
    should_cancel: Callable[[], bool] | None,
) -> WorkItemResult:
    scientific = context.scientific
    native = dict(scientific.native)
    seed, raw_seed = scientific.seed, native.get("seed")
    if seed is not None and raw_seed is not None and int(raw_seed) != int(seed):
        raise DomainError(
            "confgen v4 seed conflict: step seed "
            f"{seed!r} disagrees with native seed {raw_seed!r}; "
            "the top-level step seed is the sole authority"
        )
    if seed is None:
        seed = raw_seed
    if seed is None:
        raise DomainError(
            "confgen v4 search requires an explicit top-level seed "
            "(the seed is the sole stochastic authority)"
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DomainError(f"confgen v4 seed must be an integer, got {seed!r}")
    executor._reject_freeze(work_item, context)
    driving = executor._driving(work_item)
    spec = dict(native)
    spec["seed"] = int(seed)
    try:
        science = build_search_context(driving, spec)
    except ValueError as exc:
        raise DomainError(f"confgen v4 spec rejected: {exc}") from exc
    section = native.get("search")
    if section is not None and not isinstance(section, Mapping):
        raise DomainError("confgen search section must be a mapping or null")
    try:
        starts = resolve_search_starts(section, has_coordination=science.spec is not None)
    except ValueError as exc:
        raise DomainError(f"confgen v4 search rejected: {exc}") from exc
    coord_section = science.coordination_section
    coord, shape = science.spec, science.shape
    high_cn_diag = _high_cn_arrangement_diagnostic(
        coord.coordination_number if coord is not None else None,
        (section or {}).get("arrangement", "search"),
        context.step_id,
        work_item,
    )
    if coord_section is not None and (coord is None or shape is None):
        raise DomainError("confgen search coordination did not resolve to one shape")
    base, natoms = int(native.get("index_base", 1)), len(driving.atoms)
    donor_doc = _donor_job_fields(spec, driving, base)
    raw_charges = section.get("fragment_charges", []) if isinstance(section, Mapping) else []
    frag = tuple((int(e["atom"]) - base, int(e["charge"])) for e in raw_charges)
    for atom, _ in frag:
        if atom < 0 or atom >= natoms:
            raise DomainError(
                f"confgen search fragment charge atom {atom + base} is out of range for {natoms} atoms"
            )
    raw_freeze_core = section.get("freeze_core", []) if isinstance(section, Mapping) else []
    if not isinstance(raw_freeze_core, (list, tuple)):
        raise DomainError("confgen search freeze_core must be a list of atom indices")
    freeze_core: list[int] = []
    for value in raw_freeze_core:
        if isinstance(value, bool) or not isinstance(value, int):
            raise DomainError(f"confgen search freeze_core index must be an integer, got {value!r}")
        index = value - base
        if index < 0 or index >= natoms:
            raise DomainError(
                f"confgen search freeze_core atom {value} is out of range for {natoms} atoms"
            )
        if index in freeze_core:
            raise DomainError(f"confgen search freeze_core repeats atom {value}")
        freeze_core.append(index)
    raw_fixed_states = section.get("fixed_states", []) if isinstance(section, Mapping) else []
    if not isinstance(raw_fixed_states, (list, tuple)):
        raise DomainError("confgen search fixed_states must be a list of 4-atom lists")
    fixed_states: list[list[int]] = []
    for quad in raw_fixed_states:
        if not isinstance(quad, (list, tuple)) or len(quad) != 4:
            raise DomainError(
                f"confgen search fixed_states entry {quad!r} must hold 4 atom indices"
            )
        atoms: list[int] = []
        for value in quad:
            if isinstance(value, bool) or not isinstance(value, int):
                raise DomainError(
                    f"confgen search fixed_states atom must be an integer, got {value!r}"
                )
            index = value - base
            if index < 0 or index >= natoms:
                raise DomainError(
                    f"confgen search fixed_states atom {value} is out of range for {natoms} atoms"
                )
            atoms.append(index)
        if len(set(atoms)) != 4:
            raise DomainError(f"confgen search fixed_states entry {quad!r} repeats an atom")
        if atoms in fixed_states or atoms[::-1] in fixed_states:
            raise DomainError(f"confgen search fixed_states entry {quad!r} is duplicated")
        fixed_states.append(atoms)
    charge, multiplicity = driving.charge, driving.multiplicity
    defaults = getattr(context, "scientific_defaults", None)
    if charge is None and defaults is not None:
        charge = getattr(defaults, "charge", None)
    if multiplicity is None and defaults is not None:
        multiplicity = getattr(defaults, "multiplicity", None)
    if (
        charge is None
        or multiplicity is None
        or isinstance(charge, bool)
        or not isinstance(charge, int)
        or isinstance(multiplicity, bool)
        or not isinstance(multiplicity, int)
    ):
        raise DomainError("confgen search requires a resolved charge and multiplicity")
    binding = context.execution_binding
    xtb = binding.executable if binding is not None else None
    if not xtb:
        raise DomainError(
            "confgen search requires an xTB executable: set step execution.executable "
            "or provide the request xtb executable"
        )
    cores = work_item.resources.cores_per_item
    if cores is None or context.supervisor is None or not context.run_root:
        raise DomainError(
            "confgen search requires resolved cores, a process supervisor, "
            "and a run root for item directories"
        )
    attempt = context.attempt_dir(work_item)
    os.makedirs(attempt, exist_ok=True)
    rundir = os.path.join(attempt, "search_run")
    os.makedirs(rundir, exist_ok=True)
    settings_doc = dict(section) if isinstance(section, Mapping) else {}
    # unset budget: spend at least the default, continue while the low-energy region grows
    settings_doc["adaptive"] = settings_doc.get("budget") is None
    if settings_doc["adaptive"]:
        settings_doc["budget"] = DEFAULT_SEARCH_BUDGET
        settings_doc["budget_cap"] = DEFAULT_SEARCH_BUDGET_CAP
    settings_doc.setdefault("window_kcal", DEFAULT_SEARCH_WINDOW_KCAL)
    settings_doc.setdefault("arrangement", "search")
    settings_doc.setdefault("release", True)
    settings_doc.setdefault("small_ring_torsions", "both")
    settings_doc.setdefault("embed_timeout_seconds", 120)
    settings_doc.setdefault("max_cycles", 1000)
    settings_doc.setdefault("bond_scale", 1.25)
    settings_doc["starts"] = starts
    settings_doc["seed"] = int(seed)
    settings_doc["fragment_charges"] = [[a, q] for a, q in frag]
    settings_doc["freeze_core"] = freeze_core
    settings_doc["fixed_states"] = fixed_states
    walltime: float | None = binding.walltime_seconds if binding is not None else None
    walltime = float(walltime) if walltime else None
    job_path = os.path.join(attempt, "search_job.json")
    graph_doc = science.graph.to_mapping(convention="internal0")
    graph_doc["total_charge"] = int(charge)
    graph_doc["multiplicity"] = int(multiplicity)
    job_doc = {
        "atoms": list(driving.atoms),
        "reference": [[float(x), float(y), float(z)] for x, y, z in driving.coordinates],
        "graph": graph_doc,
        "coordination": thaw_value(coord_section) if isinstance(coord_section, Mapping) else None,
        "shape": shape,
        "charge": int(charge),
        "uhf": int(multiplicity) - 1,
        "settings": settings_doc,
        "max_cycles": int(settings_doc.get("max_cycles", 1000)),
        "xtb": xtb,
        "cores": cores,
        "workdir": os.path.join(rundir, "starts"),
        "structures_file": os.path.join(rundir, "structures.xyz"),
        "summary_file": os.path.join(rundir, "summary.json"),
        "checkpoint_file": os.path.join(rundir, "checkpoint.json"),
        "input_baseline_file": os.path.join(rundir, "input_baseline.json"),
        # Leave time for worker setup/final serialization before the outer hard deadline.
        "time_limit_seconds": None if walltime is None else max(0.001, walltime * 0.9),
        **donor_doc,
    }
    with open(job_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(job_doc, sort_keys=True, indent=2) + "\n")
    request = NativeExecutionRequest(
        sys.executable,
        (sys.executable, "-m", "confflow.execution.confgen_search_worker", job_path),
        rundir,
        FrozenDict(effective_native_env(binding, inherit=os.environ)),
        walltime,
        "stdout.log",
        "stderr.log",
    )
    try:
        launched = WorkItemExecutor().launch_and_wait(
            context.supervisor,
            request,
            context.poll_interval_seconds,
            should_cancel=should_cancel,
            quota=(work_item.resources, str(context.run_root), work_item.id),
        )
    except QuotaCancelled:
        raise
    except QuotaError as exc:
        raise DomainError(f"confgen search quota refused: {exc}") from exc

    def _result(
        status: WorkItemStatus,
        structures: StructureSet,
        diagnostics: tuple[Diagnostic, ...],
        artifacts: ArtifactSet,
    ) -> WorkItemResult:
        return WorkItemResult(
            work_item.id,
            status,
            structures,
            ResultSet(),
            artifacts,
            diagnostics,
            Timing(
                started_at=wall_start,
                finished_at=max(time.time(), wall_start),
                duration_seconds=max(0.0, time.monotonic() - monotonic_start),
            ),
            None,
            RecoveryInfo(profile="none", attempted=False),
            work_item.semantic_digest,
        )

    def _failed(
        msg: str,
        code: str,
        artifacts: ArtifactSet,
        details: dict[str, Any] | None = None,
        extra_diagnostics: tuple[Diagnostic, ...] = (),
    ) -> WorkItemResult:
        diagnostic = Diagnostic(
            code,
            msg,
            DiagnosticSeverity.ERROR,
            context.step_id,
            work_item.id,
            work_item.logical_key,
            None,
            FrozenDict(dict(details or {})),
        )
        return _result(
            WorkItemStatus.FAILED, StructureSet(), (diagnostic, *extra_diagnostics), artifacts
        )

    def _artifacts() -> ArtifactSet:
        run_root = os.path.realpath(context.run_root)
        refs: list[ArtifactRef] = []
        roles = (
            ("summary.json", "search_summary"),
            ("checkpoint.json", "search_checkpoint"),
            ("input_baseline.json", "search_input_baseline"),
            ("structures.xyz", "search_structures"),
            ("stdout.log", "search_log"),
            ("stderr.log", "search_log"),
        )
        for name, role in roles:
            path = os.path.join(rundir, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                checksum = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
            relative = os.path.relpath(os.path.realpath(path), run_root).replace(os.sep, "/")
            refs.append(
                ArtifactRef(
                    id=f"{work_item.id}/{name}",
                    role=role,
                    locator=ArtifactLocator.run_relative(relative),
                    checksum=checksum,
                    producer_step_id=work_item.step_id,
                    producer_work_item_id=work_item.id,
                    subject_structure_id=driving.id,
                    metadata=FrozenDict({"seed": int(seed), "starts": starts}),
                )
            )
        return ArtifactSet(tuple(refs))

    def _completed(artifacts: ArtifactSet, charge: int, multiplicity: int) -> WorkItemResult:
        try:
            totals, payload, ordered, frames = _read_validated(driving, rundir)
        except _ImportError as exc:
            return _failed(str(exc), "confgen_search_failed", artifacts)
        parent_ids, lineage_root, group_key = endpoint_lineage(driving)
        persist = should_persist_working_graph(driving)
        inherit_patch = driving.topology_patch if persist else None
        inherit_graph = (
            tuple(tuple(int(v) for v in row) for row in science.adjacency) if persist else None
        )
        low = min(float(r.get("energy_eh") or 0.0) for r in ordered)
        search_stats = payload.get("search")
        search_stats = search_stats if isinstance(search_stats, dict) else {}
        release = search_stats.get("release")
        donor_sites = [(str(sid), int(atom)) for sid, atom in donor_doc.get("donor_sites", [])]
        members: list[StructureRecord] = []
        for rank, rec in enumerate(ordered):
            energy = rec.get("energy_eh")
            coords = tuple((float(x), float(y), float(z)) for x, y, z in frames[rank].coordinates)
            members.append(
                StructureRecord(
                    conformer_output_id(work_item.logical_key, rank),
                    tuple(driving.atoms),
                    coords,
                    charge,
                    multiplicity,
                    parent_ids,
                    lineage_root,
                    work_item.step_id,
                    work_item.id,
                    "conformer",
                    rank,
                    group_key,
                    FrozenDict(
                        {
                            CONFORMER_MEMBER_METADATA_KEY: rank,
                            "seed": int(seed),
                            "starts": starts,
                            "energy_eh": energy,
                            "rel_kcal": (float(energy or 0.0) - low) * _HARTREE_TO_KCAL,
                            "search_target": rec.get("target"),
                            "search_arrangement": rec.get("arrangement"),
                            "search_start": rec.get("start"),
                            "search_actual_target": rec.get("actual_target"),
                            "search_reaction_distances": rec.get("reaction_distances") or [],
                            "search_donor_stretch": rec.get("donor_stretch"),
                            "search_state_flips": rec.get("state_flips"),
                            "search_release": rec.get("release", release),
                            **(
                                {"search_kept_by": "state"} if rec.get("kept_by") == "state" else {}
                            ),
                            **_donor_metadata(rec, donor_sites),
                        }
                    ),
                    inherit_patch,
                    inherit_graph,
                )
            )
        counts = _histogram(payload)
        targets_searched = search_stats.get("targets_searched")
        diagnostic = Diagnostic(
            "confgen_completed",
            f"confgen search published {len(members)} conformers "
            f"({totals.get('generated')} relaxations, {totals.get('relaxed')} converged, "
            f"{totals.get('passed')} passed across {totals.get('targets')} targets; "
            f"failed checks: {', '.join(f'{k}={v}' for k, v in counts.items()) or 'none'})",
            DiagnosticSeverity.INFO,
            context.step_id,
            work_item.id,
            work_item.logical_key,
            None,
            FrozenDict(
                {
                    "targets": totals.get("targets"),
                    "generated": totals.get("generated"),
                    "relaxed": totals.get("relaxed"),
                    "passed": totals.get("passed"),
                    "starts": starts,
                    "failed_checks": counts,
                    "release": release,
                    "stop": search_stats.get("stop"),
                    "outcome_class": search_stats.get("outcome_class"),
                    "template": search_stats.get("template"),
                    "arrangement_scope": search_stats.get("arrangement_scope"),
                    "arrangements": search_stats.get("arrangements"),
                    "targets_searched": targets_searched,
                }
            ),
        )
        diagnostics = [diagnostic]
        # a merged donor run can publish structures from several states with different releases
        member_releases = sorted({str(rec.get("release", release)) for rec in ordered})
        if {release, *member_releases} & {"deferred", "partial"}:
            by_state: dict[str, Any] = {}
            for row in search_stats.get("donor_states") or []:
                if isinstance(row, dict):
                    by_state[str(row.get("label"))] = row.get("release")
            diagnostics.append(
                Diagnostic(
                    "confgen_release_incomplete",
                    "time budget limited release: deferred results retain search restraints; "
                    "partial results contain only the successfully released subset",
                    DiagnosticSeverity.WARNING,
                    context.step_id,
                    work_item.id,
                    work_item.logical_key,
                    None,
                    FrozenDict(
                        {
                            "release": release,
                            "member_release": member_releases,
                            "release_by_state": by_state,
                        }
                    ),
                )
            )
        if release == "failed":
            diagnostics.append(
                Diagnostic(
                    "confgen_release_failed",
                    "every release relaxation failed: the published conformers still carry all "
                    "search restraints (metadata search_release='failed')",
                    DiagnosticSeverity.WARNING,
                    context.step_id,
                    work_item.id,
                    work_item.logical_key,
                    None,
                    FrozenDict({"release_relaxations": search_stats.get("release_relaxations")}),
                )
            )
        shape_diag = _shape_fit_diagnostic(
            search_stats.get("input_shape_fit"), context.step_id, work_item
        )
        if shape_diag is not None:
            diagnostics.append(shape_diag)
        keep_diag = _keep_scope_diagnostic(search_stats, context.step_id, work_item)
        if keep_diag is not None:
            diagnostics.append(keep_diag)
        if high_cn_diag is not None:
            diagnostics.append(high_cn_diag)
        arrangement_diag = _arrangement_changed_diagnostic(
            search_stats, payload.get("structures"), context.step_id, work_item
        )
        if arrangement_diag is not None:
            diagnostics.append(arrangement_diag)
        state_diag = _state_change_diagnostic(search_stats, context.step_id, work_item, base)
        if state_diag is not None:
            diagnostics.append(state_diag)
        window_diag = _state_window_diagnostic(search_stats, context.step_id, work_item, base)
        if window_diag is not None:
            diagnostics.append(window_diag)
        donor_diag = _donor_stretch_diagnostic(ordered, context.step_id, work_item, base)
        if donor_diag is not None:
            diagnostics.append(donor_diag)
        if isinstance(search_stats.get("donor_states"), list):
            measured = search_stats.get("donor_measured")
            diagnostics.extend(
                _donor_states_diagnostics(
                    search_stats["donor_states"],
                    measured if isinstance(measured, list) else [],
                    context.step_id,
                    work_item,
                )
            )
        gap_diag = _input_gap_diagnostic(payload.get("input_baseline"), context.step_id, work_item)
        if gap_diag is not None:
            diagnostics.append(gap_diag)
        structures = StructureSet(tuple(members))
        return _result(WorkItemStatus.COMPLETED, structures, tuple(diagnostics), artifacts)

    if launched is None:
        if should_cancel is not None and should_cancel():
            return executor._cancelled(work_item, context, wall_start, monotonic_start)
        raise DomainError("confgen search process handle was lost before completion")
    execution_result, cancel_outcome = launched
    if cancel_outcome is not None:
        if cancel_outcome.confirmed:
            return executor._cancelled(work_item, context, wall_start, monotonic_start)
        return _failed(
            f"cancellation could not be confirmed: {cancel_outcome.detail}",
            "cancellation_error",
            _artifacts(),
        )
    if execution_result.timed_out or execution_result.exit_code is None:
        return _failed(
            "confgen search worker did not exit cleanly",
            "confgen_search_failed",
            _artifacts(),
            {"timed_out": bool(execution_result.timed_out)},
        )
    code = execution_result.exit_code
    artifacts = _artifacts()
    if code == 0:
        return _completed(artifacts, int(charge), int(multiplicity))
    if code == 2:
        try:
            with open(os.path.join(rundir, "summary.json"), encoding="utf-8") as handle:
                payload = json.loads(handle.read())
        except (OSError, ValueError) as exc:
            return _failed(
                f"confgen search published nothing and its summary is unreadable: {exc}",
                "confgen_search_failed",
                artifacts,
            )
        if not isinstance(payload, dict):
            payload = {}
        totals = payload.get("totals", {})
        counts = _histogram(payload)
        generated = totals.get("generated", "?") if isinstance(totals, dict) else "?"
        search_stats = payload.get("search")
        search_stats = search_stats if isinstance(search_stats, dict) else {}
        outcome_class = str(search_stats.get("outcome_class", ""))
        diagnostic_code = {
            "backend_failure": "confgen_backend_failure",
            "chemical_rejection": "confgen_chemical_rejection",
            "core_displacement": "confgen_core_displacement",
            "truncated": "confgen_search_truncated",
        }.get(outcome_class, "confgen_no_passing_structures")
        outcome_message = {
            "backend_failure": "backend relaxations failed",
            "chemical_rejection": "chemical audit rejected all candidates",
            "core_displacement": "the backend moved one or more frozen core atoms",
            "truncated": "the search was truncated before it found a passing structure",
        }.get(outcome_class, "none passed all audit checks")
        keep_diag = _keep_scope_diagnostic(search_stats, context.step_id, work_item)
        extra_diagnostics = tuple(d for d in (keep_diag, high_cn_diag) if d is not None)
        return _failed(
            f"confgen search ran {generated} relaxations; none passed all audit checks; {outcome_message} "
            f"(failed checks: {', '.join(f'{k}={v}' for k, v in counts.items()) or 'none'})",
            "confgen_no_passing_structures",
            artifacts,
            {
                "failed_checks": counts,
                "generated": generated,
                "outcome_class": outcome_class,
                "reason_code": diagnostic_code,
                "dg_errors": search_stats.get("dg_errors"),
            },
            extra_diagnostics,
        )
    try:
        tail = open(os.path.join(rundir, "stderr.log"), encoding="utf-8").read()
    except OSError:
        tail = ""
    tail = "\n".join(tail.splitlines()[-20:])[-4000:]
    return _failed(
        f"confgen search worker failed with exit code {code}: {tail}",
        "confgen_search_failed",
        artifacts,
    )
