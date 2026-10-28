#!/usr/bin/env python3

"""Architecture review R3: MLIP resolves charge/multiplicity per structure like calculations.

Each structure goes through ``resolve_scientific_parameters``; the runner receives one
(charge, multiplicity) per job, so a heterogeneous batch fails, and output StructureRecords
carry the effective values that were actually computed.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from confflow.domain.completion import WorkItemStatus
from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.domain.structure import StructureRecord
from confflow.execution.mlip_executor import MlipExecutor
from tests.v4.test_mlip_executor import (
    _ctx,
    _install_runner,
    _item,
    _native,
    _records,
)

# Water (10 electrons): cation (1, 2) and neutral singlet (0, 1) are both consistent.
CATION = (1, 2)


def _with_science(records: list[StructureRecord], **fields: Any) -> list[StructureRecord]:
    return [dataclasses.replace(record, **fields) for record in records]


def _set_scientific(
    ctx: Any, *, overrides: dict[str, Any] | None = None, defaults: Any = (0, 1)
) -> Any:
    """Return *ctx* with explicit step overrides and run-level defaults (charge, multiplicity)."""
    if defaults is None:
        defaults_ns = SimpleNamespace(charge=None, multiplicity=None)
    else:
        defaults_ns = SimpleNamespace(charge=defaults[0], multiplicity=defaults[1])
    return dataclasses.replace(
        ctx,
        scientific=SimpleNamespace(native=ctx.scientific.native, overrides=overrides or {}),
        scientific_defaults=defaults_ns,
    )


def _execute(ctx: Any, records: list[StructureRecord]) -> Any:
    return MlipExecutor().execute(_item(records), ctx)


def _job_charge(ctx: Any, records: list[StructureRecord]) -> tuple[int, int]:
    path = Path(ctx.attempt_dir(_item(records))) / "job.json"
    job = json.loads(path.read_text(encoding="utf-8"))
    return job["charge"], job["multiplicity"]


def _labels(result: Any) -> set[tuple[int | None, int | None]]:
    return {(record.charge, record.multiplicity) for record in result.structures}


def test_charged_structure_drives_job_and_output_labels(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    records = _with_science(_records(), charge=CATION[0], multiplicity=CATION[1])
    ctx = _set_scientific(_ctx(tmp_path, _native(mode="sp")), defaults=(0, 1))
    result = _execute(ctx, records)
    assert result.status is WorkItemStatus.COMPLETED
    assert _job_charge(ctx, records) == CATION
    assert _labels(result) == {CATION}


def test_missing_structure_values_use_run_defaults(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    records = _with_science(_records(), charge=None, multiplicity=None)
    ctx = _set_scientific(_ctx(tmp_path, _native(mode="sp")), defaults=(0, 1))
    result = _execute(ctx, records)
    assert result.status is WorkItemStatus.COMPLETED
    assert _job_charge(ctx, records) == (0, 1)
    assert _labels(result) == {(0, 1)}


def test_nothing_declared_fails_without_zero_one_fallback(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    records = _with_science(_records(), charge=None, multiplicity=None)
    ctx = _set_scientific(_ctx(tmp_path, _native(mode="sp")), defaults=None)
    result = _execute(ctx, records)
    assert result.status is WorkItemStatus.FAILED
    assert "charge and multiplicity" in result.diagnostics[0].message
    assert not (Path(ctx.attempt_dir(_item(records))) / "job.json").exists()


def test_step_override_beats_structure_value_with_warning(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    records = _with_science(_records(), charge=CATION[0], multiplicity=CATION[1])
    ctx = _set_scientific(
        _ctx(tmp_path, _native(mode="sp")),
        overrides={"charge": 0, "multiplicity": 1},
        defaults=(0, 1),
    )
    result = _execute(ctx, records)
    assert result.status is WorkItemStatus.COMPLETED
    assert _job_charge(ctx, records) == (0, 1)
    assert _labels(result) == {(0, 1)}
    conflicts = [
        d
        for d in result.diagnostics
        if d.code == "scientific_parameter_conflict" and d.severity is DiagnosticSeverity.WARNING
    ]
    assert conflicts, [d.code for d in result.diagnostics]
    assert conflicts[0].step_id == "uma"


def test_heterogeneous_batch_fails_clearly(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    base = _records()
    records = [
        base[0],
        dataclasses.replace(base[1], charge=CATION[0], multiplicity=CATION[1]),
        base[2],
    ]
    ctx = _set_scientific(_ctx(tmp_path, _native(mode="sp")), defaults=(0, 1))
    result = _execute(ctx, records)
    assert result.status is WorkItemStatus.FAILED
    message = result.diagnostics[0].message
    assert "mixes (charge, multiplicity) pairs" in message
    assert "one pair per job" in message


def test_parity_error_fails_before_the_runner(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    # Neutral water has an even electron count, so a doublet is impossible.
    records = _with_science(_records(), charge=0, multiplicity=2)
    ctx = _set_scientific(_ctx(tmp_path, _native(mode="sp")), defaults=(0, 1))
    result = _execute(ctx, records)
    assert result.status is WorkItemStatus.FAILED
    assert "multiplicity parity" in result.diagnostics[0].message
    assert not (Path(ctx.attempt_dir(_item(records))) / "job.json").exists()


def test_run_defaults_apply_to_relaxed_outputs_too(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    records = _with_science(_records(), charge=None, multiplicity=None)
    ctx = _set_scientific(_ctx(tmp_path, _native(mode="relax")), defaults=(0, 1))
    result = _execute(ctx, records)
    assert result.status is WorkItemStatus.COMPLETED
    assert _labels(result) == {(0, 1)}
    assert all(record.id.endswith(":mlip-relax") for record in result.structures)
