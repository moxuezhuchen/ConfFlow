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


def test_core_and_shared_never_import_the_blocks_layer() -> None:
    """Core/shared remain usable without importing concrete workflow blocks."""
    offenders = [
        (path, module)
        for package in ("core", "shared")
        for path, module in _package_modules(package)
        if _contains_blocks(module)
    ]
    assert offenders == []


def test_calc_never_imports_the_blocks_layer() -> None:
    """Calc depends on a neutral refine port, never on concrete blocks."""
    offenders = [
        (path, module) for path, module in _package_modules("calc") if _contains_blocks(module)
    ]
    assert offenders == []


def test_calc_does_not_import_the_legacy_refine_result_module() -> None:
    """Calc code consumes the neutral result contract owned by calc itself."""
    offenders = [
        (path, module)
        for path, module in _package_modules("calc")
        if module.lstrip(".").endswith("blocks.refine.result")
    ]
    assert offenders == []


def test_legacy_compatibility_shims_preserve_object_identity() -> None:
    """Old import paths re-export the exact neutral/core objects."""
    from confflow.blocks.confgen.generator import load_mol_from_xyz as legacy_loader
    from confflow.blocks.confgen.validator import ChainValidator as legacy_validator
    from confflow.blocks.refine.result import RefineResult as legacy_result
    from confflow.calc.result import RefineResult
    from confflow.core.chem_validation import (
        ChainValidator,
        load_mol_from_xyz,
    )

    assert legacy_result is RefineResult
    assert legacy_validator is ChainValidator
    assert legacy_loader is load_mol_from_xyz


def _contains_blocks(module: str) -> bool:
    """Return whether an absolute or relative module points into ``blocks``."""
    normalized = module.lstrip(".")
    return normalized == "blocks" or normalized.startswith("blocks.") or ".blocks" in normalized


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


def test_workflow_entrypoint_delegates_preparation_to_typed_plan() -> None:
    """Keep config and DAG construction outside the execution facade."""
    engine_path = PACKAGE_ROOT / "workflow" / "engine.py"
    tree = ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
    run_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_workflow"
    )
    calls = [ast.unparse(node.func) for node in ast.walk(run_node) if isinstance(node, ast.Call)]
    assert calls.count("build_workflow_plan") == 1
    for forbidden in (
        "load_workflow_model",
        "build_step_graph",
        "topo_order",
        "build_step_dir_name_map",
    ):
        assert forbidden not in calls


def test_workflow_entrypoint_delegates_finalization_to_typed_boundary() -> None:
    """Keep durable finalization out of the step-execution entrypoint."""
    engine_path = PACKAGE_ROOT / "workflow" / "engine.py"
    tree = ast.parse(engine_path.read_text(encoding="utf-8"), filename=str(engine_path))
    run_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_workflow"
    )
    calls = [ast.unparse(node.func) for node in ast.walk(run_node) if isinstance(node, ast.Call)]
    assert calls.count("finalize_workflow") == 1
    assert "Tracer.trace_low_energy" not in calls
    assert "emit_final_report_and_lowest" not in calls
    assert "write_final_statistics" not in calls

    finalizer = PACKAGE_ROOT / "workflow" / "finalize.py"
    source = finalizer.read_text(encoding="utf-8")
    assert "state_store.save(state)" in source
    assert "write_final_statistics(root_dir, final_stats)" in source
