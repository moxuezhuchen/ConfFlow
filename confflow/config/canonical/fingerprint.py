"""Canonical workflow configuration bindings for safe resume."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .resolve import resolve_calc_step
from .schema import WORKFLOW_SCHEMA_VERSION, workflow_schema_sha256
from .serialization import canonical_json, canonical_sha256
from .types import GlobalOptions

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
    return frozenset(
        {
            "iprog",
            "itask",
            "keyword",
            "gaussian_path",
            "orca_path",
            "cores_per_task",
            "total_memory",
            "max_parallel_jobs",
            "charge",
            "multiplicity",
            "freeze",
            "auto_clean",
            "dedup_only",
            "keep_all_topos",
            "noH",
            "rmsd_threshold",
            "energy_window",
            "energy_tolerance",
            "clean_params",
            "clean_opts",
            "imag",
            "max_conformers",
            "enable_dynamic_resources",
            "resume_from_backups",
            "max_wall_time_seconds",
            "delete_work_dir",
            "sandbox_root",
            "input_chk_dir",
            "allowed_executables",
            "gaussian_write_chk",
            "stop_check_interval_seconds",
            "ts_bond_atoms",
            "ts_rescue_scan",
            "ts_bond_drift_threshold",
            "ts_rmsd_threshold",
            "scan_coarse_step",
            "scan_fine_step",
            "scan_uphill_limit",
            "scan_max_steps",
            "scan_fine_half_window",
            "ts_rescue_keep_scan_dirs",
            "ts_rescue_scan_backup",
            "blocks",
            "orca_maxcore",
            "maxcore",
            "gaussian_modredundant",
            "gaussian_link0",
            "ibkout",
            "chk_from_step",
        }
    )


def _known_confgen_params(
    params: Mapping[str, Any], global_options: GlobalOptions
) -> dict[str, Any]:
    """Mirror the execution adapter's aliases/defaults without importing it."""

    def first(*keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in params and params[key] is not None:
                return params[key]
        return default

    workers = first(
        "workers",
        "max_workers",
        "max_parallel_jobs",
        default=global_options.max_parallel_jobs,
    )
    try:
        workers = int(workers)
    except (TypeError, ValueError):
        workers = str(workers)
    return {
        "angle_step": first("angle_step", default=120),
        "bond_threshold": first("bond_multiplier", "bond_threshold", default=1.15),
        "clash_threshold": first("clash_threshold", default=0.65),
        "add_bond": first("add_bond", default=None),
        "del_bond": first("del_bond", default=None),
        "no_rotate": first("no_rotate", default=None),
        "force_rotate": first("force_rotate", default=None),
        "optimize": first("optimize", default=False),
        "chains": first("chains", "chain", default=None),
        "chain_steps": first("chain_steps", "steps", default=None),
        "chain_angles": first("chain_angles", "angles", default=None),
        "rotate_side": first("rotate_side", default="left"),
        "workers": workers,
    }


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
        extras = {key: value for key, value in params.items() if str(key) not in _known_calc_keys()}
        if extras:
            semantic["extra"] = _normalize(extras, path=f"$.steps[{name}].params.extra")
    else:
        semantic = {"resolved": _normalize(_known_confgen_params(params, global_options))}
        known = {
            "angle_step",
            "bond_multiplier",
            "bond_threshold",
            "clash_threshold",
            "add_bond",
            "del_bond",
            "no_rotate",
            "force_rotate",
            "optimize",
            "chains",
            "chain",
            "chain_steps",
            "steps",
            "chain_angles",
            "angles",
            "rotate_side",
            "workers",
            "max_workers",
            "max_parallel_jobs",
        }
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
    payload = {
        "workflow_schema": WORKFLOW_SCHEMA_VERSION,
        "workflow_schema_sha256": workflow_schema_sha256(),
        "global": _normalize(dataclasses.asdict(global_options), path="$.global"),
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
