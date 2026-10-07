#!/usr/bin/env python3
"""R7-boat 03: stratified sample 100/729 per boat leaf (seed 7) + ORCA XTB2 Opt inputs.

Per leaf (TB_0, B_1): random 100/729, covers each of 6 axes x 3 states.
Writes inputs_boat_{TB0,B1}_100/ + job lists.
Run: python3 -I <OUT-DIR>/r7boat-run/scripts/03_boat_sample.py

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import csv
import os
import random
import sys
from collections import Counter
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
OUT_DIR = Path(os.environ.get("R7_BOAT_RUN_OUT", Path.cwd() / "r7boat-run"))
OUT = OUT_DIR

sys.path.insert(0, str(REPO_ROOT))

CSV = OUT / "csv"

AXIS = ["omega", "oh1", "oh2", "oh3", "oh4", "oh6"]
LEAVES = [
    ("TB0", "boat_TB0_leaves_cp.csv", "boat_TB0_leaves.xyz"),
    ("B1", "boat_B1_leaves_cp.csv", "boat_B1_leaves.xyz"),
]


def sample_one(tag, cp_csv, xyz_name):
    rows = list(csv.DictReader(open(CSV / cp_csv)))
    assert len(rows) == 729, (tag, len(rows))
    grids = [[int(r[f"grid_{a}"]) for a in AXIS] for r in rows]

    rng = random.Random(7)
    sampled = sorted(rng.sample(range(729), 100))

    def coverage(idxs):
        cov = {}
        for j, a in enumerate(AXIS):
            c = Counter(grids[i][j] for i in idxs)
            cov[a] = {s: c.get(s, 0) for s in (0, 1, 2)}
        return cov

    cov = coverage(sampled)
    missing = [(a, s) for a in AXIS for s in (0, 1, 2) if cov[a][s] == 0]
    if missing:
        pool = [i for i in range(729) if i not in set(sampled)]
        for a, s in missing:
            j = AXIS.index(a)
            cand = next(i for i in pool if grids[i][j] == s)
            sampled = sorted(sampled)
            out_idx = sampled[-1]
            sampled = sorted([x for x in sampled if x != out_idx] + [cand])
            pool = [i for i in pool if i != cand]
        cov = coverage(sampled)
        missing = [(a, s) for a in AXIS for s in (0, 1, 2) if cov[a][s] == 0]
    assert not missing, (tag, missing)

    lines = (OUT / xyz_name).read_text().splitlines()
    frames = {}
    i = 0
    while i < len(lines):
        n = int(lines[i].strip())
        comment = lines[i + 1]
        leaf = int(comment.split()[2])
        frames[leaf] = (n, comment, lines[i + 2 : i + 2 + n])
        i += 2 + n
    assert len(frames) == 729, (tag, len(frames))

    inpdir = OUT / f"inputs_boat_{tag}_100"
    inpdir.mkdir(parents=True, exist_ok=True)
    for f in inpdir.glob("*.inp"):
        f.unlink()
    prefix = "tb" if tag == "TB0" else "b1"
    for leaf in sampled:
        n, comment, coords = frames[leaf]
        (inpdir / f"boat_{prefix}_{leaf:03d}.inp").write_text(
            "! XTB2 Opt\n%pal nprocs 1 end\n* xyz 0 1\n" + "\n".join(coords) + "\n*\n"
        )

    with open(CSV / f"boat_{tag}_sample100.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "sample_ord",
                "boat_leaf",
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
            ]
        )
        rmap = {int(r["leaf"]): r for r in rows}
        for o, leaf in enumerate(sampled):
            r = rmap[leaf]
            w.writerow(
                [
                    o,
                    leaf,
                    r["grid_omega"],
                    r["grid_oh1"],
                    r["grid_oh2"],
                    r["grid_oh3"],
                    r["grid_oh4"],
                    r["grid_oh6"],
                    r["cmd_omega"],
                    r["cmd_oh1"],
                    r["cmd_oh2"],
                    r["cmd_oh3"],
                    r["cmd_oh4"],
                    r["cmd_oh6"],
                ]
            )

    lst = OUT / f"inputs_boat_{tag}_100.list"
    with open(lst, "w") as f:
        for leaf in sampled:
            f.write(str(inpdir / f"boat_{prefix}_{leaf:03d}.inp") + "\n")
    print(f"{tag}: sampled={len(sampled)} coverage={cov} inputs={len(list(inpdir.glob('*.inp')))}")
    return sampled, cov


def main():
    all_cov = {}
    for tag, cp_csv, xyz_name in LEAVES:
        _, cov = sample_one(tag, cp_csv, xyz_name)
        all_cov[tag] = cov
    # combined list
    with open(OUT / "inputs_boat200.list", "w") as out:
        for tag in ("TB0", "B1"):
            for line in (OUT / f"inputs_boat_{tag}_100.list").read_text().splitlines():
                out.write(line + "\n")
    print("combined list: inputs_boat200.list (200)")


if __name__ == "__main__":
    main()
