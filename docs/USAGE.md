# ConfFlow 使用说明

怎么装、怎么写一份 V4 工作流、怎么校验和运行、怎么续跑、结果在哪。命令行参数的完整列表见
[`COMMAND_REFERENCE.md`](COMMAND_REFERENCE.md)，工作流语义见 [`architecture/WORKFLOW_V4.md`](architecture/WORKFLOW_V4.md)。

## 1. 安装

```bash
pip install -e .            # 开发安装；或 pip install .
confflow --version
```

真实计算需要你自己安装并取得许可的 Gaussian 16 或 ORCA；ConfFlow 不随包提供它们。

### 服务器安装方式

服务器上默认使用源码安装（离线发布流水线已退役，不再提供发布用 wheel）：

```bash
git clone <confflow-仓库地址>     # 首次安装
cd ConfFlow
pip install .
```

更新时：

```bash
git pull && pip install .
```

### 无外网集群的临时办法（自建 wheel）

集群无法联网或不便从源码构建时，可在能联网的机器上自己构建 wheel，再拷到集群安装（只作临时办法，不是发布渠道）：

```bash
pip wheel --no-deps .        # 在联网机器的仓库根目录执行，生成 confflow-*.whl
pip install confflow-*.whl   # 在集群上执行
```

从源码构建需要 `setuptools>=82.0.1`（`pyproject.toml` 的 `[build-system] requires`）。
`--no-deps` 只作用于构建命令；运行依赖仍由 `pyproject.toml` 的 `dependencies` 声明，集群上需另行提供。

## 2. 工作流文档

唯一受支持的格式是 V4（`schema: confflow.workflow.v4`，YAML 或 JSON）。一份文档由四部分组成：

- `inputs`：具名运行输入（例如 `structures`，多个结构）。
- `global`：运行级默认值：`scientific_defaults`（电荷、多重度、freeze）、`resources`、`scheduler`。它们是**默认值**，步骤级声明会覆盖。
- `steps`：每步恰好一个 `executor`，用 `bindings` 声明输入来自哪里（`source.run` 或 `source.step` + `port`）。
  依赖关系只来自 bindings，不来自文件名或列表顺序。
- 步骤类型：`confgen`（构象生成）、`calculation`（Gaussian / ORCA 计算）、`mlip`（UMA 整批单点与约束弛豫，见 §2 对应小节）、`structure_transform`（精修 `refine`、
  `deduplicate`、`filter`，YAML 块名 `transform`）、`script`（服务器登记脚本，见 `CONFGEN_CAPABILITIES.md` §1）。
  `analysis`（反应/PES 分析）已于 R2.2（`383a1f6`）退役：`confflow/analysis/` 不存在，默认 registry 无此
  capability，`contract --json` 的 `analysis_capabilities` 为空。

没有隐式的清理或去重：想去重就写一个显式的 `structure_transform` 步骤。

一个"生成构象 → 精修去重 → 优化"的例子（可直接 `validate`）：

```yaml
schema: confflow.workflow.v4

inputs:
  structures: {kind: structure, cardinality: many}

global:
  scientific_defaults: {charge: 0, multiplicity: 1}
  resources: {cores_per_item: 4, memory_per_item: 8GiB}

steps:
  - id: s_gen
    executor: confgen
    bindings:
      structure: {source: {run: structures}}
    confgen:
      schema_version: 4           # typed v4：迭代 DG 搜索是唯一的 confgen 引擎
      index_base: 1               # 原子序号从 1 开始
      seed: 11                    # 显式整数种子（随机性的唯一权威）
      search: {budget: 1500}      # 搜索阶段的优化次数上限（收尾另加；默认 1500）

  - id: s_refine
    executor: structure_transform
    bindings:
      structure: {source: {step: s_gen, port: structures}}
    transform:
      kind: refine
      native: {rmsd_threshold_angstrom: 0.2}

  - id: s_opt
    executor: calculation
    bindings:
      structure: {source: {step: s_refine, port: structures}}
    calculation:
      program: orca
      role: opt
      native: {keyword: "B3LYP D3BJ def2-SVP Opt"}
      checks: [normal_termination, geometry_required]
```

仓库根目录的 `confflow.example.yaml` 是更完整的、带注释的示例（安装后的 wheel 里是 `share/confflow/confflow.example.yaml`）。

### ConfGen 的声明方式

ConfGen 只有一个生成引擎（迭代 DG 搜索：全局 DG 出发、势阱探测、反馈轮次、去约束收尾），文档必须写 `schema_version: 4`，`seed` 必填。
完整字段、默认值、审计项与输出见 [`CONFGEN_CAPABILITIES.md`](CONFGEN_CAPABILITIES.md)。

