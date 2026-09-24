"""Canonical workflow configuration bindings for safe resume."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from ...shared.confgen_params import confgen_known_keys, resolve_confgen_params
from ...shared.defaults import DEFAULT_SCAN_FINE_HALF_WINDOW, DEFAULT_SCAN_MAX_STEPS
from .resolve import resolve_calc_step
from .schema import WORKFLOW_SCHEMA_VERSION, workflow_schema_sha256
from .serialization import canonical_json, canonical_sha256
from .types import GlobalOptions

if TYPE_CHECKING:
    from .workflow import CanonicalWorkflowDefinition

WORKFLOW_BINDING_SCHEMA = "confflow.workflow_binding.v1"
_SHA256_PREFIX = "sha256:"
_BINDING_KEYS = frozenset({"schema", "workflow_schema", "workflow_schema_sha256", "fingerprint"})


class WorkflowBindingCompatibilityError(ValueError):
    """A persisted workflow binding cannot be safely interpreted."""


class WorkflowFingerprintError(ValueError):
    """A workflow contains a value that cannot be fingerprinted strictly."""


@dataclass(frozen=True)
class WorkflowConfigBinding:
    """The immutable schema and semantic digest recorded in workflow state."""

    schema: str
    workflow_schema: str
    workflow_schema_sha256: str
    fingerprint: str

    @property
    def schema_version(self) -> str:
        return self.schema

    @property
    def digest(self) -> str:
        return self.fingerprint

    def to_dict(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "workflow_schema": self.workflow_schema,
            "workflow_schema_sha256": self.workflow_schema_sha256,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> WorkflowConfigBinding:
        if not isinstance(raw, Mapping):
            raise WorkflowBindingCompatibilityError("workflow config binding must be an object")
        keys = set(raw)
        if keys != _BINDING_KEYS:
            unknown = sorted(str(key) for key in keys - _BINDING_KEYS)
            missing = sorted(str(key) for key in _BINDING_KEYS - keys)
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if unknown:
                detail.append(f"unknown {', '.join(unknown)}")
            raise WorkflowBindingCompatibilityError(
                "invalid workflow config binding: " + "; ".join(detail)
            )
        values = {key: raw[key] for key in _BINDING_KEYS}
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise WorkflowBindingCompatibilityError(
                "workflow config binding fields must be non-empty strings"
            )
        if values["schema"] != WORKFLOW_BINDING_SCHEMA:
            raise WorkflowBindingCompatibilityError(
                f"unsupported workflow binding schema {values['schema']!r}"
            )
        schema_digest = values["workflow_schema_sha256"]
        if len(schema_digest) != 64 or any(
            char not in "0123456789abcdef" for char in schema_digest.lower()
        ):
            raise WorkflowBindingCompatibilityError(
                "invalid workflow binding workflow_schema_sha256"
            )
        fingerprint = values["fingerprint"]
        digest = fingerprint.removeprefix(_SHA256_PREFIX)
        if not fingerprint.startswith(_SHA256_PREFIX) or len(digest) != 64:
            raise WorkflowBindingCompatibilityError("invalid workflow binding fingerprint")
        if any(char not in "0123456789abcdef" for char in digest.lower()):
            raise WorkflowBindingCompatibilityError("invalid workflow binding fingerprint")
        return cls(**values)


def _normalize(value: Any, *, path: str = "$") -> Any:
    """Normalize to strict JSON primitives and reject non-finite numbers."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowFingerprintError(f"non-finite number at {path}")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key)
            if normalized_key in result:
                raise WorkflowFingerprintError(f"duplicate mapping key at {path}: {normalized_key}")
            result[normalized_key] = _normalize(item, path=f"{path}.{normalized_key}")
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        normalized = [_normalize(item, path=f"{path}[]") for item in value]
        return sorted(normalized, key=canonical_json)
    if dataclasses.is_dataclass(value):
        return _normalize(dataclasses.asdict(value), path=path)
    raise WorkflowFingerprintError(f"unsupported value at {path}: {type(value).__name__}")


def _known_calc_keys() -> frozenset[str]:
    """Return the V2 calc parameter vocabulary from the single descriptor registry."""
    from .param_fields import v2_calc_keys

    return v2_calc_keys()


def _known_confgen_params(
    params: Mapping[str, Any], global_options: GlobalOptions
) -> dict[str, Any]:
    """Resolve confgen params through the shared resolver (single source of truth)."""
    return resolve_confgen_params(params, default_workers=global_options.max_parallel_jobs)


