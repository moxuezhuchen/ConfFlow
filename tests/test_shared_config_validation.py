"""Tests for shared YAML config validation helpers."""

from __future__ import annotations

from confflow.shared.config_validation import validate_step_config, validate_yaml_config


def test_validate_yaml_config_accepts_minimal_valid_config() -> None:
    errors = validate_yaml_config(
        {
            "global": {"cores_per_task": "2", "max_parallel_jobs": 1},
            "steps": [
                {
                    "name": "gen",
                    "type": "confgen",
                    "params": {"chains": ["1-2-3-4"], "angle_step": 120},
                },
                {
                    "name": "sp",
                    "type": "calc",
                    "params": {"iprog": "orca", "itask": "sp", "keyword": "HF STO-3G"},
                },
            ],
        }
    )

    assert errors == []


def test_validate_yaml_config_reports_root_and_global_errors(tmp_path) -> None:
    missing = tmp_path / "missing-orca"
    errors = validate_yaml_config(
        {
            "global": {
                "gaussian_path": str(missing),
                "orca_path": "orca",
                "cores_per_task": 0,
                "max_parallel_jobs": "many",
            },
            "steps": "not-a-list",
        }
    )

    assert f"Gaussian path not found: {missing}" in errors
    assert "invalid cores_per_task: 0" in errors
    assert "invalid max_parallel_jobs: many" in errors
    assert "'steps' must be a list" in errors


def test_validate_yaml_config_reports_required_sections_and_type_errors() -> None:
    assert validate_yaml_config({}, required_sections=["global"]) == [
        "missing required section: 'global'"
    ]
    assert validate_yaml_config({"global": [], "steps": [None]}) == [
        "'global' must be a dict",
        "step 1 must be a dict",
    ]


def test_validate_step_config_reports_missing_and_invalid_step_fields() -> None:
    errors = validate_step_config({"params": []}, 0)

    assert "step 1: missing 'name' field" in errors
    assert "step 1: missing 'type' field" in errors
    assert "step 1: 'params' must be a dict" in errors


def test_validate_step_config_reports_calc_errors() -> None:
    errors = validate_step_config(
        {
            "name": "bad_calc",
            "type": "calc",
            "params": {"iprog": "orca", "itask": "bad"},
        },
        1,
    )

    assert "step 'bad_calc': invalid itask value 'bad'" in errors
    assert "step 'bad_calc': ORCA task missing 'keyword' parameter" in errors

    iprog_errors = validate_step_config(
        {
            "name": "bad_prog",
            "type": "task",
            "params": {"iprog": "xtb", "itask": 4, "keyword": "opt"},
        },
        2,
    )
    assert "step 'bad_prog': invalid iprog value 'xtb'" in iprog_errors


def test_validate_step_config_reports_confgen_errors() -> None:
    errors = validate_step_config(
        {
            "name": "bad_gen",
            "type": "gen",
            "params": {
                "add_bond": "1",
                "del_bond": [1, "2"],
                "no_rotate": [[1, 2], [3]],
                "force_rotate": ["1 2", "3"],
                "angle_step": -10,
            },
        },
        3,
    )

    assert any("confgen step requires 'chains' (or 'chain')" in error for error in errors)
    assert any("add_bond" in error for error in errors)
    assert any("del_bond" in error for error in errors)
    assert any("no_rotate" in error for error in errors)
    assert any("force_rotate" in error for error in errors)
    assert "step 'bad_gen': invalid angle_step value '-10'" in errors


def test_validate_step_config_accepts_pair_list_variants() -> None:
    for value in ("1 2", [1, 2], [[1, 2], [3, 4]], ["1 2", "3-4"], []):
        errors = validate_step_config(
            {
                "name": "gen",
                "type": "confgen",
                "params": {"chains": ["1-2"], "add_bond": value},
            },
            0,
        )
        assert errors == []


def test_validate_step_config_reports_invalid_type() -> None:
    assert validate_step_config({"name": "x", "type": "viz", "params": {}}, 0) == [
        "step 'x': invalid type 'viz', must be 'confgen', 'calc', 'gen' or 'task'"
    ]
