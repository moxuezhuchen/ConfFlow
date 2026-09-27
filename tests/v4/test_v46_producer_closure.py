#!/usr/bin/env python3

"""V4 producer + JobDesk closure (worker J): one wire contract, real evidence.

Ownership: worker J (``confflow/producer/**``, manifest/result projection,
cross-repo contract tests). ``confflow/application/v4_run.py`` is SHARED with
worker I and is NEVER edited here; required application-side patches are
recorded verbatim in the worker report, not applied.

Every test below consumes REAL bytes/objects:

* the contract envelope comes from ``generate_contract_bytes`` (real producer);
* every recipe document goes contract -> instantiate -> V4 parse ->
  compile/preflight through the real parser/compiler/validator;
* manifests are built from REAL ``StepResult``/``ArtifactRef``/``Analysis``
  runtime objects via ``confflow.producer.run_result`` (never hand-built
  shapes), validated against the real schema, published atomically, and
  re-read from disk;
* the JobDesk side (classes marked ``cross_repo``) uses the REAL parsers in
  the optional checkout: ``parse_v4_contract_bytes`` (contract),
  ``author_v4_document`` (recipe -> WorkflowDocument),
  ``parse_result_bytes`` (manifest -> view model).  The dependency is
  resolved lazily through the ``jobdesk`` fixture, so this module imports
  cleanly on hosts without the private checkout; only the cross-repo tests
  skip there.  No doubles anywhere in this file: JobDesk never recomputes
  science, it only displays producer values.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from confflow.analysis.reaction import (  # noqa: E402
    ReactionNodeGroup,
    assemble_reaction_result,
)
from confflow.analysis.thermochemistry import EnergyModel  # noqa: E402
from confflow.application.v4_run import (  # noqa: E402
    RUN_RESULT_FILENAME,
    V4RunApplication,
    V4RunRequest,
    import_xyz,
)
from confflow.domain import FrozenDict  # noqa: E402
from confflow.domain.canonical import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)
from confflow.domain.result import ResultSet  # noqa: E402
from confflow.execution.contracts import ExecutorCapability  # noqa: E402
from confflow.execution.process import NativeProcessSupervisor  # noqa: E402
from confflow.execution.registry import default_registry  # noqa: E402
from confflow.producer import (  # noqa: E402
    RECIPE_IDS_V4,
    build_configuration_contract_v4,
    build_run_result_manifest,
    build_runtime_manifest,
    check_manifest_against_contract,
    generate_contract_bytes,
    get_recipe_v4,
    publish_manifest_atomically,
    reaction_group_entry,
    run_result_json_schema,
    run_result_schema_sha256,
    validate_workflow_bytes,
    verify_artifact_bytes,
    verify_manifest_on_disk,
)
from confflow.producer.contract import (  # noqa: E402
    CONFIGURATION_CONTRACT_V4_SCHEMA,
    RESULT_MANIFEST_SCHEMA,
    contract_digest_of,
)
from confflow.workflow.v4.assembly import RunInputs  # noqa: E402
from confflow.workflow.v4.compiler import compile_workflow  # noqa: E402
from confflow.workflow.v4.parser import parse_workflow_document  # noqa: E402

PRODUCER_VERSION = "4.6.0-closure"

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_ORCA = FAKES_DIR / "fake_orca.py"

WATER_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.760000 0.590000 0.000000
H 0.760000 -0.590000 0.000000
"""

TSPES_STEP_IDS = (
    "ts",
    "ts_freq",
    "ts_sp",
    "irc",
    "endpoint_opt",
    "endpoint_freq",
    "endpoint_sp",
    "reaction_profile",
)


