#!/usr/bin/env python3

"""Generate the producer-owned P0 boundary fixtures.

Every fixture is produced by running the real producer code now -- JCS vectors
come from ``confflow.domain.canonical`` through
``confflow.producer.boundary.canonicalize_text``, and the named-binding
workflow is validated by ``validate_workflow_bytes``.  Consumers copy these files and
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
    jcs_vectors,
)
from confflow.producer.contract import build_boundary_document  # noqa: E402
from confflow.producer.validation import validate_workflow_bytes  # noqa: E402

JCS_VECTORS_SCHEMA = "confflow.p0.jcs_vectors.v1"
NAMED_BINDING_WORKFLOW_SCHEMA = "confflow.p0.named_binding_workflow.v1"

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
        keyword: B3LYP D3BJ Opt
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
      native:
        keyword: B3LYP D3BJ SP
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
