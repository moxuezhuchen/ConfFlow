# ConfGen 能力边界

面向用户：ConfFlow 能算什么、实测结果如何、哪些不支持、删了什么、怎么配服务器。
事实基准为当前 `main`（已合并 FIX-1R、FIX-1D 与 DIET-2 R1/R2、N1–N4）。
行号只作核对用，执行与复核一律按符号定位。

安装与运行本文不重复，见 [`USAGE.md`](USAGE.md)（服务器源码安装与更新）；
路径写法见 [`CONFGEN_PATHS.md`](CONFGEN_PATHS.md)；命令全表见 [`COMMAND_REFERENCE.md`](COMMAND_REFERENCE.md)；
工作流语义见 `architecture/WORKFLOW_V4.md`；简化输入见 [`PRODUCER_INTENT.md`](PRODUCER_INTENT.md)。

## 1. 能算什么

计算卡片（`confflow/producer/cards.py` 的卡表为准，共 9 种）：
`opt`、`sp`、`freq`、`opt_freq`、`ts`、`ts_freq`（`ts_freq` 默认期望 1 个虚频），
以及 `confgen`、`refine`、`deduplicate`。
`filter` 不是卡片，是 `structure_transform` 的一种 `kind`
（`confflow/workflow/v4/schema.py` 的 `TRANSFORM_KINDS=(refine, deduplicate, filter)`）。

- ConfGen：typed v3 构象生成（`schema_version: 3`），科学实现在
  `confflow/science/confgen/`，执行器为 `confflow/execution/confgen_executor.py`。
- `refine`：拓扑分组的 RMSD 精修（默认阈值 0.25 Å，见 [`USAGE.md`](USAGE.md) 精修节）。
- `deduplicate`：内容一致去重。
- `filter`（按能量/虚频，N3）：`results` 必须显式绑定某一个计算步骤的 `results` 端口，
  只读该步骤的能量与频率，不追溯历史；参数为 `energy_key(electronic|gibbs)`、
  `energy_window_kcal`、`lowest_n`、`max_imaginary_count`、`imaginary_threshold_cm1`
  （默认 0，即任何负频率都算虚频）。实现见 `confflow/execution/energy_filter.py`，
  绑定校验见 `confflow/workflow/v4/validation.py`。
- `script` 步骤（N2，登记脚本）：工作流只能按 id 引用服务器上已登记的脚本，
  不写任意命令。`args` 占位符只允许 `{input}`、`{cores}`、`{mem_gb}`、`{item_id}`；
  输出只有三个固定通道 `artifacts`、`structures`、`summary`，
  下游只能消费 `structures`。登记与校验见
  `confflow/execution/script_registry.py`、`confflow/execution/script_executor.py`。
- recipe：`monomer_conformers`（ORCA `XTB2` 预优化 → ConfGen 环+转子 → 去重，
  示例环/转子轴使用前必须确认替换）与 `ensemble_refine`
  （多帧 xyz → 去重 → 优化 → 精修 → 频率 → 按能量筛选，`results` 自动绑直接前驱的
  `freq` 步骤；MD 轨迹先抽帧；可选的 N2 脚本预筛需手动插在去重与优化之间）。
  另有 6 个单算 recipe（`optimize`、`single_point`、`frequency`、`opt_freq`、
  `transition_state`、`confgen_torsion`），共 8 个，以 `contract --json` 的
  `supported_recipes` 为准。
  目录见 `confflow/producer/recipes.py` 的 `RECIPE_IDS_V4`。

## 2. ConfGen TS1 配位实测

生产 TS1 共 12 个 target，按后端分别计数（`published_leaf` 即 REALIZED 叶）：

- default：**4/12**（8 `failed_numerical`）。
- rigid：**1/12**（11 失败）。
- flexible：**3/12**（9 失败）。

仓库内验收记录见 `docs/confgen-fix/LOG.md`（D1–D3 节：D2 起 default 由 3→4，
仅 `coordination:000001` 由失败转为发布，其余 target 状态与证据不变）
与 `docs/confgen-fix/checkpoints/D2/`、`docs/confgen-fix/checkpoints/D3/` 的
`MANIFEST.json`/`DIFF.md`（含 golden 变化方向与 `start_statistics`）。
失败均保留 attempts 与审计证据，不是不可行性证明。

