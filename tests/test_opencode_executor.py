"""Tests for the OpenCode self-hosted executor infrastructure.

Covers input validation (including injection rejection), test-profile
mapping, workflow static safety properties, the refactor state schema, and
executor shell-script guardrails. Existing CI contracts are not touched.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from jsonschema import validate as jsonschema_validate

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "opencode-executor.yml"
RUN_TASK = ROOT / "scripts" / "opencode_executor" / "run_task.sh"
SCHEMA = ROOT / ".github" / "refactor" / "state.schema.json"
EXAMPLE_STATE = ROOT / ".github" / "refactor" / "example-state.yaml"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate_input = _load_module(
    "opencode_validate_input",
    ROOT / "scripts" / "opencode_executor" / "validate_input.py",
)
select_tests = _load_module(
    "opencode_select_tests",
    ROOT / "scripts" / "opencode_executor" / "select_tests.py",
)


# ---------------------------------------------------------- validation ----


@pytest.mark.parametrize("phase", ["P1", "P2", "P3", "P5.2", "P10", "P0.1"])
def test_validate_phase_accepts(phase):
    assert validate_input.validate_phase(phase) == phase


@pytest.mark.parametrize(
    "phase",
    [
        "",
        "p1",
        "Phase1",
        "P",
        "P1;",
        "P1 && rm -rf /",
        "P1|cat",
        "$(P1)",
        "`P1`",
        "P1\nP2",
        "P-1",
        "1P",
    ],
)
def test_validate_phase_rejects(phase):
    with pytest.raises(ValueError):
        validate_input.validate_phase(phase)


@pytest.mark.parametrize("issue", ["1", "123", "999999"])
def test_validate_task_issue_accepts(issue):
    assert validate_input.validate_task_issue(issue) == int(issue)


@pytest.mark.parametrize(
    "issue", ["", "0", "-1", "12a", "1; rm", "$(1)", "`1`", "1 2", "1000000", "1\n2"]
)
def test_validate_task_issue_rejects(issue):
    with pytest.raises(ValueError):
        validate_input.validate_task_issue(issue)


@pytest.mark.parametrize(
    "model",
    [
        "opencode-go/muse-spark-1.3-contributor",
        "opencode-go/deepseek-v4-pro",
        "deepseek/deepseek-v4-pro",
        "provider/model#variant",
    ],
)
def test_validate_model_accepts(model):
    assert validate_input.validate_model(model) == model


@pytest.mark.parametrize(
    "model",
    [
        "",
        "model-only",
        "/model",
        "a/b/c",
        "a/b#",
        "provider/model; rm -rf /",
        "provider/model && evil",
        "$(evil)/model",
        "a|b/c",
        "a/b`c`",
        "a b/c",
        "a/b#c d",
    ],
)
def test_validate_model_rejects(model):
    with pytest.raises(ValueError):
        validate_input.validate_model(model)


@pytest.mark.parametrize("profile", ["none", "fast", "full", "gui", "integration"])
def test_validate_test_profile_accepts(profile):
    assert validate_input.validate_test_profile(profile) == profile


@pytest.mark.parametrize(
    "profile", ["", "pytest -q", "fast; evil", "FULL", "all", "$(fast)", "none|cat"]
)
def test_validate_test_profile_rejects(profile):
    with pytest.raises(ValueError):
        validate_input.validate_test_profile(profile)


@pytest.mark.parametrize(
    "branch",
    ["automation/jobdesk-v3-P3", "automation/P1", "automation/a-b_c/d.e"],
)
def test_validate_target_branch_accepts(branch):
    assert validate_input.validate_target_branch(branch) == branch


@pytest.mark.parametrize(
    "branch",
    [
        "",
        "main",
        "master",
        "feat/my-work",
        "automation/",
        "automation/main",
        "automation/master",
        "P3",
        "automation/a b",
        "automation/a;b",
        "automation/a&&b",
        "automation/a|b",
        "automation/a$(b)",
        "automation/a`b`",
        "automation/a'b",
        'automation/a"b',
        "automation/../escape",
        "automation/a//b",
        "automation/a/",
        "automation/a.lock",
        "/automation/a",
    ],
)
def test_validate_target_branch_rejects(branch):
    with pytest.raises(ValueError):
        validate_input.validate_target_branch(branch)


def test_validate_all_roundtrip():
    out = validate_input.validate_all(
        "123", "P3", "opencode-go/x-y", "fast", "automation/jobdesk-v3-P3"
    )
    assert out == {
        "task_issue": 123,
        "phase": "P3",
        "model": "opencode-go/x-y",
        "test_profile": "fast",
        "target_branch": "automation/jobdesk-v3-P3",
    }


# -------------------------------------------------------------- mapping ----


def test_resolve_none_is_skip():
    assert select_tests.resolve_profile("none") is None


def test_resolve_fast_matches_repo_runner():
    argv = select_tests.resolve_profile("fast")
    assert argv[0] == "./scripts/test.sh"
    assert "-m" in argv and "not integration" in argv
    assert any("test_install_release_wheel" in a for a in argv)


def test_resolve_full_runs_canonical_suite():
    assert select_tests.resolve_profile("full") == ["./scripts/test.sh", "-q"]


def test_resolve_integration_selects_marker():
    argv = select_tests.resolve_profile("integration")
    assert argv == ["./scripts/test.sh", "-q", "-m", "integration"]


def test_resolve_gui_fails_loudly():
    with pytest.raises(select_tests.UnsupportedProfileError):
        select_tests.resolve_profile("gui")


def test_resolve_unknown_rejected():
    with pytest.raises(ValueError):
        select_tests.resolve_profile("pytest -q")


def test_null_mode_roundtrips_argv_with_spaces(capsys):
    rc = select_tests.main(["fast", "--null"])
    assert rc == 0
    out, _ = capsys.readouterr()
    assert out.endswith("\0") and "\n" not in out
    argv = out[:-1].split("\0")
    assert argv == select_tests.resolve_profile("fast")
    assert "not integration" in argv  # space preserved as ONE element


def test_null_mode_none_writes_nothing(capsys):
    assert select_tests.main(["none", "--null"]) == 0
    out, _ = capsys.readouterr()
    assert out == ""


# ------------------------------------------------------------- workflow ----


@pytest.fixture(scope="module")
def workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow_doc(workflow_text):
    # NOTE: YAML 1.1 parses the `on:` key as boolean True.
    doc = yaml.safe_load(workflow_text)
    doc["on"] = doc.get("on", doc.get(True))
    return doc


def test_workflow_file_exists():
    assert WORKFLOW.is_file()


def test_workflow_dispatch_only(workflow_doc):
    triggers = workflow_doc.get("on", {})
    assert set(triggers.keys()) == {"workflow_dispatch"}, triggers.keys()
    raw = yaml.safe_dump(workflow_doc)
    for forbidden in ("pull_request", "pull_request_target", "issue_comment"):
        assert forbidden not in raw


def test_workflow_runs_on_executor_label(workflow_doc):
    labels = workflow_doc["jobs"]["execute"]["runs-on"]
    assert "self-hosted" in labels
    assert "opencode-executor" in labels


def test_workflow_concurrency_single_flight(workflow_doc):
    conc = workflow_doc.get("concurrency", {})
    assert "group" in conc
    assert conc.get("cancel-in-progress") is False


def test_workflow_permissions_minimal(workflow_doc):
    perms = workflow_doc.get("permissions", {})
    assert perms, "workflow must declare explicit minimal permissions"
    flat = yaml.safe_dump(perms)
    assert "write-all" not in flat
    assert "read-all" not in flat
    for scope, level in perms.items():
        assert level in ("read", "write"), (scope, level)
    assert perms.get("contents") == "write"  # push result branches
    assert perms.get("issues") == "read"  # read task issue


def test_workflow_no_direct_input_interpolation(workflow_text):
    """No ${{ github.event.inputs.* }} may appear inside a run: script."""
    blocks = re.findall(r"(?m)^\s*run:\s*\|.*?(?=^\s*\w|\Z)", workflow_text, re.S)
    assert blocks, "expected at least one run: block"
    for block in blocks:
        assert "github.event.inputs" not in block, block


def test_workflow_inputs_constrained(workflow_doc):
    inputs = workflow_doc["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) >= {"task_issue", "phase", "model", "test_profile", "target_branch"}
    assert inputs["test_profile"].get("type") == "choice"
    assert set(inputs["test_profile"]["options"]) == {
        "none",
        "fast",
        "full",
        "gui",
        "integration",
    }


# ----------------------------------------------------------------- state ----


def test_state_schema_valid_json():
    json.loads(SCHEMA.read_text(encoding="utf-8"))


def test_example_state_conforms_to_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    example = yaml.safe_load(EXAMPLE_STATE.read_text(encoding="utf-8"))
    jsonschema_validate(instance=example, schema=schema)


def test_lifecycle_enum_covers_required_statuses():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    enum = schema["$defs"]["lifecycle"]["enum"]
    for status in ("pending", "ready", "running", "review", "blocked", "done", "failed"):
        assert status in enum


# ------------------------------------------------------------------ shell ----


@pytest.fixture(scope="module")
def run_task_text():
    return RUN_TASK.read_text(encoding="utf-8")


def _code_lines(text):
    """Shell lines that actually execute (strip comments and blanks)."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(line)
    return "\n".join(out)


