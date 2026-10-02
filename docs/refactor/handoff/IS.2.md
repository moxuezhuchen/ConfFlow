# IS.2 补充说明（ConfFlow 输入简化分支，logic 卡）

- 工作树 /opt/cf-worktrees/exec-cf-is，分支 implementation/input-simplification，HEAD 应为 fe2acf0 开头，工作树干净。不符就停止报告。
- `git apply --check /opt/cf-worktrees/refactor-plan/docs/refactor/handoff/IS.2-src-tests.patch && git apply` 同一路径；git status 应有 5 个文件。
- 自检（原始输出；全量用 run_sharded.py，`--jdpin /opt/cf-worktrees/jd-pin`；该工具现在会在存在“收集阶段错误”的文件时直接报错退出）：
  1. `ruff check confflow tests scripts && mypy confflow && git diff --name-only HEAD | grep '\.py$' | xargs black --check`（新文件先 git add -A）
  2. `python3 /opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/run_sharded.py --cf /opt/cf-worktrees/exec-cf-is --out /tmp/refactor-acc/IS.2/out.json --jdpin /opt/cf-worktrees/jd-pin` 期望 `{"passed": 5138, "skipped": 12}`
  3. collect（应用前后各存排序列表，命令同 C5.3b.md 第 3 步，`JOBDESK_V2_SRC=/opt/cf-worktrees/jd-pin/src`）：5097 → 5150；`comm -23` 为空；`comm -13` 与 handoff/IS.2-added-tests.txt 逐行相同（53 项）。
  4. golden：`PYTHONPATH=…/tools-acc/noeditable:. python3 docs/refactor/tools/golden_check.py --base docs/refactor/baseline --cf . --jd-src /opt/cf-worktrees/jd-pin/src --checkpoint docs/refactor/baseline/checkpoints/IS.0/contract.json --out /tmp/refactor-acc/IS.2/golden.json`：contract 五项 ok、ts1 三种 ok；engine_reports 为 added 5 / different 1（与 checkpoints/IS.0/engine_reports 同一批，整体 `ok` 为 false 是预期，因为这些差异已由 IS.0 检查点记录）。再对这 6 份报告用 capture_engine_reports 重新生成并与 checkpoints/IS.0/engine_reports/ 逐字节比较（命令见 checkpoints/IS.0/README.md 与 capture_engine_reports.py 文档字符串），6 份全部相同。
- 提交：`feat(producer)!: compile ConfGen intent paths into typed v3 scopes`，Added-Tests 53（测试名从清单逐字复制），Behavior-Change 含：legacy paths 以 typed v3 执行；bare 声明显式 `step`；含 `waypoint` 或未知路径键的声明编译期被拒绝（`waypoint` 不再可用）。不要 push（该分支不在推送授权内）；补丁无法应用或数字不符，停止报告。
