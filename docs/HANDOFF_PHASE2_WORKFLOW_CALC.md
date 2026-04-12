# 阶段 2 Handoff / Recovery 文档（最终收口版）

## 1. 当前代码状态快照

### 已真正落地并通过验证的内容
- 已引入 `CalcTaskConfig` 及相关结构化配置对象，且 `build_structured_task_config()` 已改为**直接从 typed YAML values 构建**，不再经过 legacy dict round-trip。
- `build_task_config()` / `create_runtask_config()` 继续作为 **legacy compat 导出层**，对外签名与输出保持不变。
- `workflow -> calc` 已完成双轨接线：
  - `run_calc_step()` 用 legacy config 做 `validate / prepare / record signature`
  - 同时把 structured config 传给 `ChemTaskManager(..., execution_config=...)`
- `ChemTaskManager` 已显式区分：
  - `manager.config`：公开真源
  - `manager.compat_config`：当前是 `manager.config` 的别名
  - `manager.execution_config`：execution lane 的 structured 补充视图
- `auto_clean` 已完成 bool/string 兼容化，不再因 `bool.lower()` 崩溃。
- structured builder 已保留显式 `input_chk_dir`，并支持 `chk_from_step` fallback。
- cleanup 解析已补齐：
  - `build_structured_task_config()` 会保留 `clean_params / clean_opts` 的阈值语义，至少覆盖 `threshold / ewin / energy_tolerance`
  - `manager._resolve_effective_clean_opts()` 优先级为：
    1. `manager.config["clean_opts"]`
    2. `execution_config.cleanup`
    3. 默认 `-t 0.25`

### 阶段 2 最终收口（2026-04-12）
- **R10 已修复**：`compute_calc_config_signature()` 调用 `resolve_effective_auto_clean()` 获取有效 cleanup 语义，并在 `auto_clean_enabled=True` 时 overlay 到 signature_view。
- **R11 已修复**：`run_calc_step()` 顺序已调整为：
  1. 构建 legacy config
  2. `validate_calc_config()`
  3. **构建 structured config（可能因 cleanup 参数非法而失败）**
  4. compute input signature
  5. **`prepare_calc_step_dir()`（仅在 structured config 构建成功后执行）**
- **一致性测试已补齐**：
  - `test_config_hash_matches_auto_clean_effective_semantics`：验证 `.config_hash` 与 `_run_auto_clean()` 使用相同的 effective cleanup 语义
  - `test_dual_lane_clean_opts_update_syncs_signature_and_auto_clean`：验证 `manager.config.update({"clean_opts": ...})` 后 signature 和 auto-clean 同步使用更新后的值
- **测试验收通过**：708 个测试全部通过，mypy 和 ruff 检查通过。

### 尚未推进的内容（留待后续阶段）
- TaskRunner / policy / setup 的结构化消费。
- 异常体系调整、运行时重构、大模块拆分。

## 2. 当前稳定原则

- `legacy config` 仍是 **compat/signature 基线**。
- `structured config` 当前只用于 **execution lane**。
- calc step signature **已覆盖所有会影响实际 auto-clean 行为的有效 cleanup 语义**。
- structured config 参数有效性确认**在删除旧 step 工件之前完成**。
- `manager.config` 是公开真源；构造后对 `manager.config.update(...)` 的修改，compat/signature 路径和 execution 路径都必须可见。
- execution lane 可以逐步消费 structured config，但不能绕开 compat/signature 的稳定基线。

## 3. 当前配置与执行链路现状

### workflow 侧
- `build_task_config(...)`：生成 legacy compat dict
- `build_structured_task_config(...)`：生成 `CalcTaskConfig`
- `run_calc_step(...)` 当前行为：
  - legacy config -> `ConfigSchema.validate_calc_config(...)`
  - **structured config -> `build_structured_task_config(...)`（先验证参数有效性）**
  - legacy config + structured config -> `prepare_calc_step_dir(...)`
  - structured config -> `ChemTaskManager(settings=legacy, execution_config=structured)`
  - legacy config + structured config -> `record_calc_step_signature(...)`

### calc / manager 侧
- `manager.config`：公开真源，当前也是 compat/signature 可见对象
- `manager.compat_config`：`manager.config` 的别名
- `manager.execution_config`：structured execution 视图
- `run()` 当前：
  - `prepare_calc_step_dir(..., self.compat_config, execution_config=self.execution_config, ...)`
  - `record_calc_step_signature(..., self.compat_config, execution_config=self.execution_config, ...)`
  - task building / execute / recovery 仍主要经由 dict-like config 兼容
- `auto-clean` 当前：
  - 运行前先求最终有效 `clean_opts`（通过 `resolve_effective_auto_clean()`）
  - 最终统一走 `_parse_clean_opts(...)`

