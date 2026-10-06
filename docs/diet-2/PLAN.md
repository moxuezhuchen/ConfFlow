# ConfFlow 瘦身与聚焦方案（DIET-2）

> 状态：**v1.1 — 已批准进入 P0（2026-10-06）**。Q1、Q2 已确认（§0.3）。P0 结束时的里程碑级验收是下一个审查点：通过后才能进入 R1（§2 末）。其余待确认项按里程碑分别确认。
> 作者角色：方案与验收（不执行）。通用规则与验收协议沿用 `docs/process/RULES.md`（G1–G14）与 `docs/process/ACCEPTANCE.md`；本文件只写不同之处。
> 事实基准：ConfFlow `main` = **`213b306`**（PR #105 `fix/confgen-1a` 合并，2026-10-05）。文中 `文件:行号` 均基于该提交；执行时先用符号锚点定位，行号只作核对。
> JobDesk：`moxuezhuchen/jobdesk-v2`，基准沿用 `docs/confgen-fix/PLAN.md` 文首记录的 `5c90732`。方案作者的会话无权访问该仓库，JobDesk 侧的锚点由 R2.0 现场核实。
> 与 `docs/confgen-fix/PLAN.md` 的关系：FIX-1R、FIX-1D 不变；L1、L2、L3、E1 的范围由本文件调整（§1.3）；其余卡片不受影响。

---

## 0. 背景与决策

### 0.1 诊断（@213b306，只统计 `.py`）

| | 9 月 19 日 | 现在 | 说明 |
|---|---|---|---|
| 生产代码 `confflow/` | 26,829 | **88,333** | 其中 docstring 16,760 行（19%），空行、注释、import 13,435 行 |
| 测试 `tests/` | 29,199 | **99,227** | 按阶段命名的 `test_v42_*`…`test_v46_*` 共 57 个文件、41,869 行；`*_coverage*`、`*_parity*`、`*_hardening*`、`*_crash_matrix*` 等 25 个文件 |
| 合计 | 57,159 | **196,632** | 增长几乎全部发生在 9 月 22–26 日（V4 在 5 天内生成） |

膨胀的主要原因（按影响排序）：

1. 覆盖率门槛 `fail_under = 85`（`pyproject.toml:142`）与高密度防御式代码相互放大：生产代码有 2,607 个 `raise`、1,579 次 `isinstance`，每个分支都需要测试来维持 85%。
2. "功能 + 补齐门禁测试"的生成节奏，测试只加不并；测试代码量超过生产代码。
3. 实现了一批从未接入入口、或用户不使用的功能（§0.2 D2）。
4. docstring 占生产代码 19%。

死代码约 1,100 行，复制粘贴重复约 4.7%，都不是主因。

### 0.2 已确定的决策（用户，2026-10-05/06）

| # | 决策 |
|---|---|
| D1 | **定位**：ConfFlow 是"在服务器上批量生成结构并优化、筛选"的工具。计算卡片保留 `opt`、`sp`、`freq`、`opt_freq`、`ts`、`ts_freq`；保留 ConfGen、`refine`、`deduplicate`、`filter`；保留 `ts_rescue_scan`；保留 Gaussian checkpoint 读取（`%OldChk`、`ReadFC`）。 |
| D2 | **删除**：远程执行；QST2/QST3；NEB；GOAT；IRC；`tspes` recipe；`analysis` 能力（`reaction_profile`、热化学、PES）；多输出机制（`ensemble`、`path_endpoints` 输出形式、`multi_output`）；`named_structures` 适配器及 QST 专用的原子映射；IRC 的 `RCFC` checkpoint 规则；离线发布与安装流水线；产物垃圾回收；兼容转发层；死代码。打包在生产代码里的测试夹具移到 `tests/`。 |
| D3 | **保留**（虽然与上面的功能相邻）：`science/topology_mapping.py`、`science/frame_compare.py`（`refine` 使用）；`install_provenance.py` 与版本握手（JobDesk 读取）；控制面、`confflow-control-worker` 及顶层 `worker_*.py`（控制 worker 自己的模块，与 `remote/` 无关）；持久化中的断点续算、复用、终态仲裁。 |
| D4 | **分析**：JobDesk 只负责汇总、相对能量表格和作图；准谐振等热化学校正交给 GoodVibes 或 Shermo，不重新实现。ConfFlow 只保证计算结果中带有从输出解析出的电子能、热校正和 Gibbs 自由能（Gaussian `programs/gaussian/parsing.py:44-52`、ORCA `programs/orca/parsing.py:129-143` 已经解析）。 |
| D5 | **IRC 与精修**：用户已有独立脚本 `tspes.py`（2,345 行，仅依赖标准库，自带指纹续跑）。它保持为独立工具，放在单独的仓库，通过 N2 的脚本步骤接入 ConfFlow。 |
| D6 | **安装方式**：在服务器上源码 `pip install`。建议改为在服务器上 `git clone`，更新时 `git pull && pip install .`。 |
| D7 | **服务器**：直接启动 g16 和 ORCA，没有作业调度系统；96 核；通常同时跑 2 个 48 核任务。 |
| D8 | **新增**：服务器级核数配额（N1）；通用外部脚本步骤（N2）；按能量筛选（N3）；"构象集合精炼"recipe（N4）。 |

### 0.3 待确认（按里程碑）

| # | 问题 | 推荐 | 阻塞 |
|---|---|---|---|
| Q1 | 覆盖率门槛 | **已确认（2026-10-06）**：全局行覆盖率 ≥ 70%；本次新增或修改的可执行代码行 ≥ 85%；关键子系统（执行引擎、持久化、工作流编译、ConfGen kernel）不得低于 **P0 时一次性冻结的绝对基线**减 5 个百分点。基线写入后不随后续里程碑重取，避免逐轮下滑 | P0 |
| Q2 | 行数预算 | **已确认（2026-10-06）**：以 P0 执行时的实际行数为初始上限；之后默认只降不升，任何提高须写明理由并经用户确认。上限的收紧规则见 P0.2 | P0 |
| Q3 | 本轮是否允许修改 JobDesk，基准提交 | 允许；开工前确认 `master` 的最新提交 | R2、N2.6 |
| Q4 | 核数配额的配置位置与默认值 | 服务器上的 `~/.config/confflow/server.toml`：`total_cores = 96`、`total_memory = "<服务器内存>"`；文件不存在时不启用配额（与现状相同） | N1 |
| Q5 | 脚本登记表的位置与格式 | 同一个 `server.toml` 中的 `[scripts.<id>]` 段：`command = ["python3", "/path/tspes.py"]`；工作流只能按 id 引用已登记的脚本 | N2 |
| Q6 | `tspes.py` 输出 JSON 摘要的格式 | 见 N2.5；在 `tspes.py` 自己的仓库里实现 | N2.5 |
| Q8 | 虚频判定阈值的默认值 | `imaginary_threshold_cm1` 默认 **0**（任何负频率都算虚频，与 Gaussian 的输出约定一致）；需要容忍数值噪声时由用户在步骤里显式设置，例如 −20 | N3 |
| Q7 | FIX-1R（环修复）是否需要提前 | 默认排在 N 之后；如果环的问题影响当前研究，可以提到 R1 之后 | 排期 |