## 3. 环：默认与全集、葡萄糖结论

- 支持孤立共价 4/5/6 元环（`ring/realization.py` 的 `SUPPORTED_RING_SIZES=(4,5,6)`）。
  正则形式全集为 CP 表权威（`ring/puckering.py` 的 `canonical_forms`）：
  6 元环 38（2C+6B+6TB+12E+12H）、5 元环 20（10E+10T）、4 元环 3（P/B+/B-）。
- 默认枚举（`ring/forms.py` 的 `default_forms`）：6 元环只枚举 **2C+6TB（8 个）**，
  不含 6B；5 元环默认全部 20 个；4 元环默认全部 3 个。
  需要 B/E/H 时显式声明 `forms`（族选择器如 `B` 展开为该族全部，精确选择器如 `B_2` 取单个）。
- 螯合环（含金属）、稠环、桥环、螺环（共享原子/重叠体系）、大环一律 fail-closed，
  见 `ring/realization.py` 的 `RingUnsupported` 分支与
  `tests/v4/test_confgen_r4_scope_guards.py`、`tests/v4/test_confgen_v3_ring.py`。
- 输入局部几何（键长/杂化）继承输入；`distorted_input` 只是诊断（WARNING），不是错误，
  TS/中间体/约束优化结构的真实畸变会被如实保留。
- 葡萄糖（β-D-吡喃糖）结论：显式 38 形式下 strict CP `<15°` 为 **127/155**，
  basin 归属 **155/155**；默认 8 形式下 strict 为 113/155。
  转子声明 + 无约束优化后，环×转子联合抽样优化并集达到 strict **152/155**、
  重原子 153/155、全原子 147/155，剩余均为参考系最高能尾部。
  口径不变：strict `<15°` 仍是严格诊断指标，不放宽；basin 只做并列诊断，不进 engine 报告。
  长表与复现步骤只在 [`confgen-fix/R7-GLUCOSE-RECALL.md`](confgen-fix/R7-GLUCOSE-RECALL.md)，
  本文不复制。

## 4. 限制与不支持

- 多金属：模型只有一个 `metal_center`（整数槽，见
  `confflow/science/confgen/graph.py` 的 `CoordinationSpec`/`TypedGraph`），
  配位数只支持 4–6（`CN_RANGE=(4,6)`）。第二个金属无法表示。
  本仓未提供一条字面 `multiple metals not supported` 错误语句，
  对外只表现为 schema 单槽与越界 fail-closed。
- hapticity：`BindingSite.__post_init__` 只接受 `kind='atom'` 且
  `hapticity=1`、单原子位点，多 hapto 会抛 `UnsupportedTopologyError`
 （见 `confflow/science/confgen/graph.py` 与
  `tests/v4/test_confgen_v3_coordination.py` 的 fail-closed 测试）。
- 不要泛称“全部螯合配合物不可用”：一个 bidentate 配体（如 N–C–C–N）
  以两个单齿位点（各 `hapticity=1`）同时放置全部 donor 的方式是支持的，
  几何不一致则诚实失败；证据见 `coordination/realization.py` 与上述测试中的
  bidentate 合成体系。上面“不支持的螯合环”专指环组件把含金属的环判为
  `chelate` 而拒绝，与配位 bidentate 不是同一含义。
- torsion：`paths` 仅端点模式，每条 path 必须显式声明 `move: start|end`；
  `waypoint`、自动转子识别不支持；对称转子只产生冗余（FIX-2 范围）。
- 采样：精确条件枚举支持；条件树 + 全局 cap 联合采样有意 fail-closed（待加权树采样）。
- 对称抑制：只支持已审计的单轴 verified-symmetry 抑制；
  联合轴、采样 cap 下、顶层 exclusions 下的抑制一律禁用并如实记录。
- checkpoint：支持 Gaussian 标准 `%OldChk` 与 Opt `ReadFC`
 （`REUSE_MODES=(checkpoint, readfc)`）；
  ORCA checkpoint 复用、QST2/QST3 不支持。
  注意与旧方案文字的差异：旧 G1 曾写 IRC（`RCFC`）支持，
  现 IRC 已随 DIET-2 删除，`RCFC` 不再提供（见第 5 节）。
