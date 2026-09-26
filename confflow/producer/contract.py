#!/usr/bin/env python3

"""Producer-owned V4 configuration contract envelope.

The V4 contract (``confflow.configuration-contract.v4``) is the next wire
version after v1/v2 (parsed by JobDesk today) and v3 (the V3 workflow line).
It keeps the v1 validation response schema
(``confflow.configuration-validation.v1``) and advertises the V4 workflow
line: the real V4 JSON schema, the V4 editor manifest, the V4 recipe catalog,
and capability descriptors generated from the real execution and program
registries -- imported, never copied.

No legacy truth is published: ``result.xyz``, ``failed.xyz``,
``workflow_stats.json``, ``.workflow_state.json``, ``output_path``, and
``min_xyz`` never appear as authoritative outputs.
"""

from __future__ import annotations

import copy
import importlib
from typing import Any

from ..config.canonical.contract import CONFIGURATION_VALIDATION_SCHEMA
from ..domain.binding import (
    Cardinality,
    Pairing,
    PartialConsumption,
    PortKind,
    SelectorKind,
    SourceKind,
)
from ..domain.canonical import canonical_json_bytes, canonical_sha256
from ..domain.completion import CompletionMode, PartialOutputPolicy
from ..domain.resources import OnFailure
from ..execution.contracts import (
    ExecutionAdapterSpec,
    ExecutorCapability,
    ExecutorContract,
    PortSpec,
    ResultProfileSpec,
)
from ..execution.registry import ExecutionRegistry, default_registry
from ..workflow.v4.document import SCHEMA_ID
from ..workflow.v4.schema import (
    TRANSFORM_KINDS,
    build_workflow_json_schema,
    defaults_summary,
)
from .manifest import build_editor_manifest_v4
from .recipes import build_recipe_catalog_v4

#: Next wire version of the configuration contract: the V4 workflow line.
CONFIGURATION_CONTRACT_V4_SCHEMA = "confflow.configuration-contract.v4"

#: Content schema of the run-result manifest defined below.
RESULT_MANIFEST_SCHEMA = "confflow.run_result_manifest.v1"

#: Frozen analysis capability: the reaction-profile analysis contract.
ANALYSIS_REACTION_PROFILE_CAPABILITY = "reaction_profile"
ANALYSIS_REACTION_PROFILE_CONTRACT = "confflow.contract.analysis.reaction_profile.v1"

#: Scientific override keys with defined precedence semantics in V4. The
#: allowlist is enforced by ``ScientificDefinition``; the drift test in
#: ``tests/v4/test_v46_producer_contract.py`` proves this list still matches
#: the domain rule, so the copy can never drift silently.
SCIENTIFIC_OVERRIDE_KEYS: tuple[str, ...] = ("charge", "multiplicity", "freeze")


def _analysis_capabilities() -> tuple[list[dict[str, str]], str]:
    """Return analysis capabilities plus their provenance.

    The capabilities are imported from ``confflow.analysis.registry`` when
    that module exists; otherwise the frozen reaction-profile strings below
    are declared. The drift test fails loudly the moment the registry
    appears with different strings.
    """
    try:
        analysis_registry: Any = importlib.import_module("confflow.analysis.registry")
        capabilities = analysis_registry.capabilities()
        return (
            [
                {
                    "capability": str(item["capability"]),
                    "contract_version": str(item["contract_version"]),
                }
                for item in capabilities
            ],
            "registry",
        )
    except Exception:  # noqa: BLE001 - absence of the registry is the normal path
        return (
            [
                {
                    "capability": ANALYSIS_REACTION_PROFILE_CAPABILITY,
                    "contract_version": ANALYSIS_REACTION_PROFILE_CONTRACT,
                }
            ],
            "frozen",
        )


def _port_dict(port: PortSpec) -> dict[str, Any]:
    """Return the wire form of one registry port contract."""
    return {
        "name": port.name,
        "kind": port.kind.value,
        "cardinality": port.cardinality.value,
        "pairing": port.pairing.value,
        "roles": list(port.roles),
        "required": port.is_required,
        "description": port.description,
    }


