#!/usr/bin/env python3

"""Dead/drifted config options: fingerprint neutrality and deprecation signals."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from confflow.config.canonical import resolve_calc_step
from confflow.config.canonical.fingerprint import workflow_fingerprint
from confflow.config.canonical.types import GlobalOptions
from confflow.core.exceptions import ConfigurationError
from confflow.shared.confgen_params import resolve_confgen_params


def _calc_fingerprint(extra_params: dict) -> str:
    params = {"keyword": "HF", "iprog": "orca", "itask": "ts", **extra_params}
    plan = SimpleNamespace(
        typed_global=GlobalOptions.from_mapping({}),
        steps=[{"name": "c", "type": "calc", "params": params}],
        predecessors={},
        execution_order=["c"],
        terminal_steps=["c"],
        step_dirnames=["step_01_c"],
    )
    return workflow_fingerprint(plan)


def _ts_step() -> dict:
    return {
        "keyword": "HF",
        "iprog": "orca",
        "itask": "ts",
        "ts_bond_atoms": "1,2",
    }


def test_scan_defaults_are_fingerprint_neutral():
    omitted = _calc_fingerprint(_ts_step())
    explicit = _calc_fingerprint({**_ts_step(), "scan_max_steps": 60, "scan_fine_half_window": 0.1})
    assert omitted == explicit


def test_scan_option_changes_do_change_fingerprint():
    assert _calc_fingerprint(_ts_step()) != _calc_fingerprint({**_ts_step(), "scan_max_steps": 120})


def test_scan_options_are_consumed_from_resolved_config():
    resolved = resolve_calc_step(
        {**_ts_step(), "scan_max_steps": 120, "scan_fine_half_window": 0.2},
        GlobalOptions.from_mapping({}),
    )
    canonical = resolved.canonical_dict()
    assert canonical["scan_max_steps"] == 120
    assert canonical["scan_fine_half_window"] == 0.2


def test_resume_from_backups_does_not_change_fingerprint():
    def fingerprint(value):
        plan = SimpleNamespace(
            typed_global=GlobalOptions.from_mapping({"resume_from_backups": value}),
            steps=[{"name": "gen", "type": "confgen", "params": {}}],
            predecessors={},
            execution_order=["gen"],
            terminal_steps=["gen"],
            step_dirnames=["step_01_gen"],
        )
        return workflow_fingerprint(plan)

    assert fingerprint(True) == fingerprint(False)


def test_resume_from_backups_warns_when_set(caplog):
    with caplog.at_level(logging.WARNING, logger="confflow.config"):
        GlobalOptions.from_mapping({"resume_from_backups": True})

    assert any("resume_from_backups is deprecated" in r.message for r in caplog.records)


def test_ts_rescue_scan_backup_is_not_in_canonical_dict():
    resolved = resolve_calc_step(
        {**_ts_step(), "ts_rescue_scan_backup": True}, GlobalOptions.from_mapping({})
    )
    assert "ts_rescue_scan_backup" not in resolved.canonical_dict()


def test_ts_rescue_scan_backup_warns_when_set(caplog):
    with caplog.at_level(logging.WARNING, logger="confflow.config"):
        resolve_calc_step(
            {**_ts_step(), "ts_rescue_scan_backup": True}, GlobalOptions.from_mapping({})
        )

    assert any("ts_rescue_scan_backup is deprecated" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# ConfGen boolean normalization
# ---------------------------------------------------------------------------


def test_optimize_bool_normalization():
    truthy = [True, 1, "true", "1"]
    falsy = [False, 0, "false", "0"]
    for value in truthy:
        assert resolve_confgen_params({"optimize": value}, default_workers=1)["optimize"] is True
    for value in falsy:
        assert resolve_confgen_params({"optimize": value}, default_workers=1)["optimize"] is False


def test_optimize_invalid_value_rejected():
    with pytest.raises(ConfigurationError):
        resolve_confgen_params({"optimize": "maybe"}, default_workers=1)


def test_optimize_fingerprint_matches_execution():
    def fingerprint(value):
        plan = SimpleNamespace(
            typed_global=GlobalOptions.from_mapping({}),
            steps=[{"name": "gen", "type": "confgen", "params": {"optimize": value}}],
            predecessors={},
            execution_order=["gen"],
            terminal_steps=["gen"],
            step_dirnames=["step_01_gen"],
        )
        return workflow_fingerprint(plan)

    # A YAML string "false" must fingerprint identically to a real false.
    assert fingerprint("false") == fingerprint(False)
    assert fingerprint("false") != fingerprint(True)