- freeze：Calculation 的 `freeze`（1-based 整数表，`[]` 为显式清空）支持；
  ConfGen 不支持 `freeze`。

## 5. 已删除的功能

按 `docs/diet-2/PLAN.md` D2（以该文件为准，旧 confgen-fix PLAN 的相关行已 supersede）：

删除：远程执行；QST2/QST3（含 Gaussian 命名槽与原子映射）；NEB；GOAT（含 ensemble 解析）；
IRC（含 Gaussian/ORCA 路径实现与 checkpoint 的 `RCFC` 规则）；`tspes` recipe；
`analysis` 能力（`reaction_profile`、热化学、PES，整个 `confflow/analysis/` 包已不存在）；
多输出机制（`ensemble`、`path_endpoints` 输出形式、`multi_output`）；
`named_structures` 适配器；`remote_capability` 与步骤绑定的 `target` 字段；
离线发布与安装流水线；产物垃圾回收；兼容转发层。

热化学分工（D4）：准谐振等热化学校正交给 GoodVibes 或 Shermo，不重新实现；
ConfFlow 只保证计算结果中带有从输出解析出的电子能、热校正和 Gibbs 自由能
（Gaussian/ORCA 解析器已解析）。

`tspes.py` 接入方式：它不在本仓，是独立仓库的独立工具（仅标准库，自带指纹续跑），
经 N2 脚本步骤接入——服务器登记后，工作流用 `executor: script` + `script: <id>` 引用，
输出摘要由该脚本自己的 `--summary-json` 在其仓库实现。

## 6. 安装与运行

以 [`USAGE.md`](USAGE.md) 为准，本文不重复步骤与命令：
服务器源码安装与更新（D6）见该文件“服务器安装方式”节；
校验、运行、续跑、结果位置见该文件第 3–5 节。
Gaussian 16 / ORCA 需自行安装并取得许可，ConfFlow 不随包提供。

## 7. `server.toml` 配置示例

字段名与实现一字对应（`confflow/execution/quota.py`、
`confflow/execution/script_registry.py`；测试写法见 `tests/test_script_steps.py`）。
路径优先级：显式传入 > `$CONFFLOW_SERVER_CONFIG` > `~/.config/confflow/server.toml`；
文件不存在时配额与登记表都不启用（与现状相同）。
状态目录：`$CONFFLOW_SERVER_STATE_DIR` 或 `~/.local/state/confflow/server`。

```toml
total_cores = 96
total_memory = "192GB"

[scripts.tspes]
command = ["python3", "/opt/scripts/tspes.py"]
description = "TS refinement entry"
```

- `total_cores` 须为 ≥1 的整数（bool 拒收）；`total_memory` 须 >0（如 `"192GB"`）。
- `[scripts.<id>]` 的 `command` 须为非空字符串数组，`description` 可选但须为字符串；
  `sha256`/`interpreter`/`script_path` 由实现派生，不手写。
  单元素命令须自身为可执行文件；多元素命令取 `argv[1:]` 中首个存在文件为脚本，
  找不到即 fail-closed。
- 超过整机容量的申请立即失败，不等待；等待中取消立即生效。

## 8. DG 搜索脚本（DG 出发 → 约束 xTB → 审计）

- 脚本 `scripts/confgen_dg_search.py`（独立 CLI，`main(argv=None) -> int`，仅标准库 + NumPy +
  `confflow` 导入）对一个给定的分子做：有金属时，策略过滤后的每个配位类各做若干 DG 出发构象；
  无金属时只有一个 target（`t00`，无配位类）。DG 出发来自
  `confflow/science/confgen/dg_seed.py`（`spec=None` 即无金属路径：`placement` 为空、`shape`
  被忽略，片段在全部原子上感知，只保留反应对 bounds），每个出发构象在谐振距离约束下做 GFN2-xTB
  优化（金属–给体与反应对回到参考距离，无金属时只有反应对；无任何约束时 xTB 不带 `--input`），
  再逐个审计；只写出全部通过的结构（按能量排序）。
  ConfGen 工作流只能冻原子、不能做距离约束，因此该协议以 N2 登记脚本形式提供，
  不动 engine/schema/executor/registry（见 `confflow/execution/script_registry.py`、
  `confflow/execution/script_executor.py`，测试见 `tests/test_script_steps.py`）。
  搜索流水线本体为包内纯模块 `confflow/science/confgen/search.py`（DG 出发 → 约束 → 审计；仅 numpy 与 confgen 科学导入，无子进程/文件 I/O/argparse），脚本只保留 I/O 与 xTB 子进程调用。
