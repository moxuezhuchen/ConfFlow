# Compat / Execution 边界契约文档

## 1. 核心概念

### 1.1 Compat/Signature 基线

**定义**：用于 calc step 断点续传、stale 判定、工件复用的配置基线。

**形式**：`dict[str, Any]`（legacy flat config）

**职责**：
- 计算 `.config_hash` 文件内容
- 判断旧 step 工件是否可复用
- 判断旧 step 工件是否 stale（需要清理重算）
- 保证跨版本、跨运行的配置兼容性

**关键特性**：
- 必须稳定：相同语义的配置必须产生相同的 hash
- 必须完整：所有影响实际计算行为的参数都必须体现在 signature 中
- 必须可序列化：可以写入 `.config_hash` 文件并在后续运行中读取

### 1.2 Execution 覆盖视图

**定义**：用于运行期执行的结构化配置视图。

**形式**：`CalcTaskConfig`（Pydantic v2 模型）或 `dict[str, Any]`

**职责**：
- 提供类型化的配置访问
- 支持复杂数据结构（如 `CleanupOptions`、`TSOptions`、`ExecutionOptions`）
- 允许运行期动态调整（如 `enable_dynamic_resources`）
- 为未来的结构化消费提供基础

**关键特性**：
- 可以覆盖 compat 基线中的某些参数（如 `cleanup`）
- 不能破坏 compat/signature 的稳定性契约
- 当前主要用于 execution lane，不直接参与 signature 计算

## 2. Dual-Lane Handoff 机制

### 2.1 当前工作流程

```
workflow (step_handlers.py)
  ↓
  1. build_task_config(...)           → legacy_config (dict)
  2. validate_calc_config(...)        ← legacy_config
  3. build_structured_task_config(...) → structured_config (CalcTaskConfig)
  4. compute_calc_input_signature(...) ← legacy_config + structured_config
  5. prepare_calc_step_dir(...)       ← legacy_config + structured_config
  ↓
calc (manager.py)
  ↓
  6. ChemTaskManager(settings=legacy, execution_config=structured)
  7. manager.config                   → legacy_config (公开真源)
  8. manager.compat_config            → manager.config 的别名
  9. manager.execution_config         → structured_config
  ↓
  10. prepare_calc_step_dir(...)      ← manager.compat_config + manager.execution_config
  11. record_calc_step_signature(...) ← manager.compat_config + manager.execution_config
  12. task building / execute         ← 主要经由 manager.config (dict-like)
```

### 2.2 为什么需要两个 config

**Compat config 的不可替代性**：
- 历史 step 工件的 `.config_hash` 是基于 legacy flat config 计算的
- 改变 signature 计算方式会导致所有旧 step 工件被误判为 stale
- 必须保持 legacy config 作为 signature 基线，直到所有用户的旧工件都失效

**Execution config 的必要性**：
- 某些参数（如 `cleanup`）需要结构化表达（`CleanupOptions`）
- 运行期需要类型安全的配置访问
- 为未来的 TaskRunner / policy / setup 结构化消费提供基础

## 3. 边界交汇点

### 3.1 resolve_effective_auto_clean()

**位置**：`confflow/calc/step_contract.py`

**签名**：
```python
def resolve_effective_auto_clean(
    config: dict[str, Any],
    execution_config: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
```

**为什么需要两个 config**：
- `auto_clean` flag 的优先级：
  1. `config["auto_clean"]`（compat 基线）
  2. `execution_config.cleanup.enabled`（structured 覆盖）
  3. `execution_config["auto_clean"]`（fallback）
  4. 默认 `False`

- `clean_opts` 的优先级（仅当 `auto_clean=True` 时）：
  1. `config["clean_opts"]`（compat 基线）
  2. `execution_config.cleanup.to_legacy_clean_opts()`（structured 覆盖）
  3. `execution_config["clean_opts"]`（fallback）
  4. 默认 `"-t 0.25"`

**关键契约**：
- 运行期 `manager.config.update({"clean_opts": ...})` 必须对 signature 和 auto-clean 同步生效
- structured cleanup 必须能够生成等价的 legacy `clean_opts` 字符串
- signature 计算和 auto-clean 执行必须使用相同的优先级规则

### 3.2 compute_calc_config_signature()

**位置**：`confflow/calc/step_contract.py`

**签名**：
```python
def compute_calc_config_signature(
    config: dict[str, Any],
    *,
    execution_config: Mapping[str, Any] | None = None,
) -> str:
```