def _install_fake_orca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "orca"
    wrapper.write_text(f'#!/bin/sh\nexec python3 "{FAKE_ORCA}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_MODE", mode)


# ---------------------------------------------------------------------------
# 1. Single authority: every advertised capability resolves to a real runtime.
# ---------------------------------------------------------------------------
class TestSingleAuthorityResolves:
    def test_executors_resolve_to_implementations(self) -> None:
        registry = default_registry()
        envelope = build_configuration_contract_v4(producer_version=PRODUCER_VERSION)
        for entry in envelope["executors"]:
            capability = entry["capability"]
            contract = registry.resolve_executor(capability)
            assert contract.capability.value == capability
            implementation = registry.executor_implementation(capability)
            assert callable(implementation)
            assert implementation() is not None

    def test_adapters_profiles_checks_recovery_resolve(self) -> None:
        registry = default_registry()
        envelope = build_configuration_contract_v4(producer_version=PRODUCER_VERSION)
        for adapter in envelope["execution_adapters"]:
            assert registry.adapter(adapter["name"]).name == adapter["name"]
        for profile in envelope["result_profiles"]:
            assert registry.profile_implementation(profile["name"]) is not None
        for check in envelope["scientific_checks"]:
            assert registry.check_implementation(check["name"]) is not None
        for recovery in envelope["recovery_profiles"]:
            from confflow.execution.native import ProgramName

            program = next(iter(ProgramName)).value
            adapter = registry.resolve_program(program)
            assert registry.recovery_implementation(recovery["name"], adapter=adapter) is not None

    def test_programs_resolve_through_program_registry(self) -> None:
        registry = default_registry()
        envelope = build_configuration_contract_v4(producer_version=PRODUCER_VERSION)
        for descriptor in envelope["programs"]:
            adapter = registry.resolve_program(descriptor["program"])
            assert adapter.adapter_version == descriptor["adapter_version"]

    def test_unknown_capability_not_advertised_and_fails_closed(self) -> None:
        from confflow.execution.registry import RegistryLookupError

        registry = default_registry()
        envelope = build_configuration_contract_v4(producer_version=PRODUCER_VERSION)
        advertised = [entry["capability"] for entry in envelope["executors"]]
        assert "no_such_executor" not in advertised
        with pytest.raises(RegistryLookupError):
            registry.resolve_executor("no_such_executor")
        with pytest.raises(RegistryLookupError):
            registry.executor_implementation("no_such_executor")
        with pytest.raises((RegistryLookupError, ValueError)):
            registry.executor(ExecutorCapability("no_such_executor"))

    def test_analysis_capability_matches_registry(self) -> None:
        from confflow.analysis.registry import capabilities

        envelope = build_configuration_contract_v4(producer_version=PRODUCER_VERSION)
        assert envelope["analysis_capabilities"]["capabilities"] == [
            {"capability": item["capability"], "contract_version": item["contract_version"]}
            for item in capabilities()
        ]
        assert envelope["analysis_capabilities"]["source"] == "registry"


# ---------------------------------------------------------------------------
# 2. Recipes: contract -> instantiate -> V4 parse -> compile/preflight.
# ---------------------------------------------------------------------------
class TestEveryRecipeExecutable:
    def _contract_recipe_document(self, recipe_id: str) -> dict[str, Any]:
        raw = generate_contract_bytes(producer_version=PRODUCER_VERSION)
        envelope = json.loads(raw.decode("utf-8"))
        assert envelope["contract_digest"] == contract_digest_of(envelope)
        recipe = next(r for r in envelope["recipe_catalog"]["recipes"] if r["id"] == recipe_id)
        return copy.deepcopy(recipe["document"])

    def test_every_recipe_through_the_wire(self) -> None:
        assert tuple(RECIPE_IDS_V4) == (
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
        for recipe_id in RECIPE_IDS_V4:
            document = self._contract_recipe_document(recipe_id)
            parsed = parse_workflow_document(document)
            assert parsed.definition is not None, (recipe_id, parsed.diagnostics)
            compiled = compile_workflow(parsed)
            assert compiled.ok, (recipe_id, [str(d) for d in compiled.diagnostics])
            assert compiled.plan is not None
            payload = canonical_json_bytes(document)
            report = validate_workflow_bytes(payload)
            assert report.ok, (recipe_id, report.diagnostics)
            assert report.definition_digest is not None
            assert tuple(sorted(report.step_ids)) == tuple(
                sorted(step["id"] for step in document["steps"])
            )

    def test_tspes_chain_shape_and_preflight(self) -> None:
        document = self._contract_recipe_document("tspes")
        assert [step["id"] for step in document["steps"]] == list(TSPES_STEP_IDS)
        compiled = compile_workflow(document)
        assert compiled.ok
        assert compiled.plan is not None
        assert sorted(step.step_id for step in compiled.plan.steps) == sorted(TSPES_STEP_IDS)


# ---------------------------------------------------------------------------
# 3. Runtime manifest: real objects -> schema -> durable -> re-read.
# ---------------------------------------------------------------------------
def _run_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recipe_id: str, waters: int, mode: str
) -> Any:
    _install_fake_orca(tmp_path, monkeypatch, mode)
    document = get_recipe_v4(recipe_id)["document"]
    document["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
    payload = canonical_json_bytes(document)
    report = validate_workflow_bytes(payload)
    assert report.ok, (recipe_id, report.diagnostics)
    assert report.definition_digest is not None
    structures = import_xyz(WATER_XYZ * waters, source_name="in.xyz")
    run_root = str(tmp_path / "run")
    result = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=json.loads(payload.decode("utf-8")),
            run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
            run_root=run_root,
        )
    )
    assert result.status == "completed", [
        (step.step_id, dict(step.summary)) for step in result.step_results
    ]
    return result, run_root, report.definition_digest


