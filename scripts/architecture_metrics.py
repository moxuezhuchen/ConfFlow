#!/usr/bin/env python3
"""Canonical Architecture Diet reachability metrics (pure analysis tool).

This script fixes ONE metric definition for every Architecture Diet PR.  It
never imports ``confflow``; it only parses the source tree, so it runs
identically against any revision.

Fixed definitions (PR-2.6):

- ``PHYSICAL_PRODUCTION_MODULES`` / ``PHYSICAL_PRODUCTION_LOC``: every module
  under ``<root>/confflow``.  Tests, fixtures, and scripts live outside the
  package and are excluded by construction.
- ``V4_REACHABLE_MODULES`` / ``V4_REACHABLE_LOC``: the static import closure
  from the formal V4 production roots:
    * ``confflow.v4cli`` — V4 command surface;
    * ``confflow.application.v4_entry`` — formal runner authority;
    * ``confflow.application.execution.workflow_adapter`` — formal workflow
      service boundary;
    * ``confflow.control_worker`` — control-worker production path.
  The closure counts module-level imports, package ``__init__`` execution
  (importing a submodule pulls its parent packages), and statically visible
  function-local imports.  PEP 562 ``importlib`` lazy exports and other
  dynamic indirection are not statically resolvable and are excluded.
- ``LEGACY_OR_NON_V4_REACHABLE_LOC`` = ``PHYSICAL_PRODUCTION_LOC`` -
  ``V4_REACHABLE_LOC``.
- ``CALC_MODULES`` / ``CALC_LOC``: every module under
  ``confflow/calc`` (the legacy calculation tooling).
- ``CALC_V4_REACHABLE_MODULES``: calc modules inside the fixed V4 closure.
  Must stay ``0`` — the formal V4 runtime never imports calc.
- ``CALC_PUBLIC_TOOLING_MODULES``: calc modules reachable from the public
  tooling roots (the ``confts`` CLI, the ``confrefine`` package, the refine
  composition bridge) plus the declared PEP 562 lazy exports of
  ``confflow/__init__.py`` and ``confflow/calc/__init__.py``.  These are the
  calc modules with an explicit current consumer.
- ``CALC_DEAD_MODULES``: calc modules that are neither in the V4 closure nor
  reachable from any public tooling root or declared lazy export.  Must stay
  ``0``.

Usage::

    python scripts/architecture_metrics.py [--root PATH] [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from collections import defaultdict, deque

#: Formal V4 production entrypoints; the fixed metric never changes these.
V4_ROOTS: tuple[str, ...] = (
    "confflow.v4cli",
    "confflow.application.v4_entry",
    "confflow.application.execution.workflow_adapter",
    "confflow.control_worker",
)

#: Public tooling entrypoints that consume calc; used by the CALC metrics.
CALC_PUBLIC_TOOLING_ROOTS: tuple[str, ...] = (
    "confflow.confts",
    "confflow.blocks.refine",
    "confflow.workflow.composition",
    "confflow.calc",
)

#: ``(file, package)`` pairs whose PEP 562 ``_LAZY_EXPORTS`` declare public
#: tooling names; the declared concrete modules count as consumed.
_LAZY_EXPORT_FILES: tuple[tuple[str, str], ...] = (
    ("confflow/__init__.py", "confflow"),
    ("confflow/calc/__init__.py", "confflow.calc"),
)


def _module_name(root: str, path: str) -> str:
    relative = os.path.relpath(path, root)
    if relative.endswith("__init__.py"):
        relative = os.path.dirname(relative)
    else:
        relative = relative[:-3]
    return relative.replace(os.sep, ".")


def _discover_modules(root: str) -> dict[str, str]:
    modules: dict[str, str] = {}
    package_root = os.path.join(root, "confflow")
    for dirpath, dirnames, filenames in os.walk(package_root):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for filename in filenames:
            if filename.endswith(".py"):
                path = os.path.join(dirpath, filename)
                modules[_module_name(root, path)] = path
    return modules


def _resolve_import(modules: dict[str, str], base: str, module: str, name: str) -> str:
    """Resolve one ``from base.module import name`` target to a module name."""
    if module:
        target = f"{base}.{module}" if base else module
        candidate = f"{target}.{name}"
        return candidate if candidate in modules else target
    candidate = f"{base}.{name}" if base else name
    return candidate if candidate in modules else (base or name)


def _parse_imports(modules: dict[str, str], module: str, path: str) -> set[str]:
    is_init = path.endswith("__init__.py")
    package = module if is_init else module.rsplit(".", 1)[0]
    found: set[str] = set()
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("confflow"):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                up = node.level - 1
                if up:
                    parts = parts[:-up] if up <= len(parts) else []
                base = ".".join(parts)
            else:
                base = ""
            imported = node.module or ""
            if node.names and all(alias.name == "*" for alias in node.names):
                target = f"{base}.{imported}" if base or imported else "confflow"
                if target.startswith("confflow"):
                    found.add(target)
                continue
            for alias in node.names:
                resolved = _resolve_import(modules, base, imported, alias.name)
                if resolved.startswith("confflow"):
                    found.add(resolved)
    return found


def _parents(modules: dict[str, str], module: str) -> set[str]:
    """Return the parent packages whose ``__init__`` executes on import."""
    parts = module.split(".")
    return {
        ".".join(parts[:index])
        for index in range(1, len(parts))
        if ".".join(parts[:index]) in modules
    }


def _closure(modules: dict[str, str], imports: dict[str, set[str]], roots: list[str]) -> set[str]:
    """Compute the static import closure from *roots*, including parents."""
    seen: set[str] = set()
    queue: deque[str] = deque()
    for root in roots:
        if root not in modules:
            raise SystemExit(f"closure root module not found: {root}")
        for candidate in [root, *sorted(_parents(modules, root))]:
            if candidate not in seen:
                seen.add(candidate)
                queue.append(candidate)
    while queue:
        module = queue.popleft()
        for target in imports.get(module, ()):
            for candidate in [target, *sorted(_parents(modules, target))]:
                if candidate in modules and candidate not in seen:
                    seen.add(candidate)
                    queue.append(candidate)
    return seen


def _reachable(modules: dict[str, str], imports: dict[str, set[str]]) -> set[str]:
    return _closure(modules, imports, list(V4_ROOTS))


def _lazy_export_targets(root: str, relative_path: str, package: str) -> set[str]:
    """Return the concrete modules referenced by a PEP 562 lazy export map."""
    path = os.path.join(root, *relative_path.split("/"))
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        assigned = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "_LAZY_EXPORTS" for t in assigned):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for value in node.value.values:
            if not isinstance(value, ast.Tuple) or not value.elts:
                continue
            relative = value.elts[0]
            if not isinstance(relative, ast.Constant) or not isinstance(relative.value, str):
                continue
            targets.add(package if relative.value == "." else f"{package}{relative.value}")
    return targets


def _loc(path: str) -> int:
    with open(path, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def collect(root: str) -> dict[str, object]:
    modules = _discover_modules(root)
    imports: dict[str, set[str]] = defaultdict(set)
    for module, path in modules.items():
        imports[module] = _parse_imports(modules, module, path)
    reachable = _reachable(modules, imports)
    physical_loc = sum(_loc(path) for path in modules.values())
    reachable_loc = sum(_loc(modules[module]) for module in reachable)

    calc_modules = sorted(
        module
        for module in modules
        if module == "confflow.calc" or module.startswith("confflow.calc.")
    )
    calc_set = set(calc_modules)
    calc_loc = sum(_loc(modules[module]) for module in calc_modules)

    tooling_roots = set(CALC_PUBLIC_TOOLING_ROOTS)
    for relative_path, package in _LAZY_EXPORT_FILES:
        tooling_roots |= _lazy_export_targets(root, relative_path, package)
    tooling_roots &= set(modules)
    tooling_reach = _closure(modules, imports, sorted(tooling_roots))
    calc_public = sorted(calc_set & tooling_reach)
    calc_v4 = sorted(calc_set & reachable)
    calc_dead = sorted(calc_set - tooling_reach - reachable)

    return {
        "root": os.path.abspath(root),
        "V4_ROOTS": list(V4_ROOTS),
        "PHYSICAL_PRODUCTION_MODULES": len(modules),
        "PHYSICAL_PRODUCTION_LOC": physical_loc,
        "V4_REACHABLE_MODULES": len(reachable),
        "V4_REACHABLE_LOC": reachable_loc,
        "LEGACY_OR_NON_V4_REACHABLE_LOC": physical_loc - reachable_loc,
        "CALC_MODULES": len(calc_modules),
        "CALC_LOC": calc_loc,
        "CALC_V4_REACHABLE_MODULES": len(calc_v4),
        "CALC_PUBLIC_TOOLING_MODULES": len(calc_public),
        "CALC_DEAD_MODULES": len(calc_dead),
        "v4_reachable": sorted(reachable),
        "calc_public_tooling": calc_public,
        "calc_dead": calc_dead,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Repository root to analyse (default: this script's parent repo).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)
    metrics = collect(args.root)
    if args.json:
        print(json.dumps(metrics, indent=1, sort_keys=True))
        return 0
    print(f"root={metrics['root']}")
    print(f"PHYSICAL_PRODUCTION_MODULES={metrics['PHYSICAL_PRODUCTION_MODULES']}")
    print(f"PHYSICAL_PRODUCTION_LOC={metrics['PHYSICAL_PRODUCTION_LOC']}")
    print(f"V4_REACHABLE_MODULES={metrics['V4_REACHABLE_MODULES']}")
    print(f"V4_REACHABLE_LOC={metrics['V4_REACHABLE_LOC']}")
    print("LEGACY_OR_NON_V4_REACHABLE_LOC=" f"{metrics['LEGACY_OR_NON_V4_REACHABLE_LOC']}")
    print(f"CALC_MODULES={metrics['CALC_MODULES']}")
    print(f"CALC_LOC={metrics['CALC_LOC']}")
    print(f"CALC_V4_REACHABLE_MODULES={metrics['CALC_V4_REACHABLE_MODULES']}")
    print(f"CALC_PUBLIC_TOOLING_MODULES={metrics['CALC_PUBLIC_TOOLING_MODULES']}")
    print(f"CALC_DEAD_MODULES={metrics['CALC_DEAD_MODULES']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
