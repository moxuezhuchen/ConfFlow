#!/usr/bin/env python3
"""R7-boat 04: post-xTB analysis — strict CP + RMSD + energy window + union with C_1/D.

Reads <OUT-DIR>/r7boat-run/opt_boat200 (ORCA outputs), <OUT-DIR>/r7joint-run/joint_opt.xyz,
<OUT-DIR>/r7d-out/seeds_opt.xyz. Writes csv/ + json.
Run: python3 -I <OUT-DIR>/r7boat-run/scripts/04_boat_postopt.py

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import csv
import json
import os
import re
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
OUT_DIR = Path(os.environ.get("R7_BOAT_RUN_OUT", Path.cwd() / "r7boat-run"))
OUT = OUT_DIR
R7D_DIR = Path(os.environ.get("R7_D_OUT", Path.cwd() / "r7d-out"))
R7J_DIR = Path(os.environ.get("R7_JOINT_RUN_OUT", Path.cwd() / "r7joint-run"))

sys.path.insert(0, str(REPO_ROOT))
from confflow.core.io import read_xyz_file  # noqa: E402
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    cp_distance,
    cremer_pople,
)

CSV = OUT / "csv"
OPT = OUT / "opt_boat200"
R7D = R7D_DIR
R7J = R7J_DIR


RING = [1, 2, 3, 4, 5, 6]
CP_HIT = 15.0
CUTOFF = 0.5
HA_PER_MOL = 627.509474
FIXD = FIXTURE_DIR
BOAT_MISS7 = [48, 69, 76, 110, 112, 123, 148]


def kabsch_rmsd(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a - a.mean(0)
    b = b - b.mean(0)
    h = a.T @ b
    u, _, vt = np.linalg.svd(h)
    d = float(np.sign(np.linalg.det(u @ vt)))
    r = u @ np.diag([1.0, 1.0, d]) @ vt
    return float(np.sqrt(((a @ r - b) ** 2).sum() / len(a)))


def read_orca_xyz(path):
    lines = Path(path).read_text().splitlines()
    n = int(lines[0].strip())
    comment = lines[1]
    atoms, coords = [], []
    for ln in lines[2 : 2 + n]:
        p = ln.split()
        atoms.append(p[0])
        coords.append([float(x) for x in p[1:4]])
    return {"atoms": atoms, "coords": np.array(coords), "comment": comment}


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
        atoms, coords = [], []
        for ln in lines[i + 2 : i + 2 + n]:
            p = ln.split()
            atoms.append(p[0])
            coords.append([float(x) for x in p[1:4]])
        frames.append({"atoms": atoms, "coords": np.array(coords), "comment": comment})
        i += 2 + n
    return frames


def strict_hits(opt_cp, refs_cp):
    rows = []
    for k, r in enumerate(refs_cp):
        a = CPCoords(n=6, q=r["q"], theta=r["theta"], phi=r["phi"])
        best, bi = 1e9, -1
        for i, s in enumerate(opt_cp):
            b = CPCoords(n=6, q=s["q"], theta=s["theta"], phi=s["phi"])
            d = float(cp_distance(a, b))
            if d < best:
                best, bi = d, i
        rows.append({"ref": k, "best": bi, "dist": best, "hit": int(best < CP_HIT)})
    return rows


def rmsd_hits(opt_xyz, refs_xyz, idx):
    rows = []
    for j, rf in enumerate(refs_xyz):
        dists = sorted((kabsch_rmsd(sf[idx], rf[idx]), i) for i, sf in enumerate(opt_xyz))
        best, bi = dists[0]
        n_within = sum(1 for d, _ in dists if d <= CUTOFF)
        rows.append(
            {
                "ref": j,
                "best": bi,
                "rmsd": best,
                "hit": int(best <= CUTOFF),
                "n_within": n_within,
                "redundant_extra": max(0, n_within - 1),
            }
        )
    return rows


def main():
    crest = read_xyz_file(str(FIXD / "crest_conformers.xyz"), strict=True)
    assert len(crest) == 155
    refs_xyz = [np.asarray(fr["coords"], dtype=float) for fr in crest]
    refs_cp = []
    for fr in crest:
        cp = cremer_pople(np.asarray(fr["coords"], dtype=float)[RING])
        refs_cp.append({"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi)})
    ref_atoms = crest[0]["atoms"]
    heavy_idx = list(range(12))
    all_idx = list(range(len(ref_atoms)))
    ref_E = np.array([float(str(fr["comment"]).strip().split()[0]) for fr in crest])

    # boat samples
    samples = {}
    for tag, _prefix in (("TB0", "boat_tb"), ("B1", "boat_b1")):
        samp = list(csv.DictReader(open(CSV / f"boat_{tag}_sample100.csv")))
        assert len(samp) == 100
        samples[tag] = samp

    prog = {}
    if (OPT / "progress.log").exists():
        for line in (OPT / "progress.log").read_text().splitlines():
            m = re.match(r"(\S+)\s+rc=(\d+)\s+wall_s=([\d.]+)", line.strip())
            if m:
                prog[m.group(1)] = (int(m.group(2)), float(m.group(3)))

    status_rows, opt_xyz, opt_cp, opt_E, opt_tag = [], [], [], [], []
    for tag, prefix in (("TB0", "boat_tb"), ("B1", "boat_b1")):
        for r in samples[tag]:
            leaf = int(r["boat_leaf"])
            base = f"boat_{prefix.split('_')[1]}_{leaf:03d}"
            d = OPT / base
            out = (d / f"{base}.out").read_text() if (d / f"{base}.out").exists() else ""
            hur = "THE OPTIMIZATION HAS CONVERGED" in out
            done = "OPTIMIZATION RUN DONE" in out
            term = "ORCA TERMINATED NORMALLY" in out
            es = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", out)
            fe = es[-1] if es else ""
            e0 = es[0] if es else ""
            xyz_ok = (d / f"{base}.xyz").exists()
            rc, wall = prog.get(base, ("", ""))
            ok = bool(hur and done and xyz_ok)
            status_rows.append(
                {
                    "job": base,
                    "ring": tag,
                    "boat_leaf": leaf,
                    "orca_rc": rc,
                    "wall_s": round(float(wall), 2) if wall != "" else "",
                    "converged": int(hur and done),
                    "terminated_normally": int(term),
                    "n_energy_evals": len(es),
                    "E_final_Eh": fe,
                    "E_first_Eh": e0,
                    "xyz_present": int(xyz_ok),
                    "counted": int(ok),
                }
            )
            if ok:
                xyz = read_orca_xyz(d / f"{base}.xyz")
                assert xyz["atoms"] == ref_atoms, base
                opt_xyz.append(xyz["coords"])
                cp = cremer_pople(xyz["coords"][RING])
                opt_cp.append(
                    {
                        "q": float(cp.q),
                        "theta": float(cp.theta),
                        "phi": float(cp.phi),
                        "tag": tag,
                        "leaf": leaf,
                        "job": base,
                    }
                )
                opt_E.append(float(fe))
                opt_tag.append(tag)
    with open(CSV / "boat_opt_status.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(status_rows[0].keys()))
        w.writeheader()
        w.writerows(status_rows)
    n_ok = sum(r["counted"] for r in status_rows)
    print(f"boat converged+xyz: {n_ok}/{len(status_rows)}")
    n_tb = sum(1 for r in status_rows if r["counted"] and r["ring"] == "TB0")
    n_b1 = sum(1 for r in status_rows if r["counted"] and r["ring"] == "B1")

    with open(OUT / "boat_opt.xyz", "w") as f:
        for xyz, r in zip(opt_xyz, [s for s in status_rows if s["counted"]]):
            f.write(f"{len(ref_atoms)}\n")
            f.write(
                f"boat_opt ring={r['ring']} leaf={r['boat_leaf']} {r['job']} E={r['E_final_Eh']}\n"
            )
            for el, (x, y, z) in zip(ref_atoms, xyz):
                f.write(f"{el} {x:.6f} {y:.6f} {z:.6f}\n")

    # displacement vs own parent
    sumrun = json.loads((OUT / "boat_run_summary.json").read_text())
    parents = {p["form"]: p["parent_CP"] for p in sumrun["per_leaf"]}
    pmap = {"TB0": parents["TB_0"], "B1": parents["B_1"]}
    disp_rows = []
    for xyz, r in zip(opt_xyz, [s for s in status_rows if s["counted"]]):
        cp = cremer_pople(xyz[RING])
        par = pmap[r["ring"]]
        a = CPCoords(n=6, q=float(cp.q), theta=float(cp.theta), phi=float(cp.phi))
        b = CPCoords(n=6, q=par["q"], theta=par["theta"], phi=par["phi"])
        d = float(cp_distance(a, b))
        disp_rows.append(
            {
                "job": r["job"],
                "ring": r["ring"],
                "boat_leaf": r["boat_leaf"],
                "theta": round(float(cp.theta), 3),
                "phi": round(float(cp.phi), 3),
                "q": round(float(cp.q), 4),
                "cp_dist_vs_parent_deg": round(d, 3),
            }
        )
    with open(CSV / "boat_opt_displacement.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(disp_rows[0].keys()))
        w.writeheader()
        w.writerows(disp_rows)

    # strict + RMSD for boat200 opt
    srows = strict_hits(opt_cp, refs_cp)
    shits = sum(r["hit"] for r in srows)
    with open(CSV / "boat_opt_strict_match.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ref",
                "best_opt_ord",
                "best_ring",
                "best_boat_leaf",
                "min_cp_dist_deg",
                "hit_lt15",
            ],
        )
        w.writeheader()
        for r in srows:
            o = opt_cp[r["best"]] if r["best"] >= 0 else {"tag": "", "leaf": -1}
            w.writerow(
                {
                    "ref": r["ref"],
                    "best_opt_ord": r["best"],
                    "best_ring": o["tag"],
                    "best_boat_leaf": o["leaf"],
                    "min_cp_dist_deg": round(r["dist"], 4),
                    "hit_lt15": r["hit"],
                }
            )
    h_ref = rmsd_hits(opt_xyz, refs_xyz, heavy_idx)
    a_ref = rmsd_hits(opt_xyz, refs_xyz, all_idx)
    h_rec = f"{sum(r['hit'] for r in h_ref)}/{len(h_ref)}"
    a_rec = f"{sum(r['hit'] for r in a_ref)}/{len(a_ref)}"
    for name, ref_rows in [
        ("boat_opt_rmsd_heavy_match", h_ref),
        ("boat_opt_rmsd_all_match", a_ref),
    ]:
        with open(CSV / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "ref",
                    "best_opt_ord",
                    "best_ring",
                    "best_boat_leaf",
                    "best_rmsd",
                    "hit",
                    "n_seeds_within_cutoff",
                    "redundant_extra",
                ],
            )
            w.writeheader()
            for r in ref_rows:
                o = [s for s in status_rows if s["counted"]][r["best"]]
                w.writerow(
                    {
                        "ref": r["ref"],
                        "best_opt_ord": r["best"],
                        "best_ring": o["ring"],
                        "best_boat_leaf": o["boat_leaf"],
                        "best_rmsd": round(r["rmsd"], 4),
                        "hit": r["hit"],
                        "n_seeds_within_cutoff": r["n_within"],
                        "redundant_extra": r["redundant_extra"],
                    }
                )

    # D comparisons (published numbers + recomputed union sets)
    d_after = {int(r["ref"]): r for r in csv.DictReader(open(R7D / "csv" / "recall_after_opt.csv"))}
    d_rmsd = {int(r["ref"]): r for r in csv.DictReader(open(R7D / "csv" / "rmsd_match.csv"))}
    assert len(d_after) == 155 and len(d_rmsd) == 155
    orig_miss = sorted(k for k, r in d_after.items() if r["orig_hit"] == "0")
    assert len(orig_miss) == 28, orig_miss
    lost_hit = sorted(
        k for k, r in d_after.items() if r["orig_hit"] == "1" and r["hit_after"] == "0"
    )
    assert len(lost_hit) == 18, lost_hit
    d_strict_hits = {k for k, r in d_after.items() if r["hit_after"] == "1"}
    d_heavy_hits = {k for k, r in d_rmsd.items() if r["hit_heavy_05"] == "1"}
    d_all_hits = {k for k, r in d_rmsd.items() if r["hit_all_05"] == "1"}
    assert len(d_strict_hits) == 135 and len(d_heavy_hits) == 99 and len(d_all_hits) == 33

    # C_1 joint100 opt + D38 opt xyz for union (same code path)
    jopt_frames = read_xyz_frames(str(R7J / "joint_opt.xyz"))
    assert len(jopt_frames) == 100, len(jopt_frames)
    jopt_xyz = [f["coords"] for f in jopt_frames]
    jopt_cp = []
    for f in jopt_frames:
        cp = cremer_pople(f["coords"][RING])
        jopt_cp.append({"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi)})
    dopt_frames = read_xyz_frames(str(R7D / "seeds_opt.xyz"))
    assert len(dopt_frames) == 38, len(dopt_frames)
    dopt_xyz = [f["coords"] for f in dopt_frames]
    dopt_cp = []
    for f in dopt_frames:
        cp = cremer_pople(f["coords"][RING])
        dopt_cp.append({"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi)})

    # recomputed hits with same functions (cross-check D numbers)
    j_s = strict_hits(jopt_cp, refs_cp)
    d_s = strict_hits(dopt_cp, refs_cp)
    j_h = rmsd_hits(jopt_xyz, refs_xyz, heavy_idx)
    d_h = rmsd_hits(dopt_xyz, refs_xyz, heavy_idx)
    j_a = rmsd_hits(jopt_xyz, refs_xyz, all_idx)
    d_a = rmsd_hits(dopt_xyz, refs_xyz, all_idx)
    j_strict_set = {r["ref"] for r in j_s if r["hit"]}
    d_strict_re = {r["ref"] for r in d_s if r["hit"]}
    j_heavy_set = {r["ref"] for r in j_h if r["hit"]}
    d_heavy_re = {r["ref"] for r in d_h if r["hit"]}
    j_all_set = {r["ref"] for r in j_a if r["hit"]}
    d_all_re = {r["ref"] for r in d_a if r["hit"]}

    b_strict = {r["ref"] for r in srows if r["hit"]}
    b_heavy = {r["ref"] for r in h_ref if r["hit"]}
    b_all = {r["ref"] for r in a_ref if r["hit"]}

    # unions: boat200 + C1-100 (=300); +D38 (=338) as extra
    union300_cp = jopt_cp + opt_cp
    union300_xyz = jopt_xyz + opt_xyz
    u_s = strict_hits(union300_cp, refs_cp)
    u_h = rmsd_hits(union300_xyz, refs_xyz, heavy_idx)
    u_a = rmsd_hits(union300_xyz, refs_xyz, all_idx)
    u_strict = {r["ref"] for r in u_s if r["hit"]}
    u_heavy = {r["ref"] for r in u_h if r["hit"]}
    u_all = {r["ref"] for r in u_a if r["hit"]}

    union338_cp = jopt_cp + opt_cp + dopt_cp
    union338_xyz = jopt_xyz + opt_xyz + dopt_xyz
    v_s = strict_hits(union338_cp, refs_cp)
    v_h = rmsd_hits(union338_xyz, refs_xyz, heavy_idx)
    v_a = rmsd_hits(union338_xyz, refs_xyz, all_idx)
    v_strict = {r["ref"] for r in v_s if r["hit"]}
    v_heavy = {r["ref"] for r in v_h if r["hit"]}
    v_all = {r["ref"] for r in v_a if r["hit"]}

    cov = {
        "boat7_recovered_strict": sorted(b_strict & set(BOAT_MISS7)),
        "boat7_still_miss_strict": sorted(set(BOAT_MISS7) - b_strict),
        "boat7_recovered_heavy": sorted(b_heavy & set(BOAT_MISS7)),
        "boat7_still_miss_heavy": sorted(set(BOAT_MISS7) - b_heavy),
        "boat7_recovered_all": sorted(b_all & set(BOAT_MISS7)),
        "boat7_still_miss_all": sorted(set(BOAT_MISS7) - b_all),
        "orig28_recovered_strict_boat": sorted(b_strict & set(orig_miss)),
        "orig28_still_miss_strict_boat": sorted(set(orig_miss) - b_strict),
        "lost18_recaptured_strict_boat": sorted(b_strict & set(lost_hit)),
        "lost18_still_miss_strict_boat": sorted(set(lost_hit) - b_strict),
        "union300_recovered_boat7_strict": sorted(u_strict & set(BOAT_MISS7)),
        "union300_still_miss_boat7_strict": sorted(set(BOAT_MISS7) - u_strict),
        "union300_recaptured_lost18_strict": sorted(u_strict & set(lost_hit)),
        "union300_still_miss_lost18_strict": sorted(set(lost_hit) - u_strict),
        "union300_still_miss_strict_all": sorted(set(range(155)) - u_strict),
        "union300_still_miss_heavy_all": sorted(set(range(155)) - u_heavy),
        "union300_still_miss_all_all": sorted(set(range(155)) - u_all),
    }

    # energy window
    opt_E = np.array(opt_E)
    e_min = float(opt_E.min())
    rel = (opt_E - e_min) * HA_PER_MOL
    n_in3 = int((rel <= 3.0).sum())
    # per-ring minima
    tb_E = np.array(
        [float(s["E_final_Eh"]) for s in status_rows if s["counted"] and s["ring"] == "TB0"]
    )
    b1_E = np.array(
        [float(s["E_final_Eh"]) for s in status_rows if s["counted"] and s["ring"] == "B1"]
    )
    ref_min = float(ref_E.min())
    ref_rel = (ref_E - ref_min) * HA_PER_MOL
    ref_in3 = int((ref_rel <= 3.0).sum())
    miss_rel = {k: round(float(ref_rel[k]), 2) for k in orig_miss}
    boat7_rel = {k: round(float(ref_rel[k]), 2) for k in BOAT_MISS7}
    union_still = cov["union300_still_miss_strict_all"]
    union_still_rel = {k: round(float(ref_rel[k]), 2) for k in union_still}
    with open(CSV / "boat_opt_energies.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job", "ring", "boat_leaf", "E_final_Eh", "rel_min_kcal"])
        for s, e, rr in zip([x for x in status_rows if x["counted"]], opt_E, rel):
            w.writerow([s["job"], s["ring"], s["boat_leaf"], f"{e:.8f}", round(float(rr), 3)])
    with open(CSV / "boat_crest_ref_energies.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["ref", "E_Eh", "rel_min_kcal", "orig_miss", "boat7", "union300_still_miss_strict"]
        )
        for k in range(155):
            w.writerow(
                [
                    k,
                    f"{float(ref_E[k]):.8f}",
                    round(float(ref_rel[k]), 3),
                    int(k in orig_miss),
                    int(k in BOAT_MISS7),
                    int(k in union_still),
                ]
            )

    walls = [float(s["wall_s"]) for s in status_rows if s["wall_s"] != ""]
    summary = {
        "n_opt_counted": f"{n_ok}/200",
        "n_opt_TB0": f"{n_tb}/100",
        "n_opt_B1": f"{n_b1}/100",
        "wall_s": {
            "min": round(min(walls), 2),
            "max": round(max(walls), 2),
            "mean": round(float(np.mean(walls)), 2),
        },
        "strict_postopt": {
            "boat200_opt": f"{shits}/155",
            "C1_100_opt_recomputed": f"{len(j_strict_set)}/155",
            "D_38_opt_recomputed": f"{len(d_strict_re)}/155",
            "D_38_opt_published": "135/155",
            "union300_C1boat_strict": f"{len(u_strict)}/155",
            "union338_C1boatD_strict": f"{len(v_strict)}/155",
        },
        "rmsd_postopt_cutoff0.5": {
            "heavy_boat200_opt": h_rec,
            "heavy_C1_100_recomputed": f"{len(j_heavy_set)}/155",
            "heavy_D_38_recomputed": f"{len(d_heavy_re)}/155",
            "heavy_D_38_published": "99/155",
            "heavy_union300": f"{len(u_heavy)}/155",
            "heavy_union338": f"{len(v_heavy)}/155",
            "all_boat200_opt": a_rec,
            "all_C1_100_recomputed": f"{len(j_all_set)}/155",
            "all_D_38_recomputed": f"{len(d_all_re)}/155",
            "all_D_38_published": "33/155",
            "all_union300": f"{len(u_all)}/155",
            "all_union338": f"{len(v_all)}/155",
        },
        "recompute_crosscheck": {
            "C1_strict_match_csv": "94/155",
            "C1_strict_recomputed_here": f"{len(j_strict_set)}/155",
            "D_strict_published": "135/155",
            "D_strict_recomputed_here": f"{len(d_strict_re)}/155",
            "C1_heavy_published": "94/155",
            "C1_heavy_recomputed_here": f"{len(j_heavy_set)}/155",
            "D_heavy_published": "99/155",
            "D_heavy_recomputed_here": f"{len(d_heavy_re)}/155",
            "C1_all_published": "93/155",
            "C1_all_recomputed_here": f"{len(j_all_set)}/155",
            "D_all_published": "33/155",
            "D_all_recomputed_here": f"{len(d_all_re)}/155",
        },
        "coverage_qa": {k: (v if len(v) < 60 else f"{len(v)} refs") for k, v in cov.items()}
        | {
            "n_boat7_recovered_strict": len(cov["boat7_recovered_strict"]),
            "n_orig28_recovered_strict_boat": len(cov["orig28_recovered_strict_boat"]),
            "n_lost18_recaptured_strict_boat": len(cov["lost18_recaptured_strict_boat"]),
            "n_union300_strict": len(u_strict),
            "n_union300_heavy": len(u_heavy),
            "n_union300_all": len(u_all),
        },
        "energy_window": {
            "boat_opt_Emin_Eh": round(e_min, 6),
            "boat_opt_rel_range_kcal": [0.0, round(float(rel.max()), 2)],
            "boat_opt_within3kcal": f"{n_in3}/{n_ok}",
            "boat_TB0_Emin_Eh": round(float(tb_E.min()), 6),
            "boat_B1_Emin_Eh": round(float(b1_E.min()), 6),
            "crest_ref_Emin_Eh": round(ref_min, 6),
            "crest_ref_rel_range_kcal": [0.0, round(float(ref_rel.max()), 2)],
            "crest_ref_within3kcal": f"{ref_in3}/155",
            "orig28_ref_rel_kcal": miss_rel,
            "boat7_ref_rel_kcal": boat7_rel,
            "union300_still_miss_strict_ref_rel_kcal": union_still_rel,
        },
        "calibre": "strict refs unopt CREST, cp_distance<15deg; RMSD Kabsch proper-rotation det-corrected, cutoff 0.5A, heavy=first 12, all=24; energies Eh from ORCA FINAL SINGLE POINT ENERGY vs CREST xyz comments",
    }
    with open(OUT / "boat_postopt_summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