- `index_base`（0 或 1）、`seed`（整数）、`topology`（`bonds`/`add_bond`/`del_bond`/`atoms`）、
  `tolerances.bond_scale`、`coordination`（可选）、`search`（可选，整节缺席即全部默认）。
- `schema_version: 3` 文档被拒绝，并提示环、扭转、路径与配位实现引擎已删除，应改用 `schema_version: 4`。
- 旧字段 `rings`、`torsions`、`paths`、`sampling`、`limits`、`exclusions`、`stereochemistry`、`overrides`、
  `native` 等被严格模型按未知字段拒绝（失败关闭）；没有兼容转换。
- 消费上游 ConfGen 步骤产出成员的步骤，把这些成员当作普通结构。

### 精修去重（refine）

`refine` 与 ConfGen 共用同一套构象身份判据：在图的自同构下比较 RMSD（重原子，外加杂原子上的 H 与非共价带类型边上的 H），
低于阈值（默认 0.2 Å，严格小于才算重复）即合并。因此仅原子标号对称置换的构象（甲基氢、叔丁基的三个甲基、环的取代位）会被合并；
镜像构象默认也算同一构象。需要时：

- `rmsd_threshold_angstrom`：阈值（默认 0.2）；`0` 表示不合并任何结构。
- `mirror`：默认 `true`，镜像构象视为同一构象；设为 `false` 则镜像构象各自保留。
- `mapping_budget`：默认 1000，只限制一次同构搜索的节点数。该搜索把键图与前面某个结构同构、但原子标号不同的记录重新标号；
  预算用尽时该记录保留，只与同一键图的记录比较，并在步骤说明里注明。不再有逐对的 "unresolved" 判定。
- `results`（可选输入端口）：绑定计算步骤的 `results` 输出，来源规则与 `filter` 相同：必须与 `structure` 输入来自同一个计算步骤，
  否则报 `refine_results_source_mismatch`。不绑定时 refine 只看几何，每个构象保留 id 最靠前的记录。绑定后，执行器读取每个记录的电子能，
  并按能量从低到高处理记录；两个记录都有能量时，二者还必须在 0.5 kcal/mol 之内才会合并，每个构象保留能量最低的记录。
  `heavy_only` 已删除，写入会被拒绝（未知 native 键）。
- `topology_bonds`：显式声明带类型的拓扑（形状同 ConfGen 的 `topology`，外加 `index_base`、`coordination`、`bond_scale`）。
  不写时，拓扑来自结构记录自带的工作拓扑，再不然由几何感知得到。
  ConfGen spec 声明的 `topology`/`coordination`/`add_bond`/`del_bond` **不会**写入输出记录；反应边/配位边体系的 refine 需要手写 `topology_bonds`，否则按几何感知建图（`bond_scale` 1.15，与 ConfGen 默认一致）。
- `max_structures`：按 id 顺序截断。


### 用 UMA 重排构象（Gaussian External）

UMA 是机器学习势能，ConfFlow 不附带也不安装 UMA 或它的 Gaussian 包装脚本。前提是：Gaussian 支持 External 接口，
并且包装脚本（例如 `uma_s`）在 `PATH` 上，运行后在日志里输出 `Recovered energy=`。

路线写在计算步骤的 `native.keyword` 里：

- 单点：`# external="uma_s"`，即 `native: {keyword: 'external="uma_s"'}`
- 优化：`# opt=(modredundant,nomicro) external="uma_s"`，即 `native: {keyword: 'opt=(modredundant,nomicro) external="uma_s"'}`

路线里含双引号，所以 YAML 值要整体用单引号包住。

对 External 计算，ConfFlow 把日志中最后一个 `Recovered energy=` 作为电子能。该能量只在日志同时包含 "External calculation of energy"、
且不含 "SCF Done" 与 ONIOM 时才被采用；ONIOM 与 External 同时出现会被拒绝。

推荐流程：ConfGen（引擎本身用 GFN2-xTB 优化，无需另外设置）→ 对**全部**已发布构象做 UMA-s 单点 → 用 `filter` 配合 `energy_window_kcal` 筛选（`results` 绑定到同一个 UMA 步骤）→ DFT。
完整可校验的例子见 `docs/examples/uma_rescoring.yaml`。

需要约束弛豫，或按单点能挑选少数结构再弛豫时，改用 `mlip` 步骤（见下一小节）。

基准：5 个体系，各取 GFN2 能量最低的 40 个构象（均在 GFN2 6 kcal/mol 窗口内），以 r2SCAN-3c 单点为参照。每格为相对能量的平均绝对偏差（kcal/mol）/ DFT 最低构象在该方法下的名次。

