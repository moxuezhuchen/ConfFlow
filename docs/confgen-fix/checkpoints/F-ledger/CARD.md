# F-ledger 卡（FIX1D 记账缺口定点分析+修复预演，根独立验收，不正式提交）

- 父：`c9bbc9f8cc0ae94875aaaab180c8488f7ea81f25`（`fix/confgen-1d`）；源 `/tmp/fix1d-exec` 只读；独占 `/tmp/fix1d-ledger-proto`（本树，detached）与 `/tmp/fix1d-ledger-output`（证据）。
- 每命令显式 `cd`，核 HEAD/干净/分支；不得修改 `/tmp/fix1d-d3-root-review`、任何 capture/基线或 L1 树。
- 反例：`/tmp/fix1d-final-root-run-v2/capture/engine_reports/tests_v4_test_confgen_v3_core.py_test_suppression_positive_control_real_stage_fewer_attempts__0.json`（input 20 vs ledger 18/12，差 2 为 issued 后抑制）。
- 任务：实际源码/调用链核实 + 最小 spy 复现 + PLAN FIX1D 不变量 + 现有 accounting 测试 → 最小通用 kernel 解法（首次 issued 历史与终态分类分离，不认识 coordination，不破旧未重试字节/旧 REALIZED 字节，保持 certificate/leaf 方程；重试多起点只在 `start_statistics`，不混 ledger；禁改 basis 粉饰、禁删测试/改容差；取消/异常不漏）。若涉其他接口先 DESIGN 再实施；全量/golden 由根统一跑。
- 白名单：`engine.py`/`accounting.py`（必要时 `kernel_records.py`，本卡未动）+ 针对性新测试 + 本卡 `docs/confgen-fix/checkpoints/F-ledger` 与 LOG；不动组件实现。
- 测试/环境：`pytest -o addopts= -p no:cacheprovider`，`PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen JOBDESK_V2_SRC=/opt/cf-worktrees/jd-pin/src PYTHONPATH=树/tools/refactor-acc/noeditable:树:树/tools/refactor`；`ruff/mypy/black`；冻结 `git diff --binary` 含新文件；精确 restore 回父干净，不 `git clean`/rm 整树，不编造数字。
- 尾行：`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`

详见 `DESIGN.md`（字段/分类表/白名单）与 `DIFF.md`（改动/验证/数字）。