**为什么需要两个 config**：
- 基线来自 `config`（compat）
- 但 effective cleanup 需要从 `execution_config` 中解析
- 最终 signature 是 compat 基底 + effective cleanup overlay

**关键契约**：
- 如果 `auto_clean=False`，cleanup 参数不影响 signature
- 如果 `auto_clean=True`，effective cleanup 必须体现在 signature 中
- signature 必须覆盖所有会影响实际 auto-clean 行为的参数

### 3.3 prepare_calc_step_dir()

**位置**：`confflow/calc/step_contract.py`

**签名**：
```python
def prepare_calc_step_dir(
    step_dir: str,
    task_config: dict[str, Any],
    *,
    input_signature: str | None = None,
    execution_config: Mapping[str, Any] | None = None,
) -> PreparedCalcStep:
```

**为什么需要两个 config**：
- `task_config` 用于计算 current signature（compat 基线）
- `execution_config` 用于解析 effective cleanup（structured 覆盖）
- 两者共同决定是否需要清理旧工件

**关键契约**：
- 只有当 `stored_signature != current_signature` 时才清理旧工件
- current signature 必须包含 effective cleanup 语义
- 清理操作在 structured config 构建成功后执行（避免非法参数提前删除旧工件）

### 3.4 record_calc_step_signature()

**位置**：`confflow/calc/step_contract.py`

**签名**：
```python
def record_calc_step_signature(
    step_dir: str,
    config: dict[str, Any],
    *,
    input_signature: str | None = None,
    execution_config: Mapping[str, Any] | None = None,
) -> None:
```

**为什么需要两个 config**：
- 与 `compute_calc_config_signature()` 使用相同的双参数接口
- 保证写入 `.config_hash` 的 signature 与 stale 判定使用的 signature 一致

**关键契约**：
- 必须在 calc step 成功完成后调用
- 写入的 signature 必须包含 effective cleanup 语义
- 后续运行会读取这个 signature 进行 stale 判定

## 4. manager.config.update(...) 的影响路径

### 4.1 当前行为

`manager.config` 是公开真源（mutable dict），运行期对它的修改会影响：

1. **Signature 路径**：
   - `manager._config_for_signature` 返回 `manager.config`
   - `prepare_calc_step_dir(manager.compat_config, ...)` 使用 `manager.config`
   - `record_calc_step_signature(manager.compat_config, ...)` 使用 `manager.config`

2. **Auto-clean 路径**：
   - `manager._config_for_auto_clean` 返回 `manager.config`
   - `manager._resolve_effective_clean_opts()` 使用 `manager.config`
   - `manager._run_auto_clean(...)` 使用 effective cleanup 解析结果

3. **Execution 路径**：
   - `manager.config.get("enable_dynamic_resources", ...)` 读取运行期配置
   - `manager.config.get("cores_per_task", ...)` 读取资源配置
   - `run_services.py` 中的路径配置读取和写入

### 4.2 为什么这样设计

**阶段 2 的核心契约**：
- `manager.config` 必须是公开真源，不能有隐藏的内部副本
- 运行期 `manager.config.update(...)` 必须对所有路径同步可见
- compat/signature 路径和 execution 路径必须看到相同的 config 更新

**历史问题（已修复）**：
- R6：`manager.config` 曾经是内部副本，导致 `update(...)` 后 compat 路径失同步
- 修复方案：`manager.config` 恢复为公开真源，`manager.compat_config` 改为别名

### 4.3 Semantic Accessors 的作用

**当前实现**（阶段 3 第 1 小步）：
```python
@property
def _config_for_signature(self) -> dict[str, Any]:
    return self.config

@property
def _config_for_auto_clean(self) -> dict[str, Any]:
    return self.config
```

**目的**：
- 显式化最关键的 config 访问意图
- 为后续可能的 compat/execution 分离提供锚点
- 保持阶段 2 确立的契约：两个 accessor 返回同一个可变对象

**不是**：
- 不是为了隐藏 `manager.config`
- 不是为了创建内部副本
- 不是为了改变运行期 `update(...)` 的可见性

## 5. Structured Cleanup 的双重语义

### 5.1 为什么必须同时体现在 runtime 和 signature 中

**问题背景**：
- 历史上 cleanup 参数只在 compat config 中（`clean_opts` 字符串）
- 引入 structured config 后，cleanup 可以用 `CleanupOptions` 表达
- 但如果 structured cleanup 不进入 signature，会导致：
  - 相同的 `.config_hash` 对应不同的 auto-clean 行为
  - 用户修改 cleanup 参数后，旧 step 工件不会被判定为 stale
  - 断点续传会复用错误的清理结果

