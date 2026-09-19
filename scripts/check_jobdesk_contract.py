#!/usr/bin/env python3

"""Check this ConfFlow checkout against a JobDesk V2 working copy.

The test suite already contains this gate
(``tests/test_jobdesk_contract_compatibility.py``), but a test run only tells you
"pass" or "fail".  This script exists for the moment a reviewer wants to *see*
the interchange: it publishes both contract versions, feeds the bytes to JobDesk's
own strict parser, and prints what the consumer concluded.

JobDesk is never a dependency.  It is located at run time from
``--jobdesk-root`` or the ``JOBDESK_V2_ROOT`` environment variable, falling back
to the checkout path this repository is developed against.

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


def run_contract_cli(version: int | None) -> bytes:
    """Publish a contract through the real CLI and return the stdout bytes."""
    args = ["config", "contract", "--json"]
    if version is not None:
        args += ["--version", str(version)]
    result = subprocess.run(
        [sys.executable, "-c", _CLI_ENTRY, *args],
        capture_output=True,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        raise CheckFailure(result.stderr.decode("utf-8", "replace"))
    if result.stderr:
        raise CheckFailure(f"the machine-readable CLI was not quiet: {result.stderr!r}")
    return result.stdout


def parse_with_jobdesk(root: pathlib.Path, payload: bytes) -> Any:
    """Feed bytes to the consumer's parser, from the located checkout."""
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from jobdesk_v2.application.editor.contract import (  # noqa: PLC0415
        FallbackArtifacts,
        parse_contract_bytes,
    )

    return parse_contract_bytes(payload, fallback=FallbackArtifacts.shipped())


def inspect(root: pathlib.Path) -> dict[str, Any]:
    """Run the whole interchange and return a report."""
    v1_bytes = run_contract_cli(None)
    v2_bytes = run_contract_cli(2)

    v1_document = json.loads(v1_bytes.decode("utf-8"))
    v2_document = json.loads(v2_bytes.decode("utf-8"))

    v1 = parse_with_jobdesk(root, v1_bytes)
    v2 = parse_with_jobdesk(root, v2_bytes)

    return {
        "jobdesk_root": str(root),
        "v1": {
            "schema": v1_document["schema"],
            "bytes": len(v1_bytes),
            "consumer_level": v1.level.value,
            "consumer_source": v1.source,
            "consumer_diagnostics": [item.code for item in v1.diagnostics],
        },
        "v2": {
            "schema": v2_document["schema"],
            "bytes": len(v2_bytes),
            "consumer_level": v2.level.value,
            "consumer_source": v2.source,
            "consumer_diagnostics": [item.code for item in v2.diagnostics],
            "contract_key": v2.contract_key,
            "field_count": len(v2.editor_manifest.field_ids()),
            "recipe_ids": sorted(v2.recipe_catalog.ids()),
            "can_edit_fields": v2.capabilities.can_edit_fields,
            "can_use_recipes": v2.capabilities.can_use_recipes,
        },
    }


def assert_interchange(report: dict[str, Any]) -> None:
    """Fail if the consumer did not accept the published contracts."""
    v1, v2 = report["v1"], report["v2"]

    if v1["consumer_level"] != "A":
        raise CheckFailure(f"v1 should grade level A, got {v1['consumer_level']!r}")
    if v2["consumer_level"] != "C":
        raise CheckFailure(f"v2 should grade level C, got {v2['consumer_level']!r}")
    if v2["consumer_source"] != "producer":
        raise CheckFailure(f"v2 should be producer-owned, got {v2['consumer_source']!r}")
    if v2["consumer_diagnostics"]:
        raise CheckFailure(f"v2 produced diagnostics: {v2['consumer_diagnostics']}")
    if not v2["can_edit_fields"] or not v2["can_use_recipes"]:
        raise CheckFailure("v2 must leave the consumer unrestricted")


def render(report: dict[str, Any]) -> str:
    """Render the report for a human."""
    lines = [f"JobDesk V2 checkout: {report['jobdesk_root']}", ""]
    for label, key in (("v1 (default)", "v1"), ("v2 (--version 2)", "v2")):
        section = report[key]
        lines.append(f"{label}")
        lines.append(f"  schema              {section['schema']}")
        lines.append(f"  stdout bytes        {section['bytes']}")
        lines.append(f"  consumer level      {section['consumer_level']}")
        lines.append(f"  consumer source     {section['consumer_source']}")
        found = section["consumer_diagnostics"] or "none"
        lines.append(f"  consumer findings   {found}")
        if key == "v2":
            lines.append(f"  contract key        {section['contract_key']}")
            lines.append(f"  fields published    {section['field_count']}")
            lines.append(f"  recipes published   {', '.join(section['recipe_ids'])}")
            lines.append(f"  can edit fields     {section['can_edit_fields']}")
            lines.append(f"  can use recipes     {section['can_use_recipes']}")
        lines.append("")
    lines.append("The consumer accepted the producer contract.")
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
