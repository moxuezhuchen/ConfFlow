"""Tool tests for the ConfGen engine-report capture pipeline.

Acceptance-side tests only: they exercise ``run_sharded.py --capture-engine-reports``,
``capture_provenance`` and the capture mode of ``golden_check.py`` against a
small synthetic ConfFlow fixture.  They never touch product tests and do not
stand in for a full product run or a complete golden_check.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

TOOLS_ACC = Path(__file__).resolve().parent
TOOLS = TOOLS_ACC.parent / "refactor"
CF_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS_ACC))

import capture_provenance as prov  # noqa: E402
from run_sharded import _node_set_problems  # noqa: E402

STUB_ENGINE = """
class _Structure:
    atoms = ["C", "O"]
    coordinates = (0.0, 1.0)


class _StateKey:
    def to_dict(self):
        return {"k": "v"}


class _Leaf:
    def __init__(self):
        self.state_key = _StateKey()
        self.structure = _Structure()


class _Status:
    value = "REALIZED"


class _TargetRecord:
    target_id = "t1"
    axis = "a"
    ordinal = 0
    status = _Status()
    reason = "ok"
    parent_target_id = None
    evidence = {"x": 1}


class _Run:
    def __init__(self, tag):
        self._tag = tag
        self.target_records = [_TargetRecord()]
        self.leaves = [_Leaf()]

    def report_json(self):
        return {"tag": self._tag}


_SEQ = 0


class ConfgenEngine:
    def run(self, *args, **kwargs):
        global _SEQ
        _SEQ += 1
        return _Run(f"run-{_SEQ}")
