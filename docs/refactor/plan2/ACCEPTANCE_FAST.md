# 验收提速固定流程（E1 起生效）

本文件规定独立验收全量测试的固定流程：启用 `run_sharded.py` 已有的 `--weights` 加权调度，固定 Black 单 worker 与宿主权限，并强制核对结果。本流程不改变通过标准；任何一次实测时长都只是当时机器负载下的记录，**不构成对所有环境的速度保证**。

## 1. 固定原则

- 每次验收运行建**独立输出目录**（如 `/tmp/acc2/<卡名>-run/`，须为新建）。静态检查、全量、golden_check 可并行；**抽样破坏必须另用独立副本**，避免污染正在测试的树。
- 种子权重：从文档树 `docs/refactor/tools-acc/weights.json` **复制**到本次输出目录，再把副本传给 `--weights`。运行器会把实测秒数写回该副本；已提交的种子文件不允许被运行器覆盖。
- `run_sharded.py` 自行注入 `PYTHONPATH=<tools-acc>/noeditable:<待验收 CF 根目录>` 并设置 `JOBDESK_V2_SRC`、`QT_QPA_PLATFORM=offscreen`；不要破坏或覆盖这些设置。
- Black 固定单 worker：`black --check --workers 1`（或环境变量 `BLACK_NUM_WORKERS=1`）。
- 涉及 `/opt/g16` 的测试需要适当宿主权限；遇权限失败**停止报告**，不得通过跳过测试掩盖。
- 按最新批量验收规则（`plan2/ACCEPTANCE_BATCH_POLICY.md`，2026-10-04）：每卡只跑影响范围内的必要检查与相关静态检查，**不逐卡重复全量/golden**；完整 `run_sharded.py` 全量与完整 golden_check 在固定集成树最终验收时各跑一次。执行器的产物不作为独立验收证据。

## 2. 全量命令模板

```sh
E1RUN=/tmp/acc2/<卡名>-run        # 必须是本次新建的独立目录
mkdir -p "$E1RUN"
cp /opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/weights.json "$E1RUN/weights.json"
cd <待验收CF副本根目录>
PYTHONPATH=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/noeditable:$PWD \
python3 /opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/run_sharded.py \
  --cf . --out "$E1RUN/out.json" --weights "$E1RUN/weights.json" \
  --shards 12 --jdpin /opt/cf-worktrees/jd-pin > "$E1RUN/run.log" 2>&1
```

## 3. 结果核对（退出码 0 不单独构成通过证明）

先重新收集本次节点集合，再与 `out.json` 逐项比对：

```sh
cd <待验收CF副本根目录>
JOBDESK_V2_SRC=/opt/cf-worktrees/jd-pin/src PYTHONDONTWRITEBYTECODE=1 \
QT_QPA_PLATFORM=offscreen \
PYTHONPATH=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/noeditable:$PWD \
python3 -m pytest -o addopts= -p no:cacheprovider --collect-only -q \
  > "$E1RUN/collect.txt" 2>&1

python3 - "$E1RUN/out.json" "$E1RUN/collect.txt" <<'EOF'
import json, sys
out = json.load(open(sys.argv[1]))
col = {l.rstrip("\n") for l in open(sys.argv[2])
       if "::" in l and not l[:1].isspace() and not l.startswith("ERROR ")}
o = set(out)
missing, extra = sorted(col - o), sorted(o - col)
tally = {}
for v in out.values():
    tally[v] = tally.get(v, 0) + 1
bad = {k: v for k, v in out.items() if v not in ("passed", "skipped")}
print(f"collect={len(col)} out={len(out)} missing={len(missing)} extra={len(extra)}")
print("tally", dict(sorted(tally.items())))
if missing or extra or bad:
    print("MISMATCH")
    sys.exit(1)
print("OK")
EOF
```

通过条件（全部满足）：`out.json` 节点集合与本次 collect **精确一致**（无重复、无缺失、无多余）、无 `failed`/`error`、汇总实际 `passed`/`skipped` 数与卡片预期相符。

## 4. 静态检查与 golden_check 模板

```sh
cd <待验收CF副本根目录>
ruff check confflow tests scripts && mypy confflow
BLACK_NUM_WORKERS=1 black --check <本卡改动的 .py；无改动文件时可省略>

B="$E1RUN/golden-base"
rm -rf "$B" && cp -r docs/refactor/baseline "$B" && rm -rf "$B/checkpoints"
cp docs/refactor/baseline/checkpoints/IS.5/engine_reports/* "$B/engine_reports/"
cp docs/refactor/baseline/checkpoints/C4.3/engine_reports/* "$B/engine_reports/"
printf 'tests/v4/test_confgen_v3_integration.py::TestLegacyRegressions::test_v3_filenames_vs_legacy_compat\n' > "$E1RUN/removed.txt"
PYTHONPATH=/opt/cf-worktrees/refactor-plan/docs/refactor/tools-acc/noeditable:. \
python3 docs/refactor/tools/golden_check.py --base "$B" \
  --checkpoint docs/refactor/baseline/checkpoints/C4.3/contract.json --cf . \
  --jd-src /opt/cf-worktrees/jd-pin/src --removed-nodes "$E1RUN/removed.txt" \
  --out "$E1RUN/golden.json"
```

golden_check 期望 `ok: true`，契约/边界摘要 5 项全部 ok，引擎报告 added/different/missing 为空；任何 DIFF → 停止报告。

## 5. 交付钉住

交付钉文档提交与补丁 SHA256；外部执行开始后不得并发修改交付物，修订须先通知停止、再重新交付。