def _executor_dict(contract: ExecutorContract) -> dict[str, Any]:
    """Return the wire form of one executor contract from the registry."""
    return {
        "capability": contract.capability.value,
        "contract_version": contract.contract_version,
        "requires_adapter": contract.requires_adapter,
        "stochastic": contract.stochastic,
        "description": contract.description,
        "input_ports": [_port_dict(port) for port in contract.input_ports],
        "output_ports": [_port_dict(port) for port in contract.output_ports],
        "passthrough_ports": dict(contract.passthrough_ports),
    }


def _adapter_dict(adapter: ExecutionAdapterSpec) -> dict[str, Any]:
    """Return the wire form of one execution adapter from the registry."""
    return {
        "name": adapter.name,
        "contract_version": adapter.contract_version,
        "capability": adapter.capability.value,
        "description": adapter.description,
        "input_ports": [_port_dict(port) for port in adapter.input_ports],
    }


def _profile_dict(profile: ResultProfileSpec) -> dict[str, Any]:
    """Return the wire form of one result profile from the registry."""
    return {
        "name": profile.name,
        "contract_version": profile.contract_version,
        "supported_checks": list(profile.supported_checks),
        "provides_structures": profile.provides_structures,
        "provides_results": profile.provides_results,
        "provides_artifacts": profile.provides_artifacts,
        "description": profile.description,
    }


def _program_descriptors() -> list[dict[str, Any]]:
    """Return program descriptors resolved through the real program registry."""
    from ..execution.native import ProgramName
    from ..programs.registry import PROGRAM_ALIASES, get_program_adapter

    descriptors: list[dict[str, Any]] = []
    for program in ProgramName:
        adapter = get_program_adapter(program.value)
        aliases = sorted(
            alias for alias, canonical in PROGRAM_ALIASES.items() if canonical == program.value
        )
        descriptors.append(
            {
                "program": program.value,
                "aliases": aliases,
                "adapter_version": adapter.adapter_version,
                "parser_version": adapter.parser_version,
                "input_extension": adapter.input_extension,
                "log_extension": adapter.log_extension,
                "default_executable": adapter.default_executable,
            }
        )
    return sorted(descriptors, key=lambda item: str(item["program"]))


def _allowed_pairings_by_kind() -> dict[str, list[str]]:
    """Probe the pairing rule from the single source: ``PortSpec`` itself.

    Each pairing is accepted for a kind exactly when ``PortSpec`` constructs
    without raising, so the published rule can never disagree with the
    validation the compiler enforces.
    """
    table: dict[str, list[str]] = {}
    for kind in PortKind:
        allowed: list[str] = []
        for pairing in Pairing:
            try:
                PortSpec(
                    name="probe",
                    kind=kind,
                    cardinality=Cardinality.ONE,
                    pairing=pairing,
                )
            except Exception:  # noqa: BLE001 - rejection is the signal
                continue
            allowed.append(pairing.value)
        table[kind.value] = allowed
    return table


def _ports_section(registry: ExecutionRegistry) -> dict[str, Any]:
    """Build ports/pairing/cardinality descriptors from domain + registry."""
    capabilities = sorted(registry.capability_names)
    executors = [
        _executor_dict(registry.executor(ExecutorCapability(capability)))
        for capability in capabilities
    ]
    adapters = [_adapter_dict(registry.adapter(name)) for name in sorted(registry.adapter_names)]
    return {
        "vocabulary": {
            name: [item.value for item in enum]
            for name, enum in (
                ("port_kinds", PortKind),
                ("source_kinds", SourceKind),
                ("cardinalities", Cardinality),
                ("pairings", Pairing),
                ("partial_consumption", PartialConsumption),
                ("selector_kinds", SelectorKind),
            )
        },
        "allowed_pairings_by_kind": _allowed_pairings_by_kind(),
        "port_index": [
            {
                "owner": f"executor:{entry['capability']}",
                "input_ports": [port["name"] for port in entry["input_ports"]],
                "output_ports": [port["name"] for port in entry["output_ports"]],
            }
            for entry in executors
        ]
        + [
            {
                "owner": f"adapter:{entry['name']}",
                "input_ports": [port["name"] for port in entry["input_ports"]],
                "output_ports": [],
            }
            for entry in adapters
        ],
    }


