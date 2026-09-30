# 结构与能量可靠性审计

审计日期：2026-09-30。对象：当前工作区的 V4 正式执行路径，重点覆盖原生输出解析、科学检查、结果绑定与能量类型。修改未发布。

结论：本次修复减少了已复现的错误接受风险，并通过真实 ORCA 的正反例验证；不能据此保证任意方法、任意分子的最终结构与能量均正确。Gaussian 的相关能解析尚不支持，现已拒绝已知受影响的方法，避免错误交付 SCF 参考能。

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

两个程序的 parser_version 升级为 v2，Gaussian 的 adapter_version 也升级为 v2。版本进入执行环境身份，避免新规则沿用旧身份。

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

1. **Gaussian 的 post-SCF 方法存在能量解析缺口，现由输入拒绝保护。** 当前 `programs/gaussian/parsing.py::parse_energies` 读取 `SCF Done`，无相应行时退回 archive HF；没有读取 MP2/CC 等最终相关能字段。不能将这些方法的 SCF 参考能当作目标方法的最终电子能，也不能据此构造高层复合 Gibbs 能。`native_definition_errors` 现拒绝已知的 MP2–MP5、CCSD/CCD、QCISD/CISD/CID/CASSCF、常见双杂化和 CBS/G1–G4/W1 方法，包括已覆盖的 R/U/RO 前缀形式；这不是所有 Gaussian 方法的完整白名单。当前环境没有可用的 Gaussian 可执行程序，未做真实 Gaussian 验证。支持这些方法需要方法专属解析和真实日志回归之后才能开启。
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
