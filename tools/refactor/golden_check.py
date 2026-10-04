#!/usr/bin/env python3
"""Regenerate the golden artifacts and compare them byte-for-byte to the baseline.

Usage: golden_check.py --base DIR --cf DIR [--jd-src DIR] [--checkpoint FILE]
                       [--removed-nodes FILE] [--out FILE]
       golden_check.py … --engine-capture DIR --run-id ID

Checks: TS1 (3 backends), engine reports (full pytest capture of
tests/v4/test_confgen_*.py), contract/boundary digests and JD contract_key.
Exit status 0 only when everything matches.

With ``--engine-capture DIR --run-id ID`` the live engine capture is replaced
by the reports of a completed ``run_sharded.py --capture-engine-reports``
run.  Every binding of the capture manifest (run-id, paths, CF/tool/JD
content digests, node coverage, report sha256s) is re-verified first; any
mismatch exits nonzero with the concrete reasons instead of falling back.
TS1, contract/boundary digests and the removed-nodes semantics stay live and
unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
BACKENDS = ("default", "rigid", "flexible")
DIGEST_KEYS = (
    "contract_cli_sha256",
    "boundary_cli_sha256",
    "contract_bytes_sha256",
    "contract_digest",
)


def _env(cf: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.setdefault("JOBDESK_V2_SRC", "/opt/cf-worktrees/jd-pin/src")
    return env


def _run(cmd: list[str], cf: Path, env: dict[str, str]) -> None:
    proc = subprocess.run(cmd, cwd=cf, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[:3]} failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")


def check_ts1(base: Path, cf: Path, tmp: Path, report: dict) -> None:
    for backend in BACKENDS:
        out = tmp / f"{backend}.json"
        _run(
            [
                sys.executable,
                str(TOOLS / "ts1_engine.py"),
                "--backend",
                backend,
                "--out",
                str(out),
                "--cf",
                str(cf),
            ],
            cf,
            _env(cf),
        )
        same = out.read_bytes() == (base / "ts1" / f"{backend}.json").read_bytes()
        report["ts1"][backend] = "ok" if same else "DIFF"


def check_engine(base: Path, cf: Path, tmp: Path, removed: set[str], report: dict) -> None:
    out = tmp / "engine_reports"
    env = _env(cf)
    env["CAP_OUT"] = str(out)
    env["PYTHONPATH"] = f"{TOOLS}{os.pathsep}{cf}"
    # pytest exit status is not decisive here: only the captured files are compared.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "addopts=",
            "-p",
            "capture_engine_reports",
            "-p",
            "no:cacheprovider",
            *sorted(str(p) for p in (cf / "tests/v4").glob("test_confgen_*.py")),
        ],
        cwd=cf,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    compare_engine_reports(base, out, removed, report)


def compare_engine_reports(base: Path, out: Path, removed: set[str], report: dict) -> None:
    want = {p.name for p in (base / "engine_reports").glob("*.json")}
    got = {p.name for p in out.glob("*.json")} if out.exists() else set()
    eng = report["engine_reports"]
    eng["missing"] = sorted(n for n in want - got if not _is_removed(n, removed))
    eng["missing_allowed_removed"] = sorted(n for n in want - got if _is_removed(n, removed))
    eng["added"] = sorted(got - want)
    eng["different"] = sorted(
        n
        for n in want & got
        if (out / n).read_bytes() != (base / "engine_reports" / n).read_bytes()
    )


def _collect_nodeids(cf: Path) -> set[str]:
    """Fresh ``pytest --collect-only`` node set (no tests are executed)."""
    env = _env(cf)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(TOOLS.parent / "refactor-acc" / "noeditable"), str(TOOLS), str(cf)]
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
        ],
        cwd=cf,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"collect failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return {
        line
        for line in proc.stdout.splitlines()
        if "::" in line and not line[0].isspace() and not line.startswith("ERROR ")
    }


def verify_engine_capture(
    cf: Path, capture_dir: Path, run_id: str, jd_src: Path | None
) -> list[str]:
    """Re-verify a capture run; returns concrete problems (empty = usable)."""
    import capture_provenance as prov

    if jd_src is None:
        return ["--jd-src is required to verify an engine capture"]
    try:
        nodeids = _collect_nodeids(cf)
    except RuntimeError as exc:
        return [str(exc)]
    return prov.verify_capture(
        capture_dir,
        run_id=run_id,
        cf=cf,
        jd_src=jd_src,
        collect_nodeids=nodeids,
    )


def _is_removed(filename: str, removed: set[str]) -> bool:
    stem = filename.rsplit("__", 1)[0]
    for node in removed:
        # mirror capture_engine_reports._clean (runs of invalid chars -> one "_")
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", node).strip("_")
        if clean == stem:
            return True
    return False


def check_contract(
    base: Path, cf: Path, jd_src: Path | None, ckpt: Path | None, tmp: Path, report: dict
) -> None:
    cmd = [
        sys.executable,
        str(TOOLS / "contract_digests.py"),
        "--cf",
        str(cf),
        "--out",
        str(tmp / "contract.json"),
    ]
    if jd_src is not None:
        cmd += ["--jd-src", str(jd_src)]
    _run(cmd, cf, _env(cf))
    new = json.loads((tmp / "contract.json").read_text())
    ref_path = ckpt if ckpt is not None else base / "contract.json"
    ref = json.loads(ref_path.read_text())
    keys = list(DIGEST_KEYS) + (["jd_contract_key"] if jd_src is not None else [])
    report["contract"] = {k: ("ok" if new.get(k) == ref.get(k) else "DIFF") for k in keys}
    report["contract_reference"] = str(ref_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--cf", required=True)
    parser.add_argument("--jd-src")
    parser.add_argument("--checkpoint")
    parser.add_argument("--removed-nodes")
    parser.add_argument("--out")
    parser.add_argument("--engine-capture")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if (args.engine_capture is None) != (args.run_id is None):
        parser.error("--engine-capture and --run-id must be given together")
    base, cf = Path(args.base).resolve(), Path(args.cf).resolve()
    removed = set(Path(args.removed_nodes).read_text().split()) if args.removed_nodes else set()
    capture_dir = Path(args.engine_capture).resolve() if args.engine_capture else None
    report: dict = {"ts1": {}, "engine_reports": {}, "contract": {}}
    if capture_dir is not None:
        problems = verify_engine_capture(
            cf, capture_dir, args.run_id, Path(args.jd_src).resolve() if args.jd_src else None
        )
        if problems:
            for problem in problems:
                sys.stderr.write(f"engine-capture rejected: {problem}\n")
            return 1
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        check_ts1(base, cf, tmp, report)
        if capture_dir is not None:
            compare_engine_reports(base, capture_dir / "engine_reports", removed, report)
        else:
            check_engine(base, cf, tmp, removed, report)
        check_contract(
            base,
            cf,
            Path(args.jd_src) if args.jd_src else None,
            Path(args.checkpoint) if args.checkpoint else None,
            tmp,
            report,
        )
    if capture_dir is not None:
        import capture_provenance as prov

        report["engine_capture"] = {
            "run_id": args.run_id,
            "manifest_sha256": prov.sha256_file(capture_dir / "manifest.json"),
        }
    eng = report["engine_reports"]
    ok = (
        all(v == "ok" for v in report["ts1"].values())
        and not (eng["missing"] or eng["added"] or eng["different"])
        and all(v == "ok" for v in report["contract"].values())
    )
    report["ok"] = ok
    text = json.dumps(report, sort_keys=True, indent=1) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    sys.stdout.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
