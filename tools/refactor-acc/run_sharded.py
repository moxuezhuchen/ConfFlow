#!/usr/bin/env python3
"""Run a ConfFlow test tree in parallel shards and merge the outcomes.

Acceptor tool (not part of the executed refactor branch).  Same output format
as ``test_inventory.py run``: ``{nodeid: passed|failed|error|skipped}``.

Usage: run_sharded.py --cf DIR --out FILE [--shards 12] [--weights FILE]
                      [--jdpin DIR] [-- extra pytest args]
       run_sharded.py … --capture-engine-reports DIR --run-id ID

Files are distributed over the shards greedily by weight (seconds from a
previous run when ``--weights`` exists, otherwise the number of tests); the
measured per-file seconds are written back to ``--weights`` for the next run.

With ``--capture-engine-reports`` every shard additionally writes ConfGen
engine reports into its own subdirectory (only for files matching
``tests/v4/test_confgen_*.py``; all tests still run).  After every shard has
succeeded and the JUnit node set matches the collect node set exactly, the
reports are merged and a completion manifest is written LAST (see
``tools/refactor/capture_provenance.py``).  Any failure exits nonzero
and never produces a manifest.  Without the capture options the behavior is
unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
CONFGEN_SCOPE = "tests/v4/test_confgen_*.py"


class CaptureError(Exception):
    """A capture-mode failure that must abort the run without a manifest."""


def _nodeids_from_collect(stdout: str) -> set[str]:
    return {
        line
        for line in stdout.splitlines()
        if "::" in line and not line[0].isspace() and not line.startswith("ERROR ")
    }


def _setup_capture(cf: Path, capture_arg: str, run_id: str, jdpin: str) -> dict:
    """Validate and open the capture directory; returns capture context."""
    if not run_id:
        raise CaptureError("--run-id is required with --capture-engine-reports")
    capture_dir = Path(capture_arg).resolve()
    if capture_dir.exists() and any(capture_dir.iterdir()):
        raise CaptureError(f"capture directory exists and is not empty: {capture_dir}")
    tools_dir = TOOLS.parent / "refactor"
    if not (tools_dir / "capture_provenance.py").is_file():
        raise CaptureError(
            f"capture mode requires the ConfFlow-tree tool layout ({tools_dir} missing)"
        )
    sys.path.insert(0, str(tools_dir))
    import capture_provenance as prov

    jd_src = (Path(jdpin) / "src").resolve()
    for label, root in (("CF tree", cf), ("JD source tree", jd_src)):
        if root == capture_dir or root in capture_dir.parents:
            raise CaptureError(f"capture directory must live outside the {label}")
    capture_dir.mkdir(parents=True, exist_ok=True)
    (capture_dir / "shards").mkdir()
    return {
        "dir": capture_dir,
        "prov": prov,
        "tools_dir": tools_dir,
        "jd_src": jd_src,
        "cf_digest_pre": prov.tree_source_digest(cf),
        "jd_digest_pre": prov.directory_source_digest(jd_src),
        "tools_pre": prov.tool_digests(cf),
    }


def _node_set_problems(
    collect: set[str], outcomes: dict[str, str], counter: Counter[str]
) -> tuple[list[str], list[str], list[str]]:
    """Exact-set comparison: missing, extra (both directions) and duplicates."""
    duplicated = sorted(n for n, c in counter.items() if c > 1)
    missing = sorted(set(collect) - set(outcomes))
    extra = sorted(set(outcomes) - set(collect))
    return missing, extra, duplicated


def _merge_reports(capture: dict) -> Path:
    """Merge shard report files; duplicate names or corrupt JSON abort."""
    merged = capture["dir"] / "engine_reports"
    merged.mkdir()
    seen: set[str] = set()
    for shard_dir in sorted((capture["dir"] / "shards").iterdir()):
        for report in sorted(shard_dir.glob("*.json")):
            if report.name in seen:
                raise CaptureError(f"duplicate engine report name: {report.name}")
            seen.add(report.name)
            try:
                json.loads(report.read_bytes())
            except ValueError as exc:
                raise CaptureError(f"corrupt engine report {report.name}: {exc}") from exc
            shutil.copyfile(report, merged / report.name)
    return merged


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
    parser.add_argument("--capture-engine-reports", default="")
    parser.add_argument("--run-id", default="")
    args, extra = parser.parse_known_args(argv)
    extra = [a for a in extra if a != "--"]
    cf = Path(args.cf).resolve()
    capture = None
    if args.capture_engine_reports:
        try:
            capture = _setup_capture(cf, args.capture_engine_reports, args.run_id, args.jdpin)
        except CaptureError as exc:
            sys.stderr.write(f"CAPTURE SETUP FAILED: {exc}\n")
            return 4
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
    collect_nodes = _nodeids_from_collect(collect.stdout)
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
            shard_env = env
            shard_cmd = [*base, "-q", f"--junitxml={junit}", *extra, *sorted(files)]
            if capture:
                shard_env = dict(env)
                shard_env["PYTHONPATH"] = os.pathsep.join(
                    [str(capture["tools_dir"]), env.get("PYTHONPATH", "")]
                )
                shard_env["CAP_OUT"] = str(capture["dir"] / "shards" / f"shard{i}")
                # Explicit CAP_SCOPE_GLOB from the environment wins (final
                # 93-report scope); otherwise keep the historical broad default.
                # Scope filters report write-out only; all tests still execute.
                shard_env["CAP_SCOPE_GLOB"] = env.get("CAP_SCOPE_GLOB", CONFGEN_SCOPE)
                shard_cmd += ["-p", "capture_engine_reports"]
            procs.append(
                (
                    junit,
                    subprocess.Popen(shard_cmd, cwd=cf, env=shard_env, stdout=subprocess.DEVNULL),
                )
            )
        for _, proc in procs:
            proc.wait()
        outcomes: dict[str, str] = {}
        seconds: dict[str, float] = defaultdict(float)
        junit_counter: Counter[str] = Counter()
        for junit, proc in procs:
            if capture and proc.returncode != 0:
                raise CaptureError(f"shard pytest exited {proc.returncode} (junit {junit.name})")
            if not junit.is_file():
                sys.stderr.write(f"missing junit {junit.name}\n")
                return 2
            for case in ET.parse(junit).iter("testcase"):
                nid = _resolve_nodeid(cf, case.get("classname", ""), case.get("name", ""))
                outcomes[nid] = _outcome(case)
                junit_counter[nid] += 1
                seconds[nid.split("::")[0]] += float(case.get("time", "0") or 0)
        if capture:
            missing, extra, duplicated = _node_set_problems(collect_nodes, outcomes, junit_counter)
            if duplicated:
                raise CaptureError(f"duplicate nodes across junit files: {duplicated[:5]}")
            if missing or extra:
                raise CaptureError(
                    f"junit node set != collect node set "
                    f"(missing={len(missing)}, extra={len(extra)}): "
                    f"{(missing + extra)[:5]}"
                )
            bad = {n: v for n, v in outcomes.items() if v not in ("passed", "skipped")}
            if bad:
                raise CaptureError(f"{len(bad)} failed/error nodes, e.g. {sorted(bad)[:3]}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(outcomes, sort_keys=True, indent=1) + "\n")
    if args.weights:
        Path(args.weights).write_text(json.dumps(dict(seconds), sort_keys=True, indent=1) + "\n")
    if capture:
        shutil.copyfile(Path(args.out), capture["dir"] / "out.json")
        merged = _merge_reports(capture)
        cf_digest = capture["prov"].tree_source_digest(cf)
        if cf_digest != capture["cf_digest_pre"]:
            raise CaptureError("CF source content changed during the run")
        jd_digest = capture["prov"].directory_source_digest(capture["jd_src"])
        if jd_digest != capture["jd_digest_pre"]:
            raise CaptureError("JD source content changed during the run")
        tools = capture["prov"].tool_digests(cf)
        if tools != capture["tools_pre"]:
            raise CaptureError("acceptance tool content changed during the run")
        manifest = capture["prov"].build_manifest(
            run_id=args.run_id,
            cf=cf,
            jd_src=capture["jd_src"],
            capture_dir=capture["dir"],
            nodeids=sorted(collect_nodes),
            tally=dict(Counter(outcomes.values())),
            out_file=capture["dir"] / "out.json",
            reports_dir=merged,
            cf_source_sha256=cf_digest,
            tools_sha256=tools,
            jd_src_sha256=jd_digest,
        )
        # The completion manifest is written LAST: its mere existence under a
        # nonempty capture directory means every check above has passed.
        (capture["dir"] / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=1) + "\n"
        )
    tally: dict[str, int] = defaultdict(int)
    for value in outcomes.values():
        tally[value] += 1
    print(json.dumps(dict(sorted(tally.items()))), f"wall {time.time() - start:.0f}s")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CaptureError as exc:
        sys.stderr.write(f"CAPTURE FAILED: {exc}\n")
        raise SystemExit(4) from exc
