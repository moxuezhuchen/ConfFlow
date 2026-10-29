# PR #199 验收复核（2026-10-29）

PR：https://github.com/moxuezhuchen/ConfFlow/pull/199

验收提交：`368151f37a3a175beeed9dc454d32be82b49b05e`；base：`71cc92ee99463bc8ea0f8d54ade090e54a0ddd78`。远端 PR 为 open、未合并，本地 PR worktree 与该 head 一致。

**结论：暂不建议合并。V1、V2 与 V3 的原始复现场景均通过，但“测量与启动使用同一次命令解析结果”仍有一个 P1 缺口。** 另有一个不作为阻断项的升级行为需要补充说明。

本轮仅 review 与验证：未修改生产代码、仓库测试、PR 内容或分支；没有合并 PR，也没有清理用户文件。测试和探针运行在 head 的独立 git archive 快照 `/tmp/confflow-pr199-source`。证据在本报告同目录，均为本轮新结果，未覆盖上一轮材料。

## A2 — P1：解析结果未贯穿到执行器，仍可“测量 A、运行 B”，随后错误复用 B 的结果

相关代码：

- `confflow/application/v4_run.py:966`：原生步骤解析命令，把绝对路径交给身份测量。
- `confflow/application/v4_run.py:1034`：MLIP 解析解释器，把绝对路径交给 runtime identity 测量。
- `confflow/application/v4_run.py:1099`：交给 batch 的仍是原始 `binding`，没有携带上述解析结果。
- `confflow/execution/work_item_executor.py:1151`：实际启动前重新调用 `locate_command(candidate, ...)`。
- `confflow/execution/mlip_executor.py:533`、`:537`：重新从原始 binding 取命令名并解析。

原生计算在 `_enforce_policy()` 中还有一次查找。调用同一个解析模块保证了查找规则一致，但未保证整条执行链使用同一个查找结果。

触发条件：配置裸命令名，PATH 字符串不变，但测量之后、启动之前，更靠前的 PATH 目录出现同名可执行文件。例如运行期间安装了该程序。原来的 A 文件保留且字节不变，并不需要覆盖 A 或修改已冻结的环境快照。

独立探针 `resolution_pin_probe.py` 使用正常 `V4RunApplication.run()`，没有 mock 命令解析、身份测量、batch、持久化、发布或复用逻辑。假解释器只用来在确定的时点模拟新安装文件的出现，避免依赖并发时序；科学计算由假 runner 返回可区分的能量。

MLIP 实测：

| 场景 | 程序日志 | 结果 |
| --- | --- | --- |
| 首次运行：初始解析为 A，版本探测后更高优先级目录出现 B | probe:A → launch:B | completed，能量 -10 |
| 移除临时出现的 B，恢复最初的 A，再在同 root 续跑 | probe:A；没有新计算 | completed，reuse_hit，仍是 -10 |
| A 下新建 root 的对照 | probe:A → launch:A | completed，能量 -5 |

第一次测量后到恢复续跑期间，A 的内容、模型、runner、工作流配置与父进程环境都未改变。只是在更高优先级目录增加、随后移除了 B。旧 run 和新 root 对照的登记 `environment_digest` 完全相同：`sha256:e13811f73977c0d930c92ba97562c8e09218f67fe3260e87075658f75c04cc2a`。旧 run 的 `current_attempt` 仍为 1，确认恢复时没有重算。

原生计算对照：同一步设置 `max_parallel_items: 1`，两个工作项依次执行。第一个工作项启动 A 后出现 B，第二个工作项启动 B。两项均完成并登记在同一份测量环境摘要下。日志为 `A, B`。当前测试中的静态 A/B 路径分离场景不能发现这个缺口。

影响：B 的科学结果被登记在 A 的执行身份下；恢复 A 后，结果缓存把 B 的结果当成 A 的结果复用。这已超出路径选择不符合配置的范畴，属于结果正确性问题。

建议修复方向：把 `ResolvedCommand`（同时保留原始配置名和解析后的绝对路径）从应用层贯穿到 step request / item context。策略、测量、启动使用这一份结果，启动阶段不再按原始裸名搜索 PATH。保留原始名称供裸名白名单判断，避免简单改成绝对路径后再次破坏 V2。没有应用层上下文的底层调用可在自己的入口解析一次，并在后续策略和启动中复用。

