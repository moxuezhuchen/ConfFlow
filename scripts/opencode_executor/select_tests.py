"""Map executor test profiles to the repository's real test commands.

Profiles are a closed allowlist. Each profile resolves to an argv list that
the caller must execute directly (no shell), or to ``None`` for ``none``.
The ``gui`` profile is intentionally unsupported: this repository has no GUI
test suite, so resolving it raises instead of silently skipping tests.
"""

from __future__ import annotations

import argparse
import shlex
import sys

SUPPORTED_PROFILES = ("none", "fast", "full", "gui", "integration")

_RELEASE_WHEEL_TEST = "tests/test_install_release_wheel.py"


class UnsupportedProfileError(ValueError):
    """Raised when a profile cannot be honestly implemented in this repo."""


def resolve_profile(name: str) -> list[str] | None:
    """Resolve *name* to an executable argv list, ``None`` for ``none``.

    Raises ValueError for unknown names and UnsupportedProfileError for
    profiles with no backing test suite.
    """
    if name == "none":
        return None
    if name == "fast":
        # Unit + non-integration suite, skipping the release-installer test
        # that requires a pinned Python (see .github/workflows/ci.yml: the
        # 3.12-only installer job). Matches CI's "matrix excluding
        # release installer lock-dependent tests" behaviour.
        return [
            "./scripts/test.sh",
            "-q",
            "-m",
            "not integration",
            f"--ignore={_RELEASE_WHEEL_TEST}",
        ]
    if name == "full":
        # Entire suite through the repo's canonical runner (CI parity:
        # `pytest -q` on the pinned installer job, full matrix otherwise).
        return ["./scripts/test.sh", "-q"]
    if name == "gui":
        raise UnsupportedProfileError(
            "test_profile 'gui' is not implemented: this repository has no GUI "
            "(Qt/Tk) test suite. Use fast, full, integration, or none."
        )
    if name == "integration":
        return ["./scripts/test.sh", "-q", "-m", "integration"]
    raise ValueError(
        f"unknown test_profile {name!r}: must be one of {', '.join(SUPPORTED_PROFILES)}"
    )


def format_command(argv: list[str] | None) -> str:
    """Render an argv list as shell-quoted text for logs, or 'SKIP'."""
    if argv is None:
        return "SKIP (profile 'none': no tests requested)"
    return shlex.join(argv)


def write_null_separated(argv: list[str] | None) -> int:
    """Write argv NUL-separated for machine consumption by run_task.sh.

    One argv element per NUL-terminated record (never shell-quoted), so
    elements containing spaces survive intact. ``None`` writes nothing.
    """
    if argv is not None:
        sys.stdout.write("\0".join(argv) + "\0")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI: print the resolved command; exit 0, or 2/3 on error."""
    parser = argparse.ArgumentParser(description="Resolve an executor test profile.")
    parser.add_argument("profile", choices=SUPPORTED_PROFILES, help="test profile to resolve")
    parser.add_argument(
        "--null",
        action="store_true",
        help="print raw argv NUL-separated for run_task.sh (default: human-readable)",
    )
    args = parser.parse_args(argv)
    try:
        resolved = resolve_profile(args.profile)
    except UnsupportedProfileError as exc:
        print(f"unsupported test_profile: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"invalid test_profile: {exc}", file=sys.stderr)
        return 2
    if args.null:
        return write_null_separated(resolved)
    print(format_command(resolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
