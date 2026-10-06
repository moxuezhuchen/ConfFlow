#!/usr/bin/env python3
"""Find tests affected by changed confflow modules.

Reads changed production modules from ``git diff`` and reverse-maps them to
``tests/**/*.py`` files through a static AST import graph.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return its stdout."""
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return out.stdout


def _repo_root(start: Path) -> Path:
    """Resolve the repository top level, falling back to the start dir."""
    try:
        top = _run_git(["rev-parse", "--show-toplevel"], start).strip()
    except subprocess.CalledProcessError:
        return start
    return Path(top) if top else start


def _path_to_module(rel: str) -> str:
    """Convert a repo-relative .py path to a dotted module name."""
    stem = rel[:-3] if rel.endswith(".py") else rel
    parts = stem.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _file_module(path: Path, root: Path) -> str:
    """Return the dotted module name of a .py file under root."""
    return _path_to_module(path.relative_to(root).as_posix())


def _file_package(path: Path, root: Path) -> str:
    """Return the dotted package containing the given .py file."""
    module = _file_module(path, root)
    if path.name == "__init__.py":
        return module
    return module.rpartition(".")[0]


def _submodule_if_file(base: str, alias: str, root: Path) -> str | None:
    """Return base.alias if it exists as a module file, else None."""
    if alias == "*":
        return None
    candidate = root.joinpath(*(base.split(".") + [alias]))
    if candidate.with_suffix(".py").is_file():
        return f"{base}.{alias}"
    if (candidate / "__init__.py").is_file():
        return f"{base}.{alias}"
    return None


def _resolve_relative(package: str, level: int, module: str | None) -> str | None:
    """Resolve a relative import to a dotted base name."""
    parts = package.split(".") if package else []
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)] if level > 1 else parts
    if module:
        base = [*base, module]
    return ".".join(base) if base else None


def _file_imports(path: Path, root: Path) -> set[str]:
    """Collect imported dotted names from a .py file via AST."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    package = _file_package(path, root)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = _resolve_relative(package, node.level, node.module)
            else:
                base = node.module
            if not base:
                continue
            found.add(base)
            for alias in node.names:
                sub = _submodule_if_file(base, alias.name, root)
                if sub is not None:
                    found.add(sub)
    return found


def changed_modules(base: str, root: Path) -> set[str]:
    """List changed confflow modules from git diff against base."""
    diff = _run_git(["diff", "--name-only", "--diff-filter=ACMRD", base, "--", "confflow"], root)
    modules: set[str] = set()
    for line in diff.splitlines():
        line = line.strip()
        if line.startswith("confflow/") and line.endswith(".py"):
            modules.add(_path_to_module(line))
    return modules


def _confflow_graph(root: Path) -> dict[str, set[str]]:
    """Map each confflow module to the confflow modules it imports."""
    graph: dict[str, set[str]] = {}
    for path in sorted((root / "confflow").rglob("*.py")):
        module = _file_module(path, root)
        deps = {d for d in _file_imports(path, root) if d.startswith("confflow")}
        graph[module] = deps
    return graph


def _related(first: str, second: str) -> bool:
    """Check whether two module names denote the same or nested modules."""
    return first == second or first.startswith(second + ".") or second.startswith(first + ".")


def affected_tests(changed: set[str], root: Path, depth: int = 3) -> list[str]:
    """Find tests importing changed modules, following imports up to depth."""
    if not changed:
        return []
    graph = _confflow_graph(root)
    selected: list[str] = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        direct = {d for d in _file_imports(path, root) if d.startswith("confflow")}
        closure = set(direct)
        frontier = set(direct)
        for _ in range(max(depth - 1, 0)):
            nxt: set[str] = set()
            for mod in frontier:
                nxt.update(graph.get(mod, set()) - closure)
            closure.update(nxt)
            frontier = nxt
        if any(_related(dep, mod) for dep in closure for mod in changed):
            selected.append(path.relative_to(root).as_posix())
    return selected


def main(argv: list[str] | None = None) -> int:
    """Entry point: print affected test files, one per line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="git base ref")
    parser.add_argument("--depth", type=int, default=3, help="import closure depth")
    parser.add_argument("--root", default=None, help="repo root (default: git top)")
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else _repo_root(Path.cwd())
    for line in affected_tests(changed_modules(args.base, root), root, args.depth):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
