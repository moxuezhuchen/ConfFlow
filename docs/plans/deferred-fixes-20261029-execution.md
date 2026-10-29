# 遗留项 D1–D4 执行计划 · 2026-10-29

遗留项定义见 `docs/plans/deferred-fixes-20261029.md`。分支 / worktree：`refactor/deferred-20261029` @ `/opt/cf-worktrees/deferred-1029`，基于 PR #197 的 49dd2df8；PR #197 合并后再开 PR。

公共规则：只改自己拥有的文件；先写回归测试，确认它在修复前失败；ruff、black --check、`mypy confflow` 必须通过；docstring 首句用祈使语气（D401）。不提交、不推送、不开后台进程；临时文件放在 `/tmp/claude-0/-opt-ConfFlow/bfc5cc9d-eb0c-41d7-a82d-612d2df713ea/scratchpad`，绝不写到 `/`。
运行测试：`cd /opt/cf-worktrees/deferred-1029 && PYTHONPATH="$PWD" /opt/ConfFlow/.venv/bin/python -m pytest <files> -q -o addopts=''`
如果需要修改拥有文件以外的文件，先停下来报告。

## 任务 A — D1：缺少环境快照时明确拒绝

拥有文件：`confflow/execution/confgen_search_run.py`；构造 ConfGen search 执行上下文却没有提供 `native_env` 的测试文件（用 grep 找出，例如 `tests/v4/test_confgen_search_step.py`）；新测试 `tests/v4/test_deferred_d1_confgen_snapshot.py`。
不要碰：`confgen_xtb.py`、`confgen_search_worker.py`、`test_confgen_xtb*.py`、`test_confgen_search_worker.py`（属于任务 B）；`batch.py`（属于任务 C）。

修复：删除 confgen_search_run.py 约 835 行的回退 `effective_native_env(binding, inherit=os.environ)`。`context.native_env` 为 None 时，在创建任何目录、启动任何进程之前抛出 DomainError，并说明原因（需要由应用层提供已摘要的环境快照）。调整直接调用方：让测试上下文提供显式快照（例如 `FrozenDict(effective_native_env(binding, inherit=os.environ))`，或一个最小的显式 dict），不放宽任何断言。
测试：缺少快照时拒绝，并且没有启动、没有创建 rundir；提供快照时，启动的环境正好是该快照。

## 任务 B — D2：xtb 显式接收环境快照

拥有文件：`confflow/execution/confgen_xtb.py`、`confflow/execution/confgen_search_worker.py`、`XtbCalculator` 的其他构造方（用 grep 找出，只限 `confflow/` 下）、`tests/v4/test_confgen_xtb*.py`、`tests/v4/test_confgen_search_worker.py`；新测试 `tests/v4/test_deferred_d2_xtb_env.py`。
不要碰：`confgen_search_run.py`（属于任务 A）、`batch.py`（属于任务 C）。

