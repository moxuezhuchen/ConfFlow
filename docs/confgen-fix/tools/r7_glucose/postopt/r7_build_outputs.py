#!/usr/bin/env python3
"""Assemble R7-D post-optimization outputs.

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import csv
import os
import re
import sys
from pathlib import Path


def _find_repo_root(start):
    # 向上查找仓库根（以 pyproject.toml 为锚），供归档脚本定位 confflow。
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").is_file():
            return parent
    return start


REPO_ROOT = Path(os.environ.get("CONFFLOW_REPO", _find_repo_root(Path(__file__).resolve().parent)))


def _require_dir(env, what):
    # 读取必需的外部输入目录（未发布数据，不在仓库内）。
    val = os.environ.get(env, "")
    if not val:
        raise SystemExit(f"{env} 未设置：需指向{what}，详见 README。")
    return Path(val)


FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "confgen" / "ring" / "beta_d_glucopyranose"
OUT_DIR = Path(os.environ.get("R7_D_OUT", Path.cwd() / "r7d-out"))
OUT = OUT_DIR
DIAG_DIR = Path(os.environ.get("R7_DIAG_OUT", Path.cwd() / "r7diag-out"))

sys.path.insert(0, str(REPO_ROOT))

OUT = OUT_DIR
OPT = OUT / "opt_all"
CSV = OUT / "csv"
# opt_status.csv
prog = {}
for line in (OPT / "progress.log").read_text().splitlines():
    # "seed_00 rc=0 wall_s=2.87"
    m = re.match(r"(\S+)\s+rc=(\d+)\s+wall_s=([\d.]+)", line.strip())
    if m:
        prog[m.group(1)] = (int(m.group(2)), float(m.group(3)))
rows = []
for d in sorted(OPT.iterdir()):
    if not d.is_dir():
        continue
    base = d.name
    out = (d / f"{base}.out").read_text()
    hur = "THE OPTIMIZATION HAS CONVERGED" in out
    done = "OPTIMIZATION RUN DONE" in out
    term = "ORCA TERMINATED NORMALLY" in out
    es = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", out)
    fe = es[-1] if es else ""
    nE = len(es)
    rc, wall = prog.get(base, ("", ""))
    # initial energy = first FINAL SINGLE POINT? actually first SCF; use first FINAL
    e0 = es[0] if es else ""
    dE = float(fe) - float(e0) if fe and e0 else ""
    xyz_exists = (d / f"{base}.xyz").exists()
    rows.append(
        {
            "job": base,
            "orca_rc": rc,
            "wall_s": round(float(wall), 2) if wall != "" else "",
            "converged": int(hur and done),
            "terminated_normally": int(term),
            "n_energy_evals": nE,
            "E_final_Eh": fe,
            "E_first_Eh": e0,
            "dE_Eh": round(dE, 6) if dE != "" else "",
            "xyz_present": int(xyz_exists),
            "counted": int(hur and done and xyz_exists),
        }
    )
with open(CSV / "opt_status.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print(
    f"status: {sum(r['converged'] for r in rows)}/{len(rows)} converged, all counted={sum(r['counted'] for r in rows)}"
)
# assemble seeds_opt.xyz (38) in seed order
with open(OUT / "seeds_opt.xyz", "w") as out:
    for s in range(38):
        base = f"seed_{s:02d}"
        xyz = (OPT / base / f"{base}.xyz").read_text().splitlines()
        n = int(xyz[0].strip())
        out.write(f"{n}\nseed_opt {s} from={base} {xyz[1].strip()}\n")
        out.write("\n".join(xyz[2 : 2 + n]) + "\n")
print("seeds_opt.xyz written")
# assemble ctrl_opt.xyz
idx = [int(x) for x in Path(OUT / "ctrl_idx.txt").read_text().strip().split(",")]
with open(OUT / "ctrl_opt.xyz", "w") as out:
    for k in idx:
        base = f"ctrl_ref{k:03d}"
        xyz = (OPT / base / f"{base}.xyz").read_text().splitlines()
        n = int(xyz[0].strip())
        out.write(f"{n}\nctrl_opt ref={k} {xyz[1].strip()}\n")
        out.write("\n".join(xyz[2 : 2 + n]) + "\n")
print("ctrl_opt.xyz written")
# opt file index
with open(CSV / "opt_index.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["job", "file", "bytes"])
    for d in sorted(OPT.iterdir()):
        if not d.is_dir():
            continue
        for fp in sorted(d.iterdir()):
            w.writerow([d.name, fp.name, fp.stat().st_size])
print("index done")


# extend confusion to all 20 after-miss
def wrap180(a):
    a = float(a) % 360.0
    if a > 180.0:
        a -= 360.0
    return a


def wrapdiff(a, b):
    return abs(wrap180(float(a) - float(b)))


refs_after = list(csv.DictReader(open(CSV / "recall_after_opt.csv")))
after_miss = [int(r["ref"]) for r in refs_after if r["hit_after"] == "0"]
print("after_miss", after_miss)
# rotor tables already: reload
opt_rot = {r["seed"]: r for r in csv.DictReader(open(CSV / "opt_seed_rotor.csv"))}
# ref rotors recompute quickly from all_refs_rotor
refmap = {r["ref"]: r for r in csv.DictReader(open(DIAG_DIR / "all_refs_rotor.csv"))}
# for after-miss, nearest opt seed already in recall_after; build extended confusion incl. orig_hit flag
with open(CSV / "rotor_confusion_all20.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "ref",
            "orig_hit",
            "nearest_opt_seed",
            "cp_dist_deg",
            "miss_dO1",
            "miss_dO2",
            "miss_dO3",
            "miss_dO4",
            "miss_dO6",
            "miss_omega",
            "miss_rotamer",
            "miss_hb",
            "seed_dO1",
            "seed_dO2",
            "seed_dO3",
            "seed_dO4",
            "seed_dO6",
            "seed_omega",
            "seed_rotamer",
            "seed_hb",
            "d_dO1",
            "d_dO2",
            "d_dO3",
            "d_dO4",
            "d_dO6",
            "d_omega",
            "rotamer_changed",
            "d_hb",
            "rotor_flip_candidate",
        ]
    )
    for k in after_miss:
        r = refs_after[k]
        ns = r["best_seed_after"]
        dd = r["min_cp_after"]
        mr = refmap[str(k)]
        sr = opt_rot[str(ns)]
        ds = [round(wrapdiff(mr[n], float(sr[n])), 2) for n in ["dO1", "dO2", "dO3", "dO4", "dO6"]]
        dw = round(wrapdiff(mr["omega"], float(sr["omega"])), 2)
        rc = int(mr["rotamer"] != sr["rotamer"])
        dh = int(mr["hbond_count_lt2p5"]) - int(sr["hbond"])
        cand = int(any(80 <= x <= 160 for x in ds) or (rc == 1 and dw > 60))
        w.writerow(
            [
                k,
                r["orig_hit"],
                ns,
                dd,
                mr["dO1"],
                mr["dO2"],
                mr["dO3"],
                mr["dO4"],
                mr["dO6"],
                mr["omega"],
                mr["rotamer"],
                mr["hbond_count_lt2p5"],
                sr["dO1"],
                sr["dO2"],
                sr["dO3"],
                sr["dO4"],
                sr["dO6"],
                sr["omega"],
                sr["rotamer"],
                sr["hbond"],
                *ds,
                dw,
                rc,
                dh,
                cand,
            ]
        )
print("extended confusion done")
