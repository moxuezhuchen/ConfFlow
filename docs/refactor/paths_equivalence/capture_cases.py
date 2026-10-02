#!/usr/bin/env python3
"""pytest plugin: capture every legacy-paths execution as an equivalence case.

Run from the exec-cf-is repo root with ``PYTHONPATH=docs/refactor/paths_equivalence``
and ``-p capture_cases`` added to the pytest invocation over
``tests/v4/test_confgen_paths_phase0.py`` and ``tests/v4/test_confgen_paths_audit.py``.

Wraps ``ConfgenExecutor._run_legacy_paths`` and records each call's driving
record (id, atoms, coordinates) and native mapping.  Calls are deduplicated by
their canonical JSON form, sorted by that key, given ``case_XXXX`` ids, and
written to ``cases.json`` next to this file together with three handwritten
``extra_*`` cases (terminal endpoint and bare declarations, PLAN Q2/Q2b).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent

#: Handwritten supplements (PLAN IS.1 step 1b / handoff step 2).  The butane
#: record mirrors ``tests.v4.test_repair_executors._butane``.
_BUTANE = {
    "id": "butane",
    "atoms": ["C", "C", "C", "C"],
    "coordinates": [
        [0.0, 0.0, 0.0],
        [1.5, 0.0, 0.0],
        [3.0, 0.4, 0.0],
        [4.5, 0.4, 0.0],
    ],
}

EXTRA_CASES: list[dict[str, Any]] = [
    {
        "case_id": "extra_terminal_endpoint",
        "record": _BUTANE,
        "native": {"paths": [{"start": 1, "end": 3, "move": "end", "angles": [0.0, 120.0, 240.0]}]},
    },
    {
        "case_id": "extra_bare_path",
        "record": _BUTANE,
        "native": {"paths": [{"start": 1, "end": 4, "move": "end"}]},
    },
    {
        "case_id": "extra_bare_path_angle_step",
        "record": _BUTANE,
        "native": {"paths": [{"start": 1, "end": 4, "move": "end"}], "angle_step": 60},
    },
]

_captured: list[dict[str, Any]] = []


def _plain(value: Any) -> Any:
    """Convert FrozenDict/Mapping/tuple structures into plain JSON data."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def pytest_configure(config: pytest.Config) -> None:
    from confflow.execution.confgen_executor import ConfgenExecutor

    original = ConfgenExecutor._run_legacy_paths
    pytest._capture_original = original  # type: ignore[attr-defined]

    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        driving = kwargs["driving"]
        native = kwargs["native"]
        _captured.append(
            {
                "record": {
                    "id": driving.id,
                    "atoms": list(driving.atoms),
                    "coordinates": [[float(v) for v in point] for point in driving.coordinates],
                },
                "native": _plain(native),
            }
        )
        return original(self, *args, **kwargs)

    ConfgenExecutor._run_legacy_paths = wrapped  # type: ignore[method-assign]


def pytest_unconfigure(config: pytest.Config) -> None:
    from confflow.execution.confgen_executor import ConfgenExecutor

    ConfgenExecutor._run_legacy_paths = pytest._capture_original  # type: ignore[attr-defined]

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for entry in _captured:
        key = json.dumps(entry, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    unique.sort(key=lambda entry: json.dumps(entry, sort_keys=True))
    cases = [{"case_id": f"case_{index:04d}", **entry} for index, entry in enumerate(unique)]
    cases.extend(EXTRA_CASES)
    (_HERE / "cases.json").write_text(
        json.dumps({"cases": cases}, sort_keys=True, indent=1) + "\n", encoding="utf-8"
    )