## 4. Review 问题总台账（Chronological Issue Ledger）

### R1
- `issue_id`：R1
- `首次出现背景/轮次`：阶段 2 第一小步第一次 review
- `问题摘要`：legacy calc dict round-trip 不稳定，导致旧 work_dir 的 resume/skip 兼容性被破坏
- `影响范围`：`confflow/workflow/task_config.py`，workflow calc signature / stale 判定
- `严重级别`：P1
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - `build_task_config()` / `create_runtask_config()` 退回直接使用 legacy builder
  - 不再经过 structured -> legacy round-trip

### R2
- `issue_id`：R2
- `首次出现背景/轮次`：阶段 2 第一小步第一次 review
- `问题摘要`：structured builder 先走 legacy builder，typed YAML 值在进入结构化模型前被字符串化
- `影响范围`：`confflow/workflow/task_config.py`
- `严重级别`：P2
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - `build_structured_task_config()` 改为直接从 typed YAML values 构建
  - 保留 `blocks` / `gaussian_link0` / `gaussian_modredundant` / `allowed_executables` 的 typed 形态

### R3
- `issue_id`：R3
- `首次出现背景/轮次`：workflow→calc 第一版 handoff review
- `问题摘要`：workflow calc hash 被 structured config 覆盖，导致下一次运行把旧 step 误判为 stale
- `影响范围`：`confflow/workflow/step_handlers.py`，`confflow/calc/manager.py`
- `严重级别`：P1
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - manager 引入 dual-lane 接口
  - `prepare_calc_step_dir()` / `record_calc_step_signature()` 重新绑定到 compat/legacy config
  - 后续通过 R10 进一步完善 signature 覆盖 effective cleanup 语义

### R4
- `issue_id`：R4
- `首次出现背景/轮次`：workflow→calc 第一版 handoff review
- `问题摘要`：`auto_clean` 为 bool 时仍调用 `.lower()`，成功执行后会崩溃
- `影响范围`：`confflow/calc/manager.py`
- `严重级别`：P1
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - `auto_clean` 判断统一走 `_is_enabled_flag(...)`

### R5
- `issue_id`：R5
- `首次出现背景/轮次`：workflow→calc 第一版 handoff review
- `问题摘要`：structured lane 丢失显式 `input_chk_dir`
- `影响范围`：`confflow/workflow/task_config.py`
- `严重级别`：P2
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - structured builder 的 `input_chk_dir` 优先级改为：
    1. step 显式值
    2. global 显式值
    3. `chk_from_step` fallback

### R6
- `issue_id`：R6
- `首次出现背景/轮次`：manager 分轨后的第一次 review
- `问题摘要`：`manager.config.update(...)` 后 compat/signature 路径与公开 config 失同步
- `影响范围`：`confflow/calc/manager.py`
- `严重级别`：P1
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - `manager.config` 恢复为公开真源
  - `manager.compat_config` 改为 `manager.config` 别名

### R7
- `issue_id`：R7
- `首次出现背景/轮次`：manager 分轨后的第一次 review
- `问题摘要`：structured cleanup 没有真正驱动 auto-clean，仍只读 flat `clean_opts`
- `影响范围`：`confflow/calc/manager.py`
- `严重级别`：P2
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - 新增 `_resolve_effective_clean_opts()`
  - structured cleanup 可生成等价 legacy `clean_opts`
  - 后续通过 R10 进一步完善 signature 覆盖 effective cleanup 语义

### R8
- `issue_id`：R8
- `首次出现背景/轮次`：cleanup 语义第一次 review
- `问题摘要`：`clean_params` 在 structured builder 中丢失阈值语义，退回默认 `-t 0.25`
- `影响范围`：`confflow/workflow/task_config.py`
- `严重级别`：P1
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - structured builder 现在会解析 `clean_params / clean_opts` 风格字符串
  - 保留 `threshold / ewin / energy_tolerance`

### R9
- `issue_id`：R9
- `首次出现背景/轮次`：cleanup 语义第一次 review
- `问题摘要`：`manager.config.update({"clean_opts": ...})` 后，auto-clean 仍优先使用旧的 structured cleanup
- `影响范围`：`confflow/calc/manager.py`
- `严重级别`：P2
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - `_resolve_effective_clean_opts()` 优先级改为：
    1. `manager.config["clean_opts"]`
    2. `execution_config.cleanup`
    3. 默认值

### R10
- `issue_id`：R10
- `首次出现背景/轮次`：最新 review
- `问题摘要`：structured cleanup 未进入 calc step signature/hash；相同 hash 可能对应不同 auto-clean 行为（历史问题，已修复）
- `影响范围`：`confflow/calc/manager.py`，`confflow/calc/step_contract.py`，workflow->calc dual-lane handoff
- `严重级别`：P1
- `当前状态`：**已修复并验证通过（2026-04-12）**
- `已实施的修复内容`：
  - `compute_calc_config_signature()` 调用 `resolve_effective_auto_clean()` 获取有效 cleanup 语义
  - 在 `auto_clean_enabled=True` 时将 effective cleanup overlay 到 signature_view
  - signature 与 auto-clean 使用相同的优先级规则
