"""engine 模块测试（合并版）"""

import json
import os
import pytest
from unittest.mock import patch

from confflow.workflow.engine import (
    normalize_pair_list,
    as_list,
    count_conformers_any,
    count_conformers_in_xyz,
    validate_inputs_compatible,
    create_runtask_config,
    _normalize_iprog_label,
    _itask_label,
    _count_task_statuses_in_results_db,
    run_workflow,
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

    with pytest.raises(ValueError):
        normalize_pair_list("1 2 3")
    with pytest.raises(ValueError):
        normalize_pair_list(123)


def test_normalize_pair_list_extended_errors():
    with pytest.raises(ValueError, match="键对格式错误"):
        normalize_pair_list(["1,2,3"])
    with pytest.raises(ValueError, match="键对格式错误"):
        normalize_pair_list("1,2,3")
    with pytest.raises(ValueError, match="不支持的键对格式"):
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
    with pytest.raises(ValueError, match="未提供输入文件"):
        validate_inputs_compatible([])

    f1 = tmp_path / "f1.xyz"
    f1.write_text("invalid")
    with pytest.raises(ValueError, match="输入 XYZ 无法解析"):
        validate_inputs_compatible([str(f1)])

    f2 = tmp_path / "f2.xyz"
    f2.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n2\ntest\nC 0 0 0\nH 0 0 1.1\n")
    with pytest.raises(ValueError, match="多文件输入模式要求每个输入为单帧"):
        validate_inputs_compatible([str(f2)])

    f3 = tmp_path / "f3.xyz"
    f3.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")
    f4 = tmp_path / "f4.xyz"
    f4.write_text("2\ntest\nO 0 0 0\nH 0 0 1\n")
    with pytest.raises(ValueError, match="要求所有输入具有相同的原子数与元素顺序"):
        validate_inputs_compatible([str(f3), str(f4)])


def test_normalize_labels():
    assert _normalize_iprog_label("1") == "g16"
    assert _normalize_iprog_label("orca") == "orca"
    assert _normalize_iprog_label("custom") == "custom"

    assert _itask_label("0") == "opt"
    assert _itask_label("ts") == "ts"
    assert _itask_label("unknown") == "unknown"


def test_count_task_statuses_in_results_db(tmp_path):
    assert _count_task_statuses_in_results_db(str(tmp_path / "nonexistent.db")) is None

    db_path = tmp_path / "results.db"
    db_path.write_text("not a db")
    assert _count_task_statuses_in_results_db(str(db_path)) is None

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

    counts = _count_task_statuses_in_results_db(str(db_path))
    assert counts["success"] == 2
    assert counts["failed"] == 1
    assert counts["total"] == 3


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


def test_run_workflow_full_and_resume(tmp_path):
    xyz_content = "2\ntest\nC 0 0 0\nH 0 0 1\n"
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text(xyz_content)

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
        with open("traj.xyz", "w") as f:
            f.write("2\ngenerated\nC 0 0 0\nH 0 0 1.1\n")
            f.write("2\ngenerated\nC 0 0 0\nH 0 0 1.2\n")

    def mock_manager_run(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        with open(os.path.join(self.work_dir, "isomers_cleaned.xyz"), "w") as f:
            f.write("2\ncleaned\nC 0 0 0\nH 0 0 1.1\n")
        import sqlite3
        db_path = os.path.join(self.work_dir, "results.db")
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE task_results (status TEXT)")
        con.execute("INSERT INTO task_results VALUES ('success')")
        con.commit()
        con.close()

    with patch("confflow.blocks.confgen.run_generation", side_effect=mock_run_generation), \
         patch("confflow.calc.ChemTaskManager.run", autospec=True, side_effect=mock_manager_run), \
         patch("confflow.blocks.viz.generate_html_report"):

        with patch("confflow.config.schema.ConfigSchema.validate_calc_config", side_effect=[None, ValueError("stop here")]):
            with pytest.raises(ValueError, match="stop here"):
                run_workflow([str(input_xyz)], str(config_file), str(work_dir))

        checkpoint_file = work_dir / ".checkpoint"
        assert checkpoint_file.exists()
        with open(checkpoint_file, "r") as f:
            cp = json.load(f)
        assert cp["last_completed_step"] == 1

        stats = run_workflow([str(input_xyz)], str(config_file), str(work_dir), resume=True)
        assert len(stats["steps"]) == 1
        assert stats["steps"][0]["status"] == "completed"


def test_run_workflow_low_energy_trace(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")

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
        with open(os.path.join(self.work_dir, "isomers_cleaned.xyz"), "w") as f:
            f.write("2\nCID=s01_1 E=-1.0\nC 0 0 0\nH 0 0 1.1\n")

    with patch("confflow.calc.ChemTaskManager.run", autospec=True, side_effect=mock_manager_run), \
         patch("confflow.blocks.viz.generate_html_report"):
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
    from confflow.workflow.engine import create_runtask_config
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
        with open("traj.xyz", "w", encoding="utf-8") as f:
            f.write("2\nconf1\nH 0 0 0\nH 0 0 1\n")
            f.write("2\nconf2\nH 0 0 0\nH 0 0 1\n")

    monkeypatch.setattr(engine.confgen, "run_generation", fake_run_generation)
    monkeypatch.setattr(engine.viz, "parse_xyz_file", lambda p: [])
    monkeypatch.setattr(engine.viz, "generate_html_report", lambda *a, **k: None)

    work_dir = tmp_path / "work"
    engine.run_workflow(
        input_xyz=[str(a), str(b)],
        config_file=str(cfg_path),
        work_dir=str(work_dir),
        resume=False,
        verbose=False,
    )

    assert called["inputs"] is not None
    assert os.path.exists(work_dir / "step_01" / "traj.xyz")
