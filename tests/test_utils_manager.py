#!/usr/bin/env python3

"""Tests for utils and manager modules (merged)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from confflow.calc.config_types import (
    CalcTaskConfig,
    CleanupOptions,
    ExecutionOptions,
    Program,
    TaskKind,
)
from confflow.calc.manager import ChemTaskManager
from confflow.core.utils import (
    ConfFlowLogger,
    InputFileError,
    XYZFormatError,
    format_duration_hms,
    format_index_ranges,
    parse_index_spec,
    parse_iprog,
    parse_itask,
    parse_memory,
    validate_xyz_file,
    validate_yaml_config,
)

# =============================================================================
# utils tests
# =============================================================================


def test_validate_yaml_config_requires_chains_for_confgen():
    cfg = {
        "global": {
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "gaussian_path": "g16",
            "orca_path": "orca",
        },
        "steps": [
            {"name": "step_01", "type": "confgen", "params": {}},
        ],
    }
    errors = validate_yaml_config(cfg)
    assert any("confgen step requires" in e for e in errors)


def test_validate_yaml_config_accepts_freeze_list_global():
    cfg = {
        "global": {
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "gaussian_path": "g16",
            "orca_path": "orca",
            "freeze": [86, 92],
        },
        "steps": [
            {"name": "step_01", "type": "confgen", "params": {"chains": ["1-2-3-4"]}},
        ],
    }
    errors = validate_yaml_config(cfg)
    assert errors == []


def test_validate_yaml_config_accepts_confgen_bond_overrides():
    cfg = {
        "global": {
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "gaussian_path": "g16",
            "orca_path": "orca",
        },
        "steps": [
            {
                "name": "step_01",
                "type": "confgen",
                "params": {
                    "chains": ["1-2-3-4"],
                    "add_bond": [[1, 2]],
                    "del_bond": [[3, 4]],
                    "no_rotate": [[2, 3]],
                    "force_rotate": [[2, 3]],
                },
            },
        ],
    }
    errors = validate_yaml_config(cfg)
    assert errors == []


def test_validate_yaml_config_accepts_confgen_bond_override_strings():
    cfg = {
        "global": {
            "cores_per_task": 1,
            "max_parallel_jobs": 1,
            "gaussian_path": "g16",
            "orca_path": "orca",
        },
        "steps": [
            {
                "name": "step_01",
                "type": "confgen",
                "params": {
                    "chains": ["1-2-3-4"],
                    "add_bond": "1 2",
                    "del_bond": ["3-4"],
                    "no_rotate": "2,3",
                    "force_rotate": ["2 3"],
                },
            },
        ],
    }
    errors = validate_yaml_config(cfg)
    assert errors == []


def test_validate_xyz_file_errors(tmp_path):
    with pytest.raises(InputFileError, match="File does not exist"):
        validate_xyz_file(str(tmp_path / "nonexistent.xyz"))

    with pytest.raises(InputFileError, match="Path is not a file"):
        validate_xyz_file(str(tmp_path))

    empty = tmp_path / "empty.xyz"
    empty.write_text("")
    with pytest.raises(InputFileError, match="File is empty"):
        validate_xyz_file(str(empty))

    f1 = tmp_path / "f1.xyz"
    f1.write_text("abc\ntest\nC 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f1))
    assert not valid
    assert len(geoms) == 0

    f2 = tmp_path / "f2.xyz"
    f2.write_text("-1\ntest\nC 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f2))
    assert not valid

    f3 = tmp_path / "f3.xyz"
    f3.write_text("2\ntest\nC 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f3))
    assert not valid

    f4 = tmp_path / "f4.xyz"
    f4.write_text("1\ntest\nC123 0 0 0\n")
    valid, geoms = validate_xyz_file(str(f4))
    assert not valid

    with pytest.raises(XYZFormatError):
        validate_xyz_file(str(f4), strict=True)


def test_validate_yaml_config_errors():
    errors = validate_yaml_config({})
    assert any("missing required section" in e for e in errors)

    config = {
        "global": {
            "gaussian_path": "/nonexistent/g16",
            "orca_path": "/nonexistent/orca",
            "cores_per_task": 0,
            "max_parallel_jobs": "abc",
        },
        "steps": "not a list",
    }
    errors = validate_yaml_config(config)
    assert any("Gaussian path not found" in e for e in errors)
    assert any("ORCA path not found" in e for e in errors)
    assert any("invalid cores_per_task" in e for e in errors)
    assert any("invalid max_parallel_jobs" in e for e in errors)
    assert any("'steps' must be a list" in e for e in errors)


def test_validate_yaml_config_rejects_missing_windows_absolute_executable_path():
    config = {
        "global": {
            "orca_path": r"C:\Program Files\ORCA\orca.exe",
        },
        "steps": [],
    }

    errors = validate_yaml_config(config)

    assert any("ORCA path not found" in e for e in errors)


def test_validate_yaml_config_keeps_plain_executable_names_unchecked():
    config = {
        "global": {
            "gaussian_path": "g16",
            "orca_path": "orca",
        },
        "steps": [],
    }

    errors = validate_yaml_config(config)

    assert errors == []


def test_validate_yaml_config_rejects_missing_posix_absolute_executable_path():
    config = {
        "global": {
            "gaussian_path": "/nonexistent/g16",
        },
        "steps": [],
    }

    errors = validate_yaml_config(config)

    assert any("Gaussian path not found" in e for e in errors)


def test_validate_yaml_config_handles_invalid_shapes():
    errors = validate_yaml_config({"global": [], "steps": ["bad"]})
    assert "'global' must be a dict" in errors
    assert "step 1 must be a dict" in errors

    errors = validate_yaml_config(
        {
            "global": {},
            "steps": [{"name": "s1", "type": "calc", "params": ["bad"]}],
        }
    )
    assert "step 's1': 'params' must be a dict" in errors


def test_validate_step_config_errors():
    from confflow.core.utils import _validate_step_config

    errors = _validate_step_config({}, 0)
    assert any("missing 'name' field" in e for e in errors)
    assert any("missing 'type' field" in e for e in errors)

    errors = _validate_step_config({"name": "s1", "type": "invalid"}, 0)
    assert any("invalid type" in e for e in errors)

    step = {
        "name": "s1",
        "type": "calc",
        "params": {
            "itask": "invalid",
            "iprog": "invalid",
        },
    }
    errors = _validate_step_config(step, 0)
    assert any("invalid itask value" in e for e in errors)
    assert any("invalid iprog value" in e for e in errors)

    step_ok = {
        "name": "s1",
        "type": "calc",
        "params": {"itask": "1", "iprog": "2", "keyword": "HF"},
    }
    assert _validate_step_config(step_ok, 0) == []

    step = {"name": "s1", "type": "calc", "params": {"iprog": "orca"}}
    errors = _validate_step_config(step, 0)
    assert any("ORCA task missing 'keyword' parameter" in e for e in errors)

    step = {"name": "s1", "type": "confgen", "params": {"add_bond": "invalid"}}
    errors = _validate_step_config(step, 0)
    assert any("requires 'chains'" in e for e in errors)
    assert any("format error" in e for e in errors)


def test_manager_default_backup_dir_is_step_local(tmp_path):
    mgr = ChemTaskManager(settings={"iprog": "orca", "itask": "sp", "keyword": "B3LYP"})
    mgr.work_dir = str(tmp_path / "step_01")
    mgr._ensure_work_dir()

    assert mgr.backup_dir == os.path.join(mgr.work_dir, "backups")
    assert os.path.isdir(mgr.backup_dir)


def test_parse_index_spec():
    assert parse_index_spec("1-3,5") == [1, 2, 3, 5]
    assert parse_index_spec("10") == [10]
    assert parse_index_spec("") == []


def test_parse_index_spec_extended():
    assert parse_index_spec("0") == []
    assert parse_index_spec("none") == []
    assert parse_index_spec("false") == []
    assert parse_index_spec([1, "2-3", 5]) == [1, 2, 3, 5]
    assert parse_index_spec("1-0") == []
    assert parse_index_spec("abc 123 def") == [123]


def test_format_index_ranges():
    assert format_index_ranges([1, 2, 3, 5]) == "1-3,5"
    assert format_index_ranges([10]) == "10"
    assert format_index_ranges([]) == "none"


def test_format_duration_hms():
    assert format_duration_hms(3661) == "1:01:01"
    assert format_duration_hms(60) == "1:00"


def test_parse_memory_extended():
    assert parse_memory("4GB") == 4096
    assert parse_memory("4GB", unit="GB") == 4
    assert parse_memory("1024MB") == 1024
    assert parse_memory("1024") == 1024
    assert parse_memory("invalid") == 4096


def test_parse_iprog_itask():
    assert parse_iprog({"iprog": "g16"}) == 1
    assert parse_iprog("orca") == 2
    assert parse_iprog(1) == 1
    assert parse_iprog("invalid") == 2

    assert parse_itask({"itask": "opt"}) == 0
    assert parse_itask("sp") == 1
    assert parse_itask("4") == 4
    assert parse_itask("invalid") == 3


def test_logger_embedded_mode():
    """Test that explicit embedded mode is honored before logger creation."""
    ConfFlowLogger._instance = None
    ConfFlowLogger._initialized = False
    ConfFlowLogger._embedded_mode = False
    ConfFlowLogger._embedded_mode_override = None
    ConfFlowLogger.set_embedded_mode(True)
    logger = ConfFlowLogger()

    assert ConfFlowLogger._embedded_mode is True
    assert logger.logger.propagate is True
    assert "console" not in logger.handlers

    ConfFlowLogger.set_embedded_mode(False)
    assert ConfFlowLogger._embedded_mode is False
    logger.close()


def test_logger_file_handler(tmp_path):
    log_file = tmp_path / "test.log"
    logger = ConfFlowLogger()
    ConfFlowLogger._embedded_mode = False
    logger.add_file_handler(str(log_file))
    assert "file" in logger.handlers
    logger.info("test message")
    logger.close()
    assert log_file.exists()


# =============================================================================
# manager tests
# =============================================================================


def test_manager_init_no_config(tmp_path):
    manager = ChemTaskManager(None, resume_dir=str(tmp_path / "work"))
    assert isinstance(manager.config, dict)
    assert manager.config.get("iprog") is None or manager.config.get("iprog") == "orca"
    assert manager.work_dir == str(tmp_path / "work")


def test_manager_init_with_config(tmp_path):
    cfg = tmp_path / "test.ini"
    cfg.write_text("[DEFAULT]\nprogram = gaussian\n")
    manager = ChemTaskManager(str(cfg))
    assert manager.config["program"] == "gaussian"


def test_manager_init_with_structured_config_preserves_object_and_bool_flags(tmp_path):
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="HF def2-SVP",
        execution=ExecutionOptions(enable_dynamic_resources=True),
    )
    legacy = {"iprog": "orca", "itask": "sp", "keyword": "HF def2-SVP"}
    manager = ChemTaskManager(
        settings=legacy, execution_config=structured, resume_dir=str(tmp_path / "work")
    )
    assert manager.compat_config is manager.config
    assert manager.execution_config is structured
    assert manager.config["iprog"] == "orca"
    assert manager.config["itask"] == "sp"
    assert manager.config["keyword"] == "HF def2-SVP"
    assert manager.monitor is not None


def test_manager_run_uses_legacy_config_for_signature_paths_with_structured_execution(
    tmp_path, monkeypatch
):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n", encoding="utf-8")

    legacy = {"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": "false"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(auto_clean=False),
    )
    manager = ChemTaskManager(
        settings=legacy,
        execution_config=structured,
        resume_dir=str(tmp_path / "work"),
    )

    seen: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "confflow.calc.manager.prepare_calc_step_dir",
        lambda step_dir, config, input_signature=None, execution_config=None: (
            seen.append(("prepare", config)),
            SimpleNamespace(cleaned_stale_artifacts=False),
        )[1],
    )
    monkeypatch.setattr(
        "confflow.calc.manager.record_calc_step_signature",
        lambda step_dir, config, input_signature=None, execution_config=None: seen.append(
            ("record", config)
        ),
    )
    monkeypatch.setattr(
        "confflow.calc.manager.TaskSourceBuilder.build_from_input",
        lambda self, input_xyz_file: ([], {}),
    )

    manager.run(str(xyz_file))

    assert manager.compat_config is manager.config
    assert seen[0][1] == manager.config
    assert seen[1][1] == manager.config
    # Runtime config remains sparse
    assert manager.config["iprog"] == "orca"
    assert manager.config.get("auto_clean") == "false"


def test_manager_config_updates_affect_signature_paths_with_dual_lane(tmp_path, monkeypatch):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n", encoding="utf-8")

    legacy = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(auto_clean=False),
    )
    manager = ChemTaskManager(
        settings=legacy,
        execution_config=structured,
        resume_dir=str(tmp_path / "work"),
    )
    manager.config.update({"max_parallel_jobs": "7"})

    seen: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "confflow.calc.manager.prepare_calc_step_dir",
        lambda step_dir, config, input_signature=None, execution_config=None: (
            seen.append(("prepare", config)),
            SimpleNamespace(cleaned_stale_artifacts=False),
        )[1],
    )
    monkeypatch.setattr(
        "confflow.calc.manager.record_calc_step_signature",
        lambda step_dir, config, input_signature=None, execution_config=None: seen.append(
            ("record", config)
        ),
    )
    monkeypatch.setattr(
        "confflow.calc.manager.TaskSourceBuilder.build_from_input",
        lambda self, input_xyz_file: ([], {}),
    )

    manager.run(str(xyz_file))

    assert manager.compat_config is manager.config
    assert seen[0][1] == manager.config
    assert seen[1][1] == manager.config
    assert manager.config["max_parallel_jobs"] == "7"


def test_manager_signature_uses_workflow_legacy_baseline_for_cleanup_mapping(tmp_path, monkeypatch):
    from confflow.workflow.task_config import build_structured_task_config, build_task_config

    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\nx\nH 0 0 0\n", encoding="utf-8")

    global_config = {
        "charge": 0,
        "multiplicity": 1,
        "cores_per_task": 1,
        "total_memory": "4GB",
        "max_parallel_jobs": 1,
    }
    params = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": False,
        "clean_params": {"threshold": 0.5, "energy_window": 8.0},
    }
    expected = build_task_config(params, global_config)
    structured = build_structured_task_config(params, global_config)

    manager = ChemTaskManager(settings=expected, execution_config=structured, resume_dir=str(tmp_path / "work"))

    seen = []
    monkeypatch.setattr(
        "confflow.calc.manager.prepare_calc_step_dir",
        lambda step_dir, config, input_signature=None, execution_config=None: (
            seen.append(("prepare", dict(config))),
            SimpleNamespace(cleaned_stale_artifacts=False),
        )[1],
    )
    monkeypatch.setattr(
        "confflow.calc.manager.record_calc_step_signature",
        lambda step_dir, config, input_signature=None, execution_config=None: seen.append(
            ("record", dict(config))
        ),
    )
    monkeypatch.setattr(
        "confflow.calc.manager.TaskSourceBuilder.build_from_input",
        lambda self, input_xyz_file: ([], {}),
    )

    manager.run(str(xyz_file))

    assert seen[0][1] == manager.config
    assert seen[1][1] == manager.config
    runtime_keys = {"backup_dir", "stop_beacon_file"}
    assert {
        k: v for k, v in manager._compat_signature_config().items() if k not in runtime_keys
    } == {k: v for k, v in expected.items() if k not in runtime_keys}


def test_manager_auto_clean_accepts_bool_flag(tmp_path):
    manager = ChemTaskManager(
        settings={"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": "true"},
        execution_config=CalcTaskConfig(
            program=Program.ORCA,
            task=TaskKind.SP,
            keyword="xTB",
            execution=ExecutionOptions(auto_clean=True),
        ),
    )
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    mock_refine.assert_called_once()


def test_manager_auto_clean_uses_structured_cleanup_values(tmp_path):
    manager = ChemTaskManager(
        settings={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": "true",
        },
        execution_config=CalcTaskConfig(
            program=Program.ORCA,
            task=TaskKind.SP,
            keyword="xTB",
            cleanup=CleanupOptions(
                enabled=True,
                rmsd_threshold=0.12,
                energy_window=7.5,
                energy_tolerance=0.03,
                dedup_only=True,
                no_h=True,
            ),
            execution=ExecutionOptions(auto_clean=True),
        ),
    )
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    kwargs = mock_refine.call_args.kwargs
    assert kwargs["threshold"] == 0.12
    assert kwargs["ewin"] == 7.5
    assert kwargs["energy_tolerance"] == 0.03


def test_manager_auto_clean_prefers_updated_public_clean_opts_over_structured_cleanup(tmp_path):
    manager = ChemTaskManager(
        settings={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": "true",
        },
        execution_config=CalcTaskConfig(
            program=Program.ORCA,
            task=TaskKind.SP,
            keyword="xTB",
            cleanup=CleanupOptions(
                enabled=True,
                rmsd_threshold=0.12,
                energy_window=7.5,
                energy_tolerance=0.03,
            ),
            execution=ExecutionOptions(auto_clean=True),
        ),
    )
    manager.config.update({"clean_opts": "-t 0.44 -ewin 11.0 --energy-tolerance 0.08"})
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    kwargs = mock_refine.call_args.kwargs
    assert kwargs["threshold"] == 0.44
    assert kwargs["ewin"] == 11.0
    assert kwargs["energy_tolerance"] == 0.08


def test_manager_auto_clean_falls_back_to_legacy_clean_opts(tmp_path):
    manager = ChemTaskManager(
        settings={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": "true",
            "clean_opts": "-t 0.21 -ewin 8.0 --energy-tolerance 0.07",
        },
    )
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    kwargs = mock_refine.call_args.kwargs
    assert kwargs["threshold"] == 0.21
    assert kwargs["ewin"] == 8.0
    assert kwargs["energy_tolerance"] == 0.07


def test_manager_auto_clean_prefers_structured_cleanup_over_legacy_clean_opts(tmp_path):
    manager = ChemTaskManager(
        settings={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": "true",
            "clean_opts": "-t 0.99 -ewin 99.0 --energy-tolerance 0.99",
        },
        execution_config=CalcTaskConfig(
            program=Program.ORCA,
            task=TaskKind.SP,
            keyword="xTB",
            cleanup=CleanupOptions(
                enabled=True,
                rmsd_threshold=0.18,
                energy_window=5.0,
                energy_tolerance=0.02,
            ),
            execution=ExecutionOptions(auto_clean=True),
        ),
    )
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    kwargs = mock_refine.call_args.kwargs
    assert kwargs["threshold"] == 0.99
    assert kwargs["ewin"] == 99.0
    assert kwargs["energy_tolerance"] == 0.99


def test_manager_auto_clean_uses_structured_cleanup_when_public_clean_opts_absent(tmp_path):
    manager = ChemTaskManager(
        settings={
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": "true",
        },
        execution_config=CalcTaskConfig(
            program=Program.ORCA,
            task=TaskKind.SP,
            keyword="xTB",
            cleanup=CleanupOptions(
                enabled=True,
                rmsd_threshold=0.18,
                energy_window=5.0,
                energy_tolerance=0.02,
            ),
            execution=ExecutionOptions(auto_clean=True),
        ),
    )
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    kwargs = mock_refine.call_args.kwargs
    assert kwargs["threshold"] == 0.18
    assert kwargs["ewin"] == 5.0
    assert kwargs["energy_tolerance"] == 0.02


def test_job_name_for_geom_discards_legacy_numeric_cid():
    assert ChemTaskManager._job_name_for_geom(2, {"metadata": {"CID": 1.0}}) == "A000003"
    assert ChemTaskManager._job_name_for_geom(2, {"metadata": {"CID": "2.0"}}) == "A000003"


def test_manager_ensure_work_dir(tmp_path):
    manager = ChemTaskManager(None, resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()
    assert os.path.exists(tmp_path / "work")
    assert manager.results_db is not None
    assert manager.backup_dir is not None


def test_read_single_frame_xyz_coords(tmp_path):
    manager = ChemTaskManager(None)
    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\n\nC 0 0 0\nH 0 0 1\n")
    coords = manager._read_single_frame_xyz_coords(str(xyz))
    assert coords is not None
    assert len(coords) == 2
    assert "C" in coords[0]
    assert "0" in coords[0]


def test_read_single_frame_xyz_coords_invalid(tmp_path):
    manager = ChemTaskManager(None)
    xyz = tmp_path / "bad.xyz"
    xyz.write_text("not an xyz")
    assert manager._read_single_frame_xyz_coords(str(xyz)) is None
    assert manager._read_single_frame_xyz_coords("nonexistent.xyz") is None


def test_read_xyz_basic(tmp_path):
    mgr = ChemTaskManager(None)
    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\ncomment\nC 0 0 0\nC 1.5 0 0\n")
    confs = mgr._read_xyz(str(xyz))
    assert len(confs) == 1
    assert confs[0]["title"] == "comment"
    assert len(confs[0]["coords"]) == 2


def test_read_xyz_fallback(tmp_path):
    manager = ChemTaskManager(None)
    xyz_path = tmp_path / "bad.xyz"
    xyz_path.write_text(
        "2\ncomment\nC 0.0 0.0 0.0\nC 1.5 0.0 0.0\n\n3\nnext\nO 0 0 0\nH 1 0 0\nH 0 1 0"
    )

    geoms = manager._read_xyz(str(xyz_path))
    assert len(geoms) == 2
    assert geoms[0]["title"] == "comment"
    assert len(geoms[0]["coords"]) == 2


def test_recover_result_from_backups_gaussian(tmp_path):
    mgr = ChemTaskManager(None)
    mgr.backup_dir = str(tmp_path / "backup")
    os.makedirs(mgr.backup_dir)

    log = tmp_path / "backup" / "job1.log"
    log.write_text("Normal termination of Gaussian 16\nSCF Done: E(RB3LYP) = -1.0\n")

    xyz = tmp_path / "backup" / "job1.xyz"
    xyz.write_text("2\n\nC 0 0 0\nC 1.5 0 0\n")

    task = {"job_name": "job1", "config": {"iprog": 1}}
    res = mgr._recover_result_from_backups(task)
    assert res["status"] == "success"
    assert res["energy"] == -1.0


def test_manager_run_stop_beacon(tmp_path):
    settings = tmp_path / "settings.ini"
    settings.write_text("[calc]\nmax_parallel_jobs=1\n")

    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n2\ntest2\nC 0 0 0\nH 0 0 2\n")

    manager = ChemTaskManager(str(settings), resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()

    stop_file = tmp_path / "work" / "STOP"
    stop_file.touch()

    manager.run(str(xyz))
    assert manager.stop_requested is True


def test_manager_run_failed_output(tmp_path, monkeypatch):
    import confflow.calc.manager

    settings = tmp_path / "settings.ini"
    settings.write_text("[calc]\nmax_parallel_jobs=1\n")

    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\ntest\nC 0 0 0\nH 0 0 1\n")

    manager = ChemTaskManager(str(settings), resume_dir=str(tmp_path / "work"))

    monkeypatch.setattr(
        confflow.calc.manager,
        "_run_task",
        lambda t: {"job_name": t["job_name"], "status": "failed", "error": "test error"},
    )

    manager.run(str(xyz))

    failed_file = tmp_path / "work" / "failed.xyz"
    assert failed_file.exists()


def test_manager_stop_beacon_async(tmp_path, monkeypatch):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n1\ntest2\nH 0.0 0.0 1.0\n")

    settings_file = tmp_path / "settings.ini"
    settings_file.write_text("[Global]\nmax_parallel_jobs=1\n")

    manager = ChemTaskManager(str(settings_file), resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()

    monkeypatch.setattr("confflow.calc.manager.as_completed", lambda x: x)

    class SyncExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def shutdown(self, *args, **kwargs):
            pass

        def submit(self, func, *args, **kwargs):
            stop_file = tmp_path / "work" / "STOP"
            stop_file.write_text("")
            res = func(*args, **kwargs)
            fut = MagicMock()
            fut.result.return_value = res
            return fut

    monkeypatch.setattr("confflow.calc.manager.ProcessPoolExecutor", SyncExecutor)

    def fake_run_task(task):
        return {
            "job_name": task["job_name"],
            "status": "success",
            "energy": -1.0,
            "final_coords": task["coords"],
        }

    with patch("confflow.calc.manager._run_task", side_effect=fake_run_task):
        manager.run(str(xyz_file))

    assert manager.stop_requested is True


def test_manager_auto_clean(tmp_path, monkeypatch):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n")

    settings_file = tmp_path / "settings.ini"
    settings_file.write_text("[Global]\nauto_clean=true\nclean_opts=-t 0.1 -ewin 10.0\n")

    manager = ChemTaskManager(str(settings_file), resume_dir=str(tmp_path / "work"))
    manager._ensure_work_dir()
    monkeypatch.setattr(
        "confflow.calc.manager.prepare_calc_step_dir",
        lambda *args, **kwargs: SimpleNamespace(cleaned_stale_artifacts=False),
    )

    manager.results_db.insert_result(
        {
            "job_name": "A000001",
            "status": "success",
            "energy": -1.0,
            "final_coords": ["H 0.0 0.0 0.0"],
        }
    )

    mock_refine = MagicMock()
    monkeypatch.setattr("confflow.blocks.refine.process_xyz", mock_refine)

    manager.run(str(xyz_file))

    assert mock_refine.called


def test_manager_recreates_results_db_after_stale_cleanup(tmp_path, monkeypatch):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n", encoding="utf-8")

    manager = ChemTaskManager(settings={"iprog": "orca", "itask": "sp", "keyword": "xTB"})
    manager.work_dir = str(tmp_path / "work")
    manager._ensure_work_dir()
    original_db = tmp_path / "work" / "results.db"
    assert original_db.exists()

    def fake_prepare(step_dir, config, input_signature=None, execution_config=None):
        original_db.unlink(missing_ok=True)
        return SimpleNamespace(cleaned_stale_artifacts=True)

    monkeypatch.setattr("confflow.calc.manager.prepare_calc_step_dir", fake_prepare)
    monkeypatch.setattr(
        "confflow.calc.manager.record_calc_step_signature",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "confflow.calc.manager.TaskSourceBuilder.build_from_input",
        lambda self, input_xyz_file: ([], {}),
    )

    manager.run(str(xyz_file))

    assert original_db.exists()
    assert (tmp_path / "work" / "calc.log").exists()


def test_manager_run_uses_overridden_input_signature(tmp_path, monkeypatch):
    xyz_file = tmp_path / "input.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n", encoding="utf-8")

    manager = ChemTaskManager(settings={"iprog": "orca", "itask": "sp", "keyword": "xTB"})
    manager.work_dir = str(tmp_path / "work")
    manager._input_signature_override = "combinedsig"

    seen: list[str | None] = []

    monkeypatch.setattr(
        "confflow.calc.manager.prepare_calc_step_dir",
        lambda step_dir, config, input_signature=None, execution_config=None: (
            seen.append(input_signature),
            SimpleNamespace(cleaned_stale_artifacts=False),
        )[1],
    )
    monkeypatch.setattr(
        "confflow.calc.manager.record_calc_step_signature",
        lambda step_dir, config, input_signature=None, execution_config=None: seen.append(
            input_signature
        ),
    )
    monkeypatch.setattr(
        "confflow.calc.manager.TaskSourceBuilder.build_from_input",
        lambda self, input_xyz_file: ([], {}),
    )

    manager.run(str(xyz_file))

    assert seen == ["combinedsig", "combinedsig"]


def test_manager_recover_orca(tmp_path):
    manager = ChemTaskManager("", resume_dir=str(tmp_path / "work"))
    manager.backup_dir = str(tmp_path / "backup")
    os.makedirs(manager.backup_dir)

    log_file = tmp_path / "backup" / "job1.out"
    log_file.write_text(
        "FINAL SINGLE POINT ENERGY      -123.456\n****ORCA TERMINATED NORMALLY****\n"
    )

    xyz_file = tmp_path / "backup" / "job1.xyz"
    xyz_file.write_text("1\ntest\nH 0.0 0.0 0.0\n")

    task = {
        "job_name": "job1",
        "config": {"iprog": "orca", "itask": "sp"},
    }

    res = manager._recover_result_from_backups(task)
    assert res is not None
    assert res["status"] == "success"
    assert res["energy"] == -123.456


def test_manager_read_xyz_errors(tmp_path):
    manager = ChemTaskManager("")
    assert manager._read_xyz("non_existent.xyz") == []

    bad_xyz = tmp_path / "bad.xyz"
    bad_xyz.write_text("not_a_number\ncomment\nH 0 0 0\n")
    assert manager._read_xyz(str(bad_xyz)) == []

    truncated_xyz = tmp_path / "truncated.xyz"
    truncated_xyz.write_text("2\ncomment\nH 0 0 0\n")
    assert manager._read_xyz(str(truncated_xyz)) == []


def test_manager_iter_input_geometries_skips_bad_frame_and_keeps_later_valid(tmp_path):
    xyz = tmp_path / "mixed.xyz"
    xyz.write_text(
        "1\nok1\nH 0 0 0\n" "1\nbad\nH nope 0 0\n" "1\nok2\nH 0 0 1\n",
        encoding="utf-8",
    )

    manager = ChemTaskManager("")
    geoms = list(manager._iter_input_geometries(str(xyz)))

    assert len(geoms) == 2
    assert geoms[0]["title"] == "ok1"
    assert geoms[1]["title"] == "ok2"


# =============================================================================
# Manager path-coverage tests (merged from test_calc_manager_paths.py)
# =============================================================================


def test_manager_main_cli(tmp_path):
    from confflow.calc.manager import main as manager_main

    xyz_path = tmp_path / "test.xyz"
    xyz_path.write_text("1\n\nH 0 0 0\n")
    ini_path = tmp_path / "test.ini"
    ini_path.write_text("[global]\nengine=orca\n")

    with patch("confflow.calc.manager.ChemTaskManager.run") as mock_run:
        with patch("sys.argv", ["confcalc", str(xyz_path), "-s", str(ini_path)]):
            manager_main()
            mock_run.assert_called_once()

    with patch("sys.argv", ["confcalc", "nonexistent.xyz", "-s", str(ini_path)]):
        with pytest.raises(SystemExit) as e:
            manager_main()
        assert e.value.code == 1

    with patch("sys.argv", ["confcalc", str(xyz_path), "-s", "nonexistent.ini"]):
        with pytest.raises(SystemExit) as e:
            manager_main()
        assert e.value.code == 1


def test_manager_read_xyz_fallback_more(tmp_path):
    mgr = ChemTaskManager(None)

    tmp_path / "bad.xyz"
    mgr = ChemTaskManager(settings_file="", resume_dir=str(tmp_path / "wd"))

    mgr._ensure_work_dir()
    stop_path = mgr.config["stop_beacon_file"]
    os.makedirs(os.path.dirname(stop_path), exist_ok=True)
    with open(stop_path, "w") as f:
        f.write("STOP")

    geoms = [
        {"title": "a", "coords": ["H 0 0 0"], "metadata": {}},
        {"title": "b", "coords": ["H 0 0 1"], "metadata": {}},
    ]

    class FakeResultsDB:
        def __init__(self, *args, **kwargs):
            self.inserted = []

        def get_result_by_job_name(self, job_name):
            return None

        def insert_result(self, res):
            self.inserted.append(res)

        def get_all_results(self):
            return []

    class _Fut:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExec:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def shutdown(self, *args, **kwargs):
            pass

        def submit(self, fn, arg):
            return _Fut(
                {"job_name": arg["job_name"], "status": "success", "final_coords": ["H 0 0 0"]}
            )

    with (
        patch("confflow.calc.manager.ResultsDB", FakeResultsDB),
        patch.object(ChemTaskManager, "_read_xyz", return_value=geoms),
        patch("confflow.calc.manager.ProcessPoolExecutor", FakeExec),
        patch("confflow.calc.manager.as_completed", lambda futs: list(futs)),
        patch("confflow.calc.manager.CalcProgressReporter"),
        patch("confflow.calc.manager.parse_iprog", return_value=1),
        patch("confflow.calc.manager.get_policy"),
        patch("confflow.calc.manager._cleanup_lingering_processes") as mock_cleanup,
    ):
        mgr.run(str(tmp_path / "input.xyz"))
        assert mock_cleanup.called


def test_calc_manager_failed_output_and_auto_clean_parse_errors(tmp_path):
    from types import SimpleNamespace

    mgr = ChemTaskManager(settings_file="", resume_dir=str(tmp_path / "wd"))
    mgr.config.update(
        {
            "auto_clean": "true",
            "clean_opts": "-t nope -ewin nope",
            "cores_per_task": "2",
            "max_parallel_jobs": "1",
        }
    )

    geoms = [
        {
            "title": "geom1",
            "coords": ["H 0 0 0", "H 0 0 1"],
            "metadata": {"CID": "123"},
        }
    ]

    long_err = "x" * 500

    class FakeResultsDB:
        def __init__(self, *args, **kwargs):
            self.inserted = []

        def get_result_by_job_name(self, job_name):
            return None

        def insert_result(self, res):
            self.inserted.append(res)

        def get_all_results(self):
            return [
                {"job_name": "A000001", "status": "failed", "error": long_err},
                {
                    "job_name": "A000001",
                    "status": "success",
                    "energy": -1.0,
                    "final_coords": ["H 0 0 0", "H 0 0 1"],
                    "num_imag_freqs": 1,
                    "lowest_freq": -12.3,
                    "ts_bond_atoms": "1,2",
                    "ts_bond_length": 1.234567,
                },
            ]

        def close(self):
            pass

    with (
        patch("confflow.calc.manager.ResultsDB", FakeResultsDB),
        patch.object(ChemTaskManager, "_read_xyz", return_value=geoms),
        patch(
            "confflow.calc.manager._run_task",
            return_value={
                "job_name": "A000001",
                "status": "success",
                "final_coords": ["H 0 0 0", "H 0 0 1"],
                "energy": -1.0,
            },
        ),
        patch("confflow.blocks.refine.RefineOptions") as mock_opts,
        patch("confflow.blocks.refine.process_xyz", side_effect=RuntimeError("boom")),
    ):
        mock_opts.return_value = SimpleNamespace(output=str(tmp_path / "wd" / "output.xyz"))
        mgr.run(str(tmp_path / "input.xyz"))

        assert (tmp_path / "wd" / "failed.xyz").exists()
        assert (tmp_path / "wd" / "result.xyz").exists()


def test_calc_manager_executor_path_inserts_results(tmp_path):
    geoms = [
        {"title": "a", "coords": ["H 0 0 0"], "metadata": {"CID": "1"}},
        {"title": "b", "coords": ["H 0 0 1"], "metadata": {"CID": "2"}},
    ]

    class FakeResultsDB:
        def __init__(self, *args, **kwargs):
            self.inserted = []

        def get_result_by_job_name(self, job_name):
            return None

        def insert_result(self, res):
            self.inserted.append(res)

        def get_all_results(self):
            return list(self.inserted)

        def close(self):
            pass

    class _Fut:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExec:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def shutdown(self, *args, **kwargs):
            pass

        def submit(self, fn, arg):
            return _Fut(
                {
                    **arg,
                    "status": "success",
                    "energy": -1.0,
                    "final_coords": arg.get("coords") or ["H 0 0 0"],
                }
            )

    with (
        patch("confflow.calc.manager.ResultsDB", FakeResultsDB),
        patch.object(ChemTaskManager, "_read_xyz", return_value=geoms),
        patch("confflow.calc.manager.ProcessPoolExecutor", FakeExec),
        patch("confflow.calc.manager.as_completed", lambda futs: list(futs)),
        patch("confflow.calc.manager.CalcProgressReporter"),
    ):
        mgr = ChemTaskManager(settings_file="", resume_dir=str(tmp_path / "wd"))
        mgr.run(str(tmp_path / "input.xyz"))

        assert (tmp_path / "wd" / "result.xyz").exists()


def test_config_hash_matches_auto_clean_effective_semantics(tmp_path):
    """Verify .config_hash effective cleanup semantics match _run_auto_clean() actual clean_opts."""
    from confflow.calc.step_contract import (
        compute_calc_config_signature,
        resolve_effective_auto_clean,
    )

    legacy = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": "true",
        "clean_opts": "-t 0.33 -ewin 9.5 --energy-tolerance 0.05",
    }
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        cleanup=CleanupOptions(
            enabled=True,
            rmsd_threshold=0.22,
            energy_window=6.0,
            energy_tolerance=0.03,
        ),
        execution=ExecutionOptions(auto_clean=True),
    )

    # Compute signature hash
    sig_hash = compute_calc_config_signature(legacy, execution_config=structured)

    # Resolve effective auto-clean (same logic used by _run_auto_clean)
    enabled, effective_clean_opts = resolve_effective_auto_clean(legacy, structured)

    assert enabled is True
    # Legacy clean_opts should take priority over structured cleanup
    assert "0.33" in effective_clean_opts
    assert "9.5" in effective_clean_opts
    assert "0.05" in effective_clean_opts

    # Verify signature changes when effective cleanup changes
    legacy2 = dict(legacy)
    legacy2["clean_opts"] = "-t 0.44 -ewin 10.0 --energy-tolerance 0.06"
    sig_hash2 = compute_calc_config_signature(legacy2, execution_config=structured)
    assert sig_hash != sig_hash2


def test_dual_lane_clean_opts_update_syncs_signature_and_auto_clean(tmp_path):
    """Verify manager.config.update(clean_opts) syncs both signature and auto-clean."""
    from confflow.calc.step_contract import compute_calc_config_signature

    legacy = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": "true",
    }
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        cleanup=CleanupOptions(
            enabled=True,
            rmsd_threshold=0.15,
            energy_window=5.0,
            energy_tolerance=0.02,
        ),
        execution=ExecutionOptions(auto_clean=True),
    )

    manager = ChemTaskManager(settings=legacy, execution_config=structured)

    # Initial signature uses structured cleanup (no legacy clean_opts)
    sig_before = compute_calc_config_signature(
        manager.config, execution_config=manager.execution_config
    )
    _, clean_opts_before = manager._resolve_effective_clean_opts()
    assert "0.15" in clean_opts_before  # From structured cleanup

    # Update public clean_opts
    manager.config.update({"clean_opts": "-t 0.55 -ewin 12.0 --energy-tolerance 0.07"})

    # Both signature and auto-clean should now use updated value
    sig_after = compute_calc_config_signature(
        manager.config, execution_config=manager.execution_config
    )
    _, clean_opts_after = manager._resolve_effective_clean_opts()

    assert sig_before != sig_after
    assert "0.55" in clean_opts_after
    assert "12.0" in clean_opts_after
    assert "0.07" in clean_opts_after

    # Verify auto-clean actually uses the updated value
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    kwargs = mock_refine.call_args.kwargs
    assert kwargs["threshold"] == 0.55
    assert kwargs["ewin"] == 12.0
    assert kwargs["energy_tolerance"] == 0.07


def test_config_hash_ignores_execution_only_runtime_knobs():
    from confflow.calc.step_contract import compute_calc_config_signature

    base = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": "false",
        "gaussian_write_chk": "false",
        "enable_dynamic_resources": "false",
        "resume_from_backups": "false",
    }
    changed = dict(base)
    changed.update(
        {
            "gaussian_write_chk": "true",
            "enable_dynamic_resources": "true",
            "resume_from_backups": "true",
        }
    )

    assert compute_calc_config_signature(base) == compute_calc_config_signature(changed)


def test_clean_params_alias_keeps_runtime_and_signature_in_sync():
    from confflow.calc.step_contract import (
        compute_calc_config_signature,
        resolve_effective_auto_clean,
    )

    cfg = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": "true",
        "clean_params": {"threshold": 0.4, "energy_window": 8.0},
    }
    changed = dict(cfg)
    changed["clean_params"] = {"threshold": 0.5, "energy_window": 8.0}

    enabled, clean_opts = resolve_effective_auto_clean(cfg)

    assert enabled is True
    assert clean_opts == "-t 0.4 -ewin 8.0"
    assert compute_calc_config_signature(cfg) != compute_calc_config_signature(changed)


def test_manager_missing_auto_clean_allows_overlay_to_enable(tmp_path):
    """When compat config lacks auto_clean, execution overlay can enable it."""
    settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(auto_clean=True),
    )
    manager = ChemTaskManager(settings=settings, execution_config=structured)
    
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    mock_refine.assert_called_once()


def test_manager_explicit_false_auto_clean_blocks_overlay(tmp_path):
    """When compat config explicitly sets auto_clean=false, overlay cannot override."""
    settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": "false"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(auto_clean=True),
    )
    manager = ChemTaskManager(settings=settings, execution_config=structured)
    
    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    mock_refine.assert_not_called()


def test_manager_missing_enable_dynamic_resources_allows_overlay(tmp_path):
    """When compat config lacks enable_dynamic_resources, overlay can enable it."""
    settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(enable_dynamic_resources=True),
    )
    manager = ChemTaskManager(settings=settings, execution_config=structured, resume_dir=str(tmp_path / "work"))
    
    assert manager.monitor is not None


def test_manager_explicit_false_enable_dynamic_resources_blocks_overlay(tmp_path):
    """When compat config explicitly sets enable_dynamic_resources=false, overlay cannot override."""
    settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB", "enable_dynamic_resources": "false"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(enable_dynamic_resources=True),
    )
    manager = ChemTaskManager(settings=settings, execution_config=structured, resume_dir=str(tmp_path / "work"))
    
    assert manager.monitor is None


def test_manager_and_workflow_produce_same_config_hash():
    """Sparse manager config and workflow path must produce identical .config_hash when semantics match."""
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config

    # Sparse standalone compat path without auto_clean now follows runtime default: false.
    sparse_settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    manager = ChemTaskManager(settings=sparse_settings, execution_config=None)
    manager_baseline = manager._compat_signature_config()
    manager_hash = compute_calc_config_signature(manager_baseline)

    assert manager_baseline["auto_clean"] == "false"

    # Match against an explicit workflow baseline with auto_clean=false.
    global_config = {}
    params = {"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": False}
    workflow_baseline = build_task_config(params, global_config)
    workflow_hash = compute_calc_config_signature(workflow_baseline)

    assert manager_hash == workflow_hash

    # Explicit auto_clean=false remains stable.
    sparse_settings2 = {"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": "false"}
    manager2 = ChemTaskManager(settings=sparse_settings2, execution_config=None)
    manager_baseline2 = manager2._compat_signature_config()
    manager_hash2 = compute_calc_config_signature(manager_baseline2)

    params2 = {"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": False}
    workflow_baseline2 = build_task_config(params2, global_config)
    workflow_hash2 = compute_calc_config_signature(workflow_baseline2)

    assert manager_hash2 == workflow_hash2


@pytest.mark.parametrize(
    ("settings", "execution_config", "expected_enabled"),
    [
        ({"iprog": "orca", "itask": "sp", "keyword": "xTB"}, None, False),
        (
            {"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": "false"},
            None,
            False,
        ),
        (
            {"iprog": "orca", "itask": "sp", "keyword": "xTB"},
            CalcTaskConfig(
                program=Program.ORCA,
                task=TaskKind.SP,
                keyword="xTB",
                execution=ExecutionOptions(auto_clean=False),
            ),
            False,
        ),
        (
            {"iprog": "orca", "itask": "sp", "keyword": "xTB"},
            CalcTaskConfig(
                program=Program.ORCA,
                task=TaskKind.SP,
                keyword="xTB",
                cleanup=CleanupOptions(enabled=True, rmsd_threshold=0.25),
                execution=ExecutionOptions(auto_clean=True),
            ),
            True,
        ),
    ],
    ids=[
        "sparse-no-auto-clean-no-overlay",
        "sparse-explicit-false",
        "sparse-overlay-false",
        "sparse-overlay-true",
    ],
)
def test_sparse_compat_auto_clean_runtime_and_signature_stay_aligned(
    tmp_path, settings, execution_config, expected_enabled
):
    from confflow.calc.step_contract import inspect_calc_step_state, record_calc_step_signature

    manager = ChemTaskManager(
        settings=settings,
        execution_config=execution_config,
        resume_dir=str(tmp_path / "work"),
    )
    baseline = manager._compat_signature_config()

    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    assert mock_refine.called is expected_enabled
    assert baseline["auto_clean"] == str(expected_enabled).lower()

    step_dir = tmp_path / "step"
    step_dir.mkdir()
    record_calc_step_signature(
        str(step_dir),
        baseline,
        execution_config=manager.execution_config,
    )
    state = inspect_calc_step_state(
        str(step_dir),
        baseline,
        execution_config=manager.execution_config,
    )
    assert state.stored_signature == state.current_signature


def test_manager_missing_auto_clean_with_overlay_matches_workflow_hash():
    """When compat lacks auto_clean but overlay enables it, hash must match workflow."""
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config
    
    # Manager path: sparse settings (no auto_clean) + overlay with auto_clean=true
    sparse_settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        cleanup=CleanupOptions(enabled=True, rmsd_threshold=0.25),
        execution=ExecutionOptions(auto_clean=True),
    )
    manager = ChemTaskManager(settings=sparse_settings, execution_config=structured)
    manager_baseline = manager._compat_signature_config()
    manager_hash = compute_calc_config_signature(manager_baseline, execution_config=structured)
    
    # Workflow path: explicit auto_clean=true with cleanup params
    global_config = {}
    params = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": True,
        "clean_params": {"threshold": 0.25},
    }
    workflow_baseline = build_task_config(params, global_config)
    workflow_hash = compute_calc_config_signature(workflow_baseline)
    
    # Hashes should match because effective cleanup semantics are the same
    assert manager_hash == workflow_hash


def test_manager_missing_auto_clean_with_overlay_false_matches_workflow():
    """When compat lacks auto_clean and overlay disables it, hash must match workflow with auto_clean=false."""
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config
    
    # Manager path: sparse settings (no auto_clean) + overlay with auto_clean=false
    sparse_settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(auto_clean=False),
    )
    manager = ChemTaskManager(settings=sparse_settings, execution_config=structured)
    manager_baseline = manager._compat_signature_config()
    manager_hash = compute_calc_config_signature(manager_baseline, execution_config=structured)
    
    # Workflow path: explicit auto_clean=false
    global_config = {}
    params = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": False,
    }
    workflow_baseline = build_task_config(params, global_config)
    workflow_hash = compute_calc_config_signature(workflow_baseline)
    
    # Hashes should match because effective cleanup semantics are the same
    assert manager_hash == workflow_hash
    # Verify runtime cleanup is actually disabled
    assert manager_baseline["auto_clean"] == "false"


def test_manager_missing_auto_clean_overlay_false_with_clean_params_stays_disabled(tmp_path):
    """Sparse compat + explicit overlay false + cleanup params must stay disabled."""
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config

    sparse_settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        cleanup=CleanupOptions(enabled=True, rmsd_threshold=0.25, energy_window=8.0),
        execution=ExecutionOptions(auto_clean=False),
    )
    manager = ChemTaskManager(settings=sparse_settings, execution_config=structured)
    manager_baseline = manager._compat_signature_config()

    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")
    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    workflow_baseline = build_task_config(
        {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": False,
            "clean_params": {"threshold": 0.25, "energy_window": 8.0},
        },
        {},
    )

    assert mock_refine.called is False
    assert manager_baseline["auto_clean"] == "false"
    assert (
        compute_calc_config_signature(manager_baseline, execution_config=structured)
        == compute_calc_config_signature(workflow_baseline)
    )


def test_manager_missing_auto_clean_overlay_true_with_clean_params_stays_enabled(tmp_path):
    """Sparse compat + explicit overlay true + cleanup params must stay enabled."""
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config

    sparse_settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        cleanup=CleanupOptions(enabled=True, rmsd_threshold=0.25, energy_window=8.0),
        execution=ExecutionOptions(auto_clean=True),
    )
    manager = ChemTaskManager(settings=sparse_settings, execution_config=structured)
    manager_baseline = manager._compat_signature_config()

    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")
    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    workflow_baseline = build_task_config(
        {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": True,
            "clean_params": {"threshold": 0.25, "energy_window": 8.0},
        },
        {},
    )

    assert mock_refine.called is True
    assert manager_baseline["auto_clean"] == "true"
    assert (
        compute_calc_config_signature(manager_baseline, execution_config=structured)
        == compute_calc_config_signature(workflow_baseline)
    )


def test_manager_explicit_false_auto_clean_with_clean_params_stays_disabled(tmp_path):
    """Compat explicit false must win even if cleanup params exist."""
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config

    settings = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": "false",
        "clean_params": {"threshold": 0.25, "energy_window": 8.0},
    }
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        cleanup=CleanupOptions(enabled=True, rmsd_threshold=0.4, energy_window=10.0),
        execution=ExecutionOptions(auto_clean=True),
    )
    manager = ChemTaskManager(settings=settings, execution_config=structured)
    manager_baseline = manager._compat_signature_config()

    out_file = tmp_path / "result.xyz"
    out_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")
    with patch("confflow.calc.manager.run_refine_postprocess") as mock_refine:
        manager._run_auto_clean(str(out_file))

    workflow_baseline = build_task_config(
        {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": False,
            "clean_params": {"threshold": 0.25, "energy_window": 8.0},
        },
        {},
    )

    assert mock_refine.called is False
    assert manager_baseline["auto_clean"] == "false"
    assert (
        compute_calc_config_signature(manager_baseline, execution_config=structured)
        == compute_calc_config_signature(workflow_baseline)
    )


def test_manager_explicit_false_auto_clean_overrides_overlay_in_signature():
    """When compat explicitly sets auto_clean=false, overlay cannot override in signature."""
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config
    
    # Manager path: explicit auto_clean=false + overlay with auto_clean=true
    settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB", "auto_clean": "false"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        execution=ExecutionOptions(auto_clean=True),
    )
    manager = ChemTaskManager(settings=settings, execution_config=structured)
    manager_baseline = manager._compat_signature_config()
    manager_hash = compute_calc_config_signature(manager_baseline, execution_config=structured)
    
    # Workflow path: explicit auto_clean=false
    global_config = {}
    params = {
        "iprog": "orca",
        "itask": "sp",
        "keyword": "xTB",
        "auto_clean": False,
    }
    workflow_baseline = build_task_config(params, global_config)
    workflow_hash = compute_calc_config_signature(workflow_baseline)
    
    # Hashes should match because compat lane takes priority
    assert manager_hash == workflow_hash
    # Verify signature reflects compat priority
    assert manager_baseline["auto_clean"] == "false"


def test_step_contract_signature_from_sparse_manager_config_matches_workflow_baseline():
    from confflow.calc.step_contract import compute_calc_config_signature
    from confflow.workflow.task_config import build_task_config

    sparse_settings = {"iprog": "orca", "itask": "sp", "keyword": "xTB"}
    structured = CalcTaskConfig(
        program=Program.ORCA,
        task=TaskKind.SP,
        keyword="xTB",
        cleanup=CleanupOptions(enabled=True, rmsd_threshold=0.25, energy_window=8.0),
        execution=ExecutionOptions(auto_clean=True),
    )
    workflow_baseline = build_task_config(
        {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "xTB",
            "auto_clean": True,
            "clean_params": {"threshold": 0.25, "energy_window": 8.0},
        },
        {},
    )

    assert compute_calc_config_signature(
        sparse_settings,
        execution_config=structured,
    ) == compute_calc_config_signature(workflow_baseline)
