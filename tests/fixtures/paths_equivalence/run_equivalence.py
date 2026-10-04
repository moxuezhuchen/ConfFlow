#!/usr/bin/env python3
"""Run every captured legacy-paths case through both execution routes.

Usage (cwd must be the exec-cf-is repo root):

    python3 docs/refactor/paths_equivalence/run_equivalence.py           # generate
    python3 docs/refactor/paths_equivalence/run_equivalence.py --check   # verify

For each case in ``cases.json`` the legacy native is executed as-is and
re-expressed as a typed v3 declaration (bare declarations get ``step`` from
``angle_step`` or the legacy default 120; ``bond_scale`` maps to
``tolerances.bond_scale``; ``strict_path_bond_check`` maps to the v3
top-level flag).  Both runs go through the public
``ConfgenExecutor().execute(item, ctx)`` entry with ``seed=11``.

All equivalence / dedup / set-equality judgements use
:func:`proper_rotation_rmsd` (reflection-free Kabsch, wrapping
``confflow.science.cluster.kabsch_rmsd``) with the threshold ``TOL = 1e-5``
angstrom.  Before any verdict is produced the tool runs three self-checks
(SC1 rigid motion -> identical, SC2 mirror -> different, SC3 real dihedral
change -> different); if any fails it exits non-zero without writing
``result.json``.

Verdicts (hydrogen cases, the only ones that count):

* ``EQUIVALENT``: same terminal state and the geometry sets agree (one-to-one
  within ``TOL`` after proper-rotation alignment);
* ``LEGACY_ONLY_TERMINAL_ROTOR``: v3 refused the terminal-endpoint declaration,
  the control declaration (terminal endpoints replaced by their unique heavy
  neighbour) completed, and deduplicated legacy structures remain that have no
  counterpart in the control set; the counts are recorded as measured;
* ``BOTH_REJECT``: legacy and v3 both ended non-COMPLETED (both reasons recorded);
* ``V3_EMPTY_DEGENERATE``: legacy produced structures while v3 or the control
  completed with zero structures and the input chain atoms are all collinear;
* ``NOT_EQUIVALENT``: anything else.

The 28 no-hydrogen cases from IS.1 are re-judged with the same metric into the
``no_hydrogen_regression`` section and do not count towards the conclusion.
``result.json`` uses ``sort_keys=True, indent=1`` and contains no timestamps or
temporary paths, so repeated runs are byte-identical.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT))

from confflow.science.cluster import kabsch_rmsd  # noqa: E402

TOL = 1e-5
SCOPE_KEYS = {"paths", "angle_step", "bond_scale", "strict_path_bond_check"}
PATH_KEYS = {"start", "end", "move", "id", "angles", "step"}
DEGENERATE_MARK = "no measurable dihedral frame"
COLLINEAR_NORM = 1e-9


def proper_rotation_rmsd(first: Any, second: Any) -> float:
    """Reflection-free Kabsch RMSD of two ``(N, 3)`` coordinate arrays."""
    return kabsch_rmsd(np.asarray(first, dtype=np.float64), np.asarray(second, dtype=np.float64))


# ---------------------------------------------------------------------------
# Self-checks SC1 / SC2 / SC3
# ---------------------------------------------------------------------------


def _rotation_matrix_60deg(axis_from: np.ndarray, axis_to: np.ndarray) -> np.ndarray:
    axis = axis_to - axis_from
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    cross = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    angle = math.radians(60.0)
    return np.eye(3) + math.sin(angle) * cross + (1.0 - math.cos(angle)) * (cross @ cross)


def _run_selfchecks(fixtures: dict[str, Any]) -> dict[str, Any]:
    """SC1/SC2/SC3; raise SystemExit when any fails (no result.json is written)."""
    report: dict[str, Any] = {"threshold": TOL}
    failures: list[str] = []

    rigid = np.asarray(fixtures["selfcheck"]["rigid"]["coordinates"], dtype=np.float64)
    rng = np.random.default_rng(12345)
    basis, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(basis) < 0.0:
        basis[:, 0] = -basis[:, 0]
    translation = rng.uniform(-3.0, 3.0, size=3)
    moved = rigid @ basis.T + translation
    sc1 = proper_rotation_rmsd(rigid, moved)
    report["SC1_rigid_proper_rotation_rmsd"] = sc1
    if sc1 > TOL:
        failures.append(f"SC1 rigid rotation gave RMSD {sc1} > {TOL}")

    chiral = np.asarray(fixtures["selfcheck"]["chiral"]["coordinates"], dtype=np.float64)
    mirrored = chiral.copy()
    mirrored[:, 0] = -mirrored[:, 0]
    sc2 = proper_rotation_rmsd(chiral, mirrored)
    report["SC2_mirror_rmsd"] = sc2
    if sc2 <= TOL:
        failures.append(f"SC2 mirror gave RMSD {sc2} <= {TOL}")

    butane = fixtures["selfcheck"]["dihedral"]
    coords = np.asarray(butane["coordinates"], dtype=np.float64)
    bonds = butane["bonds"]
    # Rotate the C3-C4 end (with its hydrogens) about the C2-C3 axis.
    moved_atoms = {2, 3}  # 0-based: C3, C4
    for begin, end in bonds:
        begin_zero, end_zero = begin - 1, end - 1
        if begin_zero in moved_atoms or end_zero in moved_atoms:
            moved_atoms.add(begin_zero)
            moved_atoms.add(end_zero)
    moved_atoms.discard(0)  # C1 and C2 sit on/before the rotation axis
    moved_atoms.discard(1)
    anchor = coords[1]  # C2: the C2-C3 bond is the rotation axis
    rotation = _rotation_matrix_60deg(coords[1], coords[2])
    twisted = coords.copy()
    for index in sorted(moved_atoms):
        twisted[index] = anchor + rotation @ (coords[index] - anchor)
    sc3 = proper_rotation_rmsd(coords, twisted)
    report["SC3_dihedral_60deg_rmsd"] = sc3
    if sc3 <= TOL:
        failures.append(f"SC3 dihedral change gave RMSD {sc3} <= {TOL}")

    report["pass"] = not failures
    report["failures"] = failures
    if failures:
        sys.stderr.write("self-check FAILED:\n" + "\n".join(failures) + "\n")
        raise SystemExit(1)
    return report


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _failure_texts(result: Any) -> list[str]:
    texts: list[str] = []
    if result.error is not None:
        texts.append(str(result.error.message))
    for diagnostic in result.diagnostics:
        texts.append(str(diagnostic.message))
    return texts


def _execute(record: dict[str, Any], native: dict[str, Any]) -> dict[str, Any]:
    """Run one case through the public executor entry and summarize it."""
    from confflow.domain import FrozenDict, StructureRecord
    from confflow.execution.confgen_executor import ConfgenExecutor
    from tests.v4.test_repair_executors import _ctx, _item, _sci

    structure = StructureRecord(
        id=record["id"],
        atoms=tuple(record["atoms"]),
        coordinates=tuple(tuple(point) for point in record["coordinates"]),
        charge=0,
        multiplicity=1,
    )
    with tempfile.TemporaryDirectory() as tmp:
        item = _item("c1:g1", "c1", [structure])
        sci = _sci(seed=11, native=FrozenDict(dict(native)))
        result = ConfgenExecutor().execute(item, _ctx(sci, tmp))
        structures = [
            {
                "ordinal": getattr(member, "ordinal", None),
                "atoms": list(member.atoms),
                "coordinates": [[float(v) for v in point] for point in member.coordinates],
            }
            for member in result.structures
        ]
        structures.sort(key=lambda entry: (entry["ordinal"] is None, entry["ordinal"]))
        return {
            "status": result.status.name,
            "error": (
                None
                if result.error is None
                else {"code": result.error.code, "message": result.error.message}
            ),
            "diagnostics": [
                {"code": diagnostic.code, "message": str(diagnostic.message)}
                for diagnostic in result.diagnostics
            ],
            "structures": structures,
            "rotors": _report_rotors(tmp),
            "failure_texts": _failure_texts(result),
        }


def _report_rotors(tmp: str) -> list[dict[str, Any]] | None:
    """Extract per-rotor angle sets from whichever report the run wrote."""
    import os

    for root, _, files in os.walk(tmp):
        for name in ("confgen_report.json", "ensemble_report.json"):
            if name in files:
                with open(os.path.join(root, name), encoding="utf-8") as handle:
                    payload = json.load(handle)
                resolution = payload.get("path_resolution") or {}
                rotors = resolution.get("rotors")
                if rotors is None:
                    return None
                return [
                    {
                        "bond": [int(v) for v in rotor["bond"]],
                        "angles": [float(v) for v in rotor["angles"]],
                    }
                    for rotor in rotors
                ]
    return None


def map_native(
    native: dict[str, Any], overrides: dict[tuple[int, str], int] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Translate a legacy native into a typed v3 declaration.

    ``overrides`` maps (path index, "start"|"end") to a replacement atom for
    the degenerate control declarations.
    """
    unmapped: list[str] = []
    v3: dict[str, Any] = {"schema_version": 3, "index_base": 1, "paths": []}
    if "strict_path_bond_check" in native:
        v3["strict_path_bond_check"] = native["strict_path_bond_check"]
    if "bond_scale" in native:
        v3["tolerances"] = {"bond_scale": native["bond_scale"]}
    default_step = native.get("angle_step", 120)
    for index, declaration in enumerate(native.get("paths", [])):
        entry: dict[str, Any] = {}
        for key in ("start", "end", "move", "id"):
            if key in declaration:
                entry[key] = declaration[key]
        if "angles" in declaration:
            entry["angles"] = declaration["angles"]
        elif "step" in declaration:
            entry["step"] = declaration["step"]
        else:
            entry["step"] = default_step
        if overrides:
            for endpoint in ("start", "end"):
                if (index, endpoint) in overrides:
                    entry[endpoint] = overrides[(index, endpoint)]
        for key in declaration:
            if key not in PATH_KEYS:
                unmapped.append(f"paths[{index}].{key}={declaration[key]!r}")
        v3["paths"].append(entry)
    return v3, unmapped


