#!/usr/bin/env python3

"""confts - TS-specific entry point (primarily provides scan keyword rewriting).

Notes
-----
- Name: confts
- The scan "method" is the same as TS (uses the same program/basis set),
  but Gaussian keywords need rule-based rewriting.

Rewriting rules (for Gaussian keyword strings):
- Inside opt(...) / opt=(...) parentheses: remove calcfc, tight, ts, noeigentest.
- If a freq keyword exists (any form: freq / freq=... / freq(...)), remove it.
- nomicro is left untouched (preserved).
- "ts" appearing outside opt() parentheses is not removed.
"""

from __future__ import annotations

import argparse

from .core.cli_base import require_existing_path
from .core.contracts import ExitCode, cli_output_to_txt
from .core.keyword_rewrite import make_scan_keyword_from_ts_keyword

__all__ = [
    "main",
]


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="confts",
        description="confts - TS executor (with TS-scan rescue support)",
    )
    parser.add_argument("input_xyz", nargs="?", help="Input XYZ file (may contain multiple frames)")
    parser.add_argument("-s", "--settings", help="INI configuration file path (same as confcalc)")
    parser.add_argument(
        "--rewrite-scan-keyword",
        metavar="KEYWORD",
        help="Output scan keyword (rewritten from TS keyword rules)",
    )

    args = parser.parse_args(argv)

    if args.rewrite_scan_keyword is not None:
        print(make_scan_keyword_from_ts_keyword(args.rewrite_scan_keyword))
        return ExitCode.SUCCESS

    if not args.input_xyz or not args.settings:
        parser.print_help()
        return ExitCode.USAGE_ERROR

    # Run as executor: equivalent to confcalc, but ts_rescue_scan can
    # also be enabled in the YAML for itask=ts.
    if args.input_xyz and args.settings:
        from . import calc

        require_existing_path(args.input_xyz, "Input file")
        require_existing_path(args.settings, "Settings file")

        with cli_output_to_txt(args.input_xyz):
            manager = calc.ChemTaskManager(settings_file=args.settings)
            # Respect YAML config instead of forcing ts_rescue_scan
            manager.run(args.input_xyz)
        return ExitCode.SUCCESS

    parser.print_help()
    return ExitCode.USAGE_ERROR


def main(args_list: list[str] | None = None):
    raise SystemExit(_cli(args_list))
