import json
from datetime import datetime
from unittest.mock import patch

import pytest


def test_workflow_engine_helpers():
    from confflow.workflow.engine import _itask_label, _normalize_iprog_label

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
    from confflow.workflow.engine import as_list, normalize_pair_list, validate_inputs_compatible

    with pytest.raises(Exception):
        validate_inputs_compatible(["a.xyz", "b.xyz"])

    assert as_list(None) is None
    assert as_list("a") == ["a"]
    assert as_list(["a"]) == ["a"]

    assert normalize_pair_list(None) is None
    assert normalize_pair_list("1,2") == [[1, 2]]
    assert normalize_pair_list(["1,2", "3,4"]) == [[1, 2], [3, 4]]


def test_workflow_engine_run_workflow_errors(tmp_path):
    from confflow.workflow.engine import run_workflow

    with pytest.raises(Exception):
        run_workflow(input_xyz=["test.xyz"], config_file="nonexistent.yaml", work_dir=str(tmp_path))

    config = {"global": {"work_dir": str(tmp_path)}, "steps": [{"name": "step1", "type": "confgen"}]}
    config_path = tmp_path / "config.yaml"
    import yaml

    with open(config_path, "w") as f:
        yaml.dump(config, f)

    with pytest.raises(FileNotFoundError):
        run_workflow(input_xyz=["nonexistent.xyz"], config_file=str(config_path), work_dir=str(tmp_path))


def test_workflow_engine_load_config_errors(tmp_path):
    from confflow.workflow.engine import load_workflow_config

    bad_cfg = tmp_path / "bad.yaml"
    bad_cfg.write_text("invalid: yaml: :")

    with pytest.raises(Exception):
        load_workflow_config(str(bad_cfg))

    missing_cfg = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        load_workflow_config(str(missing_cfg))


def test_workflow_engine_resume_logic(tmp_path):
    from confflow.workflow.engine import run_workflow

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
    (step2_dir / "isomers_cleaned.xyz").write_text("1\nCID=1\nC 0 0 0\n")

    res = run_workflow([str(input_xyz)], str(config_file), work_dir=str(root), resume=True)
    assert len(res["steps"]) == 1
    assert res["steps"][0]["name"] == "step2"


def test_workflow_engine_calc_resume(tmp_path):
    from confflow.workflow.engine import run_workflow

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
    (step_dir / "isomers_cleaned.xyz").write_text("1\nCID=1\nC 0 0 0\n")

    res = run_workflow([str(input_xyz)], str(config_file), work_dir=str(root))
    assert res["steps"][0]["status"] == "skipped"


def test_workflow_engine_load_checkpoint_exception(tmp_path):
    from confflow.workflow.engine import run_workflow

    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text("global:\n  itask: 1\n  keyword: sp\n  iprog: orca\nsteps:\n  - name: step1\n    type: calc")

    checkpoint = tmp_path / ".checkpoint"
    checkpoint.write_text("invalid json")

    try:
        run_workflow([str(xyz)], str(conf), work_dir=str(tmp_path), resume=True)
    except Exception:
        pass


def test_workflow_engine_trace_exception_trigger(tmp_path):
    from confflow.workflow.engine import run_workflow

    xyz = tmp_path / "test.xyz"
    xyz.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 0 -1")

    conf = tmp_path / "conf.yaml"
    conf.write_text(
        "global:\n  itask: 1\n  keyword: sp\n  iprog: orca\n"
        "steps:\n  - name: step1\n    type: calc\n  - name: step2\n    type: calc\n"
    )

    def mock_read_xyz_file(path, **kwargs):
        if "step1" in str(path) and "trace" not in str(path):
            return [{"cid": "1", "energy": -1.0, "atoms": []}]
        if "trace" in str(path):
            raise Exception("Simulated trace error")
        return []

    with patch("confflow.workflow.engine.calc.manager.ChemTaskManager.run", return_value={"success": 1}), \
        patch("confflow.workflow.engine.io_xyz.read_xyz_file", side_effect=mock_read_xyz_file), \
        patch("confflow.workflow.engine.viz.parse_xyz_file", return_value=[{"cid": "1", "energy": -1.0, "metadata": {}}]), \
        patch("confflow.workflow.engine.viz.generate_html_report"), \
        patch("confflow.workflow.engine.count_conformers_any", return_value=1), \
        patch("confflow.workflow.engine.is_multi_frame_any", return_value=False), \
        patch("confflow.workflow.engine.os.path.exists", return_value=True):
        run_workflow([str(xyz)], str(conf), work_dir=str(tmp_path))


def test_workflow_engine_low_energy_trace_full(tmp_path):
    from confflow.workflow.engine import run_workflow

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
    (step1_dir / "isomers_cleaned.xyz").write_text("1\nCID=1 Energy=-100.0\nC 0 0 0\n")

    (root / "final.xyz").write_text("1\nCID=1 Energy=-100.0\nC 0 0 0\n")

    res = run_workflow([str(input_xyz)], str(config_file), work_dir=str(root))

    stats_path = root / "workflow_stats.json"
    assert stats_path.exists()

    with open(stats_path) as f:
        stats = json.load(f)

    assert "low_energy_trace" in stats
    assert len(stats["low_energy_trace"]["conformers"]) > 0
    assert "trace" in stats["low_energy_trace"]["conformers"][0]
