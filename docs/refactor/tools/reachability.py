#!/usr/bin/env python3
"""Compute the AST import closure of the ConfFlow package and list dead modules.

Usage: reachability.py --cf DIR [--json]

Starting from the fixed entry modules (CLI surfaces and workers), every module
reachable through ``import`` / ``from ... import`` statements, relative imports,
literal ``importlib.import_module("...")`` calls, and package ``_LAZY_EXPORTS``
tables (only when some module actually does ``from <package> import <name>``)
is marked reachable.  In addition, the static and literal-importlib confflow
imports of the Python scripts under ``scripts/`` (including subdirectories)
are added as extra closure entrypoints: scripts are not package modules, and
the ``script_entry_modules`` diagnostic records which script pulls in which
module.  The remaining modules are printed, one per line.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ENTRY_MODULES = (
    "confflow.main",
    "confflow.v4cli",
    "confflow.cli",
    "confflow.control_worker",
    "confflow.fixture_agent",
    "confflow.remote.worker",
    "confflow.worker_attempt",
)


def enumerate_modules(cf: Path, package: str) -> dict[str, Path]:
    parts_all = package.split(".")
    root = cf / parts_all[0]
    if len(parts_all) > 1:
        root = root.joinpath(*parts_all[1:])
    modules: dict[str, Path] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        modules[".".join([package, *parts])] = path
    return modules


def _resolve_relative(module: str, is_package: bool, level: int) -> str:
    parts = module.split(".")
    if not is_package:
        parts = parts[:-1]
    for _ in range(level - 1):
        parts = parts[:-1]
    return ".".join(parts)


def _literal_str(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _lazy_exports(tree: ast.AST, module: str) -> dict[str, str]:
    """Extract ``_LAZY_EXPORTS`` entries (name -> absolute target module)."""
    exports: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_LAZY_EXPORTS" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            name = _literal_str(key) if key is not None else None
            if name is None or not isinstance(value, ast.Tuple) or len(value.elts) != 2:
                continue
            target = _literal_str(value.elts[0])
            if target is None:
                continue
            if target.startswith("."):
                target = _resolve_relative(
                    module, True, len(target) - len(target.lstrip("."))
                ) + target.lstrip(".")
            exports[name] = target
    return exports


def imports_of(module: str, path: Path, lazy_tables: dict[str, dict[str, str]]) -> set[str]:
    """Return the confflow module names this module may pull in."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    is_package = path.name == "__init__.py"
    deps: set[str] = set()
    import_module_aliases = {"import_module"}

    def _record(name: str) -> None:
        if name.startswith("confflow"):
            deps.add(name)

    def _from_targets(base: str, names: list[str]) -> None:
        # ``from <base> import n1, n2``: the package itself runs; each name may
        # be a submodule or a lazy export of that package.
        _record(base)
        for name in names:
            _record(f"{base}.{name}")
            for target in lazy_tables.get(base, {}).get(name, "").split(";"):
                if target:
                    _record(target)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _record(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module and node.module.startswith("confflow"):
                    _from_targets(node.module, [a.name for a in node.names])
                if node.module == "importlib":
                    for alias in node.names:
                        if alias.name == "import_module":
                            import_module_aliases.add(alias.asname or alias.name)
            else:
                base = _resolve_relative(module, is_package, node.level)
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
                _from_targets(base, [a.name for a in node.names])
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if name in import_module_aliases and node.args:
                target = _literal_str(node.args[0])
                if target is not None and target.startswith("."):
                    pkg: str | None = None
                    for kw in node.keywords:
                        if kw.arg == "package":
                            pkg = _literal_str(kw.value)
                    if pkg is not None:
                        level = len(target) - len(target.lstrip("."))
                        target = _resolve_relative(pkg, False, level) + target.lstrip(".")
                if target:
                    _record(target)
    return deps


def script_entry_imports(cf: Path, modules: dict[str, Path]) -> dict[str, set[str]]:
    """Collect static / literal-importlib confflow imports from ``scripts/``.

    Scripts are not package modules; the returned mapping keys each script by
    its path relative to ``cf`` (POSIX separators) and lists the confflow
    modules it pulls in.  Only files that actually import confflow modules
    appear, and only names that are real modules of the package are kept
    (imported symbol names are not module paths).
    """
    root = cf / "scripts"
    entries: dict[str, set[str]] = {}
    if not root.is_dir():
        return entries
    for path in sorted(root.rglob("*.py")):
        try:
            deps = imports_of(f"scripts.{path.stem}", path, {})
        except (OSError, SyntaxError, ValueError):
            continue
        deps = {dep for dep in deps if dep in modules}
        if deps:
            entries[path.relative_to(cf).as_posix()] = deps
    return entries


def compute(cf: Path, package: str) -> dict[str, list[str] | dict[str, list[str]]]:
    modules = enumerate_modules(cf, package)
    lazy_tables: dict[str, dict[str, str]] = {}
    for name, path in modules.items():
        if path.name == "__init__.py":
            exports = _lazy_exports(ast.parse(path.read_text(encoding="utf-8")), name)
            if exports:
                lazy_tables[name] = exports
    deps_by_module = {name: imports_of(name, path, lazy_tables) for name, path in modules.items()}
    script_entries = script_entry_imports(cf, modules)
    script_roots = sorted(
        {dep for deps in script_entries.values() for dep in deps if dep in modules}
    )
    reachable: set[str] = set()
    stack = [m for m in ENTRY_MODULES if m in modules]
    stack.extend(script_roots)
    reachable.update(stack)
    while stack:
        name = stack.pop()
        # Importing a module imports (and executes ``__init__`` of) every
        # ancestor package, so those parents are reachable too and their own
        # imports must enter the closure.
        parts = name.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if parent in modules and parent not in reachable:
                reachable.add(parent)
                stack.append(parent)
        for dep in deps_by_module.get(name, ()):
            if dep in modules and dep not in reachable:
                reachable.add(dep)
                stack.append(dep)
    unreachable = sorted(set(modules) - reachable)
    missing_entries = sorted(set(ENTRY_MODULES) - set(modules))
    return {
        "module_count": len(modules),
        "reachable_count": len(reachable),
        "missing_entry_modules": missing_entries,
        "script_entry_modules": {k: sorted(v) for k, v in sorted(script_entries.items())},
        "unreachable": unreachable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cf", required=True, help="ConfFlow worktree")
    parser.add_argument("--package", default="confflow")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    result = compute(Path(args.cf).resolve(), args.package)
    if args.as_json:
        sys.stdout.write(json.dumps(result, sort_keys=True, indent=1) + "\n")
    else:
        for name in result["unreachable"]:
            sys.stdout.write(name + "\n")
    return 0 if not result["missing_entry_modules"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
