#!/usr/bin/env python3

"""Generate the producer-owned P0 boundary fixtures.

Every fixture is produced by running the real producer code now -- JCS vectors
come from ``confflow.domain.canonical`` through
``confflow.producer.boundary.canonicalize_text``, compatibility expectations
come from ``evaluate_compatibility``, and the named-binding workflow is
validated by ``validate_workflow_bytes``.  Consumers copy these files and
verify against them; they may never hand-write an expected value.

Usage
-----
    python scripts/generate_p0_boundary_fixtures.py [--out DIR]

The default output directory is ``docs/internal/fixtures/p0_boundary``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import confflow  # noqa: E402
from confflow.producer.boundary import (  # noqa: E402
    CANONICALIZATION_ID,
    compatibility_vocabulary,
    evaluate_compatibility,
    jcs_vectors,
)
from confflow.producer.contract import build_boundary_document  # noqa: E402
from confflow.producer.validation import validate_workflow_bytes  # noqa: E402

JCS_VECTORS_SCHEMA = "confflow.p0.jcs_vectors.v1"
COMPATIBILITY_CASES_SCHEMA = "confflow.p0.compatibility_cases.v1"
NAMED_BINDING_WORKFLOW_SCHEMA = "confflow.p0.named_binding_workflow.v1"

#: Producer-owned compatibility cases.  Expected decisions are computed below,
#: never written down.
_COMPATIBILITY_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "exact_capability_match",
        "requirements": [
            {
                "kind": "capability",
                "name": "calculation",
                "contract_version": "confflow.contract.calculation.v1",
            }
        ],
        "offered": {
            "capabilities": {"calculation": "confflow.contract.calculation.v1"},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": False,
        "build_provenance_changed": False,
    },
    {
        "id": "missing_capability",
        "requirements": [
            {
                "kind": "capability",
                "name": "confgen",
                "contract_version": "confflow.contract.confgen.v1",
            }
        ],
        "offered": {
            "capabilities": {"calculation": "confflow.contract.calculation.v1"},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": False,
        "build_provenance_changed": False,
    },
    {
        "id": "capability_version_bump",
        "requirements": [
            {
                "kind": "capability",
                "name": "calculation",
                "contract_version": "confflow.contract.calculation.v1",
            }
        ],
        "offered": {
            "capabilities": {"calculation": "confflow.contract.calculation.v2"},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": False,
        "build_provenance_changed": False,
    },
    {
        "id": "display_only_manifest_change",
        "requirements": [
            {
                "kind": "capability",
                "name": "calculation",
                "contract_version": "confflow.contract.calculation.v1",
            }
        ],
        "offered": {
            "capabilities": {"calculation": "confflow.contract.calculation.v1"},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": False,
        "build_provenance_changed": False,
    },
    {
        "id": "content_identity_changed_same_capabilities",
        "requirements": [
            {
                "kind": "capability",
                "name": "calculation",
                "contract_version": "confflow.contract.calculation.v1",
            }
        ],
        "offered": {
            "capabilities": {"calculation": "confflow.contract.calculation.v1"},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": True,
        "build_provenance_changed": False,
    },
    {
        "id": "build_provenance_changed",
        "requirements": [
            {
                "kind": "capability",
                "name": "calculation",
                "contract_version": "confflow.contract.calculation.v1",
            }
        ],
        "offered": {
            "capabilities": {"calculation": "confflow.contract.calculation.v1"},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": False,
        "build_provenance_changed": True,
    },
    {
        "id": "semantics_version_mismatch",
        "requirements": [
            {
                "kind": "semantic",
                "name": "semantics_version",
                "contract_version": "confflow.workflow.v4.semantics.v2",
            }
        ],
        "offered": {
            "capabilities": {},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": False,
        "build_provenance_changed": False,
    },
    {
        "id": "unsupported_beats_revalidation",
        "requirements": [
            {
                "kind": "capability",
                "name": "goat",
                "contract_version": "confflow.contract.calculation.v1",
            }
        ],
        "offered": {
            "capabilities": {},
            "contracts": {},
            "semantics_version": "confflow.workflow.v4.semantics.v1",
        },
        "content_identity_changed": True,
        "build_provenance_changed": True,
    },
)

#: The frozen named-binding wire example: run input -> step 1 -> step 2, with a
#: step-level memory-only override over global resources.  Producer-validated
#: below; consumers use it for parser and resource-presence tests.
NAMED_BINDING_WORKFLOW = """\
schema: confflow.workflow.v4
inputs:
  structures:
    kind: structure
    cardinality: many
    pairing: per_structure
