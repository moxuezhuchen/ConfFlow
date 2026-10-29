"""Verify the previous review's scenarios against the reviewed PR head."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="confflow-pr197-final-evidence."))
TREE = Path("/tmp/confflow-pr197-final.RiClQi")
BASE = Path("/tmp/confflow-review-20261029.h7Jh9l")
SCRIPTS = Path("/opt/ConfFlow/docs/reviews/data/pr197-verification-20261029")
ENV = {**os.environ, "PYTHONPATH": str(TREE), "PYTHONDONTWRITEBYTECODE": "1"}


def run_script(name, args=(), *, expect_error=False):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, args)],
        cwd=ROOT,
        env=ENV,
        text=True,
        capture_output=True,
        timeout=150,
    )
    label = name + ("-" + str(args[-1]) if name == "upgrade-reuse.py" else "")
    (ROOT / (label + ".stdout.txt")).write_text(result.stdout)
    (ROOT / (label + ".stderr.txt")).write_text(result.stderr)
    if not expect_error:
        assert result.returncode == 0, result.stderr
    return result


def snapshot(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


results = {}
combined = json.loads(run_script("combined-cases.py").stdout)
assert len(combined["combined_axes"]) == 9
assert all(
    r["error"] and not r["executed"] and not r["identity_overwritten"]
    for r in combined["combined_axes"]
)
assert combined["named_allowlist"]["status"] == "completed"
results["combined"] = combined
print("F1 combined identity cases: 9 refused, no launch or registration rewrite", flush=True)

workflow = json.loads(run_script("workflow-combined-change.py").stdout)
assert "invalidate_input" in workflow["error"]
assert not workflow["input_identity_replaced"] and not workflow["environment_changed"]
results["workflow"] = workflow
print("F1 script-to-MLIP workflow: refused, original identity retained", flush=True)

upgrade_root = ROOT / "upgrade"
old = run_script("upgrade-reuse.py", (BASE, upgrade_root, "baseline"))
before = snapshot(upgrade_root / "run")
resumed = run_script("upgrade-reuse.py", (TREE, upgrade_root, "resume"), expect_error=True)
assert resumed.returncode != 0 and "use a fresh run root" in resumed.stderr
assert snapshot(upgrade_root / "run") == before
fresh = run_script("upgrade-reuse.py", (TREE, upgrade_root, "fresh"))
fresh_data = json.loads(fresh.stdout)
assert fresh_data["working_topology"] == [[1], [0], []]
assert fresh_data["provenance"] and "fairchem" in fresh_data["provenance"]
results["upgrade"] = dict(
    baseline=json.loads(old.stdout),
    refused=resumed.stderr.splitlines()[-1],
    old_run_files_unchanged=True,
    fresh=fresh_data,
)
print(
    "F2 real cross-commit upgrade: refused without changing old run; fresh output correct",
    flush=True,
)

named = json.loads(
    run_script(
        "named-allowlist.py", (TREE / "tests/v4/test_arch_review_1029_confgen_xtb_identity.py",)
    ).stdout
)
assert named["status"] == "completed" and named["launches"] == 14
results["named_allowlist"] = named
print("F3 bare-name allowlist: completed, 14 launches", flush=True)

original = run_script("original-scenarios.py")
assert original.stdout.count("PASS") == 7
results["original_scenarios"] = original.stdout
print("N1–N4 original scenarios: 7 passed", flush=True)

(ROOT / "results.json").write_text(json.dumps(results, indent=2))
print("REPORT", ROOT / "results.json", flush=True)
