# 阶段 2 Handoff / Recovery 文档（完整覆盖版）

## 1. 当前代码状态快照

### 已真正落地并通过验证的内容
- 已引入 `CalcTaskConfig` 及相关结构化配置对象，且 `build_structured_task_config()` 已改为**直接从 typed YAML values 构建**，不再经过 legacy dict round-trip。
- `build_task_config()` / `create_runtask_config()` 继续作为 **legacy compat 导出层**，对外签名与输出保持不变。
- `workflow -> calc` 已完成一小步双轨接线：
  - `run_calc_step()` 继续用 legacy config 做 `validate / prepare / record signature`
  - 同时把 structured config 传给 `ChemTaskManager(..., execution_config=...)`
- `ChemTaskManager` 已显式区分：
  - `manager.config`：公开真源
  - `manager.compat_config`：当前是 `manager.config` 的别名
  - `manager.execution_config`：execution lane 的 structured 补充视图
- `auto_clean` 已完成 bool/string 兼容化，不再因 `bool.lower()` 崩溃。
- structured builder 已保留显式 `input_chk_dir`，并支持 `chk_from_step` fallback。
- cleanup 解析已补齐：
  - `build_structured_task_config()` 会保留 `clean_params / clean_opts` 的阈值语义，至少覆盖 `threshold / ewin / energy_tolerance`
  - `manager._resolve_effective_clean_opts()` 当前优先级为：
    1. `manager.config["clean_opts"]`
    2. `execution_config.cleanup`
    3. 默认 `-t 0.25`

### 当前停留的中间状态
- **signature/hash 基线仍然只绑定 legacy compat config**。
- execution lane 的 structured cleanup 已能影响实际 auto-clean，但 **尚未纳入 calc step signature/hash 基线**。
- `run_calc_step()` 当前仍是：
  1. 构建 legacy config
  2. `validate_calc_config()`
  3. `prepare_calc_step_dir()`
  4. 再构建 structured config
- 这意味着如果 structured cleanup 参数非法，**可能先清理旧 step 工件，再因为 structured 构建失败报错**。

### 只有方案、尚未实施的内容
- 尚未把 **structured cleanup 的有效语义** 纳入 calc step signature/hash。
- 尚未调整 `step_handlers` 顺序为：
  - 先完成 structured config 构建与参数有效性确认
  - 再决定是否执行 `prepare_calc_step_dir()` 清理旧工件
- 尚未推进 TaskRunner / policy / setup 的结构化消费。
- 尚未做异常体系调整、运行时重构、大模块拆分。

## 2. 当前稳定原则

- `legacy config` 仍是 **compat/signature 基线**。
- `structured config` 当前只用于 **execution lane**。
- calc step signature **必须覆盖所有会影响实际 auto-clean 行为的有效 cleanup 语义**。
- 在 structured config 可能因参数非法而失败之前，**不能先删除旧 step 工件**。
- `manager.config` 是公开真源；构造后对 `manager.config.update(...)` 的修改，compat/signature 路径和 execution 路径都必须可见。
- execution lane 可以逐步消费 structured config，但不能绕开 compat/signature 的稳定基线。

## 3. 当前配置与执行链路现状

### workflow 侧
- `build_task_config(...)`：生成 legacy compat dict
- `build_structured_task_config(...)`：生成 `CalcTaskConfig`
- `run_calc_step(...)` 当前行为：
  - legacy config -> `ConfigSchema.validate_calc_config(...)`
  - legacy config -> `prepare_calc_step_dir(...)`
  - structured config -> `ChemTaskManager(settings=legacy, execution_config=structured)`
  - legacy config -> `record_calc_step_signature(...)`

### calc / manager 侧
- `manager.config`：公开真源，当前也是 compat/signature 可见对象
- `manager.compat_config`：`manager.config` 的别名
- `manager.execution_config`：structured execution 视图
- `run()` 当前：
  - `prepare_calc_step_dir(..., self.compat_config, ...)`
  - `record_calc_step_signature(..., self.compat_config, ...)`
  - task building / execute / recovery 仍主要经由 dict-like config 兼容
- `auto-clean` 当前：
  - 运行前先求最终有效 `clean_opts`
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
- `尚未解决的剩余缺口`：
  - 无直接剩余缺口
- `最新已知 review 结论`：
  - 后续 review 未再复现该问题
- `下一步最小动作`：
  - 保持 legacy builder 仅作为 compat 导出层，不再让其依赖 structured round-trip
- `必须补的测试`：
  - legacy 输出稳定性测试：已存在
  - signature 稳定性测试：已存在

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
- `尚未解决的剩余缺口`：
  - 无直接剩余缺口
- `最新已知 review 结论`：
  - 后续 review 未再复现该问题
- `下一步最小动作`：
  - 保持 structured builder 不依赖 legacy builder
