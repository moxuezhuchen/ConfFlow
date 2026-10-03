# 卡片总表（2026-10-03，Phase 4 与文档收尾之后）

状态：**完成**（已验收）／**取消**／**可选**（默认不做）／**待做**。哈希为提交所在仓库：CF = ConfFlow `refactor/diet-p4`（含合并进来的 `refactor/diet`、`refactor/diet-c5`、`implementation/input-simplification` 的提交），JD = JobDesk-v2 `refactor/diet-p4`。所有提交均未推送（`docs/refactor-plan` 除外）。

## 基线与 JD 兼容（J0 / Phase 0）
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| J0a 移植 manifest 解析器修复 | 完成 | JD 5847bc7 |
| J0b 测试侧修复并重新 vendor fixture | 完成 | JD ee0eabb |
| B0.1 基线与验收工具 | 完成 | CF eec4e80 |
| B0.2 修复 B0.1 的两个工具缺陷 | 完成 | CF 096af5f |

## Phase 1：ConfFlow 外围删除
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| C1.1 合并两个跨仓 CI 工作流 | 完成 | CF fc6b821 |
| C1.2 删除 `_retired_runtime` | 完成 | CF 8eda87f |
| C1.3 删除 workflow 辅助模块与 `confgen_params` | 完成 | CF 445d4f5 |
| C1.4 删除 `_analysis_capabilities` 静默 fallback | 完成 | CF b8e85a3 |

## J1 / Phase 2：JD 删除 V1/V2 合同实现
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| J1 删除兼容性求值器 | 完成 | JD fe85b0d |
| J2.1a 会话默认值改为"无合同" | 完成 | JD df25678 |
| J2.1b 非 V4 服务器报"不可用" | 完成 | JD 4c5f6ec |
| J2.1c 修复启动崩溃 | 完成 | JD 13b6f55 |
| J2.2 测试夹具不再经过 V1/V2 解析器 | 完成 | JD b1ba08d |
| J2.3 V4 仍需要的符号移出 `parse.py` | 完成 | JD c94fcab |
| J2.4 删除 V1/V2 合同实现、file mode、内置快照 | 完成 | JD fedac08 |
| J2.5 离线编辑：缓存最近一次 V4 contract | **可选，不做**（用户 2026-10-03 保持可选） | — |

## Phase 3：边界瘦身（两仓成对）
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| J3.1 删除 presenter 里读取 `capability_identity` 的死分支 | 完成 | JD 26c5ccb |
| J3.2 JD 不再要求 producer 的无读取方身份字段 | 完成 | JD 1ca4052 |
| C3.1 re-pin 到 J3.2 | 完成 | CF 65c4e0c |
| C3.2 producer 删除无读取方的边界成员与摘要 | 完成 | CF e5c3032 |
| C3.3 contract 中标注 ConfGen 溯源字段 | 完成 | CF 8d968dc |
| J3.3 JD 重新 vendor P0 fixture | 完成 | JD edb068a |
| C3.4 re-pin 到 J3.3 | 完成 | CF baeb451 |

## 输入简化分支改造（IS）
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| IS.1 legacy paths 与 v3 paths 等价 golden | 完成 | CF 9340601 |
| IS.1b 修正比较方法并补含氢用例 | 完成 | CF e21277d |
| IS.0 把 Phase 1–3 后的 main 合入 IS 分支 | 完成 | CF b0e8c64（合并）、JD bcdea5b（合并） |
| IS.2 intent 编译器把 legacy paths 编译成 typed v3 | 完成 | CF 2e0295a |
| IS.2c waypoint 的编译期提示补半句 | 完成 | CF 6046b44 |
| IS.2b v3 拒绝末端原子端点时指明具体的键 | 完成 | CF 0a9f28d |
| IS.3 `_wire_confgen` 拒绝非 v3 native | 完成 | CF c094af4 |
| IS.4 JD 输入简化分支改用 typed v3 | 完成 | JD 49f9d37 |
| IS.5 把 IS 分支合入 main | 已合入**本地** main/master（CF 7d93fae、JD 2c7e121），**用户未批准合并进 main/master**，保持本地、不推送；正式合并由用户在 GitHub 上从 `refactor/diet-p4` 开 PR 完成 | — |

## Phase 4（含用户改序后的卡）
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| C4.1 测试构造器默认改为 v3 | 完成 | CF c6b88ff |
| J4.1 + C4.2（合并为"C4.3 之后的 JD 配对卡"）：JD 删 `confgen.native` 字段/控件；CF 钉住 J4.1' | 完成 | JD 9de35d6（J4.1'）；CF 1de64b6（C4.2） |
| C4.3a（新增）typed 版路径测试 | 完成 | CF 23251fd |
| C4.3 删除 legacy native 执行路径与 schema | 完成 | CF 250f947 |
| C4.4 删除 `science/torsion.py` 的 chain 函数 | 完成 | CF ea93daa |
| C4.5（新增）删除 `build_chain_rotors`、`cut_component` 包装（零调用方）及 C4.3 之后无人引用的 6 个测试辅助函数 | 完成 | CF 888f872 |
| C4.6（新增）删除 `architecture_metrics.py` 里针对已删除对象（calc、V1/V2/V3 wire）的 31 个全为 0 的统计键及其代码，保留键的值逐值不变，并更新两个脚本的说明 | 完成 | CF 015a174 |
| C4.7（新增）文档收尾（10 个文件：把 docs-rewrite 的内容应用到最终树，更新 CONFGEN_PATHS 旧段落与 SECURITY_MODEL 的 results.db） | 完成 | CF 4900cb8 |
| C4.7b（新增）WORKFLOW_V4.md 顶部加"历史设计记录"说明（+2 行） | 完成 | CF 59465df |
| C4.8（新增）删除运行已不存在测试文件的 numba CI 作业，及无人使用的 `tests/_helpers.py` | 完成 | CF b9b9797 |