修复：`XtbCalculator` 增加一个必填的、仅限关键字的参数 `base_env: Mapping[str, str]`，在构造时冻结一份副本。confgen_xtb.py 约 247 行和 353 行改为在 `base_env` 的基础上覆盖线程和栈参数（`OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`MKL_NUM_THREADS`、`OMP_STACKSIZE`），取值保持现状。calculator 内部不再读取 `os.environ`。worker 在 `_run` 开始时取一次进程环境（这就是 supervisor 传入的快照），显式传给 calculator。
测试：启动 xtb 时，环境等于 base_env 加上线程 / 栈覆盖；构造之后再修改 `os.environ`，不影响启动环境；两处覆盖取值不变。

## 任务 C — D4 提示 + D3 评估

拥有文件：`confflow/execution/batch.py`；新测试 `tests/v4/test_deferred_d4_invalidation_hint.py`。

D4：在 batch.py 约 1007 行的步骤级失效诊断中，对 INVALIDATE_PROVENANCE / INVALIDATE_DEFINITION / INVALIDATE_INPUT / INVALIDATE_ARTIFACT 补充"use a fresh run root or explicit migration"提示，措辞与 run 级的 `_definition_mismatch_error` 一致。测试：旧 provenance 被拒绝时，诊断 message 中包含该提示；已有测试中如果有断言旧 message 全文的，在报告中列出。

D3：只读评估，不改代码。核对 `_current_reuse_inputs` / `_stored_reuse_inputs` 中 `artifact_checksums=()` 的来历（git log -S）；确认绑定 artifact 的 checksum 是否、以及在哪里进入 work-item 摘要（assembly）；检查两种情况是否会被拒绝：(a) 实际文件字节被篡改但登记未变，(b) 直接 API 构造身份时绕过 assembly。给出建议：接通该字段、移除该字段，或保持现状，并附上理由与证据（文件:行）。可以在 scratchpad 里写探针脚本。

## Claude 验收

审 diff；全量 pytest 8 片、ruff、black 26.10.1、mypy、v4_arch_scan、architecture policy / loc_budget；根据 D3 的评估决定后续；更新 `docs/plans/deferred-fixes-20261029.md` 中的状态；PR #197 合并后开 PR，并补记最终复核报告中的合并结果。

## 任务 D — D5：消费方校验绑定输入 artifact 的字节（2026-10-29 用户决定修复）

拥有文件：`confflow/execution/batch.py`；新测试 `tests/v4/test_deferred_d5_input_artifact_bytes.py`。如果其他文件也必须改（例如 imports 的 locator 处理），先停下来报告。

问题：`verify_artifact`（persistence/artifacts.py:32）只在 batch.py `_verify_stored_artifacts` 中，用于 COMPLETED 条目自己的产物。被消费的绑定输入 artifact（`item.named_inputs` 中的 `ArtifactRef`）从来不做字节校验。文件字节被篡改、但 checksum 不变时，复用命中旧结果；新执行则会读取被篡改的字节，结果却仍标着原 checksum。D3 探针：`/tmp/claude-0/-opt-ConfFlow/bfc5cc9d-eb0c-41d7-a82d-612d2df713ea/scratchpad/probe_d3.py`（a-input 场景）。

修复：
1. 在每个 work item 做复用判定和启动之前（两条路径都要覆盖），对 `named_inputs` 中所有带 checksum 的绑定 `ArtifactRef` 调用 `verify_artifact(run_root=..., ref=...)`。同一步骤内，按 artifact id（加 checksum）去重，每个文件只哈希一次。
2. 校验失败时，该 item fail closed：返回不可重试的 durable synthetic failure，诊断中写明 artifact id、locator 和原因；不启动计算、不命中复用、不改写登记。按 strict publication 规则，该步骤随后拒绝发布。错误码沿用 `invalidate_artifact`，或者新增一个明确的码；选哪个都要在报告中说明理由，并与 D4 的提示规则保持一致。
3. 没有 checksum 的引用，以及 `EXTERNAL_URI` locator：保持 `verify_artifact` 现有语义（前者无法校验，后者只做形状检查）。在报告中说明正常工作流中有多少绑定输入不带 checksum。
4. 性能：大文件（.chk / .gbw）会被哈希。报告一个 TS1 级别或测试级工作流在改动前后的耗时差；如果明显变慢，提出缓存方案，但不实现。

测试（先写，并确认修复前失败）：(i) 上游输出被篡改、下游 COMPLETED 续跑时，拒绝复用，0 次启动；(ii) 下游 PENDING / 新执行在启动前被拒绝，0 次启动；(iii) 通过 `run_inputs.artifacts` 从 run 外部传入的输入被篡改时，被拒绝；(iv) 未篡改的对照组正常复用 / 执行；(v) 同一 artifact 被多个 item 绑定时，只哈希一次（例如对 `verify_artifact` 计数）。
回归：`test_v43_*.py`、`test_v45_resume*.py`、`test_arch_review_1029_resume_identity.py`、`test_pr197_mlip_upgrade_reuse.py`、`test_deferred_d4_invalidation_hint.py`、artifact / assembly / checkpoint 相关测试（grep `verify_artifact`、`ArtifactRef`）。