def _resources_section() -> dict[str, Any]:
    """Build resource-field descriptors from domain models and defaults."""
    defaults = defaults_summary()
    return {
        "fields": [
            {
                "name": "cores_per_item",
                "path": "resources.cores_per_item",
                "value_type": "integer",
                "constraints": ">= 1",
                "default": defaults["resources"]["cores_per_item"],
                "digest_axis": "scientific",
                "description": "CPU cores reserved per work item; changes native input.",
            },
            {
                "name": "memory_per_item",
                "path": "resources.memory_per_item",
                "value_type": "string",
                "constraints": "binary suffixes KB/KiB/MB/MiB/GB/GiB/TB/TiB",
                "default": defaults["resources"]["memory_per_item"],
                "digest_axis": "scientific",
                "description": "Memory reserved per work item; changes native input.",
            },
            {
                "name": "max_parallel_items",
                "path": "scheduler.max_parallel_items",
                "value_type": "integer",
                "constraints": ">= 1",
                "default": defaults["scheduler"]["max_parallel_items"],
                "digest_axis": "operational",
                "description": "Scheduler-only width; never changes reuse identity.",
            },
            {
                "name": "on_failure",
                "path": "scheduler.on_failure",
                "value_type": "string",
                "constraints": f"one of {[item.value for item in OnFailure]}",
                "default": OnFailure.CONTINUE.value,
                "digest_axis": "operational",
                "description": "Scheduler-only failure behavior; digest-inert.",
            },
        ],
    }


def _policy_section() -> dict[str, Any]:
    """Build completion/scheduler policy descriptors from domain enums."""
    defaults = defaults_summary()
    return {
        "completion": {
            "modes": [item.value for item in CompletionMode],
            "partial_output_policies": [item.value for item in PartialOutputPolicy],
            "defaults": defaults["completion"],
            "description": (
                "Acceptance policy per step; partial consumption needs an "
                "explicit partial_consumption declaration on the binding."
            ),
        },
        "scheduler": {
            "on_failure": [item.value for item in OnFailure],
            "defaults": defaults["scheduler"],
            "description": "Operational policy only; excluded from every digest.",
        },
    }


def _native_section() -> dict[str, Any]:
    """Build native escape-hatch descriptors from the V4 block shapes."""
    return {
        "blocks": [
            {
                "block": "calculation.native",
                "role": "verbatim_escape_hatch",
                "description": (
                    "Program-native definition (keyword, irc/goat/neb sections, "
                    "atom mapping). Rendered verbatim by the program adapter."
                ),
            },
            {
                "block": "confgen.native",
                "role": "verbatim_escape_hatch",
                "description": "Conformer-generation native definition.",
            },
            {
                "block": "transform.native",
                "role": "verbatim_escape_hatch",
                "description": "Structure-transform native options.",
            },
            {
                "block": "analysis.native",
                "role": "verbatim_escape_hatch",
                "description": "Analysis native definition (method, options).",
            },
        ],
        "overrides": {
            "allowlist": list(SCIENTIFIC_OVERRIDE_KEYS),
            "description": (
                "The only scientific overrides with defined precedence "
                "semantics; any other override key fails closed."
            ),
        },
        "parameter_mappings": [
            {
                "block": "calculation.check_params",
                "description": "Per-check parameters keyed by declared check name.",
            },
            {
                "block": "calculation.recovery.params",
                "description": "Parameters of the declared recovery profile.",
            },
        ],
    }


def _remote_section() -> dict[str, Any]:
    """Return the remote capability ids imported from the frozen envelope."""
    from ..remote.envelope import HANDOFF_SCHEMA_V2, RESULT_SCHEMA_V2

    return {"handoff": HANDOFF_SCHEMA_V2, "result": RESULT_SCHEMA_V2}


