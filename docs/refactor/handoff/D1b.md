# D1b — CHANGELOG 按这次重构重写，README 发布身份段落手工移植（ConfFlow，refactor/cleanup-d1；纯文档）

- 工作树 /opt/cf-worktrees/d1，分支 `refactor/cleanup-d1`，HEAD 应为 `bcfc9d8`（D1a，标题 `test: delete unreferenced durable-v2 fixtures, fake_orca.sh and legacy gjf inputs`，父提交 afdf9df），工作树干净。**提交前再核对分支名。**每条命令显式 `cd`。
- 类型：`doc`。白名单（恰 2 个文件）：`CHANGELOG.md`、`README.md`。
- 应用：`cd /opt/cf-worktrees/d1 && git apply --check /opt/cf-worktrees/refactor-plan/docs/refactor/handoff/D1b.patch && git apply …`；`git status --short` 恰 2 个已修改文件。

## 内容
1. `CHANGELOG.md`：在 `## v2.1.6` 之前新增 `## Unreleased — Architecture diet: legacy native path retired`（+52 行），分 Removed / Added / Changed，只写用户可见变化，每条标明对应卡片与 ConfFlow 提交：`confgen.native` 移除（250f947 C4.3，及 ea93daa、888f872、015a174）、`confflow export` 移除（a261bbf C5.2b）、`confts`/`confgen`/`confrefine` 与 calc/blocks 移除（2f95dc1、deadf46、da44bf5、b84f84b）、producer 边界无读取方成员移除（e5c3032）、retired-runtime 桩与 `run_workflow` 导出移除（8eda87f）、intent 映射到 typed v3 与拒绝非 v3/`waypoint`（2e0295a、c094af4、6046b44）、refine 的 `topology_bonds`/`mapping_budget`（6521581、efbaecd）、ConfGen 溯源字段（8d968dc）、refine 对称映射去重（da047c0、efbaecd）、v3 拒绝末端原子端点并指明键（0a9f28d）、文档重写（4900cb8）。不改动文件里原有的任何条目。
2. `README.md`："Identity boundaries" 段落：把原来挤在一段里的事实拆成四条要点（`confflow --version` 打印 2.1.6；`--capabilities --json` 的安装身份块与 `install_provenance.status`；v2.1.4/v2.1.5 为失败的仅标签发布、v2.1.6 候选未发布、生产端点仍为 v2.0.0；`v4-core-closure` 是内部架构标签——用户 2026-10-04 决定去掉对远端不存在的 `pre-v4-greenfield` 的引用，只保留 `v4-core-closure`；不推送任何标签），内容取自分支 `refactor/v4-diet-pr10-repository-hygiene` 的提交 69ba0d7，**只改事实措辞，不借机重写，README 其余部分一字不动**（该提交的补丁因 README 已被重构重写而不能直接应用，故手工移植；`git diff README.md` 只有这一处）。

## 自检（原始输出）
1. `cd /opt/cf-worktrees/d1 && git diff --name-only HEAD`：恰 `CHANGELOG.md`、`README.md`（提交前）。
2. `cd /opt/cf-worktrees/d1 && git diff --stat HEAD`：`CHANGELOG.md | 52 +++`（只有新增）、`README.md | 19 ++++----`。
3. 逐个核对 CHANGELOG 里引用的哈希确实存在且主题与条目相符：`cd /opt/cf-worktrees/d1 && for h in 250f947 ea93daa 888f872 015a174 a261bbf 2f95dc1 deadf46 da44bf5 b84f84b e5c3032 8eda87f 2e0295a c094af4 6046b44 6521581 efbaecd 8d968dc da047c0 0a9f28d 4900cb8; do git log -1 --format='%h %s' $h; done`，20 行，原样贴出。
4. `cd /opt/cf-worktrees/d1 && PYTHONPATH=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/noeditable:. python3 -m pytest -q -p no:cacheprovider -o addopts= tests/test_example_workflow.py tests/test_release_workflow.py`，期望 `17 passed`。
5. 全量 `{"passed": 4473, "skipped": 10}`，**golden_check（不得省略）** `ok: true`（命令同 D1a 第 4、5 项，目录换成 `/tmp/acc2/D1b-exec`）。
- 提交：`docs: record the architecture diet in the changelog and state the release identity as facts`，Removed-Tests 0，Behavior-Change: none。不 push；不动 main/master；补丁无法应用或数字不符，停止报告，不要自行修复。
