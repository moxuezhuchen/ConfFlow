"""Machine-readable configuration-contract command handlers.

``confflow config contract --json`` emits ``confflow.configuration-contract.v1``
by default; ``--version 2`` emits the same document plus the editor manifest and
the recipe catalog. The default is v1 on purpose: the v1 document has a published
shape and existing consumers parse it, so a version bump has to be asked for.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..__build__ import COMMIT, DIRTY
from ..core.contracts import ExitCode
from .canonical import (
    CONFIGURATION_CONTRACT_BUILDERS,
    CONFIGURATION_VALIDATION_SCHEMA,
    WORKFLOW_SCHEMA_VERSION_V2,
    WORKFLOW_SCHEMA_VERSION_V3,
    build_configuration_contract_for_version,
    detect_schema_version,
    validate_workflow_definition,
    workflow_schema_sha256,
    workflow_schema_sha256_v3,
)

#: The contract version emitted when ``--version`` is not given. Kept as a named
#: constant so the default and its rationale are not buried in the argparse call.
#: v1 stays the default for old-consumer compatibility; v3 is opt-in.
DEFAULT_CONTRACT_VERSION = 1


def _validation_response_digest(raw: dict[str, Any] | None) -> str:
    """Return the digest of the schema that validates ``raw``.

    The frozen ``configuration-validation.v1`` wire shape carries one digest
    member; it must name the schema version that actually judged the document —
    the V2 digest for the historical default, the V3 DOCUMENT digest for V3. An
    unrecognisable document has no version, so it keeps the historical V2
    digest; the response is invalid either way and downstream consumers refuse
    it on ``valid``/digest, never on shape.
    """
    if raw is None:
        return workflow_schema_sha256()
    try:
        version = detect_schema_version(raw)
    except Exception:  # noqa: BLE001 - any recognition failure keeps the default digest
        return workflow_schema_sha256()
    if version == WORKFLOW_SCHEMA_VERSION_V3:
        return workflow_schema_sha256_v3()
    assert version == WORKFLOW_SCHEMA_VERSION_V2  # recognition is closed-world
    return workflow_schema_sha256()


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
    response_digest = _validation_response_digest(raw)
    # Definition validation only: it examines the document, never the run
    # context (input files, executables on this host), which stdin does not
    # carry. The internal diagnostics are projected to the frozen
    # ``configuration-validation.v1`` issue shape (exactly ``path``/``message``),
    # which is what the JobDesk consumer parses without extra members.
    errors = [diagnostic for diagnostic in validate_workflow_definition(raw) if diagnostic.is_error]
    if errors:
        _emit(
            {
                "schema": CONFIGURATION_VALIDATION_SCHEMA,
                "valid": False,
                "workflow_schema_sha256": response_digest,
                "issues": [diagnostic.to_v1_issue() for diagnostic in errors],
            }
        )
        return ExitCode.USAGE_ERROR
    _emit(
        {
            "schema": CONFIGURATION_VALIDATION_SCHEMA,
            "valid": True,
            "workflow_schema_sha256": response_digest,
            "issues": [],
        }
    )
    return ExitCode.SUCCESS


def main(args_list: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="confflow config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    contract = subparsers.add_parser("contract")
    contract.add_argument("--json", action="store_true", required=True)
    contract.add_argument(
        "--version",
        type=int,
        choices=sorted(CONFIGURATION_CONTRACT_BUILDERS),
        default=DEFAULT_CONTRACT_VERSION,
        help=(
            "Configuration contract version to emit. Defaults to "
            f"{DEFAULT_CONTRACT_VERSION}, which is the document consumers already "
            "parse; version 2 additionally carries the editor manifest and the "
            "recipe catalog."
        ),
    )
    validate = subparsers.add_parser("validate")
    validate.add_argument("--json", action="store_true", required=True)
    validate.add_argument("--stdin", action="store_true", required=True)
    args = parser.parse_args(args_list)
    if args.command == "contract":
        producer_version = __import__("confflow").__version__
        _emit(
            build_configuration_contract_for_version(
                args.version,
                producer_version=producer_version,
                producer_commit=COMMIT,
                producer_dirty=DIRTY,
            )
        )
        return ExitCode.SUCCESS
    return _validate_stdin()
