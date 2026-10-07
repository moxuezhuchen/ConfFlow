#!/usr/bin/env python3

"""V4 production command surface.

Formal `confflow v4 ...` runtime path; never falls back to legacy engine: unknown/legacy
workflows fail closed with `legacy_workflow_not_executable` plus migration-required message.
Commands: contract/boundary/canonical/validate/authoring/run (whole-workflow run).
Canonicalize JSON via RFC 8785 (JCS); duplicate keys and non-finite numbers rejected structurally.
Machine output is JSON on stdout, logs on stderr; exit 0 success/valid, 1 structured
failure/invalid/failed run, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = [
    "main",
]


def main(argv: list[str] | None = None) -> int:
    """Run the V4 command surface; return the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            return _contract(args)
        if args.command == "boundary":
            return _boundary(args)
        if args.command == "canonical":
            return _canonical(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "authoring":
            return _authoring(args)
        if args.command == "run":
            return _run(args)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    parser.error("unknown v4 command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    """Build the V4 argument parser."""
    parser = argparse.ArgumentParser(prog="confflow v4")
    subparsers = parser.add_subparsers(dest="command", required=True)
    contract = subparsers.add_parser("contract", help="Print the V4 producer contract")
    contract.add_argument("--json", action="store_true", required=True)
    boundary = subparsers.add_parser("boundary", help="Print the P0 boundary protocol document")
    boundary.add_argument("--json", action="store_true", required=True)
    canonical = subparsers.add_parser("canonical", help="Canonicalize JSON bytes (RFC 8785 / JCS)")
    canonical.add_argument("--json", action="store_true", required=True)
    canonical.add_argument(
        "--stdin", action="store_true", required=True, help="Read JSON bytes from stdin"
    )
    validate = subparsers.add_parser("validate", help="Validate V4 workflow bytes")
    validate.add_argument("--json", action="store_true", required=True)
    source = validate.add_mutually_exclusive_group(required=True)
    source.add_argument("--workflow", help="Workflow document file")
    source.add_argument("--stdin", action="store_true", help="Read workflow bytes from stdin")
    authoring = subparsers.add_parser("authoring", help="Dispatch a confflow.authoring.v4 request")
    authoring.add_argument("--json", action="store_true", required=True)
    authoring.add_argument(
        "--stdin", action="store_true", required=True, help="Read request JSON from stdin"
    )
    run = subparsers.add_parser("run", help="Run a whole V4 workflow")
    run.add_argument("--workflow", required=True, help="Workflow document file")
    run.add_argument(
        "--inputs",
        action="append",
        default=[],
        metavar="NAME=FILE",
        help="Run input NAME from XYZ file FILE (repeatable)",
    )
    run.add_argument("--run-root", required=True, help="Managed run root directory")
    run.add_argument(
        "--executable",
        action="append",
        default=[],
        metavar="PROG=PATH",
        help="Native executable for program PROG (repeatable)",
    )
    run.add_argument("--owner-token", default="v4-cli", help="Claim owner token")
    run.add_argument("--json", action="store_true", help="Machine JSON report on stdout")
    return parser


def _contract(args: argparse.Namespace) -> int:
    """Print the V4 producer contract bytes."""
    import warnings

    import confflow

    from .producer.contract import generate_contract_bytes

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        payload = generate_contract_bytes(producer_version=confflow.__version__)
    sys.stdout.write(payload.decode("utf-8"))
    sys.stdout.write("\n")
    return 0


def _read_workflow_bytes(args: argparse.Namespace) -> bytes:
    """Read the exact workflow bytes under validation."""
    if args.stdin:
        return sys.stdin.buffer.read()
    with open(args.workflow, "rb") as handle:
        return handle.read()


def _boundary(args: argparse.Namespace) -> int:
    """Print the full P0 boundary protocol document."""
    import warnings

    import confflow

    from .producer.contract import build_boundary_document

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        document = build_boundary_document(producer_version=confflow.__version__)
    sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True))
    sys.stdout.write("\n")
    return 0


def _canonical(args: argparse.Namespace) -> int:
    """Canonicalize JSON stdin and print the producer's canonical form."""
    from .producer.boundary import CANONICALIZATION_ID, canonicalize_text

    data = sys.stdin.buffer.read()
    outcome = canonicalize_text(data)
    if outcome["ok"]:
        payload = {
            "ok": True,
            "canonicalization_id": CANONICALIZATION_ID,
            "canonical_json": outcome["canonical_json"],
            "canonical_sha256": outcome["canonical_sha256"],
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        sys.stdout.write("\n")
        return 0
    payload = {
        "ok": False,
        "canonicalization_id": CANONICALIZATION_ID,
        "reason_code": outcome["reason_code"],
        "detail": outcome["detail"],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    sys.stdout.write("\n")
    return 1


def _validate(args: argparse.Namespace) -> int:
    """Validate workflow bytes and print the validation report."""
    from .producer.validation import validate_workflow_bytes

    data = _read_workflow_bytes(args)
    report = validate_workflow_bytes(data)
    payload = report.to_dict()
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0 if report.ok else 1


def _authoring(args: argparse.Namespace) -> int:
    """Dispatch one authoring request and print the response envelope."""
    from .producer.authoring import dispatch_request

    data = sys.stdin.buffer.read()
    envelope = dispatch_request(data)
    sys.stdout.write(json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True))
    sys.stdout.write("\n")
    return 0 if envelope.get("ok") else 1


def _run(args: argparse.Namespace) -> int:
    """Run a whole V4 workflow and print the report."""
    from .application.v4_entry import require_v4_document
    from .application.v4_run import V4RunApplication, V4RunRequest, import_xyz
    from .core.exceptions import ConfFlowError
    from .domain._immutable import FrozenDict
    from .domain.errors import DomainError
    from .execution.process import NativeProcessSupervisor
    from .workflow.v4.assembly import RunInputs

    with open(args.workflow, encoding="utf-8") as handle:
        import yaml

        document = yaml.safe_load(handle)
    # Single version-discriminator authority: a V1/V2/V3 (or unknown) schema
    # fails closed with ``legacy_workflow_not_executable`` plus the stable
    # ``unsupported_workflow_version`` code for a retired id, before any run
    # root, store or process side effect.
    try:
        require_v4_document(document)
    except ConfFlowError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    structures: dict[str, Any] = {}
    for assignment in args.inputs:
        name, _, path = assignment.partition("=")
        if not name or not path:
            raise ValueError(f"--inputs entry must be NAME=FILE, got {assignment!r}")
        with open(path, encoding="utf-8") as handle:
            structures[name] = import_xyz(handle.read(), source_name=path)
    executables: dict[str, str] = {}
    for assignment in args.executable:
        program, _, path = assignment.partition("=")
        if not program or not path:
            raise ValueError(f"--executable entry must be PROG=PATH, got {assignment!r}")
        executables[program] = path
    try:
        report = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
            V4RunRequest(
                workflow_document=document,
                run_inputs=RunInputs(structures=FrozenDict(structures)),
                run_root=args.run_root,
                owner_token=args.owner_token,
                executables=FrozenDict(executables),
            )
        )
    except DomainError as exc:
        return _report_run_failure(args, exc)
    payload = {
        "run_id": report.run_id,
        "status": report.status,
        "definition_digest": report.definition_digest,
        "steps": [
            {"id": result.step_id, "status": result.status.value} for result in report.step_results
        ],
        "manifest": report.manifest.thaw(),
    }
    if report.status != "completed":
        payload["failures"] = _step_failures(payload["manifest"])
    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")
    else:
        print(f"run {report.run_id}: {report.status}", file=sys.stderr)
    return 0 if report.status == "completed" else 1


def _report_run_failure(args: argparse.Namespace, exc: Exception) -> int:
    """Report a run that failed with a domain error as a structured failure."""
    import os

    run_id = os.path.basename(str(args.run_root).rstrip(os.sep)) or "run"
    manifest: dict[str, Any] = {}
    try:
        with open(os.path.join(str(args.run_root), "run_result.json"), encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            manifest = loaded
    except (OSError, ValueError):
        manifest = {}
    steps = [
        {"id": item.get("id"), "status": item.get("status")}
        for item in manifest.get("steps", ())
        if isinstance(item, dict)
    ]
    payload = {
        "run_id": run_id,
        "status": "failed",
        "definition_digest": manifest.get("definition_digest"),
        "steps": steps,
        "failures": [
            {
                "step_id": None,
                "code": "run_not_executable",
                "message": str(exc),
            },
            *_step_failures(manifest),
        ],
        "manifest": manifest,
    }
    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
        sys.stdout.write("\n")
    else:
        print(f"Error: {exc}", file=sys.stderr)
    return 1


def _step_failures(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """List the error diagnostics of the failed steps of a run manifest."""
    failures: list[dict[str, Any]] = []
    for step in manifest.get("steps", ()):
        if not isinstance(step, dict) or step.get("status") != "failed":
            continue
        for item in step.get("diagnostics", ()):
            if isinstance(item, dict) and item.get("severity") == "error":
                failures.append(
                    {
                        "step_id": step.get("id"),
                        "code": item.get("code"),
                        "message": item.get("message"),
                    }
                )
    return failures