### 0.4 v1 → v1.1 修订记录

| # | 评审意见 | 处理 | 位置 |
|---|---|---|---|
| W1 | T 阶段以"独有覆盖贡献为零"作为删除依据风险最大：两个测试可以覆盖相同的代码行，却验证不同的故障模型 | **接受**。独有覆盖只用于生成候选；删除前必须写明该测试验证的行为或不变量，以及由哪个保留下来的测试继续验证它；给不出替代的不删 | T0、T1 |
| W2 | R1 标为"零行为"，但 R1.1 删除了控制台命令、R1.2 删除了 transport 注入，自相矛盾 | **接受**。R1 改为"科学结果与正式接口零变化"；R1.0 建立"非承诺接口清单"，只有清单中的接口允许在 R1 删除 | R1、R1.0 |
| W3 | N1 用 ConfFlow 进程是否存活来回收配额，而 ConfFlow 崩溃后 g16/ORCA 子进程可能仍在运行，配额会被绕过 | **接受**。配额绑定实际的原生进程组；只有该进程组已不存在才回收；归还必须幂等 | N1 |
| W4 | N2 的输出通道如果以后扩成任意端口，会重新长出多输出机制 | **接受**。固定三个通道；下游只能消费 `structures` | N2.1、G19 |
| W5 | 把服务器上登记的脚本 id 写进 contract，同一提交在不同服务器上 contract 会不同 | **接受**。静态 contract 只描述 `script` 能力本身；已登记的脚本列表放进运行时能力（`confflow control capabilities` 的响应） | N2.6 |
| W6 | N3 中"从上游结果读取能量"的关联方式没有定义，执行器会被迫猜测取哪一步的能量 | **接受**。`filter` 只能显式绑定一个计算步骤的 `results` 端口（已核实计算步骤有该端口，按结构配对 `BY_SUBJECT`）；不追溯历史；虚频参数拆成个数与阈值两个 | N3 |
| W7 | 每张卡都跑全量验收，在约 10 万行测试的仓库里太慢 | **接受**。新增 P0.4：卡片级与里程碑级两级验收 | P0.4、§1.2 |
| W8 | 覆盖率不能只设一个全局 70%；行数预算要防止模型靠压行过关；"11 万行"不应作为验收指标 | **接受**。覆盖率分三层；新增防压行条款（G20）；局部性作为主指标，行数预算只是护栏，§8 的数字只是估计 | P0.1、G20、§1.2、§8 |

---

## 1. 总览

### 1.1 里程碑与顺序

```
P0  止住增长           P0.1 → P0.2 → P0.3 → P0.4
R1  单仓退役（科学结果与正式接口不变）  R1.0 → R1.1 … R1.6
R2  与 JobDesk 成对退役（contract 变化）R2.0 → R2.1(JD) → R2.2 → R2.3a…f → R2.4(JD)
N1  服务器级核数配额
N2  外部脚本步骤        N2.1 … N2.6
N3  按能量筛选
N4  构象集合精炼 recipe
—— 之后接 docs/confgen-fix/PLAN.md 的 FIX-1R、FIX-1D ——
L1' producer 按 capability 拆分（范围已因 R2 缩小）
L2  退休旧 service 兼容链（不变）
T   测试整合            T0 → T1（非 confgen）→ T2（confgen，FIX-1D 之后）
S   生产代码压缩        S1 docstring → S2 内部重复校验 → S3 重复代码
L3' TS1 原始日志外移、可选目录搬迁
```

- 一次只把一个里程碑交给执行模型，合并后再开始下一个。
- **P0 必须最先做**：不改覆盖率门槛，R 和 T 阶段删除代码和测试时 CI 会失败，执行模型也会继续写凑覆盖率的测试。
- R 在 N 之前：先删除，新功能就不必兼容即将删除的代码（例如 N2 不需要考虑多输出、远程传输）。
- L1' 在 R2 之后：先删掉 `tspes`、QST、NEB、GOAT、IRC 在 producer 中的特殊处理，再拆分，要拆的代码少得多。
- T2 在 FIX-1D 之后：FIX-1R、FIX-1D 的 golden 是从 `tests/v4/test_confgen_*.py` 捕获的。

### 1.2 总体验收口径

**主指标是局部性，行数只是护栏**（W8）。沿用 `docs/confgen-fix/PLAN.md` 的目标：新增一个组件或能力时，中央文件改动不超过 1 个；开发一个组件所需的生产代码上下文约 5–10 个文件。N1、N2 的验收都要按这一条检查。

其他要求：

- **每张卡的提交信息记录行数变化**：`Lines-Prod: <前> -> <后>`、`Lines-Test: <前> -> <后>`（按 P0.2 的统计口径）。
- **新增功能的卡必须声明行数预算**，超出即退回。
- **两级验收**（P0.4）：每张卡跑卡片级验收，里程碑结束时跑一次里程碑级验收。

### 1.3 对 `docs/confgen-fix/PLAN.md` 的调整

| 原卡片 | 调整 |
|---|---|
| L1 | 改为 L1'：在 R2 之后进行；`tspes`、QST、NEB、GOAT、IRC 相关的 intent 和 checkpoint 逻辑已删除，不再拆分 |
| L3 | 测试重组改由本文件的 T 阶段承担（方法改为"按独有覆盖贡献删除"）；剩余部分（TS1 原始日志外移、可选目录搬迁）为 L3' |
| E1 | 保留：只组合现有卡片的 recipe（`monomer_conformers`，畸变输入先 xTB 预优化再生成构象）；与 N4（已有构象集合的精炼）不重复 |
| J3 | 不受影响；J1（GJF/INP 原子选择）、J2（confgen 下禁用 freeze）保留 |

