# PLAN-2 / 清理进度（2026-10-04）

- D1a `bcfc9d8`（分支 `refactor/cleanup-d1`，基点 afdf9df）：验收通过。删除 25 个无引用文件并同步 TESTING.md；全量 4473/10、golden ok。
- D1b `715c018`（同分支）：验收通过。CHANGELOG 新增 Unreleased 条目（20 个哈希已逐一核对）、README 发布身份段落手工移植（已去掉 `pre-v4-greenfield`）；diff 与补丁逐字相同。
- 已按用户决定删除本地分支 `refactor/v4-diet-pr10-repository-hygiene`（6 个未合并提交；bundle 备份 `/root/refactor-backups/confflow-refactor-v4-diet-pr10-repository-hygiene.bundle`，已验证）。`refactor/cleanup-d1` 尚未推送，等待用户决定。
- A1-2 `6f5bbde`（分支 `refactor/a1-empty-inputs`）：已提交，验收中。A1-3、A1-1 待执行。
- A1-3 `bc2a0b4`（`refactor/a1-empty-inputs`）：验收通过（diff 与补丁一致；全量 4477/10；golden ok）。A1-1 待执行。
- A2-b 补丁已备（分支 `refactor/a2-connectivity-report`，基点 afdf9df，handoff/A2b.patch）：只往 refine 已有的 `notes` 加"键连接与组内多数不同"的条目，不过滤、不改契约。
- A1-1 `f8752e6`（`refactor/a1-empty-inputs`）：验收通过（diff 与补丁一致；全量 4480/10；golden ok；契约/边界摘要不变）。A1 三卡序列完成：A1-2 `6f5bbde` → A1-3 `bc2a0b4` → A1-1 `f8752e6`，未推送，待用户决定。端到端复现（共线 C6，w1 到 w4）：四种工作流均退出码 1、`status: failed`、无回溯，`failures` 首条分别为 `confgen_no_realized_structures`（仅 ConfGen）与 `run_not_executable`（下游步骤被挡住）。
- A2-b：执行器第一次因 `a2` 工作树里有验收方预演遗留的暂存改动而按规则停止（验收方的疏漏，已清理；补丁未变）。待重新传给执行器。