| 体系 | GFN2 | g-xTB | UMA-s | UMA-m |
|---|---|---|---|---|
| axurer | 1.59 / 9 | 0.71 / 1 | 0.58 / 1 | 0.28 / 1 |
| cnetpa | 3.78 / 17 | 1.06 / 1 | 0.42 / 1 | 0.25 / 1 |
| 奥司他韦 | 0.85 / 11 | 0.53 / 4 | 0.13 / 1 | 0.12 / 1 |
| akugod | 3.33 / 27 | 1.09 / 1 | 0.63 / 1 | 0.30 / 1 |
| ts1_re | 5.72 / 5 | 1.30 / 1 | 0.48 / 1 | 0.37 / 1 |

- 48 核服务器上单点的平均耗时：UMA-s 0.2–0.5 s，UMA-m 1.0–3.2 s，g-xTB 2.9–10 s，r2SCAN-3c 9–50 s。
- DFT 能量在 2 kcal/mol 以内的构象，按 UMA-s 排序时距 UMA-s 最低点最多 +1.8 kcal/mol（按 GFN2 排序时最多 +2.3），所以 3 kcal/mol 的 UMA 窗口能把它们都留下。
- 局限：只测试了每个体系 GFN2 最低的 40 个构象，窗口之外没有验证。因此应对全部已发布构象做 UMA 重打分（单点很便宜），
  DFT 之前用约 3 kcal/mol 的 `energy_window_kcal`，而不是先用窄的 GFN2 窗口截断。
- UMA-m 略更准确，但慢 5–7 倍。


### 用 UMA 单点与约束弛豫（mlip 步骤）

`mlip` 步骤把输入的全部结构放进一个工作项：UMA 模型在一次运行里只加载一次（集群 lustre 上加载约 3–13 分钟），
之后由运行器内部按 `workers × threads` 并行。逐结构起进程会把加载时间乘以结构数，因此不这样做。
需要"全部单点、挑少数再弛豫"或约束弛豫时用它；只做单点且已有 `uma_s` 包装脚本时，也可以继续用上一节的 Gaussian 路线。

**前提**（ConfFlow 不安装它们）：

- `execution.executable` 指向装有 fairchem 与 ASE 的 Python。
- 模型文件与可选的参考能量文件通过 `execution.env` 给出，不写进工作流（它们属于机器配置）：
  - `CONFFLOW_MLIP_MODEL_PATH`：模型文件路径（必填）。
  - `CONFFLOW_MLIP_REFS_PATH`：参考能量文件路径（可选）。
  - `CONFFLOW_MLIP_DEVICE`：默认 `cpu`。
  - `CONFFLOW_MLIP_THREADS`：每个 worker 的线程数，默认 4。worker 数 = `resources.cores_per_item // CONFFLOW_MLIP_THREADS`，至少 1。

**工作流写法**（注释给出每个字段的默认值；`relax`、`pick` 按模式出现）：

```yaml
- id: s_uma
  executor: mlip
  bindings:
    structure: {source: {step: s_gen, port: structures}}
  mlip:
    model: uma-s-1p2p1            # 必填：模型名（科学身份）
    task: omol                    # 默认 omol
    mode: sp_then_relax           # 必填：sp | relax | sp_then_relax
    index_base: 1                 # 默认 1；0 或 1
    relax:                        # 默认整节缺省；mode 含 relax 时才能写
      optimizer: precon_lbfgs     # lbfgs | precon_lbfgs | fire，默认 lbfgs
      fmax: 0.02                  # eV/Å，默认 0.02，必须 > 0
      max_steps: 600              # 默认 600，≥ 1
      fix_distances: [[39, 95]]   # 默认 []；按 index_base 的原子对
    pick:                         # sp_then_relax 必填；其它模式不能写
      lowest: 8                   # 默认 0
      per_label: 2                # 默认 0；lowest 与 per_label 至少一个 > 0
    reject_bond_stretch: null     # 默认 null（只标记）；设为 > 1 的数，如 1.25，则超限结构丢弃
  resources: {cores_per_item: 12}
  execution:
    executable: /opt/uma/.venv-fairchem/bin/python
    env:
      CONFFLOW_MLIP_MODEL_PATH: /opt/uma/uma-s-1p2p1.pt
      CONFFLOW_MLIP_REFS_PATH: /opt/uma/iso_atom_elem_refs.yaml
      CONFFLOW_MLIP_THREADS: "4"
```

