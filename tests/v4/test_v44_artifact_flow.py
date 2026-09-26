#!/usr/bin/env python3

"""V4-4 typed cross-step artifact flow.

Pins the frozen restart-artifact semantics: the single restart role, the
re-subjecting rule for minted output structures, exact subject-plus-role
selection with fail-closed errors, exact role filtering (including the
ORCA ``.gbw`` normalization), the cardinality matrix, order/name
independence, and an end-to-end cross-step chain where a re-subjected
checkpoint binds the downstream item for its new subject and for no
sibling.
"""

from __future__ import annotations

import hashlib
import random

import pytest

from confflow.domain.artifact import ArtifactLocator, ArtifactRef, ArtifactSet
from confflow.domain.binding import Cardinality
from confflow.domain.errors import DomainError
from confflow.execution.native import (
    GeometryOutput,
    NativeResult,
    ProducedFile,
    ProgramName,
)
from confflow.programs.orca.adapter import OrcaProgramAdapter
from confflow.workflow.v4.artifact_flow import (
    RESTART_ROLES,
    ArtifactFlowError,
    filter_by_role,
    resolve_restart_subject,
    select_restart_artifact,
    subject_for_output,
    verify_binding_cardinality,
)
from tests.v4._builders import structure


def _checksum(seed: str) -> str:
    """Return a deterministic checksum for *seed*."""
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _artifact(
    artifact_id: str,
    *,
    role: str,
    subject: str | None,
    locator_path: str,
    seed: str | None = None,
) -> ArtifactRef:
    """Build a deterministic artifact reference."""
    return ArtifactRef(
        id=artifact_id,
        role=role,
        locator=ArtifactLocator.run_relative(locator_path),
        checksum=_checksum(seed if seed is not None else artifact_id),
        subject_structure_id=subject,
    )


def _checkpoint(
    artifact_id: str, subject: str, locator_path: str = "steps/s_freq/job.chk"
) -> ArtifactRef:
    """Build a checkpoint artifact bound to *subject*."""
    return _artifact(artifact_id, role="checkpoint", subject=subject, locator_path=locator_path)


def _set_for_subject(subject: str, count: int, *, prefix: str = "chk") -> ArtifactSet:
    """Build *count* distinct checkpoints bound to *subject*."""
    return ArtifactSet.of(
        *(
            _checkpoint(
                f"{prefix}_{subject}_{index}",
                subject,
                f"steps/s_freq/{prefix}_{subject}_{index}.chk",
            )
            for index in range(count)
        )
    )


class TestRestartRoles:
    """The restart vocabulary is exactly one semantic role."""

    def test_restart_roles_is_only_checkpoint(self) -> None:
        assert RESTART_ROLES == frozenset({"checkpoint"})

    def test_restart_roles_is_a_frozenset(self) -> None:
        assert isinstance(RESTART_ROLES, frozenset)


class TestResolveRestartSubject:
    """Output id wins for minted structures; native subject is fallback."""

    def test_produced_output_wins_over_native_subject(self) -> None:
        assert (
            resolve_restart_subject(
                native_subject="struct_A",
                output_structure_id="struct_A_prime",
                geometry_semantics="produced",
            )
            == "struct_A_prime"
        )

    def test_passthrough_output_wins_over_native_subject(self) -> None:
        assert (
            resolve_restart_subject(
                native_subject="struct_A",
                output_structure_id="struct_A_prime",
                geometry_semantics="passthrough",
            )
            == "struct_A_prime"
        )

    def test_native_subject_kept_when_output_id_empty(self) -> None:
        assert (
            resolve_restart_subject(
                native_subject="struct_A",
                output_structure_id="",
                geometry_semantics="produced",
            )
            == "struct_A"
        )

    def test_empty_when_both_subjects_unknown(self) -> None:
        assert (
            resolve_restart_subject(
                native_subject=None,
                output_structure_id="",
                geometry_semantics="passthrough",
            )
            == ""
        )


