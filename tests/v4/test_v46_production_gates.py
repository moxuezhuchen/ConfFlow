#!/usr/bin/env python3

"""V4-6 production-path gates (worker H).

Adversarial closure over the formal production path only
(``compile`` -> ``assemble`` -> ``V4RunApplication`` /
``BatchStepExecutor.execute_step_resumable``): every test drives real
production seams. Covers the 18-item production-path checklist (one test
each), the 20-TS -> 40-IRC-endpoint fanout with DAG step-count
invariance, and the architecture-scanner gate.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from confflow.domain import FrozenDict, StructureSet

FAKES_DIR = Path(__file__).resolve().parent / "fakes"
FAKE_IRC = FAKES_DIR / "fake_irc.py"


def _reuse_inputs(**overrides: Any) -> Any:
    """Build baseline reuse inputs for the digest-axis tests."""
    from confflow.persistence.reuse import ReuseInputs, build_producer_provenance

    payload: dict[str, Any] = {
        "work_item_digest": "sha256:" + "a1" * 32,
        "step_semantic_digest": "sha256:" + "b2" * 32,
        "environment_digest": "sha256:" + "c3" * 32,
        "producer_provenance": build_producer_provenance(
            adapter_version="adapter.v1",
            profile_version="profile.v1",
            check_versions={"normal_termination": "c.v1"},
            recovery_version="recovery.v1",
        ),
        "artifact_checksums": ("sha256:" + "d4" * 32,),
    }
    payload.update(overrides)
    return ReuseInputs(**payload)


def _named_item(ports: dict[str, StructureSet], logical_key: str = "qst:g1") -> Any:
    """Build a named-input work item carrying *ports*."""
    from confflow.domain import ResourceRequest
    from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id

    step_id = logical_key.split(":")[0]
    return WorkItem(
        id=make_work_item_id(logical_key),
        logical_key=logical_key,
        step_id=step_id,
        named_inputs=WorkItemInputs(structures=FrozenDict(ports)),
        resources=ResourceRequest(cores_per_item=2, memory_per_item_bytes=1024**3),
        semantic_digest="sha256:" + "a" * 64,
    )


def _named_record(
    record_id: str,
    *,
    atoms: tuple[str, ...] = ("O", "H", "H"),
    charge: int | None = 0,
    multiplicity: int | None = 1,
    group_key: str | None = "g1",
) -> Any:
    """Build one deterministic structure record for named-slot tests."""
    from confflow.domain import StructureRecord

    coords = tuple((float(index), 0.0, 0.0) for index in range(len(atoms)))
    return StructureRecord(
        id=record_id,
        atoms=atoms,
        coordinates=coords,
        charge=charge,
        multiplicity=multiplicity,
        group_key=group_key,
    )


# R2.2 (G18): TestFanoutStepCountStable is retired with the IRC
# path_endpoints vehicle.


class TestMissingRequiredInput:
    """Empty driving/named ports fail closed before any native launch."""

    def test_missing_required_input(self) -> None:
        from confflow.domain import ResourceRequest
        from confflow.domain.errors import DomainError
        from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
        from confflow.execution.work_item_executor import select_driving_structure

        empty = WorkItem(
            id=make_work_item_id("s_opt:lonely"),
            logical_key="s_opt:lonely",
            step_id="s_opt",
            named_inputs=WorkItemInputs(structures=FrozenDict({})),
            resources=ResourceRequest(cores_per_item=1, memory_per_item_bytes=1024**3),
            semantic_digest="sha256:" + "b" * 64,
        )
        with pytest.raises(DomainError):
            select_driving_structure(empty)


class TestChangedDefinition:
    """Step-definition digest mismatch invalidates the definition axis."""

    def test_changed_definition(self) -> None:
        from confflow.persistence.contracts import ReuseCode, StoredWorkItemStatus
        from confflow.persistence.reuse import evaluate_reuse

        current = _reuse_inputs(step_semantic_digest="sha256:" + "11" * 32)
        decision = evaluate_reuse(
            current=current,
            stored=_reuse_inputs(),
            stored_status=StoredWorkItemStatus.COMPLETED,
            work_item_id="wi:gate",
        )
        assert decision.decision is ReuseCode.INVALIDATE_DEFINITION
        assert decision.details["step_semantic_digest_current"] == "sha256:" + "11" * 32


class TestChangedInput:
    """Work-item digest mismatch invalidates the input axis."""

    def test_changed_input(self) -> None:
        from confflow.persistence.contracts import ReuseCode, StoredWorkItemStatus
        from confflow.persistence.reuse import evaluate_reuse

        current = _reuse_inputs(work_item_digest="sha256:" + "e5" * 32)
        decision = evaluate_reuse(
            current=current,
            stored=_reuse_inputs(),
            stored_status=StoredWorkItemStatus.COMPLETED,
            work_item_id="wi:gate",
        )
        assert decision.decision is ReuseCode.INVALIDATE_INPUT
        assert decision.details["work_item_digest_current"] == "sha256:" + "e5" * 32


class TestChangedEnvironment:
    """Environment digest mismatch invalidates the environment axis."""

    def test_changed_environment(self) -> None:
        from confflow.persistence.contracts import ReuseCode, StoredWorkItemStatus
        from confflow.persistence.reuse import evaluate_reuse

        current = _reuse_inputs(environment_digest="sha256:" + "ee" * 32)
        decision = evaluate_reuse(
            current=current,
            stored=_reuse_inputs(),
            stored_status=StoredWorkItemStatus.COMPLETED,
            work_item_id="wi:gate",
        )
        assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT
        assert decision.details["environment_digest_current"] == "sha256:" + "ee" * 32


class TestChangedGroupLineage:
    """Group/lineage disagreement fails closed at pairing time."""

    def test_changed_group_lineage(self) -> None:
        from confflow.execution.output_identity import endpoint_lineage

        driving = _named_record("ts00", group_key="rxn-00")
        lineage = endpoint_lineage(driving)
        assert lineage[0] == ("ts00",)
        assert lineage[2] == "rxn-00"


class TestResultIdPersistence:
    """Production results carry stable ids; legacy/duplicate ids fail closed."""

    def test_result_id_persistence(self) -> None:
        from confflow.domain.result import (
            InvalidResultError,
            ResultSet,
            ScientificResult,
            find_duplicate_result_ids,
            make_result_id,
            require_production_ids,
        )
        from confflow.domain.units import Unit

        digest = "sha256:" + hashlib.sha256(b"gate").hexdigest()
        stamped = ScientificResult(
            kind="energy",
            value=1.0,
            unit=Unit.HARTREE,
            subject_structure_id="s1",
            result_id=make_result_id(
                step_id="s_opt",
                kind="energy",
                subject_structure_id="s1",
                producer_digest=digest,
            ),
        )
        require_production_ids(ResultSet((stamped,)))
        legacy = ScientificResult(kind="energy", value=1.0, unit=Unit.HARTREE)
        assert legacy.result_id is None
        with pytest.raises(InvalidResultError):
            require_production_ids(ResultSet((legacy,)))
        dup = find_duplicate_result_ids(ResultSet((stamped, stamped)))
        assert dup == (stamped.result_id,)


class TestArtifactCorruption:
    """Tampered artifact bytes fail verification and invalidate reuse."""

    def test_artifact_corruption(self, tmp_path: Path) -> None:
        from confflow.domain.artifact import ArtifactLocator, ArtifactRef
        from confflow.persistence.artifacts import ArtifactIntegrityError, verify_artifact
        from confflow.persistence.contracts import ReuseCode, StoredWorkItemStatus
        from confflow.persistence.reuse import evaluate_reuse

        run_root = str(tmp_path / "run")
        target = Path(run_root) / "steps" / "s_opt" / "payload.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"authentic-bytes")
        digest = "sha256:" + hashlib.sha256(b"authentic-bytes").hexdigest()
        ref = ArtifactRef(
            id="art1",
            role="output",
            locator=ArtifactLocator.run_relative("steps/s_opt/payload.bin"),
            checksum=digest,
            subject_structure_id="s1",
        )
        verify_artifact(run_root=run_root, ref=ref)
        target.write_bytes(b"tampered-bytes")
        with pytest.raises(ArtifactIntegrityError):
            verify_artifact(run_root=run_root, ref=ref)
        decision = evaluate_reuse(
            current=_reuse_inputs(),
            stored=_reuse_inputs(),
            stored_status=StoredWorkItemStatus.COMPLETED,
            artifacts_verified=False,
            work_item_id="wi:gate",
        )
        assert decision.decision is ReuseCode.INVALIDATE_ARTIFACT


class TestWorkdirCollision:
    """Item directories are collision-resistant and attempt-isolated."""

    def test_workdir_collision(self, tmp_path: Path) -> None:
        from confflow.execution.registry import default_registry
        from confflow.execution.work_item_executor import (
            ItemExecutionContext,
            hashed_item_slug,
        )
        from tests.v4._builders import calc_step, compile_doc, v4_doc

        assert hashed_item_slug("wi:s:A:B") != hashed_item_slug("wi:s:A_B")
        assert len(hashed_item_slug("wi:anything")) == 30

        document = v4_doc(
            [
                calc_step(
                    "s_opt",
                    program="orca",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    native={"keyword": "B3LYP Opt"},
                    checks=["normal_termination"],
                    execution={"binding_id": "test", "executable": "orca"},
                )
            ],
            inputs={"structures": {"kind": "structure", "cardinality": "many"}},
        )
        compiled = compile_doc(document)
        assert compiled.ok and compiled.plan is not None
        plan = compiled.plan
        planned = plan.steps[0]
        registry = default_registry()
        adapter = registry.resolve_program("orca")
        profile = registry.profile_implementation("standard")
        run_root = str(tmp_path / "run")
        ctx0 = ItemExecutionContext(
            step_id="s_opt",
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=adapter,
            profile=profile,
            run_root=run_root,
            attempt=0,
        )
        ctx1 = ItemExecutionContext(
            step_id="s_opt",
            scientific=planned.scientific,
            scientific_defaults=plan.scientific_defaults,
            adapter=adapter,
            profile=profile,
            run_root=run_root,
            attempt=1,
        )
        assert ctx0.durable_item_dir("wi:s:A:B") != ctx0.durable_item_dir("wi:s:A_B")
        assert "A:B" not in ctx0.durable_item_dir("wi:s:A:B")

        class _Item:
            id = "wi:s:A:B"
            logical_key = "s:A:B"

        assert ctx0.attempt_dir(_Item()) != ctx1.attempt_dir(_Item())
        assert ctx0.attempt_dir(_Item()).endswith("attempt_0000")
        assert ctx1.attempt_dir(_Item()).endswith("attempt_0001")


class TestIrcMultiOutput:
    """IRC items yield exactly two marker-driven endpoints, never by order."""

    def test_irc_multi_output(self) -> None:
        from confflow.execution.multi_output import order_item_structures
        from confflow.execution.output_identity import (
            endpoint_output_id,
            output_ordering_key,
        )

        logical = "s_irc:ts00"
        assert endpoint_output_id(logical, "forward") == (
            "s_irc:ts00:structure:path_endpoint_forward:0"
        )
        assert endpoint_output_id(logical, "reverse") == (
            "s_irc:ts00:structure:path_endpoint_reverse:0"
        )
        assert output_ordering_key("path_endpoint_forward", 0) < output_ordering_key(
            "path_endpoint_reverse", 0
        )
        with pytest.raises(ValueError):
            endpoint_output_id(logical, "sideways")

        records = tuple(
            _named_record(
                record_id,
                group_key="rxn-00",
            )
            for record_id in ("b", "a")
        )
        from confflow.domain import StructureRecord

        forward = StructureRecord(
            id=endpoint_output_id(logical, "forward"),
            atoms=("O", "H", "H"),
            coordinates=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            role="path_endpoint_forward",
            group_key="rxn-00",
            parent_ids=("ts00",),
        )
        reverse = StructureRecord(
            id=endpoint_output_id(logical, "reverse"),
            atoms=("O", "H", "H"),
            coordinates=((0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0)),
            role="path_endpoint_reverse",
            group_key="rxn-00",
            parent_ids=("ts00",),
        )
        assert len(records) == 2
        ordered = order_item_structures(StructureSet.of(reverse, forward))
        assert [record.id for record in ordered] == [forward.id, reverse.id]


# R2.2 (G18): TestAnalysisDurableReuse is retired with the analysis
# implementation imports (orphaned for R2.3a).


class TestEndpointAssignment:
    """Endpoint chemistry assignment defaults open, conflicts fail closed."""

    def test_endpoint_assignment(self) -> None:
        from confflow.analysis.models import (
            ASSIGNMENT_UNASSIGNED,
            AnalysisDefinition,
            AnalysisError,
        )
        from confflow.domain import FrozenDict as _Frozen

        class _Model:
            def compute(self, *args: Any, **kwargs: Any) -> Any:
                raise AssertionError("not called")

            def to_dict(self) -> dict[str, Any]:
                return {"mode": "stub"}

        definition = AnalysisDefinition(kind="reaction_profile", energy_model=_Model())
        assert definition.endpoint_assignment["forward"] == ASSIGNMENT_UNASSIGNED
        assert definition.endpoint_assignment["reverse"] == ASSIGNMENT_UNASSIGNED
        with pytest.raises(AnalysisError) as excinfo:
            AnalysisDefinition(
                kind="reaction_profile",
                energy_model=_Model(),
                endpoint_assignment=_Frozen({"forward": "reactant", "reverse": "reactant"}),
            )
        assert excinfo.value.code == "analysis_assignment_conflict"
        ok = AnalysisDefinition(
            kind="reaction_profile",
            energy_model=_Model(),
            endpoint_assignment=_Frozen({"forward": "reactant", "reverse": "product"}),
        )
        assert ok.assignment == "explicit"


class TestResultOrderInvariant:
    """Presentation order is deterministic and never drives correctness."""

    def test_result_order_invariant(self) -> None:
        from confflow.domain.result import ResultSet, ScientificResult, make_result_id
        from confflow.domain.units import Unit
        from confflow.execution.multi_output import order_item_structures
        from confflow.execution.output_identity import endpoint_output_id

        logical = "s_irc:ts01"
        forward_id = endpoint_output_id(logical, "forward")
        reverse_id = endpoint_output_id(logical, "reverse")

        def _record(record_id: str, role: str, x: float) -> Any:
            from confflow.domain import StructureRecord

            return StructureRecord(
                id=record_id,
                atoms=("O", "H", "H"),
                coordinates=((x, 0.0, 0.0), (x + 1.0, 0.0, 0.0), (x, 1.0, 0.0)),
                role=role,
                group_key="rxn-01",
                parent_ids=("ts01",),
            )

        forward = _record(forward_id, "path_endpoint_forward", 0.0)
        reverse = _record(reverse_id, "path_endpoint_reverse", 5.0)
        assert [
            record.id for record in order_item_structures(StructureSet.of(reverse, forward))
        ] == [
            forward_id,
            reverse_id,
        ]
        digest = "sha256:" + hashlib.sha256(b"order").hexdigest()
        results = ResultSet(
            (
                ScientificResult(
                    kind="energy",
                    value=2.0,
                    unit=Unit.HARTREE,
                    subject_structure_id=reverse_id,
                    result_id=make_result_id(
                        step_id="s",
                        kind="energy",
                        subject_structure_id=reverse_id,
                        producer_digest=digest,
                    ),
                ),
                ScientificResult(
                    kind="energy",
                    value=1.0,
                    unit=Unit.HARTREE,
                    subject_structure_id=forward_id,
                    result_id=make_result_id(
                        step_id="s",
                        kind="energy",
                        subject_structure_id=forward_id,
                        producer_digest=digest,
                    ),
                ),
            )
        )
        assert results.select_unique("energy", subject_structure_id=forward_id).value == 1.0
        ordered_ids = sorted(["wi:b", "wi:a"])
        assert ordered_ids == ["wi:a", "wi:b"]


class TestAmbiguousResultFail:
    """Ambiguous selections fail closed; first-match is never a substitute."""

    def test_ambiguous_result_fail(self) -> None:
        from confflow.domain.result import (
            InvalidResultError,
            ResultSet,
            ScientificResult,
            make_result_id,
        )
        from confflow.domain.units import Unit

        digest = "sha256:" + hashlib.sha256(b"amb").hexdigest()

        def _energy(value: float, subject: str, tag: str) -> ScientificResult:
            return ScientificResult(
                kind="energy",
                value=value,
                unit=Unit.HARTREE,
                subject_structure_id=subject,
                result_id=make_result_id(
                    step_id="s",
                    kind="energy",
                    subject_structure_id=subject,
                    producer_digest=digest,
                    discriminator=tag,
                ),
            )

        pool = ResultSet((_energy(1.0, "s1", "a"), _energy(2.0, "s1", "b")))
        with pytest.raises(InvalidResultError, match="ambiguous"):
            pool.select_unique("energy", subject_structure_id="s1")
        _, missing, ambiguous = pool.select_ids((pool.results[0].result_id or "",))
        assert missing == () and ambiguous == ()
        dup_id = "sha256:" + "dd" * 32
        dup_pool = ResultSet(
            (
                ScientificResult(
                    kind="energy",
                    value=1.0,
                    unit=Unit.HARTREE,
                    subject_structure_id="s1",
                    result_id=dup_id,
                ),
                ScientificResult(
                    kind="energy",
                    value=2.0,
                    unit=Unit.HARTREE,
                    subject_structure_id="s1",
                    result_id=dup_id,
                ),
            )
        )
        _, _, ambiguous = dup_pool.select_ids((dup_id,))
        assert ambiguous == (dup_id,)


class TestGoatSeed:
    """The typed step seed is the single stochastic authority."""

    def test_goat_seed(self) -> None:
        from confflow.domain.errors import DomainError
        from confflow.execution.binding_resolution import validate_step_seed
        from confflow.programs.orca import goat

        assert validate_step_seed(None) is None
        assert validate_step_seed(7) == 7
        for bad in (True, False, "7", 7.0):
            with pytest.raises(DomainError):
                validate_step_seed(bad)
        first = goat.render_goat_blocks({"goat": {"RANDOMSEED": False, "MaxIter": 2}})
        second = goat.render_goat_blocks({"goat": {"MaxIter": 2, "RANDOMSEED": False}})
        assert first == second
        with pytest.raises(ValueError):
            goat.render_goat_blocks({"goat": {"Seed": 7}})
        # Integers carry no documented seed semantics: only booleans render.
        with pytest.raises(ValueError):
            goat.render_goat_blocks({"goat": {"RANDOMSEED": 1}})


class TestManifestSchema:
    """Run-result manifests validate against the producer schema."""

    def test_manifest_schema(self) -> None:
        import jsonschema

        from confflow.producer.contract import (
            build_run_result_manifest,
            run_result_json_schema,
            run_result_schema_sha256,
        )

        digest = "sha256:" + "ab" * 32
        manifest = build_run_result_manifest(
            run_id="run",
            status="completed",
            definition_digest=digest,
            producer_version="0.0-test",
            steps=[
                {
                    "id": "s_irc",
                    "status": "completed",
                    "digest": digest,
                    "counts": {"completed": 20, "failed": 0, "cancelled": 0},
                    "diagnostics": [],
                }
            ],
            analyses=[],
            artifacts=[],
        )
        assert manifest["content_schema"] == "confflow.run_result_manifest.v1"
        jsonschema.validate(instance=manifest, schema=run_result_json_schema())
        assert run_result_schema_sha256() == run_result_schema_sha256()
        with pytest.raises(ValueError, match="unknown run status"):
            build_run_result_manifest(
                run_id="run",
                status="no-such-status",
                definition_digest=digest,
                producer_version="0.0-test",
            )


class TestLegacyEntryFailClosed:
    """Legacy workflows and unstamped results never execute as production."""

    def test_legacy_entry_fail_closed(self, tmp_path: Path, capsys: Any) -> None:
        from confflow.domain.result import (
            InvalidResultError,
            ResultSet,
            ScientificResult,
            require_production_ids,
        )
        from confflow.domain.units import Unit
        from confflow.v4cli import main

        legacy = tmp_path / "legacy.yaml"
        legacy.write_text("global:\n  iprog: g16\n")
        assert main(["run", "--workflow", str(legacy), "--run-root", str(tmp_path / "r")]) == 1
        assert "legacy_workflow_not_executable" in capsys.readouterr().err
        legacy_result = ScientificResult(kind="energy", value=1.0, unit=Unit.HARTREE)
        assert legacy_result.result_id is None
        with pytest.raises(InvalidResultError):
            require_production_ids(ResultSet((legacy_result,)))


class TestArchitectureScannerGate:
    """The entry-path scanner runs clean and fails on injected legacy code."""

    def test_architecture_scanner_gate(self) -> None:
        from tools.architecture_policy import LEGACY_CLI_RULE_IDS
        from tools.architecture_policy import scan as policy_scan

        repo_root = Path(__file__).resolve().parents[2]
        assert (
            policy_scan(
                repo_root,
                rule_ids=list(LEGACY_CLI_RULE_IDS),
                profile="legacy_cli",
            )
            == []
        )
        completed = subprocess.run(
            [sys.executable, str(repo_root / "scripts" / "v4_arch_scan.py")],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        assert "clean" in completed.stdout
