"""Machine-readable configuration-contract command handlers."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..__build__ import COMMIT, DIRTY
from ..core.contracts import ExitCode
from .canonical import (
    CONFIGURATION_VALIDATION_SCHEMA,
    ConfigValidationError,
    build_configuration_contract,
    parse_workflow_mapping,
    workflow_schema_sha256,
)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))


def _validate_stdin() -> int:
    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON input: {exc.msg}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    if not isinstance(raw, dict):
        _emit(
            {
                "schema": CONFIGURATION_VALIDATION_SCHEMA,
                "valid": False,
                "workflow_schema_sha256": workflow_schema_sha256(),
                "issues": [{"path": "", "message": "workflow config root must be a mapping"}],
            }
        )
        return ExitCode.USAGE_ERROR
    try:
        parse_workflow_mapping(raw)
    except ConfigValidationError as exc:
        _emit(
            {
                "schema": CONFIGURATION_VALIDATION_SCHEMA,
                "valid": False,
                "workflow_schema_sha256": workflow_schema_sha256(),
                "issues": [{"path": exc.issue.path, "message": exc.issue.message}],
            }
        )
        return ExitCode.USAGE_ERROR
    _emit(
        {
            "schema": CONFIGURATION_VALIDATION_SCHEMA,
            "valid": True,
            "workflow_schema_sha256": workflow_schema_sha256(),
            "issues": [],
        }
    )
    return ExitCode.SUCCESS


def main(args_list: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="confflow config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    contract = subparsers.add_parser("contract")
    contract.add_argument("--json", action="store_true", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--json", action="store_true", required=True)
    validate.add_argument("--stdin", action="store_true", required=True)
    args = parser.parse_args(args_list)
    if args.command == "contract":
        version = __import__("confflow").__version__
        _emit(
            build_configuration_contract(
                producer_version=version, producer_commit=COMMIT, producer_dirty=DIRTY
            )
        )
        return ExitCode.SUCCESS
    return _validate_stdin()
