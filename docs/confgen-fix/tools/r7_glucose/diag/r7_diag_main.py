#!/usr/bin/env python3
"""R7 beta-D-glucopyranose gap diagnosis (READ-ONLY).

Reads:
  <OUT-DIR>/fix1r-r7-v3-root-all-systems/beta_d_glucopyranose/{reference_match.csv,cp_table.csv,seeds.xyz}
  <repo-root>/tests/fixtures/confgen/ring/beta_d_glucopyranose/crest_conformers.xyz
  run_meta.json (ring_0based)
Uses existing confflow ring perception/CP only (canonical_forms, cremer_pople, cp_distance).
Writes all outputs to <OUT-DIR>/r7diag-out/ (this file must also live there).

Run:
  PYTHONPATH=<repo-root> python3 -I <OUT-DIR>/r7diag-out/diag_main.py
  (script inserts <repo-root> into sys.path itself because -I ignores PYTHONPATH)

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
OUT_DIR = Path(os.environ.get("R7_DIAG_OUT", Path.cwd() / "r7diag-out"))
OUT = OUT_DIR
DATA_DIR = _require_dir("R7_DATA_DIR", "未发布的 root 全体系输入 beta_d_glucopyranose 目录")
FIXTURE_CREST = FIXTURE_DIR / "crest_conformers.xyz"

sys.path.insert(0, str(REPO_ROOT))
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    canonical_forms,
    cp_distance,
    cp_to_coords,
    cremer_pople,
)  # noqa: E402

DATA = DATA_DIR
FIX = FIXTURE_CREST


RING = [1, 2, 3, 4, 5, 6]  # from run_meta.json ring_0based
POS2IUPAC = {0: "C5", 1: "C4", 2: "C3", 3: "C2", 4: "C1", 5: "O5"}

# global indices (0-based, element order fixed per fixture MANIFEST)
C1, C2, C3, C4, C5, C6 = 5, 4, 3, 2, 1, 0
O5, O1, O2, O3, O4, O6 = 6, 7, 8, 9, 10, 11
H19, H20, H21, H22, H23 = 19, 20, 21, 22, 23


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


def pyr_symbol(form, z=None):
    """Inferred pyranose symbol.

    Precise selector is authoritative; symbol is
    positional inference under ring order [1..6] pos->IUPAC {0:C5,1:C4,2:C3,3:C2,4:C1,5:O5}.
    Chairs anchored by population: C_1 (theta180, populated stable chair) = 4C1, C_0 = 1C4.
    """
    fam, idx = form.family, form.index
    if fam == "C":
        return "4C1" if abs(float(form.cp_target.theta) - 180.0) < 1e-9 else "1C4"
    if z is None:
        xyz = cp_to_coords(form.cp_target)
        z = xyz[:, 2]
    z = np.asarray(z, dtype=float)
    lab = [POS2IUPAC[i] for i in range(6)]
    hemi = (
        "N"
        if float(form.cp_target.theta) < 90.0
        else ("S" if float(form.cp_target.theta) > 90.0 else "eq")
    )
    if fam == "B":
        order = sorted(range(6), key=lambda i: z[i])
        lo = sorted([order[0], order[1]])
        hi = sorted([order[-1], order[-2]])
        # boat: the two extreme same-side atoms (pair with |pair mean| largest)
        lo_mean = float(z[lo[0]] + z[lo[1]]) / 2
        hi_mean = float(z[hi[0]] + z[hi[1]]) / 2
        if abs(lo_mean) >= abs(hi_mean):
            pair, sgn = lo, "-"
        else:
            pair, sgn = hi, "+"
        return f"B{lab[pair[0]]}/{lab[pair[1]]}{sgn}"
    if fam == "TB":
        order = sorted(range(6), key=lambda i: abs(z[i]))
        flat = sorted([order[0], order[1]])
        return f"TW{lab[flat[0]]}-{lab[flat[1]]}(phi{float(form.cp_target.phi):.0f})"
    if fam == "E":
        k = int(np.argmax(np.abs(z)))
        return f"E-{lab[k]}-{hemi}"
    if fam == "H":
        order = sorted(range(6), key=lambda i: abs(z[i]), reverse=True)
        pair = sorted([order[0], order[1]])
        return f"H-{lab[pair[0]]}/{lab[pair[1]]}-{hemi}"
    return f"{fam}_{idx}"


def read_xyz_frames(path):
    frames = []
    lines = Path(path).read_text().splitlines()
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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    forms = list(canonical_forms(6))
    assert len(forms) == 38
    # ideal z cache for symbols
    zcache = {}
    for f in forms:
        zcache[(f.family, f.index)] = cp_to_coords(f.cp_target)[:, 2]
    sym = {(f.family, f.index): pyr_symbol(f, zcache[(f.family, f.index)]) for f in forms}

    # reference_match.csv
    refs = []
    with open(DATA / "reference_match.csv") as fh:
        for row in csv.DictReader(fh):
            refs.append(
                {
                    k: (
                        float(v)
                        if k in ("ref_theta", "ref_phi", "ref_q_over_rbar", "min_cp_dist_deg")
                        and v != ""
                        else v
                    )
                    for k, v in row.items()
                }
            )
            refs[-1]["ref"] = int(row["ref"])
            refs[-1]["best_seed"] = int(row["best_seed"])
            refs[-1]["hit_lt15"] = int(row["hit_lt15"])
    # cp_table.csv seeds
    seeds = []
    with open(DATA / "cp_table.csv") as fh:
        for row in csv.DictReader(fh):
            seeds.append(row)
    seed_cmd = {(r["family"], int(r["index"])): int(r["seed"]) for r in seeds}

    # recompute CP from reference xyz (verification)
    frames = read_xyz_frames(FIX)
    assert len(frames) == 155, len(frames)
    rec = []
    maxd = 0.0
    for k, fr in enumerate(frames):
        cp = cremer_pople(fr["coords"][RING])
        rec.append((float(cp.q), float(cp.theta), float(cp.phi)))
        dth = abs(cp.theta - refs[k]["ref_theta"])
        dph = abs(wrap180(cp.phi - refs[k]["ref_phi"]))
        maxd = max(maxd, dth, dph)
    print(f"recomputed CP vs reference_match.csv max |dtheta|,|dphi| = {maxd:.4f} deg")

    # nearest canonical per ref
    ref_near = []
    for _k, r in enumerate(refs):
        a = CPCoords(n=6, q=0.6, theta=r["ref_theta"], phi=r["ref_phi"])
        ds = []
        for f in forms:
            ds.append((float(cp_distance(a, f.cp_target)), f))
        ds.sort(key=lambda t: t[0])
        (d1, f1), (d2, f2) = ds[0], ds[1]
        ref_near.append({"nearest": f1, "d1": d1, "second": f2, "d2": d2, "all": ds})

    misses = [i for i, r in enumerate(refs) if r["hit_lt15"] == 0]
    hits = [i for i, r in enumerate(refs) if r["hit_lt15"] == 1]
    assert len(misses) == 28 and len(hits) == 127, (len(misses), len(hits))

    # ---- 1. miss_canonical.csv ----
    with open(OUT / "miss_canonical.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "ref",
                "ref_theta",
                "ref_phi",
                "ref_q_over_rbar",
                "best_seed",
                "min_cp_dist_deg",
                "nearest_precise",
                "nearest_symbol",
                "d_nearest_ideal_deg",
                "second_precise",
                "second_symbol",
                "d_second_ideal_deg",
                "has_seed_same_form",
                "covering_seed",
            ]
        )
        for k in misses:
            r = refs[k]
            n = ref_near[k]
            key = (n["nearest"].family, n["nearest"].index)
            w.writerow(
                [
                    k,
                    round(r["ref_theta"], 3),
                    round(r["ref_phi"], 3),
                    r["ref_q_over_rbar"],
                    r["best_seed"],
                    r["min_cp_dist_deg"],
                    f"{n['nearest'].family}_{n['nearest'].index}",
                    sym[key],
                    round(n["d1"], 3),
                    f"{n['second'].family}_{n['second'].index}",
                    sym[(n["second"].family, n["second"].index)],
                    round(n["d2"], 3),
                    int(key in seed_cmd),
                    seed_cmd.get(key, ""),
                ]
            )
    # ---- form_seed_coverage.csv ----
    with open(OUT / "form_seed_coverage.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "family",
                "index",
                "precise",
                "symbol_inferred",
                "ideal_theta",
                "ideal_phi",
                "has_seed",
                "seed_id",
                "anchor",
                "direction",
            ]
        )
        for f in forms:
            key = (f.family, f.index)
            sid = seed_cmd.get(key)
            anchor = direction = ""
            if sid is not None:
                anchor = seeds[sid]["anchor"]
                direction = seeds[sid]["direction"]
            w.writerow(
                [
                    f.family,
                    f.index,
                    f"{f.family}_{f.index}",
                    sym[key],
                    f.cp_target.theta,
                    round(float(f.cp_target.phi), 1),
                    int(sid is not None),
                    sid if sid is not None else "",
                    anchor,
                    direction,
                ]
            )
    # ---- 2. boat_zone.csv (theta 90-120) ----
    boat = [k for k in misses if 90.0 <= refs[k]["ref_theta"] <= 120.0]
    with open(OUT / "boat_zone.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "ref",
                "ref_theta",
                "ref_phi",
                "nearestB_precise",
                "nearestB_symbol",
                "d_nearestB_deg",
                "nearestTB_precise",
                "nearestTB_symbol",
                "d_nearestTB_deg",
                "assign_BT",
                "best_seed",
                "best_seed_family",
            ]
        )
        for k in boat:
            r = refs[k]
            a = CPCoords(n=6, q=0.6, theta=r["ref_theta"], phi=r["ref_phi"])
            bB = sorted(
                ((float(cp_distance(a, f.cp_target)), f) for f in forms if f.family == "B"),
                key=lambda t: t[0],
            )[0]
            bT = sorted(
                ((float(cp_distance(a, f.cp_target)), f) for f in forms if f.family == "TB"),
                key=lambda t: t[0],
            )[0]
            assign = "B" if bB[0] <= bT[0] else "TB"
            w.writerow(
                [
                    k,
                    round(r["ref_theta"], 3),
                    round(r["ref_phi"], 3),
                    f"{bB[1].family}_{bB[1].index}",
                    sym[(bB[1].family, bB[1].index)],
                    round(bB[0], 3),
                    f"{bT[1].family}_{bT[1].index}",
                    sym[(bT[1].family, bT[1].index)],
                    round(bT[0], 3),
                    assign,
                    r["best_seed"],
                    seeds[r["best_seed"]]["family"],
                ]
            )

    # ---- 3. rotor fingerprints ----
    OHSET = {
        "dO1": (H19, O1, C1, C2),
        "dO2": (H20, O2, C2, C3),
        "dO3": (H21, O3, C3, C4),
        "dO4": (H22, O4, C4, C5),
        "dO6": (H23, O6, C6, C5),
    }
    OHSET2 = {
        "dO2b": (H20, O2, C2, C1),
        "dO3b": (H21, O3, C3, C2),
        "dO4b": (H22, O4, C4, C3),
        "dO1b": (H19, O1, C1, O5),
    }
    OM = (O5, C5, C6, O6)
    allrot = []
    for k, fr in enumerate(frames):
        xyz = fr["coords"]
        d = {
            nm: round(float(dihedral(xyz[a], xyz[b], xyz[c], xyz[e])), 2)
            for nm, (a, b, c, e) in OHSET.items()
        }
        d2 = {
            nm: round(float(dihedral(xyz[a], xyz[b], xyz[c], xyz[e])), 2)
            for nm, (a, b, c, e) in OHSET2.items()
        }
        om = round(float(dihedral(xyz[OM[0]], xyz[OM[1]], xyz[OM[2]], xyz[OM[3]])), 2)
        _, hbn, hbm = hbond_stats(xyz)
        allrot.append(
            {
                "ref": k,
                **d,
                **d2,
                "omega": om,
                "rotamer": rotamer(om),
                "hbond_count_lt2p5": hbn,
                "min_HO_dist": round(float(hbm), 3),
                "theta": round(refs[k]["ref_theta"], 3),
                "hit": refs[k]["hit_lt15"],
            }
        )
    with open(OUT / "all_refs_rotor.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(allrot[0].keys()))
        w.writeheader()
        w.writerows(allrot)

    # nearest hit ref per miss (CP direction distance)
    def cpdd(i, j):
        a = CPCoords(n=6, q=0.6, theta=refs[i]["ref_theta"], phi=refs[i]["ref_phi"])
        b = CPCoords(n=6, q=0.6, theta=refs[j]["ref_theta"], phi=refs[j]["ref_phi"])
        return float(cp_distance(a, b))

    with open(OUT / "miss_rotor_vs_hit.csv", "w", newline="") as fh:
        cols = [
            "miss_ref",
            "nearest_hit_ref",
            "cp_dist_miss_hit_deg",
            "miss_theta",
            "hit_theta",
            "miss_nearest_form",
            "hit_nearest_form",
            "same_nearest_form",
        ]
        for nm in (
            list(OHSET) + list(OHSET2) + ["omega", "rotamer", "hbond_count_lt2p5", "min_HO_dist"]
        ):
            cols += [f"miss_{nm}", f"hit_{nm}"]
        cols += [
            "d_dO1",
            "d_dO2",
            "d_dO3",
            "d_dO4",
            "d_dO6",
            "d_omega",
            "rotamer_changed",
            "d_hbond_count",
        ]
        w = csv.writer(fh)
        w.writerow(cols)
        for k in misses:
            j = min(hits, key=lambda h: cpdd(k, h))
            dd = cpdd(k, j)
            mk, hk = allrot[k], allrot[j]
            fm = f"{ref_near[k]['nearest'].family}_{ref_near[k]['nearest'].index}"
            fh_ = f"{ref_near[j]['nearest'].family}_{ref_near[j]['nearest'].index}"
            row = [k, j, round(dd, 3), mk["theta"], hk["theta"], fm, fh_, int(fm == fh_)]
            for nm in (
                list(OHSET)
                + list(OHSET2)
                + ["omega", "rotamer", "hbond_count_lt2p5", "min_HO_dist"]
            ):
                row += [mk[nm], hk[nm]]
            row += [
                round(abs(wrap180(mk[n] - hk[n])), 2) for n in ["dO1", "dO2", "dO3", "dO4", "dO6"]
            ]
            row += [
                round(abs(wrap180(mk["omega"] - hk["omega"])), 2),
                int(mk["rotamer"] != hk["rotamer"]),
                mk["hbond_count_lt2p5"] - hk["hbond_count_lt2p5"],
            ]
            w.writerow(row)
    # correlation stats -> stats.json
    flat = [k for k in misses if 150.0 <= refs[k]["ref_theta"] <= 170.0]

    def pear(x, y):
        x = np.array(x, float)
        y = np.array(y, float)
        xm, ym = x - x.mean(), y - y.mean()
        d = float(np.sqrt((xm**2).sum() * (ym**2).sum()))
        return float(xm @ ym / d) if d > 0 else float("nan")

    flat_d = [ref_near[k]["d1"] for k in flat]
    flat_hb = [allrot[k]["hbond_count_lt2p5"] for k in flat]
    flat_minh = [allrot[k]["min_HO_dist"] for k in flat]
    flat_dev = [180.0 - refs[k]["ref_theta"] for k in flat]
    # hit chairs near C_1 for comparison
    hitchair = [
        k
        for k in hits
        if f"{ref_near[k]['nearest'].family}_{ref_near[k]['nearest'].index}" == "C_1"
    ]
    stats = {
        "n_miss": 28,
        "n_hit": 127,
        "n_boat_zone_theta90_120": len(boat),
        "n_flat_theta150_170": len(flat),
        "recompute_max_absdiff_deg": round(maxd, 4),
        "boat_refs": boat,
        "flat_refs": flat,
        "pearson_flat_dev_vs_hbond_count": round(pear(flat_dev, flat_hb), 3),
        "pearson_flat_dideal_vs_hbond_count": round(pear(flat_d, flat_hb), 3),
        "pearson_flat_dev_vs_minHO": round(pear(flat_dev, flat_minh), 3),
        "pearson_flat_dideal_vs_minHO": round(pear(flat_d, flat_minh), 3),
        "miss_rotamer_frac": {
            r: round(sum(1 for k in misses if allrot[k]["rotamer"] == r) / 28, 3)
            for r in ("gg", "gt", "tg")
        },
        "hit_rotamer_frac": {
            r: round(sum(1 for k in hits if allrot[k]["rotamer"] == r) / 127, 3)
            for r in ("gg", "gt", "tg")
        },
        "miss_hb_mean": round(float(np.mean([allrot[k]["hbond_count_lt2p5"] for k in misses])), 3),
        "hit_hb_mean": round(float(np.mean([allrot[k]["hbond_count_lt2p5"] for k in hits])), 3),
        "miss_hb_dist": {
            str(c): sum(1 for k in misses if allrot[k]["hbond_count_lt2p5"] == c) for c in range(6)
        },
        "hit_hb_dist": {
            str(c): sum(1 for k in hits if allrot[k]["hbond_count_lt2p5"] == c) for c in range(6)
        },
        "n_hitchair_C1": len(hitchair),
    }
    with open(OUT / "stats.json", "w") as fh:
        json.dump(stats, fh, indent=1)

    # ---- 4. basin recall ----
    with open(OUT / "basin_recall.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "ref",
                "hit_lt15",
                "nearest_precise",
                "d_nearest_ideal_deg",
                "best_seed",
                "best_seed_cmd",
                "basin_hit_same_form",
                "family_hit_same_family",
            ]
        )
        for k, r in enumerate(refs):
            n = ref_near[k]
            prec = f"{n['nearest'].family}_{n['nearest'].index}"
            cmd = f"{seeds[r['best_seed']]['family']}_{seeds[r['best_seed']]['index']}"
            w.writerow(
                [
                    k,
                    r["hit_lt15"],
                    prec,
                    round(n["d1"], 3),
                    r["best_seed"],
                    cmd,
                    int(prec == cmd),
                    int(n["nearest"].family == seeds[r["best_seed"]]["family"]),
                ]
            )
    # recount
    n_basin = n_fam = 0
    for k, r in enumerate(refs):
        n = ref_near[k]
        if (
            f"{n['nearest'].family}_{n['nearest'].index}"
            == f"{seeds[r['best_seed']]['family']}_{seeds[r['best_seed']]['index']}"
        ):
            n_basin += 1
        if n["nearest"].family == seeds[r["best_seed"]]["family"]:
            n_fam += 1
    stats["recall_lt15"] = f"{sum(r['hit_lt15'] for r in refs)}/155"
    stats["recall_basin_same_form"] = f"{n_basin}/155"
    stats["recall_family_same_family"] = f"{n_fam}/155"
    with open(OUT / "stats.json", "w") as fh:
        json.dump(stats, fh, indent=1)
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