### 1.4 新增规则（在 G1–G14 之外）

- G15 只在边界（schema 解析、外部输入、进程输出解析）做校验；内部函数之间不重复校验已经校验过的值。
- G16 不得新增按阶段或轮次命名的测试文件（例如 `test_v4x_*`、`*_coverage*`、`*_round*`）；新测试按行为命名，优先合并到已有的测试文件。不得编写只为提高覆盖率、没有行为断言的测试。
- G17 新写的私有函数 docstring 最多一行；新模块的模块 docstring 最多 15 行。公开接口与不变量说明不受此限。
- G18 删除类的卡，被删代码的守护测试随之删除，但必须在提交信息中逐个列出；不得为了让测试通过而保留只服务于被删功能的生产代码。
- G19 不得为任何能力新增"多个结构输出端口"或通用的多输出机制；脚本步骤只有 N2.1 规定的三个固定输出通道。
- G20 不得用压行来满足行数预算：一行多条语句、为缩短而写的复杂表达式、把无关职责合并到一个函数、删除必要的说明，都视为违规。格式仍以 `black` 与 `ruff` 为准。

### 1.5 新增卡片类型

| 类型 | 含义 |
|---|---|
| `retire` | 删除一个功能，包括其实现、对外暴露（contract、CLI、recipe）与测试。卡片必须列出：删除的模块、被修改的调用方、删除的测试文件或测试节点、预期的 contract 差异（没有则写"无"）。golden 中与该功能无关的部分必须逐字节不变。 |

---

## 2. P0：止住增长

### P0.1 — 覆盖率门槛（三层）

- 类型：`ci` ／ Q1
- 目的：降低全局门槛是为了允许删除历史上重复的测试，**不是**让新代码只需要 70% 覆盖。
- 修改：
  1. `pyproject.toml:142` 的 `fail_under = 85` 改为 70。
  2. **本次改动的代码行覆盖率 ≥ 85%**：CI 中用 `diff-cover`（新增为开发依赖）或在 `tools/` 下新增一个小脚本，对比 `coverage xml` 与 `git diff`，只检查新增和修改的生产代码行。纯删除的提交自然通过。
  3. **关键子系统的下限**：执行引擎（`confflow/execution/`）、持久化（`confflow/persistence/`）、工作流编译（`confflow/workflow/`）、ConfGen kernel（`confflow/science/confgen/` 中组件目录以外的部分）的覆盖率，不得低于基线减 5 个百分点。基线在本卡中测量一次并写入 `tools/coverage_baseline.json`，**此后冻结**：后续任何里程碑都与这份 P0 基线比较，不得用当时的覆盖率重新生成基线。修改该文件按 Q2 的方式需要写明理由并经用户确认。
- 允许修改：`pyproject.toml`、`.github/workflows/ci.yml`、`.github/workflows/coverage_push.yml`、`tools/coverage_baseline.json`（新）、可选的 `tools/changed_coverage.py`（新）
- 验收：CI 配置解析通过；全量测试通过；在临时分支上新增一段无测试的生产代码，第 2 层检查失败。

### P0.2 — 行数预算

- 类型：`test-only` ＋ 工具 ／ Q2
- 新增 `tools/loc_budget.json`：按子系统记录生产代码行数上限（统计口径：`.py` 物理行，按 `tools/architecture_policy.py` 现有的模块分组；子系统划分见附录 A），以及测试总行数上限。初始值为本卡运行时的实际值。
- 在 `tools/architecture_policy.py` 中新增规则：任一子系统超过上限即失败；新增文件名匹配 `tests/**/test_v4[0-9]_*.py` 或 `*_coverage*.py` 即失败（G16）。规则附反例与正例，按 L0.4 的要求。
- **上限的收紧规则（ratchet）**：上限**不**在每张卡之后自动收紧到当前行数，否则 R 阶段删完代码后，N 阶段按计划新增的功能会撞上人为的上限。规则如下：

  | 阶段 | 规则 |
  |---|---|
  | P0 | 建立初始上限（本卡运行时的实际值） |
  | R1、R2 | 代码只能减少；上限不变 |
  | N1–N4 | 允许增长，但每张卡的增量不得超过该卡已批准的行数预算 |
  | 每个里程碑的里程碑级验收通过后 | 把当时的实际值写入 `tools/loc_budget.json`，成为新的长期上限 |
  | 之后 | 只降不升 |

- 任何超出上述规则的提高，必须在 `tools/loc_budget.json` 对应条目的 `reason` 字段写明理由，并由验收方向用户确认。
- 允许修改：`tools/loc_budget.json`（新）、`tools/architecture_policy.py`、`tests/v4/test_architecture_policy.py`、反例文件
- 验收：标准验收；golden 不变；人为在临时分支上加一个超出预算的文件，策略测试失败。

### P0.3 — 执行模型规则

- 类型：`doc`
- 把 §1.4 的 G15–G20 与 §1.2 的行数记录要求写进 `docs/process/RULES.md`；交接提示词模板中加入这几条。
- 验收：文档审阅。

### P0.4 — 两级验收协议

- 类型：`doc` ＋ 工具（W7）
- 在 `docs/process/ACCEPTANCE.md` 中新增两级验收，替代"每张卡都跑全量"：

  | 级别 | 何时 | 内容 |
  |---|---|---|
  | **卡片级** | 每张卡 | 受影响的测试（import 了被修改模块的测试文件，加上卡片点名的测试）；架构策略测试；P0.1 的覆盖率三层检查；与改动相关的 golden（改到 ConfGen 或执行引擎才跑 TS1 与 engine 报告；改到 producer、注册表或 schema 才跑 contract 与 boundary 摘要）；冒烟测试（`confflow --version`、`confflow --capabilities`、用模拟程序跑一个最小 V4 工作流） |
  | **里程碑级** | 里程碑的最后一张卡合并前 | 全量测试；全部 golden（TS1 三份、engine 报告、contract、boundary）；跨仓测试 |

- 新增 `tools/affected_tests.py`：根据 `git diff` 中的生产模块，用 import 图找出受影响的测试文件。
- 卡片级验收没有覆盖到的回归，只要在里程碑级被发现，就由造成它的那张卡返工；不允许在里程碑末尾以一张"修补卡"一并处理。
- 验收：在 R1 的第一张卡上试用，记录卡片级与全量的耗时对比。

