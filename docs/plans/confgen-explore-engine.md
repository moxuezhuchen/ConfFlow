# ConfGen 迭代搜索引擎落地计划

状态：**已确认**（2026-10-15，用户同意第 5 节三点：配置项与默认值、输出调整、暂不加双转子联动）。按本计划开发。

## 0. 目标与边界

把 2026-10 原型研究（`/root/confgen-dg-experiment-20261009/auto_20261012/`，服务器 `v5/` 各轮）验证过的路线
落成 ConfFlow 唯一的构象生成引擎：

- 一个通用引擎，不按体系做专门设计；全部由 typed graph 推导。
- 只做构象生成，不做 CREST 式动力学采样（见记忆 confgen-scope-vs-crest）；以后可与 CREST 协同。
- 计算引擎可替换：所有能量/优化经 `Calculator` 协议（PR #149，已完成），xTB 只是默认实现。
- 默认保持输入的配位排布；枚举异构体是显式选项。

不在本计划内（后续单独立项）：η 配位与 CN 2–3 等未支持配位类型；动力学增量；柔性体系自适应预算
（本计划只留接口）；DFT 抽检。

## 1. 现状

| 项 | 状态 |
| --- | --- |
| PR #149 计算引擎协议（`calculator.py`、`confgen_xtb.py`） | CI 通过，待合并 |
| PR #150 `.gitattributes` | CI 通过，待合并 |
| 本分支 `feat/confgen-explore-engine-20261015`（叠在 #149 上） | 已写未提交：`identity.py`、`rotors.py`、`explore.py`；审计加成键滞后；DG 嵌入失败回退；协议加 Hessian 能力 |

本分支首个实测（axurer，31 原子 Cd 配合物，1500 次优化，16 核，235 s）：最低能量与原型/GOAT 相差 0.01
kcal/mol，<1 kcal 4/4，<3 kcal 21/26（原型 23），Boltzmann 覆盖 97%（原型 98.5%，GOAT 83%）。
此前一次实测暴露“输入本身不属于任何理想多面体时仍按角度保持排布”会漏掉真实最低（+0.68），已改为
只在输入可归类时保持排布。

## 2. 设计决定（需要确认的标 ★）

### 2.1 模块划分（science 层纯计算，无 I/O、无进程）

| 模块 | 职责 |
| --- | --- |
| `science/confgen/calculator.py` | 引擎协议：约束、优化请求/结果、引擎身份与能力（带约束优化 / Hessian / 动力学） |
| `science/confgen/identity.py` | 构象同一性：图对称类、自同构、对称 RMSD、镜像合并、去重池 |
| `science/confgen/rotors.py` | 可转动键识别、周期与移动网格、刚性转动、二面角、小环签名、碰撞判断 |
| `science/confgen/explore.py` | 迭代搜索策略（见 2.2）；CPU 密集的 RDKit 任务经注入的 `parallel_map` 执行 |
| `science/confgen/search.py` | 目标规划、DG 起点、审计（保留）；旧的单轮 `run_search` 由 `explore` 取代 |
| `execution/confgen_xtb.py` | xTB 实现（优化、Hessian） |
| `execution/confgen_search_worker.py` | 构造引擎与进程池，调用 `explore`，写结果 |

### 2.2 搜索策略（与原型 r16 配置一致）

1. 全局起点：保持排布时只对输入所属类别做 DG；枚举时对所有类别（按配体图自同构合并等价类别）；
   第二轮只补有产出的类别。
2. 势阱探测：在至多 3 个“环状态×类别”格子的最低构象上，测出每个转子实际弛豫到的扭转偏移，并补上与任何实测偏移都相差 >30° 的网格移动（2026-10-15 验证后定：只用实测偏移时 TS1 漏掉探测父结构上不存在的阱，Boltzmann 覆盖 81%→补齐后 92%；axurer 80%→97%；奥司他韦不变；cnetpa 92%→88%，在单次波动内）。
3. 反馈轮次，固定预算份额：DG 30%、单键 55%、局部重嵌入 15%；单键命中率（指数平均）低于 10% 时，
   从单键里让 10% 给低频模式（仅当引擎声明 Hessian 能力）。
4. 去约束收尾（release）：对最低能量以上 window+2 kcal 内的构象，只保留反应对约束重新优化、审计、去重，
   发布 window 内的构象。
5. 审计：原有检查 + 成键滞后（参考键 >1.45×半径和才算断，新键 <1.10× 才算成）+ 约束偏差 >0.03 Å 拒绝；
   配位类别不符但每个配体原子距离 ≤1.3× 参考时记为 `unclassified` 接受；保持排布时按配体–金属–配体
   夹角（模图等价置换）偏差 ≤45° 判断。
