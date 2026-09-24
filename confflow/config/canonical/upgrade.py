#!/usr/bin/env python3

"""Deterministic V2 -> V3 workflow upgrade.

The upgrade is a pure function of the input mapping: no UUIDs, no timestamps, no
randomness. It never re-implements V2 semantics — it starts from the V2 parser +
canonical validator, migrates the resolved IR, and re-checks the result against
the V3 DOCUMENT schema:

    raw V2 -> detect version -> V2 validate -> V2 IR -> migration
           -> V3 document -> SchemaProfile.DOCUMENT check

Migration rules live in ``docs/rfc/WORKFLOW_V3_RFC.md`` §14 (including the
"Migration metadata mapping" subsection). Two points are worth restating:

* V3 step ids are assigned in **document order** (``s001``…), the only sequential
  allocation; the ids are then persisted and never reassigned.
* All non-semantic migration metadata goes under the one reserved annotation key
  ``confflow.migration.v2``; unknown V2 fields are never promoted to the semantic
  ``extensions`` bucket. ``--unknown-params=extensions:<ns>`` is the *opposite*
  (semantic) routing and does not also write annotations.

This module is structural/semantic migration only. It does **not** implement the
R3.4 V3 semantic validator (cycle/ancestry/extension-recognition) — it relies on
the already-validated V2 semantics plus the V3 structural schema.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from ...core.exceptions import ConfFlowError
from .diagnostics import Diagnostic
from .extensions import DEFAULT_EXTENSION_REGISTRY, ExtensionRegistry, is_valid_namespace
from .issues import ConfigValidationError
from .param_fields import confgen_keys, v3_calc_keys
from .parser import detect_schema_version, parse_workflow_mapping
from .resolve import resolve_calc_step
from .schema import WORKFLOW_SCHEMA_VERSION_V3, SchemaProfile, workflow_json_schema_v3
from .validation import validate_workflow_definition
from .workflow import CanonicalStepDefinition, CanonicalWorkflowDefinition

__all__ = [
    "MIGRATION_NAMESPACE",
    "UNKNOWN_PARAMS_ANNOTATIONS",
    "UNKNOWN_PARAMS_FAIL",
    "MigrationRecord",
    "UnknownParamsPolicy",
    "UpgradeError",
    "UpgradeResult",
    "upgrade_v2_to_v3",
]

#: Reserved annotation key owned by the upgrade (RFC §14 "Migration metadata mapping").
MIGRATION_NAMESPACE = "confflow.migration.v2"

UNKNOWN_PARAMS_FAIL = "fail"
UNKNOWN_PARAMS_ANNOTATIONS = "annotations"

_ANNOTATIONS_UNKNOWN_FIELDS = f"annotations.{MIGRATION_NAMESPACE}.unknown_fields"
_ANNOTATIONS_UNKNOWN_PARAMS = f"annotations.{MIGRATION_NAMESPACE}.unknown_params"


@dataclass(frozen=True)
class UnknownParamsPolicy:
    """How to route a V2 core param unknown to the V3 vocabulary."""

    mode: str  # "fail" | "annotations" | "extensions"
    namespace: str | None = None

    @property
    def spec(self) -> str:
        return (
            UNKNOWN_PARAMS_FAIL
            if self.mode == "fail"
            else (
                UNKNOWN_PARAMS_ANNOTATIONS
                if self.mode == "annotations"
                else f"extensions:{self.namespace}"
            )
        )


@dataclass(frozen=True)
class MigrationRecord:
    """One field moved during the upgrade."""

    step: str | None  # V3 step id, or None for a root-level field
    key: str
    destination: str
    lossy: bool = False


@dataclass(frozen=True)
class UpgradeResult:
    """The migrated V3 document plus what moved and why."""

    document: dict[str, Any]
    migrations: tuple[MigrationRecord, ...] = ()
    warnings: tuple[str, ...] = field(default=())


class UpgradeError(ConfFlowError):
    """Raised when a document cannot be deterministically upgraded."""

    def __init__(self, diagnostics: Sequence[Diagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        message = "; ".join(str(diagnostic) for diagnostic in self.diagnostics)
        super().__init__(message or "workflow upgrade failed")


def _error(code: str, path: str, message: str, step_ref: str | None = None) -> Diagnostic:
    return Diagnostic(code, "error", path, message, step_ref)


def parse_unknown_params_policy(spec: str) -> UnknownParamsPolicy:
    """Parse ``--unknown-params`` into a policy, failing on an invalid value."""
    if spec == UNKNOWN_PARAMS_FAIL:
        return UnknownParamsPolicy(mode="fail")
    if spec == UNKNOWN_PARAMS_ANNOTATIONS:
        return UnknownParamsPolicy(mode="annotations")
    prefix = "extensions:"
    if spec.startswith(prefix):
        namespace = spec[len(prefix) :]
        if not is_valid_namespace(namespace):
            raise UpgradeError(
                [
                    _error(
                        "workflow.upgrade.unknown_params_namespace",
                        "unknown_params",
                        f"--unknown-params namespace is not a valid extension namespace: "
                        f"{namespace!r}",
                    )
                ]
            )
        return UnknownParamsPolicy(mode="extensions", namespace=namespace)
    raise UpgradeError(
        [
            _error(
                "workflow.upgrade.unknown_params_policy",
                "unknown_params",
                f"unknown --unknown-params policy: {spec!r} "
                "(expected 'fail', 'annotations' or 'extensions:<namespace>')",
            )
        ]
    )


def _known_param_keys(step_type: str) -> frozenset[str]:
    return v3_calc_keys() if step_type == "calc" else confgen_keys()


def _resolve_chk_from_step(
    text: str, steps: tuple[CanonicalStepDefinition, ...], id_by_name: Mapping[str, str]
) -> str:
    """Resolve a V2 ``chk_from_step`` value exactly as V2 does (name or 1-based position)."""
    if text.isdigit():
        position = int(text)
        if 1 <= position <= len(steps):
            return id_by_name[steps[position - 1].name]
    elif text in id_by_name:
        return id_by_name[text]
    # Unreachable: V2 definition validation already rejected unresolvable refs.
    raise UpgradeError(
        [
            _error(
                "workflow.upgrade.chk_unresolved",
                "chk_from_step",
                f"unresolved chk_from_step: {text!r}",
            )
        ]
    )


def _migration_annotations(
    *, unknown_fields: dict[str, Any] | None, unknown_params: dict[str, Any] | None
) -> dict[str, Any]:
    """Assemble the reserved migration annotation block, omitting empty groups."""
    inner: dict[str, Any] = {}
    if unknown_fields:
        inner["unknown_fields"] = unknown_fields
    if unknown_params:
        inner["unknown_params"] = unknown_params
    return {MIGRATION_NAMESPACE: inner} if inner else {}


def _canonicalise_present_calc_aliases(
    params: dict[str, Any], definition: CanonicalWorkflowDefinition
) -> None:
    """Canonicalise *present* ``iprog``/``itask`` alias values in place (no defaults added)."""
    if "iprog" not in params and "itask" not in params:
        return
    resolved = resolve_calc_step(params, definition.global_options)
    if "iprog" in params:
        params["iprog"] = resolved.program
    if "itask" in params:
        params["itask"] = resolved.task


def upgrade_v2_to_v3(
    raw: Mapping[str, Any],
    *,
    unknown_params_policy: UnknownParamsPolicy | str = UNKNOWN_PARAMS_FAIL,
    registry: ExtensionRegistry | None = None,
) -> UpgradeResult:
    """Deterministically migrate a V2 workflow mapping into a V3 document."""
    if isinstance(unknown_params_policy, str):
        policy = parse_unknown_params_policy(unknown_params_policy)
    else:
        policy = unknown_params_policy
    extensions_registry = registry if registry is not None else DEFAULT_EXTENSION_REGISTRY

    if not isinstance(raw, Mapping):
        raise UpgradeError(
            [_error("workflow.upgrade.root_not_mapping", "", "workflow root must be a mapping")]
        )

    # 1. Version dispatch: refuse V3 (ids are persist-once), fail closed on unknown.
    try:
        version = detect_schema_version(raw)
    except ConfigValidationError as exc:
        raise UpgradeError(
            [_error("workflow.upgrade.schema", "schema", exc.issue.message)]
        ) from exc
    if version == WORKFLOW_SCHEMA_VERSION_V3:
        raise UpgradeError(
            [
                _error(
                    "workflow.upgrade.already_v3",
                    "schema",
                    "document is already Workflow V3; upgrade expects a V2 document",
                )
            ]
        )

    # 2. The source V2 document must be semantically valid as-is.
    source_errors = [
        diagnostic for diagnostic in validate_workflow_definition(raw) if diagnostic.is_error
    ]
    if source_errors:
        raise UpgradeError(source_errors)

    workflow = parse_workflow_mapping(raw)
    from .v2_adapter import to_canonical_workflow

    definition = to_canonical_workflow(workflow)

    # 3. Stable ids: document order, the only sequential allocation.
    id_by_name = {
        step.name: f"s{index:03d}" for index, step in enumerate(definition.steps, start=1)
    }

    records: list[MigrationRecord] = []
    warnings: list[str] = []
    unknown_param_errors: list[Diagnostic] = []

    # 4. Per-step migration.
    out_steps: list[dict[str, Any]] = []
    for step in definition.steps:
        step_id = id_by_name[step.name]
        params = copy.deepcopy(step.params)

        checkpoint: dict[str, str] | None = None
        chk = params.pop("chk_from_step", None)
        if chk is not None and str(chk).strip():
            checkpoint = {
                "from_step": _resolve_chk_from_step(str(chk).strip(), definition.steps, id_by_name)
            }

        if step.type == "calc":
            _canonicalise_present_calc_aliases(params, definition)

        unknown_keys = [key for key in params if key not in _known_param_keys(step.type)]
        step_extensions: dict[str, Any] = {}
        unknown_params_annotation: dict[str, Any] | None = None
        if unknown_keys:
            if policy.mode == "fail":
                for key in unknown_keys:
                    unknown_param_errors.append(
                        _error(
                            "workflow.upgrade.unknown_param",
                            f"steps[{step_id}].params.{key}",
                            f"step {step.name!r} has a parameter unknown to the V3 {step.type} "
                            f"vocabulary: {key!r}. Re-route it with --unknown-params="
                            "annotations|extensions:<namespace>, or fix/remove it.",
                            step_id,
                        )
                    )
            elif policy.mode == "annotations":
                unknown_params_annotation = {
                    key: copy.deepcopy(params[key]) for key in unknown_keys
                }
                for key in unknown_keys:
                    params.pop(key)
                    records.append(
                        MigrationRecord(step_id, key, _ANNOTATIONS_UNKNOWN_PARAMS, lossy=True)
                    )
                warnings.append(
                    f"step {step.name!r}: parameter(s) {', '.join(sorted(unknown_keys))} moved to "
                    f"{_ANNOTATIONS_UNKNOWN_PARAMS}; they no longer participate in execution "
                    "semantics (non-semantic annotation)."
                )
            else:  # extensions
                assert policy.namespace is not None
                step_extensions[policy.namespace] = {
                    key: copy.deepcopy(params[key]) for key in unknown_keys
                }
                for key in unknown_keys:
                    params.pop(key)
                    records.append(MigrationRecord(step_id, key, f"extensions.{policy.namespace}"))
                if not extensions_registry.is_known(policy.namespace):
                    warnings.append(
                        f"step {step.name!r}: parameter(s) {', '.join(sorted(unknown_keys))} moved to "
                        f"extension namespace {policy.namespace!r}, which the current producer does "
                        "not recognize; the document is structurally valid but future runnable "
                        "validation may reject it."
                    )

        step_unknown_fields = copy.deepcopy(step.extensions)
        for key in step_unknown_fields:
            records.append(MigrationRecord(step_id, key, _ANNOTATIONS_UNKNOWN_FIELDS))

        step_annotation = _migration_annotations(
            unknown_fields=step_unknown_fields or None,
            unknown_params=unknown_params_annotation,
        )

        out_step: dict[str, Any] = {
            "id": step_id,
            "label": step.label,
            "type": step.type,
        }
        if not step.enabled:
            out_step["enabled"] = False
        out_step["inputs"] = [id_by_name[name] for name in step.predecessors]
        out_step["params"] = params
        if checkpoint is not None:
            out_step["checkpoint"] = checkpoint
        if step_extensions:
            out_step["extensions"] = step_extensions
        if step_annotation:
            out_step["annotations"] = step_annotation
        out_steps.append(out_step)

    if unknown_param_errors:
        raise UpgradeError(unknown_param_errors)

    # 5. Root migration: unknown root-level fields are non-semantic (never extensions).
    root_unknown_fields = copy.deepcopy(definition.extensions)
    for key in root_unknown_fields:
        records.append(MigrationRecord(None, key, _ANNOTATIONS_UNKNOWN_FIELDS))
    root_annotation = _migration_annotations(
        unknown_fields=root_unknown_fields or None, unknown_params=None
    )

    document: dict[str, Any] = {"schema": WORKFLOW_SCHEMA_VERSION_V3}
    if "global" in raw:
        document["global"] = copy.deepcopy(dict(raw.get("global") or {}))
    document["steps"] = out_steps
    if root_annotation:
        document["annotations"] = root_annotation

    # 6. The migrated document must satisfy the V3 DOCUMENT profile.
    validator = Draft202012Validator(workflow_json_schema_v3(SchemaProfile.DOCUMENT))
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path)
        raise UpgradeError(
            [
                _error(
                    "workflow.upgrade.output_invalid",
                    path,
                    f"internal error: migrated document failed V3 validation: {first.message}",
                )
            ]
        )

    return UpgradeResult(document=document, migrations=tuple(records), warnings=tuple(warnings))
