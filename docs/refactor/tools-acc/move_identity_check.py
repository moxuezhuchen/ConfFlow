#!/usr/bin/env python3
"""Acceptor tool: verify that moved functions are AST-identical to their originals.

Usage: move_identity_check.py REPO_DIR OLD_REV OLD_PATH NEW_PATH name [name ...]
Compares ``ast.unparse`` of each named top-level def/class in ``OLD_REV:OLD_PATH``
with the one in the working-tree ``NEW_PATH``.  With the single name ``*`` every
top-level def/class of the old file must be identical.  Exit 0 only if all match.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


def defs(source: str) -> dict[str, str]:
    return {
        node.name: ast.unparse(node)
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def main() -> int:
    repo, rev, old_path, new_path, *names = sys.argv[1:]
    old = defs(
        subprocess.check_output(["git", "-C", repo, "show", f"{rev}:{old_path}"], text=True)
    )
    new = defs((Path(repo) / new_path).read_text(encoding="utf-8"))
    wanted = list(old) if names == ["*"] else names
    bad = [name for name in wanted if name not in new or old.get(name) != new[name]]
    for name in wanted:
        print(f"{name}: {'DIFFERENT' if name in bad else 'identical'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
