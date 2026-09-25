"""R4.5 — Output Manifest V2 and Workflow Stats V2 (M1–M12, STATS1–6).

The manifest is the completed-output publication (the state remains the
execution-progress authority). V2 identity is the stable step ID; the label
is a display snapshot; artifact paths are relative and work-root safe. V1
manifest/stats behavior is pinned unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from confflow.application.execution.errors import ErrorCode, ExecutionServiceError
from confflow.application.execution.workflow_adapter import _load_artifacts, _load_stats
from confflow.contract import (
    OUTPUT_MANIFEST_SCHEMA,
    OUTPUT_MANIFEST_SCHEMA_V2,
    WORKFLOW_STATS_SCHEMA,
    WORKFLOW_STATS_SCHEMA_V2,
)
from confflow.workflow.v3_runtime import run_v3_workflow
from tests.test_workflow_v3_runtime import _FakeHandlers, _write_xyz

V3 = "confflow.workflow.v3"

# Hermetic CI (via tests.test_workflow_v3_runtime fixtures): bare "orca"/"g16"
# defaults resolve against fake executables on PATH; no system QC needed.
pytestmark = pytest.mark.usefixtures("fake_qc_executables_on_path")

_STEPS = [
    {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
]


def _run(tmp_path: Path, steps: list[dict[str, Any]], monkeypatch, **kw: Any) -> dict[str, Any]:
    _FakeHandlers(monkeypatch)
    _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "wf.yaml"
    config.write_text(json.dumps({"schema": V3, "steps": steps}), encoding="utf-8")
    return run_v3_workflow(
        input_xyz=[str(tmp_path / "input.xyz")],
        config_file=str(config),
        work_dir=str(tmp_path / "work"),
        **kw,
    )


def _manifest(work: Path) -> dict[str, Any]:
    return json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))


def _stats(work: Path) -> dict[str, Any]:
    return json.loads((work / "workflow_stats.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Manifest writer (M1–M6)
# ---------------------------------------------------------------------------
class TestManifestWriter:
    def test_m1_single_terminal(self, tmp_path: Path, monkeypatch) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        manifest = _manifest(tmp_path / "work")
        assert manifest["content_schema"] == "confflow.output_manifest.v2"
        assert manifest["terminals"] == [
            {
                "id": "s002",
                "label": None,
                "artifacts": ["steps/s002/result.xyz"],
            }
        ]

    def test_m2_multiple_terminals(self, tmp_path: Path, monkeypatch) -> None:
        _run(
            tmp_path,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            ],
            monkeypatch,
        )
        manifest = _manifest(tmp_path / "work")
        assert [entry["id"] for entry in manifest["terminals"]] == ["s001", "s002"]
        assert all(
            entry["artifacts"] == [f"steps/{entry['id']}/search.xyz"]
            for entry in manifest["terminals"]
        )

    def test_m3_duplicate_labels_distinct_ids(self, tmp_path: Path, monkeypatch) -> None:
        _run(
            tmp_path,
            [
                {
                    "id": "s001",
                    "label": "Optimize",
                    "type": "confgen",
                    "inputs": [],
                    "params": {"chains": ["1-2"]},
                },
                {
                    "id": "s002",
                    "label": "Optimize",
                    "type": "confgen",
                    "inputs": [],
                    "params": {"chains": ["1-2"]},
                },
            ],
            monkeypatch,
        )
        manifest = _manifest(tmp_path / "work")
        assert [entry["id"] for entry in manifest["terminals"]] == ["s001", "s002"]
        assert {entry["label"] for entry in manifest["terminals"]} == {"Optimize"}

    def test_m4_m5_label_rename_and_reorder_identity_unchanged(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        first = _run(tmp_path, _STEPS, monkeypatch)
        base_manifest = _manifest(tmp_path / "work")
        base_stats = _stats(tmp_path / "work")

        renamed = json.loads(json.dumps(_STEPS))
        renamed[1]["label"] = "Renamed"
        second = _run(tmp_path, renamed, monkeypatch)

        reordered = [renamed[1], renamed[0]]
        third = _run(tmp_path, reordered, monkeypatch)

        stats = _stats(tmp_path / "work")
        # ids and artifact paths identical across rename and reorder
        assert [s["id"] for s in stats["steps"]] == [s["id"] for s in base_stats["steps"]]
        assert {s["id"] for s in stats["steps"]} == {"s001", "s002"}
        assert first["final_output"].endswith("steps/s002/result.xyz")
        assert second["final_output"].endswith("steps/s002/result.xyz")
        assert third["final_output"].endswith("steps/s002/result.xyz")
        assert base_manifest["terminals"][0]["id"] == "s002"

    def test_m4_label_snapshot_differs(self, tmp_path: Path, monkeypatch) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        first = _manifest(tmp_path / "work")
        work = tmp_path / "work"
        stats = json.loads((work / "workflow_stats.json").read_text(encoding="utf-8"))
        assert stats["steps"][0]["label"] is None

        renamed = json.loads(json.dumps(_STEPS))
        renamed[0]["label"] = "Generate"
        _run(tmp_path, renamed, monkeypatch)
        second = _manifest(tmp_path / "work")
        assert first["terminals"][0]["id"] == second["terminals"][0]["id"] == "s002"
        # the label is a snapshot: it may drift freely, identity does not
        assert second["terminals"][0]["label"] in {None, "Generate"}

    def test_m6_disabled_terminal_publishes_effective_output(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _run(
            tmp_path,
            _STEPS
            + [
                {
                    "id": "s003",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s002"],
                    "params": {"keyword": "HF"},
                },
            ],
            monkeypatch,
        )
        manifest = _manifest(tmp_path / "work")
        # the declared terminal s003 is disabled: no skipped-step artifact is
        # invented — the entry carries the declared terminal's stable ID and
        # the *effective* producer's artifact (s002's result) as the payload
        assert manifest["terminals"] == [
            {"id": "s003", "label": None, "artifacts": ["steps/s002/result.xyz"]}
        ]

    def test_zero_terminals_manifest(self, tmp_path: Path, monkeypatch) -> None:
        """A workflow with every step disabled publishes an empty terminal list."""
        _run(
            tmp_path,
            [
                {
                    "id": "s001",
                    "type": "calc",
                    "enabled": False,
                    "inputs": [],
                    "params": {"keyword": "HF"},
                },
            ],
            monkeypatch,
        )
        manifest = _manifest(tmp_path / "work")
        assert manifest["terminals"] == []
        stats = _stats(tmp_path / "work")
        assert stats["steps"][0]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Stats writer (STATS1–4, STATS5-writer-side)
# ---------------------------------------------------------------------------
class TestStatsWriter:
    def test_stats1_keyed_by_stable_id(self, tmp_path: Path, monkeypatch) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        stats = _stats(tmp_path / "work")
        assert stats["content_schema"] == "confflow.workflow_stats.v2"
        assert [step["id"] for step in stats["steps"]] == ["s001", "s002"]
        assert all("name" not in step for step in stats["steps"])
        # durable terminal identity: stable ID, not label
        assert list(stats["terminal_outputs"]) == ["s002"]
        assert stats["terminal_outputs"]["s002"][0].endswith("steps/s002/result.xyz")

    def test_stats2_duplicate_labels_safe(self, tmp_path: Path, monkeypatch) -> None:
        steps = [
            {
                "id": "s001",
                "label": "Same",
                "type": "confgen",
                "inputs": [],
                "params": {"chains": ["1-2"]},
            },
            {
                "id": "s002",
                "label": "Same",
                "type": "calc",
                "inputs": ["s001"],
                "params": {"keyword": "HF"},
            },
        ]
        _run(tmp_path, steps, monkeypatch)
        stats = _stats(tmp_path / "work")
        assert [step["id"] for step in stats["steps"]] == ["s001", "s002"]
        assert {step["label"] for step in stats["steps"]} == {"Same"}

    def test_stats3_display_label_update_on_resume(self, tmp_path: Path, monkeypatch) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        before = _stats(tmp_path / "work")
        renamed = json.loads(json.dumps(_STEPS))
        renamed[1]["label"] = "Renamed"
        _run(tmp_path, renamed, monkeypatch, resume=True)
        after = _stats(tmp_path / "work")
        assert [s["id"] for s in after["steps"]] == [s["id"] for s in before["steps"]]
        labels = {s["id"]: s["label"] for s in after["steps"]}
        assert labels["s002"] == "Renamed"

    def test_stats4_reorder_neutral_identity(self, tmp_path: Path, monkeypatch) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        before = _stats(tmp_path / "work")
        _run(tmp_path, [_STEPS[1], _STEPS[0]], monkeypatch)
        after = _stats(tmp_path / "work")
        assert [s["id"] for s in after["steps"]] == [s["id"] for s in before["steps"]]

    def test_stats5_completed_run_artifacts_exist(self, tmp_path: Path, monkeypatch) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        stats = _stats(tmp_path / "work")
        work = tmp_path / "work"
        for output in stats["final_outputs"]:
            assert (work / output if not Path(output).is_absolute() else Path(output)).is_file()
        assert stats["final_conformers"] >= 1
        assert stats["definition_fingerprint"].startswith("sha256:")
        assert stats["execution_fingerprint"].startswith("sha256:")


# ---------------------------------------------------------------------------
# Manifest loader (M7–M11, STATS5-loader-side)
# ---------------------------------------------------------------------------
class TestManifestLoader:
    def test_m7_v2_manifest_loaded_into_service_artifacts(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        work = tmp_path / "work"
        artifacts = _load_artifacts(str(work))
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.terminal == "s002"
        assert artifact.path == "steps/s002/result.xyz"
        assert artifact.content_schema == OUTPUT_MANIFEST_SCHEMA_V2
        assert artifact.size > 0
        assert len(artifact.sha256) == 64

    def test_m8_unknown_schema_fails_closed(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        manifest = work / "output_manifest.json"
        manifest.write_text(
            json.dumps({"content_schema": "confflow.output_manifest.v999", "terminals": []}),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    def test_m9_manifest_rejects_escaping_and_absolute_paths(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        manifest = work / "output_manifest.json"
        outside = tmp_path / "outside.xyz"
        outside.write_text("outside", encoding="utf-8")

        for bad_path in ["/etc/passwd", str(outside), "../outside.xyz", "foo/../../outside.xyz"]:
            manifest.write_text(
                json.dumps(
                    {
                        "content_schema": OUTPUT_MANIFEST_SCHEMA_V2,
                        "terminals": [{"id": "s001", "artifacts": [bad_path]}],
                    }
                ),
                encoding="utf-8",
            )
            with pytest.raises(ExecutionServiceError) as exc_info:
                _load_artifacts(str(work))
            assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    def test_m10_manifest_rejects_missing_artifact(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        manifest = work / "output_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "content_schema": OUTPUT_MANIFEST_SCHEMA_V2,
                    "terminals": [{"id": "s001", "artifacts": ["steps/s001/missing.xyz"]}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    def test_m11_manifest_rejects_malformed_shape(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        work.mkdir()
        manifest = work / "output_manifest.json"

        # 1. Unknown field in top-level payload
        manifest.write_text(
            json.dumps({"content_schema": OUTPUT_MANIFEST_SCHEMA_V2, "terminals": [], "extra": 1}),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        # 2. Terminals is not a list
        manifest.write_text(
            json.dumps({"content_schema": OUTPUT_MANIFEST_SCHEMA_V2, "terminals": {}}),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        # 3. Terminal entry is not a dict
        manifest.write_text(
            json.dumps({"content_schema": OUTPUT_MANIFEST_SCHEMA_V2, "terminals": ["bad"]}),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        # 4. Unknown field in terminal entry
        manifest.write_text(
            json.dumps(
                {
                    "content_schema": OUTPUT_MANIFEST_SCHEMA_V2,
                    "terminals": [{"id": "s001", "artifacts": [], "unknown_field": True}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        # 5. Missing or invalid id
        manifest.write_text(
            json.dumps(
                {
                    "content_schema": OUTPUT_MANIFEST_SCHEMA_V2,
                    "terminals": [{"artifacts": []}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        # 6. Invalid label type
        manifest.write_text(
            json.dumps(
                {
                    "content_schema": OUTPUT_MANIFEST_SCHEMA_V2,
                    "terminals": [{"id": "s001", "label": 123, "artifacts": []}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

        # 7. Artifacts is not a list of strings
        manifest.write_text(
            json.dumps(
                {
                    "content_schema": OUTPUT_MANIFEST_SCHEMA_V2,
                    "terminals": [{"id": "s001", "artifacts": [123]}],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_artifacts(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED

    def test_stats5_loader_validates_v2_stats(self, tmp_path: Path, monkeypatch) -> None:
        _run(tmp_path, _STEPS, monkeypatch)
        work = tmp_path / "work"
        stats = _load_stats(str(work))
        assert stats is not None
        assert stats["content_schema"] == WORKFLOW_STATS_SCHEMA_V2

        # Corrupt stats content_schema
        (work / "workflow_stats.json").write_text(
            json.dumps({"content_schema": "confflow.workflow_stats.unknown"}),
            encoding="utf-8",
        )
        with pytest.raises(ExecutionServiceError) as exc_info:
            _load_stats(str(work))
        assert exc_info.value.code is ErrorCode.ARTIFACT_INTEGRITY_FAILED


# ---------------------------------------------------------------------------
# V1 goldens (M12, STATS6)
# ---------------------------------------------------------------------------
def test_m12_stats6_v1_manifest_and_stats_unchanged(tmp_path: Path, monkeypatch) -> None:
    from confflow.workflow.engine import run_workflow

    def fake_confgen(step_dir, *args, **kwargs):
        output = Path(step_dir) / "search.xyz"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("1\nfake\nH 0 0 0\n", encoding="utf-8")

        class _Result:
            output_path = str(output)
            reused_existing = False
            copied_multi_frame = False

        return _Result()

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
    _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "v2.yaml"
    document = {"steps": [{"name": "gen", "type": "confgen", "params": {"chains": "1-2"}}]}
    config.write_text(json.dumps(document), encoding="utf-8")
    work = tmp_path / "v1work"
    run_workflow([str(tmp_path / "input.xyz")], str(config), str(work))
    manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
    stats = json.loads((work / "workflow_stats.json").read_text(encoding="utf-8"))
    assert manifest["content_schema"] == OUTPUT_MANIFEST_SCHEMA
    assert isinstance(manifest["terminals"], dict)  # v1 shape: {name: [paths]}
    assert stats["content_schema"] == WORKFLOW_STATS_SCHEMA
    assert all("name" in step for step in stats["steps"])