- `--metal`、`--donors`、`--shape` 要么全给、要么全不给（其它组合即 argparse 错误）；全不给即
  无金属路径，此时 `--forbid-trans` 不允许，`coordination_class` 与 `metal_donor_distance`
  记为 `skipped` 且不判失败（记在每个 structure 的 `skipped_checks`）。
- 小环 torsion 开关 `--small-ring-torsions {both,on,off}`（默认 `both`）：`both` 时每个 target
  前 `ceil(count/2)` 个出发用开、后 `floor(count/2)` 个用关（种子公式见脚本内注释），每个出发
  的开关记在每个 structure 的 `small_ring_torsions`；开偏向低能环形并压住其它构象，关覆盖更广
  但低能出发更少（同 `dg_seed.py` 中 `small_ring_torsions` docstring 的一句话）。
- 服务器登记（`command` 中首个存在文件即脚本，找不到即 fail-closed）：
  ```toml
  [scripts.confgen_dg_search]
  command = ["python3", "<repo>/scripts/confgen_dg_search.py"]
  ```
- 工作流 `script` 步骤示例（`args` 占位符只允许 `{input}`、`{cores}`、`{mem_gb}`、`{item_id}`，
  其余一律 plain flag；输出只有三个固定通道 `artifacts`、`structures`、`summary`）：
  ```yaml
  - id: dg_search
    executor: script
    script: confgen_dg_search
    args: ["{input}", "--metal", "1", "--donors", "2,3,4,5", "--shape", "square_planar",
           "--xtb", "/opt/orca611/otool_xtb", "--count", "8",
           "--out", "structures.xyz", "--summary", "summary.json"]
    resources: {cores_per_item: 2, memory_per_item: "4GB"}
    outputs: {structures: "structures.xyz", summary: "summary.json"}
    bindings: {structure: {source: {run: structures}}}
  ```
  `{input}` 按位置传给脚本（另有等效 `--input`）；无金属时 args 去掉
  `--metal/--donors/--shape`，其余相同；`--xtb` 缺省读 `$CONFFLOW_XTB`，
  找不到可执行文件即在开工前 fail-closed；已写过同内容 `done.json` 的出发目录自动续跑。
- 审计（相对输入参考逐项记录，一项不过即不通过）：`converged`（收敛标记且 `xtbopt.xyz` 存在）；
  `topology`（同规则感知的共价键集与参考一致，反应对永不计入）；
  `coordination_class`（`perceive_donors` 无歧义且归一到被命令类，无金属时记 `skipped`）；
  `stereo`（只比参考上 RDKit 标出的真四面体手性中心，即 `DGSeedResult.stereo_centers`，
  的符号体积；CH2/CH3 的等价 H 互换不再误判）；`reaction_distance`（反应对 ±0.02 Å）；
  `metal_donor_distance`（金属–给体 ±0.03 Å，无金属时记 `skipped`）；`donor_orientation`（每个给体的金属–给体–取代基夹角与参考至多差 30.0°，无金属时记 `skipped`）；`contacts`（相隔 3 根键以上且去金属的原子对，
  不短于 0.70 倍半径和；仓内无独立 vdW 半径表，沿用共价半径表，与
  `coordination/realization.py` 的 clash guard 一致）。
- 退出码：至少一个通过为 0；零通过仍写 `summary.json`、不写空 xyz，退出码为 2 并在 stderr 说明。
- 限度：出发构象必须经 xTB 松弛（测试用假 xTB，不依赖真二进制）；电荷启发式带有
  `--fragment-charge ATOM:Q` 覆盖；配位路径只支持单金属、`hapticity=1`，无金属路径无此限制；
  结果是约束下的 GFN2 极小点，不是验证过的过渡态。