- `必须补的测试`：
  - typed field preservation：已存在

### R3
- `issue_id`：R3
- `首次出现背景/轮次`：workflow→calc 第一版 handoff review
- `问题摘要`：workflow calc hash 被 structured config 覆盖，导致下一次运行把旧 step 误判为 stale
- `影响范围`：`confflow/workflow/step_handlers.py`，`confflow/calc/manager.py`
- `严重级别`：P1
- `当前状态`：已修复，但后续又暴露出相关新问题
- `已实施的修复内容`：
  - manager 引入 dual-lane 接口
  - `prepare_calc_step_dir()` / `record_calc_step_signature()` 重新绑定到 compat/legacy config
- `尚未解决的剩余缺口`：
  - 新问题 R10：structured cleanup 的有效语义尚未纳入 signature/hash
- `最新已知 review 结论`：
  - “structured cleanup 尚未进入 calc step signature”
- `下一步最小动作`：
  - 将最终有效 cleanup 语义并入 signature 基线
- `必须补的测试`：
  - execution cleanup 改变会导致 signature 改变：缺
  - dual-lane 下 `.config_hash` 与实际 auto-clean 一致：缺

### R4
- `issue_id`：R4
- `首次出现背景/轮次`：workflow→calc 第一版 handoff review
- `问题摘要`：`auto_clean` 为 bool 时仍调用 `.lower()`，成功执行后会崩溃
- `影响范围`：`confflow/calc/manager.py`
- `严重级别`：P1
- `当前状态`：已修复并验证通过
- `已实施的修复内容`：
  - `auto_clean` 判断统一走 `_is_enabled_flag(...)`
- `尚未解决的剩余缺口`：
  - 无直接剩余缺口
- `最新已知 review 结论`：
  - 后续 review 未再复现该 crash
- `下一步最小动作`：
  - 保持 bool/string flag 统一解析
- `必须补的测试`：
  - bool `auto_clean` 回归测试：已存在

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
- `尚未解决的剩余缺口`：
  - 无直接剩余缺口
- `最新已知 review 结论`：
  - 后续 review 未再复现
- `下一步最小动作`：
  - 保持显式路径优先于派生路径
- `必须补的测试`：
  - 显式 `input_chk_dir` 保留：已存在
  - `chk_from_step` fallback：已存在

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
- `尚未解决的剩余缺口`：
  - 无直接剩余缺口
- `最新已知 review 结论`：
  - 后续 review 未再复现该同步问题
- `下一步最小动作`：
  - 保持 `config` 为公开真源，不再引入并行可变副本
- `必须补的测试`：
  - `manager.config.update(...)` 影响 signature 路径：已存在

### R7
- `issue_id`：R7
- `首次出现背景/轮次`：manager 分轨后的第一次 review
- `问题摘要`：structured cleanup 没有真正驱动 auto-clean，仍只读 flat `clean_opts`
- `影响范围`：`confflow/calc/manager.py`
- `严重级别`：P2
- `当前状态`：已修复，但后续又暴露出相关新问题
- `已实施的修复内容`：
  - 新增 `_resolve_effective_clean_opts()`
  - structured cleanup 可生成等价 legacy `clean_opts`
- `尚未解决的剩余缺口`：
  - 新问题 R10：cleanup 语义尚未进入 signature/hash
- `最新已知 review 结论`：
  - cleanup 运行时解析已改进，但 signature 仍可能脱节
- `下一步最小动作`：
  - 把最终有效 cleanup 语义并入 signature 基线
- `必须补的测试`：
  - structured cleanup 影响 signature：缺

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
- `尚未解决的剩余缺口`：
  - 这些语义尚未纳入 signature 基线，见 R10
- `最新已知 review 结论`：
  - 参数保留本身未再被最新 review 指出
- `下一步最小动作`：
  - 让 signature 也覆盖这些实际生效参数
- `必须补的测试`：
  - `clean_params` -> structured cleanup 阈值保留：已存在
  - `clean_params` 影响 signature：缺

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
- `尚未解决的剩余缺口`：
  - 该优先级尚未同步到 signature/hash，见 R10
- `最新已知 review 结论`：
  - 最新 review 未再单独指出该优先级错误
- `下一步最小动作`：
  - 让 signature 复用同一份“最终有效 cleanup 语义”
- `必须补的测试`：
  - public `clean_opts` override 优先级：已存在
  - override 后 signature 同步变化：缺

### R10
- `issue_id`：R10
- `首次出现背景/轮次`：最新 review
- `问题摘要`：structured cleanup 尚未进入 calc step signature/hash；相同 hash 可能对应不同 auto-clean 行为
- `影响范围`：`confflow/calc/manager.py`，`confflow/calc/step_contract.py`，workflow->calc dual-lane handoff
- `严重级别`：P1
- `当前状态`：当前仍开放 / 阻塞
- `已实施的修复内容`：
  - 无
