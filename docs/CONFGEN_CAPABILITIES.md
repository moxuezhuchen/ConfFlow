# ConfGen 能力边界

面向用户：ConfFlow 的 ConfGen 现在能算什么、怎么配、输出什么、不支持什么。
事实基准为 2026-10-15 的 ConfGen 迭代搜索引擎状态（`schema_version: 4`）。
行号只作核对用，执行与复核一律按符号定位。

安装与运行见 [`USAGE.md`](USAGE.md)；命令全表见 [`COMMAND_REFERENCE.md`](COMMAND_REFERENCE.md)；
工作流语义见 `architecture/WORKFLOW_V4.md`；简化输入见 [`PRODUCER_INTENT.md`](PRODUCER_INTENT.md)。

## 1. 能算什么

计算卡片（`confflow/producer/cards.py` 的卡表为准，共 9 种）：
`opt`、`sp`、`freq`、`opt_freq`、`ts`、`ts_freq`、`confgen`、`refine`、`deduplicate`。
`filter` 不是卡片，是 `structure_transform` 的一种 `kind`
（`confflow/workflow/v4/schema.py` 的 `TRANSFORM_KINDS=(refine, deduplicate, filter)`）。

- ConfGen：构象生成，只有一个引擎，即迭代 DG 搜索（见第 2 节）。科学代码在
  `confflow/science/confgen/`，执行器为 `confflow/execution/confgen_executor.py`。
- `refine`：拓扑分组的 RMSD 精修（默认阈值 0.25 Å，见 [`USAGE.md`](USAGE.md) 精修节）。
- `deduplicate`：内容一致去重。
- `filter`（按能量/虚频）：`results` 必须显式绑定某一个计算步骤的 `results` 端口，
  只读该步骤的能量与频率。实现见 `confflow/execution/energy_filter.py`。
- `script` 步骤：工作流只能按 id 引用服务器上已登记的脚本，不写任意命令。
  登记与校验见 `confflow/execution/script_registry.py`。
- recipe（目录见 `confflow/producer/recipes.py` 的 `RECIPE_IDS_V4`，共 8 个）：
  - `confgen_search`：迭代 DG 搜索，种子与起始数需用户确认；
  - `monomer_conformers`：ORCA `XTB2` 预优化 → ConfGen DG 搜索 → 去重；
  - `ensemble_refine`：多帧 xyz → 去重 → 优化 → 精修 → 频率 → 按能量筛选；
  - 另有 5 个单算 recipe：`optimize`、`single_point`、`frequency`、`opt_freq`、`transition_state`。

## 2. 迭代 DG 搜索引擎

一个 ConfGen 步骤是一次迭代搜索（`confflow/science/confgen/explore.py`），依次进行：

1. 对输入结构做类型拓扑（共价键、可选的配位与反应对），并记下输入的配位排布（`a0`，`arrangement` 默认 `search`，即同时搜索其他给体排布）。
2. 全局出发：用 RDKit ETKDG 距离几何（`confflow/science/confgen/dg_seed.py`）生成出发构象。
   首轮只是小规模启动：出发总数为 min(`relaxation_batch`, `budget`/4) 减去输入结构已用的 1 次
   （生产中 `relaxation_batch` = max(4, 核数)），随机抽取不超过该数目的类各分一部分；`starts` 只是首轮每类的上限，
   `null` 为不设上限。其余 DG 出发由反馈轮次的全局 DG 份额和补满轮次提供。`arrangement: search`（默认）对所有类做 DG；全局出发中约一半不限定输入的给体排布
   （受限的出发只保留 metal–给体距离，其余约束不变），从而发现其他排布（配位异构体）。`arrangement: keep` 只对输入所属的类做 DG；输入不能归入任何理想多面体时
   保留全部类（此时发出 WARNING `confgen_keep_scope_widened`，见第 6 节）。`enumerate` 对所有类做 DG（按配体图自同构合并等价类）。之后的 DG 出发只补有产出的类（都没有产出时补全部类）。
3. 势阱探测：在至多 3 个“环状态×类别”格子的最低构象上，测出每个转子实际弛豫到的扭转偏移，
   作为转子移动，并保留与任何实测偏移都相差 30° 以上的网格移动（探测父结构上不存在的阱仍可到达）。
