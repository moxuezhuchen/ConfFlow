#!/usr/bin/env python3

"""Producer-owned recipe catalog: named starting points for a run.

A recipe is a partial workflow document plus the metadata needed to offer it. It
exists here, rather than in a GUI, because "Optimize + Frequency" is a claim about
what ConfFlow can be asked to do -- and because the alternative is a consumer
inventing a recipe list and attributing it to the producer.

Two rules keep a recipe honest:

1. **``document`` is an ordinary workflow fragment.** Same shape as any YAML file,
   subject to the same schema, parsed by the same entry point
   (:func:`confflow.config.canonical.parser.parse_workflow_mapping`). It carries no
   private schema of its own. Each document here is asserted to parse in the tests.
2. **A recipe pins only what makes it a different recipe.** ``opt_freq`` pins
   ``itask: opt_freq``. It must not also pin ``iprog`` or ``keyword``: those are
   the user's choices, and writing them in would turn a producer default into an
   explicit override the moment a user picked the recipe. It must not pin
   ``cores_per_task: 1`` for the same reason -- resources are defaults, not intent.

No provenance is published inside the catalog. The ``producer`` block belongs to
the contract envelope, so the catalog's digest is a function of its content alone.
"""

from __future__ import annotations

import copy
from typing import Any

from .serialization import canonical_sha256

RECIPE_CATALOG_SCHEMA = "confflow.recipe-catalog.v1"

_RECIPES: list[dict[str, Any]] = [
    {
        "id": "optimize",
        "label": "Optimize",
        "description": "Relax the structure to a minimum.",
        "category": "Optimization",
        "order": 10,
        "document": {
            "global": {},
            "steps": [
                {"name": "opt", "type": "calc", "params": {"itask": "opt"}},
            ],
        },
        "required_fields": ["calc.program", "calc.keyword"],
        "exposed_fields": ["calc.program", "calc.task", "calc.keyword"],
    },
    {
        "id": "opt_freq",
        "label": "Optimize + Frequency",
        "description": (
            "Relax the structure, then confirm the stationary point with a "
            "frequency calculation."
        ),
        "category": "Optimization",
        "order": 20,
        "document": {
            "global": {},
            "steps": [
                {"name": "opt_freq", "type": "calc", "params": {"itask": "opt_freq"}},
            ],
        },
        "required_fields": ["calc.program", "calc.keyword"],
        "exposed_fields": ["calc.program", "calc.task", "calc.keyword"],
    },
    {
        "id": "single_point",
        "label": "Single Point",
        "description": "One energy evaluation on the geometry as provided.",
        "category": "Energy",
        "order": 30,
        "document": {
            "global": {},
            "steps": [
                {"name": "sp", "type": "calc", "params": {"itask": "sp"}},
            ],
        },
        "required_fields": ["calc.program", "calc.keyword"],
        "exposed_fields": ["calc.program", "calc.task", "calc.keyword"],
    },
]

_RECIPE_CATALOG: dict[str, Any] = {
    "schema": RECIPE_CATALOG_SCHEMA,
    "label": "Calculation recipes",
    "recipes": _RECIPES,
}


def build_recipe_catalog() -> dict[str, Any]:
    """Return a new, isolated recipe-catalog document.

    A copy rather than the singleton itself, so a caller cannot change what the
    next caller -- or :func:`recipe_catalog_sha256` -- sees.
    """
    return copy.deepcopy(_RECIPE_CATALOG)


def recipe_catalog_sha256() -> str:
    """Return the canonical SHA-256 of the recipe catalog document."""
    return canonical_sha256(_RECIPE_CATALOG)


__all__ = [
    "RECIPE_CATALOG_SCHEMA",
    "build_recipe_catalog",
    "recipe_catalog_sha256",
]
