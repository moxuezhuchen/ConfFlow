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

服务器上使用源码安装，不使用离线 wheel 包安装：

```bash
git clone <confflow-仓库地址>     # 首次安装
cd ConfFlow
pip install .
```

更新时：

```bash
git pull && pip install .
```

## 2. 工作流文档

唯一受支持的格式是 V4（`schema: confflow.workflow.v4`，YAML 或 JSON）。一份文档由四部分组成：

- `inputs`：具名运行输入（例如 `structures`，多个结构）。
- `global`：运行级默认值：`scientific_defaults`（电荷、多重度、freeze）、`resources`、`scheduler`。它们是**默认值**，步骤级声明会覆盖。
- `steps`：每步恰好一个 `executor`，用 `bindings` 声明输入来自哪里（`source.run` 或 `source.step` + `port`）。
  依赖关系只来自 bindings，不来自文件名或列表顺序。
- 步骤类型：`confgen`（构象生成）、`calculation`（Gaussian / ORCA 计算）、`structure_transform`（精修 `refine`、
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
      schema_version: 4           # typed v4：DG 搜索是唯一的 confgen 引擎
      index_base: 1               # 原子序号从 1 开始
      seed: 11                    # 显式整数种子（随机性的唯一权威）
      search: {starts: 400}       # 无金属时为总出发数；声明 coordination 时为每配位类

  - id: s_refine
    executor: structure_transform
    bindings:
      structure: {source: {step: s_gen, port: structures}}
    transform:
      kind: refine
      native: {rmsd_threshold_angstrom: 0.25}

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

ConfGen 只有一个生成引擎（DG 搜索），文档必须写 `schema_version: 4`，`seed` 必填。
完整字段、默认值、审计项与输出见 [`CONFGEN_CAPABILITIES.md`](CONFGEN_CAPABILITIES.md)。

- `index_base`（0 或 1）、`seed`（整数）、`topology`（`bonds`/`add_bond`/`del_bond`/`atoms`）、
  `tolerances.bond_scale`、`coordination`（可选）、`search`（可选，整节缺席即全部默认）。
- `schema_version: 3` 文档被拒绝，并提示环、扭转、路径与配位实现引擎已删除，应改用 `schema_version: 4`。
- 旧字段 `rings`、`torsions`、`paths`、`sampling`、`limits`、`exclusions`、`stereochemistry`、`overrides`、
  `native` 等被严格模型按未知字段拒绝（失败关闭）；没有兼容转换。
- 消费上游 ConfGen 步骤产出成员的步骤，把这些成员当作普通结构。

### 精修去重（refine）

`refine` 只在合法的元素/边保持映射下比较 RMSD（默认阈值 0.25 Å，严格小于才算重复），所以仅原子标号对称置换的构象
（甲基氢、叔丁基的三个甲基、环的取代位）会被合并。需要时：

- `rmsd_threshold_angstrom`：阈值；`0` 表示不合并任何结构。
- `heavy_only`：只比较重原子。
- `mapping_budget`：每对结构的映射搜索节点预算（默认 1000）；用尽的一对被保留并在步骤说明里标明。
- `topology_bonds`：显式声明带类型的拓扑（形状同 ConfGen 的 `topology`，外加 `index_base`、`coordination`、`bond_scale`）。
  不写时，拓扑来自结构记录自带的工作拓扑，再不然由几何感知得到。
  ConfGen spec 声明的 `topology`/`coordination`/`add_bond`/`del_bond` **不会**写入输出记录；反应边/配位边体系的 refine 需要手写 `topology_bonds`，否则按几何感知建图（`bond_scale` 1.15，与 ConfGen 默认一致）。
- `max_structures`：按 id 顺序截断。


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