完整可校验的例子见 `docs/examples/uma_relax_funnel.yaml`（单点 → 挑 8+2 个弛豫 → 3 kcal/mol 能量窗口）。

**三种模式**

- `sp`：全部输入做单点，几何不变，全部输出，能量为 UMA 电子能。不能写 `relax` 与 `pick`。
- `relax`：全部输入做约束弛豫后输出。不能写 `pick`。
- `sp_then_relax`：先对全部输入做单点，再弛豫按下面规则挑中的结构；**只输出被挑中的结构**（弛豫后的几何与能量），
  未挑中的结构不进入输出，它们的单点能只用于校准诊断。

挑选规则：挑中集合 = 单点能最低的 `lowest` 个，与每个 `search_state_flips` 标签组内单点能最低的 `per_label` 个的并集。
没有该标签的结构视为同一组。能量相同时按结构 key 排序决定先后。

**约束与优化器**

- `fix_distances` 的原子序号按 `index_base` 解释。执行器检查序号范围、同一原子对自身，以及重复的原子对（与顺序无关），不合法则整步失败。
  弛豫用 ASE 的 FixBondLengths（tolerance 1e-8），因此被固定的距离与输入精确一致；每个输出的偏差记在 `mlip_fix_max_dev`。
- `optimizer` 可选 `lbfgs`、`precon_lbfgs`、`fire`。优化器会改变 UMA 的重排结果：在一个 144 原子的算例中，换优化器使 UMA 排序最多改变 6.6 kcal/mol。
  因此每个结构都记录所用优化器（`mlip_optimizer`），比较不同运行的能量时要先核对它。

**键检查**

- 每个输出结构与输入比较：输入的每条键（固定的原子对除外）在弛豫后的伸长比取最大，记为 `mlip_max_bond_stretch`，对应的键记为 `mlip_stretched_bond`（1 基）。
  伸长比超过 1.25 时发 WARNING `mlip_bond_stretched`。
- `mlip_graph_changed` 标记键图是否变化（有新成键或断键）；变化的结构发 WARNING `mlip_graph_changed`。
- 默认 `reject_bond_stretch` 为 null，只标记、不丢弃。设为数值后，伸长比超过它的结构被丢弃，并在 `mlip_dropped` 中列出。

**输出**

- `structures`：每个结构带以下 metadata：`mlip_energy_eh`、`mlip_model`、`mlip_stage`（`sp` 或 `relax`）、`mlip_optimizer`、`mlip_converged`、
  `mlip_n_steps`、`mlip_n_evals`（计算器调用次数）、`mlip_wall_s`、`mlip_fix_max_dev`、`mlip_input_energy_eh`、`mlip_sp_energy_eh`、
  `mlip_max_bond_stretch`、`mlip_stretched_bond`、`mlip_graph_changed`。`search_state_flips` 等输入 metadata 保留。
- `results`：每个输出结构的 UMA 电子能（Eh）。`filter` 与 `refine` 的 `results` 可以绑定到 `mlip` 步骤，来源规则与计算步骤相同。
- 未收敛的结构照常输出，`mlip_converged=false`，并发 WARNING `mlip_not_converged`。出错的结构丢弃，原因写入 `mlip_dropped` 诊断，并列在 `mlip_completed` 的 `error_rows` 中。
- 产物：`job.json`、`mlip_results.jsonl`（每个结构每个阶段一行，含能量、步数、评估次数、墙钟与错误）、`mlip_done.json`（模型哈希、fairchem/ASE 版本、总墙钟、挑中的 key）、
  `mlip_runner.log`、`mlip_structures.xyz`（注释行含能量与上述字段）。
- 诊断：INFO `mlip_completed`（输入数、单点数、弛豫数、未收敛数、丢弃数、总墙钟、平均评估次数与墙钟）；
  WARNING `mlip_not_converged`、`mlip_bond_stretched`、`mlip_graph_changed`、`mlip_dropped`；
  INFO `mlip_calibration`：输入带 `energy_eh` 时，报告输入能量与 UMA 能量排序的 Spearman 系数，以及输入最低者在 UMA 下高出最低者多少 kcal/mol。
  sp 模式下 basis 为 `sp_all_inputs`（全部输入的单点），`relax` 模式下为 `relaxed_outputs`（输出结构）。

**线程建议**

- 笔记本：`workers × threads` 用 3×4 或 2×6，总线程不超过 12。笔记本上用满 16 线程实测约慢 6 倍。


## 3. 校验与运行

```bash
# 校验精确的文档字节（不运行任何东西）
confflow v4 validate --workflow flow.yaml --json

# 运行整份工作流
confflow v4 run --workflow flow.yaml \
  --inputs structures=mol.xyz \
  --run-root ./run \
  --executable orca=/opt/orca/orca \
  --json
```

