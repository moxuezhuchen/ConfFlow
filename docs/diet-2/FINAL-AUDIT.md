# DIET-2 + FIX-1 最终完成度审计（2026-10-07）

> 事实基准：ConfFlow `main = 5ca27fa`，JobDesk `master = 9278fc0`。PR 号均为 `moxuezhuchen/ConfFlow` 与 `moxuezhuchen/jobdesk-v2`。
> 结论先行：DIET-2 的 P0、R1、R2、N1–N4、T0、T1（第一波）、S1、S2/S3（仅确凿项）、L3'、G1 已完成并合并；FIX-1（FIX-1R、FIX-1D）及其配套（L1、L2、E1、J1/J3）已完成并合并。**未达成的是行数目标**：实际约 176k 行，离方案估计的约 11 万行差距大，原因与数字见 §3。

## 1. 逐项状态

| 项 | 状态 | 证据（PR / 结果） |
|---|---|---|
| FIX-1R 环修复、FIX-1D coordination 多起点 | 完成 | #106、#107；TS1 default 4/12、rigid 1/12、flexible 3/12 |
| L1 producer 按 capability 拆分 | 完成 | #112 |
| E1 `monomer_conformers` recipe | 完成（保留，只组合现有卡片） | #113 |
| J1 结构预览（含 JD）、J3 环形式编辑 | 完成，成对发布 | #115、JD #23 |
| L2 旧 service 兼容链退役（两仓） | 完成 | #117、#120、JD #24 |
| P0.1–P0.4 护栏 | 完成 | #108–#116；覆盖率三层检查、行数预算、规则 G15–G20、两级验收 |
| R1.0–R1.6 单仓退役 | 完成，里程碑验收通过 | #118、#119；全量测试、TS1 三份、93 份 engine 报告、契约/boundary 摘要均与基线一致 |
| R2.0–R2.4 与 JobDesk 成对退役 | 完成，里程碑验收通过 | #121（含能量字段）、#122、#123、JD #25–#27 |
| N1 服务器级核数配额 | 完成 | #125 |
| N2 外部脚本步骤（N2.1–N2.4、N2.6） | 完成 | #128、JD #28；N2.5 在 `tspes.py` 仓库，ConfFlow 侧接口文档见 `docs/diet-2/N2.5-TSPES-SUMMARY-JSON.md` |
| N3 按能量筛选 | 完成 | #124 |
| N4 `ensemble_refine` recipe | 完成（核心；脚本预筛需手动插入 N2 步骤） | #127、JD #27 |
| T0 工具、T1 第一波 | 完成 | #126、#129 |
| T2（ConfGen 测试整合） | **不做**（用户同意） | 零损失候选上限约 8.0k 行，实际可得 400–800 行，且需重做 golden 捕获名单 |
| S1 docstring 压缩 | 完成 | #130（−3253 行，AST 逐文件等价） |
| S2/S3 | 仅确凿项（用户决定） | #131（−12 行） |
| L3' TS1 原始日志外移 | 完成 | #131（4.58 MB，可由 `d5a40ae` 取回，sha 已核对） |
| G1 能力文档 | 完成 | #131，`docs/CONFGEN_CAPABILITIES.md` |
| R7 葡萄糖缺口 | 按用户修订的验收文字完成 | `docs/confgen-fix/R7-GLUCOSE-RECALL.md`（#126） |
| 统一修复清单 | 大部分完成 | #131 的清理提交；余项见 §4 |

## 2. 门禁与验证（不是自说自话）

