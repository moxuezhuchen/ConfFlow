# PLAN-2 / 清理进度（2026-10-04）

- D1a `bcfc9d8`（分支 `refactor/cleanup-d1`，基点 afdf9df）：验收通过。删除 25 个无引用文件并同步 TESTING.md；全量 4473/10、golden ok。
- D1b `715c018`（同分支）：验收通过。CHANGELOG 新增 Unreleased 条目（20 个哈希已逐一核对）、README 发布身份段落手工移植（已去掉 `pre-v4-greenfield`）；diff 与补丁逐字相同。
- 已按用户决定删除本地分支 `refactor/v4-diet-pr10-repository-hygiene`（6 个未合并提交；bundle 备份 `/root/refactor-backups/confflow-refactor-v4-diet-pr10-repository-hygiene.bundle`，已验证）。`refactor/cleanup-d1` 尚未推送，等待用户决定。
- A1-2 `6f5bbde`（分支 `refactor/a1-empty-inputs`）：已提交，验收中。A1-3、A1-1 待执行。
- A1-3 `bc2a0b4`（`refactor/a1-empty-inputs`）：验收通过（diff 与补丁一致；全量 4477/10；golden ok）。A1-1 待执行。
- A2-b 补丁已备（分支 `refactor/a2-connectivity-report`，基点 afdf9df，handoff/A2b.patch）：只往 refine 已有的 `notes` 加"键连接与组内多数不同"的条目，不过滤、不改契约。
- A1-1 `f8752e6`（`refactor/a1-empty-inputs`）：验收通过（diff 与补丁一致；全量 4480/10；golden ok；契约/边界摘要不变）。A1 三卡序列完成：A1-2 `6f5bbde` → A1-3 `bc2a0b4` → A1-1 `f8752e6`，未推送，待用户决定。端到端复现（共线 C6，w1 到 w4）：四种工作流均退出码 1、`status: failed`、无回溯，`failures` 首条分别为 `confgen_no_realized_structures`（仅 ConfGen）与 `run_not_executable`（下游步骤被挡住）。
- A2-b：执行器第一次因 `a2` 工作树里有验收方预演遗留的暂存改动而按规则停止（验收方的疏漏，已清理；补丁未变）。待重新传给执行器。
- 推送（2026-10-04，用户："都可以推送"）：`refactor/cleanup-d1`（715c018，D1a + D1b）与 `refactor/a1-empty-inputs`（f8752e6，A1-2/A1-3/A1-1）已推送；推送前历史扫描无密钥、无本地绝对路径、无导出文件（唯一命中是 CHANGELOG 里提到已移除的 `results.db` 读取器）。push 触发的 CI：run 37174742202（d1）与 37174742159（a1），`test-matrix` 3.10 到 3.13 与 `coverage` 全部 success。远端 `main` 仍为 afdf9df。A2-b 未推送（执行器尚未提交）。
- A2-b `801ad07`（`refactor/a2-connectivity-report`）：验收通过（diff 与补丁一致；全量 4477/10；golden ok，契约/边界摘要 5 项不变）。未推送，待用户决定。C1 评估材料已写：`docs/refactor/plan2/C1-evaluation.md`（TS1 上 1.15 与 1.2 的 refine 结果相同；未动代码）。
- 推送（2026-10-04，用户："推送"）：`refactor/a2-connectivity-report`（801ad07，A2-b）已推送；推送前历史扫描无密钥、本地绝对路径或导出文件；push 触发的 CI run 37177174765：`test-matrix` 3.10 到 3.13 与 `coverage` 全部 success。远端 `main` 仍为 afdf9df。
- C1-a（2026-10-04，用户批准 C1-a 方向）：预演完成（临时分支 `plan2/c1a-proto` c94bf38，基点 afdf9df，预演树已清回干净）。独立复核：`REFINE_DEFAULT_BOND_SCALE=1.2`（transform_executor.py:96）、显式 `bond_scale` 覆盖（native.get 默认值）、`working_topology` 逐字优先（`resolve_working_adjacency`，与尺度无关）、`topology_bonds` 路径不经过该默认。TS1 独立重跑：默认(1.15)与显式 1.2 保留集合相同（7/8，budget 1000/200000 一致）；O74–C79 d=1.6853，1.15 阈 1.6445 / 1.2 阈 1.7160。数字：collect 4483→4486、全量 4476/10、golden ok（5 项摘要不变）。产物：`handoff/C1a.patch`、`handoff/C1a.md`、`handoff/C1a-executor-prompt.txt`；执行器工作树 `/opt/cf-worktrees/c1a-exec`（refactor/c1a-bond-scale @ afdf9df，干净，补丁 apply --check 通过）。待执行器提交后验收。

- C1-a 执行提示词复核：固定基点仍为 afdf9df（不跟随正在合并的 main）；预设显式 1.2 留在本卡范围外；修正补丁中过宽的统一尺度注释。验收方在 /tmp 独立应用修订补丁，新测试实跑 3 passed；执行器工作树保持干净、apply --check 通过（USAGE 文件模式警告无变化）。已补全执行命令与固定基点抽样破坏要求。此为提示词准备，不表示执行器提交已验收；全量/golden/TS1 将在提交后独立复跑。

- 合并（2026-10-04，用户“那就合并”，随后明确委派 subagent）：PR #100 D1 → 3185e0b；PR #101 A1 更新基点后 → 13807fdd；PR #102 A2 更新基点后 → d05927cf。各 PR 必需矩阵/coverage 与 JobDesk 契约均通过；未使用 admin、未改保护规则、未删除分支。验收方独立核对三个 PR 均 MERGED，本地/远端 main=d05927cf，两个未跟踪项保留。
- C1-a c4a78d6（基点 afdf9df，refactor/c1a-bond-scale）：最终实现独立验收通过，diff 与修订补丁逐字一致、白名单恰三文件、单提交；ruff/mypy/black 通过，collect 4486；全量 4476 passed / 10 skipped，321s；golden ok（五摘要、TS1 三后端、引擎报告均一致）；抽样破坏恰 1 failed / 2 passed，恢复后 3 passed。TS1 默认/显式1.15/1.2×budget1000/200000共六组均保留7/8，预算1000的未决notes数为20/20/22，预算200000均为0。距离1.6853 Å对应scine_F1；ts1_original为1.6955 Å，各夹具都介于两尺度阈值之间。预设显式1.2保持不变；C1尚未push/开PR/合并，也尚未验证叠加最新main后的完整集成树。

- 验收提速（用户“就这样做，写后续提示词”，外部执行器glm5.3-flash）：按小卡串行，E1仅启用现有加权调度、单worker和正确权限/日志流程；E2分片捕获报告；E3校验来源完整性后golden复用。E1规划预演提示词已备 handoff/E1-planning-prompt.txt；此时尚未生成E1实现补丁或执行器卡片，不能视为已验证提速。E2/E3需先定接口与补丁再交执行器；C1最新main集成及B1/B2/D尚未开展。

- 用户要求“能合并的都合并，现在太慢”：E1/E2/E3合为E-fast一张卡；旧E1提示词作废，改handoff/E-fast-planning-execution-prompt.txt。预演、卡片/补丁、正式执行在同一交付内按顺序完成，最后一次报告；根代理仍独立全量/golden验收。B1/B2按依赖合卡，D类合并兼容零行为项；不把这些代码混进E-fast。本轮仅更新提示词，工具尚未实现。
