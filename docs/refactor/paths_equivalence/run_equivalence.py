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

Verdicts: ``EQUIVALENT`` (states, failure reasons, conformer sets within
1e-6 A, and rotor angle sets all agree), ``LEGACY_DEGENERATE`` (v3 refused
the declaration with "no measurable dihedral frame"; the terminal endpoint(s)
are replaced by their unique neighbours and the deduplicated legacy geometry
set equals the control v3 set), ``NOT_EQUIVALENT`` (anything else),
``OUT_OF_SCOPE`` (native has keys outside the paths scope).

Output is ``result.json`` (``sort_keys=True, indent=1``); it contains no
timestamps or temporary paths, so repeated runs are byte-identical.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT))

TOLERANCE = 1e-6
SCOPE_KEYS = {"paths", "angle_step", "bond_scale", "strict_path_bond_check"}
PATH_KEYS = {"start", "end", "move", "id", "angles", "step"}
DEGENERATE_MARK = "no measurable dihedral frame"


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


def _max_dev(first: list[list[float]], second: list[list[float]]) -> float:
    worst = 0.0
    for row_a, row_b in zip(first, second):
        for value_a, value_b in zip(row_a, row_b):
            worst = max(worst, abs(value_a - value_b))
    return worst


def _atoms_equal(first: list[str], second: list[str]) -> bool:
    return first == second


def _match_sets(legacy: list[dict[str, Any]], v3: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Greedy one-to-one geometry matching; returns (equal, difference notes)."""
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
                and _max_dev(left["coordinates"], right["coordinates"]) <= TOLERANCE
            ):
                matched = other
                break
        if matched is None:
            notes.append(f"legacy structure {index} has no v3 counterpart within {TOLERANCE} A")
            return False, notes
        used.add(matched)
    return True, notes


def _dedup(structures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    for candidate in structures:
        duplicate = any(
            _atoms_equal(candidate["atoms"], kept["atoms"])
            and _max_dev(candidate["coordinates"], kept["coordinates"]) <= TOLERANCE
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
        if len(left) != len(right) or any(abs(a - b) > TOLERANCE for a, b in zip(left, right)):
            notes.append(f"rotor {list(bond)} angle sets differ: {left} vs {right}")
            return False, notes
    return True, notes


def _terminal_replacements(
    record: dict[str, Any], native: dict[str, Any]
) -> dict[tuple[int, str], int] | None:
    """Map each terminal path endpoint to its unique neighbour (or None)."""
    from confflow.domain.elements import atomic_number
    from confflow.science.bonds import perceive_adjacency

    atomic_numbers = [atomic_number(symbol) for symbol in record["atoms"]]
    adjacency = perceive_adjacency(
        atomic_numbers,
        record["coordinates"],
        bond_scale=float(native.get("bond_scale", 1.15)),
    )
    overrides: dict[tuple[int, str], int] = {}
    for index, declaration in enumerate(native.get("paths", [])):
        for endpoint in ("start", "end"):
            atom = declaration[endpoint]
            if len(adjacency[atom - 1]) == 1:
                replacement = adjacency[atom - 1][0] + 1
                other = "end" if endpoint == "start" else "start"
                if replacement == declaration[other]:
                    return None  # start == end after replacement
                overrides[(index, endpoint)] = replacement
    return overrides


def evaluate(case: dict[str, Any]) -> dict[str, Any]:
    native = case["native"]
    record = case["record"]
    extra_keys = sorted(set(native) - SCOPE_KEYS)
    entry: dict[str, Any] = {
        "in_scope": not extra_keys,
        "out_of_scope_keys": extra_keys,
        "unmapped": [],
    }
    if extra_keys:
        entry["verdict"] = "OUT_OF_SCOPE"
        return entry

    legacy_summary = _execute(record, native)
    v3_native, unmapped = map_native(native)
    entry["unmapped"] = unmapped
    v3_summary = _execute(record, v3_native)
    entry["legacy"] = legacy_summary
    entry["v3"] = v3_summary

    v3_refused_degenerate = v3_summary["status"] == "FAILED" and any(
        DEGENERATE_MARK in text for text in v3_summary["failure_texts"]
    )
    if v3_refused_degenerate:
        overrides = _terminal_replacements(record, native)
        if overrides is None:
            entry["verdict"] = "NOT_EQUIVALENT"
            entry["differences"] = [
                "v3 refused with an unmeasurable dihedral frame and replacing the "
                "terminal endpoint would degenerate the declaration (start == end)"
            ]
            return entry
        control_native, _ = map_native(native, overrides=overrides)
        control_summary = _execute(record, control_native)
        deduped = _dedup(legacy_summary["structures"])
        equal, notes = _match_sets(deduped, control_summary["structures"])
        entry["degenerate"] = {
            "replaced_endpoints": {
                f"paths[{index}].{endpoint}": replacement
                for (index, endpoint), replacement in sorted(overrides.items())
            },
            "control_native": control_native,
            "legacy_structure_count": len(legacy_summary["structures"]),
            "legacy_dedup_count": len(deduped),
            "control_structure_count": len(control_summary["structures"]),
            "legacy_dedup_set": deduped,
            "control_set": control_summary["structures"],
            "control_status": control_summary["status"],
            "set_equal": equal,
            "notes": notes,
        }
        if (
            control_summary["status"] == "COMPLETED"
            and equal
            and legacy_summary["status"] == "COMPLETED"
        ):
            entry["verdict"] = "LEGACY_DEGENERATE"
        else:
            entry["verdict"] = "NOT_EQUIVALENT"
            entry["differences"] = notes or [
                "degenerate control did not reproduce the deduplicated legacy set"
            ]
        return entry

    differences: list[str] = []
    if legacy_summary["status"] != v3_summary["status"]:
        differences.append(
            f"status differs: legacy {legacy_summary['status']} vs v3 {v3_summary['status']}"
        )
    if legacy_summary["error"] != v3_summary["error"]:
        differences.append(
            f"failure differs: legacy {legacy_summary['error']} vs v3 {v3_summary['error']}"
        )
    equal, notes = _match_sets(legacy_summary["structures"], v3_summary["structures"])
    differences.extend(notes)
    rotors_equal, rotor_notes = _rotor_sets_equal(legacy_summary["rotors"], v3_summary["rotors"])
    if rotors_equal is False:
        differences.extend(rotor_notes)
    if unmapped:
        differences.append(f"unmapped native keys: {unmapped}")
    entry["differences"] = differences
    entry["verdict"] = "EQUIVALENT" if not differences else "NOT_EQUIVALENT"
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="re-run and compare byte-for-byte")
    args = parser.parse_args(argv)

    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
    results = {case["case_id"]: evaluate(case) for case in cases}
    counts: dict[str, int] = {}
    for entry in results.values():
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    document = {"counts": counts, "cases": results}
    text = json.dumps(document, sort_keys=True, indent=1) + "\n"
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