### P0 里程碑级验收（进入 R1 前的审查点）

P0.4 完成后，做一次 P0 的里程碑级验收，并由用户审查以下几点，全部通过后才进入 R1：

1. `tools/coverage_baseline.json` 已冻结，三层覆盖率检查在 CI 中生效；
2. `tools/loc_budget.json` 已建立，超出上限、新增按阶段命名的测试文件都会被拦截；
3. 两级验收能实际运行，`tools/affected_tests.py` 的结果合理；
4. G15–G20 已写入 `docs/process/RULES.md` 与交接提示词模板。

---

## 3. R1：单仓退役（科学结果与正式接口不变）

**本里程碑的约束（W2）**：TS1、engine 报告、contract、boundary 必须与 R1.0 逐字节相同；正式用户接口（`confflow` 的 `v4`、`control`、`config` 子命令，`confflow-control-worker`，`--version` 与 `--capabilities` 握手）不变。**允许删除的只有 R1.0 的"非承诺接口清单"中列出的接口**；删除清单以外的任何接口都必须移到 R2 与 JobDesk 成对处理。

### R1.0 — 基线

- 类型：`baseline`
- 在 `$CKPT/diet2/R1.0/` 生成：全量 collect 与结果、TS1 三份、engine 报告、contract/boundary 摘要，以及按附录 A 分组的行数表。仓库内只提交 manifest（沿用 V38 规则）。
- 新增 `docs/diet-2/unsupported_surface.json`（W2）：列出不属于承诺接口、允许在 R1 删除的入口，每项写明理由与证据。初始内容：
  - `confflow-fixture-agent` 控制台命令与 `confflow.fixture_agent` 模块（测试夹具；R1.1 执行前须确认 JobDesk 不使用）；
  - `application.execution` 中指向 `synthetic_producer` 的懒导出；
  - `formal_v4_runner`、`run_v4_document` 的 `transport` 注入参数取非 `None` 值的用法（生产代码中没有调用方传入非 `None`）；
  - `confflow.remote` 中除 `envelope` 以外的模块（生产代码中从未构造 `RemoteTransport`）；
  - `confflow.release_dependencies` 与 `scripts/install_release_wheel.py`（用户不使用离线安装）；
  - `persistence.artifacts.plan_gc`、`apply_gc`（生产代码中无调用方）。

### R1.1 — 测试夹具移出生产包

- 类型：`move`（删除的入口均在非承诺接口清单中）
- **执行前核实**：在 JobDesk 仓库中搜索 `fixture-agent`、`fixture_agent`、`synthetic`。若 JobDesk 的测试或运行时使用它们，本卡整体移到 R2，与 JobDesk 成对进行。
- 步骤：
  1. `confflow/application/execution/synthetic_producer.py`（450 行）、`confflow/fixture_agent.py`（77 行）、`confflow/application/execution/memory.py`（89 行）移到 `tests/support/`，内容逐字不变，只改 import。
  2. 删除 `confflow/application/execution/__init__.py:40-43` 中指向 `synthetic_producer` 的懒导出项。
  3. 删除 `pyproject.toml` `[project.scripts]` 中的 `confflow-fixture-agent`（约第 80 行）。
  4. 使用它们的测试（`tests/test_synthetic_producer.py`、`tests/test_fixture_agent.py` 等）改为从 `tests/support/` 导入。
- 验收：标准验收；`python -c "import confflow.application.execution"` 之后不加载这三个模块；collect 节点不变。

### R1.2 — 删除远程传输实现

- 类型：`retire`（contract 不变：`remote/envelope.py` 中被 contract 引用的常量保留到 R2.3f）
- 背景：生产代码中没有任何地方构造 `RemoteTransport`（只有测试使用）；`remote/` 只被 `execution/batch.py`（L68 的类型导入、L783 的 `EXECUTION_ENVIRONMENT_METADATA_KEY`）和 `producer/contract.py:383-387` 引用。顶层 `worker_*.py` 属于控制 worker，与本卡无关。
- 步骤：
  1. 把 `EXECUTION_ENVIRONMENT_METADATA_KEY` 从 `remote/staging.py` 移到 `execution/environment.py`，值不变。
  2. 删除 `remote/handoff.py`（374）、`remote/result_bundle.py`（455）、`remote/staging.py`（1,163）、`remote/transport.py`（1,066）、`remote/worker.py`（1,419）；`remote/__init__.py` 只保留 envelope 的导出。
  3. 去掉 `execution/batch.py`、`application/v4_run.py`、`application/v4_entry.py:132、299` 中的 transport 传递链。**行为必须不变**：步骤声明了非本地 `target` 时，仍在任何进程启动前以相同的错误信息失败（`execution/binding_resolution.py:173` 起的 `require_target_transport` 在 transport 为空时的分支）。`formal_v4_runner` 仍接受 `transport` 关键字参数（旧签名兼容），但只允许为 `None`。
  4. 删除只测试远程传输的测试文件：`test_v44_worker.py`、`test_v44_staging.py`、`test_v44_handoff.py`、`test_v44_security.py`、`test_v44_resume_remote.py`、`test_v44_parity.py`、`test_v45_parity.py`、`test_v44_faults.py`、`test_v44_hardening.py`、`test_v44_coverage.py`、`test_v44_crash_matrix.py`、`test_v44_crash_matrix2.py`。**执行前逐个确认**：文件中凡是测试本地执行路径（而非远程）的断言，迁移到按行为命名的测试文件，不得随之删除（G18）。混合文件（`test_audit_regressions_r2.py`、`test_audit_redteam_r2.py`、`test_crossstate_hardening.py`、`test_scientific_reliability.py`、`test_topology_patch_phase1.py`、`test_v46_production_gates.py`）只删除与远程相关的测试函数。
- 验收：标准验收；golden 与 contract 不变；`git grep -n "RemoteTransport\|remote.staging\|remote.worker\|remote.transport" confflow` 为空；非本地 `target` 的报错测试保留并通过。

### R1.3 — 删除离线发布与安装流水线

