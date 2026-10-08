# Architecture Diet 1 — docs/refactor 归档摘要

第一轮架构瘦身（B0.1–IS.1b 及 PLAN-2 验收记录）的工作树归档。

- **archive_parent**（完整 SHA）：`671c3fb14663e9a6f4ccf9880228c59d28fbb762` —— 该提交完整保留被归档的 `docs/refactor/`，是本 manifest 与 bundle 的唯一来源；历史对象不改写，不打 tag。
- **manifest**：`docs/archive_manifests/architecture_diet_1.json`（覆盖父提交中 `docs/refactor/` 下全部 134 个文件：path、git mode、blob、内容 sha256）。
- **仓库外 bundle**：`/tmp/l0-archive-output/architecture-diet-1.bundle`（sha256 见 manifest 内 `bundle.sha256`；`git bundle verify` 通过，含 archive_parent 及其全部祖先，可独立恢复）。
- 说明：删除工作树文件缩小工作树、搜索范围与模型上下文，**不缩小 git 历史**；本卡目的是局部性。历史定位一律用 git 对象（提交/blob SHA），不用工作树路径。

## 补充：Plan2 / 第一轮瘦身的过程记录（2026-10-08）

`docs/refactor/` 的后期过程记录（总表 `STATUS.md`、验收标准 `ACCEPTANCE.md`、`LOG.md`、`PLAN.md` 及每张卡的 handoff 文档、执行提示词与补丁，共 167 个文件）原先只在本地分支 `docs/plan2-e1`（含 `docs/refactor-plan`）上。现在它们的提交已通过 `ours` 合并留在 main 的历史里，**工作树不恢复**，与上面的归档决定一致。

- 位置：`docs/archive_manifests/architecture_diet_1_records.json`（`archive_tip` 为完整 SHA，列出每个文件的 mode、blob、sha256）。
- 读取：`git show <archive_tip>:docs/refactor/STATUS.md`，或 `git show <archive_tip>:docs/refactor/handoff/C4.1.md`。
