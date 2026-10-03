# A2 调查报告 — 优化后键连接发生变化的构象，V4 有检查吗？（只读调查）

被测版本：ConfFlow `main` = `afdf9df`。旧 `blocks/refine` 的"多数拓扑过滤"会删掉少数拓扑构象；V4 refine 不过滤。问题是：V4 对"优化之后键连接变了"有没有任何检查或感知？

## 1. 现有检查清单（注册表，`default_registry()`）
```
checks: ['bond_drift', 'frequencies_required', 'geometry_required',
         'imaginary_frequency_count', 'max_rmsd_from_input', 'normal_termination']
```
- `bond_drift`：只检查**用户明确写出的一对原子**的键长漂移；没有 `atoms` 参数时返回 `bond_atoms_missing`（`checks_standard.py:403-455`）。不是连接性检查。
- `max_rmsd_from_input`：整体 Kabsch RMSD 阈值，不区分"成键关系是否变了"。
- 其余四个是终止状态、几何是否存在、频率相关，与连接无关。
- 结论：**没有任何检查比较优化前后的成键关系。**

## 2. 拓扑在计算前后怎么传递（`execution/profile_standard.py:271-290`）
计算输出记录继承的是**在计算前的源几何上解析好的"预期共价图"**（`resolve_and_persist_kwargs(source, source.coordinates)`，注释："resolved once on the pre-change source geometry"）：
- 源记录带有 `working_topology` 或非空 `topology_patch` 时：输出继承这份图，后续 refine/ConfGen 都用它。**优化中真实断键/成键不会被发现**，下游继续按旧图工作。
- 源记录没有这些（纯几何感知）时：输出没有持久化的图，下游按新几何重新感知；连接变了会被"看到"，但没有人把它当作事件报告。
- 所以：V4 对这个问题的现状是**既不检查，也不报告**，且在有持久图时会把差异隐藏。

## 3. 缺口是否真实
真实，但严重程度取决于工作流：TS 搜索、金属配合物（配位键 1.15 与 1.2 的判定边界很敏感，见 C1 证据）里最容易出现；纯有机构象搜索里很少出现。旧路径的多数拓扑过滤在 V4 里没有对应物。

## 4. 可选方案（均未实现，等你选）
| 方案 | 内容 | 代价 |
| --- | --- | --- |
| A2-a | 新增注册表检查 `connectivity_preserved`：把计算输出按同一规则（含 patch）感知的图，与输入的预期图比较，不同则失败/告警；默认不启用，需工作流显式声明（与现有 checks 一致） | 契约会新增一个检查（JobDesk 的校验词汇需要成对确认） |
| A2-b | 在 refine 里报告：同一组中若有结构的感知图与多数不同，写进步骤 notes（只报告，不过滤、不删除） | 不改变科学输出，只增加诊断 |
| A2-c | 保持现状，文档明确写出"V4 不检查优化后连接变化" | 零改动 |

建议：先做 A2-b（零风险、只增加信息），再决定要不要 A2-a。

## 5. 与 C1（统一默认 `bond_scale`）的关系——给你评估用的证据
refine 默认用 1.2，ConfGen 默认用 1.15。在仓库自带的分子上比较两种尺度的感知结果（`perceive_adjacency`，只读）：

| 分子 | 原子数 | 键数@1.15 | 键数@1.2 | @1.2 多出 | @1.2 丢失 |
| --- | --- | --- | --- | --- | --- |
| benzene、butane（3 种构象）、butanol2（2 种）、tbutanol、toluene | 12 到 15 | 与 @1.2 相同 | | 0 | 0 |
| TS1 全部 8 个结构（scine_F1..F6、ts1_original 等） | 122 | 130 | 131 | **1：`O74–C79`** | 0 |

- 普通有机分子两种尺度**完全一致**，统一不会改变结果。
- 在 TS1 上，1.2 多出一条 `O74–C79`，很可能就是反应坐标上的 FORMING/BREAKING 键——ConfGen（1.15）不把它当作键，refine（1.2）当作键。这意味着对 TS 类体系，二者今天看到的"预期共价图"不同；统一到哪一边，会决定 refine 里这条键是否参与对称映射的边保持。
- 所以 C1 的影响集中在 TS/配合物，需要你指定统一到 1.15 还是 1.2（或改成总是从 ConfGen/记录继承）；建议在 A2 的 A2-b 先落地之后，用真实的 TS 工作流各跑一遍再定。
