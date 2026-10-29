# 架构复审遗留项 · 2026-10-29

来源：`docs/reviews/architecture-review-20261029.md`（N1–N4）、`docs/reviews/pr197-verification-20261029.md`（F1–F3）、`docs/reviews/pr197-final-verification-20261029.md`（最终复核）。N1–N4、F1–F3 已在 PR #197 修复。下列遗留项不阻断合并，也没有发现它们阻断正常工作流。本文件是这些遗留项的长期记录（`/tmp/deferred-fixes.md` 只是本地工作副本）。

| # | 遗留项 | 影响 | 后续工作 | 状态 |
|---|---|---|---|---|
| D1 | `execution/confgen_search_run.py`：`context.native_env` 为 None 时，退回 `effective_native_env(binding, inherit=os.environ)` 读取当前进程环境 | 只影响直接调用执行器；V4 应用总会提供快照 | 缺少快照时明确拒绝，并调整直接调用方（含测试上下文） | 待修 |
| D2 | `execution/confgen_xtb.py`：xtb 的环境由 `dict(os.environ)` 构建 | 正常 worker 已继承快照，再覆盖线程和栈参数；属于接口整理 | 显式接收环境快照，保留线程 / 栈参数的覆盖 | 待修 |
| D3 | `execution/batch.py`：`_current_reuse_inputs` / `_stored_reuse_inputs` 中独立的 `artifact_checksums` 字段为 `()` | 正常装配已把绑定 artifact 的 checksum 计入 work-item 输入摘要，已测场景都能拒绝变化（证据：`docs/reviews/data/pr197-final-verification-20261029/artifact-axis.json`） | 先评估：实际文件字节变化、直接 API 构造的身份、独立诊断轴的覆盖；再决定接通该字段还是移除它 | 待评估 |
| D4 | 旧 MLIP 契约结果被拒绝时，步骤级 provenance 拒绝的诊断里没有"使用新 run root"提示（run 级拒绝信息里有） | 只影响诊断可读性 | 在步骤级失效诊断中补充提示 | 待修 |

不计入遗留：R6 维持"测量后暂不改"；原 review 复现脚本的 tuple 键问题已解决。统一启动描述、科学输出最低契约属于后续架构改进建议（见 `architecture-review-20261029.md` 末尾），尚未实施。