4. 反馈轮次，搜索阶段的预算按固定份额分配：全局 DG 30 %、单键转动 55 %、局部重嵌入 15 %。
   单键转动从最低的未展开极小出发；其他“环状态×类别”格子的最低点不交给单键转动（标记为已用），
   留给局部重嵌入处理（实测展开它们更差：奥司他韦 3 个种子 Boltzmann 74.5 % 对 77.8 %）；
   窗口内已无未用极小时，才取窗口外各格子的最低点。单键命中率（指数平均）低于 10 % 时，
   从单键份额中让出 10 % 给低频模式位移，且仅当引擎声明 Hessian 能力时进行。
5. 去约束收尾（release，`release: true` 时）：对最低能量以上 `window_kcal + 2` 内的极小，只保留
   反应对约束重新优化、审计、去重；发布 `window_kcal` 内的构象。
6. 审计：每个松弛结构按第 5 节的审计项判定。配位类不符但每个给体原子距金属不超过参考的 1.3 倍时
   记为 `unclassified` 接受；保持排布时，配体–金属–配体夹角（按图等价的给体置换）偏差 ≤45° 才算同一排布。
   通过的结构按其**实际**被感知的配位类发布（第 6 节）。
7. 去重：能量差 ≤0.5 kcal/mol 且对称 RMSD（重原子加杂原子上的 H，镜像合并）<0.2 Å 的为同一极小。

搜索由 `confflow/science/confgen/explore.py` 编排。每次能量计算与松弛都经后端无关的 `Calculator`
协议（`confflow/science/confgen/calculator.py`）完成，能量引擎可替换；默认实现为 xTB
（`confflow/execution/confgen_xtb.py`）。worker 是被监督的子进程 `confflow/execution/confgen_search_worker.py`，
执行侧为 `confflow/execution/confgen_search_run.py`。CPU 密集的 RDKit 任务经注入的并行映射执行。
对同一输入、同一 `seed` 与同一设置（并行映射顺序相同），结果确定；嵌入超时按墙钟计，恰在上限附近完成的出发或重嵌入可能因机器负载而不同。

约束：结果是约束下 xTB 的极小点，不是能量验证过的过渡态或最终构象能量；
ConfFlow 不给出 DFT 能量。

## 3. 配置

每个 `confgen` 步骤必须写 `schema_version: 4`。未知字段一律拒绝（严格模型），
`schema_version: 3` 文档被拒绝并给出明确信息：环、扭转、路径和配位实现引擎已删除，
步骤现在运行 DG 搜索引擎，请改用 `schema_version: 4`。

```yaml
- id: dg_search
  executor: confgen
  bindings: {structure: {source: {run: structures}}}
  resources: {cores_per_item: 2, memory_per_item: "4GB"}
  execution: {executable: "/opt/xtb-pr/current/bin/xtb"}
  confgen:
    schema_version: 4
    index_base: 1
    seed: 11
    coordination:
      metal_center: 1
      binding_sites: [{id: a, atoms: [2]}, {id: b, atoms: [3]},
                      {id: c, atoms: [4]}, {id: d, atoms: [5]}]
      shapes: [square_planar]
    search:
      budget: 1500
```

字段：

| 字段 | 必需 | 默认 | 含义 |
| --- | --- | --- | --- |
| `schema_version` | 是 | — | 必须为 `4` |
| `index_base` | 否 | `1` | 本步骤所有原子索引的基准（`0` 或 `1`），全文档唯一约定 |
| `seed` | 是 | — | 整数；随机性的唯一权威。步骤 seed 与此处不一致时失败 |
| `topology.bonds` | 否 | 感知 | 显式全部键（给出即覆盖感知，不可与 `add_bond`/`del_bond` 同用） |
| `topology.add_bond` | 否 | — | 增补键；可写 `kind`（`COVALENT`/`FORMING`/`COORDINATION`/`BREAKING`）、`bond_order`、`provenance` |
| `topology.del_bond` | 否 | — | 删除感知到的共价键（只能是共价对） |
| `topology.atoms` | 否 | — | 原子声明：`index`、`label`、`role`、`stereo` |
| `tolerances.bond_scale` | 否 | `1.15` | 图的键感知尺度（仅此一项可配） |
| `coordination` | 否 | — | 配位声明，见下 |
| `search` | 否 | 全默认 | 搜索设置，见下；整节缺席即全部默认 |

