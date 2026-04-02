# ConfFlow 代码评估报告 & 整改路线图

**评估日期**: 2026-03-15
**评估范围**: 架构可维护性 · 可靠性与潜在缺陷 · 测试覆盖 · 性能与资源
**方法**: 全量静态阅读 + 验证执行（`pytest -q`, `pytest --cov`, `ruff check .`, `mypy confflow`）
**基线**: 全部 655 测试通过；branch coverage 90.52%；无静态报错

**当前执行状态**: 主要整改项已继续落地，最近补齐了 calc/resume 工件识别与 `results.db` 最新记录聚合的一致性回归。
**最近验证**: `pytest -q`、`pytest tests/ --cov=confflow --cov-report=term`、`ruff check .`、`mypy confflow` 均通过。
**当前覆盖率快照**: 总 branch coverage 90.52%；`workflow/validation.py` 84%，`generator.py` 91%，`scan_ops.py` 89%，`stats.py` 89%，`engine.py` 87%，`manager.py` 82%。

---

## 一、总体评价

仓库结构清晰，分层设计（core → config → blocks → calc → workflow）职责边界合理，Pydantic 模型、TypedDict 和测试基础设施的质量均高于平均水平。当前主要问题集中在 **少量高风险逻辑路径的异常语义和测试空白**，而非系统级架构缺陷。修复代价可控，优先级可分批执行。

### 本轮执行结果

已完成：

- `mapping.py`：补上 MCS 超时显式处理，并将链映射改为对称感知的最小位移选择
- `manager.py`：补上 `fut.result()` 异常保护，并在 STOP 信标出现时主动取消未完成 future
- `engine.py`：补上 resume 丢失输出警告、多输入直入 calc 警告，并移除静默吞错的 `except Exception: pass`
- `scan_ops.py`：整理坐标解析逻辑，降低后续维护误判风险
- `rmsd_engine.py`：将 topology hash 的 O(N²) 全距离矩阵替换为 `cKDTree.query_pairs`
- `stats.py`：将 summary 解析中的宽泛吞错收紧为带日志的定向异常处理
- `stats.py`：`results.db` 状态统计改为优先按每个 `job_name` 的最新记录聚合，避免旧失败记录污染 step 统计
- `helpers.py` / `engine.py` / `step_handlers.py`：收紧 calc/resume 工件契约，`calc` 只认 `output.xyz` / `result.xyz`，不再把 `search.xyz` 误判为完成输出
- `generator.py`：当所有输入文件都无法生成构象时，直接抛出更接近根因的异常
- 测试：新增 `tests/test_collision.py`，并补齐 `test_refine.py`、`test_engine.py`、manager 假执行器兼容性测试
- 测试：新增针对 `search.xyz` 误判与 latest-record 统计的一组回归用例

仍未完成：

- `mapping.py` 的 MCS timeout / 对称映射专项单测尚未补齐
- `manager.py` 的 `BrokenProcessPool` 专项测试尚未补齐
- `pyproject.toml` 尚未对 Numba JIT 函数体 coverage 统计做专门排除

---

## 二、问题清单（按优先级）

### P0 — 正确性风险（直接导致错误结果）

#### P0-1 · MCS 超时后部分匹配被静默接受
- **文件**: `confflow/blocks/confgen/mapping.py:63`
- **覆盖率**: lines 66-87 仅 77%，超时分支未测

```python
# 当前代码
if not res.canceled and res.numAtoms == 0:
    raise ValueError("MCS search found no common substructure")
# 若 res.canceled=True 且 res.numAtoms > 0（超时但有部分匹配），
# 继续用部分匹配检查 coverage_ratio，但 ≥70% 时静默通过。
# 部分匹配可能导致链索引被映射到错误原子，后续构象旋转全部错误。
```


**修复方向**: `res.canceled` 时无论 `numAtoms` 是否为 0，均 raise 或 warn+degrade，不静默放行。

---

#### P0-2 · `transfer_chain_indices` 对称分子只取第一个 MCS 匹配
- **文件**: `confflow/blocks/confgen/mapping.py:97-141`

```python
ref_match = ref_mol.GetSubstructMatch(patt)     # RDKit 只返回一个任意匹配
target_match = target_mol.GetSubstructMatch(patt)
```

对 C₂ᵥ/C₃ᵥ 等对称分子，`GetSubstructMatch` 返回任意一个等价匹配，可能将链原子映射到完全不同的原子上。

**修复方向**: 枚举 `GetSubstructMatches` 并选取链原子索引差异最小的映射；或记录 warning，提示对称分子需手动指定。

