# ConfFlow TS 步骤方案（C4，2026-10-26）

来源：`CONFFLOW-NOTES.md` 2026-10-25 stage 4/5 各条；用户 stage 4 流程（`runs/20261024-pilot-site1-ts1/stage4_check.py`、`run_ts.sh`）。
用户 2026-10-26：剩余大功能全部做，顺序自定。本方案在 MLIP 步骤（PR #187）之后。

## 1. 现状与缺口

ConfFlow 已能跑 TS 优化：`calculation` + Gaussian `opt=(calcfc,ts,...) external="uma_s"` 或 ORCA OptTS；已有检查
`normal_termination`、`imaginary_frequency_count`、`bond_drift`；`refine` 绑定 `results` 可去重（stage 4 37→15 已验证同口径）。

用户 stage 4 手工补的、ConfFlow 缺的：
1. **键长窗口**：O39–C95 1.6–2.1、C95–O96 1.24–1.30、C95–O97 1.33–1.40 Å。约 45% 的 TS 优化滑到甲基/叔丁基转子鞍点，
   形成键停在 2.45 或 1.52 Å——绝对范围检查能拦住；`bond_drift` 只看相对输入的漂移，不够。
2. **TS 模式检查**：虚频振动应主要是反应键伸缩，而不是转子（本征值 ~-1e-3）。
3. **成键图不变**：除反应对外共价图与输入相同（stage 4 的 bonds/zr/face 检查的通用部分）。
4. **从鞍点附近起步**：在 UMA 鞍点距离（1.73 Å）重新约束弛豫后再做 TS 优化，救回约一半失败者。

## 2. 设计

### 2.1 新检查（calculation 步骤，Gaussian 与 ORCA 通用）

```yaml
checks: [normal_termination, imaginary_frequency_count, distance_window, ts_mode, graph_unchanged]
check_params:
  imaginary_frequency_count: {expected: 1}
  distance_window: {distances: [[39, 95, 1.6, 2.1], [95, 96, 1.24, 1.30]]}   # 1 基，Å，闭区间
  ts_mode: {pairs: [[39, 95]], min_overlap: 0.3}                            # 默认 min_overlap 见 §4
  graph_unchanged: {ignore_pairs: [[39, 95]], bond_scale: 1.15}
```

- `distance_window`：输出几何上每个距离都在 [lo, hi]；失败信息列出每个距离的实测值。
- `ts_mode`：取最低虚频的笛卡尔位移向量 u（需在同一作业里做频率：Gaussian `opt=(calcfc,ts) freq`，ORCA `OptTS Freq`）。
  子空间定义：对每个反应对 (a,b)，取单位伸缩向量 s_ab（a 上 +e_ab、b 上 −e_ab，e_ab 为 a→b 单位向量），
  对 {s_ab} 正交化得到 Q（丢弃 <1e-8 的近线性相关方向），重叠 = ||Q^T u|| / ||u||。
  与符号无关：形成键与断裂键反向伸缩时两者都计入（旧的求和向量会相互抵消）。单对时等于 |u·s|/|u|。
  前提：位移与几何须处于同一取向（取同一日志解析出的最终几何，即 Gaussian/ORCA 打印振动模式所用的坐标系）。
  转子模式的重叠接近 0，反应键伸缩模式接近 1。没有虚频或没有位移数据 → 失败（原因分开写）。
  真实 HCN→HNC 鞍点（Gaussian External UMA freq）：N–H 0.563，C–H 0.184；两对合用 0.662（≥ 单对最大值）。
- `graph_unchanged`：输入与输出按 `bond_scale` 感知共价键（与 refine/mlip 同一函数），去掉 `ignore_pairs` 后比较；
  失败列出新增/断开的键。

### 2.2 解析器

Gaussian 与 ORCA 解析最后一次频率计算的最低虚频位移向量，放进 `native_metadata["imaginary_mode"] =
{"frequency_cm": f, "displacements": [[dx,dy,dz], ...]}`；parser_version 递增。检查从 profile 输出读它（必要时 profile 透传）。

### 2.3 MLIP 步骤：带目标值的固定距离

`relax.fix_distances` 每项可写 `[a, b]`（保持输入距离）或 `[a, b, target]`（先把距离设为 target，两原子各移一半，再固定）。
用于"在 UMA 鞍点距离重新约束弛豫"后再做 TS 优化。

### 2.4 推荐流程（文档 + 示例 `docs/examples/uma_ts_funnel.yaml`）

ConfGen（反应对约束）→ mlip `sp_then_relax`（`fix_distances: [[39, 95, 1.73]]`）→ Gaussian External UMA TS
（`opt=(calcfc,ts,noeigentest) freq`，上面五个检查，`scheduler.on_failure: continue`、completion 允许部分成功）→
refine（绑定 results）去重 → DFT。

不做：作业内自动重试/换起点（失败者由用户按报告重新提交）；"力收敛即接受"（Gaussian 关键字层面的事，文档说明用默认收敛而不是 tight）。

## 3. 分工（Haiku 5.5 子代理，文件不重叠）

- A：`checks.py`（CHECK_DEFAULTS）、`checks_standard.py`（DistanceWindowCheck、GraphUnchangedCheck、CHECKS 注册含 B 的 TsModeCheck）、
  `registry.py`（三个 CheckSpec）+ `tests/v4/test_ts_checks.py`。
- B：`programs/gaussian/parsing.py`、`programs/orca/parsing.py`（虚频位移）、必要时 `profile_standard.py` 透传、
  新文件 `execution/checks_ts.py`（TsModeCheck）+ `tests/v4/test_ts_mode.py`。
- C：MLIP 带目标值固定距离（`workflow/v4/schema.py` 的 MlipRelaxModel、`mlip_executor.py`、`mlip_runner.py`）+ 对应测试。
- 我：方案、阈值标定（§4）、示例与文档、契约重钉、验收。

## 4. 阈值标定（验收前必须做）

用 stage 4 已有结果：通过的 TS 与滑到转子鞍点的失败者各取几个，用 UMA Hessian（`uma_hess.py` 同法，本机小计算）
得到最低虚频模式，算重叠，确认两类分得开，再定 `min_overlap` 默认值。数据写进本节。

**2026-10-26 实测（现有 UMA Hessian，无新计算）：**

| 结构 | 最低虚频 | 反应对 | 重叠 |
|---|---|---|---|
| TS1 poolHm0358（stage 6） | −175.2 cm⁻¹ | O39–C95 | 0.472 |
| TS2 c7 final | −185.2 cm⁻¹ | C95–O97 | 0.479 |

真实反应模式的重叠约 0.47（模式里还有配体的协同运动，所以不接近 1）。转子模式运动的是远离反应对的甲基/叔丁基氢，
按几何应接近 0；stage 4 的转子鞍点没有频率数据，补算每个 122 原子 Hessian 本机约 30 分钟，未做。
默认 `min_overlap` 定为 **0.2**（正例余量一倍以上）。另：stage 4 的 37 个失败者里 15 个停在 1.47–1.55 Å、16 个停在
2.44–2.70 Å，全部能被 `distance_window`（1.6–2.1）拦住；`ts_mode` 是第二道保险。

## 5. 验收

全部测试、mypy、black 26.10.1、ruff、行数预算、changed-coverage ≥85%；标定数据显示两类可分；示例 validate 通过；
用一个真实 Gaussian External UMA TS 日志（stage 4）跑检查，结果与 `stage4_check.py` 一致。