`coordination`（可选）：

- `metal_center`：金属原子索引；
- `binding_sites`：4 至 6 个位点，每个 `{id, kind: atom, atoms: [i], hapticity: 1}`，位点 id 唯一；
- `shapes`：恰好一个已注册形状（`confflow/science/confgen/graph.py` 的 `SUPPORTED_SHAPES`），
  配位数必须与形状相容（4–6，`CN_SHAPES`），或 `input`（见下）；
- `constraints`：可选的 `FORBIDDEN_TRANS` 策略声明（`classification` 固定为 `REJECTED_BY_POLICY`），
  是策略而非证明。

`shapes: [input]`（任意配位数 ≥2，仅与 `arrangement: keep` 或 `search` 搭配，不支持 `enumerate`）：不套用理想多面体，模板取输入自身的金属–给体核心
（给体相对金属的向量），即输入的配位排布本身。DG 边界放宽 0.25 Å 以容纳输入几何（去掉金属后 RDKit 的自由配体先验，
如 s-trans 共轭螯合环，可能排除真实结构）。新配位以输入为参照：非给体原子距金属小于共价半径和的 1.25 倍、且比参考近 0.3 Å 以上，
即判 `coordination_class` 失败。

`arrangement` 的含义：`search`（默认）搜索全部给体排布，每个极小按排布分组并标为 `aN`（`a0` 为输入排布，id 按最低极小排序，
`a1`、`a2`…）；同一排布指按图等价的给体置换后，所有给体–金属–给体夹角偏差 ≤45° 且（手性排布）手性相同。
`keep` 只保留输入排布（只有 `a0`）；`enumerate` 枚举全部配位类。`search` 与 `keep` 的给体集合与共价拓扑相同，不改变给体集合
（解离、半柔性配体、配位数变化不在范围内）；声明的立体状态（四面体手性、双键、`fixed_states`）在三种模式下都保持。
`release: true` 时，最终极小在去掉金属–给体约束后重新弛豫（第 5 节），保留的是排布与手性，而不是搜索阶段的坐标。

`search`（可选）与默认值：

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `starts` | `null` | 首轮启动阶段每个配位类（无金属时为整体）的 DG 出发上限；首轮总数另受 min(`relaxation_batch`, `budget`/4) 限制，`null` 为不设每类上限 |
| `budget` | `null`（自适应） | 不设时：至少 1500 次优化，之后只要低能区仍在增长（最近 max(500, 已花费 25 %) 次里 3 kcal/mol 内新极小多于每 250 次 1 个，或最低能量又降了 0.1 kcal/mol 以上）就继续，上限 8000。设为整数时：搜索阶段的固定上限。收尾（release）对最低能量以上 `window_kcal + 2` 内的每个极小再加一次，Hessian 不计 |
| `window_kcal` | `6.0` | 发布与父结构选择的能量窗口（kcal/mol，须 >0） |
| `arrangement` | `search` | `search`：搜索其他给体排布，按排布分组输出（见上）；`keep`：保持输入的配位排布（输入不能归入任何理想形状时搜索全部类别，并发出 WARNING `confgen_keep_scope_widened`）；`enumerate`：枚举所有配位类；`shapes: [input]` 可用 `search` 或 `keep`，二者都不扩大范围 |
| `release` | `true` | 是否做去约束收尾 |
| `small_ring_torsions` | `both` | 小环扭转开关：`both` 时每批 DG 出发约一半开、一半关；`on`/`off` 全局开或关 |
| `embed_timeout_seconds` | `120` | 逐起点的 DG 嵌入超时（秒）：每个出发单独一次 RDKit 调用；超时或无法嵌入的出发被跳过（计入 `search.failures.dg_no_start`），其余出发保留。每个出发单独作为一个并行任务（种子与整次请求相同，结果不变）。局部重嵌入另用 10 s 上限（与本值取较小者）：实测成功的重嵌入都在 4 s 内，失败的会一直跑到上限 |
| `max_cycles` | `1000` | xTB 松弛最大循环数 |
| `bond_scale` | `1.25` | 审计阶段重新感知键时的尺度（与 `tolerances.bond_scale` 独立） |
| `fragment_charges` | `[]` | DG 片段电荷覆盖：`{atom, charge}`，`atom` 使用本步骤的 `index_base` |
| `fixed_states` | `[]` | 需保持侧别的二面角，每项为 4 个不同原子（有顺序，使用本步骤的 `index_base`），判定规则见第 5 节 |