- 类型：`retire`
- 删除：`release/`（14 个锁文件与校验文件）、`scripts/install_release_wheel.py`（711）、`scripts/generate_dependency_locks.py`、`confflow/release_dependencies.py`（288）、`.github/workflows/release.yml`（498）、`tests/test_release_dependencies.py`、`tests/test_install_release_wheel.py`；`tests/test_release_workflow.py` 中只删除针对 `release.yml` 的断言（对 `jobdesk-contract.yml` 等其他工作流的断言保留）；`.github/workflows/ci.yml` 中 `--ignore=tests/test_install_release_wheel.py` 一并去掉；`tools/refactor-acc/weights.json`、`tools/refactor-acc/test_reachability_scripts.py` 中的对应条目。
- 保留：`confflow/install_provenance.py`、`confflow/__build__.py`、`setup.py` 的提交号写入、`tests/test_install_provenance.py`（其中依赖 wheelhouse 的用例改为不依赖；若无法改则在提交信息中说明并删除该用例）。
- 新增：`docs/USAGE.md` 中写明服务器安装方式（D6）。
- 验收：标准验收；`confflow --version`、`confflow --capabilities` 输出与 R1.0 相同；`pip install .` 在干净环境中成功。

### R1.4 — 删除产物垃圾回收

- 类型：`retire`
- 删除 `persistence/artifacts.py:196-370` 的 `plan_gc`、`apply_gc` 及 `GCPlan`（生产代码中无调用方）；`verify_artifact` 保留。删除对应测试。
- 验收：标准验收；golden 不变。

### R1.5 — 兼容转发层与死代码

- 类型：`delete`
- 步骤：
  1. 用 `tools/refactor/reachability.py` 列出从入口不可达的模块（@213b306 包括 `core.bonding`、`core.constants`、`core.data` 等转发层，`analysis.pes` 留给 R2.3a）。仍被测试按旧路径导入的，测试改用新路径。
  2. 用 AST 名称引用分析列出生产代码与工具中都无引用的定义（@213b306 约 76 个、1,100 行）。**每一个都必须给出"确实未被调用"的证据**：通过 `getattr`、pydantic 装饰器、懒导出表、字符串分派调用的都不算死代码（v3.3 规则）。
- 验收：标准验收；golden 与 contract 不变。

### R1.6 — 合并确认过的重复代码

- 类型：`logic`（Behavior-Change: none）
- 候选（@213b306，连续 8 行以上相同的窗口数）：`execution/confgen_executor.py` 与 `execution/transform_executor.py`（45）；`programs/orca/rendering.py` 与 `shared/orca_blocks.py`（21）；`execution/checks_standard.py` 与 `execution/recovery_standard.py`（16）；`analysis/item_adapter.py` 与 `execution/confgen_executor.py`（16，R2.3a 后自然消失）。
- 每一对先判断是真重复还是结构相似。只合并真重复，抽出的公共函数放在两者共同的上层模块。
- 验收：标准验收；golden 与 contract 不变。

---

## 4. R2：与 JobDesk 成对退役（contract 变化）

### R2.0 — JobDesk 侧现状核实

- 类型：`doc` ／ Q3
- 在 JobDesk 仓库中列出对以下内容的全部引用，写进 `docs/diet-2/R2.0-JD-INVENTORY.md`：卡片 `qst2`、`qst3`、`neb`、`goat`、`irc`；recipe `qst2`、`qst3`、`neb`、`goat`、`irc`、`tspes`；能力 `analysis` 与 `reaction_profile`；输出形式 `ensemble`、`path_endpoints`；适配器 `named_structures`；contract 中的 `remote_capability`；步骤绑定的 `target` 字段；`fixture-agent`（若 R1.1 未做）。
- 同时确认 ConfFlow 的 `run_result.json` 中，每个计算结果都带有电子能、`gibbs`、`gibbs_correction`（D4）。缺少时，新增一张 `logic` 卡把已解析的值写进结果，不改变其他字段。

### R2.1 —（JobDesk）停止提供

- 类型：JobDesk 侧 `retire`
- 删除 R2.0 清单中的界面、表单、卡片和 recipe 入口；分析界面改为只读取计算结果中的能量并汇总（D4）。
- 验收：JobDesk 全量测试通过；跨仓测试在当前 ConfFlow 上仍通过（此时 ConfFlow 仍提供这些功能，JobDesk 只是不再使用）。

### R2.2 — ConfFlow contract 收缩

- 类型：`retire`（contract 变化，需要 JobDesk 重新 pin）
- 修改：
  - `producer/cards.py:107-158`：删除 `irc`、`goat`、`qst2`、`qst3`、`neb` 五张卡片；
  - `producer/recipes.py`：删除 `_tspes_recipe`（L238 起）以及 `irc`、`qst2`、`qst3`、`neb`、`goat` 的 recipe（L409-577 区间），并从目录列表（L572-577）中移除；
  - `producer/intent.py`：删除 `tspes` 的专用逻辑（L589-637）、GOAT（L862、L1846）、QST/NEB（L1205）；`producer/seeds.py:300` 的 GOAT 分支；
  - `execution/registry.py`：删除输出形式 `path_endpoints`（L605）、`ensemble`（L619），适配器 `named_structures`（L654、L890），能力 `analysis`（L773、L884）；`ExecutorCapability.ANALYSIS` 从枚举中删除；
  - `producer/contract.py`：删除 `remote_capability` 段（L383-387、L639、L698）；
  - `workflow/v4/schema.py:259`：删除步骤绑定的 `target` 字段；`execution/binding_resolution.py` 中对 `target` 的处理随之删除。
- 预期 contract 差异：只有上述条目被移除。验收方用 `json_paths_diff.py` 核对，出现其他差异即退回。
- 验收：标准验收；TS1 与 engine 报告不变；contract、boundary 差异与声明一致。

### R2.3 — 删除实现（按功能分 6 张卡，每张单独提交与验收）