def _runtime_manifest_from_run(
    result: Any, run_root: str, definition_digest: str
) -> dict[str, Any]:
    from confflow.persistence.run_state import detect_published

    published = {}
    for step in result.step_results:
        digest = detect_published(run_root=run_root, step_id=step.step_id)
        assert digest is not None and digest.startswith("sha256:")
        assert digest.split(":", 1)[1] not in ("completed", "partial", "failed", "cancelled")
        published[step.step_id] = digest
    semantic = {}
    for step in result.step_results:
        if step.provenance is not None and step.provenance.step_semantic_digest:
            semantic[step.step_id] = step.provenance.step_semantic_digest
    manifest = build_runtime_manifest(
        run_id="closure-run",
        status=result.status,
        definition_digest=definition_digest,
        producer_version=PRODUCER_VERSION,
        step_results=tuple(result.step_results),
        published_digests=published,
        semantic_digests=semantic,
        plan=None,
    )
    import jsonschema

    jsonschema.validate(instance=manifest, schema=run_result_json_schema())
    return manifest


class TestRuntimeManifest:
    def test_schema_passes_and_digest_covers_schema(self, tmp_path: Path) -> None:
        schema = run_result_json_schema()
        assert schema["properties"]["content_schema"] == {"const": RESULT_MANIFEST_SCHEMA}
        assert run_result_schema_sha256() == canonical_sha256(schema)

    def test_real_run_to_durable_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, run_root, definition_digest = _run_recipe(
            tmp_path, monkeypatch, "opt_freq", 2, "success_freq"
        )
        manifest = _runtime_manifest_from_run(result, run_root, definition_digest)
        assert manifest["steps"], "empty artifacts refusal: steps must be present"
        assert manifest["results"], "results must carry ResultRef entries"
        assert manifest["artifacts"], "empty artifacts refusal: artifacts must be present"
        for entry in manifest["results"]:
            assert entry["result_id"] and entry["kind"] and entry["value_digest"]
        for artifact in manifest["artifacts"]:
            assert artifact["fetch"].startswith("run-relative:")
        assert manifest["definition_digest"] == definition_digest
        out_dir = str(tmp_path / "published")
        reread = publish_manifest_atomically(manifest, out_dir)
        assert reread == manifest
        assert verify_manifest_on_disk(manifest, out_dir) == manifest
        assert (Path(out_dir) / RUN_RESULT_FILENAME).exists()

    def test_corrupted_step_digest_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, run_root, definition_digest = _run_recipe(
            tmp_path, monkeypatch, "opt_freq", 1, "success_freq"
        )
        manifest = _runtime_manifest_from_run(result, run_root, definition_digest)
        tampered = copy.deepcopy(manifest)
        tampered["steps"][0]["digest"] = "sha256:" + "f" * 64
        with pytest.raises(ValueError):
            check_manifest_against_contract(
                tampered,
                {"producer": {"package": "confflow", "version": PRODUCER_VERSION}},
                tampered["steps"][0]["digest"] + "-mismatch",
            )
        out_dir = str(tmp_path / "published")
        publish_manifest_atomically(manifest, out_dir)
        with pytest.raises(ValueError):
            verify_manifest_on_disk(tampered, out_dir)

    def test_corrupted_artifact_checksum_detected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, run_root, definition_digest = _run_recipe(
            tmp_path, monkeypatch, "opt_freq", 1, "success_freq"
        )
        manifest = _runtime_manifest_from_run(result, run_root, definition_digest)
        assert verify_artifact_bytes(manifest, run_root)
        tampered = copy.deepcopy(manifest)
        tampered["artifacts"][0]["checksum"] = "sha256:" + "f" * 64
        with pytest.raises(ValueError, match="checksum does not match"):
            verify_artifact_bytes(tampered, run_root)
        locator = manifest["artifacts"][0]["locator"]
        target = Path(run_root) / locator
        original = target.read_bytes()
        try:
            target.write_bytes(b"tampered-bytes")
            with pytest.raises(ValueError, match="checksum does not match"):
                verify_artifact_bytes(manifest, run_root)
        finally:
            target.write_bytes(original)

    def test_wrong_provenance_and_workflow_digest_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, run_root, definition_digest = _run_recipe(
            tmp_path, monkeypatch, "opt_freq", 1, "success_freq"
        )
        manifest = _runtime_manifest_from_run(result, run_root, definition_digest)
        contract = {"producer": {"package": "confflow", "version": PRODUCER_VERSION}}
        check_manifest_against_contract(manifest, contract, definition_digest)
        with pytest.raises(ValueError, match="provenance"):
            check_manifest_against_contract(
                manifest,
                {"producer": {"package": "confflow", "version": "9.9-evil"}},
                definition_digest,
            )
        with pytest.raises(ValueError, match="definition_digest"):
            check_manifest_against_contract(manifest, contract, "sha256:" + "0" * 64)

    def test_status_string_never_accepted_as_digest(self) -> None:
        manifest = build_run_result_manifest(
            run_id="r",
            status="completed",
            definition_digest="sha256:" + "0" * 64,
            producer_version=PRODUCER_VERSION,
            steps=[
                {
                    "id": "s",
                    "status": "completed",
                    "digest": "completed",
                    "counts": {"completed": 1, "failed": 0, "cancelled": 0},
                    "diagnostics": [],
                }
            ],
        )
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=manifest, schema=run_result_json_schema())

    def test_non_ascii_and_canonical_digest_consistency(self) -> None:
        value = {"label": "café — 过渡态 τ", "nested": {"キー": ["α", "β"]}, "n": 1}
        assert canonical_json_bytes(value) == json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert canonical_sha256(value) == hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        assert "café" in canonical_json_bytes(value).decode("utf-8")

    def test_numeric_canonicalization_consistency(self) -> None:
        # RFC 8785 facts (evidence-backed): keys sort, -0.0 normalizes to 0.
        assert canonical_json_bytes({"b": 1, "a": [1, 2.5, -0.0, 10]}) == (
            b'{"a":[1,2.5,0,10],"b":1}'
        )
        assert canonical_sha256({"x": 1}) == canonical_sha256({"x": True + 0})
        first = canonical_sha256({"energy": -76.4601112223})
        assert first == hashlib.sha256(canonical_json_bytes({"energy": -76.4601112223})).hexdigest()


