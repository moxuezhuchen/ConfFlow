#!/usr/bin/env python3

"""Tests for engine module (merged)."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

import confflow.blocks.confgen as confgen
import confflow.blocks.viz as viz
from confflow.calc.step_contract import (
    compute_calc_config_signature,
    compute_calc_input_signature,
    record_calc_step_signature,
)
from confflow.config.schema import ConfigSchema
from confflow.core.exceptions import XYZFormatError
from confflow.core.pairs import normalize_pair_list
from confflow.core.types import TaskStatus
from confflow.workflow.config_builder import (
    build_step_dir_name_map,
    build_task_config,
    create_runtask_config,
    load_workflow_config,
)
from confflow.workflow.engine import count_conformers_any, run_workflow, validate_inputs_compatible
from confflow.workflow.helpers import as_list, count_conformers_in_xyz, resolve_step_output
from confflow.workflow.stats import count_task_statuses_in_results_db
from confflow.workflow.step_handlers import CalcStepResult
from confflow.workflow.task_config import (
    _itask_label,
    _normalize_iprog_label,
    build_structured_task_config,
)


def test_as_list():
    assert as_list(None) is None
    assert as_list(1) == [1]
    assert as_list([1, 2]) == [1, 2]


def test_normalize_pair_list_variants():
    assert normalize_pair_list(None) is None
    assert normalize_pair_list([]) == []
    assert normalize_pair_list([1, 2]) == [[1, 2]]
    assert normalize_pair_list([[1, 2], [3, 4]]) == [[1, 2], [3, 4]]
    assert normalize_pair_list(["1 2", "3,4"]) == [[1, 2], [3, 4]]
    assert normalize_pair_list("1 2") == [[1, 2]]
    assert normalize_pair_list("1-2") == [[1, 2]]

    with pytest.raises(ValueError):
        normalize_pair_list("1 2 3")
    with pytest.raises(ValueError):
        normalize_pair_list(123)


def test_normalize_pair_list_extended_errors():
    with pytest.raises(ValueError, match="pair format error"):
        normalize_pair_list(["1,2,3"])
    with pytest.raises(ValueError, match="pair format error"):
        normalize_pair_list("1,2,3")
    with pytest.raises(ValueError, match="unsupported pair format"):
        normalize_pair_list(123)


def test_count_conformers_any_nonexistent():
    assert count_conformers_any("nonexistent.xyz") == 0
    assert count_conformers_any(["nonexistent1.xyz", "nonexistent2.xyz"]) == 0


def test_count_conformers_in_xyz(tmp_path):
    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nC 0 0 1\nH 0 1 0\n3\n\nC 0 0 0\nC 0 0 1\nH 0 1 1\n")
    assert count_conformers_in_xyz(str(xyz)) == 2


def test_count_conformers_any_real(tmp_path):
    xyz1 = tmp_path / "1.xyz"
    xyz1.write_text("3\n\nC 0 0 0\nC 0 0 1\nH 0 1 0\n")
    xyz2 = tmp_path / "2.xyz"
    xyz2.write_text("3\n\nC 0 0 0\nC 0 0 1\nH 0 1 0\n3\n\nC 0 0 0\nC 0 0 1\nH 0 1 1\n")
    assert count_conformers_any([str(xyz1), str(xyz2)]) == 3
    assert count_conformers_any(str(xyz1)) == 1


def test_validate_inputs_compatible(tmp_path):
    with pytest.raises(ValueError, match="no input files provided"):
        validate_inputs_compatible([])

    f1 = tmp_path / "f1.xyz"
    f1.write_text("invalid")
    with pytest.raises(ValueError, match="cannot parse input XYZ"):
        validate_inputs_compatible([str(f1)])

    f2 = tmp_path / "f2.xyz"
    f2.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n2\ntest\nC 0 0 0\nH 0 0 1.1\n")
    with pytest.raises(ValueError, match="multi-input mode requires single-frame XYZ"):
        validate_inputs_compatible([str(f2)])

    f3 = tmp_path / "f3.xyz"
    f3.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    f4 = tmp_path / "f4.xyz"
    f4.write_text("2\ntest\nO 0 0 0\nH 0 0 1\n")
    with pytest.raises(
        ValueError, match="all inputs must have the same atom count and element order"
    ):
        validate_inputs_compatible([str(f3), str(f4)])


def test_run_workflow_rejects_malformed_xyz_strict(tmp_path):
    bad_xyz = tmp_path / "bad.xyz"
    bad_xyz.write_text("2\ncomment\nC 0 0 0\nH not-a-number 0 1\n", encoding="utf-8")

    config_file = tmp_path / "workflow.yaml"
    config_file.write_text(
        "global:\n  iprog: orca\n  keyword: B3LYP\nsteps:\n  - name: s1\n    type: calc\n    params:\n      itask: sp\n",
        encoding="utf-8",
    )

    with pytest.raises(XYZFormatError, match="cannot parse coordinates"):
        run_workflow([str(bad_xyz)], str(config_file), str(tmp_path / "work"))


def test_normalize_labels():
    assert _normalize_iprog_label("1") == "g16"
    assert _normalize_iprog_label("orca") == "orca"
    assert _normalize_iprog_label("custom") == "custom"

    assert _itask_label("0") == "opt"
    assert _itask_label("ts") == "ts"
    assert _itask_label("unknown") == "unknown"


def test_count_task_statuses_in_results_db(tmp_path):
    assert count_task_statuses_in_results_db(str(tmp_path / "nonexistent.db")) is None

    db_path = tmp_path / "results.db"
    db_path.write_text("not a db")
    assert count_task_statuses_in_results_db(str(db_path)) is None

    if db_path.exists():
        db_path.unlink()
    import sqlite3

    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE task_results (status TEXT)")
    con.execute("INSERT INTO task_results VALUES ('success')")
    con.execute("INSERT INTO task_results VALUES ('success')")
    con.execute("INSERT INTO task_results VALUES ('failed')")
    con.commit()
    con.close()

    counts = count_task_statuses_in_results_db(str(db_path))
    assert counts["success"] == 2
    assert counts["failed"] == 1
    assert counts["total"] == 3


def test_count_task_statuses_in_results_db_latest_records_only(tmp_path):
    import sqlite3

    db_path = tmp_path / "results_latest.db"
    con = sqlite3.connect(str(db_path))
    con.execute("""
        CREATE TABLE task_results (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            task_index INTEGER,
            status TEXT NOT NULL,
            energy REAL,
            final_gibbs_energy REAL,
            final_sp_energy REAL,
            num_imag_freqs INTEGER,
            lowest_freq REAL,
            g_corr REAL,
            ts_bond_atoms TEXT,
            ts_bond_length REAL,
            final_coords TEXT,
            error TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute(
        "INSERT INTO task_results (job_name, status, error) VALUES ('job1', 'failed', 'boom')"
    )
    con.execute(
        "INSERT INTO task_results (job_name, status, energy) VALUES ('job1', 'success', -1.0)"
    )
    con.commit()
    con.close()

    counts = count_task_statuses_in_results_db(str(db_path))
    assert counts["success"] == 1
    assert counts["failed"] == 0
    assert counts["total"] == 1


def test_resolve_step_output(tmp_path):
    step_dir = tmp_path / "step_01"
    step_dir.mkdir()

    assert resolve_step_output(str(step_dir), "calc") is None

    raw = step_dir / "result.xyz"
    raw.write_text("1\n\nH 0 0 0\n")
    assert resolve_step_output(str(step_dir), "calc") == str(raw)

    clean = step_dir / "output.xyz"
    clean.write_text("1\n\nH 0 0 0\n")
    assert resolve_step_output(str(step_dir), "calc") == str(clean)

    search = step_dir / "search.xyz"
    search.write_text("1\n\nH 0 0 0\n")
    assert resolve_step_output(str(step_dir), "confgen") == str(search)

    calc_search_dir = tmp_path / "step_calc_search"
    calc_search_dir.mkdir()
    (calc_search_dir / "search.xyz").write_text("1\n\nH 0 0 0\n")
    assert resolve_step_output(str(calc_search_dir), "calc") is None


def test_create_runtask_config(tmp_path):
    ini_path = tmp_path / "test.ini"
    params = {
        "itask": "ts",
        "iprog": "orca",
        "ts_bond_atoms": "1,2",
        "cores_per_task": 8,
        "keyword": "B3LYP",
    }
    global_cfg = {
        "gaussian_path": "/usr/bin/g16",
        "orca_path": "/usr/bin/orca",
        "total_memory": "8GB",
    }

    create_runtask_config(str(ini_path), params, global_cfg)

    import configparser

    config = configparser.ConfigParser()
    config.read(str(ini_path))

    assert config["DEFAULT"]["orca_path"] == "/usr/bin/orca"
    assert config["DEFAULT"]["cores_per_task"] == "8"
    assert config["DEFAULT"]["ts_bond_atoms"] == "1,2"
    assert config["Task"]["itask"] == "ts"
    assert config["Task"]["keyword"] == "B3LYP"

    params["dedup_only"] = True
    params["rmsd_threshold"] = 0.5
    create_runtask_config(str(ini_path), params, global_cfg)
    config.read(str(ini_path))
    assert "--dedup-only" in config["Task"]["clean_opts"]
    assert "-t 0.5" in config["Task"]["clean_opts"]


def test_run_workflow_full_and_resume(input_xyz, tmp_path):
    input_xyz = input_xyz

    config_content = """
global:
  iprog: orca
  itask: opt
  keyword: B3LYP
  cores_per_task: 1
  max_parallel_jobs: 1

steps:
  - name: step1
    type: confgen
    params:
      chains: ["1-2"]
      angle_step: 120
  - name: step2
    type: calc
    params:
      itask: sp
      keyword: B3LYP
  - name: step3
    type: calc
    params:
      itask: sp
      keyword: B3LYP
"""
    config_file = tmp_path / "workflow.yaml"
    config_file.write_text(config_content)

    work_dir = tmp_path / "work"

    def mock_run_generation(*args, **kwargs):
        with open("search.xyz", "w") as f:
            f.write("2\ngenerated\nC 0 0 0\nH 0 0 1.1\n")
            f.write("2\ngenerated\nC 0 0 0\nH 0 0 1.2\n")

    def mock_manager_run(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        with open(os.path.join(self.work_dir, "output.xyz"), "w") as f:
            f.write("2\ncleaned\nC 0 0 0\nH 0 0 1.1\n")
        import sqlite3

        db_path = os.path.join(self.work_dir, "results.db")
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE task_results (status TEXT)")
        con.execute("INSERT INTO task_results VALUES ('success')")
        con.commit()
        con.close()

        with (
            patch("confflow.blocks.confgen.run_generation", side_effect=mock_run_generation),
            patch("confflow.calc.ChemTaskManager.run", autospec=True, side_effect=mock_manager_run),
            patch("confflow.blocks.viz.generate_text_report", return_value=""),
        ):
            with patch(
                "confflow.config.schema.ConfigSchema.validate_calc_config",
                side_effect=[None, ValueError("stop here")],
            ):
                with pytest.raises(ValueError, match="stop here"):
                    run_workflow([str(input_xyz)], str(config_file), str(work_dir))

            checkpoint_file = work_dir / ".checkpoint"
            assert checkpoint_file.exists()
            with open(checkpoint_file) as f:
                cp = json.load(f)
            assert cp["last_completed_step"] == 1

            stats = run_workflow([str(input_xyz)], str(config_file), str(work_dir), resume=True)
            assert len(stats["steps"]) == 1
            assert stats["steps"][0]["status"] == "completed"


def test_run_workflow_low_energy_trace(input_xyz, tmp_path):
    input_xyz = input_xyz

    config_file = tmp_path / "workflow.yaml"
    config_file.write_text("""
global:
  iprog: orca
  keyword: B3LYP
steps:
  - name: s1
    type: calc
    params:
      itask: sp
""")

    work_dir = tmp_path / "work"

    def mock_manager_run(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        with open(os.path.join(self.work_dir, "output.xyz"), "w") as f:
            f.write("2\nCID=s01_1 E=-1.0\nC 0 0 0\nH 0 0 1.1\n")

    with (
        patch("confflow.calc.ChemTaskManager.run", autospec=True, side_effect=mock_manager_run),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow([str(input_xyz)], str(config_file), str(work_dir))

    assert "low_energy_trace" in stats
    assert len(stats["low_energy_trace"]["conformers"]) > 0
    assert stats["low_energy_trace"]["conformers"][0]["cid"] == "s01_1"


def _read_ini(path) -> dict:
    import configparser

    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str
    cfg.read(path)
    out = {}
    out.update({k: v for k, v in cfg.defaults().items()})
    if cfg.has_section("Task"):
        out.update({k: v for k, v in cfg.items("Task")})
    return out


def test_freeze_only_effective_for_opt_and_opt_freq(tmp_path):
    from confflow.workflow.config_builder import create_runtask_config

    ini = tmp_path / "config.ini"
    global_cfg = {
        "gaussian_path": "g16",
        "orca_path": "orca",
        "cores_per_task": 1,
        "total_memory": "1GB",
        "max_parallel_jobs": 1,
        "freeze": [86, 92],
    }

    create_runtask_config(
        str(ini),
        params={"iprog": "orca", "itask": "sp", "keyword": "r2SCAN-3c"},
        global_config=global_cfg,
    )
    data = _read_ini(ini)
    assert data.get("freeze") == "0"

    create_runtask_config(
        str(ini),
        params={"iprog": "g16", "itask": "opt", "keyword": "opt(nomicro)"},
        global_config=global_cfg,
    )
    data = _read_ini(ini)
    assert data.get("freeze") == "86,92"


def test_confflow_accepts_multiple_xyz_inputs_and_runs_confgen(monkeypatch, tmp_path):
    import os

    import yaml

    import confflow.workflow.engine as engine

    a = tmp_path / "a.xyz"
    b = tmp_path / "b.xyz"
    a.write_text("2\nA\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")
    b.write_text("2\nB\nH 0 0 0\nH 0 0 1\n", encoding="utf-8")

    cfg = {
        "global": {
            "gaussian_path": "g16",
            "orca_path": "orca",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "max_parallel_jobs": 1,
        },
        "steps": [
            {
                "name": "step_01",
                "type": "confgen",
                "params": {"chains": ["1-2"]},
            }
        ],
    }
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    called = {"inputs": None}

    def fake_run_generation(input_files, **kwargs):
        assert isinstance(input_files, list)
        assert len(input_files) == 2
        called["inputs"] = list(input_files)
        with open("search.xyz", "w", encoding="utf-8") as f:
            f.write("2\nconf1\nH 0 0 0\nH 0 0 1\n")
            f.write("2\nconf2\nH 0 0 0\nH 0 0 1\n")

    monkeypatch.setattr(confgen, "run_generation", fake_run_generation)
    monkeypatch.setattr(viz, "parse_xyz_file", lambda p: [])
    monkeypatch.setattr(viz, "generate_text_report", lambda *a, **k: "")

    work_dir = tmp_path / "work"
    engine.run_workflow(
        input_xyz=[str(a), str(b)],
        config_file=str(cfg_path),
        work_dir=str(work_dir),
        resume=False,
        verbose=False,
    )

    assert called["inputs"] is not None
    assert os.path.exists(work_dir / "step_01" / "search.xyz")


def test_run_workflow_resume_without_checkpoint(input_xyz, tmp_path):
    input_xyz = input_xyz

    config_file = tmp_path / "workflow.yaml"
    config_file.write_text("""
global:
  iprog: orca
  keyword: B3LYP
steps:
  - name: s1
    type: calc
    params:
      itask: sp
""")

    work_dir = tmp_path / "work"

    def mock_manager_run(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        with open(os.path.join(self.work_dir, "output.xyz"), "w") as f:
            f.write("2\nCID=s01_1 E=-1.0\nC 0 0 0\nH 0 0 1.1\n")

    with (
        patch("confflow.calc.ChemTaskManager.run", autospec=True, side_effect=mock_manager_run),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow([str(input_xyz)], str(config_file), str(work_dir), resume=True)

    assert isinstance(stats, dict)
    assert len(stats.get("steps", [])) == 1


def test_run_workflow_marks_reused_calc_step_as_skipped(input_xyz, tmp_path, monkeypatch):
    config_file = tmp_path / "workflow.yaml"
    config_file.write_text(
        """
global:
  iprog: orca
  keyword: B3LYP
steps:
  - name: s1
    type: calc
    params:
      itask: sp
""",
        encoding="utf-8",
    )

    work_dir = tmp_path / "work"
    output = work_dir / "step_01" / "output.xyz"
    output.parent.mkdir(parents=True)
    output.write_text("2\nCID=s01_1 E=-1.0\nC 0 0 0\nH 0 0 1.1\n", encoding="utf-8")

    monkeypatch.setattr(
        "confflow.workflow.engine.step_run_calc_step",
        lambda **kwargs: CalcStepResult(str(output), reused_existing=True),
    )
    monkeypatch.setattr(viz, "generate_text_report", lambda *args, **kwargs: "")

    stats = run_workflow([str(input_xyz)], str(config_file), str(work_dir))

    assert stats["steps"][0]["status"] == TaskStatus.SKIPPED


def test_build_task_config_chk_from_step_uses_sanitized_dir(tmp_path):
    steps = [
        {"name": "step/06 ts", "type": "calc", "params": {}},
        {"name": "step:07?sp", "type": "calc", "params": {}},
    ]
    step_dirs, _ = build_step_dir_name_map(steps)
    assert step_dirs[0] != "step/06 ts"

    cfg = build_task_config(
        params={
            "iprog": "g16",
            "itask": "sp",
            "keyword": "hf/3-21g",
            "chk_from_step": "step/06 ts",
        },
        global_config={},
        root_dir=str(tmp_path / "work"),
        all_steps=steps,
    )
    assert cfg.get("input_chk_dir") == os.path.join(str(tmp_path / "work"), step_dirs[0], "backups")


def test_build_task_config_normalizes_freeze_and_ts_sources():
    cfg = build_task_config(
        params={
            "iprog": "2",
            "itask": "opt",
            "keyword": "HF",
            "freeze": [1, "2-3", 5],
        },
        global_config={"ts_bond_atoms": [7, 8]},
    )

    assert cfg["iprog"] == "orca"
    assert cfg["freeze"] == "1,2,3,5"
    assert cfg["ts_bond_atoms"] == "7,8"


def test_build_structured_task_config_keeps_typed_fields():
    cfg = build_structured_task_config(
        params={
            "iprog": "2",
            "itask": "ts",
            "keyword": "HF",
            "freeze": [1, "2-3"],
            "ts_rescue_scan": "true",
            "dedup_only": "true",
            "noH": "true",
            "rmsd_threshold": 0.4,
            "allowed_executables": ["/usr/bin/orca", "orca"],
        },
        global_config={},
    )

    assert cfg["iprog"] == "orca"
    assert cfg["itask"] == "ts"
    assert cfg["freeze"] == []
    assert cfg["ts_bond_atoms"] == [1, 2]
    assert cfg["ts_rescue_scan"] is True
    assert cfg["allowed_executables"] == ["/usr/bin/orca", "orca"]
    assert cfg.cleanup.enabled is True
    assert cfg.cleanup.dedup_only is True
    assert cfg.cleanup.no_h is True
    assert cfg.cleanup.rmsd_threshold == 0.4


def test_build_structured_task_config_inherits_global_program_and_task():
    cfg = build_structured_task_config(
        params={"keyword": "HF"},
        global_config={"iprog": "g16", "itask": "sp", "keyword": "B3LYP"},
    )

    assert cfg["iprog"] == "g16"
    assert cfg["itask"] == "sp"
    assert cfg["keyword"] == "HF"


@pytest.mark.parametrize("global_itask", ["sp", "freq", "ts"])
def test_build_structured_task_config_global_non_opt_suppresses_freeze(global_itask):
    cfg = build_structured_task_config(
        params={"keyword": "HF"},
        global_config={"itask": global_itask, "freeze": [1, "2-3"]},
    )

    assert cfg["itask"] == global_itask
    assert cfg["freeze"] == []
    assert cfg.freeze == ()


def test_build_structured_task_config_step_opt_override_uses_freeze():
    cfg = build_structured_task_config(
        params={"itask": "opt", "keyword": "HF"},
        global_config={"itask": "sp", "freeze": [1, "2-3"]},
    )

    assert cfg["itask"] == "opt"
    assert cfg["freeze"] == [1, 2, 3]
    assert cfg.freeze == (1, 2, 3)


def test_build_structured_task_config_global_ts_uses_ts_options():
    cfg = build_structured_task_config(
        params={"keyword": "HF"},
        global_config={
            "iprog": "g16",
            "itask": "ts",
            "ts_rescue_scan": "true",
            "scan_coarse_step": 0.2,
            "scan_fine_step": 0.05,
            "scan_uphill_limit": 4,
        },
    )

    assert cfg["itask"] == "ts"
    assert cfg["ts_rescue_scan"] is True
    assert cfg["scan_coarse_step"] == 0.2
    assert cfg["scan_fine_step"] == 0.05
    assert cfg["scan_uphill_limit"] == 4
    assert cfg.ts.rescue_scan is True
    assert cfg.ts.scan_coarse_step == 0.2
    assert cfg.ts.scan_fine_step == 0.05
    assert cfg.ts.scan_uphill_limit == 4


def test_build_structured_task_config_step_program_and_task_override_global():
    cfg = build_structured_task_config(
        params={"iprog": "orca", "itask": "opt", "keyword": "HF"},
        global_config={
            "iprog": "g16",
            "itask": "ts",
            "keyword": "B3LYP",
            "ts_rescue_scan": "true",
            "scan_coarse_step": 0.2,
        },
    )

    assert cfg["iprog"] == "orca"
    assert cfg["itask"] == "opt"
    assert cfg["keyword"] == "HF"
    assert "ts_rescue_scan" not in cfg
    assert "scan_coarse_step" not in cfg
    assert cfg.ts.rescue_scan is False
    assert cfg.ts.scan_coarse_step is None


def test_build_structured_task_config_respects_delete_work_dir_override():
    cfg = build_structured_task_config(
        params={"keyword": "HF", "delete_work_dir": "false"},
        global_config={"delete_work_dir": True},
    )
    default_cfg = build_structured_task_config(params={"keyword": "HF"}, global_config={})

    assert cfg["delete_work_dir"] is False
    assert cfg.execution.delete_work_dir is False
    assert default_cfg["delete_work_dir"] is True
    assert default_cfg.execution.delete_work_dir is True


def test_build_structured_task_config_delete_work_dir_is_known(caplog):
    with caplog.at_level("WARNING", logger="confflow.workflow.config_builder"):
        cfg = build_structured_task_config(
            params={"keyword": "HF", "delete_work_dir": "false"},
            global_config={},
        )

    assert cfg["delete_work_dir"] is False
    assert "Ignored unknown calc parameter 'delete_work_dir'" not in caplog.text


def test_build_structured_task_config_preserves_typed_blocks_and_lists():
    cfg = build_structured_task_config(
        params={
            "iprog": "g16",
            "itask": "opt",
            "keyword": "HF",
            "freeze": [1, 2],
            "blocks": {"geom": {"Constraints": ["{ C 0 C }"]}},
            "gaussian_link0": ["%Mem=8GB", "%NoSave"],
            "gaussian_modredundant": ["B 1 2 F"],
            "allowed_executables": ["/opt/g16/g16", "g16"],
        },
        global_config={},
    )

    assert cfg["freeze"] == [1, 2]
    assert cfg["blocks"] == {"geom": {"Constraints": ["{ C 0 C }"]}}
    assert cfg["gaussian_link0"] == ["%Mem=8GB", "%NoSave"]
    assert cfg["gaussian_modredundant"] == ["B 1 2 F"]
    assert cfg["allowed_executables"] == ["/opt/g16/g16", "g16"]


def test_build_structured_task_config_preserves_clean_params_thresholds():
    cfg = build_structured_task_config(
        params={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "HF",
            "clean_params": "-t 0.12 -ewin 7.5 --energy-tolerance 0.03 --dedup-only --noH",
        },
        global_config={},
    )

    assert cfg.cleanup.enabled is True
    assert cfg.cleanup.rmsd_threshold == 0.12
    assert cfg.cleanup.energy_window == 7.5
    assert cfg.cleanup.energy_tolerance == 0.03
    assert cfg.cleanup.dedup_only is True
    assert cfg.cleanup.no_h is True


def test_build_structured_task_config_keeps_explicit_auto_clean_false_with_clean_params():
    cfg = build_structured_task_config(
        params={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "HF",
            "auto_clean": False,
            "clean_params": {"threshold": 0.12, "energy_window": 7.5},
        },
        global_config={},
    )

    assert cfg.execution.auto_clean is False
    assert cfg.cleanup.enabled is True
    assert cfg.cleanup.rmsd_threshold == 0.12
    assert cfg.cleanup.energy_window == 7.5


def test_build_structured_task_config_preserves_explicit_input_chk_dir(tmp_path):
    cfg = build_structured_task_config(
        params={
            "iprog": "g16",
            "itask": "sp",
            "keyword": "HF",
            "input_chk_dir": str(tmp_path / "step_override"),
        },
        global_config={"input_chk_dir": str(tmp_path / "global_override")},
        root_dir=str(tmp_path),
        all_steps=[],
    )

    assert cfg.execution.input_chk_dir == str(tmp_path / "step_override")
    assert cfg["input_chk_dir"] == str(tmp_path / "step_override")


def test_build_structured_task_config_uses_chk_from_step_input_chk_dir_fallback(tmp_path):
    cfg = build_structured_task_config(
        params={
            "iprog": "g16",
            "itask": "sp",
            "keyword": "HF",
            "chk_from_step": "step_01",
        },
        global_config={},
        root_dir=str(tmp_path),
        all_steps=[{"name": "step_01"}],
    )

    assert cfg.execution.input_chk_dir == str(tmp_path / "step_01" / "backups")
    assert cfg["input_chk_dir"] == str(tmp_path / "step_01" / "backups")


def test_build_task_config_falls_back_to_freeze_for_ts_pair():
    cfg = build_task_config(
        params={
            "iprog": "1",
            "itask": "ts",
            "keyword": "opt=(ts,calcfc)",
            "freeze": [4, "5-6"],
        },
        global_config={},
    )

    assert cfg["iprog"] == "g16"
    assert cfg["freeze"] == "0"
    assert cfg["ts_bond_atoms"] == "4,5"


def test_build_task_config_respects_string_false_flags():
    cfg = build_task_config(
        params={
            "iprog": "orca",
            "itask": "ts",
            "keyword": "HF",
            "dedup_only": "false",
            "keep_all_topos": "false",
            "noH": "false",
            "ts_rescue_scan": "false",
            "enable_dynamic_resources": "false",
            "resume_from_backups": "false",
        },
        global_config={},
    )

    assert "clean_opts" not in cfg
    assert cfg["ts_rescue_scan"] == "false"
    assert cfg["enable_dynamic_resources"] == "false"
    assert cfg["resume_from_backups"] == "false"


def test_build_task_config_respects_string_true_flags():
    cfg = build_task_config(
        params={
            "iprog": "orca",
            "itask": "ts",
            "keyword": "HF",
            "dedup_only": "true",
            "keep_all_topos": "yes",
            "noH": "1",
            "ts_rescue_scan": "on",
        },
        global_config={
            "enable_dynamic_resources": "true",
            "resume_from_backups": "yes",
        },
    )

    assert "--dedup-only" in cfg["clean_opts"]
    assert "--keep-all-topos" in cfg["clean_opts"]
    assert "--noH" in cfg["clean_opts"]
    assert cfg["ts_rescue_scan"] == "true"
    assert cfg["enable_dynamic_resources"] == "true"
    assert cfg["resume_from_backups"] == "true"


def test_build_task_config_non_ts_does_not_emit_ts_rescue_scan_default():
    cfg = build_task_config(
        params={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "HF",
            "rmsd_threshold": 0.25,
        },
        global_config={},
    )

    assert "ts_rescue_scan" not in cfg


def test_build_task_config_signature_stable_for_legacy_output():
    params = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "HF",
        "dedup_only": "true",
        "noH": "true",
        "rmsd_threshold": 0.5,
        "allowed_executables": ["/usr/bin/orca", "orca"],
    }
    global_cfg = {
        "enable_dynamic_resources": "true",
        "resume_from_backups": "true",
    }

    cfg1 = build_task_config(params, global_cfg)
    cfg2 = build_task_config(params, global_cfg)

    assert cfg1 == cfg2
    assert compute_calc_config_signature(cfg1) == compute_calc_config_signature(cfg2)


def test_validate_inputs_compatible_force_consistency_bypass(tmp_path):
    from confflow.workflow.validation import validate_inputs_compatible

    f1 = tmp_path / "a.xyz"
    f2 = tmp_path / "b.xyz"
    f1.write_text("2\nA\nC 0 0 0\nH 0 0 1\n")
    f2.write_text("2\nB\nO 0 0 0\nH 0 0 1\n")

    validate_inputs_compatible([str(f1), str(f2)], confgen_params=None, force_consistency=True)


# =============================================================================
# Workflow engine path-coverage tests (merged from test_workflow_engine_paths.py)
# =============================================================================


def test_workflow_engine_helpers_extended():
    assert _itask_label(0) == "opt"
    assert _itask_label(1) == "sp"
    assert _itask_label(2) == "freq"
    assert _itask_label(3) == "opt_freq"
    assert _itask_label(4) == "ts"
    assert _itask_label("unknown") == "unknown"

    assert _normalize_iprog_label(1) == "g16"
    assert _normalize_iprog_label(2) == "orca"
    assert _normalize_iprog_label("unknown") == "unknown"


def test_workflow_engine_misses(tmp_path):
    with pytest.raises(ValueError, match="cannot parse input XYZ"):
        validate_inputs_compatible(["a.xyz", "b.xyz"])

    assert as_list(None) is None
    assert as_list("a") == ["a"]
    assert as_list(["a"]) == ["a"]

    assert normalize_pair_list(None) is None
    assert normalize_pair_list("1,2") == [[1, 2]]
    assert normalize_pair_list(["1,2", "3,4"]) == [[1, 2], [3, 4]]


def test_workflow_engine_run_workflow_errors(tmp_path):
    import yaml

    with pytest.raises(FileNotFoundError):
        run_workflow(input_xyz=["test.xyz"], config_file="nonexistent.yaml", work_dir=str(tmp_path))

    config = {
        "global": {"work_dir": str(tmp_path)},
        "steps": [{"name": "step1", "type": "confgen"}],
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    with pytest.raises(FileNotFoundError):
        run_workflow(
            input_xyz=["nonexistent.xyz"], config_file=str(config_path), work_dir=str(tmp_path)
        )


def test_workflow_engine_load_config_errors(tmp_path):
    from confflow.workflow.engine import load_workflow_config

    bad_cfg = tmp_path / "bad.yaml"
    bad_cfg.write_text("invalid: yaml: :")

    with pytest.raises(ValueError):
        load_workflow_config(str(bad_cfg))

    missing_cfg = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        load_workflow_config(str(missing_cfg))


def test_workflow_engine_resume_logic(tmp_path):
    from datetime import datetime

    root = tmp_path / "resume"
    root.mkdir()

    input_xyz = root / "input.xyz"
    input_xyz.write_text("1\nCID=A000001\nC 0 0 0\n")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global: {}\n"
        "steps:\n"
        "  - type: calc\n"
        "    name: step1\n"
        "    params:\n"
        "      iprog: gaussian\n"
        "      itask: opt\n"
        "      keyword: opt\n"
        "  - type: calc\n"
        "    name: step2\n"
        "    params:\n"
        "      iprog: gaussian\n"
        "      itask: opt\n"
        "      keyword: opt\n"
    )

    checkpoint = root / ".checkpoint"
    checkpoint.write_text(
        json.dumps(
            {
                "last_completed_step": 0,
                "timestamp": datetime.now().isoformat(),
                "stats": {"steps": [{"name": "step1", "status": "success"}]},
            }
        )
    )

    step1_dir = root / "step1"
    step1_dir.mkdir()
    (step1_dir / "output.xyz").write_text("1\nCID=A000001\nC 0 0 0\n", encoding="utf-8")
    step2_dir = root / "step2"
    step2_dir.mkdir()
    (step2_dir / "output.xyz").write_text("1\nCID=A000001\nC 0 0 0\n", encoding="utf-8")
    cfg_data = load_workflow_config(str(config_file))
    cfg1 = build_task_config(
        cfg_data["steps"][0]["params"],
        cfg_data["global"],
        root_dir=str(root),
        all_steps=cfg_data["steps"],
    )
    ConfigSchema.validate_calc_config(cfg1)
    record_calc_step_signature(
        str(step1_dir),
        cfg1,
        input_signature=compute_calc_input_signature(str(input_xyz)),
    )
    cfg = build_task_config(
        cfg_data["steps"][1]["params"],
        cfg_data["global"],
        root_dir=str(root),
        all_steps=cfg_data["steps"],
    )
    ConfigSchema.validate_calc_config(cfg)
    record_calc_step_signature(
        str(step2_dir),
        cfg,
        input_signature=compute_calc_input_signature(str(step1_dir / "output.xyz")),
    )

    res = run_workflow([str(input_xyz)], str(config_file), work_dir=str(root), resume=True)
    assert len(res["steps"]) == 1
    assert res["steps"][0]["name"] == "step2"


def test_workflow_engine_resume_skips_disabled_completed_step(input_xyz, tmp_path):
    config_file = tmp_path / "workflow.yaml"
    config_file.write_text(
        """
global: {}
steps:
  - name: disabled_step
    type: confgen
    enabled: false
    params:
      chains: ["1-2"]
      angle_step: 120
  - name: enabled_step
    type: confgen
    params:
      chains: ["1-2"]
      angle_step: 120
""",
        encoding="utf-8",
    )

    work_dir = tmp_path / "work"

    def mock_run_generation(*args, **kwargs):
        with open("search.xyz", "w", encoding="utf-8") as f:
            f.write("2\ngenerated\nC 0 0 0\nH 0 0 1.1\n")

    with patch("confflow.blocks.confgen.run_generation", side_effect=mock_run_generation):
        run_workflow([str(input_xyz)], str(config_file), str(work_dir))

    assert not (work_dir / "disabled_step").exists()
    checkpoint = json.loads((work_dir / ".checkpoint").read_text(encoding="utf-8"))
    assert checkpoint["last_completed_step"] == 1

    with patch(
        "confflow.blocks.confgen.run_generation",
        side_effect=AssertionError("resume should reuse completed enabled step"),
    ):
        stats = run_workflow([str(input_xyz)], str(config_file), str(work_dir), resume=True)

    assert stats["final_conformers"] == 1


def test_workflow_engine_resume_missing_output_raises(tmp_path):
    from datetime import datetime

    root = tmp_path / "resume_missing"
    root.mkdir()

    input_xyz = root / "input.xyz"
    input_xyz.write_text("1\nCID=A000001\nC 0 0 0\n", encoding="utf-8")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global:\n  iprog: gaussian\n  itask: opt\n  keyword: opt\n"
        "steps:\n  - type: calc\n    name: step1\n  - type: calc\n    name: step2\n",
        encoding="utf-8",
    )

    checkpoint = root / ".checkpoint"
    checkpoint.write_text(
        json.dumps(
            {
                "last_completed_step": 0,
                "timestamp": datetime.now().isoformat(),
                "stats": {"steps": [{"name": "step1", "status": "success"}]},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Resume failed"):
        run_workflow([str(input_xyz)], str(config_file), work_dir=str(root), resume=True)


def test_workflow_engine_resume_calc_does_not_accept_search_xyz(tmp_path):
    from datetime import datetime

    root = tmp_path / "resume_calc_search_only"
    root.mkdir()

    input_xyz = root / "input.xyz"
    input_xyz.write_text("1\nCID=A000001\nC 0 0 0\n", encoding="utf-8")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global:\n  iprog: gaussian\n  itask: opt\n  keyword: opt\n"
        "steps:\n  - type: calc\n    name: step1\n",
        encoding="utf-8",
    )

    checkpoint = root / ".checkpoint"
    checkpoint.write_text(
        json.dumps(
            {
                "last_completed_step": 0,
                "timestamp": datetime.now().isoformat(),
                "stats": {"steps": [{"name": "step1", "status": "success"}]},
            }
        ),
        encoding="utf-8",
    )

    step1_dir = root / "step1"
    step1_dir.mkdir()
    (step1_dir / "search.xyz").write_text("1\nCID=A000001\nC 0 0 0\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Resume failed"):
        run_workflow([str(input_xyz)], str(config_file), work_dir=str(root), resume=True)


def test_workflow_engine_calc_resume(tmp_path):
    root = tmp_path / "workflow_resume"
    root.mkdir()

    input_xyz = root / "input.xyz"
    input_xyz.write_text("1\nCID=A000001\nC 0 0 0\n")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global: {}\n"
        "steps:\n"
        "  - type: calc\n"
        "    name: step1\n"
        "    params:\n"
        "      iprog: gaussian\n"
        "      itask: opt\n"
        "      keyword: opt\n"
    )

    step_dir = root / "step1"
    step_dir.mkdir()
    (step_dir / "output.xyz").write_text("1\nCID=A000001\nC 0 0 0\n")
    cfg_data = load_workflow_config(str(config_file))
    cfg = build_task_config(
        cfg_data["steps"][0]["params"],
        cfg_data["global"],
        root_dir=str(root),
        all_steps=cfg_data["steps"],
    )
    ConfigSchema.validate_calc_config(cfg)
    record_calc_step_signature(
        str(step_dir),
        cfg,
        input_signature=compute_calc_input_signature(str(input_xyz)),
    )

    res = run_workflow([str(input_xyz)], str(config_file), work_dir=str(root))
    assert res["steps"][0]["status"] == "skipped"


def test_workflow_engine_load_checkpoint_exception(tmp_path):
    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text(
        "global:\n  itask: 1\n  keyword: sp\n  iprog: orca\nsteps:\n  - name: step1\n    type: calc"
    )

    checkpoint = tmp_path / ".checkpoint"
    checkpoint.write_text("invalid json")

    try:
        run_workflow([str(xyz)], str(conf), work_dir=str(tmp_path), resume=True)
    except Exception:
        pass


def test_workflow_engine_trace_exception_trigger(tmp_path):
    def mock_read_xyz_file(path, **kwargs):
        basename = os.path.basename(str(path))
        if "step1" in str(path) and "trace" not in basename:
            return [{"cid": "1", "energy": -1.0, "atoms": []}]
        if "trace" in basename:
            raise ValueError("Simulated trace error")
        return []

    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text(
        "global:\n  itask: 1\n  keyword: sp\n  iprog: orca\n"
        "steps:\n  - name: step1\n    type: calc\n  - name: step2\n    type: calc\n"
    )

    with (
        patch("confflow.calc.manager.ChemTaskManager.run", return_value={"success": 1}),
        patch(
            "confflow.workflow.engine.validate_xyz_file", return_value=(True, [{"atoms": ["C"]}])
        ),
        patch("confflow.workflow.engine.io_xyz.read_xyz_file", side_effect=mock_read_xyz_file),
        patch(
            "confflow.blocks.viz.parse_xyz_file",
            return_value=[{"cid": "1", "energy": -1.0, "metadata": {}}],
        ),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
        patch("confflow.workflow.engine.count_conformers_any", return_value=1),
        patch("confflow.workflow.engine.is_multi_frame_any", return_value=False),
        patch("confflow.workflow.engine.os.path.exists", return_value=True),
    ):
        run_workflow([str(xyz)], str(conf), work_dir=str(tmp_path))


def test_workflow_engine_low_energy_trace_full(tmp_path):
    root = tmp_path / "trace_full"
    root.mkdir()

    input_xyz = root / "input.xyz"
    input_xyz.write_text("1\nCID=A000001\nC 0 0 0\n")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global: {}\n"
        "steps:\n"
        "  - type: calc\n"
        "    name: step1\n"
        "    params:\n"
        "      iprog: gaussian\n"
        "      itask: opt\n"
        "      keyword: opt\n"
    )

    step1_dir = root / "step1"
    step1_dir.mkdir()
    (step1_dir / "output.xyz").write_text("1\nCID=A000001 Energy=-100.0\nC 0 0 0\n")
    cfg_data = load_workflow_config(str(config_file))
    cfg = build_task_config(
        cfg_data["steps"][0]["params"],
        cfg_data["global"],
        root_dir=str(root),
        all_steps=cfg_data["steps"],
    )
    ConfigSchema.validate_calc_config(cfg)
    record_calc_step_signature(
        str(step1_dir),
        cfg,
        input_signature=compute_calc_input_signature(str(input_xyz)),
    )

    (root / "final.xyz").write_text("1\nCID=A000001 Energy=-100.0\nC 0 0 0\n")

    run_workflow([str(input_xyz)], str(config_file), work_dir=str(root))

    stats_path = root / "workflow_stats.json"
    summary_path = root / "run_summary.json"
    assert stats_path.exists()
    assert summary_path.exists()

    with open(stats_path) as f:
        stats = json.load(f)
    with open(summary_path) as f:
        summary = json.load(f)

    assert "low_energy_trace" in stats
    assert len(stats["low_energy_trace"]["conformers"]) > 0
    assert "trace" in stats["low_energy_trace"]["conformers"][0]
    assert summary["final_conformers"] >= 1
    assert summary["step_status_counts"]["skipped"] == 1


# ---------------------------------------------------------------------------
# P1-6: validate_inputs_compatible with validate_chain_bonds=True
# ---------------------------------------------------------------------------


def test_validate_inputs_compatible_chain_bonds_valid(tmp_path):
    """validate_chain_bonds=True with all-valid chains → no exception raised."""
    xyz = tmp_path / "mol.xyz"
    xyz.write_text("2\n\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\n")

    with (
        patch("confflow.workflow.validation.load_mol_from_xyz") as mock_load,
        patch("confflow.workflow.validation.ChainValidator") as MockCV,
    ):
        mock_load.return_value = object()  # any truthy mol object
        MockCV.return_value.validate_mol.return_value = [
            {"valid": True, "raw_chain": "1-2", "error": None}
        ]

        # Should complete without raising
        validate_inputs_compatible(
            [str(xyz)],
            confgen_params={"chains": ["1-2"], "validate_chain_bonds": True},
        )


def test_validate_inputs_compatible_chain_bonds_invalid_chain(tmp_path):
    """validate_chain_bonds=True with an invalid chain → raises ValueError."""
    xyz = tmp_path / "mol.xyz"
    xyz.write_text("2\n\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\n")

    with (
        patch("confflow.workflow.validation.load_mol_from_xyz") as mock_load,
        patch("confflow.workflow.validation.ChainValidator") as MockCV,
    ):
        mock_load.return_value = object()
        MockCV.return_value.validate_mol.return_value = [
            {"valid": False, "raw_chain": "1-2", "error": "atoms not bonded"}
        ]

        with pytest.raises(ValueError, match="atoms not bonded"):
            validate_inputs_compatible(
                [str(xyz)],
                confgen_params={"chains": ["1-2"], "validate_chain_bonds": True},
            )


def test_validate_inputs_compatible_chain_bonds_load_oserror(tmp_path):
    """OSError from load_mol_from_xyz → _raise_or_warn path (raises by default)."""
    xyz = tmp_path / "mol.xyz"
    xyz.write_text("2\n\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\n")

    with patch("confflow.workflow.validation.load_mol_from_xyz", side_effect=OSError("read error")):
        with pytest.raises(ValueError, match="failed to validate flexible chains"):
            validate_inputs_compatible(
                [str(xyz)],
                confgen_params={"chains": ["1-2"], "validate_chain_bonds": True},
            )


def test_validate_inputs_compatible_chain_bonds_disabled(tmp_path):
    """validate_chain_bonds=False (default) → chain path not entered, no mol load."""
    xyz = tmp_path / "mol.xyz"
    xyz.write_text("2\n\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\n")

    with patch("confflow.workflow.validation.load_mol_from_xyz") as mock_load:
        validate_inputs_compatible(
            [str(xyz)],
            confgen_params={"chains": ["1-2"], "validate_chain_bonds": False},
        )
        mock_load.assert_not_called()


def test_validate_inputs_compatible_accepts_chain_alias_for_mapping(tmp_path):
    xyz1 = tmp_path / "a.xyz"
    xyz1.write_text("3\n\nC 0 0 0\nH 0 0 1\nO 1 0 0\n", encoding="utf-8")
    xyz2 = tmp_path / "b.xyz"
    xyz2.write_text("3\n\nO 1 0 0\nH 0 0 1\nC 0 0 0\n", encoding="utf-8")

    validate_inputs_compatible(
        [str(xyz1), str(xyz2)],
        confgen_params={"chain": "1-2"},
    )


def test_build_task_config_propagates_safety_policy(tmp_path):
    global_config = {
        "gaussian_path": "g16",
        "orca_path": "orca",
        "sandbox_root": str(tmp_path / "sandbox"),
        "allowed_executables": ["g16", "/opt/orca/orca"],
    }

    cfg = build_task_config(
        {"iprog": "orca", "itask": "sp", "keyword": "xTB"},
        global_config,
        root_dir=str(tmp_path),
        all_steps=[],
    )

    assert cfg["sandbox_root"] == str(tmp_path / "sandbox")
    assert cfg["allowed_executables"] == "g16,/opt/orca/orca"
