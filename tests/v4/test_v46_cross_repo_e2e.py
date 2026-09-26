#!/usr/bin/env python3

"""V4-6 cross-repo E2E on REAL producer bytes (sibling workstream E).

Unlike ``tests/v4/test_v46_cross_repo.py`` (which programs against doubles
because the producer had not landed), THIS file consumes the exact bytes
produced by the REAL producer in this repo::

    .venv/bin/python -c "from confflow.producer.contract import generate_contract_bytes; ..."

``generate_contract_bytes(*, producer_version, producer_commit=None,
producer_dirty=None) -> bytes`` emits schema
``"confflow.configuration-contract.v4"``.  A session-scoped fixture generates
those bytes ONCE, writes them to a temp file (path via ``CONFFLOW_CONTRACT_JSON``
or a documented default), and every roundtrip / recipe / validation / manifest
test below reads that file from both the producer side and the simulated
JobDesk side.  No test here touches a hand-written envelope, so nothing here
is labeled DOUBLE except the explicitly marked JobDesk-consumer simulation
(which is stdlib-only and MUST stay ``confflow``-free: enforced by
``tests/v4/test_v46_debt.py``) and the fake native execution counts.

LOUD DOUBLE/FAKE NOTICE (V4-6, remove as siblings land):
  - ``JobdeskV4ConsumerDouble`` is a CONSUMER DOUBLE for the JobDesk-side V4
    parser (``jobdesk_v2.application.editor.contract.v4``, not landed: JobDesk
    still parses v1/v2).  Unlike the sibling file's double it speaks the REAL
    V4 shapes (``content_schema``, ``workflow_schema_id``, bare-hex digests,
    recipe ``document`` payloads) and verifies digests with the stdlib JCS
    approximation proven byte-equal to ``confflow.domain.canonical`` by
    ``TestRealContractRoundtrip::test_stdlib_jcs_approximation_matches``.
  - ``_fake_native_counts`` is a FAKE for native execution: only counts, ids,
    and lineage are asserted.  The analysis step is REAL
    (``confflow.analysis.reaction.assemble_reaction_result``) and the manifest
    is built by the REAL ``confflow.producer.build_run_result_manifest``.

REAL-vs-FROZEN divergences (frozen spec in the work order vs landed producer):
  - digests are BARE hex (``canonical_sha256``), not ``"sha256:"``-prefixed,
    except ``ValidationReport.definition_digest`` and manifest
    ``definition_digest`` which ARE ``"sha256:"``-prefixed (``typed_digest``).
  - validation responses carry ``ok`` (not ``valid``); diagnostics carry
    ``code/severity/step_id/field_path/message`` (+ ``reason``).
  - the manifest carries ``definition_digest`` + ``provenance`` (not
    ``workflow_definition_digest`` + ``producer``).
  - ``executors`` entries key on ``capability``; ``analysis_capabilities`` is
    ``{"capabilities": [{"capability": "reaction_profile", ...}]}``;
    ``ports``/``resources``/``policies``/``native_escape_hatches`` are objects.
  - KNOWN DISPUTE (do not regress): the REAL V4 compiler does NOT validate
    program names against ``ProgramName`` -- an unknown ``program`` still
    compiles ``ok``.  ``test_real_validator_accepts_unknown_program_name``
    pins this; rejection tests use an unknown EXECUTOR (really rejected).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# REAL producer seams (LANDED in this repo -- direct imports, no doubles).
# ---------------------------------------------------------------------------
from confflow.analysis.reaction import ReactionNodeGroup, assemble_reaction_result
from confflow.analysis.thermochemistry import EnergyModel
from confflow.domain.canonical import canonical_json_bytes, canonical_sha256
from confflow.domain.result import ResultSet, ScientificResult
from confflow.domain.units import Unit
from confflow.producer import (
    CONFIGURATION_CONTRACT_V4_SCHEMA,
    RESULT_MANIFEST_SCHEMA,
    build_run_result_manifest,
)
from confflow.producer.contract import contract_digest_of, generate_contract_bytes
from confflow.producer.validation import validate_workflow_bytes
from confflow.workflow.v4 import compile_workflow

USING_REAL_PRODUCER = True
USING_REAL_VALIDATOR = True
USING_REAL_ANALYSIS = True
USING_REAL_MANIFEST_BUILDER = True
# Consumer simulation + native execution counts are doubled/faked (see notice).
USING_JOBDESK_CONSUMER_DOUBLE = True
USING_FAKE_NATIVE_EXECUTION = True

E2E_PRODUCER_VERSION = "4.6.0-e2e"
CONTRACT_ENV_VAR = "CONFFLOW_CONTRACT_JSON"
DEFAULT_CONTRACT_FILENAME = "confflow-v46-contract.json"

CONTRACT_SCHEMA_V4 = "confflow.configuration-contract.v4"
VALIDATION_SCHEMA_V1 = "confflow.configuration-validation.v1"
RESULT_MANIFEST_SCHEMA_V1 = "confflow.run_result_manifest.v1"
REACTION_PROFILE_CONTRACT = "confflow.contract.analysis.reaction_profile.v1"

N_TS = 20
N_ENDPOINTS = 40
N_GROUPS = 20


class E2EContractError(ValueError):
    """Structured V4-6 E2E failure: always a machine-readable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _default_contract_path() -> Path:
    override = os.environ.get(CONTRACT_ENV_VAR)
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / DEFAULT_CONTRACT_FILENAME


