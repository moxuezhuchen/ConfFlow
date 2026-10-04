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
