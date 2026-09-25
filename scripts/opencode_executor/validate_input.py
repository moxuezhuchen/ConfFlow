"""Input validation for the OpenCode self-hosted executor.

All workflow_dispatch inputs are untrusted strings. Every value is checked
against a strict allowlist / charset before it is allowed near a shell or
git invocation. Charset-allowlist validation is the primary defence against
shell injection; callers must still pass values as separate argv entries
(never interpolate them into shell source).
"""

from __future__ import annotations

import argparse
import re
import sys

# Phase labels such as P1, P2, P3, P5.2 — nothing else is accepted.
_PHASE_RE = re.compile(r"^P[0-9]+(\.[0-9]+)?$")

# OpenCode model reference: provider/model with an optional #variant suffix.
# Mirrors the format documented by `opencode run --help` ("provider/model#variant").
_MODEL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}/[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}"
    r"(#[A-Za-z0-9][A-Za-z0-9_.\-]{0,63})?$"
)

# Branch names may only contain the git-safe charset below. The charset itself
# excludes every shell metacharacter (space, ;, &, |, $, `, quotes, parens,
# redirection, glob, tilde, braces, brackets, backslash, newline, ...).
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,149}$")

TEST_PROFILES = ("none", "fast", "full", "gui", "integration")

_MAX_ISSUE_NUMBER = 999999


def validate_phase(value: str) -> str:
    """Return the value if it is a well-formed phase label, else raise ValueError."""
    if not isinstance(value, str) or not _PHASE_RE.match(value):
        raise ValueError(
            f"invalid phase {value!r}: expected e.g. P1, P2, P3, P5.2 "
            "(pattern ^P[0-9]+(\\.[0-9]+)?$)"
        )
    return value


def validate_task_issue(value: str) -> int:
    """Return the issue number if it is a positive integer, else raise ValueError."""
    if not isinstance(value, str) or not re.match(r"^[0-9]{1,6}$", value):
        raise ValueError(f"invalid task_issue {value!r}: expected a positive integer")
    number = int(value)
    if number < 1 or number > _MAX_ISSUE_NUMBER:
        raise ValueError(f"invalid task_issue {value!r}: expected a positive integer")
    return number


def validate_model(value: str) -> str:
    """Return the value if it is a well-formed provider/model[#variant] ref."""
    if not isinstance(value, str) or not _MODEL_RE.match(value):
        raise ValueError(
            f"invalid model {value!r}: expected provider/model[#variant] "
            "using only [A-Za-z0-9_.-] characters"
        )
    return value


def validate_test_profile(value: str) -> str:
    """Return the value if it is one of the allowlisted test profiles."""
    if value not in TEST_PROFILES:
        raise ValueError(
            f"invalid test_profile {value!r}: must be one of {', '.join(TEST_PROFILES)}; "
            "raw shell commands are never accepted"
        )
    return value


def validate_target_branch(value: str) -> str:
    """Return the value if it is a safe automation branch name.

    Rules:
      * strict git-safe charset only (no shell metacharacters possible);
      * must live under the ``automation/`` namespace so the executor can
        never be pointed at ``main``/``master`` or a user development branch;
      * rejects ``..``, ``//``, trailing ``/``, ``.lock`` suffix and other
        git-hostile constructs.
    """
    if not isinstance(value, str) or not _BRANCH_RE.match(value):
        raise ValueError(
            f"invalid target_branch {value!r}: only [A-Za-z0-9._/-] allowed, "
            "max 150 chars, must start with an alphanumeric character"
        )
    if not value.startswith("automation/"):
        raise ValueError(
            f"invalid target_branch {value!r}: must start with 'automation/' "
            "(executor branches only; main/master and user branches are forbidden)"
        )
    if (
        value == "automation/"
        or ".." in value
        or "//" in value
        or value.endswith("/")
        or value.endswith(".lock")
        or "@{" in value
        or "\\" in value
    ):
        raise ValueError(f"invalid target_branch {value!r}: rejected unsafe branch construct")
    lowered = value.lower()
    if lowered in ("automation/main", "automation/master"):
        raise ValueError(f"invalid target_branch {value!r}: reserved name")
    return value


def validate_all(
    task_issue: str, phase: str, model: str, test_profile: str, target_branch: str
) -> dict:
    """Validate a full input set; raise ValueError on the first problem."""
    return {
        "task_issue": validate_task_issue(task_issue),
        "phase": validate_phase(phase),
        "model": validate_model(model),
        "test_profile": validate_test_profile(test_profile),
        "target_branch": validate_target_branch(target_branch),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: exit 0 when all inputs are valid, 2 otherwise."""
    parser = argparse.ArgumentParser(description="Validate OpenCode executor inputs.")
    parser.add_argument("--task-issue", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--test-profile", required=True)
    parser.add_argument("--target-branch", required=True)
    args = parser.parse_args(argv)
    try:
        validate_all(args.task_issue, args.phase, args.model, args.test_profile, args.target_branch)
    except ValueError as exc:
        print(f"input validation failed: {exc}", file=sys.stderr)
        return 2
    print("inputs valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
