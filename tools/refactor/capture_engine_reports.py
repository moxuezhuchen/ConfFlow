"""pytest plugin: snapshot every ``ConfgenEngine.run`` call made by a test.

Environment: ``CAP_OUT`` is the output directory.  Load with
``PYTHONPATH=<tools dir> pytest -p capture_engine_reports``.

Optional: ``CAP_SCOPE_GLOB`` restricts the *write-out* to tests whose file
matches the glob (e.g. ``tests/v4/test_confgen_*.py``).  Filtered engine runs
are still executed and returned; only the report writing is skipped.  When
unset, every run is written (the original behavior).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from ts1_engine import summarize_run

_state: dict[str, Any] = {"nodeid": "no-test", "seq": 0, "orig": None}


def _clean(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid).strip("_")


def _out_dir() -> Path:
    out = os.environ.get("CAP_OUT")
    if not out:
        raise RuntimeError("CAP_OUT must name the output directory")
    path = Path(out)
    path.mkdir(parents=True, exist_ok=True)
    return path


def pytest_configure(config: pytest.Config) -> None:
    from confflow.science.confgen.engine import ConfgenEngine

    original = ConfgenEngine.run
    _state["orig"] = original
    _state["scope"] = os.environ.get("CAP_SCOPE_GLOB", "")

    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        run = original(self, *args, **kwargs)
        scope = _state.get("scope", "")
        if scope and not fnmatch.fnmatch(_state["nodeid"].split("::")[0], scope):
            return run
        summary = summarize_run(run)
        payload = {
            "report": run.report_json(),
            "targets": summary["targets"],
            "leaves": summary["leaves"],
        }
        name = f"{_clean(_state['nodeid'])}__{_state['seq']}.json"
        _state["seq"] += 1
        (_out_dir() / name).write_text(json.dumps(payload, sort_keys=True, indent=1) + "\n")
        return run

    ConfgenEngine.run = wrapped  # type: ignore[method-assign]


def pytest_unconfigure(config: pytest.Config) -> None:
    if _state["orig"] is not None:
        from confflow.science.confgen.engine import ConfgenEngine

        ConfgenEngine.run = _state["orig"]  # type: ignore[method-assign]


def pytest_runtest_setup(item: pytest.Item) -> None:
    _state["nodeid"] = item.nodeid
    _state["seq"] = 0