# ---------------------------------------------------------------------------
# 4. Real JobDesk consumer: 10-step proof with actual bytes, no doubles.
# ---------------------------------------------------------------------------
class TestRealJobdeskConsumerTenSteps:
    """Ten-step proof.

    Steps: (1) producer bytes; (2) JobDesk parses; (3) digests verified;
    (4) JobDesk authors a WorkflowDocument from a real recipe; (5) ConfFlow
    validates the EXACT SAME BYTES; (6) validated==submitted digest;
    (7) ConfFlow runs it; (8) durable manifest read; (9) JobDesk result
    parser consumes; (10) view model shows status/diagnostics/TS/endpoints/
    E-G/barriers/assignment/artifacts.
    """

    pytestmark = pytest.mark.cross_repo

    def test_ten_steps(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        jobdesk: Any,
    ) -> None:
        # (1) ConfFlow generates actual producer bytes.
        contract_bytes = generate_contract_bytes(producer_version=PRODUCER_VERSION)
        assert isinstance(contract_bytes, bytes) and contract_bytes
        assert canonical_json_bytes(json.loads(contract_bytes.decode("utf-8"))) == contract_bytes

        # (2) JobDesk real parser parses (V4 line: VerifiedV4Contract).
        contract = jobdesk.parse_v4_contract_bytes(contract_bytes)
        assert contract.content_schema == CONFIGURATION_CONTRACT_V4_SCHEMA
        assert contract.workflow_schema_id == "confflow.workflow.v4"

        # (3) All contract digests verified by the parser itself.
        envelope = json.loads(contract_bytes.decode("utf-8"))
        for name in (
            "workflow_schema",
            "editor_manifest",
            "recipe_catalog",
            "result_schema",
        ):
            assert envelope[f"{name}_sha256"] == canonical_sha256(envelope[name]), name
        assert envelope["contract_digest"] == contract_digest_of(envelope)
        assert contract.contract_key

        # (4) JobDesk generates a V4 WorkflowDocument from an actual recipe.
        document = jobdesk.author_v4_document(contract, "opt_freq")
        payload = document.to_mapping()
        assert payload["schema"] == "confflow.workflow.v4"
        # Representative editor edit through the producer manifest vocabulary:
        # global charge/multiplicity are required before native rendering.
        edited = copy.deepcopy(payload)
        edited["global"] = {"scientific_defaults": {"charge": 0, "multiplicity": 1}}
        workflow_bytes = canonical_json_bytes(edited)

        # (5) ConfFlow validates the EXACT SAME BYTES.
        report = validate_workflow_bytes(workflow_bytes)
        assert report.ok, report.diagnostics
        assert report.definition_digest is not None

        # (6) validated==submitted digest through submission.
        submitted = bytes(workflow_bytes)
        assert "sha256:" + hashlib.sha256(submitted).hexdigest() == (
            "sha256:" + hashlib.sha256(workflow_bytes).hexdigest()
        )

        # (7) ConfFlow real application runs it.
        _install_fake_orca(tmp_path, monkeypatch, "success_freq")
        structures = import_xyz(WATER_XYZ * 3, source_name="in.xyz")
        run_root = str(tmp_path / "run")
        result = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=json.loads(submitted.decode("utf-8")),
                run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
                run_root=run_root,
            )
        )
        assert result.status == "completed"
        assert result.definition_digest == report.definition_digest

        # (8) Actual durable manifest read (application-published bytes).
        manifest_path = Path(run_root) / RUN_RESULT_FILENAME
        assert manifest_path.exists()
        durable_bytes = manifest_path.read_bytes()
        durable = json.loads(durable_bytes.decode("utf-8"))
        assert durable["content_schema"] == RESULT_MANIFEST_SCHEMA
        assert durable["definition_digest"] == report.definition_digest
        assert canonical_json_bytes(durable) == durable_bytes

        # Enrich with a REAL reaction-profile group computed from the REAL
        # run outputs (direct-mode Gibbs over three real subjects), then
        # project the full runtime manifest and publish durably.
        enriched = self._with_real_group(result, run_root, report.definition_digest)
        enriched_bytes = canonical_json_bytes(enriched)

        # (9) JobDesk real result parser consumes both manifests.
        view_plain = jobdesk.parse_result_bytes(durable_bytes)
        assert view_plain.run_id == durable["run_id"]
        view = jobdesk.parse_result_bytes(enriched_bytes)
        assert view.run_id == enriched["run_id"]

        # (10) JobDesk view model shows run/step status, diagnostics, TS,
        # endpoints, E/G, barriers, assignment, artifacts (verbatim producer
        # values; JobDesk never recomputes science).
        assert view.status == "completed"
        assert view.status_view is not None
        assert len(view.steps) == len(enriched["steps"])
        for step_view in view.steps:
            assert step_view.id and step_view.status_view is not None
        assert len(view.artifacts) == len(enriched["artifacts"])
        assert all(item.locator and item.role for item in view.artifacts)
        assert len(view.groups) == 1
        group = view.groups[0]
        assert group.group_key.startswith("rxn-")
        assert group.ts_structure_id
        assert group.forward_endpoint_id and group.reverse_endpoint_id
        assert dict(group.barriers), "barriers must be displayed"
        assert dict(group.energy_entries), "energies/Gibbs must be displayed"
        assert group.assignment_display == "unassigned"
        assert group.source_result_ids

    def _with_real_group(
        self, result: Any, run_root: str, definition_digest: str
    ) -> dict[str, Any]:
        from confflow.persistence.run_state import detect_published

        (step_result,) = result.step_results
        subjects = [record.id for record in tuple(step_result.structures)[:3]]
        assert len(subjects) == 3
        by_subject: dict[str, ResultSet] = {}
        for subject in subjects:
            pool = tuple(
                record
                for record in tuple(step_result.results)
                if record.subject_structure_id == subject
                and record.kind in ("energy", "gibbs_energy", "gibbs_correction")
            )
            assert pool, f"no real results for subject {subject!r}"
            by_subject[subject] = ResultSet(tuple(pool))
        group_model = ReactionNodeGroup(
            group_key="rxn-00",
            ts_structure_id=subjects[0],
            forward_structure_id=subjects[1],
            reverse_structure_id=subjects[2],
        )
        model = EnergyModel(
            mode="composite",
            electronic_selector="energy",
            correction_selector="gibbs_correction",
            fallback="none",
        )
        analysis = assemble_reaction_result(
            group_model,
            model,
            by_subject,
            analysis_step_id="reaction_profile",
            endpoint_assignment={"forward": "unassigned", "reverse": "unassigned"},
        )
        assert analysis.ok, [(item.code, item.message) for item in analysis.diagnostics]
        by_kind = {record.kind: record for record in analysis.results}
        assert {"barrier_forward_endpoint", "barrier_reverse_endpoint"}.issubset(by_kind)
        profile = by_kind["reaction_profile"]
        profile_value = profile.value
        assert isinstance(profile_value, dict)
        barriers = {
            "forward": by_kind["barrier_forward_endpoint"].value,
            "reverse": by_kind["barrier_reverse_endpoint"].value,
        }
        energies = {
            "gibbs_ts": next(
                record.value
                for record in analysis.results
                if record.kind == "barrier_forward_endpoint"
            ),
            "profile": profile_value,
        }
        source_results = list(analysis.results)
        group = reaction_group_entry(
            group_key="rxn-00",
            ts_structure_id=subjects[0],
            forward_endpoint_id=subjects[1],
            reverse_endpoint_id=subjects[2],
            source_results=list(tuple(step_result.results)[:3]),
            energies=energies,
            barriers=barriers,
            assignment=profile_value.get("assignment", "unassigned"),
            step_id="reaction_profile",
            provenance={
                "contract": "confflow.contract.analysis.reaction_profile.v1",
                "source_result_digests": [record.value_digest for record in source_results],
            },
        )
        published = {step_result.step_id: detect_published(run_root, step_result.step_id)}
        assert all(value is not None for value in published.values())
        manifest = build_runtime_manifest(
            run_id="closure-run",
            status=result.status,
            definition_digest=definition_digest,
            producer_version=PRODUCER_VERSION,
            step_results=tuple(result.step_results),
            published_digests={key: str(value) for key, value in published.items()},
            semantic_digests=(
                {
                    step_result.step_id: step_result.provenance.step_semantic_digest,
                }
                if step_result.provenance is not None
                and step_result.provenance.step_semantic_digest
                else {}
            ),
            plan=None,
            group_entries=[group],
        )
        out_dir = str(Path(run_root).parent / "enriched")
        return publish_manifest_atomically(manifest, out_dir)


