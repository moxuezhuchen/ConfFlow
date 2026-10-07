# L3' 评估：TS1 原始日志外移与可选目录搬迁（只读重核 + 执行结论）

> 基线：`main = ee821e2`（执行时最新；准备材料基线 `ce0dd996` 已过期，全部锚点已在当前 main 重定位）。
> 工作树：`/tmp/finl3`（分支 `fin/l3`，`git status` 起始 clean）。
> 结论先行：**TS1 日志外移已实施**（零测试删除、零断言改动、收益 4581599 bytes）；**可选目录搬迁不执行**。

## 1. TS1 原始日志盘点（当前 main 实测）

### 1.1 体积大的日志/输出

| 路径 | 大小 (bytes) | sha256 | 说明 |
|---|---|---|---|
| `tests/fixtures/confgen/coordination/ts1/source/si-rr-salanal2-r-end-spdd-ts1.log` | 4581599 | `5908d7fc95ad9b59dc0be3d2c7482f7360513bdc548d20479fc5307dbe2ff060`（实测 `sha256sum` 与 MANIFEST 登记一致） | 唯一大文件，占该 fixture 目录 97.6%（目录原 4696531 bytes，外移后 117110 bytes） |
| `ts1_confgen_golden_fixture.zip`（仓根） | 1052623（压缩）/ 4696531（解压，含同一 log 4581599 bytes，实测 zip 内 sha 与上表相同） | — | 同一 fixture 的压缩快照；本卡不动，作为取回备用源 |
| `tests/fixtures/paths_equivalence/result.json` | 763075 | — | 非 TS1，与本卡无关，仅登记 |

准备材料（DESIGN.md）称"唯一大文件 4581599 bytes + sha `5908d7fc…be2ff060`"——**经重核属实**，行号有漂移但符号锚点有效。

### 1.2 被哪些测试/工具读取（`git grep` 全仓）

- 直接引用该 `.log` 文件名的生产/测试代码：**零**。命中仅为：
  - `tests/fixtures/confgen/coordination/ts1/MANIFEST.json:46`（登记）、`README.md:47`（说明）、`source/scine_generation_metadata.json:3`（记录上游绝对路径 `/mnt/data/...`，非仓内读取）、`structures/ts1_original{,_plus_F1-F6}.xyz:2`（注释行 `"...from si-rr-...log"`，纯文本说明）。
  - `docs/confgen-fix/PLAN.md:85,1000`（L3 定义本身）。
- 真实消费链（与准备材料一致，重核确认）：
  - `tests/v4/test_confgen_v3_coordination.py:80-81,140,499,588-592,673,1822` 读 `topology/typed_topology.json` + `structures/ts1_original.xyz`；
  - `tests/v4/test_confgen_v3_integration.py:1017,1104-1105` 同上；
  - `tools/refactor/ts1_engine.py:21,65,135` 同上；
  - `benchmark/verify_fixture.py`（改前 53 行）只读 topology/benchmark/structures，**无任何 `.log` 读取**。
- 唯一与 `source/` 强相关的测试断言：`test_scine_declared_maps_convention_gap_is_source_provenanced`（`test_confgen_v3_coordination.py:570`）断言 `scine_generation_metadata.json` 与 `scine_F1-F6_report.txt` 存在——**不是** `.log`，本卡保留这两个文件，不受影响。
- 无任何代码读取 `MANIFEST.json` 做运行时校验（`git grep MANIFEST` 命中均为执行引擎的 run_result manifest，与 fixture MANIFEST 无关）。

### 1.3 可行性：外移 + 仓内只留 manifest+sha

可行，且已按此实施：

