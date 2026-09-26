#!/usr/bin/env python3

"""Producer-owned V4 recipe catalog: named runnable starting points.

The catalog follows the structural pattern of
:mod:`confflow.config.canonical.recipes` (entries with ``id``/``label``/
``description``/``category``/``order``/``document``/``required_fields``/
``exposed_fields``), but every ``document`` is a complete V4 workflow document
that compiles through :mod:`confflow.workflow.v4.parser` and
:mod:`confflow.workflow.v4.compiler` as shipped.

V4 recipes are runnable documents, not fragments: compilation requires a
program on every calculation step, so each recipe pins a reviewed program
default and a native keyword. Both stay flagged in ``required_fields`` (a
subset of ``exposed_fields``), which names the editors the user must confirm
before production use. A recipe pins only what makes it a different recipe
plus those compile-required defaults -- never resources, scheduler widths, or
execution bindings.

The ``tspes`` recipe is the real transition-state-plus-path chain:
TS opt, TS frequency, TS single-point branch, IRC, endpoint opt, endpoint
frequency, endpoint single point, and a reaction-profile analysis.
"""

from __future__ import annotations

import copy
from typing import Any

from ..config.canonical.recipes import RECIPE_CATALOG_SCHEMA
from ..domain.canonical import canonical_sha256
from ..workflow.v4.document import SCHEMA_ID

_STRUCTURES_INPUT = {"structures": {"kind": "structure", "cardinality": "many"}}

_CALC_EXPOSED = [
    "calc.program",
    "calc.role",
    "calc.execution_adapter",
    "calc.result_profile",
    "calc.checks",
    "calc.check_params",
    "calc.recovery",
    "calc.seed",
    "calc.native",
    "calc.overrides.charge",
    "calc.overrides.multiplicity",
    "calc.overrides.freeze",
]

_CALC_REQUIRED = ["calc.program", "calc.native"]


