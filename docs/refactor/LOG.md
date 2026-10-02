# 重构验收日志

按 `ACCEPTANCE.md` §6 的格式追加，最新的在最后。不得修改或删除已有条目。

---

## 2026-10-02 J0a — 升级（未提交，执行方按 G9 停止）

- 仓库/分支/提交：JobDesk-v2 `refactor/diet`，改动未提交（父 9beeaf2）
- 类型：logic；白名单检查：ok（只改 `src/jobdesk_v2/application/editor/manifest.py`，只移植 92d48f1 的前 3 个 hunk 和 docstring，没有 `step_selector`/`{id}`）
- 静态检查：ruff ok、ruff format ok；**mypy 失败 9 处，全部是 `Library stubs not installed for "yaml"`**；未修改的 9beeaf2 上同样是这 9 处，所以是环境问题，不是 J0a 引起的。JD `pyproject.toml:34` 的 dev extras 声明了 `types-PyYAML>=6.0.12`（CI 会安装），本机没有安装。CF `mypy confflow` 通过。
- 测试：collect 2411（声明 -0 +0）；2396 passed / 8 failed / 7 skipped；8 个失败与 J0a 卡预期的 8 项逐条相同
- golden：不适用（B0.1 尚未建立）
- 结论依据：方案 §2.1 写的"环境已安装 mypy"不完整，缺 `types-PyYAML`，需要用户决定环境处理方式；Q0b 仍未答复
- 对用户的问题：(1) 是否在本机安装 `types-PyYAML`（与 JD CI 一致）；(2) Q0b
- 产物：/tmp/refactor-acc/J0a/

## 2026-10-02 J0a — 通过（重新验收）

- 仓库/分支/提交：JobDesk-v2 `refactor/diet` 5847bc7（父 9beeaf2）
- 执行者：Opus 子代理（Claude Code）完成改动；用户确认后由验收方按原改动提交。J0b 起执行模型改为 CodeBuddy `glm-5.3-flash`。
- 环境：按用户决定安装 `types-PyYAML 6.0.12.20260906`（`pip install --break-system-packages`，与其他包同在 `/usr/local/lib/python3.12/dist-packages`），已写入 PLAN §2.1。
- 类型：logic；白名单检查：ok（仅 `src/jobdesk_v2/application/editor/manifest.py`，+12/-7，与 92d48f1 的前三个 hunk 及 docstring 逐字一致）
- 静态检查：ruff ok；ruff format ok；mypy ok（166 个源文件无问题）
- 测试（`run_jd_tests.sh --cf exec-cf@10e8c69`）：collect 2411（-0 +0）；2396 passed / 8 failed / 7 skipped；失败集合与卡片预期的 8 项逐条相同
- 测试削弱检查：ok（没有改动测试）
- golden：不适用（B0.1 尚未建立）
- 结论依据：满足用户给定的通过条件（mypy 通过，且失败集合等于卡片预期的 8 项）
- 产物：/tmp/refactor-acc/J0a/

## 2026-10-02 推送授权

- 用户授权推送 JD `refactor/diet` 和 CF `docs/refactor-plan` 两个工作分支，不动 `master`/`main`。

## 2026-10-02 J0b — 发起失败（不计为退回）

- 执行模型：CodeBuddy `glm-5.3-flash`
- 情况：`--allowedTools "... Bash"` 在非交互模式下没有授予 shell 权限，执行模型的第一条只读命令就被拒绝；它按 G9 停止，没有改动任何文件（`exec-jd` 仍为 5847bc7，工作树干净）。
- 原因：验收方的调用参数写错，应为 `Bash(*)`。已用最小命令实测：`Bash(*)` 可以执行；`--disallowedTools` 仍能拦截 `unshare`。PLAN §2.8 已更正。
- 处理：以更正后的参数重新发起 J0b。这一次不计入"退回"次数。
- 产物：/tmp/refactor-acc/J0b/codebuddy.json
- 补记：第二次发起时，交接提示词中的 CF HEAD 写错（写成 25e0e19），验收方在执行模型改动文件之前终止了该次运行；`exec-jd` 仍为 5847bc7，工作树干净。这一次也不计入退回次数。
