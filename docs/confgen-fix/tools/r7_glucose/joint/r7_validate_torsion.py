#!/usr/bin/env python3
"""Validate the torsion draft JSON without touching the repo.

只读验证 torsion 草案 JSON（归档版），用法见 README。

归档说明：本文件为 R7 只读诊断脚本的归档版，计算逻辑与原脚本一致；原机器绝对路径硬编码已集中到文件头常量（可用环境变量覆盖，详见 README），输出大文件（优化产物 xyz/out）未归档。保留 `python3 -I` 与 `sys.path.insert` 写法。
"""

import json
import os
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
FIXTURE_INPUT = FIXTURE_DIR / "input.xyz"

sys.path.insert(0, str(REPO_ROOT))

DRAFT = DRAFT_FILE
FIXTURE = FIXTURE_INPUT


def main() -> int:
    from confflow.science.confgen.planner import _TORSION_MODELS, _TREATMENTS
    from confflow.science.confgen.torsion.spec import normalize_spec, resolve_torsion_axes

    raw = json.load(open(DRAFT))
    assert raw["index_base"] == 1 and raw["schema_version"] == 3
    entries = raw["torsions"]
    assert [e["model"] for e in entries] == ["absolute_dihedral_grid"] * 6
    assert set(_TORSION_MODELS) >= {"absolute_dihedral_grid"}
    assert set(_TREATMENTS) >= {"enumerate"}
    # 结构无关校验(0-based 内码): 唯一 id/合法键/4 原子/有限角/无周期重复/单键单轴
    norm = dict(normalize_spec({"torsions": entries}, index_base=1))
    axes = resolve_torsion_axes(tuple(norm["torsions"]), index_base=0)
    assert len(axes) == 6 and len({a.axis_id for a in axes}) == 6
    bonds = [(min(a.bond), max(a.bond)) for a in axes]
    assert len(set(bonds)) == 6, f"duplicate bond {bonds}"
    print("structure-independent OK:", [(a.axis_id, a.bond, a.values) for a in axes])

    # 结构相关校验(夹具 input.xyz, 只读): 成键/非环键/可测帧 + 联合计数
    from confflow.domain.structure import StructureRecord
    from confflow.science.confgen.model import WorkingRealization, build_context
    from confflow.science.confgen.torsion.stage import TorsionStage

    lines = open(FIXTURE).read().splitlines()
    n = int(lines[0].strip())
    atoms, coords = [], []
    for ln in lines[2 : 2 + n]:
        p = ln.split()
        atoms.append(p[0])
        coords.append((float(p[1]), float(p[2]), float(p[3])))
    rec = StructureRecord(
        id="glc-fixture",
        atoms=tuple(atoms),
        coordinates=tuple(coords),
        charge=0,
        multiplicity=1,
    )
    ctx = build_context(rec, {"schema_version": 3, "index_base": 1, "torsions": entries})
    stage = TorsionStage(ctx.resolved_spec)
    root = WorkingRealization(structure=ctx.structure, state_key=ctx.input_state_key)
    est = stage.estimate(root, ctx)  # 内含 bonded/ring-bond/frame 三项 fail-closed 检查
    assert est.declared_count == 3**6 == 729 and est.exact
    print("context OK: bonded+non-ring+measurable, estimate ==", est.declared_count)
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