`starts` 为 `null`（或缺省）时按上表规则在搜索内解析。不设 `budget` 时预算自适应：低能区很快饱和的体系停得早（axurer 约 2100 次），
低能构象多的体系继续（cnetpa 约 4000 次；奥司他韦、四条正丁基链到 8000 上限），耗时可达固定 1500 次的 3 倍以上，覆盖显著提高
（cnetpa Boltzmann 覆盖 79 %→99 %，奥司他韦 80 %→96 %）。需要控制耗时时显式设定 `budget`。
8000 的上限对柔性分子也够用：2026-10 在 GEOM 的 7 个柔性分子（14–19 个可转动键）上把单次搜索拉长到 15000–20000 次，最低构象都在 8000 次以内已找到，1 kcal/mol 内的构象只多出 0–7 %（r16_02 例外），耗时约翻倍；同样的成本改用两个种子各跑 8000 次再合并，1 kcal/mol 内的构象更多（r16_02：两种子合并 43 个，单种子 20000 次 35 个；r14_08：9 个对 7 个，能量相差 <0.01 kcal/mol 视为同一构象）。需要更全的低能构象时，用不同 `seed` 分别运行再合并结果，不要只加大 `budget`。
2026-10 原型验证（TMCONF 配合物、奥司他韦、TS1）的摘要与指标见
[`docs/plans/confgen-explore-engine.md`](plans/confgen-explore-engine.md)。

过渡态只需声明反应对，不必列出全部共价键，例如 `topology: {add_bond: [{atoms: [2, 4], kind: FORMING}]}`。
搜索模式下未声明 `topology.bonds` 时，图的键感知使用 `tolerances.bond_scale`（默认 1.15），
审计用 `search.bond_scale`（默认 1.25）重新感知。

无金属时省略 `coordination` 整节即可；`coordination_class`、`metal_donor_distance`、`donor_orientation`
三项审计记为 `skipped`，不判失败。

## 4. xTB 配置

搜索需要 xTB 可执行文件，按 `xtb` 程序名解析：步骤的 `execution.executable` 优先，
否则取运行请求中 `executables["xtb"]` 的默认值。解析不到即在开工前 fail-closed。
worker 记录 xTB 的 `xtb version … (commit)` 行到 `summary.json`。已完成的松弛只有在坐标、约束、
电荷、自旋、循环上限和 xTB 可执行文件（按内容哈希）都相同时才复用。

推荐的 xTB：上游 main 68fe777 加 grimme-lab/xtb #1465、#1466、#1464 的静态构建，版本行为
`xtb version 6.7.1 (68fe777+pr1464.1465.1466)`。本机路径 `/opt/xtb-pr/current/bin/xtb`，
814 服务器 `/home/xianj/xtb-pr/current/bin/xtb`（目录内有 README 与可重建的源码包）。
- 官方 6.7.1（`otool_xtb`、`/opt/xtb671`）在 TS1 六配位 Al 上的 GFN2 解析梯度与有限差分相差
  最多 3.3e-2 Eh/bohr，上游 #1214 已修复，但尚无正式版本包含该修复。
- 2026-10-18 验证（TS1 106 原子、Zr7 144 原子各 32 个真实输入，带约束）：单点与上游 main 相差
  ≤3e-12 Eh，梯度与有限差分一致到 1e-6，优化全部收敛；16 个单线程并发时每步耗时相对官方 6.7.1
  本机 1.66×/1.89×，814 服务器 2.15×/2.28×（TS1/Zr7）。