| 卡 | 删除 | 注意 |
|---|---|---|
| R2.3a 分析与 tspes | `confflow/analysis/` 整个包（4,111 行，含 `pes.py`、`thermochemistry.py`）；`execution/registry.py` 中的 `AnalysisItemAdapter`；测试 `test_v46_analysis*.py`、`test_v46_reaction.py`、`test_v46_pes.py`、`test_v45_tspes.py`、`test_v46_tspes_*.py`、`test_v45_reaction_chain.py` 等 | 热化学不搬进 JobDesk（D4） |
| R2.3b IRC 与多输出 | `execution/profile_path_endpoints.py`（322）、`execution/multi_output.py`（145）及 `work_item_executor.py` 中的多输出分支；`programs/gaussian/path.py`（588）；`programs/orca/path.py`（423）中 IRC 部分；`producer/checkpoints.py` 中 IRC 的 `RCFC` 规则（`_add_irc_rcfc` L388 起、`REUSE_MODES` 中的 `"rcfc"` L107、`_RCFC_CONFLICTS` L165 等）；测试 `test_v45_path_profile.py`、`test_v45_gaussian_path.py`、`test_v45_multi_output.py` 等 | `ReadFC` 与 `%OldChk` 保留；`ts_rescue_scan`（`execution/recovery_standard.py`）不受影响，执行前确认它不依赖多输出 |
| R2.3c QST | `programs/gaussian/named.py`（479）、`execution/named_structures.py`（335）；`execution/atom_mapping.py`（549）以及 `workflow/v4/assembly.py` 中命名结构配对的逻辑（L14 的说明、L48-52 的导入、L511-540 起）；测试 `test_v45_named.py`、`test_v45_atom_mapping.py`、`test_repair_identity.py` 中的相关部分 | `science/topology_mapping.py`、`science/frame_compare.py` 保留（D3）。执行前确认 `atom_mapping` 在删除 QST 后不再有其他调用方 |
| R2.3d NEB | `programs/orca/neb.py`（508）、`orca/path.py` 的剩余部分、`orca/adapter.py` 中的 NEB 分支；测试 `test_v45_orca_path.py` | — |
| R2.3e GOAT | `programs/orca/goat.py`（312）、`programs/orca/ensemble_parse.py`（393）、`execution/profile_ensemble.py`（318）；测试 `test_v45_goat.py` | — |
| R2.3f 远程收尾 | `remote/` 整个包（含 `envelope.py`）；R1.2 中保留的兼容参数 | — |

- 每张卡：标准验收；TS1、engine 报告不变；contract 与 R2.2 之后的检查点相同。

### R2.4 —（JobDesk）重新 pin 与跨仓验证

- JobDesk pin 到 R2.3f 之后的 ConfFlow 提交；两仓跨仓测试通过。

---

## 5. N：新增功能

### N1 — 服务器级核数配额

- 类型：`logic`（启用配额时行为改变；配置文件不存在时与现状相同）／ Q4
- 行数预算：生产代码 ≤ 300 行，测试 ≤ 400 行。
- 背景：机器配置中的 `total_cores`、`cores_per_item`、`max_parallel_items`（`producer/machine.py`，在 `execution/batch.py:317、545` 生效）只在**单个运行内部**限制并发。同时提交两个运行时，每个都以为自己独占全部核数，会超载。现有的文件锁只用于运行租约与终态仲裁。
- 设计：
  1. 读取 `server.toml` 中的 `total_cores`、`total_memory`。
  2. 配额存放在服务器状态目录下，用文件锁保护。每个原生进程启动前（`execution/process.py:230` 的 `NativeProcessSupervisor.submit`）申请 `cores_per_item` 与 `memory_per_item`；申请不到时等待，并定期检查取消信号。
  3. **配额绑定实际的原生进程组，而不是 ConfFlow 进程**（W3）。每条占用记录：`lease_id`、`run_id`、`work_item_id`、`cores`、`memory`、`pgid`、`leader_pid`、`leader_start_time`、`state`（`reserved` → `running` → `released`）。`pgid` 与 `leader_pid` 来自 `NativeProcessSupervisor` 已经记录的进程组信息（`execution/process.py:301`）。
     - 申请成功但进程尚未启动时为 `reserved`；启动后立即写入 `pgid`、`leader_pid`、`leader_start_time` 并转为 `running`。
     - **回收条件**：`running` 状态下，该进程组中已没有存活进程（不是"启动它的 ConfFlow 进程已退出"）；`reserved` 状态下，记录的 ConfFlow 进程已不存在且超过一个短的宽限期。这样 ConfFlow 崩溃而 g16 仍在运行时，配额不会被释放，新任务不会叠加上去。
     - **归还必须幂等**：进程结束、取消处理、结果采集三条路径都可能触发归还，以 `lease_id` 为键，只有第一次生效，其余为无操作。
     - 判断进程组是否存活时，用 `leader_start_time` 排除 PID 复用。
  4. 单个任务申请的核数超过 `total_cores` 时，立即以明确的错误失败，不能永远等待。
  5. 等待状态写进运行日志与诊断。是否在 JobDesk 中显示"排队中"，作为 JobDesk 侧的后续卡，本卡不改变控制协议的事件类型。
- 测试：两个运行、各 2 个 48 核任务，`total_cores = 96`，任何时刻最多 2 个进程在运行（用模拟可执行程序）；**ConfFlow 进程被强制杀死而模拟计算子进程仍在运行时，配额不被回收，第三个任务继续等待**；子进程组全部退出后配额被回收；三条归还路径并发触发时只归还一次；PID 复用不被误判为存活；等待中取消立即生效；超额申请立即失败；配置文件不存在时行为与 R2 之后的基线相同。
- 验收：标准验收；golden 不变。

### N2 — 外部脚本步骤

- 行数预算：生产代码 ≤ 800 行（N2.1–N2.4 合计），测试 ≤ 1,000 行。
- 总体原则：ConfFlow 负责批量、并发（经由 N1 配额）、续算、提交与监控、取消；化学逻辑留在脚本里。只用于流水线的末端或旁支；`opt`、`ts`、`freq`、去重等需要 ConfFlow 理解结果的主线步骤保持原生实现。

#### N2.1 — schema、登记表与编译

- 类型：`logic` ／ Q5
- 新增能力 `script`（加在 `execution/contracts.py` 的 `ExecutorCapability` 与 `execution/registry.py`）。步骤字段：
  ```yaml
  - id: refine_ts
    executor: script
    script: tspes                 # 只能引用 server.toml 中登记过的脚本 id
    args: ["{input}", "--nprocs", "{cores}", "--mem", "{mem_gb}"]
    resources: {cores_per_item: 48, memory_per_item: 120GB}
    outputs:
      artifacts: ["*.log", "tspes_results_all.txt"]   # 可选
      structures: "final.xyz"                          # 可选，多帧 xyz
      summary: "summary.json"                          # 可选
  ```