- 每个里程碑（P0、R1、R2）做过全量测试 + 全部 golden：TS1 三份、`test_confgen_[pv]*.py` 的 93 份 engine 报告、contract/boundary 摘要，对 `R1.0` 基线逐字节比对；差异只有已声明的（FIX-1D 的 10 个科学差异、E1/J1/R2.2/R2.0-logic/N2/N3/N4 的契约新增或移除，均有按名称的差异证明）。
- 跨仓：JobDesk master 整仓对每一个改契约的 ConfFlow 卡都跑过；JobDesk 侧只出现已知环境类失败。
- 主干保护：`test-matrix` 四个 Python 版本 + `changed-coverage`（新增行覆盖 ≥85%）是 PR 必需检查；全量 `coverage`（含冻结的子系统下限 execution 88.81%、persistence 88.88%、workflow 87.66%、confgen 内核 86.75%，容差 5 个百分点）在 main 与手动里程碑门上运行，R1 之后实测均在容差内。

## 3. 行数（`tools/loc_budget.json` 同一口径，物理行）

| | 生产 | 测试 | 合计 |
|---|---|---|---|
| 方案基线（`213b306`） | 88,333 | 99,227 | 187,560 |
| P0 开始时（含 FIX-1D、L1、E1、J1 之后） | 97,753 | 120,560 | 218,313 |
| **现在** | **80,081** | **96,182** | **176,263** |

- R1 + R2 实际删除生产 16.2k、测试 26.0k，与方案估计的被删量（约 14.5k / 25–30k）一致；N1–N4 新增约 2.3k 生产、约 2.4k 测试（N2 生产预算经用户批准由 800 提到 1120）。
- 离 11 万行的差距：方案之后 FIX-1D、L1、E1、J1、L2 新增约 9.4k 生产、约 21k 测试；T1 在“每个删除必须有替代测试”（W1）下仅净减约 230 行（零覆盖损失的候选多为共用代码的参数化变体，全部删光上限也只有约 16.9k 行）；S1 只做零风险的 docstring 压缩，S2/S3 只做确凿项。
- 要继续压需要放宽 W1 或降低覆盖，属取舍，已向用户说明，本轮未做。
- 预算上限已于本次棘轮到实际值（只降不升）。

## 4. 偏离与用户决定（记录）

- `ensemble` 输出形式、`profile_ensemble.py`、`multi_output.py` 保留：留存的 ConfGen 步骤在编译期硬编码 `result_profile="ensemble"`，不改语义摘要就删不掉；后续可开小卡收敛 NEB 遗留的角色处理（用户同意保留）。
- N4 的步骤顺序为 `dedup → opt → refine → freq → filter`（方案字面顺序会违反 N3 的“structure 与 results 同源”冻结规则）。
- R7：strict 15° 保留为严格诊断并列 basin 召回；A（默认 6 元环加 B）另开卡评估，未实施；C（畸变椅种子）冻结；D 已做，结论为不需要新增环种子。
- 临时预算例外：L2 投影卡（+402 行）已在旧链删除卡中撤销；R1.1 生产→测试转移已体现在棘轮后的上限中。
- N2.6（JobDesk 脚本表单）生产 +756 行，超出声明的 500 行；JobDesk 没有行数预算守卫，已告知用户。

## 5. 遗留与建议

- JobDesk：`PROVENANCE` 的 `source_commit` 可同步到最新 ConfFlow main（内容无变化）；CI 偶发 Qt 段错误（`tests/gui/test_cards_drawer.py`，重跑通过）；让 JobDesk 的 CI 也检出 ConfFlow 以真实执行 J1 的 4 个 live 测试（目前在 CI 中跳过，本地与集成验证里真实通过）。
- 文件名含 `_coverage` 的 2 个测试文件改名并缩减 AP-108 豁免清单（可选）。
- `ensemble` 遗留收敛小卡（见 §4）；A（默认形式集）评估卡；N2.5（`tspes.py` 的 `--summary-json`，在其仓库实现）。
- 用户的 `/opt/jobdesk-v2-v4` 检出正处于 `chore/slim-phase0` 分支（他们自己的瘦身工作）；其基底早于本轮合入 JobDesk master 的若干提交，合并回 master 时可能需要处理冲突。
