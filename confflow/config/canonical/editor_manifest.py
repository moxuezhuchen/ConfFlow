#!/usr/bin/env python3

"""Producer-owned editor manifest: which fields exist and how to address them.

The editor manifest is the field-level editing model. It answers, for a GUI that
must render a form, the questions only ConfFlow can answer:

* which parameters exist and what they are called as *concepts*
  (``calc.program``), as opposed to where they live on the wire
  (``/steps/{index}/params/iprog``);
* which values are legal, derived from the typed literals that already define
  them (``ProgramName`` / ``TaskName``);
* what ConfFlow itself does when a member is absent (``default``);
* which field a step-scoped value falls back to when it is cleared
  (``inherit_from``), and when a field is even meaningful (``visible_when``).

Everything here is *derived* from a source that already owns the fact. No default
is written down twice: every ``default`` below reads a constant from
:mod:`confflow.shared.defaults` or :mod:`confflow.shared.confgen_params`, or is
absent because ConfFlow has no default for that member. A default is a statement
about producer behaviour, and a second copy of it would be a second, drifting
statement.

Two shapes are deliberately *not* published:

* No Qt, no widget class, no toolkit name. ``editor`` names a concept
  (``atom_pair`` means "two atom indices"), never a control.
* No provenance. The ``producer`` block and commit/dirty information belong on the
  contract envelope, so an artifact's digest stays a function of its own content.

V3 addressing (``build_editor_manifest_v3``): V1/V2 address a step-scoped field
by array position (``/steps/{index}/params/...``) and that spelling is frozen.
V3 steps are identified by stable id, so every V3 step-scoped field carries the
``/steps/{id}/params/...`` (or ``/steps/{id}/checkpoint/...``) template *plus*
an explicit ``step_selector: "id"`` member and the ``relative_pointer`` within
the selected step (``/params/...``). The template alone would be dishonest --
``steps`` is an array, so ``{id}`` is not traversable; the selector says how to
resolve it (look the step up by id). The envelope schema id stays shared
(``confflow.editor-manifest.v1``): the envelope shape is unchanged, the
selector members are additive, and ``workflow_schema_version`` tells a consumer
which addressing family a manifest uses.

V3 structured theory: ``calc.theory.method`` / ``calc.theory.basis`` /
``calc.theory.dispersion`` / ``calc.theory.solvent`` expose the structured
``params.theory`` members with id-addressed pointers. ``calc.program`` and
``calc.task`` stay the single program/task editors -- there are deliberately no
``calc.theory.program`` / ``calc.theory.task`` fields. The producer compiler
(:mod:`confflow.config.canonical.structured`) writes the edited value to both
``iprog``/``itask`` and ``theory.program``/``theory.task``, so the pair can
never disagree. Choice lists are never duplicated: program/task come from the
``ProgramName`` / ``TaskName`` literals, dispersion/solvent models from
``PROGRAM_CAPABILITIES``.
"""

from __future__ import annotations

import copy
from typing import Any, get_args

from ...shared.confgen_params import (
    DEFAULT_CONFGEN_ANGLE_STEP,
    DEFAULT_CONFGEN_BOND_THRESHOLD,
)
from ...shared.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_PROGRAM,
    DEFAULT_SCAN_COARSE_STEP,
    DEFAULT_TASK,
    DEFAULT_TOTAL_MEMORY,
    DEFAULT_TS_BOND_DRIFT_THRESHOLD,
    DEFAULT_TS_RESCUE_SCAN,
)
from .schema import WORKFLOW_SCHEMA_VERSION, WORKFLOW_SCHEMA_VERSION_V3
from .serialization import canonical_sha256
from .theory import PROGRAM_CAPABILITIES
from .types import ProgramName, TaskName

EDITOR_MANIFEST_SCHEMA = "confflow.editor-manifest.v1"

#: Human labels for the values of :data:`ProgramName`.  A value added to the
#: literal without a label here fails loudly at import rather than silently
#: publishing a choice the GUI would render as a bare token.
_PROGRAM_LABELS: dict[str, str] = {
    "g16": "Gaussian",
    "orca": "ORCA",
}

#: Human labels for the values of :data:`TaskName`.
_TASK_LABELS: dict[str, str] = {
    "opt": "Optimize",
    "opt_freq": "Optimize + Frequency",
    "sp": "Single point",
    "freq": "Frequency",
    "ts": "Transition state",
}