- `尚未解决的剩余缺口`：
  - signature 仍只对 compat/legacy config 求值
  - execution_config.cleanup 改变不会改变 `.config_hash`
- `最新已知 review 结论`：
  - “structured cleanup 尚未进入 calc step signature”
- `下一步最小动作`：
  - 在 signature 计算前生成一份 **effective signature config**
  - 其 cleanup 语义必须与 `_resolve_effective_clean_opts()` 保持一致
- `必须补的测试`：
  - `execution_config.cleanup` 变化会导致 signature 变化：缺
  - same compat config + different structured cleanup => stale：缺

### R11
- `issue_id`：R11
- `首次出现背景/轮次`：最新 review
- `问题摘要`：`step_handlers` 先清理旧工件，再构建 structured config；若 cleanup 参数非法，会在报错前先删掉旧 step 工件
- `影响范围`：`confflow/workflow/step_handlers.py`
- `严重级别`：P2
- `当前状态`：当前仍开放 / 阻塞
- `已实施的修复内容`：
  - 无
- `尚未解决的剩余缺口`：
  - 仍存在“先删旧工件、后发现 structured cleanup 参数非法”的顺序风险
- `最新已知 review 结论`：
  - “step_handlers 存在先删旧工件、后构建 structured config 的顺序风险”
- `下一步最小动作`：
  - 在 `prepare_calc_step_dir()` 之前先完成 `build_structured_task_config()` 与 cleanup 参数有效性确认
- `必须补的测试`：
  - 非法 cleanup 参数不会在报错前先删旧工件：缺
  - 旧 `work_dir / output.xyz / results.db` 在参数非法时不会被提前清理：缺

## 5. 当前开放阻塞点

### 阻塞 1：structured cleanup 尚未进入 calc step signature
- 这是当前最核心的语义缺口。
- 当前实际 auto-clean 语义已经可能来自：
  - `manager.config["clean_opts"]`
  - `execution_config.cleanup`
- 但 `.config_hash` 仍只绑定 compat/legacy config。
- 结果：
  - 相同 hash 可能对应不同 refine 行为
  - 旧 `output.xyz / results.db / backups` 可能被错误复用
  - 也可能把本应 stale 的状态误判为可继续恢复

### 阻塞 2：step_handlers 的先删旧工件、后构建 structured config 顺序风险
- 当前 `prepare_calc_step_dir()` 发生在 structured config 构建之前。
- 若 cleanup 参数非法：
  - `validate_calc_config()` 可能不会报错
  - `build_structured_task_config()` 才会因为 `float()` 等转换失败而抛错
- 结果：
  - 旧 step 工件可能先被清理
  - 再抛出配置错误
  - 造成排查信息丢失与恢复状态丢失

## 6. 下一步最小修复方案（计划，不实施）

### 6.1 把 structured cleanup 的有效语义纳入 signature/hash 基线
最小方案：
- 在 workflow/manager 进入 signature 计算前，先生成一份 **effective cleanup signature view**
- 这份 view 不是全面替换 legacy config，而是：
  - 以 legacy compat config 为基底
  - 用与 `_resolve_effective_clean_opts()` 相同的规则，求出**最终有效 clean_opts**
  - 把这条最终有效 `clean_opts` 明确写入 signature 输入
- 这样可保持原则：
  - legacy config 仍是 compat/signature 基线
  - 但凡会改变实际 auto-clean 行为的 cleanup 语义，必须体现在 signature/hash 上

建议最小实现边界：
- 新增一个共享 helper，例如：
  - `resolve_effective_clean_opts_for_signature(public_config, execution_config) -> str`
- `manager.run()` 与 workflow calc signature 路径都使用这套逻辑
- 避免出现“runtime 优先级”和“signature 优先级”两套不同规则

### 6.2 调整 step_handlers 顺序
最小方案：
- `run_calc_step()` 顺序调整为：
  1. 构建 legacy config
  2. `validate_calc_config(...)`
  3. 构建 structured config，并完成 cleanup 参数有效性确认
  4. 如 structured 构建成功，再进入 `prepare_calc_step_dir(...)`
  5. 再做 manager handoff / record signature
- 目标：
  - 任何 structured cleanup 参数错误，都必须在删除旧 step 工件之前暴露

### 6.3 保持 legacy compat/signature 路径与 execution lane 一致
最小方案：
- 继续保留 dual-lane：
  - compat lane：legacy config
  - execution lane：structured config
- 但 signature 不能只看 compat lane 原始值，必须看 **compat 基底 + effective cleanup overlay**
- 运行期 auto-clean 也必须复用同一套 effective cleanup 解析规则

