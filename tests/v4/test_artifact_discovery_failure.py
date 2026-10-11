"""Successful calculations must fail closed when artifact publication fails."""

import pytest

from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import WorkItemExecutor
from tests.v4._builders import structure_set
from tests.v4._helpers.v42_executors import (
    FAKE_ORCA,
    ORCA_NATIVE,
    assemble_items,
    calculation_doc,
    compile_plan,
    item_context,
)


def test_successful_native_run_does_not_hide_artifact_discovery_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_MODE", "success_opt")
    plan = compile_plan(calculation_doc("orca", ORCA_NATIVE, str(FAKE_ORCA)))
    item = assemble_items(plan, structure_set("s0"))[0]
    context = item_context(
        plan,
        "orca",
        str(FAKE_ORCA),
        str(tmp_path),
        str(tmp_path / "items"),
        NativeProcessSupervisor(),
        ["normal_termination"],
    )

    def fail_discovery(self, **kwargs):
        raise RuntimeError("artifact discovery failed")

    monkeypatch.setattr(type(context.adapter), "discover_artifacts", fail_discovery)
    with pytest.raises(RuntimeError, match="artifact discovery failed"):
        WorkItemExecutor().execute(item, context)
