"""Verify that typed transform outputs become downloadable scientific artifacts."""
from hashlib import sha256
from types import SimpleNamespace

from confflow.application.structure_exports import publish_transform_structures
from confflow.domain.completion import StepStatus
from confflow.domain.step_result import StepResult
from confflow.domain.structure import StructureRecord, StructureSet


def test_transform_outputs_are_downloadable_checksum_verified_xyz(tmp_path):
    result = StepResult(
        step_id="dedup",
        status=StepStatus.COMPLETED,
        structures=StructureSet(
            (
                StructureRecord(
                    id="kept",
                    atoms=("O", "H"),
                    coordinates=((0, 0, 0), (0, 0, 1)),
                    charge=0,
                    multiplicity=1,
                ),
            )
        ),
    )
    artifacts = publish_transform_structures(
        result, run_root=str(tmp_path), generation_id="generation"
    )
    assert len(artifacts) == 1
    artifact = artifacts[0]
    path = tmp_path / artifact.locator.path
    assert path.read_text().splitlines()[0] == "2"
    assert path.read_text().splitlines()[-1] == "H 0 0 1"
    assert artifact.checksum == "sha256:" + sha256(path.read_bytes()).hexdigest()
    assert artifact.subject_structure_id == "kept"
    assert not result.artifacts  # exports do not mutate semantic truth
    again = publish_transform_structures(result, run_root=str(tmp_path), generation_id="generation")
    assert artifacts == again


def test_failed_or_empty_transform_does_not_fabricate_structure(tmp_path):
    assert (
        publish_transform_structures(
            SimpleNamespace(status=StepStatus.FAILED),
            run_root=str(tmp_path),
            generation_id="generation",
        )
        == ()
    )
    assert (
        publish_transform_structures(
            StepResult(step_id="empty", status=StepStatus.COMPLETED),
            run_root=str(tmp_path),
            generation_id="generation",
        )
        == ()
    )
