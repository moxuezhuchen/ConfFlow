# legacy paths 与 typed v3 paths 的等价性 golden（IS.1）

本目录记录输入简化改造（IS.2）之前，同一个 legacy paths 声明分别走 legacy
执行路径和对应 typed v3 声明的实际输出，并给出逐用例等价性结论。
ConfFlow 分支 `implementation/input-simplification`，基线提交 `f87da58`。

## 文件

- `capture_cases.py`：pytest 插件。包装
  `confflow.execution.confgen_executor.ConfgenExecutor._run_legacy_paths`，
  记录每次调用的 driving record（id、atoms、coordinates）和 native，按规范
  JSON 去重、排序后写入 `cases.json`，并追加三个手写 `extra_*` 用例。
- `cases.json`：用例清单（25 个捕获用例 + 3 个手写用例 = 28 个）。捕获来源：
  `tests/v4/test_confgen_paths_phase0.py`（44 项）与
  `tests/v4/test_confgen_paths_audit.py`（26 项）。
- `result.json`：逐用例结论（`sort_keys=True, indent=1`；不含时间戳或临时
  目录路径，重复运行逐字节相同）。
- `run_equivalence.py`：对每个用例执行 legacy 与 v3 两次运行并比较。

## 生成命令

```bash
cd /opt/cf-worktrees/exec-cf-is
PYTHONPATH=docs/refactor/paths_equivalence python3 -m pytest -q -o addopts="" \
  -p capture_cases -p no:cacheprovider \
  tests/v4/test_confgen_paths_phase0.py tests/v4/test_confgen_paths_audit.py
python3 docs/refactor/paths_equivalence/run_equivalence.py            # 生成 result.json
python3 docs/refactor/paths_equivalence/run_equivalence.py --check    # 重跑并逐字节比较
```

## 标记含义与计数（照实抄自 result.json）

- `EQUIVALENT`（4）：最终状态、失败原因、构象集合（逐原子最大偏差 ≤ 1e-6 Å，
  原子顺序相同）、转子角度集合全部相同。
- `LEGACY_DEGENERATE`（1）：v3 以 "no measurable dihedral frame (terminal
  pair)" 拒绝；把路径的末端端点（邻原子数 == 1 的端点）替换为其唯一邻居后，
  v3 对照声明的输出集合与按几何去重（≤ 1e-6 Å）后的 legacy 输出集合完全相同。
  `result.json` 中记录了被替换的端点、去重前后数量与两边集合摘要。
- `NOT_EQUIVALENT`（14）：其余任何差异（主要是：v3 拒绝末端端点声明，而
  legacy 的去重输出集合与对照声明集合不相同——legacy 绕末端端点键的旋转
  不是恒等操作；或 legacy 因 pregeometry 上限拒绝而 v3 因末端端点拒绝）。
- `OUT_OF_SCOPE`（9）：native 含 paths 范围之外的键（如 `chains`、
  `chain_angles`、`no_rotate`），不做 v3 映射与比较。

计数：EQUIVALENT 4、LEGACY_DEGENERATE 1、NOT_EQUIVALENT 14、OUT_OF_SCOPE 9，
合计 28。

## 备注

- 两边运行都通过公开入口 `ConfgenExecutor().execute(item, ctx)`，`seed=11`，
  与 `tests/v4/test_confgen_paths_audit.py` 的 `_run_legacy` 构造一致。
- `angle_step` 缺省时 bare 声明补 `step: 120`（`_DEFAULT_ANGLE_STEP`，Q2b）；
  `bond_scale` 映射到 v3 `tolerances.bond_scale`；`strict_path_bond_check`
  映射到 v3 顶层同名字段（`confflow/workflow/v4/confgen_schema.py` L418/L458）。
- LEGACY_DEGENERATE 的端点替换规则：凡邻原子数 == 1 的路径端点都替换为其
  唯一邻居（PLAN 原文为"把末端端点换成它唯一的邻居原子"；当两个端点都是
  末端原子时两个都替换）；替换后 `start == end` 视为声明非法，直接判
  `NOT_EQUIVALENT`。
- 存在 `NOT_EQUIVALENT`：按 PLAN §8 IS.1，验收结论应为"升级"，IS.2 不得
  开始。各用例的具体差异见 `result.json` 的 `differences` 字段。