global:
  scientific_defaults:
    charge: 0
    multiplicity: 1
  resources:
    cores_per_item: 8
    memory_per_item: 16GiB
  scheduler:
    max_parallel_items: 2
steps:
  - id: opt_1
    label: Optimize
    executor: calculation
    calculation:
      program: orca
      role: opt
      native:
        calculation:
          task: optimize
    bindings:
      structure:
        source:
          run: structures
  - id: sp_1
    label: Single point
    executor: calculation
    calculation:
      program: orca
      role: sp
    resources:
      memory_per_item: 32GiB
    bindings:
      structure:
        source:
          step: opt_1
          port: structures
"""


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_jcs_vectors_fixture() -> dict[str, Any]:
    """Return the producer-owned JCS vector fixture."""
    return {
        "content_schema": JCS_VECTORS_SCHEMA,
        "canonicalization_id": CANONICALIZATION_ID,
        "producer_version": confflow.__version__,
        "vectors": jcs_vectors(),
    }


def build_compatibility_cases_fixture() -> dict[str, Any]:
    """Return compatibility cases with producer-computed expectations."""
    cases: list[dict[str, Any]] = []
    for case in _COMPATIBILITY_CASES:
        decision = evaluate_compatibility(
            case["requirements"],
            case["offered"],
            content_identity_changed=case["content_identity_changed"],
            build_provenance_changed=case["build_provenance_changed"],
        )
        cases.append(
            {
                "id": case["id"],
                "requirements": case["requirements"],
                "offered": case["offered"],
                "content_identity_changed": case["content_identity_changed"],
                "build_provenance_changed": case["build_provenance_changed"],
                "expected": {
                    "status": decision["status"],
                    "reason_codes": [reason["code"] for reason in decision["reasons"]],
                },
            }
        )
    return {
        "content_schema": COMPATIBILITY_CASES_SCHEMA,
        "vocabulary": compatibility_vocabulary(),
        "producer_version": confflow.__version__,
        "cases": cases,
    }


def build_named_binding_fixture() -> tuple[str, dict[str, Any]]:
    """Return the named-binding workflow text plus its producer validation."""
    report = validate_workflow_bytes(NAMED_BINDING_WORKFLOW.encode("utf-8"))
    if not report.ok:
        raise SystemExit(
            "the frozen named-binding workflow does not validate: "
            + json.dumps(report.to_dict(), sort_keys=True)
        )
    fixture = {
        "content_schema": NAMED_BINDING_WORKFLOW_SCHEMA,
        "producer_version": confflow.__version__,
        "workflow_yaml": NAMED_BINDING_WORKFLOW,
        "validation": report.to_dict(),
        "resource_presence": {
            "global": {"cores_per_item": 8, "memory_per_item": "16GiB"},
            "opt_1": "no step resources block (inherits everything)",
            "sp_1": "memory_per_item only; cores_per_item stays absent in the wire",
            "note": (
                "field presence is the frozen wire rule; effective values are "
                "resolved by the producer through ResourceRequest.with_defaults()"
            ),
        },
    }
    return NAMED_BINDING_WORKFLOW, fixture


def generate(out_dir: Path) -> dict[str, str]:
    """Write every fixture under *out_dir*; return file -> sha256."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    def emit(name: str, payload: Any) -> None:
        path = out_dir / name
        _write_json(path, payload)
        written[name] = hashlib.sha256(path.read_bytes()).hexdigest()

    boundary = build_boundary_document(producer_version=confflow.__version__)
    emit("boundary_protocol.json", boundary)
    emit("jcs_vectors.json", build_jcs_vectors_fixture())
    emit("compatibility_cases.json", build_compatibility_cases_fixture())
    _, named = build_named_binding_fixture()
    emit("named_binding_workflow.json", named)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "internal" / "fixtures" / "p0_boundary"),
        help="output directory (default: docs/internal/fixtures/p0_boundary)",
    )
    args = parser.parse_args(argv)
    written = generate(Path(args.out))
    for name, digest in sorted(written.items()):
        print(f"{digest}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
