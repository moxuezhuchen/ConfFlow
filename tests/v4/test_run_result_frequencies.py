"""Run-result frequency refs preserve canonical ScientificResult values."""

from __future__ import annotations

from types import SimpleNamespace

import jsonschema

from confflow.domain.artifact import ArtifactLocator, ArtifactRef
from confflow.domain.result import ScientificResult
from confflow.domain.units import Unit
from confflow.producer.contract import build_run_result_manifest, run_result_json_schema
from confflow.producer.run_result import artifact_entry, result_ref_entries


def test_frequency_ref_projects_values_unit_and_sibling_imaginary_count() -> None:
    frequency = ScientificResult(
        kind="frequencies",
        value=[-42.5, 118.0, 240.25],
        unit=Unit.CM_INVERSE,
        subject_structure_id="structure-1",
        source_step_id="freq-step",
        source_work_item_id="freq-step:item-1",
        result_id="frequency-result",
    )
    imaginary_count = ScientificResult(
        kind="num_imaginary_frequencies",
        value=1,
        subject_structure_id="structure-1",
        source_step_id="freq-step",
        source_work_item_id="freq-step:item-1",
        result_id="imaginary-count-result",
    )
    step = SimpleNamespace(step_id="freq-step", results=(frequency, imaginary_count))

    (frequency_ref, count_ref) = result_ref_entries((step,))

    assert frequency_ref["frequencies_cm_1"] == [-42.5, 118.0, 240.25]
    assert frequency_ref["frequencies_unit"] == "cm^-1"
    assert frequency_ref["num_imaginary_frequencies"] == 1
    assert "frequencies_cm_1" not in count_ref

    manifest = build_run_result_manifest(
        run_id="run-1",
        status="completed",
        definition_digest="sha256:" + "ab" * 32,
        producer_version="0.0.test",
        steps=[],
        results=[frequency_ref, count_ref],
    )
    jsonschema.validate(manifest, run_result_json_schema())


def test_frequency_projection_requires_canonical_unit_and_matching_count() -> None:
    frequency = ScientificResult(
        kind="frequencies",
        value=[100.0],
        unit=Unit.CM_INVERSE,
        subject_structure_id="structure-1",
        source_step_id="freq-step",
        source_work_item_id="freq-step:item-1",
        result_id="frequency-result",
    )
    other_structure_count = ScientificResult(
        kind="num_imaginary_frequencies",
        value=0,
        subject_structure_id="structure-2",
        source_step_id="freq-step",
        source_work_item_id="freq-step:item-1",
        result_id="imaginary-count-result",
    )
    step = SimpleNamespace(step_id="freq-step", results=(frequency, other_structure_count))

    (frequency_ref, _) = result_ref_entries((step,))

    assert frequency_ref["frequencies_cm_1"] == [100.0]
    assert frequency_ref["frequencies_unit"] == "cm^-1"
    assert "num_imaginary_frequencies" not in frequency_ref


def test_artifact_projection_preserves_explicit_producer_identity() -> None:
    record = ArtifactRef(
        id="orca-output",
        role="native_output",
        locator=ArtifactLocator.run_relative("steps/sp/attempt_0002/job.out"),
        checksum="sha256:" + "ab" * 32,
        producer_step_id="sp",
        producer_work_item_id="wi:sp:water",
    )

    entry = artifact_entry(record)

    assert entry["step_id"] == "sp"
    assert entry["work_item_id"] == "wi:sp:water"
    manifest = build_run_result_manifest(
        run_id="run-1",
        status="completed",
        definition_digest="sha256:" + "cd" * 32,
        producer_version="0.0.test",
        steps=[],
        artifacts=[entry],
    )
    jsonschema.validate(manifest, run_result_json_schema())
