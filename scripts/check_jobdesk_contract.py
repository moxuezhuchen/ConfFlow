#!/usr/bin/env python3

"""Check this ConfFlow checkout against a JobDesk V2 working copy.

Architecture Diet PR-9 retired the released V1/V2 configuration wire, so this
interchange is now a **V4-only** check: it publishes the one current producer
contract (``confflow v4 contract --json`` / ``confflow config contract --json``,
both emitting ``confflow.configuration-contract.v4``), feeds the bytes to
JobDesk's own strict V4 parser, and prints what the consumer concluded.

Two boundaries are checked, and both are reported honestly:

``producer -> JobDesk V4``
    the current boundary.  JobDesk's V4 parser
    (``jobdesk_v2.application.editor.contract.v4.parse_v4_contract_bytes``)
    must accept the producer bytes, re-verify every published digest and
    report ``is_v4_capable``.

``retired producer argv``
    ``confflow config contract --json --version 1|2|3`` must fail closed with
    the stable ``unsupported_workflow_version`` code and emit nothing.  A
    consumer that still asks for one of those (JobDesk's legacy V2 editor
    contract fetch does) therefore degrades to its own fallback until the
    consumer moves to the V4 parser.  This script reports that as
    ``consumer_change_required`` instead of pretending compatibility.

JobDesk is never a dependency.  It is located at run time from
``--jobdesk-root`` or the ``JOBDESK_V2_ROOT`` environment variable, falling
back to the checkout path this repository is developed against.

Usage::

    python scripts/check_jobdesk_contract.py
    python scripts/check_jobdesk_contract.py --jobdesk-root /path/to/jobdesk-v2
    python scripts/check_jobdesk_contract.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_JOBDESK_ROOT = pathlib.Path("/mnt/c/dft/tool/jobdesk-v2")
JOBDESK_ENV_VAR = "JOBDESK_V2_ROOT"

_CLI_ENTRY = "import sys; from confflow.main import main; sys.exit(main())"

#: The retired contract versions a consumer might still ask for.
RETIRED_CONTRACT_VERSIONS = (1, 2, 3)


class CheckFailure(RuntimeError):
    """Raised when the interchange does not hold."""


def locate_jobdesk(explicit: str | None) -> pathlib.Path:
    """Return a usable JobDesk V2 root, or explain why there is not one."""
    candidates = []
    if explicit:
        candidates.append(pathlib.Path(explicit))
    elif os.environ.get(JOBDESK_ENV_VAR):
        candidates.append(pathlib.Path(os.environ[JOBDESK_ENV_VAR]))
    else:
        candidates.append(DEFAULT_JOBDESK_ROOT)

    for candidate in candidates:
        if (candidate / "src" / "jobdesk_v2").is_dir():
            return candidate
    raise CheckFailure(
        "no JobDesk V2 checkout found; pass --jobdesk-root or set "
        f"{JOBDESK_ENV_VAR} to a jobdesk-v2 working copy"
    )


def run_cli(*args: str, expect_success: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Run the machine CLI and return the completed process."""
    result = subprocess.run(
        [sys.executable, "-c", _CLI_ENTRY, *args],
        capture_output=True,
        cwd=str(REPO_ROOT),
    )
    if expect_success and result.returncode != 0:
        raise CheckFailure(result.stderr.decode("utf-8", "replace"))
    return result


def publish_current_contract() -> tuple[bytes, bytes]:
    """Return ``(v4_cli_bytes, config_cli_bytes)`` for the current wire."""
    v4 = run_cli("v4", "contract", "--json")
    config = run_cli("config", "contract", "--json")
    if v4.stderr or config.stderr:
        raise CheckFailure(
            f"the machine-readable CLI was not quiet: {sorted({v4.stderr, config.stderr})!r}"
        )
    if v4.stdout != config.stdout:
        raise CheckFailure(
            "the two current contract routes disagree; "
            "'confflow config contract --json' must emit the V4 envelope"
        )
    return v4.stdout, config.stdout


def check_retired_versions() -> dict[str, Any]:
    """Every retired contract version fails closed with the stable code."""
    report: dict[str, Any] = {}
    for version in RETIRED_CONTRACT_VERSIONS:
        result = run_cli(
            "config", "contract", "--json", "--version", str(version), expect_success=False
        )
        stderr = result.stderr.decode("utf-8", "replace")
        report[str(version)] = {
            "returncode": result.returncode,
            "stdout_bytes": len(result.stdout),
            "stable_code_present": "unsupported_workflow_version" in stderr,
        }
        if result.returncode == 0 or result.stdout:
            raise CheckFailure(f"contract version {version} must fail closed and emit nothing")
        if "unsupported_workflow_version" not in stderr:
            raise CheckFailure(f"contract version {version} lost its stable rejection code")
    return report