**解决方案**（R10 已修复）：
- `compute_calc_config_signature()` 调用 `resolve_effective_auto_clean()`
- 在 `auto_clean=True` 时将 effective cleanup overlay 到 signature_view
- signature 与 auto-clean 使用相同的优先级规则

### 5.2 优先级规则

**Auto-clean flag**：
1. `config["auto_clean"]`（compat 基线）
2. `execution_config.cleanup.enabled`（structured 覆盖）
3. `execution_config["auto_clean"]`（fallback）
4. 默认 `False`

**Clean opts**（仅当 `auto_clean=True` 时）：
1. `config["clean_opts"]`（compat 基线）
2. `config["clean_params"]`（compat 基线 legacy alias）
3. `execution_config.cleanup.to_legacy_clean_opts()`（structured 覆盖）
4. `execution_config["clean_opts"]`（fallback）
5. `execution_config["clean_params"]`（fallback legacy alias）
6. 默认 `"-t 0.25"`

### 5.3 关键测试覆盖

- `test_config_hash_matches_auto_clean_effective_semantics`：验证 signature 与 auto-clean 使用相同语义
- `test_dual_lane_clean_opts_update_syncs_signature_and_auto_clean`：验证 `manager.config.update(...)` 同步
- `test_calc_step_cleanup_change_triggers_stale`：验证 cleanup 变化触发 stale
- `test_calc_step_only_execution_cleanup_change_triggers_stale`：验证仅 execution cleanup 变化触发 stale

## 6. 已稳定契约 vs 未来候选方向

### 6.1 Parameter Classification Table

下表明确列出所有 calc config 参数的边界归属，用于指导后续重构和新增参数时的决策。

| Parameter Name | Compat Baseline | Execution Override | Only in Execution | Notes |
|----------------|-----------------|-------------------|-------------------|-------|
| `gaussian_path` | ✓ | - | - | 程序路径，影响 signature |
| `orca_path` | ✓ | - | - | 程序路径，影响 signature |
| `iprog` | ✓ | - | - | 程序选择，影响 signature |
| `itask` | ✓ | - | - | 任务类型，影响 signature |
| `keyword` | ✓ | - | - | 计算关键字，影响 signature |
| `charge` | ✓ | - | - | 电荷，影响 signature |
| `multiplicity` | ✓ | - | - | 自旋多重度，影响 signature |
| `freeze` | ✓ | - | - | 冻结原子索引，影响 signature |
| `cores_per_task` | ✓ | - | - | 核心数，影响 signature |
| `total_memory` | ✓ | - | - | 内存配置，影响 signature |
| `max_parallel_jobs` | ✓ | - | - | 并行任务数，影响 signature |
| `auto_clean` | ✓ | ✓ | - | **双重语义**：compat 基线可被 execution 覆盖，影响 signature |
| `clean_opts` | ✓ | ✓ | - | **双重语义**：compat 基线可被 execution 覆盖，影响 signature（仅当 `auto_clean=True`） |
| `clean_params` | ✓ | ✓ | - | **双重语义**：legacy alias，运行时按 `clean_opts` 同语义解析 |
| `ts_bond_atoms` | ✓ | - | - | TS 键原子对，影响 signature |
| `ts_rescue_scan` | ✓ | - | - | TS 救援开关，影响 signature |
| `ts_bond_drift_threshold` | ✓ | - | - | TS 键漂移阈值，影响 signature |
| `ts_rmsd_threshold` | ✓ | - | - | TS RMSD 阈值，影响 signature |
| `scan_coarse_step` | ✓ | - | - | Scan 粗步长，影响 signature |
| `scan_fine_step` | ✓ | - | - | Scan 细步长，影响 signature |
| `scan_uphill_limit` | ✓ | - | - | Scan 上坡限制，影响 signature |
| `scan_max_steps` | ✓ | - | - | Scan 最大步数，影响 signature |
| `scan_fine_half_window` | ✓ | - | - | Scan 细扫窗口，影响 signature |
| `ts_rescue_keep_scan_dirs` | ✓ | - | - | Scan 目录保留开关，影响 signature |
| `ts_rescue_scan_backup` | ✓ | - | - | Scan 备份开关，影响 signature |
| `blocks` | ✓ | - | - | ORCA blocks，影响 signature |
| `orca_maxcore` | ✓ | - | - | ORCA maxcore，影响 signature |
| `gaussian_modredundant` | ✓ | - | - | Gaussian modredundant，影响 signature |
| `gaussian_link0` | ✓ | - | - | Gaussian link0，影响 signature |
| `gaussian_write_chk` | ✓ | - | - | **Runtime-only in compat lane**：当前仍存在于 legacy/compat config 并被运行时读取，但已从 signature 排除，仅控制 .chk 写入行为 |
| `enable_dynamic_resources` | ✓ | - | - | **Runtime-only in compat lane**：当前仍存在于 legacy/compat config 并被运行时读取，但已从 signature 排除，仅控制运行期资源调整 |
| `resume_from_backups` | ✓ | - | - | **Runtime-only in compat lane**：当前仍存在于 legacy/compat config 并被运行时读取，但已从 signature 排除，仅控制断点续传行为 |
| `sandbox_root` | - | - | ✓ | **Execution-only**：不影响 signature，仅用于路径安全校验 |
| `input_chk_dir` | - | - | ✓ | **Execution-only**：不影响 signature，仅用于跨 step .chk 传递 |
| `allowed_executables` | - | - | ✓ | **Execution-only**：不影响 signature，仅用于可执行路径白名单校验 |
| `backup_dir` | - | - | ✓ | **Excluded from signature**：运行期动态写入，不影响 signature |
| `stop_beacon_file` | - | - | ✓ | **Excluded from signature**：运行期控制信号，不影响 signature |
| `delete_work_dir` | - | - | ✓ | **Execution-only**：不影响 signature，仅控制工作目录清理 |

