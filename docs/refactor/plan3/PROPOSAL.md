# PLAN-3 提案 — ConfGen 组件化与最终 ConfGen 方案（realizer / validator / ledger）

状态：**提案，不执行，不并入 PLAN-2。** 依据：`research/realization-handoff` 分支的 `research/confgen_realization/`（`METHOD.md`、`realization/*`、`results/*`，7 个提交，2026-10-02）与当前 `main`（`afdf9df`）的 `confflow/science/confgen/`。

## 1. 研究分支的结论（要点，出处 METHOD.md）
- 问题：给定离散配位目标（位点→多面体顶点 + 立体描述）与一个大致在正确盆地的种子，产出**既是声明的目标、又相对可信参考几何化学有效**的 3D 几何。
- 三类输入必须分开：**参考**（母体 TS/输入结构，只提供内坐标值）、**种子**（Molassembler、σ 像、模板转移，只提供起点/盆地）、**目标**（枚举结果，提供放置与立体描述）。生产代码 `realize_flexible` 现在用自己的输入做审计，拿 DG 种子当输入时会把键长偏离参考 0.29 Å、非键对只有 0.49×Σr_vdW 的几何报成 REALIZED——这是第一个要修的缺陷。
- 求解：一个问题类型、无阶段、无族权重；行均衡化（`pipe2.py`），松弛再收紧的形状阶梯，增广拉格朗日闭合。
- 证据：TS1 严格门 12/12、TS2 12/12；τ 尺度不变性（均衡化后 30/30，几何逐位相同）；0.3 Å 噪声种子 18/18、0.5 Å 15/18；没有盆地种子则 2/12（所以种子器必须有）；TS2 own-arm-trans 的 X00 到 X07 在 4 种 N 构型下 0/96，**未解决、不是证明**。
- 必须由离散步骤处理而不是连续求解器：prochiral 标签奇偶（用自同构见证交换，不用 WL）、供体构型由目标声明而不是从种子感知、σ 展开只产出种子并重新求解、非立体感知种子器需先做立体修复。
- 已知缺口：FORMING/BREAKING 还不是独立的约束族；元素表只覆盖 H/C/N/O/Al；单金属中心、无 η/多原子位点；非键对是 O(N²)；L10/L11 型强应变目标慢（160 到 460 s）；Molassembler 尚未在真实环境里对 TS2 全部 20 类运行。

## 2. 目标架构：五个有明确接口的组件
```
enumeration ──► seed pool ──► ConstraintProblem ──► Realizer ──► Validator ──► Ledger
(状态模型、     (Molassembler   (约束编译器：        (求解后端：   (独立严格门：    (REALIZED /
 精确群商)       主；其他需     reference+target      行均衡化、   与求解无关，     UNRESOLVED /
                 立体修复)      → 行)                 松弛再收紧)  最终权威)        PROVEN_INFEASIBLE)
                                                       σ 展开 = 产出种子并重新求解
```
| 组件 | 职责（只做这件事） | 现状（main） | 研究分支提供 |
| --- | --- | --- | --- |
| Enumerator | 离散目标空间、对称商、可证明不可行 | `coordination/enumeration.py`、`symmetry.py`（已有，含 `PROVEN_INFEASIBLE` 的证明约束） | — |
| SeedProvider | 给出盆地种子，种子不当参考 | 内部 `rigid`/`flexible` 后端自己造起点 | 种子/参考/目标分离；Molassembler 为主种子器；非立体感知种子的立体修复 |
| Realizer | `ConstrainedFeasibilityBackend`：约束问题 → 候选几何，**必须**行均衡化使后端尺度无关 | `coordination/realization.py` 的 `rigid`/`flexible`（`BACKEND_CHOICES`） | `cons.py`、`solvers.py`、`pipe2.py` |
| Validator | 独立于求解器的严格门（供体误差、碰撞、反应边、氢键下限严格在门内） | 混在 `realization.py` 里，用输入当参考 | `System` 的 gate；氢键下限 1.61 Å / 门 1.55 Å |
| Ledger | 每个目标一个终态；**求解失败从不是证明** | `accounting.py`（REALIZED/UNRESOLVED…已有，终态权重） | 终态约定与 `step3` 记录格式 |

`accounting.py` 与 `coordination/realization.py` 已经使用 REALIZED/UNRESOLVED/PROVEN_INFEASIBLE 的词汇，所以 PLAN-3 是**把它们拆成可替换的组件并换上新的 Realizer/Validator**，不是推倒重来。

