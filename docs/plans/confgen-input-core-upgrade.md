# ConfGen 输入核心 keep 升级计划（CN4–9 通用单核配合物）

状态：**已确认**（2026-10-18，用户："都按照你的建议。执行"：接口用 `shapes: [input]` 增量扩展、不升 schema 版本；本次只做 keep，enumerate 另立项）。按本计划开发。G1 已通过（2026-10-18，见第 1 节）。

## 0. 目标与边界

把 2026-10-17 原型验证过的"输入核心 DG"做成 keep 模式的起点来源，使 keep 不再依赖理想多面体模板：

- 配位数由输入决定，不按元素、配位数或体系写分支。去掉 4–6 个位点、"必须恰好一个注册形状"、720 次排列等上限对 keep 的限制。
- 只改 keep 的全局 DG 起点来源。迭代引擎的轮次、份额、探针、单转子、局部重嵌入、去重、预算和 release 全部沿用现有生产代码。原型已经确认，生产份额正是要用的分配。
- 默认行为不变：现有 v4 文档（`shapes: ["octahedral"]` 等）照旧走理想模板，golden 不变。
- 不在本计划内：enumerate（改变配位排布）用输入核心；η 配位；多金属；配体解离/再配位；GFN2 对开壳层的适用性。

## 1. 证据与合并门槛

原型与报告：
- 路线快验：`/tmp/confgen-route-fast-20261017-v4/REPORT.md`
- CN4–9 留出：`/tmp/confgen-holdout-cn49-20261017/REPORT.md` 与 `PROTOCOL.md`（含修正案 1–5）
- 服务器数据：`/home/xianj/confgen-proto-20261012/v4b-20261017/`

| 结论 | 证据 |
|---|---|
| 输入核心起点（C）远优于扰动输入配体（B）；B+C 混合、单纯加 DG 起点都无增量 | Zr7 等预算 12/300 次，多种子 |
| 必须用生产份额（DG 约 30%） | 原型旧分配 DG 仅约 5%，Zr7 最好能量差 1.2–4.8 kcal |
| CN4–9 能找到最低能量 | 修复后 11 个体系 × 3 种子，33/33 都在并集最低 0.37 kcal 内 |
| DG 距离范围必须能容纳输入（dgfix） | 修复前 3/6 个 COD/Acta 输入全部无法生成起点（三角平滑失败） |
| 生产理想模板无法识别 3/7 个真实 CN4–6 输入的排布 | axurer、efoyeg、Cd(bpy)3 不匹配任何注册形状 |
| 参考构象覆盖随预算增加；柔性 CN5 在 1500 次、1 个种子时仍只约 50% | TMCONF40 amozeh、egozuv |

合并主分支前的门槛：
- **G1：CN4–6 同预算对照不退化 —— 已通过。** 在 akugod、amozeh、egozuv、cnetpa 上，3 个种子、300 次优化：两边都是 3/3 个种子到最低 0.5 kcal 内；3 kcal 内参考构象 C 找回 4/8/5/7 个，A 找回 4/6/4/4 个；CPU 多 3–9%。柔性体系的覆盖不足是原有问题，A 同样偏低。
- **G2：input 路径的给体审计。** 生产 `audit_structure` 有金属时必须提供形状，并用 `perception.perceive_donors(shape)` 判定给体，没有原型审计的"边界区无法判定"状态。所以原型里 κ2-硝酸根（N 离 Cd 2.84 Å）的问题来自原型审计，不是生产审计。但 input 路径需要新的给体判定：声明的给体相对 reference 的距离仍在容差内，且不新增配位原子。"新增"按相对 reference 的距离判定（例如比 reference 近 0.3 Å 以上），不用绝对截断，这样输入里本来就近的硝酸根 N 不会误判。WP5 实现，cd7_ntb 作为测试用例。
- **G3：用实现后的生产代码做小规模复现。** 4 个留出体系 × 1 种子 × 300 次，与原型同种子结果一致（相同种子下能量分布和最好能量在噪声内），证明落地没走样。
  **已完成（2026-10-18，PR #167 合并前）：** 在 814 服务器上，用户路径 `shapes: [input]`、keep、release 关闭、300 次、1 个种子、XtbCalculator。结果与"原型最好结构在同一计算器和生产约束下重新优化"相比：ti7_salan −0.02、zr8_onn +0.23、la9_holdout −0.63、amozeh +0.67 kcal/mol。amozeh 是 1 个种子对比原型 3 个种子的最好值；原型各种子的最好值本身就相差 0.10–0.37 kcal，所以这里属于种子波动。各体系都只搜索了 t00，并在预算耗尽时停止。