def run_result_json_schema() -> dict[str, Any]:
    """Return the JSON Schema of the run-result manifest.

    The manifest reports run identity, per-step statuses with semantic
    digests, analysis references, and durable artifacts addressed by portable
    run-relative locators plus checksums. Absolute paths, owner tokens, and
    sqlite internals are not part of the shape.
    """
    step_schema: dict[str, Any] = {
        "type": "object",
        "required": ["id", "status", "digest", "counts", "diagnostics"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "status": {"enum": ["completed", "partial", "failed", "cancelled"]},
            "digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "counts": {
                "type": "object",
                "required": ["completed", "failed", "cancelled"],
                "additionalProperties": False,
                "properties": {
                    "completed": {"type": "integer", "minimum": 0},
                    "failed": {"type": "integer", "minimum": 0},
                    "cancelled": {"type": "integer", "minimum": 0},
                },
            },
            "diagnostics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["code", "severity", "message"],
                    "additionalProperties": False,
                    "properties": {
                        "code": {"type": "string", "minLength": 1},
                        "severity": {"type": "string", "minLength": 1},
                        "step_id": {"type": ["string", "null"]},
                        "field_path": {"type": ["string", "null"]},
                        "message": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ConfFlow run result manifest",
        "type": "object",
        "required": [
            "content_schema",
            "run_id",
            "status",
            "definition_digest",
            "provenance",
            "steps",
            "analyses",
            "artifacts",
        ],
        "additionalProperties": False,
        "properties": {
            "content_schema": {"const": RESULT_MANIFEST_SCHEMA},
            "run_id": {"type": "string", "minLength": 1},
            "status": {"enum": ["completed", "partial", "failed", "cancelled"]},
            "definition_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "provenance": {
                "type": "object",
                "required": ["package", "version"],
                "additionalProperties": False,
                "properties": {
                    "package": {"type": "string", "minLength": 1},
                    "version": {"type": "string", "minLength": 1},
                    "commit": {"type": ["string", "null"]},
                    "dirty": {"type": ["boolean", "null"]},
                },
            },
            "steps": {"type": "array", "items": step_schema},
            "analyses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["capability", "step_id"],
                    "additionalProperties": False,
                    "properties": {
                        "capability": {"type": "string", "minLength": 1},
                        "step_id": {"type": "string", "minLength": 1},
                    },
                },
            },
            "artifacts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["role", "checksum", "locator"],
                    "additionalProperties": False,
                    "properties": {
                        "role": {"type": "string", "minLength": 1},
                        "checksum": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                        "locator": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
    }


def run_result_schema_sha256() -> str:
    """Return the canonical SHA-256 of the run-result JSON Schema."""
    return canonical_sha256(run_result_json_schema())


