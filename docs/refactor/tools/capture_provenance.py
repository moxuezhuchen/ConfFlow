"""Shared content binding and manifest validation for engine-report capture.

Single implementation used by BOTH ends of the pipeline:

- ``tools-acc/run_sharded.py`` (writer): builds the completion manifest after
  every shard succeeded, binding the run to content digests of the CF tree,
  the acceptance tools and the JobDesk source.
- ``tools/golden_check.py`` (verifier): re-verifies every binding before the
  captured engine reports are accepted in place of a live capture run.

A capture run is only usable when a ``manifest.json`` with ``status ==
"complete"`` exists and every recomputed digest matches.  Any mismatch must
be reported as a concrete problem string; callers must not fall back silently.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

KIND = "confflow-engine-capture/1"
CONFGEN_SCOPE = "tests/v4/test_confgen_*.py"

#: Tool files bound by content, relative to the ConfFlow tree root.  The
#: capture plugin, the shared digest/validation module, the runner, the
#: golden checker, the existing TS1/contract helpers and the sitecustomize
#: hook that actually steers test imports.
TOOL_RELPATHS = (
    "docs/refactor/tools/capture_engine_reports.py",
    "docs/refactor/tools/capture_provenance.py",
    "docs/refactor/tools/golden_check.py",
    "docs/refactor/tools/ts1_engine.py",
    "docs/refactor/tools/contract_digests.py",
    "docs/refactor/tools-acc/run_sharded.py",
    "docs/refactor/tools-acc/noeditable/sitecustomize.py",
)

BAD_OUTCOMES = ("failed", "error")

#: Untracked files under these top-level directories can still be imported by
#: Python or collected by pytest, so they participate in the tree digest.
UNTRACKED_SCAN_DIRS = ("confflow", "tests")
#: Untracked root-level files that influence pytest/Python behavior.
UNTRACKED_ROOT_CONFIGS = ("conftest.py", "pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini")
#: Run artifacts that must never enter the digest (caches, logs, archives,
#: user data such as ``research/``).
UNTRACKED_EXCLUDE_DIR_NAMES = {"__pycache__", "research", ".git"}
UNTRACKED_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".log", ".tar.gz")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def nodes_digest(nodeids: list[str] | set[str]) -> str:
    h = hashlib.sha256()
    for node in sorted(nodeids):
        h.update(node.encode("utf-8") + b"\n")
    return h.hexdigest()


def _relevant_untracked(root: Path) -> list[bytes]:
    """Untracked files that Python/pytest could actually pick up.

    ``git ls-files --others --exclude-standard`` minus run artifacts: caches,
    logs, archives and ``research/`` never participate, and only source/test
    directories plus root-level pytest/Python configs are relevant.
    """
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "-z", "--exclude-standard"],
        capture_output=True,
        check=True,
    )
    relevant = []
    for rel in sorted(f for f in proc.stdout.split(b"\0") if f):
        parts = Path(rel.decode("utf-8")).parts
        name = parts[-1]
        if name.endswith(".tar.gz") or any(name.endswith(s) for s in UNTRACKED_EXCLUDE_SUFFIXES):
            continue
        if any(p in UNTRACKED_EXCLUDE_DIR_NAMES for p in parts[:-1]):
            continue
        if len(parts) == 1 and name in UNTRACKED_ROOT_CONFIGS:
            relevant.append(rel)
        elif parts[0] in UNTRACKED_SCAN_DIRS:
            relevant.append(rel)
    return relevant


def tree_source_digest(root: Path) -> str:
    """Content digest over the CF source/tests/config actually on disk.

    Covers every git-tracked file read from the working tree (not HEAD, so
    uncommitted modifications change the digest; tracked-but-deleted files
    hash as a MISSING marker) plus untracked files that Python/pytest could
    still import or collect (``confflow/``, ``tests/`` and root-level
    pytest/Python configs).  Caches, logs, archives, ``research/`` and other
    run artifacts are excluded.
    """
    files = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    files = sorted({f for f in files if f} | set(_relevant_untracked(root)))
    h = hashlib.sha256()
    for rel in files:
        path = root.joinpath(*Path(rel.decode("utf-8")).parts)
        if path.is_file() and not path.is_symlink():
            digest = sha256_bytes(path.read_bytes())
        else:
            digest = sha256_bytes(b"\0MISSING\0")
        h.update(rel + b"\0" + digest.encode("ascii") + b"\n")
    return h.hexdigest()


def directory_source_digest(root: Path) -> str:
    """Content digest over every file below ``root`` (for the JD source tree).

    Skips ``.git``, ``__pycache__`` directories and ``*.pyc`` files.
    """
    h = hashlib.sha256()
    for path in sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not p.is_symlink()
        and ".git" not in p.relative_to(root).parts
        and "__pycache__" not in p.relative_to(root).parts
        and p.suffix != ".pyc"
    ):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        h.update(rel + b"\0" + sha256_file(path).encode("ascii") + b"\n")
    return h.hexdigest()


def tool_digests(cf: Path) -> dict[str, str]:
    """sha256 of each bound tool file, resolved inside the ConfFlow tree."""
    return {rel: sha256_file(cf / rel) for rel in TOOL_RELPATHS}


def build_manifest(
    *,
    run_id: str,
    cf: Path,
    jd_src: Path,
    capture_dir: Path,
    nodeids: list[str] | set[str],
    tally: dict[str, int],
    out_file: Path,
    reports_dir: Path,
    cf_source_sha256: str | None = None,
    tools_sha256: dict[str, str] | None = None,
    jd_src_sha256: str | None = None,
) -> dict[str, Any]:
    """Assemble the completion manifest (does not write it).

    The digest arguments let the runner pass the END digests it already
    compared against the pre-run values, so the manifest binds exactly the
    verified content instead of a recomputation that could race ahead.
    """
    return {
        "kind": KIND,
        "status": "complete",
        "run_id": run_id,
        "cf_path": str(cf),
        "cf_source_sha256": (
            cf_source_sha256 if cf_source_sha256 is not None else tree_source_digest(cf)
        ),
        "tools_sha256": tools_sha256 if tools_sha256 is not None else tool_digests(cf),
        "jd_src_path": str(jd_src),
        "jd_src_sha256": (
            jd_src_sha256 if jd_src_sha256 is not None else directory_source_digest(jd_src)
        ),
        "nodes_sha256": nodes_digest(nodeids),
        "node_count": len(nodeids),
        "tally": dict(tally),
        "out_sha256": sha256_file(out_file),
        "reports": {p.name: sha256_file(p) for p in sorted(reports_dir.glob("*.json"))},
        "capture_dir_outside_cf": str(capture_dir),
    }


def verify_capture(
    capture_dir: Path,
    *,
    run_id: str,
    cf: Path,
    jd_src: Path,
    collect_nodeids: list[str] | set[str] | None = None,
) -> list[str]:
    """Re-verify every binding of a capture run; return concrete problems.

    An empty list means the capture may be used in place of a live engine
    capture.  ``collect_nodeids`` (when given) is a fresh collect of the tree
    under verification; the bound node set must match it exactly.
    """
    problems: list[str] = []
    manifest_path = capture_dir / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json missing (run did not complete; reports may be partial)"]
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return [f"manifest.json unreadable: {exc}"]
    if manifest.get("kind") != KIND:
        problems.append(f"manifest kind {manifest.get('kind')!r} != {KIND!r}")
    if manifest.get("status") != "complete":
        problems.append(f"manifest status {manifest.get('status')!r} != 'complete'")
    if manifest.get("run_id") != run_id:
        problems.append(f"manifest run_id {manifest.get('run_id')!r} != requested {run_id!r}")
    manifest_cf = Path(manifest.get("cf_path", ""))
    if manifest_cf != cf:
        problems.append(f"manifest cf_path {manifest_cf} != {cf}")
    _inside(problems, capture_dir, cf, "cf tree")
    _inside(problems, capture_dir, jd_src, "JD source tree")

    try:
        if manifest.get("cf_source_sha256") != tree_source_digest(cf):
            problems.append("CF source content changed since the capture run")
        if manifest.get("tools_sha256") != tool_digests(cf):
            problems.append("acceptance tool content changed since the capture run")
        if manifest.get("jd_src_sha256") != directory_source_digest(jd_src):
            problems.append("JD source content changed since the capture run")
    except (OSError, subprocess.CalledProcessError) as exc:
        problems.append(f"digest recomputation failed: {exc}")
        return problems

    out_file = capture_dir / "out.json"
    if not out_file.is_file():
        problems.append("out.json missing from capture directory")
    else:
        if sha256_file(out_file) != manifest.get("out_sha256"):
            problems.append("out.json content differs from the bound sha256")
        else:
            try:
                outcomes = json.loads(out_file.read_text())
            except ValueError as exc:
                problems.append(f"out.json unreadable: {exc}")
            else:
                bad = {k: v for k, v in outcomes.items() if v in BAD_OUTCOMES}
                if bad:
                    problems.append(f"bound run has {len(bad)} failed/error nodes")
                tally: dict[str, int] = {}
                for value in outcomes.values():
                    tally[value] = tally.get(value, 0) + 1
                if tally != manifest.get("tally"):
                    problems.append(
                        f"out.json tally {tally} != manifest tally {manifest.get('tally')}"
                    )
                if collect_nodeids is not None and set(outcomes) != set(collect_nodeids):
                    problems.append("out.json node set differs from the fresh collect")

    if collect_nodeids is not None:
        if manifest.get("nodes_sha256") != nodes_digest(collect_nodeids):
            problems.append("collect node set differs from the bound node digest")
        if manifest.get("node_count") != len(set(collect_nodeids)):
            problems.append(
                f"collect node count {len(set(collect_nodeids))} "
                f"!= manifest node_count {manifest.get('node_count')}"
            )

    reports_dir = capture_dir / "engine_reports"
    bound = manifest.get("reports")
    if not isinstance(bound, dict) or not bound:
        problems.append("manifest binds no engine reports")
    elif not reports_dir.is_dir():
        problems.append("engine_reports directory missing")
    else:
        actual = {p.name: sha256_file(p) for p in sorted(reports_dir.glob("*.json"))}
        missing = sorted(set(bound) - set(actual))
        extra = sorted(set(actual) - set(bound))
        if missing:
            problems.append(f"engine reports missing vs manifest: {missing}")
        if extra:
            problems.append(f"engine reports not bound by manifest: {extra}")
        changed = sorted(n for n in set(bound) & set(actual) if bound[n] != actual[n])
        if changed:
            problems.append(f"engine report content differs from bound sha256: {changed}")
    return problems


def _inside(problems: list[str], capture_dir: Path, root: Path, label: str) -> None:
    if root == capture_dir or root in capture_dir.parents:
        problems.append(f"capture directory must live outside the {label}")


def load_outcomes(capture_dir: Path) -> dict[str, str]:
    return json.loads((capture_dir / "out.json").read_text())
