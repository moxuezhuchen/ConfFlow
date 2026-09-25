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

V3 recipes (``build_recipe_catalog_v3``) are Workflow V3 **fragments**
(``SchemaProfile.FRAGMENT``): single-step recipes omit the step ``id`` entirely,
so no recipe ships a final identity and no consumer can mistake a template for a
runnable document. A recipe becomes runnable only through
:func:`instantiate_recipe_v3`, which allocates a fresh opaque step id
(``^s_[a-z2-7]{8}$`` -- ``s_`` plus 8 RFC 4648 base32-lowercase symbols) for
every template step, rewrites intra-recipe ``inputs`` / ``checkpoint.from_step``
references through the old-to-new map, and attaches template roots to
``attach_roots_to`` only when it is given. The allocator never issues the
sequential ``sNNN`` family (V2-to-V3 upgrade is the only sequential producer)
and never derives an id from the existing set (never ``max()+1``), so deleting
the highest step can never cause its id to be reissued.

No provenance is published inside the catalog. The ``producer`` block belongs to
the contract envelope, so the catalog's digest is a function of its content alone.
"""

from __future__ import annotations

import copy
import random
import re
import string
from collections.abc import Iterable, Mapping
from typing import Any

from .schema import WORKFLOW_SCHEMA_VERSION_V3
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
            "Relax the structure, then confirm the stationary point with a frequency calculation."
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
    {
        "id": "conformer_search",
        "label": "Conformer Search",
        "description": (
            "Build a conformer ensemble by rotating the bonds you name, then "
            "search the resulting structures."
        ),
        "category": "Conformers",
        "order": 40,
        "document": {
            "global": {},
            "steps": [
                {"name": "confgen", "type": "confgen", "params": {}},
            ],
        },
        "required_fields": ["confgen.chains"],
        "exposed_fields": [
            "confgen.chains",
            "confgen.angle_step",
            "confgen.bond_multiplier",
        ],
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


_RECIPES_V3: list[dict[str, Any]] = [
    {
        "id": "optimize",
        "label": "Optimize",
        "description": "Relax the structure to a minimum.",
        "category": "Optimization",
        "order": 10,
        "document": {
            "schema": WORKFLOW_SCHEMA_VERSION_V3,
            "global": {},
            "steps": [
                {
                    "label": "Optimize",
                    "type": "calc",
                    "inputs": [],
                    "params": {"itask": "opt"},
                },
            ],
        },
        "required_fields": ["calc.program", "calc.keyword"],
        "exposed_fields": ["calc.program", "calc.task", "calc.keyword"],
    },
    {
        "id": "opt_freq",
        "label": "Optimize + Frequency",
        "description": (
            "Relax the structure, then confirm the stationary point with a frequency calculation."
        ),
        "category": "Optimization",
        "order": 20,
        "document": {
            "schema": WORKFLOW_SCHEMA_VERSION_V3,
            "global": {},
            "steps": [
                {
                    "label": "Optimize + Frequency",
                    "type": "calc",
                    "inputs": [],
                    "params": {"itask": "opt_freq"},
                },
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
            "schema": WORKFLOW_SCHEMA_VERSION_V3,
            "global": {},
            "steps": [
                {
                    "label": "Single Point",
                    "type": "calc",
                    "inputs": [],
                    "params": {"itask": "sp"},
                },
            ],
        },
        "required_fields": ["calc.program", "calc.keyword"],
        "exposed_fields": ["calc.program", "calc.task", "calc.keyword"],
    },
    {
        "id": "conformer_search",
        "label": "Conformer Search",
        "description": (
            "Build a conformer ensemble by rotating the bonds you name, then "
            "search the resulting structures."
        ),
        "category": "Conformers",
        "order": 40,
        "document": {
            "schema": WORKFLOW_SCHEMA_VERSION_V3,
            "global": {},
            "steps": [
                {
                    "label": "Conformer Search",
                    "type": "confgen",
                    "inputs": [],
                    "params": {},
                },
            ],
        },
        "required_fields": ["confgen.chains"],
        "exposed_fields": [
            "confgen.chains",
            "confgen.angle_step",
            "confgen.bond_multiplier",
        ],
    },
]

_RECIPE_CATALOG_V3: dict[str, Any] = {
    "schema": RECIPE_CATALOG_SCHEMA,
    "label": "Calculation recipes",
    "recipes": _RECIPES_V3,
}


def build_recipe_catalog_v3() -> dict[str, Any]:
    """Return a new, isolated recipe-catalog document for Workflow V3."""
    return copy.deepcopy(_RECIPE_CATALOG_V3)


def recipe_catalog_sha256_v3() -> str:
    """Return the canonical SHA-256 of the V3 recipe catalog document."""
    return canonical_sha256(_RECIPE_CATALOG_V3)


#: Opaque step-id shape for application-generated V3 steps (RFC section 5.4):
#: ``s_`` plus 8 RFC 4648 base32-lowercase symbols. Deliberately narrower than
#: the schema grammar (:data:`WORKFLOW_V3_ID_PATTERN`), which must also admit
#: the sequential upgrade family (``sNNN``) and author-chosen tokens. In
#: particular ``s001`` matches the grammar but never matches this pattern.
RECIPE_STEP_ID_PATTERN = r"^s_[a-z2-7]{8}$"

#: The 32 symbols the opaque allocator draws from: RFC 4648 base32 lowercase
#: (26 letters plus the six digits ``2``-``7`` -- no ``0``/``1``/``8``/``9``).
STEP_ID_ALPHABET = string.ascii_lowercase + "234567"

#: How many alphabet symbols follow the ``s_`` prefix (32**8 = 2**40 values).
STEP_ID_SUFFIX_LENGTH = 8

#: Bounded redraw attempts before allocation fails (RFC section 5.5).
STEP_ID_MAX_ATTEMPTS = 32

_STEP_ID_RE = re.compile(RECIPE_STEP_ID_PATTERN)


class StepIdExhaustionError(RuntimeError):
    """The opaque allocator exhausted its bounded retries without a fresh id."""


def allocate_step_id(existing_ids: Iterable[str] = (), *, rng: random.Random | None = None) -> str:
    """Allocate one fresh opaque V3 step id outside ``existing_ids``.

    The candidate is ``s_`` plus :data:`STEP_ID_SUFFIX_LENGTH` draws from
    :data:`STEP_ID_ALPHABET`. A candidate that is already taken is discarded
    and redrawn, up to :data:`STEP_ID_MAX_ATTEMPTS` attempts, after which
    :class:`StepIdExhaustionError` is raised -- never a silent duplicate. The
    sequential ``sNNN`` family is never issued and no id is derived from the
    existing set (never ``max()+1``), so deleting the highest step can never
    cause its id to be reissued.

    ``rng`` is the randomness source (anything with ``choice(seq)``). Tests pass
    ``random.Random(seed)`` for determinism; production defaults to system
    entropy.
    """
    taken = set(existing_ids)
    chooser = rng if rng is not None else random.SystemRandom()
    for _ in range(STEP_ID_MAX_ATTEMPTS):
        candidate = "s_" + "".join(
            chooser.choice(STEP_ID_ALPHABET) for _ in range(STEP_ID_SUFFIX_LENGTH)
        )
        assert _STEP_ID_RE.fullmatch(candidate), candidate
        if candidate not in taken:
            return candidate
    raise StepIdExhaustionError(
        "could not allocate a fresh step id after "
        f"{STEP_ID_MAX_ATTEMPTS} attempts ({len(taken)} ids taken)"
    )


def instantiate_recipe_v3(
    recipe: Mapping[str, Any],
    existing_ids: Iterable[str] = (),
    attach_roots_to: str | Iterable[str] | None = None,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    """Instantiate a V3 recipe fragment into fresh, rebased steps.

    Every template step -- id-less (the shipped single-step recipes) or carrying
    a template-local id (a future multi-step recipe, e.g. ``r_opt``) -- receives
    a fresh opaque id from :func:`allocate_step_id`, drawn against
    ``existing_ids`` plus the ids already allocated by this call, so two
    instantiations into one document stay disjoint. Intra-recipe ``inputs`` and
    ``checkpoint.from_step`` references are rewritten through the old-to-new
    map; the internal topology is never altered. A template reference that names
    no template step is left as-is for validation to reject -- instantiation
    rebases, it does not invent edges.

    Roots (template steps whose ``inputs`` are empty) are attached only when
    ``attach_roots_to`` is given: ``None`` leaves roots as roots, while an
    explicit list -- including the empty list (starter semantics) -- becomes
    every root's new ``inputs``. Non-root steps keep their rebased internal
    edges.

    The function is pure: the template is deep-copied, nothing is validated,
    and the output topology is deterministic given the allocator output (pass a
    seeded ``rng`` for a reproducible instantiation). The caller validates the
    rebased steps (``ValidationProfile.RUNNABLE``) once the recipe's
    ``required_fields`` are filled.
    """
    template = recipe.get("document", recipe)
    if not isinstance(template, Mapping):
        raise ValueError("recipe template must be a mapping with a 'steps' list")
    template_steps = template.get("steps")
    if not isinstance(template_steps, list):
        raise ValueError("recipe template 'steps' must be a list")
    for index, template_step in enumerate(template_steps):
        if not isinstance(template_step, Mapping):
            raise ValueError(f"recipe step {index} must be a mapping")

    taken = set(existing_ids)
    old_to_new: dict[str, str] = {}
    fresh_ids: list[str] = []
    for template_step in template_steps:
        assert isinstance(template_step, Mapping)
        new_id = allocate_step_id(taken, rng=rng)
        taken.add(new_id)
        fresh_ids.append(new_id)
        template_id = template_step.get("id")
        if template_id is not None:
            old_to_new[str(template_id)] = new_id

    if attach_roots_to is None:
        attach: list[str] | None = None
    elif isinstance(attach_roots_to, str):
        attach = [attach_roots_to]
    else:
        attach = [str(item) for item in attach_roots_to]

    rebased: list[dict[str, Any]] = []
    for template_step, new_id in zip(template_steps, fresh_ids):
        assert isinstance(template_step, Mapping)
        step = copy.deepcopy(dict(template_step))
        template_inputs = template_step.get("inputs") or []
        if attach is not None and not list(template_inputs):
            step["inputs"] = list(attach)
        else:
            step["inputs"] = [old_to_new.get(str(item), str(item)) for item in template_inputs]
        checkpoint = step.get("checkpoint")
        if isinstance(checkpoint, Mapping) and checkpoint.get("from_step") is not None:
            rewritten = dict(checkpoint)
            target = str(checkpoint["from_step"])
            rewritten["from_step"] = old_to_new.get(target, target)
            step["checkpoint"] = rewritten
        step["id"] = new_id
        rebased.append(step)
    return rebased


__all__ = [
    "RECIPE_CATALOG_SCHEMA",
    "RECIPE_STEP_ID_PATTERN",
    "STEP_ID_ALPHABET",
    "STEP_ID_MAX_ATTEMPTS",
    "STEP_ID_SUFFIX_LENGTH",
    "StepIdExhaustionError",
    "allocate_step_id",
    "build_recipe_catalog",
    "build_recipe_catalog_v3",
    "instantiate_recipe_v3",
    "recipe_catalog_sha256",
    "recipe_catalog_sha256_v3",
]
