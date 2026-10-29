# 架构复审遗留项 · 2026-10-29

来源：`docs/reviews/architecture-review-20261029.md`（N1–N4）、`docs/reviews/pr197-verification-20261029.md`（F1–F3）、`docs/reviews/pr197-final-verification-20261029.md`（最终复核）。N1–N4、F1–F3 已在 PR #197 修复。下列遗留项不阻断合并，也没有发现它们阻断正常工作流。本文件是这些遗留项的长期记录（`/tmp/deferred-fixes.md` 只是本地工作副本）。

| # | 遗留项 | 影响 | 后续工作 | 状态 |
|---|---|---|---|---|
| D1 | `execution/confgen_search_run.py`：`context.native_env` 为 None 时，退回 `effective_native_env(binding, inherit=os.environ)` 读取当前进程环境 | 只影响直接调用执行器；V4 应用总会提供快照 | 缺少快照时明确拒绝，并调整直接调用方（含测试上下文） | 已修（refactor/deferred-20261029） |
| D2 | `execution/confgen_xtb.py`：xtb 的环境由 `dict(os.environ)` 构建 | 正常 worker 已继承快照，再覆盖线程和栈参数；属于接口整理 | 显式接收环境快照，保留线程 / 栈参数的覆盖 | 已修（refactor/deferred-20261029；`XtbCalculator(base_env=...)` 为必填参数） |
| D3 | `execution/batch.py`：`_current_reuse_inputs` / `_stored_reuse_inputs` 中独立的 `artifact_checksums` 字段为 `()` | 正常装配已把绑定 artifact 的 checksum 计入 work-item 输入摘要，已测场景都能拒绝变化（证据：`docs/reviews/data/pr197-final-verification-20261029/artifact-axis.json`） | 已评估（2026-10-29）：**保持现状**。该字段来自 631fe51c（batch.py:702/714）；绑定 checksum 经 assembly.py:827 → artifact.py:228-235 → work_item.py:461-478 进入 work-item 摘要。存储层（work_items.py:691-701）没有保存绑定输入的 checksum，接通需要改 schema；直接 API 构造的身份在所有轴上都由调用方自行声明，接通也挡不住。真正的缺口见 D5 | 已关闭（保持） |
| D4 | 旧 MLIP 契约结果被拒绝时，步骤级 provenance 拒绝的诊断里没有"使用新 run root"提示（run 级拒绝信息里有） | 只影响诊断可读性 | 在步骤级失效诊断中补充提示 | 已修（refactor/deferred-20261029） |
| D5 | 被消费的绑定输入 artifact 从不做字节校验：`verify_artifact` 只在 batch.py:1107 校验 COMPLETED 条目自己的产物 | 同一 run 中上游输出被篡改时，会在其生产步骤被拒绝；只有 run 外部传入的 artifact 输入（`run_inputs.artifacts`，v4_run.py:855）会命中旧字节，而 CLI / 入口目前只传结构 | 需要单独决定：在消费方对绑定输入调用 `verify_artifact`；之后再清理惰性的 `artifact_checksums` 字段 | 待决定 |

不计入遗留：R6 维持"测量后暂不改"；原 review 复现脚本的 tuple 键问题已解决。统一启动描述、科学输出最低契约属于后续架构改进建议（见 `architecture-review-20261029.md` 末尾），尚未实施。