def _canonical_step(step: Mapping[str, Any], global_options: GlobalOptions) -> dict[str, Any]:
    step_type = str(step.get("type", "")).strip().lower()
    if step_type == "task":
        step_type = "calc"
    elif step_type == "gen":
        step_type = "confgen"
    name = str(step.get("name") or "").strip()
    params = step.get("params") or {}
    if not isinstance(params, Mapping):
        raise WorkflowFingerprintError(f"step {name or '<unnamed>'} params must be an object")
    if step_type == "calc":
        resolved = resolve_calc_step(dict(params), global_options)
        semantic = {"resolved": resolved.canonical_dict()}
        # Deprecated / no-op values must not influence the fingerprint, and an
        # explicitly-set scan default is equivalent to omitting it.
        resolved_dict = semantic["resolved"]
        resolved_dict.pop("resume_from_backups", None)
        for key, default in (
            ("scan_max_steps", DEFAULT_SCAN_MAX_STEPS),
            ("scan_fine_half_window", DEFAULT_SCAN_FINE_HALF_WINDOW),
        ):
            value = resolved_dict.get(key)
            if value is not None and float(value) == float(default):
                resolved_dict.pop(key)
        # chk_from_step is consumed by execution but is not part of the resolved
        # CalcStepParams; bind it explicitly so changing which checkpoint a step
        # reads changes the workflow fingerprint (resume safety).
        chk_from_step = str(params.get("chk_from_step") or "").strip()
        if chk_from_step:
            semantic["chk_from_step"] = chk_from_step
        extras = {key: value for key, value in params.items() if str(key) not in _known_calc_keys()}
        if extras:
            semantic["extra"] = _normalize(extras, path=f"$.steps[{name}].params.extra")
    else:
        semantic = {"resolved": _normalize(_known_confgen_params(params, global_options))}
        known = confgen_known_keys()
        extras = {key: value for key, value in params.items() if str(key) not in known}
        if extras:
            semantic["extra"] = _normalize(extras, path=f"$.steps[{name}].params.extra")
    return {
        "name": name,
        "type": step_type,
        "enabled": bool(step.get("enabled", True)),
        "params": semantic,
    }


def canonical_workflow_payload(plan: Any) -> dict[str, Any]:
    """Build the semantic, environment-independent payload for a workflow plan."""
    global_options = plan.typed_global
    steps = [_canonical_step(step, global_options) for step in plan.steps]
    explicit = any("inputs" in step for step in plan.steps)
    # resume_from_backups is deprecated and has no execution semantics; it must
    # not participate in the workflow fingerprint.
    global_payload = dataclasses.asdict(global_options)
    global_payload.pop("resume_from_backups", None)
    payload = {
        "workflow_schema": WORKFLOW_SCHEMA_VERSION,
        "workflow_schema_sha256": workflow_schema_sha256(),
        "global": _normalize(global_payload, path="$.global"),
        "steps": steps,
        "dag": {
            "mode": "explicit" if explicit else "linear",
            "predecessors": _normalize(plan.predecessors, path="$.dag.predecessors"),
            "execution_order": list(plan.execution_order),
            "terminal_steps": list(plan.terminal_steps),
            "step_dirnames": list(plan.step_dirnames),
        },
    }
    return cast(dict[str, Any], _normalize(payload))


def workflow_fingerprint(plan: Any) -> str:
    """Return the strict semantic digest for a prepared workflow plan."""
    return _SHA256_PREFIX + canonical_sha256(canonical_workflow_payload(plan))


def build_workflow_binding(plan: Any) -> WorkflowConfigBinding:
    """Create the versioned binding persisted in workflow state."""
    return WorkflowConfigBinding(
        schema=WORKFLOW_BINDING_SCHEMA,
        workflow_schema=WORKFLOW_SCHEMA_VERSION,
        workflow_schema_sha256=workflow_schema_sha256(),
        fingerprint=workflow_fingerprint(plan),
    )


def parse_workflow_binding(raw: Mapping[str, Any]) -> WorkflowConfigBinding:
    """Strictly parse a persisted binding."""
    return WorkflowConfigBinding.from_dict(raw)


__all__ = [
    "WORKFLOW_BINDING_SCHEMA",
    "WorkflowBindingCompatibilityError",
    "WorkflowFingerprintError",
    "WorkflowConfigBinding",
    "canonical_workflow_payload",
    "workflow_fingerprint",
    "build_workflow_binding",
    "parse_workflow_binding",
]


# ---------------------------------------------------------------------------
# V3 definition fingerprint (R3.4, RFC §16.A). Frozen; separate from the V2
# algorithm above, whose behaviour must not change.
# ---------------------------------------------------------------------------
WORKFLOW_SEMANTICS_VERSION = "confflow.workflow-semantics.v3"

