"""AST fitness checks for the stable core, shared, calc, and block seams."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "confflow"


def _imported_modules(path: Path) -> list[str]:
    """Return normalized import module names from one Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            modules.append(f"{prefix}{node.module or ''}")
    return modules


def _package_modules(package: str) -> list[tuple[Path, str]]:
    """Collect imports with their source path for a package subtree."""
    return [
        (path, module)
        for path in (PACKAGE_ROOT / package).rglob("*.py")
        for module in _imported_modules(path)
    ]


def test_control_worker_staging_is_extracted_behind_compatibility_wrappers() -> None:
    """Keep secure staging implementation out of the process orchestrator."""
    control_path = PACKAGE_ROOT / "control_worker.py"
    staging_path = PACKAGE_ROOT / "worker_staging.py"
    control_tree = ast.parse(control_path.read_text(encoding="utf-8"), filename=str(control_path))
    staging_tree = ast.parse(staging_path.read_text(encoding="utf-8"), filename=str(staging_path))
    control_defs = {
        node.name
        for node in control_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    staging_defs = {
        node.name
        for node in staging_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"_stage_worker_inputs", "_stage_file", "_ensure_directory"} <= control_defs
    assert {"_stage_worker_inputs", "_stage_file", "_ensure_directory"} <= staging_defs
    for name in ("_stage_worker_inputs", "_stage_file", "_ensure_directory"):
        node = next(node for node in control_tree.body if getattr(node, "name", None) == name)
        assert "_worker_staging" in ast.unparse(node)
    control_source = control_path.read_text(encoding="utf-8")
    assert "os.open(" not in control_source
    assert "hashlib.sha256(" not in control_source