## 5. 审计

每个松弛结构都与输入参考逐项比较，任一项不过即该结构不通过。收尾（release）阶段只约束反应对，
第 9 项不检查。

1. `converged`：xTB 收敛标记存在且 `xtbopt.xyz` 存在；
2. `topology`：同规则感知的共价键集与参考一致（反应对不计入）。键的增减按成键滞后判定：参考中的键
   长超过 1.45 倍共价半径和才算断开，新键须短于 1.10 倍才算形成；
3. `coordination_class`：配位类无歧义地归一到本次计划的某个目标类（即策略过滤后的目标集合，
   因此禁止的反式配对所对应的类不在其中）。感知有歧义时判失败；落在计划集合之外时，若每个给体原子
   与金属距离都不超过参考的 1.3 倍，则记为 `unclassified` 接受，否则判失败（无金属时 `skipped`）；
4. `stereo`：比较四面体手性的符号体积，分两部分：
   - 构型保持：过渡态成键图（稳定键加声明的反应对）上每个恰有 4 个邻居的非金属原子，都保持输入中按原子编号的邻居手性。
     不依赖 RDKit 立体中心识别，因此反应中新出现的中心（如亲核进攻的面）也覆盖；构象变化不改变它，不损失构象。
   - 原有立体中心：3 邻居可反转原子（膦、亚砜）仍只比 RDKit 标出的真四面体手性中心（`DGSeedResult.stereo_centers`）。
   - 跳过：带 ≥2 个同元素末端原子的原子（CH2、CH3、CF2 等，互换即复原）；金属中心（由 `arrangement` 管）；
     参考中四邻居近乎共面（单位体积 <0.1）的原子，仅在 stats 的 `flat_reference` 中报告。
   CH2/CH3 的等价 H 互换不判失败；
5. `reaction_distance`：反应对距离在参考的 ±0.02 Å 内；
6. `metal_donor_distance`：金属–给体距离在参考的 ±0.03 Å 内（无金属时 `skipped`）；
7. `donor_orientation`：每个给体的金属–给体–取代基夹角与参考至多差 30.0°（无金属时 `skipped`）；
8. `contacts`：相隔 3 根键以上、且不含金属的原子对，距离不短于共价半径和的 0.70 倍。
   仓内没有独立 vdW 半径表，沿用 `confflow/science/bonding.py` 的共价半径表；
9. `restraint`：搜索阶段，金属–给体与反应对的距离偏离约束目标超过 0.03 Å 即拒绝；
10. `arrangement`：保持排布时，配体–金属–配体夹角（按图等价的给体置换取最优）相对输入的偏差超过 45° 即拒绝；
11. `fixed_state`：`search.fixed_states` 声明的二面角须保持参考侧别（收尾阶段同样检查）。

声明的二面角按参考二面角 θ0 分类：|sin θ0| ≥ 0.5 为 `hand`（螺旋、阻转异构、gauche±），保持 sin θ 的符号，镜像即判失败；
否则为 `side`（酰胺、酯的 s-cis/s-trans），保持 cos θ 的符号，镜像不改变它。恰在边界（sin 或 cos 为零）的候选判失败，
参考中的退化几何（原子重合或共线）直接报错。生成端在 xTB 之前过滤（计入 `generated_fixed_state`）。ETKDG 不能直接生成声明的状态，
每声明一个二面角，可用的 DG 出发大致减半；未声明的二面角仍自由探索。

## 6. 输出与退出语义

- `structures.xyz`：发布的构象，每个唯一极小一条，能量升序，只含 `window_kcal` 内的构象；注释行格式不变，
  为 `target=<来源> start=<n> energy_eh=<e> rel_kcal=<r>`，实际类可判定时（有金属的搜索）末尾追加
  ` actual=<实际目标>`，无金属时不追加。
