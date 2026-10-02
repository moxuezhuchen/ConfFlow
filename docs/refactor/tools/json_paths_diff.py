#!/usr/bin/env python3
"""List JSON-pointer level added / removed / changed paths between two files.

Usage: json_paths_diff.py OLD NEW
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _esc(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _walk(old: Any, new: Any, path: str, out: dict[str, list[str]]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(old.keys() | new.keys()):
            sub = f"{path}/{_esc(key)}"
            if key not in new:
                out["removed"].append(sub)
            elif key not in old:
                out["added"].append(sub)
            else:
                _walk(old[key], new[key], sub, out)
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            sub = f"{path}/{i}"
            if i >= len(new):
                out["removed"].append(sub)
            elif i >= len(old):
                out["added"].append(sub)
            else:
                _walk(old[i], new[i], sub, out)
    elif old != new or type(old) is not type(new):
        out["modified"].append(path or "/")


def diff_paths(old: Any, new: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"added": [], "removed": [], "modified": []}
    _walk(old, new, "", out)
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    old = json.loads(Path(args[0]).read_text())
    new = json.loads(Path(args[1]).read_text())
    print(json.dumps(diff_paths(old, new), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