def _calc_step(
    step_id: str,
    *,
    program: str,
    role: str,
    bindings: dict[str, Any],
    native: dict[str, Any],
    checks: list[str],
    adapter: str = "standard",
    profile: str = "standard",
    check_params: dict[str, dict[str, Any]] | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Build one calculation step mapping for a recipe document."""
    calculation: dict[str, Any] = {
        "program": program,
        "role": role,
        "execution_adapter": adapter,
        "result_profile": profile,
        "native": dict(native),
        "checks": list(checks),
        "recovery": {"profile": "none"},
    }
    if check_params:
        calculation["check_params"] = {name: dict(params) for name, params in check_params.items()}
    step: dict[str, Any] = {
        "id": step_id,
        "executor": "calculation",
        "bindings": copy.deepcopy(bindings),
        "calculation": calculation,
    }
    if label is not None:
        step["label"] = label
    return step


def _run_binding() -> dict[str, Any]:
    """Return the standard single-structure binding to the run input."""
    return {"structure": {"source": {"run": "structures"}}}


def _document(
    steps: list[dict[str, Any]], *, inputs: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Wrap recipe steps into a complete V4 workflow document."""
    return {
        "schema": SCHEMA_ID,
        "inputs": copy.deepcopy(inputs or _STRUCTURES_INPUT),
        "steps": steps,
    }


def _single_calc_recipe(
    *,
    recipe_id: str,
    label: str,
    description: str,
    category: str,
    order: int,
    program: str,
    role: str,
    native: dict[str, Any],
    checks: list[str],
    check_params: dict[str, dict[str, Any]] | None = None,
    adapter: str = "standard",
    profile: str = "standard",
    inputs: dict[str, Any] | None = None,
    bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single-calculation recipe entry."""
    step = _calc_step(
        recipe_id,
        program=program,
        role=role,
        bindings=bindings or _run_binding(),
        native=native,
        checks=checks,
        adapter=adapter,
        profile=profile,
        check_params=check_params,
        label=label,
    )
    return {
        "id": recipe_id,
        "label": label,
        "description": description,
        "category": category,
        "order": order,
        "document": _document([step], inputs=inputs),
        "required_fields": list(_CALC_REQUIRED),
        "exposed_fields": list(_CALC_EXPOSED),
    }


def _tspes_recipe() -> dict[str, Any]:
    """Build the transition-state-plus-path (TSPES) chain recipe.

    The real chain: TS optimization, TS frequency, a TS single-point branch,
    IRC into forward/reverse endpoints, endpoint optimization, endpoint
    frequency, endpoint single point, and a reaction-profile analysis over
    the endpoint results.
    """
    ts = _calc_step(
        "ts",
        program="orca",
        role="ts",
        bindings=_run_binding(),
        native={"keyword": "B3LYP D3BJ OptTS"},
        checks=["normal_termination", "imaginary_frequency_count"],
        check_params={"imaginary_frequency_count": {"expected": 1}},
        label="Transition state",
    )
    ts_freq = _calc_step(
        "ts_freq",
        program="orca",
        role="freq",
        bindings={"structure": {"source": {"step": "ts", "port": "structures"}}},
        native={"keyword": "B3LYP D3BJ Freq"},
        checks=["normal_termination", "frequencies_required"],
        label="TS frequency",
    )
    ts_sp = _calc_step(
        "ts_sp",
        program="orca",
        role="sp",
        bindings={"structure": {"source": {"step": "ts", "port": "structures"}}},
        native={"keyword": "B3LYP D3BJ SP"},
        checks=["normal_termination"],
        label="TS single point",
    )
    irc = _calc_step(
        "irc",
        program="orca",
        role="irc",
        bindings={"structure": {"source": {"step": "ts", "port": "structures"}}},
        native={"keyword": "B3LYP D3BJ Opt", "irc": {"direction": "both"}},
        checks=["normal_termination"],
        profile="path_endpoints",
        label="IRC",
    )
    endpoint_opt = _calc_step(
        "endpoint_opt",
        program="orca",
        role="opt",
        bindings={"structure": {"source": {"step": "irc", "port": "structures"}}},
        native={"keyword": "B3LYP D3BJ Opt"},
        checks=["normal_termination", "geometry_required"],
        label="Endpoint optimization",
    )
    endpoint_freq = _calc_step(
        "endpoint_freq",
        program="orca",
        role="freq",
        bindings={"structure": {"source": {"step": "endpoint_opt", "port": "structures"}}},
        native={"keyword": "B3LYP D3BJ Freq"},
        checks=["normal_termination", "frequencies_required"],
        label="Endpoint frequency",
    )
    endpoint_sp = _calc_step(
        "endpoint_sp",
        program="orca",
        role="sp",
        bindings={"structure": {"source": {"step": "endpoint_freq", "port": "structures"}}},
        native={"keyword": "B3LYP D3BJ SP"},
        checks=["normal_termination"],
        label="Endpoint single point",
    )
    analysis: dict[str, Any] = {
        "id": "reaction_profile",
        "executor": "analysis",
        "label": "Reaction profile",
        "bindings": {
            "structures": {"source": {"step": "irc", "port": "structures"}},
            "ts_structures": {"source": {"step": "ts", "port": "structures"}},
            "results": {"source": {"step": "endpoint_sp", "port": "results"}},
            "ts_results": {"source": {"step": "ts_freq", "port": "results"}},
        },
        "analysis": {"native": {"method": "reaction_profile"}, "checks": []},
    }
    exposed = list(_CALC_EXPOSED) + ["analysis.checks", "analysis.native"]
    return {
        "id": "tspes",
        "label": "TS + Path Endpoints + SP",
        "description": (
            "Transition-state search with frequency confirmation, an IRC into "
            "forward/reverse endpoints, endpoint optimization, frequency, and "
            "single point, plus a reaction-profile analysis."
        ),
        "category": "Reaction path",
        "order": 110,
        "document": _document(
            [ts, ts_freq, ts_sp, irc, endpoint_opt, endpoint_freq, endpoint_sp, analysis]
        ),
        "required_fields": list(_CALC_REQUIRED),
        "exposed_fields": exposed,
    }


def _qst_inputs() -> dict[str, Any]:
    """Return the reactant/product run-input declarations for QST/NEB."""
    return {
        "reactants": {"kind": "structure", "cardinality": "many"},
        "products": {"kind": "structure", "cardinality": "many"},
    }


def _named_binding(port: str, run_input: str) -> dict[str, Any]:
    """Return one named-structure binding paired by explicit group key."""
    return {
        port: {
            "source": {"run": run_input},
            "pairing": "by_group_key",
            "cardinality": "one",
        }
    }


def _recipes() -> list[dict[str, Any]]:
    """Build the full V4 recipe list (fresh objects on every call)."""
    qst2 = _single_calc_recipe(
        recipe_id="qst2",
        label="QST2",
        description="Two-ended transition-state search from reactant and product.",
        category="Transition state",
        order=60,
        program="gaussian",
        role="ts",
        adapter="named_structures",
        native={
            "keyword": "B3LYP/6-31G(d) QST2 Opt",
            "atom_mapping": {"kind": "identity"},
        },
        checks=["normal_termination", "imaginary_frequency_count"],
        check_params={"imaginary_frequency_count": {"expected": 1}},
        inputs=_qst_inputs(),
        bindings={
            **_named_binding("reactant", "reactants"),
            **_named_binding("product", "products"),
        },
    )
    qst3_inputs = {**_qst_inputs(), "guesses": {"kind": "structure", "cardinality": "many"}}
    qst3 = _single_calc_recipe(
        recipe_id="qst3",
        label="QST3",
        description="Three-ended transition-state search with an explicit guess.",
        category="Transition state",
        order=70,
        program="gaussian",
        role="ts",
        adapter="named_structures",
        native={
            "keyword": "B3LYP/6-31G(d) QST3 Opt",
            "atom_mapping": {"kind": "identity"},
        },
        checks=["normal_termination", "imaginary_frequency_count"],
        check_params={"imaginary_frequency_count": {"expected": 1}},
        inputs=qst3_inputs,
        bindings={
            **_named_binding("reactant", "reactants"),
            **_named_binding("product", "products"),
            **_named_binding("guess", "guesses"),
        },
    )
    neb = _single_calc_recipe(
        recipe_id="neb",
        label="NEB",
        description="Nudged elastic band path with reactant and product endpoints.",
        category="Reaction path",
        order=80,
        program="orca",
        role="neb",
        adapter="named_structures",
        profile="ensemble",
        native={
            "keyword": "B3LYP D3BJ Opt",
            "neb": {"n_images": 7, "neb_ts": True},
            "atom_mapping": {"kind": "identity"},
        },
        checks=["normal_termination"],
        inputs=_qst_inputs(),
        bindings={
            **_named_binding("reactant", "reactants"),
            **_named_binding("product", "products"),
        },
    )
    return [
        _single_calc_recipe(
            recipe_id="optimize",
            label="Optimize",
            description="Relax the structure to a minimum.",
            category="Optimization",
            order=10,
            program="orca",
            role="opt",
            native={"keyword": "B3LYP D3BJ Opt"},
            checks=["normal_termination", "geometry_required"],
        ),
        _single_calc_recipe(
            recipe_id="single_point",
            label="Single Point",
            description="One energy evaluation on the geometry as provided.",
            category="Energy",
            order=20,
            program="orca",
            role="sp",
            native={"keyword": "B3LYP D3BJ SP"},
            checks=["normal_termination"],
        ),
        _single_calc_recipe(
            recipe_id="frequency",
            label="Frequency",
            description="Vibrational frequencies on the geometry as provided.",
            category="Energy",
            order=30,
            program="orca",
            role="freq",
            native={"keyword": "B3LYP D3BJ Freq"},
            checks=["normal_termination", "frequencies_required"],
        ),
        _single_calc_recipe(
            recipe_id="opt_freq",
            label="Optimize + Frequency",
            description="Relax the structure, then confirm the stationary point.",
            category="Optimization",
            order=40,
            program="orca",
            role="opt_freq",
            native={"keyword": "B3LYP D3BJ Opt Freq"},
            checks=["normal_termination", "frequencies_required"],
        ),
        _single_calc_recipe(
            recipe_id="transition_state",
            label="Transition State",
            description="Saddle-point search with one imaginary frequency expected.",
            category="Transition state",
            order=50,
            program="gaussian",
            role="ts",
            native={"keyword": "B3LYP/6-31G(d) Opt(TS,CalcFC)"},
            checks=["normal_termination", "imaginary_frequency_count"],
            check_params={"imaginary_frequency_count": {"expected": 1}},
        ),
        _single_calc_recipe(
            recipe_id="irc",
            label="IRC",
            description="Intrinsic reaction coordinate into forward/reverse endpoints.",
            category="Reaction path",
            order=55,
            program="orca",
            role="irc",
            profile="path_endpoints",
            native={"keyword": "B3LYP D3BJ Opt", "irc": {"direction": "both"}},
            checks=["normal_termination"],
        ),
        qst2,
        qst3,
        neb,
        _single_calc_recipe(
            recipe_id="goat",
            label="GOAT Conformers",
            description="Global-optimization conformer ensemble for one structure.",
            category="Conformers",
            order=100,
            program="orca",
            role="goat",
            profile="ensemble",
            native={"keyword": "B3LYP D3BJ Opt", "goat": {"MaxIter": 50}},
            checks=["normal_termination"],
        ),
        _tspes_recipe(),
    ]


#: Frozen recipe ids in catalog order.
RECIPE_IDS_V4: tuple[str, ...] = (
    "optimize",
    "single_point",
    "frequency",
    "opt_freq",
    "transition_state",
    "irc",
    "qst2",
    "qst3",
    "neb",
    "goat",
    "tspes",
)


def build_recipe_catalog_v4() -> dict[str, Any]:
    """Return a new, isolated V4 recipe-catalog document.

    Every recipe document compiles through the V4 parser and compiler as
    shipped; no recipe carries hidden runtime behavior.
    """
    catalog: dict[str, Any] = {
        "schema": RECIPE_CATALOG_SCHEMA,
        "workflow_schema_version": SCHEMA_ID,
        "label": "V4 calculation recipes",
        "recipes": _recipes(),
    }
    assert [recipe["id"] for recipe in catalog["recipes"]] == list(RECIPE_IDS_V4)
    return copy.deepcopy(catalog)


def get_recipe_v4(recipe_id: str) -> dict[str, Any]:
    """Return an isolated copy of the V4 recipe named *recipe_id*.

    Raises
    ------
    KeyError
        Raised when no recipe carries *recipe_id*.
    """
    for recipe in _recipes():
        if recipe["id"] == recipe_id:
            return copy.deepcopy(recipe)
    raise KeyError(f"unknown V4 recipe {recipe_id!r}")


def recipe_catalog_sha256_v4() -> str:
    """Return the canonical SHA-256 of the V4 recipe catalog document."""
    return canonical_sha256(build_recipe_catalog_v4())


__all__ = [
    "RECIPE_IDS_V4",
    "build_recipe_catalog_v4",
    "get_recipe_v4",
    "recipe_catalog_sha256_v4",
]