def _choice(value: str, label: str) -> dict[str, str]:
    """Return one labelled selectable value."""
    return {"value": value, "label": label}


def _choices(values: tuple[str, ...], labels: dict[str, str]) -> list[dict[str, str]]:
    """Return the labelled choice list for a typed literal's values.

    Keyed lookup rather than ``labels.get(value, value)`` on purpose: a new member
    of the literal is a contract change that must be reviewed, not something to
    paper over with a placeholder label.
    """
    return [_choice(value, labels[value]) for value in values]


_PROGRAM_CHOICES = _choices(get_args(ProgramName), _PROGRAM_LABELS)
_TASK_CHOICES = _choices(get_args(TaskName), _TASK_LABELS)


def program_choices() -> list[dict[str, str]]:
    """Return a fresh copy of the program choice list (derived from ProgramName)."""
    return [dict(item) for item in _PROGRAM_CHOICES]


def task_choices() -> list[dict[str, str]]:
    """Return a fresh copy of the task choice list (derived from TaskName)."""
    return [dict(item) for item in _TASK_CHOICES]


def theory_dispersion_choices() -> list[dict[str, str]]:
    """Return the dispersion choice list.

    Derived -- never duplicated -- from the union of ``dispersion_models``
    across :data:`PROGRAM_CAPABILITIES`. Labels are the mechanical uppercase
    rendering of the registry tokens, not a second hand-written list.
    """
    models = sorted(
        {
            str(model).strip().lower()
            for capabilities in PROGRAM_CAPABILITIES.values()
            for model in capabilities.get("dispersion_models", ())
            if str(model).strip()
        }
    )
    return [_choice(value, value.upper()) for value in models]


def theory_solvent_models() -> list[str]:
    """Return the solvent models the structured compiler understands.

    Derived from the union of ``solvent_models`` across
    :data:`PROGRAM_CAPABILITIES`. The ``calc.theory.solvent`` field stays free
    text (a bare name, ``MODEL(name)`` or a mapping), and its description names
    these models from this function rather than from a copied list.
    """
    return sorted(
        {
            str(model).strip().lower()
            for capabilities in PROGRAM_CAPABILITIES.values()
            for model in capabilities.get("solvent_models", ())
            if str(model).strip()
        }
    )


def _step_pointer(member: str) -> str:
    """Return the ``params`` JSON pointer a step-scoped field is addressed at."""
    return f"/steps/{{index}}/params/{member}"


