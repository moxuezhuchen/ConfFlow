#!/usr/bin/env python3

"""Tests for engine module (merged)."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from confflow.core.pairs import normalize_pair_list
from confflow.workflow.config_builder import (
    build_step_dir_name_map,
    build_task_config,
    create_runtask_config,
)
from confflow.workflow.engine import (
    _itask_label,
    _normalize_iprog_label,
    as_list,
    count_conformers_any,
    run_workflow,
    validate_inputs_compatible,
)
from confflow.workflow.helpers import count_conformers_in_xyz, resolve_step_output
from confflow.workflow.stats import count_task_statuses_in_results_db


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
        "mem_per_task": "8GB",
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

    monkeypatch.setattr(engine.confgen, "run_generation", fake_run_generation)
    monkeypatch.setattr(engine.viz, "parse_xyz_file", lambda p: [])
    monkeypatch.setattr(engine.viz, "generate_text_report", lambda *a, **k: "")

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
    from confflow.core.utils import InputFileError

    with pytest.raises(InputFileError):
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
    input_xyz.write_text("1\nCID=1\nC 0 0 0\n")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global:\n  iprog: gaussian\n  itask: opt\n  keyword: opt\n"
        "steps:\n  - type: calc\n    name: step1\n  - type: calc\n    name: step2\n"
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

    step2_dir = root / "step2"
    step2_dir.mkdir()
    (step2_dir / "output.xyz").write_text("1\nCID=1\nC 0 0 0\n")

    res = run_workflow([str(input_xyz)], str(config_file), work_dir=str(root), resume=True)
    assert len(res["steps"]) == 1
    assert res["steps"][0]["name"] == "step2"


def test_workflow_engine_calc_resume(tmp_path):
    root = tmp_path / "workflow_resume"
    root.mkdir()

    input_xyz = root / "input.xyz"
    input_xyz.write_text("1\nCID=1\nC 0 0 0\n")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global:\n  iprog: gaussian\n  itask: opt\n  keyword: opt\n"
        "steps:\n  - type: calc\n    name: step1\n"
    )

    step_dir = root / "step1"
    step_dir.mkdir()
    (step_dir / "output.xyz").write_text("1\nCID=1\nC 0 0 0\n")

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
            raise Exception("Simulated trace error")
        return []

    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text(
        "global:\n  itask: 1\n  keyword: sp\n  iprog: orca\n"
        "steps:\n  - name: step1\n    type: calc\n  - name: step2\n    type: calc\n"
    )

    with (
        patch(
            "confflow.workflow.engine.calc.manager.ChemTaskManager.run", return_value={"success": 1}
        ),
        patch("confflow.workflow.engine.io_xyz.read_xyz_file", side_effect=mock_read_xyz_file),
        patch(
            "confflow.workflow.engine.viz.parse_xyz_file",
            return_value=[{"cid": "1", "energy": -1.0, "metadata": {}}],
        ),
        patch("confflow.workflow.engine.viz.generate_text_report", return_value=""),
        patch("confflow.workflow.engine.count_conformers_any", return_value=1),
        patch("confflow.workflow.engine.is_multi_frame_any", return_value=False),
        patch("confflow.workflow.engine.os.path.exists", return_value=True),
    ):
        run_workflow([str(xyz)], str(conf), work_dir=str(tmp_path))


def test_workflow_engine_low_energy_trace_full(tmp_path):
    root = tmp_path / "trace_full"
    root.mkdir()

    input_xyz = root / "input.xyz"
    input_xyz.write_text("1\nCID=1\nC 0 0 0\n")

    config_file = root / "config.yaml"
    config_file.write_text(
        "global:\n  iprog: gaussian\n  itask: opt\n  keyword: opt\n"
        "steps:\n  - type: calc\n    name: step1\n"
    )

    step1_dir = root / "step1"
    step1_dir.mkdir()
    (step1_dir / "output.xyz").write_text("1\nCID=1 Energy=-100.0\nC 0 0 0\n")

    (root / "final.xyz").write_text("1\nCID=1 Energy=-100.0\nC 0 0 0\n")

    run_workflow([str(input_xyz)], str(config_file), work_dir=str(root))

    stats_path = root / "workflow_stats.json"
    assert stats_path.exists()

    with open(stats_path) as f:
        stats = json.load(f)

    assert "low_energy_trace" in stats
    assert len(stats["low_energy_trace"]["conformers"]) > 0
    assert "trace" in stats["low_energy_trace"]["conformers"][0]