- `summary.json`：
  - `settings`：设置（含解析后的 `starts`、`arrangement`、xTB 身份）；
  - `structures`：只列发布的极小，每个唯一极小一条。`target` 为实际类 id（无金属为 `t00`，
    无法归入任何理想多面体的为 `unclassified`），`start` 为极小 id，`passed` 为 `true`；
  - `totals`：`generated` 为全部优化次数（含收尾），`relaxed` 为收敛数，`passed` 为发布数；
  - `search`：`relaxations`（搜索阶段的优化次数）、`stop`（搜索结束原因：`budget` 用完预算、`saturated`
    自适应判定低能区不再增长、`no_moves` 没有可用的父构象或移动、`no_minima` 没有任何构象被接受）、
    `release`（去约束收尾状态：`done`；`off` 未开启；`not_needed` 没有可去掉的约束；`failed` 收尾全部失败，
    此时发布的是仍带全部搜索约束的构象，worker 在 stderr 给出 warning）、`release_relaxations`（收尾次数）、`hessians`、
    `rotors`（转子数）、`targets_searched`、`unique_minima`、`spent`（各移动的花费）、
    `found_in_window`（各移动在窗口内的产出）、`failures`（失败原因计数）、`template`（`input`、理想形状名，或无金属时为 None）、
    `arrangement_scope`（搜索范围：`input_arrangement` 只搜索输入所属的类；`all_classes_input_unclassified` keep 下输入不能归类，
    扩大到全部类；`all_classes` 搜索全部类（`enumerate`）；`all_arrangements` 为 `search`；无金属时为 None）。
    `arrangements`（仅 `search` 与有金属时）：每个排布一行 `{id, input, lowest_eh, rel_kcal, minima, published}`，
    `id` 为 `aN`，`a0` 恒为输入排布（`input: true`），`rel_kcal` 为该排布最低极小相对全局最低的能量差，`minima` 为该排布的极小数，
    `published` 为发布的数量。发布规则：排布的最低极小在全局最低 `window_kcal` 之内才发布该排布；排布内只发布距其自身最低
    `window_kcal` 之内的极小；输入排布 `a0` 始终发布。
  - `tetrahedral_stereo`（构型保持统计）：`states`（受保护的中心数）、`embedding_enforced`（DG 嵌入时加了输入手性标签的数）、
    `filter_only`（仅生成后过滤的数）、`flat_reference`（参考中近共面、未受保护的原子，0 基内部索引）。
    生成后被过滤的计数为 `generated_tetrahedral_stereo`，reembed 失败原因为 `tetrahedral_stereo`。
  - `fixed_states`（声明的二面角）：`states`、`generated_rejected`、`audit_rejected`、`lowest_audit_rejected_kcal`（被拒结构中最低者相对最低构象的 kcal/mol）；
    `state_changes`（提示层）：扫描全部最终极小（含窗口外）中的离散两态二面角，远离边界 20° 以上，按相同结构集合合并，按图自同构换标。
  逐起点的失败记录不再列出（几千条），失败原因统一计入 `search.failures`。
- worker 的 `stdout.log`、`stderr.log`（均为制品）。
- 成员的 `role` 为 `conformer`，`ordinal` 为能量名次（0-based），id 按 `conformer_output_id`；
  metadata 含 `seed`、`starts`、`energy_eh`、`rel_kcal`、`search_target`、`search_start`、
  `search_actual_target`、`search_release`。`search_target` 是发布的类目标，`search_start` 是极小 id，
  `search_actual_target` 是实际类，`search_release` 与 summary 的 `search.release` 相同（`failed` 表示
  构象仍带全部搜索约束），`search_arrangement` 是排布标签（`a0` 为输入排布，`keep` 下恒为 `a0`）。
  xyz 注释行在 `actual=` 之后追加 ` arrangement=<aN>`（`arrangement` 为空时不追加）。
  搜索成员是普通结构，下游按导入结构消费，不带额外状态。

退出：至少一个构象通过审计并发布为 `COMPLETED`，附 INFO `confgen_completed`（生成、松弛、发布的数量，
失败审计项直方图、`release` 与 `stop`）；去约束收尾全部失败时仍为 `COMPLETED`，另附 WARNING
`confgen_release_failed`；keep 因输入不能归类而扩大到全部类时另附 WARNING `confgen_keep_scope_widened`
（`details` 含 `template`、`arrangement_scope` 与 `targets_searched`，建议改用 `shapes: [input]`）；没有任何构象通过审计为 `FAILED`，错误码 `confgen_no_passing_structures`，
错误信息为 `confgen search ran N relaxations but none passed all audit checks`，并附失败审计项直方图。
此时 worker 以退出码 2 结束、不写 `structures.xyz`。
worker 异常（包括 job 非法或 xTB 缺失）为 `FAILED`，附 stderr 尾部。

