# ConfGen 报告捕获与 golden 复用（E23 起）

本文件说明验收全量运行如何在分片执行的同时捕获与 golden 同范围的 ConfGen
引擎报告，以及 golden_check 如何在绑定校验通过后复用该捕获。**不改变任何
通过标准**；没有可用捕获时，两条工具的旧入口行为逐字保留。

## 组件

| 文件 | 角色 |
| --- | --- |
| `docs/refactor/tools-acc/run_sharded.py` | runner：分片执行 + 可选捕获（`--capture-engine-reports DIR --run-id ID`） |
| `docs/refactor/tools/capture_engine_reports.py` | pytest 插件；`CAP_SCOPE_GLOB` 限制**写出范围**（默认不过滤，旧行为不变） |
| `docs/refactor/tools/capture_provenance.py` | runner 与 golden **共用**的绑定/校验实现 |
| `docs/refactor/tools/golden_check.py` | 校验通过后复用捕获（`--engine-capture DIR --run-id ID`） |

工具链必须在 ConfFlow 树内布局（`docs/refactor/tools-acc` 与
`docs/refactor/tools` 同树），capture 模式据此定位共享模块与插件。

## 捕获语义

- 每个分片写自己的 `CAP_OUT`（`DIR/shards/shardN/`）；**全部测试照常执行**，
  过滤只发生在报告写出（仅 `tests/v4/test_confgen_*.py` 对应的报告落盘）。
- 报告文件名与 JSON 字节格式与旧 golden 现场捕获完全一致；合并时重名即失败。
- 完成证明：分片退出码、JUnit 节点集合与 collect 节点集合**精确一致**
  （无缺失/多余/重复/等数量换节点）、无 failed/error、捕获目录无异常，
  全部通过后才写 `manifest.json`（status=complete，最后写入）。任何失败
  都以非零码退出且**不产生 manifest**。
- manifest 绑定：本轮 run-id、CF 路径、CF 源/测试/配置**内容**摘要（读取
  工作树而非 HEAD；运行前后各算一次，变化即失败）、工具文件内容摘要、
  JD 源树内容摘要、节点摘要与数量、tally、out.json 与全部报告的 sha256。
- 捕获目录必须事先不存在或为空，且位于 CF 树与 JD 源树之外。

## golden 复用

`golden_check.py --engine-capture DIR --run-id ID` 先重校验全部绑定
（run-id、CF 路径、重新计算的 CF/工具/JD 内容摘要、fresh collect 的节点
集合与完成状态、报告文件集合与 sha256），任何不符都以非零码退出并给出
具体原因，**不静默回退**；通过后才用捕获的报告代替现场 ConfGen pytest。
TS1 三后端、契约/边界摘要仍现场计算；`--removed-nodes` 语义与
added/different/missing 判定不放宽。捕获模式不重跑该批 ConfGen pytest。

## 最终集成命令（模板）

```sh
RUN=/tmp/acc2/integr-<日期>-$$          # 新建独立输出目录
mkdir -p "$RUN"
cp docs/refactor/tools-acc/weights.json "$RUN/weights.json"
cd <固定集成CF树>
PYTHONPATH=$PWD/docs/refactor/tools-acc/noeditable:$PWD \
python3 docs/refactor/tools-acc/run_sharded.py \
  --cf . --out "$RUN/out.json" --weights "$RUN/weights.json" \
  --shards 12 --jdpin /opt/cf-worktrees/jd-pin \
  --capture-engine-reports "$RUN/capture" --run-id "integr-<日期>-$$"

PYTHONPATH=$PWD/docs/refactor/tools-acc/noeditable:$PWD \
python3 docs/refactor/tools/golden_check.py \
  --base <按既有流程准备的 base 目录> \
  --checkpoint docs/refactor/baseline/checkpoints/C4.3/contract.json \
  --cf . --jd-src /opt/cf-worktrees/jd-pin/src \
  --removed-nodes "$RUN/removed.txt" \
  --engine-capture "$RUN/capture" --run-id "integr-<日期>-$$" \
  --out "$RUN/golden.json"
```

## 限制

- run-id 与捕获目录每次运行都必须新建；不得复用外部执行器或旧轮次的制品。
- 捕获运行与 golden 校验之间，CF 树、工具文件或 JD 源树的任何内容变化都会
  使捕获失效（这正是绑定目的）。
- 捕获只覆盖 ConfGen 引擎报告；其余 golden 内容（TS1、契约/边界）始终现场
  计算，不进入制品复用。
