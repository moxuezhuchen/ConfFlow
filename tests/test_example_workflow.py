#!/usr/bin/env python3

"""The shipped example workflow is a current V4 document.

``confflow.example.yaml`` is installed as
``share/confflow/confflow.example.yaml`` and referenced by the README, so it is
a *documented* artifact: it must be a document the shipped runtime actually
accepts.  Architecture Diet PR-9 converted it once from the released V2 shape
(``global`` + ``steps[].params.iprog/itask``) to V4 and deleted the V2 example;
this test keeps the converted file from silently drifting out of the only
supported format.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from confflow.producer.validation import validate_workflow_bytes
from confflow.workflow.v4 import compile_workflow

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "confflow.example.yaml"


def test_example_is_a_v4_document() -> None:
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert document["schema"] == "confflow.workflow.v4"


def test_example_compiles_and_validates() -> None:
    raw = EXAMPLE.read_bytes()
    report = validate_workflow_bytes(raw)
    assert report.ok is True, [item["message"] for item in report.errors()]
    assert report.schema_id == "confflow.workflow.v4"

    compiled = compile_workflow(yaml.safe_load(raw))
    assert compiled.ok is True, [item.message for item in compiled.diagnostics]
    assert compiled.plan is not None


def test_example_carries_no_retired_vocabulary() -> None:
    text = EXAMPLE.read_text(encoding="utf-8")
    for retired in ("iprog", "itask", "chk_from_step", "auto_clean", "ibkout"):
        # The comments may *name* the retired vocabulary once to explain the
        # conversion; the YAML keys must not use it.
        document = yaml.safe_load(text)
        for step in document["steps"]:
            assert retired not in step
            assert retired not in (step.get("calculation") or {})