应补的回归：更高优先级 PATH 文件在测量后出现；同一原生步骤两个工作项之间出现；恢复原程序后的同 root 续跑。固定启动 A 或明确拒绝均可，但不能成功保存“测量 A、实际 B”的结果。

这发生在正常原生计算／MLIP 步骤路径，不属于 PR 描述中列出的控制服务自测量、脚本注册阶段两个 deferred 项。

证据：`resolution-pin-results.json`、`resolution-pin.log`、`registered-identities.json`。

## 已通过的部分

- 上一轮 `runtime_probes.py` 独立重跑：后端失败变为 failed；恢复后同 root 重算得到 1 个结构、1 条能量；MLIP／脚本裸名与绝对路径白名单均完成；原生程序在稳定的步骤 PATH 中正确选择 B。
- V1 新测试：单 worker 和 pool 的加载失败协议、可重试结果、恢复续跑、全部结构错误、部分错误、合法空集合与复用、旧空完成结果的新 attempt／旧历史保留、内存复用拒绝均通过。旧空结果测试使用持久化 fixture；这些测试与实际 runner 升级的环境摘要变化应分别理解。
- 17 个跨执行器契约测试全部包含在全量测试中，通过。它们验证静态路径与白名单组合，未覆盖 A2 的“测量后解析结果变化”。
- 脚本新加的解释器内容核对，以及相关既有回归测试通过。本轮没有据此发现新的独立阻断问题。

## 升级说明需补充：正常旧 MLIP 结果也会重新计算

MLIP runtime identity 在 `confflow/execution/environment.py:436` 纳入 runner 文件的完整内容摘要。本 PR 改变了 `mlip_runner.py` 的字节，因此不升 executor 契约版本也会改变 MLIP 的环境身份。

`runner_upgrade_probe.py` 把真实旧／新 runner 源码分别接到同一个可控健康后端，使用当前 V4 应用、同一模型和环境验证：

- 旧 runner 首次正常完成：能量 -1，启动次数 1。
- 旧 runner 不变续跑：reuse_hit，启动次数仍为 1。
- 换为本 PR 新 runner 后续跑：完成，能量仍为 -1，启动次数增加到 2。

所以“只对旧全错误空结果使用窄失效钩子”描述了钩子的行为，不能据此承诺升级只重算这类旧结果。建议在 PR／升级说明中写明正常旧 MLIP 结果也会受 runner 身份变化影响。本项不作为错误缓存修复的阻断项，也不建议为避免重算而削弱 runner 内容身份。

证据：`runner-upgrade-results.json`、`runner-upgrade.log`。该探针验证源文件升级造成的身份变化，不是实际 fairchem 数值基准。

## 验证记录

本地 head 快照，244 个已跟踪测试文件按 8 个独立 pytest 进程分组，使用各自独立 basetemp：**5121 passed、10 skipped、0 failed**。未跟踪实验不属于本次基线。

Ruff 通过；mypy 200 个源文件通过；`v4_arch_scan.py` 42 个入口文件通过；架构／LOC 门禁测试包含在全量测试中通过。本机 Black 为 26.5.1，本轮没有用它替代 CI 的格式检查。

已直接核对该 head 的远端检查：

- [CI](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40817318533)：成功；Python 3.10–3.13 测试矩阵通过，3.11 Black／Ruff／mypy 通过，changed-coverage 通过。
- [JobDesk Consumer Contract](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40817318528)：成功。

本轮没有启动真实 ORCA／Gaussian／xTB／fairchem；独立场景使用假程序／后端检验公共入口、执行身份、状态和持久化行为。

## 复现命令与材料

```bash
cd /tmp/confflow-pr199-source
PYTHONPATH="$PWD" /opt/ConfFlow/.venv/bin/python \
  /tmp/confflow-pr199-acceptance/resolution_pin_probe.py
```

`resolution_pin_probe.py` 每次创建独立临时运行目录，输出根目录路径和 JSON 结果。`runtime_probes.py` 为上一轮脚本的输出目录重定向副本；`runner_upgrade_probe.py` 为正常 runner 升级对照。原始运行目录保留，未删除。

其他材料：`run_suite.py`、`test-groups.json`、`test-results.json`、`pytest-0.txt` 至 `pytest-7.txt`、`ruff.txt`、`mypy.txt`、`arch-scan.txt`。

本目录位于 `/tmp`，尚未写入 PR 分支。报告与材料可归档到 PR 的 `docs/reviews/`；本轮未代为提交或发布。