## 2. 接口设计

不升 schema 版本，做纯增量扩展（现有文档语义不变，无需迁移）：

```yaml
coordination:
  metal_center: 1
  binding_sites: [...]      # keep + input：2 个以上位点即可（原 4–6）
  shapes: [input]           # 新值：以输入自身的配位核心为模板
search:
  arrangement: keep         # input 只允许 keep；enumerate + input 报错（第 0 节边界）
```

- `shapes: [input]` 时：单一目标，placement 为恒等；DG 模板顶点 = 输入中 金属→给体 的向量。排布保持用现有的 `_same_arrangement`（给体–金属–给体夹角，按图自同构置换），不调用理想形状分类。
- 现有理想形状路径完全不变，包括 720 上限和 4–6 位点上限，这些只对理想形状适用。
- JobDesk：新增 `input` 选项。建议在输入不匹配任何理想形状时默认推荐它；是否设为默认，单独决定。

## 3. 工作包（可并行，文件不重叠）

| WP | 文件 | 内容 | 测试 |
|---|---|---|---|
| WP1 | `confflow/science/confgen/dg_seed.py` | `shape == "input"` 时模板顶点取自 reference（与原型 v9 等价，但只在 input 生效，理想形状路径逐字节不变）；dgfix：金属的非键先验置为无信息，再把每对原子的距离范围放宽到能容纳输入 ±0.25 Å（只对 input 生效） | DG 单测：Cd(bpy)3、Ti salan、Zr[ONN]2Cl2 的固定输入 8/8 生成并通过审计；理想形状测试结果不变 |
| WP2 | `confflow/science/confgen/search.py` | `plan_targets` 对 input 返回单目标；`audit_structure` 对 input 跳过 `coordination_class`（排布由 explore 判定），其余检查不变 | search 单测 |
| WP3 | `confflow/science/confgen/explore.py` | `_keep_arrangement` 对 input 直接用输入夹角作为参考；`_reduce_targets` 对单目标不作处理 | explore 单测：CN7 小预算冒烟（mock calculator） |
| WP4 | `confflow/science/confgen/search_spec.py`、`confflow/workflow/v4/confgen_schema.py`、`confflow/science/confgen/graph.py`（`CN_RANGE`、`SUPPORTED_SHAPES`） | 接受 `shapes: [input]`；input 时放开位点数上限（≥2），包括 `CoordinationSpec` 的 CN 4–6 检查（理想形状仍按原范围）；`input` + `enumerate` 报错并给出清晰信息 | spec/schema 单测；v4 golden 不变 |
| WP5 | `confflow/science/confgen/search.py`（审计部分，与 WP2 同文件，**在 WP2 之后串行做**） | input 路径的给体审计（G2）：声明给体相对 reference 的距离在容差内；新增配位按相对 reference 的距离判定；`donor_orientation`、拓扑、碰撞检查不变 | 审计单测：cd7_ntb 输入通过；人为拉近一个非给体原子会被拒；去掉一个给体会被拒 |
| WP6 | `tests/` 新 fixture | 从留出集挑 CN7 Ti salan 与 CN8 Zr[ONN]2Cl2 的输入（小而有代表性），做端到端冒烟（真 xtb，小预算，标记为慢测试） | CI 慢测试 |

执行方式：按用户习惯，先确认本计划，再派 Haiku 子代理执行。
- WP1、WP3、WP4、WP6 可以并行；WP2 → WP5 串行。
- 每个 WP 一个 `refactor/*` 分支与 PR。
- 每个子代理只改自己的文件，跑本仓库测试与 black/ruff；LOC 预算小幅增加不用逐次确认。