class TestSelectRestartArtifact:
    """Exact subject-plus-role selection, fail closed."""

    def test_single_match_returns_the_artifact(self) -> None:
        candidates = ArtifactSet.of(
            _checkpoint("chk_A", "struct_A"),
            _checkpoint("chk_B", "struct_B"),
        )
        selected = select_restart_artifact(
            artifacts=candidates,
            subject_structure_id="struct_A",
            step_id="s_ts",
            logical_key="s_ts:struct_A",
            port="checkpoint",
        )
        assert selected.id == "chk_A"
        assert selected.subject_structure_id == "struct_A"

    def test_zero_matches_raise_missing_with_exact_code(self) -> None:
        candidates = ArtifactSet.of(_checkpoint("chk_B", "struct_B"))
        with pytest.raises(ArtifactFlowError) as caught:
            select_restart_artifact(
                artifacts=candidates,
                subject_structure_id="struct_A",
                step_id="s_ts",
                logical_key="s_ts:struct_A",
                port="checkpoint",
            )
        assert caught.value.code == "artifact_subject_missing"
        assert "struct_A" in str(caught.value)
        assert "checkpoint" in str(caught.value)

    def test_two_matches_raise_ambiguous_never_first(self) -> None:
        candidates = ArtifactSet.of(
            _checkpoint("chk_A_first", "struct_A"),
            _checkpoint("chk_A_second", "struct_A"),
        )
        with pytest.raises(ArtifactFlowError) as caught:
            select_restart_artifact(
                artifacts=candidates,
                subject_structure_id="struct_A",
            )
        assert caught.value.code == "artifact_subject_ambiguous"
        reversed_candidates = ArtifactSet.of(*reversed(candidates.artifacts))
        with pytest.raises(ArtifactFlowError) as again:
            select_restart_artifact(
                artifacts=reversed_candidates,
                subject_structure_id="struct_A",
            )
        assert again.value.code == "artifact_subject_ambiguous"

    def test_role_mismatch_counts_as_missing(self) -> None:
        candidates = ArtifactSet.of(
            _artifact(
                "log_A",
                role="native_output",
                subject="struct_A",
                locator_path="steps/s_freq/job.log",
            )
        )
        with pytest.raises(ArtifactFlowError) as caught:
            select_restart_artifact(artifacts=candidates, subject_structure_id="struct_A")
        assert caught.value.code == "artifact_subject_missing"

    def test_error_carries_step_context(self) -> None:
        candidates = ArtifactSet.of()
        with pytest.raises(ArtifactFlowError) as caught:
            select_restart_artifact(
                artifacts=candidates,
                subject_structure_id="struct_A",
                step_id="s_ts",
                logical_key="s_ts:struct_A",
                port="checkpoint",
            )
        assert caught.value.step_id == "s_ts"
        assert caught.value.logical_key == "s_ts:struct_A"
        assert caught.value.port == "checkpoint"
        message = str(caught.value)
        assert "s_ts" in message and "checkpoint" in message

    def test_error_is_a_domain_error(self) -> None:
        assert issubclass(ArtifactFlowError, DomainError)


class TestFilterByRole:
    """Role filtering is exact membership, nothing inferred."""

    def _mixed_set(self) -> ArtifactSet:
        return ArtifactSet.of(
            _checkpoint("chk_A", "struct_A"),
            _artifact(
                "log_A",
                role="native_output",
                subject="struct_A",
                locator_path="steps/s_freq/job.log",
            ),
            _artifact(
                "err_A",
                role="stderr",
                subject="struct_A",
                locator_path="steps/s_freq/job.err",
            ),
        )

    def test_checkpoint_filter_excludes_native_output_and_stderr(self) -> None:
        assert filter_by_role(self._mixed_set(), frozenset({"checkpoint"})).ids == ("chk_A",)

    def test_native_output_filter_is_exact(self) -> None:
        assert filter_by_role(self._mixed_set(), frozenset({"native_output"})).ids == ("log_A",)

    def test_multi_role_filter_preserves_set_order(self) -> None:
        assert filter_by_role(self._mixed_set(), frozenset({"stderr", "checkpoint"})).ids == (
            "chk_A",
            "err_A",
        )

    def test_raw_wavefunction_role_does_not_match_checkpoint(self) -> None:
        candidates = ArtifactSet.of(
            _artifact(
                "wave_A",
                role="checkpoint_wavefunction",
                subject="struct_A",
                locator_path="steps/s_freq/job.gbw",
            )
        )
        assert filter_by_role(candidates, RESTART_ROLES).is_empty

    def test_gbw_normalized_checkpoint_matches(self) -> None:
        candidates = ArtifactSet.of(
            _artifact(
                "wave_A",
                role="checkpoint",
                subject="struct_A",
                locator_path="steps/s_freq/job.gbw",
            )
        )
        assert filter_by_role(candidates, RESTART_ROLES).ids == ("wave_A",)

    def test_empty_roles_match_nothing(self) -> None:
        assert filter_by_role(self._mixed_set(), frozenset()).is_empty


