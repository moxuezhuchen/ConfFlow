# ConfFlow MLIP（UMA）步骤方案（2026-10-26）

来源：`CONFFLOW-NOTES.md` 2026-10-24/25 各条（无 UMA 执行器、FixBondLengths 容差、GFN2 窗口不能预筛、
优化器影响结果、全量弛豫太贵、需记录评估次数与墙钟）。用户 2026-10-26 决定：剩余大功能全部做，顺序由我定。
顺序：本方案（C1）→ TS 步骤（C4，复用本步骤的 UMA 运行器）→ 可选配位位点（C3）→ 每状态能量窗口。

## 1. 现状

- UMA 单点已可用：`calculation` + Gaussian `external="uma_s"`，逐结构一个任务，靠 `uma_s` 的常驻服务避免重复加载模型。
- 缺：约束弛豫（固定距离、可选优化器）、"单点全部 → 挑少数弛豫" 的便宜漏斗、每个结构记录优化器/步数/评估次数/墙钟、
  弛豫后的键检查。现在每个项目都手写 ASE 脚本（`runs/20261024-pilot-site1-ts1/uma_reopt.py`）。

## 2. 设计：新执行器 `mlip`（整批一个任务）

为什么整批：模型加载在集群 lustre 上要 3–13 分钟，逐结构起进程不可接受；整批时运行器内部按 `workers × threads`
并行（本机经验：3×4 或 2×6，总线程 ≤12）。输入端口与 `structure_transform` 相同（`ONE_OR_MORE`, `SINGLE`）。

```yaml
- id: s_uma
  executor: mlip
  bindings:
    structure: {source: {step: s_gen, port: structures}}
  mlip:
    model: uma-s-1p2p1          # 必填，模型名（科学身份）；文件路径属机器配置，见 execution.env
    task: omol                  # 默认 omol
    mode: sp_then_relax         # 必填：sp | relax | sp_then_relax
    index_base: 1               # 默认 1
    relax:                      # mode 含 relax 时生效
      optimizer: precon_lbfgs   # lbfgs | precon_lbfgs | fire，默认 lbfgs；结果里逐结构记录
      fmax: 0.02                # eV/Å，默认 0.02
      max_steps: 600            # 默认 600
      fix_distances: [[39, 95]] # 默认 []；保持输入距离精确不变（FixBondLengths，tolerance 1e-8）
    pick:                       # 仅 sp_then_relax，必填：按单点能挑选要弛豫的结构
      lowest: 8                 # 单点能最低的 N 个
      per_label: 2              # 另外每个状态标签各取最低 M 个（标签 = search_state_flips；无标签时视为同一组）
    reject_bond_stretch: null   # 默认只标记；设数值（如 1.25）则拒绝任一输入键被拉长到超过该倍数的结构
  resources: {cores_per_item: 12}
  execution:
    executable: /opt/uma/.venv-fairchem/bin/python
    env: {CONFFLOW_MLIP_MODEL_PATH: /opt/uma/uma-s-1p2p1.pt, CONFFLOW_MLIP_REFS_PATH: /opt/uma/iso_atom_elem_refs.yaml,
          CONFFLOW_MLIP_THREADS: "4"}
```

- 电荷、多重度来自 `scientific_defaults`。
- `sp_then_relax` 的输出：被挑中的结构是弛豫后的几何与能量；未挑中的结构**不输出**（其单点能只用于 GFN2→UMA 校准诊断）。
  `sp` 输出全部结构（几何不变，能量为 UMA）。`relax` 弛豫全部。
- 未收敛的结构照常输出并标记 `mlip_converged=false`（默认只标记，与 PR #185 的口径一致）；出错的结构丢弃并在诊断里列出原因。
- `results` 输出端口带每个结构的 UMA 电子能。`filter`/`refine` 的 `results` 来源规则放宽为"calculation **或 mlip** 步骤"，
  因此 `mlip → filter(energy_window_kcal)` 可直接用。

## 3. 运行器（独立脚本，不导入 confflow）

`confflow/execution/mlip_runner.py`：执行器把它复制到任务目录，用 `execution.executable`（装有 fairchem+ASE 的 Python）运行
`python mlip_runner.py job.json`。

`job.json`（执行器写）：
```json
{"model_path": "...", "refs_path": "... or null", "task": "omol", "device": "cpu",
 "charge": 0, "multiplicity": 1, "mode": "sp_then_relax",
 "optimizer": "lbfgs", "fmax": 0.02, "max_steps": 600, "fix_pairs": [[38, 94]],
 "pick": {"lowest": 8, "per_label": 2} , "workers": 3, "threads": 4,
 "structures": [{"key": "s0001", "label": "flips=-", "symbols": ["Zr", ...], "coords": [[x, y, z], ...]}]}
```
`fix_pairs` 为 0 基。输出（运行器写，逐行追加，可续算——已有 key+stage 的行跳过）：
- `mlip_results.jsonl`：每行 `{"key","stage":"sp"|"relax","energy_eh","coords"|null,"converged","n_steps","n_evals",
  "wall_s","optimizer","fix_max_dev","error"}`；能量 eV → Eh 用 27.211386245988。
- `mlip_done.json`：`{"model_sha256","fairchem_version","ase_version","torch_threads","workers","wall_s","picked":[keys]}`。
- `mlip_runner.log`：人读日志。

