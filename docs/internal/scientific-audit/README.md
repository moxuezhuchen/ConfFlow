# 结构与能量可靠性审计

审计日期：2026-09-30。对象：当前工作区的 V4 正式执行路径，重点覆盖原生输出解析、科学检查、结果绑定与能量类型。修改未发布。

结论：本次修复减少了已复现的错误接受风险，并通过真实 ORCA 的正反例验证；不能据此保证任意方法、任意分子的最终结构与能量均正确。Gaussian 的相关能解析尚不支持，本次拒绝已知受影响的方法，避免错误交付 SCF 参考能。**该拒绝列表当时未用真实 Gaussian 二进制覆盖 Gaussian 的实际关键字拼写空间；后续独立复现（2026-09-30）证实存在绕过，见文末修复记录。**

## 已修复的问题

| 问题 | 原行为与影响 | 修复 |
| --- | --- | --- |
| ORCA 优化未收敛仍被接受 | 达到最大迭代次数后，ORCA 可以正常退出，执行器可能将非驻点的结构和能量标为完成 | 对标准计算的明确优化/SCF 未收敛信息产生错误诊断；执行器拒绝带错误诊断的结果，即使用户没有声明额外检查 |
| 解析器错误未决定完成状态 | 错误诊断可以被记录，但不进入执行器的失败判定 | 将原生解析器 ERROR 诊断纳入失败判定，失败结果不携带成功的科学结果 |
| ORCA 能量被读取为数字前缀 | `-1.2345E+02` 原先可能被读取为 `-1.2345`；非法 token 也可能截取合法前缀 | 读取完整 token，支持 E/e/D/d 指数，拒绝非法值及非有限值 |
| 较早的正常结束标记掩盖后续失败 | Gaussian/ORCA 日志中只要含正常结束标记就可能被认为成功 | 比较最后正常结束与错误结束标记；后续错误覆盖较早的成功 |
| 错误输出原子序列 | 部分坐标块或错误伴随文件可能改变元素、原子数量或顺序，导致能量绑定到错误结构 | 在结果归一化之前校验输出原子序列与本次输入一致；不一致返回解析失败 |
| 空 ORCA XYZ | 零原子 XYZ 可被解析为空几何 | 拒绝非正原子数 |
| Gaussian 未支持方法的参考能被误用 | MP2、CC 等方法的 SCF 参考能可能被当作目标方法的最终能量 | 在编译验证和渲染入口拒绝已知未支持的相关能、双杂化及复合能方法 |

两个程序的 parser_version 升级为 v2，Gaussian 的 adapter_version 也升级为 v2。当时的意图是版本进入执行环境身份、避免新规则沿用旧身份；**独立复现证实 ORCA 侧当时并未达成该目标（parser_version 仅在 `ExecutionEnvironment.metadata`，未进入 reuse/provenance/handoff 身份），已在本轮修复，见文末。**

ORCA 官方手册明确指出，未收敛优化仍可能产生正常结束标记，应检查优化自身的收敛信息：
https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations.html （4.1.12）。

## 真实 ORCA 验证

环境：本机 `/opt/orca611/orca`，ORCA 6.1.1，单核、256 MB。水分子，HF/STO-3G，`TightSCF Opt Freq`。这是一项程序与数据链路验证，不表示该模型具备研究目标所需的精度。

起始坐标（Å）：

```text
O  0.00 0.00 0.00
H  0.76 0.59 0.00
H -0.76 0.59 0.00
```

通过真实 WorkItemExecutor 执行与校验：

- 状态 `completed`。
- 电子能：`-74.965901192193 Hartree`。
- Gibbs 自由能：`-74.95926275 Hartree`。
- Gibbs 热校正：`0.00663844 Hartree`。
- `E + G_corr` 与原生日志的 `G` 差异小于 `1e-7 Hartree`（日志打印精度范围内）。
- 三个振动频率：`2169.82, 4139.63, 4390.66 cm^-1`，报告虚频数为 0。
- 最终结构原子序列及坐标与原生 XYZ 一致；日志坐标与 XYZ 最大分量差异为 `3.8519755e-7 Å`。

反例：将两个 H 移至 `(±1.5, 0.5, 0)`，使用 `Opt` 与 `%geom MaxIter 1 end`。真实日志同时含未收敛警告与正常结束标记。修复后同一执行器返回 `failed`，`geometry_not_converged`，不发布科学结果。