---

### P1 — 可靠性风险（可能导致流程中断或结果丢失）

#### P1-1 · `_execute_tasks` 中 `fut.result()` 没有异常保护
- **文件**: `confflow/calc/manager.py:295`
- **覆盖率**: lines 301-302 未测

```python
for fut in as_completed(futures):
    res = fut.result()          # 工作进程被 OOM Kill / 被信号终止时此处 raise
    self.results_db.insert_result(res)
```

进程崩溃时 `fut.result()` 抛 `BrokenProcessPool` / `concurrent.futures.process.BrokenProcessPool`，此异常会穿透 `with ProcessPoolExecutor` 块，`finally` 中 DB `close()` 虽会执行，但已完成任务结果不会被写入 DB（DB.insert_result 在 raise 之前从未调用），导致任务结果永久丢失。

**修复方向**: 包裹 `res = fut.result()` 在 `try/except (BrokenProcessPool, Exception) as e`，记录失败任务并继续。

---

#### P1-2 · 停止信号后 `ProcessPoolExecutor` 等待所有已提交任务完成
- **文件**: `confflow/calc/manager.py:283-293`

```python
for fut in as_completed(futures):
    if os.path.exists(self.config["stop_beacon_file"]):
        self.stop_requested = True
        break         # break 后 with-block __exit__ 调用 shutdown(wait=True)
```

`break` 仅退出 `for` 循环，`ProcessPoolExecutor.__exit__` 依然等待所有已提交的 future 执行完毕（Python ≥3.9 默认行为）。对于大型任务集，"停止"可能需要等待数小时。

**修复方向**: 用 `executor.shutdown(wait=False, cancel_futures=True)` 替代隐式 exit，或将 STOP 文件轮询逻辑下沉到 worker 内部。

---

#### P1-3 · resume 时若步骤输出文件缺失，`current_input` 静默保留旧值
- **文件**: `confflow/workflow/engine.py:196-211 `

```python
if resume_from_step >= i:
    expected_output = ...
    if os.path.exists(expected_output):
        current_input = expected_output   # 文件不存在时跳过赋值，无警告
    continue
```

若重启前某步骤输出文件被手动删除或磁盘满，resume 时该步骤的 `current_input` 不更新，后续步骤会以错误数据运行，且不会报错。

**修复方向**: 文件不存在时发出 `logger.warning` 并记录到统计；或直接 `raise RuntimeError` 让用户 re-run。

---

#### P1-4 · 多输入时第一步为 `calc` 步骤，仅 `current_input[0]` 被使用
- **文件**: `confflow/workflow/engine.py:_run_calc_step` 调用处

```python
manager.run(
    input_xyz_file=current_input if isinstance(current_input, str) else current_input[0]
)
```

若工作流没有 confgen 步骤（多输入直接进 calc），`current_input` 是 `list[str]`，只有第一个文件被处理，其余静默丢弃。没有任何 warning 或错误提示。

**修复方向**: 在 `_run_calc_step` 入口检查 `isinstance(current_input, list) and len(current_input) > 1` 时发出 warning，说明只使用第一个文件（或改用 confgen pipeline 合并多输入）。

---

#### P1-5 · `greedy_permutation_rmsd` 几乎 0% 测试覆盖
- **文件**: `confflow/blocks/refine/rmsd_engine.py:195-262`
- **覆盖率**: lines 202-262 完全未触达

该函数是对称感知 RMSD 的核心，逻辑包含 4 种坐标轴符号变换 + 贪心元素匹配。130 行 Numba JIT 代码完全没有独立单元测试，任何算法 bug 均无法被现有测试集发现。

**修复方向**: 针对已知对称分子添加 `test_greedy_permutation_rmsd_{identical,mirror,symmetric_mol,large}` 系列测试。

---

#### P1-6 · `workflow/validation.py` 链验证路径完全未测
- **文件**: `confflow/workflow/validation.py:108-127`
- **覆盖率**: 70%，lines 108-127（`validate_chain_bonds=True` 分支）为 0%

该分支调用 `load_mol_from_xyz` + `ChainValidator.validate_mol`，在金属原子、非标准键型等情况下行为未知。

**修复方向**: 补充 `test_validate_inputs_compatible_chain_validation_enabled` 测试用例。

---

### P2 — 可维护性 / 调试性问题

#### P2-1 · `except Exception: pass`（无日志）
- **文件**: `confflow/workflow/engine.py:177`

```python
try:
    shutil.copy2(config_file, os.path.join(failed_dir, ...))
except Exception:
    pass    # 磁盘满、权限错误等 I/O 故障完全静默
```