def _jcs_bytes(value: Any) -> bytes:
    """Stdlib JCS approximation (sort_keys, no whitespace, UTF-8).

    Proven byte-equal to ``confflow.domain.canonical.canonical_json_bytes``
    for real producer envelopes by
    ``TestRealContractRoundtrip::test_stdlib_jcs_approximation_matches``.
    Used ONLY inside the JobDesk consumer double (which must stay
    ``confflow``-free); producer-side code uses the real canonical helpers.
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _jcs_sha256(value: Any) -> str:
    return hashlib.sha256(_jcs_bytes(value)).hexdigest()


@pytest.fixture(scope="session")
def real_contract_bytes(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """Generate the REAL producer bytes ONCE per session and share via file.

    The bytes are written to ``CONFFLOW_CONTRACT_JSON`` (or the documented
    default temp path, also consumed by the JobDesk-side E2E) and read back,
    so producer side and consumer side provably share the exact same bytes.
    A session-local copy under ``tmp_path_factory`` guards against a stale
    pre-existing file at the shared path.
    """
    payload = generate_contract_bytes(producer_version=E2E_PRODUCER_VERSION)
    assert isinstance(payload, bytes) and payload
    session_copy = tmp_path_factory.mktemp("v46-e2e") / DEFAULT_CONTRACT_FILENAME
    session_copy.write_bytes(payload)
    shared_path = _default_contract_path()
    try:
        shared_path.write_bytes(payload)
    except OSError:
        pass  # shared path is best-effort; the session copy is authoritative
    reread = session_copy.read_bytes()
    assert reread == payload
    try:
        assert shared_path.read_bytes() == payload
    except OSError:
        pass
    return payload


@pytest.fixture(scope="session")
def real_envelope(real_contract_bytes: bytes) -> dict[str, Any]:
    return json.loads(real_contract_bytes.decode("utf-8"))


# ---------------------------------------------------------------------------
# JobDesk-side consumer simulation DOUBLE (pure stdlib; MUST NOT touch
# ``confflow`` -- enforced by tests/v4/test_v46_debt.py).
#
# LOUD DOUBLE NOTICE: stands in for the JobDesk V4 consumer
# (``jobdesk_v2.application.editor.contract.v4``, not landed).  It speaks the
# REAL V4 shapes (not the frozen sketch): ``content_schema``,
# ``workflow_schema_id``, bare-hex digests, recipe ``document`` payloads.
# ---------------------------------------------------------------------------
class JobdeskV4ConsumerDouble:
    """Simulated JobDesk consumer over REAL producer bytes (stdlib only)."""

    @staticmethod
    def parse(contract_bytes: bytes) -> dict[str, Any]:
        try:
            text = contract_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise E2EContractError(
                "schema_mismatch", f"contract bytes are not UTF-8: {exc}"
            ) from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise E2EContractError(
                "schema_mismatch", f"contract bytes are not JSON: {exc}"
            ) from exc
        return JobdeskV4ConsumerDouble.verify(raw)

    @staticmethod
    def verify(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise E2EContractError("schema_mismatch", "contract top level must be an object")
        schema = raw.get("content_schema")
        if schema in ("confflow.configuration-contract.v1", "confflow.configuration-contract.v2"):
            raise E2EContractError(
                "old_contract_no_v4",
                f"producer contract {schema!r} predates V4: no V4 descriptors to edit against",
            )
        if schema != CONTRACT_SCHEMA_V4:
            raise E2EContractError(
                "schema_mismatch", f"unsupported contract content_schema {schema!r}"
            )
        for name in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema"):
            artifact = raw.get(name)
            claimed = raw.get(f"{name}_sha256")
            if not isinstance(artifact, dict):
                raise E2EContractError(
                    "schema_mismatch", f"contract member {name!r} must be an object"
                )
            if not isinstance(claimed, str) or not claimed:
                raise E2EContractError("bad_digest", f"contract publishes no digest for {name!r}")
            if _jcs_sha256(artifact) != claimed.strip().lower():
                raise E2EContractError(
                    "bad_digest", f"{name!r} does not match its published digest"
                )
        claimed_envelope = raw.get("contract_digest")
        if not isinstance(claimed_envelope, str) or not claimed_envelope:
            raise E2EContractError("bad_digest", "contract publishes no contract_digest")
        unsigned = {key: value for key, value in raw.items() if key != "contract_digest"}
        if _jcs_sha256(unsigned) != claimed_envelope.strip().lower():
            raise E2EContractError(
                "bad_digest", "contract envelope does not match its contract_digest"
            )
        return raw

    @staticmethod
    def recipe(contract: dict[str, Any], recipe_id: str) -> dict[str, Any]:
        catalog = contract.get("recipe_catalog")
        if not isinstance(catalog, dict) or not isinstance(catalog.get("recipes"), list):
            raise E2EContractError("recipe_mismatch", "recipe catalog is malformed")
        for recipe in catalog["recipes"]:
            if isinstance(recipe, dict) and recipe.get("id") == recipe_id:
                required = recipe.get("required_fields", [])
                exposed = recipe.get("exposed_fields", [])
                for field_id in required:
                    if field_id not in exposed:
                        raise E2EContractError(
                            "recipe_mismatch",
                            f"recipe {recipe_id!r} requires field {field_id!r} "
                            "outside its exposed fields",
                        )
                if not isinstance(recipe.get("document"), dict):
                    raise E2EContractError(
                        "recipe_mismatch", f"recipe {recipe_id!r} carries no workflow document"
                    )
                return recipe
        raise E2EContractError(
            "recipe_mismatch", f"recipe {recipe_id!r} is not in the producer catalog"
        )

    @staticmethod
    def recipe_document(contract: dict[str, Any], recipe_id: str) -> dict[str, Any]:
        return copy.deepcopy(JobdeskV4ConsumerDouble.recipe(contract, recipe_id)["document"])

    @staticmethod
    def edit_native_keyword(workflow: dict[str, Any], step_id: str, keyword: str) -> dict[str, Any]:
        edited = copy.deepcopy(workflow)
        for step in edited.get("steps", []):
            if step.get("id") == step_id:
                try:
                    step["calculation"]["native"]["keyword"] = keyword
                except (KeyError, TypeError) as exc:
                    raise E2EContractError(
                        "recipe_mismatch", f"step {step_id!r} has no calculation.native.keyword"
                    ) from exc
                return edited
        raise E2EContractError("recipe_mismatch", f"unknown step {step_id!r}")

    @staticmethod
    def serialize_workflow(workflow: dict[str, Any]) -> bytes:
        return _jcs_bytes(workflow)


def _raise_for_report(report: Any) -> dict[str, Any]:
    """Map the REAL ``ValidationReport`` to the validated gate (no fallback)."""
    payload = report.to_dict()
    if payload.get("schema") != VALIDATION_SCHEMA_V1:
        raise E2EContractError("schema_mismatch", "validation response has the wrong schema")
    if report.ok is not True:
        first = (payload.get("diagnostics") or [{}])[0]
        raise E2EContractError(
            "validation_rejects",
            f"producer validation rejected the workflow: {first.get('code')}: {first.get('message')}",
        )
    return payload


def _submit_validated_workflow(receipt: dict[str, Any], submitted_bytes: bytes) -> bytes:
    expected = receipt.get("workflow_sha256")
    actual = "sha256:" + hashlib.sha256(submitted_bytes).hexdigest()
    if expected != actual:
        raise E2EContractError(
            "validated_not_submitted",
            "submitted bytes differ from the validated bytes: re-validate before submit",
        )
    return submitted_bytes


def _check_contract_fresh(submitted_against_digest: str, current_contract: dict[str, Any]) -> None:
    if submitted_against_digest != current_contract.get("contract_digest"):
        raise E2EContractError(
            "contract_refresh_race",
            "contract was refreshed between read and submit: re-read and re-validate",
        )


# ---------------------------------------------------------------------------
# 1. REAL-BYTES contract roundtrip.
# ---------------------------------------------------------------------------
class TestRealContractRoundtrip:
    def test_generate_write_read_reverify(self, real_contract_bytes: bytes) -> None:
        shared = _default_contract_path()
        try:
            assert shared.read_bytes() == real_contract_bytes
        except OSError:
            pytest.skip(f"shared contract path {shared} is not writable in this environment")
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        assert envelope["content_schema"] == CONTRACT_SCHEMA_V4
        assert envelope["content_schema"] == CONFIGURATION_CONTRACT_V4_SCHEMA
        assert envelope["contract_digest"] == contract_digest_of(envelope)
        for name in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema"):
            assert envelope[f"{name}_sha256"] == canonical_sha256(envelope[name]), name

    def test_wire_bytes_are_canonical(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        assert canonical_json_bytes(envelope) == real_contract_bytes

    def test_stdlib_jcs_approximation_matches(self, real_contract_bytes: bytes) -> None:
        """Guard for the JobDesk double: stdlib canonical == real canonical."""
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        assert _jcs_bytes(envelope) == canonical_json_bytes(envelope) == real_contract_bytes
        for name in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema"):
            assert _jcs_bytes(envelope[name]) == canonical_json_bytes(envelope[name]), name
        unsigned = {k: v for k, v in envelope.items() if k != "contract_digest"}
        assert _jcs_sha256(unsigned) == envelope["contract_digest"]

    def test_producer_block_echoes_inputs(self) -> None:
        raw = generate_contract_bytes(
            producer_version="9.9-e2e", producer_commit="abc123", producer_dirty=True
        )
        producer = json.loads(raw.decode("utf-8"))["producer"]
        assert producer == {
            "package": "confflow",
            "version": "9.9-e2e",
            "commit": "abc123",
            "dirty": True,
        }

    def test_double_parses_real_bytes(self, real_contract_bytes: bytes) -> None:
        """The JobDesk DOUBLE verifies the REAL bytes (digest re-verification)."""
        parsed = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        assert (
            parsed["contract_digest"]
            == json.loads(real_contract_bytes.decode("utf-8"))["contract_digest"]
        )

    def test_tampered_artifact_rejected(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        tampered = copy.deepcopy(envelope)
        tampered["workflow_schema"]["title"] = "evil"
        with pytest.raises(E2EContractError) as excinfo:
            JobdeskV4ConsumerDouble.parse(_jcs_bytes(tampered))
        assert excinfo.value.code == "bad_digest"

    def test_tampered_envelope_digest_rejected(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        tampered = copy.deepcopy(envelope)
        tampered["contract_digest"] = "0" * 64
        with pytest.raises(E2EContractError) as excinfo:
            JobdeskV4ConsumerDouble.parse(_jcs_bytes(tampered))
        assert excinfo.value.code == "bad_digest"

    def test_real_digest_helpers_reject_tamper(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        tampered = copy.deepcopy(envelope)
        tampered["recipe_catalog"]["label"] = "evil"
        assert contract_digest_of(tampered) != envelope["contract_digest"]
        assert canonical_sha256(tampered["recipe_catalog"]) != envelope["recipe_catalog_sha256"]


# ---------------------------------------------------------------------------
# 2. Recipe -> workflow -> REAL validator on the SAME bytes.
# ---------------------------------------------------------------------------
class TestRealRecipeToValidation:
    def test_tspes_recipe_present_in_same_bytes(self, real_contract_bytes: bytes) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        recipe = JobdeskV4ConsumerDouble.recipe(contract, "tspes")
        assert recipe["required_fields"] == ["calc.program", "calc.native"]
        assert "document" in recipe and recipe["document"]["schema"] == "confflow.workflow.v4"

    def test_recipe_document_compiles_and_validates_same_bytes(
        self, real_contract_bytes: bytes
    ) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        document = JobdeskV4ConsumerDouble.recipe_document(contract, "tspes")
        compiled = compile_workflow(document)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        workflow_bytes = JobdeskV4ConsumerDouble.serialize_workflow(document)
        report = validate_workflow_bytes(workflow_bytes)
        payload = _raise_for_report(report)
        assert payload["schema"] == VALIDATION_SCHEMA_V1
        assert report.definition_digest is not None
        assert tuple(sorted(report.step_ids)) == tuple(
            sorted(step["id"] for step in document["steps"])
        )

    def test_edit_representative_field_then_revalidate(self, real_contract_bytes: bytes) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        document = JobdeskV4ConsumerDouble.recipe_document(contract, "tspes")
        edited = JobdeskV4ConsumerDouble.edit_native_keyword(
            document, "ts", "B3LYP D3BJ OptTS Tight"
        )
        assert edited["steps"][0]["calculation"]["native"]["keyword"] == "B3LYP D3BJ OptTS Tight"
        before = validate_workflow_bytes(JobdeskV4ConsumerDouble.serialize_workflow(document))
        after = validate_workflow_bytes(JobdeskV4ConsumerDouble.serialize_workflow(edited))
        _raise_for_report(before)
        payload = _raise_for_report(after)
        assert payload["schema"] == VALIDATION_SCHEMA_V1
        assert after.definition_digest != before.definition_digest

    def test_validated_equals_submitted_gate(self, real_contract_bytes: bytes) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        workflow_bytes = JobdeskV4ConsumerDouble.serialize_workflow(
            JobdeskV4ConsumerDouble.recipe_document(contract, "tspes")
        )
        report = validate_workflow_bytes(workflow_bytes)
        _raise_for_report(report)
        receipt = {
            "workflow_sha256": "sha256:" + hashlib.sha256(workflow_bytes).hexdigest(),
            "definition_digest": report.definition_digest,
        }
        assert _submit_validated_workflow(receipt, workflow_bytes) == workflow_bytes

    def test_validated_not_submitted_tamper(self, real_contract_bytes: bytes) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        workflow_bytes = JobdeskV4ConsumerDouble.serialize_workflow(
            JobdeskV4ConsumerDouble.recipe_document(contract, "tspes")
        )
        _raise_for_report(validate_workflow_bytes(workflow_bytes))
        receipt = {"workflow_sha256": "sha256:" + hashlib.sha256(workflow_bytes).hexdigest()}
        tampered = workflow_bytes.replace(b"OptTS", b"OptTS-Evil")
        assert tampered != workflow_bytes
        with pytest.raises(E2EContractError) as excinfo:
            _submit_validated_workflow(receipt, tampered)
        assert excinfo.value.code == "validated_not_submitted"

    def test_real_validator_rejects_unknown_executor(self) -> None:
        """Rejection case matching REAL validator behavior (unknown executor)."""
        document = {
            "schema": "confflow.workflow.v4",
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "steps": [
                {
                    "id": "evil",
                    "executor": "no_such_executor",
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                    "calculation": {
                        "program": "orca",
                        "native": {"keyword": "B3LYP"},
                        "recovery": {"profile": "none"},
                    },
                }
            ],
        }
        report = validate_workflow_bytes(_jcs_bytes(document))
        assert report.ok is False
        assert report.errors(), "expected structured error diagnostics"
        for diagnostic in report.diagnostics:
            assert set(diagnostic) >= {"code", "severity", "step_id", "field_path", "message"}
        with pytest.raises(E2EContractError) as excinfo:
            _raise_for_report(report)
        assert excinfo.value.code == "validation_rejects"

    def test_real_validator_accepts_unknown_program_name(self) -> None:
        """Pin REAL behavior for the known dispute.

        V4 compile does NOT check program names against ``ProgramName``.
        An unknown program still validates ``ok`` -- rejection tests MUST
        use unknown executors.
        """
        document = {
            "schema": "confflow.workflow.v4",
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "steps": [
                {
                    "id": "plain",
                    "executor": "calculation",
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                    "calculation": {
                        "program": "no-such-program",
                        "native": {"keyword": "B3LYP"},
                        "recovery": {"profile": "none"},
                    },
                }
            ],
        }
        report = validate_workflow_bytes(_jcs_bytes(document))
        assert report.ok is True, (
            "real V4 validator accepts unknown program names; " f"diagnostics: {report.diagnostics}"
        )


# ---------------------------------------------------------------------------
# 3. Fake full TSPES chain (20 TS -> IRC -> 40 endpoints -> opt/freq -> SP ->
#    REAL analysis -> 20 ReactionGroups -> REAL manifest) + self-consistency.
# ---------------------------------------------------------------------------
def _fake_ts_subjects(count: int = N_TS) -> list[dict[str, str]]:
    return [
        {
            "ts": f"ts{i:02d}",
            "forward": f"ts{i:02d}:endpoint:forward:0",
            "reverse": f"ts{i:02d}:endpoint:reverse:0",
            "group_key": f"rxn-{i:02d}",
        }
        for i in range(count)
    ]


def _fake_endpoint_ids(subjects: list[dict[str, str]]) -> list[str]:
    endpoints: list[str] = []
    for entry in subjects:
        endpoints.extend([entry["forward"], entry["reverse"]])
    return endpoints


def _deterministic_energies(subjects: list[dict[str, str]]) -> dict[str, tuple[float, float]]:
    """FAKE native energies: deterministic (electronic, gibbs) per subject."""
    energies: dict[str, tuple[float, float]] = {}
    for index, entry in enumerate(subjects):
        ts_e = -76.0 - index * 0.001
        energies[entry["ts"]] = (ts_e, ts_e + 0.1)
        fwd_e = ts_e - 0.05 - index * 0.0001
        energies[entry["forward"]] = (fwd_e, fwd_e + 0.1)
        rev_e = ts_e - 0.03 - index * 0.0001
        energies[entry["reverse"]] = (rev_e, rev_e + 0.1)
    return energies


def _lookup_for_group(
    entry: dict[str, str], energies: dict[str, tuple[float, float]]
) -> dict[str, ResultSet]:
    lookup: dict[str, ResultSet] = {}
    for node in ("ts", "forward", "reverse"):
        subject = entry[node]
        electronic, gibbs = energies[subject]
        lookup[subject] = ResultSet.of(
            ScientificResult(
                kind="energy",
                value=electronic,
                unit=Unit.HARTREE,
                subject_structure_id=subject,
                source_step_id="endpoint_sp",
            ),
            ScientificResult(
                kind="gibbs_energy",
                value=gibbs,
                unit=Unit.HARTREE,
                subject_structure_id=subject,
                source_step_id="endpoint_sp",
            ),
        )
    return lookup


def _run_real_analysis_for_group(
    entry: dict[str, str], energies: dict[str, tuple[float, float]]
) -> dict[str, Any]:
    """Run the REAL ``reaction_profile`` analysis for one group (fail-closed)."""
    group = ReactionNodeGroup(
        group_key=entry["group_key"],
        ts_structure_id=entry["ts"],
        forward_structure_id=entry["forward"],
        reverse_structure_id=entry["reverse"],
    )
    model = EnergyModel(
        mode="direct",
        electronic_selector="energy",
        correction_selector="gibbs_correction",
        fallback="none",
    )
    try:
        lookup = _lookup_for_group(entry, energies)
    except KeyError as exc:
        raise E2EContractError(
            "missing_analysis_result",
            f"group {entry['group_key']!r} has no source results for {exc}",
        ) from exc
    analysis = assemble_reaction_result(group, model, lookup, analysis_step_id="reaction_profile")
    if not analysis.ok:
        raise E2EContractError(
            "missing_analysis_result",
            f"group {entry['group_key']!r} failed closed: "
            f"{[(d.code, d.message) for d in analysis.diagnostics]}",
        )
    by_kind = {}
    for result in analysis.results:
        by_kind.setdefault(result.kind, []).append(result)
    assert set(by_kind) == {
        "barrier_forward_endpoint",
        "barrier_reverse_endpoint",
        "endpoint_gibbs_delta",
        "endpoint_energy_delta",
        "reaction_profile",
    }, set(by_kind)
    return {
        "group_key": entry["group_key"],
        "ts_structure_id": entry["ts"],
        "forward_structure_id": entry["forward"],
        "reverse_structure_id": entry["reverse"],
        "results": [result.to_dict() for result in analysis.results],
        "source_result_ids": [result.value_digest for result in analysis.results],
    }


def _build_manifest_from_chain(
    *,
    run_id: str,
    definition_digest: str,
    producer_version: str,
    producer_commit: Any = None,
    producer_dirty: Any = None,
) -> tuple[dict[str, Any], dict[str, bytes], set[str], set[str]]:
    """Fake native chain + REAL analysis + REAL manifest builder.

    Returns ``(manifest, artifact_store, produced_structure_ids,
    produced_result_ids)`` for self-consistency verification.
    """
    subjects = _fake_ts_subjects()
    endpoints = _fake_endpoint_ids(subjects)
    assert len(subjects) == N_TS
    assert len(endpoints) == N_ENDPOINTS
    energies = _deterministic_energies(subjects)

    seen: set[str] = set()
    analyses: list[dict[str, Any]] = []
    for entry in subjects:
        if entry["group_key"] in seen:
            raise E2EContractError("ambiguous_group", f"group {entry['group_key']!r} claimed twice")
        seen.add(entry["group_key"])
        analyses.append(_run_real_analysis_for_group(entry, energies))
    assert len(analyses) == N_GROUPS

    produced_structure_ids = {
        entry[node] for entry in subjects for node in ("ts", "forward", "reverse")
    }
    produced_result_ids = {rid for analysis in analyses for rid in analysis["source_result_ids"]}

    artifact_store: dict[str, bytes] = {}
    artifacts: list[dict[str, str]] = []
    for analysis in analyses:
        summary = {
            "group_key": analysis["group_key"],
            "barriers": {
                result["kind"]: result["value"]
                for result in analysis["results"]
                if result["kind"].startswith("barrier_")
            },
        }
        payload = canonical_json_bytes(summary)
        locator = f"artifacts/{analysis['group_key']}/reaction_profile.json"
        artifact_store[locator] = payload
        artifacts.append(
            {
                "role": "reaction_profile",
                "checksum": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "locator": locator,
            }
        )

    steps = [
        {"id": "ts", "status": "completed", "counts": {"completed": N_TS, "failed": 0}},
        {"id": "ts_freq", "status": "completed", "counts": {"completed": N_TS, "failed": 0}},
        {"id": "ts_sp", "status": "completed", "counts": {"completed": N_TS, "failed": 0}},
        {"id": "irc", "status": "completed", "counts": {"completed": N_TS, "failed": 0}},
        {
            "id": "endpoint_opt",
            "status": "completed",
            "counts": {"completed": N_ENDPOINTS, "failed": 0},
        },
        {
            "id": "endpoint_freq",
            "status": "completed",
            "counts": {"completed": N_ENDPOINTS, "failed": 0},
        },
        {
            "id": "endpoint_sp",
            "status": "completed",
            "counts": {"completed": N_ENDPOINTS, "failed": 0},
        },
        {
            "id": "reaction_profile",
            "status": "completed",
            "counts": {"completed": N_GROUPS, "failed": 0},
        },
    ]
    manifest = build_run_result_manifest(
        run_id=run_id,
        status="completed",
        definition_digest=definition_digest,
        producer_version=producer_version,
        producer_commit=producer_commit,
        producer_dirty=producer_dirty,
        steps=steps,
        analyses=analyses,
        artifacts=artifacts,
    )
    return manifest, artifact_store, produced_structure_ids, produced_result_ids


def _verify_manifest_self_consistency(
    manifest: dict[str, Any],
    artifact_store: dict[str, bytes],
    produced_structure_ids: set[str],
    produced_result_ids: set[str],
    *,
    contract_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if manifest.get("content_schema") != RESULT_MANIFEST_SCHEMA_V1:
        raise E2EContractError("schema_mismatch", "manifest has the wrong content_schema")
    for key in (
        "run_id",
        "status",
        "definition_digest",
        "provenance",
        "steps",
        "analyses",
        "artifacts",
    ):
        if key not in manifest:
            raise E2EContractError("malformed_manifest", f"manifest is missing {key!r}")
    if contract_envelope is not None:
        expected = contract_envelope.get("producer", {})
        actual = manifest.get("provenance", {})
        if actual.get("package") != expected.get("package") or actual.get(
            "version"
        ) != expected.get("version"):
            raise E2EContractError(
                "result_producer_mismatch",
                "manifest provenance does not match the contract producer",
            )
    seen_groups: set[str] = set()
    for analysis in manifest["analyses"]:
        for key in (
            "group_key",
            "ts_structure_id",
            "forward_structure_id",
            "reverse_structure_id",
            "results",
            "source_result_ids",
        ):
            if key not in analysis:
                raise E2EContractError("malformed_manifest", f"analysis entry is missing {key!r}")
        group = analysis["group_key"]
        if group in seen_groups:
            raise E2EContractError("ambiguous_group", f"group {group!r} is claimed by two analyses")
        seen_groups.add(group)
        for ref in (
            analysis["ts_structure_id"],
            analysis["forward_structure_id"],
            analysis["reverse_structure_id"],
        ):
            if ref not in produced_structure_ids:
                raise E2EContractError(
                    "manifest_mismatch", f"analysis ref {ref!r} was never produced"
                )
        if not analysis["source_result_ids"]:
            raise E2EContractError(
                "missing_analysis_result", f"group {group!r} cites no source result ids"
            )
        for source in analysis["source_result_ids"]:
            if source not in produced_result_ids:
                raise E2EContractError(
                    "missing_analysis_result",
                    f"source result {source!r} for group {group!r} was never produced",
                )
    for artifact in manifest["artifacts"]:
        for key in ("role", "checksum", "locator"):
            if key not in artifact:
                raise E2EContractError("malformed_manifest", f"artifact entry is missing {key!r}")
        payload = artifact_store.get(artifact["locator"])
        if payload is None:
            raise E2EContractError(
                "manifest_mismatch", f"artifact {artifact['locator']!r} has no bytes"
            )
        if "sha256:" + hashlib.sha256(payload).hexdigest() != artifact["checksum"]:
            raise E2EContractError(
                "manifest_mismatch",
                f"artifact {artifact['locator']!r} checksum does not match its bytes",
            )
    return manifest


class TestFakeTspesChainToManifest:
    def _definition_digest(self, real_contract_bytes: bytes) -> str:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        workflow_bytes = JobdeskV4ConsumerDouble.serialize_workflow(
            JobdeskV4ConsumerDouble.recipe_document(contract, "tspes")
        )
        report = validate_workflow_bytes(workflow_bytes)
        _raise_for_report(report)
        assert report.definition_digest is not None
        return report.definition_digest

    def test_counts_20_40_20(self, real_contract_bytes: bytes) -> None:
        manifest, _, _, _ = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest=self._definition_digest(real_contract_bytes),
            producer_version=E2E_PRODUCER_VERSION,
        )
        assert len(manifest["analyses"]) == N_GROUPS
        assert len(manifest["artifacts"]) == N_GROUPS
        counts = {step["id"]: step["counts"]["completed"] for step in manifest["steps"]}
        assert counts == {
            "ts": 20,
            "ts_freq": 20,
            "ts_sp": 20,
            "irc": 20,
            "endpoint_opt": 40,
            "endpoint_freq": 40,
            "endpoint_sp": 40,
            "reaction_profile": 20,
        }

    def test_real_analysis_groups_and_manifest_shape(self, real_contract_bytes: bytes) -> None:
        manifest, _, _, _ = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest=self._definition_digest(real_contract_bytes),
            producer_version=E2E_PRODUCER_VERSION,
        )
        assert manifest["content_schema"] == RESULT_MANIFEST_SCHEMA_V1
        assert manifest["content_schema"] == RESULT_MANIFEST_SCHEMA
        assert manifest["provenance"]["package"] == "confflow"
        assert manifest["provenance"]["version"] == E2E_PRODUCER_VERSION
        assert [a["group_key"] for a in manifest["analyses"]] == [f"rxn-{i:02d}" for i in range(20)]
        for analysis in manifest["analyses"]:
            assert len(analysis["results"]) == 5  # REAL analysis: 2 barriers + 2 deltas + profile
            assert len(analysis["source_result_ids"]) == 5

    def test_manifest_self_consistency(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        manifest, store, structures, results = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest=self._definition_digest(real_contract_bytes),
            producer_version=E2E_PRODUCER_VERSION,
        )
        _verify_manifest_self_consistency(
            manifest, store, structures, results, contract_envelope=envelope
        )

    def test_manifest_producer_matches_contract(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        manifest, store, structures, results = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest=self._definition_digest(real_contract_bytes),
            producer_version=envelope["producer"]["version"],
        )
        _verify_manifest_self_consistency(
            manifest, store, structures, results, contract_envelope=envelope
        )


# ---------------------------------------------------------------------------
# 4. Real-vs-double inventory (machine-readable).
# ---------------------------------------------------------------------------
class TestRealVsDoubleInventoryE2E:
    def test_inventory(self, real_contract_bytes: bytes) -> None:
        assert real_contract_bytes  # every test above consumed these exact bytes
        assert USING_REAL_PRODUCER and generate_contract_bytes is not None
        assert USING_REAL_VALIDATOR and validate_workflow_bytes is not None
        assert USING_REAL_ANALYSIS and assemble_reaction_result is not None
        assert USING_REAL_MANIFEST_BUILDER and build_run_result_manifest is not None
        # Doubled/faked by design (see module notice):
        assert USING_JOBDESK_CONSUMER_DOUBLE
        assert USING_FAKE_NATIVE_EXECUTION
        assert compile_workflow is not None  # real compiler inside the loop


# ---------------------------------------------------------------------------
# 5. Failure matrix: structured failures, no silent fallback.
# ---------------------------------------------------------------------------
class TestFailureMatrixE2E:
    def test_producer_unavailable_seam(self) -> None:
        try:
            import confflow.producer as _producer

            _producer.nonexistent_entrypoint_xyz  # noqa: B018 - presence probe
        except (ImportError, AttributeError):
            pass
        else:  # pragma: no cover - the seam must stay missing
            raise AssertionError("expected the bogus producer entrypoint to be absent")
        # The real seam IS available here; refusing to silently double it:
        assert generate_contract_bytes is not None

    def test_old_no_v4_contract(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        envelope["content_schema"] = "confflow.configuration-contract.v2"
        with pytest.raises(E2EContractError) as excinfo:
            JobdeskV4ConsumerDouble.parse(_jcs_bytes(envelope))
        assert excinfo.value.code == "old_contract_no_v4"

    def test_bad_digest(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        envelope["editor_manifest_sha256"] = "f" * 64
        with pytest.raises(E2EContractError) as excinfo:
            JobdeskV4ConsumerDouble.parse(_jcs_bytes(envelope))
        assert excinfo.value.code == "bad_digest"

    def test_schema_mismatch(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        envelope["content_schema"] = "confflow.configuration-contract.v9"
        with pytest.raises(E2EContractError) as excinfo:
            JobdeskV4ConsumerDouble.parse(_jcs_bytes(envelope))
        assert excinfo.value.code == "schema_mismatch"

    def test_schema_mismatch_non_json(self) -> None:
        with pytest.raises(E2EContractError) as excinfo:
            JobdeskV4ConsumerDouble.parse(b"\xff\xfe not utf-8 \x00")
        assert excinfo.value.code == "schema_mismatch"

    def test_manifest_mismatch(self, real_contract_bytes: bytes) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        workflow_bytes = JobdeskV4ConsumerDouble.serialize_workflow(
            JobdeskV4ConsumerDouble.recipe_document(contract, "tspes")
        )
        report = validate_workflow_bytes(workflow_bytes)
        _raise_for_report(report)
        assert report.definition_digest is not None
        manifest, store, structures, results = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest=report.definition_digest,
            producer_version=E2E_PRODUCER_VERSION,
        )
        manifest["analyses"][0]["forward_structure_id"] = "ts99:endpoint:forward:0"
        with pytest.raises(E2EContractError) as excinfo:
            _verify_manifest_self_consistency(manifest, store, structures, results)
        assert excinfo.value.code == "manifest_mismatch"

    def test_artifact_checksum_mismatch(self, real_contract_bytes: bytes) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        workflow_bytes = JobdeskV4ConsumerDouble.serialize_workflow(
            JobdeskV4ConsumerDouble.recipe_document(contract, "tspes")
        )
        report = validate_workflow_bytes(workflow_bytes)
        _raise_for_report(report)
        assert report.definition_digest is not None
        manifest, store, structures, results = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest=report.definition_digest,
            producer_version=E2E_PRODUCER_VERSION,
        )
        first_locator = manifest["artifacts"][0]["locator"]
        store[first_locator] = b"tampered-bytes"
        with pytest.raises(E2EContractError) as excinfo:
            _verify_manifest_self_consistency(manifest, store, structures, results)
        assert excinfo.value.code == "manifest_mismatch"

    def test_recipe_mismatch(self, real_contract_bytes: bytes) -> None:
        contract = JobdeskV4ConsumerDouble.parse(real_contract_bytes)
        with pytest.raises(E2EContractError) as excinfo:
            JobdeskV4ConsumerDouble.recipe_document(contract, "no-such-recipe")
        assert excinfo.value.code == "recipe_mismatch"

    def test_validation_rejects(self) -> None:
        document = {
            "schema": "confflow.workflow.v4",
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "steps": [
                {
                    "id": "evil",
                    "executor": "no_such_executor",
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                    "calculation": {
                        "program": "orca",
                        "native": {"keyword": "B3LYP"},
                        "recovery": {"profile": "none"},
                    },
                }
            ],
        }
        report = validate_workflow_bytes(_jcs_bytes(document))
        assert report.ok is False
        with pytest.raises(E2EContractError) as excinfo:
            _raise_for_report(report)
        assert excinfo.value.code == "validation_rejects"

    def test_validated_not_submitted(self, real_contract_bytes: bytes) -> None:
        receipt = {"workflow_sha256": "sha256:" + "0" * 64}
        with pytest.raises(E2EContractError) as excinfo:
            _submit_validated_workflow(receipt, b'{"schema": "confflow.workflow.v4"}')
        assert excinfo.value.code == "validated_not_submitted"

    def test_result_producer_mismatch(self, real_contract_bytes: bytes) -> None:
        envelope = json.loads(real_contract_bytes.decode("utf-8"))
        manifest, store, structures, results = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest="sha256:" + "0" * 64,
            producer_version="9.9-evil",
        )
        with pytest.raises(E2EContractError) as excinfo:
            _verify_manifest_self_consistency(
                manifest, store, structures, results, contract_envelope=envelope
            )
        assert excinfo.value.code == "result_producer_mismatch"

    def test_malformed_manifest(self, real_contract_bytes: bytes) -> None:
        manifest, store, structures, results = _build_manifest_from_chain(
            run_id="run-v46-e2e",
            definition_digest="sha256:" + "0" * 64,
            producer_version=E2E_PRODUCER_VERSION,
        )
        del manifest["analyses"]
        with pytest.raises(E2EContractError) as excinfo:
            _verify_manifest_self_consistency(manifest, store, structures, results)
        assert excinfo.value.code == "malformed_manifest"

    def test_missing_analysis_result_real_fail_closed(self) -> None:
        """REAL analysis with an incomplete lookup fails closed (no results)."""
        entry = _fake_ts_subjects(count=1)[0]
        group = ReactionNodeGroup(
            group_key=entry["group_key"],
            ts_structure_id=entry["ts"],
            forward_structure_id=entry["forward"],
            reverse_structure_id=entry["reverse"],
        )
        model = EnergyModel(
            mode="direct",
            electronic_selector="energy",
            correction_selector="gibbs_correction",
            fallback="none",
        )
        analysis = assemble_reaction_result(group, model, {}, analysis_step_id="reaction_profile")
        assert analysis.ok is False
        assert analysis.results == ()
        assert any(d.code == "analysis_energy_missing" for d in analysis.diagnostics)
        with pytest.raises(E2EContractError) as excinfo:
            _run_real_analysis_for_group(entry, {})
        assert excinfo.value.code == "missing_analysis_result"

    def test_ambiguous_group(self) -> None:
        seen: set[str] = set()
        with pytest.raises(E2EContractError) as excinfo:
            for entry in [*(_fake_ts_subjects(count=1)), *(_fake_ts_subjects(count=1))]:
                if entry["group_key"] in seen:
                    raise E2EContractError(
                        "ambiguous_group", f"group {entry['group_key']!r} claimed twice"
                    )
                seen.add(entry["group_key"])
        assert excinfo.value.code == "ambiguous_group"

    def test_contract_refresh_race(self, real_contract_bytes: bytes) -> None:
        first = json.loads(real_contract_bytes.decode("utf-8"))
        refreshed_raw = generate_contract_bytes(producer_version=E2E_PRODUCER_VERSION + "+refresh1")
        second = json.loads(refreshed_raw.decode("utf-8"))
        assert first["contract_digest"] != second["contract_digest"]
        with pytest.raises(E2EContractError) as excinfo:
            _check_contract_fresh(first["contract_digest"], second)
        assert excinfo.value.code == "contract_refresh_race"
        _check_contract_fresh(second["contract_digest"], second)  # fresh read passes
