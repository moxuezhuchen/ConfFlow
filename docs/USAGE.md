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
  `deduplicate`、`filter`，YAML 块名 `transform`）、`analysis`（反应/PES 分析）。

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
      schema_version: 3           # typed v3：声明要转的路径
      index_base: 1               # 原子序号从 1 开始
      paths:
        - {start: 1, end: 4, move: end, step: 120}

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

新工作流请用 typed v3（`schema_version: 3`）：`paths`（起止原子对 + 移动侧 + 步长或角度表）、`torsions`、`rings`、
`coordination` 等，细节见 [`CONFGEN_PATHS.md`](CONFGEN_PATHS.md)。全枚举不需要种子；使用 `sampling` 上限时必须给种子。
旧的 `native.chains` / `native.paths` 词汇已不再被执行（非 v3 的 confgen 文档在解析阶段失败关闭）。迁移旧文档时注意：**v3 比旧路径严格，拒绝末端原子端点**（旋转末端原子在几何上是 no-op），路径的起止原子必须是有可测二面角的非末端原子。

#### 迁移旧文档里的 `confgen.native`

含 `confgen.native` 的旧文档会被 ConfFlow 以 `unknown_member` 拒绝（失败关闭），JobDesk 也不再提供编辑或移除它的界面，文档里的 `native` 内容不会被丢弃，需要手动改为 typed v3。`native.chains` 的对照写法（与 `confflow.example.yaml` 已验证的写法相同）：

| 旧写法 | typed v3 |
| --- | --- |
| `native: {chains: ["1-2-3-4"], angle_step: 120}` | `schema_version: 3`、`index_base: 1`、`paths: [{start: 1, end: 4, move: start, step: 120}]` |

- 链 `1-2-3-4` 旋转 1-2、2-3、3-4 三根键；旧默认旋转侧是 left，对应 `move: start`；`angle_step` 对应 `step`。
- `native.paths` 的写法（`start`/`end`/`move`，`angles` 或 `step`）可以原样搬到 typed 的 `paths`；裸声明须补上 `step` 或 `angles`（旧默认 120 不再隐式生效）。
- 种子原样保留；全枚举不需要种子，`sampling.cap` 才必须给种子。
- 注意：**末端原子端点会被 v3 拒绝**，起止原子必须是有可测二面角的非末端原子。
- `no_rotate`、`chain_steps`、`chain_angles`、`max_conformers` 等旧词汇没有一对一的 typed 写法，需要按 `docs/CONFGEN_PATHS.md` 与 typed 的 `torsions`/`sampling` 重新声明。


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

ConfGen 报告里的"带标号状态数"与精修之后的"物理构象数"是两个口径：σ 相关（对称等价）的结构会被合并。

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
- 反应路径分析产出结构化的反应组结果（TS、前后端点、Gibbs 能、势垒）。

## 6. 给 GUI 与脚本用的接口

- `confflow v4 contract --json`：配置契约（工作流 schema、editor manifest、recipe 目录、能力词汇）。
- `confflow v4 boundary --json`：边界协议。
- `confflow v4 authoring --json --stdin`：authoring 请求（`describe_step`、`binding_candidates`、`instantiate_card`、
  `validate_document`、`check_compatibility`、`compile_intent`、`preview_paths`）。
- 简化输入（intent）：用 `confflow.intent.v1` 写"卡片 + 预设"式的简化文档，由 producer 编译成严格的 V4 文档，
  见 [`PRODUCER_INTENT.md`](PRODUCER_INTENT.md)。
- `confflow --capabilities --json`：能力握手。
