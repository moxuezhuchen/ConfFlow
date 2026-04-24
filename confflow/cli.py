#!/usr/bin/env python3

"""ConfFlow CLI entrypoint (without business logic)."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

import yaml

from .core.contracts import ExitCode, cli_output_to_txt, output_txt_path_for_input
from .core.exceptions import ConfigurationError, InputFileError, PathSafetyError, XYZFormatError
from .core.io import parse_gaussian_input_text, write_xyz_file
from .core.path_policy import resolve_sandbox_root, validate_managed_path
from .core.utils import get_logger
from .workflow.dry_run import run_dry_run
from .workflow.engine import run_workflow

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
        "--stop",
        action="store_true",
        help="Stop all running ConfFlow tasks, including child processes",
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

            cmd_str = " ".join(cmdline)
            # Simple heuristic to identify confflow processes
            if "confflow" in cmd_str and "--stop" not in cmd_str:
                # Exclude common editors and tools
                if any(x in cmd_str for x in ["grep", "vim", "nano", "code", "emacs", "pytest"]):
                    continue
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


def main(args_list: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(args_list)

    if args.stop:
        return stop_all_confflow_processes()

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

            run_workflow(
                input_xyz=input_files,
                config_file=config_file,
                work_dir=work_dir,
                original_input_files=original_input_files,
                resume=bool(args.resume),
                verbose=bool(args.verbose),
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
