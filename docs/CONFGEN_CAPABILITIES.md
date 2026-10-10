# ConfGen 能力边界

面向用户：ConfFlow 的 ConfGen 现在能算什么、怎么配、输出什么、不支持什么。
事实基准为 2026-10-11 的 ConfGen 单引擎状态（`schema_version: 4`）。
行号只作核对用，执行与复核一律按符号定位。

安装与运行见 [`USAGE.md`](USAGE.md)；命令全表见 [`COMMAND_REFERENCE.md`](COMMAND_REFERENCE.md)；
工作流语义见 `architecture/WORKFLOW_V4.md`；简化输入见 [`PRODUCER_INTENT.md`](PRODUCER_INTENT.md)。

## 1. 能算什么

计算卡片（`confflow/producer/cards.py` 的卡表为准，共 9 种）：
`opt`、`sp`、`freq`、`opt_freq`、`ts`、`ts_freq`、`confgen`、`refine`、`deduplicate`。
`filter` 不是卡片，是 `structure_transform` 的一种 `kind`
（`confflow/workflow/v4/schema.py` 的 `TRANSFORM_KINDS=(refine, deduplicate, filter)`）。

- ConfGen：构象生成，只有一个引擎，即 DG 搜索（见第 2 节）。科学代码在
  `confflow/science/confgen/`，执行器为 `confflow/execution/confgen_executor.py`。
- `refine`：拓扑分组的 RMSD 精修（默认阈值 0.25 Å，见 [`USAGE.md`](USAGE.md) 精修节）。
- `deduplicate`：内容一致去重。
- `filter`（按能量/虚频）：`results` 必须显式绑定某一个计算步骤的 `results` 端口，
  只读该步骤的能量与频率。实现见 `confflow/execution/energy_filter.py`。
- `script` 步骤：工作流只能按 id 引用服务器上已登记的脚本，不写任意命令。
  登记与校验见 `confflow/execution/script_registry.py`。
- recipe（目录见 `confflow/producer/recipes.py` 的 `RECIPE_IDS_V4`，共 8 个）：
  - `confgen_search`：单步 DG 搜索，种子与起始数需用户确认；
  - `monomer_conformers`：ORCA `XTB2` 预优化 → ConfGen DG 搜索 → 去重；
  - `ensemble_refine`：多帧 xyz → 去重 → 优化 → 精修 → 频率 → 按能量筛选；
  - 另有 5 个单算 recipe：`optimize`、`single_point`、`frequency`、`opt_freq`、`transition_state`。

## 2. DG 搜索引擎

一个 ConfGen 步骤的全部工作是：

1. 对输入结构做类型拓扑（共价键、可选的配位与反应对）；
2. 用 RDKit ETKDG 距离几何（`confflow/science/confgen/dg_seed.py`）生成出发构象；
   有金属时，每个策略过滤后的配位类各取若干出发（每类 `starts` 个），无金属时整体取 `starts` 个；
3. 每个出发在 GFN2-xTB 下做受限松弛（金属–给体与反应对回到参考距离；无金属时只约束反应对；
   无任何约束时不向 xTB 传 `--input`）；
4. 对每个松弛结构做 8 项审计（第 5 节），只写出全部通过的结构，按能量排序。

搜索由 `confflow/science/confgen/search.py` 编排，worker 是被监督的子进程
`confflow/execution/confgen_search_worker.py`，执行侧为 `confflow/execution/confgen_search_run.py`。
对同一输入、同一 `seed` 与同一设置，结果确定。

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
  execution: {executable: "/opt/orca611/otool_xtb"}
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
      starts: 8
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
  配位数必须与形状相容（4–6，`CN_SHAPES`）；
- `constraints`：可选的 `FORBIDDEN_TRANS` 策略声明（`classification` 固定为 `REJECTED_BY_POLICY`），
  是策略而非证明。

`search`（可选）与默认值：

| 字段 | 默认 | 含义 |
| --- | --- | --- |
| `starts` | 声明了 `coordination` 时每个配位类 `8`；否则总计 `400` | DG 出发数 |
| `small_ring_torsions` | `both` | 小环扭转开关：`both` 时前 `ceil(starts/2)` 个出发开、其余关；`on`/`off` 全局开或关 |
| `embed_timeout_seconds` | `120` | 每个出发的 DG 嵌入超时（秒） |
| `max_cycles` | `1000` | xTB 松弛最大循环数 |
| `bond_scale` | `1.25` | 审计阶段重新感知键时的尺度（与 `tolerances.bond_scale` 独立） |
| `fragment_charges` | `[]` | DG 片段电荷覆盖：`{atom, charge}`，`atom` 使用本步骤的 `index_base` |

