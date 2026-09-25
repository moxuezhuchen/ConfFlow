"""R4.2 — Workflow Binding V2 and the Execution Fingerprint C.

Covers the typed immutable binding, the ordered external-input identity
(I1–I6), the execution-site executable identity (EX1–EX7), the producer/
schema/canonicalization compatibility policies (PR1–PR7, SC1–SC6), the
layered A/B/C comparator (B1–B10), the C determinism properties (C1–C9),
and the state-v2 binding immutability boundary. No V3 execution, no runtime
directories, no engine/service/worker wiring.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from confflow.config.canonical import (
    CANONICALIZATION_VERSION,
    CAPABILITIES,
    WORKFLOW_SCHEMA_VERSION_V3,
    can_execute,
    workflow_schema_sha256_v3,
)
from confflow.workflow.binding_v2 import (
    PRODUCER_IDENTITY,
    BindingProvenanceV2,
    WorkflowBindingV2,
    build_workflow_binding_v2,
    compare_workflow_binding_v2,
)
from confflow.workflow.execution_context import (
    resolve_execution_context_v3,
)
from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan
from confflow.workflow.state import (
    WorkflowStateV2Store,
    build_initial_state_v2,
    state_v2_payload,
)

V3 = "confflow.workflow.v3"


def _write_xyz(path: Path, note: str = "seed") -> Path:
    path.write_text(f"1\n{note}\nH 0 0 0\n", encoding="utf-8")
    return path


def _v3_plan(
    tmp_path: Path,
    steps: list[dict[str, Any]] | None = None,
    *,
    subdir: str = "",
) -> WorkflowV3Plan:
    base = tmp_path / subdir if subdir else tmp_path
    base.mkdir(parents=True, exist_ok=True)
    _write_xyz(base / "input.xyz")
    document = {
        "schema": V3,
        "steps": (
            steps
            if steps is not None
            else [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ]
        ),
    }
    (base / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
    plan = build_workflow_plan([str(base / "input.xyz")], str(base / "wf.yaml"))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _orca_config(tmp_path: Path, exe: Path) -> dict[str, Any]:
    return {
        "schema": V3,
        "global": {"iprog": "orca", "itask": "sp", "orca_path": str(exe)},
        "steps": [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ],
    }


def _orca_plan(tmp_path: Path, exe: Path, *, subdir: str = "") -> WorkflowV3Plan:
    base = tmp_path / subdir if subdir else tmp_path
    base.mkdir(parents=True, exist_ok=True)
    _write_xyz(base / "input.xyz")
    (base / "wf.yaml").write_text(json.dumps(_orca_config(tmp_path, exe)), encoding="utf-8")
    plan = build_workflow_plan([str(base / "input.xyz")], str(base / "wf.yaml"))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


def _clean_provenance() -> BindingProvenanceV2:
    return BindingProvenanceV2(
        workflow_schema=V3,
        workflow_schema_sha256=workflow_schema_sha256_v3(),
        canonicalization_version=CANONICALIZATION_VERSION,
        producer_identity=PRODUCER_IDENTITY,
        producer_version="1.0.0",
        producer_commit="abc123",
        producer_dirty=False,
    )


def _context(plan: WorkflowV3Plan, *, input_files: list[str] | None = None):
    files = input_files if input_files is not None else plan.input_files
    return resolve_execution_context_v3(plan, input_files=files)


def _binding(plan: WorkflowV3Plan, **kwargs: Any) -> WorkflowBindingV2:
    return build_workflow_binding_v2(plan, _context(plan), provenance=_clean_provenance(), **kwargs)


# ---------------------------------------------------------------------------
# Capability: V3 execution enabled post-flip, future schemas fail closed
# ---------------------------------------------------------------------------
def test_v3_execution_capability_allows_execution() -> None:
    assert CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3].execute is True
    assert not can_execute("confflow.workflow.v4")
    assert not can_execute("confflow.workflow.v99")


# ---------------------------------------------------------------------------
# Typed model: deep immutability + wire roundtrip
# ---------------------------------------------------------------------------
class TestTypedBindingModel:
    def test_frozen_outer(self, tmp_path: Path) -> None:
        binding = _binding(_v3_plan(tmp_path))
        with pytest.raises(dataclasses.FrozenInstanceError):
            binding.execution_fingerprint = "sha256:" + "0" * 64  # type: ignore[misc]

    def test_frozen_nested_provenance(self) -> None:
        provenance = _clean_provenance()
        with pytest.raises(dataclasses.FrozenInstanceError):
            provenance.producer_version = "9.9.9"  # type: ignore[misc]

    def test_to_payload_returns_fresh_mapping(self, tmp_path: Path) -> None:
        binding = _binding(_v3_plan(tmp_path))
        payload = binding.to_payload()
        payload["execution_fingerprint"] = "tampered"
        payload["provenance"]["producer_version"] = "tampered"
        assert binding.to_payload()["execution_fingerprint"] != "tampered"
        assert binding.to_payload()["provenance"]["producer_version"] != "tampered"

    def test_roundtrip_equality(self, tmp_path: Path) -> None:
        binding = _binding(_v3_plan(tmp_path))
        parsed = WorkflowBindingV2.from_payload(binding.to_payload())
        assert parsed == binding
        assert parsed.to_payload() == binding.to_payload()

    def test_schema_id_is_frozen(self, tmp_path: Path) -> None:
        binding = _binding(_v3_plan(tmp_path))
        assert binding.schema == "confflow.workflow_binding.v2"
        assert binding.source_version == WORKFLOW_SCHEMA_VERSION_V3

    def test_unknown_payload_field_rejected(self, tmp_path: Path) -> None:
        binding = _binding(_v3_plan(tmp_path))
        payload = binding.to_payload() | {"surprise": True}
        with pytest.raises(ValueError):
            WorkflowBindingV2.from_payload(payload)

    def test_builder_takes_a_from_plan(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        binding = _binding(plan)
        assert binding.definition_fingerprint == plan.definition_fingerprint


# ---------------------------------------------------------------------------
# State v2 typed integration: immutability through every public API
# ---------------------------------------------------------------------------
class TestStateBindingImmutability:
    def test_state_binding_is_typed_and_frozen(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        binding = _binding(plan)
        state = build_initial_state_v2(
            plan,
            run_id="r",
            work_dir=str(tmp_path),
            config_file="wf.yaml",
            binding=binding,
        )
        assert state.binding is binding
        with pytest.raises(dataclasses.FrozenInstanceError):
            state.binding.execution_fingerprint = "sha256:" + "0" * 64  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            state.binding.provenance.producer_version = "tampered"  # type: ignore[misc]

    def test_state_accepts_payload_mapping_and_normalizes(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        mapping = _binding(plan).to_payload()
        state = build_initial_state_v2(
            plan,
            run_id="r",
            work_dir=str(tmp_path),
            config_file="wf.yaml",
            binding=mapping,
        )
        # Mutating the caller's mapping must not affect the state.
        mapping["execution_fingerprint"] = "sha256:" + "0" * 64
        assert state.binding.execution_fingerprint != "sha256:" + "0" * 64

    def test_step_progress_never_touches_the_binding(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        binding = _binding(plan)
        state = build_initial_state_v2(
            plan,
            run_id="r",
            work_dir=str(tmp_path),
            config_file="wf.yaml",
            binding=binding,
        )
        before = json.dumps(binding.to_payload(), sort_keys=True)
        state.update_step("s001", status="completed")
        state.update_step("s002", status="submitted", fail_count=1)
        assert json.dumps(binding.to_payload(), sort_keys=True) == before
        assert json.dumps(state.binding.to_payload(), sort_keys=True) == before

    def test_save_load_roundtrip_keeps_typed_binding(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        binding = _binding(plan)
        state = build_initial_state_v2(
            plan,
            run_id="r",
            work_dir=str(tmp_path),
            config_file="wf.yaml",
            binding=binding,
        )
        WorkflowStateV2Store(str(tmp_path)).save(state)
        loaded = WorkflowStateV2Store(str(tmp_path)).load()
        assert loaded is not None
        assert loaded.binding == binding
        assert loaded.binding.to_payload() == binding.to_payload()
        # The R4.1 wire shape is unchanged: binding is the same five-field object.
        payload = state_v2_payload(loaded)
        assert set(payload["binding"]) == {
            "schema",
            "source_version",
            "definition_fingerprint",
            "execution_fingerprint",
            "provenance",
        }

    def test_state_payload_mutation_cannot_touch_binding(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        state = build_initial_state_v2(
            plan,
            run_id="r",
            work_dir=str(tmp_path),
            config_file="wf.yaml",
            binding=_binding(plan),
        )
        payload = state_v2_payload(state)
        payload["binding"]["execution_fingerprint"] = "tampered"
        assert state.binding.execution_fingerprint != "tampered"


# ---------------------------------------------------------------------------
# Producer provenance policy (PR1–PR7)
# ---------------------------------------------------------------------------
def _variant_provenance(**changes: Any) -> BindingProvenanceV2:
    payload = _clean_provenance().to_payload()
    payload.update(changes)
    return BindingProvenanceV2.from_payload(payload)


class TestProducerPolicy:
    def _stored_and_current(self, current_provenance: BindingProvenanceV2, tmp_path: Path):
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        current = build_workflow_binding_v2(plan, _context(plan), provenance=current_provenance)
        return stored, current

    def test_pr1_same_clean_provenance_compatible(self, tmp_path: Path) -> None:
        stored, current = self._stored_and_current(_clean_provenance(), tmp_path)
        result = compare_workflow_binding_v2(stored, current)
        assert result.compatible
        assert result.errors == () and result.warnings == ()

    def test_pr2_version_changed_rejected(self, tmp_path: Path) -> None:
        stored, current = self._stored_and_current(
            _variant_provenance(producer_version="2.0.0"), tmp_path
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.producer_version" in result.error_codes

    def test_pr3_commit_changed_rejected(self, tmp_path: Path) -> None:
        stored, current = self._stored_and_current(
            _variant_provenance(producer_commit="def456"), tmp_path
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.producer_commit" in result.error_codes

    def test_pr4_identity_is_captured_by_schema_id_and_version(self, tmp_path: Path) -> None:
        # The producer identity surfaces through the workflow schema identity
        # and the producer fields; a different identity cannot share them.
        stored, current = self._stored_and_current(
            _variant_provenance(workflow_schema="confflow.workflow.v4"), tmp_path
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.schema_id" in result.error_codes

    def test_pr5_stored_dirty_rejected(self, tmp_path: Path) -> None:
        stored, current = self._stored_and_current(_clean_provenance(), tmp_path)
        stored = WorkflowBindingV2(
            source_version=stored.source_version,
            definition_fingerprint=stored.definition_fingerprint,
            execution_fingerprint=stored.execution_fingerprint,
            provenance=_variant_provenance(producer_dirty=True),
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.dirty" in result.error_codes

    def test_pr6_current_dirty_rejected(self, tmp_path: Path) -> None:
        stored, current = self._stored_and_current(
            _variant_provenance(producer_dirty=True), tmp_path
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.dirty" in result.error_codes

    def test_pr7_fresh_dirty_binding_buildable(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        binding = build_workflow_binding_v2(
            plan,
            _context(plan),
            provenance=_variant_provenance(producer_dirty=True),
        )
        assert binding.provenance.producer_dirty is True

    def test_unknown_commit_fails_closed_even_when_equal(self, tmp_path: Path) -> None:
        stored, current = self._stored_and_current(
            _variant_provenance(producer_commit=None), tmp_path
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.producer_commit" in result.error_codes


# ---------------------------------------------------------------------------
# Schema / canonicalization policy (SC1–SC6)
# ---------------------------------------------------------------------------
class TestSchemaPolicy:
    def _with_digest(self, digest: str) -> BindingProvenanceV2:
        return _variant_provenance(workflow_schema_sha256=digest)

    def _current(self, tmp_path: Path, provenance: BindingProvenanceV2):
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        current = build_workflow_binding_v2(plan, _context(plan), provenance=provenance)
        return stored, current

    def test_sc1_same_everything_passes(self, tmp_path: Path) -> None:
        stored, current = self._current(tmp_path, _clean_provenance())
        assert compare_workflow_binding_v2(stored, current).compatible

    def test_sc2_schema_id_changed_rejected(self, tmp_path: Path) -> None:
        stored, current = self._current(
            tmp_path, _variant_provenance(workflow_schema="confflow.workflow.v4")
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.schema_id" in result.error_codes

    def test_sc3_digest_only_changed_warn_and_allow(self, tmp_path: Path) -> None:
        stored, current = self._current(tmp_path, self._with_digest("d" * 64))
        result = compare_workflow_binding_v2(stored, current)
        assert result.compatible
        assert "binding.schema_digest" in result.warning_codes
        assert result.errors == ()

    def test_sc4_digest_changed_and_a_changed_rejected(self, tmp_path: Path) -> None:
        plan = _v3_plan(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "B3LYP"}},
            ],
        )
        stored = _binding(_v3_plan(tmp_path / "base"))
        current = build_workflow_binding_v2(
            plan, _context(plan), provenance=self._with_digest("d" * 64)
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.definition_fingerprint" in result.error_codes

    def test_sc5_canonicalization_changed_rejected(self, tmp_path: Path) -> None:
        stored, current = self._current(
            tmp_path, _variant_provenance(canonicalization_version="confflow.canonical-json.v2")
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.canonicalization" in result.error_codes

    def test_sc6_digest_and_producer_changed_rejected(self, tmp_path: Path) -> None:
        stored, current = self._current(
            tmp_path,
            _variant_provenance(workflow_schema_sha256="d" * 64, producer_version="2.0.0"),
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.producer_version" in result.error_codes
        assert "binding.schema_digest" not in result.warning_codes


# ---------------------------------------------------------------------------
# Layered comparator (B1–B10)
# ---------------------------------------------------------------------------
class TestComparatorLayers:
    def test_b1_exact_match_compatible(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        binding = _binding(plan)
        assert compare_workflow_binding_v2(binding, binding).compatible

    def test_b2_a_mismatch_reports_definition_layer(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        changed_plan = _v3_plan(
            tmp_path / "b",
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "B3LYP"}},
            ],
        )
        current = _binding(changed_plan)
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert result.error_codes == ("binding.definition_fingerprint",)

    def test_b3_b4_b5_b6_b7_covered_by_policy_suites(self) -> None:
        # B3/SC2, B4/SC3, B5/SC5, B6/PR2-3, B7/PR5-6 — kept in one place above.
        assert True

    def test_b8_c_input_mismatch_classified(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        current = build_workflow_binding_v2(plan, _context(plan), provenance=_clean_provenance())
        # Force a C difference and provide input snapshots that explain it.
        changed = _write_xyz(tmp_path / "changed.xyz", "different")
        current_context = _context(plan, input_files=[str(changed)])
        current = build_workflow_binding_v2(plan, current_context, provenance=_clean_provenance())
        result = compare_workflow_binding_v2(
            stored,
            current,
            stored_input_digests=_context(plan).input_digests,
            current_input_digests=current_context.input_digests,
        )
        assert not result.compatible
        assert result.error_codes == ("binding.execution_fingerprint_inputs",)

    def test_b9_c_resource_mismatch_classified(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        plan = _orca_plan(tmp_path, exe)
        stored = _binding(plan)
        _executable(exe, "#!/bin/sh\n# rebuilt\nexit 0\n")
        current = _binding(plan)
        result = compare_workflow_binding_v2(
            stored,
            current,
            stored_input_digests=_context(plan).input_digests,
            current_input_digests=_context(plan).input_digests,
        )
        assert not result.compatible
        assert result.error_codes == ("binding.execution_fingerprint_resources",)

    def test_b10_label_order_annotations_only_compatible(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        stored = _binding(_orca_plan(tmp_path, exe))
        document = _orca_config(tmp_path, exe)
        steps = document["steps"]
        document["steps"] = [
            dict(steps[1], label="renamed", annotations={"confflow.migration.v2": {"x": 1}}),
            steps[0],
        ]
        (tmp_path / "changed").mkdir()
        _write_xyz(tmp_path / "changed" / "input.xyz")
        (tmp_path / "changed" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        changed_plan = build_workflow_plan(
            [str(tmp_path / "changed" / "input.xyz")], str(tmp_path / "changed" / "wf.yaml")
        )
        current = _binding(changed_plan)
        result = compare_workflow_binding_v2(stored, current)
        assert result.compatible
        assert result.errors == ()

    def test_snapshot_is_never_an_independent_authority(self, tmp_path: Path) -> None:
        """Keep the snapshot from becoming a second authority.

        Stored C == current C with a deliberately disagreeing input snapshot
        must stay compatible: the snapshot classifies mismatches, it never
        rejects on its own.
        """
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        current = _binding(plan)
        result = compare_workflow_binding_v2(
            stored,
            current,
            stored_input_digests=("sha256:" + "1" * 64,),
            current_input_digests=("sha256:" + "2" * 64,),
        )
        assert result.compatible
        assert result.errors == ()

    def test_resume_matrix_properties(self, tmp_path: Path) -> None:
        exe = _executable(tmp_path / "orca")
        base = _orca_plan(tmp_path, exe)
        stored = _binding(base)
        compatibles = [stored]

        # label rename / reorder / annotations (B10 fixture)
        document = _orca_config(tmp_path, exe)
        steps = document["steps"]
        document["steps"] = [dict(steps[1], label="renamed"), steps[0]]
        (tmp_path / "m1").mkdir()
        _write_xyz(tmp_path / "m1" / "input.xyz")
        (tmp_path / "m1" / "wf.yaml").write_text(json.dumps(document), encoding="utf-8")
        compatibles.append(
            _binding(
                build_workflow_plan(
                    [str(tmp_path / "m1" / "input.xyz")], str(tmp_path / "m1" / "wf.yaml")
                )
            )
        )
        for current in compatibles:
            assert compare_workflow_binding_v2(stored, current).compatible

        # rejects
        def _rejects(current: WorkflowBindingV2, code: str) -> None:
            result = compare_workflow_binding_v2(stored, current)
            assert not result.compatible
            assert code in result.error_codes

        scientific = _v3_plan(
            tmp_path / "sci",
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "B3LYP"}},
            ],
        )
        _rejects(_binding(scientific), "binding.definition_fingerprint")

        graph = _v3_plan(
            tmp_path / "graph",
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
            ],
        )
        _rejects(_binding(graph), "binding.definition_fingerprint")

        checkpoint_ref = _v3_plan(
            tmp_path / "ckpt",
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                {
                    "id": "s003",
                    "type": "calc",
                    "inputs": ["s002"],
                    "params": {"keyword": "HF"},
                    "checkpoint": {"from_step": "s002"},
                },
            ],
        )
        _rejects(_binding(checkpoint_ref), "binding.definition_fingerprint")


# ---------------------------------------------------------------------------
# Producer identity (PI1–PI6, R4.2 review fix)
# ---------------------------------------------------------------------------
class TestProducerIdentity:
    def test_pi1_authoritative_provenance_carries_stable_identity(self) -> None:
        from confflow.workflow.binding_v2 import authoritative_provenance

        provenance = authoritative_provenance()
        assert provenance.producer_identity == "confflow"

    def test_pi2_exact_same_identity_compatible(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        current = build_workflow_binding_v2(plan, _context(plan), provenance=_clean_provenance())
        result = compare_workflow_binding_v2(stored, current)
        assert result.compatible
        assert "binding.producer_identity" not in result.error_codes

    def test_pi3_identity_mismatch_rejected(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        current = build_workflow_binding_v2(
            plan,
            _context(plan),
            provenance=_variant_provenance(producer_identity="not-confflow"),
        )
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.producer_identity" in result.error_codes

    def test_pi4_missing_identity_rejected_on_parse(self, tmp_path: Path) -> None:
        plan = _v3_plan(tmp_path)
        payload = _binding(plan).to_payload()
        del payload["provenance"]["producer_identity"]
        with pytest.raises(ValueError):
            WorkflowBindingV2.from_payload(payload)
        # The state layer fails closed with its stable error type, too.
        from confflow.workflow.state import WorkflowStateCompatibilityError

        with pytest.raises(WorkflowStateCompatibilityError):
            build_initial_state_v2(
                plan,
                run_id="r",
                work_dir=str(tmp_path),
                config_file="wf.yaml",
                binding=payload,
            )

    def test_pi5_empty_identity_rejected(self) -> None:
        payload = _clean_provenance().to_payload()
        payload["producer_identity"] = "   "
        with pytest.raises(ValueError):
            BindingProvenanceV2.from_payload(payload)

    def test_pi6_source_version_cannot_substitute_identity(self, tmp_path: Path) -> None:
        """Reject an identity change even with the same source_version.

        Same workflow schema family (source_version), different producer
        implementation identity ⇒ reject: the two axes are independent.
        """
        plan = _v3_plan(tmp_path)
        stored = _binding(plan)
        current = build_workflow_binding_v2(
            plan,
            _context(plan),
            provenance=_variant_provenance(producer_identity="other-producer"),
        )
        assert stored.source_version == current.source_version
        result = compare_workflow_binding_v2(stored, current)
        assert not result.compatible
        assert "binding.producer_identity" in result.error_codes