## Phase 5：calc / 遗留 CLI / core 清理
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| D11 DECISIONS.md 追加 D011（取代 D010） | 完成（非 git；sha256 前 f0f6540b…，后 aba1dacc…） | — |
| C5.1 refine 差异报告 | 完成（用户已确认各项） | docs/refactor-plan 4c80857 |
| C5.2 删除 `calc/`、`confts.py`、`composition`、`blocks/viz` | 完成 | CF 2f95dc1 |
| C5.2b 删除 `confflow export` | 完成 | CF a261bbf |
| C5.3a 图同构映射搬入 `science/` | 完成 | CF da047c0 |
| C5.3b V4 refine 调用对称映射去重 | 完成 | CF efbaecd |
| C5.3c-1 refine 的 `topology_bonds` 参数 | 完成 | CF 6521581（后续修正 f8a1f75） |
| **C5.3c-2 编译器自动复制拓扑** | **取消**（用户 2026-10-03 确认）。依据：refine 无 `topology_bonds` 时按记录的 `working_topology`、否则几何感知建图，不是固定索引；含甲基氢互换的丁烷用例两种情况结果相同（RMSD 0.0000，对照固定索引 0.671）；spec 里声明的拓扑不会写入输出记录，反应边/配位边体系需手写 `topology_bonds`（已写入 USAGE） | — |
| C5.3d + C5.4 删除 `blocks/refine` 与 `blocks/confgen`（C5.4 并入） | 完成 | CF deadf46 |
| C5.5 删除只被 legacy 使用的 `core/` 与 `shared/` 部分 | 完成 | CF da44bf5 |
| C5.5a（新增）删除 `workflow/step_naming.py` | 完成 | CF aa920b8 |
| C5.6 + C5.7 + C5.8 进程识别、文档、架构测试收尾（三张卡合并为一个提交，见下方证据） | 完成 | CF b84f84b（合并提交，父 da44bf5；11 个文件，+13/−828；Removed-Tests 6） |

## 逻辑尾部
| 卡 | 状态 | 提交 |
| --- | --- | --- |
| L1 `_allowed_pairings_by_kind` 读常量 | 完成 | CF b55b476 |
| L2 validation token 改为 content / contract / target | 完成 | JD 4b9ed1c |

## C5.6 / C5.7 / C5.8 三张卡各自的验收证据（合并提交 b84f84b）
- **C5.6（`cli.py` 清理）**：`confflow/cli.py` 的进程识别集合由 `{"confflow", "confts", "confgen", "confrefine", "confcalc"}` 改为 `{"confflow"}`，并删除关于 `confcalc` 的 6 行历史注释（`git show b84f84b -- confflow/cli.py`：8 行改动，−7 +1）。当前树：`grep -nE "confts|confgen|confrefine|confcalc" confflow/cli.py` 无命中；没有测试断言过这些名字，合并时全量无失败（3849 passed / 10 skipped）。
- **C5.7（文档）**：README 删除三个已删命令的命令表与 `confgen` 示例；`COMMAND_REFERENCE.md` 删 `confgen`/`confrefine`/`confts` 三节；`USAGE.md`、`ARCHITECTURE.md`、`DEVELOPMENT.md`、`TESTING.md`、`architecture/WORKFLOW_V4.md` 删去已删工具的描述；`docs/KEYWORD_REFERENCE.md` 删除。当前树：`ls docs/KEYWORD_REFERENCE.md` 不存在；卡片里的 grep（`confts|confrefine|confflow.calc|blocks/refine|CalcStepRunner|KEYWORD_REFERENCE`）在 C4.7 之前的 015a174 无命中；C4.7 之后有 1 处命中，位置是 `docs/ARCHITECTURE.md` §8"已退役"一句，那一句是说明这些东西已被删除，属有意保留。
- **C5.8（架构测试）**：`tests/v4/test_architecture_boundaries.py` 与 `scripts/v4_arch_scan.py` 的 `FORBIDDEN_IMPORT_PREFIXES` 去掉对已不存在的包 `confflow.calc`、`confflow.blocks`、`confflow.confts` 的三个条目（`git show b84f84b -- scripts/v4_arch_scan.py`：−3）；删除整个对不存在的包恒为真的 `TestLegacyToolingBoundary` 类（6 个参数化节点，清单 handoff/C5.6-8-removed-tests.txt）。当前树：`git grep -n "TestLegacyToolingBoundary" -- tests` 无命中。

## 登记到 PLAN-2（本轮不做）
`preview_paths` 改收 v3；JD 测试 producer 路径改环境变量；v3 发布 0 个结构的行为；优化后键连接变化的检查；`core/bonding.py`、`data.py`、`constants.py` 移入 `science/`；refine 与 ConfGen 默认 `bond_scale` 统一；v3 `waypoint`；`analysis/pes.py` 定位；可达性工具补扫 `scripts/`；路径预览改接 v3 并统一 `topology_digest`；GUI 内移除/迁移旧 `confgen.native`；`SECURITY_MODEL.md` 文件清单核对（详见 PLAN §13 的 1–4 与 N 到 N+8）。

## 推送与合并（尚未做）
- 推送两个 `refactor/diet-p4`（先 JD 后 CF，CI 全绿才报告）：用户 2026-10-03 批准，时机为 C4.7、C4.7b、C4.8 验收且本表确认无漏项之后；历史扫描已做，用户选"按现状推送"。
- `main` / `master`（含本地 f8a1f75、2c7e121）不动；用户在 GitHub 上从 `refactor/diet-p4` 开 PR 自己合并。
