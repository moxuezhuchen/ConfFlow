# 交接（2026-10-04）— 给新会话的上下文

## 我是谁、怎么协作
我（Claude）是规划者和验收方；用户把我写的"卡片 + 补丁 + 提示词"传给一个外部执行器模型执行，再把执行器的报告贴回来；我**独立重跑**一切后才接受（不采信执行器的数字）。**不再启动 subagent**（用户 2026-10-03 指示），只由我写提示词、做验收。

## 仓库与位置
- ConfFlow：`/opt/ConfFlow`（当前检出 `main` = afdf9df，与 origin/main 一致）。JobDesk-v2：`/opt/jobdesk-v2-v4`（当前 `master` = 3addb94）。
- 文档/卡片/补丁：`/opt/cf-worktrees/refactor-plan`，分支 **`docs/refactor-plan2`**（已推送）。旧分支 `docs/refactor-plan` 的远端已删，内容存档为标签 `archive/refactor-plan`（ConfFlow，5cf2000）；**不要往 `docs/refactor-plan` 推**。
- 目录：`docs/refactor/{PLAN.md,LOG.md,STATUS.md,ACCEPTANCE.md,handoff/,tools-acc/,plan2/,plan3/}`；PLAN-2 进度在 `plan2/STATUS.md`；各卡在 `handoff/*.md/.patch/*-executor-prompt.txt`。
- 备份：`/root/refactor-backups/*.bundle`（6 个，已验证）。
- 验收工具：`docs/refactor/tools-acc/run_sharded.py`（全量，必须 `PYTHONPATH=…/tools-acc/noeditable:.`）、`docs/refactor/tools/golden_check.py`（每张卡必须跑，不得省略；基线=baseline 去掉 checkpoints，再叠加 `checkpoints/IS.5/engine_reports` 与 `checkpoints/C4.3/engine_reports`；`--removed-nodes` 文件写一行 `tests/v4/test_confgen_v3_integration.py::TestLegacyRegressions::test_v3_filenames_vs_legacy_compat`）、`run_jd_tests.sh`（JD 测试，绑定 CF 到 /opt/ConfFlow）。JD 只读检出用 `/opt/cf-worktrees/jd-pin`（需要时从 JD 9de35d6 重建）。验收临时目录 `/tmp/acc2/`（可能已清）。

## 已完成（都已合并进 main/master）
重构的全部 PLAN 卡片（见 `docs/refactor/STATUS.md` 总表）；ConfFlow PR #99（afdf9df）与 JobDesk PR #21（3addb94）。PLAN-2 之后又做：D1a/D1b、A1（三层）、A2-b。

## 已推送、等用户合并（互不依赖，均基于 afdf9df，CI 全绿，未开 PR）
- `refactor/cleanup-d1`（715c018）：删 25 个死文件、CHANGELOG（Unreleased 条目）、README 身份段落。
- `refactor/a1-empty-inputs`（f8752e6）：A1-2 `6f5bbde`（必需结构端口只数结构）→ A1-3 `bc2a0b4`（`v4 run` 结构化失败，`failures`，退出码 1）→ A1-1 `f8752e6`（ConfGen 实现 0 个结构则失败，诊断码 `confgen_no_realized_structures`）。
- `refactor/a2-connectivity-report`（801ad07）：refine 把"键连接与组内多数不同"写进 `notes`，不过滤，不改契约。
用户想合并时我可以开 PR（附验收证据），**合并由用户点**（`main` 受保护，我的 `gh pr merge` 曾被权限分类器拦下）。

## 待办（PLAN-2，用户倾向与顺序：A1 → A2 → C1 → B1 → B2，D 类并行）
- **C1**（统一 refine 1.2 与 ConfGen 1.15 默认 `bond_scale`）：材料已写 `plan2/C1-evaluation.md`（TS1 上两种尺度 refine 结果相同；TS1 的 `O74–C79` 只在 1.2 下是键；选项 C1-a 到 C1-d）。**等用户定方向，批准前不动代码。**
- **B1**：`preview_paths` 改收 v3 声明 + 统一预览与 v3 运行时的 `topology_digest`（见 PLAN §13 的 1 与 N+6；两仓成对）。**B2**：JD 路径预览提前报末端原子端点（依赖 B1）。
- **D 类**（零行为，可合并成一两张卡）：`core/bonding.py`、`data.py`、`constants.py` 移入 `science/`；JD 测试的 producer 路径改环境变量；可达性工具补扫 `scripts/`；`SECURITY_MODEL.md` 文件清单核对；`analysis/pes.py` 定位（需用户决定）。
- **触发条件项，不主动做**：v3 `waypoint`、GUI 内移除旧 `confgen.native`、J2.5 离线编辑。
- **PLAN-3**（`plan3/PROPOSAL.md`，ConfGen 组件化 realizer/validator/ledger）：**只是提案，不执行**。用户倾向：Molassembler 暂不引入、TS2 own-arm-trans 只写 UNRESOLVED、默认后端切换单独批准、`research/realization-handoff` 暂不推远端；最终以用户后续明确回复为准。

## 硬规则（务必遵守）
- **`main`/`master`：任何合并、提交、reset 和任何推送都必须先报告并等用户明确批准**（G12）；没有明确指示不动，包括本地。推送只限 `refactor/*` 与 `docs/refactor-plan2`；删远端分支、关 PR、打/推标签都要用户明确指示。
- 每张卡：我先在**临时工作树里预演**并生成补丁，再写卡片（白名单、预期数字、自检命令）和执行器提示词；预演后**必须把工作树清回干净**（教训：A2-b 第一次被执行器停下，就是我留了暂存改动）。
- 执行器硬规则（写进每份提示词）：每条命令显式 `cd`、动手前确认 HEAD 与干净树、提交前核对分支名并贴出、一卡一提交、不 push、HEAD/数字不符就停止报告不要自行修复、不编造输出。
- 验收时：diff 与补丁逐字比较、ruff/mypy/black、全量 `run_sharded.py`、**golden_check**、必要时抽样破坏（"还原一层→测试应失败"）；两仓成对的卡还要核对 JobDesk。执行器报告里有不实之处要如实记录（例：C4.3 提交信息的 Removed-Tests 清单被缩成 1 行，我用 `--amend` 只改信息并记入 LOG）。
- 失败要坦白：自己的疏漏（如验收 C4.3a 时漏跑 golden）要在 LOG 里写明。
- 用户用中文交流；回复要简短、直接说结论，列出需要用户决定的点。
- 用户给的 Co-Authored-By 尾行：`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`；PR 描述末尾 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`。
- 记忆文件：`/root/.claude/projects/-opt-ConfFlow/memory/push-authorization.md`（推送授权与 main/master 规则）。

## 当前未决（等用户）
1. 三条已推送分支是否开 PR/何时合并。
2. C1 选哪个方向。
3. 是否继续 B1/B2/D 类，或收手。
4. 保留的本地分支 `research/realization-handoff`、`repair/refine-audit-quarantine` 的去留（均有 bundle 备份）。