def build_run_result_manifest(
    *,
    run_id: str,
    status: str,
    definition_digest: str,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
    steps: list[dict[str, Any]] | None = None,
    analyses: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one run-result manifest instance.

    Raises
    ------
    ValueError
        Raised when *run_id*, *status*, or *definition_digest* is malformed.
    """
    if not run_id or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    if status not in ("completed", "partial", "failed", "cancelled"):
        raise ValueError(f"unknown run status {status!r}")
    if not definition_digest.startswith("sha256:"):
        raise ValueError("definition_digest must be a sha256: digest")
    return {
        "content_schema": RESULT_MANIFEST_SCHEMA,
        "run_id": run_id,
        "status": status,
        "definition_digest": definition_digest,
        "provenance": {
            "package": "confflow",
            "version": producer_version,
            "commit": producer_commit,
            "dirty": producer_dirty,
        },
        "steps": copy.deepcopy(steps or []),
        "analyses": copy.deepcopy(analyses or []),
        "artifacts": copy.deepcopy(artifacts or []),
    }


def build_configuration_contract_v4(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
    registry: ExecutionRegistry | None = None,
) -> dict[str, Any]:
    """Build a deterministic V4 contract without runtime or workload probing.

    Parameters
    ----------
    producer_version :
        Producer package version recorded on the envelope.
    producer_commit :
        Source commit recorded on the envelope, if known.
    producer_dirty :
        Whether the producer tree was dirty, if known.
    registry :
        Execution registry every capability descriptor is generated from.
        Defaults to the shared default registry.
    """
    active = registry if registry is not None else default_registry()
    workflow_schema = build_workflow_json_schema(active)
    manifest = build_editor_manifest_v4(registry=active)
    catalog = build_recipe_catalog_v4()
    analysis_capabilities, analysis_source = _analysis_capabilities()
    remote = _remote_section()
    envelope: dict[str, Any] = {
        "content_schema": CONFIGURATION_CONTRACT_V4_SCHEMA,
        "producer": {
            "package": "confflow",
            "version": producer_version,
            "commit": producer_commit,
            "dirty": producer_dirty,
        },
        "workflow_schema_id": SCHEMA_ID,
        "workflow_schema": workflow_schema,
        "workflow_schema_sha256": canonical_sha256(workflow_schema),
        "editor_manifest": manifest,
        "editor_manifest_sha256": canonical_sha256(manifest),
        "recipe_catalog": catalog,
        "recipe_catalog_sha256": canonical_sha256(catalog),
        "executors": [
            _executor_dict(active.executor(ExecutorCapability(capability)))
            for capability in sorted(active.capability_names)
        ],
        "execution_adapters": [
            _adapter_dict(active.adapter(name)) for name in sorted(active.adapter_names)
        ],
        "result_profiles": [
            _profile_dict(active.profile(name)) for name in sorted(active.profile_names)
        ],
        "scientific_checks": [
            {
                "name": name,
                "contract_version": active.check(name).contract_version,
                "description": active.check(name).description,
            }
            for name in sorted(active.check_names)
        ],
        "recovery_profiles": [
            {
                "name": name,
                "contract_version": active.recovery(name).contract_version,
                "supported_capabilities": sorted(
                    item.value for item in active.recovery(name).supported_capabilities
                ),
                "description": active.recovery(name).description,
            }
            for name in sorted(active.recovery_names)
        ],
        "programs": _program_descriptors(),
        "ports": _ports_section(active),
        "resources": _resources_section(),
        "policies": _policy_section(),
        "native_escape_hatches": _native_section(),
        "analysis_capabilities": {
            "capabilities": analysis_capabilities,
            "source": analysis_source,
        },
        "result_schema": run_result_json_schema(),
        "result_schema_sha256": run_result_schema_sha256(),
        "validation_response_schema": CONFIGURATION_VALIDATION_SCHEMA,
        "remote_capability": remote,
        "transform_kinds": list(TRANSFORM_KINDS),
    }
    envelope["contract_digest"] = canonical_sha256(
        {key: value for key, value in envelope.items() if key != "contract_digest"}
    )
    return envelope


def contract_digest_of(envelope: dict[str, Any]) -> str:
    """Recompute the contract digest of an envelope (minus the digest)."""
    return canonical_sha256(
        {key: value for key, value in envelope.items() if key != "contract_digest"}
    )


def contract_canonical_json(envelope: dict[str, Any]) -> str:
    """Return the deterministic canonical JSON bytes of an envelope, decoded."""
    return canonical_json_bytes(envelope).decode("utf-8")


def generate_contract_bytes(
    *,
    producer_version: str,
    producer_commit: str | None = None,
    producer_dirty: bool | None = None,
) -> bytes:
    """Return the canonical contract JSON bytes for downstream consumers.

    Parameters
    ----------
    producer_version :
        Producer package version recorded on the envelope.
    producer_commit :
        Source commit recorded on the envelope, if known.
    producer_dirty :
        Whether the producer tree was dirty, if known.

    Returns
    -------
    bytes
        JCS canonical JSON encoding of the V4 contract envelope, including
        its ``contract_digest``. Downstream cross-repo E2E parses these bytes
        with plain JSON, recomputes the digest, and compiles recipes from
        the embedded catalog.
    """
    envelope = build_configuration_contract_v4(
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
    )
    return canonical_json_bytes(envelope)


__all__ = [
    "ANALYSIS_REACTION_PROFILE_CAPABILITY",
    "ANALYSIS_REACTION_PROFILE_CONTRACT",
    "CONFIGURATION_CONTRACT_V4_SCHEMA",
    "CONFIGURATION_VALIDATION_SCHEMA",
    "RESULT_MANIFEST_SCHEMA",
    "SCIENTIFIC_OVERRIDE_KEYS",
    "build_configuration_contract_v4",
    "build_run_result_manifest",
    "contract_canonical_json",
    "contract_digest_of",
    "generate_contract_bytes",
    "run_result_json_schema",
    "run_result_schema_sha256",
]
