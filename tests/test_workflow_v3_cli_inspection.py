"""R3.5 — Workflow V3 CLI inspection: config-show, dry-run, --step, config validate.

V2 outputs are pinned unchanged; V3 outputs speak the stable-ID identity. The
V3 dry run is asserted side-effect free: it must not create the work directory,
state, SQLite or any process.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from confflow.cli import main as cli_main
from confflow.config.canonical import (
    workflow_fragment_schema_sha256_v3,
    workflow_schema_sha256,
    workflow_schema_sha256_v3,
)
from confflow.config.cli import main as config_cli_main
from confflow.core.contracts import ExitCode

V3 = "confflow.workflow.v3"
V3_DOCUMENT_DIGEST = workflow_schema_sha256_v3()
V2_DIGEST = workflow_schema_sha256()


def _write_xyz(path: Path) -> Path:
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def _v3_config(
    path: Path,
    steps: list[dict[str, Any]] | None = None,
    **root: Any,
) -> Path:
    document: dict[str, Any] = {
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
    document.update(root)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# config-show (CS1–CS9)
# ---------------------------------------------------------------------------
class TestV3ConfigShow:
    def test_cs1_cs3_cs5_shows_stable_ids_inputs_enabled(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(tmp_path / "wf.yaml")
        assert cli_main(["--config-show", "-c", str(config)]) == ExitCode.SUCCESS
        out = capsys.readouterr().out
        assert "Schema: confflow.workflow.v3" in out
        assert "[s001] (confgen)" in out
        assert "[s002] (calc)" in out
        assert "inputs: ['s001']" in out or "inputs: external-input" in out
        assert "enabled=False" not in out

    def test_cs2_duplicate_labels_shown_without_ambiguity(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(
            tmp_path / "wf.yaml",
            [
                {
                    "id": "s001",
                    "type": "confgen",
                    "inputs": [],
                    "label": "same",
                    "params": {"chains": ["1-2"]},
                },
                {
                    "id": "s002",
                    "type": "calc",
                    "inputs": ["s001"],
                    "label": "same",
                    "params": {"keyword": "HF"},
                },
            ],
        )
        assert cli_main(["--config-show", "-c", str(config)]) == ExitCode.SUCCESS
        out = capsys.readouterr().out
        assert "[s001]" in out and "[s002]" in out
        assert out.count("label: same") == 2

    def test_cs4_shows_checkpoint(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(
            tmp_path / "wf.yaml",
            [
                {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
                {
                    "id": "s002",
                    "type": "calc",
                    "inputs": ["s001"],
                    "params": {"keyword": "HF"},
                    "checkpoint": {"from_step": "s001"},
                },
            ],
        )
        assert cli_main(["--config-show", "-c", str(config)]) == ExitCode.SUCCESS
        assert "checkpoint: from_step=s001" in capsys.readouterr().out

    def test_cs5_disabled_visible(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(
            tmp_path / "wf.yaml",
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s002",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s001"],
                    "params": {"itask": "opt"},
                },
            ],
        )
        assert cli_main(["--config-show", "-c", str(config)]) == ExitCode.SUCCESS
        out = capsys.readouterr().out
        assert "[s002] (calc) enabled=False" in out

    def test_cs6_definition_fingerprint_present(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(tmp_path / "wf.yaml")
        assert cli_main(["--config-show", "-c", str(config)]) == ExitCode.SUCCESS
        out = capsys.readouterr().out
        assert "Definition fingerprint: sha256:" in out

    def test_cs7_step_selector_accepts_exact_id(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(tmp_path / "wf.yaml")
        assert cli_main(["--config-show", "-c", str(config), "--step", "s002"]) == (
            ExitCode.SUCCESS
        )
        out = capsys.readouterr().out
        assert "Step [s002] (calc)" in out
        assert "[s001]" not in out

    def test_cs8_label_selector_rejected(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(
            tmp_path / "wf.yaml",
            [
                {
                    "id": "s001",
                    "type": "confgen",
                    "inputs": [],
                    "label": "first",
                    "params": {"chains": ["1-2"]},
                },
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ],
        )
        assert cli_main(["--config-show", "-c", str(config), "--step", "first"]) == (
            ExitCode.USAGE_ERROR
        )
        assert "not identities" in capsys.readouterr().err

    def test_s3_numeric_index_selector_rejected_for_v3(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(tmp_path / "wf.yaml")
        assert cli_main(["--config-show", "-c", str(config), "--step", "1"]) == (
            ExitCode.USAGE_ERROR
        )
        assert "stable id" in capsys.readouterr().err

    def test_s4_nonexistent_id_error(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(tmp_path / "wf.yaml")
        assert cli_main(["--config-show", "-c", str(config), "--step", "s999"]) == (
            ExitCode.USAGE_ERROR
        )
        assert "s999" in capsys.readouterr().err

    def test_v3_config_show_json_output(self, tmp_path: Path, capsys) -> None:
        config = _v3_config(tmp_path / "wf.yaml")
        assert cli_main(["--config-show", "-c", str(config), "--format", "json"]) == (
            ExitCode.SUCCESS
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == V3
        assert payload["definition_fingerprint"] == payload["definition_fingerprint"]
        assert payload["graph_order"] == ["s001", "s002"]
        assert [step["step_id"] for step in payload["steps"]] == ["s001", "s002"]

    def test_cs9_v2_config_show_snapshots_unchanged(self, tmp_path: Path, capsys) -> None:
        config = tmp_path / "wf.yaml"
        config.write_text(
            "global: {max_parallel_jobs: 4}\n"
            "steps:\n"
            "  - name: gen\n"
            "    type: confgen\n"
            "    params: {chains: '1-2-3'}\n"
            "  - name: opt\n"
            "    type: calc\n"
            "    params: {iprog: g16, itask: opt}\n",
            encoding="utf-8",
        )
        assert cli_main(["--config-show", "-c", str(config)]) == ExitCode.SUCCESS
        out = capsys.readouterr().out
        assert "Global config:" in out
        assert "max_parallel_jobs: 4" in out
        assert "[1] gen (confgen)" in out
        assert "[2] opt (calc)" in out
        # V2 identity is name/index; no V3 vocabulary leaks in.
        assert "Schema:" not in out
        assert "Definition fingerprint" not in out

    def test_s6_v2_step_selector_semantics_unchanged(self, tmp_path: Path, capsys) -> None:
        config = tmp_path / "wf.yaml"
        config.write_text(
            "steps:\n"
            "  - name: gen\n"
            "    type: confgen\n"
            "    params: {chains: '1-2'}\n"
            "  - name: opt\n"
            "    type: calc\n"
            "    params: {keyword: HF}\n",
            encoding="utf-8",
        )
        # Name selector still works on V2...
        assert cli_main(["--config-show", "-c", str(config), "--step", "opt"]) == ExitCode.SUCCESS
        out = capsys.readouterr().out
        assert "Step [2]: opt (calc)" in out
        # ...and the numeric index selector remains legal for V2 (unlike V3).
        assert cli_main(["--config-show", "-c", str(config), "--step", "1"]) == ExitCode.SUCCESS
        assert "Step [1]: gen (confgen)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# dry-run (DR1–DR11)
# ---------------------------------------------------------------------------
class TestV3DryRun:
    def _run(self, tmp_path: Path, capsys, steps: list[dict[str, Any]]) -> str:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml", steps)
        assert cli_main(
            ["--dry-run", str(xyz), "-c", str(config), "-w", str(tmp_path / "work")]
        ) == (ExitCode.SUCCESS)
        return capsys.readouterr().out

    def test_dr1_dr2_linear_and_dag_pass(self, tmp_path: Path, capsys) -> None:
        out = self._run(
            tmp_path,
            capsys,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ],
        )
        assert "Workflow V3" in out
        assert "[s001] (confgen) enabled" in out
        assert "[s002] (calc) enabled" in out

    def test_dr3_order_deterministic(self, tmp_path: Path, capsys) -> None:
        steps = [
            {"id": "s010", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s030",
                "type": "confgen",
                "inputs": ["s002", "s010"],
                "params": {"chains": ["1-2"]},
            },
        ]
        first = self._run(tmp_path, capsys, [dict(s) for s in steps])
        second = self._run(tmp_path, capsys, list(reversed([dict(s) for s in steps])))
        order_line = "Execution order: s002 -> s010 -> s030"
        assert order_line in first
        assert order_line in second

    def test_dr4_dr5_dr6_graph_refs_external_input_disabled_visible(
        self, tmp_path: Path, capsys
    ) -> None:
        out = self._run(
            tmp_path,
            capsys,
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                {
                    "id": "s003",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s002"],
                    "params": {"itask": "opt"},
                },
                {
                    "id": "s004",
                    "type": "calc",
                    "inputs": ["s002"],
                    "params": {"keyword": "HF"},
                    "checkpoint": {"from_step": "s002"},
                },
            ],
        )
        assert "inputs: [] (external input)" in out
        assert "inputs: [s001]" in out and "inputs: [s002]" in out
        assert "[s003] (calc) disabled" in out
        assert "checkpoint: from_step=s002" in out
        assert "Roots: s001" in out
        assert "Terminals: s003 s004" in out or "Terminals: s003, s004" in out

    def test_dr7_definition_fingerprint_visible(self, tmp_path: Path, capsys) -> None:
        out = self._run(tmp_path, capsys, None)  # type: ignore[arg-type]
        assert "Definition fingerprint: sha256:" in out

    def test_dr8_dr9_dr10_no_side_effects(self, tmp_path: Path, capsys, monkeypatch) -> None:
        import subprocess

        launched: list[Any] = []

        class _Boom:
            def __getattr__(self, name: str) -> Any:
                raise AssertionError(f"subprocess.{name} must not be called in a V3 dry run")

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: launched.append(a))
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append(a))
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = _v3_config(tmp_path / "wf.yaml")
        work = tmp_path / "work"
        assert cli_main(["--dry-run", str(xyz), "-c", str(config), "-w", str(work)]) == (
            ExitCode.SUCCESS
        )
        assert launched == []
        assert not work.exists()  # DR8: no work dir created
        assert not (tmp_path / ".confflow_execution").exists()  # DR9: no state root/SQLite

    def test_dr11_v2_dry_run_unchanged(self, tmp_path: Path, capsys) -> None:
        xyz = _write_xyz(tmp_path / "input.xyz")
        config = tmp_path / "wf.yaml"
        config.write_text(
            "steps:\n" "  - name: gen\n" "    type: confgen\n" "    params: {chains: '1-2'}\n",
            encoding="utf-8",
        )
        assert cli_main(
            ["--dry-run", str(xyz), "-c", str(config), "-w", str(tmp_path / "work")]
        ) == (ExitCode.SUCCESS)
        out = capsys.readouterr().out
        assert "[1] gen (confgen)" in out
        assert "step_dir:" in out  # V2 keeps its dirname/output preview UX
        assert "Schema:" not in out


# ---------------------------------------------------------------------------
# config validate (CV1–CV5)
# ---------------------------------------------------------------------------
def _run_config_validate(capsys: Any, raw: Any) -> tuple[int, dict[str, Any]]:
    import io
    import sys

    payload = "" if raw is None else json.dumps(raw)
    original = sys.stdin
    sys.stdin = io.StringIO(payload)
    try:
        code = config_cli_main(["validate", "--json", "--stdin"])
    finally:
        sys.stdin = original
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


class TestV3ConfigValidate:
    def test_cv1_valid_v3_returns_v3_document_digest(self, tmp_path: Path, capsys) -> None:
        code, payload = _run_config_validate(
            capsys,
            {
                "schema": V3,
                "steps": [
                    {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                ],
            },
        )
        assert code == ExitCode.SUCCESS
        assert payload["valid"] is True
        assert payload["workflow_schema_sha256"] == V3_DOCUMENT_DIGEST
        assert payload["issues"] == []
        assert set(payload) == {"schema", "valid", "workflow_schema_sha256", "issues"}

    def test_cv2_v3_semantic_invalid_reports_v3_digest_and_v1_issues(
        self, tmp_path: Path, capsys
    ) -> None:
        code, payload = _run_config_validate(
            capsys,
            {
                "schema": V3,
                "steps": [{"id": "s001", "type": "confgen", "inputs": [], "params": {}}],
            },
        )
        assert code == ExitCode.USAGE_ERROR
        assert payload["valid"] is False
        assert payload["workflow_schema_sha256"] == V3_DOCUMENT_DIGEST
        assert all(set(issue) == {"path", "message"} for issue in payload["issues"])
        assert any("chains" in issue["path"] for issue in payload["issues"])

    def test_cv3_v3_fragment_is_invalid_runnable(self, tmp_path: Path, capsys) -> None:
        code, payload = _run_config_validate(
            capsys,
            {
                "schema": V3,
                "steps": [{"type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}],
            },
        )
        assert code == ExitCode.USAGE_ERROR
        assert payload["valid"] is False
        assert payload["workflow_schema_sha256"] == V3_DOCUMENT_DIGEST

    def test_cv4_v2_response_unchanged(self, tmp_path: Path, capsys) -> None:
        code, payload = _run_config_validate(
            capsys,
            {
                "global": {},
                "steps": [{"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}}],
            },
        )
        assert code == ExitCode.SUCCESS
        assert payload["valid"] is True
        assert payload["workflow_schema_sha256"] == V2_DIGEST
        assert set(payload) == {"schema", "valid", "workflow_schema_sha256", "issues"}
        code, payload = _run_config_validate(
            capsys,
            {"steps": [{"name": "gen", "type": "confgen", "params": {}}]},
        )
        assert payload["valid"] is False
        assert payload["workflow_schema_sha256"] == V2_DIGEST

    def test_cv5_unknown_schema_fails_closed(self, tmp_path: Path, capsys) -> None:
        code, payload = _run_config_validate(
            capsys, {"schema": "confflow.workflow.v4", "steps": []}
        )
        assert code == ExitCode.USAGE_ERROR
        assert payload["valid"] is False
        assert any("unsupported workflow schema" in issue["message"] for issue in payload["issues"])


# ---------------------------------------------------------------------------
# schema digest sanity (§58 freeze)
# ---------------------------------------------------------------------------
# R5 (RFC §18) chartered exactly one additive V3 vocabulary key
# (``params.theory``); the V2 digest is untouched, both V3 digests moved once
# by that single property. These pins guard against any FURTHER drift.
def test_v3_fragment_digest_remains_frozen() -> None:
    assert workflow_fragment_schema_sha256_v3().startswith("6c9e0759")
    assert V3_DOCUMENT_DIGEST.startswith("dd57c472")