"""

# Tool files copied into the fixture CF tree (left UNTRACKED there on purpose:
# they must be bound by the tool digest, not by the CF source digest).
TOOL_FILES = {
    "tools/refactor/capture_engine_reports.py",
    "tools/refactor/capture_provenance.py",
    "tools/refactor/golden_check.py",
    "tools/refactor/ts1_engine.py",
    "tools/refactor/contract_digests.py",
    "tools/refactor-acc/run_sharded.py",
    "tools/refactor-acc/noeditable/sitecustomize.py",
}


def _clean(nodeid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid).strip("_")


def _jdpin(cf: Path) -> Path:
    return cf.parent / "jdpin"


def _jd_src(cf: Path) -> Path:
    return _jdpin(cf) / "src"


def _fixture(
    tmp: Path,
    *,
    failing: bool = False,
    jd_mutator: bool = False,
    tool_mutator: bool = False,
) -> Path:
    files = {
        "confflow/__init__.py": "",
        "confflow/domain/__init__.py": "",
        "confflow/domain/_immutable.py": "def thaw_value(value):\n    return value\n",
        "confflow/science/__init__.py": "",
        "confflow/science/confgen/__init__.py": "",
        "confflow/science/confgen/engine.py": STUB_ENGINE,
        "tests/v4/test_confgen_alpha.py": (
            "from confflow.science.confgen.engine import ConfgenEngine\n\n\n"
            "def test_alpha_one():\n    ConfgenEngine().run()\n\n\n"
            "def test_alpha_two():\n    ConfgenEngine().run()\n    ConfgenEngine().run()\n"
        ),
        "tests/v4/test_confgen_beta.py": (
            "from confflow.science.confgen.engine import ConfgenEngine\n\n\n"
            "def test_beta_one():\n    ConfgenEngine().run()\n"
        ),
        "tests/test_plain.py": (
            "import pytest\n"
            "from confflow.science.confgen.engine import ConfgenEngine\n\n\n"
            "def test_plain_one():\n    assert True\n\n\n"
            "@pytest.mark.slow\n"
            "def test_plain_slow():\n    assert True\n\n\n"
            "def test_plain_engine():\n    ConfgenEngine().run()\n"
        ),
    }
    if failing:
        files["tests/test_plain.py"] += "def test_plain_failing():\n    assert False\n"
    if jd_mutator:
        files["tests/test_jd_mutator.py"] = (
            "import os\nfrom pathlib import Path\n\n\n"
            "def test_touch_jd():\n"
            "    if os.environ.get('E23TEST_JD_MUTATE') != '1':\n"
            "        return\n"
            "    with (Path(os.environ['JOBDESK_V2_SRC']) / 'marker.py').open('a') as fh:\n"
            "        fh.write('# mutated during run\\n')\n"
        )
    if tool_mutator:
        files["tests/test_tool_mutator.py"] = (
            "import os\nfrom pathlib import Path\n\n\n"
            "def test_touch_tool():\n"
            "    if os.environ.get('E23TEST_TOOL_MUTATE') != '1':\n"
            "        return\n"
            "    tool = Path.cwd() / 'tools/refactor/ts1_engine.py'\n"
            "    with tool.open('a') as fh:\n"
            "        fh.write('# mutated during run\\n')\n"
        )
    for rel, text in files.items():
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for rel in TOOL_FILES:
        path = tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((CF_ROOT / rel).read_text())
    # The JD source tree lives OUTSIDE the CF tree.
    (_jdpin(tmp) / "src").mkdir(parents=True, exist_ok=True)
    (_jd_src(tmp) / "marker.py").write_text("MARKER = 1\n")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True)
    # Tool copies stay untracked: only real CF source/tests/config participate
    # in the tree digest; tools are bound separately via the tool digest.
    subprocess.run(
        ["git", "rm", "--cached", "-r", "-f", "-q", "tools"],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    return tmp


def _run_runner(
    cf: Path, out: Path, jdpin: Path, *extra: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TOOLS_ACC / "run_sharded.py"),
            "--cf",
            str(cf),
            "--out",
            str(out),
            "--shards",
            "2",
            "--jdpin",
            str(jdpin),
            *extra,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def _capture_args(capture: Path, run_id: str = "run-1") -> list[str]:
    return [
        "--capture-engine-reports",
        str(capture),
        "--run-id",
        run_id,
    ]


def test_runner_without_capture_keeps_old_behavior(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    out = tmp_path / "out.json"
    proc = _run_runner(cf, out, _jdpin(cf))
    assert proc.returncode == 0, proc.stderr
    outcomes = json.loads(out.read_text())
    assert len(outcomes) == 6
    assert set(outcomes.values()) == {"passed"}
    assert not (tmp_path / "capture").exists()


def test_capture_success_binds_manifest_and_plain_tests_still_run(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    out = tmp_path / "out.json"
    capture = tmp_path / "capture"
    proc = _run_runner(cf, out, _jdpin(cf), *_capture_args(capture))
    assert proc.returncode == 0, proc.stderr
    outcomes = json.loads((capture / "out.json").read_text())
    # Non-ConfGen tests were executed, not skipped.
    assert outcomes["tests/test_plain.py::test_plain_one"] == "passed"
    assert outcomes["tests/test_plain.py::test_plain_engine"] == "passed"
    manifest = json.loads((capture / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["tally"] == {"passed": 6}
    assert manifest["node_count"] == 6
    # Only ConfGen files produced reports; the plain engine run was filtered.
    names = {p.name for p in (capture / "engine_reports").glob("*.json")}
    assert names == {
        f"{_clean(n)}__{seq}.json"
        for n, seq in (
            ("tests/v4/test_confgen_alpha.py::test_alpha_one", 0),
            ("tests/v4/test_confgen_alpha.py::test_alpha_two", 0),
            ("tests/v4/test_confgen_alpha.py::test_alpha_two", 1),
            ("tests/v4/test_confgen_beta.py::test_beta_one", 0),
        )
    }
    assert not [p for p in names if "plain" in p]
    assert prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=_jd_src(cf)) == []


def test_failed_shard_produces_no_manifest_and_no_out(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf", failing=True)
    out = tmp_path / "out.json"
    capture = tmp_path / "capture"
    proc = _run_runner(cf, out, _jdpin(cf), *_capture_args(capture))
    assert proc.returncode != 0
    assert not (capture / "manifest.json").exists()
    assert not out.exists()
    assert "CAPTURE FAILED" in proc.stderr


def test_missing_junit_aborts_without_manifest(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    elsewhere = tmp_path / "elsewhere.xml"
    proc = _run_runner(
        cf,
        tmp_path / "out.json",
        _jdpin(cf),
        *_capture_args(tmp_path / "capture"),
        "--junitxml=" + str(elsewhere),
    )
    assert proc.returncode != 0
    assert not (tmp_path / "capture" / "manifest.json").exists()
    assert elsewhere.is_file()


def test_deselected_node_is_reported_missing(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    proc = _run_runner(
        cf,
        tmp_path / "out.json",
        _jdpin(cf),
        *_capture_args(tmp_path / "capture"),
        "-k",
        "not slow",
    )
    assert proc.returncode != 0
    assert "missing=1" in proc.stderr
    assert not (tmp_path / "capture" / "manifest.json").exists()


def test_node_set_problems_detect_equal_count_wrong_nodes() -> None:
    collect = {"a", "b", "c"}
    outcomes = {"a": "passed", "b": "passed", "d": "passed"}
    counts = Counter({node: 1 for node in outcomes})
    missing, extra, duplicated = _node_set_problems(collect, outcomes, counts)
    assert missing == ["c"]
    assert extra == ["d"]
    assert duplicated == []


def test_merge_rejects_duplicate_report_names(tmp_path: Path) -> None:
    from run_sharded import CaptureError, _merge_reports

    capture_dir = tmp_path / "capture"
    for shard in ("shards/shard0", "shards/shard1"):
        (capture_dir / shard).mkdir(parents=True)
    for shard in ("shard0", "shard1"):
        (capture_dir / "shards" / shard / "same__0.json").write_text("{}\n")
    with pytest.raises(CaptureError, match="duplicate engine report name"):
        _merge_reports({"dir": capture_dir})


def test_verify_rejects_tampering(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    capture = tmp_path / "capture"
    out = tmp_path / "out.json"
    assert _run_runner(cf, out, _jdpin(cf), *_capture_args(capture)).returncode == 0
    jd_src = _jd_src(cf)

    wrong_id = prov.verify_capture(capture, run_id="run-0", cf=cf, jd_src=jd_src)
    assert any("run_id" in p for p in wrong_id)

    with (cf / "tests" / "test_plain.py").open("a") as fh:
        fh.write("# uncommitted touch\n")
    drifted = prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=jd_src)
    assert any("CF source content changed" in p for p in drifted)


def test_untracked_science_source_cannot_silently_reuse(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    capture = tmp_path / "capture"
    assert (
        _run_runner(cf, tmp_path / "out.json", _jdpin(cf), *_capture_args(capture)).returncode == 0
    )
    probe = cf / "confflow" / "_accept_untracked_probe.py"
    probe.write_text("X = 1\n")
    problems = prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=_jd_src(cf))
    assert any("CF source content changed" in p for p in problems)


def test_verify_rejects_tool_and_jd_changes(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    capture = tmp_path / "capture"
    assert (
        _run_runner(cf, tmp_path / "out.json", _jdpin(cf), *_capture_args(capture)).returncode == 0
    )
    jd_src = _jd_src(cf)

    tool_copy = cf / "tools" / "refactor" / "ts1_engine.py"
    tool_copy.write_text(tool_copy.read_text() + "# tool touch\n")
    assert any(
        "tool content changed" in p
        for p in prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=jd_src)
    )
    tool_copy.write_text(tool_copy.read_text().removesuffix("# tool touch\n"))

    (jd_src / "marker.py").write_text("MARKER = 2\n")
    assert any(
        "JD source content changed" in p
        for p in prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=jd_src)
    )


def test_noeditable_change_fails_reuse(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    capture = tmp_path / "capture"
    assert (
        _run_runner(cf, tmp_path / "out.json", _jdpin(cf), *_capture_args(capture)).returncode == 0
    )
    sitecustomize = cf / "tools" / "refactor-acc" / "noeditable" / "sitecustomize.py"
    sitecustomize.write_text(sitecustomize.read_text() + "# touch\n")
    problems = prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=_jd_src(cf))
    assert any("tool content changed" in p for p in problems)


def test_verify_rejects_missing_extra_and_corrupt_reports(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    capture = tmp_path / "capture"
    assert (
        _run_runner(cf, tmp_path / "out.json", _jdpin(cf), *_capture_args(capture)).returncode == 0
    )
    jd_src = _jd_src(cf)
    reports = capture / "engine_reports"
    victim = sorted(reports.glob("*.json"))[0]
    original = victim.read_bytes()

    victim.unlink()
    assert any(
        "missing vs manifest" in p
        for p in prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=jd_src)
    )
    victim.write_bytes(original)

    (reports / "bogus__0.json").write_text("{}\n")
    assert any(
        "not bound by manifest" in p
        for p in prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=jd_src)
    )
    (reports / "bogus__0.json").unlink()

    victim.write_bytes(b"{corrupt")
    assert any(
        "differs from bound sha256" in p
        for p in prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=jd_src)
    )


def test_verify_rejects_incomplete_manifest(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    capture = tmp_path / "capture"
    assert (
        _run_runner(cf, tmp_path / "out.json", _jdpin(cf), *_capture_args(capture)).returncode == 0
    )
    manifest = json.loads((capture / "manifest.json").read_text())
    manifest["status"] = "partial"
    (capture / "manifest.json").write_text(json.dumps(manifest))
    problems = prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=_jd_src(cf))
    assert any("status" in p for p in problems)

    (capture / "manifest.json").unlink()
    problems = prov.verify_capture(capture, run_id="run-1", cf=cf, jd_src=_jd_src(cf))
    assert any("manifest.json missing" in p for p in problems)


def test_runner_rejects_capture_dir_inside_cf(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    proc = _run_runner(cf, tmp_path / "out.json", _jdpin(cf), *_capture_args(cf / "capture"))
    assert proc.returncode == 4
    assert "outside" in proc.stderr
    assert not (cf / "capture" / "manifest.json").exists()


def test_jd_change_during_run_writes_no_manifest(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf", jd_mutator=True)
    capture = tmp_path / "capture"
    proc = _run_runner(
        cf,
        tmp_path / "out.json",
        _jdpin(cf),
        *_capture_args(capture),
        env={"E23TEST_JD_MUTATE": "1"},
    )
    assert proc.returncode != 0
    assert "JD source content changed" in proc.stderr
    assert not (capture / "manifest.json").exists()


def test_tool_change_during_run_writes_no_manifest(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf", tool_mutator=True)
    capture = tmp_path / "capture"
    proc = _run_runner(
        cf,
        tmp_path / "out.json",
        _jdpin(cf),
        *_capture_args(capture),
        env={"E23TEST_TOOL_MUTATE": "1"},
    )
    assert proc.returncode != 0
    assert "tool content changed" in proc.stderr
    assert not (capture / "manifest.json").exists()


def test_golden_pair_args_validation(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    cmd = [
        sys.executable,
        str(TOOLS / "golden_check.py"),
        "--base",
        str(tmp_path / "base"),
        "--cf",
        str(cf),
        "--engine-capture",
        str(tmp_path / "capture"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 2
    assert "--run-id" in proc.stderr


def test_golden_rejects_stale_run_id_before_any_live_work(tmp_path: Path) -> None:
    cf = _fixture(tmp_path / "cf")
    capture = tmp_path / "capture"
    assert (
        _run_runner(cf, tmp_path / "out.json", _jdpin(cf), *_capture_args(capture)).returncode == 0
    )
    base = tmp_path / "base"
    (base / "ts1").mkdir(parents=True)
    (base / "engine_reports").mkdir()
    for backend in ("default", "rigid", "flexible"):
        (base / "ts1" / f"{backend}.json").write_text("{}\n")
    proc = subprocess.run(
        [
            sys.executable,
            str(TOOLS / "golden_check.py"),
            "--base",
            str(base),
            "--cf",
            str(cf),
            "--jd-src",
            str(_jd_src(cf)),
            "--engine-capture",
            str(capture),
            "--run-id",
            "run-stale",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "engine-capture rejected" in proc.stderr
    assert "run_id" in proc.stderr
