#!/usr/bin/env python3

"""``confflow workflow`` command handlers.

R3.3 exposes one command, ``workflow upgrade``, which deterministically migrates a
V2 workflow document to a V3 document. It reads the input, calls the pure
:func:`confflow.config.canonical.upgrade.upgrade_v2_to_v3`, and either prints the
serialized document to stdout or writes it atomically to ``-o``. Migration
warnings (lossy unknown-params routing, an unrecognised extension namespace) go to
stderr; the document on stdout stays clean.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.contracts import ExitCode
from .canonical import (
    ConfigValidationError,
    UpgradeError,
    dump_workflow_yaml,
    load_raw_mapping,
    parse_unknown_params_policy,
    upgrade_v2_to_v3,
    write_workflow_yaml_atomic,
)

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="confflow workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)
    upgrade = subparsers.add_parser(
        "upgrade",
        help="deterministically migrate a V2 workflow document to Workflow V3",
    )
    upgrade.add_argument("input", help="V2 workflow YAML to upgrade")
    upgrade.add_argument(
        "-o",
        "--output",
        default=None,
        help="write the V3 document here (default: stdout); refuses to overwrite",
    )
    upgrade.add_argument(
        "--check",
        action="store_true",
        help="report whether the document can be upgraded without writing anything",
    )
    upgrade.add_argument(
        "--unknown-params",
        dest="unknown_params",
        default="fail",
        metavar="POLICY",
        help=(
            "route V2 params unknown to the V3 core vocabulary: 'fail' (default), "
            "'annotations' (lossy; demotes to non-semantic annotations), or "
            "'extensions:<namespace>' (semantic extension)"
        ),
    )
    return parser


def _run_upgrade(args: argparse.Namespace) -> int:
    try:
        raw = load_raw_mapping(args.input)
    except FileNotFoundError as error:
        print(f"Error: {error}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    except ConfigValidationError as error:
        print(f"Error: {error.issue.message}", file=sys.stderr)
        return ExitCode.USAGE_ERROR

    try:
        policy = parse_unknown_params_policy(args.unknown_params)
        result = upgrade_v2_to_v3(raw, unknown_params_policy=policy)
    except UpgradeError as error:
        for diagnostic in error.diagnostics:
            print(f"Error: {diagnostic}", file=sys.stderr)
        return ExitCode.USAGE_ERROR

    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if args.check:
        print("OK: document can be upgraded to Workflow V3", file=sys.stderr)
        return ExitCode.SUCCESS

    if args.output is None:
        sys.stdout.write(dump_workflow_yaml(result.document))
        return ExitCode.SUCCESS

    destination = Path(args.output)
    if destination.exists():
        print(
            f"Error: output file already exists: {destination} (refusing to overwrite)",
            file=sys.stderr,
        )
        return ExitCode.USAGE_ERROR
    try:
        write_workflow_yaml_atomic(destination, result.document)
    except OSError as error:
        print(f"Error: cannot write {destination}: {error}", file=sys.stderr)
        return ExitCode.RUNTIME_ERROR
    return ExitCode.SUCCESS


def main(args_list: list[str]) -> int:
    parser = _build_parser()
    args = parser.parse_args(args_list)
    if args.command == "upgrade":
        return _run_upgrade(args)
    parser.error(f"unknown workflow command: {args.command}")
    return ExitCode.USAGE_ERROR
