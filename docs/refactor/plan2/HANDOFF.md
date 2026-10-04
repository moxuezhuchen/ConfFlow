# 交接（2026-10-04）— 给新会话的上下文

## 我是谁、怎么协作
我（Claude）是规划者和验收方；用户把我写的"卡片 + 补丁 + 提示词"传给一个外部执行器模型执行，再把执行器的报告贴回来；我**独立重跑**一切后才接受（不采信执行器的数字）。默认不启动 subagent；用户本轮明确委派合并任务给 subagent，默认模型 `gpt-6-luna`、effort `max`。规划及验收仍由根代理负责。

## 仓库与位置
- ConfFlow：`/opt/ConfFlow`（当前检出 `main` = d05927cf，与 origin/main 一致）。JobDesk-v2：`/opt/jobdesk-v2-v4`（当前 `master` = 3addb94）。
- 文档/卡片/补丁：`/opt/cf-worktrees/refactor-plan`，分支 **`docs/refactor-plan2`**（远端66f3456；本轮规划/提示词/验收记录有本地未推提交）。旧分支 `docs/refactor-plan` 的远端已删，内容存档为标签 `archive/refactor-plan`（ConfFlow，5cf2000）；**不要往 `docs/refactor-plan` 推**。
- 目录：`docs/refactor/{PLAN.md,LOG.md,STATUS.md,ACCEPTANCE.md,handoff/,tools-acc/,plan2/,plan3/}`；PLAN-2 进度在 `plan2/STATUS.md`；各卡在 `handoff/*.md/.patch/*-executor-prompt.txt`。
- 备份：`/root/refactor-backups/*.bundle`（6 个，已验证）。
- 验收工具：`docs/refactor/tools-acc/run_sharded.py`（全量，必须 `PYTHONPATH=…/tools-acc/noeditable:.`）、`docs/refactor/tools/golden_check.py`（每张卡必须跑，不得省略；基线=baseline 去掉 checkpoints，再叠加 `checkpoints/IS.5/engine_reports` 与 `checkpoints/C4.3/engine_reports`；`--removed-nodes` 文件写一行 `tests/v4/test_confgen_v3_integration.py::TestLegacyRegressions::test_v3_filenames_vs_legacy_compat`）、`run_jd_tests.sh`（JD 测试，绑定 CF 到 /opt/ConfFlow）。JD 只读检出用 `/opt/cf-worktrees/jd-pin`（需要时从 JD 9de35d6 重建）。验收临时目录 `/tmp/acc2/`（可能已清）。

## 已完成（都已合并进 main/master）
重构的全部 PLAN 卡片（见 `docs/refactor/STATUS.md` 总表）；ConfFlow PR #99（afdf9df）与 JobDesk PR #21（3addb94）。PLAN-2 之后又做：D1a/D1b、A1（三层）、A2-b。

## PLAN-2 已合并（原实现基于 afdf9df）
- `refactor/cleanup-d1`（715c018）：删 25 个死文件、CHANGELOG（Unreleased 条目）、README 身份段落。
- `refactor/a1-empty-inputs`（f8752e6）：A1-2 `6f5bbde`（必需结构端口只数结构）→ A1-3 `bc2a0b4`（`v4 run` 结构化失败，`failures`，退出码 1）→ A1-1 `f8752e6`（ConfGen 实现 0 个结构则失败，诊断码 `confgen_no_realized_structures`）。
- `refactor/a2-connectivity-report`（801ad07）：refine 把"键连接与组内多数不同"写进 `notes`，不过滤，不改契约。
本轮用户明确授权由我合并。三 PR 已合并：#100 D1=3185e0b、#101 A1=13807fdd、#102 A2=d05927cf；CI/契约通过后普通merge，未绕过保护。新合并仍须单独明确授权。

## 待办（PLAN-2，用户倾向与顺序：A1 → A2 → C1 → B1 → B2，D 类并行）
- **C1-a** 已执行并独立验收通过：`refactor/c1a-bond-scale` c4a78d6（基点afdf9df），全量4476/10、golden ok、静态及抽样破坏通过。默认1.15；预设显式1.2保持不变。尚未推送/开PR/合并；叠加最新main后的集成验证仍需做。详见STATUS/LOG。
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
1. C1-a 是否进入更新基点、集成验证和推送/PR阶段。
2. refine预设显式1.2是否另卡调整。
3. 是否继续 B1/B2/D 类，或收手。
4. 保留的本地分支 `research/realization-handoff`、`repair/refine-audit-quarantine` 的去留（均有 bundle 备份）。

## 本轮验收提速与交接约束
- Black 使用 `--workers 1`，宿主权限运行会检查 /opt/g16 的测试，避免沙箱PermissionError导致收集失败。不得靠跳过测试解决。
- 验收原始证据 `/tmp/c1a-accept-8go9pcgm/`，全量321秒，weights.json可供后续加权分片使用。
- 提示词、补丁须钉文档提交/摘要，外部执行开始后不并发修订；修订需先通知停止，再重新交付。

## 外部执行模型与任务量
- 外部执行器为 `glm5.3-flash`（用户明确）；subagent默认仍为 `gpt-6-luna/max`，两者不要混淆。
- 提速任务按用户最新要求合并为E-fast一张卡，配置/捕获/golden复用在卡内分步完成；预演与正式执行一次交回，根代理独立验收。旧E1提示词作废，不把B1或C1集成混入。

## 最新验收与任务范围（优先于上文旧要求）
用户明确要求必要测试先行、最后集成一次全量/golden；执行器报告仍独立复核。每卡只记阶段通过，最终通过需要集成全量对应最终树。详见 `plan2/ACCEPTANCE_BATCH_POLICY.md`。E1已在执行，原45ace94提示词逐字恢复，不改本轮E1；后续只合E2/E3。旧E-fast提示词已停用。
