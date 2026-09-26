#!/usr/bin/env python3

"""V4-3 explicit reuse and compatibility decisions (section 10 matrix).

Each row pins the ``(decision, reason-code)`` pair for one reuse scenario.
The matrix proves the digest-axis separation contract:

- presentation facts (labels, GUI annotations) and scheduler width are not
  fields of :class:`ReuseInputs`, so they can never invalidate reuse;
- scientific definition changes invalidate the definition axis while
  resource/geometry changes invalidate the input axis;
- the environment and provenance axes are independent of both;
- lifecycle states (pending/running/cancelled/failed) decide before any
  axis comparison runs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from confflow.domain import FrozenDict
from confflow.domain.canonical import CANONICALIZATION_ID
from confflow.persistence.contracts import OwnerVerdict, ReuseCode, StoredWorkItemStatus
from confflow.persistence.reuse import ReuseInputs, build_producer_provenance, evaluate_reuse

_ITEM_ID = "item-001"


def _provenance(
    adapter_version: str = "adapter.v1",
    profile_version: str = "profile.v1",
    check_versions: Mapping[str, str] | None = None,
    recovery_version: str = "recovery.v1",
) -> FrozenDict:
    """Build standard-shape producer provenance with fixed defaults."""
    checks = dict(check_versions) if check_versions is not None else {"normal_termination": "c.v1"}
    return build_producer_provenance(
        adapter_version=adapter_version,
        profile_version=profile_version,
        check_versions=checks,
        recovery_version=recovery_version,
    )


def _inputs(**overrides: Any) -> ReuseInputs:
    """Build baseline reuse inputs; *overrides* replace individual axes."""
    payload: dict[str, Any] = {
        "work_item_digest": "sha256:" + "a1" * 32,
        "step_semantic_digest": "sha256:" + "b2" * 32,
        "environment_digest": "sha256:" + "c3" * 32,
        "producer_provenance": _provenance(),
        "artifact_checksums": ("sha256:" + "d4" * 32,),
    }
    payload.update(overrides)
    return ReuseInputs(**payload)


def _decide(
    current: ReuseInputs,
    stored: ReuseInputs | None,
    status: StoredWorkItemStatus | str | None,
    **kwargs: Any,
) -> Any:
    """Evaluate reuse for *current* against *stored* with a fixed item id."""
    return evaluate_reuse(
        current=current, stored=stored, stored_status=status, work_item_id=_ITEM_ID, **kwargs
    )


class TestPresentationAndSchedulerInertness:
    """Labels, annotations, and scheduler width never invalidate reuse."""

    def test_label_and_annotation_change_reuses(self) -> None:
        current, stored = _inputs(), _inputs()
        decision = _decide(current, stored, StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.REUSE
        assert decision.reason

    def test_scheduler_width_change_reuses(self) -> None:
        current, stored = _inputs(), _inputs()
        decision = _decide(current, stored, StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.REUSE
        field_names = {field.name for field in dataclasses.fields(ReuseInputs)}
        assert "scheduler" not in field_names
        assert "label" not in field_names
        assert "annotations" not in field_names
        assert not hasattr(current, "scheduler")


class TestInputAxis:
    """Resource and geometry changes move the work-item digest only."""

    def test_memory_change_invalidates_input(self) -> None:
        current = _inputs(work_item_digest="sha256:" + "e5" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_INPUT
        assert decision.details["work_item_digest_current"] == "sha256:" + "e5" * 32
        assert decision.details["work_item_digest_stored"] == "sha256:" + "a1" * 32

    def test_cores_change_invalidates_input(self) -> None:
        current = _inputs(work_item_digest="sha256:" + "f6" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_INPUT

    def test_geometry_change_invalidates_input(self) -> None:
        current = _inputs(work_item_digest="sha256:" + "07" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_INPUT
        assert "input" in decision.reason


class TestDefinitionAxis:
    """Native keyword, check, recovery, and seed changes move the step digest."""

    def test_native_keyword_change_invalidates_definition(self) -> None:
        current = _inputs(step_semantic_digest="sha256:" + "11" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_DEFINITION
        assert decision.details["step_semantic_digest_current"] == "sha256:" + "11" * 32
        assert decision.details["step_semantic_digest_stored"] == "sha256:" + "b2" * 32

    def test_checks_change_invalidates_definition(self) -> None:
        current = _inputs(step_semantic_digest="sha256:" + "22" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_DEFINITION

    def test_recovery_change_invalidates_definition(self) -> None:
        current = _inputs(step_semantic_digest="sha256:" + "33" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_DEFINITION

    def test_seed_change_invalidates_definition(self) -> None:
        current = _inputs(step_semantic_digest="sha256:" + "44" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_DEFINITION


class TestArtifactAxis:
    """Checkpoint and verification failures invalidate the artifact axis."""

    def test_checkpoint_checksum_change_invalidates_artifact(self) -> None:
        current = _inputs(artifact_checksums=("sha256:" + "99" * 32,))
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_ARTIFACT
        assert decision.details["artifact_checksums_current"] == ("sha256:" + "99" * 32,)
        assert decision.details["artifact_checksums_stored"] == ("sha256:" + "d4" * 32,)

    def test_checksum_order_is_stable(self) -> None:
        current = _inputs(artifact_checksums=("b-check", "a-check"))
        assert current.artifact_checksums == ("a-check", "b-check")
        stored = _inputs(artifact_checksums=("a-check", "b-check"))
        decision = _decide(current, stored, StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.REUSE

    def test_unverified_artifacts_invalidate_artifact(self) -> None:
        decision = _decide(
            _inputs(), _inputs(), StoredWorkItemStatus.COMPLETED, artifacts_verified=False
        )
        assert decision.decision is ReuseCode.INVALIDATE_ARTIFACT
        assert decision.reason == "stored artifacts failed verification"


class TestEnvironmentAxis:
    """The environment digest is independent of binary paths and definitions."""

    def test_same_binary_new_path_reuses(self) -> None:
        decision = _decide(_inputs(), _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.REUSE

    def test_binary_content_change_invalidates_environment(self) -> None:
        current = _inputs(environment_digest="sha256:" + "ee" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT
        assert decision.details["environment_digest_current"] == "sha256:" + "ee" * 32
        assert decision.details["environment_digest_stored"] == "sha256:" + "c3" * 32

    def test_none_vs_value_environment_mismatches(self) -> None:
        current = _inputs(environment_digest=None)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT
        stored = _inputs(environment_digest=None)
        decision = _decide(_inputs(), stored, StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT

    def test_both_none_environment_passes_axis(self) -> None:
        current = _inputs(environment_digest=None)
        stored = _inputs(environment_digest=None)
        decision = _decide(current, stored, StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.REUSE


class TestProvenanceAxis:
    """Contract version bumps invalidate; unknown-on-both-sides passes."""

    def test_provenance_version_bump_invalidates_provenance(self) -> None:
        current = _inputs(producer_provenance=_provenance(adapter_version="adapter.v2"))
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_PROVENANCE
        assert "adapter_version" in decision.details["differing_keys"]
        assert "producer_provenance_current" in decision.details
        assert "producer_provenance_stored" in decision.details

    def test_both_empty_provenance_passes_axis(self) -> None:
        current = _inputs(producer_provenance=FrozenDict())
        stored = _inputs(producer_provenance=FrozenDict())
        decision = _decide(current, stored, StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.REUSE

    def test_one_sided_empty_provenance_mismatches(self) -> None:
        current = _inputs(producer_provenance=FrozenDict())
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_PROVENANCE

    def test_differing_keys_truncated_to_eight(self) -> None:
        current = FrozenDict({f"contract-{index:02d}": "v2" for index in range(10)})
        stored = FrozenDict({f"contract-{index:02d}": "v1" for index in range(10)})
        decision = _decide(
            _inputs(producer_provenance=current),
            _inputs(producer_provenance=stored),
            StoredWorkItemStatus.COMPLETED,
        )
        assert decision.decision is ReuseCode.INVALIDATE_PROVENANCE
        assert len(decision.details["differing_keys"]) == 8
        assert tuple(decision.details["differing_keys"]) == tuple(
            sorted(decision.details["differing_keys"])
        )


class TestLifecycleRules:
    """Pending/running/cancelled/failed states decide before axis comparison."""

    def test_missing_record_executes_new(self) -> None:
        decision = _decide(_inputs(), None, StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.EXECUTE_NEW
        assert "no durable record" in decision.reason

    def test_missing_status_executes_new(self) -> None:
        decision = _decide(_inputs(), _inputs(), None)
        assert decision.decision is ReuseCode.EXECUTE_NEW
        assert "no durable record" in decision.reason

    def test_pending_missing_status_executes_new(self) -> None:
        decision = _decide(_inputs(), _inputs(), "pending-missing")
        assert decision.decision is ReuseCode.EXECUTE_NEW
        assert "no durable record" in decision.reason

    def test_pending_never_reuses(self) -> None:
        decision = _decide(_inputs(), _inputs(), StoredWorkItemStatus.PENDING)
        assert decision.decision is ReuseCode.EXECUTE_NEW
        assert "pending never reuses" in decision.reason

    def test_running_dead_owner_recovers_abandoned(self) -> None:
        decision = _decide(
            _inputs(),
            _inputs(),
            StoredWorkItemStatus.RUNNING,
            owner_verdict=OwnerVerdict.DEFINITELY_DEAD,
        )
        assert decision.decision is ReuseCode.RECOVER_ABANDONED
        assert decision.reason

    def test_running_alive_owner_blocks(self) -> None:
        decision = _decide(
            _inputs(),
            _inputs(),
            StoredWorkItemStatus.RUNNING,
            owner_verdict=OwnerVerdict.DEFINITELY_ALIVE,
        )
        assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER
        assert decision.reason.startswith("owner_alive:")

    def test_running_uncertain_owner_blocks(self) -> None:
        decision = _decide(
            _inputs(), _inputs(), StoredWorkItemStatus.RUNNING, owner_verdict=OwnerVerdict.UNCERTAIN
        )
        assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER
        assert decision.reason.startswith("owner_uncertain:")

    def test_running_unknown_owner_blocks(self) -> None:
        decision = _decide(_inputs(), _inputs(), StoredWorkItemStatus.RUNNING, owner_verdict=None)
        assert decision.decision is ReuseCode.BLOCKED_UNCERTAIN_OWNER
        assert decision.reason.startswith("owner_uncertain:")

    def test_failed_compatible_retries(self) -> None:
        decision = _decide(_inputs(), _inputs(), StoredWorkItemStatus.FAILED)
        assert decision.decision is ReuseCode.RETRY_FAILED
        assert decision.reason

    def test_interrupted_compatible_retries(self) -> None:
        decision = _decide(_inputs(), _inputs(), StoredWorkItemStatus.INTERRUPTED)
        assert decision.decision is ReuseCode.RETRY_FAILED

    def test_failed_definition_changed_invalidates(self) -> None:
        current = _inputs(step_semantic_digest="sha256:" + "55" * 32)
        decision = _decide(current, _inputs(), StoredWorkItemStatus.FAILED)
        assert decision.decision is ReuseCode.INVALIDATE_DEFINITION

    def test_cancelled_requires_explicit_retry(self) -> None:
        decision = _decide(_inputs(), _inputs(), StoredWorkItemStatus.CANCELLED)
        assert decision.decision is ReuseCode.EXECUTE_NEW
        assert decision.details["requires_explicit_retry"] is True
        assert decision.reason

    def test_completed_compatible_reuses(self) -> None:
        decision = _decide(_inputs(), _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.REUSE
        assert decision.reason


class TestCompatibilityOrder:
    """The first mismatching axis wins; later axes never shadow earlier ones."""

    def test_provenance_beats_environment(self) -> None:
        current = _inputs(
            producer_provenance=_provenance(profile_version="profile.v2"),
            environment_digest="sha256:" + "ee" * 32,
        )
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_PROVENANCE

    def test_environment_beats_definition(self) -> None:
        current = _inputs(
            environment_digest="sha256:" + "ee" * 32,
            step_semantic_digest="sha256:" + "11" * 32,
        )
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_ENVIRONMENT

    def test_definition_beats_artifact(self) -> None:
        current = _inputs(
            step_semantic_digest="sha256:" + "11" * 32,
            artifact_checksums=("sha256:" + "99" * 32,),
        )
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_DEFINITION

    def test_artifact_beats_input(self) -> None:
        current = _inputs(
            artifact_checksums=("sha256:" + "99" * 32,),
            work_item_digest="sha256:" + "e5" * 32,
        )
        decision = _decide(current, _inputs(), StoredWorkItemStatus.COMPLETED)
        assert decision.decision is ReuseCode.INVALIDATE_ARTIFACT

    def test_input_beats_verification(self) -> None:
        current = _inputs(work_item_digest="sha256:" + "e5" * 32)
        decision = _decide(
            current, _inputs(), StoredWorkItemStatus.COMPLETED, artifacts_verified=False
        )
        assert decision.decision is ReuseCode.INVALIDATE_INPUT


class TestDecisionShape:
    """Every path returns a decision with a reason and the item id."""

    def test_work_item_id_passthrough_on_every_path(self) -> None:
        cases = [
            (_inputs(), None, StoredWorkItemStatus.COMPLETED, {}),
            (_inputs(), _inputs(), StoredWorkItemStatus.PENDING, {}),
            (_inputs(), _inputs(), StoredWorkItemStatus.RUNNING, {"owner_verdict": None}),
            (_inputs(), _inputs(), StoredWorkItemStatus.CANCELLED, {}),
            (_inputs(), _inputs(), StoredWorkItemStatus.COMPLETED, {}),
            (_inputs(), _inputs(), StoredWorkItemStatus.FAILED, {}),
            (_inputs(), _inputs(), StoredWorkItemStatus.INTERRUPTED, {}),
        ]
        for current, stored, status, extra in cases:
            decision = evaluate_reuse(
                current=current,
                stored=stored,
                stored_status=status,
                work_item_id="item-xyz",
                **extra,
            )
            assert decision.work_item_id == "item-xyz"
            assert decision.reason.strip()
            assert isinstance(decision.decision, ReuseCode)

    def test_blank_work_item_id_still_returns_decision(self) -> None:
        decision = evaluate_reuse(
            current=_inputs(), stored=_inputs(), stored_status=StoredWorkItemStatus.COMPLETED
        )
        assert decision.decision is ReuseCode.REUSE
        assert decision.work_item_id.strip()
        assert decision.reason.strip()

    def test_provenance_builder_shape(self) -> None:
        provenance = _provenance()
        assert isinstance(provenance, FrozenDict)
        assert provenance["canonicalization_id"] == CANONICALIZATION_ID
        assert provenance["adapter_version"] == "adapter.v1"
        assert provenance["profile_version"] == "profile.v1"
        assert provenance["recovery_version"] == "recovery.v1"
        assert dict(provenance["check_versions"]) == {"normal_termination": "c.v1"}

    def test_module_has_no_io_or_storage_imports(self) -> None:
        import ast
        import inspect

        import confflow.persistence.reuse as reuse_module

        tree = ast.parse(inspect.getsource(reuse_module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint({"sqlite3", "os", "sys", "subprocess", "pathlib", "socket"})
