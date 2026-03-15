# ConfFlow 使用说明（精简版）

本文档只保留必要信息：安装、命令行工具参数、YAML 配置格式。

## 1. 安装

```bash
pip install -e .
```

## 2. 工具总览

本项目提供 5 个命令行工具（安装后可直接调用）：

- `confflow`：按 YAML 工作流调度（当前支持 `confgen` / `calc` 步骤，并在流程结束后自动生成报告）
- `confgen`：构象生成（链模式）
- `confcalc`：对轨迹执行量化计算（读取 INI 配置）
- `confrefine`：构象去重/筛选（RMSD/能量窗口/虚频过滤）
- `confts`：TS 专用执行器/工具（TS 失败后 scan 救援、keyword 改写）

所有 CLI 的运行日志默认写入输入目录中的 `<input_basename>.txt`。

> `viz` 当前作为工作流内部步骤自动运行（生成美化的纯文本总结报告并合并到 .txt 输出），无需用户手动调用。

更完整的命令参数与示例见：`docs/COMMAND_REFERENCE.md`。

## 3. confflow：工作流调度

### 3.1 命令格式

```bash
confflow <input.xyz> -c <config.yaml> [-w <work_dir>] [--resume] [--verbose]
```

也支持一次输入多个 XYZ（会在 confgen 步骤对每个输入生成构象并合并后，进入后续统一计算/报告）：

```bash
confflow a.xyz b.xyz -c confflow.yaml
```

约束：多文件输入模式要求每个输入为单帧 XYZ。

- 未指定 `chains`：要求所有输入具有相同的原子数与元素顺序。
- 指定 `chains`：允许原子顺序不同，但要求原子数一致且元素计数一致；柔性链将基于拓扑映射自动对齐。

### 3.2 参数说明

- `<input.xyz>`：输入 XYZ（单帧或多帧）
- `-c/--config`：YAML 配置文件路径
- `-w/--work_dir`：工作目录（默认：`<input_basename>_work`）
- `--resume`：从 `.checkpoint` 断点继续
- `--verbose`：输出更详细日志

> 说明：`confflow` 运行时默认将 stdout/stderr 全部重定向到输入目录下的 `<input_basename>.txt`，
> 因此终端默认不会有输出；你需要查看该 `.txt` 文件获取运行日志、step 摘要与最终报告。
> 文本输出采用固定 100 列宽，分隔线与表格按同一宽度对齐。

一致性校验失败（多输入原子顺序/柔性链不一致等）时也不会弹出交互提示；错误信息会写入 `<input>.txt`，并以非 0 退出码结束。

统一返回码：`0` 成功，`1` 输入/配置/用法错误，`2` 运行时失败。

### 3.3 柔性链一致性与自动映射

当 `confgen` 使用 `chains` 时，系统会在第一个输入文件上校验链定义：

- 链上相邻原子必须有键连接（否则报错，并提示使用 `--add_bond` 或调整 `bond_threshold`）。
- 多输入情况下，后续输入会通过拓扑 MCS 映射自动识别对应柔性链，即便原子序号不同也可匹配。

若映射失败（例如骨架不一致或 MCS 覆盖率过低），会报错并终止。

### 3.4 主要输出文件（用户重点关注）

- `<input>.txt`：运行日志 + FINAL REPORT（统一主输出）
- `<input>min.xyz`：最低能量构象（单帧 XYZ）
- 最后一步输出多帧 XYZ：通常为 `step_xx/output.xyz`（若未生成 cleaned 则为 `step_xx/result.xyz`）

其余如 `.checkpoint`、`workflow_stats.json`、`results.db`、`confflow.log` 属于过程/诊断工件，不作为主交付物。

### 3.5 Step 摘要输出（写入 .txt）

运行 `confflow` 时，每个 step 会在开始/结束各写入一段简洁摘要（位于 `<input>.txt` 中）：

- 开始行：step 名称、类型、输入构象数；对 `calc/task` 还会输出 `prog/itask/max_jobs/cores/mem/freeze`。
- keyword：另起一行输出完整 `keyword`（便于复制复现）。
- 结束行：step 状态与汇总统计（输入/输出/失败数）及耗时。

每个 `calc/task` step 目录下常见文件：

- `step_xx/output.xyz`：该步后处理（refine）输出（若开启 `auto_clean`）
- `step_xx/result.xyz`：未精炼的原始输出（若未生成 cleaned 则以此为准）
- `step_xx/failed.xyz`：该步失败构象集合（始终使用输入结构坐标），注释行包含 `Job/CID/Error`

在 `_work/failed` 目录下会聚合所有步骤的失败信息：

- `_work/failed/failed.xyz`：合并后的失败构象集合（注释行附带 `Step=...`）
- `_work/failed/failed_summary.txt`：失败清单（结构名 + 错误原因 + 建议救援方案）
- `_work/failed/<config>.yaml`：本次运行的工作流配置副本（便于手动重跑）

## Gaussian `.chk` 跨步骤传递（按 CID 完全对应）

ConfFlow 对每个构象会维护一个稳定的 **CID**（写在 XYZ comment metadata 里），并据此派生稳定的 **job_name**：