结构：纯 Python 部分（读 job、挑选、续算、写 jsonl、调度）与后端分开；后端接口 `energy(symbols, coords)` /
`relax(symbols, coords, fix_pairs, optimizer, fmax, max_steps)`。真实后端 `_FairchemBackend`（每个 worker 进程加载一次模型，
`torch.set_num_threads(threads)`；precon_lbfgs 用 50 Å 盒子 + `Exp(r_cut=3.0, A=3.0)`、`use_armijo=True`；FixBondLengths tolerance 1e-8；
评估次数用计算器调用计数）标 `# pragma: no cover`。测试用进程内假后端覆盖其余部分；另有一个只在本机有 fairchem 时运行的
真实冒烟测试（`CONFFLOW_MLIP_PYTHON` 未设则跳过）。

## 4. 执行器侧（`confflow/execution/mlip_executor.py`）

1. 取全部输入记录，按 `index_base` 换算 `fix_distances`，越界或同一原子则失败。
2. 写 `job.json`，复制运行器，按 `execution.executable` 启动子进程；遵守 walltime 与取消（复用 script 执行器的进程监管方式）。
   `workers = max(1, cores_per_item // threads)`，`threads` 来自 env `CONFFLOW_MLIP_THREADS`（默认 4）。
3. 读 `mlip_results.jsonl`，每个输出结构建 `StructureRecord`（单父谱系，id 用 `conformer_output_id` 同类的稳定派生），
   元数据：`mlip_energy_eh`、`mlip_model`、`mlip_stage`、`mlip_optimizer`、`mlip_converged`、`mlip_n_steps`、`mlip_n_evals`、
   `mlip_wall_s`、`mlip_fix_max_dev`、`mlip_max_bond_stretch`、`mlip_stretched_bond`、`mlip_graph_changed`、`mlip_sp_energy_eh`，
   保留输入记录的 `search_state_flips`。
4. 键检查在执行器侧做（confflow 已有成键感知）：输入图的每条键在输出里的伸长比，取最大；新成键/断键记 `mlip_graph_changed`。
   设了 `reject_bond_stretch` 时超限的丢弃。
5. 诊断：INFO `mlip_completed`（输入数、单点数、弛豫数、未收敛数、丢弃数、总墙钟、每结构平均评估次数与墙钟）；
   WARNING `mlip_not_converged`、`mlip_bond_stretched`、`mlip_graph_changed`（各附结构列表）；
   以及 GFN2→UMA 校准：输入带 `energy_eh` 时报告两者排序的 Spearman 系数和 "GFN2 最低者在 UMA 下高出多少 kcal"。
6. 产物：`job.json`、`mlip_results.jsonl`、`mlip_done.json`、`mlip_runner.log`、`mlip_structures.xyz`（注释行含能量与上述字段）。

## 5. 接线

- `ExecutorCapability.MLIP = "mlip"`；`StepModel.mlip`（严格 pydantic 模型 `MlipModel`）；parser 的块映射；
  registry 新增执行器合同（输出 structures/results/artifacts）；validation 的结构目标端口、results 来源规则。
- 契约摘要会变：按测试重钉。`contract --json` 列出 mlip 字段。文档：USAGE 新节、CONFGEN_CAPABILITIES §1、示例
  `docs/examples/uma_relax_funnel.yaml`（validate 通过）。

## 6. 分工（Haiku 子代理，文件不重叠）

- A：`confflow/execution/mlip_runner.py` + `tests/v4/test_mlip_runner.py`（假后端；真实冒烟测试按环境变量跳过）。
- B：schema/parser/contracts/registry/validation 接线 + `tests/v4/test_mlip_step_schema.py`。
- C：`confflow/execution/mlip_executor.py` + `tests/v4/test_mlip_executor.py`（假运行器：一个写固定 jsonl 的小脚本）。
- 我：方案、接口、验收、契约重钉、文档、本机真实冒烟（TS1 金标准 7 个结构，sp_then_relax，约 10 分钟内，属已批准方案内的小型准备计算）。

## 7. 验收

- 全部测试、mypy、black 26.10.1、ruff、行数预算通过；changed-coverage ≥85%。
- 真实冒烟：7 个 TS1 结构，`fix_distances` 下距离偏离 <1e-6 Å，能量与 `uma_reopt.py` 同几何单点一致（<1e-6 Eh）；
  记录每结构步数/评估次数/墙钟。

## 8. 实测（2026-10-26，本机，已完成）

TS1 金标准 7 帧（帧 0 为 DFT 优化 TS，其余为 Molassembler 未优化嵌入），`sp_then_relax`、`pick.lowest: 3`、
`precon_lbfgs`、`fix_distances: [[74, 79]]`、3 workers × 4 threads，接 `filter energy_window_kcal: 3`：

- 运行 completed，总墙钟 1168 s（模型加载约 10 s；单点 1.2–3.1 s/个）。
- 单点能与独立进程复算一致（差 <1e-7 Eh）；固定距离偏差 ≤3e-9 Å；3 个弛豫全部收敛，成键图不变。
- 帧 0（已优化）：25 步 / 57 次评估 / 99 s；两个未优化帧：约 400 步 / 约 800 次评估 / 约 1130 s。
  未优化几何直接做 UMA 弛豫代价很高，应先经 ConfGen/GFN2。
- 键号以记录 id 排序生成（s00001…），与输入帧顺序不一定相同；结构对应关系看输出元数据与 `job.json`。