**修复方向**: 改为 `except OSError as e: logger.debug(f"copy config failed: {e}")`。

---

#### P2-2 · `_coords_lines_to_xyz` 变量命名混淆
- **文件**: `confflow/calc/scan_ops.py:38-61`

```python
for tok in reversed(p[1:]):   # 逆序遍历 x,y,z tokens
    xyz.append(float(tok))
    if len(xyz) == 3: break
z, y, x = xyz   # xyz[0] 实际是 z，xyz[2] 实际是 x（逆向赋值）
out.append((sym, float(x), float(y), float(z)))  # 最终顺序正确但极易误读
```

逻辑正确，但命名反直觉，维护者极易在未来修改时引入 `x/y/z` 顺序 bug。

**修复方向**: 改为正向读取并去除逆序绕路：`x, y, z = float(p[-3]), float(p[-2]), float(p[-1])`。

---

#### P2-3 · `get_topology_hash_worker` 中 O(N²) 全距离矩阵
- **文件**: `confflow/blocks/refine/rmsd_engine.py:300-311`

```python
delta = coords[:, np.newaxis, :] - coords[np.newaxis, :, :]   # (N, N, 3) 全矩阵
dist_sq = np.sum(delta**2, axis=-1)                            # (N, N)
```

N=500 时 delta 矩阵占 ~3 MB，N=2000 时 ~48 MB。对于大批量 refine，每个 topology-group worker 独立分配此矩阵。可用 `scipy.spatial.cKDTree` 替代。

**修复方向**: topology hash 使用 `cKDTree.query_pairs` 或仅在小分子时使用全矩阵，超过阈值切换稀疏策略。

---

#### P2-4 · `FailureTracker._update_summary` 的 `except Exception: break` 截断日志
- **文件**: `confflow/workflow/stats.py:194`

```python
except Exception:
    break   # failed.xyz 任何格式异常都静默截断 summary
```


**修复方向**: `except (ValueError, IndexError): logger.debug(...); break` 并记录截断位置。

---

#### P2-5 · Numba 覆盖率假盲区：`collision.py` 40%
- **文件**: `confflow/blocks/confgen/collision.py:56-84`

`check_clash_core` 被 `@numba.njit` 编译为本地代码，Python coverage 无法追踪其函数体。实际上函数通过集成测试被调用，但 **没有独立单元测试** 验证碰撞检测的正确性与边界情况（如 1-4 拓扑过滤、极小键长、H-H 对）。

**修复方向**: 添加 `test_check_clash_core_{no_clash,1_4_filtered,heavy_clash,hydrogen_pair}` 测试；并在 `pyproject.toml` 中排除 `@numba.njit` 函数体的覆盖率计数。

---

### P3 — 低优先级（代码风格 / 未来风险）

#### P3-1 · `run_generation` 按文件失败改 `continue`，不向上传播
若所有输入文件均处理异常，`all_confs_data` 为空，`search.xyz` 不生成，引擎层再抛 `RuntimeError("confgen did not produce search.xyz")`，真实原因被淹没。

**修复方向**: 若所有文件均失败，改 raise；若部分失败，在统计中标记。

#### P3-2 · `process_topology_group` BATCH_SIZE 硬编码
`BATCH_SIZE = min(len(candidates), max(100, workers * 20))` 缺乏用户配置入口；超大数据集下批次策略不可调优。

#### P3-3 · `core/logging.py` 63% 覆盖率
日志模块本身有较多未测路径（`add_file_handler`, `set_level` 等），虽为工具类，但其行为直接影响 debug 能力。

---

## 三、测试缺口汇总

| 缺口 | 文件 | 目标测试 | 补测入口 |
|------|------|----------|----------|
| `greedy_permutation_rmsd` 0% | `rmsd_engine.py:195` | 对称/大/共线分子 | `test_refine.py` 或新 `test_rmsd_engine.py` |
| `check_clash_core` 已 JIT 无 unit test | `collision.py:56` | 碰撞/非碰撞/1-4过滤 | `test_confgen.py` 或新 `test_collision.py` |
| MCS 超时分支 | `mapping.py:63` | `res.canceled=True` mock | `test_confgen.py` |
| chain 验证路径 | `validation.py:108` | `validate_chain_bonds=True` | `test_validation.py` |
| `fut.result()` 进程崩溃 | `manager.py:295` | mock `BrokenProcessPool` | `test_calc.py` |
| stop beacon + 大任务集 | `manager.py:289` | 模拟 STOP 文件 | `test_calc.py` |
| resume 步骤输出缺失 | `engine.py:196` | 删除中间输出后 resume | `test_engine.py` |
| multi-input → calc（无confgen）| `engine.py` | workflow 含 calc 首步 | `test_engine.py` |
| `_coords_lines_to_xyz` 带额外 token | `scan_ops.py:38` | 带标签行的 Gaussian coords | `test_rescue.py` |
| auto_clean enabled path | `manager.py:450` | `auto_clean=true` 配置 | `test_calc.py` |

