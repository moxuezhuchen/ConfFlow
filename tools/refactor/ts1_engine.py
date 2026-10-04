#!/usr/bin/env python3
"""Run the TS1 sigma workflow through the full ConfGen engine and record it.

Usage: ts1_engine.py --backend {default|rigid|flexible} --out FILE [--cf DIR]

The input mirrors ``test_ts1_sigma_workflow_boundary``; only the fixed
provenance string and the absent budgets differ.  Nothing here predicts a
result: whatever the engine reports is written down.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

FIXTURE_REL = Path("tests/fixtures/confgen/coordination/ts1")
EXPECTED_TOPOLOGY_BONDS = 131


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def summarize_run(run: Any) -> dict[str, Any]:
    """Return targets and leaves of an ``EngineRun`` as plain JSON data."""
    from confflow.domain._immutable import thaw_value

    targets = []
    for rec in run.target_records:
        targets.append(
            {
                "target_id": rec.target_id,
                "axis": rec.axis,
                "ordinal": rec.ordinal,
                "status": rec.status.value,
                "reason": rec.reason,
                "parent_target_id": rec.parent_target_id,
                "evidence_sha256": _sha256_text(canonical_json(thaw_value(rec.evidence))),
            }
        )
    leaves = []
    for leaf in run.leaves:
        leaves.append(
            {
                "state_key": thaw_value(leaf.state_key.to_dict()),
                "atoms": list(leaf.structure.atoms),
                "coords_sha256": _sha256_text(repr(leaf.structure.coordinates)),
            }
        )
    return {"targets": targets, "leaves": leaves}


def build_block(fixture: Path, backend: str) -> dict[str, Any]:
    from confflow.science.confgen.graph import load_typed_topology

    _graph, spec0, raw = load_typed_topology(fixture / "topology" / "typed_topology.json")
    seen: set[tuple[int, int, str]] = set()
    bonds: list[dict[str, Any]] = []
    for entry in raw["edges"]:
        key = (entry["a_1based"], entry["b_1based"], entry["type"])
        if key in seen:
            continue
        seen.add(key)
        bonds.append({"atoms": [entry["a_1based"], entry["b_1based"]], "kind": entry["type"]})
    for rel in raw["reaction_relations"]:
        key = (rel["a_1based"], rel["b_1based"], "FORMING")
        if key in seen:
            continue
        seen.add(key)
        bonds.append({"atoms": [rel["a_1based"], rel["b_1based"]], "kind": "FORMING"})
    assert len(bonds) == EXPECTED_TOPOLOGY_BONDS, len(bonds)
    benchmark = json.loads(
        (fixture / "benchmark" / "expected_coordination_benchmark.json").read_text()
    )
    sigma_perm = benchmark["sigma_site_permutation_0based"]
    constraints = json.loads((fixture / "benchmark" / "coordination_constraints.json").read_text())[
        "constraints"
    ]
    coordination: dict[str, Any] = {
        "metal_center": 47,
        "binding_sites": [
            {
                "id": site.id,
                "kind": "atom",
                "atoms": [a + 1 for a in site.atoms],
                "hapticity": 1,
            }
            for site in spec0.binding_sites
        ],
        "shapes": ["octahedral"],
        "treatment": "enumerate",
        "constraints": [
            {
                "id": entry["id"],
                "kind": "FORBIDDEN_TRANS",
                "sites": entry["sites"],
                "classification": entry["classification"],
                "provenance": entry["source"],
            }
            for entry in constraints
        ],
        "donor_configuration": list(spec0.site_ids),
        "site_group": {
            "generators": [sigma_perm],
            "provenance": "expected_sigma_witness.json",
        },
    }
    if backend != "default":
        coordination["backend"] = backend
    return {
        "schema_version": 3,
        "index_base": 1,
        "topology": {"bonds": bonds},
        "coordination": coordination,
    }


def run_ts1(cf: Path, backend: str) -> dict[str, Any]:
    from confflow.domain.structure import StructureRecord
    from confflow.science.confgen import model as core_model
    from confflow.science.confgen.engine import ConfgenEngine
    from confflow.science.confgen.graph import load_xyz_frame
    from confflow.workflow.v4.confgen_schema import ConfgenModelV3

    fixture = cf / FIXTURE_REL
    elements, xyz = load_xyz_frame(fixture / "structures" / "ts1_original.xyz")
    structure = StructureRecord(
        id="ts1",
        atoms=tuple(elements),
        coordinates=tuple(tuple(point) for point in xyz),
        charge=0,
        multiplicity=1,
    )
    block = build_block(fixture, backend)
    native = ConfgenModelV3.model_validate(block).scientific_native()
    context = core_model.build_context(structure, native)
    run = ConfgenEngine(allow_preserve_input=True).run(context)
    report = run.report_json()
    summary = summarize_run(run)
    counts = Counter(t["status"] for t in summary["targets"])
    return {
        "backend": backend,
        "status_counts": dict(sorted(counts.items())),
        "targets": summary["targets"],
        "leaves": summary["leaves"],
        "report_sha256": _sha256_text(canonical_json(report)),
        "report": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["default", "rigid", "flexible"], required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cf", default=".", help="ConfFlow worktree (default: cwd)")
    args = parser.parse_args(argv)
    cf = Path(args.cf).resolve()
    sys.path.insert(0, str(cf))
    result = run_ts1(cf, args.backend)
    text = json.dumps(result, sort_keys=True, indent=1) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