- `验证测试`：
  - `test_config_hash_matches_auto_clean_effective_semantics`
  - `test_dual_lane_clean_opts_update_syncs_signature_and_auto_clean`
  - `test_calc_step_cleanup_change_triggers_stale`
  - `test_calc_step_only_execution_cleanup_change_triggers_stale`

### R11
- `issue_id`：R11
- `首次出现背景/轮次`：最新 review
- `问题摘要`：`step_handlers` 先清理旧工件，再构建 structured config；若 cleanup 参数非法，会在报错前先删掉旧 step 工件
- `影响范围`：`confflow/workflow/step_handlers.py`
- `严重级别`：P2
- `当前状态`：**已修复并验证通过（2026-04-12）**
- `已实施的修复内容`：
  - `run_calc_step()` 顺序调整为：先构建 structured config，再调用 `prepare_calc_step_dir()`
  - 非法 cleanup 参数会在删除旧工件前抛出异常
- `验证测试`：
  - `test_calc_step_invalid_cleanup_preserves_old_artifacts`
  - `test_calc_step_invalid_cleanup_does_not_overwrite_config_hash`

## 5. 阶段 2 最终状态总结

### 无开放阻塞点

R10 和 R11 已修复并通过验证：
- structured cleanup 已进入 calc step signature
- step_handlers 顺序已调整，非法 cleanup 参数不会提前删除旧工件
- 一致性测试已补齐

### 已实施的修复方案

#### 5.1 structured cleanup 的有效语义已纳入 signature/hash 基线
已实施：
- `compute_calc_config_signature()` 调用 `resolve_effective_auto_clean()` 获取有效 cleanup 语义
- 在 `auto_clean_enabled=True` 时将 effective cleanup overlay 到 signature_view
- signature 与 auto-clean 使用相同的优先级规则：
  1. `manager.config["clean_opts"]`
  2. `execution_config.cleanup`
  3. 默认 `-t 0.25`

#### 5.2 step_handlers 顺序已调整
已实施：
- `run_calc_step()` 顺序调整为：
  1. 构建 legacy config
  2. `validate_calc_config(...)`
  3. **构建 structured config，并完成 cleanup 参数有效性确认**
  4. compute input signature
  5. **`prepare_calc_step_dir(...)`（仅在 structured 构建成功后执行）**
  6. manager handoff / record signature

#### 5.3 legacy compat/signature 路径与 execution lane 保持一致
已实施：
- 继续保留 dual-lane：
  - compat lane：legacy config
  - execution lane：structured config
- signature 通过 **compat 基底 + effective cleanup overlay** 覆盖实际生效语义
- 运行期 auto-clean 复用同一套 effective cleanup 解析规则

### 测试验收结果

```
============================= 708 passed in 7.36s ==============================
Success: no issues found in 84 source files (mypy)
All checks passed! (ruff)
```

关键测试覆盖：
- `test_config_hash_matches_auto_clean_effective_semantics` - 验证 signature 与 auto-clean 使用相同语义
- `test_dual_lane_clean_opts_update_syncs_signature_and_auto_clean` - 验证 dual-lane 下 config.update 同步
- `test_calc_step_cleanup_change_triggers_stale` - 验证 cleanup 变化触发 stale
- `test_calc_step_only_execution_cleanup_change_triggers_stale` - 验证仅 execution cleanup 变化触发 stale
- `test_calc_step_invalid_cleanup_preserves_old_artifacts` - 验证非法参数不删旧工件
- `test_calc_step_invalid_cleanup_does_not_overwrite_config_hash` - 验证非法参数不覆盖 hash

### 当前分支状态

- 分支名：`claude-try`
- 基于：`main` (commit 3982095)
- 最新提交：`9056517 test(calc): add cleanup consistency tests and close phase2 validation gaps`
- 状态：所有修复已完成，测试全部通过

## 6. 面向下一阶段的结论

阶段 2 已完成收口：
- R1-R11 所有 review 问题已修复并验证通过
- structured cleanup 已正确进入 calc step signature
- step_handlers 顺序已调整，保证参数有效性先于工件清理
- 一致性测试已补齐，覆盖 signature 与 auto-clean 同步
- 708 个测试全部通过，mypy 和 ruff 检查通过

下一阶段可推进内容（留待后续）：
- TaskRunner / policy / setup 的结构化消费
- 异常体系调整、运行时重构、大模块拆分
