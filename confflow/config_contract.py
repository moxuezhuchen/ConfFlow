#!/usr/bin/env python3
"""Versioned workflow-configuration contract commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from importlib import resources
from typing import Any

import yaml

from . import __version__
from .config.models import WorkflowConfig

CONTRACT_SCHEMA = "confflow.config.contract.v1"
WORKFLOW_SCHEMA_VERSION = "v1"
SEMANTIC_CONTRACT_VERSION = "1.0"
_SCHEMA_RESOURCE = "workflow_config_v1.schema.json"


def _schema_bytes() -> bytes:
    resource = resources.files("confflow.config").joinpath(_SCHEMA_RESOURCE)
    return resource.read_bytes()


def workflow_schema() -> dict[str, Any]:
    value = json.loads(_schema_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("packaged workflow schema must be a JSON object")
    return value


def workflow_schema_hash() -> str:
    schema = workflow_schema()
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def contract_payload() -> dict[str, Any]:
    return {
        "response_schema": CONTRACT_SCHEMA,
        "workflow_schema": {
            "version": WORKFLOW_SCHEMA_VERSION,
            "sha256": workflow_schema_hash(),
            "resource": _SCHEMA_RESOURCE,
        },
        "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
        "producer": {
            "distribution": "confflow",
            "version": __version__,
            "configuration_contract": SEMANTIC_CONTRACT_VERSION,
        },
    }


def _issue(code: str, message: str, *, path: str = "", severity: str = "error") -> dict[str, str]:
    return {"code": code, "severity": severity, "path": path, "message": message}


def validate_mapping(raw: Any) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        issues.append(
            _issue("root_not_mapping", "workflow configuration root must be a mapping", path="$")
        )
    else:
        try:
            WorkflowConfig.from_mapping(raw)
        except (TypeError, ValueError) as exc:
            issues.append(_issue("configuration_invalid", str(exc), path="$"))
    return {**contract_payload(), "valid": not issues, "issues": issues}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="confflow config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    contract = subparsers.add_parser("contract")
    contract.add_argument("--json", action="store_true", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--json", action="store_true", required=True)
    validate.add_argument("--stdin", action="store_true", required=True)
    return parser


def main(args_list: list[str] | None = None) -> int:
    args = _parser().parse_args(args_list)
    if args.command == "contract":
        print(json.dumps(contract_payload(), sort_keys=True, indent=2))
        return 0
    try:
        raw = yaml.safe_load(sys.stdin.read())
        result = validate_mapping(raw)
    except (OSError, yaml.YAMLError, UnicodeError) as exc:
        result = {
            **contract_payload(),
            "valid": False,
            "issues": [_issue("input_invalid", str(exc), path="$")],
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["valid"] else 2


__all__ = [
    "CONTRACT_SCHEMA",
    "SEMANTIC_CONTRACT_VERSION",
    "WORKFLOW_SCHEMA_VERSION",
    "contract_payload",
    "main",
    "validate_mapping",
    "workflow_schema_hash",
]
