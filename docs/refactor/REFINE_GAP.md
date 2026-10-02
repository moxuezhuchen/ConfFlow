# blocks/refine 与 V4 refine 的功能差异报告（C5.1）

> 状态：**待用户确认**。用户决定（2026-10-02）：删除 `confflow/blocks/refine/` 之前，先对比它与 `transform_executor` 的 refine 功能，缺失项列出来交用户确认。
> 对比对象（@ConfFlow `refactor/diet` b8e85a3 与 main d5a40ae 在这两处完全相同）：
> - 旧：`confflow/blocks/refine/`（`processor.py` 878 行、`rmsd_engine.py` 797 行、`topology.py` 660 行）。
> - 新：`confflow/execution/transform_executor.py` 的 `TransformExecutor._refine`（L235 起）、`_duplicate_of`（L321 起）。
> 方法：阅读源码，并用同一对结构在两边各跑一次（见第 3 节）。其余条目只来自阅读，没有运行。行号已逐个对照 b8e85a3 核对。
> 注：第 3 节的实验里两帧的能量相差 1e-6 Hartree（约 0.0006 kcal/mol），会触发旧 refine 的能量辅助阈值放宽（第 6 项）；但在对称映射下两帧的 RMSD 本来就是 0，远低于 0.25 Å，所以合并的原因是对称映射，不是能量放宽。

## 0. 结论（先看这里）

1. **有一项真实的科学功能缺失，需要你决定：对称感知的去重。** 旧 refine 先用图同构找到合法的原子映射，再在映射下比较 RMSD，所以"只是对称等价原子（如甲基的三个氢）标号不同"的构象会被识别为重复。V4 的 refine 只做固定下标比较（要求元素顺序和邻接矩阵逐项相同），这类重复**不会被去掉**。在第 3 节的实验里：同一个丁烷分子，仅把一个甲基的三个氢标号循环置换，固定下标 RMSD 为 0.807 Å；V4 refine 保留 2 个，旧 refine 去重后保留 1 个。
2. 这个差距和 ConfGen 直接相关：v3 的 torsion 枚举按标号区分构象（IS.1b 里 27 个构象互不相同，且无法互相叠合），后接 refine 时，V4 不会合并"只是氢标号不同"的构象，构象数会被放大。
3. 其余缺失项要么因为 V4 的 transform 端口只带结构、没有能量或频率数据而**设计上不适用**，要么是 CLI/报告/并行层面的功能，**可以不要**。
4. 因此我的建议是：**不要直接删除 `blocks/refine`**。先决定第 1 项是移植还是放弃。移植的话，把图同构映射这一块（`topology.py` 的 `MappingSearch` / `find_isomorphism` 与 `rmsd_engine.py` 的 `compare_frames` 里与映射相关的部分）移到 `confflow/science/` 下，作为 V4 refine 的依赖，其余整个删掉。

## 1. 逐项对比

图例：**缺失（科学）**＝V4 没有且对结果有影响；**设计不适用**＝V4 的 transform 端口只传结构，没有这类数据；**可不要**＝CLI/报告/性能层面。

