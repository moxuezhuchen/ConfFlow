#!/usr/bin/env python3

"""ConfFlow CLI entrypoint (without business logic)."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

import yaml

from .__build__ import COMMIT, DIRTY
from .agent.cli import main as agent_main
from .application.execution.workflow_adapter import run_workflow_through_service
from .contract import (
    CAPABILITY_SCHEMA_VERSION,
    OUTPUT_MANIFEST_FILE,
    REQUIRED_COMMANDS,
    RUN_MIN_XYZ_TEMPLATE,
    RUN_REPORT_FILE,
    RUN_SUMMARY_FILE,
    WORKFLOW_STATE_FILE,
    WORKFLOW_STATS_FILE,
)
from .core.contracts import ExitCode, cli_output_to_txt, output_txt_path_for_input
from .core.exceptions import ConfigurationError, InputFileError, PathSafetyError, XYZFormatError
from .core.io import parse_gaussian_input_text, write_xyz_file
from .core.path_policy import resolve_sandbox_root, validate_managed_path
from .core.utils import get_logger
from .install_provenance import read_install_provenance
from .workflow.dry_run import run_dry_run
from .workflow.engine import run_workflow
from .workflow.export import NoExportableResultsError, export_results
from .workflow.rerun_failed import (
    RerunFailedRuntimeError,
    RerunFailedUsageError,
    run_rerun_failed,
)

# Package initialization suppresses import-time warnings for the real probes.
_HANDSHAKE_PROBE = any(flag in sys.argv[1:] for flag in ("--version", "--capabilities"))

# Capability-contract constants (JobDesk <-> ConfFlow handshake).
# The producer-side single source of truth lives in ``confflow.contract``;
# this module must import from there so the CLI payload and the artifacts
# it advertises can never drift apart.
_CAPABILITY_SCHEMA_VERSION: int = CAPABILITY_SCHEMA_VERSION


def _resolved_confflow_executable(executable_override: str | None = None) -> str | None:
    """Return the entry point belonging to the interpreter running this probe.

    A capability probe may be invoked with an absolute path while another
    ConfFlow shim remains earlier on ``PATH``. Prefer the console script next
    to ``sys.executable`` so the reported executable cannot silently describe
    a different venv; retain argv/PATH fallbacks for legacy module launchers.
    """
    if executable_override is not None:
        candidate = Path(executable_override)
        if candidate.is_file():
            return os.path.realpath(os.path.abspath(os.fspath(candidate)))
        return None

    candidates = [Path(sys.executable).with_name("confflow"), Path(sys.argv[0])]
    path_executable = shutil.which("confflow")
    if path_executable:
        candidates.append(Path(path_executable))
    for candidate in candidates:
        candidate_text = os.fspath(candidate)
        if candidate_text and os.path.isfile(candidate_text):
            return os.path.realpath(os.path.abspath(candidate_text))
    return None


def _file_sha256(path: str | None) -> str | None:
    """Return a SHA-256 digest for an executable when it is a regular file."""
    if path is None or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_device_inode(path: str | None) -> str | None:
    """Return the device/inode binding for an executable regular file."""
    if path is None or not os.path.isfile(path):
        return None
    try:
        metadata = os.stat(path)
    except OSError:
        return None
    return f"{metadata.st_dev}:{metadata.st_ino}"


def _build_capability_payload(executable_override: str | None = None) -> dict[str, Any]:
    """Build the handshake payload with v3 compatibility and v4 provenance.

    v4 adds ``producer.install_provenance`` as the source of truth for
    the wheel filename and final SHA-256. When the file at
    ``<sys.prefix>/share/confflow/install-provenance.json`` is missing
    or invalid the payload surfaces the v4 diagnostic shape
    (``wheel.filename/sha256 = null`` plus ``status`` /
    ``reason_code``); JobDesk production gates must reject any
    non-``verified`` status. ConfFlow never emits the literal string
    ``"unbound"`` for either field.
    """
    executable = _resolved_confflow_executable(executable_override)
    build = {"commit": COMMIT, "dirty": DIRTY}
    version = __import__("confflow").__version__
    provenance, _errors = read_install_provenance()
    return {
        "schema_version": _CAPABILITY_SCHEMA_VERSION,
        "version": version,
        "capabilities": {
            "workflow_state": True,
            "resume": True,
            "dag": True,
            # The external worker relies on POSIX O_DIRECTORY/O_NOFOLLOW
            # state-root primitives. Keep the base package cross-platform,
            # but advertise the worker only where its fail-closed path
            # contract can actually run.
            "control_worker": (
                os.name == "posix"
                and hasattr(os, "O_DIRECTORY")
                and hasattr(os, "O_NOFOLLOW")
            ),
        },
        "artifacts": {
            "run_summary": RUN_SUMMARY_FILE,
            "workflow_stats": WORKFLOW_STATS_FILE,
            "workflow_state": WORKFLOW_STATE_FILE,
            "run_report": RUN_REPORT_FILE,
            "min_xyz": RUN_MIN_XYZ_TEMPLATE,
            "output_manifest": OUTPUT_MANIFEST_FILE,
        },
        "commands": {name: shutil.which(name) is not None for name in REQUIRED_COMMANDS},
        "build": build,
        "producer": {
            "package": "confflow",
            "version": version,
            "build": build,
            "wheel": {
                "filename": provenance.wheel_filename,
                "sha256": provenance.wheel_sha256,
            },
            "install_provenance": {
                "status": provenance.status,
                "reason_code": provenance.reason_code,
            },
        },
        "executable": {
            "path": executable,
            "realpath": executable,
            "device_inode": _file_device_inode(executable),
            "sha256": _file_sha256(executable),
            # Keep the venv path spelling; realpath() would collapse a venv's
            # python symlink to /usr/bin and break executable identity binding.
            "python": os.path.abspath(sys.executable),
        },
    }


_CAPABILITY_PAYLOAD: dict[str, Any] = _build_capability_payload()


__all__ = [
    "build_parser",
    "kill_proc_tree",
    "stop_all_confflow_processes",
    "main",
]

logger = get_logger()


def _resolve_default_work_dir(
    input_files: list[str],
    *,
    sandbox_root: str | None,
) -> str:
    """Resolve the implicit CLI work_dir, preferring sandbox_root when present."""
    input_basename = os.path.splitext(os.path.basename(input_files[0]))[0]
    dirname = f"{input_basename}_work" if len(input_files) == 1 else f"{input_basename}_multi_work"
    if sandbox_root:
        return os.path.join(sandbox_root, dirname)
    return dirname


def _parse_gaussian_input_geometry(text: str) -> tuple[int, int, list[str], list[list[float]]]:
    """Parse a Gaussian .gjf/.com input file into (charge, multiplicity, atoms, coords)."""
    res = parse_gaussian_input_text(text)
    if not res["atoms"]:
        raise ValueError("Gaussian input does not contain a geometry section")
    return res["charge"], res["multiplicity"], res["atoms"], res["coords"]


def _convert_gjf_to_xyz(gjf_path: str, xyz_out: str) -> None:
    """Convert Gaussian input to XYZ format."""
    try:
        text = Path(gjf_path).read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        raise RuntimeError(f"Failed to read Gaussian input file {gjf_path}: {e}") from e

    charge, mult, atoms, coords = _parse_gaussian_input_geometry(text)
    comment = f"SourceGJF={os.path.abspath(gjf_path)} | charge={charge} | multiplicity={mult}"
    conf = {
        "natoms": len(atoms),
        "comment": comment,
        "atoms": atoms,
        "coords": coords,
    }
    write_xyz_file(xyz_out, [conf], atomic=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the ConfFlow workflow for one or more XYZ inputs",
        epilog="Example: confflow hexane.xyz -c confflow.yaml\nDefault working directory: hexane_work/",
    )
    parser.add_argument("input_xyz", nargs="*", help="Path to one or more input XYZ files")
    parser.add_argument("-c", "--config", help="Path to the workflow YAML configuration file")
    parser.add_argument(
        "-w",
        "--work_dir",
        default=None,
        help="Path to the working directory (default: <input_name>_work)",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from an existing checkpoint")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and configuration, then print the planned workflow without running it",
    )
    parser.add_argument(
        "--config-show",
        action="store_true",
        help="Show the resolved configuration for a workflow YAML without running it",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop all running ConfFlow tasks, including child processes",
    )
    parser.add_argument(
        "--export",
        dest="export_work_dir",
        help="Export existing workflow results from a work directory without running ConfFlow",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "json", "text"),
        default="csv",
        help="Output format for --export (csv/json) or --config-show (text/json, default: csv for --export, text for --config-show)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Output file for --export (default: <work_dir>/confflow_results.<format>), "
            "or output directory for --rerun-failed"
        ),
    )
    parser.add_argument(
        "--rerun-failed",
        dest="rerun_failed_step_dir",
        help="Rerun failed.xyz from an existing calc/task step directory",
    )
    parser.add_argument(
        "--step",
        dest="step",
        help="Workflow step name or 1-based index for --rerun-failed or --config-show",
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help="Forward to the confflow-agent CLI (serve, status, submit, list, pause, resume, cancel, stop, logs)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the ConfFlow version and exit",
    )
    parser.add_argument(
        "--capabilities",
        action="store_true",
        help="Print ConfFlow capability-contract JSON and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Use JSON output with --capabilities",
    )
    return parser


def _append_to_output(output_path: str, text: str) -> None:
    try:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
    except OSError as e:
        logger.warning(f"Failed to append to output file {output_path}: {e}")


def _safe_log_cli_exception(message: str, exc: BaseException | None = None) -> None:
    """Best-effort CLI exception logging that never raises back into the CLI."""
    try:
        if exc is None:
            logger.error(message)
        else:
            logger.exception(message)
    except Exception:
        pass


def _write_cli_error(output_path: str, exc: BaseException, hint: str | None = None) -> None:
    """Write a user-facing CLI error message plus an optional follow-up hint."""
    _append_to_output(output_path, f"[ERROR] {type(exc).__name__}: {exc}")
    if hint:
        _append_to_output(output_path, hint)


def _load_sandbox_root_hint(config_file: str) -> str | None:
    """Best-effort read of ``global.sandbox_root`` without full schema validation."""
    try:
        with open(config_file, encoding="utf-8") as handle:
            raw: Any = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    global_cfg = raw.get("global") or {}
    if not isinstance(global_cfg, dict):
        return None
    return resolve_sandbox_root(global_cfg)


def kill_proc_tree(
    pid: int, sig=signal.SIGTERM, include_parent=True, timeout=None, on_terminate=None
):
    """Gracefully kill a process tree using psutil (including recursive children).

    Sends the specified signal, waits for the given timeout, then sends
    SIGKILL to any processes still alive.

    Parameters
    ----------
    pid : int
        The root process ID to kill.
    sig : signal.Signals
        Signal to send (default: SIGTERM).
    include_parent : bool
        Whether to include the parent process itself.
    timeout : float or None
        Seconds to wait for graceful termination.
    on_terminate : callable or None
        Ignored.

    Returns
    -------
    tuple or None
        (gone, alive) lists of terminated and still-alive processes,
        or None if psutil is unavailable or process not found.
    """
    del on_terminate
    if not psutil:
        return None

    if pid == os.getpid():
        raise RuntimeError("Refusing to stop the current process")

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None

    # Collect the child process tree first.
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []

    procs = children + ([parent] if include_parent else [])

    # First, attempt graceful termination.
    for p in procs:
        try:
            p.send_signal(sig)
        except psutil.NoSuchProcess:
            pass

    # Wait for graceful termination before escalating.
    gone, alive = psutil.wait_procs(procs, timeout=timeout)

    # Then force-kill any remaining processes.
    if alive:
        for p in alive:
            try:
                p.kill()  # SIGKILL
            except psutil.NoSuchProcess:
                pass
        # Refresh the final process state after SIGKILL.
        gone_final, alive = psutil.wait_procs(alive, timeout=0.2)
        gone.extend(gone_final)

    return (gone, alive)


def _is_confflow_process_cmdline(cmdline: list[str]) -> bool:
    """Return True only for known ConfFlow entrypoint command lines."""
    if not cmdline or "--stop" in cmdline:
        return False

    # NOTE (P2 audit, v1.4.5 Gate A): "confcalc" remains in the
    # process-recognizer set as a historical entry. Current README,
    # [project.scripts], and the pyproject CLI entry do not register a
    # `confcalc` command. Removing this entry requires an independent
    # code change with its own tests; per the P2 plan this Gate A
    # stage does not delete or restore a `confcalc` CLI surface.
    entrypoints = {"confflow", "confts", "confgen", "confrefine", "confcalc"}
    first = os.path.basename(cmdline[0])
    if first in entrypoints:
        return True

    if not first.startswith("python"):
        return False

    args = cmdline[1:]
    if len(args) >= 2 and args[0] == "-m":
        module = args[1]
        return module == "confflow" or module.startswith("confflow.")

    if args:
        script_name = os.path.basename(args[0])
        return script_name in entrypoints

    return False


def stop_all_confflow_processes() -> int:
    if psutil is None:
        print(
            "Error: the 'psutil' module is required for the --stop command. Install it with 'pip install psutil'.",
            file=sys.stderr,
        )
        return 1

    # Discover candidate ConfFlow processes.
    confflow_procs = []
    myself = psutil.Process()
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "cwd", "status"]):
        try:
            if p.pid == myself.pid:
                continue
            if p.status() == psutil.STATUS_ZOMBIE:
                continue

            cmdline = p.info["cmdline"]
            if not cmdline:
                continue

            if _is_confflow_process_cmdline(cmdline):
                confflow_procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    if not confflow_procs:
        print("No running ConfFlow processes were found.")
        return 0

    print(
        f"Found {len(confflow_procs)} running ConfFlow process(es). Stopping each process tree..."
    )

    for p in confflow_procs:
        try:
            print(f"Stopping the process tree for PID {p.pid}...")
            kill_proc_tree(p.pid, timeout=3)
            print(f"Stopped the process tree for PID {p.pid}.")
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            print(f"Failed to stop PID {p.pid}: access denied.")
        except (psutil.Error, OSError, RuntimeError) as e:
            print(f"Failed to stop PID {p.pid}: {e}")

    return 0


def _service_run_id(config_file: str, input_files: list[str], work_dir: str) -> str:
    """Derive a stable service run ID for one CLI work directory."""
    payload = json.dumps(
        {
            "config_file": os.path.abspath(config_file),
            "input_files": [os.path.abspath(path) for path in input_files],
            "work_dir": os.path.abspath(work_dir),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "run_" + hashlib.sha256(payload).hexdigest()[:48]


def main(
    args_list: list[str] | None = None,
    *,
    executable_override: str | None = None,
):
    # Fast-path: if --agent is present, strip it and forward directly to
    # the agent CLI without confflow's argument parser seeing agent flags.
    # Use sys.argv[1:] when args_list is None (i.e., when called as entry point).
    effective_args = args_list if args_list is not None else sys.argv[1:]

    if effective_args and effective_args[0] == "control":
        from .control import main as control_main

        return control_main(effective_args[1:])

    if "--agent" in effective_args:
        stripped = [a for a in effective_args if a != "--agent"]
        return agent_main(stripped if stripped else None)

    parser = build_parser()
    args = parser.parse_args(args_list)

    if args.json and not args.capabilities:
        parser.error("--json requires --capabilities")

    # These probes return before input/config/workflow validation or execution.
    if args.version:
        print(__import__("confflow").__version__)
        if _HANDSHAKE_PROBE:
            logging.disable(logging.NOTSET)
        return ExitCode.SUCCESS
    if args.capabilities:
        print(json.dumps(_build_capability_payload(executable_override), indent=2))
        if _HANDSHAKE_PROBE:
            logging.disable(logging.NOTSET)
        return ExitCode.SUCCESS

    if args.stop:
        return stop_all_confflow_processes()

    if args.export_work_dir:
        if args.format not in {"csv", "json"}:
            print("Error: --export supports --format csv or json", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        try:
            result = export_results(
                args.export_work_dir,
                output_format=args.format,
                output_path=args.output,
            )
        except (FileNotFoundError, PathSafetyError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        except NoExportableResultsError as e:
            for warning in e.warnings:
                print(f"Warning: {warning}", file=sys.stderr)
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.RUNTIME_ERROR
        except (OSError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.RUNTIME_ERROR

        for warning in result.warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        print(f"Exported {result.row_count} result row(s) to {result.output_path}")
        return ExitCode.SUCCESS

    if args.rerun_failed_step_dir:
        if not args.config:
            print("Error: --config is required with --rerun-failed", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        if not args.step:
            print("Error: --step is required with --rerun-failed", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        try:
            rerun_result = run_rerun_failed(
                step_dir=args.rerun_failed_step_dir,
                config_file=args.config,
                step_ref=args.step,
                output_dir=args.output,
            )
        except (FileNotFoundError, PathSafetyError, RerunFailedUsageError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        except (
            ConfigurationError,
            InputFileError,
            OSError,
            RerunFailedRuntimeError,
            ValueError,
            XYZFormatError,
        ) as e:
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.RUNTIME_ERROR

        print(f"Rerun failed conformers from: {rerun_result.failed_path}")
        print(f"Workflow config: {rerun_result.config_file}")
        print(f"Workflow step: {rerun_result.step_label}")
        print(f"Rerun output directory: {rerun_result.output_dir}")
        print(
            "Rerun summary: "
            f"input={rerun_result.input_count}, "
            f"output={rerun_result.output_count}, "
            f"failed={rerun_result.failed_count}"
        )
        print("Use --export on the rerun output directory to export rerun results.")
        return ExitCode.SUCCESS

    if args.config_show:
        if not args.config:
            print("Error: --config is required with --config-show", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        # Determine effective format: for --config-show, treat "csv" as "text"
        show_format = args.format if args.format in ("json", "text") else "text"
        try:
            from .workflow.config_show import show_resolved_config

            show_resolved_config(
                config_file=os.path.abspath(args.config),
                step_ref=args.step,
                output_format=show_format,
            )
        except (ConfigurationError, FileNotFoundError, PathSafetyError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        return ExitCode.SUCCESS

    # Manual validation for required arguments when not stopping
    if not args.input_xyz:
        parser.error("At least one input XYZ file is required.")

    input_files = [os.path.abspath(x) for x in args.input_xyz]
    original_input_files = list(input_files)

    # Resolve config file: default to confflow.yaml under input directory
    if args.config:
        config_file = os.path.abspath(args.config)
    else:
        default_cfg = os.path.join(os.path.dirname(input_files[0]), "confflow.yaml")
        if not os.path.exists(default_cfg):
            parser.error(
                f"No configuration file was provided, and the default file was not found: {default_cfg}"
            )
        config_file = default_cfg

    first_input = os.path.abspath(args.input_xyz[0])
    output_path = output_txt_path_for_input(first_input)
    sandbox_root = _load_sandbox_root_hint(config_file)
    if args.work_dir is None:
        work_dir = _resolve_default_work_dir(input_files, sandbox_root=sandbox_root)
    else:
        work_dir = args.work_dir

    if args.dry_run:
        try:
            run_dry_run(input_files, config_file, work_dir)
        except (
            ConfigurationError,
            FileNotFoundError,
            InputFileError,
            OSError,
            PathSafetyError,
            ValueError,
            XYZFormatError,
        ) as e:
            print(f"Error: {e}", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        return ExitCode.SUCCESS

    try:
        work_dir = validate_managed_path(work_dir, label="work_dir", sandbox_root=sandbox_root)
        with cli_output_to_txt(first_input) as output_path:
            # Support Gaussian input (.gjf/.com): auto-convert to single-frame XYZ then run workflow.
            # Converted files are placed under work_dir/_converted_inputs/ to avoid polluting CWD.
            converted_inputs: list[str] = []
            os.makedirs(work_dir, exist_ok=True)
            conv_dir = validate_managed_path(
                os.path.join(work_dir, "_converted_inputs"),
                label="_converted_inputs",
                sandbox_root=sandbox_root,
            )
            for path in input_files:
                ext = os.path.splitext(path)[1].lower()
                if ext not in {".gjf", ".com"}:
                    converted_inputs.append(path)
                    continue
                stem = os.path.splitext(os.path.basename(path))[0]
                os.makedirs(conv_dir, exist_ok=True)
                out_xyz = os.path.join(conv_dir, f"{stem}.xyz")
                _convert_gjf_to_xyz(path, out_xyz)
                converted_inputs.append(os.path.abspath(out_xyz))
            input_files = converted_inputs

            run_workflow_through_service(
                input_xyz=input_files,
                config_file=config_file,
                work_dir=work_dir,
                state_root=os.path.join(work_dir, ".confflow_execution"),
                run_id=_service_run_id(config_file, input_files, work_dir),
                original_input_files=original_input_files,
                resume=bool(args.resume),
                verbose=bool(args.verbose),
                pause_beacon_file=None,
                step_started_callback=None,
                workflow_runner=run_workflow,
            )

        return ExitCode.SUCCESS
    except ValueError as e:
        msg = str(e)
        msg_lower = msg.lower()
        if "multi-input mode requires" in msg_lower or "element order mismatch" in msg_lower:
            _append_to_output(output_path, f"[ERROR] Input consistency validation failed: {msg}")
            _append_to_output(
                output_path,
                "Hint: set 'force_consistency: true' under the global config to skip this check.",
            )
            return ExitCode.USAGE_ERROR
        _append_to_output(output_path, f"[ERROR] {msg}")
        return ExitCode.USAGE_ERROR

    except KeyboardInterrupt as e:
        _safe_log_cli_exception("ConfFlow interrupted by user", e)
        _write_cli_error(output_path, e)
        return ExitCode.RUNTIME_ERROR

    except Exception as e:
        _safe_log_cli_exception("ConfFlow CLI failed", e)
        _write_cli_error(
            output_path,
            e,
            hint="Hint: inspect the generated log output for a traceback if the cause is unclear.",
        )
        return ExitCode.RUNTIME_ERROR