**分类说明**：

- **Compat Baseline (✓)**：参数存在于 legacy flat config 中，参与 signature 计算的基线
- **Execution Override (✓)**：参数可被 `execution_config` 中的结构化值覆盖（当前仅 `auto_clean` / `clean_opts` / `clean_params`）
- **Only in Execution (✓)**：参数仅存在于 execution 视图，不参与 signature 计算（或显式排除）

**关键契约**：

1. **Cleanup 的双重语义**：
   - `auto_clean` / `clean_opts` / `clean_params` 既影响 runtime（决定是否执行 auto-clean），又影响 signature（当 `auto_clean=True` 时）
   - `clean_params` 在 compat/execution 边界中作为 `clean_opts` 的 legacy alias 处理，并在 signature 中规范化为 `clean_opts`
   - 优先级规则：`config` 基线 → `execution_config` 覆盖 → 默认值
   - `resolve_effective_auto_clean()` 和 `compute_calc_config_signature()` 必须使用相同的优先级规则

2. **manager.config.update(...) 的同步可见性**：
   - `manager.config` 是公开真源（mutable dict）
   - 运行期 `manager.config.update({"clean_opts": ...})` 必须对 signature 路径和 auto-clean 路径同步可见
   - 当前通过 `manager._config_for_signature` 和 `manager._config_for_auto_clean` 返回同一个 `manager.config` 对象保证

3. **Execution-only 参数的边界**：
   - `gaussian_write_chk` / `enable_dynamic_resources` / `resume_from_backups` 等参数不影响计算结果，仅控制运行期行为
   - 这些参数的变化不应触发 stale 判定（不进入 signature）
   - `backup_dir` / `stop_beacon_file` 等运行期动态写入的参数显式排除在 signature 之外（`_CONFIG_HASH_EXCLUDE_KEYS`）

4. **新增参数时的决策流程**：
   - 如果参数影响计算结果（如新的量化方法关键字）→ 必须进入 compat baseline，影响 signature
   - 如果参数仅控制运行期行为（如新的资源调度策略）→ 标记为 execution-only，不影响 signature
   - 如果参数需要结构化表达且可能覆盖 compat 基线 → 参考 cleanup 的双重语义模式，补充 `resolve_effective_*()` 函数

### 6.2 已稳定契约（绝对不能破坏）

### 6.2 已稳定契约（绝对不能破坏）
- legacy flat config 必须继续作为 signature 计算的基底
- `.config_hash` 文件格式不能改变
- signature 计算算法不能改变（除非有明确的迁移方案）

✅ **Dual-lane handoff 接口**：
- `prepare_calc_step_dir(config, execution_config=...)`
- `record_calc_step_signature(config, execution_config=...)`
- `compute_calc_config_signature(config, execution_config=...)`
- `resolve_effective_auto_clean(config, execution_config=...)`

✅ **manager.config 的公开可变性**：
- `manager.config` 必须是公开真源
- 运行期 `manager.config.update(...)` 必须对所有路径同步可见
- `manager.compat_config` 必须是 `manager.config` 的别名

