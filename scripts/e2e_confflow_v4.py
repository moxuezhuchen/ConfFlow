#!/usr/bin/env python3

"""ConfFlow V4 whole-loop E2E smoke script (workstream E).

Runs the cross-repo loop with REAL producer bytes and prints every stage::

    .venv/bin/python scripts/e2e_confflow_v4.py [--producer-version X] [--contract-out PATH]

Stages: generate contract bytes -> write shared file -> digest re-verify ->
tspes recipe -> REAL validator on the SAME bytes -> validated==submitted gate
-> fake native chain (20 TS -> 40 endpoints) -> REAL reaction-profile analysis
(20 groups) -> REAL manifest build -> self-consistency check.

Exit codes: 0 success; 1 usage/environment error; 2 E2E failure (the
structured failure ``code`` is printed, never a silent fallback).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from confflow.analysis.reaction import ReactionNodeGroup, assemble_reaction_result  # noqa: E402
from confflow.analysis.thermochemistry import EnergyModel  # noqa: E402
from confflow.domain.canonical import canonical_json_bytes, canonical_sha256  # noqa: E402
from confflow.domain.result import ResultSet, ScientificResult  # noqa: E402
from confflow.domain.units import Unit  # noqa: E402
from confflow.producer import RESULT_MANIFEST_SCHEMA, build_run_result_manifest  # noqa: E402
from confflow.producer.contract import contract_digest_of, generate_contract_bytes  # noqa: E402
from confflow.producer.validation import validate_workflow_bytes  # noqa: E402

N_TS = 20


class E2EFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


def _jcs(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def stage(name: str) -> None:
    print(f"==> {name}", flush=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="ConfFlow V4 whole-loop E2E")
    parser.add_argument("--producer-version", default="4.6.0-e2e")
    parser.add_argument("--contract-out", default=None)
    args = parser.parse_args(argv)

    try:
        stage("1/8 generate REAL producer bytes")
        payload = generate_contract_bytes(producer_version=args.producer_version)
        envelope = json.loads(payload.decode("utf-8"))
        print(f"    bytes={len(payload)} schema={envelope['content_schema']}")
        print(f"    contract_digest={envelope['contract_digest']}")

        out = Path(args.contract_out) if args.contract_out else Path(
            os.environ.get("CONFFLOW_CONTRACT_JSON", str(Path(tempfile.gettempdir()) / "confflow-v46-contract.json"))
        )
        try:
            out.write_bytes(payload)
            print(f"    wrote {out}")
        except OSError as exc:
            raise E2EFailure("contract_write_failed", f"cannot write {out}: {exc}") from exc

        stage("2/8 digest re-verification (same bytes, both sides)")
        if canonical_json_bytes(envelope) != payload:
            raise E2EFailure("bad_digest", "wire bytes are not canonical")
        if envelope["contract_digest"] != contract_digest_of(envelope):
            raise E2EFailure("bad_digest", "contract_digest mismatch")
        for name in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema"):
            if envelope[f"{name}_sha256"] != canonical_sha256(envelope[name]):
                raise E2EFailure("bad_digest", f"{name} digest mismatch")
        tampered = copy.deepcopy(envelope)
        tampered["workflow_schema"]["title"] = "evil"
        if contract_digest_of(tampered) == envelope["contract_digest"]:
            raise E2EFailure("bad_digest", "tamper not detected (digest did not change)")
        print("    4/4 artifact digests ok; tamper changes the digest")

        stage("3/8 recipe -> workflow document (tspes, from the same bytes)")
        recipes = envelope["recipe_catalog"]["recipes"]
        recipe = next(r for r in recipes if r["id"] == "tspes")
        document = copy.deepcopy(recipe["document"])
        print(f"    steps={[s['id'] for s in document['steps']]}")

        stage("4/8 REAL validator on the SAME bytes")
        workflow_bytes = _jcs(document)
        report = validate_workflow_bytes(workflow_bytes)
        if not report.ok:
            raise E2EFailure(
                "validation_rejects", f"validator rejected the recipe: {report.diagnostics}"
            )
        print(f"    ok schema={report.to_dict()['schema']} definition_digest={report.definition_digest}")

        stage("5/8 validated==submitted gate + tamper probe")
        receipt = "sha256:" + hashlib.sha256(workflow_bytes).hexdigest()
        if "sha256:" + hashlib.sha256(workflow_bytes).hexdigest() != receipt:
            raise E2EFailure("validated_not_submitted", "gate miscomputed")
        if "sha256:" + hashlib.sha256(workflow_bytes.replace(b"OptTS", b"Evil")).hexdigest() == receipt:
            raise E2EFailure("validated_not_submitted", "tamper not detected by gate")
        print("    gate holds; tampered bytes rejected")

        stage("6/8 fake native chain (20 TS -> IRC -> 40 endpoints) + REAL analysis x20")
        model = EnergyModel(
            mode="direct", electronic_selector="energy",
            correction_selector="gibbs_correction", fallback="none",
        )
        analyses = []
        produced_structures: set[str] = set()
        produced_results: set[str] = set()
        for i in range(N_TS):
            ts, fwd, rev = f"t{i:02d}", f"t{i:02d}:endpoint:forward:0", f"t{i:02d}:endpoint:reverse:0"
            group = ReactionNodeGroup(
                group_key=f"rxn-{i:02d}", ts_structure_id=ts,
                forward_structure_id=fwd, reverse_structure_id=rev,
            )
            base = -76.0 - i * 0.001
            lookup = {}
            for node, subject, shift in (("ts", ts, 0.0), ("forward", fwd, -0.05), ("reverse", rev, -0.03)):
                e_val, g_val = base + shift, base + shift + 0.1
                lookup[subject] = ResultSet.of(
                    ScientificResult(kind="energy", value=e_val, unit=Unit.HARTREE,
                                     subject_structure_id=subject, source_step_id="endpoint_sp"),
                    ScientificResult(kind="gibbs_energy", value=g_val, unit=Unit.HARTREE,
                                     subject_structure_id=subject, source_step_id="endpoint_sp"),
                )
            outcome = assemble_reaction_result(group, model, lookup, analysis_step_id="reaction_profile")
            if not outcome.ok:
                raise E2EFailure("missing_analysis_result", f"group rxn-{i:02d} failed closed")
            entry = {
                "group_key": f"rxn-{i:02d}", "ts_structure_id": ts,
                "forward_structure_id": fwd, "reverse_structure_id": rev,
                "results": [r.to_dict() for r in outcome.results],
                "source_result_ids": [r.value_digest for r in outcome.results],
            }
            analyses.append(entry)
            produced_structures.update([ts, fwd, rev])
            produced_results.update(entry["source_result_ids"])
        print(f"    groups={len(analyses)} endpoints={len(produced_structures) - N_TS} "
              f"results_per_group={len(analyses[0]['results'])}")

        stage("7/8 REAL manifest build")
        assert report.definition_digest is not None
        store: dict[str, bytes] = {}
        artifacts = []
        for entry in analyses:
            blob = canonical_json_bytes({"group_key": entry["group_key"]})
            locator = f"artifacts/{entry['group_key']}/reaction_profile.json"
            store[locator] = blob
            artifacts.append({"role": "reaction_profile",
                              "checksum": "sha256:" + hashlib.sha256(blob).hexdigest(),
                              "locator": locator})
        manifest = build_run_result_manifest(
            run_id="run-v46-e2e-script", status="completed",
            definition_digest=report.definition_digest,
            producer_version=args.producer_version,
            steps=[{"id": "reaction_profile", "status": "completed",
                    "counts": {"completed": N_TS, "failed": 0}}],
            analyses=analyses, artifacts=artifacts,
        )
        assert manifest["content_schema"] == RESULT_MANIFEST_SCHEMA
        print(f"    manifest run_id={manifest['run_id']} analyses={len(manifest['analyses'])}")

        stage("8/8 manifest self-consistency")
        for entry in manifest["analyses"]:
            for ref in (entry["ts_structure_id"], entry["forward_structure_id"],
                        entry["reverse_structure_id"]):
                if ref not in produced_structures:
                    raise E2EFailure("manifest_mismatch", f"ref {ref!r} never produced")
            for source in entry["source_result_ids"]:
                if source not in produced_results:
                    raise E2EFailure("missing_analysis_result", f"source {source!r} never produced")
        for artifact in manifest["artifacts"]:
            blob = store.get(artifact["locator"])
            if blob is None or "sha256:" + hashlib.sha256(blob).hexdigest() != artifact["checksum"]:
                raise E2EFailure("manifest_mismatch", f"artifact {artifact['locator']!r} mismatch")
        if manifest["provenance"]["version"] != envelope["producer"]["version"]:
            raise E2EFailure("result_producer_mismatch", "manifest provenance vs contract producer")
        print("    all refs resolve; all checksums match; provenance matches contract")
    except E2EFailure as exc:
        print(f"E2E FAILED {exc}", file=sys.stderr)
        return 2
    print("E2E OK: whole loop completed on real producer bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
