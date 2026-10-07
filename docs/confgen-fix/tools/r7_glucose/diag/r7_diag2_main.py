#!/usr/bin/env python3
"""R7-diag2 read-only diagnostic (no repo writes, no threshold/seed changes).

Reads:
  fixture tests/fixtures/confgen/ring/beta_d_glucopyranose/{input.xyz,crest_conformers.xyz}
  <OUT-DIR>/fix1r-r7-v3-root-all-systems/beta_d_glucopyranose/{reference_match.csv,cp_table.csv}
  <OUT-DIR>/r7diag-out/{miss_canonical.csv,all_refs_rotor.csv,miss_rotor_vs_hit.csv}
Writes: <OUT-DIR>/r7diag2-out/ only.
Run: python3 -I <OUT-DIR>/r7diag2-out/diag2_main.py  (script inserts sys.path itself)

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
OUT_DIR = Path(os.environ.get("R7_DIAG2_OUT", Path.cwd() / "r7diag2-out"))
OUT = OUT_DIR
DATA_DIR = _require_dir("R7_DATA_DIR", "未发布的 root 全体系输入 beta_d_glucopyranose 目录")
DIAG_DIR = Path(os.environ.get("R7_DIAG_OUT", Path.cwd() / "r7diag-out"))

sys.path.insert(0, str(REPO_ROOT))
from confflow.core.io import read_xyz_file  # noqa: E402
from confflow.domain.structure import StructureRecord  # noqa: E402
from confflow.science.confgen.engine import ConfgenEngine  # noqa: E402
from confflow.science.confgen.model import build_context  # noqa: E402
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    canonical_forms,
    cp_distance,
    cremer_pople,
)  # noqa: E402

FIXD = FIXTURE_DIR
PRIOR = DATA_DIR
PRIOR1 = DIAG_DIR


RING = [1, 2, 3, 4, 5, 6]
CP_HIT = 15.0
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


def ring_rbar(xyz, ring):
    n = len(ring)
    return float(np.mean([np.linalg.norm(xyz[ring[(i + 1) % n]] - xyz[ring[i]]) for i in range(n)]))


def rotamer(om):
    if om is None or (isinstance(om, float) and math.isnan(om)):
        return "?"
    if -120.0 <= om < 0.0:
        return "gg"
    if 0.0 <= om < 120.0:
        return "gt"
    return "tg"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    from pathlib import Path as _P

    import confflow

    mod_file = str(_P(confflow.__file__).resolve())
    import subprocess

    try:
        head = subprocess.check_output(
            ["git", "-C", "/opt/ConfFlow", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        head = "unknown"

    # ---- reference CP (recomputed, same calibre as ring_benchmark) ----
    crest_frames = read_xyz_file(str(FIXD / "crest_conformers.xyz"), strict=True)
    assert len(crest_frames) == 155
    refs = []
    for k, fr in enumerate(crest_frames):
        xyz = np.asarray(fr["coords"], dtype=float)
        cp = cremer_pople(xyz[RING])
        rb = ring_rbar(xyz, RING)
        refs.append(
            {
                "k": k,
                "q": float(cp.q),
                "theta": float(cp.theta),
                "phi": float(cp.phi),
                "rbar": rb,
                "qor": float(cp.q / rb),
            }
        )

    # ---- Q1: full engine run, rings-only spec (torsions inactive) ----
    forms = list(canonical_forms(6))
    by_prec = {(f.family, f.index): f for f in forms}
    # Same driving/forms as prior explicit run: crest frame0 + explicit C,B,TB,E,H.
    driving = crest_frames[0]
    rec = StructureRecord(
        id="r7diag2:beta_d_glucopyranose:crest-first:explicit",
        atoms=tuple(driving["atoms"]),
        coordinates=tuple(map(tuple, np.asarray(driving["coords"], dtype=float))),
        charge=0,
        multiplicity=1,
    )
    ctx = build_context(
        rec,
        {
            "index_base": 0,
            "rings": [{"id": "r1", "atoms": list(RING), "forms": ["C", "B", "TB", "E", "H"]}],
        },
    )
    active = tuple(ctx.active_components)
    engine = ConfgenEngine()
    run = engine.run(ctx)
    leaves = list(run.leaves)
    targets = list(run.target_records)
    leaf_rows = []
    for leaf in leaves:
        xyz = np.asarray(leaf.structure.coordinates, dtype=float)
        cp = cremer_pople(xyz[RING])
        rb = ring_rbar(xyz, RING)
        sk = leaf.state_key
        try:
            cmd = dict(getattr(sk, "rings", {}).get("r1", {}))
        except Exception:
            cmd = {}
        leaf_rows.append(
            {
                "xyz": xyz,
                "q": float(cp.q),
                "theta": float(cp.theta),
                "phi": float(cp.phi),
                "rbar": rb,
                "qor": float(cp.q / rb),
                "cmd": cmd,
                "prov": dict(getattr(leaf, "provenance", {})),
            }
        )
    # order leaves by commanded (family,index) for determinism in csv
    leaf_rows.sort(key=lambda r: (str(r["cmd"].get("form")), int(r["cmd"].get("index", 0))))
    with open(OUT / "final_leaves_cp.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["leaf", "cmd_family", "cmd_index", "Q", "theta", "phi", "rbar", "q_over_rbar"])
        for i, s in enumerate(leaf_rows):
            w.writerow(
                [
                    i,
                    s["cmd"].get("form"),
                    s["cmd"].get("index"),
                    round(s["q"], 4),
                    round(s["theta"], 3),
                    round(s["phi"], 3),
                    round(s["rbar"], 4),
                    round(s["qor"], 4),
                ]
            )
    # recall vs refs (ring_benchmark calibre: cp_distance on theta/phi, q ignored for n=6)
    match = []
    for r in refs:
        a = CPCoords(n=6, q=r["q"], theta=r["theta"], phi=r["phi"])
        best, bi = 1e9, -1
        for i, s in enumerate(leaf_rows):
            b = CPCoords(n=6, q=s["q"], theta=s["theta"], phi=s["phi"])
            d = float(cp_distance(a, b))
            if d < best:
                best, bi = d, i
        match.append(
            {
                "ref": r["k"],
                "theta": r["theta"],
                "phi": r["phi"],
                "qor": r["qor"],
                "best": bi,
                "dist": best,
                "hit": int(best < CP_HIT),
            }
        )
    with open(OUT / "final_reference_match.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "ref",
                "ref_theta",
                "ref_phi",
                "ref_q_over_rbar",
                "best_leaf",
                "min_cp_dist_deg",
                "hit_lt15",
            ],
        )
        w.writeheader()
        for m in match:
            w.writerow(
                {
                    "ref": m["ref"],
                    "ref_theta": round(m["theta"], 4),
                    "ref_phi": round(m["phi"], 4),
                    "ref_q_over_rbar": round(m["qor"], 4),
                    "best_leaf": m["best"],
                    "min_cp_dist_deg": round(m["dist"], 4),
                    "hit_lt15": m["hit"],
                }
            )
    hits = sum(m["hit"] for m in match)
    n_pub = len(leaf_rows)
    n_tgt = len(targets)
    failed = [
        t
        for t in targets
        if str(getattr(getattr(t, "status", ""), "value", getattr(t, "status", "")))
        != "published_leaf"
    ]
    n_failed = len(failed)
    # solver drift: leaf CP vs commanded ideal
    max_leaf_ideal = 0.0
    for s in leaf_rows:
        cmd = (str(s["cmd"].get("form")), int(s["cmd"].get("index", 0)))
        f = by_prec.get(cmd)
        if f is not None:
            b = CPCoords(n=6, q=s["q"], theta=s["theta"], phi=s["phi"])
            max_leaf_ideal = max(max_leaf_ideal, float(cp_distance(b, f.cp_target)))
    # miss-set equality vs prior (computed after misses known; placeholder here)
    final_misses = sorted(m["ref"] for m in match if not m["hit"])

    # ---- torsion-invariance mechanism check (illustrative, NOT a recall claim) ----
    # One ring leaf (C_1) + one exocyclic relative rotation (C5-C6 bond 1-0, +60deg).
    # Proves TorsionStage leaves ring CP invariant by construction (refuses ring bonds).
    tors_drift = {"note": "not-run", "cp_drift_deg": None, "bond": None, "angle": None}
    try:
        # pick commanded C_1 leaf
        cli = next(
            i
            for i, s in enumerate(leaf_rows)
            if str(s["cmd"].get("form")) == "C" and int(s["cmd"].get("index")) == 1
        )
        # rebuild a minimal torsion context on that leaf geometry is complex;
        # instead rotate directly with torsion mechanics on exocyclic bond and measure CP.
        from confflow.science.torsion import rotate_atoms_around_bond, rotating_side

        xyz0 = leaf_rows[cli]["xyz"]
        n_atoms = xyz0.shape[0]
        adj = [list(row) for row in ctx.adjacency]
        first, second = 1, 0  # C5-C6 exocyclic, relative frame exists
        side = rotating_side(adj, n_atoms, first, second, [second], [first])
        xyz1 = xyz0.copy()
        rotate_atoms_around_bond(xyz1, first, second, side, 60.0)
        cp0 = cremer_pople(xyz0[RING])
        cp1 = cremer_pople(xyz1[RING])
        drift = float(cp_distance(cp0, cp1))
        tors_drift = {
            "note": "direct exocyclic +60deg rotation on published C_1 leaf",
            "cp_drift_deg": drift,
            "bond": "1-0(C5-C6)",
            "angle": 60.0,
            "ring_atoms_moved_max": float(max(np.linalg.norm(xyz1[a] - xyz0[a]) for a in RING)),
        }
    except Exception as exc:
        tors_drift = {
            "note": f"check failed: {type(exc).__name__}: {exc}",
            "cp_drift_deg": None,
            "bond": None,
            "angle": None,
        }

    # ---- 28 misses from prior reference_match ----
    prior = []
    with open(PRIOR / "reference_match.csv") as f:
        for row in csv.DictReader(f):
            prior.append(
                {
                    "ref": int(row["ref"]),
                    "hit": int(row["hit_lt15"]),
                    "dist": float(row["min_cp_dist_deg"]),
                }
            )
    misses = [r["ref"] for r in prior if r["hit"] == 0]
    assert len(misses) == 28, len(misses)
    # nearest ideal per ref (for Q3)
    near = {}
    for r in refs:
        a = CPCoords(n=6, q=0.6, theta=r["theta"], phi=r["phi"])
        ds = sorted(((float(cp_distance(a, f.cp_target)), f) for f in forms), key=lambda t: t[0])
        near[r["k"]] = (ds[0][1], ds[0][0], ds[1][1], ds[1][0])

    # ---- Q2: miss -> nearest FINAL leaf ----
    q2rows = []
    for k in misses:
        m = next(m for m in match if m["ref"] == k)
        q2rows.append(
            {
                "ref": k,
                "ref_theta": round(refs[k]["theta"], 3),
                "ref_phi": round(refs[k]["phi"], 3),
                "best_leaf": m["best"],
                "cp_dist_deg": round(m["dist"], 3),
                "hit": m["hit"],
            }
        )
    with open(OUT / "q2_miss_to_final.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f, fieldnames=["ref", "ref_theta", "ref_phi", "best_leaf", "cp_dist_deg", "hit_lt15"]
        )
        w.writeheader()
        for r in q2rows:
            w.writerow(
                {
                    "ref": r["ref"],
                    "ref_theta": r["ref_theta"],
                    "ref_phi": r["ref_phi"],
                    "best_leaf": r["best_leaf"],
                    "cp_dist_deg": r["cp_dist_deg"],
                    "hit_lt15": r["hit"],
                }
            )
    q2hits = sum(r["hit"] for r in q2rows)
    miss_set_equal = sorted(misses) == final_misses

    # ---- Q3: displacement vectors ----
    q3rows = []
    for k in misses:
        f1, d1, f2, d2 = near[k]
        ideal = f1.cp_target
        r = refs[k]
        dtheta = wrap180(r["theta"] - float(ideal.theta))
        dphi = wrap180(r["phi"] - float(ideal.phi))
        dq = float(r["q"] - float(ideal.q))
        dqor = float(r["qor"] - float(ideal.q) / float(r["rbar"]))
        # note: ideal qor uses ref rbar so amplitude comparable; also record raw
        q3rows.append(
            {
                "ref": k,
                "nearest": f"{f1.family}_{f1.index}",
                "family": f1.family,
                "ideal_theta": float(ideal.theta),
                "ideal_phi": float(ideal.phi),
                "ideal_q": float(ideal.q),
                "ref_theta": r["theta"],
                "ref_phi": r["phi"],
                "ref_q": r["q"],
                "ref_rbar": r["rbar"],
                "ref_qor": r["qor"],
                "dtheta": dtheta,
                "dphi": dphi,
                "dq": dq,
                "dqor": dqor,
                "cp_dist": d1,
            }
        )
    with open(OUT / "q3_displacement.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(q3rows[0].keys()))
        w.writeheader()
        w.writerows(
            {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in q3rows
        )

    def circ_mean_deg(vals):
        an = np.radians(np.asarray(vals, float))
        C, S = float(np.cos(an).sum()), float(np.sin(an).sum())
        R = math.hypot(C, S) / len(vals)
        mu = math.degrees(math.atan2(S, C)) % 360.0
        if mu > 180.0:
            mu -= 360.0
        cstd = (
            float(math.sqrt(-2.0 * math.log(max(R, 1e-12)))) * 180.0 / math.pi
            if R > 0
            else float("nan")
        )
        return mu, R, cstd

    def lin_stats(vals):
        x = np.asarray(vals, float)
        return (
            float(x.mean()),
            float(x.std(ddof=1)) if len(x) > 1 else 0.0,
            float(x.min()),
            float(x.max()),
        )

    groups = {}
    for gname, members in [
        ("C_chair20", [r for r in q3rows if r["family"] == "C"]),
        ("boat_zone7", [r for r in q3rows if r["ref"] in (48, 69, 76, 110, 112, 123, 148)]),
        ("H_single1", [r for r in q3rows if r["family"] == "H"]),
        ("all28", q3rows),
    ]:
        if not members:
            continue
        mu_phi, R_phi, cs_phi = circ_mean_deg([r["dphi"] for r in members])
        mu_th, R_th, cs_th = circ_mean_deg([r["dtheta"] for r in members])
        m_dq, s_dq, _, _ = lin_stats([r["dq"] for r in members])
        m_dqor, s_dqor, _, _ = lin_stats([r["dqor"] for r in members])
        m_cp, s_cp, _, _ = lin_stats([r["cp_dist"] for r in members])
        groups[gname] = {
            "n": len(members),
            "dtheta_circmean": round(mu_th, 2),
            "dtheta_R": round(R_th, 3),
            "dtheta_circstd": round(cs_th, 2),
            "dphi_circmean": round(mu_phi, 2),
            "dphi_R": round(R_phi, 3),
            "dphi_circstd": round(cs_phi, 2),
            "dq_mean": round(m_dq, 3),
            "dq_sd": round(s_dq, 3),
            "dqor_mean": round(m_dqor, 3),
            "dqor_sd": round(s_dqor, 3),
            "cp_mean": round(m_cp, 2),
            "cp_sd": round(s_cp, 2),
            "refs": [r["ref"] for r in members],
        }
    with open(OUT / "q3_groups.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "group",
                "n",
                "dtheta_circmean",
                "dtheta_R",
                "dtheta_circstd",
                "dphi_circmean",
                "dphi_R",
                "dphi_circstd",
                "dq_mean",
                "dq_sd",
                "dqor_mean",
                "dqor_sd",
                "cp_mean",
                "cp_sd",
                "refs",
            ],
        )
        w.writeheader()
        for g, s in groups.items():
            w.writerow({"group": g, **{k: v for k, v in s.items()}})

    # ---- Q4: 20 flattened chairs vs nearest hit ----
    OHSET = {
        "dO1": (H19, O1, C1, C2),
        "dO2": (H20, O2, C2, C3),
        "dO3": (H21, O3, C3, C4),
        "dO4": (H22, O4, C4, C5),
        "dO6": (H23, O6, C6, C5),
    }
    OM = (O5, C5, C6, O6)
    frames = crest_frames

    def rotor_of(k):
        xyz = np.asarray(frames[k]["coords"], dtype=float)
        d = {
            nm: float(dihedral(xyz[a], xyz[b], xyz[c], xyz[e]))
            for nm, (a, b, c, e) in OHSET.items()
        }
        om = float(dihedral(xyz[OM[0]], xyz[OM[1]], xyz[OM[2]], xyz[OM[3]]))
        return d, om, rotamer(om)

    # nearest hit (CP) per miss, same calibre as prior diag
    hits_idx = [r["ref"] for r in prior if r["hit"] == 1]

    def cpdd(i, j):
        a = CPCoords(n=6, q=0.6, theta=refs[i]["theta"], phi=refs[i]["phi"])
        b = CPCoords(n=6, q=0.6, theta=refs[j]["theta"], phi=refs[j]["phi"])
        return float(cp_distance(a, b))

    chairs = [r for r in q3rows if r["family"] == "C"]
    assert len(chairs) == 20, len(chairs)

    def flatbin(cpdist):
        if cpdist < 18.0:
            return "15-18"
        if cpdist < 21.0:
            return "18-21"
        return "21-25"

    q4rows = []
    for r in chairs:
        k = r["ref"]
        j = min(hits_idx, key=lambda h: cpdd(k, h))
        dm, omm, rmm = rotor_of(k)
        dh, omh, rmh = rotor_of(j)
        flips = f"{rmm}->{rmh}"
        big_oh = sum(
            1 for nm in ["dO1", "dO2", "dO3", "dO4", "dO6"] if abs(wrap180(dm[nm] - dh[nm])) > 60.0
        )
        q4rows.append(
            {
                "miss": k,
                "hit": j,
                "cp_miss_hit": round(cpdd(k, j), 2),
                "miss_omega": round(omm, 1),
                "hit_omega": round(omh, 1),
                "miss_rot": rmm,
                "hit_rot": rmh,
                "flip": flips,
                "d_omega": round(abs(wrap180(omm - omh)), 1),
                **{
                    f"d_{nm}": round(abs(wrap180(dm[nm] - dh[nm])), 1)
                    for nm in ["dO1", "dO2", "dO3", "dO4", "dO6"]
                },
                "nOH_gt60": big_oh,
                "flatbin": flatbin(r["cp_dist"]),
                "miss_cp_ideal": round(r["cp_dist"], 2),
            }
        )
    with open(OUT / "q4_flatten_flip.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(q4rows[0].keys()))
        w.writeheader()
        w.writerows(q4rows)
    # confusion: flip x flatbin
    flips = sorted(set(r["flip"] for r in q4rows))
    bins = ["15-18", "18-21", "21-25"]
    with open(OUT / "q4_confusion.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["flip", *bins, "total"])
        for fl in flips:
            row = [fl] + [
                sum(1 for r in q4rows if r["flip"] == fl and r["flatbin"] == b) for b in bins
            ]
            row += [sum(row[1:])]
            w.writerow(row)
        w.writerow(
            ["total"] + [sum(1 for r in q4rows if r["flatbin"] == b) for b in bins] + [len(q4rows)]
        )
    n_flip = sum(1 for r in q4rows if r["miss_rot"] != r["hit_rot"])
    n_same_omega_bigOH = sum(
        1 for r in q4rows if r["miss_rot"] == r["hit_rot"] and r["nOH_gt60"] > 0
    )

    stats = {
        "HEAD": head,
        "binding": mod_file,
        "active_components": list(active),
        "targets": n_tgt,
        "published": n_pub,
        "failed_nonleaf": n_failed,
        "recall_final_lt15": f"{hits}/155",
        "q2_miss_hit_lt15": f"{q2hits}/28",
        "q2_remaining": 28 - q2hits,
        "tors_check": tors_drift,
        "max_leaf_vs_ideal_deg": round(max_leaf_ideal, 4),
        "miss_set_equal_prior": bool(miss_set_equal),
        "final_misses": final_misses,
        "q3_groups": groups,
        "q4_flip_changed": f"{n_flip}/20",
        "q4_sameRot_bigOH": n_same_omega_bigOH,
    }
    with open(OUT / "stats2.json", "w") as f:
        json.dump(stats, f, indent=1)
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
