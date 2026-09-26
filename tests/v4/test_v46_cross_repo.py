#!/usr/bin/env python3

"""V4-6 cross-repository contract roundtrip + fake TSPES chain.

Scope: NEW tests only, programming against the FROZEN V4 wire shapes.  The
real producer side (``confflow.producer.*``) and the real analysis side
(``confflow.analysis.*``) have NOT landed in this repo yet, and the real
JobDesk V4 parser (``jobdesk_v2.application.editor.contract.v4``) has NOT
landed in the JobDesk repo yet.  Every such seam uses the prefer-real
pattern: try the real import first, fall back to a clearly-marked
``*_DOUBLE`` with a LOUD comment, and record the choice in
``TestRealVsDoubleInventory`` so the final report can state real-vs-double
per test honestly.

LOUD DOUBLE NOTICE (V4-6, remove as siblings land):
  - ``build_contract_envelope`` / ``validate_workflow_bytes`` below are
    PRODUCER DOUBLES.  They stand in for ``confflow.producer.contract`` and
    ``confflow.producer.validation`` (workstream B/C).  The validation double
    is STRICT: it runs the REAL ``confflow.workflow.v4.compile_workflow``
    inside and only the envelope mapping (frozen-shape dict) is doubled.
  - ``JobdeskContractDouble`` is a CONSUMER DOUBLE standing in for the
    JobDesk-side V4 parser.  It is pure stdlib and MUST NEVER import
    ``confflow`` (enforced by ``tests/v4/test_v46_debt.py``).
  - ``run_fake_tspes_chain`` / ``stub_reaction_analysis`` are FAKES for the
    native execution + ``reaction_profile`` analysis capability.  Counts
    (20 TS -> 40 endpoints -> 20 ReactionGroups) are exact; energies are
    deterministic placeholders.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

from confflow.domain.units import Unit

# ---------------------------------------------------------------------------
# Prefer-real seam: ConfFlow producer modules (NOT LANDED as of V4-6).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - real side has not landed; exercised when it does
    from confflow.producer.contract import (  # type: ignore[import-not-found]
        build_configuration_contract as _real_build_contract,
    )
    from confflow.producer.validation import (  # type: ignore[import-not-found]
        validate_workflow_bytes as _real_validate_workflow_bytes,
    )

    _REAL_PRODUCER_AVAILABLE = True
except ImportError:
    _real_build_contract = None
    _real_validate_workflow_bytes = None
    _REAL_PRODUCER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Prefer-real seam: ConfFlow analysis modules (NOT LANDED as of V4-6).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - real side has not landed; exercised when it does
    from confflow.analysis import (  # type: ignore[import-not-found]
        compute_reaction_profile as _real_compute_reaction_profile,
    )

    _REAL_ANALYSIS_AVAILABLE = True
except ImportError:
    _real_compute_reaction_profile = None
    _REAL_ANALYSIS_AVAILABLE = False

# Real V4 compiler: LANDED, always preferred for workflow validation.
from confflow.workflow.v4 import compile_workflow

USING_PRODUCER_CONTRACT_DOUBLE = _real_build_contract is None
USING_PRODUCER_VALIDATION_DOUBLE = _real_validate_workflow_bytes is None
USING_ANALYSIS_STUB = _real_compute_reaction_profile is None

# ---------------------------------------------------------------------------
# Frozen wire-shape constants (program against these literally).
# ---------------------------------------------------------------------------
CONTRACT_SCHEMA_V4 = "confflow.configuration-contract.v4"
WORKFLOW_SCHEMA_V4 = "confflow.workflow.v4"
VALIDATION_SCHEMA_V1 = "confflow.configuration-validation.v1"
RESULT_MANIFEST_SCHEMA_V1 = "confflow.run_result_manifest.v1"
REACTION_PROFILE_CAPABILITY = "confflow.contract.analysis.reaction_profile.v1"
HANDOFF_CAPABILITY = "confflow.control.worker-handoff.v2"
RESULT_CAPABILITY = "confflow.control.worker-result.v2"

# Every artifact digest below is "sha256:" + hex(sha256(canonical JSON)).


class V46ContractError(ValueError):
    """Structured V4-6 failure: never a silent fallback, always a ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Canonical JSON double.
#
# LOUD DOUBLE NOTICE: mirrors the documented producer algorithm
# (``confflow/config/canonical/serialization.py`` as quoted by JobDesk's
# ``canonical`` module: sort_keys, no whitespace, ensure_ascii=False,
# allow_nan=False).  When ``confflow.producer`` lands with its own digest
# helper, ``build_contract_envelope`` MUST prefer it over ``_canonical_sha256``.
# ---------------------------------------------------------------------------
def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


# ---------------------------------------------------------------------------
# Producer contract envelope DOUBLE.
#
# LOUD DOUBLE NOTICE: stands in for ``confflow.producer.contract`` (not
# landed).  Digest convention used on BOTH sides of this file: every artifact
# digest covers the canonical JSON of the artifact alone, and
# ``contract_digest`` covers the canonical JSON of the envelope with the
# ``contract_digest`` member itself removed.  The convention is arbitrary but
# applied identically at produce and verify time; the real producer owns the
# final convention and these tests must adopt it when it lands.
# ---------------------------------------------------------------------------
def _frozen_workflow_schema() -> dict[str, Any]:
    return {
        "schema": WORKFLOW_SCHEMA_V4,
        "version": "4.6",
        "step_kinds": ["calculation", "analysis"],
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
    }


def _frozen_editor_manifest() -> dict[str, Any]:
    return {
        "fields": [
            {"id": "calculation.native.keyword", "type": "string"},
            {"id": "calculation.resources.cores_per_item", "type": "integer"},
            {"id": "scheduler.max_parallel_items", "type": "integer"},
        ],
        "workflow_schema_version": WORKFLOW_SCHEMA_V4,
    }


