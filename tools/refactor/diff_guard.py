#!/usr/bin/env python3
"""Check a refactor card's diff against the whitelist and type rules.

Usage: diff_guard.py --repo DIR --base SHA --head SHA --type TYPE
                     --whitelist FILE [--json]

Implements ACCEPTANCE.md §2: R1 (whitelist), R2 (single commit whose parent is
--base), R3 (no modification of existing in-repo checkpoint files under
docs/confgen-fix/checkpoints/), R4 (pytest config untouched) and the per-type
rules (delete / move / test-only).  Rules that need
human judgement are printed as SUSPECT lines; rule breaches make the exit
status non-zero.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# R3 protects the in-repo checkpoint record (manifest + DIFF) as add-only.
# External checkpoint trees ($CKPT, e.g. /tmp/l0-baseline-run-v2/baseline) are
# outside git diff; the acceptance side verifies them separately via MANIFEST.
CHECKPOINT_PREFIX = "docs/confgen-fix/checkpoints/"
CONFTEST_KEYWORDS = (
    "collect_ignore",
    "pytest_collection_modifyitems",
    "addopts",
    "markers",
    "filterwarnings",
)
CI_KEYWORDS = ("--deselect", "-k ", "--ignore")
NEW_LOGIC = re.compile(
    r"^\s*(def\s|class\s|if\s|elif\s|else\s*:|try\s*:|except\b|raise\b|for\s|while\s|return\b|assert\b)"
)
ASSIGNMENT = re.compile(r"^[^=\s][^=]*[^=!<>]=[^=]")
IMPORT_LINE = re.compile(r"^\s*(import\s|from\s+\S+\s+import\s)")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _changed_files(repo: Path, base: str, head: str) -> dict[str, str]:
    """Map path -> status letter (renames produce both old and new paths)."""
    out: dict[str, str] = {}
    for line in _git(repo, "diff", "--name-status", "-M", base, head).splitlines():
        parts = line.split("\t")
        status = parts[0][0]
        if status == "R" and len(parts) >= 3:
            out[parts[1]] = "D"
            out[parts[2]] = "A"
        elif status in ("A", "D", "M", "T") and len(parts) >= 2:
            out[parts[1]] = status
    return out


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    pattern = pattern.rstrip("/")
    if not any(c in pattern for c in "*?"):
        return re.compile("^" + re.escape(pattern) + "(/.*)?$")
    parts = pattern.split("/")
    regex = []
    for part in parts:
        if part == "**":
            regex.append(".*")
            continue
        escaped = re.escape(part)
        escaped = escaped.replace(re.escape("**"), ".*").replace(re.escape("*"), "[^/]*")
        escaped = escaped.replace(re.escape("?"), "[^/]")
        regex.append(escaped)
    return re.compile("^" + "/".join(regex) + "(/.*)?$")


def _load_whitelist(path: str) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(_pattern_to_regex(line))
    return patterns


def _diff_lines(repo: Path, base: str, head: str) -> dict[str, dict[str, list[str]]]:
    """Per-file added/deleted lines from a -U0 diff (renames split upstream)."""
    text = _git(repo, "diff", "-U0", "--no-renames", base, head)
    files: dict[str, dict[str, list[str]]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
            files.setdefault(current, {"added": [], "deleted": []})
        elif line.startswith("--- a/"):
            old = line[6:]
            files.setdefault(old, {"added": [], "deleted": []})
        elif line.startswith("+") and not line.startswith("+++"):
            if current:
                files[current]["added"].append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            if current:
                files[current]["deleted"].append(line[1:])
    return files


def _section(source: str, header: str) -> str:
    lines: list[str] = []
    inside = False
    for line in source.splitlines():
        if line.startswith("[") and line.endswith("]"):
            inside = line == header
            if inside:
                continue
        if inside:
            lines.append(line)
    return "\n".join(lines)


def check_r4(
    repo: Path, base: str, head: str, files: dict[str, dict[str, list[str]]], problems: list[str]
) -> None:
    if "pyproject.toml" in files:
        old = _git(repo, "show", f"{base}:pyproject.toml")
        new = _git(repo, "show", f"{head}:pyproject.toml")
        if _section(old, "[tool.pytest.ini_options]") != _section(new, "[tool.pytest.ini_options]"):
            problems.append("R4: pyproject.toml [tool.pytest.ini_options] changed")
    for path in files:
        name = Path(path).name
        if name == "conftest.py":
            touched = [
                ln
                for ln in files[path]["added"] + files[path]["deleted"]
                if any(k in ln for k in CONFTEST_KEYWORDS)
            ]
            if touched:
                problems.append(f"R4: {path} touches pytest configuration: {touched[:3]}")
        if path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")):
            touched = [ln for ln in files[path]["added"] if any(k in ln for k in CI_KEYWORDS)]
            if touched:
                problems.append(f"R4: {path} changes test selection: {touched[:3]}")


def check_type_rules(
    card_type: str, files: dict[str, dict[str, list[str]]], problems: list[str], suspects: list[str]
) -> None:
    if card_type == "test-only":
        for path in files:
            if path.startswith(("confflow/", "src/")):
                problems.append(f"test-only: production code changed: {path}")
    elif card_type == "delete":
        for path, lines in files.items():
            deleted = {ln.strip() for ln in lines["deleted"]}
            for raw in lines["added"]:
                stripped = raw.strip()
                if not stripped or stripped.startswith("#") or IMPORT_LINE.match(raw):
                    continue
                if stripped in deleted:
                    continue
                if NEW_LOGIC.match(raw) or ASSIGNMENT.match(stripped):
                    problems.append(f"delete: new logic in {path}: {stripped[:100]}")
                else:
                    suspects.append(f"delete: added line to review in {path}: {stripped[:100]}")
    elif card_type == "move":

        def _keep(ln: str) -> bool:
            s = ln.strip()
            return bool(s) and "__all__" not in s and not IMPORT_LINE.match(ln)

        removed = Counter(
            ln.strip() for lines in files.values() for ln in lines["deleted"] if _keep(ln)
        )
        added = Counter(
            ln.strip() for lines in files.values() for ln in lines["added"] if _keep(ln)
        )
        if removed != added:
            for line, n in (removed - added).items():
                problems.append(f"move: deleted but not re-added ({n}x): {line[:100]}")
            for line, n in (added - removed).items():
                problems.append(f"move: added but not deleted elsewhere ({n}x): {line[:100]}")
    elif card_type in ("baseline", "doc"):
        for path in files:
            if not path.startswith("docs/"):
                problems.append(f"{card_type}: file outside docs/: {path}")
    elif card_type in ("logic", "merge", "ci"):
        suspects.append(f"{card_type}: type rules need human review of the diff and commit message")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--type", required=True, dest="card_type")
    parser.add_argument("--whitelist", required=True)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    base = _git(repo, "rev-parse", "--verify", args.base).strip()
    head = _git(repo, "rev-parse", "--verify", args.head).strip()
    problems: list[str] = []
    suspects: list[str] = []

    # R2: exactly one commit, parent is the previous accepted card.
    parents = _git(repo, "rev-list", "--parents", "-n", "1", head).split()
    if len(parents) != 2:
        problems.append(f"R2: head {head[:12]} is not a single-parent commit")
    elif parents[1] != base:
        problems.append(f"R2: parent {parents[1][:12]} != declared base {base[:12]}")

    files = _changed_files(repo, base, head)
    lines = _diff_lines(repo, base, head)

    # R1: whitelist.
    patterns = _load_whitelist(args.whitelist)
    for path in sorted(files):
        if not any(p.match(path) for p in patterns):
            problems.append(f"R1: {path} ({files[path]}) not in whitelist")

    # R3: baseline files may only be added, never modified or deleted.
    for path, status in sorted(files.items()):
        if path.startswith(CHECKPOINT_PREFIX) and status != "A":
            problems.append(f"R3: existing checkpoint file modified ({status}): {path}")

    check_r4(repo, base, head, lines, problems)
    check_type_rules(args.card_type, lines, problems, suspects)

    report = {
        "type": args.card_type,
        "changed_files": {p: files[p] for p in sorted(files)},
        "problems": problems,
        "suspects": suspects,
        "ok": not problems,
    }
    if args.as_json:
        sys.stdout.write(json.dumps(report, sort_keys=True, indent=1) + "\n")
    else:
        sys.stdout.write(f"changed files: {len(files)}\n")
        for path, status in report["changed_files"].items():
            sys.stdout.write(f"  {status} {path}\n")
        for problem in problems:
            sys.stdout.write(f"PROBLEM {problem}\n")
        for item in suspects:
            sys.stdout.write(f"SUSPECT {item}\n")
        sys.stdout.write(
            "result: OK\n" if not problems else f"result: FAIL ({len(problems)} problems)\n"
        )
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
