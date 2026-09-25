#!/usr/bin/env python3

"""Producer-owned structured-calculation compiler for Workflow V3.

A V3 calc step may carry its science two ways: the structured
``params.theory`` vocabulary (method/basis/dispersion/solvent, optionally
program/task) and the raw ``params.keyword`` escape hatch, which is passed to
the program verbatim. This module owns the single compilation between them, so
JobDesk -- or any other consumer -- never reimplements keyword logic.

**The ONE canonical program/task mapping.** The GUI faces exactly one program
editor (``calc.program``) and one task editor (``calc.task``). There are no
separate ``calc.theory.program`` / ``calc.theory.task`` fields. The mapping the
compiler applies is:

* effective program = explicit ``program`` override, else ``theory.program``,
  else the producer default (:data:`DEFAULT_PROGRAM`);
* effective task = explicit ``task`` override, else ``theory.task``, else the
  producer default (:data:`DEFAULT_TASK`);
* the effective values are written to **both** the wire keys (``iprog`` /
  ``itask``) **and** the echoed ``theory.program`` / ``theory.task`` members,
  so the pair can never disagree;
* an override that contradicts a pinned ``theory.program`` / ``theory.task``
  fails loudly (via :func:`validate_theory_keyword_consistency`) instead of
  silently winning;
* ``theory.program`` / ``theory.task`` are echoed only when stated (by override
  or by the input theory). Defaults fill ``iprog`` / ``itask`` but are not
  pinned into ``theory``, and an empty ``theory`` mapping is omitted entirely.

Keyword rule: an explicit ``keyword`` is used verbatim after a consistency check
against the stated theory; otherwise the keyword is compiled from
``theory.method`` (required in that case) with :func:`compile_theory_keyword`.
A theory without a method and no explicit keyword cannot compile and raises
``ValueError``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...shared.defaults import DEFAULT_PROGRAM, DEFAULT_TASK
from .theory import (
    TheorySpec,
    compile_theory_keyword,
    normalize_program_name,
    normalize_task_name,
    validate_theory_keyword_consistency,
)

__all__ = ["STRUCTURED_THEORY_FIELDS", "compile_structured_calc"]

#: The structured theory vocabulary under ``params.theory``, in canonical order.
#: The set matches what :class:`TheorySpec` accepts; the order here is the
#: publication order used by the editor manifest and the v3 contract.
STRUCTURED_THEORY_FIELDS: tuple[str, ...] = (
    "program",
    "task",
    "method",
    "basis",
    "dispersion",
    "solvent",
)


def compile_structured_calc(
    theory: TheorySpec | Mapping[str, Any] | None = None,
    *,
    program: str | None = None,
    task: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    """Compile structured theory (+ overrides) into canonical V3 calc params.

    Returns a params mapping with ``iprog``, ``itask`` and ``keyword`` plus the
    echoed ``theory`` mapping when non-empty -- ready to drop into a V3 calc
    step. ``theory`` may be a :class:`TheorySpec` or a plain mapping (unknown
    theory fields raise ``ValueError``); ``program`` / ``task`` are explicit
    overrides that win over unpinned theory but fail against a pinned,
    contradicting theory value. An explicit ``keyword`` is used verbatim after
    the agreement check; otherwise it is compiled from ``theory.method``.

    Raises ``ValueError`` for an unknown program/task spelling, a
    theory/keyword disagreement, or a missing compilable keyword.
    """
    if isinstance(theory, Mapping):
        spec: TheorySpec | None = TheorySpec.from_dict(dict(theory))
    elif isinstance(theory, TheorySpec) or theory is None:
        spec = theory
    else:
        raise ValueError(f"theory must be a TheorySpec or a mapping, got {type(theory).__name__}")

    override_program = normalize_program_name(program) if program is not None else None
    override_task = normalize_task_name(task) if task is not None else None
    stated_program = override_program or (spec.program if spec is not None else None)
    stated_task = override_task or (spec.task if spec is not None else None)
    effective_program = stated_program or DEFAULT_PROGRAM
    effective_task = stated_task or DEFAULT_TASK

    raw_keyword = str(keyword).strip() if keyword is not None else ""
    if raw_keyword:
        compiled_keyword = raw_keyword
    elif spec is not None and spec.method is not None and str(spec.method).strip():
        compiled_keyword = compile_theory_keyword(
            spec, program=effective_program, task=effective_task
        )
    else:
        raise ValueError(
            "compile_structured_calc needs either theory.method "
            "or an explicit keyword to produce params.keyword"
        )

    if spec is not None:
        validate_theory_keyword_consistency(
            spec,
            compiled_keyword,
            program=effective_program,
            task=effective_task,
        )

    params: dict[str, Any] = {
        "iprog": effective_program,
        "itask": effective_task,
        "keyword": compiled_keyword,
    }
    echo: dict[str, Any] = spec.to_dict() if spec is not None else {}
    if override_program is not None:
        echo["program"] = override_program
    if override_task is not None:
        echo["task"] = override_task
    if echo:
        params["theory"] = echo
    return params