✅ **Effective cleanup 的双重语义**：
- structured cleanup 必须能够生成等价的 legacy `clean_opts` 字符串
- signature 计算和 auto-clean 执行必须使用相同的优先级规则
- cleanup 变化必须触发 stale 判定

✅ **Step handlers 顺序**：
- structured config 构建必须在 `prepare_calc_step_dir()` 之前
- 非法 cleanup 参数必须在删除旧工件前抛出异常

### 6.3 未来候选方向（当前不实施）

🔄 **Compat / Execution 边界收紧**：
- 明确哪些参数属于 compat 基线，哪些属于 execution 覆盖
- 可能引入显式的 "signature-affecting params" 集合
- 可能将 dual-lane 接口改为单一的 structured config 接口（需要迁移方案）

🔄 **Manager 内部 config 访问的语义显式化**：
- 当前只有 `_config_for_signature` 和 `_config_for_auto_clean` 两个 semantic accessor
- 未来可能新增 `_config_for_execution` 覆盖更多访问点
- 但需要先解决 `run_services.py` 的写入操作和混合语义问题

🔄 **类型安全提升**：
- calc 模块内部 29 处 `dict[str, Any]` 签名
- 可能引入 TypedDict 或更严格的 Pydantic 模型
- 但需要保持与 legacy config 的兼容性

🔄 **异常模型与失败语义分层**：
- 当前 `TaskRunner._classify_error()` 返回字符串
- 可能引入 `FailureKind` 枚举（已完成）
- 但需要调整所有消费 error string 的代码

## 7. 后续重构的边界红线

### 7.1 绝对不能做的事

❌ **改变 signature 计算方式而不提供迁移方案**：
- 会导致所有旧 step 工件被误判为 stale
- 会破坏用户的断点续传能力

❌ **让 manager.config.update(...) 对某些路径不可见**：
- 会破坏阶段 2 确立的核心契约
- 会导致 signature 与 auto-clean 失同步

❌ **让 structured cleanup 不进入 signature**：
- 会导致相同 hash 对应不同 auto-clean 行为
- 会破坏 stale 判定的正确性

❌ **在 structured config 构建前清理旧工件**：
- 会导致非法参数提前删除旧工件
- 会破坏用户的断点续传能力

### 7.2 可以做但需要谨慎的事

⚠️ **引入新的 config 参数**：
- 必须明确是否影响 signature
- 如果影响 signature，必须加入 signature 计算
- 如果不影响 signature，必须加入 `_CONFIG_HASH_EXCLUDE_KEYS`

⚠️ **修改 effective cleanup 的优先级规则**：
- 必须同时修改 signature 计算和 auto-clean 执行
- 必须补充测试覆盖
- 必须考虑对旧 step 工件的影响

⚠️ **拆分 task_config.py 或 manager.py**：
- 必须保持 dual-lane handoff 接口不变
- 必须保持 `manager.config` 的公开可变性
- 必须保持 semantic accessors 的语义

⚠️ **引入新的 structured config 字段**：
- 必须明确是否需要进入 signature
- 必须提供 to_legacy_*() 方法生成等价的 legacy 表达
- 必须补充 dual-lane 一致性测试

## 8. 验收标准

任何涉及 compat/execution 边界的修改，必须通过以下测试：

```bash
# 核心一致性测试
pytest tests/test_utils_manager.py::test_config_hash_matches_auto_clean_effective_semantics -v
pytest tests/test_utils_manager.py::test_dual_lane_clean_opts_update_syncs_signature_and_auto_clean -v

# Stale 判定测试
pytest tests/test_step_handlers.py::TestRunCalcStep::test_calc_step_cleanup_change_triggers_stale -v
pytest tests/test_step_handlers.py::TestRunCalcStep::test_calc_step_only_execution_cleanup_change_triggers_stale -v

# 工件保护测试
pytest tests/test_step_handlers.py::TestRunCalcStep::test_calc_step_invalid_cleanup_preserves_old_artifacts -v
pytest tests/test_step_handlers.py::TestRunCalcStep::test_calc_step_invalid_cleanup_does_not_overwrite_config_hash -v

# 全量测试
pytest -q
mypy confflow
ruff check confflow
```

## 9. 参考文档

- `docs/archive/HANDOFF_PHASE2_WORKFLOW_CALC.md`：阶段 2 完整历史与问题台账
- `confflow/calc/step_contract.py`：边界交汇点的实现
- `confflow/calc/manager.py`：manager 内部的 semantic accessors
- `confflow/workflow/step_handlers.py`：workflow 侧的 dual-lane handoff
- `confflow/workflow/task_config.py`：legacy 和 structured builder 的实现