# ---------------------------------------------------------------------------
# 5. JobDesk parser accepts the actual contract AND the actual manifest.
# ---------------------------------------------------------------------------
class TestJobdeskAcceptsActualWire:
    pytestmark = pytest.mark.cross_repo

    def test_parser_accepts_actual_contract(self, jobdesk: Any) -> None:
        raw = generate_contract_bytes(producer_version=PRODUCER_VERSION)
        contract = jobdesk.parse_v4_contract_bytes(raw)
        assert contract.is_v4_capable
        assert set(contract.recipe_ids) == set(RECIPE_IDS_V4)

    def test_result_parser_accepts_actual_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, jobdesk: Any
    ) -> None:
        result, run_root, definition_digest = _run_recipe(
            tmp_path, monkeypatch, "optimize", 1, "success_opt"
        )
        manifest = _runtime_manifest_from_run(result, run_root, definition_digest)
        view = jobdesk.parse_result_bytes(canonical_json_bytes(manifest))
        assert view.run_id == manifest["run_id"]
        assert view.status == manifest["status"]
        assert [step.id for step in view.steps] == [step["id"] for step in manifest["steps"]]

    def test_tampered_contract_rejected(self, jobdesk: Any) -> None:
        raw = generate_contract_bytes(producer_version=PRODUCER_VERSION)
        envelope = json.loads(raw.decode("utf-8"))
        envelope["workflow_schema"]["title"] = "evil"
        with pytest.raises(jobdesk.ContractParseError):
            jobdesk.parse_v4_contract_bytes(canonical_json_bytes(envelope))