class TestOrcaGbwNormalization:
    """The ORCA adapter normalizes ``.gbw`` to checkpoint plus metadata."""

    def test_discover_artifacts_maps_gbw_to_checkpoint(self, tmp_path) -> None:
        names = ("job.out", "job.gbw", "job.err")
        for name in names:
            (tmp_path / name).write_bytes(b"payload:" + name.encode("utf-8"))
        native = NativeResult(
            program=ProgramName.ORCA,
            terminated_normally=True,
            geometry_output=GeometryOutput.NONE,
            produced_files=tuple(
                ProducedFile(name=name, role=role, size_bytes=8)
                for name, role in (
                    ("job.out", "native_output"),
                    ("job.gbw", "checkpoint_wavefunction"),
                    ("job.err", "stderr"),
                )
            ),
            log_file_name="job.out",
        )
        found = OrcaProgramAdapter().discover_artifacts(
            work_dir=str(tmp_path),
            run_relative_prefix="steps/s_orca/job",
            native_result=native,
            step_id="s_orca",
            work_item_id="wi:s_orca:struct_A",
            subject_structure_id="struct_A",
        )
        by_role = {record.role for record in found}
        assert "checkpoint" in by_role
        assert "checkpoint_wavefunction" not in by_role
        wave = found.by_role("checkpoint")[0]
        assert wave.metadata["program_format"] == "orca_gbw"
        assert wave.subject_structure_id == "struct_A"
        assert filter_by_role(found, RESTART_ROLES).ids == (wave.id,)


class TestCardinalityMatrix:
    """ONE is exact; MANY and OPTIONAL pass through."""

    @pytest.mark.parametrize(
        ("cardinality", "count", "expected_code"),
        [
            ("one", 1, None),
            ("one", 0, "artifact_subject_missing"),
            ("one", 2, "artifact_subject_ambiguous"),
            ("one", 3, "artifact_subject_ambiguous"),
            ("many", 0, None),
            ("many", 1, None),
            ("many", 4, None),
            ("optional", 0, None),
            ("optional", 1, None),
        ],
    )
    def test_matrix(self, cardinality: str, count: int, expected_code: str | None) -> None:
        candidates = _set_for_subject("struct_A", count)
        if expected_code is None:
            assert (
                verify_binding_cardinality(
                    artifacts=candidates,
                    port_name="checkpoint",
                    cardinality=cardinality,
                    subject_structure_id="struct_A",
                    step_id="s_ts",
                    logical_key="s_ts:struct_A",
                )
                is None
            )
        else:
            with pytest.raises(ArtifactFlowError) as caught:
                verify_binding_cardinality(
                    artifacts=candidates,
                    port_name="checkpoint",
                    cardinality=cardinality,
                    subject_structure_id="struct_A",
                    step_id="s_ts",
                    logical_key="s_ts:struct_A",
                )
            assert caught.value.code == expected_code

    def test_cardinality_enum_values_behave_identically(self) -> None:
        single = _set_for_subject("struct_A", 1)
        assert (
            verify_binding_cardinality(
                artifacts=single,
                port_name="checkpoint",
                cardinality=Cardinality.ONE,
                subject_structure_id="struct_A",
            )
            is None
        )
        assert (
            verify_binding_cardinality(
                artifacts=ArtifactSet.of(),
                port_name="checkpoint",
                cardinality=Cardinality.OPTIONAL,
                subject_structure_id="struct_A",
            )
            is None
        )
        with pytest.raises(ArtifactFlowError) as caught:
            verify_binding_cardinality(
                artifacts=ArtifactSet.of(),
                port_name="checkpoint",
                cardinality=Cardinality.ONE,
                subject_structure_id="struct_A",
            )
        assert caught.value.code == "artifact_subject_missing"

    def test_error_names_port_and_subject(self) -> None:
        with pytest.raises(ArtifactFlowError) as caught:
            verify_binding_cardinality(
                artifacts=ArtifactSet.of(),
                port_name="checkpoint",
                cardinality="one",
                subject_structure_id="struct_A",
                step_id="s_ts",
                logical_key="s_ts:struct_A",
                port="checkpoint",
            )
        assert caught.value.code == "artifact_subject_missing"
        assert "struct_A" in str(caught.value)
        assert "checkpoint" in str(caught.value)