- `--inputs NAME=FILE` 可重复；`--executable PROG=PATH` 指定程序的可执行文件。
- `--run-root` 是受管的运行根目录：持久化状态、已发布的步骤结果与产物都在里面。
- 旧的 V1/V2/V3 文档（`iprog`/`itask` 一类）不能再运行：无论从哪个入口，都会以 `legacy_workflow_not_executable` 失败关闭，且没有副作用。
- 退出码：运行 `completed` 为 0，否则为 1；`--json` 在标准输出打印报告（`run_id`、`status`、`definition_digest`、
  各步骤状态、`manifest`）。

## 4. 续跑

重新执行同一条命令即可：已发布的步骤从磁盘加载，未完成的步骤按 work item 续跑，已完成的 work item 复用，
不会从头再来。改动科学内容（native、checks、seed、资源、科学默认值）会改变 digest，相应步骤不会被复用；
改 label、`max_parallel_items`、executable 路径不会。

## 5. 结果

- 每个步骤发布带类型的 `StepResult`（结构、科学结果、产物）。
- 整次运行发布 `run_result.json`（`confflow.run_result_manifest.v1`）：步骤状态、digest、结果与产物的可移植定位。
  GUI 客户端只读取它，不重新计算。
- 反应路径分析（`analysis` 反应组结果）已于 R2.2（`383a1f6`）/ R2.3a（`e961d3c`）退役，
  详见 `CONFGEN_CAPABILITIES.md` §5 与 `architecture/WORKFLOW_V4.md` §30。

## 6. 给 GUI 与脚本用的接口

- `confflow v4 contract --json`：配置契约（工作流 schema、editor manifest、recipe 目录、能力词汇）。
- `confflow v4 boundary --json`：边界协议。
- `confflow v4 authoring --json --stdin`：authoring 请求（`describe_step`、`binding_candidates`、`instantiate_card`、
  `validate_document`、`check_compatibility`、`compile_intent`）。
- 简化输入（intent）：用 `confflow.intent.v1` 写"卡片 + 预设"式的简化文档，由 producer 编译成严格的 V4 文档，
  见 [`PRODUCER_INTENT.md`](PRODUCER_INTENT.md)。
- `confflow --capabilities --json`：能力握手。

## 7. 比较与去重任意 XYZ 结构

`confflow compare` 与 `confflow dedupe` 不需要工作流文档，直接比较任意多帧 XYZ 文件（如 TS 搜索的输出、
多次运行合并的构象集）中的结构是否相同。

```bash
# 两个结构是否为同一个（A.xyz 第 0 帧 与 B.xyz 第 3 帧）
confflow compare A.xyz B.xyz@3

# 合并多个文件，去掉重复，保留每组能量最低者，写入 unique.xyz
confflow dedupe run1.xyz run2.xyz -o unique.xyz

# 机器可读报告
confflow dedupe run1.xyz run2.xyz --json
```

**什么算同一个结构**

- 比较对象是对称 RMSD（考虑图自同构的最小 RMSD），只取重原子，以及杂原子上的 H 与非共价边上的 H。
- 甲基、叔丁基上的 C–H 氢不参与比较，所以甲基转动和叔丁基三个甲基的互换不影响结果；苯环邻/间位的等价翻转同样算同一个。
- 镜像默认算同一个（与 ConfGen 一致）；`--no-mirror` 关闭。
- 阈值与 ConfGen 相同：对称 RMSD 小于 `--rmsd`（默认 0.2 Å），且两者能量都已知时 |ΔE| 不超过 `--e-tol`（默认 0.5 kcal/mol）。
  能量读自注释行 `energy_eh=<Eh>` 或 ORCA 风格的 `E <数>`；读不到则只按几何判同（`--no-energy` 同样如此）。
- 成键由第一帧的距离判定（`--bond-scale 1.15`）。金属可用 `--metal N` 指定，反应对用 `--pair I J` 声明为成键中的边；
  两者都是 1 基序号。

**输出**

- `compare` 打印对称 RMSD、普通重原子 RMSD（不做置换）、ΔE 和判定 `same` / `different`。
- `dedupe` 打印 `N frames -> M unique`，然后每帧一行，标明 `unique` 或 `duplicate of <文件>@<k>`。
  `-o` 写出的代表帧按能量升序排列（所有帧都有能量时），注释保留原文并追加 `members=<组内帧数>`。

输入不合法（XYZ 截断、坐标非数值、帧之间元素序列不一致、索引越界）时返回码为 `2`，并打印 `Error:` 说明。