- 外移内容：仅 `source/si-rr-salanal2-r-end-spdd-ts1.log`（`git rm`，工作树 −4581599 bytes）。
- 保留：`MANIFEST.json` 该条目保留 `path/bytes/sha256` 并新增 `external` 段（取回命令 + 说明）；`README.md` 新增"External source log retrieval"节；`verify_fixture.py` 对 `external` 条目改为"存在则校验 sha256，不存在则打印 SKIP（含期望 sha/大小/取回命令）并 exit 0"，其余断言逐字不变。
- 取回源（二者实测 sha 均等于登记值）：`git show d5a40ae:<path>`；仓根 zip 内同名文件（python zipfile 实测 4581599 bytes、同 sha；`unzip` 二进制在部分环境缺失，故文档给 python 命令）。
- 需要真实日志的测试：**不存在**——全仓无测试读取该 log，故无"缺失时 skip"改造对象；科学/安全回归（coordination 感知、SCINE 审计、TS1 golden）全部基于 xyz/拓扑/benchmark，继续原样运行。
- 局限如实声明：`git rm` 只减小工作树/检出体积；历史 blob 仍在 git 对象库中，新 clone 体积不变；彻底减体积需历史改写，不在本卡范围。

## 2. 可选目录搬迁评估（不执行）

范围（两块）：(a) `tests/` 中遗留阶段命名（`test_v42_*`…`test_v46_*` 共 33 个、`test_v4*.py` 共 34 个、`tests/v4/test_*.py` 共 143 个）按行为重组；(b) `confflow/science/confgen/` → `kernel/`+`components/` 搬迁及 canonicalizer 合并。

- 收益：命名清晰；目录与行为对齐。体积收益为零（纯移动）。
- 风险：
  1. 测试重组在 diet-2 中已划归 **T 阶段**（T1/T2，FIX-1D 之后），且前置 FIX-1D、L1、L2 均未合并，节点 ID 与 helper 归属（`tests/v4/_helpers/`）未冻结；此时重组会与 T 冲突，且验收需"旧→新"全映射表，成本高。
  2. `science.confgen` 被生产代码 25 个文件、测试 48 个文件引用；搬迁涉及公开 import 路径、G13 policy 作用域、`tools/architecture_policy.py` 白名单、wheel 打包路径，需与 JobDesk 成对评估（L2 Q8 模式）。canonicalizer 双实现错误类型不同（`core`: `ValueError("Invalid…")` vs `domain`: `ElementSymbolError("unknown…"/"must not be empty")`），合并属行为变化。
- 建议：**不执行**，留待 T（测试）与 science 卡（目录/canonicalizer，单独评审）处理。

## 3. 结论与建议（已执行项）

- 条件核验：零测试删除（无测试读该 log）✓；零断言改动（verify 既有断言逐字保留，仅新增外部条目分支）✓；体积收益 4581599 bytes > 1 MB ✓。故实施外移。
- 被改测试清单：**空**——经 `git grep` 全仓核实，不存在读取该 `.log` 的测试或工具，因此无任何测试需要"缺失时 skip"改造；科学/安全回归测试（`test_confgen_v3_coordination.py`、`test_confgen_v3_integration.py` TS1 部分）零改动。
- 本提交改动（4 项，无 `confflow/`）：
  1. `tests/fixtures/confgen/coordination/ts1/source/si-rr-salanal2-r-end-spdd-ts1.log` 删除（外移）；
  2. `tests/fixtures/confgen/coordination/ts1/MANIFEST.json`（log 条目加 `external`；同步 `README.md`/`verify_fixture.py` 的 bytes/sha256）；
  3. `tests/fixtures/confgen/coordination/ts1/benchmark/verify_fixture.py`（仅新增外部条目存在则校验/缺失则 SKIP 分支）；
  4. `tests/fixtures/confgen/coordination/ts1/README.md`（仅外部位置与取回说明）+ 本评估文件。
- 验证：`verify_fixture.py` 在缺失下 SKIP+PASS；`git show` 与 zip 取回 sha 均匹配；TS1 三 backend 与 engine 93 份对 `/tmp/ckpt/diet2/R1-final` 比对（见 `/tmp/finl3-out/REPORT.md`）；受影响测试与 architecture policy 结果同见 REPORT。
