#!/usr/bin/env python3
"""Machine-readable configuration-contract command handlers (V4 only).

The only supported configuration wire is V4.  ``confflow config contract
--json`` emits ``confflow.configuration-contract.v4`` -- the same canonical
envelope ``confflow v4 contract --json`` emits -- and ``--version 4`` names it
explicitly.

The released V1 (``confflow.configuration-contract.v1``) and V2
(``…-contract.v2``) documents, and the never-released V3 wire, were retired by
the Architecture Diet (PR-7 retired V3, PR-9 retired V1/V2).  Asking for any
of them now fails closed with the stable ``unsupported_workflow_version``
code: no fallback to another version, no automatic upgrade, no parsing of the
retired document, and no execution.

The historical ``config validate --stdin`` handler validated the released V2
document shape.  It has no V4 successor here on purpose: the producer-owned V4
validator is reached through ``confflow v4 validate`` (or the
``confflow.producer.validation`` module boundary JobDesk already uses), so a
second V4 validation surface cannot drift from it.  The retired handler keeps
its route and fails closed.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from ..core.contracts import ExitCode

#: The configuration-contract version emitted when ``--version`` is not given.
#: V4 is the only current configuration wire, so it is also the only default.
DEFAULT_CONTRACT_VERSION = 4

#: Every contract version this CLI will emit. ``--version 1`` / ``2`` / ``3``
#: and every unknown value are explicitly unsupported.
SUPPORTED_CONTRACT_VERSIONS: tuple[int, ...] = (4,)

#: Stable fail-closed vocabulary for a retired or unknown version.
UNSUPPORTED_WORKFLOW_VERSION = "unsupported_workflow_version"

#: Retail explanations, keyed by the retired version a caller asked for.
_RETIRED_VERSIONS: dict[int, str] = {
    1: "the released V1 configuration wire was retired by the Architecture Diet PR-9",
    2: "the released V2 configuration wire was retired by the Architecture Diet PR-9",
    3: "the never-released V3 configuration wire was retired by the Architecture Diet PR-7",
}


def _unsupported_version_error(version: Any) -> ValueError:
    """Build the stable, side-effect-free rejection for *version*."""
    reason = _RETIRED_VERSIONS.get(version) if isinstance(version, int) else None
    detail = f" ({reason})" if reason else ""
    return ValueError(
        f"{UNSUPPORTED_WORKFLOW_VERSION}: unsupported configuration contract "
        f"version {version!r}{detail}; supported: "
        f"{', '.join(str(item) for item in SUPPORTED_CONTRACT_VERSIONS)} "
        "(confflow.configuration-contract.v4). Use "
        "'confflow config contract --json' or 'confflow v4 contract --json'."
    )


def build_contract_document(version: int) -> dict[str, Any]:
    """Build the canonical contract envelope for *version*, or fail closed.

    The envelope is built with no build-time provenance: the V4 wire has a
    single published reference envelope (``confflow v4 contract --json``, whose
    ``producer.commit`` / ``producer.dirty`` are unknown in a source checkout),
    and ``config contract`` must emit exactly those bytes on every route and in
    every install.  ``build_configuration_contract_v4`` still accepts provenance
    for callers that own a released build.
    """
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise _unsupported_version_error(version)
    import warnings

    from ..producer.contract import build_configuration_contract_v4

    producer_version = __import__("confflow").__version__
    # Machine-readable stdout must stay the only output: the producer's own
    # envelope is imported here for the first time on this route, and
    # importing it defines pydantic models whose field-shadowing warnings are
    # not part of the contract.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return build_configuration_contract_v4(producer_version=producer_version)


def main(args_list: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="confflow config")
    subparsers = parser.add_subparsers(dest="command", required=True)
    contract = subparsers.add_parser("contract")
    contract.add_argument("--json", action="store_true", required=True)
    contract.add_argument(
        "--version",
        type=int,
        default=DEFAULT_CONTRACT_VERSION,
        help=(
            "Configuration contract version to emit. V4 "
            "(confflow.configuration-contract.v4) is the only current wire and "
            f"the default ({DEFAULT_CONTRACT_VERSION}); versions 1, 2 and 3 are "
            "retired and fail closed."
        ),
    )
    validate = subparsers.add_parser(
        "validate",
        help="Retired: the released V2 document validator; use 'confflow v4 validate'",
    )
    validate.add_argument("--json", action="store_true", required=True)
    validate.add_argument("--stdin", action="store_true", required=True)
    args = parser.parse_args(args_list)
    if args.command == "contract":
        try:
            document = build_contract_document(args.version)
        except ValueError as error:
            print(f"Error: {error}", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        from ..producer.contract import contract_canonical_json

        # Canonical machine output: one document on stdout, nothing else.
        sys.stdout.write(contract_canonical_json(document))
        sys.stdout.write("\n")
        return ExitCode.SUCCESS
    print(
        "Error: "
        f"{UNSUPPORTED_WORKFLOW_VERSION}: 'confflow config validate' validated the "
        "released V2 workflow document, which is retired by the Architecture Diet "
        "PR-9; the only supported workflow format is 'confflow.workflow.v4'. Use "
        "'confflow v4 validate --workflow FILE --json' (or --stdin).",
        file=sys.stderr,
    )
    return ExitCode.USAGE_ERROR


__all__ = [
    "DEFAULT_CONTRACT_VERSION",
    "SUPPORTED_CONTRACT_VERSIONS",
    "UNSUPPORTED_WORKFLOW_VERSION",
    "build_contract_document",
    "main",
]
