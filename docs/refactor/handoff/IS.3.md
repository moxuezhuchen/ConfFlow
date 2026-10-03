# IS.3 补充说明（ConfFlow 输入简化分支，logic 卡）

- 工作树 /opt/cf-worktrees/exec-cf-is，分支 implementation/input-simplification，HEAD 应为 0a9f28d 开头（IS.2b 的提交），工作树干净。不符就停止报告。
- `git apply --check /opt/cf-worktrees/refactor-plan/docs/refactor/handoff/IS.3-src-tests.patch && git apply` 同一路径；git status 应有 2 个文件（intent.py、tests/v4/test_intent_legacy_paths_to_v3.py）。
- 自检（原始输出；全量用 run_sharded.py，`--jdpin /opt/cf-worktrees/jd-pin`；报告收集阶段错误就停止；已知负载敏感测试若恰好是唯一失败，单独重跑，通过则如实报告）：
  1. `ruff check confflow tests scripts && mypy confflow && git diff --name-only HEAD | grep '\.py$' | xargs black --check`
  2. 全量期望 `{"passed": 5149, "skipped": 12}`。
  3. collect（应用前后各存排序列表）：5157 → 5161；`comm -23` 与 handoff/IS.3-removed-tests.txt 逐行相同（9 项）；`comm -13` 与 handoff/IS.3-added-tests.txt 逐行相同（13 项）。
  4. golden：命令同 IS.2b（`--checkpoint docs/refactor/baseline/checkpoints/IS.0/contract.json`）：contract 五项 ok、ts1 三种 ok、engine_reports added 5 / different 1；再重新生成那 6 份报告并与 checkpoints/IS.0/engine_reports/ 逐字节比较，6 份全部相同。
- 提交：`feat(producer)!: reject non-v3 ConfGen native in intent compilation`，Removed-Tests 9、Added-Tests 13（名字逐字复制）。不要 push；补丁无法应用或数字不符，停止报告。
