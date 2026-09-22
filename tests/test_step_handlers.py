#!/usr/bin/env python3

"""Tests for workflow step handlers on the typed execution boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.calc.runner import CalcStepResult
from confflow.core.exceptions import ConfFlowError
from confflow.core.io import read_xyz_file
from confflow.workflow.step_handlers import (
    ConfgenSignatureCompatibilityError,
    StepExecutionResult,
    run_calc_step,
    run_confgen_step,
)


def _xyz(path: Path, multi: bool = False) -> Path:
    text = "1\nframe1\nH 0 0 0\n"
    if multi:
        text += "1\nframe2\nH 0 0 1\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_confgen_multiframe_input_is_copied_and_reused(tmp_path):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    source = _xyz(tmp_path / "multi.xyz", multi=True)

    first = run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={},
        input_files=[str(source)],
    )
    second = run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={},
        input_files=[str(source)],
    )

    assert first.output_path == str(step_dir / "search.xyz")
    assert first.copied_multi_frame is True
    assert second.reused_existing is True
    assert (step_dir / ".confgen_signature").exists()


def test_confgen_fanin_preserves_every_frame_and_metadata(tmp_path):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    left = tmp_path / "left.xyz"
    left.write_text(
        "1\nEnergy=-1 | CID=A000001 | Source=left\nH 0 0 0\n"
        "1\nEnergy=-2 | CID=A000002 | Source=left\nH 0 0 1\n",
        encoding="utf-8",
    )
    right = tmp_path / "right.xyz"
    # Deliberately reuse the left CIDs.  The merge must retain all frames and
    # assign deterministic source-scoped IDs to the collisions.
    right.write_text(
        "1\nEnergy=-3 | CID=A000001 | Source=right\nH 1 0 0\n"
        "1\nEnergy=-4 | CID=A000002 | Source=right\nH 1 0 1\n",
        encoding="utf-8",
    )

    result = run_confgen_step(
        step_dir=str(step_dir),
        current_input=[str(left), str(right)],
        params={},
        input_files=[str(left), str(right)],
    )

    frames = read_xyz_file(result.output_path, strict=True)
    assert result.copied_multi_frame is True
    assert len(frames) == 4
    assert [frame["coords"][0][0] for frame in frames] == [0.0, 0.0, 1.0, 1.0]
    assert [frame["metadata"]["Source"] for frame in frames] == [
        "left",
        "left",
        "right",
        "right",
    ]
    cids = [frame["metadata"]["CID"] for frame in frames]
    assert len(cids) == len(set(cids))
    assert cids[:2] == ["A000001", "A000002"]
    assert cids[2:] == ["B000001", "B000002"]
    assert [frame["metadata"]["SourceCID"] for frame in frames[2:]] == [
        "A000001",
        "A000002",
    ]
    assert [frame["metadata"]["SourceFrame"] for frame in frames[2:]] == [1.0, 2.0]


def test_confgen_fanin_mixed_single_and_multi_frame_inputs(tmp_path):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    single = _xyz(tmp_path / "single.xyz")
    multi = _xyz(tmp_path / "multi.xyz", multi=True)

    result = run_confgen_step(
        step_dir=str(step_dir),
        current_input=[str(single), str(multi)],
        params={},
        input_files=[str(single), str(multi)],
    )

    frames = read_xyz_file(result.output_path, strict=True)
    assert len(frames) == 3
    assert [frame["coords"][0][2] for frame in frames] == [0.0, 0.0, 1.0]


def test_confgen_single_source_cid_conflict_keeps_original_id_trace(tmp_path):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    source = tmp_path / "duplicate.xyz"
    source.write_text(
        "1\nCID=old\nH 0 0 0\n1\nCID=old\nH 0 0 1\n",
        encoding="utf-8",
    )

    result = run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={},
        input_files=[str(source)],
    )

    frames = read_xyz_file(result.output_path, strict=True)
    assert [frame["metadata"]["CID"] for frame in frames] == ["old", "A000002"]
    assert frames[1]["metadata"]["SourceCID"] == "old"
    assert frames[1]["metadata"]["SourceFrame"] == 2.0


def test_confgen_fanin_rejects_invalid_later_frame_without_replacing_output(tmp_path):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    existing = step_dir / "search.xyz"
    existing.write_text("1\nold\nH 9 9 9\n", encoding="utf-8")
    left = _xyz(tmp_path / "left.xyz", multi=True)
    invalid = tmp_path / "invalid.xyz"
    invalid.write_text("1\nvalid\nH 1 0 0\n1\ntruncated\n", encoding="utf-8")

    with pytest.raises(ConfFlowError, match="invalid confgen XYZ input"):
        run_confgen_step(
            step_dir=str(step_dir),
            current_input=[str(left), str(invalid)],
            params={},
            input_files=[str(left), str(invalid)],
        )

    assert existing.read_text(encoding="utf-8") == "1\nold\nH 9 9 9\n"
    assert not (step_dir / "search.xyz.tmp").exists()


def test_confgen_recomputes_when_params_change(tmp_path, monkeypatch):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    source = _xyz(tmp_path / "single.xyz")

    def fake_run_generation(**kwargs):
        Path("search.xyz").write_text(
            f"1\nangle={kwargs['angle_step']}\nH 0 0 0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "confflow.workflow.step_handlers.confgen.run_generation", fake_run_generation
    )

    run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={"angle_step": 120},
        input_files=[str(source), str(source)],
    )
    result = run_confgen_step(
        step_dir=str(step_dir),
        current_input=str(source),
        params={"angle_step": 60},
        input_files=[str(source), str(source)],
    )

    assert result.cleaned_stale_artifacts is True
    assert "angle=60" in (step_dir / "search.xyz").read_text(encoding="utf-8")


def test_calc_step_builds_typed_request_and_tracks_failed_file(tmp_path, monkeypatch):
    input_xyz = _xyz(tmp_path / "input.xyz")
    step_dir = tmp_path / "step_02_calc"
    output = step_dir / "result.xyz"
    failed = step_dir / "failed.xyz"

    class FakeRunner:
        def run(self, request):
            assert request.step_name == "calc1"
            assert request.input_xyz == str(input_xyz)
            assert request.config.program == "orca"
            assert request.config.task == "sp"
            step_dir.mkdir()
            output.write_text("1\nok\nH 0 0 0\n", encoding="utf-8")
            failed.write_text("1\nbad\nH 0 0 1\n", encoding="utf-8")
            return CalcStepResult(
                output_path=str(output),
                failed_path=str(failed),
                total_tasks=2,
                succeeded=1,
                failed=1,
                cleaned_stale_artifacts=True,
            )

    class Tracker:
        def __init__(self):
            self.calls = []

        def append(self, failed_path, step_name):
            self.calls.append((failed_path, step_name))

    tracker = Tracker()
    monkeypatch.setattr("confflow.workflow.step_handlers.CalcStepRunner", FakeRunner)

    result = run_calc_step(
        step_dir=str(step_dir),
        current_input=str(input_xyz),
        params={"iprog": "orca", "itask": "sp", "keyword": "HF"},
        global_config={},
        root_dir=str(tmp_path),
        steps=[],
        failure_tracker=tracker,
        step_name="calc1",
    )

    assert isinstance(result, StepExecutionResult)
    assert result.output_path == str(output)
    assert result.failed_path == str(failed)
    assert result.cleaned_stale_artifacts is True
    assert tracker.calls == [(str(failed), "calc1")]


def test_calc_step_rejects_multiple_inputs_without_confgen(tmp_path):
    with pytest.raises(ConfFlowError, match="exactly one input file"):
        run_calc_step(
            step_dir=str(tmp_path / "step"),
            current_input=["a.xyz", "b.xyz"],
            params={"keyword": "HF"},
            global_config={},
            root_dir=str(tmp_path),
            steps=[],
            failure_tracker=None,
            step_name="calc",
        )


@pytest.mark.parametrize("signature", ("future:v9", "sha256:not-a-digest"))
def test_unknown_confgen_signature_fails_closed_without_cleanup(tmp_path, signature):
    step_dir = tmp_path / "step_01_confgen"
    step_dir.mkdir()
    source = _xyz(tmp_path / "multi.xyz", multi=True)
    output = step_dir / "search.xyz"
    output.write_text("preserve", encoding="utf-8")
    signature_path = step_dir / ".confgen_signature"
    signature_path.write_text(signature, encoding="utf-8")

    with pytest.raises(ConfgenSignatureCompatibilityError):
        run_confgen_step(
            step_dir=str(step_dir),
            current_input=str(source),
            params={},
            input_files=[str(source)],
        )

    assert output.read_text(encoding="utf-8") == "preserve"
    assert signature_path.read_text(encoding="utf-8") == signature
