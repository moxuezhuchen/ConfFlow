#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""任务管理器（迁移自旧版 confflow/calc.py 的 ChemTaskManager）。

兼容目标：
- 调用方式保持不变：`ChemTaskManager(settings_file).run(input_xyz_file)`
- 结果库路径/备份恢复/输出 result.xyz / auto_clean 行为保持一致
"""

from __future__ import annotations

import configparser
import logging
import os
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import re

# from tqdm import tqdm
from ..core.console import create_progress, console
from rich import box
from rich.panel import Panel

from .analysis import _bond_length_from_xyz_lines, _parse_ts_bond_atoms
from .core import get_itask, parse_iprog, setup_logging
from .db.database import ResultsDB
from .components.executor import _cleanup_lingering_processes
from .components.task_runner import TaskRunner
from .policies.gaussian import GaussianPolicy
from .policies.orca import OrcaPolicy
from .resources import ResourceMonitor

from ..core import io as io_xyz
from ..blocks import refine

logger = logging.getLogger("confflow.calc.manager")


def _run_task(task_info: Dict[str, Any]) -> Dict[str, Any]:
    return TaskRunner().run(task_info)


class ChemTaskManager:
    def __init__(
        self,
        settings_file: Optional[Any] = None,
        resume_dir: Optional[str] = None,
        settings: Optional[Dict[str, Any]] = None,
    ):
        if settings is not None:
            self.config = dict(settings)
        elif isinstance(settings_file, dict):
            self.config = dict(settings_file)
        elif settings_file and os.path.exists(settings_file):
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.optionxform = str
            cfg.read(settings_file)
            self.config = {
                k: v.strip('"') for sec in cfg.sections() for k, v in cfg.items(sec) if v
            }
            self.config.update({k: v.strip('"') for k, v in cfg.defaults().items() if v})
        else:
            self.config = {}

        self._default_work_dir = os.path.join(
            os.getcwd(), f"chem_tasks_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self.work_dir = resume_dir if resume_dir else self._default_work_dir
        self._work_dir_initialized = False

        self.backup_dir: Optional[str] = None
        self.results_db: Optional[ResultsDB] = None
        self.monitor = (
            ResourceMonitor()
            if self.config.get("enable_dynamic_resources", "false").lower() == "true"
            else None
        )
        self.stop_requested = False

    def _ensure_work_dir(self):
        if self._work_dir_initialized:
            return
        os.makedirs(self.work_dir, exist_ok=True)
        setup_logging(self.work_dir)

        backup_dir_cfg = self.config.get("backup_dir")
        if backup_dir_cfg and str(backup_dir_cfg).strip():
            self.backup_dir = str(backup_dir_cfg).strip()
        else:
            self.backup_dir = os.path.join(self.work_dir, "backups")
            self.config["backup_dir"] = self.backup_dir
        os.makedirs(self.backup_dir, exist_ok=True)

        self.config["stop_beacon_file"] = os.path.join(self.work_dir, "STOP")
        self.results_db = ResultsDB(os.path.join(self.work_dir, "results.db"))
        self._work_dir_initialized = True

    def _read_single_frame_xyz_coords(self, xyz_path: str) -> Optional[List[str]]:
        try:
            if not os.path.exists(xyz_path):
                return None
            with open(xyz_path, "r", errors="ignore") as f:
                lines = [ln.rstrip("\n") for ln in f.readlines()]
            if len(lines) < 3:
                return None
            try:
                n = int(lines[0].strip())
            except Exception:
                return None
            coords = []
            for ln in lines[2 : 2 + n]:
                p = ln.split()
                if len(p) >= 4:
                    coords.append(f"{p[0]} {p[1]} {p[2]} {p[3]}")
            return coords if len(coords) == n else None
        except Exception:
            return None

    def _recover_result_from_backups(self, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            if not self.backup_dir or not os.path.isdir(self.backup_dir):
                return None
            job_name = task["job_name"]
            cfg = task.get("config", self.config)

            iprog = parse_iprog(cfg)
            if iprog == 1:
                policy = GaussianPolicy()
            elif iprog == 2:
                policy = OrcaPolicy()
            else:
                return None

            log_path = os.path.join(self.backup_dir, f"{job_name}.{policy.log_ext}")
            xyz_path = os.path.join(self.backup_dir, f"{job_name}.xyz")

            parsed: Dict[str, Any] = {}
            final_coords = None

            if os.path.exists(log_path) and policy.check_termination(log_path):
                is_sp_task = get_itask(cfg) == 1
                parsed = policy.parse_output(log_path, cfg, is_sp_task=is_sp_task) or {}
                final_coords = parsed.get("final_coords")

            if not final_coords and os.path.exists(xyz_path):
                final_coords = self._read_single_frame_xyz_coords(xyz_path)

            if not final_coords:
                return None

            itask = get_itask(cfg)
            e = parsed.get("e_low")
            g = parsed.get("g_low")
            eh = parsed.get("e_high")
            gc = parsed.get("g_corr")
            if itask in [2, 3, 4] and gc is None and e is not None and g is not None:
                gc = g - e

            final_val = g if g is not None else (eh if eh is not None else e)
            key = "final_gibbs_energy" if g is not None else "energy"
            result: Dict[str, Any] = {
                **task,
                "status": "success",
                key: final_val,
                "final_sp_energy": eh,
                "final_coords": final_coords,
                "num_imag_freqs": parsed.get("num_imag_freqs"),
                "lowest_freq": parsed.get("lowest_freq"),
                "g_corr": gc,
            }

            if itask == 4:
                ts_bond_atoms = cfg.get("ts_bond_atoms", cfg.get("ts_bond"))
                pair = _parse_ts_bond_atoms(ts_bond_atoms)
                if pair:
                    result["ts_bond_atoms"] = f"{pair[0]},{pair[1]}"
                    bl = _bond_length_from_xyz_lines(final_coords, pair[0], pair[1])
                    if bl is not None:
                        result["ts_bond_length"] = bl

            return result
        except Exception:
            return None

    def _read_xyz(self, f: str):
        try:
            conformers = io_xyz.read_xyz_file(f, parse_metadata=True)
            return [
                {
                    "title": conf["comment"],
                    "coords": [
                        f"{a} {x} {y} {z}" for a, (x, y, z) in zip(conf["atoms"], conf["coords"])
                    ],
                    "metadata": conf.get("metadata", {}),
                }
                for conf in conformers
            ]
        except Exception as e:
            logger.warning(f"使用 io_xyz 读取 {f} 失败 ({e})，尝试回退读取")
            geometries = []
            try:
                with open(f, "r") as file:
                    lines = file.readlines()
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if not line:
                        i += 1
                        continue
                    try:
                        num_atoms = int(line)
                    except Exception:
                        i += 1
                        continue
                    if i + 1 >= len(lines):
                        break
                    comment = lines[i + 1].strip()
                    coords = []
                    for j in range(num_atoms):
                        if i + 2 + j >= len(lines):
                            break
                        parts = lines[i + 2 + j].strip().split()
                        if len(parts) >= 4:
                            try:
                                atom = parts[0]
                                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                                coords.append(f"{atom} {x} {y} {z}")
                            except Exception:
                                pass
                    if len(coords) == num_atoms:
                        geometries.append(
                            {
                                "title": comment,
                                "coords": coords,
                                "metadata": io_xyz.parse_comment_metadata(comment),
                            }
                        )
                    i += 2 + num_atoms
            except Exception:
                pass
            return geometries

    def run(self, input_xyz_file: str) -> None:
        self._ensure_work_dir()

        assert self.results_db is not None
        try:
            geoms = self._read_xyz(input_xyz_file)

            def _sanitize_token(s: str) -> str:
                s = str(s).strip()
                s = re.sub(r"[^A-Za-z0-9_\-]+", "_", s)
                s = re.sub(r"_+", "_", s).strip("_")
                return s

            def _job_name_for_geom(i: int, g: Dict[str, Any]) -> str:
                meta = g.get("metadata") or {}
                cid = meta.get("CID") if isinstance(meta, dict) else None
                if cid is None or str(cid).strip() == "":
                    return f"c{i + 1:04d}"

                cid_raw = str(cid).strip()
                # Prefer numeric CID (keeps classic c0001 style, but stable across steps).
                try:
                    cid_int = int(cid_raw)
                    if cid_int > 0:
                        return f"c{cid_int:04d}"
                except Exception:
                    pass

                # Fallback: use sanitized string CID.
                token = _sanitize_token(cid_raw)
                if not token:
                    return f"c{i + 1:04d}"
                if len(token) > 48:
                    token = token[:48]
                return token

            tasks: List[Dict[str, Any]] = []
            used_names: Dict[str, int] = {}
            for i, g in enumerate(geoms):
                job_name = _job_name_for_geom(i, g)
                # Ensure uniqueness (rare but possible if CID duplicates).
                if job_name in used_names:
                    used_names[job_name] += 1
                    job_name = f"{job_name}_dup{used_names[job_name]}"
                else:
                    used_names[job_name] = 0

                tasks.append(
                    {
                        "index": i,
                        "job_name": job_name,
                        "work_dir": os.path.join(self.work_dir, job_name),
                        **g,
                        "config": self.config,
                    }
                )

            todo = []
            for t in tasks:
                res = self.results_db.get_result_by_job_name(t["job_name"])
                if res and res.get("status") == "success":
                    continue

                if str(self.config.get("resume_from_backups", "true")).lower() == "true":
                    recovered = self._recover_result_from_backups(t)
                    if recovered and recovered.get("status") == "success":
                        self.results_db.insert_result(recovered)
                        continue

                todo.append(t)

            max_jobs = int(self.config.get("max_parallel_jobs", 4))

            if len(todo) == 1:
                res = _run_task(todo[0])
                self.results_db.insert_result(res)
            elif todo:
                with ProcessPoolExecutor(max_workers=max_jobs) as exc:
                    futures = {exc.submit(_run_task, t): t for t in todo}

                    with create_progress() as progress:
                        task_id = progress.add_task("[cyan]Calculating...", total=len(todo))

                        for fut in as_completed(futures):
                            if os.path.exists(self.config["stop_beacon_file"]):
                                self.stop_requested = True
                                break
                            res = fut.result()
                            self.results_db.insert_result(res)
                            progress.advance(task_id)

            if self.stop_requested:
                iprog = parse_iprog(self.config)
                policy = None
                if iprog == 1:
                    policy = GaussianPolicy()
                elif iprog == 2:
                    policy = OrcaPolicy()

                if policy:
                    _cleanup_lingering_processes(self.config, policy)
                return

            all_res = self.results_db.get_all_results()
            success = [r for r in all_res if r["status"] in ["success", "skipped"]]
            failed = [r for r in all_res if r.get("status") == "failed"]

            # 输出失败构象（用于排查与重算）
            if failed:
                # Build geometry maps (keyed by actual job_name; may be CID-derived)
                job_meta_map = {t["job_name"]: t.get("metadata", {}) for t in tasks}
                job_coords_map = {t["job_name"]: t.get("coords") for t in tasks}

                failed_file = os.path.join(self.work_dir, "failed.xyz")
                with open(failed_file, "w") as f:
                    for t in failed:
                        job_name = t.get("job_name")
                        # 用户需求：失败构象输出始终使用输入结构（便于复现/重算）。
                        coords = job_coords_map.get(job_name) or []
                        if not coords:
                            continue

                        # 拼接 comment：尽量保留 CID，并附带错误信息（做长度限制避免过长）
                        orig_meta = job_meta_map.get(job_name, {})
                        cid = orig_meta.get("CID")
                        err = (t.get("error") or "").strip()
                        if len(err) > 200:
                            err = err[:200] + "..."
                        info = f"Failed=1 Job={job_name}"
                        if cid is not None and str(cid).strip() != "":
                            info += f" CID={cid}"
                        if err:
                            info += f" Error={err}"

                        f.write(f"{len(coords)}\n{info}\n" + "\n".join(coords) + "\n")

            if not success:
                return

            # Build metadata map
            job_meta_map = {t["job_name"]: t.get("metadata", {}) for t in tasks}

            out_file = os.path.join(self.work_dir, "result.xyz")
            with open(out_file, "w") as f:
                for t in success:
                    # Recover metadata
                    orig_meta = job_meta_map.get(t["job_name"], {})

                    # NOTE: 能量字段用于后续排序/筛选。
                    # 新约定（用户侧更直观、避免字段爆炸）：
                    # - freq/opt_freq：输出 G_corr（供后续 sp 继承）
                    # - sp：一旦合成出 Gibbs（G = E_sp + G_corr），则只输出 G，不再输出/传递 G_corr/E_sp 等。
                    e_gibbs = t.get("final_gibbs_energy")
                    e_sp = t.get("final_sp_energy")
                    g_corr_res = t.get("g_corr")
                    combined_to_g = (e_gibbs is not None) and (e_sp is not None) and (g_corr_res is not None)

                    if combined_to_g:
                        info = f"G={e_gibbs}"
                    else:
                        e_any = e_gibbs if e_gibbs is not None else t.get("energy")
                        info = f"Energy={e_any}"

                    # CID
                    cid = orig_meta.get("CID")
                    if cid is not None and str(cid).strip() != "":
                        info += f" CID={cid}"

                    # G_corr：仅在未合成出 G 时才保留（用于下一步 sp 继承）
                    if not combined_to_g:
                        g_corr = g_corr_res
                        if g_corr is None:
                            g_corr = orig_meta.get("G_corr")
                        if g_corr is not None:
                            info += f" G_corr={g_corr}"

                    # Imag (from result OR original metadata)
                    imag = t.get("num_imag_freqs")
                    if imag is None:
                        imag = orig_meta.get("Imag") or orig_meta.get("num_imag_freqs")
                    if imag is not None:
                        info += f" Imag={imag}"

                    if t.get("lowest_freq") is not None:
                        info += f" LowestFreq={t['lowest_freq']:.1f}"
                    if t.get("ts_bond_atoms") is not None:
                        info += f" TSAtoms={t['ts_bond_atoms']}"
                    if t.get("ts_bond_length") is not None:
                        info += f" TSBond={float(t['ts_bond_length']):.6f}"
                    f.write(f"{len(t['final_coords'])}\n{info}\n" + "\n".join(t["final_coords"]) + "\n")

            if self.config.get("auto_clean", "false").lower() == "true":
                # 简化 refine 标题
                console.print("  Refine: ", end="")
                # print("\n🧹 自动后处理: 调用 refine 模块")
                try:
                    opts_str = self.config.get("clean_opts", "-t 0.25")
                    thresh = 0.25
                    ewin = None
                    if "-t" in opts_str:
                        try:
                            thresh = float(opts_str.split("-t")[1].split()[0])
                        except Exception as e:
                            logger.debug(f"failed to parse clean_opts -t: {e}")
                    if "-ewin" in opts_str:
                        try:
                            ewin = float(opts_str.split("-ewin")[1].split()[0])
                        except Exception as e:
                            logger.debug(f"clean_opts -ewin 解析失败: {e}")

                    task_cores = int(self.config.get("cores_per_task", 1))
                    clean_args = refine.RefineOptions(
                        input_file=out_file,
                        output=os.path.join(os.path.dirname(out_file), "output.xyz"),
                        threshold=thresh,
                        ewin=ewin,
                        workers=task_cores,
                    )
                    refine.process_xyz(clean_args)
                    # print(f"✅ 清理完成: {clean_args.output}")
                except Exception as e:
                    print(f"❌ 清理失败: {e}")
        finally:
            try:
                self.results_db.close()
            except Exception:
                pass



def main():
    multiprocessing.freeze_support()
    import argparse

    parser = argparse.ArgumentParser(
        description="ConfFlow Calc (v1.0) - 量子化学任务执行器",
        epilog="示例: confcalc search.xyz -s settings.ini",
    )
    parser.add_argument("input_xyz", help="输入XYZ轨迹文件")
    parser.add_argument("-s", "--settings", required=True, help="INI配置文件路径")
    args = parser.parse_args()

    if not os.path.exists(args.input_xyz):
        print(f"❌ 输入文件不存在: {args.input_xyz}")
        raise SystemExit(1)
    if not os.path.exists(args.settings):
        print(f"❌ 配置文件不存在: {args.settings}")
        raise SystemExit(1)

    ChemTaskManager(args.settings).run(args.input_xyz)


if __name__ == "__main__":
    main()
