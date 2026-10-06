# FIX1D-F-ledger DESIGN (proto, parent c9bbc9f8cc0ae94875aaaab180c8488f7ea81f25)

## 1. 定点反例（已核实）
- 真实根反例：`/tmp/fix1d-final-root-run-v2/capture/engine_reports/tests_v4_test_confgen_v3_core.py_test_suppression_positive_control_real_stage_fewer_attempts__0.json`
  - `start_statistics.input.attempts=20`, `attempt_ledger.issued_attempts=18`, `suppressed_skipped=12`
  - 差 2 = 首次已 issued（input 求解失败）后因新成功 representative 在 retry pass 被抑制，最终 `status=suppressed` 抹去历史。
  - `accounting.py:342 attempt_ledger_counts` 定义明确 suppressed 是 `realization skipped by witness`（从未 issued），不可把实际 issued 当 skip。
- 最小 spy（kernel 直调，`run_kernel` + `stage_overrides`，见输出证据）：
  - 3 target：`000000` input 失败→retry 愈合为 published；`000001` input 失败（rep `000000` 当时未 published 故抑制拒绝）→retry 时 rep 已 published 故抑制成功；`000002` input published。
  - `realize_calls=[000000,000001,000002]`（3 次 issued），最终记录 `000000 published, 000001 suppressed, 000002 published`，旧 ledger `issued=2/skipped=1`，缺口 1。
  - 调用链：`run_level(batch)` → `_expand_target`（先 `_try_suppression`，后 input 门 `stage.realize`）→ `_retry_level` 对 retryable 再次 `_expand_target`（先抑制门，后 solver）→ 抑制成功时 `_supersede_failure(old_idx)` 删除旧失败记录，`failed_counts` 回滚，历史丢失。

## 2. 不变量（PLAN FIX1D + 本卡）
- 已 REALIZED target 记录逐字节不变；TS1 REALIZED 只增不减（PLAN §FIX-1D）。
- 旧未重试报告字节不变；旧已 REALIZED target 字节不变（V27）。
- `certificate`/`leaf_certificate`/`verify_*` 终态方程语义不变：leaf 仍是终态划分（published/terminal-fail/reject/suppressed/deferred），certificate digest 仍 over 终态记录。
- 重试多起点求解次数（`start_statistics`：每 start 一行，`attempts/solve_successes/accepted`）与 target 级 issued 严格区分：前者已在 `scope.component_statistics`，不混入 leaf/ledger。
- 禁止只改 basis 粉饰计数；禁止删旧测试/改容差；取消和异常记账不遗漏（`stage_error` 仍 `FAILED_NUMERICAL` 且不可重试；`EngineCancelledError` 在 input 门按旧语义记 `stage_error`，在 retry 门直接传播无 partial；probe 前未 issued 不记，probe 中已 issued 必留痕）。

## 3. 最小通用 kernel 解法（不认识 coordination）
- 保留每 `(parent_target_id, axis, ordinal)` 首次 issued 历史，与最终 terminal 分类分离。
- 历史来源：engine 真实 solve 门（input 门进入前 + retry solver 返回非 None 时），每 target/parent/level 至多 1 条，不按 solver 内部多起点展开（多起点细节只在 `start_statistics`）。
- 分类表（`attempt_ledger_counts(records, issued_history=None)`，`issued_history` 为上述 key 集，`None` = 旧行为）：
  | 终态 status | 曾 issued? | ledger 归类 |
  |---|---|---|
  | EXPANDED / PUBLISHED_LEAF | 是（必） | `issued`（`geometry_successful`，`by_status[status]`） |
  | FAILED_* / UNRESOLVED / UNSUPPORTED（含 `stage_error`） | 是（必） | `issued`（`attempted_without_success`，`by_status[status]`） |
  | REJECTED_BY_POLICY | 否（门前拒绝） | `policy_screened`，非 issued |
  | DEFERRED_* range | 否（从未 issued） | `deferred_unissued`，非 issued |
  | SUPPRESSED_BY_VERIFIED_SYMMETRY | 否 | `suppressed_skipped`（真 skip），非 issued |
  | SUPPRESSED_BY_VERIFIED_SYMMETRY | 是 | `issued`（`attempted_without_success`，`by_status[suppressed]`），另计 `suppressed_after_issue`；不计入 `suppressed_skipped` |
- 报告：
  - `attempt_ledger`：`issued_attempts=successful+without_success（含 suppressed_after_issue）`；`suppressed_skipped=总 suppressed - suppressed_after_issue`；`by_status` 对 issued 的 suppressed 加 `suppressed_by_verified_symmetry` 条目；仅当 `suppressed_after_issue>0` 才新增 `suppressed_after_issue` 键，否则不新增键（旧未重试报告逐字节不变）。
  - `realization.realization_attempts`：由 `attempted - suppressed` 改为 `attempted - suppressed_skipped(true skip)`（即 `published+failed+rejected+suppressed_after_issue`），仅缺口 case 变化（旧无缺口不变）；`realization.suppressed/attempted/count_details/leaf_certificate/certificate` 终态语义不变。
  - `basis` 文案只澄清 suppressed_after_issue 定义，不粉饰计数。
- 取消/异常：历史在 input 门 `stage.realize` 调用前已入账，故抛异常（含 input 门 `EngineCancelledError` 按旧语义记 `stage_error`）仍保留 issued；probe 在目标前触发则无历史（正确未 issued）；retry 门取消直接传播（无 partial run），历史已有 input 条目不丢。

## 4. 改动白名单（本卡）
- `confflow/science/confgen/accounting.py`：`attempt_ledger_counts` 加可选 `issued_history`（缺省 `None` 旧行为），实现上表 + 条件 `suppressed_after_issue` 键。
- `confflow/science/confgen/engine.py`：`_RunState` 加通用 `_issued_history:set`（无轴名字面量，无组件 import），input 门 + retry solver 非 None 时入账，`finish()` 传入 accounting 并按真 skip 重算 `realization_attempts`。
- `kernel_records.py`：不动。
- 组件实现（`coordination/*`、`registry`、`wire`、`model`、`planner`、容差、fixtures）：不动。
- 新测试（针对性）：`tests/v4/test_confgen_ledger_suppressed_after_issue.py`（spy kernel 直调复现 + 真实 C2 抑制报告差分断言 + 无重试逐字相同 + 取消/异常不遗漏）。
- 本卡 docs：`docs/confgen-fix/checkpoints/F-ledger/DESIGN.md`（本文件）、`CARD.md`、`DIFF.md`、`MANIFEST.json`、`LOG.md` 片段（`docs/confgen-fix/LOG.md` 追加 F-ledger 条目）。
