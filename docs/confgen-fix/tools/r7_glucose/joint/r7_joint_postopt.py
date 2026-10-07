#!/usr/bin/env python3
"""R7-joint 04: post-xTB analysis — strict CP + RMSD + energy window.

Reads <OUT-DIR>/r7joint-run/opt_joint100 (ORCA outputs). Writes csv/ + json.
Compares against D's 38 optimized seeds (135/155 strict, 99/155 heavy, 33/155 all).
Run: python3 -I <OUT-DIR>/r7joint-run/scripts/04_postopt.py

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
OUT_DIR = Path(os.environ.get("R7_JOINT_RUN_OUT", Path.cwd() / "r7joint-run"))
OUT = OUT_DIR
R7D_DIR = Path(os.environ.get("R7_D_OUT", Path.cwd() / "r7d-out"))

sys.path.insert(0, str(REPO_ROOT))
from confflow.core.io import read_xyz_file  # noqa: E402
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    cp_distance,
    cremer_pople,
)

CSV = OUT / "csv"
OPT = OUT / "opt_joint100"
R7D = R7D_DIR


RING = [1, 2, 3, 4, 5, 6]
CP_HIT = 15.0
CUTOFF = 0.5
HA_PER_MOL = 627.509474
FIXD = FIXTURE_DIR


def ring_rbar(xyz, ring):
    n = len(ring)
    return float(np.mean([np.linalg.norm(xyz[ring[(i + 1) % n]] - xyz[ring[i]]) for i in range(n)]))


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


def main():
    sample = list(csv.DictReader(open(CSV / "joint_sample100.csv")))
    assert len(sample) == 100
    _sample_leaves = [int(r["joint_leaf"]) for r in sample]

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
    # CREST reference energies from xyz comments (Eh, same calibre as D §energy_unit)
    ref_E = np.array([float(str(fr["comment"]).strip().split()[0]) for fr in crest])

    # opt status
    prog = {}
    if (OPT / "progress.log").exists():
        for line in (OPT / "progress.log").read_text().splitlines():
            m = re.match(r"(\S+)\s+rc=(\d+)\s+wall_s=([\d.]+)", line.strip())
            if m:
                prog[m.group(1)] = (int(m.group(2)), float(m.group(3)))
    status_rows, opt_xyz, opt_cp, opt_E = [], [], [], []
    for r in sample:
        leaf = int(r["joint_leaf"])
        base = f"joint_{leaf:03d}"
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
                "joint_leaf": leaf,
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
                {"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi), "leaf": leaf}
            )
            opt_E.append(float(fe))
    with open(CSV / "joint_opt_status.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(status_rows[0].keys()))
        w.writeheader()
        w.writerows(status_rows)
    n_ok = sum(r["counted"] for r in status_rows)
    print(f"converged+xyz: {n_ok}/{len(status_rows)}")

    # assemble joint_opt.xyz (counted only, sample order)
    with open(OUT / "joint_opt.xyz", "w") as f:
        for xyz, r in zip(opt_xyz, [s for s in status_rows if s["counted"]]):
            f.write(f"{len(ref_atoms)}\n")
            f.write(f"joint_opt leaf={r['joint_leaf']} {r['job']} E={r['E_final_Eh']}\n")
            for el, (x, y, z) in zip(ref_atoms, xyz):
                f.write(f"{el} {x:.6f} {y:.6f} {z:.6f}\n")

    # displacement: opt CP vs its own unopt parent (all parents share C_1 CP, but record per-leaf anyway)
    # parent CP from joint_run_summary
    parent = json.loads((OUT / "joint_run_summary.json").read_text())["parent_C1_CP"]
    disp_rows = []
    for xyz, r in zip(opt_xyz, [s for s in status_rows if s["counted"]]):
        cp = cremer_pople(xyz[RING])
        a = CPCoords(n=6, q=float(cp.q), theta=float(cp.theta), phi=float(cp.phi))
        b = CPCoords(n=6, q=parent["q"], theta=parent["theta"], phi=parent["phi"])
        d = float(cp_distance(a, b))
        disp_rows.append(
            {
                "job": r["job"],
                "joint_leaf": r["joint_leaf"],
                "theta": round(float(cp.theta), 3),
                "phi": round(float(cp.phi), 3),
                "q": round(float(cp.q), 4),
                "cp_dist_vs_parent_deg": round(d, 3),
            }
        )
    with open(CSV / "joint_opt_displacement.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(disp_rows[0].keys()))
        w.writeheader()
        w.writerows(disp_rows)

    # strict CP recall post-opt (refs unopt, same calibre)
    srows = []
    for k, r in enumerate(refs_cp):
        a = CPCoords(n=6, q=r["q"], theta=r["theta"], phi=r["phi"])
        best, bi = 1e9, -1
        for i, s in enumerate(opt_cp):
            b = CPCoords(n=6, q=s["q"], theta=s["theta"], phi=s["phi"])
            d = float(cp_distance(a, b))
            if d < best:
                best, bi = d, i
        srows.append(
            {
                "ref": k,
                "best_opt": bi,
                "best_leaf": opt_cp[bi]["leaf"] if bi >= 0 else -1,
                "dist": best,
                "hit": int(best < CP_HIT),
            }
        )
    shits = sum(r["hit"] for r in srows)
    with open(CSV / "joint_opt_strict_match.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["ref", "best_opt_ord", "best_joint_leaf", "min_cp_dist_deg", "hit_lt15"]
        )
        w.writeheader()
        for r in srows:
            w.writerow(
                {
                    "ref": r["ref"],
                    "best_opt_ord": r["best_opt"],
                    "best_joint_leaf": r["best_leaf"],
                    "min_cp_dist_deg": round(r["dist"], 4),
                    "hit_lt15": r["hit"],
                }
            )

    # RMSD heavy/all post-opt
    def rmsd_recall(idx):
        ref_rows = []
        for j, rf in enumerate(refs_xyz):
            dists = sorted((kabsch_rmsd(sf[idx], rf[idx]), i) for i, sf in enumerate(opt_xyz))
            best, bi = dists[0]
            n_within = sum(1 for d, _ in dists if d <= CUTOFF)
            ref_rows.append(
                {
                    "ref": j,
                    "best_opt": bi,
                    "best_leaf": [s for s in status_rows if s["counted"]][bi]["joint_leaf"],
                    "rmsd": best,
                    "hit": int(best <= CUTOFF),
                    "n_within": n_within,
                    "redundant_extra": max(0, n_within - 1),
                }
            )
        return ref_rows

    h_ref = rmsd_recall(heavy_idx)
    a_ref = rmsd_recall(all_idx)
    h_rec = f"{sum(r['hit'] for r in h_ref)}/{len(h_ref)}"
    a_rec = f"{sum(r['hit'] for r in a_ref)}/{len(a_ref)}"
    for name, ref_rows in [
        ("joint_opt_rmsd_heavy_match", h_ref),
        ("joint_opt_rmsd_all_match", a_ref),
    ]:
        with open(CSV / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "ref",
                    "best_opt_ord",
                    "best_joint_leaf",
                    "best_rmsd",
                    "hit",
                    "n_seeds_within_cutoff",
                    "redundant_extra",
                ],
            )
            w.writeheader()
            for r in ref_rows:
                w.writerow(
                    {
                        "ref": r["ref"],
                        "best_opt_ord": r["best_opt"],
                        "best_joint_leaf": r["best_leaf"],
                        "best_rmsd": round(r["rmsd"], 4),
                        "hit": r["hit"],
                        "n_seeds_within_cutoff": r["n_within"],
                        "redundant_extra": r["redundant_extra"],
                    }
                )

    # D comparisons: load D csvs
    d_after = {int(r["ref"]): r for r in csv.DictReader(open(R7D / "csv" / "recall_after_opt.csv"))}
    d_rmsd = {int(r["ref"]): r for r in csv.DictReader(open(R7D / "csv" / "rmsd_match.csv"))}
    assert len(d_after) == 155 and len(d_rmsd) == 155
    orig_miss = sorted(k for k, r in d_after.items() if r["orig_hit"] == "0")
    assert len(orig_miss) == 28
    lost_hit = sorted(
        k for k, r in d_after.items() if r["orig_hit"] == "1" and r["hit_after"] == "0"
    )
    assert len(lost_hit) == 18
    d_strict_hits = {k for k, r in d_after.items() if r["hit_after"] == "1"}
    d_heavy_hits = {k for k, r in d_rmsd.items() if r["hit_heavy_05"] == "1"}
    d_all_hits = {k for k, r in d_rmsd.items() if r["hit_all_05"] == "1"}
    j_strict = {r["ref"] for r in srows if r["hit"]}
    j_heavy = {r["ref"] for r in h_ref if r["hit"]}
    j_all = {r["ref"] for r in a_ref if r["hit"]}
    cov = {
        "orig28_recovered_strict": sorted(j_strict & set(orig_miss)),
        "orig28_still_miss_strict": sorted(set(orig_miss) - j_strict),
        "lost18_recaptured_strict": sorted(j_strict & set(lost_hit)),
        "lost18_still_miss_strict": sorted(set(lost_hit) - j_strict),
        "orig28_recovered_heavy": sorted(j_heavy & set(orig_miss)),
        "orig28_still_miss_heavy": sorted(set(orig_miss) - j_heavy),
        "orig28_recovered_all": sorted(j_all & set(orig_miss)),
        "orig28_still_miss_all": sorted(set(orig_miss) - j_all),
        "D_strict_135_only": sorted(d_strict_hits - j_strict),
        "joint_strict_only_vs_D": sorted(j_strict - d_strict_hits),
        "D_heavy_99_only": sorted(d_heavy_hits - j_heavy),
        "joint_heavy_only_vs_D": sorted(j_heavy - d_heavy_hits),
        "D_all_33_only": sorted(d_all_hits - j_all),
        "joint_all_only_vs_D": sorted(j_all - d_all_hits),
    }

    # energy window: joint opt Eh rel min (kcal/mol), within 3 kcal; CREST refs same calibre
    opt_E = np.array(opt_E)
    e_min = float(opt_E.min())
    rel = (opt_E - e_min) * HA_PER_MOL
    n_in3 = int((rel <= 3.0).sum())
    ref_min = float(ref_E.min())
    ref_rel = (ref_E - ref_min) * HA_PER_MOL
    ref_in3 = int((ref_rel <= 3.0).sum())
    # miss refs' relative energies (which misses are high-energy?)
    miss_rel = {k: round(float(ref_rel[k]), 2) for k in orig_miss}
    still_strict = cov["orig28_still_miss_strict"]
    still_rel = {k: miss_rel[k] for k in still_strict}
    with open(CSV / "joint_opt_energies.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["job", "joint_leaf", "E_final_Eh", "rel_min_kcal"])
        for s, e, rr in zip([x for x in status_rows if x["counted"]], opt_E, rel):
            w.writerow([s["job"], s["joint_leaf"], f"{e:.8f}", round(float(rr), 3)])
    with open(CSV / "crest_ref_energies.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ref", "E_Eh", "rel_min_kcal", "orig_miss", "still_miss_strict_joint"])
        for k in range(155):
            w.writerow(
                [
                    k,
                    f"{float(ref_E[k]):.8f}",
                    round(float(ref_rel[k]), 3),
                    int(k in orig_miss),
                    int(k in still_strict),
                ]
            )

    summary = {
        "n_opt_counted": f"{n_ok}/100",
        "strict_postopt": {
            "joint100_opt": f"{shits}/155",
            "D_38_opt": "135/155",
            "note": "different seed budgets (100 single-ring descendants vs 38 diverse rings); compare coverage sets, not just totals",
        },
        "rmsd_postopt_cutoff0.5": {
            "heavy_joint100_opt": h_rec,
            "heavy_D_38_opt": "99/155",
            "all_joint100_opt": a_rec,
            "all_D_38_opt": "33/155",
        },
        "coverage": {k: (v if len(v) < 60 else f"{len(v)} refs") for k, v in cov.items()}
        | {
            "n_orig28_recovered_strict": len(cov["orig28_recovered_strict"]),
            "n_lost18_recaptured_strict": len(cov["lost18_recaptured_strict"]),
            "n_orig28_recovered_heavy": len(cov["orig28_recovered_heavy"]),
            "n_orig28_recovered_all": len(cov["orig28_recovered_all"]),
        },
        "energy_window": {
            "joint_opt_Emin_Eh": round(e_min, 6),
            "joint_opt_rel_range_kcal": [0.0, round(float(rel.max()), 2)],
            "joint_opt_within3kcal": f"{n_in3}/{n_ok}",
            "crest_ref_Emin_Eh": round(ref_min, 6),
            "crest_ref_rel_range_kcal": [0.0, round(float(ref_rel.max()), 2)],
            "crest_ref_within3kcal": f"{ref_in3}/155",
            "orig28_ref_rel_kcal": miss_rel,
            "still_miss_strict_ref_rel_kcal": still_rel,
        },
        "calibre": "strict refs unopt CREST, cp_distance<15deg; RMSD Kabsch proper-rotation det-corrected, cutoff 0.5A, heavy=first 12, all=24; energies Eh from ORCA FINAL SINGLE POINT ENERGY vs CREST xyz comments",
    }
    with open(OUT / "joint_postopt_summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
