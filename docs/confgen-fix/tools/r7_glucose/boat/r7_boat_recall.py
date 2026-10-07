#!/usr/bin/env python3
"""R7-boat 02: unoptimized joint recall — strict CP + RMSD heavy/all (combined 1458).

Compares combined TB_0x729+B_1x729 vs 155 unopt CREST refs: strict CP + RMSD.
Also per-leaf breakdowns and 38-seed anchor.
Run: python3 -I <OUT-DIR>/r7boat-run/scripts/02_boat_recall.py

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import csv
import json
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
OUT_DIR = Path(os.environ.get("R7_BOAT_RUN_OUT", Path.cwd() / "r7boat-run"))
OUT = OUT_DIR
DATA_DIR = _require_dir("R7_DATA_DIR", "未发布的 root 全体系输入 beta_d_glucopyranose 目录")

sys.path.insert(0, str(REPO_ROOT))
from confflow.core.io import read_xyz_file  # noqa: E402
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    cp_distance,
    cremer_pople,
)

CSV = OUT / "csv"


RING = [1, 2, 3, 4, 5, 6]
CP_HIT = 15.0
CUTOFF = 0.5
FIXD = FIXTURE_DIR
PRIOR = DATA_DIR


def kabsch_rmsd(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a - a.mean(0)
    b = b - b.mean(0)
    h = a.T @ b
    u, _, vt = np.linalg.svd(h)
    d = float(np.sign(np.linalg.det(u @ vt)))
    r = u @ np.diag([1.0, 1.0, d]) @ vt
    assert float(np.linalg.det(r)) > 0.0
    return float(np.sqrt(((a @ r - b) ** 2).sum() / len(a)))


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


def strict_match(refs_cp, seeds_cp):
    rows = []
    for k, r in enumerate(refs_cp):
        a = CPCoords(n=6, q=r["q"], theta=r["theta"], phi=r["phi"])
        best, bi = 1e9, -1
        for i, s in enumerate(seeds_cp):
            b = CPCoords(n=6, q=s["q"], theta=s["theta"], phi=s["phi"])
            d = float(cp_distance(a, b))
            if d < best:
                best, bi = d, i
        rows.append({"ref": k, "best": bi, "dist": best, "hit": int(best < CP_HIT)})
    return rows


def rmsd_match(seeds_xyz, refs_xyz, idx, cutoff=0.5):
    ref_rows, seed_rows = [], []
    for j, rf in enumerate(refs_xyz):
        dists = sorted((kabsch_rmsd(sf[idx], rf[idx]), i) for i, sf in enumerate(seeds_xyz))
        best, bi = dists[0]
        n_within = sum(1 for d, _ in dists if d <= cutoff)
        ref_rows.append(
            {
                "ref": j,
                "best_seed": bi,
                "best_rmsd": round(best, 4),
                "hit": int(best <= cutoff),
                "n_within": n_within,
                "redundant_extra": max(0, n_within - 1),
            }
        )
    for i, sf in enumerate(seeds_xyz):
        dists = sorted((kabsch_rmsd(sf[idx], rf[idx]), j) for j, rf in enumerate(refs_xyz))
        best, bj = dists[0]
        seed_rows.append(
            {"seed": i, "best_ref": bj, "best_rmsd": round(best, 4), "matched": int(best <= cutoff)}
        )
    hits = sum(r["hit"] for r in ref_rows)
    return ref_rows, seed_rows, f"{hits}/{len(ref_rows)}"


def main():
    crest = read_xyz_file(str(FIXD / "crest_conformers.xyz"), strict=True)
    assert len(crest) == 155
    refs_cp, refs_xyz = [], []
    for fr in crest:
        xyz = np.asarray(fr["coords"], dtype=float)
        refs_xyz.append(xyz)
        cp = cremer_pople(xyz[RING])
        refs_cp.append({"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi)})
    heavy_idx = list(range(12))
    all_idx = list(range(len(crest[0]["atoms"])))

    tb = read_xyz_frames(str(OUT / "boat_TB0_leaves.xyz"))
    b1 = read_xyz_frames(str(OUT / "boat_B1_leaves.xyz"))
    assert len(tb) == 729 and len(b1) == 729
    assert tb[0]["atoms"] == crest[0]["atoms"] and b1[0]["atoms"] == crest[0]["atoms"]
    tb_xyz = [f["coords"] for f in tb]
    b1_xyz = [f["coords"] for f in b1]
    both_xyz = tb_xyz + b1_xyz

    def cp_of(frames):
        out = []
        for f in frames:
            cp = cremer_pople(f["coords"][RING])
            out.append({"q": float(cp.q), "theta": float(cp.theta), "phi": float(cp.phi)})
        return out

    tb_cp, b1_cp = cp_of(tb), cp_of(b1)
    both_cp = tb_cp + b1_cp

    seeds38 = read_xyz_frames(str(PRIOR / "seeds.xyz"))
    assert len(seeds38) == 38
    seeds38_xyz = [f["coords"] for f in seeds38]
    seeds38_cp = cp_of(seeds38)

    jrows = strict_match(refs_cp, both_cp)
    trows = strict_match(refs_cp, tb_cp)
    brows = strict_match(refs_cp, b1_cp)
    srows = strict_match(refs_cp, seeds38_cp)
    # single parents = first leaf each (all same CP per leaf)
    t1 = strict_match(refs_cp, [tb_cp[0]])
    b1s = strict_match(refs_cp, [b1_cp[0]])
    jhits = sum(r["hit"] for r in jrows)
    thits = sum(r["hit"] for r in trows)
    bhits = sum(r["hit"] for r in brows)
    shits = sum(r["hit"] for r in srows)
    t1h = sum(r["hit"] for r in t1)
    b1h = sum(r["hit"] for r in b1s)
    assert shits == 127, shits

    with open(CSV / "boat_strict_match.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ref",
                "best_combined",
                "min_cp_combined_deg",
                "hit_combined_lt15",
                "best_TB0",
                "min_cp_TB0_deg",
                "hit_TB0_lt15",
                "best_B1",
                "min_cp_B1_deg",
                "hit_B1_lt15",
                "best_seed38",
                "min_cp_38_deg",
                "hit38_lt15",
            ],
        )
        w.writeheader()
        for jr, tr, br, sr in zip(jrows, trows, brows, srows):
            # combined index: 0-728 TB0, 729-1457 B1
            w.writerow(
                {
                    "ref": jr["ref"],
                    "best_combined": jr["best"],
                    "min_cp_combined_deg": round(jr["dist"], 4),
                    "hit_combined_lt15": jr["hit"],
                    "best_TB0": tr["best"],
                    "min_cp_TB0_deg": round(tr["dist"], 4),
                    "hit_TB0_lt15": tr["hit"],
                    "best_B1": br["best"],
                    "min_cp_B1_deg": round(br["dist"], 4),
                    "hit_B1_lt15": br["hit"],
                    "best_seed38": sr["best"],
                    "min_cp_38_deg": round(sr["dist"], 4),
                    "hit38_lt15": sr["hit"],
                }
            )

    jh_ref, jh_seed, jh_rec = rmsd_match(both_xyz, refs_xyz, heavy_idx, CUTOFF)
    ja_ref, ja_seed, ja_rec = rmsd_match(both_xyz, refs_xyz, all_idx, CUTOFF)
    sh_ref, sh_seed, sh_rec = rmsd_match(seeds38_xyz, refs_xyz, heavy_idx, CUTOFF)
    sa_ref, sa_seed, sa_rec = rmsd_match(seeds38_xyz, refs_xyz, all_idx, CUTOFF)
    th_ref, _, th_rec = rmsd_match(tb_xyz, refs_xyz, heavy_idx, CUTOFF)
    bh_ref, _, bh_rec = rmsd_match(b1_xyz, refs_xyz, heavy_idx, CUTOFF)
    ta_ref, _, ta_rec = rmsd_match(tb_xyz, refs_xyz, all_idx, CUTOFF)
    ba_ref, _, ba_rec = rmsd_match(b1_xyz, refs_xyz, all_idx, CUTOFF)

    for name, ref_rows in [
        ("boat_rmsd_heavy_match", jh_ref),
        ("boat_rmsd_all_match", ja_ref),
        ("boat_seed38_rmsd_heavy_match", sh_ref),
        ("boat_seed38_rmsd_all_match", sa_ref),
    ]:
        with open(CSV / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(
                f,
                fieldnames=[
                    "ref",
                    "best_seed",
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
                        "best_seed": r["best_seed"],
                        "best_rmsd": r["best_rmsd"],
                        "hit": r["hit"],
                        "n_seeds_within_cutoff": r["n_within"],
                        "redundant_extra": r["redundant_extra"],
                    }
                )

    jh_set = {r["ref"] for r in jh_ref if r["hit"]}
    sh_set = {r["ref"] for r in sh_ref if r["hit"]}
    ja_set = {r["ref"] for r in ja_ref if r["hit"]}
    sa_set = {r["ref"] for r in sa_ref if r["hit"]}

    summary = {
        "strict_CP_lt15": {
            "combined1458_unopt": f"{jhits}/155",
            "TB0_729_unopt": f"{thits}/155",
            "B1_729_unopt": f"{bhits}/155",
            "single_TB0_parent": f"{t1h}/155",
            "single_B1_parent": f"{b1h}/155",
            "seed38_unopt": f"{shits}/155",
            "combined_minus_seed38": jhits - shits,
            "verified_TB0_joint_eq_parent": bool(thits == t1h),
            "verified_B1_joint_eq_parent": bool(bhits == b1h),
            "expectation": "each 729 shares its parent ring CP (drift 0.0); combined==union of two single-parent hits",
        },
        "rmsd_unopt_vs_unopt_cutoff0.5": {
            "heavy_combined1458": jh_rec,
            "heavy_seed38": sh_rec,
            "heavy_TB0": th_rec,
            "heavy_B1": bh_rec,
            "all_combined1458": ja_rec,
            "all_seed38": sa_rec,
            "all_TB0": ta_rec,
            "all_B1": ba_rec,
            "heavy_combined_only": sorted(jh_set - sh_set),
            "heavy_seed38_only": sorted(sh_set - jh_set),
            "heavy_gain": len(jh_set - sh_set),
            "heavy_loss": len(sh_set - jh_set),
            "all_combined_only": sorted(ja_set - sa_set),
            "all_seed38_only": sorted(sa_set - ja_set),
            "all_gain": len(ja_set - sa_set),
            "all_loss": len(sa_set - ja_set),
            "redundant_extra_heavy_combined": sum(r["redundant_extra"] for r in jh_ref),
            "redundant_extra_heavy_seed38": sum(r["redundant_extra"] for r in sh_ref),
            "unmatched_combined_leaves_heavy": sum(1 for r in jh_seed if not r["matched"]),
            "unmatched_seed38_heavy": sum(1 for r in sh_seed if not r["matched"]),
        },
        "calibre": "strict: cp_distance(theta,phi)<15deg (ring_benchmark same); RMSD: Kabsch proper rotation + det correction (match_after_opt.py DRAFT verbatim), heavy=first 12 atoms (6C+6O), all=24 atoms, no mirror matching",
    }
    with open(OUT / "boat_unopt_recall.json", "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