`starts` 为 `null`（或缺省）时在运行时解析为上表默认值，解析结果写入 job 文件与 `summary.json`。
测量（β-D-吡喃葡萄糖，与 155 个 CREST 参考构象比较，2026-10-11）：400 starts 全原子召回 149/155，
800 starts 为 155/155。

过渡态只需声明反应对，不必列出全部共价键，例如 `topology: {add_bond: [{atoms: [2, 4], kind: FORMING}]}`。
搜索模式下未声明 `topology.bonds` 时，图的键感知使用 `tolerances.bond_scale`（默认 1.15），
审计用 `search.bond_scale`（默认 1.25）重新感知。

无金属时省略 `coordination` 整节即可；`coordination_class`、`metal_donor_distance`、`donor_orientation`
三项审计记为 `skipped`，不判失败。

## 4. xTB 配置

搜索需要 xTB 可执行文件，按 `xtb` 程序名解析：步骤的 `execution.executable` 优先，
否则取运行请求中 `executables["xtb"]` 的默认值。解析不到即在开工前 fail-closed。
worker 记录 xTB 的 `--version` 行到 `summary.json`。

## 5. 审计

每个松弛结构都与输入参考逐项比较，任一项不过即该结构不通过：

1. `converged`：xTB 收敛标记存在且 `xtbopt.xyz` 存在；
2. `topology`：同规则感知的共价键集与参考一致（反应对不计入）；
3. `coordination_class`：配位类无歧义地归一到被命令的类（无金属时 `skipped`）；
4. `stereo`：只比参考上 RDKit 标出的真四面体手性中心（`DGSeedResult.stereo_centers`）的符号体积；
   CH2/CH3 的等价 H 互换不判失败；
5. `reaction_distance`：反应对距离在参考的 ±0.02 Å 内；
6. `metal_donor_distance`：金属–给体距离在参考的 ±0.03 Å 内（无金属时 `skipped`）；
7. `donor_orientation`：每个给体的金属–给体–取代基夹角与参考至多差 30.0°（无金属时 `skipped`）；
8. `contacts`：相隔 3 根键以上、且不含金属的原子对，距离不短于共价半径和的 0.70 倍。
   仓内没有独立 vdW 半径表，沿用 `confflow/science/bonding.py` 的共价半径表。

## 6. 输出与退出语义

- `structures.xyz`：通过的结构，按能量排序；注释行为
  `target=<id> start=<n> energy_eh=<e> rel_kcal=<r>`。
- `summary.json`：设置（含解析后的 `starts`、xTB 身份）、每个目标与出发的审计结果、
  `fragment_charges`、汇总计数。
- worker 的 `stdout.log`、`stderr.log`（均为制品）。
- 成员的 `role` 为 `conformer`，`ordinal` 为能量名次（0-based），id 按 `conformer_output_id`；
  metadata 含 `seed`、`starts`、`energy_eh`、`rel_kcal`、`search_target`、`search_start`。
  搜索成员是普通结构，下游按导入结构消费，不带额外状态。

退出：至少一个结构通过为 `COMPLETED`，附 INFO `confgen_completed`（生成、松弛、通过的数量，
失败审计项直方图）；零通过为 `FAILED`，附一条 ERROR 并点名失败审计项直方图；
worker 异常（包括 job 非法或 xTB 缺失）为 `FAILED`，附 stderr 尾部。

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
- 构象空间：没有环或扭转的专门采样；覆盖依赖 DG 出发数与 `small_ring_torsions`。
  `starts` 不足时可能漏掉低能构象（见第 3 节的召回测量）。
- 能量：只有 xTB（GFN2）松弛后的能量，无 DFT 能量，无加权玻尔兹曼统计。
- Freeze：ConfGen 不支持 `freeze`，非空即拒绝。
- `fragment_charges` 是启发式覆盖，需要用户确认。
- 过渡态：反应对只做距离约束，不验证过渡态。
- 对称：没有对称抑制；等价构象可能重复出现，去重由后续 `deduplicate` 步骤负责。
