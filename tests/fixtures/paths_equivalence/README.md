# legacy paths 与 typed v3 paths 的等价性 golden（IS.1 + IS.1b）

本目录记录：同一个 legacy paths 声明分别走 legacy 执行路径和对应 typed v3
声明（或对照声明）时的实际输出，并给出逐用例等价性结论。
ConfFlow 分支 `implementation/input-simplification`，基线提交 `f87da58`。

## 比较方法（IS.1b 修订）

- 所有"等价 / 去重 / 集合相等"判断一律使用 `proper_rotation_rmsd`
  （只含真旋转、无镜像的 Kabsch，包装 `confflow.science.cluster.kabsch_rmsd`），
  阈值统一为 **1e-5 Å**（`TOL`）。IS.1 原来的"不做叠合、逐原子最大偏差"
  比较已废弃（无氢夹具上绕末端键的旋转只是刚体转动）。
- 工具在每次运行（含 `--check`）开始时执行三个自检；任一失败则以非零退出，
  不写 `result.json`。自检数值写入 `result.json` 的 `selfcheck` 节：
  - SC1 刚体旋转 → 相同：带氢非平面分子（1-丙醇）施加固定种子
    （`numpy default_rng(12345)`）的真旋转+平移，RMSD = 1.31e-15 ≤ 1e-5；
  - SC2 镜像 → 不同：手性分子 CHFClBr 做 x → −x 镜像，
    RMSD = 1.231 > 1e-5；
  - SC3 真实二面角变化 → 不同：带氢 n-丁烷绕 C2–C3 键把 C3–C4 端
    （连同氢）转 60°，RMSD = 0.578 > 1e-5。

## 文件

- `capture_cases.py`：pytest 插件（IS.1），捕获 legacy 执行并生成 25 个
  无氢用例（`cases.json` 的 `cases`）。
- `make_fixtures_h.py`：一次性用 RDKit（`AddHs` + `EmbedMolecule(randomSeed=7)`
  + `MMFFOptimizeMolecule`）生成 n-丁烷、1-丙醇、丙胺、CHFClBr 的坐标并写入
  `fixtures_h.json`（原子序列：重原子在前、氢在后；含键表），同时把从键表
  推导出的 `hydrogen_cases` 合并进 `cases.json`。此后所有步骤只读 JSON，
  不再依赖 RDKit。
- `fixtures_h.json`：含氢分子坐标与键表（含 `selfcheck` 节引用的三个分子）。
- `cases.json`：`cases`（28 个 IS.1 无氢用例，其中 3 个 `extra_*`）+
  `hydrogen_cases`（9 个 `h_*` 用例）。
- `run_equivalence.py`：自检 → 逐用例执行 legacy 与 v3（公开入口
  `ConfgenExecutor().execute(item, ctx)`，`seed=11`）→ 比较并写 `result.json`。
- `result.json`：`selfcheck`、`counts`（只统计含氢用例）、`hydrogen_cases`、
  `no_hydrogen_regression`（IS.1 的 28 个无氢用例在新度量下的重判，**不计入
  结论**）。`sort_keys=True, indent=1`，无时间戳/临时路径，重复运行逐字节相同。

## 生成命令

```bash
cd /opt/cf-worktrees/exec-cf-is
PYTHONPATH=docs/refactor/paths_equivalence python3 -m pytest -q -o addopts="" \
  -p capture_cases -p no:cacheprovider \
  tests/v4/test_confgen_paths_phase0.py tests/v4/test_confgen_paths_audit.py
python3 docs/refactor/paths_equivalence/make_fixtures_h.py
python3 docs/refactor/paths_equivalence/run_equivalence.py            # 生成 result.json
python3 docs/refactor/paths_equivalence/run_equivalence.py --check    # 连续两次逐字节相同
```

## 含氢用例（结论计数只来自这里）

