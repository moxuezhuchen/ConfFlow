#!/usr/bin/env python3
"""R7-boat 01: boat-zone two leaves (TB_0, B_1) x 6-rotor (729 each) joint engine runs.

Read-only wrt repo. Writes only <OUT-DIR>/r7boat-run/.
Copied logic from <OUT-DIR>/r7joint-run/scripts/01_joint_run.py, parametrized by ring form.
Run: python3 -I <OUT-DIR>/r7boat-run/scripts/01_boat_run.py

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import csv
import json
import os
import subprocess
import sys
from collections import Counter
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
DRAFT_FILE = Path(
    os.environ.get(
        "R7_TORSION_DRAFT",
        REPO_ROOT
        / "docs"
        / "confgen-fix"
        / "tools"
        / "r7_glucose"
        / "joint"
        / "torsion_draft.json",
    )
)

sys.path.insert(0, str(REPO_ROOT))
from confflow.core.io import read_xyz_file  # noqa: E402
from confflow.domain.structure import StructureRecord  # noqa: E402
from confflow.science.confgen.engine import ConfgenEngine  # noqa: E402
from confflow.science.confgen.model import build_context  # noqa: E402
from confflow.science.confgen.ring.puckering import (  # noqa: E402
    CPCoords,
    cp_distance,
    cremer_pople,
)

CSV = OUT / "csv"
OUT.mkdir(parents=True, exist_ok=True)
CSV.mkdir(parents=True, exist_ok=True)


RING = [1, 2, 3, 4, 5, 6]
FIXD = FIXTURE_DIR
DRAFT = DRAFT_FILE
CP_TABLE = DATA_DIR / "cp_table.csv"
LEAVES = [("TB_0", 8), ("B_1", 3)]  # (precise form, expected explicit-38 seed ordinal)


def ring_rbar(xyz, ring):
    n = len(ring)
    return float(np.mean([np.linalg.norm(xyz[ring[(i + 1) % n]] - xyz[ring[i]]) for i in range(n)]))


def thaw(v):
    from confflow.domain._immutable import FrozenDict as _FD

    if isinstance(v, _FD):
        return {str(k): thaw(x) for k, x in dict(v).items()}
    if isinstance(v, (list, tuple)):
        return [thaw(x) for x in v]
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return str(v)
    return v


def run_one(form, expect_seed):
    crest_frames = read_xyz_file(str(FIXD / "crest_conformers.xyz"), strict=True)
    driving = crest_frames[0]
    rec = StructureRecord(
        id=f"r7boat:beta_d_glucopyranose:crest-first:{form}x729",
        atoms=tuple(driving["atoms"]),
        coordinates=tuple(map(tuple, np.asarray(driving["coords"], dtype=float))),
        charge=0,
        multiplicity=1,
    )
    draft = json.loads(DRAFT.read_text())
    assert draft["index_base"] == 1
    tors0 = []
    for e in draft["torsions"]:
        e2 = dict(e)
        e2["atoms"] = [int(a) - 1 for a in e["atoms"]]
        tors0.append(e2)
    axis_ids = [e["id"] for e in draft["torsions"]]
    axis_angles = [[float(a) for a in e["angles"]] for e in draft["torsions"]]

    spec = {
        "index_base": 0,
        "rings": [{"id": "r1", "atoms": list(RING), "forms": [form]}],
        "torsions": tors0,
    }
    ctx = build_context(rec, spec)
    active = tuple(ctx.active_components)
    assert set(active) == {"rings", "torsions"}, active

    ctx0 = build_context(
        rec, {"index_base": 0, "rings": [{"id": "r1", "atoms": list(RING), "forms": [form]}]}
    )
    run0 = ConfgenEngine().run(ctx0)
    leaves0 = list(run0.leaves)
    assert len(leaves0) == 1, (form, len(leaves0))
    xyz_parent = np.asarray(leaves0[0].structure.coordinates, dtype=float)
    cp_parent = cremer_pople(xyz_parent[RING])
    parent = {"q": float(cp_parent.q), "theta": float(cp_parent.theta), "phi": float(cp_parent.phi)}
    try:
        pcmd = dict(getattr(leaves0[0].state_key, "rings", {}).get("r1", {}))
    except Exception:
        pcmd = {}

    run = ConfgenEngine().run(ctx)
    targets = list(run.target_records)
    leaves = list(run.leaves)
    n_targets = len(targets)
    n_pub = len(leaves)

    fail_counter = Counter()
    fail_rows = []
    for t in targets:
        st = str(getattr(getattr(t, "status", ""), "value", getattr(t, "status", "")))
        rs = str(getattr(t, "reason", ""))
        if st != "published_leaf":
            fail_counter[(st, rs)] += 1
            fail_rows.append(
                {
                    "target_id": t.target_id,
                    "axis": getattr(t, "axis", ""),
                    "status": st,
                    "reason": rs,
                    "state_value": json.dumps(thaw(getattr(t, "state_value", {})), sort_keys=True)[
                        :400
                    ],
                }
            )

    leaf_rows = []
    for li, leaf in enumerate(leaves):
        xyz = np.asarray(leaf.structure.coordinates, dtype=float)
        cp = cremer_pople(xyz[RING])
        rb = ring_rbar(xyz, RING)
        sk = leaf.state_key
        try:
            tcmd = dict(getattr(sk, "torsions", {}))
        except Exception:
            tcmd = {}
        try:
            rcmd = dict(getattr(sk, "rings", {}).get("r1", {}))
        except Exception:
            rcmd = {}
        a = CPCoords(n=6, q=float(cp.q), theta=float(cp.theta), phi=float(cp.phi))
        b = CPCoords(n=6, q=parent["q"], theta=parent["theta"], phi=parent["phi"])
        drift = float(cp_distance(a, b))
        leaf_rows.append(
            {
                "leaf": li,
                "target_id": dict(getattr(leaf, "provenance", {})).get("leaf_target_id", ""),
                "ring_form": rcmd.get("form"),
                "ring_index": rcmd.get("index"),
                "ring_anchor": rcmd.get("anchor"),
                "ring_direction": rcmd.get("direction"),
                "tors_cmd": json.dumps(tcmd, sort_keys=True),
                "Q": float(cp.q),
                "theta": float(cp.theta),
                "phi": float(cp.phi),
                "rbar": rb,
                "q_over_rbar": float(cp.q / rb),
                "cp_drift_vs_parent_deg": drift,
                "xyz": xyz,
            }
        )
    for r in leaf_rows:
        cmd = json.loads(r["tors_cmd"])
        coords = []
        for aid, angs in zip(axis_ids, axis_angles):
            v = cmd.get(aid)
            pos = min(range(len(angs)), key=lambda i: abs(angs[i] - float(v)))
            coords.append(pos)
        r["grid"] = coords

    max_drift = max((r["cp_drift_vs_parent_deg"] for r in leaf_rows), default=float("nan"))
    mean_drift = (
        float(np.mean([r["cp_drift_vs_parent_deg"] for r in leaf_rows]))
        if leaf_rows
        else float("nan")
    )

    tag = form.replace("_", "")
    els = list(driving["atoms"])
    with open(OUT / f"boat_{tag}_leaves.xyz", "w") as f:
        for r in leaf_rows:
            f.write(f"{len(els)}\n")
            f.write(
                f"boat leaf {r['leaf']} {r['target_id']} ring={form} grid={r['grid']} drift={r['cp_drift_vs_parent_deg']:.4f}\n"
            )
            for el, (x, y, z) in zip(els, r["xyz"]):
                f.write(f"{el} {x:.6f} {y:.6f} {z:.6f}\n")

    with open(CSV / f"boat_{tag}_leaves_cp.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "leaf",
                "target_id",
                "ring_form",
                "ring_index",
                "ring_anchor",
                "ring_direction",
                "grid_omega",
                "grid_oh1",
                "grid_oh2",
                "grid_oh3",
                "grid_oh4",
                "grid_oh6",
                "cmd_omega",
                "cmd_oh1",
                "cmd_oh2",
                "cmd_oh3",
                "cmd_oh4",
                "cmd_oh6",
                "Q",
                "theta",
                "phi",
                "rbar",
                "q_over_rbar",
                "cp_drift_vs_parent_deg",
            ]
        )
        for r in leaf_rows:
            cmd = json.loads(r["tors_cmd"])
            w.writerow(
                [
                    r["leaf"],
                    r["target_id"],
                    r["ring_form"],
                    r["ring_index"],
                    r["ring_anchor"],
                    r["ring_direction"],
                    *r["grid"],
                    cmd.get("omega"),
                    cmd.get("oh1"),
                    cmd.get("oh2"),
                    cmd.get("oh3"),
                    cmd.get("oh4"),
                    cmd.get("oh6"),
                    round(r["Q"], 4),
                    round(r["theta"], 3),
                    round(r["phi"], 3),
                    round(r["rbar"], 4),
                    round(r["q_over_rbar"], 4),
                    round(r["cp_drift_vs_parent_deg"], 4),
                ]
            )

    with open(CSV / f"boat_{tag}_failures.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["target_id", "axis", "status", "reason", "state_value"])
        w.writeheader()
        w.writerows(fail_rows)

    from confflow.science.confgen.model import WorkingRealization
    from confflow.science.confgen.torsion.stage import TorsionStage

    stage = TorsionStage(ctx.resolved_spec)
    root = WorkingRealization(structure=ctx.structure, state_key=ctx.input_state_key)
    est = stage.estimate(root, ctx)

    return {
        "form": form,
        "expect_seed": expect_seed,
        "active": list(active),
        "declared_torsion_exact": (
            int(est.declared_count) if hasattr(est, "declared_count") else None
        ),
        "estimate_exact": bool(getattr(est, "exact", None)),
        "targets": n_targets,
        "published": n_pub,
        "eliminated": n_targets - n_pub,
        "fail_census": {f"{k[0]}|{k[1]}": v for k, v in fail_counter.items()},
        "parent_CP": {k: round(v, 4) for k, v in parent.items()},
        "parent_cmd": pcmd,
        "max_cp_drift_vs_parent_deg": round(float(max_drift), 4),
        "mean_cp_drift_vs_parent_deg": round(float(mean_drift), 6),
        "xyz_file": f"boat_{tag}_leaves.xyz",
    }


def main():
    try:
        head = subprocess.check_output(
            ["git", "-C", "/opt/ConfFlow", "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        head = "unknown"
    import confflow

    binding = str(Path(confflow.__file__).resolve())

    # cp_table confirmation for TB_0 (seed8) and B_1 (seed3)
    cp_rows = list(csv.DictReader(open(CP_TABLE)))
    by_seed = {int(r["seed"]): r for r in cp_rows}
    seed_confirm = {}
    for form, seed in LEAVES:
        r = by_seed[seed]
        fam, idx = form.split("_")
        seed_confirm[form] = {
            "seed": seed,
            "cp_table_family": r["family"],
            "cp_table_index": r["index"],
            "cp_table_anchor": r["anchor"],
            "cp_table_direction": r["direction"],
            "match": bool(r["family"] == fam and int(r["index"]) == int(idx)),
            "Q": float(r["Q"]),
            "theta": float(r["theta"]),
            "phi": float(r["phi"]),
        }
    assert all(v["match"] for v in seed_confirm.values()), seed_confirm

    per_leaf = []
    for form, seed in LEAVES:
        per_leaf.append(run_one(form, seed))

    # cross-check parent CP vs cp_table ideal for each leaf (tolerance: solver drift small)
    summary = {
        "HEAD": head,
        "binding": binding,
        "driving": "crest_conformers.xyz:frame0",
        "torsion_spec": "torsion_draft.json 6x absolute_dihedral_grid enumerate [60,180,-60], omega=right else left; converted 1-based->0-based",
        "seed_confirm_cp_table": seed_confirm,
        "per_leaf": per_leaf,
        "total_targets": sum(p["targets"] for p in per_leaf),
        "total_published": sum(p["published"] for p in per_leaf),
        "total_eliminated": sum(p["eliminated"] for p in per_leaf),
    }
    with open(OUT / "boat_run_summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