def _frozen_recipe_catalog() -> dict[str, Any]:
    return {
        "recipes": [
            {
                "id": "tspes-default",
                "required_fields": ["calculation.native.keyword"],
                "exposed_fields": [
                    "calculation.native.keyword",
                    "calculation.resources.cores_per_item",
                ],
                "defaults": {"calculation.native.keyword": "B3LYP D3BJ"},
            }
        ]
    }


def _descriptor_list(names: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"name": name, "contract_version": version} for name, version in names]


def build_contract_envelope_double(
    *,
    producer_version: str = "4.6.0-test",
    contract_schema: str = CONTRACT_SCHEMA_V4,
) -> dict[str, Any]:
    """DOUBLE for ``confflow.producer.contract.build_configuration_contract``."""
    workflow_schema = _frozen_workflow_schema()
    editor_manifest = _frozen_editor_manifest()
    recipe_catalog = _frozen_recipe_catalog()
    result_schema = {
        "content_schema": RESULT_MANIFEST_SCHEMA_V1,
        "required": ["run_id", "status", "steps", "analyses", "artifacts"],
    }
    envelope: dict[str, Any] = {
        "content_schema": contract_schema,
        "producer": {
            "package": "confflow",
            "version": producer_version,
            "commit": "double-no-vcs",
            "dirty": True,
        },
        "workflow_schema": workflow_schema,
        "workflow_schema_sha256": _canonical_sha256(workflow_schema),
        "editor_manifest": editor_manifest,
        "editor_manifest_sha256": _canonical_sha256(editor_manifest),
        "recipe_catalog": recipe_catalog,
        "recipe_catalog_sha256": _canonical_sha256(recipe_catalog),
        "executors": _descriptor_list([("calculation", "confflow.contract.executor.v1")]),
        "programs": _descriptor_list([("orca", "confflow.contract.program.orca.v1")]),
        "execution_adapters": _descriptor_list(
            [("standard", "confflow.contract.adapter.standard.v1")]
        ),
        "result_profiles": _descriptor_list(
            [
                ("standard", "confflow.contract.profile.standard.v1"),
                ("path_endpoints", "confflow.contract.profile.path_endpoints.v1"),
            ]
        ),
        "scientific_checks": _descriptor_list(
            [("normal_termination", "confflow.contract.check.v1")]
        ),
        "recovery_profiles": _descriptor_list([("none", "confflow.contract.recovery.v1")]),
        "ports": {"input": ["structure"], "output": ["structure", "energy"]},
        "pairing": "by_identity",
        "cardinality": "many",
        "resources": {"cores_per_item": "integer", "memory_per_item": "string"},
        "completion": {"modes": ["require_all", "allow_partial"]},
        "scheduler": {"max_parallel_items": "integer"},
        "native_escape_hatches": ["keyword"],
        "analysis_capabilities": [
            {"name": "reaction_profile", "contract_version": REACTION_PROFILE_CAPABILITY}
        ],
        "result_schema": result_schema,
        "result_schema_sha256": _canonical_sha256(result_schema),
        "validation_response_schema": VALIDATION_SCHEMA_V1,
        "remote_capability": {"handoff": HANDOFF_CAPABILITY, "result": RESULT_CAPABILITY},
    }
    unsigned = {key: value for key, value in envelope.items() if key != "contract_digest"}
    envelope["contract_digest"] = _canonical_sha256(unsigned)
    return envelope


def build_contract_envelope(**kwargs: Any) -> dict[str, Any]:
    """Prefer the real producer builder; fall back to the loud double."""
    if _real_build_contract is not None:  # pragma: no cover - not landed
        return _real_build_contract(**kwargs)
    return build_contract_envelope_double(**kwargs)


def serialize_contract(envelope: dict[str, Any]) -> bytes:
    return _canonical_json_bytes(envelope)


