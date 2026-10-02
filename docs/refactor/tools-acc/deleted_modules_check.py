#!/usr/bin/env python3
"""Acceptor tool: deleted modules must raise ModuleNotFoundError, and the
repo package must resolve to the tree under test (not to a main checkout).

Usage:
  deleted_modules_check.py cf  --tree DIR module [module ...]
  deleted_modules_check.py jd  --tree DIR module [module ...]   (run under run_jd_tests.sh's mount namespace)

``cf`` masks the venv's editable finder (tools-acc/noeditable); ``jd`` puts
``DIR/src`` first on sys.path.  Exit status 0 only when every module is gone and
the package resolves under DIR.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

CHILD = r"""
import importlib, sys
pkg, tree = sys.argv[1], sys.argv[2]
mods = sys.argv[3:]
m = importlib.import_module(pkg)
where = m.__file__
print("package ->", where)
ok = str(where).startswith(tree)
bad = []
for name in mods:
    try:
        importlib.import_module(name)
        bad.append(name)
    except ModuleNotFoundError:
        pass
print("still importable:", bad)
sys.exit(0 if ok and not bad else 1)
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", choices=["cf", "jd"])
    parser.add_argument("--tree", required=True)
    parser.add_argument("modules", nargs="+")
    args = parser.parse_args()
    tree = str(Path(args.tree).resolve())
    env = dict(os.environ)
    if args.repo == "cf":
        env["PYTHONPATH"] = os.pathsep.join([str(HERE / "noeditable"), tree])
        pkg, cwd = "confflow", tree
        python = "/opt/ConfFlow/.venv/bin/python"
    else:
        env["PYTHONPATH"] = os.path.join(tree, "src")
        env["QT_QPA_PLATFORM"] = "offscreen"
        pkg, cwd = "jobdesk_v2", tree
        python = sys.executable
    return subprocess.run(
        [python, "-c", CHILD, pkg, tree, *args.modules], cwd=cwd, env=env
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
