#!/usr/bin/env python3

"""PR-6 dev-fixture surface guardrails.

The retired remote helpers (``confflow.remote.lease`` / ``supervision`` /
``schema``) and the V4-root/dev-fixture import boundary are locked by
``tests/v4/test_architecture_boundaries.py`` (``RETIRED_RUNTIME_MODULES``,
``TestRemoteBoundary``, ``TestRuntimeIsolation``).  This file keeps only the
fixture-tool contracts that have no home in the V4 boundary gate:

- the fixture-only execution helpers stay importable through the public
  lazy map;
- the ``confflow-fixture-agent`` console entry keeps working and stays lazy
  about the synthetic producer stack.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Public fixture names that must stay importable from the lazy package map.
PUBLIC_FIXTURE_EXPORTS: Final[tuple[str, ...]] = (
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
    """Fixture-only helpers keep working through their public surface."""

    def test_public_lazy_exports_still_importable(self) -> None:
        script = (
            "from confflow.application.execution import (\n"
            + "".join(f"    {name},\n" for name in PUBLIC_FIXTURE_EXPORTS)
            + ")\n"
            "assert callable(SyntheticProducerExecutor)\n"
            "assert callable(open_synthetic_service)\n"
            "assert callable(synthetic_agent_entry)\n"
            "assert callable(InMemoryExecutionRepository)\n"
            "assert isinstance(SYNTHETIC_ARTIFACT_CONTENT, str)\n"
        )
        result = _run(script)
        assert result.returncode == 0, result.stderr

    def test_fixture_agent_console_entry_remains(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'confflow-fixture-agent = "confflow.fixture_agent:main"' in pyproject
        script = (
            "import confflow.fixture_agent as fixture_agent; " "assert callable(fixture_agent.main)"
        )
        result = _run(script)
        assert result.returncode == 0, result.stderr

    def test_fixture_agent_import_does_not_pull_the_synthetic_stack(self) -> None:
        script = (
            "import sys; import confflow.fixture_agent as fixture_agent; "
            "assert callable(fixture_agent.main); "
            "banned = ('confflow.application.execution.synthetic_producer', "
            "'confflow.application.execution.memory'); "
            "loaded = [m for m in banned if m in sys.modules]; "
            "assert not loaded, loaded"
        )
        result = _run(script)
        assert result.returncode == 0, result.stderr
