# A1 调查报告 — v3 ConfGen 发布 0 个结构时仍报 completed（只读调查，未改任何代码）

- 被测版本：ConfFlow `main` = `afdf9df`（`/opt/ConfFlow/.venv`，只读运行，输出在 `/tmp/a1/`）。
- 输入：6 个共线碳原子（无氢），`path: 2→5`、角度 `[0.0, 120.0]`（`a1-inputs/lin.xyz`）。四个最小工作流见 `a1-inputs/w1..w4.yaml`，都用 typed v3 的 `confgen`，下游分别为：无（w1）、`refine`（w2）、`orca` 计算（w3）、`deduplicate` 再 `orca` 计算（w4）。计算用 `tests/v4/fakes/fake_orca.py`。

## 1. 复现：ConfGen 本身（executor 直接调用）
```
collinear C6, path 2->5: status=completed structures=0
  diag=[('confgen_completed', 'confgen v3 published 0 leaf structures (8 raw targets, 0 sampled out)'),
        ('confgen_path_resolved', 'Path P1: 2(C)-3(C)-4(C)-5(C); move end endpoint 5; rotors: 2-3, 3-4, 4-5')]
tight C6 (对照，会产生结构): status=completed structures=8 ...
```
8 个原始目标全部被丢弃，发布 0 个叶子，状态仍是 `completed`；唯一的线索是一条 info 级诊断。

## 2. 工作流层面的下游行为（`confflow v4 validate` 全部通过，即编译期不拒绝）

| 工作流 | 下游步骤 | 退出码 | 结果 |
| --- | --- | --- | --- |
| w1 | 无 | 0 | `status: completed`；`s_gen` completed，计数 `{"completed": 1}`，结构 0 |
| w2 | `refine`（`structure_transform`） | **1** | **未处理的异常（Python 回溯）**：`DomainError: step 's_refine' is not assemblable and never executes: cardinality_error: port 'structure' expected one_or_more but matched 0`；标准输出没有 `--json` 报告；磁盘上的 `run_result.json` 写成 `status: failed`，只有 `s_gen: completed` |
| w3 | `orca` 计算（每个结构一个 work item） | **0** | **`status: completed`，`s_opt` 也是 completed，但计数 `{"cancelled": 0, "completed": 0, "failed": 0}`、`results: []`**——一次计算都没有执行却报成功 |
| w4 | `deduplicate` 再 `orca` 计算 | **1** | 同 w2：`step 's_dedup' is not assemblable…`，回溯，`run_result.json` 为 failed |

原始证据（w3，`run-w3/run_result.json`）：
```
s_gen completed {"cancelled": 0, "completed": 1, "failed": 0}
s_opt completed {"cancelled": 0, "completed": 0, "failed": 0}
results: []
```
w2 的回溯末尾（`confflow/application/v4_run.py:585`）：
```
raise DomainError(f"step {planned.step_id!r} is not assemblable and never executes: {codes}")
```
对应代码：`assemble_work_items` 报告的装配错误在 `v4_run.py:_run_generation` 里被当成异常抛出，而不是转成该步骤的 failed 结果。

## 3. 结论：有三个独立问题，危险程度不同
1. **源头（ConfGen）**：发布 0 个结构却报 completed。
2. **最危险**：每结构一个 work item 的计算步骤，在输入为 0 时会**静默成功**（w3）。ConfGen 之外，任何会产出 0 个结构的上游（比如把所有结构都过滤掉）都会触发同样的静默成功。
3. **次要但丑**：`refine`/`deduplicate` 遇到空输入时不是结构化失败，而是未处理异常 + 没有 `--json` 报告（w2、w4）。

## 4. 方案（你倾向"失败"；以下按"失败"写）
| 层 | 改动 | 说明 |
| --- | --- | --- |
| A1-1 源头 | `confgen_executor._run_v3`：叶子数为 0 时返回 `FAILED`，诊断码例如 `confgen_no_structures`，消息带原始目标数、被丢弃原因分类 | 最小改动，直接修掉 w1，并让 w2、w3、w4 在源头就失败，下游不再被执行 |
| A1-2 下游 | 每结构 work item 的步骤在必需输入为 0 个结构时，装配阶段把该步骤标成 failed（原因 `no_input_structures`），而不是 completed | 修掉 w3 一类（与 ConfGen 无关的 0 输入）；需要先确认"部分/可选输入"语义 |
| A1-3 下游 | `v4_run.py:585` 的 `DomainError` 转成该步骤的 failed 结果并写入 `run_result.json`，`--json` 照常输出 | 修掉 w2、w4 的回溯 |

风险与需要你确认的点：
- 个别场景下"0 个结构"可能是用户想要的（例如严格的 `max_clashes` 把所有构象都筛掉后希望继续后续分析）；若要保留，应是显式的"允许空"开关，默认失败。
- 改成失败是**行为变化**，会让今天"completed 但为空"的运行变成 failed；JobDesk 对 failed 的展示需要核对（两仓成对检查）。
- A1-2 的范围（只管 ConfGen 的下游，还是通用）需要你定。

## 5. 建议
先做 A1-1（单点、最小、立刻消除静默成功的主要来源），A1-2/A1-3 作为紧随其后的第二张卡；三者都带最小工作流回归测试（即 w1 到 w4 的行为各一条，共线 C6 输入）。等你批准"失败"与 A1-2 的范围后出补丁。