_EDITOR_FIELDS: list[dict[str, Any]] = [
    # -- global ------------------------------------------------------------
    {
        "field_id": "global.charge",
        "context": "global",
        "json_pointer": "/global/charge",
        "label": "Charge",
        "description": "Total molecular charge applied to every calc step.",
        "value_type": "integer",
        "editor": "integer",
        "group": "system",
        "level": "basic",
        "order": 10,
        "default": DEFAULT_CHARGE,
    },
    {
        "field_id": "global.multiplicity",
        "context": "global",
        "json_pointer": "/global/multiplicity",
        "label": "Multiplicity",
        "description": "Spin multiplicity; must be at least 1.",
        "value_type": "integer",
        "editor": "integer",
        "group": "system",
        "level": "basic",
        "order": 20,
        "default": DEFAULT_MULTIPLICITY,
    },
    {
        "field_id": "global.cores_per_task",
        "context": "global",
        "json_pointer": "/global/cores_per_task",
        "label": "CPU cores per task",
        "description": "Default core count. A calc step may override it.",
        "value_type": "integer",
        "editor": "integer",
        "group": "resources",
        "level": "basic",
        "order": 30,
        "default": DEFAULT_CORES_PER_TASK,
    },
    {
        "field_id": "global.total_memory",
        "context": "global",
        "json_pointer": "/global/total_memory",
        "label": "Memory",
        "description": "Default memory per task, written as e.g. 16GB or 4000MB.",
        "value_type": "string",
        "editor": "memory",
        "group": "resources",
        "level": "basic",
        "order": 40,
        "default": DEFAULT_TOTAL_MEMORY,
    },
    {
        "field_id": "global.max_parallel_jobs",
        "context": "global",
        "json_pointer": "/global/max_parallel_jobs",
        "label": "Parallel jobs",
        "description": "Maximum number of tasks running concurrently.",
        "value_type": "integer",
        "editor": "integer",
        "group": "resources",
        "level": "basic",
        "order": 50,
        "default": DEFAULT_MAX_PARALLEL_JOBS,
    },
    {
        "field_id": "global.freeze",
        "context": "global",
        "json_pointer": "/global/freeze",
        "label": "Frozen atoms",
        "description": "1-based atom indices held fixed during opt / opt_freq / ts. Ignored by sp and freq.",
        "value_type": "array",
        "editor": "string_list",
        "item_type": "integer",
        "group": "system",
        "level": "advanced",
        "order": 60,
    },
    {
        "field_id": "global.energy_window",
        "context": "global",
        "json_pointer": "/global/energy_window",
        "label": "Energy window",
        "description": "Default conformer energy window in kcal/mol. No default is set.",
        "value_type": "number",
        "editor": "number",
        "group": "screening",
        "level": "advanced",
        "order": 70,
    },
    {
        "field_id": "global.scan_coarse_step",
        "context": "global",
        "json_pointer": "/global/scan_coarse_step",
        "label": "TS scan coarse step",
        "description": "Coarse scan step in Angstrom used by TS rescue.",
        "value_type": "number",
        "editor": "number",
        "group": "transition state",
        "level": "advanced",
        "order": 80,
        "default": DEFAULT_SCAN_COARSE_STEP,
    },
    {
        "field_id": "global.ts_bond_drift_threshold",
        "context": "global",
        "json_pointer": "/global/ts_bond_drift_threshold",
        "label": "TS bond drift threshold",
        "description": "Key-bond drift in Angstrom used by the geometric TS criterion.",
        "value_type": "number",
        "editor": "number",
        "group": "transition state",
        "level": "advanced",
        "order": 90,
        "default": DEFAULT_TS_BOND_DRIFT_THRESHOLD,
    },
    {
        "field_id": "global.gaussian_path",
        "context": "global",
        "json_pointer": "/global/gaussian_path",
        "label": "Gaussian executable",
        "description": (
            "Resolved by the compute server profile. Shown for reference only; "
            "the GUI never edits a remote executable path."
        ),
        "value_type": "string",
        "editor": "text",
        "group": "programs",
        "level": "advanced",
        "order": 100,
        "read_only": True,
    },
    {
        "field_id": "global.orca_path",
        "context": "global",
        "json_pointer": "/global/orca_path",
        "label": "ORCA executable",
        "description": (
            "Resolved by the compute server profile. Shown for reference only; "
            "the GUI never edits a remote executable path."
        ),
        "value_type": "string",
        "editor": "text",
        "group": "programs",
        "level": "advanced",
        "order": 110,
        "read_only": True,
    },
    # -- calc --------------------------------------------------------------
    {
        "field_id": "calc.program",
        "context": "calc",
        "json_pointer": _step_pointer("iprog"),
        "label": "Program",
        "description": "Quantum chemistry program. Relates to the wire key 'iprog'.",
        "value_type": "string",
        "editor": "select",
        "group": "calculation",
        "level": "basic",
        "order": 10,
        "choices": _PROGRAM_CHOICES,
        "default": DEFAULT_PROGRAM,
    },
    {
        "field_id": "calc.task",
        "context": "calc",
        "json_pointer": _step_pointer("itask"),
        "label": "Task",
        "description": "Calculation task. Relates to the wire key 'itask'.",
        "value_type": "string",
        "editor": "select",
        "group": "calculation",
        "level": "basic",
        "order": 20,
        "choices": _TASK_CHOICES,
        "default": DEFAULT_TASK,
    },
    {
        "field_id": "calc.keyword",
        "context": "calc",
        "json_pointer": _step_pointer("keyword"),
        "label": "Keyword",
        "description": (
            "Program keyword line, for example 'B3LYP/6-31G(d)'. "
            "Passed to the program verbatim; ConfFlow requires it to be non-empty."
        ),
        "value_type": "string",
        "editor": "text",
        "group": "calculation",
        "level": "basic",
        "order": 30,
    },
    {
        "field_id": "calc.cores_per_task",
        "context": "calc",
        "json_pointer": _step_pointer("cores_per_task"),
        "label": "CPU cores per task",
        "description": "Step override. Clear it to inherit the global value.",
        "value_type": "integer",
        "editor": "integer",
        "group": "resources",
        "level": "advanced",
        "order": 40,
        "inherit_from": "global.cores_per_task",
    },
    {
        "field_id": "calc.total_memory",
        "context": "calc",
        "json_pointer": _step_pointer("total_memory"),
        "label": "Memory",
        "description": "Step override. Clear it to inherit the global value.",
        "value_type": "string",
        "editor": "memory",
        "group": "resources",
        "level": "advanced",
        "order": 50,
        "inherit_from": "global.total_memory",
    },
    {
        "field_id": "calc.max_parallel_jobs",
        "context": "calc",
        "json_pointer": _step_pointer("max_parallel_jobs"),
        "label": "Parallel jobs",
        "description": "Step override. Clear it to inherit the global value.",
        "value_type": "integer",
        "editor": "integer",
        "group": "resources",
        "level": "advanced",
        "order": 60,
        "inherit_from": "global.max_parallel_jobs",
    },
    {
        "field_id": "calc.orca_maxcore",
        "context": "calc",
        "json_pointer": _step_pointer("orca_maxcore"),
        "label": "ORCA maxcore",
        "description": (
            "Per-core memory in MB. ORCA only, so it is hidden for Gaussian. "
            "ConfFlow computes it automatically unless it is set."
        ),
        "value_type": "integer",
        "editor": "integer",
        "group": "resources",
        "level": "advanced",
        "order": 70,
        "visible_when": {"field": "calc.program", "not_equals": "g16"},
    },
    {
        "field_id": "calc.energy_window",
        "context": "calc",
        "json_pointer": _step_pointer("energy_window"),
        "label": "Energy window",
        "description": "Step override of the conformer energy window.",
        "value_type": "number",
        "editor": "number",
        "group": "screening",
        "level": "advanced",
        "order": 80,
        "inherit_from": "global.energy_window",
    },
    {
        "field_id": "calc.ts_bond_atoms",
        "context": "calc",
        "json_pointer": _step_pointer("ts_bond_atoms"),
        "label": "TS bond atoms",
        "description": "The reactive atom pair, 1-based. Defaults to the first two frozen atoms.",
        "value_type": "array",
        "editor": "atom_pair",
        "group": "transition state",
        "level": "advanced",
        "order": 85,
        "visible_when": {"field": "calc.task", "equals": "ts"},
    },
    {
        "field_id": "calc.ts_rescue_scan",
        "context": "calc",
        "json_pointer": _step_pointer("ts_rescue_scan"),
        "label": "Attempt scan rescue",
        "description": "Run a relaxed scan when the TS search fails. Defaults to off.",
        "value_type": "boolean",
        "editor": "boolean",
        "group": "transition state",
        "level": "advanced",
        "order": 90,
        "visible_when": {"field": "calc.task", "equals": "ts"},
        "default": DEFAULT_TS_RESCUE_SCAN,
    },
    {
        "field_id": "calc.scan_coarse_step",
        "context": "calc",
        "json_pointer": _step_pointer("scan_coarse_step"),
        "label": "TS scan coarse step",
        "description": "Only meaningful when the TS scan rescue is enabled.",
        "value_type": "number",
        "editor": "number",
        "group": "transition state",
        "level": "advanced",
        "order": 100,
        "visible_when": {
            "all": [
                {"field": "calc.task", "equals": "ts"},
                {"field": "calc.ts_rescue_scan", "equals": True},
            ]
        },
        "inherit_from": "global.scan_coarse_step",
    },
    {
        "field_id": "calc.ts_bond_drift_threshold",
        "context": "calc",
        "json_pointer": _step_pointer("ts_bond_drift_threshold"),
        "label": "TS bond drift threshold",
        "description": "Applies to the geometric TS criterion for opt, opt_freq and ts.",
        "value_type": "number",
        "editor": "number",
        "group": "transition state",
        "level": "advanced",
        "order": 110,
        "visible_when": {"field": "calc.task", "in": ["opt", "opt_freq", "ts"]},
        "inherit_from": "global.ts_bond_drift_threshold",
    },
    {
        "field_id": "calc.blocks",
        "context": "calc",
        "json_pointer": _step_pointer("blocks"),
        "label": "Additional input blocks",
        "description": "Extra program input: an ORCA %block or a Gaussian input section.",
        "value_type": "string",
        "editor": "multiline",
        "group": "input blocks",
        "level": "advanced",
        "order": 120,
    },
    # -- confgen -----------------------------------------------------------
    {
        "field_id": "confgen.chains",
        "context": "confgen",
        "json_pointer": _step_pointer("chains"),
        "label": "Rotatable chains",
        "description": (
            "Required. Atom paths of the bonds to rotate, 1-based and joined by '-', "
            "for example '1-2-3-4'."
        ),
        "value_type": "array",
        "editor": "string_list",
        "group": "conformer generation",
        "level": "basic",
        "order": 10,
    },
    {
        "field_id": "confgen.angle_step",
        "context": "confgen",
        "json_pointer": _step_pointer("angle_step"),
        "label": "Angle step",
        "description": "Default rotation step in degrees for every chain bond.",
        "value_type": "integer",
        "editor": "integer",
        "group": "conformer generation",
        "level": "basic",
        "order": 20,
        "default": DEFAULT_CONFGEN_ANGLE_STEP,
    },
    {
        "field_id": "confgen.bond_multiplier",
        "context": "confgen",
        "json_pointer": _step_pointer("bond_multiplier"),
        "label": "Bond detection multiplier",
        "description": "Covalent-radius scaling used when detecting bonds.",
        "value_type": "number",
        "editor": "number",
        "group": "conformer generation",
        "level": "advanced",
        "order": 30,
        "default": DEFAULT_CONFGEN_BOND_THRESHOLD,
    },
]

