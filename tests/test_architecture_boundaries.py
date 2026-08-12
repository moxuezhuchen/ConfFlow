"""Phase F dependency-direction fitness tests for ConfFlow."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parents[1] / "confflow"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            values.add(node.module or "")
    return values


def test_phase_f_layer_violations_are_explicitly_allowlisted() -> None:
    core_imports = {
        f"{path.relative_to(_ROOT).as_posix()}:{line}"
        for path in (_ROOT / "core").rglob("*.py")
        for line, text in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1)
        if "blocks" in text and ("import" in text or "from" in text)
    }
    calc_result_imports = {
        f"{path.relative_to(_ROOT).as_posix()}:{line}"
        for path in (_ROOT / "calc").rglob("*.py")
        for line, text in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1)
        if "blocks.refine.result" in text
    }
    config_core_imports = {
        f"{path.relative_to(_ROOT).as_posix()}:{line}"
        for path in (_ROOT / "config").rglob("*.py")
        for line, text in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1)
        if "core.models" in text
    }

    assert core_imports == set()
    assert calc_result_imports == set()
    assert config_core_imports == set()

    # Keep this helper exercised so a future edit cannot accidentally turn the
    # test into a text-only scan for all other import-direction checks.
    assert "blocks" not in _imports(_ROOT / "config" / "__init__.py")


def test_importing_config_does_not_load_core_models() -> None:
    """The typed config package must not trigger the legacy Pydantic models."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import confflow.config; "
            "assert 'confflow.core.models' not in sys.modules",
        ],
        cwd=_ROOT.parent,
        check=True,
    )


def test_public_workflow_entry_is_a_thin_runtime_facade() -> None:
    """The public entry point must not own runtime orchestration details."""
    engine_path = _ROOT / "workflow" / "engine.py"
    tree = ast.parse(engine_path.read_text(encoding="utf-8-sig"), filename=str(engine_path))
    run_workflow = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_workflow"
    )
    forbidden_names = {
        "validate_inputs_compatible",
        "WorkflowStateStore",
        "ResumePolicy",
        "WorkflowExecutor",
        "initialize_runtime_context",
        "finalize_workflow",
        "create_initial_workflow_state",
    }
    called_names = {
        node.func.id
        for node in ast.walk(run_workflow)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint(forbidden_names)
    assert "_WorkflowOrchestrator" in called_names
