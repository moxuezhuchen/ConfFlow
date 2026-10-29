# PR #198 验收 · 2026-10-29

审查提交：`7dc67b1150d61096fa66d9fa1bf61596a2e99479`。基线：`c1ba73f2561955d1513c670e4f2cc808da180249`。

结论：D1、D2 的修改符合范围，D3 保持现状的理由成立，D5 的阻止计算/复用和保护登记逻辑有效；但有 **1 项 P2 验收缺口**，使 D4 和 D5 的对外诊断要求尚未完成。建议补上诊断传递后再关闭本轮验收。此次只审查与验证，没有合并 PR、修改生产源码、挪动主仓库文件或清理分支/临时大文件。

## A1 / P2：拒绝信息在发布异常处丢失，D4 提示与 D5 文件定位均未传到调用方

新增代码位置：`confflow/execution/batch.py:777`（D5 的端口、artifact id、locator、原因及修复提示）和 `batch.py:1094`（D4 的 fresh run root 提示）。实际丢失位置：`batch.py:620` 的 `_gap_reason()` 只提取 error.code；`batch.py:626` 抛出新的 `PersistenceError`，丢弃临时 `WorkItemResult` 中的 message/details。该临时结果按设计不持久化，整个步骤也因 strict publication 拒绝发布，因此这些诊断没有其他可见出口。

独立验证没有替换 `_durable_synthetic_failure` 或其他执行方法：

- D4：分别登记旧 provenance、输入摘要和定义摘要，再调用公开的 `execute_step_resumable()`，三种拒绝的对外异常都不包含 `use a fresh run root or explicit migration`。
- D5：绑定输入文件被篡改、被删除、或符号链接越过 run root，三种情况都正确拒绝且不启动、不改登记；但对外异常都没有端口、文件路径、具体原因或修复提示。
- 正常 `V4RunApplication.run()` 路径：通过真实 `RunInputs.artifacts`、编译和组装传入被篡改的 `inputs/external.chk`。没有登记工作项、没有创建 attempt；调用方仅看到下述通用异常。`run_generation.json` 及 `generation_ownership.json` 的 failure 字段也只有同一条异常，`run_result.json` 为 failed 且 steps 为空，没有被丢弃的诊断。

```text
refusing to publish step 's_opt': 1 items lack durable results (wi:s_opt:c000); gap code=publication_durability_gap; reasons=wi:s_opt:c000=failed:invalidate_artifact
```

因此用户仍无法从公开错误判断具体哪个文件失败，D4 所说的“步骤级提示已补齐”也没有对外效果。它不导致错误科学结果被复用，但未满足这次明确约定的诊断交付。

现有测试漏检原因：`test_deferred_d4_invalidation_hint.py:33` 和 `test_deferred_d5_input_artifact_bytes.py:110` 用 spy 截取内部 `_durable_synthetic_failure()` 的参数；证明了提示曾被构造，没有证明调用者能收到它。

修复建议：让拒绝发布的异常或公开失败载荷保留工作项的诊断消息/必要 details（至少端口、artifact id、locator、校验失败原因及处理建议），并让运行失败记录保留该信息。保持已有历史登记不变，仍拒绝发布没有持久化依据的步骤结果。增加从公开 batch/application 调用的异常或失败记录断言字段的回归测试，不仅检查内部 spy。

## 已通过的验证

- 在 `git archive 7dc67b11` 的独立源码快照中运行相关测试，**348 passed，92.57 秒**。包含新增 D1、D2、D4、D5 测试，既有 reuse/resume、artifact/assembly、ConfGen、MLIP 升级拒绝等相关测试。
- D1：缺少快照的直接 ConfGen 调用在建目录/启动前拒绝，显式快照按原样传给启动请求。
- D2：calculator 冻结传入环境，优化与 Hessian 保留原线程和栈参数覆盖，构造后改变源字典或当前环境不会影响它。worker 显式传入进程环境。独立的版本探测函数仍继承 worker 环境，本次未发现由此产生的实际环境漂移。
- D3：独立 artifact_checksums 比较字段保持为空；正常装配已将声明 checksum 纳入 work-item 摘要。D5 补充消费前实际字节验证，与该身份规则互补。
- D5：新增测试中的已完成/待执行拒绝、未篡改对照、共享引用去重均通过；额外独立验证了文件缺失、越界符号链接，以及真实 V4 应用的 run-supplied artifact 输入。
- [CI 40804495957](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40804495957) 成功：Python 3.10/3.11/3.12/3.13 的 Pytest、3.11 的 black/ruff/mypy、changed-coverage 均通过。完整 coverage job 按 PR 配置跳过。
- [JobDesk Consumer Contract 40804495929](https://github.com/moxuezhuchen/ConfFlow/actions/runs/40804495929) 成功。
- 本轮没有本地重跑全部 5083 个测试，也没有重新测量 256 MB 文件的耗时；这些具体数字来自提交者报告。

“每个文件只哈希一次”的精确范围是新增 verifier 在每次步骤执行内，按 `(artifact id, checksum, locator)` 缓存验证结果；不是跨步骤缓存，也不是按物理文件全局去重。原生执行器在 staging 时已有的完整性检查仍保留。无 checksum 的引用与 EXTERNAL_URI 维持本次约定的例外规则。

## 复现与记录

- [独立复现脚本](data/pr198-acceptance-20261029/probe.py)
- [原始结果](data/pr198-acceptance-20261029/probe-results.json)
- [定向测试日志](data/pr198-acceptance-20261029/focused-tests.txt)
- [正常应用失败状态](data/pr198-acceptance-20261029/application-run-generation.json)
- [正常应用失败清单](data/pr198-acceptance-20261029/application-run-result.json)

脚本依赖 PR 内的测试 fixture，并只在临时目录创建验证 run。复现命令（源码目录可换成同一提交的独立快照）：

```bash
cd /tmp/confflow-pr198-review.jusatk_s
PYTHONPATH="$PWD" /opt/ConfFlow/.venv/bin/python /tmp/confflow-pr198-acceptance/probe.py
```

材料保存在 `/tmp/confflow-pr198-acceptance/`，尚未加入项目或推送；`/tmp` 不是长期归档位置。
