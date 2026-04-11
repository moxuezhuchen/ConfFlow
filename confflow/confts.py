#!/usr/bin/env python3

"""Provide the TS-specific CLI entry point and scan-keyword rewrite helper."""

from __future__ import annotations

import argparse
import importlib

from .core.cli_base import require_existing_path
from .core.contracts import ExitCode, cli_output_to_txt
from .core.keyword_rewrite import make_scan_keyword_from_ts_keyword

__all__ = [
    "main",
]


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="confts",
        description="Run TS calculations with scan-keyword rewrite support",
    )
    parser.add_argument("input_xyz", nargs="?", help="Path to the input XYZ file")
    parser.add_argument("-s", "--settings", help="Path to the INI settings file")
    parser.add_argument(
        "--rewrite-scan-keyword",
        metavar="KEYWORD",
        help="Print the scan keyword rewritten from the TS keyword rules",
    )

    args = parser.parse_args(argv)

    if args.rewrite_scan_keyword is not None:
        print(make_scan_keyword_from_ts_keyword(args.rewrite_scan_keyword))
        return ExitCode.SUCCESS

    if not args.input_xyz or not args.settings:
        parser.print_help()
        return ExitCode.USAGE_ERROR

    # Run as a calc executor. YAML can still enable ts_rescue_scan for TS tasks.
    if args.input_xyz and args.settings:
        calc = importlib.import_module("confflow.calc")

        require_existing_path(args.input_xyz, "Input file")
        require_existing_path(args.settings, "Settings file")

        with cli_output_to_txt(args.input_xyz):
            manager = calc.ChemTaskManager(settings_file=args.settings)
            # Respect the YAML configuration instead of forcing ts_rescue_scan.
            manager.run(args.input_xyz)
        return ExitCode.SUCCESS

    parser.print_help()
    return ExitCode.USAGE_ERROR


def main(args_list: list[str] | None = None):
    raise SystemExit(_cli(args_list))