# ---------------------------------------------------------------------------
# Geometry comparison (proper-rotation RMSD, TOL = 1e-5)
# ---------------------------------------------------------------------------


def _atoms_equal(first: list[str], second: list[str]) -> bool:
    return first == second


def _match_sets(legacy: list[dict[str, Any]], v3: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Greedy one-to-one matching; returns (equal, difference notes)."""
    notes: list[str] = []
    if len(legacy) != len(v3):
        notes.append(f"structure count differs: legacy {len(legacy)} vs v3 {len(v3)}")
        return False, notes
    used: set[int] = set()
    for index, left in enumerate(legacy):
        matched = None
        for other, right in enumerate(v3):
            if other in used:
                continue
            if (
                _atoms_equal(left["atoms"], right["atoms"])
                and proper_rotation_rmsd(left["coordinates"], right["coordinates"]) <= TOL
            ):
                matched = other
                break
        if matched is None:
            notes.append(f"legacy structure {index} has no v3 counterpart within {TOL} A")
            return False, notes
        used.add(matched)
    return True, notes


def _dedup(structures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    for candidate in structures:
        duplicate = any(
            _atoms_equal(candidate["atoms"], kept["atoms"])
            and proper_rotation_rmsd(candidate["coordinates"], kept["coordinates"]) <= TOL
            for kept in unique
        )
        if not duplicate:
            unique.append(candidate)
    return unique


def _rotor_sets_equal(
    legacy: list[dict[str, Any]] | None, v3: list[dict[str, Any]] | None
) -> tuple[bool | None, list[str]]:
    if legacy is None or v3 is None:
        return None, []
    if len(legacy) != len(v3):
        return False, [f"rotor count differs: legacy {len(legacy)} vs v3 {len(v3)}"]
    notes: list[str] = []
    by_bond = {tuple(sorted(rotor["bond"])): rotor for rotor in v3}
    for rotor in legacy:
        bond = tuple(sorted(rotor["bond"]))
        other = by_bond.get(bond)
        if other is None:
            notes.append(f"rotor {list(bond)} missing in v3")
            return False, notes
        left = sorted(rotor["angles"])
        right = sorted(other["angles"])
        if len(left) != len(right) or any(abs(a - b) > TOL for a, b in zip(left, right)):
            notes.append(f"rotor {list(bond)} angle sets differ: {left} vs {right}")
            return False, notes
    return True, notes


def _hydrogen_permutations(
    structure: dict[str, Any], heavy_atom: int, bonds: list[list[int]]
) -> list[dict[str, Any]]:
    """Yield the structure with the hydrogens on ``heavy_atom`` permuted.

    Only permutations of hydrogen coordinates attached to that single
    terminal-group heavy atom are produced (the identity included).
    """
    atoms = structure["atoms"]
    hydrogen_indices = [
        index
        for index, symbol in enumerate(atoms)
        if symbol == "H"
        and any(
            (begin == heavy_atom and end == index + 1) or (end == heavy_atom and begin == index + 1)
            for begin, end in bonds
        )
    ]
    if len(hydrogen_indices) < 2:
        return [structure]
    variants: list[dict[str, Any]] = []
    for order in itertools.permutations(hydrogen_indices):
        coordinates = [list(point) for point in structure["coordinates"]]
        for position, original in enumerate(hydrogen_indices):
            coordinates[original] = structure["coordinates"][order[position]]
        variants.append({**structure, "coordinates": coordinates})
    return variants


def _legacy_only_counts(
    legacy: list[dict[str, Any]],
    reference: list[dict[str, Any]],
    bonds: list[list[int]],
    hydrogen_atom: int | None,
) -> tuple[int, int, list[int]]:
    """Return (legacy_only, symmetry_aware_legacy_only, unmatched indices).

    ``symmetry_aware_legacy_only`` recounts the unmatched structures after
    permuting the hydrogens bonded to the one terminal-group heavy atom
    (``hydrogen_atom``, 1-based) -- CH3 3!, NH2 2, OH none.
    """
    unmatched = [
        index
        for index, left in enumerate(legacy)
        if not any(
            _atoms_equal(left["atoms"], right["atoms"])
            and proper_rotation_rmsd(left["coordinates"], right["coordinates"]) <= TOL
            for right in reference
        )
    ]
    if hydrogen_atom is None or not unmatched:
        return len(unmatched), len(unmatched), unmatched
    still = 0
    for index in unmatched:
        left = legacy[index]
        found = any(
            any(
                _atoms_equal(variant["atoms"], right["atoms"])
                and proper_rotation_rmsd(variant["coordinates"], right["coordinates"]) <= TOL
                for right in reference
            )
            for variant in _hydrogen_permutations(left, hydrogen_atom, bonds)
        )
        if not found:
            still += 1
    return len(unmatched), still, unmatched


def _terminal_replacements(
    record: dict[str, Any], native: dict[str, Any], bonds: list[list[int]]
) -> dict[tuple[int, str], int] | None:
    """Map each terminal heavy-atom path endpoint to its unique heavy neighbour."""
    atoms = record["atoms"]
    heavy_set = {index + 1 for index, symbol in enumerate(atoms) if symbol != "H"}
    adjacency: dict[int, list[int]] = {}
    for begin, end in bonds:
        if begin in heavy_set and end in heavy_set:
            adjacency.setdefault(begin, []).append(end)
            adjacency.setdefault(end, []).append(begin)
    overrides: dict[tuple[int, str], int] = {}
    for index, declaration in enumerate(native.get("paths", [])):
        for endpoint in ("start", "end"):
            atom = declaration[endpoint]
            heavy_neighbours = list(adjacency.get(atom, []))
            if len(heavy_neighbours) == 1:
                replacement = heavy_neighbours[0]
                other = "end" if endpoint == "start" else "start"
                if replacement == declaration[other]:
                    return None  # start == end after replacement
                overrides[(index, endpoint)] = replacement
            elif not heavy_neighbours:
                return None  # no heavy neighbour: cannot build a control
    return overrides


def _heavy_adjacency(record: dict[str, Any], bonds: list[list[int]]) -> dict[int, list[int]]:
    atoms = record["atoms"]
    heavy_set = {index + 1 for index, symbol in enumerate(atoms) if symbol != "H"}
    adjacency: dict[int, list[int]] = {}
    for begin, end in bonds:
        if begin in heavy_set and end in heavy_set:
            adjacency.setdefault(begin, []).append(end)
            adjacency.setdefault(end, []).append(begin)
    return adjacency


def _move_side_heavy_atom(
    record: dict[str, Any], native: dict[str, Any], bonds: list[list[int]]
) -> int | None:
    """Return the declaration's move-side endpoint if it is a terminal heavy atom."""
    adjacency = _heavy_adjacency(record, bonds)
    declaration = native["paths"][0]
    endpoint = declaration["end"] if declaration["move"] == "end" else declaration["start"]
    if endpoint in adjacency and len(adjacency[endpoint]) == 1:
        return endpoint
    return None


def _collinearity(
    record: dict[str, Any], native: dict[str, Any], bonds: list[list[int]]
) -> dict[str, Any]:
    """Collinearity data for the declared path chain (heavy-atom route)."""
    adjacency = _heavy_adjacency(record, bonds)
    declaration = native["paths"][0]
    start, end = declaration["start"], declaration["end"]
    route = [start]
    seen = {start}
    queue = [start]
    reached = start == end
    while queue and not reached:
        atom = queue.pop(0)
        for neighbour in sorted(adjacency.get(atom, [])):
            if neighbour not in seen:
                seen.add(neighbour)
                route.append(neighbour)
                queue.append(neighbour)
                if neighbour == end:
                    reached = True
                    break
    coordinates = np.asarray(record["coordinates"], dtype=np.float64)
    worst = 0.0
    for first in range(len(route)):
        for second in range(first + 1, len(route)):
            for third in range(second + 1, len(route)):
                point_a = coordinates[route[first] - 1]
                point_b = coordinates[route[second] - 1]
                point_c = coordinates[route[third] - 1]
                norm = float(np.linalg.norm(np.cross(point_b - point_a, point_c - point_a)))
                worst = max(worst, norm)
    return {
        "route": route,
        "max_cross_product_norm": worst,
        "collinear": worst < COLLINEAR_NORM,
        "limit": COLLINEAR_NORM,
    }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_hydrogen(case: dict[str, Any]) -> dict[str, Any]:
    native = case["native"]
    record = case["record"]
    bonds = case.get("bonds", [])
    entry: dict[str, Any] = {"unmapped": []}
    legacy = _execute(record, native)
    v3_native, unmapped = map_native(native)
    v3 = _execute(record, v3_native)
    entry["legacy"] = legacy
    entry["v3"] = v3
    entry["unmapped"] = unmapped

    legacy_ok = legacy["status"] == "COMPLETED"
    v3_ok = v3["status"] == "COMPLETED"
    entry["counts"] = {
        "legacy_count": len(legacy["structures"]) if legacy_ok else 0,
        "control_count": 0,
        "legacy_only_count": 0,
        "symmetry_aware_legacy_only": 0,
    }

    if not legacy_ok and not v3_ok:
        entry["verdict"] = "BOTH_REJECT"
        entry["reasons"] = {
            "legacy": legacy["failure_texts"],
            "v3": v3["failure_texts"],
        }
        return entry

    if v3_ok and not legacy_ok:
        entry["verdict"] = "NOT_EQUIVALENT"
        entry["differences"] = [
            f"legacy status {legacy['status']} vs v3 status {v3['status']}",
            f"legacy failure texts: {legacy['failure_texts']}",
        ]
        return entry

    if v3_ok:
        if not legacy["structures"] and len(v3["structures"]) == 0:
            pass
        if len(v3["structures"]) == 0 and legacy_ok:
            entry["collinearity"] = _collinearity(record, native, bonds)
            if entry["collinearity"]["collinear"]:
                entry["verdict"] = "V3_EMPTY_DEGENERATE"
                return entry
        equal, notes = _match_sets(legacy["structures"], v3["structures"])
        rotors_equal, rotor_notes = _rotor_sets_equal(legacy["rotors"], v3["rotors"])
        if rotors_equal is False:
            notes.extend(rotor_notes)
        if unmapped:
            notes.append(f"unmapped native keys: {unmapped}")
        entry["differences"] = notes
        entry["verdict"] = "EQUIVALENT" if equal and not notes else "NOT_EQUIVALENT"
        if not equal:
            deduped = _dedup(legacy["structures"])
            only, aware, unmatched = _legacy_only_counts(
                deduped, v3["structures"], bonds, _move_side_heavy_atom(record, native, bonds)
            )
            entry["legacy_only_diagnostics"] = {
                "legacy_count": len(deduped),
                "v3_count": len(v3["structures"]),
                "legacy_only_count": only,
                "symmetry_aware_legacy_only": aware,
                "unmatched_legacy_indices": unmatched,
            }
            entry["counts"].update(
                {
                    "legacy_count": len(deduped),
                    "control_count": len(v3["structures"]),
                    "legacy_only_count": only,
                    "symmetry_aware_legacy_only": aware,
                }
            )
        return entry

    # v3 refused.
    refused_degenerate = any(DEGENERATE_MARK in text for text in v3["failure_texts"])
    if not refused_degenerate or not legacy_ok:
        entry["verdict"] = "NOT_EQUIVALENT"
        entry["differences"] = [
            f"legacy status {legacy['status']} vs v3 status {v3['status']}",
            f"v3 failure texts: {v3['failure_texts']}",
        ]
        return entry

    overrides = _terminal_replacements(record, native, bonds)
    if overrides is None:
        entry["verdict"] = "NOT_EQUIVALENT"
        entry["differences"] = [
            "v3 refused with an unmeasurable dihedral frame and replacing the "
            "terminal endpoint would degenerate the declaration (start == end)"
        ]
        return entry
    control_native, _ = map_native(native, overrides=overrides)
    control = _execute(record, control_native)
    entry["degenerate"] = {
        "replaced_endpoints": {
            f"paths[{index}].{endpoint}": replacement
            for (index, endpoint), replacement in sorted(overrides.items())
        },
        "control_native": control_native,
        "control_status": control["status"],
    }
    if control["status"] != "COMPLETED":
        entry["verdict"] = "NOT_EQUIVALENT"
        entry["differences"] = [
            f"control declaration ended {control['status']}: {control['failure_texts']}"
        ]
        return entry
    if len(control["structures"]) == 0:
        collinearity = _collinearity(record, native, bonds)
        entry["collinearity"] = collinearity
        if collinearity["collinear"]:
            entry["verdict"] = "V3_EMPTY_DEGENERATE"
            return entry
        entry["verdict"] = "NOT_EQUIVALENT"
        entry["differences"] = ["control completed with zero structures"]
        return entry

    deduped = _dedup(legacy["structures"])
    hydrogen_atom = _move_side_heavy_atom(record, native, bonds)
    only, aware, unmatched = _legacy_only_counts(
        deduped, control["structures"], bonds, hydrogen_atom
    )
    entry["degenerate"].update(
        {
            "legacy_structure_count": len(legacy["structures"]),
            "legacy_dedup_count": len(deduped),
            "control_structure_count": len(control["structures"]),
            "legacy_only_count": only,
            "symmetry_aware_legacy_only": aware,
            "symmetry_hydrogen_atom": hydrogen_atom,
            "unmatched_legacy_indices": unmatched,
            "legacy_dedup_set": deduped,
            "control_set": control["structures"],
        }
    )
    entry["counts"].update(
        {
            "legacy_count": len(deduped),
            "control_count": len(control["structures"]),
            "legacy_only_count": only,
            "symmetry_aware_legacy_only": aware,
        }
    )
    if only == 0:
        entry["verdict"] = "EQUIVALENT"
    else:
        entry["verdict"] = "LEGACY_ONLY_TERMINAL_ROTOR"
    return entry


def evaluate_no_h(case: dict[str, Any]) -> dict[str, Any]:
    """IS.1 regression evaluation of the no-hydrogen cases (new metric)."""
    native = case["native"]
    record = case["record"]
    entry: dict[str, Any] = {"unmapped": []}
    extra_keys = sorted(set(native) - SCOPE_KEYS)
    entry["out_of_scope_keys"] = extra_keys
    if extra_keys:
        entry["verdict"] = "OUT_OF_SCOPE"
        return entry

    legacy = _execute(record, native)
    v3_native, unmapped = map_native(native)
    v3 = _execute(record, v3_native)
    entry["legacy"] = legacy
    entry["v3"] = v3
    entry["unmapped"] = unmapped

    refused_degenerate = v3["status"] != "COMPLETED" and any(
        DEGENERATE_MARK in text for text in v3["failure_texts"]
    )
    if legacy["status"] == "COMPLETED" and refused_degenerate:
        deduped = _dedup(legacy["structures"])
        entry["legacy_dedup_count"] = len(deduped)
        equal, notes = _match_sets(deduped, v3["structures"])
        entry["verdict"] = "LEGACY_DEGENERATE" if equal else "NOT_EQUIVALENT"
        entry["differences"] = notes
        return entry
    if legacy["status"] != v3["status"]:
        entry["verdict"] = "NOT_EQUIVALENT"
        entry["differences"] = [f"status differs: legacy {legacy['status']} vs v3 {v3['status']}"]
        return entry
    equal, notes = _match_sets(legacy["structures"], v3["structures"])
    rotors_equal, rotor_notes = _rotor_sets_equal(legacy["rotors"], v3["rotors"])
    if rotors_equal is False:
        notes.extend(rotor_notes)
    if unmapped:
        notes.append(f"unmapped native keys: {unmapped}")
    entry["differences"] = notes
    entry["verdict"] = "EQUIVALENT" if equal and not notes else "NOT_EQUIVALENT"
    return entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="re-run and compare byte-for-byte")
    args = parser.parse_args(argv)

    fixtures = json.loads((HERE / "fixtures_h.json").read_text(encoding="utf-8"))
    selfcheck = _run_selfchecks(fixtures)

    document = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    hydrogen_results = {
        case["case_id"]: evaluate_hydrogen(case) for case in document["hydrogen_cases"]
    }
    regression_results = {case["case_id"]: evaluate_no_h(case) for case in document["cases"]}
    counts: dict[str, int] = {}
    for entry in hydrogen_results.values():
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    regression_counts: dict[str, int] = {}
    for entry in regression_results.values():
        regression_counts[entry["verdict"]] = regression_counts.get(entry["verdict"], 0) + 1
    result = {
        "selfcheck": selfcheck,
        "counts": counts,
        "hydrogen_cases": hydrogen_results,
        "no_hydrogen_regression": {"counts": regression_counts, "cases": regression_results},
    }
    text = json.dumps(result, sort_keys=True, indent=1) + "\n"
    if args.check:
        reference = (HERE / "result.json").read_text(encoding="utf-8")
        if text == reference:
            sys.stdout.write("check ok: result.json reproduced byte-for-byte\n")
            return 0
        sys.stdout.write("check FAILED: regenerated result.json differs from result.json\n")
        return 1
    (HERE / "result.json").write_text(text, encoding="utf-8")
    sys.stdout.write(json.dumps(counts, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
