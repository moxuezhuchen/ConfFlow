#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ConfFlow CLI 入口（不包含业务逻辑）。"""

from __future__ import annotations

import argparse
import os
import sys
import signal
import re
from typing import Optional
from pathlib import Path
from typing import Any, Dict, List, Tuple
from contextlib import redirect_stdout, redirect_stderr

try:
    import psutil
except ImportError:
    psutil = None

from .core.utils import get_logger
from .workflow.engine import run_workflow
from .core.io import write_xyz_file
from .calc.constants import get_element_symbol

logger = get_logger()


def _parse_gaussian_input_geometry(text: str) -> Tuple[int, int, List[str], List[List[float]]]:
    """Parse a Gaussian .gjf/.com input file into (charge, multiplicity, atoms, coords).

    Notes:
    - Finds the first line matching charge/multiplicity: two integers.
    - Reads subsequent non-empty lines as geometry until a blank line.
    - For each geometry line, takes the first token as element (or atomic number)
      and the last three numeric tokens as x/y/z. This supports frozen-atom format
      where an extra column (e.g. 0 / -1) appears after the element.
    """
    lines = text.splitlines()
    qm_idx = None
    charge = 0
    mult = 1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if re.match(r"^\s*-?\d+\s+-?\d+\s*$", s):
            qm_idx = i
            parts = s.split()
            charge = int(parts[0])
            mult = int(parts[1])
            break
    if qm_idx is None:
        raise ValueError("Cannot find charge/multiplicity line in Gaussian input")

    atoms: List[str] = []
    coords: List[List[float]] = []
    for ln in lines[qm_idx + 1 :]:
        if not ln.strip():
            break
        p = ln.split()
        if len(p) < 4:
            break

        sym = p[0]
        if sym.isdigit():
            sym = get_element_symbol(int(sym))

        xyz: List[float] = []
        for tok in reversed(p[1:]):
            try:
                xyz.append(float(tok))
            except (ValueError, TypeError):
                continue
            if len(xyz) == 3:
                break
        if len(xyz) != 3:
            break
        z, y, x = xyz
        atoms.append(sym)
        coords.append([x, y, z])

    if not atoms:
        raise ValueError("No geometry found in Gaussian input")
    return charge, mult, atoms, coords


def _convert_gjf_to_xyz(gjf_path: str, xyz_out: str) -> None:
    """将 Gaussian 输入文件转换为 XYZ 格式"""
    try:
        text = Path(gjf_path).read_text(encoding="utf-8", errors="ignore")
    except (IOError, OSError) as e:
        raise RuntimeError(f"无法读取 Gaussian 输入文件 {gjf_path}: {e}") from e
    
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
        description="ConfFlow - 自动化计算化学工作流",
        epilog="示例: confflow hexane.xyz -c confflow.yaml\n工作目录将自动生成为 hexane_work/",
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
    except Exception:
        pass


import time


def kill_proc_tree(
    pid: int, sig=signal.SIGTERM, include_parent=True, timeout=None, on_terminate=None
):
    """Kill a process tree (including grandchildren) with signal "sig"."""
    # 兼容参数：当前实现未使用回调。
    del on_terminate
    if not psutil:
        return

    if pid == os.getpid():
        raise RuntimeError("I refuse to kill myself")

    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    children = parent.children(recursive=True)

    # Send signal to children first
    for p in children:
        try:
            p.send_signal(sig)
        except psutil.NoSuchProcess:
            pass

    if include_parent:
        try:
            parent.send_signal(sig)
        except psutil.NoSuchProcess:
            pass

    # Wait for processes to terminate (manual polling to avoid ChildProcessError on non-children)
    procs = children + ([parent] if include_parent else [])
    gone = []
    alive = []

    start_time = time.time()
    while True:
        alive = []
        for p in procs:
            try:
                if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                    alive.append(p)
                else:
                    if p not in gone:
                        gone.append(p)
            except psutil.NoSuchProcess:
                if p not in gone:
                    gone.append(p)

        if not alive:
            break

        if timeout is not None and time.time() - start_time > timeout:
            break

        time.sleep(0.1)

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


def main(args_list: Optional[list[str]] = None):
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
                f"配置文件未指定，且未在输入文件目录找到默认配置: {default_cfg}"
            )
        config_file = default_cfg

    if args.work_dir is None:
        input_basename = os.path.splitext(os.path.basename(input_files[0]))[0]
        work_dir = (
            f"{input_basename}_work" if len(input_files) == 1 else f"{input_basename}_multi_work"
        )
    else:
        work_dir = args.work_dir

    # Output file: same name as first input, placed in input directory
    first_input = os.path.abspath(args.input_xyz[0])
    output_dir = os.path.dirname(first_input)
    output_base = os.path.splitext(os.path.basename(first_input))[0]
    output_path = os.path.join(output_dir, f"{output_base}.txt")

    # 捕获柔性链/原子顺序一致性错误，进行交互提示，除非 --yes/--force
    try:
        with open(output_path, "w", encoding="utf-8") as out_f, redirect_stdout(
            out_f
        ), redirect_stderr(out_f):
            try:
                # Support Gaussian input (.gjf/.com): auto-convert to single-frame XYZ then run workflow.
                # Converted files are placed under work_dir/_converted_inputs/ to avoid polluting CWD.
                converted_inputs: List[str] = []
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
            except Exception as e:
                # 在文件日志中记录完整 trace
                import traceback
                traceback.print_exc()
                # 重新抛出，以便外层从 stderr (文件) 逃逸到终端（如果有机制）
                # 注意：此时 stderr 指向文件， raise 仍会写入文件
                
                # 尝试向原始 stderr 写一条紧急提示，告诉用户去哪里看日志
                print(f"\n[FATAL ERROR] 程序异常终止: {e}", file=sys.__stderr__)
                print(f"详细错误信息已写入日志文件: {output_path}", file=sys.__stderr__)
                raise

        return 0
    except ValueError as e:
        # 检测是否为多输入一致性相关错误
        msg = str(e)
        if "多文件输入模式要求" in msg or "柔性链在不同输入间不一致" in msg:
            # 如果用户已经开启了 force_consistency 参数（通过 args.force/yes 传递逻辑待建立）
            # 暂时只做交互提示

            # 交互式提示（仅在标准输出为终端时）
            if sys.stdin.isatty() and sys.stdout.isatty():
                print(f"\n{'!'*60}\n[ERROR] 输入一致性校验失败:\n{msg}\n{'!'*60}")
                resp = input("\n[交互模式] 是否确认忽略警告并强制继续? (y/N): ").lower()
                if resp == "y":
                    print("提示：当前版本请在该配置文件 global 节添加 'force_consistency: true' 以跳过此检查。")
                    return 1
                else:
                    return 1
            else:
                # 非交互模式，直接报错
                _append_to_output(output_path, f"[ERROR] {msg}")
                return 1
        # 其他 ValueError 原样记录
        _append_to_output(output_path, f"[ERROR] {msg}")
        return 1

    except Exception as e:
        # CLI 层负责退出码
        _append_to_output(output_path, f"[ERROR] {e}")
        return 1
