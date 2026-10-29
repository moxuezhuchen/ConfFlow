"""Run tracked test modules in independent pytest processes and temp roots."""

import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path("/tmp/confflow-pr199-source")
out = Path("/tmp/confflow-pr199-acceptance")
files = sorted(str(p.relative_to(root)) for p in (root / "tests").rglob("test_*.py"))
groups = [files[index::8] for index in range(8)]
(out / "test-groups.json").write_text(json.dumps(groups, indent=2))
env = {**os.environ, "PYTHONPATH": str(root)}


def run(index):
    started = time.monotonic()
    log = out / f"pytest-{index}.txt"
    with log.open("w") as stream:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-o",
                "addopts=",
                f"--basetemp={out}/pytest-temp-{index}",
                *groups[index],
            ],
            cwd=root,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    record = {
        "group": index,
        "exit_code": result.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "files": len(groups[index]),
    }
    print(json.dumps(record), flush=True)
    return record


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(run, range(8)))
(out / "test-results.json").write_text(json.dumps(results, indent=2))
sys.exit(0 if all(r["exit_code"] == 0 for r in results) else 1)