完整执行器验证记录见 [orca611-verification.json](orca611-verification.json)。原始输入、输出和校验脚本位于本次环境的 `/tmp/confflow-scientific-audit/`，临时目录不属于持久交付。

同一水分子还通过真实 `V4RunApplication` 完整运行与结果发布；落盘 `step_result.json` 的电子能和 Gibbs 能与上述值一致，所有科学结果均绑定到同一最终结构，结果清单的 result_id 与落盘结果一致，且所有发布产物的 SHA-256 均与清单吻合。`run_result.json` 状态为 `completed`。记录见 [application-verification.json](application-verification.json)。

## 仍需约束的科学适用范围

1. **Gaussian 的 post-SCF 方法存在能量解析缺口，现由输入拒绝保护。** 当前 `programs/gaussian/parsing.py::parse_energies` 读取 `SCF Done`，无相应行时退回 archive HF；没有读取 MP2/CC 等最终相关能字段。不能将这些方法的 SCF 参考能当作目标方法的最终电子能，也不能据此构造高层复合 Gibbs 能。本轮的修复记录见文末：该缺口当时只用字符串模式拒绝“已知”方法，**独立复现（2026-09-30，真实 Gaussian 16 Rev C.02）证实 `B2PLYPD`、`B2PLYPD3`、`mPW2PLYPD`、`DSDPBEP86`、`G4MP2`、`G3B3`、`G3MP2B3` 等真实关键字可绕过并静默发布 SCF 参考能**；当时的回归测试使用了 Gaussian 不接受的拼写 `DSD-PBEP86`，因此没有覆盖真实生产关键字。当前实现已改为方法族能力模型（静态拒绝）＋发布时运行证明（运行日志中最后一个 `SCF Done` 之后不得出现其他方法的最终能量标记），详见文末。
2. **正常结束、优化收敛与极小值是不同证据。** 仅 Opt 不能验证局部极小值。需要同一结构、同一预期模型的完整频率计算，并声明 `frequencies_required` 和 `imaginary_frequency_count`；对于普通极小值设 expected=0。过渡态通常要求一个有效虚频，还需检查振动方向及反应路径连接。
3. **默认存在 10 cm^-1 噪声阈值。** 当前解析/归一化忽略绝对值不超过该阈值的频率。因此“报告虚频数为零”不等于原生日志所有频率均为正；软模必须结合原生日志审查。
4. **最终最低能构象仅代表已生成并保留的候选集合。** 此次审计未证明任意分子的全局最低能构象已被搜索到。还需检查构象采样覆盖、拓扑和立体化学保持、去重阈值，以及比较对象的组成、电荷、自旋态和计算模型。
5. **能量类型不能混用。** V4 standard 将电子能、全 Gibbs 能和热校正分别保存；复合能分析使用 `E_high + G_corr_low`。应显式限定两个量的来源步骤，并校验结构对应关系、温度与模型假设。没有频率数据时，单独 expected=0 的虚频数量检查按现有合同允许通过，不能替代 `frequencies_required`。
6. **此次未提供具体生产任务的原始输出。** 本次验证是代码审计、回归测试和水分子正反例；没有认证任何已有生产计算结果，也没有系统验证 IRC、NEB、GOAT、全部程序版本或全部理论方法。

## 软件验证

新增 `tests/v4/test_scientific_reliability.py`：37 项回归覆盖完整数值 token、错误终止覆盖、非收敛拦截、空 XYZ、解析器错误门禁、输入输出原子一致性、Gaussian 未支持方法拒绝及 HF/DFT 兼容性。

针对当前 ConfFlow 的解析器、执行器、原生定义验证、渲染、协议快照及上述回归的最终测试：262 项通过。Ruff check 与 `git diff --check` 通过。验证期间有其他维护提交合入，已在当前代码上重跑相关测试及真实 ORCA 应用层验证。

全量测试应指定独立临时目录，例如：

```bash
.venv/bin/python -m pytest -q -m 'not cross_repo' --disable-warnings --basetemp=/tmp/confflow-audit-final-full
```

当前工具环境的进程号可能在不同隔离执行环境中重复，不能依赖 tests/conftest.py 的 PID 临时目录命名来隔离并发 pytest。早期受临时目录干扰的运行不作为有效结论。

一次独立目录的全量运行记录为 4170 passed、11 failed、7 skipped、7 errors（4195 项，805.14 秒）。其中协议快照过期已重新生成并验证通过；10 项打包测试受环境阻碍：继承的 PYTHONPATH 包含不可访问的 Gaussian 目录，清理后隔离构建仍因代理无法下载 setuptools/wheel 而失败。它们未进入科学计算结果验证，因此不声称全量测试全部通过。