class TestOrderAndNameIndependence:
    """Shuffled order, renamed files, and renamed dirs bind identically."""

    def _variants(self, seed: int) -> list[ArtifactSet]:
        rng = random.Random(seed)
        variants: list[ArtifactSet] = []
        for round_index in range(4):
            records = [
                _artifact(
                    f"chk_{subject}",
                    role="checkpoint",
                    subject=subject,
                    locator_path=(
                        f"run{round_index}/dir_{rng.randrange(1000)}/"
                        f"job_{rng.randrange(1000)}.chk"
                    ),
                    seed=subject,
                )
                for subject in ("struct_A", "struct_B", "struct_C")
            ]
            rng.shuffle(records)
            variants.append(ArtifactSet.of(*records))
        return variants

    def test_same_selection_across_shuffled_variants(self) -> None:
        selections = [
            select_restart_artifact(artifacts=variant, subject_structure_id="struct_B").id
            for variant in self._variants(20260926)
        ]
        assert selections == ["chk_struct_B"] * 4

    def test_digest_payload_ignores_locator_moves(self) -> None:
        variants = self._variants(7)
        payloads = [
            select_restart_artifact(
                artifacts=variant, subject_structure_id="struct_A"
            ).digest_payload()
            for variant in variants
        ]
        assert all(payload == payloads[0] for payload in payloads)


class TestSubjectForOutput:
    """Minted outputs become the subject; inputs survive only as fallback."""

    def test_output_id_wins(self) -> None:
        assert (
            subject_for_output(
                input_structure_id="struct_A",
                output_structure_id="struct_A_prime",
            )
            == "struct_A_prime"
        )

    def test_input_id_survives_when_no_output_minted(self) -> None:
        assert (
            subject_for_output(input_structure_id="struct_A", output_structure_id=None)
            == "struct_A"
        )
        assert (
            subject_for_output(input_structure_id="struct_A", output_structure_id="") == "struct_A"
        )


class TestCrossStepChain:
    """A re-subjected checkpoint binds the new subject and no sibling."""

    def _chain(self) -> ArtifactSet:
        parent = structure("struct_A")
        child = structure(
            "struct_A_prime",
            parent_ids=(parent.id,),
            lineage_root_id=parent.id,
        )
        sibling = structure("struct_B")
        assert child.parent_ids == ("struct_A",)
        assert sibling.id == "struct_B"
        restarted = resolve_restart_subject(
            native_subject=parent.id,
            output_structure_id=child.id,
            geometry_semantics="produced",
        )
        assert restarted == "struct_A_prime"
        assert subject_for_output(input_structure_id=parent.id, output_structure_id=child.id) == (
            "struct_A_prime"
        )
        return ArtifactSet.of(_checkpoint("chk_freq_A_prime", restarted))

    def test_downstream_item_for_new_subject_binds(self) -> None:
        produced = self._chain()
        verify_binding_cardinality(
            artifacts=filter_by_role(produced.by_subject("struct_A_prime"), RESTART_ROLES),
            port_name="checkpoint",
            cardinality="one",
            subject_structure_id="struct_A_prime",
            step_id="s_ts",
            logical_key="s_ts:struct_A_prime",
            port="checkpoint",
        )
        selected = select_restart_artifact(
            artifacts=produced,
            subject_structure_id="struct_A_prime",
            step_id="s_ts",
            logical_key="s_ts:struct_A_prime",
            port="checkpoint",
        )
        assert selected.id == "chk_freq_A_prime"

    def test_sibling_subject_does_not_bind(self) -> None:
        produced = self._chain()
        with pytest.raises(ArtifactFlowError) as caught:
            select_restart_artifact(
                artifacts=produced,
                subject_structure_id="struct_B",
                step_id="s_ts",
                logical_key="s_ts:struct_B",
                port="checkpoint",
            )
        assert caught.value.code == "artifact_subject_missing"

    def test_stale_parent_subject_does_not_bind(self) -> None:
        produced = self._chain()
        with pytest.raises(ArtifactFlowError) as caught:
            select_restart_artifact(
                artifacts=produced,
                subject_structure_id="struct_A",
                step_id="s_ts",
                logical_key="s_ts:struct_A",
                port="checkpoint",
            )
        assert caught.value.code == "artifact_subject_missing"


class TestErrorCodes:
    """Error codes are exact frozen strings."""

    def test_codes_are_exact_strings(self) -> None:
        with pytest.raises(ArtifactFlowError) as missing:
            select_restart_artifact(artifacts=ArtifactSet.of(), subject_structure_id="struct_A")
        assert missing.value.code == "artifact_subject_missing"
        with pytest.raises(ArtifactFlowError) as ambiguous:
            select_restart_artifact(
                artifacts=_set_for_subject("struct_A", 2),
                subject_structure_id="struct_A",
            )
        assert ambiguous.value.code == "artifact_subject_ambiguous"