_EDITOR_MANIFEST: dict[str, Any] = {
    "schema": EDITOR_MANIFEST_SCHEMA,
    "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
    "step_contexts": {"calc": "calc", "confgen": "confgen"},
    "fields": _EDITOR_FIELDS,
}


def build_editor_manifest() -> dict[str, Any]:
    """Return a new, isolated editor-manifest document.

    A copy rather than the singleton itself: a caller that mutated the returned
    document would otherwise change what the next caller -- and
    :func:`editor_manifest_sha256` -- sees.
    """
    return copy.deepcopy(_EDITOR_MANIFEST)


def editor_manifest_sha256() -> str:
    """Return the canonical SHA-256 of the editor manifest document."""
    return canonical_sha256(_EDITOR_MANIFEST)


def _v3_step_field(field: dict[str, Any]) -> dict[str, Any]:
    """Re-address one step-scoped V1 field at a stable V3 step id.

    The ``{index}`` template becomes ``{id}`` and the field gains the explicit
    selector pair: ``step_selector: "id"`` says *how* to resolve the template
    (look the step up by its stable id -- ``steps`` is an array, so the ``{id}``
    template is not itself traversable) and ``relative_pointer`` is the pointer
    within the selected step.
    """
    pointer = str(field["json_pointer"]).replace("/steps/{index}", "/steps/{id}", 1)
    reframed = copy.deepcopy(field)
    reframed["json_pointer"] = pointer
    reframed["step_selector"] = "id"
    reframed["relative_pointer"] = pointer.replace("/steps/{id}", "", 1)
    return reframed