def parse_with_jobdesk_v4(root: pathlib.Path, payload: bytes) -> Any:
    """Feed bytes to the consumer's V4 parser, from the located checkout."""
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from jobdesk_v2.application.editor.contract.v4 import (  # noqa: PLC0415
        V4_CONTRACT_SCHEMA,
        V4_WORKFLOW_SCHEMA_ID,
        parse_v4_contract_bytes,
    )

    contract = parse_v4_contract_bytes(payload)
    if contract.content_schema != V4_CONTRACT_SCHEMA:  # pragma: no cover - defensive
        raise CheckFailure(f"jobdesk parsed a non-V4 envelope: {contract.content_schema!r}")
    if contract.workflow_schema_id != V4_WORKFLOW_SCHEMA_ID:  # pragma: no cover - defensive
        raise CheckFailure(
            f"jobdesk parsed the wrong workflow line: {contract.workflow_schema_id!r}"
        )
    return contract


def inspect(root: pathlib.Path) -> dict[str, Any]:
    """Run the whole interchange and return a report."""
    contract_bytes, config_bytes = publish_current_contract()
    document = json.loads(contract_bytes.decode("utf-8"))
    contract = parse_with_jobdesk_v4(root, contract_bytes)

    return {
        "jobdesk_root": str(root),
        "current_contract": {
            "schema": document["content_schema"],
            "workflow_schema_id": document["workflow_schema_id"],
            "bytes": len(contract_bytes),
            "config_route_bytes": len(config_bytes),
            "consumer_schema": contract.content_schema,
            "consumer_workflow_schema_id": contract.workflow_schema_id,
            "consumer_v4_capable": contract.is_v4_capable,
            "consumer_contract_digest": contract.contract_digest,
            "same_bytes_on_both_routes": True,
        },
        "retired_contract_versions": check_retired_versions(),
        "consumer_change_required": {
            "required": True,
            "surface": "jobdesk_v2.application.editor.contract (legacy V2 editor contract fetch)",
            "producer_argv": "config contract --json --version 2",
            "replacement": "v4 contract --json (parsed by jobdesk_v2.application.editor.contract.v4)",
            "note": (
                "the retired argv now fails closed with unsupported_workflow_version, so the "
                "legacy V2 editor fetch degrades to its stable fallback until the consumer "
                "reads the V4 envelope"
            ),
        },
    }


def assert_interchange(report: dict[str, Any]) -> None:
    """Fail if the current wire did not hold or a retired one did not fail."""
    current = report["current_contract"]
    if current["schema"] != "confflow.configuration-contract.v4":
        raise CheckFailure(f"unexpected contract schema: {current['schema']!r}")
    if current["workflow_schema_id"] != "confflow.workflow.v4":
        raise CheckFailure(f"unexpected workflow schema id: {current['workflow_schema_id']!r}")
    if current["consumer_schema"] != current["schema"]:
        raise CheckFailure("the consumer did not accept the producer's V4 envelope")
    if not current["consumer_v4_capable"]:
        raise CheckFailure("the consumer did not report V4 capability")
    for version, section in report["retired_contract_versions"].items():
        if (
            section["returncode"] == 0
            or section["stdout_bytes"]
            or not section["stable_code_present"]
        ):
            raise CheckFailure(f"contract version {version} is not retired cleanly: {section!r}")


def render(report: dict[str, Any]) -> str:
    """Render the report for a human."""
    current = report["current_contract"]
    lines = [f"JobDesk V2 checkout: {report['jobdesk_root']}", ""]
    lines.append("current producer -> JobDesk V4")
    lines.append(f"  schema                 {current['schema']}")
    lines.append(f"  workflow schema id     {current['workflow_schema_id']}")
    lines.append(f"  stdout bytes           {current['bytes']}")
    lines.append(f"  config route bytes     {current['config_route_bytes']}")
    lines.append(f"  consumer schema        {current['consumer_schema']}")
    lines.append(f"  consumer v4 capable    {current['consumer_v4_capable']}")
    lines.append("")
    lines.append("retired producer argv")
    for version, section in sorted(report["retired_contract_versions"].items()):
        lines.append(
            f"  --version {version}        rc={section['returncode']} "
            f"stdout={section['stdout_bytes']}B stable_code={section['stable_code_present']}"
        )
    lines.append("")
    change = report["consumer_change_required"]
    lines.append(f"consumer change required: {change['required']}")
    lines.append(f"  surface      {change['surface']}")
    lines.append(f"  producer     {change['producer_argv']} (retired)")
    lines.append(f"  replacement  {change['replacement']}")
    lines.append("")
    lines.append(
        "The consumer accepted the current producer contract; the retired wire failed closed."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobdesk-root", default=None, help="path to a jobdesk-v2 checkout")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    try:
        root = locate_jobdesk(args.jobdesk_root)
        report = inspect(root)
        assert_interchange(report)
    except CheckFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
