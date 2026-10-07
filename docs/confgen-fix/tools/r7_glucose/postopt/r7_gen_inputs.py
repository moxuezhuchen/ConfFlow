"""Gen ORCA XTB2 Opt inputs (no constraints).

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import os
import random
import sys
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
OUT_DIR = Path(os.environ.get("R7_D_OUT", Path.cwd() / "r7d-out"))
OUT = OUT_DIR
DATA_DIR = _require_dir("R7_DATA_DIR", "未发布的 root 全体系输入 beta_d_glucopyranose 目录")
FIXTURE_CREST = FIXTURE_DIR / "crest_conformers.xyz"

sys.path.insert(0, str(REPO_ROOT))

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "inputs").mkdir(parents=True, exist_ok=True)


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
        coords = [ln for ln in lines[i + 2 : i + 2 + n]]
        frames.append((comment, coords))
        i += 2 + n
    return frames


seeds = read_xyz_frames(DATA_DIR / "seeds.xyz")
print(f"seeds={len(seeds)}")
assert len(seeds) == 38
crest = read_xyz_frames(FIXTURE_CREST)
print(f"crest={len(crest)}")
assert len(crest) == 155
rng = random.Random(7)
ctrl_idx = sorted(rng.sample(range(155), 5))
print("ctrl_idx=", ctrl_idx)
Path(OUT / "ctrl_idx.txt").write_text(",".join(map(str, ctrl_idx)) + "\n")
T = "! XTB2 Opt\n%pal nprocs 1 end\n* xyz 0 1\n{coords}*\n"
for s, (_c, cl) in enumerate(seeds):
    Path(OUT / "inputs" / f"seed_{s:02d}.inp").write_text(
        "! XTB2 Opt\n%pal nprocs 1 end\n* xyz 0 1\n" + "\n".join(cl) + "\n*\n"
    )
for k in ctrl_idx:
    c, cl = crest[k]
    Path(OUT / "inputs" / f"ctrl_ref{k:03d}.inp").write_text(
        "! XTB2 Opt\n%pal nprocs 1 end\n* xyz 0 1\n" + "\n".join(cl) + "\n*\n"
    )
print("wrote inputs:", len(list(Path(OUT / "inputs").glob("*.inp"))))