---

## 四、整改阶段路线图

状态说明：本节保留原始整改路线图；其中部分事项已提前完成，实际完成情况以上一节“本轮执行结果”为准。

### Sprint 1（1-2天）— 修正确性问题，零风险测试先行

1. **P0-1** `mapping.py`: 当 `res.canceled=True` 时 raise `MCSTimeoutError`，添加覆盖该分支的测试
2. **P0-2** `mapping.py`: 对称分子警告（选择最优映射或 warn），添加 C₂ᵥ 分子回归测试
3. **P2-1** `engine.py:177`: `except Exception: pass` → `except OSError as e: logger.debug(...)`
4. **P2-2** `scan_ops.py`: 重写 `_coords_lines_to_xyz` 去掉逆序绕路，添加对应单元测试

验证: `pytest tests/ -q --tb=short` 全通过；拒绝任何新增的 `except Exception: pass` 模式。

---

### Sprint 2（2-3天）— 补关键测试，消除主要测试盲区

5. **P1-5** 为 `greedy_permutation_rmsd` 添加 4 个专项测试（identical/mirror/symmetric/large）
6. **P2-5** 为 `check_clash_core` 添加专项测试（并在 `pyproject.toml` 排除 Numba JIT 行体覆盖计数）
7. **P1-6** 添加 `validate_chain_bonds=True` 路径测试
8. **P1-3** 在 `engine.py` resume 路径添加 logger.warning，补 `test_resume_missing_output` 测试

验证: `pytest tests/ -q --cov=confflow --cov-report=term-missing`
目标: `rmsd_engine.py` ≥ 80%，`validation.py` ≥ 85%，`collision.py` 专项测试固化 4 个场景。

---

### Sprint 3（1-2天）— 可靠性补强

9. **P1-1** `manager.py`: 将 `fut.result()` 包裹在 `try/except`，处理 `BrokenProcessPool`，补 mock 测试
10. **P1-2** `manager.py`: 实现可取消的批量执行（Python 3.9+ `shutdown(cancel_futures=True)` 或每 future 独立 cancel）
11. **P1-4** `engine.py`: 多输入首步为 calc 时添加 warning，补 `test_multi_input_calc_first_step` 测试

验证: 全量测试；重点审查 calc/manager.py 的 stop beacon + 进程崩溃 mock 测试通过。

---

### Sprint 4（可选，性能优化）— 大数据集稳定性

12. **P2-3** `rmsd_engine.py`: `get_topology_hash_worker` 改用 `cKDTree.query_pairs`，添加 N=1000 分子的基准测试
13. **P3-2** `process_topology_group`: 暴露 `batch_size` 参数，添加配置接入
14. **P3-3** 补 `core/logging.py` 覆盖率到 ≥ 80%

---

## 五、验证命令参考

```bash
# 基线全量
pytest tests/ -q --tb=short

# 分层聚焦
pytest tests/test_refine.py tests/test_confgen_refine_fallbacks.py -v         # refine
pytest tests/test_calc.py tests/test_calc_full.py tests/test_rescue.py -v     # calc
pytest tests/test_engine.py tests/test_step_handlers.py -v                    # workflow

# 覆盖率热点追踪
pytest tests/ --cov=confflow --cov-report=term-missing 2>&1 \
  | grep -E "mapping|rmsd_engine|collision|engine|manager|rescue"

# 异常处理模式审计（sprint 1 后应为 0 结果）
grep -rn "except Exception:" confflow/ | grep -v "# ok"
grep -rn "except Exception:$" confflow/ -A1 | grep "pass$"
```

---

## 六、不在本次整改范围内的内容

| 项目 | 原因 |
|------|------|
| viz 报告样式 | 无正确性风险，UI 迭代另排期 |
| Numba JIT 算法本身的数学正确性 | 需领域专家介入，非工程 sprint 范畴 |
| TS rescue 扫描策略调参 | 启发式参数，依赖化学领域验证 |
| full mypy strictness | 类型注解尚有 D101-D107 deferred，需单独排期 |