| # | 功能 | 旧 `blocks/refine` | V4 `transform_executor` refine | 类别 | 建议 |
|---|---|---|---|---|---|
| 1 | 对称感知的重复判定 | 为每帧建图（`build_graph`），按拓扑分簇（`group_frames_by_topology`，`topology.py:591`），簇内用 `MappingSearch`/`find_isomorphism`（`topology.py:376`、`:562`，节点预算 `DEFAULT_MAPPING_NODE_BUDGET = 1000`，`topology.py:32`）找合法原子映射，在映射下比较 RMSD（`rmsd_engine.py:411` 的 `compare_frames`）。`rmsd_engine.py` 开头的文档写明："固定下标 RMSD 不等于对称等价映射下的最小值"。 | `_duplicate_of`（L321）要求 `tuple(other.atoms) == tuple(record.atoms)` 且邻接矩阵逐项相同，然后直接 `kabsch_rmsd`（固定下标）。`transform_executor.py` 的模块文档声称"RMSD 只在合法的元素/边保持映射下评估"，但实现里的映射只有恒等映射。 | **缺失（科学）** | 需用户决定（见 0.4） |
| 2 | 映射搜索预算与"无法判定"处理 | 超过节点预算时该簇标记为 `unresolved`，帧全部保留并在报告中说明（`processor.py:463`、`:540-545`、`:637`）。 | 没有映射搜索，也就没有这个状态。 | 随第 1 项 | 随第 1 项 |
| 3 | 多数拓扑过滤 | 默认只保留最大的拓扑簇，其余帧记为 `minority_topology` 删除；`keep_all_topos` 可关闭；有 `unresolved`/`invalid` 簇时跳过该过滤（`processor.py:533-556`）。 | 不过滤：不同拓扑（邻接矩阵不同）的帧各自保留，不会互相合并。 | 行为不同 | 建议不移植。V4 的"各自保留"更保守；是否需要"只留主拓扑"可以让用户在 V4 里用 filter 或后续步骤表达 |
| 4 | 能量窗口 `ewin` | 以最低能量为基准，丢弃高出窗口的帧；无能量的帧绕过过滤（`processor.py:580-615`）。 | 不适用：transform 的输入端口只有结构，没有能量（`transform_executor.py` 模块文档明确写了"stated here, not silently"）。 | 设计不适用 | 如要保留，应作为带能量输入的新 transform 种类，另议 |
| 5 | 虚频过滤 `imag` | 只保留 `num_imag_freqs` 等于指定值的帧（`processor.py:559-578`）。 | 同上，不适用。 | 设计不适用 | 同上 |
| 6 | 能量相关的 RMSD 放宽 | 两帧能量接近时，阈值放大 `ENERGY_RMSD_SCALE_FACTOR = 1.5`（`rmsd_engine.py:62`、`_effective_cutoff:312`）；`energy_tolerance` 参数（默认 0.05）。 | 不适用（无能量）。 | 设计不适用 | 不移植 |
| 7 | 转动惯量（PMI）预筛选与候选排序 | `PMI_TOLERANCE_FACTOR = 0.05`、`_build_candidate_priority`、元素距离指纹，用来先筛掉明显不同的帧（`rmsd_engine.py:61`、`:365`）。 | 没有预筛选，O(n²) 逐对 Kabsch。 | 性能 | 若移植第 1 项，需要重新评估是否要带预筛选，否则图同构搜索在大集合上会很慢 |
| 8 | 保留 ConfGen 的拓扑覆盖 | 读取每帧注释里的 `AddBond`/`DelBond`，重建 ConfGen 当时使用的拓扑（`processor.py:505-513`，`topology.py:213`、`:250`）。 | 只做按 `bond_scale` 的几何成键感知，**不识别** v3 ConfGen 声明的 `topology.bonds`。 | **缺失（科学）**，仅当 ConfGen 使用了人工加键/删键时 | 随第 1 项一并考虑 |
| 9 | 只比较重原子 `noH` | 有。 | 有（`heavy_only`）。 | 等价 | 无需动作 |
| 10 | 数量上限 | `max_conformers`，按能量排序后截断（`processor.py:681-687`）。 | `max_structures`，按 id 顺序截断（`_refine` 末尾）。 | 顺序语义不同 | 无能量时按 id 顺序是合理的；无需动作 |
| 11 | 输出排序与相对能量 | 按能量升序输出，写 `global_min`（`processor.py:666-676`）。 | 按 id 排序，无能量。 | 设计不适用 | 无需动作 |
| 12 | 键感知参数 | `BOND_SCALE_FACTOR = 1.2`。 | 默认 `bond_scale = 1.2`（`REFINE_DEFAULT_BOND_SCALE`，L84），可配置。 | 等价 | 无需动作 |
| 13 | 阈值 | `threshold` 默认 0.25 Å。 | `rmsd_threshold_angstrom` 默认 0.25 Å。 | 等价 | 无需动作 |
| 14 | 科学分组边界 | 无（按拓扑簇）。 | 在 `(charge, multiplicity, group_key, role, 元素序列)` 内去重，不跨组（`_scientific_group`，L93）。 | V4 更严格 | 无需动作 |
| 15 | 报告 | 逐帧记录：状态、重复对象、被哪一步删除及原因，写 `*.report.json`（`processor.py:390-460`、`:722`）。 | 只返回 `notes`（"dropped X as duplicate of Y (rmsd …)"）。 | 可不要 | 无需动作 |
| 16 | 并行 / 进度 / XYZ 读写 / CLI | multiprocessing、进度条、`main()`、`confrefine` 命令。 | 无（纯函数，输入输出是类型化记录）。 | 可不要 | 随 CLI 一起删 |
| 17 | 失败语义 | 以 `RefineResult(False, …, reason)` 返回：`empty_input`、`filtered_to_zero`、`deduped_to_zero`、`report_path_conflict` 等。 | 输入为空集时抛 `DomainError`（`_inputs`，L198-204）；其余失败为类型化 `FAILED` 结果。 | 等价 | 无需动作 |