## 4. 落地后的验证（G3）与发布
1. 用 WP1–5 合入后的代码，在服务器跑 G3 复现（约 1 小时，48 核）。
2. 原 golden 全部不变（默认路径未动），新增 input 路径的 golden（Ti salan、Zr[ONN]2Cl2）。
3. JobDesk 适配单独一个 PR，在 ConfFlow 合入之后做，并更新 ConfFlow 对 JobDesk 的 pin。
4. 合并到主分支逐个征得用户同意；默认切换（例如让 keep 默认用 input）另行决定。

## 5. 已知风险与后续
- **参考构象覆盖：** 柔性体系需要生产规模预算（1500 次起，自适应到 8000），这与输入核心无关。如果 G1 显示 A 的覆盖明显更好，要先查原因再合并。
- **la9：** 约 33% 的优化在 200 步内不收敛，浪费计算但不影响正确性。后续可考虑让 xtb 优化的 cycles 随原子数调整。
- **原型的输入核心 `dg_seed` 是 GPT 改过的 v9 副本**，对所有形状都用输入核心。落地时只对 `input` 生效，不能整体照搬。
- **enumerate 的输入核心与新排布**：径向约束偏置、原始起点质量差的问题留待后续立项。

## 6. 后续：配位构型身份保持（2026-10-18，用户确认"就这样做，直到完成"）

PR #167 合并后，对照外部评审意见核查了代码和数据，结论如下。

### 6.1 发现

- **局部重嵌入约 40% 输出父结构的镜像。** 在 G3 的生产运行中，4 个体系共 23/58 次。原型留出数据、理想模板路径（A 组）情况相同。原因：`_reembed_task` 只用距离区间固定非自由原子，距离无法区分手性。DG 起点（coordMap 固定核心）、探针和单转子都不产生镜像。
- **后果一，预算浪费：** 无手性配体时，镜像是同一构象的对映体，会被去重合并。
- **后果二，正确性风险：** 配体带有机手性中心时，ETKDG 按手性标签保持有机构型，非自由的金属核心却可能被镜像，得到只有核心翻转的非对映体。keep 的排布检查只比较给体–金属–给体夹角，对镜像不变，查不出来。本次数据中刚性体系的翻转全是整分子镜像，但测试集里有机手性体系很少，这种风险不能排除。
- **keep + 理想形状 + 输入不匹配任何类别时，静默搜索全部类别**（`explore.py` `_keep_arrangement`）：不报警，结果里也不记录。7 个真实 CN4–6 输入中有 3 个会触发。
- **约束强度（评审担心的问题）：** 生产搜索阶段只约束 M–D 径向距离，默认 `release=True` 在最终发布前去掉这些约束重新优化，发布前还会审计排布。所以 keep 的语义已经是"保持拓扑与排布、几何弛豫"，并不固定坐标。缺的只是手性检查。

### 6.2 修复
- **A，重嵌入固定手性：** `_reembed_task` 对非自由原子增加 `SetCoordMap`（与 DG 起点做法一致）。实验（ti7_salan、cd6_bpy3、zr8_onn，各 48/48/40 次）：镜像从 29/21/14 次降到 0 次；成功率不变；自由区仍有 2.4 Å（中位数）的位移；耗时略降。这会改变所有路径的搜索结果，golden 需要更新（PR 中列出）。
- **B，排布检查加手性：** `_same_arrangement` 在夹角通过后，再比较给体单位向量在允许的置换下"真旋转拟合"和"镜像拟合"的 RMSD。镜像明显更好就拒绝。参考排布自身镜像可叠合（无手性，如平面或高对称排布）时跳过此检查。
- **C，搜索范围透明：** stats 记录 `template`（`input`、理想形状名，或无金属时为 None）和 `arrangement_scope`（`input_arrangement`、`all_classes_input_unclassified`、`all_classes`，或 None）。keep 实际扩大到全部类别时，confgen 步骤发出 warning 诊断 `confgen_keep_scope_widened`，并建议改用 `shapes: [input]`。行为不变，只是不再静默。
- **D，测试：** 重嵌入在多个种子下保持核心手性；Cd(bpy)3 整体镜像被 keep 拒绝，原结构被接受；无手性排布跳过手性检查；release 开启时 keep 发布的结构保持排布与手性；扩大范围的诊断。
- **暂缓：** 让 input 成为默认、自动选择模式、enumerate 使用输入核心。enumerate 依赖本节的构型身份（排布类别 + 金属手性）。