def _require_v4_envelope(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise V46ContractError("schema_mismatch", "contract top level must be an object")
    schema = raw.get("content_schema")
    if schema in ("confflow.configuration-contract.v1", "confflow.configuration-contract.v2"):
        raise V46ContractError(
            "old_contract_no_v4",
            f"producer contract {schema!r} predates V4: no V4 descriptors to edit against",
        )
    if schema != CONTRACT_SCHEMA_V4:
        raise V46ContractError("schema_mismatch", f"unsupported contract content_schema {schema!r}")
    return raw


def _verify_artifact(envelope: dict[str, Any], name: str) -> None:
    artifact = envelope.get(name)
    claimed = envelope.get(f"{name}_sha256")
    if not isinstance(artifact, dict):
        raise V46ContractError("schema_mismatch", f"contract member {name!r} must be an object")
    if not isinstance(claimed, str) or not claimed:
        raise V46ContractError("bad_digest", f"contract publishes no digest for {name!r}")
    if _canonical_sha256(artifact) != claimed.strip().lower():
        raise V46ContractError(
            "bad_digest",
            f"{name!r} does not match its published digest: content changed after signing",
        )


def verify_contract_envelope(raw: Any) -> dict[str, Any]:
    """Re-verify every digest of a decoded envelope; structured failure, never None."""
    envelope = _require_v4_envelope(raw)
    for artifact in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema"):
        _verify_artifact(envelope, artifact)
    claimed = envelope.get("contract_digest")
    if not isinstance(claimed, str) or not claimed:
        raise V46ContractError("bad_digest", "contract publishes no contract_digest")
    unsigned = {key: value for key, value in envelope.items() if key != "contract_digest"}
    if _canonical_sha256(unsigned) != claimed.strip().lower():
        raise V46ContractError("bad_digest", "contract envelope does not match its contract_digest")
    return envelope


def parse_contract_bytes(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise V46ContractError("schema_mismatch", f"contract bytes are not UTF-8: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise V46ContractError("schema_mismatch", f"contract bytes are not JSON: {exc}") from exc
    return verify_contract_envelope(raw)


# ---------------------------------------------------------------------------
# JobDesk-side simulation DOUBLE (pure stdlib; MUST NOT import confflow --
# enforced by tests/v4/test_v46_debt.py).
#
# LOUD DOUBLE NOTICE: stands in for the JobDesk V4 consumer
# (``jobdesk_v2.application.editor.contract.v4``, not landed).  It speaks only
# the frozen wire shapes above.
# ---------------------------------------------------------------------------
class JobdeskContractDouble:
    """Simulated JobDesk consumer: parse/verify/build-TSPES-workflow/serialize."""

    @staticmethod
    def parse(contract_bytes: bytes) -> dict[str, Any]:
        """Parse + digest-verify raw producer bytes into a trusted mapping."""
        return parse_contract_bytes(contract_bytes)

    @staticmethod
    def recipe(contract: dict[str, Any], recipe_id: str) -> dict[str, Any]:
        catalog = contract["recipe_catalog"]
        if not isinstance(catalog, dict) or not isinstance(catalog.get("recipes"), list):
            raise V46ContractError("recipe_mismatch", "recipe catalog is malformed")
        for recipe in catalog["recipes"]:
            if isinstance(recipe, dict) and recipe.get("id") == recipe_id:
                manifest = contract["editor_manifest"]
                known = (
                    {field["id"] for field in manifest.get("fields", [])}
                    if isinstance(manifest, dict)
                    else set()
                )
                for field_id in (*recipe.get("required_fields", []),):
                    if field_id not in known:
                        raise V46ContractError(
                            "recipe_mismatch",
                            f"recipe {recipe_id!r} requires unknown field {field_id!r}",
                        )
                return recipe
        raise V46ContractError(
            "recipe_mismatch", f"recipe {recipe_id!r} is not in the producer catalog"
        )

    @staticmethod
    def build_tspes_workflow(
        contract: dict[str, Any], *, recipe_id: str = "tspes-default"
    ) -> dict[str, Any]:
        """Build a V4 TSPES workflow document from the contract + one recipe."""
        JobdeskContractDouble.recipe(contract, recipe_id)  # structured failure first
        # Plain-dict V4 document: same shape the real builder helpers emit.
        bindings = {"structure": {"source": {"run": "structures"}}}

        def _calc(step_id: str, profile: str, keyword: str, parallel: int = 8) -> dict[str, Any]:
            return {
                "id": step_id,
                "executor": "calculation",
                "bindings": dict(bindings),
                "calculation": {
                    "program": "orca",
                    "role": "tspes",
                    "execution_adapter": "standard",
                    "result_profile": profile,
                    "native": {"keyword": keyword},
                    "checks": ["normal_termination"],
                    "recovery": {"profile": "none"},
                },
                "resources": {"cores_per_item": 1, "memory_per_item": "1GB"},
                "scheduler": {"max_parallel_items": parallel},
                "execution": {"binding_id": "jobdesk", "executable": "orca"},
            }

        return {
            "schema": WORKFLOW_SCHEMA_V4,
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "steps": [
                _calc("s_irc", "path_endpoints", "IRC B3LYP D3BJ"),
                _calc("s_opt", "standard", "B3LYP Opt"),
                _calc("s_freq", "standard", "B3LYP Freq"),
                _calc("s_sp", "standard", "B3LYP SP"),
                {
                    "id": "s_analysis",
                    "executor": "analysis",
                    "bindings": dict(bindings),
                    "analysis": {"checks": [], "native": {}},
                },
            ],
        }

    @staticmethod
    def edit_workflow_field(
        workflow: dict[str, Any], step_id: str, field_path: str, value: Any
    ) -> dict[str, Any]:
        """Edit one representative field (recipe-style) and return a new mapping."""
        edited = copy.deepcopy(workflow)
        for step in edited["steps"]:
            if step.get("id") == step_id:
                node: dict[str, Any] = step
                parts = field_path.split(".")
                for part in parts[:-1]:
                    child = node.get(part)
                    if not isinstance(child, dict):
                        raise V46ContractError(
                            "recipe_mismatch",
                            f"field {field_path!r} does not exist on step {step_id!r}",
                        )
                    node = child
                if parts[-1] not in node:
                    raise V46ContractError(
                        "recipe_mismatch",
                        f"field {field_path!r} does not exist on step {step_id!r}",
                    )
                node[parts[-1]] = value
                return edited
        raise V46ContractError("recipe_mismatch", f"unknown step {step_id!r}")

    @staticmethod
    def serialize_workflow(workflow: dict[str, Any]) -> bytes:
        return _canonical_json_bytes(workflow)


# ---------------------------------------------------------------------------
# Producer validation of the SAME bytes.
#
# Prefer ``confflow.producer.validation.validate_workflow_bytes`` when it
# lands; until then the STRICT DOUBLE below runs the REAL V4 compiler and only
# the frozen response envelope is doubled.
# ---------------------------------------------------------------------------
def _double_validate_workflow_bytes(workflow_bytes: bytes) -> dict[str, Any]:
    """STRICT DOUBLE: real ``compile_workflow`` inside, frozen envelope outside."""
    try:
        raw = json.loads(workflow_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "schema": VALIDATION_SCHEMA_V1,
            "valid": False,
            "diagnostics": [
                {
                    "code": "workflow_not_json",
                    "severity": "error",
                    "step_id": None,
                    "field_path": None,
                    "message": f"workflow bytes are not JSON: {exc}",
                }
            ],
        }
    if not isinstance(raw, dict):
        return {
            "schema": VALIDATION_SCHEMA_V1,
            "valid": False,
            "diagnostics": [
                {
                    "code": "workflow_not_object",
                    "severity": "error",
                    "step_id": None,
                    "field_path": None,
                    "message": "workflow top level must be an object",
                }
            ],
        }
    compiled = compile_workflow(raw)
    diagnostics = [
        {
            "code": item.code,
            "severity": str(getattr(item.severity, "value", item.severity)).lower(),
            "step_id": item.step_id,
            "field_path": item.field_path,
            "message": item.message,
        }
        for item in compiled.diagnostics
    ]
    return {
        "schema": VALIDATION_SCHEMA_V1,
        "valid": compiled.ok,
        "diagnostics": diagnostics,
    }


def validate_workflow_bytes(workflow_bytes: bytes) -> dict[str, Any]:
    """Prefer the real producer validator; fall back to the strict double."""
    if _real_validate_workflow_bytes is not None:  # pragma: no cover - not landed
        return _real_validate_workflow_bytes(workflow_bytes)
    return _double_validate_workflow_bytes(workflow_bytes)


def ensure_valid(response: dict[str, Any]) -> dict[str, Any]:
    """Raise a structured ``validation_rejects`` unless the response is valid."""
    if response.get("schema") != VALIDATION_SCHEMA_V1:
        raise V46ContractError("schema_mismatch", "validation response has the wrong schema")
    if response.get("valid") is not True:
        first = (response.get("diagnostics") or [{}])[0]
        raise V46ContractError(
            "validation_rejects",
            f"producer validation rejected the workflow: "
            f"{first.get('code')}: {first.get('message')}",
        )
    return response


def submit_validated_workflow(receipt: dict[str, Any], submitted_bytes: bytes) -> bytes:
    """Validated-bytes==submitted-bytes gate: the receipt binds exact bytes."""
    expected = receipt.get("workflow_sha256")
    actual = "sha256:" + hashlib.sha256(submitted_bytes).hexdigest()
    if expected != actual:
        raise V46ContractError(
            "validated_not_submitted",
            "submitted bytes differ from the validated bytes: re-validate before submit",
        )
    return submitted_bytes


def check_contract_fresh(submitted_against_digest: str, current_contract: dict[str, Any]) -> None:
    """Contract-refresh race gate: stale readers are rejected, never silently kept."""
    current = current_contract.get("contract_digest")
    if submitted_against_digest != current:
        raise V46ContractError(
            "contract_refresh_race",
            "contract was refreshed between read and submit: re-read and re-validate",
        )


def _require_real_producer() -> None:
    if _real_build_contract is None or _real_validate_workflow_bytes is None:
        raise V46ContractError(
            "producer_unavailable",
            "real confflow.producer modules are not importable; double path in use",
        )


# ---------------------------------------------------------------------------
# Fake full TSPES chain: 20 TS -> IRC -> 40 endpoints -> opt/freq -> SP ->
# analysis (stub-or-real) -> 20 ReactionGroups -> RunResultManifest.
#
# FAKE NOTICE: native execution is simulated with deterministic placeholder
# energies; only counts, ids, lineage, digests, and manifest consistency are
# asserted.  The analysis step prefers the real ``confflow.analysis``
# capability when it lands and otherwise uses ``stub_reaction_analysis``.
# ---------------------------------------------------------------------------
N_TS = 20
N_ENDPOINTS = 40
N_GROUPS = 20


def fake_ts_inputs(count: int = N_TS) -> list[dict[str, str]]:
    return [
        {"id": f"ts{i:02d}", "group_key": f"rxn-{i:02d}", "lineage_root_id": f"root-{i:02d}"}
        for i in range(count)
    ]


def fake_irc_endpoints(ts_inputs: list[dict[str, str]]) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    for ts in ts_inputs:
        for direction in ("forward", "reverse"):
            endpoints.append(
                {
                    "id": f"{ts['id']}:endpoint:{direction}:0",
                    "parent_id": ts["id"],
                    "group_key": ts["group_key"],
                    "lineage_root_id": ts["lineage_root_id"],
                    "role": f"path_endpoint_{direction}",
                }
            )
    return endpoints


def fake_optimize(endpoint_ids: list[str]) -> dict[str, float]:
    return {
        endpoint_id: -76.0 - (abs(hash(endpoint_id)) % 1000) / 1e6 for endpoint_id in endpoint_ids
    }


def stub_reaction_analysis(
    group_key: str,
    ts_id: str,
    forward_id: str,
    reverse_id: str,
    energies: dict[str, float],
    source_result_ids: list[str],
) -> dict[str, Any]:
    """Reaction-profile analysis: real implementation preferred, stub fallback."""
    if _real_compute_reaction_profile is not None:
        return _real_reaction_analysis(
            group_key, ts_id, forward_id, reverse_id, energies, source_result_ids
        )
    ts_energy = -76.0
    forward_barrier = round(energies[forward_id] - ts_energy + 0.05, 6)
    reverse_barrier = round(energies[reverse_id] - ts_energy + 0.03, 6)
    return {
        "group_key": group_key,
        "ts_structure_id": ts_id,
        "forward_endpoint_id": forward_id,
        "reverse_endpoint_id": reverse_id,
        "gibbs_energy": round(energies[forward_id] - energies[reverse_id], 6),
        "barriers": {"forward": forward_barrier, "reverse": reverse_barrier},
        "assignment": "consistent",
        "source_result_ids": list(source_result_ids),
    }


def _real_reaction_analysis(
    group_key: str,
    ts_id: str,
    forward_id: str,
    reverse_id: str,
    energies: dict[str, float],
    source_result_ids: list[str],
) -> dict[str, Any]:
    """Run the landed analysis over synthetic group records."""
    from confflow.analysis.thermochemistry import EnergyModel
    from confflow.domain.result import ResultSet, ScientificResult
    from confflow.domain.structure import StructureRecord, StructureSet

    water_atoms = ("O", "H", "H")
    water_coords = ((0.0, 0.0, 0.0), (0.76, 0.59, 0.0), (0.76, -0.59, 0.0))
    structures = StructureSet.of(
        StructureRecord(id=ts_id, atoms=water_atoms, coordinates=water_coords, group_key=group_key),
        StructureRecord(
            id=forward_id,
            atoms=water_atoms,
            coordinates=water_coords,
            parent_ids=(ts_id,),
            lineage_root_id=ts_id,
            group_key=group_key,
            role="path_endpoint_forward",
        ),
        StructureRecord(
            id=reverse_id,
            atoms=water_atoms,
            coordinates=water_coords,
            parent_ids=(ts_id,),
            lineage_root_id=ts_id,
            group_key=group_key,
            role="path_endpoint_reverse",
        ),
    )
    pool = [
        ScientificResult(
            kind="energy", value=-76.02, unit=Unit.HARTREE, subject_structure_id=ts_id
        ),
        ScientificResult(
            kind="gibbs_energy", value=-76.0, unit=Unit.HARTREE, subject_structure_id=ts_id
        ),
        ScientificResult(
            kind="energy",
            value=float(energies[forward_id]) - 0.02,
            unit=Unit.HARTREE,
            subject_structure_id=forward_id,
        ),
        ScientificResult(
            kind="gibbs_energy",
            value=float(energies[forward_id]),
            unit=Unit.HARTREE,
            subject_structure_id=forward_id,
        ),
        ScientificResult(
            kind="energy",
            value=float(energies[reverse_id]) - 0.02,
            unit=Unit.HARTREE,
            subject_structure_id=reverse_id,
        ),
        ScientificResult(
            kind="gibbs_energy",
            value=float(energies[reverse_id]),
            unit=Unit.HARTREE,
            subject_structure_id=reverse_id,
        ),
    ]
    (analysis,) = _real_compute_reaction_profile(
        structures,
        ResultSet(tuple(pool)),
        EnergyModel(
            mode="direct", electronic_selector="energy", correction_selector="gibbs_correction"
        ),
    )
    ts_gibbs_value = -76.0
    by_kind = {record.kind: record for record in analysis.results}
    if not analysis.ok:
        raise AssertionError(
            "real reaction analysis failed closed: "
            + "; ".join(item.message for item in analysis.diagnostics)
        )
    return {
        "group_key": group_key,
        "ts_structure_id": ts_id,
        "forward_endpoint_id": forward_id,
        "reverse_endpoint_id": reverse_id,
        "gibbs_energy": round(ts_gibbs_value, 6),
        "barriers": {
            "forward": round(float(by_kind["barrier_forward_endpoint"].value), 6),
            "reverse": round(float(by_kind["barrier_reverse_endpoint"].value), 6),
        },
        "assignment": "consistent",
        "source_result_ids": list(source_result_ids),
    }


def run_fake_tspes_chain(
    *,
    run_id: str = "run-v46-e2e",
    producer: dict[str, str] | None = None,
    workflow_definition_digest: str = "sha256:" + "0" * 64,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Run the fake chain; return ``(manifest, artifact_store)``."""
    producer = producer or {
        "package": "confflow",
        "version": "4.6.0-test",
        "commit": "double-no-vcs",
        "dirty": "True",
    }
    ts_inputs = fake_ts_inputs()
    endpoints = fake_irc_endpoints(ts_inputs)
    assert len(endpoints) == N_ENDPOINTS
    endpoint_ids = [endpoint["id"] for endpoint in endpoints]
    energies = fake_optimize(endpoint_ids)
    # SP results: one deterministic result id per endpoint.
    sp_result_ids = {endpoint_id: f"s_sp:{endpoint_id}:energy" for endpoint_id in endpoint_ids}
    produced_ids = (
        {ts["id"] for ts in ts_inputs} | set(endpoint_ids) | {f"{eid}:opt" for eid in endpoint_ids}
    )

    def _step(step_id: str, count: int, result_ids: list[str]) -> dict[str, Any]:
        return {
            "id": step_id,
            "status": "completed",
            "step_result_digest": _canonical_sha256(sorted(result_ids)),
            "counts": {"completed": count, "failed": 0},
            "diagnostics_summary": {"errors": 0, "warnings": 0},
            "_result_ids": result_ids,  # internal seam for verification, stripped below
        }

    steps = [
        _step("s_irc", N_TS, endpoint_ids),
        _step("s_opt", N_ENDPOINTS, [f"{eid}:opt" for eid in endpoint_ids]),
        _step("s_freq", N_ENDPOINTS, [f"{eid}:opt:freq" for eid in endpoint_ids]),
        _step("s_sp", N_ENDPOINTS, sorted(sp_result_ids.values())),
        _step("s_analysis", N_GROUPS, [f"s_analysis:rxn-{i:02d}" for i in range(N_GROUPS)]),
    ]
    analyses: list[dict[str, Any]] = []
    artifact_store: dict[str, bytes] = {}
    artifacts: list[dict[str, str]] = []
    for _index, ts in enumerate(ts_inputs):
        forward_id = f"{ts['id']}:endpoint:forward:0"
        reverse_id = f"{ts['id']}:endpoint:reverse:0"
        sources = [sp_result_ids[forward_id], sp_result_ids[reverse_id]]
        analyses.append(
            stub_reaction_analysis(
                ts["group_key"], ts["id"], forward_id, reverse_id, energies, sources
            )
        )
        payload = _canonical_json_bytes(
            {"group": ts["group_key"], "gibbs": analyses[-1]["gibbs_energy"]}
        )
        locator = f"artifacts/{ts['group_key']}/reaction_profile.json"
        artifact_store[locator] = payload
        artifacts.append(
            {
                "role": "reaction_profile",
                "checksum": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "locator": locator,
            }
        )
    manifest = {
        "content_schema": RESULT_MANIFEST_SCHEMA_V1,
        "run_id": run_id,
        "status": "completed",
        "workflow_definition_digest": workflow_definition_digest,
        "producer": dict(producer),
        "steps": [
            {key: value for key, value in step.items() if not key.startswith("_")} for step in steps
        ],
        "analyses": analyses,
        "artifacts": artifacts,
        "_result_ids": sorted({result_id for step in steps for result_id in step["_result_ids"]}),
        "_produced_ids": sorted(produced_ids),
    }
    return manifest, artifact_store


def verify_run_result_manifest(
    manifest: dict[str, Any],
    artifact_store: dict[str, bytes],
    *,
    contract_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Manifest self-consistency: structured failure for every mismatch class."""
    if manifest.get("content_schema") != RESULT_MANIFEST_SCHEMA_V1:
        raise V46ContractError("schema_mismatch", "manifest has the wrong content_schema")
    for key in ("run_id", "status", "workflow_definition_digest", "producer", "steps"):
        if key not in manifest:
            raise V46ContractError("malformed_manifest", f"manifest is missing {key!r}")
    if not isinstance(manifest.get("analyses"), list):
        raise V46ContractError("malformed_manifest", "manifest is missing 'analyses'")
    if not isinstance(manifest.get("artifacts"), list):
        raise V46ContractError("malformed_manifest", "manifest is missing 'artifacts'")
    if contract_envelope is not None:
        expected = contract_envelope.get("producer", {})
        actual = manifest.get("producer", {})
        if actual.get("package") != expected.get("package") or actual.get(
            "version"
        ) != expected.get("version"):
            raise V46ContractError(
                "result_producer_mismatch",
                "manifest producer does not match the contract producer",
            )
    produced_ids = set(manifest.get("_produced_ids", []))
    result_ids = set(manifest.get("_result_ids", []))
    if not produced_ids or not result_ids:
        # Manifests that crossed the wire lose the internal seams; rebuild
        # them from steps/analyses is impossible, so demand them present.
        raise V46ContractError("malformed_manifest", "manifest carries no verifiable id seams")
    seen_groups: set[str] = set()
    for analysis in manifest["analyses"]:
        for key in (
            "group_key",
            "ts_structure_id",
            "forward_endpoint_id",
            "reverse_endpoint_id",
            "gibbs_energy",
            "barriers",
            "assignment",
            "source_result_ids",
        ):
            if key not in analysis:
                raise V46ContractError("malformed_manifest", f"analysis entry is missing {key!r}")
        group = analysis["group_key"]
        if group in seen_groups:
            raise V46ContractError("ambiguous_group", f"group {group!r} is claimed by two analyses")
        seen_groups.add(group)
        for ref in (
            analysis["ts_structure_id"],
            analysis["forward_endpoint_id"],
            analysis["reverse_endpoint_id"],
        ):
            if ref not in produced_ids:
                raise V46ContractError(
                    "manifest_mismatch", f"analysis ref {ref!r} was never produced"
                )
        sources = analysis["source_result_ids"]
        if not sources:
            raise V46ContractError(
                "missing_analysis_result",
                f"group {group!r} cites no source result ids",
            )
        for source in sources:
            if source not in result_ids:
                raise V46ContractError(
                    "missing_analysis_result",
                    f"source result {source!r} for group {group!r} was never produced",
                )
    for artifact in manifest["artifacts"]:
        for key in ("role", "checksum", "locator"):
            if key not in artifact:
                raise V46ContractError("malformed_manifest", f"artifact entry is missing {key!r}")
        payload = artifact_store.get(artifact["locator"])
        if payload is None:
            raise V46ContractError(
                "manifest_mismatch", f"artifact {artifact['locator']!r} has no bytes"
            )
        if "sha256:" + hashlib.sha256(payload).hexdigest() != artifact["checksum"]:
            raise V46ContractError(
                "manifest_mismatch",
                f"artifact {artifact['locator']!r} checksum does not match its bytes",
            )
    return manifest


# ---------------------------------------------------------------------------
# Positive path.
# ---------------------------------------------------------------------------
class TestContractRoundtrip:
    def test_generate_serialize_parse_verify(self) -> None:
        envelope = build_contract_envelope()
        wire = serialize_contract(envelope)
        parsed = JobdeskContractDouble.parse(wire)
        assert parsed["content_schema"] == CONTRACT_SCHEMA_V4
        assert parsed["contract_digest"] == envelope["contract_digest"]
        assert parsed["analysis_capabilities"] == [
            {"name": "reaction_profile", "contract_version": REACTION_PROFILE_CAPABILITY}
        ]
        assert parsed["remote_capability"] == {
            "handoff": HANDOFF_CAPABILITY,
            "result": RESULT_CAPABILITY,
        }
        assert parsed["validation_response_schema"] == VALIDATION_SCHEMA_V1

    def test_digest_reverification_is_canonical(self) -> None:
        first = build_contract_envelope()
        reordered = json.loads(json.dumps(first, sort_keys=False))
        assert serialize_contract(reordered) == serialize_contract(first)
        assert JobdeskContractDouble.parse(serialize_contract(reordered))["contract_digest"] == (
            first["contract_digest"]
        )

    def test_tamper_rejection(self) -> None:
        envelope = build_contract_envelope()
        tampered = copy.deepcopy(envelope)
        tampered["workflow_schema"]["version"] = "9.9-evil"
        with pytest.raises(V46ContractError) as excinfo:
            JobdeskContractDouble.parse(serialize_contract(tampered))
        assert excinfo.value.code == "bad_digest"

    def test_tampered_envelope_digest_rejected(self) -> None:
        envelope = build_contract_envelope()
        tampered = copy.deepcopy(envelope)
        tampered["scheduler"] = {"max_parallel_items": "1-evil"}
        with pytest.raises(V46ContractError) as excinfo:
            JobdeskContractDouble.parse(serialize_contract(tampered))
        assert excinfo.value.code == "bad_digest"

    def test_frozen_shape_keys_present(self) -> None:
        envelope = build_contract_envelope()
        for key in (
            "content_schema",
            "producer",
            "contract_digest",
            "workflow_schema",
            "workflow_schema_sha256",
            "editor_manifest",
            "editor_manifest_sha256",
            "recipe_catalog",
            "recipe_catalog_sha256",
            "executors",
            "programs",
            "execution_adapters",
            "result_profiles",
            "scientific_checks",
            "recovery_profiles",
            "ports",
            "pairing",
            "cardinality",
            "resources",
            "completion",
            "scheduler",
            "native_escape_hatches",
            "analysis_capabilities",
            "result_schema",
            "result_schema_sha256",
            "validation_response_schema",
            "remote_capability",
        ):
            assert key in envelope, key


class TestJobdeskSimulation:
    def test_parse_build_serialize_validate_same_bytes(self) -> None:
        contract_bytes = serialize_contract(build_contract_envelope())
        contract = JobdeskContractDouble.parse(contract_bytes)
        workflow = JobdeskContractDouble.build_tspes_workflow(contract)
        assert [step["id"] for step in workflow["steps"]] == [
            "s_irc",
            "s_opt",
            "s_freq",
            "s_sp",
            "s_analysis",
        ]
        workflow_bytes = JobdeskContractDouble.serialize_workflow(workflow)
        response = validate_workflow_bytes(workflow_bytes)
        assert response["schema"] == VALIDATION_SCHEMA_V1
        ensure_valid(response)

    def test_edit_representative_fields_then_revalidate(self) -> None:
        contract = JobdeskContractDouble.parse(serialize_contract(build_contract_envelope()))
        workflow = JobdeskContractDouble.build_tspes_workflow(contract)
        edited = JobdeskContractDouble.edit_workflow_field(
            workflow, "s_opt", "calculation.native.keyword", "B3LYP Opt Tight"
        )
        assert edited["steps"][1]["calculation"]["native"]["keyword"] == "B3LYP Opt Tight"
        ensure_valid(validate_workflow_bytes(JobdeskContractDouble.serialize_workflow(edited)))

    def test_validated_bytes_equal_submitted_bytes_gate(self) -> None:
        contract = JobdeskContractDouble.parse(serialize_contract(build_contract_envelope()))
        workflow_bytes = JobdeskContractDouble.serialize_workflow(
            JobdeskContractDouble.build_tspes_workflow(contract)
        )
        ensure_valid(validate_workflow_bytes(workflow_bytes))
        receipt = {
            "workflow_sha256": "sha256:" + hashlib.sha256(workflow_bytes).hexdigest(),
            "valid": True,
        }
        assert submit_validated_workflow(receipt, workflow_bytes) == workflow_bytes

    def test_validated_not_submitted_tamper(self) -> None:
        contract = JobdeskContractDouble.parse(serialize_contract(build_contract_envelope()))
        workflow_bytes = JobdeskContractDouble.serialize_workflow(
            JobdeskContractDouble.build_tspes_workflow(contract)
        )
        receipt = {
            "workflow_sha256": "sha256:" + hashlib.sha256(workflow_bytes).hexdigest(),
            "valid": True,
        }
        tampered = workflow_bytes.replace(b"B3LYP Opt", b"B3LYP Opt Evil")
        with pytest.raises(V46ContractError) as excinfo:
            submit_validated_workflow(receipt, tampered)
        assert excinfo.value.code == "validated_not_submitted"


class TestFakeTspesChain:
    def test_counts_20_40_20(self) -> None:
        manifest, store = run_fake_tspes_chain()
        assert len(manifest["analyses"]) == N_GROUPS
        assert len(manifest["artifacts"]) == N_GROUPS
        assert len(store) == N_GROUPS
        step_counts = {step["id"]: step["counts"]["completed"] for step in manifest["steps"]}
        assert step_counts == {
            "s_irc": 20,
            "s_opt": 40,
            "s_freq": 40,
            "s_sp": 40,
            "s_analysis": 20,
        }

    def test_manifest_self_consistency(self) -> None:
        manifest, store = run_fake_tspes_chain()
        verify_run_result_manifest(manifest, store)
        groups = [analysis["group_key"] for analysis in manifest["analyses"]]
        assert groups == [f"rxn-{i:02d}" for i in range(20)]
        for analysis in manifest["analyses"]:
            assert set(analysis["barriers"]) == {"forward", "reverse"}
            assert analysis["assignment"] == "consistent"

    def test_manifest_producer_matches_contract(self) -> None:
        envelope = build_contract_envelope()
        manifest, store = run_fake_tspes_chain(producer=dict(envelope["producer"]))
        verify_run_result_manifest(manifest, store, contract_envelope=envelope)


class TestRealVsDoubleInventory:
    """Machine-readable record of which seams are real and which are doubled."""

    def test_inventory(self) -> None:
        assert USING_PRODUCER_CONTRACT_DOUBLE == (_real_build_contract is None)
        assert USING_PRODUCER_VALIDATION_DOUBLE == (_real_validate_workflow_bytes is None)
        assert USING_ANALYSIS_STUB == (_real_compute_reaction_profile is None)
        # The validation double is strict: it runs the REAL V4 compiler.
        assert compile_workflow is not None


# ---------------------------------------------------------------------------
# Failure matrix: every row is a structured failure, never a silent fallback.
# ---------------------------------------------------------------------------
class TestFailureMatrix:
    def test_producer_unavailable(self) -> None:
        if _real_build_contract is not None and _real_validate_workflow_bytes is not None:
            pytest.skip("real producer landed: double path retired")
        with pytest.raises(V46ContractError) as excinfo:
            _require_real_producer()
        assert excinfo.value.code == "producer_unavailable"

    def test_old_no_v4_contract(self) -> None:
        legacy = build_contract_envelope_double(
            contract_schema="confflow.configuration-contract.v1"
        )
        # Re-sign under the legacy schema so the failure is the version, not the digest.
        unsigned = {k: v for k, v in legacy.items() if k != "contract_digest"}
        legacy["contract_digest"] = _canonical_sha256(unsigned)
        with pytest.raises(V46ContractError) as excinfo:
            JobdeskContractDouble.parse(serialize_contract(legacy))
        assert excinfo.value.code == "old_contract_no_v4"

    def test_bad_digest(self) -> None:
        envelope = build_contract_envelope()
        envelope["editor_manifest_sha256"] = "sha256:" + "f" * 64
        unsigned = {k: v for k, v in envelope.items() if k != "contract_digest"}
        envelope["contract_digest"] = _canonical_sha256(unsigned)
        with pytest.raises(V46ContractError) as excinfo:
            JobdeskContractDouble.parse(serialize_contract(envelope))
        assert excinfo.value.code == "bad_digest"

    def test_schema_mismatch(self) -> None:
        envelope = build_contract_envelope()
        envelope["content_schema"] = "confflow.configuration-contract.v9"
        unsigned = {k: v for k, v in envelope.items() if k != "contract_digest"}
        envelope["contract_digest"] = _canonical_sha256(unsigned)
        with pytest.raises(V46ContractError) as excinfo:
            JobdeskContractDouble.parse(serialize_contract(envelope))
        assert excinfo.value.code == "schema_mismatch"

    def test_manifest_mismatch(self) -> None:
        manifest, store = run_fake_tspes_chain()
        manifest["analyses"][0]["forward_endpoint_id"] = "ts99:endpoint:forward:0"
        with pytest.raises(V46ContractError) as excinfo:
            verify_run_result_manifest(manifest, store)
        assert excinfo.value.code == "manifest_mismatch"

    def test_artifact_checksum_mismatch(self) -> None:
        manifest, store = run_fake_tspes_chain()
        first_locator = manifest["artifacts"][0]["locator"]
        store[first_locator] = b"tampered-bytes"
        with pytest.raises(V46ContractError) as excinfo:
            verify_run_result_manifest(manifest, store)
        assert excinfo.value.code == "manifest_mismatch"

    def test_recipe_mismatch(self) -> None:
        contract = JobdeskContractDouble.parse(serialize_contract(build_contract_envelope()))
        with pytest.raises(V46ContractError) as excinfo:
            JobdeskContractDouble.build_tspes_workflow(contract, recipe_id="no-such-recipe")
        assert excinfo.value.code == "recipe_mismatch"

    def test_validation_rejects(self) -> None:
        contract = JobdeskContractDouble.parse(serialize_contract(build_contract_envelope()))
        workflow = JobdeskContractDouble.build_tspes_workflow(contract)
        # The real V4 compiler does not validate program names (executables
        # resolve at run time), so rejection is pinned on an unknown
        # executor, which the compiler does reject.
        workflow["steps"][0]["executor"] = "no-such-executor"
        response = validate_workflow_bytes(JobdeskContractDouble.serialize_workflow(workflow))
        assert response["valid"] is False
        assert response["diagnostics"]
        with pytest.raises(V46ContractError) as excinfo:
            ensure_valid(response)
        assert excinfo.value.code == "validation_rejects"

    def test_validated_not_submitted(self) -> None:
        receipt = {"workflow_sha256": "sha256:" + "0" * 64, "valid": True}
        with pytest.raises(V46ContractError) as excinfo:
            submit_validated_workflow(receipt, b'{"schema": "confflow.workflow.v4"}')
        assert excinfo.value.code == "validated_not_submitted"

    def test_result_producer_mismatch(self) -> None:
        envelope = build_contract_envelope()
        manifest, store = run_fake_tspes_chain(
            producer={"package": "confflow", "version": "9.9-evil", "commit": "x", "dirty": "True"}
        )
        with pytest.raises(V46ContractError) as excinfo:
            verify_run_result_manifest(manifest, store, contract_envelope=envelope)
        assert excinfo.value.code == "result_producer_mismatch"

    def test_malformed_manifest(self) -> None:
        manifest, store = run_fake_tspes_chain()
        del manifest["analyses"]
        with pytest.raises(V46ContractError) as excinfo:
            verify_run_result_manifest(manifest, store)
        assert excinfo.value.code == "malformed_manifest"

    def test_missing_analysis_result(self) -> None:
        manifest, store = run_fake_tspes_chain()
        manifest["analyses"][0]["source_result_ids"] = []
        with pytest.raises(V46ContractError) as excinfo:
            verify_run_result_manifest(manifest, store)
        assert excinfo.value.code == "missing_analysis_result"

    def test_ambiguous_group(self) -> None:
        manifest, store = run_fake_tspes_chain()
        manifest["analyses"].append(copy.deepcopy(manifest["analyses"][0]))
        with pytest.raises(V46ContractError) as excinfo:
            verify_run_result_manifest(manifest, store)
        assert excinfo.value.code == "ambiguous_group"

    def test_contract_refresh_race(self) -> None:
        first = build_contract_envelope()
        second = build_contract_envelope()
        # Two independently built doubles must differ (dirty dev version marker
        # would hide a refresh race); force the difference deterministically.
        second["producer"]["version"] = "4.6.0-test+refresh1"
        unsigned = {k: v for k, v in second.items() if k != "contract_digest"}
        second["contract_digest"] = _canonical_sha256(unsigned)
        assert first["contract_digest"] != second["contract_digest"]
        with pytest.raises(V46ContractError) as excinfo:
            check_contract_fresh(first["contract_digest"], second)
        assert excinfo.value.code == "contract_refresh_race"
        check_contract_fresh(second["contract_digest"], second)  # fresh read passes