- **输出只有三个固定通道**（W4、G19）：`structures`（唯一的结构集合输出，也是下游**唯一**可以绑定的输出）、`summary`（唯一的机器可读 JSON 附件）、`artifacts`（只保存、不进入工作流依赖关系的文件）。不得新增其他输出名称。
- 占位符只允许 `{input}`、`{cores}`、`{mem_gb}`、`{item_id}`。`args` 中出现其他花括号即编译失败。
- 编译时检查：脚本 id 已登记；登记的命令路径存在；输出声明合法。**不允许在工作流中直接写任意命令**。
- 验收：标准验收；golden 不变（新增能力会改变 contract，见 N2.6，本卡先不对外发布）。

#### N2.2 — 执行器

- 类型：`logic`
- 每个输入结构对应一个任务。工作目录固定为 `<run_root>/script/<step_id>/<item_key>/`，其中 `item_key` 由脚本内容哈希、展开后的参数、输入结构哈希计算得出。输入写为 `input.xyz`。
- 通过 `NativeProcessSupervisor` 启动：独立会话与进程组（`execution/process.py:121、301` 已支持），取消时整组结束；启动前经过 N1 配额。
- 成功条件：退出码为 0，且声明的输出文件都存在。否则任务失败，诊断中附 stderr 的最后若干行。默认不重试。
- 续算：WorkItem 的指纹包含脚本内容哈希、参数和输入哈希；中断后在同一个工作目录重新调用脚本，由脚本自己的续跑跳过已完成部分（`tspes.py` 已有指纹续跑）。脚本内容改变则视为新任务。
- 来源记录：脚本 id、路径、sha256、解释器、完整参数、退出码、耗时。
- 测试：用一个模拟脚本（写文件、可选失败、可选启动一个睡眠子进程）验证成功、失败、取消时子进程被结束、中断后续算复用同一目录、脚本修改后重跑。

#### N2.3 — 结构集合输出

- 类型：`logic`
- 声明了 `outputs.structures` 时，用与输入导入相同的 `import_xyz`（`application/v4_run.py:103`）把多帧 xyz 拆成结构集合，作为该步骤的结构输出端口，供下一步使用。这一机制替代被删除的 GOAT 原生集成和多输出机制（例如由脚本调用 CREST 或 GOAT，再接 ConfFlow 的精炼）。
- 测试：脚本输出 3 帧 xyz → 下一步 `deduplicate` 收到 3 个结构。

#### N2.4 — 摘要 JSON

- 类型：`logic`
- 声明了 `outputs.summary` 时，ConfFlow 读取该 JSON（大小上限 1 MB，必须是 JSON 对象），原样写入结果，供 JobDesk 显示成表格。ConfFlow 不解释其中的内容。

#### N2.5 —（`tspes.py` 仓库）输出摘要

- 不在 ConfFlow 仓库中进行。为 `tspes.py` 增加 `--summary-json PATH`，每个输入输出一个 JSON：`{"input", "status", "ts_imag_freq", "dG_act", "dG_rxn", "energies": {...}, "warnings": [...]}`（字段由用户确认，Q6）。

#### N2.6 — 静态 contract、运行时能力与 JobDesk 界面（成对）

- **静态 contract 只描述软件能做什么**（W5）：`script` 能力、步骤字段的 schema、三个输出通道、允许的占位符及其语义。它与服务器配置无关，同一个 ConfFlow 提交在任何服务器上 contract 都相同。
- **运行时能力描述这台服务器装了什么**：已登记的脚本 id 与说明，放进 `confflow control capabilities` 的响应（`control.py` 的 `_capabilities_response`，目前只返回协议版本），新增 `registered_scripts` 字段。执行前确认 JobDesk 解析该响应时能容忍新增字段；不能则与 JobDesk 成对修改。
- JobDesk 增加"脚本步骤"表单：脚本 id 下拉列表取自运行时能力，表单结构取自静态 contract；运行结果中显示摘要表格。

### N3 — 按能量筛选

- 类型：`logic`
- 背景：V4 的 `refine` 与 `filter` **目前不能按能量筛选**。`execution/transform_executor.py:32-35、351-353` 写明：变换步骤的输入端口只有结构，没有能量数据，旧版的能量窗口与虚频筛选没有迁移。主线上"选出能量最低的 N 个"因此无法在 V4 工作流中完成。
- **能量来源规则（W6，先冻结再实现）**：
  - `filter` 新增一个可选输入端口 `results`，**只能显式绑定到某一个计算步骤的 `results` 输出端口**（计算步骤已有该端口，按结构配对 `BY_SUBJECT`，见 `execution/registry.py` 的计算能力定义）。`structures` 输入必须来自同一个计算步骤，二者按结构身份一一配对。
  - 能量和频率**只从这个被绑定的步骤**中读取；不遍历更早的步骤，不寻找"最近一次能量"。
  - recipe（例如 N4）由编译器替用户绑定**直接前驱**的计算步骤；手写工作流必须显式声明。没有绑定 `results` 时，使用任何能量参数都是编译错误。
- 参数：
  - `energy_key`：`electronic` 或 `gibbs`，指被绑定步骤结果中的对应字段；
  - `energy_window_kcal`：相对该集合中最低值的能量窗口；
  - `lowest_n`：保留能量最低的 N 个；
  - `max_imaginary_count`：允许的最大虚频个数（例如普通结构为 0，TS 为 1）；
  - `imaginary_threshold_cm1`：低于该值的频率计为虚频，默认见 Q8。
- 缺少所需能量或频率的结构按"不满足条件"处理，并在步骤说明中逐个列出，不静默丢弃。
- 测试：能量窗口；最低 N 个；虚频个数与阈值（包括 −8 cm⁻¹ 这类噪声在不同阈值下的判定）；缺少能量的结构；Gibbs 与电子能两种口径；未绑定 `results` 却使用能量参数时编译失败；绑定的步骤与结构来源不一致时编译失败。

### N4 — 构象集合精炼 recipe

- 类型：`logic`
- 新增 recipe `ensemble_refine`：输入多帧 xyz（CREST、GOAT、MD 轨迹等）→ `deduplicate` → 可选的脚本预筛（例如 xTB，经 N2）→ `opt` → `freq` → `refine` → `filter`（N3：`results` 自动绑定到 `freq` 步骤；能量窗口与最低 N 个）。
- MD 轨迹：在 recipe 说明中写明应先抽帧（或在预筛脚本中抽帧），并给出导入帧数的上限提示。
- 行数预算：≤ 150 行（主要是 recipe 数据）。

