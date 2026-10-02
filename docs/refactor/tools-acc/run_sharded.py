#!/usr/bin/env python3
"""Run a ConfFlow test tree in parallel shards and merge the outcomes.

Acceptor tool (not part of the executed refactor branch).  Same output format
as ``test_inventory.py run``: ``{nodeid: passed|failed|error|skipped}``.

Usage: run_sharded.py --cf DIR --out FILE [--shards 12] [--weights FILE]
                      [--jdpin DIR] [-- extra pytest args]

Files are distributed over the shards greedily by weight (seconds from a
previous run when ``--weights`` exists, otherwise the number of tests); the
measured per-file seconds are written back to ``--weights`` for the next run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

TOOLS = Path(__file__).resolve().parent


def _resolve_nodeid(root: Path, classname: str, name: str) -> str:
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        if root.joinpath(*parts[:i]).with_suffix(".py").is_file():
            return "::".join(["/".join(parts[:i]) + ".py", *parts[i:], name])
    return "::".join([*parts, name])


def _outcome(case: ET.Element) -> str:
    for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
        if case.find(tag) is not None:
            return label
    return "passed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--shards", type=int, default=12)
    parser.add_argument("--weights", default="")
    parser.add_argument("--jdpin", default="/opt/cf-worktrees/jd-pin")
    args, extra = parser.parse_known_args(argv)
    extra = [a for a in extra if a != "--"]
    cf = Path(args.cf).resolve()
    env = dict(os.environ)
    env.update(
        JOBDESK_V2_SRC=str(Path(args.jdpin) / "src"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join(
            [str(TOOLS / "noeditable"), str(cf), os.environ.get("PYTHONPATH", "")]
        ),
        QT_QPA_PLATFORM="offscreen",
    )
    base = [sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider"]
    collect = subprocess.run(
        [*base, "--collect-only", "-q"], cwd=cf, env=env, capture_output=True, text=True
    )
    counts: dict[str, int] = defaultdict(int)
    for line in collect.stdout.splitlines():
        if "::" in line and not line[0].isspace():
            counts[line.split("::")[0]] += 1
    if not counts:
        sys.stderr.write(collect.stdout[-2000:] + collect.stderr[-2000:])
        return 2
    # A file that fails at *collection* (for example an import of a deleted
    # symbol) has no nodes and would silently vanish from the shards.
    broken = sorted(
        {
            line.split()[1].split("::")[0]
            for line in (collect.stdout + collect.stderr).splitlines()
            if line.startswith("ERROR ") and len(line.split()) > 1
        }
    )
    if broken or collect.returncode not in (0,):
        sys.stderr.write("COLLECTION ERRORS (these files were NOT run):\n")
        for name in broken:
            sys.stderr.write(f"  {name}\n")
        sys.stderr.write(collect.stdout[-1500:] + collect.stderr[-1500:])
        return 3
    weights: dict[str, float] = {}
    if args.weights and Path(args.weights).is_file():
        weights = json.loads(Path(args.weights).read_text())
    weight = {f: weights.get(f, 0.05 * n) for f, n in counts.items()}
    shards: list[list[str]] = [[] for _ in range(args.shards)]
    load = [0.0] * args.shards
    for f in sorted(weight, key=lambda k: -weight[k]):
        i = load.index(min(load))
        shards[i].append(f)
        load[i] += weight[f]
    start = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        procs = []
        for i, files in enumerate(shards):
            if not files:
                continue
            junit = Path(tmp) / f"shard{i}.xml"
            cmd = [*base, "-q", f"--junitxml={junit}", *extra, *sorted(files)]
            procs.append(
                (junit, subprocess.Popen(cmd, cwd=cf, env=env, stdout=subprocess.DEVNULL))
            )
        for _, proc in procs:
            proc.wait()
        outcomes: dict[str, str] = {}
        seconds: dict[str, float] = defaultdict(float)
        for junit, _ in procs:
            if not junit.is_file():
                sys.stderr.write(f"missing junit {junit.name}\n")
                return 2
            for case in ET.parse(junit).iter("testcase"):
                nid = _resolve_nodeid(cf, case.get("classname", ""), case.get("name", ""))
                outcomes[nid] = _outcome(case)
                seconds[nid.split("::")[0]] += float(case.get("time", "0") or 0)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(outcomes, sort_keys=True, indent=1) + "\n")
    if args.weights:
        Path(args.weights).write_text(json.dumps(dict(seconds), sort_keys=True, indent=1) + "\n")
    tally: dict[str, int] = defaultdict(int)
    for value in outcomes.values():
        tally[value] += 1
    print(json.dumps(dict(sorted(tally.items()))), f"wall {time.time() - start:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