6. 去重：能量差 ≤0.5 kcal/mol 且对称 RMSD（重原子+杂原子上的 H，镜像合并）<0.2 Å 视为同一构象。

双转子联动（pair）**暂不加入**：r18 结果在服务器上，未评估；服务器恢复后评估，有收益再单独加。

### 2.3 配置项（workflow schema，影响 JobDesk 契约）★

在 `search` 段新增，旧字段保留：

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `budget` | `null` → 1500 | 搜索阶段的优化次数上限；收尾每个 window+2 内的极小再加一次，Hessian 不计（2026-10-15 实现时明确） |
| `window_kcal` | 6.0 | 发布与父结构选择的能量窗口 |
| `arrangement` | `keep` | `keep` 保持输入排布（输入不可归类时自动搜索全部类别）；`enumerate` 枚举所有类别 |
| `release` | `true` | 是否做去约束收尾 |

`starts` 改为“首轮每个类别的 DG 起点数”（`null` → 60/类别数，至少 2；无金属 60）。
`small_ring_torsions`、`embed_timeout_seconds`、`max_cycles`、`bond_scale`、`fragment_charges` 含义不变。

### 2.4 输出 ★

- `structures.xyz`：发布的构象，能量升序；注释行格式不变（`target=... start=... energy_eh=...`）。
- `summary.json`：`structures` 只列发布的构象（每个唯一极小一条，`target` = 实际类别 id 或
  `unclassified`/`t00`，`start` = 极小 id）；`totals.generated` = 全部优化次数（含收尾），`relaxed` = 收敛数，
  `passed` = 发布数；新增 `search` 段（各移动的花费/产出、失败原因计数、转子数、Hessian 次数）。
  逐起点的失败记录不再列出（几千条），失败原因计数移到 `search.failures`；`confgen_search_run`
  的诊断改读这里。
- 下游 `confgen_search_run._read_validated` 的校验规则不变（passed 数、帧数、(target,start) 唯一、能量一致）。

### 2.5 不保留的旧行为

单轮“每类别 N 个起点 → 优化 → 审计 → 全部发布”的 `run_search` 删除（用户 2026-10-11 已同意只保留一个
引擎、不留兼容开关）。默认枚举所有排布改为默认保持排布（用户 2026-10-14 确认）。

## 3. PR 拆分

| PR | 内容 | 验收 |
| --- | --- | --- |
| A（本分支） | identity / rotors / explore；审计滞后；嵌入回退；Hessian 能力；worker 改调 `explore`；schema 新字段；输出与诊断适配；文档 | 见第 4 节 |
| B | JobDesk 适配新字段与输出（在 JobDesk worktree，ConfFlow 合并后） | JobDesk 契约测试 + 跨仓测试 |
| C（之后） | 双转子联动（视 r18 结果）、柔性体系自适应预算、动力学增量等 | 各自立项 |

## 4. 验证与验收（PR A）

1. 单元测试（Haiku 子代理并行编写）：identity（自同构/镜像/去重）、rotors（与原型转子集合一致）、
   explore（假引擎下的预算、份额、闸门、保持排布、release、取消）、审计滞后、嵌入回退、xTB Hessian。
2. 与原型对齐（本地，真实 xTB，每个 <10 分钟，串行）：axurer、cnetpa（配合物）、奥司他韦（有机）；
   指标：最低能量与原型差 ≤0.1 kcal/mol；<3 kcal 找回数不低于原型 80%；Boltzmann 覆盖不低于原型 5 个百分点。
   再加 TS1 re（反应对约束、刚性）一次。
3. 全量门禁：black、ruff、mypy、pytest 全过；改动行覆盖 ≥85%；LOC 预算按实测上调（小幅不另行确认）。
4. 契约：`confflow v4 contract --json` 的变化逐项核对，只包含 2.3 的新字段。

## 5. 用户决定（2026-10-15 已同意）

1. 2.3 的字段名与默认值（尤其 `budget` 默认 1500）。
2. 2.4 的输出调整（summary 不再逐起点列出失败记录）。
3. 是否同意先不加双转子联动，等 r18 评估。

## 6. 风险

- 预算默认值对柔性体系偏小（fodbop/miybaz 需 ~4000）：本计划只把 `budget` 暴露为配置，自适应停止另立项。
- 本地 16 核验证只覆盖小体系；大体系确认依赖服务器恢复。
- 改动输出格式会移动契约/边界 digest，JobDesk 必须同步（PR B）。