- **来源感知的 CID**：首个输入文件的构象前缀为 `A`，第二个为 `B`，以此类推。
- **示例**：`CID=A000001` → `job_name=A000001`。
- **稳定性**：即使在中间步骤（如 `refine`）中丢弃了部分构象，剩余构象的 CID 保持不变，确保了 Gaussian `.chk` 文件等资源在后续步骤中能准确对应。

因此，跨步骤传递 Gaussian checkpoint 时采用 **按 job_name 精确匹配** 的方式，保证“工件与输入文件完全对应”，不会因后续步骤筛选/重排构象而错配。

### 用法

在需要“读取某一步 chk”的 calc 步骤中增加：

- `chk_from_step`: 指定 chk 来源步骤（可写步骤名或 1-based 步骤序号）
  - `chk_from_step: "step_06"`
  - `chk_from_step: 6`

运行每个 job 前，ConfFlow 会执行：

1. 从 `<work_dir>/<source_step>/backups/{job_name}.chk` 找到对应 chk
2. 复制到当前 job 的工作目录并命名为 `{job_name}.old.chk`
3. 自动在 Gaussian 输入文件中注入 `%OldChk={job_name}.old.chk`
4. 默认也会写出新的 `%Chk={job_name}.chk`，供后续步骤继续链式使用（可用 `gaussian_write_chk=false` 关闭）

### 典型 route

下游步骤通常在 `keyword` 中加入：

- `guess=read geom=allcheck`

## TS 失败救援功能 (ts_rescue_scan)

对于 `itask=ts` 的步骤，ConfFlow 提供了失败救援功能。当 TS 搜索失败（不收敛、虚频不对、关键键长几何判据失败等）时，程序可以自动尝试通过 Scan 寻找更好的起始点。

### 控制参数

在步骤的 `params` 或 `global` 中设置：

- `ts_rescue_scan: true`：开启救援。
- `ts_rescue_scan: false` (默认)：关闭救援，失败后直接报错。

### 示例

```yaml
  - name: "step_06_ts"
    type: "calc"
    params:
      iprog: g16
      itask: ts
      keyword: "opt=(ts,calcfc,noeigen) b3lyp/6-31g(d) freq"
      ts_rescue_scan: true  # 显式开启救援
      ts_bond_atoms: [1, 2]
```

以便从 chk 继承波函数与几何信息。
- `step_xx/results.db`：任务结果库（按 `job_name` / `CID` 记录 success/failed/skipped 与 error 详情；统计默认读取每个任务的最新状态）

## 4. confgen：构象生成（链模式）

### 4.1 重要说明

- 已移除“自动柔性键识别”，必须用 `--chain` 指定要旋转的链。
- 原子编号均为 **1-based**。

### 4.2 命令格式

```bash
confgen <mol.xyz> [<angle_step>] --chain <a-b-c-...> [--steps <s1,s2,...> | --angles "..." ] [--rotate_side left|right] [-y] [-opt]
```

### 4.3 常用示例

- 默认角度步长=120（链模式）：

```bash
confgen mol.xyz --chain 1-2-3-4-5 --steps 180,180,180,180 -y
```

- 显式角度列表（每根键用 `;` 分隔，每根键内部用 `,` 分隔角度）：

```bash
confgen mol.xyz --chain 1-2-3-4-5 --angles "0,120,240;0,60,120,180;180;0,120" -y
```

### 4.4 主要参数

- `--chain`：链（可重复多次）
- `--steps`：每根键的角度步长列表（与链内键数一致）
- `--angles`：每根键的角度集合（优先于 `--steps`）
- `--rotate_side`：旋转链的哪一侧（默认 `left`，即包含链首原子的一侧）
- `-y/--yes`：自动确认，不交互
- `--opt/--optimize`：MMFF94s 预优化

可选：手动修正拓扑/旋转约束（用于 XYZ 猜键不可靠、金属配位/闭环等场景）：

- `--add_bond a b`：强制添加键（可重复）
- `--del_bond a b`：强制删除键（可重复）
- `--no_rotate a b`：禁止旋转指定键（可重复；仅对链上键生效）
- `--force_rotate a b`：强制将指定键视为可旋转（可重复；一般不需要）

输出：当前目录生成 `search.xyz`（多帧 XYZ）。

## 5. confrefine：构象后处理

### 5.1 命令格式

```bash
confrefine <input.xyz> [-o <output.xyz>] [-t <rmsd>] [--ewin <kcal/mol>] [--imag <n>] [--noH] [-n <max>] [--dedup-only] [--keep-all-topos] [-w <workers>]
```

### 5.2 输出

- 默认输出为 `<input>_cleaned.xyz`，或由 `-o` 指定。

## 6. confcalc：量化计算执行器

`confcalc` 主要用于直接对轨迹跑计算（更常见用法是通过 `confflow` 的 `calc` 步骤调用）。

## 6.2 TS 失败后的 scan 救援（g16）

当 `itask=ts` 任务失败（例如 freq 判据不满足/关键键长判据失败/运行异常）且配置启用 `ts_rescue_scan=true` 时，ConfFlow 会尝试自动救援：