WARNING `confgen_arrangement_changed`：`search` 下（且 summary 含 `arrangements` 表时）若最低的发布构象不属于 `a0`，发出此 WARNING，
给出该排布相对输入排布最低极小的能量差（`a0` 行的 `rel_kcal`；表中无 `a0` 时注明未找到）。不同排布是不同的配位异构体，应按 `search_arrangement` 分别比较。

WARNING `confgen_conformational_states`：`state_changes` 中的单元存在时发出，列出各二面角（已按 `index_base` 换算）及可粘贴的
`search.fixed_states: [...]`。这只是提示：几何无法判断势垒，快速转子（如甲氧基 gauche±）也可能被报告，且只有另一态也被采样到才会出现；
金属中心的 Δ/Λ 由 `arrangement` 处理（`keep` 与 `search` 都保持手性），不在此扫描范围。

续跑：出发目录内已有相同内容的 `done.json` 时自动跳过该出发。

## 7. 已删除的功能

ConfGen 的以下引擎与字段已删除，`schema_version: 4` 文档中出现即被拒绝：

- 环引擎（`rings`，含环形式枚举、CP 坐标、刚性单元）；
- 扭转引擎（`torsions`，含网格与相对/绝对扭转）；
- 扭转路径（`paths`，含 `strict_path_bond_check`）；
- 配位实现（`coordination` 中的 `backend`、`budgets`、`treatment`、`donor_configuration`、`site_group`，
  以及刚性/柔性实现与 H_geom 对称判定）；
- 分阶段的 `ConfgenEngine`、采样上限（`sampling`）、`limits`、`exclusions`、`stereochemistry`、`overrides`；
- 带标签的 ConfGen 状态键，以及链式 ConfGen 步骤间的状态继承。
  消费上游 ConfGen 步骤成员的步骤，把它们当作普通结构；
- `tolerances` 中除 `bond_scale` 外的全部键。

其他功能（远程执行、QST2/QST3、NEB、GOAT、IRC、`tspes`、分析包、多输出机制等）的删除记录见
`docs/diet-2/PLAN.md`。

## 8. 限制与不支持

- 金属：每个步骤至多一个 `metal_center`，配位数 4–6，每个位点恰好一个原子、`hapticity: 1`，
  每个步骤恰好一个形状。第二个金属或多 hapto 配位无法表示，超出即 fail-closed。
- 构象空间：没有环或扭转的专门采样；覆盖依赖迭代搜索的预算与 DG 出发数。显式设定的 `budget` 过小时，
  柔性体系（如 3×PEt3 或四条正丁基链）可能漏掉低能构象；不设 `budget` 时按低能区是否饱和自适应延长（上限 8000）。
- 参考结构：输入结构是几何参考（约束、排布与审计的基准）；搜索开始前它先按生产流程审计并在搜索约束下松弛，
  通过后以 `origin=input` 进入极小池（占 1 次优化），与其他极小一样可被选为父结构并发布。
- 能量：只有 xTB（GFN2）松弛后的能量，无 DFT 能量，无加权玻尔兹曼统计。
- Freeze：ConfGen 不支持 `freeze`，非空即拒绝。
- `fragment_charges` 是启发式覆盖，需要用户确认。
- 过渡态：反应对只做距离约束，不验证过渡态。
- 立体：邻居多于 4 个的原子（如 SN2 中心碳）、3 邻居可反转原子（膦、亚砜）和构象性手性（配体螺旋、阻转轴、环构象）不在构型保持规则内。
  该规则没有用户选项，默认总是开启；需要另一面或另一构型时，提供该构型的输入结构单独运行。
- 对称：没有对称抑制；等价构象可能重复出现，去重由后续 `deduplicate` 步骤负责。