---

## 6. T：测试整合

### T0 — 每个测试的独有覆盖贡献

- 类型：工具
- 新增 `tools/test_contribution.py`：读取 `pytest --cov=confflow --cov-context=test` 产生的覆盖数据，计算每个测试独有覆盖的行和分支，输出按文件汇总的报告。
- **用途仅限于给候选排序**（W1）：两个测试可以覆盖完全相同的代码行，却验证不同的故障模型（例如"崩溃后续算"与"重复终态竞争"）。独有覆盖为零不能说明测试是多余的。
- 覆盖数据需要在能安装完整依赖的环境（用户服务器或 CI）中生成。

### T1 — 非 confgen 测试（R2 之后）

- 类型：`delete` ＋ `move`
- 候选：独有覆盖贡献为零（或很低）的测试，按 T0 报告排序。
- **删除条件**（W1）：每个拟删除的测试，必须在删除清单中写明（1）它验证的行为或不变量；（2）由哪个保留下来的测试继续验证同一件事，给出测试节点 ID。**给不出替代测试的不删。**
- **以下类别一律保留**，即使能找到"替代"：崩溃一致性、并发与竞争、安全、取消、续算、跨仓 contract。
- 删除清单先交验收方确认，验收方抽查其中的"替代"关系是否成立。
- 按阶段命名的文件按行为合并重组到 `tests/execution/`、`tests/persistence/`、`tests/producer/`、`tests/control/` 等。节点 ID 会变化，验收时必须提供"旧节点 ID → 新节点 ID 或删除理由"的完整映射表。
- 不涉及 `tests/v4/test_confgen_*.py`。

### T2 — confgen 测试（FIX-1D 之后）

- 同 T1，范围为 `tests/v4/test_confgen_*.py`。之后 golden 捕获的测试清单需要同步更新。

---

## 7. S：生产代码压缩（每个子系统一张卡）

- S1 **docstring 压缩**（`doc`，行为零风险）：私有函数 docstring 压到一行；模块 docstring 压到 15 行以内；公开接口和不变量说明保留。验收：AST 去掉 docstring 后与压缩前完全相同。
- S2 **去掉内部重复校验**（`logic`）：在已经由边界校验过的内部函数之间，删除重复的类型和取值检查（G15）。每张卡只改一个子系统，golden 必须不变。
- S3 R1.6 之后仍存在的重复代码。

---

## 8. 预期结果（估计，不是验收指标）

| 阶段完成后 | 生产代码 | 测试 | 依据 |
|---|---|---|---|
| 现在（@213b306） | 88,333 | 99,227 | 实测 |
| R1 + R2 | 约 71,000–73,000 | 约 70,000–75,000 | 被删模块约 14,500 行，加上 producer、适配器、注册表中的相关分支；约 42,000 行测试触及被删模块，其中整文件可删的估计 2.5–3 万行 |
| N1–N4 | +约 1,300 | +约 1,600 | 各卡的行数预算 |
| T | 不变 | 约 45,000–50,000 | 按阶段命名与覆盖率类文件中的重复 |
| S | 约 60,000–65,000 | 不变 | docstring 现有 16,760 行，预计减半 |

合计估计从约 19.7 万行降到约 11 万行。**这个数字不是验收指标**（W8）：真正的验收是 §1.2 的局部性目标，即删完之后，开发一个 ConfGen 组件或执行能力时，只需要打开一个组件目录和少数几个 contract 文件。

---

## 9. 风险

| 风险 | 涉及 | 缓解 |
|---|---|---|
| 删除远程相关测试时，误删了其中测试本地路径的断言 | R1.2 | 执行前逐个文件拆分；本地路径的断言迁移，不删除（G18） |
| JobDesk 仍在使用某个被删功能 | R1.1、R2 | R2.0 先做清单；JobDesk 先停止使用（R2.1），ConfFlow 再收缩 contract |
| `atom_mapping` 除 QST 外还有其他用途 | R2.3c | 执行前确认调用方；有其他用途则只删 QST 专用部分 |
| 删除多输出后影响 `ts_rescue_scan` | R2.3b | 执行前确认依赖；它目前只依赖 `execution/recovery_standard.py` 与 Gaussian 适配器 |
| 配额文件锁在异常退出后残留，导致任务永远等待 | N1 | 按进程组存活判断回收；`reserved` 状态有宽限期；超额申请立即失败 |
| ConfFlow 崩溃而计算进程仍在运行，配额被提前释放导致超载 | N1 | 配额绑定原生进程组，不绑定 ConfFlow 进程（W3） |
| 脚本步骤被用来执行任意命令 | N2 | 只能引用服务器上登记的脚本 id；参数只允许固定的占位符 |
| 按能量筛选时，缺少能量的结构被静默丢弃 | N3 | 逐个列入步骤说明，不静默 |
| 测试删除后真实缺陷失去覆盖 | T | 独有覆盖只用于排序；每个删除都要给出替代测试；保留类别清单；删除清单经确认与抽查 |
| 卡片级验收漏掉回归，到里程碑末才发现 | P0.4 | 由造成回归的卡返工，不用一张修补卡兜底；里程碑级验收必须全量 |
| 模型为了满足行数预算而压行 | P0.2 | G20；`black` 与 `ruff` 格式检查不放宽 |

---

## 附录 A：子系统划分（行数预算与统计口径）

| 子系统 | 路径（正则，按顺序匹配） |
|---|---|
| ConfGen | `confflow/science/confgen/`、`execution/confgen_executor`、`workflow/v4/confgen_schema`、`producer/path_preview`、`science/torsion.py` |
| JobDesk 服务与控制面 | `application/execution/`、`control.py`、`contract.py`、`config/`、`control_worker.py`、`worker_*.py` |
| producer | `producer/` |
| 持久化 | `persistence/` |
| Gaussian/ORCA 适配 | `programs/`、`shared/orca_blocks` |
| V4 工作流编译 | `workflow/` |
| 执行引擎 | `execution/` |
| 领域模型 | `domain/` |
| 运行入口与 CLI | `application/`、`v4cli`、`cli.py`、`main.py` |
| 发布与安装溯源 | `install_provenance`、`__build__` |
| 通用基础 | 其余 |