## 2. 需要你确认的事项

- **Q-R1（关键）：** 对称感知去重——**移植**还是**放弃**？
  - 若移植：把图同构映射部分（`MappingSearch`、`find_isomorphism`、`compare_frames` 里与映射相关的部分、节点预算与 `unresolved` 状态）移到 `confflow/science/`，V4 refine 调用它；这是一张新的 logic 卡，需要 TS1 / engine 报告之外的新 golden（例如本报告第 3 节的实验作为回归用例）。`blocks/refine` 其余内容删除。
  - 若放弃：接受"V4 refine 不合并仅对称等价原子标号不同的构象"，并在文档里写明；`blocks/refine` 整体删除（C5.3）。
- **Q-R2：** 第 8 项（保留 ConfGen 声明的拓扑覆盖）是否随 Q-R1 一起移植？
- **Q-R3：** 第 3 项（多数拓扑过滤）不移植，是否同意？
- 其余条目均为"设计不适用"或"可不要"，不需要决定。

我的建议：**Q-R1 移植**。理由是 v3 的枚举正是按标号区分的（IS.1b 实测 27 个构象两两不能叠合），ConfGen 后接 refine 是典型用法，不合并对称重复会让后续昂贵的量化计算步骤白白多算。但这是一张有科学风险的卡，应当单独做，不要夹在删除批次里。

## 3. 证据：同一对结构在两边的表现

输入：`docs/refactor/paths_equivalence/fixtures_h.json` 中的 n-丁烷（含氢，14 个原子，输入简化分支提交 e21277d）。结构 B 与结构 A 的唯一区别：把第 1 个甲基碳上三个氢（原子序号 4、5、6）的坐标行循环置换，也就是同一个分子、同一几何，只是三个等价氢的标号不同。

```python
Y = X.copy(); a, b, c = [4, 5, 6]; Y[[a, b, c]] = X[[b, c, a]]
kabsch_rmsd(X, Y)                                   # 0.807 Å（固定下标）
TransformExecutor()._refine([A, B], {"rmsd_threshold_angstrom": 0.25})
#   → 保留 ['a', 'b']，notes = []                    # V4：不认为重复
process_xyz(RefineOptions(in.xyz, threshold=0.25))  # 旧 refine，两帧能量相差 1e-6
#   → RefineResult(produced_output=True, kept_count=1, reason='ok')   # 旧：合并为 1 个
```

运行环境：本机 Python 3.12.3，ConfFlow `refactor/diet` b8e85a3，未安装 numba（旧 refine 回退到纯 numpy，结果不受影响）。

## 4. 不影响的事实

- 第 1 项的差距只在"对称等价原子的标号置换"时出现。构象真正不同（二面角不同）的结构，两边都不会合并。
- `blocks/refine` 现在没有任何生产代码入口：唯一的外部引用是 `workflow/composition.py`（给 `confts` CLI 用）和 `confrefine` 命令。V4 工作流不经过它。
- 删除 `blocks/refine`（C5.3）的前置条件仍是"本报告经用户确认"。
