#!/usr/bin/env python3
"""R7-D post-optimization analysis (read-only).

Run: python3 -I <OUT-DIR>/r7d-out/scripts/02_analyze.py

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import csv
import json
import math
import os
import sys
from pathlib import Path

import numpy as np


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
DATA_DIR = _require_dir("R7_DATA_DIR", "未发布的 root 全体系输入 beta_d_glucopyranose 目录")
FIXTURE_CREST = FIXTURE_DIR / "crest_conformers.xyz"

sys.path.insert(0, str(REPO_ROOT))
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    cp_distance,
    cremer_pople,
)

OUT = OUT_DIR
CSV = OUT / "csv"
CSV.mkdir(parents=True, exist_ok=True)
OPT = OUT / "opt_all"
RING = [1, 2, 3, 4, 5, 6]
C1, C2, C3, C4, C5, C6 = 5, 4, 3, 2, 1, 0
O5, O1, O2, O3, O4, O6 = 6, 7, 8, 9, 10, 11
H19, H20, H21, H22, H23 = 19, 20, 21, 22, 23
OM = (O5, C5, C6, O6)
OHSET = {
    "dO1": (H19, O1, C1, C2),
    "dO2": (H20, O2, C2, C3),
    "dO3": (H21, O3, C3, C4),
    "dO4": (H22, O4, C4, C5),
    "dO6": (H23, O6, C6, C5),
}


def read_xyz_frames(path):
    lines = Path(path).read_text().splitlines()
    frames = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        n = int(lines[i].strip())
        comment = lines[i + 1] if i + 1 < len(lines) else ""
        atoms = []
        coords = []
        for ln in lines[i + 2 : i + 2 + n]:
            p = ln.split()
            atoms.append(p[0])
            coords.append([float(x) for x in p[1:4]])
        frames.append({"atoms": atoms, "coords": np.array(coords), "comment": comment})
        i += 2 + n
    return frames


def read_orca_xyz(path):
    # ORCA .xyz: first line natoms, second comment, then coords
    lines = Path(path).read_text().splitlines()
    n = int(lines[0].strip())
    comment = lines[1]
    atoms = []
    coords = []
    for ln in lines[2 : 2 + n]:
        p = ln.split()
        atoms.append(p[0])
        coords.append([float(x) for x in p[1:4]])
    return {"atoms": atoms, "coords": np.array(coords), "comment": comment}


def dihedral(p0, p1, p2, p3):
    b0 = np.asarray(p1) - np.asarray(p0)
    v1 = np.asarray(p2) - np.asarray(p1)
    v2 = np.asarray(p3) - np.asarray(p2)
    n0 = np.cross(b0, v1)
    n1 = np.cross(v1, v2)
    ax = float(np.linalg.norm(v1))
    if ax < 1e-12:
        return float("nan")
    u = v1 / ax
    y = float(np.dot(np.cross(n0, n1), u))
    x = float(np.dot(n0, n1))
    if x == 0.0 and y == 0.0:
        return float("nan")
    a = math.degrees(math.atan2(y, x)) % 360.0
    if a > 180.0:
        a -= 360.0
    return a


def wrap180(a):
    a = float(a) % 360.0
    if a > 180.0:
        a -= 360.0
    return a


def wrapdiff(a, b):
    return abs(wrap180(float(a) - float(b)))


def rotamer(om):
    if om is None or (isinstance(om, float) and math.isnan(om)):
        return "?"
    if -120.0 <= om < 0.0:
        return "gg"
    if 0.0 <= om < 120.0:
        return "gt"
    return "tg"


def hbond_stats(xyz):
    donors = [(H19, O1), (H20, O2), (H21, O3), (H22, O4), (H23, O6)]
    acc_all = [O5, O1, O2, O3, O4, O6]
    pairs = []
    for h, dO in donors:
        best = (None, 1e9)
        for a in acc_all:
            if a == dO:
                continue
            dd = float(np.linalg.norm(xyz[h] - xyz[a]))
            if dd < best[1]:
                best = (a, dd)
        pairs.append((h, dO, best[0], best[1]))
    cnt = sum(1 for _, _, _, dd in pairs if dd < 2.5)
    mind = min(dd for _, _, _, dd in pairs)
    return pairs, cnt, mind


def kabsch_rmsd(a, b):
    # proper rotation only (det correction), same calibre as match_after_opt.py DRAFT
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a = a - a.mean(0)
    b = b - b.mean(0)
    h = a.T @ b
    u, _, vt = np.linalg.svd(h)
    d = float(np.sign(np.linalg.det(u @ vt)))
    r = u @ np.diag([1.0, 1.0, d]) @ vt
    return float(np.sqrt(((a @ r - b) ** 2).sum() / len(a)))


def ring_rbar(xyz, ring):
    n = len(ring)
    return float(np.mean([np.linalg.norm(xyz[ring[(i + 1) % n]] - xyz[ring[i]]) for i in range(n)]))


# ---- load seeds before ----
seed_frames = read_xyz_frames(DATA_DIR / "seeds.xyz")
assert len(seed_frames) == 38
seed_before = []
for _s, fr in enumerate(seed_frames):
    cp = cremer_pople(fr["coords"][RING])
    rb = ring_rbar(fr["coords"], RING)
    seed_before.append(
        {"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi), "rbar": rb}
    )
# ---- load refs (unoptimized CREST) ----
ref_frames = read_xyz_frames(FIXTURE_CREST)
assert len(ref_frames) == 155
refs = []
for _k, fr in enumerate(ref_frames):
    cp = cremer_pople(fr["coords"][RING])
    rb = ring_rbar(fr["coords"], RING)
    refs.append({"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi), "rbar": rb})
# ---- original match ----
orig = []
with open(DATA_DIR / "reference_match.csv") as f:
    for row in csv.DictReader(f):
        orig.append(
            {
                "ref": int(row["ref"]),
                "best_seed": int(row["best_seed"]),
                "min_cp": float(row["min_cp_dist_deg"]),
                "hit": int(row["hit_lt15"]),
            }
        )
orig_miss = [r["ref"] for r in orig if r["hit"] == 0]
assert len(orig_miss) == 28, len(orig_miss)
print("orig_miss=", orig_miss)

# ---- opt seeds after ----
opt_seeds = []
for s in range(38):
    base = f"seed_{s:02d}"
    xyz = read_orca_xyz(OPT / base / f"{base}.xyz")
    assert xyz["atoms"] == seed_frames[0]["atoms"], base
    cp = cremer_pople(xyz["coords"][RING])
    rb = ring_rbar(xyz["coords"], RING)
    opt_seeds.append(
        {
            "seed": s,
            "q": float(cp.q),
            "theta": float(cp.theta),
            "phi": float(cp.phi),
            "rbar": rb,
            "xyz": xyz["coords"],
        }
    )
# ---- controls ----
ctrl_idx = [int(x) for x in Path(OUT / "ctrl_idx.txt").read_text().strip().split(",")]
ctrl_rows = []
for k in ctrl_idx:
    base = f"ctrl_ref{k:03d}"
    xyz = read_orca_xyz(OPT / base / f"{base}.xyz")
    cp = cremer_pople(xyz["coords"][RING])
    rb = ring_rbar(xyz["coords"], RING)
    # before
    b = refs[k]
    a = CPCoords(n=6, q=0.6, theta=b["theta"], phi=b["phi"])
    c = CPCoords(n=6, q=0.6, theta=float(cp.theta), phi=float(cp.phi))
    drift = float(cp_distance(a, c))
    # RMSD heavy + all
    heavy = list(range(12))
    rh = kabsch_rmsd(xyz["coords"][heavy], ref_frames[k]["coords"][heavy])
    ra = kabsch_rmsd(xyz["coords"], ref_frames[k]["coords"])
    ctrl_rows.append(
        {
            "ref": k,
            "theta_before": round(b["theta"], 3),
            "phi_before": round(b["phi"], 3),
            "q_before": round(b["q"], 4),
            "theta_after": round(float(cp.theta), 3),
            "phi_after": round(float(cp.phi), 3),
            "q_after": round(float(cp.q), 4),
            "cp_drift_deg": round(drift, 3),
            "rmsd_heavy": round(rh, 4),
            "rmsd_all": round(ra, 4),
        }
    )
with open(CSV / "ctrl_stability.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(ctrl_rows[0].keys()))
    w.writeheader()
    w.writerows(ctrl_rows)
print("ctrl done")

# ---- displacement csv ----
dis_rows = []
with open(CSV / "opt_displacement.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "seed",
            "theta_before",
            "phi_before",
            "q_before",
            "theta_after",
            "phi_after",
            "q_after",
            "dtheta",
            "dphi_circ",
            "dq",
            "cp_dist_deg",
        ]
    )
    for s in range(38):
        b = seed_before[s]
        a = opt_seeds[s]
        dth = abs(a["theta"] - b["theta"])
        dph = wrapdiff(a["phi"], b["phi"])
        dq = a["q"] - b["q"]
        ca = CPCoords(n=6, q=0.6, theta=b["theta"], phi=b["phi"])
        cb = CPCoords(n=6, q=0.6, theta=a["theta"], phi=a["phi"])
        dd = float(cp_distance(ca, cb))
        w.writerow(
            [
                s,
                round(b["theta"], 3),
                round(b["phi"], 3),
                round(b["q"], 4),
                round(a["theta"], 3),
                round(a["phi"], 3),
                round(a["q"], 4),
                round(dth, 3),
                round(dph, 3),
                round(dq, 4),
                round(dd, 3),
            ]
        )

# ---- recall after opt (strict <15, refs unoptimized) ----
# D_after[j] = min_i cp_distance(ref_j, optseed_i); best seed
recall_rows = []
for j in range(155):
    rj = CPCoords(n=6, q=0.6, theta=refs[j]["theta"], phi=refs[j]["phi"])
    dists = []
    for s in range(38):
        si = CPCoords(n=6, q=0.6, theta=opt_seeds[s]["theta"], phi=opt_seeds[s]["phi"])
        dists.append((float(cp_distance(rj, si)), s))
    dists.sort()
    best = dists[0]
    recall_rows.append(
        {
            "ref": j,
            "best_seed_after": best[1],
            "min_cp_after": round(best[0], 3),
            "hit_after": int(best[0] < 15.0),
        }
    )
# merge with orig
with open(CSV / "recall_after_opt.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "ref",
            "ref_theta",
            "ref_phi",
            "ref_q",
            "orig_best_seed",
            "orig_min_cp",
            "orig_hit",
            "best_seed_after",
            "min_cp_after",
            "hit_after",
        ]
    )
    for j in range(155):
        w.writerow(
            [
                j,
                round(refs[j]["theta"], 3),
                round(refs[j]["phi"], 3),
                round(refs[j]["q"], 4),
                orig[j]["best_seed"],
                orig[j]["min_cp"],
                orig[j]["hit"],
                recall_rows[j]["best_seed_after"],
                recall_rows[j]["min_cp_after"],
                recall_rows[j]["hit_after"],
            ]
        )
n_hit_after = sum(r["hit_after"] for r in recall_rows)
print(f"recall_after {n_hit_after}/155 (orig 127/155)")
# miss resolution detail: for each of 28 orig miss, distance after + recovered?
with open(CSV / "miss_resolution.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "ref",
            "ref_theta",
            "ref_phi",
            "orig_best_seed",
            "orig_min_cp",
            "best_seed_after",
            "min_cp_after",
            "recovered_lt15",
        ]
    )
    for k in orig_miss:
        w.writerow(
            [
                k,
                round(refs[k]["theta"], 3),
                round(refs[k]["phi"], 3),
                orig[k]["best_seed"],
                orig[k]["min_cp"],
                recall_rows[k]["best_seed_after"],
                recall_rows[k]["min_cp_after"],
                int(recall_rows[k]["min_cp_after"] < 15.0),
            ]
        )
still_miss = [k for k in orig_miss if recall_rows[k]["min_cp_after"] >= 15.0]
newly_hit = [k for k in orig_miss if recall_rows[k]["min_cp_after"] < 15.0]
print(f"recovered {len(newly_hit)}/{len(orig_miss)}, still {len(still_miss)}: {still_miss}")

# ---- RMSD (Kabsch heavy 0.5A primary; all-atom secondary) ----
heavy = list(range(12))
rmsd_ref_rows = []
rmsd_seed_rows = []
for j in range(155):
    rc = np.asarray(ref_frames[j]["coords"])
    dh = sorted((kabsch_rmsd(opt_seeds[s]["xyz"][heavy], rc[heavy]), s) for s in range(38))
    da = sorted((kabsch_rmsd(opt_seeds[s]["xyz"], rc), s) for s in range(38))
    bh, sh = dh[0]
    ba, sa = da[0]
    rmsd_ref_rows.append(
        {
            "ref": j,
            "best_seed_heavy": sh,
            "best_rmsd_heavy": round(bh, 4),
            "hit_heavy_05": int(bh <= 0.5),
            "best_seed_all": sa,
            "best_rmsd_all": round(ba, 4),
            "hit_all_05": int(ba <= 0.5),
            "n_seeds_heavy_within": sum(1 for d, _ in dh if d <= 0.5),
        }
    )
with open(CSV / "rmsd_match.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rmsd_ref_rows[0].keys()))
    w.writeheader()
    w.writerows(rmsd_ref_rows)
for s in range(38):
    sc = np.asarray(opt_seeds[s]["xyz"])
    dh = sorted(
        (kabsch_rmsd(sc[heavy], np.asarray(ref_frames[j]["coords"])[heavy]), j) for j in range(155)
    )
    bh, bj = dh[0]
    rmsd_seed_rows.append(
        {
            "seed": s,
            "best_ref_heavy": bj,
            "best_rmsd_heavy": round(bh, 4),
            "matched_heavy_05": int(bh <= 0.5),
        }
    )
with open(CSV / "rmsd_seed.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rmsd_seed_rows[0].keys()))
    w.writeheader()
    w.writerows(rmsd_seed_rows)
rhits = sum(r["hit_heavy_05"] for r in rmsd_ref_rows)
raits = sum(r["hit_all_05"] for r in rmsd_ref_rows)
print(f"rmsd heavy recall {rhits}/155; all-atom {raits}/155")


# ---- rotor for opt seeds + refs; confusion for still miss ----
def rotor_of(xyz):
    d = {nm: float(dihedral(xyz[a], xyz[b], xyz[c], xyz[e])) for nm, (a, b, c, e) in OHSET.items()}
    om = float(dihedral(xyz[OM[0]], xyz[OM[1]], xyz[OM[2]], xyz[OM[3]]))
    _, cnt, mind = hbond_stats(xyz)
    return d, om, rotamer(om), cnt, float(mind)


# opt seed rotors
opt_rot = []
for s in range(38):
    d, om, rt, cnt, mind = rotor_of(opt_seeds[s]["xyz"])
    opt_rot.append(
        {
            "seed": s,
            **{k: round(v, 2) for k, v in d.items()},
            "omega": round(om, 2),
            "rotamer": rt,
            "hbond": cnt,
            "minHO": round(mind, 3),
        }
    )
with open(CSV / "opt_seed_rotor.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(opt_rot[0].keys()))
    w.writeheader()
    w.writerows(opt_rot)
ref_rot = []
for j in range(155):
    d, om, rt, cnt, mind = rotor_of(np.asarray(ref_frames[j]["coords"]))
    ref_rot.append(
        {
            "ref": j,
            **{k: round(v, 2) for k, v in d.items()},
            "omega": round(om, 2),
            "rotamer": rt,
            "hbond": cnt,
            "minHO": round(mind, 3),
        }
    )
# confusion: for each still-miss ref, nearest opt seed by CP, dihedral diffs
with open(CSV / "rotor_confusion.csv", "w", newline="") as f:
    cols = [
        "ref",
        "nearest_opt_seed",
        "cp_dist_deg",
        "ref_theta",
        "seed_theta",
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
    w = csv.writer(f)
    w.writerow(cols)
    for k in still_miss:
        # nearest opt seed by CP
        rj = CPCoords(n=6, q=0.6, theta=refs[k]["theta"], phi=refs[k]["phi"])
        dists = sorted(
            (
                float(
                    cp_distance(
                        rj,
                        CPCoords(n=6, q=0.6, theta=opt_seeds[s]["theta"], phi=opt_seeds[s]["phi"]),
                    )
                ),
                s,
            )
            for s in range(38)
        )
        dd, ns = dists[0]
        mr = ref_rot[k]
        sr = opt_rot[ns]
        ds = [round(wrapdiff(mr[n], sr[n]), 2) for n in ["dO1", "dO2", "dO3", "dO4", "dO6"]]
        dw = round(wrapdiff(mr["omega"], sr["omega"]), 2)
        rc = int(mr["rotamer"] != sr["rotamer"])
        dh = mr["hbond"] - sr["hbond"]
        # flip candidate: any single OH diff in [100,140] (gauche flip ~120) or omega rotamer change with dw>80
        cand = int(any(80 <= x <= 160 for x in ds) or (rc == 1 and dw > 60))
        w.writerow(
            [
                k,
                ns,
                round(dd, 3),
                round(refs[k]["theta"], 3),
                round(opt_seeds[ns]["theta"], 3),
                mr["dO1"],
                mr["dO2"],
                mr["dO3"],
                mr["dO4"],
                mr["dO6"],
                mr["omega"],
                mr["rotamer"],
                mr["hbond"],
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
# also confusion for newly-hit (recovered) for comparison? quick stats printed
print("still miss confusion written; n=", len(still_miss))
# summary stats for report
stats = {
    "recall_after": f"{n_hit_after}/155",
    "recall_orig": "127/155",
    "recovered": len(newly_hit),
    "still": len(still_miss),
    "still_list": still_miss,
    "newly_hit": newly_hit,
    "rmsd_heavy": f"{rhits}/155",
    "rmsd_all": f"{raits}/155",
    "ctrl_max_drift": round(max(r["cp_drift_deg"] for r in ctrl_rows), 3),
    "ctrl_max_rmsdh": round(max(r["rmsd_heavy"] for r in ctrl_rows), 4),
}
Path(CSV / "_summary.json").write_text(json.dumps(stats, indent=1))
print(stats)
