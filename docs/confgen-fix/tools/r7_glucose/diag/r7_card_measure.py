#!/usr/bin/env python3
"""R7-card default-vs-explicit measurement (read-only).

Run: python3 -I <OUT-DIR>/r7-card-out/card_measure.py

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import csv
import json
import os
import subprocess
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
OUT_DIR = Path(os.environ.get("R7_CARD_OUT", Path.cwd() / "r7-card-out"))
OUT = OUT_DIR

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

FIXD = FIXTURE_DIR


RING = [1, 2, 3, 4, 5, 6]
CP_HIT = 15.0


def run_mode(forms):
    crest_frames = read_xyz_file(str(FIXD / "crest_conformers.xyz"), strict=True)
    driving = crest_frames[0]
    rec = StructureRecord(
        id="r7card:beta_d_glucopyranose:crest-first",
        atoms=tuple(driving["atoms"]),
        coordinates=tuple(map(tuple, np.asarray(driving["coords"], dtype=float))),
        charge=0,
        multiplicity=1,
    )
    entry = {"id": "r1", "atoms": list(RING)}
    if forms is not None:
        entry["forms"] = list(forms)
    ctx = build_context(rec, {"index_base": 0, "rings": [entry]})
    run = ConfgenEngine().run(ctx)
    leaves = list(run.leaves)
    targets = list(run.target_records)
    seeds = []
    for leaf in leaves:
        xyz = np.asarray(leaf.structure.coordinates, dtype=float)
        cp = cremer_pople(xyz[RING])
        try:
            cmd = dict(getattr(leaf.state_key, "rings", {}).get("r1", {}))
        except Exception:
            cmd = {}
        seeds.append(
            {
                "theta": float(cp.theta),
                "phi": float(cp.phi),
                "q": float(cp.q),
                "fam": str(cmd.get("form")),
                "idx": cmd.get("index"),
            }
        )
    refs = []
    for fr in crest_frames:
        xyz = np.asarray(fr["coords"], dtype=float)
        cp = cremer_pople(xyz[RING])
        refs.append((float(cp.theta), float(cp.phi), float(cp.q)))
    rows = []
    for k, (rt, rp, rq) in enumerate(refs):
        a = CPCoords(n=6, q=rq, theta=rt, phi=rp)
        best, bi = 1e9, -1
        for i, s in enumerate(seeds):
            b = CPCoords(n=6, q=s["q"], theta=s["theta"], phi=s["phi"])
            d = float(cp_distance(a, b))
            if d < best:
                best, bi = d, i
        rows.append(
            {
                "ref": k,
                "best_seed": bi,
                "min_cp_dist_deg": round(best, 4),
                "hit_lt15": int(best < CP_HIT),
            }
        )
    hits = sum(r["hit_lt15"] for r in rows)
    fams = sorted(set(s["fam"] for s in seeds))
    return {
        "targets": len(targets),
        "published": len(leaves),
        "families": fams,
        "recall": f"{hits}/{len(refs)}",
        "hits": hits,
        "rows": rows,
        "seeds": seeds,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        head = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
    except Exception:
        head = "unknown"
    explicit = run_mode(["C", "B", "TB", "E", "H"])
    default = run_mode(None)
    with open(OUT / "default_vs_explicit_match_default.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ref", "best_seed", "min_cp_dist_deg", "hit_lt15"])
        w.writeheader()
        w.writerows(default["rows"])
    with open(OUT / "default_vs_explicit_match_explicit.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ref", "best_seed", "min_cp_dist_deg", "hit_lt15"])
        w.writeheader()
        w.writerows(explicit["rows"])
    summary = {
        "HEAD": head,
        "system": "beta_d_glucopyranose",
        "driving": "crest_conformers.xyz:frame0",
        "ring_0based": RING,
        "recall_calibre": "cp_distance(theta,phi) < 15deg, seeds=published leaves only (ring_benchmark.py same)",
        "default_no_forms": {
            "targets": default["targets"],
            "published": default["published"],
            "families": default["families"],
            "recall": default["recall"],
        },
        "explicit_38": {
            "targets": explicit["targets"],
            "published": explicit["published"],
            "families": explicit["families"],
            "recall": explicit["recall"],
        },
    }
    with open(OUT / "default_vs_explicit_summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