def test_run_task_no_destructive_git(run_task_text):
    code = _code_lines(run_task_text)
    assert "reset --hard" not in code
    assert "clean -fd" not in code
    # Forbid real force-push invocations (prose mentions of "--force" as a
    # prohibition are fine and expected).
    assert re.search(r"(?m)git\s+push\s+.*--force", run_task_text) is None
    assert re.search(r"(?m)git\s+push\s+.*\s-f\b", run_task_text) is None
    assert "force push" in run_task_text  # the prohibition is documented


def test_run_task_no_merge_or_eval(run_task_text):
    assert re.search(r"(?m)^\s*git merge\b", run_task_text) is None
    assert re.search(r"(?m)^\s*eval\b", run_task_text) is None


def test_run_task_uses_verified_opencode_flags(run_task_text):
    # Only flags confirmed by `opencode run --help` (v2.0.16) on this host.
    assert "--model" in run_task_text
    for flag in ("--session", "--agent", "--share", "--print"):
        assert flag not in run_task_text


def test_run_task_single_flight_and_branch_guards(run_task_text):
    assert "flock -n" in run_task_text
    assert "automation/" in run_task_text
    assert "main|master" in run_task_text


def test_run_task_never_mutates_tree_before_guard(run_task_text):
    code = _code_lines(run_task_text)
    assert "chmod" not in code  # mode changes trip the dirty-tree guard


def test_run_task_validates_before_use(run_task_text):
    assert "validate_input.py" in run_task_text
    assert "select_tests.py" in run_task_text
    invocation = 'opencode "${OPENCODE_ARGS[@]}"'
    assert invocation in run_task_text
    assert run_task_text.index("validate_input.py") < run_task_text.index(invocation)
