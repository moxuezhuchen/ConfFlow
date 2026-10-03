# ConfGen 路径声明（paths）

不必逐个写出链上的每根键：给出两个端点和移动的一侧，ConfGen 在工作拓扑上解析出连接路径，
把路径上每根键变成一个相对旋转的扭转自由度。

## 1. 写法

在 V4 文档里使用 typed v3（`schema_version: 3`）；采样必须显式声明（`angles` 或 `step`），没有隐式默认值：

```yaml
steps:
  - id: s_gen
    executor: confgen
    bindings:
      structure: {source: {run: structures}}
    confgen:
      schema_version: 3
      index_base: 1
      paths:
        - {start: 81, end: 92, move: end, angles: [0.0, 120.0, 240.0]}
        - {start: 5,  end: 9,  move: start, step: 60}
```

端点**始终是面向用户的 1 基原子序号**，与 `index_base` 无关。`move` 必填，只能是 `start` 或 `end`，不会被推断。

| 键 | 必填 | 含义 |
| --- | --- | --- |
| `start` | 是 | 1 基原子序号（整数；拒绝 bool/浮点） |
| `end` | 是 | 1 基原子序号，必须与 `start` 不同 |
| `move` | 是 | `start` 或 `end`：哪个端点所在的一侧移动 |
| `angles` | 二选一 | 显式角度表，如 `[0.0, 120.0, 240.0]`；周期重复（如 `0` 与 `360`）被拒绝 |
| `step` | 二选一 | 简写，展开为 `range(0, 360, step)`，`step` 取 1..360 |
| `id` | 否 | 溯源标签 |

未知键、非整数/bool 端点、相同端点、非有限角度、`angles` 与 `step` 同时给出、两者都不给，均失败关闭。
不支持 `waypoint`（多端点路线）：需要时请改用显式的 `torsions` 声明。

## 2. 简化输入（intent）里的旧写法

`confflow.intent.v1` 的 confgen 卡仍接受旧的 `native.paths` 词汇，并由编译器**机械地**改写成 typed v3：
裸声明的 `step` 取 `angle_step`（缺省 120），`bond_scale` 变为 `tolerances.bond_scale`，
`strict_path_bond_check` 变为 v3 顶层标志；端点不被检查或改写。以下情形在编译期报错：

- `native` 含 paths 以外的旧词汇（如 `chains`）：该范围没有 typed 形式；
- 路径声明含 `waypoint` 或其他未知键：报错并提示改用 `torsions`；
- `native` 已写 `schema_version: 3` 时按 typed 原样使用。

直接写在 V4 工作流里的旧 `confgen.native`（`chains`、`paths`、`angle_step` 等）已不再被支持：文档会被 ConfFlow 以
`unknown_member` 拒绝（失败关闭）。需要手动改为 typed v3，对照见 [`USAGE.md`](USAGE.md) 的"迁移旧文档里的 `confgen.native`"。

## 3. 解析规则

解析在**每个 work item 的最终工作拓扑**上进行（结构记录自带的工作拓扑；驱动结构可以是上游产物）。
提交期的文档检查只是结构性的；同一个纯解析器在执行期运行。

1. 先在**完整图**上判断端点是否连通；不在同一连通分量 → `PATH_DISCONNECTED`。
2. 去掉环上的边（保留桥）。在桥图中连通的端点解析为**唯一**的桥路径；完整图连通而桥图不连通 →
   `PATH_CROSSES_RING`（环上的键不独立旋转；环原子仍可作为桥路径的端点）。
3. `PATH_AMBIGUOUS` 保留但目前不会产生：森林里两点之间至多一条简单路径。
4. 每根旋转键被切开，包含所选端点的分量移动。**轴原子不动**；侧链原子随所在分量刚性移动，
   其内部键不成为额外自由度；无关的分量不动；输出的原子顺序不变。
5. 路径键若没有二面角框架（例如末端键），typed v3 失败关闭，不会凭空造一个框架。

## 4. 合并与冲突

同一根键被多处声明时：

- 相同的声明（同键、同取向、同移动侧、同角度表）合并，并记录**全部**来源；
- 相反的移动侧 → `PATH_DIRECTION_CONFLICT`；
- 角度表不同、方向相反但含义不对称、绝对/相对/化学模型不一致 → `ROTOR_SAMPLING_CONFLICT`；角度表绝不隐式求并集。

## 5. 上限、警告与 freeze

- 记录两个计数：`declared_cartesian_size`（去重前的声明空间）与最终规范任务数；规范任务数在任何几何生成**之前**
  以任意精度计算，超过 `max_declared_states` 即拒绝。后置的 `max_conformers` 只是幸存者上限，不是计算安全阀。
- 新展开的键若测得长度低于共价半径之和的 **0.92 倍**，产生 `WARNING_SHORT_BOND`（例如 1.34 Å 的 C–C 会警告，1.52 Å 不会）。
  这只是距离启发式，不做化学判断；**仅警告**，不跳过。`strict_path_bond_check: true` 把它升级为 `PATH_SHORT_BOND` 错误。
- ConfGen 对 `freeze` 失败关闭，有无 paths 都一样。

## 6. 溯源

报告（`confgen_report.json`、`ensemble_report.json`）记录：驱动结构 id 与几何 digest、原子符号、
每个来源解析出的路线（`declared_paths`：来源、端点、`move`、有序 1 基路线、角度；来自唯一的解析器）、
工作图拓扑 digest、有序的旋转键（键、移动/固定集合、角度、来源）、警告，以及声明计数与规范计数。
`confgen_path_resolved` 信息诊断会打印带元素符号的有序链和所选移动端点。
已声明的 paths 参与步骤语义 digest；机器路径不进入科学 digest。