def _theory_detail_fields() -> list[dict[str, Any]]:
    """Return the V3-only structured-theory detail fields (method/basis/...)."""
    solvent_models = theory_solvent_models()
    return [
        {
            "field_id": "calc.theory.method",
            "context": "calc",
            "json_pointer": "/steps/{id}/params/theory/method",
            "step_selector": "id",
            "relative_pointer": "/params/theory/method",
            "label": "Method",
            "description": (
                "Electronic-structure method, for example 'B3LYP'. Compiled with "
                "the basis into the program keyword; required to compile a "
                "keyword from structured fields."
            ),
            "value_type": "string",
            "editor": "text",
            "group": "theory",
            "level": "basic",
            "order": 21,
        },
        {
            "field_id": "calc.theory.basis",
            "context": "calc",
            "json_pointer": "/steps/{id}/params/theory/basis",
            "step_selector": "id",
            "relative_pointer": "/params/theory/basis",
            "label": "Basis set",
            "description": (
                "Basis set, for example 'def2-SVP'. Compiled with the method "
                "into the program keyword."
            ),
            "value_type": "string",
            "editor": "text",
            "group": "theory",
            "level": "basic",
            "order": 22,
        },
        {
            "field_id": "calc.theory.dispersion",
            "context": "calc",
            "json_pointer": "/steps/{id}/params/theory/dispersion",
            "step_selector": "id",
            "relative_pointer": "/params/theory/dispersion",
            "label": "Dispersion correction",
            "description": (
                "Dispersion correction model. Support is capability-gated by "
                "program: a model the active program does not list is rejected."
            ),
            "value_type": "string",
            "editor": "select",
            "group": "theory",
            "level": "advanced",
            "order": 23,
            "choices": theory_dispersion_choices(),
        },
        {
            "field_id": "calc.theory.solvent",
            "context": "calc",
            "json_pointer": "/steps/{id}/params/theory/solvent",
            "step_selector": "id",
            "relative_pointer": "/params/theory/solvent",
            "label": "Solvent",
            "description": (
                "Solvent specification: a bare name ('water'), 'MODEL(name)', "
                "or a mapping. Supported solvent models: "
                f"{', '.join(solvent_models)}."
            ),
            "value_type": "string",
            "editor": "text",
            "group": "theory",
            "level": "advanced",
            "order": 24,
        },
    ]


