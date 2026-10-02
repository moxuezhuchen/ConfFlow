#!/usr/bin/env python3
"""Collect / run / diff the pytest inventories of ConfFlow and JobDesk-v2.

  collect --repo cf|jd --out FILE
  run     --repo cf|jd --out FILE
  diff    --prev A --cur B --declared N [--allowed-files f1,f2,...]

CF runs use ``JOBDESK_V2_SRC=$JDPIN/src``; JD runs always go through
``run_jd_tests.sh`` bound to ``--cf`` (default ConfFlow exec worktree).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

CF_DEFAULT = "/opt/cf-worktrees/exec-cf"
JD_DEFAULT = "/opt/cf-worktrees/exec-jd"
JDPIN_DEFAULT = "/opt/cf-worktrees/jd-pin"
TOOLS = Path(__file__).resolve().parent


def _cf_cmd(args: argparse.Namespace, extra: list[str]) -> tuple[list[str], Path, dict[str, str]]:
    env = dict(os.environ)
    env["JOBDESK_V2_SRC"] = str(Path(args.jdpin) / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    cmd = [sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", *extra]
    return cmd, Path(args.cf), env


def _jd_cmd(args: argparse.Namespace, extra: list[str], junit: str | None):
    cmd = [str(TOOLS / "run_jd_tests.sh"), "--cf", args.cf, "--jd", args.jd]
    if junit:
        cmd += ["--junit", junit]
    cmd += ["--", *extra]
    return cmd, Path(args.jd), dict(os.environ)


def _command(args: argparse.Namespace, extra: list[str], junit: str | None):
    if args.repo == "cf":
        cmd, cwd, env = _cf_cmd(args, extra + ([f"--junitxml={junit}"] if junit else []))
        return cmd, cwd, env
    return _jd_cmd(args, extra, junit)


def cmd_collect(args: argparse.Namespace) -> int:
    # run_jd_tests.sh already passes -q; adding another -q would collapse the
    # listing into per-file counts, so only --collect-only is given.
    cmd, cwd, env = _command(args, ["--collect-only"] + (["-q"] if args.repo == "cf" else []), None)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-3000:] + proc.stderr[-3000:])
        return proc.returncode
    nodeids = sorted(
        line.rstrip() for line in proc.stdout.splitlines() if "::" in line and not line[0].isspace()
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(n + "\n" for n in nodeids))
    print(f"collected {len(nodeids)}")
    return 0


def _outcome(case: ET.Element) -> str:
    for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
        if case.find(tag) is not None:
            return label
    return "passed"


def _resolve_nodeid(root: Path, classname: str, name: str) -> str:
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        candidate = root.joinpath(*parts[:i]).with_suffix(".py")
        if candidate.is_file():
            rel = "/".join(parts[:i]) + ".py"
            return "::".join([rel, *parts[i:], name])
    return "::".join([*parts, name])


def cmd_run(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        junit = str(Path(tmp) / "junit.xml")
        cmd, cwd, env = _command(args, [], junit)
        proc = subprocess.run(cmd, cwd=cwd, env=env, check=False)
        if not Path(junit).is_file():
            sys.stderr.write(f"no junit produced (exit {proc.returncode})\n")
            return 2
        tree = ET.parse(junit)
    outcomes: dict[str, str] = {}
    for case in tree.iter("testcase"):
        nid = _resolve_nodeid(cwd, case.get("classname", ""), case.get("name", ""))
        outcomes[nid] = _outcome(case)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(outcomes, sort_keys=True, indent=1) + "\n")
    counts: dict[str, int] = {}
    for value in outcomes.values():
        counts[value] = counts.get(value, 0) + 1
    print(json.dumps(counts, sort_keys=True), "pytest exit", proc.returncode)
    return 0


def _read(path: str) -> list[str]:
    return [ln for ln in Path(path).read_text().splitlines() if ln]


def cmd_diff(args: argparse.Namespace) -> int:
    prev, cur = set(_read(args.prev)), set(_read(args.cur))
    added, removed = sorted(cur - prev), sorted(prev - cur)
    print(f"added: {len(added)}")
    for n in added:
        print("  +", n)
    print(f"removed: {len(removed)}")
    for n in removed:
        print("  -", n)
    ok = not added and len(removed) == args.declared
    if len(removed) != args.declared:
        print(f"FAIL: removed {len(removed)} != declared {args.declared}")
    if added:
        print("FAIL: new nodes")
    if args.allowed_files:
        allowed = [f for f in args.allowed_files.split(",") if f]
        for n in removed:
            if not any(n == a or n.startswith(a + "::") or n.split("::")[0] == a for a in allowed):
                print("FAIL: removed node outside allowed files:", n)
                ok = False
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("collect", "run"):
        p = sub.add_parser(name)
        p.add_argument("--repo", choices=["cf", "jd"], required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--cf", default=CF_DEFAULT)
        p.add_argument("--jd", default=JD_DEFAULT)
        p.add_argument("--jdpin", default=JDPIN_DEFAULT)
    p = sub.add_parser("diff")
    p.add_argument("--prev", required=True)
    p.add_argument("--cur", required=True)
    p.add_argument("--declared", type=int, required=True)
    p.add_argument("--allowed-files", default="")
    args = parser.parse_args(argv)
    return {"collect": cmd_collect, "run": cmd_run, "diff": cmd_diff}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
