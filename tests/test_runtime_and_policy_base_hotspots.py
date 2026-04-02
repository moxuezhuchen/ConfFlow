#!/usr/bin/env python3

"""Hotspot tests for runtime context initialization and policy base class."""

from __future__ import annotations

from unittest.mock import patch

from confflow.calc.policies.base import CalculationPolicy
from confflow.workflow.runtime_context import initialize_runtime_context


class _ConcretePolicy(CalculationPolicy):
    @property
    def name(self) -> str:
        return CalculationPolicy.name.fget(self)

    @property
    def input_ext(self) -> str:
        return CalculationPolicy.input_ext.fget(self)

    @property
    def log_ext(self) -> str:
        return CalculationPolicy.log_ext.fget(self)

    def generate_input(self, task_info, inp_file_path):
        return CalculationPolicy.generate_input(self, task_info, inp_file_path)

    def parse_output(self, log_file, config, is_sp_task=False):
        return CalculationPolicy.parse_output(self, log_file, config, is_sp_task)

    def get_execution_command(self, config, inp_file):
        return CalculationPolicy.get_execution_command(self, config, inp_file)

    def check_termination(self, log_file):
        return CalculationPolicy.check_termination(self, log_file)

    def get_error_details(self, work_dir, job_name, config):
        return CalculationPolicy.get_error_details(self, work_dir, job_name, config)

    def cleanup_lingering_processes(self, config):
        return CalculationPolicy.cleanup_lingering_processes(self, config)


def test_calculation_policy_base_default_and_abstract_stubs_are_callable():
    policy = _ConcretePolicy()

    with patch.dict("confflow.calc.policies.base.os.environ", {"X": "1"}, clear=True):
        env = policy.get_environment({}, ["cmd"])

    assert policy.name is None
    assert policy.input_ext is None
    assert policy.log_ext is None
    assert policy.generate_input({}, "in.gjf") is None
    assert policy.parse_output("out.log", {}) is None
    assert policy.get_execution_command({}, "in.gjf") is None
    assert policy.check_termination("out.log") is None
    assert policy.get_error_details("/tmp", "job", {}) is None
    assert policy.cleanup_lingering_processes({}) is None
    assert env == {"X": "1"}
    assert env is not CalculationPolicy.get_environment(policy, {}, ["cmd"])


def test_initialize_runtime_context_logs_copy_failure_via_debug_and_keeps_multiple_inputs(tmp_path):
    config_file = tmp_path / "conf.yaml"
    config_file.write_text("global: {}\nsteps: []\n", encoding="utf-8")

    inputs = []
    for idx in range(2):
        xyz = tmp_path / f"in{idx}.xyz"
        xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")
        inputs.append(str(xyz))

    class _Logger:
        def __init__(self):
            self.debugs = []

        def debug(self, msg, *args):
            self.debugs.append(msg % args if args else msg)

    logger = _Logger()

    with patch(
        "confflow.workflow.runtime_context.shutil.copy2", side_effect=OSError("copy failed")
    ):
        runtime = initialize_runtime_context(
            work_dir=str(tmp_path / "work"),
            config_file=str(config_file),
            input_files=inputs,
            original_inputs=inputs,
            resume=False,
            logger=logger,
        )

    assert runtime.current_input == inputs
    assert logger.debugs and "Failed to copy the config file" in logger.debugs[0]
    assert (tmp_path / "work" / "failed").exists()


def test_initialize_runtime_context_logs_copy_failure_via_warning_when_debug_missing(tmp_path):
    config_file = tmp_path / "conf.yaml"
    config_file.write_text("global: {}\nsteps: []\n", encoding="utf-8")

    xyz = tmp_path / "in.xyz"
    xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")

    class _Logger:
        def __init__(self):
            self.warnings = []

        def warning(self, msg):
            self.warnings.append(msg)

    logger = _Logger()

    with patch(
        "confflow.workflow.runtime_context.shutil.copy2", side_effect=OSError("copy failed")
    ):
        runtime = initialize_runtime_context(
            work_dir=str(tmp_path / "work"),
            config_file=str(config_file),
            input_files=[str(xyz)],
            original_inputs=[str(xyz)],
            resume=False,
            logger=logger,
        )

    assert runtime.current_input == str(xyz)
    assert logger.warnings and "Failed to copy the config file" in logger.warnings[0]