9 个 `h_*` 用例（n-丁烷、1-丙醇、丙胺各一个末端端点用例 + 各自的 bare
版本，以及三个内部键对照用例；端点原子序号由 `fixtures_h.json` 的键表推导，
不是猜的）：

| case_id | verdict | legacy | v3 | legacy_count | control_count | legacy_only | symmetry_aware |
|---|---|---|---|---|---|---|---|
| h_butane_terminal | EQUIVALENT | COMPLETED(27) | COMPLETED(27) | 27 | 27 | 0 | 0 |
| h_butane_terminal_bare | EQUIVALENT | COMPLETED(27) | COMPLETED(27) | 27 | 27 | 0 | 0 |
| h_propanol_oh | EQUIVALENT | COMPLETED(27) | COMPLETED(27) | 27 | 27 | 0 | 0 |
| h_propanol_oh_bare | EQUIVALENT | COMPLETED(27) | COMPLETED(27) | 27 | 27 | 0 | 0 |
| h_propylamine_nh2 | EQUIVALENT | COMPLETED(27) | COMPLETED(27) | 27 | 27 | 0 | 0 |
| h_propylamine_nh2_bare | EQUIVALENT | COMPLETED(27) | COMPLETED(27) | 27 | 27 | 0 | 0 |
| h_butane_internal | EQUIVALENT | COMPLETED(3) | COMPLETED(3) | 3 | 3 | 0 | 0 |
| h_propanol_internal | EQUIVALENT | COMPLETED(3) | COMPLETED(3) | 3 | 3 | 0 | 0 |
| h_propylamine_internal | EQUIVALENT | COMPLETED(3) | COMPLETED(3) | 3 | 3 | 0 | 0 |

标记计数：`EQUIVALENT` 9、`LEGACY_ONLY_TERMINAL_ROTOR` 0、`BOTH_REJECT` 0、
`V3_EMPTY_DEGENERATE` 0、`NOT_EQUIVALENT` 0。

要点：带氢分子上 v3 **不再拒绝**末端端点声明（C–H 键为相对旋转轴提供了
可测二面角框架），legacy 与 v3 都直接完成且构象集合在真旋转叠合下完全一致；
`LEGACY_ONLY_TERMINAL_ROTOR` 实测为 **0**（期望值，未做任何凑数调整）。
IS.1 里无氢夹具上的 14 个 `NOT_EQUIVALENT` 属于夹具无氢导致的框架缺失，
与含氢真实分子的行为无关（见 `no_hydrogen_regression`，不计入结论）。

## 无氢回归（`no_hydrogen_regression`，不计入结论）

IS.1 的 28 个无氢用例在新度量（真旋转叠合、1e-5）下重判：
`EQUIVALENT` 5、`NOT_EQUIVALENT` 14、`OUT_OF_SCOPE` 9。与 IS.1 的差异
（无 `LEGACY_DEGENERATE`、多 1 个 `EQUIVALENT`）来自度量变化，照实记录。

## 标记含义（含氢用例）

- `EQUIVALENT`：状态相同，且 legacy 与 v3（或对照）的结构集合在真旋转叠合、
  阈值 1e-5 Å 下一一对应；
- `LEGACY_ONLY_TERMINAL_ROTOR`：端点在末端重原子上、v3 拒绝、对照声明
  COMPLETED，且 legacy 去重后存在对照集合中找不到对应的结构。记录
  `legacy_count`、`control_count`、`legacy_only_count` 与
  `symmetry_aware_legacy_only`（后者允许置换同一端基重原子上的氢：CH3 3!、
  NH2 2、OH 无）。期望 0，实测大于 0 时验收结论为升级；
- `BOTH_REJECT`：legacy 与 v3 都非 COMPLETED（两边原因都记录）；
- `V3_EMPTY_DEGENERATE`：legacy 产出结构而 v3/对照 COMPLETED 但发布 0 个
  结构，且输入链所有原子共线（任意三原子叉积范数 < 1e-9，记录判定数据）；
- `NOT_EQUIVALENT`：其他任何差异。