## 3. 建议的阶段（每阶段独立可验收，均带 golden 与 TS1/TS2 基准）
| 阶段 | 内容 | 门槛（验收证据） |
| --- | --- | --- |
| P3.0 | 在 `science/confgen/coordination/` 里定义接口（`ConstraintProblem`、`Realizer`、`Validator`、`LedgerEntry`），把现有 `rigid`/`flexible` 包成第一批实现，行为不变 | 引擎报告与 TS1 三后端输出逐字节不变 |
| P3.1 | 抽出独立 Validator（与求解器解耦），并给出"参考≠种子"的审计 | 现有基准全绿；新增用例：拿 DG 种子当输入不再误报 REALIZED |
| P3.2 | 移植研究分支的约束编译器与求解器作为**新增后端**（默认关闭），含行均衡化 | TS1/TS2 严格 12/12、τ 不变性 30/30、噪声 18/18 与 15/18 复现 |
| P3.3 | Ledger 定稿：REALIZED/UNRESOLVED/PROVEN_INFEASIBLE，加上"全部 UNRESOLVED 时整体 outcome 的语义" | 与 PLAN-2 的 A1 一致（见 §4） |
| P3.4 | 把新后端设为默认（行为变化，需用户批准）或保持可选 | 用户决定 |
| P3.5 | 元素表、FORMING/BREAKING 约束族、多金属/η 位点等扩展 | 各自单独立项 |

## 4. 与 PLAN-2 各项的依赖
| PLAN-2 项 | 与 PLAN-3 的关系 | 先后 |
| --- | --- | --- |
| **A1**（0 个结构不得 completed） | Ledger 的"整体结果"语义必须与之一致：全部目标 UNRESOLVED 时 ConfGen 步骤失败，并带终态统计。P3.3 沿用 A1-1 的诊断码，不另起一套 | A1 先做 |
| **A2**（优化后连接变化检查） | Validator 的"连接性/反应边"判断与 A2-a 的 `connectivity_preserved` 使用同一份"预期图 + 类型边"；若 A2-a 做，P3.1 复用 | A2 在 P3.1 之前 |
| **C1**（统一 `bond_scale`） | 研究分支的类型化拓扑用 **1.2× 共价半径**感知键，ConfGen 默认 1.15；TS1 上两者差一条 `O74–C79`（见 A2 报告）。Realizer 的参考图取哪一边，是 PLAN-3 的前置决定 | **C1 在 P3.2 之前** |
| **N+1**（`core/bonding`、`data`、`constants` 移入 `science/`） | 研究分支自带元素表只覆盖 H/C/N/O/Al，移植时应改用 `science` 的统一数据表，避免两份 | N+1 先做更省事 |
| **B1 / N+6**（`preview_paths` 改接 v3，统一 `topology_digest`） | 与 PLAN-3 无直接依赖；但若将来预览要给出"可实现数"，需要 Ledger 的计数接口 | 独立 |
| **N**（优化后键连接变化） | 同 A2 | 同 A2 |
| **N+3**（v3 `waypoint`） | 无关 | — |
| **D6**（`analysis/pes.py` 定位） | 无关 | — |

## 5. 风险与待你决定
1. **Molassembler 依赖**：研究环境没有运行它（TS1 用保存的 SCINE 帧，TS2 用模板转移种子），P3.2 的"主种子器"是否作为可选依赖引入，需要你决定；没有它时 P3.2 只能用转移种子验证。
2. **未解决的类**：TS2 own-arm-trans（X00 到 X07）0/96，**不能写成"不可行"**，只能是 UNRESOLVED；产品语义要写清。
3. **默认后端切换**（P3.4）会改变科学输出，必须单独批准并给出对照基准。
4. **研究分支本身**：`research/realization-handoff` 的 7 个提交只在本地（已有 bundle 备份 `/root/refactor-backups/confflow-research-realization-handoff.bundle`）；PLAN-3 开始前需要决定是否把它推到远端或并入一个 `research/` 目录。
5. 规模：研究代码约 10 个模块、数十万行结果数据（`results/`）；移植只取 `cons.py`、`solvers.py`、`system.py`、`pipe2.py` 与基准夹具（`ts2/`），结果数据不进主线。

## 6. 不在本提案内
具体补丁、接口签名的最终形态、基准阈值的最终取值——这些在 PLAN-3 立项后按卡片逐个给出。
