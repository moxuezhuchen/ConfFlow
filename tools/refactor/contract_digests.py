#!/usr/bin/env python3
"""Compute contract/boundary digests of a ConfFlow tree (and the JD contract_key).

Usage: contract_digests.py --cf DIR [--jd-src DIR] --out FILE

Also writes ``contract.full.json`` and ``boundary.full.json`` next to ``--out``.
The computation runs in a subprocess with cwd=DIR so the chosen tree's
``confflow`` package is the one imported.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _worker(jd_src: str | None) -> dict[str, Any]:
    import warnings

    import confflow
    from confflow import v4cli

    captured: dict[str, bytes] = {}
    for name in ("contract", "boundary"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = v4cli.main([name, "--json"])
        if rc != 0:
            raise RuntimeError(f"v4 {name} --json exited {rc}")
        captured[name] = buf.getvalue().encode("utf-8")
    from confflow.producer.contract import generate_contract_bytes

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        contract_bytes = generate_contract_bytes(producer_version=confflow.__version__)
    envelope = json.loads(contract_bytes)
    result: dict[str, Any] = {
        "contract_cli_sha256": _sha(captured["contract"]),
        "boundary_cli_sha256": _sha(captured["boundary"]),
        "contract_bytes_sha256": _sha(contract_bytes),
        "contract_digest": envelope["contract_digest"],
        "contract_full": json.loads(captured["contract"]),
        "boundary_full": json.loads(captured["boundary"]),
    }
    if jd_src:
        sys.path.insert(0, jd_src)
        from jobdesk_v2.application.editor.contract.v4 import parse_v4_contract_bytes

        result["jd_contract_key"] = parse_v4_contract_bytes(contract_bytes).contract_key
    return result


def compute(cf: Path, jd_src: Path | None) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cf)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker"]
    if jd_src is not None:
        cmd += ["--jd-src", str(jd_src)]
    proc = subprocess.run(cmd, cwd=cf, env=env, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))
    return json.loads(proc.stdout)


def write_outputs(result: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    full = {k: result.pop(k) for k in ("contract_full", "boundary_full")}
    (out.parent / "contract.full.json").write_text(
        json.dumps(full["contract_full"], sort_keys=True, indent=1) + "\n"
    )
    (out.parent / "boundary.full.json").write_text(
        json.dumps(full["boundary_full"], sort_keys=True, indent=1) + "\n"
    )
    out.write_text(json.dumps(result, sort_keys=True, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cf")
    parser.add_argument("--jd-src")
    parser.add_argument("--out")
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args(argv)
    if args.worker:
        sys.stdout.write(json.dumps(_worker(args.jd_src)))
        return 0
    if not args.cf or not args.out:
        parser.error("--cf and --out are required")
    result = compute(Path(args.cf).resolve(), Path(args.jd_src) if args.jd_src else None)
    write_outputs(result, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
