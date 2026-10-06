#!/usr/bin/env python3

"""PR-6 dev-fixture surface guardrails (DIET-2 R1.1: fixture lives in tests/).

DIET-2 R1.1 moved the dev-fixture helpers out of the production package
into ``tests.support`` and retired the ``confflow-fixture-agent`` console
entry.  This file locks the new surface:

- importing ``confflow.application.execution`` never loads the fixture
  modules and no longer resolves their names;
- the ``confflow-fixture-agent`` console entry is gone from ``pyproject``
  and the fixture modules are gone from the production tree;
- the same helpers stay importable from ``tests.support``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Fixture names that must resolve from tests.support after the R1.1 move.
SUPPORT_FIXTURE_EXPORTS: Final[tuple[str, ...]] = (
    "InMemoryExecutionRepository",
    "SyntheticProducerExecutor",
    "open_synthetic_service",
    "synthetic_agent_entry",
    "SYNTHETIC_ARTIFACT",
    "SYNTHETIC_ARTIFACT_CONTENT",
    "SYNTHETIC_ARTIFACT_PATH",
    "SYNTHETIC_ARTIFACT_SCHEMA",
    "SYNTHETIC_ARTIFACT_TERMINAL",
    "SYNTHETIC_CHECKPOINT_ID",
)


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class TestDevFixtureSurface:
    """Dev-fixture helpers live in tests.support, not in production."""

    def test_production_lazy_map_no_longer_resolves_the_fixture(self) -> None:
        script = (
            "import sys\n"
            "import confflow.application.execution as execution\n"
            "banned = ('confflow.application.execution.synthetic_producer', "
            "'confflow.application.execution.memory', "
            "'confflow.fixture_agent');\n"
            "loaded = [m for m in banned if m in sys.modules];\n"
            "assert not loaded, loaded\n"
            "removed = ('InMemoryExecutionRepository', "
            "'SyntheticProducerExecutor', 'open_synthetic_service', "
            "'synthetic_agent_entry', 'SYNTHETIC_ARTIFACT', "
            "'SYNTHETIC_ARTIFACT_CONTENT', 'SYNTHETIC_ARTIFACT_PATH', "
            "'SYNTHETIC_ARTIFACT_SCHEMA', 'SYNTHETIC_ARTIFACT_TERMINAL', "
            "'SYNTHETIC_CHECKPOINT_ID');\n"
            "missing = [n for n in removed if n in execution.__all__];\n"
            "assert not missing, missing\n"
            "import pytest\n"
            "from confflow.application.execution import ExecutionService, RunState\n"
            "assert callable(ExecutionService)\n"
            "assert RunState.__name__ == 'RunState'\n"
        )
        result = _run(script)
        assert result.returncode == 0, result.stderr

    def test_fixture_agent_console_entry_is_retired(self) -> None:
        # The entry point and the production-tree modules are deleted.  (A bare
        # ``import confflow.fixture_agent`` is not asserted here: this checkout
        # shares its interpreter with an editable confflow install from another
        # tree, whose meta-path finder would resolve the stale module.  File
        # absence in THIS tree is the meaningful gate.)
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "confflow-fixture-agent" not in pyproject
        assert not (REPO_ROOT / "confflow" / "fixture_agent.py").exists()
        assert not (
            REPO_ROOT / "confflow" / "application" / "execution" / "synthetic_producer.py"
        ).exists()
        assert not (REPO_ROOT / "confflow" / "application" / "execution" / "memory.py").exists()

    def test_fixture_helpers_stay_importable_from_tests_support(self) -> None:
        script = (
            "from tests.support.memory import InMemoryExecutionRepository\n"
            "from tests.support.synthetic_producer import (\n"
            + "".join(
                f"    {name},\n"
                for name in SUPPORT_FIXTURE_EXPORTS
                if name != "InMemoryExecutionRepository"
            )
            + ")\n"
            "from tests.support import fixture_agent\n"
            "assert callable(SyntheticProducerExecutor)\n"
            "assert callable(open_synthetic_service)\n"
            "assert callable(synthetic_agent_entry)\n"
            "assert callable(InMemoryExecutionRepository)\n"
            "assert callable(fixture_agent.main)\n"
            "assert isinstance(SYNTHETIC_ARTIFACT_CONTENT, str)\n"
        )
        result = _run(script)
        assert result.returncode == 0, result.stderr
