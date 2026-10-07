#!/usr/bin/env python3

"""V4-6 cross-repository contract roundtrip (R2.3a: fake TSPES chain retired).

Scope: NEW tests only, programming against the FROZEN V4 wire shapes.  The
real producer side (``confflow.producer.*``) is landed; the analysis side
(``confflow.analysis.*``) is retired (R2.3a deleted the package), and the real
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
  - R2.3a: ``run_fake_tspes_chain`` / ``stub_reaction_analysis`` (fakes for
    native execution + ``reaction_profile`` analysis) are retired with
    ``confflow.analysis``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Prefer-real seam: ConfFlow producer modules (LANDED).
#
# Architecture Diet PR-9 retired the released V1/V2 contract writer, so the
# only current producer builder is ``build_configuration_contract_v4`` (the
# former ``build_configuration_contract`` name emitted the v1 document and is
# gone).  This file still programs against its own *frozen V4-6 envelope
# double* for the literal wire-shape assertions below -- the real envelope is a
# superset with registry-generated descriptors -- while
# ``tests/v4/test_v46_cross_repo_e2e.py`` exercises the real bytes end to end.
# The availability flags are therefore truthful again: the real producer and
# the real validator are importable, and ``TestRealVsDoubleInventory`` pins
# that plus the real bytes round trip.
# ---------------------------------------------------------------------------
from confflow.producer.contract import (  # noqa: E402
    build_configuration_contract_v4 as build_real_contract,
)
from confflow.producer.contract import (  # noqa: E402
    contract_digest_of,
)
from confflow.producer.contract import (  # noqa: E402
    generate_contract_bytes as generate_real_contract_bytes,
)
from confflow.producer.validation import (  # noqa: E402
    validate_workflow_bytes as _real_validate_workflow_bytes,
)

_REAL_PRODUCER_AVAILABLE = True

# ---------------------------------------------------------------------------
# R2.3a: ConfFlow analysis modules are retired (``confflow.analysis``
# deleted). The prefer-real seam below is kept as a retired record: the
# import must now fail, and the fake-chain inventory assertion reflects
# the retirement.
# ---------------------------------------------------------------------------
try:
    from confflow.analysis import (  # type: ignore[import-not-found]
        compute_reaction_profile as _real_compute_reaction_profile,
    )

    _REAL_ANALYSIS_AVAILABLE = True
except ImportError:
    _real_compute_reaction_profile = None
    _REAL_ANALYSIS_AVAILABLE = False

# Real V4 compiler: LANDED, always preferred for workflow validation.
from confflow.workflow.v4 import compile_workflow  # noqa: E402

#: The envelope builder below is the frozen V4-6 double by construction; the
#: real producer is exercised through ``build_real_contract`` /
#: ``generate_real_contract_bytes`` and ``test_v46_cross_repo_e2e.py``.
USING_PRODUCER_CONTRACT_DOUBLE = True
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
HANDOFF_CAPABILITY = "confflow.control.worker-handoff.v3"
RESULT_CAPABILITY = "confflow.control.worker-result.v3"

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
    """Return the frozen V4-6 envelope double.

    This file's literal wire-shape assertions program against the frozen
    envelope it was written with; the *real* producer envelope is a superset
    (registry-generated descriptors) and is exercised through
    ``build_real_contract`` / ``generate_real_contract_bytes`` and
    ``tests/v4/test_v46_cross_repo_e2e.py``.
    """
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
        """Build a V4 opt/freq/sp workflow document from the contract + one recipe.

        R2.2: the retired IRC + analysis tail is gone; the double now
        authors the retained calculation chain (same wire discipline).
        """
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
                    "role": "opt",
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
            "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
            "steps": [
                _calc("s_opt", "standard", "B3LYP Opt"),
                _calc("s_freq", "standard", "B3LYP Freq"),
                _calc("s_sp", "standard", "B3LYP SP"),
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
    """Run the producer validator and project it to the frozen response envelope.

    The real ``confflow.producer.validation.validate_workflow_bytes`` returns a
    typed :class:`~confflow.producer.validation.ValidationReport`; this file's
    failure matrix programs against the frozen response envelope (``schema`` /
    ``valid`` / ``diagnostics``), so the report is projected here rather than
    duplicated.  The projection keeps every diagnostic field, including the
    producer's stable ``code``.
    """
    if _real_validate_workflow_bytes is not None:
        payload = _real_validate_workflow_bytes(workflow_bytes).to_dict()
        return {
            "schema": payload["schema"],
            "valid": payload["ok"],
            "diagnostics": [
                {
                    "code": item["code"],
                    "severity": item["severity"],
                    "step_id": item.get("step_id"),
                    "field_path": item.get("field_path"),
                    "message": item["message"],
                }
                for item in payload["diagnostics"]
            ],
        }
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
    if not _REAL_PRODUCER_AVAILABLE or _real_validate_workflow_bytes is None:
        raise V46ContractError(
            "producer_unavailable",
            "real confflow.producer modules are not importable; double path in use",
        )


# ---------------------------------------------------------------------------
# R2.3a (G18): fake TSPES chain retired with confflow.analysis and the
# ``reaction_profile`` capability (20 TS -> IRC -> endpoints -> analysis ->
# 20 ReactionGroups). The frozen wire-shape doubles above are retained;
# live analysis vehicles are gone.
# ---------------------------------------------------------------------------


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
            "s_opt",
            "s_freq",
            "s_sp",
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
        assert edited["steps"][0]["calculation"]["native"]["keyword"] == "B3LYP Opt Tight"
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


# R2.3a (G18): TestFakeTspesChain retired with confflow.analysis and the
# fake TSPES chain (test_counts_20_40_20, test_manifest_self_consistency,
# test_manifest_producer_matches_contract).


class TestRealVsDoubleInventory:
    """Machine-readable record of which seams are real and which are doubled."""

    def test_inventory(self) -> None:
        # The real producer and validator are landed and importable; this file
        # additionally keeps a frozen V4-6 envelope double for literal
        # wire-shape assertions (see the seam comment above).
        # R2.3a: analysis retired (confflow.analysis deleted from this repo;
        # the prefer-real import above may still resolve via an editable
        # install pointing elsewhere, so retirement is pinned by path).
        from pathlib import Path as _Path

        assert _REAL_PRODUCER_AVAILABLE is True
        assert USING_PRODUCER_CONTRACT_DOUBLE is True
        assert USING_PRODUCER_VALIDATION_DOUBLE is False
        assert not (_Path(__file__).resolve().parents[2] / "confflow" / "analysis").exists()
        # The validation double is strict: it runs the REAL V4 compiler.
        assert compile_workflow is not None

    def test_real_producer_bytes_are_self_consistent(self) -> None:
        """Real producer bytes re-verify under the producer's own authority.

        The real envelope is *not* the frozen V4-6 shape this file pins
        literally (it adds registry-generated descriptors and names the
        workflow line), and it publishes bare-hex canonical digests rather
        than the frozen ``sha256:``-prefixed ones -- so it is verified with
        :func:`confflow.producer.contract.contract_digest_of` instead of the
        frozen helper.  The full end-to-end real-bytes round trip lives in
        ``tests/v4/test_v46_cross_repo_e2e.py``.
        """
        envelope = build_real_contract(producer_version="4.6.0-inventory")
        wire = generate_real_contract_bytes(producer_version="4.6.0-inventory")
        parsed = json.loads(wire.decode("utf-8"))

        assert parsed["content_schema"] == CONTRACT_SCHEMA_V4
        assert parsed["workflow_schema_id"] == WORKFLOW_SCHEMA_V4
        assert parsed["contract_digest"] == envelope["contract_digest"]
        assert contract_digest_of(parsed) == parsed["contract_digest"]

    def test_real_producer_validation_is_the_producer_authority(self) -> None:
        workflow = JobdeskContractDouble.build_tspes_workflow(
            JobdeskContractDouble.parse(serialize_contract(build_contract_envelope()))
        )
        response = _real_validate_workflow_bytes(JobdeskContractDouble.serialize_workflow(workflow))
        payload = response.to_dict()
        assert payload["schema"] == VALIDATION_SCHEMA_V1
        assert payload["ok"] is True, payload["diagnostics"]


# ---------------------------------------------------------------------------
# Failure matrix: every row is a structured failure, never a silent fallback.
# ---------------------------------------------------------------------------
class TestFailureMatrix:
    def test_producer_unavailable(self) -> None:
        if _REAL_PRODUCER_AVAILABLE and _real_validate_workflow_bytes is not None:
            pytest.skip("real producer landed: the unavailable path is gone")
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

    # R2.3a (G18): test_manifest_mismatch, test_artifact_checksum_mismatch,
    # test_result_producer_mismatch, test_malformed_manifest,
    # test_missing_analysis_result, test_ambiguous_group retired with the
    # fake TSPES chain and confflow.analysis.

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
