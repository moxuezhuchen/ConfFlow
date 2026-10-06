"""Check that added/modified production lines meet a coverage floor (DIET-2 P0.1a).

Usage: python tools/changed_coverage.py [--base REF] [--min 85] [--xml coverage.xml]
Only executable lines (present in coverage.xml) under confflow/ count; pure deletions pass.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def changed_lines(base: str) -> dict[str, set[int]]:
    out = subprocess.run(
        ["git", "diff", "-U0", "--no-color", f"{base}...HEAD", "--", "confflow/*.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    result: dict[str, set[int]] = {}
    path = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        m = _HUNK.match(line)
        if m and path:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            result.setdefault(path, set()).update(range(start, start + count))
    return result


def coverage_by_file(xml_path: str) -> dict[str, dict[int, bool]]:
    files: dict[str, dict[int, bool]] = {}
    for cls in ET.parse(xml_path).getroot().iter("class"):
        lines = files.setdefault(cls.get("filename", ""), {})
        for ln in cls.iter("line"):
            lines[int(ln.get("number"))] = int(ln.get("hits", "0")) > 0
    return files


def evaluate(changed: dict[str, set[int]], cov: dict[str, dict[int, bool]]):
    total = covered = 0
    missed: list[str] = []
    for path, lines in sorted(changed.items()):
        known = cov.get(path) or cov.get(path.removeprefix("confflow/"), {})
        for n in sorted(lines):
            if n in known:
                total += 1
                if known[n]:
                    covered += 1
                else:
                    missed.append(f"{path}:{n}")
    return total, covered, missed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--min", type=float, default=85.0)
    ap.add_argument("--xml", default="coverage.xml")
    args = ap.parse_args(argv)
    total, covered, missed = evaluate(changed_lines(args.base), coverage_by_file(args.xml))
    pct = 100.0 if total == 0 else 100.0 * covered / total
    print(f"changed executable lines: {covered}/{total} covered ({pct:.1f}%), floor {args.min}%")
    if pct < args.min:
        print("uncovered changed lines:", *missed[:200], sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
