#!/usr/bin/env python3

"""A refused terminal endpoint names the offending ``paths`` key (IS.2b).

On a structure without hydrogens a path endpoint that is a terminal heavy atom
has no measurable dihedral frame, and typed v3 refuses the declaration.  The
refusal text names the declaration key (``confgen.paths[<j>].start|end``) and the
atom; torsion axes that did not come from ``paths`` keep their text unchanged.
Only wording changes: acceptance and rejection are untouched, and the key never
reaches a state key, digest or report.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from confflow.producer.intent import compile_intent

GOLDEN = Path(__file__).resolve().parent.parent.parent / "docs" / "refactor" / "paths_equivalence"


def _golden() -> Any:
    spec = importlib.util.spec_from_file_location("paths_golden", GOLDEN / "run_equivalence.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOLDEN_RUN = _golden()
CASES = {
    case["case_id"]: case
    for case in json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))["cases"]
}
CHAIN = CASES["extra_terminal_endpoint"]["record"]  # four carbons, no hydrogens


def _run_from_intent(paths: list[dict[str, Any]]) -> dict[str, Any]:
    document = compile_intent(
        {
            "schema": "confflow.intent.v1",
            "globals": {"charge": 0, "multiplicity": 1},
            "steps": [{"card": "confgen@v1", "native": {"paths": paths}}],
        }
    )
    block = {k: v for k, v in document["steps"][0]["confgen"].items() if k != "seed"}
    return GOLDEN_RUN._execute(CHAIN, block)


def _message(run: dict[str, Any]) -> str:
    assert run["status"] == "FAILED"
    return " | ".join(run["failure_texts"])


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ({"start": 1, "end": 3, "move": "end", "step": 120}, "confgen.paths[0].start (atom 1)"),
        ({"start": 2, "end": 4, "move": "end", "step": 120}, "confgen.paths[0].end (atom 4)"),
        ({"start": 1, "end": 4, "move": "end", "step": 120}, "confgen.paths[0].start (atom 1)"),
    ],
)
def test_the_refusal_names_the_path_key_and_the_atom(path: dict[str, Any], needle: str) -> None:
    text = _message(_run_from_intent([path]))
    assert needle in text
    assert "is a terminal atom with no measurable dihedral frame" in text
    # The original sentence is still there.
    assert "has no measurable dihedral frame (terminal pair)" in text


def test_the_index_of_the_offending_declaration_is_named() -> None:
    interior = {"start": 2, "end": 3, "move": "end", "step": 120}
    terminal = {"start": 1, "end": 3, "move": "end", "step": 120}
    # ``paths[0]`` only touches the bond 2-3 (both atoms have other neighbours).
    assert "confgen.paths[1].start (atom 1)" in _message(_run_from_intent([interior, terminal]))


def test_torsions_declared_axes_keep_their_original_text() -> None:
    native = {
        "schema_version": 3,
        "index_base": 1,
        "torsions": [
            {
                "id": "t1",
                "bond": [1, 2],
                "model": "relative_rotation_grid",
                "angles": [0, 120, 240],
                "treatment": "enumerate",
            }
        ],
    }
    text = _message(GOLDEN_RUN._execute(CHAIN, native))
    assert "terminal atom" not in text
    assert "torsion axis 't1': bond 1-2 has no measurable dihedral frame (terminal pair)" in text


def test_a_declaration_that_is_accepted_is_still_accepted() -> None:
    hydrogen = json.loads((GOLDEN / "cases.json").read_text(encoding="utf-8"))["hydrogen_cases"][0]
    document = compile_intent(
        {
            "schema": "confflow.intent.v1",
            "globals": {"charge": 0, "multiplicity": 1},
            "steps": [{"card": "confgen@v1", "native": hydrogen["native"]}],
        }
    )
    block = {k: v for k, v in document["steps"][0]["confgen"].items() if k != "seed"}
    assert GOLDEN_RUN._execute(hydrogen["record"], block)["status"] == "COMPLETED"
