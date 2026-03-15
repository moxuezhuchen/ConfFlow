#!/usr/bin/env python3

"""ConfFlow CLI entrypoint (without business logic)."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import traceback
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

from .core.contracts import ExitCode, cli_output_to_txt, output_txt_path_for_input
from .core.io import parse_gaussian_input_text, write_xyz_file
from .core.utils import get_logger
from .workflow.engine import run_workflow

__all__ = [
    "build_parser",
    "kill_proc_tree",
    "stop_all_confflow_processes",
    "main",
]

logger = get_logger()


def _parse_gaussian_input_geometry(text: str) -> tuple[int, int, list[str], list[list[float]]]:
    """Parse a Gaussian .gjf/.com input file into (charge, multiplicity, atoms, coords)."""
    res = parse_gaussian_input_text(text)
    if not res["atoms"]:
        raise ValueError("No geometry found in Gaussian input")
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
        description="ConfFlow - automated computational chemistry workflow",
        epilog="Example: confflow hexane.xyz -c confflow.yaml\nDefault work dir: hexane_work/",
    )
    parser.add_argument("input_xyz", nargs="*", help="Input XYZ file(s)")
    parser.add_argument("-c", "--config", help="Path to YAML configuration file")
    parser.add_argument(
        "-w", "--work_dir", default=None, help="Working directory (default: <input_name>_work)"
    )
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint if available")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG level logging")
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop all running confflow tasks (including child processes)",
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
        raise RuntimeError("I refuse to kill myself")

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None

    # Collect all child processes
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []

    procs = children + ([parent] if include_parent else [])

    # First round: attempt graceful termination
    for p in procs:
        try:
            p.send_signal(sig)
        except psutil.NoSuchProcess:
            pass

    # Efficiently wait for processes to terminate
    gone, alive = psutil.wait_procs(procs, timeout=timeout)

    # Second round: force-kill any remaining alive processes
    if alive:
        for p in alive:
            try:
                p.kill()  # SIGKILL
            except psutil.NoSuchProcess:
                pass
        # Check final status
        gone_final, alive = psutil.wait_procs(alive, timeout=0.2)
        gone.extend(gone_final)

    return (gone, alive)


def stop_all_confflow_processes() -> int:
    if psutil is None:
        print(
            "Error: 'psutil' module is required for the --stop command. Please install it via 'pip install psutil'.",
            file=sys.stderr,
        )
        return 1

    # Find confflow processes
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
        print("No running confflow processes found.")
        return 0

    print(
        f"Found {len(confflow_procs)} running confflow process(es). Stopping them and their children..."
    )

    for p in confflow_procs:
        try:
            print(f"Stopping process tree for PID {p.pid}...")
            kill_proc_tree(p.pid, timeout=3)
            print(f"Stopped PID {p.pid} and its children.")
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            print(f"Failed to stop PID {p.pid} (Access Denied)")
        except Exception as e:
            print(f"Error stopping PID {p.pid}: {e}")

    return 0


def main(args_list: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(args_list)

    if args.stop:
        return stop_all_confflow_processes()

    # Manual validation for required arguments when not stopping
    if not args.input_xyz:
        parser.error("the following arguments are required: input_xyz")

    input_files = [os.path.abspath(x) for x in args.input_xyz]
    original_input_files = list(input_files)

    # Resolve config file: default to confflow.yaml under input directory
    if args.config:
        config_file = os.path.abspath(args.config)
    else:
        default_cfg = os.path.join(os.path.dirname(input_files[0]), "confflow.yaml")
        if not os.path.exists(default_cfg):
            parser.error(
                f"Config file is not provided and default config was not found: {default_cfg}"
            )
        config_file = default_cfg

    if args.work_dir is None:
        input_basename = os.path.splitext(os.path.basename(input_files[0]))[0]
        work_dir = (
            f"{input_basename}_work" if len(input_files) == 1 else f"{input_basename}_multi_work"
        )
    else:
        work_dir = args.work_dir

    first_input = os.path.abspath(args.input_xyz[0])
    output_path = output_txt_path_for_input(first_input)

    try:
        with cli_output_to_txt(first_input) as output_path:
            try:
                # Support Gaussian input (.gjf/.com): auto-convert to single-frame XYZ then run workflow.
                # Converted files are placed under work_dir/_converted_inputs/ to avoid polluting CWD.
                converted_inputs: list[str] = []
                os.makedirs(work_dir, exist_ok=True)
                conv_dir = os.path.join(work_dir, "_converted_inputs")
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
            except Exception:
                # Log full traceback to file log
                traceback.print_exc()
                raise

        return ExitCode.SUCCESS
    except ValueError as e:
        msg = str(e)
        if "multi-input mode requires" in msg or "element order mismatch" in msg:
            _append_to_output(output_path, f"[ERROR] Input consistency validation failed: {msg}")
            _append_to_output(
                output_path,
                "Hint: add 'force_consistency: true' under global config to skip this check.",
            )
            return ExitCode.USAGE_ERROR
        _append_to_output(output_path, f"[ERROR] {msg}")
        return ExitCode.USAGE_ERROR

    except Exception as e:
        _append_to_output(output_path, f"[ERROR] {e}")
        return ExitCode.RUNTIME_ERROR