## 7. 必须补的测试（重写版）

### 本轮已存在的历史回归测试
已存在：
- legacy output/signature 稳定性测试
- structured builder 直接保留 typed fields 测试
- `input_chk_dir` 显式值与 `chk_from_step` fallback 测试
- dual-lane handoff 测试
- `manager.config.update(...)` 能影响 signature 路径测试
- bool `auto_clean` 回归测试
- `clean_params` 阈值进入 structured cleanup 测试
- `manager.config["clean_opts"]` 优先于 structured cleanup 测试
- public `clean_opts` 缺失时 structured cleanup 生效测试
- legacy `clean_opts` fallback 测试

### 当前仍缺的关键测试
必须补：
- `execution_config.cleanup` 变化会导致 signature 变化
- `same legacy config + different structured cleanup` 会触发 stale 判定
- 非法 cleanup 参数不会在报错前先删掉旧工件
- 旧 `work_dir / output.xyz / results.db / backups` 在参数非法时不会被提前清理
- `clean_params` / `clean_opts` 经过 effective cleanup signature overlay 后，`.config_hash` 与实际 auto-clean 行为一致
- dual-lane manager 下：
  - public `clean_opts` override 后
  - signature 与 auto-clean 都使用同一份 effective cleanup 语义

## 8. 下一轮可直接实施的最小修复清单（计划，不实施）

1. 在 `manager.py` 或共享 helper 中抽出“最终有效 cleanup 语义”解析：
   - 输入：`manager.config`、`execution_config`
   - 输出：最终有效 `clean_opts`
   - 优先级必须与当前 `manager._resolve_effective_clean_opts()` 保持一致

2. 引入 `effective signature config`：
   - 以 legacy compat config 为基底
   - 将最终有效 `clean_opts` 写入 signature 输入
   - `prepare_calc_step_dir()` / `record_calc_step_signature()` 使用它而不是裸 `compat_config`

3. 调整 `workflow/step_handlers.py` 顺序：
   - 先 `build_structured_task_config()`
   - 确认 cleanup 参数合法
   - 再允许 `prepare_calc_step_dir()` 清理旧工件

4. 补齐缺失回归测试：
   - signature 覆盖 effective cleanup
   - 非法 cleanup 参数不会提前删旧工件
   - 旧 step 状态在 cleanup 参数非法时保留

## 9. 下一轮可直接发给 Codex 的修复提示词

```text
只修当前两个开放阻塞点，不要扩展到阶段 2 其他迁移，也不要碰 TaskRunner / policy / setup / 异常体系 / 大模块拆分。

目标：
1. 让 calc step signature/hash 覆盖所有会影响实际 auto-clean 行为的有效 cleanup 语义。
2. 修复 step_handlers 的顺序风险：在 structured config 可能因 cleanup 参数非法而失败之前，不能先删除旧 step 工件。

硬性约束：
- legacy config 继续作为 compat/signature 基底；
- structured config 继续只用于 execution lane；
- 不新增第三方依赖；
- 不改 CLI/YAML 对外行为；
- 不做额外重构。

实施要求：
1. 抽出一套共享的 “effective cleanup -> clean_opts” 解析逻辑，优先级必须与当前 manager._resolve_effective_clean_opts() 一致：
   - 第一优先：manager.config["clean_opts"]
   - 第二优先：execution_config.cleanup
   - 第三优先：默认 -t 0.25
2. 在 manager 的 signature 路径里，不要再直接对裸 compat_config 求 hash；要对 “compat 基底 + effective cleanup overlay” 求 hash。
3. 在 workflow/step_handlers.py 中，先完成 build_structured_task_config() 与 cleanup 参数有效性确认，再调用 prepare_calc_step_dir()。
4. 只做最小可回滚改动。
5. 必须补测试并汇报：
   - execution_config.cleanup 变化会导致 signature 变化
   - 非法 cleanup 参数不会在报错前先删掉旧工件
   - 旧 work_dir / output.xyz / results.db / backups 在参数非法时不会被提前清理
   - public clean_opts override 后，signature 与 auto-clean 行为一致

验收命令：
- pytest -q tests/test_step_handlers.py tests/test_utils_manager.py tests/test_engine.py
- ruff check confflow/workflow/task_config.py confflow/workflow/step_handlers.py confflow/calc/manager.py tests/test_step_handlers.py tests/test_utils_manager.py tests/test_engine.py
- mypy confflow
```

## 10. 面向下一会话的结论

当前仓库不是“没有进展”，而是已经完成了阶段 2 的一部分可靠接线与 cleanup 解析收口，但**还停在一个必须继续补上 signature/hash 与构建顺序安全性的中间状态**。  
下一轮不需要再回头重做前面的分轨与 cleanup 解析，只需要围绕 **R10 + R11** 做最小闭环修复。