按用户后续明确的范围要求，此审计只对 ConfFlow 计算与结果输出负责；JobDesk 的开发状态及跨项目验收不作为本次科学正确性结论的条件，后续测试排除 cross_repo。

---

## 修复记录（2026-09-30）：独立复现与最小修复

对上述未发布工作的独立只读复现（真实 Gaussian 16 Rev C.02 与真实 ORCA 6.1.1，原始证据见协调器仓库
`reports/scientific-reliability-independent-reproduction.md`）确认了两个 blocker：

- `GAUSSIAN_SILENT_WRONG_ENERGY = CONFIRMED`：真实关键字 `B2PLYPD`、`B2PLYPD3`、`mPW2PLYPD`、`DSDPBEP86`、
  `G4MP2`、`G3B3`、`G3MP2B3` 通过当时的拒绝列表，工作流 `completed` 且无任何诊断；发布的电子能与真实
  最终方法能相差 7.6–204 kcal/mol（`G4MP2` 还发布了内部步骤的 Gibbs 与频率）。`mPW2PLYPD3` 在该
  Gaussian 版本中不是合法关键字（语法错误），因此不构成同一缺陷。
- `ORCA_VERSION_IDENTITY_SAFETY_GAP = CONFIRMED`：parser 语义变化没有进入 reuse/provenance/handoff 身份；
  由旧 parser 代码（维护基线 a9670a7）产生的持久结果被新 parser 代码原样复用（`reuse_hit`、无第二次执行），
  混合 parser 版本的远端 handoff 通过 adapter 检查，最终 provenance 中看不到 parser 版本。

### 旧审计的结论边界

本审计（旧）**仍然有效，但适用范围有限**（`STILL_VALID_WITH_LIMITED_SCOPE`）：

- 旧审计用真实 ORCA 验证了正反例；这些修复在独立复现中全部保持有效（7/7）。
- 旧审计没有用真实 Gaussian 验证；其未支持方法语料没有覆盖真实 Gaussian 关键字拼写空间。
- parser 版本/reuse 身份维度不在旧审计范围内，旧审计对其结论的表述（“版本进入执行环境身份”）当时并无测试支撑。

旧审计验证的流程本身是正确的，其结论不能外推到所有 Gaussian 方法与 provenance 维度。

### 本轮修复

- **Gaussian**：新增唯一权威模块 `programs/gaussian/energy_semantics.py`。编译/渲染路径按方法族静态拒绝
  （post-SCF、双杂化、复合、响应方法，含真实拼写与 R/U/RO 前缀；响应族包含 `CIS`、`CIS(D)`、
  `CIS=(NStates=…)`、`TD`、`TDA`、`TD=(NStates=…)`、`ZINDO` 等真实关键字形式），解析/发布路径要求
  “最后一个 `SCF Done` 之后没有其他方法的最终能量标记”（`E2(`、`E(Method)=`、`E(TD-HF/TD-DFT)`、
  `E(CIS/TDA)`、`MPn=`、`CCSD=`、复合能摘要、`MCSCF=` 等），否则产生
  `native_energy_semantics_error`（ERROR），执行器在未声明任何检查时也会拒绝发布。archive-only 日志
  不再视为可发布证据。独立对抗审查发现的响应方法缺口（`TD`/`TDA`/`CIS=(...)`）已按此修复并加入真实
  G16 回归。
- **二进制相关限制**：Gaussian 16 Rev C.02 将常见拼写 `PBE0` 执行为 PBE0DH（双杂化），静态验证按其他
  版本语义仍接受该拼写，但运行证明会拒绝发布（fail closed）；`PBE1PBE` 是仍可正常发布的杂化泛函对照。
- **ORCA**：`adapter_version` 升级为 v2（parser 语义变化）；`parser_version` 进入持久 producer provenance
  并参与 reuse 比较；远端 handoff 的 contract set 增加 `parser` 并由 worker 校验。旧身份结果不再透明复用，
  混合版本 handoff fail closed。
- **版本**：ORCA adapter v1→v2；Gaussian adapter/parser 与 ORCA parser 保持本轮候选定义的 v2（旧候选未进入
  任何基线，不产生身份冲突）。边界协议版本与 wire 结构不变，仅 capability digest 随版本移动。
