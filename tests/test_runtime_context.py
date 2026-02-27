#!/usr/bin/env python3

"""Contract tests for workflow.runtime_context."""

from __future__ import annotations

from confflow.workflow.runtime_context import initialize_runtime_context


def test_initialize_runtime_context_creates_structure(tmp_path):
    config_file = tmp_path / "conf.yaml"
    config_file.write_text("global: {}\nsteps: []\n", encoding="utf-8")

    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")

    class _Logger:
        def __init__(self):
            self.handlers = []
            self.warnings = []

        def add_file_handler(self, path):
            self.handlers.append(path)

        def warning(self, msg):
            self.warnings.append(msg)

    logger = _Logger()

    runtime = initialize_runtime_context(
        work_dir=str(tmp_path / "work"),
        config_file=str(config_file),
        input_files=[str(input_xyz)],
        original_inputs=[str(input_xyz)],
        resume=False,
        logger=logger,
    )

    assert runtime.root_dir.endswith("work")
    assert runtime.failed_dir.endswith("failed")
    assert runtime.resume_from_step == -1
    assert runtime.current_input == str(input_xyz)
    assert (tmp_path / "work" / "failed" / "conf.yaml").exists()
    assert logger.handlers and logger.handlers[0].endswith("confflow.log")


def test_initialize_runtime_context_resume_loads_checkpoint(tmp_path):
    config_file = tmp_path / "conf.yaml"
    config_file.write_text("global: {}\nsteps: []\n", encoding="utf-8")

    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")

    class _Logger:
        def add_file_handler(self, path):
            return None

        def warning(self, msg):
            return None

    logger = _Logger()

    runtime_first = initialize_runtime_context(
        work_dir=str(tmp_path / "work"),
        config_file=str(config_file),
        input_files=[str(input_xyz)],
        original_inputs=[str(input_xyz)],
        resume=False,
        logger=logger,
    )
    runtime_first.checkpoint.save(2, {"steps": []})

    runtime_second = initialize_runtime_context(
        work_dir=str(tmp_path / "work"),
        config_file=str(config_file),
        input_files=[str(input_xyz)],
        original_inputs=[str(input_xyz)],
        resume=True,
        logger=logger,
    )
    assert runtime_second.resume_from_step == 2