- **起点结构来源**：优先使用“失败 TS 的输入文件”中的结构（`<work_dir>/<job>.gjf|.com`）；若 TS 失败后已被备份/清理，则会在 `backup_dir/<job>.gjf|.com` 中继续寻找。
- **扫描方式**：对 `ts_bond_atoms` 指定的键长做多点优化扫描。
  - 每个点：先把目标键长设到指定值，然后执行 `opt(...)`。
  - **约束方式**：使用 confflow 的 `freeze` 机制冻结 `ts_bond_atoms` 两个原子（Gaussian 输入坐标第二列写 `-1`），不依赖 `modredundant`。
- **目录结构**：scan 点输出集中在 `<work_dir>/scan/` 下（不再为每个点创建大量子目录）。
- **文件命名**：扫描点作业名统一使用格式化后的 **键长数值** (如 `1.746.log`)，方便用户快速定位特定区域的计算。
- **Scan 表格输出**：会在 `<work_dir>/scan/scan_table.txt` 写入“键长-能量”关系表（标记能量最高点 `MAX`），并同样记录到 `<input>.txt`（终端默认无输出）。
- **选峰与 TS 重跑**：从 scan 能量曲线中选取局部极大值点作为 TS 初猜，然后用原始 TS 的 `keyword` 重新计算 TS（保持与主流程一致的方法/基组/外部势能等）。
- **结果汇总**：若 TS rescue 成功，会以 `rescued_by_scan=true` 标记，并按常规流程写入最终 `result.xyz`/结果库。

备注：`scan/` 目录会随该 TS 任务一并备份（若配置了 `backup_dir`）。备份位置为 `<work_dir>/<step>/backups/<job>_scan/`，其中也会包含 `scan_table.txt`。

### 6.1 命令格式

```bash
confcalc <search.xyz> -s <settings.ini>
```

续传说明：
- 默认会在工作目录生成 `results.db`，再次运行会自动跳过已成功的 `job_name` / `CID`。
- 若 `results.db` 丢失但 `backups/` 仍在（例如断电后只保留了备份文件），默认也会尝试从 `backups` 中恢复已完成任务并跳过（可用 `resume_from_backups=false` 关闭）。

## 7. YAML 配置：工作流格式

### 7.1 顶层结构

```yaml
global:
  gaussian_path: "/opt/g16/g16"    # 可选
  orca_path: "/opt/orca/orca"      # 可选
  cores_per_task: 4
  total_memory: "16GB"
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1

  # 冻结原子（仅对 opt/opt_freq 生效；sp/freq/ts 会强制关闭）
  # 支持：
  # - 列表： [1, 5]
  # - 字符串："1,5" / "1 5"
  # - 范围："1-5" / "1,2,5-7"
  freeze: [1, 5]


  # ORCA 专用：直接写入 %maxcore（单位：MB per core）
  orca_maxcore: 4500

  # TS 专用：指定参与“成键/断键”的两个原子（1-based），用于在 result.xyz 注释行输出 TS 键长
  # - 可写 [12, 15] 或 "12,15"
  # - 若不写，且 freeze 恰好给出两个原子，则默认沿用 freeze
  ts_bond_atoms: [12, 15]

  # 多输入柔性链检查：若为 true，允许跳过一致性检查（仅建议在已确认无误时使用）
  force_consistency: false

steps:
  - name: step_01
    type: confgen
    params: { ... }
  - name: step_02
    type: calc
    params: { ... }
```

### 7.2 step.type = confgen

```yaml
- name: step_01
  type: confgen
  params:
    chains: ["1-2-3-4-5"]
    chain_steps: ["180,180,180,180"]
    # 或 chain_angles: ["0,120,240;0,60,120,180;180;0,120"]
    rotate_side: left
    angle_step: 120
    bond_multiplier: 1.15
    optimize: false
```

### 7.3 step.type = calc

```yaml
- name: step_02_orca_sp
  type: calc
  params:
    iprog: orca              # orca / g16
    itask: sp                # opt / sp / freq / opt_freq / ts
    keyword: "B97-3c"

    # 可覆盖全局资源
    cores_per_task: 4
    total_memory: "16GB"
    max_parallel_jobs: 1

    # 可选：覆盖 ORCA %maxcore（单位：MB per core）
    orca_maxcore: 4500

    # TS 可选：指定 TS 成键/断键原子对（1-based）；用于输出 TSAtoms/TSBond 到 result.xyz 注释行
    ts_bond_atoms: [12, 15]

    # [1-based] ORCA 专用：写 ORCA 的 %block ... end
    # 推荐直接使用多行字符串，格式与 ORCA .inp 文件完全一致。
    # 程序会自动合并 freeze 产生的 %geom Constraints。
    blocks: |
      %method
        ProgExt "/opt/orcauma.py"
      end
      %geom
        Calc_Hess true
        NumHess true
      end
```

### 7.4 已移除功能（不再支持）

- 键冻结 / ModRedundant / constraints：仅保留 `freeze`（冻结原子坐标）。
- solvent_block / custom_block：已统一升级为结构化的 `blocks` 字典管理。
