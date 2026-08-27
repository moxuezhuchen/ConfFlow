"""Immutable durable v2 fixture compatibility tests."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from confflow.calc.artifacts import (
    CalcArtifactManager,
    CalcManifestCompatibilityError,
)
from confflow.config.models import CalcStepParams, GlobalOptions
from confflow.workflow.state import (
    WorkflowStateCompatibilityError,
    WorkflowStateStore,
)
from confflow.workflow.step_handlers import (
    ConfgenSignatureCompatibilityError,
    _confgen_signature_path,
    _load_confgen_step_signature,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "durable" / "v2"

FIXTURE_DIGESTS = {
    "canonical/alias-default.yaml": "dc6b09bd949d195c1a3ec07b5e1739336863aed6feb088584d1882fd9d396f51",
    "calc/input.xyz": "cb2e57ed3a08c73122149d570597f59cf04296b144f52df3d9cb6ab043db1efc",
    "calc/manifest-malformed.json": "5715ea11351f96c1faee52d83ceb8edf41612427a452f7acc780b6bc05072500",
    "calc/manifest-v1-completed.json": "fe0f556be6b77b1af05babb6a230027820f92eafc86647ded7f998e95485a0df",
    "calc/manifest-v999-unknown.json": "390911aa5b67e33dc39ff40c206eaca0e38be070dfdb2ae45d1f604da7c37035",
    "calc/output.xyz": "d079cb2d1ff2076340f16609f8899daac9254b41a6e1dfd9b1a8ae6695398adf",
    "calc/sentinel.txt": "168066530328f0afd60b5660948bf20bc95ce615a045b8ec2d75dcb454365339",
    "confgen/sentinel-search.xyz": "c602829e7e084e115523440377d3e07e093c18015ff6bf14cae868958334b93d",
    "confgen/signature-future.txt": "a642062269cf2af552c611d76772178cf0b29e3811a3be6bc66769687be00ec0",
    "confgen/signature-malformed.txt": "63216a74b366b4a3852bd9c94641ccb7f7b93b036a1f67b8b447666b08f0bcf2",
    "confgen/signature-v1.txt": "086c330a552315adc08472c445b2eaf4f0bb674e6a53cbeb9ad3ce7c44d3a0bb",
    "workflow_state/sentinel-output.xyz": "b1609a7d76e0b6849d6e1095a9651c02e57df43eff7b3512363a01310579d6f8",
    "workflow_state/state-legacy-no-schema.json": "513ba68eac8b40f7b1699ca7f44441f7037cd203754b9ab600f7a01bf0e17f47",
    "workflow_state/state-malformed-json.json": "5715ea11351f96c1faee52d83ceb8edf41612427a452f7acc780b6bc05072500",
    "workflow_state/state-malformed-step.json": "b723d848e82c047d05cdffdec8238a29b04f845c914a590db8e1ea80b3a4b49c",
    "workflow_state/state-v1-bound-completed.json": "2dabc57c3d7609b463eef35741a0c90ff9bcdc80ba505b0547358ba55a57afcd",
    "workflow_state/state-v1-bound-malformed-binding.json": "177aef6b3a6d65f27acefca1d190a25d2e057af13cb20b970043fd97f775a0ce",
    "workflow_state/state-v1-bound-unknown-binding.json": "cd5b1558d58bf1ba38deed3174d957e9b11b52fdad8a5192cce711771c09fcb3",
    "workflow_state/state-v1-completed.json": "0f10d3434791a329878ed76fac06c61eb2c221500ed3c7bda0c1ee7e827da9ff",
    "workflow_state/state-v99-unknown.json": "913d2d100fa7ea195230cfbcd952fca3f065490dad74f955c3fa787a5d01244b",
}


def _copy_fixture(relative: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_ROOT / relative, destination)


def _golden_calc_config() -> CalcStepParams:
    return CalcStepParams.from_params(
        {"iprog": "orca", "itask": "sp", "keyword": "HF"},
        GlobalOptions.from_mapping({}),
    )


def test_durable_v2_fixture_manifest_and_bytes_are_frozen() -> None:
    actual = {
        str(path.relative_to(FIXTURE_ROOT)) for path in FIXTURE_ROOT.rglob("*") if path.is_file()
    }
    assert actual == set(FIXTURE_DIGESTS)
    for relative, expected in FIXTURE_DIGESTS.items():
        digest = hashlib.sha256((FIXTURE_ROOT / relative).read_bytes()).hexdigest()
        assert digest == expected, relative


@pytest.mark.parametrize(
    "relative",
    ("workflow_state/state-v1-completed.json", "workflow_state/state-legacy-no-schema.json"),
)
def test_durable_v2_old_workflow_state_loads(relative: str, tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    _copy_fixture(relative, work_dir / ".workflow_state.json")
    _copy_fixture("workflow_state/sentinel-output.xyz", work_dir / "sentinel-output.xyz")

    loaded = WorkflowStateStore(str(work_dir)).load()

    assert loaded is not None
    assert loaded.run_id == "run-v2-fixture"
    assert loaded.steps["calc"].status == "completed"
    assert loaded.steps["calc"].executor_handle_data == {"remote_job_id": "fixture-42"}
    assert (work_dir / "sentinel-output.xyz").read_bytes() == (
        FIXTURE_ROOT / "workflow_state/sentinel-output.xyz"
    ).read_bytes()


@pytest.mark.parametrize(
    "relative",
    (
        "workflow_state/state-v99-unknown.json",
        "workflow_state/state-v1-bound-unknown-binding.json",
        "workflow_state/state-v1-bound-malformed-binding.json",
        "workflow_state/state-malformed-step.json",
        "workflow_state/state-malformed-json.json",
    ),
)
def test_durable_v2_bad_workflow_state_fails_closed_and_preserves_sentinel(
    relative: str, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    state_path = work_dir / ".workflow_state.json"
    sentinel = work_dir / "sentinel-output.xyz"
    _copy_fixture(relative, state_path)
    _copy_fixture("workflow_state/sentinel-output.xyz", sentinel)
    before_state = state_path.read_bytes()
    before_sentinel = sentinel.read_bytes()

    with pytest.raises(WorkflowStateCompatibilityError):
        WorkflowStateStore(str(work_dir)).load()

    assert state_path.read_bytes() == before_state
    assert sentinel.read_bytes() == before_sentinel


def test_durable_v2_bound_workflow_state_loads_and_exposes_binding(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    _copy_fixture(
        "workflow_state/state-v1-bound-completed.json",
        work_dir / ".workflow_state.json",
    )
    loaded = WorkflowStateStore(str(work_dir)).load()

    assert loaded is not None
    assert loaded.run_id == "run-v2-bound-fixture"
    assert loaded.config_binding is not None
    assert loaded.config_binding.to_dict() == {
        "schema": "confflow.workflow_binding.v1",
        "workflow_schema": "confflow.workflow.v2",
        "workflow_schema_sha256": "2" * 64,
        "fingerprint": "sha256:" + "1" * 64,
    }


def test_durable_v2_old_calc_manifest_reuses_fixture_output(tmp_path: Path) -> None:
    step_dir = tmp_path / "calc"
    input_path = tmp_path / "input.xyz"
    _copy_fixture("calc/input.xyz", input_path)
    _copy_fixture("calc/manifest-v1-completed.json", step_dir / "manifest.json")
    _copy_fixture("calc/output.xyz", step_dir / "output.xyz")

    manager = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=_golden_calc_config(),
        input_path=input_path,
    )
    prepared = manager.prepare(resume=True)

    assert prepared.reusable_output == step_dir / "output.xyz"
    assert prepared.reusable_output.read_bytes() == (FIXTURE_ROOT / "calc/output.xyz").read_bytes()


@pytest.mark.parametrize(
    "relative",
    ("calc/manifest-v999-unknown.json", "calc/manifest-malformed.json"),
)
def test_durable_v2_bad_calc_manifest_fails_closed_and_preserves_sentinel(
    relative: str, tmp_path: Path
) -> None:
    step_dir = tmp_path / "calc"
    input_path = tmp_path / "input.xyz"
    manifest = step_dir / "manifest.json"
    sentinel = step_dir / "sentinel.txt"
    _copy_fixture("calc/input.xyz", input_path)
    _copy_fixture(relative, manifest)
    _copy_fixture("calc/sentinel.txt", sentinel)
    before_manifest = manifest.read_bytes()
    before_sentinel = sentinel.read_bytes()

    manager = CalcArtifactManager(
        step_dir,
        step_name="calc",
        config=_golden_calc_config(),
        input_path=input_path,
    )
    with pytest.raises(CalcManifestCompatibilityError):
        manager.prepare(resume=False)

    assert manifest.read_bytes() == before_manifest
    assert sentinel.read_bytes() == before_sentinel


def test_durable_v2_old_confgen_signature_loads(tmp_path: Path) -> None:
    step_dir = tmp_path / "confgen"
    signature = step_dir / ".confgen_signature"
    _copy_fixture("confgen/signature-v1.txt", signature)

    assert _load_confgen_step_signature(str(step_dir)) == signature.read_text().strip()


@pytest.mark.parametrize(
    "relative",
    ("confgen/signature-future.txt", "confgen/signature-malformed.txt"),
)
def test_durable_v2_bad_confgen_signature_fails_closed_and_preserves_sentinel(
    relative: str, tmp_path: Path
) -> None:
    step_dir = tmp_path / "confgen"
    signature = Path(_confgen_signature_path(str(step_dir)))
    sentinel = step_dir / "search.xyz"
    _copy_fixture(relative, signature)
    _copy_fixture("confgen/sentinel-search.xyz", sentinel)
    before_signature = signature.read_bytes()
    before_sentinel = sentinel.read_bytes()

    with pytest.raises(ConfgenSignatureCompatibilityError):
        _load_confgen_step_signature(str(step_dir))

    assert signature.read_bytes() == before_signature
    assert sentinel.read_bytes() == before_sentinel