#: RFC §16.A — resolved globals whose change alters the workflow's scientific
#: semantics. Bound into the definition fingerprint.
_SCIENTIFIC_GLOBAL_MEMBERS = (
    "charge",
    "multiplicity",
    "rmsd_threshold",
    "energy_window",
    "energy_tolerance",
    "noH",
    "freeze",
    "ts_bond_atoms",
    "ts_rescue_scan",
    "scan_coarse_step",
    "scan_fine_step",
    "scan_uphill_limit",
    "ts_bond_drift_threshold",
    "ts_rmsd_threshold",
    "auto_clean",
    "force_consistency",
    "keyword",
    "iprog",
    "itask",
    "blocks",
)

#: RFC §16.A — resolved globals that describe *how* the workflow runs, not what it
#: computes. Bound into the R4 execution fingerprint, never the definition one.
_EXECUTION_CLASS_GLOBAL_MEMBERS = frozenset(
    {
        "gaussian_path",
        "orca_path",
        "cores_per_task",
        "total_memory",
        "max_parallel_jobs",
        "orca_maxcore",
        "enable_dynamic_resources",
        "delete_work_dir",
        "stop_check_interval_seconds",
        "sandbox_root",
        "input_chk_dir",
        "allowed_executables",
        "gaussian_write_chk",
        "max_wall_time_seconds",
        "resume_from_backups",
    }
)

#: A resolved per-step parameter with one of these names is execution-class (it
#: changes how a step runs, not the science), so it is excluded from the definition
#: fingerprint. Frozen normatively in RFC §16.A ("Per-step execution-class params"):
#: the execution-class global members plus the confgen `workers` knob (the step-level
#: alias of the global `max_parallel_jobs`); every other per-step-only name stays
#: definition-semantic. This is the single authoritative constant derived from that
#: table — no second copy exists in production code.
_EXECUTION_CLASS_STEP_PARAMS = _EXECUTION_CLASS_GLOBAL_MEMBERS | {"workers"}


def build_workflow_definition_payload_v3(
    definition: CanonicalWorkflowDefinition,
    *,
    registry: Any = None,
) -> dict[str, Any]:
    """Build the canonical semantic payload the V3 definition fingerprint hashes.

    Only RUNNABLE-valid definitions qualify (invalid ones raise
    :class:`WorkflowFingerprintError`), so a cycle / unknown ref / fragment / bad
    checkpoint can never be hashed. Steps are sorted by the frozen id order key,
    each step's `inputs` are canonical-sorted (a relation, not an ordered list),
    and `label`/`annotations`/array order/schema metadata are excluded.
    """
    from .v3_graph import v3_id_order_key
    from .validation import ValidationProfile, resolve_step_semantic_params, validate_v3_definition

    errors = [
        diagnostic
        for diagnostic in validate_v3_definition(
            definition, profile=ValidationProfile.RUNNABLE, registry=registry
        )
        if diagnostic.is_error
    ]
    if errors:
        raise WorkflowFingerprintError(
            "cannot fingerprint an invalid V3 definition: " + "; ".join(str(d) for d in errors)
        )

    steps: list[dict[str, Any]] = []
    for step in sorted(definition.steps, key=lambda item: v3_id_order_key(item.id or "")):
        resolved = resolve_step_semantic_params(
            step, definition, profile=ValidationProfile.RUNNABLE
        )
        params = {
            key: value
            for key, value in resolved.items()
            if value is not None and key not in _EXECUTION_CLASS_STEP_PARAMS
        }
        record: dict[str, Any] = {
            "id": step.id,
            "type": step.type,
            "enabled": step.enabled,
            "params": params,
            "inputs": sorted(step.predecessors, key=v3_id_order_key),
        }
        if step.checkpoint_from is not None:
            record["checkpoint"] = {"from_step": step.checkpoint_from}
        if step.extensions:
            record["extensions"] = step.extensions
        steps.append(record)

    globals_payload: dict[str, Any] = {}
    for name in _SCIENTIFIC_GLOBAL_MEMBERS:
        value = getattr(definition.global_options, name, None)
        if value is not None:
            globals_payload[name] = value

    return {
        "semantics_version": WORKFLOW_SEMANTICS_VERSION,
        "global": globals_payload,
        "steps": steps,
    }


def workflow_definition_fingerprint_v3(
    definition: CanonicalWorkflowDefinition,
    *,
    registry: Any = None,
) -> str:
    """Return the frozen V3 definition fingerprint (``sha256:…``) of a definition."""
    payload = build_workflow_definition_payload_v3(definition, registry=registry)
    return _SHA256_PREFIX + canonical_sha256(payload)
