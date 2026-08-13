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