def _build_v3_fields() -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for item in _EDITOR_FIELDS:
        if item["context"] == "global":
            fields.append(copy.deepcopy(item))
            continue
        field = _v3_step_field(item)
        if field["field_id"] in {"calc.program", "calc.task", "calc.keyword"}:
            field["group"] = "theory"
            if field["field_id"] == "calc.program":
                field["description"] = (
                    "Quantum chemistry program. The single program editor: the "
                    "producer compiler writes the value to both the wire key "
                    "'iprog' and 'theory.program', so the two can never disagree."
                )
            elif field["field_id"] == "calc.task":
                field["description"] = (
                    "Calculation task. The single task editor: the producer "
                    "compiler writes the value to both the wire key 'itask' and "
                    "'theory.task', so the two can never disagree."
                )
            else:
                field["description"] = (
                    "Program keyword line, for example 'B3LYP/6-31G(d)'. "
                    "Passed to the program verbatim; ConfFlow requires it to be "
                    "non-empty. The raw escape hatch: when structured theory "
                    "fields are also set, the two must agree."
                )
        fields.append(field)
        if item["field_id"] == "calc.task":
            fields.extend(_theory_detail_fields())
    fields.append(
        {
            "field_id": "calc.checkpoint_from",
            "context": "calc",
            "json_pointer": "/steps/{id}/checkpoint/from_step",
            "step_selector": "id",
            "relative_pointer": "/checkpoint/from_step",
            "label": "Checkpoint step",
            "description": (
                "Reference to an ancestor calc step, by stable step id, whose "
                "checkpoint directory will be reused."
            ),
            "value_type": "string",
            "editor": "text",
            "group": "checkpoint",
            "level": "advanced",
            "order": 130,
        }
    )
    return fields


_EDITOR_FIELDS_V3: list[dict[str, Any]] = _build_v3_fields()

_EDITOR_MANIFEST_V3: dict[str, Any] = {
    "schema": EDITOR_MANIFEST_SCHEMA,
    "workflow_schema_version": WORKFLOW_SCHEMA_VERSION_V3,
    "step_contexts": {"calc": "calc", "confgen": "confgen"},
    "fields": _EDITOR_FIELDS_V3,
}


def build_editor_manifest_v3() -> dict[str, Any]:
    """Return a new, isolated editor-manifest document for Workflow V3."""
    return copy.deepcopy(_EDITOR_MANIFEST_V3)


def editor_manifest_sha256_v3() -> str:
    """Return the canonical SHA-256 of the V3 editor manifest document."""
    return canonical_sha256(_EDITOR_MANIFEST_V3)


__all__ = [
    "EDITOR_MANIFEST_SCHEMA",
    "build_editor_manifest",
    "build_editor_manifest_v3",
    "editor_manifest_sha256",
    "editor_manifest_sha256_v3",
    "program_choices",
    "task_choices",
    "theory_dispersion_choices",
    "theory_solvent_models",
]
