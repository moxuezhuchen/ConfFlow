#!/usr/bin/env python3
"""R7-joint 03: stratified sample 100/729 (seed 7) + ORCA XTB2 Opt inputs.

Covers each of 6 axes x 3 states. Writes inputs/ + job list.
Run: python3 -I <OUT-DIR>/r7joint-run/scripts/03_sample.py

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
OUT_DIR = Path(os.environ.get("R7_JOINT_RUN_OUT", Path.cwd() / "r7joint-run"))
OUT = OUT_DIR

sys.path.insert(0, str(REPO_ROOT))

CSV = OUT / "csv"

AXIS = ["omega", "oh1", "oh2", "oh3", "oh4", "oh6"]

rows = list(csv.DictReader(open(CSV / "joint_leaves_cp.csv")))
assert len(rows) == 729
# grid columns grid_omega..grid_oh6 hold 0/1/2 positions for [60,180,-60]
grids = [[int(r[f"grid_{a}"]) for a in AXIS] for r in rows]

rng = random.Random(7)
sampled = sorted(rng.sample(range(729), 100))


# coverage check: each axis x each state present?
def coverage(idxs):
    cov = {}
    for j, a in enumerate(AXIS):
        c = Counter(grids[i][j] for i in idxs)
        cov[a] = {s: c.get(s, 0) for s in (0, 1, 2)}
    return cov


cov = coverage(sampled)
missing = [(a, s) for a in AXIS for s in (0, 1, 2) if cov[a][s] == 0]
# repair deterministically if missing (swap in lowest-index uncovered leaf)
if missing:
    pool = [i for i in range(729) if i not in set(sampled)]
    for a, s in missing:
        j = AXIS.index(a)
        cand = next(i for i in pool if grids[i][j] == s)
        # replace the sampled leaf with worst redundancy (keep deterministic: replace highest index)
        sampled = sorted(sampled)
        out_idx = sampled[-1]
        sampled = sorted([x for x in sampled if x != out_idx] + [cand])
        pool = [i for i in pool if i != cand]
    cov = coverage(sampled)
    missing = [(a, s) for a in AXIS for s in (0, 1, 2) if cov[a][s] == 0]
assert not missing, missing

# joint_leaves.xyz frames -> per-leaf xyz lines
lines = (OUT / "joint_leaves.xyz").read_text().splitlines()
frames = {}
i = 0
while i < len(lines):
    n = int(lines[i].strip())
    comment = lines[i + 1]
    # leaf index parsed from comment "joint leaf {leaf} ..."
    leaf = int(comment.split()[2])
    frames[leaf] = (n, comment, lines[i + 2 : i + 2 + n])
    i += 2 + n
assert len(frames) == 729

inpdir = OUT / "inputs_joint100"
inpdir.mkdir(parents=True, exist_ok=True)
for f in inpdir.glob("*.inp"):
    f.unlink()
for leaf in sampled:
    n, comment, coords = frames[leaf]
    (inpdir / f"joint_{leaf:03d}.inp").write_text(
        "! XTB2 Opt\n%pal nprocs 1 end\n* xyz 0 1\n" + "\n".join(coords) + "\n*\n"
    )

with open(CSV / "joint_sample100.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(
        [
            "sample_ord",
            "joint_leaf",
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

with open(OUT / "inputs_joint100.list", "w") as f:
    for leaf in sampled:
        f.write(str(inpdir / f"joint_{leaf:03d}.inp") + "\n")

print(f"sampled={len(sampled)} coverage={cov}")
print(f"inputs in {inpdir}: {len(list(inpdir.glob('*.inp')))}")
