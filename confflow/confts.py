#!/usr/bin/env python3

"""Provide the TS-specific CLI entry point and scan-keyword rewrite helper."""

from __future__ import annotations

import argparse
import sys

from .calc.runner import CalcStepRequest, CalcStepRunner
from .config.models import CalcStepParams, load_workflow_model
from .core.cli_base import require_existing_path
from .core.contracts import ExitCode, cli_output_to_txt
from .core.exceptions import ConfFlowError
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
    parser.add_argument("-c", "--config", help="Path to the workflow YAML configuration file")
    parser.add_argument("--step", help="TS calc step name or 1-based calc-step index")
    parser.add_argument("-w", "--work-dir", help="Output step directory")
    parser.add_argument(
        "--rewrite-scan-keyword",
        metavar="KEYWORD",
        help="Print the scan keyword rewritten from the TS keyword rules",
    )

    args = parser.parse_args(argv)

    if args.rewrite_scan_keyword is not None:
        print(make_scan_keyword_from_ts_keyword(args.rewrite_scan_keyword))
        return ExitCode.SUCCESS

    if not args.input_xyz or not args.config:
        parser.print_help()
        return ExitCode.USAGE_ERROR

    require_existing_path(args.input_xyz, "Input file")
    require_existing_path(args.config, "Config file")

    try:
        with cli_output_to_txt(args.input_xyz):
            workflow = load_workflow_model(args.config)
            calc_steps = [step for step in workflow.steps if step.type == "calc"]
            ts_steps = [
                step
                for step in calc_steps
                if str(step.params.get("itask", workflow.global_options.itask)).lower()
                in {"4", "ts"}
            ]
            candidates = ts_steps or calc_steps
            if not candidates:
                print("Error: no calc step found in workflow config", file=sys.stderr)
                return ExitCode.USAGE_ERROR

            selected = candidates[0]
            if args.step:
                raw = str(args.step).strip()
                if raw.isdigit():
                    idx = int(raw)
                    if idx < 1 or idx > len(candidates):
                        print(f"Error: step index out of range: {idx}", file=sys.stderr)
                        return ExitCode.USAGE_ERROR
                    selected = candidates[idx - 1]
                else:
                    matches = [step for step in candidates if step.name == raw]
                    if not matches:
                        print(f"Error: step not found: {raw}", file=sys.stderr)
                        return ExitCode.USAGE_ERROR
                    selected = matches[0]

            config = CalcStepParams.from_params(selected.params, workflow.global_options)
            step_dir = args.work_dir or f"{args.input_xyz}_ts_{selected.name}"
            result = CalcStepRunner().run(
                CalcStepRequest(
                    step_name=selected.name,
                    step_dir=step_dir,
                    input_xyz=args.input_xyz,
                    config=config,
                )
            )
            if result.failed == result.total_tasks and result.total_tasks > 0:
                print("All TS calculation tasks failed", file=sys.stderr)
                return ExitCode.RUNTIME_ERROR
    except (ConfFlowError, OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return ExitCode.RUNTIME_ERROR
    return ExitCode.SUCCESS


def main(args_list: list[str] | None = None):
    raise SystemExit(_cli(args_list))
